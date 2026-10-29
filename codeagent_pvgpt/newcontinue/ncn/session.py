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
import time                                                  # Date.now()
import uuid                                                  # uuid v4
from typing import Any, Mapping, Optional                    # 타입

from ncn.config.paths import global_dir as _global_dir, node_homedir   # 전역 폴더·홈

log = logging.getLogger("ncn.session")                       # 로거

DEFAULT_SESSION_TITLE = "Untitled Session"                   # constants/session.ts
NEW_SESSION_TITLE = "New Session"                            # core/util/constants.ts(불러오기 실패 시 제목)


def _empty_usage() -> dict:
    """SessionUsage 초기값(키 순서 cn 그대로)."""
    return {"totalCost": 0, "promptTokens": 0, "completionTokens": 0,
            "promptTokensDetails": {"cachedTokens": 0, "cacheWriteTokens": 0}}


def _dumps2(value: Any) -> str:
    """JSON.stringify(value, undefined, 2) — 2칸 들여쓰기, 한글은 그대로."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _js_number(x: float) -> Any:
    """정수로 떨어지는 실수는 정수로(JS 숫자 표기 1.0 → 1)."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def sessions_folder(environ: Optional[Mapping[str, str]] = None) -> str:
    """core paths getSessionsFolderPath — <전역>/sessions(없으면 만듦). 저장·목록 쪽이 쓰는 경로."""
    gdir = str(_global_dir(environ))                         # CONTINUE_GLOBAL_DIR(상대경로는 cwd 기준) 또는 ~/.continue
    os.makedirs(gdir, exist_ok=True)                         # getContinueGlobalPath: 없으면 만듦
    p = os.path.join(gdir, "sessions")
    os.makedirs(p, exist_ok=True)                            # 없으면 만듦
    return p


def session_dir_for_read(environ: Optional[Mapping[str, str]] = None) -> str:
    """session.ts getSessionDir — --resume 이 훑는 폴더. CONTINUE_CLI_TEST+HOME 이면 $HOME/.continue/sessions(cn 그대로)."""
    env = os.environ if environ is None else environ
    if env.get("CONTINUE_CLI_TEST") and env.get("HOME"):     # 시험 모드(저장 경로와 어긋날 수 있음 — cn 그대로)
        d = os.path.join(env["HOME"], ".continue", "sessions")
    else:                                                    # CONTINUE_GLOBAL_DIR || homedir/.continue (상대경로 그대로)
        home = env.get("CONTINUE_GLOBAL_DIR") or os.path.join(node_homedir(env), ".continue")
        d = os.path.join(home, "sessions")
    os.makedirs(d, exist_ok=True)                            # 없으면 만듦
    return d


class SessionManager:
    """session.ts SessionManager — 현재 세션 하나와 누적 사용량."""

    def __init__(self, environ: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None):
        self.environ = os.environ if environ is None else environ   # process.env
        self.cwd = cwd or os.getcwd()                        # process.cwd()
        self.current: Optional[dict] = None                  # 현재 세션
        self.usage = _empty_usage()                          # 누적 사용량

    # ── 세션 고르기 ──
    def get_current(self) -> dict:
        """getCurrentSession — 없으면 새로(CONTINUE_CLI_TEST_SESSION_ID 가 있으면 그 id)."""
        if self.current is None:
            sid = self.environ.get("CONTINUE_CLI_TEST_SESSION_ID") or str(uuid.uuid4())
            self.current = {"sessionId": sid, "title": DEFAULT_SESSION_TITLE, "workspaceDirectory": self.cwd,
                            "history": [], "usage": dict(self.usage)}   # {...sessionUsage}(얕은 복사)
        return self.current

    def set_session(self, session: dict) -> None:
        """setSession — 세션 교체 + 사용량 맞추기."""
        self.current = session
        self.usage = dict(session["usage"]) if session.get("usage") else _empty_usage()

    def start_new(self, history: list) -> dict:
        """startNewSession — 새 id(uuid)로 세션 시작(--fork)."""
        self.current = None                                  # clear()
        self.usage = _empty_usage()
        s = {"sessionId": str(uuid.uuid4()), "title": DEFAULT_SESSION_TITLE, "workspaceDirectory": self.cwd,
             "history": history, "usage": _empty_usage()}
        self.set_session(s)
        return s

    # ── 갱신 ──
    def update_history(self, history: list) -> None:
        """updateSessionHistory — 기록 교체 후 저장."""
        self.get_current()["history"] = history
        self.save()

    def track_usage(self, usage: dict) -> None:
        """trackSessionUsage — LLM 사용량(OpenAI 형식)을 누적하고 저장. 비용은 cn 대체 계산($1/$2 per MTok)만(요금표 미이식)."""
        pt = usage.get("prompt_tokens") or 0                 # 입력 토큰
        ct = usage.get("completion_tokens") or 0             # 출력 토큰
        self.usage["totalCost"] = _js_number(self.usage.get("totalCost", 0) + (pt * 1 + ct * 2) / 1_000_000)   # calculateFallbackCost
        self.usage["promptTokens"] = self.usage.get("promptTokens", 0) + pt
        self.usage["completionTokens"] = self.usage.get("completionTokens", 0) + ct
        details = usage.get("prompt_tokens_details") or {}   # 캐시 토큰(있을 때만)
        if details.get("cache_read_tokens"):
            ptd = self.usage.setdefault("promptTokensDetails", {})
            ptd["cachedTokens"] = (ptd.get("cachedTokens") or 0) + details["cache_read_tokens"]
        if details.get("cache_write_tokens"):
            ptd = self.usage.setdefault("promptTokensDetails", {})
            ptd["cacheWriteTokens"] = (ptd.get("cacheWriteTokens") or 0) + details["cache_write_tokens"]
        self.get_current()["usage"] = dict(self.usage)       # {...sessionUsage}
        self.save()

    # ── 저장 ──
    def save(self) -> None:
        """saveSession — 내용이 있을 때만, system 빼고 user 에 editorState 붙여 HistoryManager.save. 실패는 기록만."""
        try:
            if self.current is None:                         # 세션 없음
                return
            s = self.current
            hist = s.get("history") or []
            if not any(it.get("message", {}).get("role") != "system" for it in hist):   # hasSessionContent
                return
            out_hist = []                                    # modifySessionBeforeSave
            for it in hist:
                if it.get("message", {}).get("role") == "system":
                    continue
                if it.get("message", {}).get("role") == "user":
                    it = {**it, "editorState": it["message"].get("content")}
                out_hist.append(it)
            history_manager_save({**s, "history": out_hist}, self.environ)
        except Exception as e:                               # logger.error("Error saving session:")
            log.error("Error saving session: %s", e)

    # ── 불러오기 ──
    def load_latest(self) -> Optional[dict]:
        """loadSession(--resume) — 폴더에서 sessions.json 을 뺀 *.json 중 수정 시각이 가장 최근인 것(작업 폴더로 거르지 않음 — cn 그대로)."""
        try:
            d = session_dir_for_read(self.environ)
            files = [f for f in os.listdir(d) if f.endswith(".json") and f != "sessions.json"]
            if not files:
                return None
            files.sort(key=lambda f: os.stat(os.path.join(d, f)).st_mtime, reverse=True)   # 최근 먼저
            with open(os.path.join(d, files[0]), encoding="utf-8") as fh:
                session = json.load(fh)
            self.set_session(session)                        # 이후 저장도 이 세션으로
            return session
        except Exception as e:                               # logger.error("Error loading session:")
            log.error("Error loading session: %s", e)
            return None


def history_manager_save(session: dict, environ: Optional[Mapping[str, str]] = None) -> None:
    """core HistoryManager.save — 세션 파일(키 순서 고정) + sessions.json 목록 갱신."""
    ordered: dict = {"sessionId": session.get("sessionId"), "title": session.get("title"),
                     "workspaceDirectory": session.get("workspaceDirectory"), "history": session.get("history")}
    if session.get("mode"):                                  # if (session.mode)
        ordered["mode"] = session["mode"]
    if "chatModelTitle" in session:                          # !== undefined
        ordered["chatModelTitle"] = session["chatModelTitle"]
    if "usage" in session:
        ordered["usage"] = session["usage"]
    folder = sessions_folder(environ)
    with open(os.path.join(folder, f"{session.get('sessionId')}.json"), "w", encoding="utf-8", newline="") as fh:
        fh.write(_dumps2(ordered))                           # 세션 파일
    list_path = os.path.join(folder, "sessions.json")        # getSessionsListPath: 없으면 "[]"
    if not os.path.exists(list_path):
        with open(list_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("[]")
    with open(list_path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        sessions_list = json.loads(raw)
    except ValueError as e:
        if raw.strip() == "":                                # 빈 파일이면 []로
            sessions_list = []
        else:                                                # cn 원문 오류 글
            raise ValueError(f"It looks like there is a JSON formatting error in your sessions.json file ({list_path}). "
                             "Please fix this before creating a new session.") from e
    message_count = sum(1 for it in session.get("history") or [] if it.get("message", {}).get("role") == "assistant")
    found = False
    for meta in sessions_list:                               # 있으면 갱신
        if meta.get("sessionId") == session.get("sessionId"):
            meta["title"] = session.get("title")
            meta["workspaceDirectory"] = session.get("workspaceDirectory")
            meta["messageCount"] = message_count
            found = True
            break
    if not found:                                            # 없으면 추가
        sessions_list.append({"sessionId": session.get("sessionId"), "title": session.get("title"),
                              "dateCreated": str(int(time.time() * 1000)),   # String(Date.now())
                              "workspaceDirectory": session.get("workspaceDirectory"), "messageCount": message_count})
    with open(list_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(_dumps2(sessions_list))


def history_manager_load(session_id: str, environ: Optional[Mapping[str, str]] = None) -> dict:
    """core HistoryManager.load — 없거나 깨졌으면 빈 세션(제목 "New Session")을 돌려준다(예외 없음 — cn 그대로).
    그래서 cn의 --fork 는 없는 id여도 "not found"로 끝나지 않고 빈 기록으로 시작한다(cn 버그 후보)."""
    try:
        p = os.path.join(sessions_folder(environ), f"{session_id}.json")
        if not os.path.exists(p):
            raise FileNotFoundError(f"Session file {p} does not exist")
        with open(p, encoding="utf-8") as fh:
            session = json.load(fh)
        session["sessionId"] = session_id
        return session
    except Exception as e:                                   # console.log(`Error loading session: ${e}`) — 헤드리스에선 안 보임
        log.debug("Error loading session: %s", e)
        return {"history": [], "title": NEW_SESSION_TITLE, "workspaceDirectory": "", "sessionId": session_id}
