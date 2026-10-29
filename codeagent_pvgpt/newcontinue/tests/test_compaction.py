# tests/test_compaction.py — 대화 압축 시험(cn compaction.test.ts·compaction.pruneLastMessage.test.ts·
#   compaction.infiniteLoop.test.ts·streamChatResponse.autoCompaction.test.ts 대표 사례 + 녹화형 가짜 LLM 으로 요청 본문 확인)
import asyncio                                               # 비동기 실행

import pytest                                                # 시험 도구

import ncn.llm.client as client_mod                          # 대기 없애기
import ncn.llm.retry as retry_mod                            # 대기 없애기
from ncn import compaction as cp                             # 시험 대상
from ncn.llm.client import AbortSignal, LLMClient            # LLM 클라이언트·중단 신호
from ncn.loop import events as ev                            # 이벤트 종류
from ncn.loop.agent import LoopDeps, run_turn                # 루프
from ncn.loop.history import ChatHistory                     # 기록
from parity.fake_llm import serve                            # 녹화형 가짜 LLM

SMALL = {"model": "fake-model", "defaultCompletionOptions": {"contextLength": 8000, "maxTokens": 1000}}   # 임계 5600·검사 한도 6900
BIG = {"model": "fake-model", "defaultCompletionOptions": {"contextLength": 200000, "maxTokens": 4096}}   # 넉넉한 모델


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def no_sleep(monkeypatch):                                  # 재시도 대기를 없애는 픽스처
    """재시도 대기 없애기."""
    async def fast(_s):                                     # 기다리지 않는 대체 함수
        return None                                         # 아무것도 안 함
    monkeypatch.setattr(client_mod, "_sleep", fast)         # SDK 층 대기 대체
    monkeypatch.setattr(retry_mod, "_sleep", fast)          # CLI 층 대기 대체


def user(c):                                                # user 항목 도우미
    """user 항목."""
    return {"message": {"role": "user", "content": c}, "contextItems": []}   # user 메시지


def asst(c, tool_calls=None):                               # assistant 항목 도우미
    """assistant 항목(도구 호출 선택)."""
    m = {"role": "assistant", "content": c}                 # 기본 메시지
    if tool_calls:                                          # 도구 호출이 있으면
        m["toolCalls"] = tool_calls                         # 붙인다
    return {"message": m, "contextItems": []}               # 항목 반환


def system(c):                                              # system 항목 도우미
    """system 항목."""
    return {"message": {"role": "system", "content": c}, "contextItems": []}   # system 메시지


def tool(c):                                                # tool 항목 도우미
    """tool 항목."""
    return {"message": {"role": "tool", "content": c, "toolCallId": "t1"}, "contextItems": []}   # tool 메시지


TC = [{"id": "t1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]   # 도구 호출 하나


# ─────────────────────────────────────────────────────────────────────────────
# pruneLastMessage(compaction.pruneLastMessage.test.ts)
# ─────────────────────────────────────────────────────────────────────────────


def test_prune_empty_and_single():                          # 빈 것·하나뿐인 것
    assert cp.prune_last_message([]) == []                  # 빈 입력 → 빈 목록
    for it in (user("a"), system("s"), asst("a"), tool("t")):   # 어떤 역할이든
        assert cp.prune_last_message([it]) == []            # 하나뿐이면 빈 목록


def test_prune_second_to_last_user_removes_two():           # 끝에서 두 번째가 user
    h = [system("s"), user("q"), asst("a")]                 # system, user, assistant
    assert cp.prune_last_message(h) == [system("s")]        # user+assistant 둘 제거


def test_prune_tool_sequence_removes_pair():                # 끝에서 두 번째가 도구 호출 assistant
    h = [system("s"), user("q"), asst("", TC), tool("r")]   # 도구 호출 + 결과
    assert cp.prune_last_message(h) == [system("s"), user("q")]   # 호출+결과 한 쌍 제거


def test_prune_otherwise_removes_one():                     # 그 밖에는 하나
    h = [system("s"), asst("a"), asst("b")]                 # assistant 둘
    assert cp.prune_last_message(h) == [system("s"), asst("a")]   # 마지막 하나만 제거
    h2 = [user("a"), user("b")]                             # user 연속
    assert cp.prune_last_message(h2) == []                  # 둘 다 제거


# ─────────────────────────────────────────────────────────────────────────────
# /compact 판정(slashCommands.ts)
# ─────────────────────────────────────────────────────────────────────────────


def test_interactive_compact_command():                     # 대화형 /compact 판정
    assert cp.is_interactive_compact_command("/compact")    # 그대로
    assert cp.is_interactive_compact_command("/compact now")   # 인자가 있어도 compact
    assert not cp.is_interactive_compact_command(" /compact")   # 맨 앞이 '/' 가 아니면 아님
    assert not cp.is_interactive_compact_command("/compactx")   # 이름이 다르면 아님
    assert not cp.is_interactive_compact_command("compact")   # '/' 없음


# ─────────────────────────────────────────────────────────────────────────────
# compactChatHistory(compaction.test.ts)
# ─────────────────────────────────────────────────────────────────────────────


def make_deps(port, events=None):                           # LoopDeps 만들기 도우미(도구 없음)
    """가짜 LLM 을 가리키는 루프 의존성."""
    llm = LLMClient(api_base=f"http://127.0.0.1:{port}/v1/", api_key="k", model="fake-model")   # 가짜 서버 클라이언트
    return LoopDeps(llm=llm, tools=[], check_permission=lambda t, a: "allow", ask_user=None,   # 도구 없이
                    system_message=lambda: "SYS", on_event=(events.append if events is not None else ev.noop))   # 이벤트 모으기


def run_compact(steps, items, model=BIG, **kw):             # 가짜 LLM 앞에서 compact_chat_history 실행
    """(결과, 녹화기)."""
    server, rec = serve(steps)                              # 가짜 서버
    try:                                                    # 서버 종료 보장
        deps = make_deps(server.server_port)                # 의존성
        res = asyncio.run(cp.compact_chat_history(items, model, ChatHistory(items), deps, headless=True,   # 압축 실행
                                                  model_name="fake-model", **kw))   # 추가 인자
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    return res, rec                                         # 결과·녹화기


def test_compact_with_system_message_keeps_it():            # system 이 있으면 [system, 요약]
    items = [system("old sys"), user("q"), asst("a")]       # system 포함 기록
    res, rec = run_compact([{"content": "SUMMARY"}], items)   # 요약 응답 하나
    assert res["compactionIndex"] == 1                      # 요약 위치 1
    assert res["compactionContent"] == "SUMMARY"            # 요약 글
    assert res["compactedHistory"] == [system("old sys"), {"message": {"role": "assistant", "content": "SUMMARY"},   # [system, 요약]
                                                            "contextItems": [], "conversationSummary": "SUMMARY"}]   # 압축 표지
    msgs = rec.requests[0]["body"]["messages"]              # 보낸 메시지
    assert msgs[-1] == {"role": "user", "content": cp.COMPACTION_PROMPT}   # 요약 요청 글이 끝에(원문 그대로)
    assert msgs[0] == {"role": "system", "content": "SYS"}   # 시스템 메시지는 평소처럼 맨 앞


def test_compact_without_system_index_zero():               # system 없으면 [요약]
    res, _ = run_compact([{"content": "S"}], [user("q"), asst("a")])   # system 없는 기록
    assert res["compactionIndex"] == 0                      # 요약 위치 0
    assert len(res["compactedHistory"]) == 1                # 요약 하나


def test_compact_empty_content():                           # 빈 요약도 그대로
    res, _ = run_compact([{"content": ""}], [user("q")])    # 빈 응답
    assert res["compactionContent"] == ""                   # 빈 글
    assert res["compactedHistory"][0]["conversationSummary"] == ""   # 표지도 빈 글


def test_compact_prunes_when_too_long():                    # 한도를 넘으면 끝부터 잘라 요약 요청만 남음
    big = "word " * 7000                                    # 약 7000 토큰
    res, rec = run_compact([{"content": "S"}], [user(big), asst("a")], model=SMALL)   # 작은 모델
    msgs = rec.requests[0]["body"]["messages"]              # 보낸 메시지
    assert [m["role"] for m in msgs] == ["system", "user"]  # 잘려서 시스템 + 요약 요청만
    assert msgs[1]["content"] == cp.COMPACTION_PROMPT       # 남은 것은 요약 요청


def test_compact_error_propagates():                        # LLM 오류는 예외(호출자가 처리)
    with pytest.raises(Exception):                          # 예외가 나야 함
        run_compact([{"status": 400, "content": "bad"}], [user("q")])   # 400 오류


def test_retry_discards_partial_summary():                  # [부록 B] 재시도 시 그 호출의 요약 조각 버림
    events = []                                             # 이벤트
    sink, collected = cp._compaction_sink(make_deps(1, events), None)   # 수신자
    sink(ev.content("abc"))                                 # 조각
    sink(ev.retry(1, 10, "e"))                              # 재시도
    sink(ev.content("SUM"))                                 # 다시 받은 조각
    sink(ev.content_complete("SUM"))                        # 끝
    assert collected() == "SUM"                             # 버린 조각 없음
    assert [e["type"] for e in events] == ["retry"]         # 재시도 알림만 원래 수신자로


# ─────────────────────────────────────────────────────────────────────────────
# 훅(streamChatResponse.autoCompaction.test.ts·compactionHelpers)
# ─────────────────────────────────────────────────────────────────────────────


def run_hook(steps, stage, items, ctx, model=SMALL, history=None, events=None, **kw):   # 가짜 LLM 앞에서 훅 실행
    """(반환값, 녹화기, 기록)."""
    server, rec = serve(steps)                              # 가짜 서버
    history = history or ChatHistory(items)                 # 서비스 기록
    try:                                                    # 서버 종료 보장
        deps = make_deps(server.server_port, events)        # 의존성
        hook = cp.make_compaction_hook(history, model, **kw)   # 시험 대상 훅
        out = asyncio.run(hook(stage, items, {"deps": deps, "model_name": "fake-model", **ctx}))   # 훅 호출
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    return out, rec, history                                # 결과 반환


def test_hook_no_compaction_under_threshold():              # 임계 미만이면 그대로
    items = [user("hi")]                                    # 작은 기록
    (new, was), rec, _ = run_hook([], "pre_api", items, {"is_headless": True, "system_message": "SYS", "tools": []})   # 호출 전 검사
    assert was is False and new == items                    # 압축 안 함
    assert rec.requests == []                               # LLM 호출 없음


def test_hook_auto_compacts_headless_without_notice():      # 헤드리스는 알림 없이 압축
    events, saved = [], []                                  # 이벤트·저장 기록
    items = [user("word " * 6000)]                          # 임계(5600) 이상
    (new, was), rec, h = run_hook([{"content": "S"}], "pre_api", items,   # 호출 전 검사
                                  {"is_headless": True, "system_message": "SYS", "tools": []},   # 헤드리스
                                  events=events, on_compacted=saved.append)   # 저장 콜백
    assert was is True                                      # 압축됨
    assert new[0]["conversationSummary"] == "S"             # 요약 항목
    assert saved == [new]                                   # updateSessionHistory
    assert not [e for e in events if e["type"] == ev.SYSTEM_MESSAGE]   # 알림 없음
    after = h.get_history()                                 # 서비스 기록
    assert after[0] == items[0] and after[1]["message"]["content"] == "S"   # 요약 호출 답이 서비스 기록에 붙음(cn 그대로) — 교체는 루프가 한다


def test_hook_interactive_notices_and_history():            # 대화형은 시작·성공 알림을 기록과 이벤트로
    events = []                                             # 이벤트
    items = [user("word " * 6000)]                          # 임계 이상
    (new, was), _, h = run_hook([{"content": "S"}], "normal", items,   # 일반 검사
                                {"is_headless": False, "system_message": "SYS", "tools": [], "should_continue": True},   # 대화형
                                events=events)              # 이벤트 모으기
    msgs = [e["message"] for e in events if e["type"] == ev.SYSTEM_MESSAGE]   # 알림 글
    assert msgs == ["Approaching context limit (8K tokens). Auto-compacting chat history...",   # 시작 안내(원문)
                    "Chat history auto-compacted successfully."]   # 성공 안내(원문)
    assert was is True                                      # 압축됨
    assert [i["message"]["role"] for i in h.get_history()] == ["user", "system", "assistant", "system"]   # 시작 안내·요약 답·성공 안내(루프가 곧 교체)


def test_hook_normal_skipped_when_not_continuing():         # should_continue 거짓이면 검사 안 함
    items = [user("word " * 6000)]                          # 임계 이상
    (new, was), rec, _ = run_hook([], "normal", items, {"is_headless": True, "system_message": "SYS", "tools": []})   # 계속 아님
    assert was is False and rec.requests == []              # 압축 안 함


def test_hook_error_continues_with_warning():               # 압축 실패 → 경고 후 그대로(handleCompactionError)
    events = []                                             # 이벤트
    items = [user("word " * 6000)]                          # 임계 이상
    (new, was), _, h = run_hook([{"status": 400, "content": "bad"}], "pre_api", items,   # 400 오류
                                {"is_headless": False, "system_message": "SYS", "tools": []}, events=events)   # 대화형
    assert was is False and new == items                    # 압축 없이 계속
    msgs = [e["message"] for e in events if e["type"] == ev.SYSTEM_MESSAGE]   # 알림 글
    assert msgs[-1].startswith("Warning: Auto-compaction error: ")   # 경고 글 시작
    assert msgs[-1].endswith(". Continuing without compaction...")   # 경고 글 끝
    assert h.get_history()[-1]["message"]["content"] == msgs[-1]   # 기록에 남음


def test_hook_post_tool_forced_and_failure():               # 도구 결과로 한도 초과 → 강제, 실패면 예외
    items = [user("q"), asst("", TC), tool("word " * 7000)]   # 검사 한도(6900) 초과
    ctx = {"is_headless": True, "system_message": "SYS", "tools": [], "tool_calls": [object()]}   # 도구 호출 있음
    (new, was), _, h = run_hook([{"content": "S"}], "post_tool", items, ctx)   # 강제 압축
    assert was is True and h.get_history() == new           # 기록 갱신됨
    with pytest.raises(Exception, match="Context limit exceeded and compaction failed. Unable to continue."):   # 실패 원문
        run_hook([{"status": 400, "content": "bad"}], "post_tool", items, ctx)   # 요약 실패


def test_hook_post_tool_under_limit_noop():                 # 한도 이내면 아무 것도 안 함
    items = [user("q")]                                     # 작은 기록
    (new, was), rec, _ = run_hook([], "post_tool", items, {"is_headless": True, "system_message": "SYS", "tools": [],   # 작은 기록
                                                           "tool_calls": [object()]})   # 도구 호출 있음
    assert was is False and rec.requests == []              # 호출 없음


def test_hook_skips_pre_api_when_compacting():              # 요약 호출 중에는 호출 전 검사 안 함
    items = [user("word " * 6000)]                          # 임계 이상
    (new, was), rec, _ = run_hook([], "pre_api", items, {"is_headless": True, "is_compacting": True,   # 압축 중
                                                         "system_message": "SYS", "tools": []})   # 시스템 메시지·도구
    assert was is False and rec.requests == []              # 검사 안 함


# ─────────────────────────────────────────────────────────────────────────────
# 수동 /compact
# ─────────────────────────────────────────────────────────────────────────────


def test_manual_headless_success_and_error():               # 헤드리스: 성공은 기록 교체, 실패는 오류 JSON 줄
    out = []                                                # stdout 대신
    (new, was), _, h = run_hook([{"content": "S"}], "manual", [user("q")], {"is_headless": True}, model=BIG,   # 성공
                                stdout_write=out.append)    # stdout 가로채기
    assert was is True and h.get_history() == new and h.compaction_index == 0   # 압축 위치 0
    assert out == []                                        # 성공 줄은 app.py 가 쓴다
    (new, was), _, _ = run_hook([{"status": 400, "content": "bad"}], "manual", [user("q")], {"is_headless": True},   # 실패
                                model=BIG, stdout_write=out.append)   # stdout 가로채기
    assert new is None and was is False                     # 압축 안 됨
    assert out[0].startswith('{"status":"error","message":"Compaction error: ')   # 오류 줄(원문)


def test_manual_interactive_success_message():              # 대화형: 성공 system 메시지가 기록 끝에
    events = []                                             # 이벤트
    (new, was), _, h = run_hook([{"content": "S"}], "manual", [user("q")], {"is_headless": False}, model=BIG, events=events)   # 대화형
    assert was is True                                      # 압축됨
    assert [i["message"]["role"] for i in h.get_history()] == ["assistant", "system"]   # [요약, 성공 안내]
    assert h.get_history()[-1]["message"]["content"] == "Chat history compacted successfully."   # 원문
    assert [e["message"] for e in events if e["type"] == ev.SYSTEM_MESSAGE] == ["Chat history compacted successfully."]   # 알림


def test_manual_interactive_cancelled():                    # 대화형: 요약 호출이 중단돼 정상 반환되면 아무 것도 안 함(cn 그대로)
    events = []                                             # 이벤트
    sig = AbortSignal()                                     # 중단 신호
    sig.abort()                                             # 미리 취소
    (new, was), rec, h = run_hook([{"content": "S"}], "manual", [user("q")], {"is_headless": False, "signal": sig},   # 취소된 상태
                                  model=BIG, events=events)  # 이벤트 모으기
    assert was is False and rec.requests == []              # 압축 안 됨·요청 없음
    assert h.get_history() == [user("q")]                   # 기록 그대로
    assert not [e for e in events if e["type"] == ev.SYSTEM_MESSAGE]   # 안내 없음("Compaction cancelled."는 예외로 끝날 때만)


# ─────────────────────────────────────────────────────────────────────────────
# 루프 통합: 압축 뒤 "continue"(streamChatResponse.ts handleAutoContinuation)
# ─────────────────────────────────────────────────────────────────────────────


def test_loop_pre_api_compaction_then_continue():           # 큰 질문 → 호출 전 압축 → 답 → continue → 답
    server, rec = serve([{"content": "S"}, {"content": "A1"}, {"content": "A2"}])   # 요약·답·이어 간 답
    try:                                                    # 서버 종료 보장
        deps = make_deps(server.server_port)                # 의존성
        history = ChatHistory()                             # 빈 기록
        deps.compaction_hook = cp.make_compaction_hook(history, SMALL)   # 압축 훅
        res = asyncio.run(run_turn(history, "word " * 6000, deps, headless=True, model_name="fake-model"))   # 한 턴
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert res.response == "A2"                             # 헤드리스는 마지막 반복의 글
    roles = [[m["role"] for m in r["body"]["messages"]] for r in rec.requests]   # 요청마다 역할
    assert roles == [["system", "user", "user"], ["system", "assistant"], ["system", "assistant", "assistant", "user"]]   # cn 과 같은 순서
    assert rec.requests[2]["body"]["messages"][-1]["content"] == "continue"   # 이어 가기 글


# ─────────────────────────────────────────────────────────────────────────────
# ACP(ncn --stdio): prompt 글 "/compact" → 수동 압축(useChat.compaction.ts)
# ─────────────────────────────────────────────────────────────────────────────


def test_acp_slash_compact(tmp_path):                       # ACP /compact — 요약 요청·알림·다음 요청 기록
    from acp.helpers import text_block                      # 질문 블록
    from tests.test_acp import FakeIDE, Harness, run        # ACP 시험 도구(가짜 IDE·자식 프로세스)
    h = Harness(tmp_path, [{"content": "A1"}, {"content": "SUM"}, {"content": "A2"}])   # 답·요약·답
    ide = FakeIDE()                                         # 가짜 IDE

    async def go():                                         # 시나리오
        await h.start(ide)                                  # 시작
        s = await h.new()                                   # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("hi")])   # 보통 질문
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("/compact")])   # 수동 압축
        assert r.stop_reason == "end_turn"                  # 정상 끝
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("next")])   # 압축 뒤 질문
        await h.stop()                                      # 종료

    run(go())                                               # 실행
    bodies = h.bodies()                                     # LLM 요청 본문
    assert len(bodies) == 3                                 # 답·요약·답
    assert bodies[1]["messages"][-1]["content"] == cp.COMPACTION_PROMPT   # 요약 요청 글
    assert [m["role"] for m in bodies[1]["messages"]] == ["system", "user", "assistant", "user"]   # 기록 전체 + 요약 요청
    msgs = bodies[2]["messages"]                            # 압축 뒤 요청
    assert [(m["role"], m["content"]) for m in msgs[1:]] == [("assistant", "SUM"),   # 요약
                                                             ("system", "Chat history compacted successfully."),   # 성공 안내(기록에 남음 — cn 대화형)
                                                             ("user", "next")]   # 새 질문
    assert ("_ncn/system_message" in [m for m, _ in ide.ext]) or ("ncn/system_message" in [m for m, _ in ide.ext])   # 확장 알림
    thoughts = [u["content"]["text"] for u in ide.of("agent_thought_chunk")]   # session/update 알림
    assert thoughts == ["Chat history compacted successfully.\n"]   # 압축 안내
    h.check_stdout()                                        # stdout 은 JSON 줄만
