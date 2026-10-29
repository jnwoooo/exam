# parity/fake_mcp.py — 차등 시험·단위 시험용 결정적 가짜 MCP 서버(공식 mcp SDK 2.x 저수준 Server 사용)
# 도구: echo(글 되돌림) · add(두 수 더하기) · error(isError 결과) · mixed(글·그림·리소스·링크 섞인 결과) + 프롬프트 1개
# 실행: python parity/fake_mcp.py            → stdio 서버(cn·ncn 의 mcpServers command 로 띄움)
#       python parity/fake_mcp.py --fail     → stderr 에 한 줄 쓰고 바로 끝남(접속 실패 시험)
#       python parity/fake_mcp.py --slow S   → 도구 호출마다 S 초 쉬고 답함(시간 제한 시험)
# HTTP(streamable-http) 는 tests 가 http_app() 으로 만든 ASGI 앱을 uvicorn 스레드로 띄운다
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # 명령줄·stderr
from typing import Any, Optional                             # 타입

import anyio                                                 # SDK 가 쓰는 비동기 실행기
import mcp_types as types                                    # MCP 형식(TextContent 등)
from mcp.server.lowlevel import Server                       # 저수준 서버(결과를 그대로 만든다)

SERVER_NAME = "fake-mcp"                                     # 서버 이름(initialize 응답)
SLOW_SECONDS = 0.0                                           # 도구 호출 전 쉬는 시간(--slow)

# 도구 정의 — inputSchema 키 순서·중첩 설명이 LLM 요청에 그대로 실리는지 보려고 일부러 다양하게
TOOLS = [                                                    # tools/list 결과
    types.Tool(name="echo", description="Echo back the given text",   # echo: 설명 있음, required 있음
               input_schema={"type": "object",              # 최상위는 object
                             "properties": {"text": {"type": "string", "description": "Text to echo"}},   # 인자 하나
                             "required": ["text"]}),        # 필수 인자
    types.Tool(name="add", description="Add two numbers",   # add: 인자 둘(숫자)
               input_schema={"type": "object",              # 최상위는 object
                             "properties": {"b": {"type": "number"}, "a": {"type": "number", "description": "first"}},   # 키 순서 b→a(순서 보존 확인)
                             "required": ["a", "b"]}),      # 필수 인자 둘
    types.Tool(name="error", description="Always fails with isError",   # error: required 없음(키 생략 확인)
               input_schema={"type": "object", "properties": {"message": {"type": "string"}}}),   # 선택 인자 하나
    types.Tool(name="mixed",                                 # mixed: 설명 없음(cn 은 "" 로 보냄)
               input_schema={"type": "object"}),            # properties 도 없음(cn 은 {} 로 채움)
]                                                            # 목록 닫기

PROMPTS = [types.Prompt(name="greet", description="Say hello",   # 프롬프트 하나(cn 은 목록만 받아 둠)
                        arguments=[types.PromptArgument(name="who", required=False)])]   # 인자 하나

PNG_1PX = "iVBORw0KGgo="                                     # 그림 내용(짧은 base64 — 형식 확인용)


def _num(v: Any) -> str:                                     # 숫자를 JS String() 처럼 글로
    """정수 값이면 소수점 없이, 아니면 repr(시험에는 정수만 씀)."""
    if isinstance(v, float) and v.is_integer():              # 3.0 → "3"
        return str(int(v))                                   # 정수 글
    return str(v)                                            # 그 밖은 그대로


async def list_tools(ctx: Any, params: Optional[types.PaginatedRequestParams]) -> types.ListToolsResult:   # tools/list 처리기
    """도구 네 개를 한 쪽에 모두 준다(다음 쪽 없음)."""
    return types.ListToolsResult(tools=TOOLS)                # 결과


async def list_prompts(ctx: Any, params: Optional[types.PaginatedRequestParams]) -> types.ListPromptsResult:   # prompts/list 처리기
    """프롬프트 하나."""
    return types.ListPromptsResult(prompts=PROMPTS)          # 결과


async def get_prompt(ctx: Any, params: types.GetPromptRequestParams) -> types.GetPromptResult:   # prompts/get 처리기(능력 광고용)
    """greet → 사용자 메시지 하나."""
    who = (params.arguments or {}).get("who", "world")       # 인자(없으면 world)
    return types.GetPromptResult(messages=[types.PromptMessage(role="user", content=types.TextContent(text=f"Hello {who}"))])   # 결과


async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:   # tools/call 처리기
    """이름별로 결정적인 결과. 모르는 이름은 MCP 오류(-32602)."""
    if SLOW_SECONDS:                                         # --slow 면
        await anyio.sleep(SLOW_SECONDS)                      # 일부러 늦게
    args = params.arguments or {}                            # 인자(없으면 빈 dict)
    if params.name == "echo":                                # echo
        return types.CallToolResult(content=[types.TextContent(text=f"echo: {args.get('text', '')}")])   # 글 하나
    if params.name == "add":                                 # add
        return types.CallToolResult(content=[types.TextContent(text=_num(args.get("a", 0) + args.get("b", 0)))])   # 합
    if params.name == "error" and args.get("message") == "protocol":   # error(message="protocol") — 프로토콜 수준 오류
        from mcp.shared.exceptions import MCPError           # 프로토콜 오류
        raise MCPError(-32603, "internal failure")           # JSON-RPC 오류 응답(cn: "MCP error -32603: internal failure")
    if params.name == "error":                               # error — 결과 안에 isError
        return types.CallToolResult(content=[types.TextContent(text=f"failed: {args.get('message', 'boom')}")], is_error=True)   # 오류 결과
    if params.name == "mixed":                               # mixed — 여러 종류
        return types.CallToolResult(content=[                # 내용 네 개
            types.TextContent(text="한글 \"따옴표\" / slash", annotations=types.Annotations(audience=["user"], priority=0.5)),   # 글(주석 포함)
            types.ImageContent(data=PNG_1PX, mime_type="image/png"),   # 그림
            types.EmbeddedResource(resource=types.TextResourceContents(uri="file:///fake/readme.txt", mime_type="text/plain", text="hello")),   # 리소스
            types.ResourceLink(name="doc", uri="file:///fake/doc.md"),   # 리소스 링크
        ])                                                   # 목록 닫기
    from mcp.shared.exceptions import MCPError               # 프로토콜 오류
    raise MCPError(-32602, f"Unknown tool: {params.name}")   # 모르는 도구


def make_server() -> Server:                                 # 서버 객체 만들기(stdio·http 공용)
    """처리기를 단 저수준 Server."""
    return Server(SERVER_NAME, version="1.0.0",              # 이름·버전
                  on_list_tools=list_tools, on_call_tool=call_tool,   # 도구
                  on_list_prompts=list_prompts, on_get_prompt=get_prompt)   # 프롬프트


def http_app(token: Optional[str] = None, path: str = "/mcp"):   # streamable-http ASGI 앱(시험용)
    """token 을 주면 Authorization: Bearer <token> 이 아닌 요청을 401 로 막는다(사내 /mcp 흉내)."""
    app = make_server().streamable_http_app(streamable_http_path=path, json_response=False)   # SDK 의 HTTP 앱
    return app if token is None else _Guard(app, token)      # 토큰이 있으면 검사 앱으로 감쌈


class _Guard:                                                # 토큰 검사 ASGI 감싸개
    """lifespan 은 원래 앱이 받아 세션 관리자를 시작하고, HTTP 요청은 토큰을 먼저 본다."""

    def __init__(self, app, token: str):                     # 원래 앱·기대 토큰
        self.app = app                                       # 원래 앱
        self.token = token                                   # 기대 토큰

    async def __call__(self, scope, receive, send):          # ASGI 진입
        if scope["type"] == "http":                          # HTTP 요청이면
            headers = dict(scope.get("headers") or [])       # 헤더(바이트 쌍)
            if headers.get(b"authorization", b"").decode() != f"Bearer {self.token}":   # 토큰이 다르면
                from starlette.responses import JSONResponse  # 401 응답
                await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)   # 401
                return                                       # 끝
        await self.app(scope, receive, send)                 # 통과 — 원래 앱으로


async def _amain() -> None:                                  # stdio 서버 실행
    """stdin/stdout 으로 한 연결을 끝날 때까지 처리."""
    from mcp.server.stdio import stdio_server                # stdio 전송
    server = make_server()                                   # 서버
    async with stdio_server() as (read, write):              # 표준 입출력 연결
        await server.run(read, write, server.create_initialization_options())   # 연결이 끝날 때까지


def main(argv: list[str]) -> int:                            # 진입점
    """--fail: stderr 한 줄 후 종료 코드 1 / --slow S: 지연 / 그 밖: stdio 서버."""
    global SLOW_SECONDS                                      # 전역 지연 값을 바꾼다
    if "--fail" in argv:                                     # 접속 실패 흉내
        sys.stderr.write("fake_mcp: startup failed\n")       # stderr 한 줄(cn 은 경고로 모아 오류 글에 붙임)
        sys.stderr.flush()                                   # 바로 내보냄
        return 1                                             # 바로 끝
    if "--slow" in argv:                                     # 지연 흉내
        SLOW_SECONDS = float(argv[argv.index("--slow") + 1])  # 초
    anyio.run(_amain)                                        # 서버 실행
    return 0                                                 # 정상 종료


if __name__ == "__main__":                                   # 직접 실행할 때만
    raise SystemExit(main(sys.argv[1:]))                     # 종료 코드로
