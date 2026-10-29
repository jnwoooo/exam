# agent_server/app.py — 코드에이전트 서버(:8200) 조립: /v1 LLM 프록시 + /mcp MCP 도구 + /admin 감시 + /audit/events(ncn 감사 기록)
# 실행: uv run python -m agent_server.app          (AGENT_HOST·AGENT_PORT 설정 사용)
#   또는 uv run uvicorn agent_server.app:create_app --factory --port 8200
from contextlib import asynccontextmanager                   # 시작·종료 처리

from fastapi import FastAPI                                  # 웹 프레임워크

from agent_server import admin, audit, proxy                 # 감시·ncn 감사 기록·프록시 라우터
from agent_server.mcp_tools import build_mcp                 # MCP 도구
from agent_server.state import AgentState, close_state, open_state   # 공유 연결
from common.settings import Settings, get_settings           # 설정


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()                           # 설정(테스트는 직접 넣음)
    holder: dict[str, AgentState] = {}                       # 시작 후 채워지는 공유 연결 보관함
    get_state = lambda: holder["state"]                      # 라우터·도구가 쓰는 접근 함수

    mcp = build_mcp(get_state)                               # MCP 서버 + 도구 3개
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp",   # MCP 주소 = /mcp
                                      stateless_http=True,   # 요청마다 독립(세션 없음) — 여러 프로세스로 늘리기 쉬움
                                      host=s.agent_host)     # 127.0.0.1이면 SDK가 DNS 재바인딩 방어를 켠다

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        holder["state"] = await open_state(s)                # Postgres·Redis·HTTP 연결 열기
        async with mcp.session_manager.run():                # MCP 처리기 시작(마운트하면 하위 앱 lifespan이 안 돌아서 직접)
            yield                                            # 서버 동작
        await close_state(holder["state"])                   # 종료 시 연결 닫기

    app = FastAPI(title="codeagent server", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "llm": s.llm_base_url, "model": s.llm_model}   # 살아 있는지 + 어느 LLM에 붙었는지

    app.include_router(proxy.make_router(get_state))         # /v1/models, /v1/chat/completions
    app.include_router(admin.make_router(get_state))         # /admin, /admin/api/summary
    app.include_router(audit.make_router(get_state))         # /audit/events (ncn 감사 기록 받기)
    app.mount("/", mcp_app)                                  # 나머지 경로(/mcp)는 MCP 앱으로 — 위 라우트가 먼저 매칭됨
    return app


if __name__ == "__main__":
    import uvicorn                                           # 실행기
    s = get_settings()
    uvicorn.run(create_app(s), host=s.agent_host, port=s.agent_port)   # 서버 실행
