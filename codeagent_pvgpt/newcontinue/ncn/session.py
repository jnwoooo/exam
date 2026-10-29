# ncn/session.py — 대화 세션 저장·불러오기(--resume / --fork). cn extensions/cli/src/session.ts(SessionManager·saveSession·loadSession·
#   loadSessionById·startNewSession·trackSessionUsage) + core/util/history.ts(HistoryManager.save·load) + core/util/paths.ts(세션 경로)
# 위치: <전역>/sessions/<sessionId>.json + 목록 sessions/sessions.json. 형식은 JSON.stringify(…, undefined, 2), 키 순서 cn 그대로
#   (sessionId, title, workspaceDirectory, history, [mode], [chatModelTitle], [usage]) — cn이 저장한 세션을 ncn이 이어 쓸 수 있게.
# 헤드리스 저장 시점(cn과 같음): 응답이 끝날 때마다(update_history) + 사용량을 받을 때마다(track_usage). system 항목은 빼고,
#   user 항목에는 editorState(= content 복사)를 붙인다. system 아닌 항목이 없으면 저장하지 않는다.
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 파일 형식
import logging                                               # 오류 기록(stdout 금지)
import os                                                    # 환경변수·현재 폴더
import time                                                  # Date.now() — 저장 시각(밀리초)
import uuid                                                  # uuid v4 — 세션 id 생성
from typing import Any, Mapping, Optional                    # 타입

from ncn.config.paths import global_dir as _global_dir, node_homedir   # 전역 폴더·홈

log = logging.getLogger("ncn.session")                       # 로거

DEFAULT_SESSION_TITLE = "Untitled Session"                   # constants/session.ts — 기본 세션 제목
NEW_SESSION_TITLE = "New Session"                            # core/util/constants.ts(불러오기 실패 시 제목)


def _empty_usage() -> dict:                                 # 누적 사용량 초기값 함수
    """SessionUsage 초기값(키 순서 cn 그대로)."""
    return {"totalCost": 0, "promptTokens": 0, "completionTokens": 0,  # 비용·토큰을 0 으로 시작(키 순서는 cn 과 같게)
            "promptTokensDetails": {"cachedTokens": 0, "cacheWriteTokens": 0}}  # 캐시 토큰 세부도 0


def _dumps2(value: Any) -> str:                             # JSON 2칸 들여쓰기 직렬화 함수
    """JSON.stringify(value, undefined, 2) — 2칸 들여쓰기, 한글은 그대로."""
    return json.dumps(value, ensure_ascii=False, indent=2)  # JSON.stringify(v, undefined, 2) 와 같은 출력


def _js_number(x: float) -> Any:                            # JS 숫자 표기 맞춤 함수
    """정수로 떨어지는 실수는 정수로(JS 숫자 표기 1.0 → 1)."""
    return int(x) if isinstance(x, float) and x.is_integer() else x  # 정수값 실수는 int 로(1.0 → 1)


def sessions_folder(environ: Optional[Mapping[str, str]] = None) -> str:  # 세션 저장 폴더 경로 함수
    """core paths getSessionsFolderPath — <전역>/sessions(없으면 만듦). 저장·목록 쪽이 쓰는 경로."""
    gdir = str(_global_dir(environ))                         # CONTINUE_GLOBAL_DIR(상대경로는 cwd 기준) 또는 ~/.continue
    os.makedirs(gdir, exist_ok=True)                         # getContinueGlobalPath: 없으면 만듦
    p = os.path.join(gdir, "sessions")                      # <전역>/sessions
    os.makedirs(p, exist_ok=True)                            # 없으면 만듦
    return p                                                # 폴더 경로 반환


def session_dir_for_read(environ: Optional[Mapping[str, str]] = None) -> str:  # --resume 이 읽는 폴더 경로 함수
    """session.ts getSessionDir — --resume 이 훑는 폴더. CONTINUE_CLI_TEST+HOME 이면 $HOME/.continue/sessions(cn 그대로)."""
    env = os.environ if environ is None else environ        # environ 이 없으면 실제 환경변수
    if env.get("CONTINUE_CLI_TEST") and env.get("HOME"):     # 시험 모드(저장 경로와 어긋날 수 있음 — cn 그대로)
        d = os.path.join(env["HOME"], ".continue", "sessions")  # 시험 모드: $HOME/.continue/sessions
    else:                                                    # CONTINUE_GLOBAL_DIR || homedir/.continue (상대경로 그대로)
        home = env.get("CONTINUE_GLOBAL_DIR") or os.path.join(node_homedir(env), ".continue")  # 전역 폴더 지정값 또는 <홈>/.continue
        d = os.path.join(home, "sessions")                  # sessions 하위 폴더
    os.makedirs(d, exist_ok=True)                            # 없으면 만듦
    return d                                                # 폴더 경로 반환


class SessionManager:                                       # 현재 세션과 누적 사용량을 들고 있는 관리자
    """session.ts SessionManager — 현재 세션 하나와 누적 사용량."""

    def __init__(self, environ: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None):  # 환경변수·작업 폴더를 받아 초기화
        self.environ = os.environ if environ is None else environ   # process.env — 환경변수(process.env)
        self.cwd = cwd or os.getcwd()                        # process.cwd() — 작업 폴더(process.cwd())
        self.current: Optional[dict] = None                  # 현재 세션
        self.usage = _empty_usage()                          # 누적 사용량

    # ── 세션 고르기 ──
    def get_current(self) -> dict:                          # 현재 세션 반환 함수
        """getCurrentSession — 없으면 새로(CONTINUE_CLI_TEST_SESSION_ID 가 있으면 그 id)."""
        if self.current is None:                            # 아직 없으면 새로 만든다
            sid = self.environ.get("CONTINUE_CLI_TEST_SESSION_ID") or str(uuid.uuid4())  # 시험용 id 가 있으면 그것, 없으면 새 uuid
            self.current = {"sessionId": sid, "title": DEFAULT_SESSION_TITLE, "workspaceDirectory": self.cwd,  # 새 세션 사전(제목 기본값, 기록 비어 있음)
                            "history": [], "usage": dict(self.usage)}   # {...sessionUsage}(얕은 복사)
        return self.current                                 # 현재 세션 반환

    def set_session(self, session: dict) -> None:           # 세션 교체 함수
        """setSession — 세션 교체 + 사용량 맞추기."""
        self.current = session                              # 현재 세션으로 지정
        self.usage = dict(session["usage"]) if session.get("usage") else _empty_usage()  # 세션에 사용량이 있으면 이어받고 없으면 초기값

    def start_new(self, history: list) -> dict:             # --fork 용 새 세션 시작 함수
        """startNewSession — 새 id(uuid)로 세션 시작(--fork)."""
        self.current = None                                  # clear()
        self.usage = _empty_usage()                         # 누적 사용량 초기화
        s = {"sessionId": str(uuid.uuid4()), "title": DEFAULT_SESSION_TITLE, "workspaceDirectory": self.cwd,  # 새 uuid 로 세션 구성
             "history": history, "usage": _empty_usage()}   # 넘겨받은 기록과 빈 사용량
        self.set_session(s)                                 # 새 세션으로 지정
        return s                                            # 새 세션 반환

    # ── 갱신 ──
    def update_history(self, history: list) -> None:        # 응답이 끝날 때마다 기록 교체 함수
        """updateSessionHistory — 기록 교체 후 저장."""
        self.get_current()["history"] = history             # 현재 세션의 기록을 교체
        self.save()                                         # 디스크에 저장

    def track_usage(self, usage: dict) -> None:             # LLM 사용량 누적 함수
        """trackSessionUsage — LLM 사용량(OpenAI 형식)을 누적하고 저장. 비용은 cn 대체 계산($1/$2 per MTok)만(요금표 미이식)."""
        pt = usage.get("prompt_tokens") or 0                 # 입력 토큰
        ct = usage.get("completion_tokens") or 0             # 출력 토큰
        self.usage["totalCost"] = _js_number(self.usage.get("totalCost", 0) + (pt * 1 + ct * 2) / 1_000_000)   # calculateFallbackCost — 비용: 입력 $1·출력 $2 per MTok 대체 계산
        self.usage["promptTokens"] = self.usage.get("promptTokens", 0) + pt  # 입력 토큰 누적
        self.usage["completionTokens"] = self.usage.get("completionTokens", 0) + ct  # 출력 토큰 누적
        details = usage.get("prompt_tokens_details") or {}   # 캐시 토큰(있을 때만)
        if details.get("cache_read_tokens"):                # 캐시 읽기 토큰이 있으면
            ptd = self.usage.setdefault("promptTokensDetails", {})  # promptTokensDetails 가 없으면 만든다
            ptd["cachedTokens"] = (ptd.get("cachedTokens") or 0) + details["cache_read_tokens"]  # 캐시 읽기 토큰 누적
        if details.get("cache_write_tokens"):               # 캐시 쓰기 토큰이 있으면
            ptd = self.usage.setdefault("promptTokensDetails", {})  # promptTokensDetails 가 없으면 만든다
            ptd["cacheWriteTokens"] = (ptd.get("cacheWriteTokens") or 0) + details["cache_write_tokens"]  # 캐시 쓰기 토큰 누적
        self.get_current()["usage"] = dict(self.usage)       # {...sessionUsage} — 세션에 사용량 사본 반영
        self.save()                                         # 디스크에 저장

    # ── 저장 ──
    def save(self) -> None:                                 # 세션 저장 함수(실패해도 예외를 밖으로 내지 않음)
        """saveSession — 내용이 있을 때만, system 빼고 user 에 editorState 붙여 HistoryManager.save. 실패는 기록만."""
        try:                                                # 저장 중 오류는 기록만 하고 넘어감
            if self.current is None:                         # 세션 없음
                return                                      # 세션이 없으면 저장하지 않음
            s = self.current                                # 현재 세션
            hist = s.get("history") or []                   # 기록 목록
            if not any(it.get("message", {}).get("role") != "system" for it in hist):   # hasSessionContent
                return                                      # system 아닌 항목이 없으면 저장하지 않음
            out_hist = []                                    # modifySessionBeforeSave
            for it in hist:                                 # 항목을 하나씩 훑음
                if it.get("message", {}).get("role") == "system":  # system 항목은 저장에서 뺀다
                    continue                                # 다음 항목으로
                if it.get("message", {}).get("role") == "user":  # user 항목에는
                    it = {**it, "editorState": it["message"].get("content")}  # editorState(= content 복사)를 붙인다
                out_hist.append(it)                         # 저장 목록에 추가
            history_manager_save({**s, "history": out_hist}, self.environ)  # 파일 저장 호출
        except Exception as e:                               # logger.error("Error saving session:") — 저장 실패 처리
            log.error("Error saving session: %s", e)        # 오류를 기록(stdout 에는 쓰지 않음)

    # ── 불러오기 ──
    def load_latest(self) -> Optional[dict]:                # --resume 용 최근 세션 불러오기 함수
        """loadSession(--resume) — 폴더에서 sessions.json 을 뺀 *.json 중 수정 시각이 가장 최근인 것(작업 폴더로 거르지 않음 — cn 그대로)."""
        try:                                                # 불러오기 중 오류는 기록만 하고 None
            d = session_dir_for_read(self.environ)          # 읽을 폴더
            files = [f for f in os.listdir(d) if f.endswith(".json") and f != "sessions.json"]  # sessions.json 을 뺀 *.json 목록
            if not files:                                   # 파일이 없으면
                return None                                 # None 반환
            files.sort(key=lambda f: os.stat(os.path.join(d, f)).st_mtime, reverse=True)   # 최근 먼저
            with open(os.path.join(d, files[0]), encoding="utf-8") as fh:  # 가장 최근 파일 열기
                session = json.load(fh)                     # JSON 으로 읽음
            self.set_session(session)                        # 이후 저장도 이 세션으로
            return session                                  # 불러온 세션 반환
        except Exception as e:                               # logger.error("Error loading session:") — 불러오기 실패 처리
            log.error("Error loading session: %s", e)       # 오류를 기록
            return None                                     # 실패 시 None


def history_manager_save(session: dict, environ: Optional[Mapping[str, str]] = None) -> None:  # 세션 파일과 목록 파일 저장 함수
    """core HistoryManager.save — 세션 파일(키 순서 고정) + sessions.json 목록 갱신."""
    ordered: dict = {"sessionId": session.get("sessionId"), "title": session.get("title"),  # 세션 파일의 키 순서를 cn 과 같게 고정
                     "workspaceDirectory": session.get("workspaceDirectory"), "history": session.get("history")}  # workspaceDirectory·history
    if session.get("mode"):                                  # if (session.mode) — mode 가 있으면
        ordered["mode"] = session["mode"]                   # mode 도 저장
    if "chatModelTitle" in session:                          # !== undefined — chatModelTitle 이 있으면(undefined 가 아니면)
        ordered["chatModelTitle"] = session["chatModelTitle"]  # chatModelTitle 저장
    if "usage" in session:                                  # usage 가 있으면
        ordered["usage"] = session["usage"]                 # usage 저장
    folder = sessions_folder(environ)                       # 세션 폴더
    with open(os.path.join(folder, f"{session.get('sessionId')}.json"), "w", encoding="utf-8", newline="") as fh:  # <sessionId>.json 을 쓰기 모드로 연다(줄바꿈 변환 없음)
        fh.write(_dumps2(ordered))                           # 세션 파일
    list_path = os.path.join(folder, "sessions.json")        # getSessionsListPath: 없으면 "[]"
    if not os.path.exists(list_path):                       # 목록 파일이 없으면
        with open(list_path, "w", encoding="utf-8", newline="") as fh:  # 새로 만들어
            fh.write("[]")                                  # 빈 배열로 시작
    with open(list_path, encoding="utf-8") as fh:           # 목록 파일 열기
        raw = fh.read()                                     # 내용 읽기
    try:                                                    # 목록 파싱 시도
        sessions_list = json.loads(raw)                     # JSON 파싱
    except ValueError as e:                                 # 파싱 실패 시
        if raw.strip() == "":                                # 빈 파일이면 []로
            sessions_list = []                              # 빈 목록으로 시작
        else:                                                # cn 원문 오류 글
            raise ValueError(f"It looks like there is a JSON formatting error in your sessions.json file ({list_path}). "  # cn 원문 오류 글로 예외를 올린다
                             "Please fix this before creating a new session.") from e
    message_count = sum(1 for it in session.get("history") or [] if it.get("message", {}).get("role") == "assistant")  # assistant 응답 개수(messageCount)
    found = False                                           # 목록에 이미 있는지 표시
    for meta in sessions_list:                               # 있으면 갱신
        if meta.get("sessionId") == session.get("sessionId"):  # 같은 sessionId 이면
            meta["title"] = session.get("title")            # 제목 갱신
            meta["workspaceDirectory"] = session.get("workspaceDirectory")  # 작업 폴더 갱신
            meta["messageCount"] = message_count            # 메시지 수 갱신
            found = True                                    # 찾았음
            break                                           # 더 볼 필요 없음
    if not found:                                            # 없으면 추가
        sessions_list.append({"sessionId": session.get("sessionId"), "title": session.get("title"),  # 새 항목 추가
                              "dateCreated": str(int(time.time() * 1000)),   # String(Date.now()) — 생성 시각을 밀리초 문자열로
                              "workspaceDirectory": session.get("workspaceDirectory"), "messageCount": message_count})  # 작업 폴더·메시지 수
    with open(list_path, "w", encoding="utf-8", newline="") as fh:  # 목록 파일 다시 쓰기
        fh.write(_dumps2(sessions_list))                    # JSON 으로 저장


def history_manager_load(session_id: str, environ: Optional[Mapping[str, str]] = None) -> dict:  # id 로 세션 불러오기 함수
    """core HistoryManager.load — 없거나 깨졌으면 빈 세션(제목 "New Session")을 돌려준다(예외 없음 — cn 그대로).
    그래서 cn의 --fork 는 없는 id여도 "not found"로 끝나지 않고 빈 기록으로 시작한다(cn 버그 후보)."""
    try:                                                    # 오류는 빈 세션으로 대체
        p = os.path.join(sessions_folder(environ), f"{session_id}.json")  # 세션 파일 경로
        if not os.path.exists(p):                           # 파일이 없으면
            raise FileNotFoundError(f"Session file {p} does not exist")  # FileNotFoundError 를 올려 아래 except 로 보냄
        with open(p, encoding="utf-8") as fh:               # 파일 열기
            session = json.load(fh)                         # JSON 으로 읽음
        session["sessionId"] = session_id                   # 파일 안 id 보다 요청 id 를 우선
        return session                                      # 불러온 세션 반환
    except Exception as e:                                   # console.log(`Error loading session: ${e}`) — 헤드리스에선 안 보임
        log.debug("Error loading session: %s", e)           # 오류를 디버그 기록
        return {"history": [], "title": NEW_SESSION_TITLE, "workspaceDirectory": "", "sessionId": session_id}  # 제목 "New Session" 인 빈 세션 반환
