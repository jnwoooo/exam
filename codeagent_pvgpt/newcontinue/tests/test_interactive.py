# tests/test_interactive.py — 콘솔 대화형(`ncn`) 시험: prompt_toolkit 시험용 입출력(create_pipe_input·DummyOutput)으로 키를 보내고
# 가짜 LLM(parity/fake_llm.py serve(steps)) 앞에서 같은 프로세스로 InteractiveApp 을 돌린다. 화면 글은 StringIO 로 받는다(색 끔).
# 대화형 cn 규칙(요청 본문): headless 블록 없음 · ask 도구(Write·Edit) 포함 · Exit 없음 — ncn -p·ncn --stdio(ACP)와 비교한다.
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 시험
import io                                                    # 화면 글 받기
import json                                                  # 세션 파일
import os                                                    # 환경변수·작업 폴더
import time                                                  # 기다리기
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구
from prompt_toolkit.input import create_pipe_input           # 시험용 입력(키 보내기)
from prompt_toolkit.output import DummyOutput                # 시험용 출력(그리기 버림)

from ncn import console_render as cr                         # 화면 글
from ncn.app import build_parser                             # 명령줄
from ncn.interactive import TOOL_CANCELED_SYSTEM_MESSAGE, InteractiveApp   # 대화형 앱
from parity.fake_llm import serve                            # 녹화형 가짜 LLM
from parity.run import config_yaml, isolated_env             # 시험 설정·격리 환경

TIMEOUT = 30                                                 # 시나리오 하나 시간 한도(초)
ESC = "\x1b"                                                 # Esc 키
SHIFT_TAB = "\x1b[Z"                                         # Shift+Tab 키
DOWN = "\x1b[B"                                              # ↓ 키
CTRL_C, CTRL_D, CTRL_B = "\x03", "\x04", "\x02"              # Ctrl+C·Ctrl+D·Ctrl+B


class Driver:                                               # 대화형 앱 + 가짜 LLM + 작업 폴더
    """tmp 아래 work·home·cnhome 을 만들고 환경을 격리한 뒤 앱을 돌린다."""

    def __init__(self, tmp: Path, monkeypatch, steps, files=None, args=()):   # 준비
        self.work, self.home, self.cnhome = tmp / "work", tmp / "home", tmp / "cnhome"   # 폴더
        for d in (self.work, self.home, self.cnhome):        # 폴더마다
            d.mkdir(parents=True, exist_ok=True)             # 생성
        for rel, text in (files or {}).items():              # 작업 폴더 파일
            (self.work / rel).write_bytes(text.encode("utf-8"))   # 그대로 씀
        self.server, self.rec = serve(steps)                 # 재생 서버
        self.cfg = self.cnhome / "config.yaml"               # 설정 파일
        self.cfg.write_text(config_yaml(self.server.server_port), encoding="utf-8", newline="")   # 시험 설정
        for k in list(os.environ):                           # 환경 비우기
            monkeypatch.delenv(k, raising=False)             # 시험 뒤 되돌림
        for k, v in isolated_env(tmp).items():               # 격리 환경
            monkeypatch.setenv(k, v)                         # 넣기
        monkeypatch.chdir(self.work)                         # 작업 폴더
        self.args = build_parser().parse_args(["--config", str(self.cfg), *args])   # 명령줄
        self.out = io.StringIO()                             # 화면 글
        self.app: InteractiveApp | None = None               # 앱(run 에서)
        self.code = None                                     # 종료 코드

    def bodies(self) -> list[dict]:                          # LLM 이 받은 요청 본문들
        return [r["body"] for r in self.rec.requests]        # 본문 목록

    def text(self) -> str:                                   # 화면 글
        return self.out.getvalue()                           # 지금까지

    def session(self) -> dict:                               # 저장된 세션 파일
        sid = self.app.core.sessions.get_current()["sessionId"]   # 현재 id
        return json.loads((self.cnhome / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))   # 파일 내용

    def run(self, script, prompt=None):                      # 시나리오 실행
        """script(d, inp) 가 키를 보내고 끝에 종료 키를 보낸다. 앱이 끝날 때까지 기다린다."""
        async def go():                                      # 비동기 본문
            with create_pipe_input() as inp:                 # 시험 입력
                self.app = InteractiveApp(self.args, input=inp, output=DummyOutput(), out=self.out, color=False)   # 앱
                task = asyncio.ensure_future(self.app.run(prompt))   # 실행
                await until(lambda: self.app.ps is not None and self.app.ps.app.is_running or task.done())   # 입력 줄이 뜰 때까지
                try:                                         # 시나리오
                    await script(self, inp)                  # 키 보내기
                finally:                                     # 끝나지 않았으면
                    if not task.done():                      # 아직 돌면
                        self.app.request_exit()              # 종료
                self.code = await asyncio.wait_for(task, TIMEOUT)   # 종료 코드
        try:                                                 # 실행
            asyncio.run(asyncio.wait_for(go(), TIMEOUT * 2))   # 시간 한도
        finally:                                             # 정리
            self.server.shutdown()                           # 가짜 LLM 종료


async def until(cond, t: float = 10.0):                      # 조건이 참이 될 때까지 기다림
    end = time.monotonic() + t                               # 한도
    while not cond():                                        # 아직이면
        if time.monotonic() > end:                           # 넘으면
            raise TimeoutError("condition not met")          # 실패
        await asyncio.sleep(0.02)                            # 잠깐


def idle(d: Driver, n: int):                                 # 요청 n 개를 받고 한가해졌나
    return lambda: len(d.rec.requests) >= n and not d.app.busy and d.app.turn_task is not None and d.app.turn_task.done()   # 조건


def tool_names(body: dict) -> list[str]:                     # 요청의 도구 이름
    return [t["function"]["name"] for t in body.get("tools") or []]   # 이름 목록


# ─────────────────────────────────────────────────────────────────────────────
# 시나리오
# ─────────────────────────────────────────────────────────────────────────────


def test_answer_only_request_rules_and_session_saved(tmp_path, monkeypatch):   # 답만 + 대화형 요청 규칙 + 종료 시 세션 저장
    steps = [{"content": "안녕하세요, **무엇을** 도와드릴까요?"}]   # 답 하나
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 첫 메시지(인자) → 답 → Ctrl+D
        await until(idle(d, 1))                              # 답 받음
        inp.send_text(CTRL_D)                                # 종료

    d.run(script, prompt="안녕")                              # 실행(ncn "안녕")
    assert d.code == 0                                       # 정상 종료
    body = d.bodies()[0]                                     # 첫 요청
    from ncn.sysmsg import HEADLESS_INSTRUCTIONS             # headless 블록
    assert HEADLESS_INSTRUCTIONS not in body["messages"][0]["content"]   # headless 블록 없음
    names = tool_names(body)                                 # 도구
    assert "Exit" not in names and {"Write", "Edit", "Bash", "AskQuestion"} <= set(names)   # Exit 없음·ask 도구 포함
    assert body["messages"][1] == {"role": "user", "content": "안녕"}   # 사용자 메시지 그대로
    assert "● 안녕" in d.text() and "● 안녕하세요, 무엇을 도와드릴까요?" in d.text()   # 화면(굵게 표시는 색 끔이라 글만)
    sess = d.session()                                       # 세션 파일
    assert [i["message"]["role"] for i in sess["history"]] == ["user", "assistant"]   # cn 형식 기록
    assert sess["history"][0]["editorState"] == "안녕"        # user 에 editorState(saveSession 규칙)


def test_request_body_same_as_acp(tmp_path, monkeypatch):    # 같은 시나리오의 시스템 메시지·도구 목록이 ACP 와 같다
    from test_acp import FakeIDE, Harness                    # ACP 시험 도구(자식 프로세스 ncn --stdio)
    from acp.helpers import text_block                       # 질문 블록
    steps = [{"content": "네"}]                               # 답 하나
    h = Harness(tmp_path / "acp", steps)                     # ACP 쪽
    ide = FakeIDE()                                          # 가짜 IDE

    async def acp_go():                                      # ACP 한 질문
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("안녕")])   # 질문
        await h.stop()                                       # 종료

    asyncio.run(asyncio.wait_for(acp_go(), 60))              # 실행
    acp_body = h.bodies()[0]                                 # ACP 첫 요청
    d = Driver(tmp_path / "tui", monkeypatch, steps)         # 대화형 쪽

    async def script(d, inp):                                # 같은 질문
        inp.send_text("안녕\r")                               # 입력
        await until(idle(d, 1))                              # 답 받음
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    tui_body = d.bodies()[0]                                 # 대화형 첫 요청

    def norm(body, work):                                    # 작업 폴더 정규화
        s = json.dumps(body, ensure_ascii=False)             # 글로
        for p in (str(work.resolve()), str(work)):           # 실제·심볼릭 경로
            s = s.replace(p, "<CWD>")                        # 자리표시
        return json.loads(s)                                 # 다시 객체

    a, t = norm(acp_body, h.work), norm(tui_body, d.work)    # 정규화
    assert a["messages"] == t["messages"]                    # 시스템 메시지·사용자 메시지 같음
    assert a["tools"] == t["tools"]                          # 도구 목록(정의 전체) 같음


def test_permission_1_continue(tmp_path, monkeypatch):       # 승인 1 계속
    steps = [{"content": "만들게요", "tool_calls": [{"name": "Write", "arguments": {"filepath": "a.txt", "content": "hi\n"}}]},   # Write 항목
             {"content": "완료"}]                             # 답
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 1 → 계속
        inp.send_text("만들어\r")                             # 질문
        await until(lambda: d.app.choice is not None)        # 승인 질문이 뜸
        assert d.app.choice.kind == "perm" and "1. Continue" in d.app._message()   # 선택지
        assert "Continue + don't ask again" in d.app._message() and "No, and tell Continue what to do differently" in d.app._message()   # 원문 이름
        inp.send_text("1")                                   # 1 계속
        await until(idle(d, 2))                              # 끝
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert (d.work / "a.txt").read_text() == "hi\n"          # 파일 씀
    assert "Would you like to continue?" in d.text() and "File created successfully" in d.text()   # 원문 안내·결과 요약
    assert d.bodies()[1]["messages"][-1]["role"] == "tool"   # 결과가 다음 요청에


def test_permission_2_dont_ask_again(tmp_path, monkeypatch):   # 승인 2 계속 + 다시 묻지 않기(Shift+Tab 키)
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "a.txt", "content": "a"}}]},   # 첫 Write
             {"tool_calls": [{"name": "Write", "arguments": {"filepath": "b.txt", "content": "b"}}]},   # 둘째 Write(안 물어야 함)
             {"content": "끝"}]                              # 답
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비
    asked = []                                               # 승인 질문 횟수

    async def script(d, inp):                                # 2 → 규칙 추가
        inp.send_text("둘 다 만들어\r")                        # 질문
        await until(lambda: d.app.choice is not None)        # 승인 질문
        asked.append(1)                                      # 한 번
        inp.send_text(SHIFT_TAB)                             # Shift+Tab = 2(ToolPermissionSelector)
        await until(idle(d, 3))                              # 끝
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert (d.work / "a.txt").exists() and (d.work / "b.txt").exists()   # 둘 다 씀
    assert asked == [1] and d.text().count("Would you like to continue?") == 1   # 한 번만 물음
    assert "Write" in (d.cnhome / "permissions.yaml").read_text(encoding="utf-8")   # permissions.yaml 에 규칙
    assert d.app.core.perms.mode == "normal"                 # 승인 중 Shift+Tab 은 모드를 바꾸지 않음


def test_permission_3_reject_stops(tmp_path, monkeypatch):   # 승인 3 아니오 → 응답 중단 + system 글
    steps = [{"content": "만들겠습니다.", "tool_calls": [{"name": "Write", "arguments": {"filepath": "new.txt", "content": "x"}}]},   # Write 항목
             {"content": "알겠습니다."}]                       # 다음 질문의 답
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 3 → 멈춤
        inp.send_text("만들어\r")                             # 질문
        await until(lambda: d.app.choice is not None)        # 승인 질문
        inp.send_text("3")                                   # 3 아니오
        await until(lambda: not d.app.busy and d.app.turn_task.done())   # 턴 끝
        assert len(d.bodies()) == 1                          # 더 요청하지 않음
        inp.send_text("그만\r")                               # 다음 질문
        await until(idle(d, 2))                              # 답
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert not (d.work / "new.txt").exists()                 # 안 씀
    msgs = d.bodies()[1]["messages"]                         # 두 번째 요청
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "system", "user"]   # ACP 시험과 같은 순서(cn 대화형)
    assert msgs[3]["content"] == "Permission denied by user" and msgs[4]["content"] == TOOL_CANCELED_SYSTEM_MESSAGE   # 원문
    assert "Cancelled by user" in d.text()                   # 결과 요약 원문


def test_shift_tab_mode_cycle(tmp_path, monkeypatch):        # Shift+Tab 모드 순환 normal → plan → auto → normal
    steps = [{"content": "plan 답"}, {"content": "auto 답"}]   # 답 둘
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비
    seen = []                                                # 모드·맨 아래 줄

    async def script(d, inp):                                # 순환
        seen.append((d.app.core.perms.mode, cr.strip_ansi(d.app._toolbar())))   # 처음
        inp.send_text(SHIFT_TAB)                             # plan
        await until(lambda: d.app.core.perms.mode == "plan")   # 바뀜
        seen.append((d.app.core.perms.mode, cr.strip_ansi(d.app._toolbar())))   # plan 표시
        inp.send_text("계획\r")                               # plan 에서 질문
        await until(idle(d, 1))                              # 답
        inp.send_text(SHIFT_TAB)                             # auto
        await until(lambda: d.app.core.perms.mode == "auto")   # 바뀜
        seen.append((d.app.core.perms.mode, cr.strip_ansi(d.app._toolbar())))   # auto 표시
        inp.send_text("자동\r")                               # auto 에서 질문
        await until(idle(d, 2))                              # 답
        inp.send_text(SHIFT_TAB)                             # normal
        await until(lambda: d.app.core.perms.mode == "normal")   # 바뀜
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert [m for m, _ in seen] == ["normal", "plan", "auto"]   # 순서
    assert "[normal]" in seen[0][1] and "[⏸ plan]" in seen[1][1] and "[⏵⏵ auto]" in seen[2][1]   # 모드 표시(ModeIndicator 원문)
    plan_tools, auto_tools = tool_names(d.bodies()[0]), tool_names(d.bodies()[1])   # 모드별 도구
    assert "Write" not in plan_tools and "Write" in auto_tools   # plan 은 쓰기 도구 없음
    assert d.bodies()[0]["messages"][0]["content"] != d.bodies()[1]["messages"][0]["content"]   # 시스템 메시지도 모드 반영


def test_interrupt_resume_and_queue(tmp_path, monkeypatch):   # Esc 중단 → 빈 Enter 이어 가기 · 바쁠 때 입력은 줄 세움
    steps = [{"content": "느린 답", "delay": 3},              # 첫 요청: 3초 늦게(중단 대상)
             {"content": "이어 간 답"},                         # 빈 Enter 로 다시
             {"content": "첫째 답", "delay": 1},              # 줄 세우기 시험: 바쁜 동안
             {"content": "둘째 답"}]                          # 줄 선 메시지의 답
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 시나리오
        inp.send_text("길게\r")                               # 질문
        await until(lambda: len(d.rec.requests) >= 1 and d.app.busy)   # 요청 중
        t0 = time.monotonic()                                # 시각
        inp.send_text(ESC)                                   # Esc 중단
        await until(lambda: not d.app.busy)                  # 바로 한가해짐
        assert time.monotonic() - t0 < 2.5 and d.app.was_interrupted   # 3초 응답을 기다리지 않음
        assert [i["message"]["role"] for i in d.app.core.history.get_history()] == ["user"]   # 응답은 기록에 없음
        inp.send_text("\r")                                  # 빈 Enter → 이어 가기(handleResumeRequest)
        await until(idle(d, 2))                              # 다시 받은 답
        assert d.bodies()[1]["messages"] == d.bodies()[0]["messages"]   # 같은 기록으로 다시 요청
        inp.send_text("하나\r")                               # 셋째 요청(1초 걸림)
        await until(lambda: len(d.rec.requests) >= 3 and d.app.busy)   # 바쁨
        inp.send_text("둘\r")                                 # 바쁠 때 입력 → 줄 세움
        await until(lambda: len(d.app.queue) == 1)           # 줄 섬
        assert len(d.rec.requests) == 3                      # 아직 안 보냄
        await until(idle(d, 4))                              # 하나씩 처리
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    last = d.bodies()[3]["messages"]                         # 줄 선 메시지 요청
    assert [m["content"] for m in last if m["role"] == "user"] == ["길게", "하나", "둘"]   # 순서대로
    assert "Interrupted" in d.text() and "⏳ 둘" in d.text()   # 중단·줄 섬 표시


def test_double_ctrl_c_exits_and_ctrl_b_signal(tmp_path, monkeypatch):   # Ctrl+C 두 번이면 종료, Ctrl+B 는 백그라운드 신호
    from ncn.tools.background_jobs import background_signal_manager   # Ctrl+B 신호
    d = Driver(tmp_path, monkeypatch, [])                    # 준비(요청 없음)
    fired = []                                               # 신호 받음

    def listener():                                          # 듣는 함수(Bash 도구 대신)
        fired.append(1)                                      # 기록

    async def script(d, inp):                                # 시나리오
        background_signal_manager.on(listener)               # 등록
        try:                                                 # 해제 보장
            inp.send_text(CTRL_B)                            # Ctrl+B
            await until(lambda: fired == [1])                # 신호 감
        finally:                                             # 해제
            background_signal_manager.off(listener)          # 해제
        inp.send_text("지울 글")                               # 입력 중
        await asyncio.sleep(0.1)                             # 반영
        inp.send_text(CTRL_C)                                # 첫 Ctrl+C: 입력 비움 + 안내
        await until(lambda: "ctrl+c to exit" in d.app._toolbar())   # 원문 안내
        assert d.app.ps.default_buffer.text == "" and not d.app.exiting   # 아직 안 끝남
        inp.send_text(CTRL_C)                                # 1초 안 두 번째 → 종료

    d.run(script)                                            # 실행
    assert d.code == 0 and d.app.exiting                     # 종료


def test_slash_commands(tmp_path, monkeypatch):              # 슬래시 명령: /help · /title · 모르는 명령 · /clear · /config(제외) · /exit
    steps = [{"content": "첫 답"}, {"content": "clear 뒤 답"}]   # 답 둘
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 시나리오
        inp.send_text("안녕\r")                               # 질문
        await until(idle(d, 1))                              # 답
        first_sid = d.app.core.sessions.get_current()["sessionId"]   # 첫 세션
        inp.send_text("/help\r")                             # 도움말
        await until(lambda: "Keyboard Shortcuts:" in d.text())   # 원문
        inp.send_text("/title 새 제목\r")                     # 제목
        await until(lambda: 'Session title updated to: "새 제목"' in d.text())   # 원문
        assert d.session()["title"] == "새 제목"              # 저장됨
        inp.send_text("/hel\r")                              # 이름 일부가 맞는 모르는 명령
        await until(lambda: "Unknown command: hel" in d.text())   # 원문
        inp.send_text("/config\r")                           # ncn 이 막은 명령
        await until(lambda: "/config" in d.text() and "지원하지 않습니다" in d.text())   # 안내
        inp.send_text("/clear\r")                            # 새 세션
        await until(lambda: "Chat history cleared" in d.text())   # 원문
        assert d.app.core.sessions.get_current()["sessionId"] != first_sid   # 새 id
        inp.send_text("다시\r")                               # clear 뒤 질문
        await until(idle(d, 2))                              # 답
        inp.send_text("/exit\r")                             # 종료
        await until(lambda: d.app.exiting)                   # /exit 처리됨

    d.run(script)                                            # 실행
    assert d.code == 0 and "Goodbye!" in d.text()            # 원문
    msgs = d.bodies()[0]["messages"]                         # 첫 요청
    assert [m["role"] for m in msgs] == ["system", "user"]   # 슬래시 명령 전
    second = d.bodies()[1]["messages"]                       # clear 뒤 요청
    assert [m["role"] for m in second] == ["system", "system", "user"]   # 안내 system 메시지 + 새 질문(cn 그대로)
    assert second[1]["content"] == "Chat history cleared"    # 원문


def test_unknown_text_after_slash_is_message(tmp_path, monkeypatch):   # 어떤 명령에도 안 걸리는 /글은 일반 메시지(cn 그대로)
    d = Driver(tmp_path, monkeypatch, [{"content": "네"}])   # 준비

    async def script(d, inp):                                # 시나리오
        inp.send_text("/zzzq 경로 이야기\r")                    # 명령 아님
        await until(idle(d, 1))                              # LLM 으로 감
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert d.bodies()[0]["messages"][-1]["content"] == "/zzzq 경로 이야기"   # 그대로 보냄


def test_ask_question_options_and_custom(tmp_path, monkeypatch):   # AskQuestion: ↓ Enter 로 둘째 선택지, 글자를 치면 직접 입력
    steps = [{"tool_calls": [{"name": "AskQuestion", "arguments": {"question": "어느 쪽?", "options": ["A", "B"]}}]},   # 질문 1
             {"tool_calls": [{"name": "AskQuestion", "arguments": {"question": "이름은?", "options": ["X"]}}]},   # 질문 2
             {"content": "좋아요"}]                           # 답
    d = Driver(tmp_path, monkeypatch, steps)                 # 준비

    async def script(d, inp):                                # 시나리오
        inp.send_text("물어봐\r")                             # 질문
        await until(lambda: d.app.choice is not None and d.app.choice.kind == "quiz")   # 질문 뜸
        assert "❯ A" in cr.strip_ansi(d.app._message()) and "(or start typing for custom answer)" in d.app._message()   # 원문 모양
        inp.send_text(DOWN)                                  # ↓ — B
        await until(lambda: d.app.choice is not None and d.app.choice.index == 1)   # 이동
        inp.send_text("\r")                                  # Enter
        await until(lambda: len(d.rec.requests) >= 2 and d.app.choice is not None)   # 둘째 질문
        inp.send_text("홍길동\r")                              # 글자 → 직접 입력 → Enter
        await until(idle(d, 3))                              # 끝
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert d.bodies()[1]["messages"][-1]["content"] == 'User selected option 2: "B"'   # cn 결과 글(ACP 시험과 같음)
    assert "홍길동" in d.bodies()[2]["messages"][-1]["content"]   # 직접 입력 답
    assert "? 어느 쪽?" in d.text()                           # 질문 표시


def test_resume_flag_and_edit_diff(tmp_path, monkeypatch):   # Edit 승인 미리보기 색 diff + --resume 로 이어 가기
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "c.py"}}]},   # Read 항목
             {"tool_calls": [{"name": "Edit", "arguments": {"file_path": "c.py", "old_string": "0.9", "new_string": "0.8"}}]},   # Edit 항목
             {"content": "바꿨습니다."}, {"content": "이어서 답"}]   # 답·이어 간 답
    d = Driver(tmp_path, monkeypatch, steps, files={"c.py": "r = 0.9\n"})   # 준비

    async def script(d, inp):                                # 첫 실행
        inp.send_text("바꿔\r")                               # 질문
        await until(lambda: d.app.choice is not None)        # Edit 승인
        inp.send_text("\r")                                  # Enter = 고른 것(1 계속)
        await until(idle(d, 3))                              # 끝
        inp.send_text(CTRL_D)                                # 종료

    d.run(script)                                            # 실행
    assert (d.work / "c.py").read_text() == "r = 0.8\n"      # 바뀜
    txt = d.text()                                           # 화면
    assert "- r = 0.9" in txt and "+ r = 0.8" in txt and "File edited successfully" in txt   # 색 diff(색 끔)·결과 요약 원문
    sid = d.app.core.sessions.get_current()["sessionId"]     # 세션 id
    server, rec = serve(steps[3:])                           # 둘째 실행용 서버
    d.server, d.rec = server, rec                            # 바꿔 끼움
    d.cfg.write_text(config_yaml(server.server_port), encoding="utf-8", newline="")   # 새 주소
    d.args = build_parser().parse_args(["--config", str(d.cfg), "--resume"])   # --resume 항목
    d.out = io.StringIO()                                    # 새 화면

    async def script2(d, inp):                               # 둘째 실행
        assert d.app.core.sessions.get_current()["sessionId"] == sid   # 같은 세션
        inp.send_text("계속\r")                               # 질문
        await until(idle(d, 1))                              # 답
        inp.send_text(CTRL_D)                                # 종료

    d.run(script2)                                           # 실행
    roles = [m["role"] for m in d.bodies()[0]["messages"]]   # 이어 간 요청
    assert roles[:2] == ["system", "user"] and roles[-1] == "user" and len(roles) > 4   # 지난 기록 + 새 질문
    assert "● 바꿔" in d.text()                               # 지난 기록 보이기


def test_render_units():                                     # 화면 글 함수(색 끔)
    st = cr.Style(False)                                     # 색 없음
    assert cr.result_summary("Read", "", st) == ["⎿  No output"]   # 원문
    assert cr.result_summary("Search", "a\nb", st) == ["⎿  Found 2 matches"]   # 원문 복수형
    assert cr.result_summary("Foo", "Error: bad\nmore", st) == ["⎿  Error: Error: bad..."]   # 원문(오류 앞붙임 그대로)
    assert cr.result_summary("Bash", "1\n2\n3\n4\n5\n6", st)[-1] == "  ... +2 lines"   # 4줄 넘으면 생략
    assert cr.preview_lines("Foo", {"a": 1, "b": 2}, None, st) == ["⎿ Will call Foo with 2 arguments"]   # 원문
    diff = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n ctx\n-old word\n+new word\n"   # 통합 diff
    lines = cr.colored_diff(diff, st)                        # 색 diff
    assert lines == ["1      ctx", "2    - old word", "2    + new word"]   # 번호 4칸 + 기호
    assert cr.colored_diff("", st) == ["No changes detected."]   # 원문
    md = cr.MarkdownStream(cr.Style(True))                   # 색 켬
    out = md.feed("# 제목\n**굵게** `코드`\n```\nx\n```\n")   # 마크다운
    assert out[0] == "\x1b[1m제목\x1b[0m" and "\x1b[1m굵게\x1b[0m" in out[1] and "\x1b[36m코드\x1b[0m" in out[1]   # 제목·굵게·인라인 코드
    assert out[3] == "\x1b[36mx\x1b[0m"                      # 코드 블록 안
    assert cr.strip_ansi(cr.tool_title("Read", {"filepath": "a.py"}, cr.Style(True))) == "Read(a.py)"   # ToolCallTitle 항목


@pytest.mark.parametrize("argv", [["--format", "json"], ["--org", "x"]])   # 대화형에서 막히는 옵션
def test_app_interactive_flag_errors(argv, capsys):          # app.main 대화형 경로의 옵션 검사
    from ncn.app import main                                 # 진입점
    code = main(argv)                                        # 실행
    err = capsys.readouterr().err                            # stderr
    if argv[0] == "--format":                                # -p 전용 옵션
        assert code == 1 and "--format flag can only be used with -p/--print flag" in err   # cn 원문
    else:                                                    # Hub 옵션
        assert code == 2 and "--org" in err                  # 미구현 안내
