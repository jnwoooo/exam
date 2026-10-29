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
def no_sleep(monkeypatch):                                  # 모든 시험에 자동 적용되는 픽스처
    """재시도 대기 없애기."""                                       # 재시도 대기를 없애는 픽스처
    async def fast(_s):                                     # 기다리지 않는 대체 함수
        return None                                         # 아무것도 안 한다
    monkeypatch.setattr(client_mod, "_sleep", fast)         # SDK 층 대기를 대체
    monkeypatch.setattr(retry_mod, "_sleep", fast)          # CLI 층 대기를 대체


def make_tool(name, required=("filepath",), run=None, preprocess=None):  # 가짜 도구를 만드는 도우미
    """가짜 도구 하나."""
    async def default_run(args, ctx):                        # 기본: 인자를 글로
        return f"{name} ok {args.get('filepath')}"          # 인자를 글로 돌려준다
    props = {p: {"type": "string", "description": p} for p in required}  # 필수 인자별 속성 정의
    params = {"type": "object", "required": list(required), "properties": props}  # 도구 인자 스키마
    return Tool(name=name, display_name=name, description=f"{name} tool", parameters=params,  # Tool 객체 생성
                run=run or default_run, preprocess=preprocess)  # 실행·사전 처리 함수 연결


def perms(table):                                           # 권한 표를 판정 함수로 바꾸는 도우미
    """{이름: 정책} → check_permission."""
    return lambda tool, args: table.get(tool.name, "allow")  # 표에 없으면 allow


def make_deps(port, tools, table=None, ask_user=None, events=None, model="fake-model"):  # LoopDeps를 만드는 도우미
    """LoopDeps 만들기."""
    llm = LLMClient(api_base=f"http://127.0.0.1:{port}/v1/", api_key="k", model=model)  # 가짜 서버를 가리키는 클라이언트
    return LoopDeps(llm=llm, tools=tools, check_permission=perms(table or {}), ask_user=ask_user,  # 루프 의존성 묶음
                    system_message=lambda: "SYS", on_event=(events.append if events is not None else ev.noop))  # 이벤트를 목록에 모으거나 버린다


def turn(steps, tools, prompt="hi", headless=True, **kw):   # 가짜 LLM 앞에서 한 턴 실행하는 도우미
    """가짜 LLM 앞에서 한 턴 → (결과, 녹화기, 기록)."""
    server, rec = serve(steps)                              # 가짜 서버 시작
    try:                                                    # 서버 종료 보장
        deps = make_deps(server.server_port, tools, **kw)   # 의존성 만들기
        history = ChatHistory()                             # 빈 기록
        result = asyncio.run(run_turn(history, prompt, deps, headless=headless, model_name="fake-model"))  # 한 턴 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    return result, rec, history                             # 결과·녹화기·기록 반환


def test_plain_answer_request_body():                       # 일반 답변의 요청 본문 시험
    result, rec, _ = turn([{"content": "답입니다"}], [make_tool("Read")])  # 도구 없는 한 턴 실행
    assert result.response == "답입니다"                        # 응답 확인
    body = rec.requests[0]["body"]                          # 첫 요청 본문
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]  # 시스템·사용자 메시지 확인
    assert body["tools"] == [{"type": "function", "function": {"name": "Read", "description": "Read tool",  # 도구 정의 확인
                              "parameters": {"type": "object", "required": ["filepath"],  # parameters 구조
                                             "properties": {"filepath": {"type": "string", "description": "filepath"}}}}}]  # 속성 정의
    assert list(body) == ["model", "messages", "stream", "tools", "stream_options"]  # 본문 키 순서 확인


def test_tool_round_trip_reserializes_arguments():          # 도구 호출 왕복과 인자 재직렬화 시험
    steps = [{"content": "읽겠습니다", "tool_calls": [{"name": "Read", "arguments": {"filepath": "a.txt", "n": 1.0}}]},  # 도구 호출이 있는 단계
             {"content": "끝"}]                              # 마무리 단계
    result, rec, history = turn(steps, [make_tool("Read")])  # 한 턴 실행
    assert len(rec.requests) == 2 and result.response == "끝"  # 요청 2번, 최종 응답 확인
    msgs = rec.requests[1]["body"]["messages"]              # 두 번째 요청의 메시지
    assert msgs[2] == {"role": "assistant", "content": "읽겠습니다",  # assistant 메시지에 도구 호출이 실려 있다
                       "tool_calls": [{"id": "call_0_0", "type": "function",  # 호출 id와 형식
                                       "function": {"name": "Read", "arguments": '{"filepath":"a.txt","n":1}'}}]}   # 공백 없음·1.0→1
    assert msgs[3] == {"role": "tool", "content": "Read ok a.txt", "tool_call_id": "call_0_0"}  # 도구 결과 메시지 확인
    st = history.get_history()[1]["toolCallStates"][0]       # 결과는 assistant 항목 상태에 붙어 있다
    assert st["status"] == "done" and st["output"][0]["name"] == "Tool Result"  # 완료 상태와 결과 이름 확인


def test_headless_excludes_ask_tools_and_ends_turn_on_rejection():  # 헤드리스: ask 도구 제외·거부 시 턴 종료 시험
    steps = [{"content": "쓰겠습니다", "tool_calls": [{"name": "Write", "arguments": {"filepath": "x"}}]}, {"content": "안 옴"}]  # ask 도구를 부르는 단계
    result, rec, history = turn(steps, [make_tool("Read"), make_tool("Write")], table={"Write": "ask"})  # Write는 ask로 설정
    assert [t["function"]["name"] for t in rec.requests[0]["body"]["tools"]] == ["Read"]   # ask 도구는 목록에서 빠짐
    assert len(rec.requests) == 1                            # 거부 → 턴 종료
    assert result.stopped_by_rejection and result.response == "쓰겠습니다"  # 거부로 멈춘 결과 확인
    out = convert_from_unified_history(history.get_history())  # 기록을 요청 형식으로 변환
    assert out[-1] == {"role": "tool", "content": "Command blocked by security policy", "tool_call_id": "call_0_0"}  # 정책 거부 글이 도구 결과로 남는다


def test_error_messages_verbatim():                         # 오류 글 원문 시험
    async def boom(args, ctx):                              # 실행 중 오류를 내는 도구
        raise ContinueError("Unspecified", "disk on fire")  # ContinueError 발생

    async def bad_pre(args):                                # 사전 처리에서 오류를 내는 도구
        raise ContinueError("FileNotFound", "File x does not exist")  # ContinueError 발생
    tools = [make_tool("Read"), make_tool("Boom", run=boom), make_tool("Pre", preprocess=bad_pre)]  # 도구 4종
    steps = [{"tool_calls": [{"name": "Nope", "arguments": {}}, {"name": "Read", "arguments": {}},  # 없는 도구·인자 누락·실행 오류·사전 처리 오류 호출
                             {"name": "Boom", "arguments": {"filepath": "a"}}, {"name": "Pre", "arguments": {"filepath": "a"}}]},  # 실행·사전 처리 호출 인자
             {"content": "done"}]                           # 마무리 단계
    _, rec, _ = turn(steps, tools)                          # 한 턴 실행
    tool_msgs = [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"]  # 두 번째 요청의 tool 메시지만 뽑는다
    assert tool_msgs == ["Tool Nope not found", 'Required parameter "filepath" missing for tool "Read"',  # 오류 글 원문 확인
                         "Error executing tool Boom: disk on fire", "File x does not exist"]   # 원래 순서, 하나씩


def test_parallel_execution_order_and_count():              # 병렬 실행 순서·개수 시험
    started = []                                             # 시작 순서
    state = {}                                               # 동시 실행 확인용 이벤트 보관

    async def slow(args, ctx):                              # 느린 도구
        state.setdefault("ev", asyncio.Event())             # 이벤트 준비
        started.append((args["filepath"], ctx.parallel_tool_call_count))  # 시작 기록(병렬 수 포함)
        if args["filepath"] == "1":                          # 첫 도구는 둘째가 시작될 때까지 기다린다 → 동시 실행 증명
            await asyncio.wait_for(state["ev"].wait(), 2)   # 둘째가 신호를 줄 때까지 대기
        else:                                               # 둘째 도구면
            state["ev"].set()                               # 신호를 준다
        return f"r{args['filepath']}"                       # 결과 반환
    steps = [{"tool_calls": [{"name": "S", "arguments": {"filepath": "1"}}, {"name": "S", "arguments": {"filepath": "2"}}]},  # 도구 호출 두 개
             {"content": "ok"}]                             # 마무리 단계
    _, rec, _ = turn(steps, [make_tool("S", run=slow)])     # 한 턴 실행
    assert started == [("1", 2), ("2", 2)]                  # 병렬 수가 2로 전달됐는지 확인
    tool_msgs = [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"]  # 두 번째 요청의 tool 메시지
    assert tool_msgs == ["r1", "r2"]                         # 원래 순서


def test_interactive_user_rejection_continues_loop():       # 대화형: 사용자 거부 후에도 루프 계속 시험
    asked = []                                              # 물은 기록

    async def ask(name, args, preview, tool_call_id):       # 사용자에게 묻는 대체 함수
        asked.append((name, tool_call_id))                  # 질문 기록
        return "reject"                                     # 거부 대답
    events = []                                             # 이벤트 목록
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "x"}}]}, {"content": "알겠습니다"}]  # ask 도구 호출 단계
    result, rec, _ = turn(steps, [make_tool("Write")], table={"Write": "ask"}, ask_user=ask, headless=False, events=events)  # 대화형으로 한 턴 실행
    assert asked == [("Write", "call_0_0")]                 # 물어본 내용 확인
    assert len(rec.requests) == 2                            # 대화형은 거부해도 이어 간다
    assert [t["function"]["name"] for t in rec.requests[0]["body"]["tools"]] == ["Write"]   # 대화형은 ask 도구도 보임
    assert [m["content"] for m in rec.requests[1]["body"]["messages"] if m["role"] == "tool"] == ["Permission denied by user"]  # 거부 글이 도구 결과로 간다
    types = [e["type"] for e in events]                     # 이벤트 종류 목록
    assert ev.PERMISSION_REQUEST in types and ev.TOOL_RESULT in types and ev.CONTENT in types  # 권한 요청·결과·내용 이벤트가 있다
    assert result.response == "알겠습니다"                    # 대화형은 전체 글(앞 반복 글 없음)


def test_tool_delta_without_id_mapping_is_dropped():        # id 대응 없는 조각 버림 시험
    calls, idx = {}, {}                                     # 호출 모음과 index 매핑
    process_tool_call_delta({"index": 0, "function": {"arguments": "{}"}}, calls, idx)   # id도 대응도 없음 → 버림
    assert calls == {}                                      # 아무것도 모이지 않는다
    process_tool_call_delta({"index": 0, "id": "toolu_1", "function": {"name": "Read", "arguments": ""}}, calls, idx)  # id와 index를 알려 주는 조각
    for frag in ['{"filepa', 'th"', ": ", '"/U', 'sers/a"}']:   # cn 시험: index만으로 이어 붙이기
        process_tool_call_delta({"index": 0, "function": {"arguments": frag}}, calls, idx)  # 조각 반영
    tc = calls["toolu_1"]                                   # 모인 호출
    assert tc.name == "Read" and tc.arguments_str == '{"filepath": "/Users/a"}' and tc.arguments == {"filepath": "/Users/a"}  # 이름·인자 글·해석된 인자 확인


def test_tool_cancelled_and_single_tool_result_per_call():  # 취소 상태와 호출당 결과 하나 시험
    h = ChatHistory()                                       # 빈 기록
    h.add_user_message("u")                                 # 사용자 메시지 추가
    h.add_assistant_message("", [{"id": "a", "type": "function", "function": {"name": "Read", "arguments": "{}"}},  # 도구 호출 둘이 있는 assistant 메시지
                                 {"id": "b", "type": "function", "function": {"name": "Read", "arguments": "{}"}}])  # id a, b
    h.add_tool_result("a", "err", "errored")                # a만 오류 결과 기록
    msgs = convert_from_unified_history(h.get_history())    # 요청 형식으로 변환
    assert [m for m in msgs if m["role"] == "tool"] == [{"role": "tool", "content": "err", "tool_call_id": "a"},  # a는 오류 글, b는 취소 글이 한 번씩만 나온다
                                                        {"role": "tool", "content": "Tool cancelled", "tool_call_id": "b"}]  # tool 메시지 확인


def test_retry_discards_partial_chunks():                   # 재시도 시 부분 조각 폐기 시험
    class FlakyLLM:                                          # 첫 시도는 글을 조금 보내고 끊김
        model = "fake-model"                                # 모델 이름

        def __init__(self):                                 # 초기화
            self.n = 0                                      # 시도 횟수 0

        async def stream_chat(self, messages, tools, *, signal=None):  # 스트리밍 흉내
            self.n += 1                                     # 시도 횟수 증가
            yield {"choices": [{"index": 0, "delta": {"content": "가나"}}]}  # 첫 조각
            if self.n == 1:                                 # 첫 시도면
                raise LLMError("Invalid response body: Premature close", code="ERR_STREAM_PREMATURE_CLOSE")  # 연결 끊김 오류
            yield {"choices": [{"index": 0, "delta": {"content": "다"}}]}  # 둘째 조각
    deps = LoopDeps(llm=FlakyLLM(), tools=[], check_permission=perms({}), ask_user=None, system_message=lambda: "S")  # 루프 의존성 구성
    r = asyncio.run(run_turn(ChatHistory(), "q", deps, headless=True, model_name="fake-model"))  # 한 턴 실행
    assert r.response == "가나다"                            # 부록 B: 중복 없음
    deps.llm, deps.discard_partial_on_retry = FlakyLLM(), False  # 폐기 끄고 다시 구성
    r = asyncio.run(run_turn(ChatHistory(), "q", deps, headless=True, model_name="fake-model"))  # 한 턴 실행
    assert r.response == "가나가나다"                        # cn 동작(중복)


def test_preprocessed_args_used_for_run():                  # 사전 처리 인자로 실행 시험
    seen = []                                               # 실행에 쓰인 인자 기록

    async def pre(args):                                    # 인자를 고치는 사전 처리
        return PreprocessResult(args={"filepath": args["filepath"].lstrip("./")})  # 앞의 ./ 제거

    async def r(args, ctx):                                 # 실행 함수
        seen.append(args)                                   # 받은 인자 기록
        return "ok"                                         # 성공 글
    turn([{"tool_calls": [{"name": "Read", "arguments": {"filepath": "./a"}}]}, {"content": "x"}],  # 도구 호출 한 턴 실행
         [make_tool("Read", run=r, preprocess=pre)])        # 사전 처리·실행 도구 연결
    assert seen == [{"filepath": "a"}]                      # 고쳐진 인자로 실행됐는지 확인


def test_headless_output_helpers():                         # 헤드리스 출력 도우미 시험
    assert hl.format_headless_output("  ") is None          # 공백만 있으면 출력 없음
    assert hl.format_headless_output("hi") == "hi\n"        # 일반 글은 개행을 붙인다
    assert hl.format_headless_output("<think>x</think>\n\n\n\nA", silent=True) == "A\n"  # think 블록 제거·개행 정리
    assert hl.format_headless_output('{"a":1}', format="json") == '{"a":1}\n'  # json 형식은 그대로 출력
    assert hl.process_json_output("plain") == ('{"response":"plain","status":"success",'  # JSON이 아니면 감싸서 출력
                                              '"note":"Response was not valid JSON, so it was wrapped in a JSON object"}')  # 감싸는 주석 포함
    assert hl.format_error_output("boom") == '{"status":"error","message":"boom"}\n'  # 오류 출력 형식
    assert hl.final_exit_code(0, True) == 1 and hl.final_exit_code(130, True) == 130  # 종료 코드 규칙
