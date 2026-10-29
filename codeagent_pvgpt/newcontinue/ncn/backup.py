# ncn/backup.py — [ncn E4] 쓰기 전 백업과 /undo(계획 6장 E4 "승인 후 되돌리기"). cn 원본 없음(ncn 추가)
# Write/Edit/MultiEdit 가 파일을 쓰기 직전 원본을 잡아 두고(capture), 쓰기가 성공하면 백업으로 남긴다(commit).
# 위치: <전역 폴더>/ncn-backup/<세션 id>/<순번 6자리>_<경로 해시 12자리>(원본 바이트) + 같은 이름.json(경로·있었나·도구·시각).
#   전역 폴더(~/.continue, CONTINUE_GLOBAL_DIR 반영)를 고른 이유: <작업 폴더>/.continue 는 규칙 파일 때문에 저장소에 커밋되는 경우가 많아
#   백업(코드 사본)이 git status·VS 솔루션 탐색기에 섞이고 실수로 커밋될 수 있다. 전역 폴더는 저장소를 더럽히지 않는다(차등 시험의 작업 폴더 비교도 그대로).
# 새로 만든 파일은 "없었음"(existed: false)으로 남기고, 되돌리면 그 파일을 지운다.
# /undo: 마지막 수정부터 하나씩 되돌림(되돌린 백업은 지움), /undo list: 목록. 대화형 슬래시 명령·ACP 텍스트 프롬프트 "/undo" 에서 쓴다.
# 되돌림은 디스크에 원본 바이트를 그대로(인코딩·BOM·줄바꿈까지 원래대로). IDE 위임(fsio.AcpFiles) 중이면 IDE 쓰기 요청(fs/write_text_file)으로.
# 헤드리스는 백업만 남긴다 — 세션 id 가 세션 파일과 같으므로 나중에 대화형 `--resume`·ACP session/load 에서 /undo 로 되돌릴 수 있다.
# 정리: 세션당 최대 N개(ncn.yaml backup.max_per_session, 기본 50 — 넘으면 오래된 것부터 지움), keep_days(기본 30일)보다 오래된 세션 폴더는 지움.
# 끄기: ncn.yaml `backup: {enabled: false}` 또는 NCN_CN_COMPAT=1. 요청 본문(도구 목록·설명·결과 글)은 바꾸지 않는다.
from __future__ import annotations                           # 타입 주석 지연 평가

import contextvars                                           # 세션(작업)별 id(ACP 는 한 프로세스에 여러 세션)
import datetime                                              # 백업 시각
import hashlib                                               # 경로 해시·내용 해시
import json                                                  # 메타 파일
import logging                                               # 로그(stdout 금지 — stdio 모드)
import os                                                    # 경로·파일
import re                                                    # 폴더 이름 정리
import shutil                                                # 오래된 세션 폴더 지우기
import time                                                  # 보관 기간 계산
from dataclasses import dataclass                            # 자료 구조
from typing import Callable, Optional                        # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치

log = logging.getLogger("ncn.backup")                        # 로거

BACKUP_DIR_NAME = "ncn-backup"                               # 전역 폴더 아래 백업 폴더 이름
BACKUP_DIR_ENV = "NCN_BACKUP_DIR"                            # 백업 최상위 폴더를 바꾸는 환경변수(시험이 사용자 ~/.continue 를 더럽히지 않게)
UNDO_USAGE = "사용법: /undo — 마지막 파일 수정 되돌리기, /undo list — 백업 목록"   # 사용법 안내


@dataclass                                                  # 자료 클래스
class Snapshot:                                             # 쓰기 직전에 잡은 원본
    """path: 절대 경로, existed: 쓰기 전에 있었나, data: 원본(bytes 모드는 디스크 바이트, text 모드는 IDE 버퍼 글의 UTF-8), mode: 'bytes'|'text'."""
    path: str                                                # 파일 절대 경로
    existed: bool                                            # 쓰기 전에 있었나
    data: Optional[bytes]                                    # 원본 내용(없었으면 None)
    mode: str                                                # 'bytes'(디스크) | 'text'(IDE 위임)
    tool: str                                                # 도구 이름(Write·Edit·MultiEdit)
    session_id: str                                          # 백업할 세션 id(잡을 때 정함)


# ───────────────────────── 세션 id ─────────────────────────
_session_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("ncn_backup_session", default=None)   # ACP 세션 작업용
_resolver: Optional[Callable[[], str]] = None                # 대화형·헤드리스: SessionManager 현재 세션 id 를 돌려주는 함수


def set_session_id(session_id: Optional[str]) -> contextvars.Token:   # 이 작업(컨텍스트)의 세션 id
    """ACP 서버가 턴 작업마다 부른다(여러 세션이 한 프로세스에서 돎)."""
    return _session_ctx.set(session_id)                      # 토큰 반환


def set_session_resolver(fn: Optional[Callable[[], str]]) -> None:   # 프로세스 기본 세션 id 함수
    """대화형·헤드리스가 SessionManager 를 만든 뒤 부른다 — 쓸 때마다 현재 세션 id 를 묻는다(/resume 으로 바뀌어도 맞게)."""
    global _resolver                                         # 모듈 전역 바꿈
    _resolver = fn                                           # 저장


def current_session_id() -> str:                            # 지금 백업할 세션 id
    """컨텍스트 값 → 등록 함수 → 'proc-<pid>'(세션을 모를 때 — 이 프로세스 안에서만 되돌릴 수 있음)."""
    sid = _session_ctx.get()                                 # ACP 세션 작업
    if sid:                                                  # 있으면
        return sid                                          # 그것
    if _resolver is not None:                                # 등록 함수
        try:                                                # 실패 대비
            sid = _resolver()                                # 현재 세션 id
            if sid:                                          # 비지 않았으면
                return sid                                  # 그것
        except Exception as e:                               # 실패
            log.debug("세션 id 함수 실패: %s", e)             # 기록
    return f"proc-{os.getpid()}"                             # 대체 id


# ───────────────────────── 설정·폴더 ─────────────────────────
def enabled() -> bool:                                      # 백업을 남길지
    """NCN_CN_COMPAT=1 이 아니고 ncn.yaml backup.enabled 가 참(기본)이면 참."""
    if cn_compat():                                          # cn 원래 동작
        return False                                        # 백업 없음
    from ncn.encoding import ncn_settings                    # 같은 ncn.yaml 읽기
    return bool(ncn_settings().backup_enabled)               # 설정값(기본 참)


def _settings():                                            # ncn.yaml 설정
    """ncn.encoding.ncn_settings 와 같은 값."""
    from ncn.encoding import ncn_settings                    # 지연 import
    return ncn_settings()                                    # 설정


def backup_root() -> str:                                   # 백업 최상위 폴더
    """<전역 폴더>/ncn-backup. 환경변수 NCN_BACKUP_DIR 이 있으면 그 폴더(시험·관리자 지정용)."""
    override = os.environ.get(BACKUP_DIR_ENV)                # 지정 폴더
    if override:                                             # 있으면
        return os.path.abspath(override)                     # 그 폴더
    from ncn.config.paths import global_dir                  # 전역 폴더(CONTINUE_GLOBAL_DIR 반영)
    return os.path.join(str(global_dir()), BACKUP_DIR_NAME)  # 경로


def _safe(sid: str) -> str:                                 # 세션 id → 폴더 이름
    """경로에 못 쓰는 글자는 '_' 로(세션 id 는 보통 uuid 라 그대로)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", sid) or "_"       # 정리


def session_dir(session_id: str) -> str:                    # 세션 백업 폴더
    """<전역>/ncn-backup/<세션 id>."""
    return os.path.join(backup_root(), _safe(session_id))    # 경로


def _path_hash(path: str) -> str:                           # 경로 해시 12자리
    """대소문자 그대로 경로의 sha1 앞 12자리(같은 파일 백업을 이름으로 알아보기 쉽게)."""
    return hashlib.sha1(path.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]   # 해시


# ───────────────────────── 잡기·남기기 ─────────────────────────
async def capture(path: str, tool: str) -> Optional[Snapshot]:   # 쓰기 직전 원본 잡기
    """백업이 꺼져 있거나 실패하면 None(백업 실패로 수정을 막지는 않는다 — 경고만)."""
    if not enabled():                                        # 꺼져 있으면
        return None                                         # 없음
    try:                                                    # 실패는 경고만
        from ncn import fsio                                 # 파일 창구
        ap = os.path.abspath(path)                           # 절대 경로
        if not os.path.exists(ap):                           # 새 파일
            return Snapshot(ap, False, None, "bytes", tool, current_session_id())   # "없었음"
        if fsio.writes_to_ide():                             # IDE 위임 중 — 저장 안 한 버퍼가 원본
            text = await fsio.read_text_file(ap)             # IDE 버퍼(실패하면 디스크)
            return Snapshot(ap, True, text.encode("utf-8", errors="surrogatepass"), "text", tool, current_session_id())   # 글 모드
        with open(ap, "rb") as f:                            # 디스크 바이트 그대로(인코딩·BOM·줄바꿈 보존)
            return Snapshot(ap, True, f.read(), "bytes", tool, current_session_id())   # 바이트 모드
    except Exception as e:                                   # 폴더·권한 등
        log.warning("백업 실패(수정은 계속): %s (%s)", path, e)   # 경고
        return None                                         # 없음


def commit(snap: Optional[Snapshot]) -> None:               # 쓰기 성공 뒤 백업으로 남기기
    """순번을 매겨 저장하고 세션당 최대 개수·보관 기간으로 정리한다. 실패는 경고만."""
    if snap is None:                                         # 잡은 것이 없으면
        return                                              # 할 일 없음
    try:                                                    # 실패는 경고만
        d = session_dir(snap.session_id)                     # 세션 폴더
        os.makedirs(d, exist_ok=True)                        # 없으면 만듦
        entries = _entries(d)                                # 지금 백업들
        seq = (entries[-1]["seq"] + 1) if entries else 1     # 다음 순번
        base = f"{seq:06d}_{_path_hash(snap.path)}"          # 파일 이름
        if snap.data is not None:                            # 원본이 있으면
            with open(os.path.join(d, base), "wb") as f:     # 바이트 그대로
                f.write(snap.data)                           # 원본 기록
        meta = {"path": snap.path, "existed": snap.existed, "mode": snap.mode, "tool": snap.tool,   # 메타 정보
                "time": datetime.datetime.now().isoformat(timespec="seconds"), "after": _sha_of(snap.path)}   # 시각·쓴 뒤 내용 해시
        with open(os.path.join(d, base + ".json"), "w", encoding="utf-8", newline="") as f:   # 메타(줄바꿈 변환 없이)
            json.dump(meta, f, ensure_ascii=False)           # 저장
        _prune(d)                                            # 세션당 최대 개수
        _prune_old_sessions()                                # 오래된 세션 폴더
    except Exception as e:                                   # 실패
        log.warning("백업 저장 실패: %s (%s)", snap.path, e)  # 경고


def _sha_of(path: str) -> Optional[str]:                    # 디스크 내용 해시(되돌릴 때 "그 뒤에 또 바뀌었나" 확인용)
    """없거나 못 읽으면 None."""
    try:                                                    # 읽기
        with open(path, "rb") as f:                          # 바이트
            return hashlib.sha256(f.read()).hexdigest()      # 해시
    except OSError:                                          # 없음 등
        return None                                         # 모름


def _entries(d: str) -> list[dict]:                         # 세션 폴더의 백업 목록(오래된 것부터)
    """메타 파일(<순번>_<해시>.json)을 읽어 순번 순서로. 깨진 메타는 건너뜀."""
    out: list[dict] = []                                     # 결과
    if not os.path.isdir(d):                                 # 폴더가 없으면
        return out                                          # 빈 목록
    for name in os.listdir(d):                               # 파일마다
        m = re.fullmatch(r"(\d{6,})_([0-9a-f]{12})\.json", name)   # 메타 파일 이름
        if not m:                                            # 아니면
            continue                                        # 건너뜀
        try:                                                # 메타 읽기
            with open(os.path.join(d, name), encoding="utf-8") as f:   # 열기
                meta = json.load(f)                          # 해석
        except Exception:                                    # 깨짐
            continue                                        # 건너뜀
        base = name[:-5]                                     # '.json' 뗀 이름
        out.append({**meta, "seq": int(m.group(1)), "base": base, "dir": d})   # 순번·이름 붙임
    out.sort(key=lambda e: e["seq"])                         # 오래된 것부터
    return out                                              # 목록


def _remove_entry(e: dict) -> None:                         # 백업 하나 지우기
    """원본 파일과 메타 파일을 지운다(없으면 무시)."""
    for name in (e["base"], e["base"] + ".json"):            # 두 파일
        try:                                                # 지우기
            os.remove(os.path.join(e["dir"], name))          # 삭제
        except OSError:                                      # 없음
            pass                                            # 무시


def _prune(d: str) -> None:                                 # 세션당 최대 개수 정리
    """max_per_session 을 넘으면 오래된 것부터 지운다."""
    limit = int(_settings().backup_max_per_session)          # 최대 개수
    entries = _entries(d)                                    # 지금 목록
    for e in entries[:max(0, len(entries) - limit)]:         # 넘는 만큼 오래된 것
        _remove_entry(e)                                     # 지움


_pruned_old = False                                          # 이 프로세스에서 오래된 세션 정리를 했나(한 번만)


def _prune_old_sessions() -> None:                          # 보관 기간 지난 세션 폴더 정리
    """keep_days 보다 오래 안 바뀐 세션 폴더를 지운다(프로세스당 한 번)."""
    global _pruned_old                                       # 모듈 전역 바꿈
    if _pruned_old:                                          # 이미 했으면
        return                                              # 끝
    _pruned_old = True                                       # 했음 표시
    root = backup_root()                                     # 최상위 폴더
    cutoff = time.time() - int(_settings().backup_keep_days) * 86400   # 기준 시각
    try:                                                    # 목록
        names = os.listdir(root)                             # 세션 폴더들
    except OSError:                                          # 없음
        return                                              # 끝
    for name in names:                                       # 폴더마다
        p = os.path.join(root, name)                         # 경로
        try:                                                # 시각 확인·지우기
            if os.path.isdir(p) and os.path.getmtime(p) < cutoff:   # 오래됐으면
                shutil.rmtree(p, ignore_errors=True)         # 지움
        except OSError:                                      # 실패
            pass                                            # 무시


# ───────────────────────── 목록·되돌리기 ─────────────────────────
def list_entries(session_id: str) -> list[dict]:            # 세션 백업 목록(최근 것부터)
    """/undo list·/undo 가 쓰는 목록 — 최근 것이 맨 앞."""
    return list(reversed(_entries(session_dir(session_id))))   # 뒤집기


def format_list(session_id: str) -> str:                    # /undo list 글
    """최근 것부터 번호·시각·도구·경로(새 파일 표시)."""
    entries = list_entries(session_id)                       # 목록
    if not entries:                                          # 없으면
        return "되돌릴 파일 수정이 없습니다(이 세션의 백업 없음)."   # 안내
    lines = [f"백업 {len(entries)}개(최근 것부터 — /undo 는 맨 위부터 하나씩 되돌림):"]   # 머리
    for i, e in enumerate(entries, 1):                       # 하나씩
        mark = " [새 파일 — 되돌리면 삭제]" if not e.get("existed") else ""   # 새 파일 표시
        lines.append(f"  {i}. {e.get('time', '')}  {e.get('tool', '')}  {e.get('path', '')}{mark}")   # 한 줄
    return "\n".join(lines)                                  # 글


def _restore_plan(e: dict) -> tuple[str, Optional[bytes]]:  # 되돌릴 경로와 원본
    """(경로, 원본 바이트 또는 None(없었음))."""
    if not e.get("existed"):                                 # 새 파일이었음
        return e["path"], None                               # 지울 것
    with open(os.path.join(e["dir"], e["base"]), "rb") as f:   # 원본
        return e["path"], f.read()                           # 바이트


def _changed_note(e: dict) -> str:                          # 마지막 수정 뒤 또 바뀌었으면 알림
    """쓴 뒤 해시와 지금 디스크 해시가 다르면 경고 글(IDE 위임 백업은 해시가 디스크 기준이라 비교하지 않음)."""
    if e.get("mode") != "bytes" or e.get("after") is None:   # 비교할 수 없으면
        return ""                                           # 없음
    return "" if _sha_of(e["path"]) == e["after"] else " (주의: 그 수정 뒤에 파일이 또 바뀌었습니다 — 그 변경도 함께 사라졌습니다)"   # 경고


def _done_message(e: dict, remaining: int, note: str) -> str:   # 되돌림 결과 글
    """되돌린 내용·남은 백업 수."""
    what = "새로 만든 파일을 지웠습니다" if not e.get("existed") else "수정 전 내용으로 되돌렸습니다"   # 무엇을 했나
    return f"되돌림: {e.get('tool', '')} {e['path']} — {what}{note}. 남은 백업 {remaining}개."   # 글


def undo_last(session_id: str) -> str:                      # 마지막 수정 되돌리기(디스크 — 대화형)
    """가장 최근 백업을 디스크에 그대로 되돌리고(새 파일이면 지움) 그 백업을 지운다. 결과 글을 돌려준다."""
    entries = list_entries(session_id)                       # 최근 것부터
    if not entries:                                          # 없으면
        return "되돌릴 파일 수정이 없습니다(이 세션의 백업 없음)."   # 안내
    e = entries[0]                                           # 가장 최근
    try:                                                    # 되돌리기
        note = _changed_note(e)                              # 그 뒤 변경 확인
        path, data = _restore_plan(e)                        # 경로·원본
        if data is None:                                     # 새 파일이었음
            if os.path.isfile(path):                         # 아직 있으면
                os.remove(path)                              # 지움
        else:                                                # 원본 있음
            if e.get("mode") == "text":                      # IDE 버퍼 글로 잡은 백업을 디스크로 되돌릴 때
                from ncn import encoding                     # 글 → 원래 형식
                if encoding.enabled():                       # [ncn E3] 켜져 있으면
                    encoding.write_text(path, data.decode("utf-8", errors="surrogatepass"))   # 파일의 인코딩·줄바꿈으로
                else:                                        # 꺼져 있으면
                    _write_bytes(path, data)                 # UTF-8 그대로(cn 과 같은 저장 형식)
            else:                                            # 디스크 바이트
                _write_bytes(path, data)                     # 원본 바이트 그대로
        _remove_entry(e)                                     # 되돌린 백업 지움
        return _done_message(e, len(entries) - 1, note)      # 결과 글
    except Exception as ex:                                  # 실패(백업은 남김)
        return f"되돌리기 실패: {e.get('path')} — {ex}"        # 오류 글


def _write_bytes(path: str, data: bytes) -> None:           # 바이트 그대로 쓰기(상위 폴더 없으면 만듦)
    """지워진 폴더도 다시 만든다."""
    parent = os.path.dirname(path)                           # 상위 폴더
    if parent:                                               # 있으면
        os.makedirs(parent, exist_ok=True)                   # 만듦
    with open(path, "wb") as f:                              # 바이트(줄바꿈 변환 없음)
        f.write(data)                                       # 기록


async def undo_last_async(session_id: str) -> str:          # 마지막 수정 되돌리기(ACP — IDE 위임이면 IDE 쓰기 요청)
    """IDE 위임 중이면 원본 글을 fs/write_text_file 로(IDE 버퍼 편집 → Ctrl+Z 가능). 아니면 undo_last 와 같다.
    새 파일 삭제는 ACP 에 삭제 요청이 없어 디스크에서 지운다."""
    from ncn import fsio                                     # 파일 창구
    if not fsio.writes_to_ide():                             # 디스크
        return undo_last(session_id)                         # 디스크 되돌림
    entries = list_entries(session_id)                       # 최근 것부터
    if not entries:                                          # 없으면
        return "되돌릴 파일 수정이 없습니다(이 세션의 백업 없음)."   # 안내
    e = entries[0]                                           # 가장 최근
    try:                                                    # 되돌리기
        path, data = _restore_plan(e)                        # 경로·원본
        if data is None:                                     # 새 파일이었음
            if os.path.isfile(path):                         # 있으면
                os.remove(path)                              # 디스크에서 지움(ACP 에 삭제 요청 없음)
        else:                                                # 원본 있음
            from ncn.encoding import decode_bytes, fallback_encoding   # 바이트 → 글
            text = data.decode("utf-8", errors="surrogatepass") if e.get("mode") == "text" else decode_bytes(data, fallback_encoding())[0]   # 원본 글
            await fsio.write_text_file(path, text)           # IDE 쓰기 요청(실패하면 디스크)
        _remove_entry(e)                                     # 되돌린 백업 지움
        return _done_message(e, len(entries) - 1, "")        # 결과 글
    except Exception as ex:                                  # 실패
        return f"되돌리기 실패: {e.get('path')} — {ex}"        # 오류 글


def is_undo_command(text: str) -> bool:                     # 입력이 /undo 명령인지
    """'/undo' 또는 '/undo <인자>'(앞 공백 없음 — /compact 판정과 같은 규칙)."""
    return text.startswith("/") and text[1:].split(" ")[0] == "undo"   # 명령 이름 비교


def undo_command(text_args: list, session_id: str) -> str:  # 대화형 /undo 처리(동기·디스크)
    """인자 없음 → 되돌리기, 'list' → 목록, 그 밖 → 사용법."""
    a = [x for x in text_args if x]                          # 빈 조각 제거
    if not a:                                                # /undo
        return undo_last(session_id)                         # 되돌리기
    if a == ["list"]:                                        # /undo list
        return format_list(session_id)                       # 목록
    return UNDO_USAGE                                        # 사용법


async def undo_command_async(text: str, session_id: str) -> str:   # ACP /undo 처리
    """'/undo' 글 전체를 받아 처리(IDE 위임이면 IDE 쓰기)."""
    a = [x for x in text[1:].split(" ")[1:] if x.strip()]    # 인자
    if not a:                                                # /undo
        return await undo_last_async(session_id)             # 되돌리기
    if [x.strip() for x in a] == ["list"]:                   # /undo list
        return format_list(session_id)                       # 목록
    return UNDO_USAGE                                        # 사용법
