# agent_server/auth.py — 개인 토큰 → 사번. 신원은 HTTP Authorization 헤더에서만 정한다(모델이 넘긴 인자는 믿지 않음)
import time                                                  # 캐시 만료 시각

from common.db import hash_token                             # 토큰 → SHA-256

_CACHE: dict[str, tuple[float, str | None, bool]] = {}       # 토큰해시 → (만료 시각, 사번 또는 None, 관리자 여부)
CACHE_SECONDS = 60                                           # 같은 토큰을 매 요청 DB에서 찾지 않게 60초 기억(폐기 반영도 최대 60초)


def bearer(headers) -> str:
    """'Authorization: Bearer xxx' 헤더에서 xxx만 꺼낸다(없으면 빈 문자열)."""
    auth = headers.get("authorization", "")                  # 헤더 이름은 대소문자 무관
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""   # 'Bearer ' 다음 부분


async def lookup(st, token: str) -> tuple[str | None, bool]:
    """토큰 → (사번, 관리자 여부). 없거나 폐기·비활성이면 (None, False)."""
    if not token:                                            # 토큰이 없으면
        return None, False
    h = hash_token(token)                                    # DB에는 해시만 있다
    hit = _CACHE.get(h)                                      # 캐시 확인
    if hit and hit[0] > time.time():                         # 아직 유효하면
        return hit[1], hit[2]
    async with st.db.connection() as con:                    # DB 조회
        cur = await con.execute(
            """SELECT u.user_id, u.is_admin FROM api_tokens t
               JOIN users u ON u.user_id = t.user_id
               WHERE t.token_hash = %s AND t.revoked_at IS NULL AND u.active""", (h,))   # 유효 토큰 + 활성 사용자
        row = await cur.fetchone()
        if row:                                              # 찾았으면 마지막 사용 시각 갱신(감시용)
            await con.execute("UPDATE api_tokens SET last_used_at = now() WHERE token_hash = %s", (h,))
    user, admin = (row[0], bool(row[1])) if row else (None, False)
    _CACHE[h] = (time.time() + CACHE_SECONDS, user, admin)   # 결과 캐시(실패도 캐시 — 잘못된 토큰 폭주 방지)
    return user, admin


def clear_cache() -> None:
    """테스트·토큰 폐기 직후 즉시 반영용."""
    _CACHE.clear()
