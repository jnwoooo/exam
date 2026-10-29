# tests/test_mcp.py — MCP 클라이언트(ncn/mcp/) 시험. 가짜 MCP 서버(parity/fake_mcp.py)를 stdio 자식·streamable-http 스레드로 띄워
#   목록·호출·오류 글·시간 제한·종료 정리를 보고, ACP session/new 의 mcpServers 연결, 실제 ../agent_server 의 /mcp 연결도 본다.
# 기대 글(오류 글·결과 JSON)은 cn(node dist/cn.js) 을 직접 실행해 얻은 값(parity/scenarios/20~23 와 같은 출처).
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 시험
import json                                                  # 결과 글 해석
import os                                                    # 환경변수·경로
import socket                                                # 빈 포트
import subprocess                                            # 실제 agent_server 띄우기
import sys                                                   # 파이썬 실행 파일
import threading                                             # HTTP 서버 스레드
import time                                                  # 기다리기
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from ncn.mcp import acp_servers, content, tools as mtools, transports   # 시험 대상(작은 함수들)
from ncn.mcp.service import MCPService, McpClientError, is_auth_error, _transport_kinds   # 시험 대상(서비스)
from parity import fake_mcp                                  # 가짜 서버(HTTP 앱·도구 표)

HERE = Path(__file__).resolve().parent                       # tests/
FAKE = str(HERE.parent / "parity" / "fake_mcp.py")           # 가짜 서버 파일
PY = sys.executable                                          # mcp SDK 가 깔린 파이썬
REPO = HERE.parent.parent                                    # codeagent_pvgpt/
TIMEOUT = 60                                                 # 시험 하나 시간 한도(초)


def run(coro):                                               # 시간 한도 안에서 실행
    return asyncio.run(asyncio.wait_for(coro, TIMEOUT))      # 비동기 실행


def stdio_cfg(name="fake", *extra, **kw) -> dict:            # 가짜 서버 stdio 설정
    return {"name": name, "command": PY, "args": [FAKE, *extra], **kw}   # cn 설정 모양


def free_port() -> int:                                      # 빈 TCP 포트
    with socket.socket() as s:                               # 임시 소켓
        s.bind(("127.0.0.1", 0))                             # 아무 포트
        return s.getsockname()[1]                            # 번호


class HttpServer:                                            # uvicorn 을 스레드로(SDK streamable-http 앱)
    """with 블록 동안 http://127.0.0.1:<port>/mcp 로 가짜 서버를 연다."""

    def __init__(self, app):                                 # ASGI 앱
        import uvicorn                                       # ASGI 서버(mcp SDK 의존성)
        self.port = free_port()                              # 포트
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", lifespan="on"))   # 서버
        self.thread = threading.Thread(target=self.server.run, daemon=True)   # 스레드

    @property                                                # 읽기 전용
    def url(self) -> str:                                    # 주소
        return f"http://127.0.0.1:{self.port}/mcp"           # /mcp

    def __enter__(self):                                     # 시작
        self.thread.start()                                  # 스레드 시작
        for _ in range(200):                                 # 최대 10초
            if self.server.started:                          # 준비됨
                return self                                  # 사용
            time.sleep(0.05)                                 # 잠깐
        raise RuntimeError("HTTP 서버가 뜨지 않음")           # 실패

    def __exit__(self, *exc):                                # 끝
        self.server.should_exit = True                       # 멈춤 신호
        self.thread.join(10)                                 # 기다림


# ─────────────────────────────────────────────────────────────────────────────
# 작은 함수들(결과 글·도구 변환·설정 변환)
# ─────────────────────────────────────────────────────────────────────────────


def test_content_shape_follows_node_sdk_schema():            # zod strip + 스키마 순서
    blocks = [{"annotations": {"audience": ["user"]}, "text": "t", "type": "text"},   # 서버는 키를 정렬해 보냄
              {"data": "AA==", "mimeType": "image/png", "type": "image", "extra": 1},   # 모르는 키
              {"resource": {"blob": "AA==", "uri": "u", "x": 1}, "type": "resource"},   # blob 리소스
              {"name": "n", "type": "resource_link", "uri": "u", "size": 3, "icons": [{"src": "s", "theme": "d"}]}]   # 링크
    assert content.shape_content(blocks) == [{"type": "text", "text": "t"},   # annotations 빠지고 type 먼저
                                             {"type": "image", "data": "AA==", "mimeType": "image/png"},   # extra 빠짐
                                             {"type": "resource", "resource": {"uri": "u", "blob": "AA=="}},   # Blob 스키마
                                             {"name": "n", "icons": [{"src": "s"}], "uri": "u", "type": "resource_link"}]   # size·theme 빠짐, type 마지막
    assert list(content.shape_content(None)) == []           # default([])


def test_format_call_tool_result_is_json_stringify_of_content():   # JSON.stringify(result?.content) ?? ""
    assert mtools.format_call_tool_result(None) == ""        # result 없음
    assert mtools.format_call_tool_result({"content": [{"type": "text", "text": "한 \"글\""}], "isError": True}) == '[{"type":"text","text":"한 \\"글\\""}]'   # isError 무시, 한글 그대로
    assert mtools.js_json({"a": 1.0, "b": [2.5]}) == '{"a":1,"b":[2.5]}'   # 정수 값 실수는 JS 처럼
    assert mtools.format_call_tool_result({"content": []}) == "[]"   # 빈 내용


def test_convert_mcp_tool_matches_cn():                      # convertMcpToolToContinueTool
    async def runner(name, args):                            # 가짜 runTool
        return {"content": [{"type": "text", "text": f"{name}:{args}"}]}   # 결과
    t = mtools.convert_mcp_tool_to_continue_tool({"name": "mixed", "inputSchema": {"type": "object"}}, runner)   # 설명·properties 없음
    assert (t.name, t.display_name, t.description, t.is_built_in, t.readonly) == ("mixed", "mixed", "", False, False)   # 이름 그대로, 설명 ""
    assert t.parameters == {"type": "object", "properties": {}}   # properties ?? {}, required 없음
    t2 = mtools.convert_mcp_tool_to_continue_tool(fake_mcp.TOOLS[1], runner)   # SDK Tool 객체(add)
    assert t2.parameters == {"type": "object", "properties": {"b": {"type": "number"}, "a": {"type": "number", "description": "first"}}, "required": ["a", "b"]}   # 순서 보존
    from ncn.loop.tool_calls import convert_tool_to_chat_completion_tool   # 요청 본문 변환
    assert list(convert_tool_to_chat_completion_tool(t2)["function"]["parameters"]) == ["type", "required", "properties"]   # cn 요청 순서
    from ncn.tools.base import ToolRunContext                # 실행 정보
    assert run(t2.run({"a": 1}, ToolRunContext(tool_call_id="c"))) == '[{"type":"text","text":"add:{\'a\': 1}"}]'   # 결과 글


def test_acp_servers_to_cn_configs():                        # ACP McpServer → cn 설정
    from acp.schema import EnvVariable, HttpHeader, HttpMcpServer, McpServerStdio, SseMcpServer   # ACP 형식
    got = acp_servers.acp_servers_to_configs([                # 세 종류 + dict
        McpServerStdio(name="s", command="/bin/x", args=["-a"], env=[EnvVariable(name="K", value="V")]),   # stdio
        HttpMcpServer(type="http", name="h", url="http://h/mcp", headers=[HttpHeader(name="Authorization", value="Bearer t")]),   # http
        SseMcpServer(type="sse", name="e", url="http://h/sse", headers=[]),   # sse(헤더 없음)
        {"type": "acp", "name": "z", "id": "1"},              # 지원 안 함
    ])                                                        # 목록 닫기
    assert got == [{"name": "s", "command": "/bin/x", "args": ["-a"], "env": {"K": "V"}},   # stdio
                   {"name": "h", "type": "streamable-http", "url": "http://h/mcp", "requestOptions": {"headers": {"Authorization": "Bearer t"}}},   # http
                   {"name": "e", "type": "sse", "url": "http://h/sse"}]   # sse


def test_transport_helpers():                                # 헤더·환경변수·종류·인증 오류
    assert transports.build_headers({"requestOptions": {"headers": {"Authorization": "x", "X": "1"}}}, "k") == {"Authorization": "Bearer k", "X": "1"}   # apiKey 가 이김
    assert transports.build_headers({}, None) == {}          # 아무것도 없음
    assert transports.stdio_env({"env": {"A": "1"}}, {"A": "2", "B": "", "C": "3"}) == {"A": "1", "C": "3"}   # 설정 우선, 빈 값 제외
    assert _transport_kinds({"command": "x", "type": "sse"}) == ["stdio"]   # command 가 있으면 stdio
    assert _transport_kinds({"url": "u"}) == ["http", "sse"]   # 자동
    assert _transport_kinds({"url": "u", "type": "sse"}) == ["sse"]   # sse
    assert is_auth_error(McpClientError("x", 401)) and is_auth_error(Exception("got 405")) and is_auth_error(Exception("Unauthorized"))   # 인증 오류
    assert not is_auth_error(McpClientError("fetch failed"))  # 아님


def test_registry_puts_mcp_tools_after_skills_and_overrides(tmp_path):   # 도구 목록 순서
    from ncn.tools import registry                           # 도구 목록
    async def runner(name, args):                            # 쓰지 않음
        return None                                          # 없음
    extra = [mtools.convert_mcp_tool_to_continue_tool(t, runner) for t in fake_mcp.TOOLS]   # MCP 도구 넷
    names = [t.name for t in registry.get_builtin_tools(model_name="fake", headless=True, mode="normal", cwd=tmp_path, global_dir=tmp_path,   # 목록
                                                         ripgrep_installed=False, extra_tools=extra,   # rg 없음 + MCP
                                                         tool_overrides={"echo": {"disabled": True}, "add": {"description": "D"}})]   # 덮어쓰기
    assert names[names.index("Skills") + 1:] == ["add", "error", "mixed"]   # Skills 바로 뒤, 끈 echo 는 빠짐


# ─────────────────────────────────────────────────────────────────────────────
# stdio 통합
# ─────────────────────────────────────────────────────────────────────────────


def test_stdio_list_call_and_shutdown():                     # 연결·목록·호출·종료
    async def go():                                          # 시나리오
        s = MCPService()                                     # 서비스
        await s.initialize([stdio_cfg()], headless=True)     # 접속(헤드리스는 기다림)
        conn = s.connections["fake"]                         # 연결
        assert conn.status == "connected" and [p.name for p in s.get_prompts()] == ["greet"]   # 연결됨·프롬프트
        assert [t.name for t in s.get_tools()] == ["echo", "add", "error", "mixed"]   # 서버 순서
        tl = {t.name: t for t in s.get_tools()}              # 이름별
        from ncn.tools.base import ToolRunContext            # 실행 정보
        ctx = ToolRunContext(tool_call_id="c")               # 실행 정보
        assert await tl["echo"].run({"text": "안녕"}, ctx) == '[{"type":"text","text":"echo: 안녕"}]'   # 글
        assert await tl["add"].run({"a": 2, "b": 40}, ctx) == '[{"type":"text","text":"42"}]'   # 숫자
        with pytest.raises(mtools.McpToolError, match=r'^\[\{"type":"text","text":"failed: bad"\}\]$'):   # [ncn 부록B M1] isError 결과는 오류로
            await tl["error"].run({"message": "bad"}, ctx)   # 호출
        assert json.loads(await tl["mixed"].run({}, ctx))[0] == {"type": "text", "text": "한글 \"따옴표\" / slash"}   # annotations 빠짐
        with pytest.raises(McpClientError, match="^MCP error -32603: internal failure$"):   # 프로토콜 오류
            await tl["error"].run({"message": "protocol"}, ctx)   # 호출
        with pytest.raises(Exception, match="^Tool nope not found$"):   # 없는 도구
            await s.run_tool("nope", {})                     # 호출
        proc_task = conn.task                                # 붙잡는 작업
        await s.shutdown()                                   # 정리
        assert proc_task.done() and conn.status == "idle" and s.get_tools() == []   # 닫힘
    run(go())                                                # 실행


def test_headless_failures_aggregate_like_cn():              # 헤드리스 실패 글(cn 실행 결과와 같음)
    async def go():                                          # 시나리오
        s = MCPService()                                     # 서비스
        with pytest.raises(Exception) as ei:                 # 실패 예외
            await s.initialize([stdio_cfg("ok"), {"name": "bad", "command": "/nonexistent/yy"}, stdio_cfg("bad2", "--fail")], headless=True)   # 셋 중 둘 실패
        assert str(ei.value) == ("MCP server(s) failed to load:\nbad: spawn /nonexistent/yy ENOENT\n"   # cn 원문
                                 "bad2: MCP error -32000: Connection closed\n\nServer stderr:\nfake_mcp: startup failed")   # stderr 붙음
        assert s.connections["ok"].status == "connected"     # 성공한 것은 연결됨
        await s.shutdown()                                   # 정리
    run(go())                                                # 실행


def test_unresolved_secrets_headless_error_interactive_warning():   # 남은 ${{ secrets.X }}
    cfg = stdio_cfg("k", env={"TOKEN": "${{ secrets.//MISSING_TOKEN }}"})   # loader 가 못 찾은 비밀(FQSN 모양으로 남음)

    async def go():                                          # 시나리오
        s = MCPService()                                     # 헤드리스
        with pytest.raises(Exception) as ei:                 # 실패
            await s.initialize([cfg], headless=True)         # 접속
        assert str(ei.value).startswith("MCP server(s) failed to load:\nk: k MCP Server has unresolved secrets: MISSING_TOKEN\nFor personal use")   # 원문
        s2 = MCPService()                                    # 대화형
        await s2.initialize([cfg], headless=False, wait=True)   # 경고만
        c = s2.connections["k"]                              # 연결
        assert c.status == "connected" and c.warnings[0].startswith("k MCP Server has unresolved secrets: MISSING_TOKEN")   # 연결되고 경고
        await s2.shutdown()                                  # 정리
    run(go())                                                # 실행


def test_interactive_does_not_wait_by_default_and_failure_is_state():   # cn TUI: 기다리지 않음, 실패는 상태
    async def go():                                          # 시나리오
        s = MCPService()                                     # 서비스
        await s.initialize([stdio_cfg(), {"name": "bad", "command": "/nonexistent/zz"}], headless=False)   # 바로 돌아옴
        await s.wait_pending()                               # 뒤의 접속 기다림
        assert s.connections["fake"].status == "connected" and s.connections["bad"].error == "spawn /nonexistent/zz ENOENT"   # 상태
        await s.shutdown()                                   # 정리
    run(go())                                                # 실행


def test_request_timeout_option_seconds():                   # [ncn] requestOptions.timeout(초) → cn Node SDK 시간 초과 글
    async def go():                                          # 시나리오
        s = MCPService()                                     # 서비스
        await s.initialize([stdio_cfg("slow", "--slow", "3", requestOptions={"timeout": 0.5})], headless=True)   # 호출마다 3초 지연, 제한 0.5초
        t0 = time.monotonic()                                # 시작 시각
        with pytest.raises(McpClientError, match="^MCP error -32001: Request timed out$"):   # 시간 초과
            await s.run_tool("echo", {"text": "x"})          # 호출
        assert time.monotonic() - t0 < 2.5                   # 제한대로 끊김
        await s.shutdown()                                   # 정리
    run(go())                                                # 실행


# ─────────────────────────────────────────────────────────────────────────────
# streamable-http 통합(SDK 서버를 스레드로)
# ─────────────────────────────────────────────────────────────────────────────


def test_streamable_http_with_bearer_token():                # apiKey → Authorization: Bearer
    with HttpServer(fake_mcp.http_app(token="tok")) as srv:  # 토큰 검사 서버
        async def go():                                      # 시나리오
            s = MCPService()                                 # 서비스
            await s.initialize([{"name": "h", "type": "streamable-http", "url": srv.url, "apiKey": "tok"},   # apiKey
                                {"name": "h2", "url": srv.url, "requestOptions": {"headers": {"Authorization": "Bearer tok"}}}],   # 헤더(자동 종류)
                               headless=True)                # 접속
            assert all(c.status == "connected" for c in s.connections.values())   # 두 연결 모두 됨
            assert [t.name for t in s.get_tools()] == ["echo", "add", "error", "mixed"]   # [ncn 부록B M3] 겹치는 이름은 먼저 온 서버 것만(cn 은 두 번)
            r = await s.run_tool("add", {"a": 1, "b": 2})    # 첫 서버로
            assert mtools.format_call_tool_result(r) == '[{"type":"text","text":"3"}]'   # 결과
            await s.shutdown()                               # 정리
        run(go())                                            # 실행


def test_streamable_http_unauthorized_message_like_cn():     # 401 → StreamableHTTPError 원문, 자동 종류도 SSE 로 넘어가지 않음
    with HttpServer(fake_mcp.http_app(token="tok")) as srv:  # 토큰 검사 서버
        async def go():                                      # 시나리오
            s = MCPService()                                 # 서비스
            with pytest.raises(Exception) as ei:             # 실패
                await s.initialize([{"name": "h", "type": "streamable-http", "url": srv.url, "apiKey": "bad"},   # 틀린 토큰
                                    {"name": "a", "url": srv.url}], headless=True)   # 토큰 없음(자동)
            body = '{"error":"Unauthorized"}'                # 401 본문
            assert str(ei.value) == (f"MCP server(s) failed to load:\nh: Streamable HTTP error: Error POSTing to endpoint: {body}\n"   # 원문
                                     f"a: Streamable HTTP error: Error POSTing to endpoint: {body}")   # 인증 오류라 SSE 재시도 없음
            await s.shutdown()                               # 정리
        run(go())                                            # 실행


def test_refused_ports_messages_like_cn():                   # 연결 거부 글(cn 실행 결과)
    port = free_port()                                       # 아무도 안 듣는 포트

    async def go():                                          # 시나리오
        s = MCPService()                                     # 서비스
        await s.initialize([{"name": "h", "type": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp"},   # http
                            {"name": "e", "type": "sse", "url": f"http://127.0.0.1:{port}/sse"}], headless=False, wait=True)   # sse
        assert s.connections["h"].error == "fetch failed"    # Node fetch 글
        assert s.connections["e"].error == f"SSE error: TypeError: fetch failed: connect ECONNREFUSED 127.0.0.1:{port}"   # undici 글
        await s.shutdown()                                   # 정리
    run(go())                                                # 실행


# ─────────────────────────────────────────────────────────────────────────────
# ACP(session/new 의 mcpServers) 와 헤드리스 연결
# ─────────────────────────────────────────────────────────────────────────────


def test_acp_session_new_mcp_servers_connect_and_tools_used(tmp_path):   # ACP McpServer → 같은 연결 코드
    from tests.test_acp import FakeIDE, Harness, text_block, tool_names   # ACP 시험 도구
    steps = [{"tool_calls": [{"name": "echo", "arguments": {"text": "IDE"}}]}, {"content": "끝"}]   # echo 호출 후 답
    h = Harness(tmp_path, steps)                             # 준비
    ide = FakeIDE()                                          # 승인은 allow_once(대화형: MCP 는 ask)

    async def go():                                          # 시나리오
        init = await h.start(ide)                            # 시작
        caps = init.agent_capabilities.mcp_capabilities      # MCP 능력
        assert caps.http and caps.sse                        # http·sse 지원 광고
        s = await h.conn.new_session(cwd=str(h.work), mcp_servers=[{"name": "fake", "command": PY, "args": [FAKE], "env": [{"name": "X", "value": "1"}]}])   # ACP stdio 서버
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("echo 불러")])   # 질문
        assert r.stop_reason == "end_turn"                   # 정상 끝
        await h.stop()                                       # 끝(stdout JSON 검사 포함)
    run(go())                                                # 실행
    bodies = h.bodies()                                      # LLM 요청들
    assert tool_names(bodies[0])[-4:] == ["echo", "add", "error", "mixed"]   # MCP 도구가 끝에
    assert bodies[1]["messages"][-1] == {"role": "tool", "content": '[{"type":"text","text":"echo: IDE"}]', "tool_call_id": "call_0_0"}   # 결과 글
    assert len(ide.permissions) == 1                         # 대화형: MCP 도구는 승인 요청(cn "*": ask)


def test_headless_cli_end_to_end(tmp_path):                  # ncn -p + 설정 mcpServers(차등 시험 21 과 같은 길)
    from parity.fake_llm import serve                        # 가짜 LLM
    from parity.run import config_yaml, isolated_env         # 설정·격리 환경
    steps = [{"tool_calls": [{"name": "add", "arguments": {"a": 20, "b": 22}}]}, {"content": "42입니다."}]   # add 호출 후 답
    server, rec = serve(steps)                               # 서버
    try:                                                     # 끝나면 서버 종료
        work = tmp_path / "w"                                # 작업 폴더
        work.mkdir()                                         # 생성
        cfg = tmp_path / "config.yaml"                       # 설정
        cfg.write_text(config_yaml(server.server_port, {"config_append": 'mcpServers:\n  - name: fake\n    command: "{PYTHON}"\n    args: ["{PARITY_DIR}/fake_mcp.py"]\n'}), encoding="utf-8")   # 자리표시자 치환
        p = subprocess.run([PY, "-m", "ncn", "-p", "더해", "--config", str(cfg)], cwd=work, env=isolated_env(tmp_path),   # 실행
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT)   # (인자 이어짐)
    finally:                                                 # 정리
        server.shutdown()                                    # 서버 종료
    assert p.returncode == 0, p.stderr.decode()              # 정상
    assert p.stdout.decode() == "42입니다.\n"                # 최종 답
    assert rec.requests[1]["body"]["messages"][-1]["content"] == '[{"type":"text","text":"42"}]'   # 결과 글


# ─────────────────────────────────────────────────────────────────────────────
# 부록B M1~M3 — ncn 이 고친 동작(호환 모드 NCN_CN_COMPAT=1 은 cn 그대로)
# ─────────────────────────────────────────────────────────────────────────────

PAGED_SERVER = """
import sys, anyio, mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
N = int(sys.argv[1])
async def list_tools(ctx, params):
    i = int(params.cursor) if params is not None and params.cursor else 0
    tool = types.Tool(name=f"t{i}", description="", input_schema={"type": "object"})
    return types.ListToolsResult(tools=[tool], next_cursor=str(i + 1) if i + 1 < N else None)
async def call_tool(ctx, params):
    return types.CallToolResult(content=[types.TextContent(text=params.name)])
async def main():
    s = Server("paged", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (r, w):
        await s.run(r, w, s.create_initialization_options())
anyio.run(main)
"""                                                          # 한 쪽에 도구 하나씩 N 쪽을 주는 서버(nextCursor = 다음 번호)


def test_m1_is_error_result_goes_through_loop_error_path(tmp_path):   # [ncn 부록B M1] isError → "Error executing tool …" + errored
    from ncn.loop.tool_calls import PreprocessedToolCall, ToolCall, execute_streamed_tool_calls   # 루프 실행 단계
    from ncn.loop.history import ChatHistory                 # 기록

    async def go(compat: bool):                              # 한 번 실행(호환 여부)
        os.environ["NCN_CN_COMPAT"] = "1" if compat else "0"  # 스위치
        s = MCPService()                                     # 서비스
        await s.initialize([stdio_cfg()], headless=True)     # 접속
        try:                                                 # 끝나면 정리
            tool = {t.name: t for t in s.get_tools()}["error"]   # error 도구
            call = PreprocessedToolCall(call=ToolCall(id="c1", name="error", arguments={"message": "bad"}), tool=tool)   # 호출 하나
            events: list = []                                # 화면 이벤트
            _, entries = await execute_streamed_tool_calls([call], ChatHistory(), lambda t, a: "allow", None, events.append, True)   # 실행
            return entries[0], events                        # 결과
        finally:                                             # 정리
            await s.shutdown()                               # 연결 닫기
    try:                                                     # 환경변수 되돌리기
        fixed, events = run(go(False))                       # 고친 동작
        assert fixed["content"] == 'Error executing tool error: [{"type":"text","text":"failed: bad"}]'   # 내장 도구 실패 형식
        assert fixed["status"] == "errored"                  # 오류 상태(ACP 카드 failed)
        assert any("error" in str(e).lower() for e in events)   # 오류 이벤트가 나감
        compat, _ = run(go(True))                            # cn 원래 동작
        assert compat["content"] == '[{"type":"text","text":"failed: bad"}]' and compat["status"] == "done"   # 내용만·성공
    finally:                                                 # 정리
        os.environ.pop("NCN_CN_COMPAT", None)                # 지움


def test_m2_tools_list_follows_next_cursor(tmp_path, monkeypatch):   # [ncn 부록B M2] 모든 쪽 받기 + 상한
    script = tmp_path / "paged.py"                           # 서버 파일
    script.write_text(PAGED_SERVER, encoding="utf-8")        # 쓰기

    async def names(n: int) -> list:                         # N 쪽 서버의 도구 이름들
        s = MCPService()                                     # 서비스
        await s.initialize([{"name": "p", "command": PY, "args": [str(script), str(n)]}], headless=True)   # 접속
        try:                                                 # 끝나면 정리
            return [t.name for t in s.get_tools()]           # 이름
        finally:                                             # 정리
            await s.shutdown()                               # 닫기
    monkeypatch.delenv("NCN_CN_COMPAT", raising=False)       # 고친 동작
    assert run(names(3)) == ["t0", "t1", "t2"]               # 세 쪽 모두
    import ncn.mcp.service as svc                            # 상한 바꾸기
    monkeypatch.setattr(svc, "MAX_TOOL_PAGES", 2)            # 상한 2쪽
    assert run(names(5)) == ["t0", "t1"]                     # 상한에서 멈춤
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 원래 동작
    assert run(names(3)) == ["t0"]                           # 첫 쪽만


def test_m3_duplicate_server_and_tool_names(monkeypatch, capsys):   # [ncn 부록B M3] 서버·도구 이름 겹침
    from mcp_types import Tool as McpTool                    # SDK 도구 형식
    monkeypatch.delenv("NCN_CN_COMPAT", raising=False)       # 고친 동작

    async def dup_servers():                                 # 같은 서버 이름 두 번
        s = MCPService()                                     # 서비스
        await s.initialize([stdio_cfg("fake"), {"name": "fake", "command": "/nonexistent/yy"}], headless=True)   # 뒤의 것은 실패할 설정
        try:                                                 # 정리
            return s.connections["fake"].status, len(s.connections)   # 앞의 것이 남음
        finally:                                             # 정리
            await s.shutdown()                               # 닫기
    assert run(dup_servers()) == ("connected", 1)            # 뒤의 것은 건너뜀(헤드리스 실패도 없음)
    assert "MCP 서버 이름 'fake'" in capsys.readouterr().err   # stderr 경고

    def svc_with(tools_by_server: dict) -> MCPService:       # 연결된 척하는 서비스
        s = MCPService()                                     # 서비스
        from ncn.mcp.service import ServerConnection         # 연결 상태
        for name, tnames in tools_by_server.items():         # 서버마다
            s.connections[name] = ServerConnection(config={"name": name}, status="connected",   # 연결됨
                                                   tools=[McpTool(name=n, input_schema={"type": "object"}) for n in tnames])   # 도구
        return s                                             # 서비스
    s = svc_with({"a": ["x", "Read", "y"], "b": ["y", "z"]})  # y 겹침, Read 는 내장과 겹침
    assert [t.name for t in s.get_tools()] == ["x", "y", "z"]   # 먼저 온 것만, 내장 이름 뺌
    err = capsys.readouterr().err                            # 경고
    assert "'Read'" in err and "'y'" in err                  # 두 경고
    s.get_tools()                                            # 다시 불러도
    assert capsys.readouterr().err == ""                     # 같은 경고는 한 번만
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 원래 동작
    assert [t.name for t in svc_with({"a": ["x", "Read", "y"], "b": ["y"]}).get_tools()] == ["x", "Read", "y", "y"]   # 전부


# ─────────────────────────────────────────────────────────────────────────────
# 실제 코드에이전트 서버 /mcp(../agent_server/mcp_tools.py) — 서버 쪽 .venv 가 있을 때만
# ─────────────────────────────────────────────────────────────────────────────

AGENT_SERVER_SCRIPT = """
import sys, uvicorn
sys.path.insert(0, {root!r})
from agent_server.mcp_tools import build_mcp
mcp = build_mcp(lambda: None)
uvicorn.run(mcp.streamable_http_app(streamable_http_path="/mcp"), host="127.0.0.1", port={port}, log_level="error")
"""                                                          # 상태 없이 MCP 앱만(목록은 DB 가 필요 없다)


def _server_python():                                        # 서버 쪽 파이썬(없으면 None)
    p = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")   # uv 가상환경
    if not p.exists():                                       # 없으면
        return None                                          # 건너뜀
    ok = subprocess.run([str(p), "-c", "import agent_server.mcp_tools, uvicorn"], cwd=REPO, capture_output=True, timeout=60)   # import 가능?
    return str(p) if ok.returncode == 0 else None            # 가능할 때만


def test_real_agent_server_mcp_tools():                      # 실제 /mcp: search_code·search_docs·request_fix
    py = _server_python()                                    # 서버 쪽 파이썬
    if py is None:                                           # 서버 쪽 환경이 없으면
        pytest.skip("../.venv(agent_server 의존성)가 없음")   # 건너뜀
    port = free_port()                                       # 포트
    proc = subprocess.Popen([py, "-c", AGENT_SERVER_SCRIPT.format(root=str(REPO), port=port)], cwd=REPO,   # 서버 띄우기
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)   # (인자 이어짐)
    try:                                                     # 끝나면 서버 종료
        for _ in range(200):                                 # 최대 20초
            try:                                            # 포트 열렸나
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()   # 연결 시도
                break                                        # 열림
            except OSError:                                  # 아직
                if proc.poll() is not None:                  # 죽었으면
                    pytest.skip("agent_server MCP 앱을 띄우지 못함: " + proc.stderr.read().decode()[-300:])   # 건너뜀
                time.sleep(0.1)                              # 잠깐

        async def go():                                      # 시나리오
            s = MCPService()                                 # 서비스
            await s.initialize([{"name": "codeagent", "type": "streamable-http", "url": f"http://127.0.0.1:{port}/mcp", "apiKey": "tok"}], headless=True)   # 사내 설정 모양
            tl = {t.name: t for t in s.get_tools()}          # 도구
            assert list(tl) == ["search_code", "search_docs", "request_fix"]   # 서버 순서
            assert tl["search_code"].parameters["required"] == ["query"] and "k" in tl["search_code"].parameters["properties"]   # 스키마 변환
            assert tl["search_code"].description.startswith("이름을 모를 때")   # 설명 그대로
            r = await s.run_tool("search_code", {"query": "x"})   # DB 없이 호출 → 서버가 isError 결과로 돌려줌
            out = mtools.format_call_tool_result(r)          # 결과 글
            assert out.startswith('[{"type":"text","text":"') and r.is_error   # 내용 글만(isError 는 글에 없음)
            await s.shutdown()                               # 정리
        run(go())                                            # 실행
    finally:                                                 # 정리
        proc.terminate()                                     # 서버 종료
        proc.wait(10)                                        # 기다림
