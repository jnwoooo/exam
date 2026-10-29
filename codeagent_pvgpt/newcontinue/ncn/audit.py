# ncn/audit.py — [ncn E6] 감사 기록: 로컬 JSONL + (선택) 코드에이전트 서버 전송. (cn 원본 없음 — 계획 6장 E6·5-5(8))
# 로컬: <전역 폴더>/ncn-audit/YYYY-MM-DD.jsonl(UTC 날짜) — 한 줄에 사건 하나. 코드 원문·파일 내용·비밀은 남기지 않는다(경로·명령 첫 줄·인자 이름만).
#   사건 공통: id(중복 전송 방지), ts(UTC ISO), session, ui(headless|interactive|acp), mode(normal|plan|auto), kind
#   kind: turn_start · turn_end · tool_call · tool_result · tool_error · permission · approval · usage · retry · system · loop_guard
# 서버(선택): ncn.yaml/policy.yaml `audit: {server: "http://host:8200", token_env: CODEAGENT_TOKEN, batch: 20}`
#   → POST <server>/audit/events {"events": [...]} (Bearer 개인 토큰). 보낼 사건은 먼저 ncn-audit/outbox.jsonl 에 쌓고,
#   백그라운드 스레드가 batch 개가 모이거나 턴이 끝날 때 보낸다. 실패하면 outbox 에 그대로 남아 다음에 다시 보낸다(대화를 막지 않음).
# 요청 본문은 바꾸지 않는다. NCN_CN_COMPAT=1 이면 꺼진다. audit.enabled: false 면 로컬 기록·전송 모두 끈다(관리자 정책에 audit 이 있으면 정책이 이김).
from __future__ import annotations                           # 타입 주석 지연 평가

import atexit                                                # 끝날 때 마지막 전송
import datetime as dt                                        # UTC 시각
import inspect                                               # 동기/비동기 콜백 구분
import json                                                  # JSON 줄
import logging                                               # 로그(stdout 금지)
import os                                                    # 프로세스 id·파일 교체
import pathlib                                               # 경로
import re                                                    # 비밀 가리기
import threading                                             # 백그라운드 전송·파일 잠금
import time                                                  # 오래된 전송 파일 판정
import uuid                                                  # 사건 id
from typing import Any, Callable, Optional, Union            # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치

log = logging.getLogger("ncn.audit")                         # 로거

AUDIT_DIR = "ncn-audit"                                      # 전역 폴더 안 감사 기록 폴더
OUTBOX = "outbox.jsonl"                                      # 아직 서버에 안 보낸 사건
ENDPOINT = "/audit/events"                                   # 서버 받는 주소(agent_server/audit.py)
DEFAULT_TOKEN_ENV = "CODEAGENT_TOKEN"                        # 개인 토큰 환경변수 기본 이름(README 5장과 같음)
DEFAULT_BATCH = 20                                           # 묶어 보낼 개수 기본값
STALE_SENDING_SECONDS = 300                                  # 전송 중 파일이 이만큼 오래되면(앞 프로세스가 죽음) 다시 보냄
SEND_TIMEOUT = 5.0                                           # 전송 한 번의 시간 제한(초)
CLOSE_WAIT = 3.0                                             # 끝낼 때 마지막 전송을 기다리는 시간(초)

PATH_KEYS = ("filepath", "file_path", "dirpath", "path", "notebook_path")   # 경로로 보고 남기는 인자 이름(Read·Write·Edit·List·Search)
ID_KEYS = ("skill_name", "subagent_name", "job_id")           # 이름·id 로 보고 남기는 인자(내용 아님)
_SECRET_RE = re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|pwd|authorization)\s*[=:]\s*)((?:bearer|basic)\s+)?(\"[^\"]*\"|'[^']*'|\S+)")   # 키=값 모양 비밀(값 앞 Bearer 포함)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)\S+")               # Authorization: Bearer xxx


# ─────────────────────────────────────────────────────────────────────────────
# 설정(ncn.yaml audit 절 — ncn/subagent.py parse_ncn_settings 가 부름)
# ─────────────────────────────────────────────────────────────────────────────


def parse_audit_settings(data: Any, out: Any) -> None:       # ncn.yaml 의 audit 절 → NcnSettings 필드
    """audit: {enabled, server, token_env, batch} 를 out.audit_* 에 넣는다. 형식이 틀린 값은 경고 후 기본값."""
    sec = data.get("audit") if isinstance(data, dict) else None   # audit 절
    if sec is None:                                          # 없으면
        return                                              # 기본값(로컬만 기록)
    if not isinstance(sec, dict):                            # 사전이 아니면
        log.warning("ncn.yaml: audit must be a mapping — ignored")   # 경고
        return                                              # 기본값
    _apply_audit_section(sec, out, "ncn.yaml")               # 값 반영


def _apply_audit_section(sec: dict, out: Any, where: str) -> None:   # audit 절 하나를 대상 객체에 반영(ncn.yaml·policy.yaml 공용)
    """enabled(참거짓)·server(글)·token_env(글)·batch(양의 정수)."""
    v = sec.get("enabled")                                   # 로컬 기록 켜기
    if v is not None:                                        # 적혀 있으면
        if isinstance(v, bool):                              # 참거짓이면
            out.audit_enabled = v                            # 반영
        else:                                                # 아니면
            log.warning("%s: audit.enabled must be true/false — ignored", where)   # 경고
    v = sec.get("server")                                    # 서버 주소
    if v is not None:                                        # 적혀 있으면
        if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):   # http(s) 주소면
            out.audit_server = v.strip().rstrip("/")         # 끝 / 없이
        else:                                                # 아니면
            log.warning("%s: audit.server must be an http(s) URL — ignored", where)   # 경고
    v = sec.get("token_env")                                 # 토큰 환경변수 이름
    if v is not None:                                        # 적혀 있으면
        if isinstance(v, str) and v.strip():                 # 빈 글이 아니면
            out.audit_token_env = v.strip()                  # 반영
        else:                                                # 아니면
            log.warning("%s: audit.token_env must be a variable name — ignored", where)   # 경고
    v = sec.get("batch")                                     # 묶음 크기
    if v is not None:                                        # 적혀 있으면
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:   # 양의 정수면
            out.audit_batch = v                              # 반영
        else:                                                # 아니면
            log.warning("%s: audit.batch must be a positive integer — ignored", where)   # 경고


class _AuditConfig:                                          # 실제로 쓸 감사 설정(사용자 설정 + 관리자 정책)
    """audit_enabled·audit_server·audit_token_env·audit_batch."""

    def __init__(self, settings: Any = None):                # NcnSettings(없으면 기본값)
        self.audit_enabled = bool(getattr(settings, "audit_enabled", True))   # 로컬 기록
        self.audit_server = getattr(settings, "audit_server", None)   # 서버 주소
        self.audit_token_env = getattr(settings, "audit_token_env", DEFAULT_TOKEN_ENV) or DEFAULT_TOKEN_ENV   # 토큰 변수
        self.audit_batch = getattr(settings, "audit_batch", DEFAULT_BATCH) or DEFAULT_BATCH   # 묶음 크기


def effective_audit_config(settings: Any, global_dir: Any = None, environ: Any = None) -> _AuditConfig:   # 사용자 설정 위에 관리자 정책을 덮는다
    """관리자 policy.yaml 의 audit 절(있으면)이 ncn.yaml 보다 우선 — 사용자가 enabled: false 로 꺼도 정책이 켜면 켜진다."""
    cfg = _AuditConfig(settings)                             # 사용자 설정
    from ncn.loopguard import read_policy_value              # 관리자 정책 읽기(ncn/policy.py 지연 import)
    pol = read_policy_value("audit", global_dir, environ)    # 정책의 audit 절
    if isinstance(pol, dict):                                # 있으면
        if "enabled" not in pol:                             # 정책에 audit 이 있으면 기본은 켬
            cfg.audit_enabled = True                         # 사용자가 못 끔
        _apply_audit_section(pol, cfg, "policy.yaml")        # 정책 값 덮어쓰기
    return cfg                                              # 최종 설정


# ─────────────────────────────────────────────────────────────────────────────
# 요약(코드 원문·비밀 없이)
# ─────────────────────────────────────────────────────────────────────────────


def first_line(text: Any, limit: int = 200) -> str:         # 글의 첫 줄만(길면 자름)
    """None 은 빈 글. 첫 줄을 limit 글자까지, 비밀 모양은 가린다."""
    if text is None:                                         # 없으면
        return ""                                           # 빈 글
    s = str(text).strip().splitlines()                       # 줄로 나눔
    line = s[0] if s else ""                                 # 첫 줄
    if len(line) > limit:                                    # 길면
        line = line[:limit] + "…"                            # 자르고 표시
    return mask_secrets(line)                                # 비밀 가리기


def mask_secrets(s: str) -> str:                             # token=xxx·Bearer xxx 모양 가리기
    """키=값 모양의 비밀값과 Bearer 토큰을 *** 로."""
    s = _SECRET_RE.sub(lambda m: m.group(1) + "***", s)      # 키=값
    return _BEARER_RE.sub(lambda m: m.group(1) + "***", s)   # Bearer


def _strip_url(url: str) -> str:                             # URL 의 질의·조각 제거(토큰이 들어 있을 수 있음)
    """https://a/b?x=1#y → https://a/b."""
    return url.split("#", 1)[0].split("?", 1)[0][:300]       # ? · # 앞까지


def summarize_args(name: Optional[str], args: Any) -> dict:   # 도구 인자 요약(감사 기록용)
    """경로 인자·명령 첫 줄·URL(질의 제거)·이름 인자만 남기고, 나머지는 인자 이름 목록(keys)만. 파일 내용·코드 원문은 남기지 않는다."""
    if not isinstance(args, dict):                           # 인자가 사전이 아니면
        return {}                                           # 남길 것 없음
    out: dict = {}                                           # 요약
    for k in PATH_KEYS:                                      # 경로 인자
        v = args.get(k)                                      # 값
        if isinstance(v, str):                               # 글이면
            out[k] = v[:300]                                 # 경로(길면 자름)
    cmd = args.get("command")                                # Bash 명령
    if isinstance(cmd, str):                                 # 글이면
        out["command"] = first_line(cmd, 200)                # 첫 줄만(비밀 가림)
    url = args.get("url")                                    # Fetch 주소
    if isinstance(url, str):                                 # 글이면
        out["url"] = _strip_url(url)                         # 질의 제거
    for k in ID_KEYS:                                        # 이름·id 인자
        v = args.get(k)                                      # 값
        if isinstance(v, (str, int)) and not isinstance(v, bool):   # 글·숫자면
            out[k] = str(v)[:100]                            # 짧게
    out["keys"] = sorted(str(k) for k in args)               # 인자 이름만(값 없음)
    return out                                              # 요약


def _utc_now() -> dt.datetime:                               # 지금(UTC)
    """시험에서 바꿔 끼울 수 있게 함수로."""
    return dt.datetime.now(dt.timezone.utc)                  # UTC 시각


async def _maybe_await(v: Any) -> Any:                      # 동기/비동기 값을 모두 받는 보조 함수
    """awaitable 이면 기다린다."""
    if inspect.isawaitable(v):                              # 기다릴 수 있는 값이면
        return await v                                      # 결과
    return v                                                # 그대로


# ─────────────────────────────────────────────────────────────────────────────
# 기록기
# ─────────────────────────────────────────────────────────────────────────────


class AuditLog:                                             # 세션 하나의 감사 기록기
    """record(kind, **필드) 로 한 줄 쓴다. on_event 는 루프 이벤트 수신자, wrap_check·wrap_ask 는 권한 판정·승인 선택을 남기는 포장.
    server 가 있으면 outbox 에 쌓고 백그라운드 스레드가 보낸다."""

    def __init__(self, global_dir: Union[str, os.PathLike], *, ui: str,   # 전역 폴더·화면 종류
                 session_id: Union[str, Callable[[], str]] = "",   # 세션 id(글 또는 매번 읽는 함수)
                 mode: Union[str, Callable[[], str]] = "",   # 권한 모드(글 또는 함수)
                 server: Optional[str] = None, token: Optional[str] = None,   # 서버 주소·개인 토큰
                 batch: int = DEFAULT_BATCH, now: Callable[[], dt.datetime] = _utc_now,   # 묶음 크기·시계
                 post: Optional[Callable[[str, dict, dict], int]] = None):   # (url, 본문, 헤더) → 상태 코드(시험용 대체)
        self.dir = pathlib.Path(global_dir) / AUDIT_DIR      # 기록 폴더
        self.ui = ui                                         # headless|interactive|acp
        self._session = session_id                           # 세션 id
        self._mode = mode                                    # 모드
        self.server = server.rstrip("/") if server else None   # 서버 주소
        self.token = token                                   # 개인 토큰
        self.batch = max(1, int(batch or DEFAULT_BATCH))     # 묶음 크기
        self._now = now                                      # 시계
        self._post = post or _http_post                      # 전송 함수
        self._lock = threading.Lock()                        # 파일 쓰기 잠금(프로세스 안)
        self._send_lock = threading.Lock()                   # 전송은 한 번에 하나
        self._seen_calls: set = set()                        # tool_call 을 이미 남긴 호출 id(tool_start 가 두 번 올 수 있음)
        self._since_flush = 0                                # 마지막 전송 신호 뒤 쌓인 수
        self._wake = threading.Event()                       # 전송 신호
        self._closing = False                                # 끝내는 중
        self._worker: Optional[threading.Thread] = None      # 전송 스레드(처음 필요할 때 시작)
        self.closed = False                                  # close 했나
        if self.server:                                      # 서버가 있으면
            atexit.register(self.close)                      # 프로세스가 끝날 때 마지막 전송(ACP 처럼 close 를 안 부르는 경로)
            if (self.dir / OUTBOX).is_file():                # 앞 실행에서 못 보낸 것이 있으면
                self._signal()                               # 바로 다시 보낸다

    # ── 공통 필드 ──
    def _value(self, v: Any) -> str:                         # 글 또는 함수 → 글
        """함수면 불러서(실패하면 빈 글)."""
        if callable(v):                                      # 함수면
            try:                                            # 읽기 실패 대비
                return str(v() or "")                        # 호출 결과
            except Exception:                                # 실패
                return ""                                   # 빈 글
        return str(v or "")                                  # 글

    def record(self, kind: str, **fields: Any) -> Optional[dict]:   # 사건 하나 쓰기
        """한 줄 JSON 을 그날(UTC) 파일에 덧붙인다. 서버가 있으면 outbox 에도. 실패는 로그만(대화를 막지 않음)."""
        if self.closed:                                      # 이미 닫힘
            return None                                     # 무시
        now = self._now()                                    # 지금(UTC)
        ev = {"id": uuid.uuid4().hex, "ts": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),   # 사건 id·시각
              "session": self._value(self._session), "ui": self.ui, "mode": self._value(self._mode), "kind": kind}   # 세션·화면·모드·종류
        ev.update({k: v for k, v in fields.items() if v is not None})   # 사건별 필드(None 은 뺌)
        line = (json.dumps(ev, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")   # 한 줄(바이트 — Windows 줄바꿈 변환 없음)
        try:                                                # 쓰기 실패 무시
            with self._lock:                                 # 프로세스 안 동시 쓰기 방지
                self.dir.mkdir(parents=True, exist_ok=True)  # 폴더
                with open(self.dir / f"{now:%Y-%m-%d}.jsonl", "ab") as f:   # 그날 파일(덧붙이기)
                    f.write(line)                            # 한 줄
                if self.server:                              # 서버로도 보낼 것
                    with open(self.dir / OUTBOX, "ab") as f:  # 보낼 목록
                        f.write(line)                        # 한 줄
        except OSError as e:                                 # 디스크 오류
            log.warning("audit: cannot write — %s", e)       # 로그
            return ev                                       # 사건은 돌려줌
        if self.server:                                      # 전송 판단
            self._since_flush += 1                           # 쌓인 수
            if self._since_flush >= self.batch or kind == "turn_end":   # 묶음이 찼거나 턴이 끝났으면
                self._signal()                               # 보내기
        return ev                                           # 쓴 사건

    # ── 루프 이벤트(ncn/loop/events.py + agent.py turn_start·turn_end) ──
    def on_event(self, e: dict) -> None:                     # 이벤트 수신자(EventSink)
        """글 조각·승인 요청은 남기지 않는다(승인은 wrap_ask, 판정은 wrap_check 가 남김)."""
        t = e.get("type")                                    # 종류
        if t == "turn_start":                                # 턴 시작
            self.record("turn_start")                        # 한 줄
        elif t == "turn_end":                                # 턴 끝
            self.record("turn_end", aborted=e.get("aborted"), stopped_by_rejection=e.get("stopped_by_rejection"),   # 멈춘 이유들
                        loop_guard=e.get("stopped_by_loop_guard"), error=first_line(e["error"]) if e.get("error") else None)   # 반복 감시·오류 첫 줄
        elif t == "tool_start":                              # 도구 호출(권한 확인 직전)
            tcid = e.get("tool_call_id")                     # 호출 id
            if tcid in self._seen_calls:                     # 이미 남김(사전 처리 실패 뒤 한 번 더 올 수 있음)
                return                                      # 한 번만
            self._seen_calls.add(tcid)                       # 기억
            self.record("tool_call", tool=e.get("name"), tool_call_id=tcid, args=summarize_args(e.get("name"), e.get("arguments")))   # 이름·인자 요약
        elif t == "tool_result":                             # 실행 성공·거부
            self.record("tool_result", tool=e.get("name"), tool_call_id=e.get("tool_call_id"), status=e.get("status"))   # 결과 글은 안 남김
        elif t == "tool_error":                              # 실패
            self.record("tool_error", tool=e.get("name"), tool_call_id=e.get("tool_call_id"), error=first_line(e.get("error")))   # 오류 첫 줄
        elif t == "usage":                                   # 토큰 사용량
            u = e.get("usage") if isinstance(e.get("usage"), dict) else {}   # 사용량 dict
            self.record("usage", model=u.get("model"), prompt_tokens=u.get("prompt_tokens"),   # 모델·입력
                        completion_tokens=u.get("completion_tokens"), total_tokens=u.get("total_tokens"))   # 출력·합계
        elif t == "retry":                                   # LLM 재시도
            self.record("retry", attempt=e.get("attempt"), error=first_line(e.get("error")))   # 몇 번째·오류 첫 줄
        elif t == "system_message":                          # 압축 안내(압축 전 토큰 수 포함)·반복 감시 안내
            self.record("system", message=first_line(e.get("message")))   # 첫 줄

    def loop_decision(self, reason: str, decision: str, text: str) -> None:   # [E5] 반복 감시 결정(LoopGuard on_decision)
        """이유(max_iterations|repeat_failure|policy_max_iterations)·결정(continue|stop)."""
        self.record("loop_guard", reason=reason, decision=decision)   # 질문 글은 남기지 않음(이유로 충분)

    # ── 권한 판정·승인 선택 포장 ──
    def wrap_check(self, fn: Callable[[Any, Any], Any], perms: Any = None) -> Callable[[Any, Any], Any]:   # check_permission 포장
        """판정 결과(allow|ask|exclude)와 누가 정했는지(by)를 남긴다. 판정 자체는 그대로."""
        async def check(tool: Any, args: Any) -> Any:        # 포장된 판정(루프가 await)
            if perms is not None and hasattr(perms, "last_decision_source"):   # 지난 판정 출처가 남아 있으면
                try:                                        # 쓰기 실패 대비(읽기 전용 속성 등)
                    perms.last_decision_source = None       # 비워 둠(이번 판정이 다시 채움 — 묵은 값 방지)
                except Exception:                           # 실패
                    pass                                    # 무시(기록은 대체 판정으로)
            decision = await _maybe_await(fn(tool, args))     # 원래 판정
            self.record("permission", tool=getattr(tool, "name", None), decision=decision,   # 도구·판정
                        by=decision_source(perms, tool, args, decision))   # 누가
            return decision                                 # 그대로 돌려줌
        return check                                        # 포장 함수

    def wrap_ask(self, fn: Optional[Callable]) -> Optional[Callable]:   # ask_user 포장(None 이면 None — 헤드리스)
        """사용자 선택(allow|allow_always|reject)을 남긴다."""
        if fn is None:                                       # 묻는 화면이 없으면
            return None                                     # 그대로 None(루프는 거부로 봄)

        async def ask(name: str, args: Any, preview: Any, tool_call_id: str) -> Any:   # 포장된 승인 질문
            answer = await _maybe_await(fn(name, args, preview, tool_call_id))   # 원래 질문
            choice = ("allow" if answer else "reject") if isinstance(answer, bool) else str(answer).lower()   # 선택 이름
            self.record("approval", tool=name, tool_call_id=tool_call_id, choice=choice, by="user")   # 사용자 선택
            return answer                                   # 그대로 돌려줌
        return ask                                          # 포장 함수

    # ── 서버 전송 ──
    def _signal(self) -> None:                               # 전송 스레드 깨우기
        """처음이면 스레드를 시작한다(daemon — 프로세스 종료를 막지 않음)."""
        if not self.server or self._closing:                 # 서버 없음·끝내는 중
            return                                          # 무시
        self._since_flush = 0                                # 쌓인 수 초기화
        if self._worker is None:                             # 아직 스레드가 없으면
            self._worker = threading.Thread(target=self._run, name="ncn-audit-sender", daemon=True)   # 전송 스레드
            self._worker.start()                             # 시작
        self._wake.set()                                     # 깨움

    def _run(self) -> None:                                  # 전송 스레드 본체
        """신호가 올 때마다 outbox 를 보낸다. 끝내는 중이면 한 번 더 보내고 끝."""
        while True:                                          # 계속
            self._wake.wait()                                # 신호 기다림
            self._wake.clear()                               # 신호 지움
            self.send_pending()                              # 보내기
            if self._closing:                                # 끝내는 중이면
                return                                      # 스레드 끝

    def _claim(self) -> list[pathlib.Path]:                  # 보낼 파일 가져오기(다른 프로세스와 겹치지 않게 이름 바꿈)
        """outbox.jsonl → outbox.<pid>-<임의>.sending. 오래된 .sending(앞 프로세스가 죽음)도 함께 가져온다."""
        claimed: list[pathlib.Path] = []                     # 가져온 파일
        src = self.dir / OUTBOX                              # 보낼 목록
        if src.is_file():                                    # 있으면
            dst = self.dir / f"outbox.{os.getpid()}-{uuid.uuid4().hex[:8]}.sending"   # 이 프로세스 몫 이름
            try:                                            # Windows 에서 다른 프로세스가 쓰는 중이면 실패
                os.replace(src, dst)                         # 이름 바꿔 가져옴
                claimed.append(dst)                          # 내 몫
            except OSError:                                  # 실패
                pass                                        # 다음 기회에
        cutoff = time.time() - STALE_SENDING_SECONDS         # 오래된 기준
        for p in self.dir.glob("outbox.*.sending"):          # 남은 전송 파일
            try:                                            # 사라졌을 수 있음
                if p not in claimed and p.stat().st_mtime < cutoff:   # 오래됐으면(주인이 죽음)
                    fresh = p.with_name(f"outbox.{os.getpid()}-{uuid.uuid4().hex[:8]}.sending")   # 새 이름(수정 시각 갱신)
                    os.replace(p, fresh)                     # 가져옴
                    os.utime(fresh, None)                    # 지금 시각으로
                    claimed.append(fresh)                    # 내 몫
            except OSError:                                  # 다른 프로세스가 먼저 가져감
                continue                                    # 다음
        return claimed                                      # 가져온 파일들

    def send_pending(self) -> int:                           # outbox 를 서버로(보낸 개수)
        """batch 개씩 POST. 실패하면 못 보낸 줄을 outbox 에 되돌린다(다음에 다시). 서버 쪽은 id 로 중복을 버린다."""
        if not self.server:                                  # 서버 없음
            return 0                                        # 할 일 없음
        with self._send_lock:                                # 한 번에 하나
            files = self._claim()                            # 보낼 파일
            lines: list[bytes] = []                          # 보낼 줄
            for p in files:                                  # 파일마다
                try:                                        # 읽기
                    lines += [ln for ln in p.read_bytes().splitlines() if ln.strip()]   # 빈 줄 제외
                except OSError:                              # 못 읽음
                    pass                                    # 건너뜀
            sent = 0                                         # 보낸 줄 수
            headers = {"Content-Type": "application/json"}   # 요청 머리
            if self.token:                                   # 토큰이 있으면
                headers["Authorization"] = f"Bearer {self.token}"   # 개인 토큰
            try:                                            # 전송 실패는 되돌리기
                while sent < len(lines):                     # 남은 줄이 있으면
                    chunk = lines[sent:sent + self.batch]    # 한 묶음
                    events = []                              # 사건 목록
                    for ln in chunk:                         # 줄마다
                        try:                                # 깨진 줄 대비
                            events.append(json.loads(ln.decode("utf-8")))   # 사건
                        except ValueError:                   # 깨진 줄(쓰다 끊김)
                            continue                        # 버림
                    status = self._post(self.server + ENDPOINT, {"events": events}, headers)   # 보내기
                    if not 200 <= status < 300:              # 실패(인증·서버 오류)
                        log.warning("audit: server returned %s — will retry later", status)   # 로그
                        break                               # 나머지는 다음에
                    sent += len(chunk)                       # 보냄
            except Exception as e:                           # 연결 실패 등
                log.warning("audit: send failed — %s", e)    # 로그
            rest = lines[sent:]                              # 못 보낸 줄
            try:                                            # 되돌리기·정리
                if rest:                                     # 남은 게 있으면
                    with self._lock:                         # 쓰기 잠금
                        with open(self.dir / OUTBOX, "ab") as f:   # outbox 뒤에
                            f.write(b"".join(ln + b"\n" for ln in rest))   # 되돌림
                for p in files:                              # 가져온 파일
                    p.unlink()                               # 지움
            except OSError as e:                             # 디스크 오류
                log.warning("audit: cannot restore outbox — %s", e)   # 로그
            return sent                                     # 보낸 개수

    def close(self, timeout: float = CLOSE_WAIT) -> None:    # 끝 — 마지막 전송을 잠깐 기다림
        """서버가 있으면 남은 것을 보내고 최대 timeout 초 기다린다(못 보내면 outbox 에 남아 다음 실행이 보냄)."""
        if self.closed:                                      # 이미 닫힘
            return                                          # 끝
        self.closed = True                                   # 닫힘 표시(이후 record 무시)
        if not self.server:                                  # 서버 없음
            return                                          # 끝
        try:                                                # atexit 등록 해제
            atexit.unregister(self.close)                    # 두 번 부르지 않게
        except Exception:                                    # 무시
            pass                                            # 무시
        if self._worker is None:                             # 스레드가 없었으면
            self._signal()                                   # 시작해서 보냄
        self._closing = True                                 # 끝내는 중
        self._wake.set()                                     # 깨움
        if self._worker is not None:                         # 스레드가 있으면
            self._worker.join(timeout)                       # 잠깐 기다림(대화는 이미 끝남)


def _http_post(url: str, body: dict, headers: dict) -> int:   # 실제 전송(httpx 동기 — 전송 스레드 안)
    """상태 코드를 돌려준다. 연결 실패는 예외."""
    import httpx                                             # 지연 import
    with httpx.Client(timeout=SEND_TIMEOUT, trust_env=False) as c:   # 사내 서버 직통(프록시 환경변수 무시)
        return c.post(url, json=body, headers=headers).status_code   # 상태 코드


def decision_source(perms: Any, tool: Any, args: Any, decision: Any) -> str:   # 권한 판정을 누가 정했나
    """perms.last_decision_source(E1·E2 가 채우면 그 값 — 예: "E1", "E2")를 먼저 쓴다.
    없으면 정적 정책(첫 일치)과 같으면 "policy", 다르면 Bash 위험 판정("bash_security") 또는 도구의 동적 판정("dynamic")."""
    src = getattr(perms, "last_decision_source", None)       # 다른 기능이 남긴 출처
    if isinstance(src, str) and src:                         # 있으면
        return src                                          # 그대로
    base = getattr(perms, "base_permission", None)           # 정적 판정 함수
    if not callable(base):                                   # 없으면
        return "policy"                                     # 정책으로 본다
    try:                                                    # 판정 실패 대비
        static = base(getattr(tool, "name", ""), args if isinstance(args, dict) else {})   # 정적 정책(인자 포함 첫 일치)
    except Exception:                                        # 실패
        return "policy"                                     # 정책으로 본다
    if static == decision:                                   # 같으면
        return "policy"                                     # 정책표(permissions.yaml·--allow·모드)
    return "bash_security" if getattr(tool, "name", "") == "Bash" else "dynamic"   # 동적 판정이 바꿈


def _read_token(token_env: str, global_dir: Any, environ: Any) -> Optional[str]:   # 개인 토큰 찾기
    """환경변수(작업 폴더 .env 반영됨) → <전역 폴더>/.env 순서."""
    env = os.environ if environ is None else environ         # 환경변수
    v = env.get(token_env)                                   # 환경변수 값
    if v:                                                    # 있으면
        return v                                            # 그 값
    if global_dir is None:                                   # 전역 폴더를 모르면
        return None                                         # 없음
    from ncn.config.env import read_dotenv_file              # .env 읽기
    d = read_dotenv_file(os.fspath(pathlib.Path(global_dir) / ".env")) or {}   # ~/.continue/.env
    return d.get(token_env) or None                          # 값(없으면 None)


def make_audit(global_dir: Any, environ: Any, settings: Any, *, ui: str,   # 설정 → 기록기(꺼져 있으면 None)
               session_id: Union[str, Callable[[], str]] = "", mode: Union[str, Callable[[], str]] = "") -> Optional[AuditLog]:   # 세션·모드
    """NCN_CN_COMPAT=1 이거나 audit.enabled: false(정책이 켜지 않음)면 None. 서버 주소가 있으면 토큰을 찾아 전송도 켠다."""
    if cn_compat() or global_dir is None:                    # cn 원래 동작·전역 폴더 모름
        return None                                         # 기록 안 함
    cfg = effective_audit_config(settings, global_dir, environ)   # 사용자 설정 + 관리자 정책
    if not cfg.audit_enabled:                                # 꺼짐
        return None                                         # 기록 안 함
    token = _read_token(cfg.audit_token_env, global_dir, environ) if cfg.audit_server else None   # 개인 토큰
    if cfg.audit_server and not token:                       # 서버는 있는데 토큰이 없으면
        log.warning("audit: %s not set — events stay in %s/%s", cfg.audit_token_env, AUDIT_DIR, OUTBOX)   # 로그(서버가 401 → outbox 에 남음)
    return AuditLog(global_dir, ui=ui, session_id=session_id, mode=mode, server=cfg.audit_server,   # 기록기
                    token=token, batch=cfg.audit_batch)      # (이어짐)


def fan_out(*sinks: Optional[Callable[[dict], None]]) -> Callable[[dict], None]:   # 이벤트 수신자 여러 개를 하나로
    """None 은 건너뛴다. 하나가 실패해도 나머지는 받는다(감사·감시 오류가 화면을 깨지 않게)."""
    live = [s for s in sinks if s is not None]               # 있는 것만
    if len(live) == 1:                                       # 하나뿐이면
        return live[0]                                      # 그대로(화면만 — 원래와 같은 객체)

    def sink(e: dict) -> None:                               # 합친 수신자
        for s in live:                                       # 차례대로
            try:                                            # 각자 실패 격리
                s(e)                                        # 전달
            except Exception:                                # 실패
                log.exception("event sink failed")           # 로그만
    return sink                                             # 합친 수신자
