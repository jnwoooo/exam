# tests/test_loop.py — 에이전트 루프 시험(cn streamChatResponse.test.ts·handleToolCalls.test.ts 사례 + 녹화형 가짜 LLM으로 요청 본문 확인)
import asyncio                                               # 비동기 실행

import pytest                                                # 시험 도구

import ncn.llm.client as client_mod                          # 대기 없애기
import ncn.llm.retry as retry_mod                            # 대기 없애기
from ncn import headless as hl                               # 헤드리스 출력
from ncn.llm.client import LLMClient, LLMError               # LLM 클라이언트
from ncn.loop import events as ev                            # 이벤트 종류
from ncn.loop.agent import LoopDeps, run_turn                # 시험 대상
from ncn.loop.history import ChatHistory, convert_from_unified_history   # 기록
from ncn.loop.tool_calls import ToolCall, process_tool_call_delta   # 조각 모으기
from ncn.tools.base import ContinueError, PreprocessResult, Tool    # 공용 도구 형식
from parity.fake_llm import serve                            # 녹화형 가짜 LLM


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """재시도 대기 없애기."""
    async def fast(_s):
        return None
    monkeypatch.setattr(client_mod, "_sleep", fast)
    monkeypatch.setattr(retry_mod, "_sleep", fast)


def make_tool(name, required=("filepath",), run=None, preprocess=None):
    """가짜 도구 하나."""
    async def default_run(args, ctx):                        # 기본: 인자를 글로
        return f"{name} ok {args.get('filepath')}"
    props = {p: {"type": "string", "description": p} for p in required}
    params = {"type": "object", "required": list(required), "properties": props}
    return Tool(name=name, display_name=name, description=f"{name} tool", parameters=params,
                run=run or default_run, preprocess=preprocess)


def perms(table):
    """{이름: 정책} → check_permission."""
    return lambda tool, args: table.get(tool.name, "allow")


def make_deps(port, tools, table=None, ask_user=None, events=None, model="fake-model"):
    """LoopDeps 만들기."""
    llm = LLMClient(api_base=f"http://127.0.0.1:{port}/v1/", api_key="k", model=model)
    return LoopDeps(llm=llm, tools=tools, check_permission=perms(table or {}), ask_user=ask_user,
                    system_message=lambda: "SYS", on_event=(events.append if events is not None else ev.noop))


def turn(steps, tools, prompt="hi", headless=True, **kw):
    """가짜 LLM 앞에서 한 턴 → (결과, 녹화기, 기록)."""
    server, rec = serve(steps)
    try:
        deps = make_deps(server.server_port, tools, **kw)
        history = ChatHistory()
        result = asyncio.run(run_turn(history, prompt, deps, headless=headless, model_name="fake-model"))
    finally:
        server.shutdown()
    return result, rec, history


def test_plain_answer_request_body():
    result, rec, _ = turn([{"content": "답입니다"}], [make_tool("Read")])
    assert result.response == "답입니다"
    body = rec.requests[0]["body"]
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert body["tools"] == [{"type": "function", "function": {"name": "Read", "description": "Read tool",
                              "parameters": {"type": "object", "required": ["filepath"],
                                             "properties": {"filepath": {"type": "string", "description": "filepath"}}}}}]
    assert list(body) == ["model", "messages", "stream", "tools", "stream_options"]


def test_tool_round_trip_reserializes_arguments():
    steps = [{"content": "읽겠습니다", "tool_calls": [{"name": "Read", "arguments": {"filepath": "a.txt", "n": 1.0}}]},
             {"content": "끝"}]
    result, rec, history = turn(steps, [make_tool("Read")])
    assert len(rec.requests) == 2 and result.response == "끝"
    msgs = rec.requests[1]["body"]["messages"]
    assert msgs[2] == {"role": "assistant", "content": "읽겠습니다",
                       "tool_calls": [{"id": "call_0_0", "type": "function",
                                       "function": {"name": "Read", "arguments": '{"filepath":"a.txt","n":1}'}}]}   # 공백 없음·1.0→1
    assert msgs[3] == {"role": "tool", "content": "Read ok a.txt", "tool_call_id": "call_0_0"}
    st = history.get_history()[1]["toolCallStates"][0]       # 결과는 assistant 항목 상태에 붙어 있다
    assert st["status"] == "done" and st["output"][0]["name"] == "Tool Result"


def test_headless_excludes_ask_tools_and_ends_turn_on_rejection():
    steps = [{"content": "쓰겠습니다", "tool_calls": [{"name": "Write", "arguments": {"filepath": "x"}}]}, {"content": "안 옴"}]
    result, rec, history = turn(steps, [make_tool("Read"), make_tool("Write")], table={"Write": "ask"})
    assert [t["function"]["name"] for t in rec.requests[0]["body"]["tools"]] == ["Read"]   # ask 도구는 목록에서 빠짐
    assert len(rec.requests) == 1                            # 거부 → 턴 종료
    assert result.stopped_by_rejection and result.response == "쓰겠습니다"
    out = convert_from_unified_history(history.get_history())
    assert out[-1] == {"role": "tool", "content": "Command blocked by security policy", "tool_call_id": "call_0_0"}


def test_error_messages_verbatim():
    async def boom(args, ctx):
        raise ContinueError("Unspecified", "disk on fire")

    async def bad_pre(args):
        raise ContinueError("FileNotFound", "File x does not exist")
    tools = [make_tool("Read"), make_tool("Boom", run=boom), make_tool("Pre", preprocess=bad_pre)]
    steps = [{"tool_calls": [{"name": "Nope", "arguments": {}}, {"name": "Read", "arguments": {}},
                             {"name": "Boom", "arguments": {"filepath": "a"}}, {"name": "Pre", "arguments": {"filepath": "a"}}]},
             {"content": "done"}]
    _, rec, _ = turn(steps, tools)
    tool_msgs = [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert tool_msgs == ["Tool Nope not found", 'Required parameter "filepath" missing for tool "Read"',
                         "Error executing tool Boom: disk on fire", "File x does not exist"]   # 원래 순서, 하나씩


def test_parallel_execution_order_and_count():
    started = []                                             # 시작 순서
    state = {}                                               # 동시 실행 확인용 이벤트 보관

    async def slow(args, ctx):
        state.setdefault("ev", asyncio.Event())
        started.append((args["filepath"], ctx.parallel_tool_call_count))
        if args["filepath"] == "1":                          # 첫 도구는 둘째가 시작될 때까지 기다린다 → 동시 실행 증명
            await asyncio.wait_for(state["ev"].wait(), 2)
        else:
            state["ev"].set()
        return f"r{args['filepath']}"
    steps = [{"tool_calls": [{"name": "S", "arguments": {"filepath": "1"}}, {"name": "S", "arguments": {"filepath": "2"}}]},
             {"content": "ok"}]
    _, rec, _ = turn(steps, [make_tool("S", run=slow)])
    assert started == [("1", 2), ("2", 2)]                   # parallelToolCallCount = 2
    tool_msgs = [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"]
    assert tool_msgs == ["r1", "r2"]                         # 원래 순서


def test_interactive_user_rejection_continues_loop():
    asked = []

    async def ask(name, args, preview, tool_call_id):
        asked.append((name, tool_call_id))
        return "reject"
    events = []
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "x"}}]}, {"content": "알겠습니다"}]
    result, rec, _ = turn(steps, [make_tool("Write")], table={"Write": "ask"}, ask_user=ask, headless=False, events=events)
    assert asked == [("Write", "call_0_0")]
    assert len(rec.requests) == 2                            # 대화형은 거부해도 이어 간다
    assert [t["function"]["name"] for t in rec.requests[0]["body"]["tools"]] == ["Write"]   # 대화형은 ask 도구도 보임
    assert [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"] == ["Permission denied by user"]
    types = [e["type"] for e in events]
    assert ev.PERMISSION_REQUEST in types and ev.TOOL_RESULT in types and ev.CONTENT in types
    assert result.response == "알겠습니다"                    # 대화형은 전체 글(앞 반복 글 없음)


def test_tool_delta_without_id_mapping_is_dropped():
    calls, idx = {}, {}
    process_tool_call_delta({"index": 0, "function": {"arguments": "{}"}}, calls, idx)   # id도 대응도 없음 → 버림
    assert calls == {}
    process_tool_call_delta({"index": 0, "id": "toolu_1", "function": {"name": "Read", "arguments": ""}}, calls, idx)
    for frag in ['{"filepa', 'th"', ": ", '"/U', 'sers/a"}']:   # cn 시험: index만으로 이어 붙이기
        process_tool_call_delta({"index": 0, "function": {"arguments": frag}}, calls, idx)
    tc = calls["toolu_1"]
    assert tc.name == "Read" and tc.arguments_str == '{"filepath": "/Users/a"}' and tc.arguments == {"filepath": "/Users/a"}


def test_tool_cancelled_and_single_tool_result_per_call():
    h = ChatHistory()
    h.add_user_message("u")
    h.add_assistant_message("", [{"id": "a", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
                                 {"id": "b", "type": "function", "function": {"name": "Read", "arguments": "{}"}}])
    h.add_tool_result("a", "err", "errored")
    msgs = convert_from_unified_history(h.get_history())
    assert [m for m in msgs if m["role"] == "tool"] == [{"role": "tool", "content": "err", "tool_call_id": "a"},
                                                        {"role": "tool", "content": "Tool cancelled", "tool_call_id": "b"}]


def test_retry_discards_partial_chunks():
    class FlakyLLM:                                          # 첫 시도는 글을 조금 보내고 끊김
        model = "fake-model"

        def __init__(self):
            self.n = 0

        async def stream_chat(self, messages, tools, *, signal=None):
            self.n += 1
            yield {"choices": [{"index": 0, "delta": {"content": "가나"}}]}
            if self.n == 1:
                raise LLMError("Invalid response body: Premature close", code="ERR_STREAM_PREMATURE_CLOSE")
            yield {"choices": [{"index": 0, "delta": {"content": "다"}}]}
    deps = LoopDeps(llm=FlakyLLM(), tools=[], check_permission=perms({}), ask_user=None, system_message=lambda: "S")
    r = asyncio.run(run_turn(ChatHistory(), "q", deps, headless=True, model_name="fake-model"))
    assert r.response == "가나다"                            # 부록 B: 중복 없음
    deps.llm, deps.discard_partial_on_retry = FlakyLLM(), False
    r = asyncio.run(run_turn(ChatHistory(), "q", deps, headless=True, model_name="fake-model"))
    assert r.response == "가나가나다"                        # cn 동작(중복)


def test_preprocessed_args_used_for_run():
    seen = []

    async def pre(args):
        return PreprocessResult(args={"filepath": args["filepath"].lstrip("./")})

    async def r(args, ctx):
        seen.append(args)
        return "ok"
    turn([{"tool_calls": [{"name": "Read", "arguments": {"filepath": "./a"}}]}, {"content": "x"}],
         [make_tool("Read", run=r, preprocess=pre)])
    assert seen == [{"filepath": "a"}]


def test_headless_output_helpers():
    assert hl.format_headless_output("  ") is None
    assert hl.format_headless_output("hi") == "hi\n"
    assert hl.format_headless_output("<think>x</think>\n\n\n\nA", silent=True) == "A\n"
    assert hl.format_headless_output('{"a":1}', format="json") == '{"a":1}\n'
    assert hl.process_json_output("plain") == ('{"response":"plain","status":"success",'
                                              '"note":"Response was not valid JSON, so it was wrapped in a JSON object"}')
    assert hl.format_error_output("boom") == '{"status":"error","message":"boom"}\n'
    assert hl.final_exit_code(0, True) == 1 and hl.final_exit_code(130, True) == 130
