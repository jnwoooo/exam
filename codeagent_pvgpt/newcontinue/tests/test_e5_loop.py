# tests/test_e5_loop.py — [ncn E5] 반복 상한·같은 도구 반복 실패 감지 시험(cn 원본 없음 — ncn/loopguard.py)
import asyncio                                               # 비동기 실행
import subprocess                                            # 헤드리스 실행
import sys                                                   # 파이썬 실행 파일

import pytest                                                # 시험 도구

import ncn.llm.client as client_mod                          # 대기 없애기
import ncn.llm.retry as retry_mod                            # 대기 없애기
from ncn import loopguard as lg                              # 시험 대상
from ncn.llm.client import LLMClient                         # LLM 클라이언트
from ncn.loop import events as ev                            # 이벤트
from ncn.loop.agent import LoopDeps, run_turn                # 루프
from ncn.loop.history import ChatHistory                     # 기록
from ncn.subagent import NcnSettings, parse_ncn_settings     # ncn.yaml 해석
from ncn.tools.base import Tool                              # 공용 도구 형식
from parity.fake_llm import serve                            # 녹화형 가짜 LLM
from parity.run import config_yaml, isolated_env             # 시험 설정·격리 환경


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def no_sleep(monkeypatch):                                  # 재시도 대기 없애기
    """재시도 대기 없애기."""
    async def fast(_s):                                     # 기다리지 않는 대체 함수
        return None                                         # 아무것도 안 함
    monkeypatch.setattr(client_mod, "_sleep", fast)         # SDK 층 대기
    monkeypatch.setattr(retry_mod, "_sleep", fast)          # CLI 층 대기


def read_tool(fail=False):                                   # 가짜 Read 도구
    """fail=True 면 매번 실패."""
    async def run(args, ctx):                                # 실행
        if fail:                                            # 실패 모드
            raise RuntimeError("no such file")              # 실패
        return f"ok {args.get('filepath')}"                 # 성공 글
    params = {"type": "object", "required": ["filepath"], "properties": {"filepath": {"type": "string"}}}   # 인자 스키마
    return Tool(name="Read", display_name="Read", description="Read tool", parameters=params, run=run)   # 도구


def call(path):                                              # Read 호출 단계 하나
    """가짜 LLM 단계: Read(filepath=path) 호출."""
    return {"tool_calls": [{"name": "Read", "arguments": {"filepath": path}}]}   # 도구 호출 단계


def turn(steps, tools, guard, headless=True, events=None):   # 가짜 LLM 앞에서 한 턴
    """(결과, 녹화기)."""
    server, rec = serve(steps)                              # 가짜 서버
    try:                                                    # 서버 종료 보장
        sink = events.append if events is not None else ev.noop   # 이벤트 모으기
        llm = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="fake-model")   # 클라이언트

        def on_event(e):                                     # 화면 + 감시기
            sink(e)                                          # 모으기
            if guard is not None:                            # 감시기가 있으면
                guard.observe(e)                             # 관찰
        if guard is not None:                                # 감시기 안내도 모으기
            guard.on_event = sink                            # 멈춤 안내
        deps = LoopDeps(llm=llm, tools=tools, check_permission=lambda t, a: "allow", ask_user=None,   # 루프 의존성
                        system_message=lambda: "SYS", on_event=on_event, loop_guard=guard)   # 감시기
        result = asyncio.run(run_turn(ChatHistory(), "hi", deps, headless=headless, model_name="fake-model"))   # 한 턴
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    return result, rec                                      # 결과·녹화기


# ── 설정 ──

def test_parse_loop_settings_defaults_zero_and_bad_values():   # ncn.yaml loop 절 해석
    assert (NcnSettings().loop_max_iterations, NcnSettings().loop_repeat_failure_limit) == (50, 3)   # 기본값
    s = parse_ncn_settings({"loop": {"max_iterations": 0, "repeat_failure_limit": 5}})   # 0 = 끔
    assert (s.loop_max_iterations, s.loop_repeat_failure_limit) == (0, 5)   # 반영
    s = parse_ncn_settings({"loop": {"max_iterations": -1, "repeat_failure_limit": True}})   # 잘못된 값
    assert (s.loop_max_iterations, s.loop_repeat_failure_limit) == (50, 3)   # 기본값 유지
    assert parse_ncn_settings({"loop": "x"}).loop_max_iterations == 50   # 사전이 아니면 무시


def test_make_loop_guard_off_and_compat(monkeypatch):         # 전부 0 이면 None, NCN_CN_COMPAT=1 이면 None
    off = parse_ncn_settings({"loop": {"max_iterations": 0, "repeat_failure_limit": 0}})   # 둘 다 끔
    assert lg.make_loop_guard(off, environ={}) is None       # 감시 없음(cn 과 같음)
    assert lg.make_loop_guard(NcnSettings(), environ={}) is not None   # 기본은 켜짐
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                  # cn 원래 동작
    assert lg.make_loop_guard(NcnSettings(), environ={}) is None   # 꺼짐


def test_policy_max_iterations_caps(tmp_path, monkeypatch):  # 관리자 정책 max_iterations 가 상한
    from ncn import policy                                   # 정책 모듈(E1)
    if policy.system_policy_path().is_file():                # 이 PC 에 실제 시스템 정책이 있으면
        pytest.skip("system policy file present")            # NCN_POLICY_FILE 이 무시되므로 건너뜀
    p = tmp_path / "policy.yaml"                             # 시험 정책
    p.write_text("max_iterations: 2\n", encoding="utf-8")    # 상한 2
    off = parse_ncn_settings({"loop": {"max_iterations": 0}})   # 사용자는 상한을 껐어도
    g = lg.make_loop_guard(off, environ={"NCN_POLICY_FILE": str(p)})   # 정책 적용
    assert g is not None and g.policy_max_iterations == 2    # 정책 상한
    asked = []                                               # 물어봤나

    async def ask(text):                                     # 묻기(불리면 안 됨)
        asked.append(text)                                   # 기록
        return True                                         # 계속
    g.ask_continue = ask                                     # 묻기 연결
    result, rec = turn([call("a"), call("b"), call("c"), {"content": "끝"}], [read_tool()], g, headless=False)   # 대화형이어도
    assert len(rec.requests) == 2 and asked == []            # 2번 뒤 묻지 않고 멈춤
    assert result.stopped_by_loop_guard == lg.REASON_POLICY  # 이유


# ── 루프 ──

def test_max_iterations_headless_stops_turn():               # 헤드리스: 상한에서 턴 끝
    g = lg.LoopGuard(max_iterations=2, repeat_failure_limit=0)   # 상한 2
    events = []                                              # 이벤트
    result, rec = turn([call("a"), call("b"), call("c"), {"content": "끝"}], [read_tool()], g, events=events)   # 계속 도구를 부르는 모델
    assert len(rec.requests) == 2                            # LLM 2번만
    assert result.stopped_by_loop_guard == lg.REASON_MAX_ITERATIONS and not result.stopped_by_rejection   # 이유
    assert any(e["type"] == "system_message" and "loop.max_iterations" in e["message"] for e in events)   # 화면 안내


def test_repeat_failure_same_args_stops():                   # 같은 도구·같은 인자 3번 연속 실패 → 멈춤
    g = lg.LoopGuard(max_iterations=0, repeat_failure_limit=3)   # 연속 실패만
    result, rec = turn([call("x"), call("x"), call("x"), call("x"), {"content": "끝"}], [read_tool(fail=True)], g)   # 매번 같은 실패
    assert len(rec.requests) == 3                            # 3번 실패 뒤 4번째 호출 전 멈춤
    assert result.stopped_by_loop_guard == lg.REASON_REPEAT_FAILURE   # 이유


def test_repeat_failure_different_args_continues():          # 인자가 다르면 연속이 아님
    g = lg.LoopGuard(max_iterations=0, repeat_failure_limit=3)   # 연속 실패만
    result, rec = turn([call("a"), call("b"), call("a"), call("b"), {"content": "끝"}], [read_tool(fail=True)], g)   # 번갈아 실패
    assert len(rec.requests) == 5 and result.stopped_by_loop_guard is None   # 끝까지
    assert result.response == "끝"                           # 최종 답


def test_key_order_is_normalized():                          # 인자 키 순서가 달라도 같은 호출
    assert lg.normalize_args({"b": 1, "a": "x"}) == lg.normalize_args({"a": "x", "b": 1}) == '{"a":"x","b":1}'   # 정렬 JSON


def test_interactive_asks_and_continues_then_stops():        # 대화형: 묻고 "계속"이면 다시 max 만큼
    answers = [True, False]                                  # 첫 질문 계속, 둘째 멈춤
    asked = []                                               # 질문 글

    async def ask(text):                                     # 묻기
        asked.append(text)                                   # 기록
        return answers.pop(0)                                # 차례대로
    g = lg.LoopGuard(max_iterations=2, repeat_failure_limit=0, ask_continue=ask)   # 상한 2 + 묻기
    steps = [call(str(i)) for i in range(10)] + [{"content": "끝"}]   # 계속 도구
    result, rec = turn(steps, [read_tool()], g, headless=False)   # 대화형
    assert len(rec.requests) == 4 and len(asked) == 2        # 2번 → 계속 → 2번 → 멈춤
    assert "반복 상한 2회" in asked[0]                         # 질문 글
    assert result.stopped_by_loop_guard == lg.REASON_MAX_ITERATIONS   # 이유


def test_headless_never_asks():                              # 헤드리스는 ask_continue 가 있어도 묻지 않음
    async def ask(text):                                     # 불리면 안 됨
        raise AssertionError("asked")                        # 실패
    g = lg.LoopGuard(max_iterations=1, repeat_failure_limit=0, ask_continue=ask)   # 상한 1
    result, rec = turn([call("a"), call("b")], [read_tool()], g, headless=True)   # 헤드리스
    assert len(rec.requests) == 1 and result.stopped_by_loop_guard == lg.REASON_MAX_ITERATIONS   # 묻지 않고 멈춤


def test_counts_reset_each_turn():                           # 턴마다 새로 센다
    g = lg.LoopGuard(max_iterations=2, repeat_failure_limit=0)   # 상한 2
    r1, _ = turn([call("a"), {"content": "1"}], [read_tool()], g)   # 2번 호출로 끝나는 턴
    r2, rec = turn([call("b"), {"content": "2"}], [read_tool()], g)   # 다음 턴도 2번
    assert r1.stopped_by_loop_guard is None and r2.stopped_by_loop_guard is None and r2.response == "2"   # 둘 다 정상


def test_quiz_ask_continue_uses_quiz_service():              # 대화형·ACP 공용 묻기 = AskQuestion 창구
    from ncn.tools.ask_question import quiz_service          # 질문 서비스

    async def go(answer):                                    # 질문에 answer 로 답하는 화면
        def on_q(pq):                                        # 화면 처리기
            assert pq.options == [lg.CHOICE_CONTINUE, lg.CHOICE_STOP] and pq.default_answer == lg.CHOICE_STOP   # 선택지·기본 답
            asyncio.get_running_loop().call_soon(quiz_service.answer_question, pq.request_id, answer)   # 답
        quiz_service.on_question_requested.append(on_q)      # 화면 등록
        try:                                                # 해제 보장
            return await lg.quiz_ask_continue("계속할까요?")   # 묻기
        finally:                                            # 끝나면
            quiz_service.on_question_requested.remove(on_q)  # 해제
    assert asyncio.run(go(lg.CHOICE_CONTINUE)) is True       # 계속
    assert asyncio.run(go(lg.CHOICE_STOP)) is False          # 멈춤
    assert asyncio.run(lg.quiz_ask_continue("x")) is False   # 화면 없으면 멈춤


def test_compaction_summary_call_not_guarded():              # 압축 요약 호출(is_compacting)은 감시하지 않음
    from ncn.loop.agent import stream_chat_response          # 루프
    g = lg.LoopGuard(max_iterations=1, repeat_failure_limit=0)   # 상한 1
    g.total = g.since_ask = 5                                 # 이미 넘은 상태
    server, rec = serve([{"content": "요약"}])                # 가짜 LLM
    try:                                                    # 서버 종료 보장
        llm = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="fake-model")   # 클라이언트
        deps = LoopDeps(llm=llm, tools=[], check_permission=lambda t, a: "allow", ask_user=None,   # 의존성
                        system_message=lambda: "SYS", loop_guard=g)   # 감시기
        r = asyncio.run(stream_chat_response(ChatHistory(), deps, headless=True, model_name="m", is_compacting=True,   # 요약 호출
                                             items=[{"message": {"role": "user", "content": "x"}, "contextItems": []}]))   # 기록
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert r.response == "요약" and r.stopped_by_loop_guard is None   # 감시 없이 진행


# ── 헤드리스 끝까지(종료 코드·stderr) ──

def test_headless_exit_code_and_stderr(tmp_path):            # ncn -p: 연속 실패 → 종료 코드 3 + stderr 안내
    steps = [call("missing.txt")] * 4 + [{"content": "끝"}]   # 없는 파일을 계속 읽는 모델
    server, rec = serve(steps)                               # 가짜 LLM
    work, cnhome = tmp_path / "work", tmp_path / "cnhome"     # 작업·전역 폴더
    for d in (work, cnhome, tmp_path / "home"):              # 폴더
        d.mkdir(parents=True, exist_ok=True)                 # 생성
    cfg = cnhome / "config.yaml"                             # 설정
    cfg.write_text(config_yaml(server.server_port), encoding="utf-8", newline="")   # 가짜 LLM 설정
    try:                                                    # 서버 종료 보장
        proc = subprocess.run([sys.executable, "-m", "ncn", "-p", "읽어줘", "--config", str(cfg)], cwd=work,   # ncn -p
                              env=isolated_env(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, timeout=60)   # 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    err = proc.stderr.decode("utf-8", "replace")             # stderr
    assert proc.returncode == lg.EXIT_LOOP_GUARD, err        # 종료 코드 3
    assert "loop.repeat_failure_limit" in err                # 안내
    assert len(rec.requests) == 3                            # 3번 실패 뒤 멈춤


def test_headless_compat_has_no_limit(tmp_path):             # NCN_CN_COMPAT=1 → 상한 없음(cn 과 같은 종료 코드 0)
    steps = [call("missing.txt")] * 4 + [{"content": "끝"}]   # 4번 실패 뒤 끝
    server, rec = serve(steps)                               # 가짜 LLM
    work, cnhome = tmp_path / "work", tmp_path / "cnhome"     # 작업·전역 폴더
    for d in (work, cnhome, tmp_path / "home"):              # 폴더
        d.mkdir(parents=True, exist_ok=True)                 # 생성
    cfg = cnhome / "config.yaml"                             # 설정
    cfg.write_text(config_yaml(server.server_port), encoding="utf-8", newline="")   # 가짜 LLM 설정
    env = {**isolated_env(tmp_path), "NCN_CN_COMPAT": "1"}   # cn 원래 동작
    try:                                                    # 서버 종료 보장
        proc = subprocess.run([sys.executable, "-m", "ncn", "-p", "읽어줘", "--config", str(cfg)], cwd=work,   # ncn -p
                              env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)   # 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert proc.returncode == 0 and len(rec.requests) == 5   # 끝까지
    assert not (cnhome / "ncn-audit").exists()               # [E6] 감사 기록도 꺼짐
    assert proc.stdout.decode("utf-8").strip() == "끝"      # 최종 답


# ── ACP 끝까지(실제 ncn --stdio 자식 프로세스 + 가짜 IDE) ──

def _acp_loop_scenario(tmp_path, steps, ide, caps=None):     # ncn.yaml loop.max_iterations=2 로 ACP 한 턴
    """tests/test_acp.py 의 Harness·FakeIDE 를 쓴다. 돌려주는 값: (Harness, prompt 응답)."""
    from acp.helpers import text_block                       # 질문 블록
    from tests.test_acp import Harness, run                  # ACP 시험 도구
    h = Harness(tmp_path, steps, files={"a.txt": "A\n"})     # 작업 폴더에 a.txt
    (h.cnhome / "ncn.yaml").write_bytes(b"loop:\n  max_iterations: 2\n  repeat_failure_limit: 0\n")   # 반복 상한 2

    async def go():                                          # 시나리오
        await h.start(ide, caps)                             # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("읽어줘")])   # 질문
        await h.stop()                                       # 종료
        return r                                            # 응답
    return h, run(go())                                      # 실행


def test_acp_loop_guard_stop_ends_turn(tmp_path):            # ACP: 상한 → 질문(승인 형식) → "멈춤"이면 턴 끝
    from tests.test_acp import FakeIDE                       # 가짜 IDE
    ide = FakeIDE(["answer_1"])                              # 두 번째 선택지 = 멈춤
    h, r = _acp_loop_scenario(tmp_path, [call("a.txt")] * 3 + [{"content": "끝"}], ide)   # 계속 읽는 모델
    assert r.stop_reason == "end_turn" and len(h.bodies()) == 2   # LLM 2번 뒤 멈춤
    assert len(ide.permissions) == 1                         # 질문 하나(Read 는 허용이라 승인 없음)
    tc = ide.permissions[0]["tool_call"]                     # 질문 카드
    assert tc["toolCallId"].startswith("loopguard-") and tc["title"] == lg.LOOP_GUARD_CARD_TITLE   # 자기 카드·제목
    assert [o["name"] for o in ide.permissions[0]["options"]][:2] == [lg.CHOICE_CONTINUE, lg.CHOICE_STOP]   # 선택지
    starts = [u for u in ide.of("tool_call") if u["toolCallId"] == tc["toolCallId"]]   # 그 카드 시작 알림
    assert len(starts) == 1 and starts[0]["title"] == lg.LOOP_GUARD_CARD_TITLE   # 새 카드
    ends = [u for u in ide.of("tool_call_update") if u["toolCallId"] == tc["toolCallId"]]   # 그 카드 끝 알림
    assert ends and ends[-1]["status"] == "completed" and ends[-1]["rawOutput"] == lg.CHOICE_STOP   # 고른 답
    assert "끝" not in ide.text()                            # 마지막 답까지 가지 않음


def test_acp_loop_guard_continue_after_ask_question(tmp_path):   # ACP: AskQuestion 뒤 상한 질문은 자기 카드(elicitation) → "계속"이면 이어짐
    from acp.schema import ClientCapabilities, ElicitationCapabilities, ElicitationFormCapabilities   # 능력
    from tests.test_acp import FakeIDE                       # 가짜 IDE
    ask = {"tool_calls": [{"name": "AskQuestion", "arguments": {"question": "어느 쪽?", "options": [lg.CHOICE_CONTINUE, "X"]}}]}   # 질문 도구
    steps = [ask, call("a.txt"), call("a.txt"), {"content": "끝"}]   # 질문 → 읽기 → (상한 질문) → 읽기 → 끝
    ide = FakeIDE(elicit={"choice": lg.CHOICE_CONTINUE})     # 모든 질문에 "계속"
    caps = ClientCapabilities(elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities()))   # elicitation 능력
    h, r = _acp_loop_scenario(tmp_path, steps, ide, caps)    # 실행
    assert r.stop_reason == "end_turn" and len(h.bodies()) == 4 and ide.text().endswith("끝")   # 끝까지 이어짐
    assert [e["message"] for e in ide.elicitations][0] == "어느 쪽?"   # 첫 질문 = AskQuestion
    assert ide.elicitations[0]["mode"]["toolCallId"] == "call_0_0"   # AskQuestion 카드
    loop_q = ide.elicitations[1]                             # 상한 질문
    assert "반복 상한 2회" in loop_q["message"]               # 질문 글
    assert loop_q["mode"]["toolCallId"].startswith("loopguard-")   # 이전 AskQuestion 카드에 붙지 않음
    assert len(ide.elicitations) == 2                        # 질문 둘
