# agent_server/audit.py — ncn 감사 기록 받기(POST /audit/events). 개인 토큰(Bearer)으로 사용자 확인, ncn_audit 테이블에 한 사건 = 한 줄
# ncn(newcontinue/ncn/audit.py)이 묶어 보낸다: {"events": [{id, ts, session, ui, mode, kind, tool, ...}, ...]}
# 같은 사건(id)이 다시 와도 한 번만 저장한다(ncn은 전송 실패 시 다시 보냄). 코드 원문은 ncn이 보내지 않는다(경로·명령 첫 줄·인자 이름만)
import datetime as dt                                        # 사건 시각 해석
import json                                                  # 나머지 필드 → JSONB

from fastapi import APIRouter, Request                       # 라우터
from fastapi.responses import JSONResponse                   # 응답

from agent_server import auth                                # 인증

MAX_EVENTS = 500                                             # 한 요청에 받는 최대 사건 수(ncn 기본 묶음은 20)
MAX_DETAIL = 4000                                            # 나머지 필드(JSON 글) 최대 길이 — 넘으면 잘라 둔다
COLUMNS = ("id", "ts", "session", "ui", "mode", "kind", "tool")   # 따로 열로 두는 필드(나머지는 detail)


def _text(v, n: int) -> str:
    """글로 바꿔 n자까지(None은 빈 글)."""
    return "" if v is None else str(v)[:n]


def _ts(v) -> dt.datetime:
    """ncn의 '2026-10-30T12:34:56.789Z' → 시각. 못 읽으면 지금(받은 시각)."""
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))   # Python 3.11+는 Z도 읽지만 명시적으로
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)   # 시간대 없으면 UTC로 본다
    except ValueError:
        return dt.datetime.now(dt.timezone.utc)


def _decision(e: dict) -> str:
    """사건 종류별 '결정' 한 단어: 권한 판정(allow/ask/exclude)·사용자 선택·도구 결과 상태·반복 감시 결정."""
    for k in ("decision", "choice", "status"):               # 먼저 있는 것
        if e.get(k) is not None:
            return _text(e[k], 40)
    return ""


def to_row(user: str, e: dict) -> tuple | None:
    """사건 하나 → INSERT 값. id·kind가 없으면 None(버림)."""
    if not isinstance(e, dict) or not e.get("id") or not e.get("kind"):
        return None
    rest = {k: v for k, v in e.items() if k not in COLUMNS}  # 나머지(args 요약·토큰 수·by 등)
    detail = json.dumps(rest, ensure_ascii=False)[:MAX_DETAIL]
    try:
        json.loads(detail)                                   # 잘라서 깨졌으면
    except ValueError:
        detail = json.dumps({"truncated": True})             # 표시만 남긴다
    return (_text(e["id"], 64), _ts(e.get("ts")), user, _text(e.get("session"), 100), _text(e.get("ui"), 20),
            _text(e.get("mode"), 20), _text(e["kind"], 40), _text(e.get("tool"), 200), _decision(e), detail)


def make_router(get_state) -> APIRouter:
    router = APIRouter()

    @router.post("/audit/events")
    async def events(request: Request):
        st = get_state()
        user, _ = await auth.lookup(st, auth.bearer(request.headers))   # 신원은 토큰으로만(본문의 사용자 값은 믿지 않음)
        if not user:
            return JSONResponse({"error": "유효한 개인 토큰이 필요합니다"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "JSON 본문이 필요합니다"}, status_code=400)
        items = body.get("events") if isinstance(body, dict) else None
        if not isinstance(items, list) or len(items) > MAX_EVENTS:
            return JSONResponse({"error": f"events 목록(최대 {MAX_EVENTS}개)이 필요합니다"}, status_code=400)
        rows = [r for r in (to_row(user, e) for e in items) if r is not None]   # 형식이 틀린 사건은 버림
        if rows:
            async with st.db.connection() as con:
                async with con.cursor() as cur:
                    await cur.executemany(
                        """INSERT INTO ncn_audit(event_id, ts, user_id, session_id, ui, mode, kind, tool, decision, detail)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                           ON CONFLICT (event_id) DO NOTHING""", rows)   # 다시 온 사건은 무시(재전송 안전)
        return {"received": len(items), "stored": len(rows)}  # stored = 형식이 맞은 수(중복 포함)

    return router
