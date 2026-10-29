# agent_server/usage.py — 사용량 한도(Redis)와 기록(Postgres). 코드 원문은 기록하지 않는다
import datetime as dt                                        # 오늘 날짜·현재 분


def estimate_tokens(text: str) -> int:
    """LLM이 usage를 주지 않을 때의 추정치 — 한국어·코드 섞인 글 기준 대략 3글자당 1토큰."""
    return max(1, len(text) // 3) if text else 0


def _keys(user: str) -> tuple[str, str]:
    """Redis 키 두 개: 이번 분의 요청 수, 오늘의 토큰 합계."""
    now = dt.datetime.now()                                  # 서버 로컬 시각 기준
    return f"rl:rpm:{user}:{now:%Y%m%d%H%M}", f"rl:tok:{user}:{now:%Y%m%d}"


async def check_and_count(st, user: str) -> str | None:
    """요청 하나를 세고 한도를 확인한다. 넘었으면 이유 문자열, 아니면 None."""
    rpm_key, tok_key = _keys(user)                           # 키
    n = await st.redis.incr(rpm_key)                         # 이번 분 요청 수 +1
    if n == 1:                                               # 처음 만든 키면
        await st.redis.expire(rpm_key, 90)                   # 90초 뒤 자동 삭제
    if n > st.settings.limit_rpm:                            # 분당 한도 초과
        return f"분당 요청 한도({st.settings.limit_rpm}) 초과 — 잠시 후 다시 시도하세요"
    used = int(await st.redis.get(tok_key) or 0)             # 오늘 쓴 토큰
    if used >= st.settings.limit_daily_tokens:               # 일일 한도 초과
        return f"일일 토큰 한도({st.settings.limit_daily_tokens:,}) 초과 — 관리자에게 문의하세요"
    return None


async def add_tokens(st, user: str, total: int) -> None:
    """오늘 토큰 합계에 더한다(응답이 끝난 뒤 실제 사용량으로)."""
    _, tok_key = _keys(user)
    await st.redis.incrby(tok_key, total)                    # 누적
    await st.redis.expire(tok_key, 2 * 86400)                # 이틀 뒤 자동 삭제(하루치만 필요)


async def record_llm(st, *, user: str, source: str, model: str, stream: bool, usage: dict | None,
                     est_prompt: int, est_completion: int, latency_ms: int, status: int, error: str = "") -> None:
    """LLM 호출 한 번을 기록한다. usage가 없으면 추정치를 쓰고 estimated=TRUE로 표시."""
    if usage and usage.get("total_tokens") is not None:      # LLM이 실제 수를 줬으면
        p, c = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        t, est = int(usage["total_tokens"]), False
    else:                                                    # 없으면 추정
        p, c, est = est_prompt, est_completion, True
        t = p + c
    if status < 400:                                         # 성공한 호출만 한도에 더한다
        await add_tokens(st, user, t)
    async with st.db.connection() as con:                    # Postgres 기록
        await con.execute(
            """INSERT INTO llm_usage(user_id, source, model, stream, prompt_tokens, completion_tokens,
                                     total_tokens, estimated, latency_ms, status, error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user, source, model, stream, p, c, t, est, latency_ms, status, error[:300]))


async def record_tool(st, *, user: str, tool: str, latency_ms: int, ok: bool, detail: str = "") -> None:
    """MCP 도구 호출 한 번을 기록한다(결과 개수·재시도 횟수 같은 요약만)."""
    async with st.db.connection() as con:
        await con.execute("INSERT INTO tool_calls(user_id, tool, latency_ms, ok, detail) VALUES (%s,%s,%s,%s,%s)",
                          (user, tool, latency_ms, ok, detail[:300]))
