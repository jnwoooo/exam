# agent_server/state.py — 코드에이전트 서버가 요청마다 함께 쓰는 연결들(앱 시작 시 열고 종료 시 닫음)
from dataclasses import dataclass                            # 묶음 클래스

import httpx                                                 # 외부 LLM·RAG 호출
from psycopg_pool import AsyncConnectionPool                 # Postgres 비동기 연결 풀

from common.settings import Settings                         # 설정


@dataclass
class AgentState:
    settings: Settings                                       # 환경변수 설정
    db: AsyncConnectionPool                                  # Postgres(토큰·사용량·감사)
    redis: object                                            # redis.asyncio 클라이언트(한도 카운터)
    http: httpx.AsyncClient                                  # 외부 호출용 HTTP 클라이언트(연결 재사용)


async def open_state(s: Settings) -> AgentState:
    """설정으로 연결들을 연다."""
    import redis.asyncio as aioredis                         # 비동기 Redis
    db = AsyncConnectionPool(s.database_url, min_size=1, max_size=10, open=False)   # 풀 만들기(아직 연결 안 함)
    await db.open()                                          # 연결 열기
    http = httpx.AsyncClient(timeout=httpx.Timeout(s.llm_timeout, connect=10.0))   # 읽기는 LLM 시간 제한, 연결은 10초
    return AgentState(s, db, aioredis.Redis.from_url(s.redis_url), http)


async def close_state(st: AgentState) -> None:
    """종료 시 연결들을 닫는다."""
    await st.http.aclose()                                   # HTTP 클라이언트
    await st.redis.aclose()                                  # Redis
    await st.db.close()                                      # Postgres 풀
