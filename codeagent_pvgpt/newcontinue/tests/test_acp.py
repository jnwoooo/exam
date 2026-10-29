# tests/test_acp.py — ncn --stdio(ACP) 시험: SDK 의 Client 쪽으로 가짜 IDE 를 만들고 ncn 을 실제 자식 프로세스로 띄운다
# LLM 은 parity/fake_llm.py(serve(steps))로 재생. 모든 시나리오에서 stdout 의 모든 줄이 JSON 인지 검사(V5).
# 대화형 cn 과의 요청 본문 규칙: 같은 시나리오를 `ncn -p` 와 비교 — 시스템 메시지는 headless 블록만 빠지고, 도구 목록에는 ask 도구가 들어감.
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 시험
import json                                                  # 줄 검사·요청 본문
import os                                                    # 환경변수
import subprocess                                            # ncn -p 비교 실행
import sys                                                   # 파이썬 실행 파일
import threading                                             # 느린 가짜 LLM
import time                                                  # 느린 가짜 LLM
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # 느린 가짜 LLM
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from acp import PROTOCOL_VERSION                             # 규격 버전
from acp.client.connection import ClientSideConnection       # 클라이언트 쪽 연결
from acp.helpers import resource_block, embedded_text_resource, resource_link_block, text_block   # 질문 블록
from acp.schema import (AcceptElicitationResponse, AllowedOutcome, ClientCapabilities, DeniedOutcome,  # 응답 자료형
                        ElicitationCapabilities, ElicitationFormCapabilities, FileSystemCapabilities,  # 능력
                        ReadTextFileResponse, RequestPermissionResponse, WriteTextFileResponse)   # fs 응답

from ncn import fsio                                         # 파일 창구
from ncn.acp import convert                                  # 변환 함수
from parity.fake_llm import serve, sse_chunks, scripted_message   # 녹화형 가짜 LLM
from parity.run import config_yaml, isolated_env             # 시험 설정·격리 환경

TIMEOUT = 60                                                 # 시나리오 하나 시간 한도(초)


# ─────────────────────────────────────────────────────────────────────────────
# 가짜 IDE(ACP Client)
# ─────────────────────────────────────────────────────────────────────────────


class FakeIDE:                                              # acp Client 구현
    """승인 응답을 차례로 꺼내 쓰고, 받은 업데이트·요청을 모두 기록한다."""

    def __init__(self, answers=None, elicit=None, fs_files=None):   # 승인 답·질문 답·IDE 버퍼
        self.answers = list(answers or [])                    # 승인 답(optionId 또는 "cancel")
        self.elicit = elicit                                  # elicitation 답(content dict)
        self.updates: list[dict] = []                         # 받은 session/update(dict)
        self.permissions: list[dict] = []                     # 받은 승인 요청(dict)
        self.elicitations: list[dict] = []                    # 받은 질문
        self.ext: list[tuple] = []                            # 확장 알림
        self.fs_files = fs_files                              # IDE 버퍼(경로 → 글) — 있으면 fs 위임 시험
        self.fs_log: list[tuple] = []                         # fs 요청 기록
        self.conn = None                                      # 에이전트 연결
        self.on_permission = None                             # 승인 요청 때 부를 함수(취소 시험)

    def on_connect(self, conn):                               # 연결 받기
        self.conn = conn                                      # 보관

    async def request_permission(self, session_id, tool_call, options, **kw):   # 승인 요청
        self.permissions.append({"tool_call": tool_call.model_dump(by_alias=True, exclude_none=True),   # 도구 내용
                                 "options": [o.model_dump(by_alias=True, exclude_none=True) for o in options]})   # 선택지
        if self.on_permission is not None:                    # 시험 훅(취소 등)
            return await self.on_permission(session_id)       # 그 결과
        opt = self.answers.pop(0) if self.answers else "allow_once"   # 다음 답
        if opt == "cancel":                                   # 취소
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))   # cancelled 반환
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=opt))   # 선택

    async def session_update(self, session_id, update, **kw):   # 업데이트
        self.updates.append(update.model_dump(by_alias=True, exclude_none=True))   # 기록

    async def create_elicitation(self, message, mode, **kw):   # 질문
        self.elicitations.append({"message": message, "mode": mode.model_dump(by_alias=True, exclude_none=True)})   # 기록
        return AcceptElicitationResponse(action="accept", content=self.elicit or {})   # 수락

    async def read_text_file(self, session_id, path, line=None, limit=None, **kw):   # fs 읽기
        self.fs_log.append(("read", path))                    # 기록
        if path not in (self.fs_files or {}):                 # 버퍼에 없으면
            raise RuntimeError("not open")                     # 거절 → 디스크
        return ReadTextFileResponse(content=self.fs_files[path])   # 버퍼 내용

    async def write_text_file(self, session_id, path, content, **kw):   # fs 쓰기
        self.fs_log.append(("write", path))                   # 기록
        self.fs_files[path] = content                          # 버퍼에 반영
        return WriteTextFileResponse()                         # 빈 응답

    async def ext_notification(self, method, params):          # 확장 알림
        self.ext.append((method, params))                      # 기록

    async def ext_method(self, method, params):               # 확장 요청(없음)
        return {}                                             # 빈 응답

    def text(self) -> str:                                    # 받은 응답 글 전체
        return "".join(u["content"]["text"] for u in self.updates if u["sessionUpdate"] == "agent_message_chunk")   # 조각 이어 붙임

    def of(self, kind: str) -> list[dict]:                    # 종류별 업데이트
        return [u for u in self.updates if u["sessionUpdate"] == kind]   # 거르기


class Harness:                                              # ncn --stdio 자식 프로세스 + 가짜 LLM + 작업 폴더
    """자식 프로세스의 stdout 을 줄 단위로 가로채 기록(JSON 검사)하면서 SDK 클라이언트에 넘긴다."""

    def __init__(self, tmp: Path, steps, files=None, server=None, args=()):   # 준비
        self.tmp = tmp                                        # 임시 폴더
        self.work, self.home, self.cnhome = tmp / "work", tmp / "home", tmp / "cnhome"   # 작업·홈·전역 폴더
        for d in (self.work, self.home, self.cnhome):          # 폴더 만들기
            d.mkdir(parents=True, exist_ok=True)              # 생성
        for rel, text in (files or {}).items():               # 작업 폴더 파일
            (self.work / rel).write_bytes(text.encode("utf-8"))   # 그대로 씀
        if server is None:                                    # 기본 가짜 LLM
            self.server, self.rec = serve(steps)              # 재생 서버
        else:                                                 # 주어진 서버(느린 LLM)
            self.server, self.rec = server                    # 그대로
        self.cfg = self.cnhome / "config.yaml"                # 설정 파일
        self.cfg.write_text(config_yaml(self.server.server_port), encoding="utf-8")   # 시험 설정
        self.args = list(args)                                # 추가 옵션
        self.raw_lines: list[bytes] = []                      # stdout 원본 줄
        self.stderr = b""                                     # stderr 전체

    async def start(self, client: FakeIDE, caps: ClientCapabilities | None = None):   # 프로세스 시작 + initialize
        env = isolated_env(self.tmp)                          # 격리 환경
        env.pop("CONTINUE_CLI_TEST_SESSION_ID", None)         # 세션 id 는 uuid(세션 여러 개)
        self.proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "ncn", "--stdio", "--config", str(self.cfg), *self.args,   # ncn --stdio 항목
                                                         cwd=self.work, env=env, stdin=asyncio.subprocess.PIPE,   # 작업 폴더·환경
                                                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)   # 파이프
        feed = asyncio.StreamReader(limit=50 * 1024 * 1024)   # 클라이언트가 읽을 스트림

        async def tee():                                       # stdout 가로채기
            while True:                                       # 끝까지
                line = await self.proc.stdout.readline()      # 한 줄
                if not line:                                  # 끝
                    feed.feed_eof()                           # 끝 전달
                    return                                    # 종료
                self.raw_lines.append(line)                   # 원본 기록
                feed.feed_data(line)                          # 클라이언트로

        async def err():                                       # stderr 모으기
            self.stderr = await self.proc.stderr.read()       # 전부

        self._tasks = [asyncio.ensure_future(tee()), asyncio.ensure_future(err())]   # 두 작업
        self.conn = ClientSideConnection(client, self.proc.stdin, feed, use_unstable_protocol=True)   # SDK 클라이언트
        init = await self.conn.initialize(protocol_version=PROTOCOL_VERSION, client_capabilities=caps or ClientCapabilities())   # initialize 항목
        return init                                           # 응답

    async def stop(self):                                     # 끝내기
        self.proc.stdin.close()                               # EOF → ncn 종료
        await asyncio.wait_for(self.proc.wait(), TIMEOUT)     # 끝날 때까지
        await asyncio.gather(*self._tasks)                    # 읽기 작업 끝
        await self.conn.close()                               # 연결 닫기
        self.server.shutdown()                                # 가짜 LLM 종료
        assert self.proc.returncode == 0, self.stderr.decode("utf-8", "replace")   # 정상 종료
        self.check_stdout()                                   # stdout 검사

    def check_stdout(self):                                   # V5: stdout 의 모든 줄이 JSON
        assert self.raw_lines, "stdout 이 비었음"              # 응답은 있어야 함
        for line in self.raw_lines:                           # 줄마다
            assert line.endswith(b"\n") and not line.endswith(b"\r\n"), line   # \n 으로 끝(\r\n 금지)
            msg = json.loads(line.decode("utf-8"))            # UTF-8 JSON 항목
            assert msg.get("jsonrpc") == "2.0", line          # JSON-RPC 메시지

    async def new(self, mcp_servers=None):                    # session/new 항목
        return await self.conn.new_session(cwd=str(self.work), mcp_servers=mcp_servers or [])   # 응답

    def bodies(self) -> list[dict]:                           # LLM 이 받은 요청 본문들
        return [r["body"] for r in self.rec.requests]         # 본문 목록


def run(coro):                                              # 시간 한도 안에서 실행
    return asyncio.run(asyncio.wait_for(coro, TIMEOUT))     # 비동기 실행


def tool_names(body: dict) -> list[str]:                    # 요청의 도구 이름
    return [t["function"]["name"] for t in body.get("tools") or []]   # 이름 목록


def headless_body(tmp: Path, steps, prompt: str, files=None) -> dict:   # 같은 시나리오를 ncn -p 로
    """비교용 헤드리스 첫 요청 본문."""
    h = Harness(tmp, steps, files)                           # 같은 준비
    env = isolated_env(tmp)                                  # 격리 환경
    subprocess.run([sys.executable, "-m", "ncn", "-p", prompt, "--config", str(h.cfg)], cwd=h.work, env=env,   # ncn -p 항목
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT)   # 실행
    h.server.shutdown()                                      # 서버 종료
    return h.rec.requests[0]["body"]                         # 첫 요청


# ─────────────────────────────────────────────────────────────────────────────
# 시나리오
# ─────────────────────────────────────────────────────────────────────────────


def test_answer_only_and_interactive_request_rules(tmp_path):   # 답만 + 대화형 요청 규칙
    steps = [{"content": "안녕하세요, 무엇을 도와드릴까요?"}]   # 답 하나
    h = Harness(tmp_path / "a", steps)                       # 준비
    ide = FakeIDE()                                          # 가짜 IDE

    async def go():                                          # 시나리오
        init = await h.start(ide)                            # 시작
        assert init.protocol_version == 1 and init.agent_capabilities.load_session   # 능력
        s = await h.new(mcp_servers=[{"name": "x", "command": "echo", "args": [], "env": []}])   # MCP 서버는 받기만
        assert s.modes.current_mode_id == "normal" and [m.id for m in s.modes.available_modes] == ["normal", "plan", "auto"]   # 모드
        assert s.config_options[0].root.id == "mode" if hasattr(s.config_options[0], "root") else s.config_options[0].id == "mode"   # 설정 선택지
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("안녕")])   # 질문
        assert r.stop_reason == "end_turn"                   # 정상 끝
        await h.stop()                                       # 끝
        return s.session_id                                  # 세션 id

    sid = run(go())                                          # 실행
    assert ide.text() == "안녕하세요, 무엇을 도와드릴까요?"   # 글 조각 합
    assert b"mcpServers" in h.stderr                          # MCP 무시 로그는 stderr 로(stdout 아님)
    body = h.bodies()[0]                                     # 첫 요청
    hb = headless_body(tmp_path / "b", steps, "안녕")          # 같은 시나리오 헤드리스
    from ncn.sysmsg import HEADLESS_INSTRUCTIONS             # headless 블록
    sys_i, sys_h = body["messages"][0]["content"], hb["messages"][0]["content"]   # 두 시스템 메시지
    sys_h = sys_h.replace(str((tmp_path / "b" / "work").resolve()), "<CWD>").replace(str(tmp_path / "b" / "work"), "<CWD>")   # 작업 폴더 정규화
    sys_i = sys_i.replace(str((tmp_path / "a" / "work").resolve()), "<CWD>").replace(str(tmp_path / "a" / "work"), "<CWD>")   # 작업 폴더 정규화
    assert HEADLESS_INSTRUCTIONS not in sys_i and sys_h == sys_i + HEADLESS_INSTRUCTIONS   # headless 블록만 다름
    ti, th = tool_names(body), tool_names(hb)                # 도구 목록
    assert "Exit" in th and "Exit" not in ti                 # Exit 은 헤드리스만
    assert {"Write", "Edit", "Bash"} <= set(ti) and not ({"Write", "Edit"} & set(th))   # ask 도구(Write·Edit)는 대화형에만(Bash 는 헤드리스 allow)
    assert [n for n in ti if n in th] == [n for n in th if n != "Exit"]   # 공통 도구 순서 같음
    assert body["messages"][1] == {"role": "user", "content": "안녕"}   # 사용자 메시지 그대로
    sess = json.loads((h.cnhome / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))   # cn 형식 세션 저장
    assert [i["message"]["role"] for i in sess["history"]] == ["user", "assistant"]   # 기록


def test_read_tool_card(tmp_path):                          # Read 도구 카드
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "coupon.py"}}]}, {"content": "읽었습니다."}]   # Read → 답
    h = Harness(tmp_path, steps, {"coupon.py": "x = 1\n"})   # 준비
    ide = FakeIDE()                                          # IDE 항목

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("읽어줘")])   # 질문
        assert r.stop_reason == "end_turn"                   # 끝
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    start = ide.of("tool_call")[0]                           # 카드
    assert start["kind"] == "read" and start["title"] == "Read(coupon.py)" and start["status"] == "pending"   # kind·title 확인
    assert start["locations"][0]["path"] == str(h.work / "coupon.py") and start["rawInput"] == {"filepath": "coupon.py"}   # 위치·인자
    done = ide.of("tool_call_update")[-1]                    # 결과
    assert done["status"] == "completed" and "x = 1" in done["content"][0]["content"]["text"]   # 결과 글
    assert ide.permissions == []                             # Read 는 묻지 않음


def test_edit_allow_once_diff(tmp_path):                    # Edit 승인 allow_once
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "coupon.py"}}]},   # Read 항목
             {"tool_calls": [{"name": "Edit", "arguments": {"file_path": "coupon.py", "old_string": "0.9", "new_string": "0.8"}}]},   # Edit 항목
             {"content": "바꿨습니다."}]                      # 답
    h = Harness(tmp_path, steps, {"coupon.py": "r = 0.9\n"})   # 준비
    ide = FakeIDE(["allow_once"])                            # 1 계속

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("바꿔줘")])   # 질문
        assert r.stop_reason == "end_turn"                   # 끝
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert (h.work / "coupon.py").read_text() == "r = 0.8\n"   # 파일 바뀜
    p = ide.permissions[0]                                   # 승인 요청
    assert [o["optionId"] for o in p["options"]] == ["allow_once", "allow_always", "reject_once"]   # 선택지 id
    assert [o["name"] for o in p["options"]] == ["Continue", "Continue + don't ask again", "No, and tell Continue what to do differently"]   # cn 원문 이름
    d = p["tool_call"]["content"][0]                         # diff 항목
    assert d["type"] == "diff" and d["oldText"] == "r = 0.9\n" and d["newText"] == "r = 0.8\n" and d["path"].endswith("coupon.py")   # 바뀔 내용
    assert not any(k.startswith("_ncn") for k in p["tool_call"]["rawInput"])   # 내부 키 숨김
    edit_card = [u for u in ide.of("tool_call") if u["kind"] == "edit"][0]   # Edit 카드
    assert edit_card["title"] == "Edit(coupon.py)"           # 제목
    final = [u for u in ide.of("tool_call_update") if u["toolCallId"] == edit_card["toolCallId"] and u.get("status") == "completed"][0]   # 완료
    assert final["content"][0]["type"] == "diff"             # 결과에도 diff
    assert len(h.bodies()) == 3                              # 요청 3번


def test_reject_once_stops_and_records_system_message(tmp_path):   # 거부 reject_once
    steps = [{"content": "만들겠습니다.", "tool_calls": [{"name": "Write", "arguments": {"filepath": "new.txt", "content": "hi\n"}}]},   # Write 항목
             {"content": "알겠습니다."}]                       # 다음 질문의 답
    h = Harness(tmp_path, steps)                             # 준비
    ide = FakeIDE(["reject_once"])                           # 3 아니오

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("만들어줘")])   # 질문
        assert r.stop_reason == "end_turn" and r.field_meta == {"ncn": {"stoppedByRejection": True}}   # 멈춤 표시
        assert len(h.bodies()) == 1                          # 더 요청하지 않음(응답 중단)
        r2 = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("그만")])   # 다음 질문
        assert r2.stop_reason == "end_turn"                  # 끝
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert not (h.work / "new.txt").exists()                 # 안 씀
    upd = ide.of("tool_call_update")[-1]                     # 카드 결과
    assert upd["status"] == "failed" and upd["content"][0]["content"]["text"] == "Permission denied by user"   # cn 거부 글
    msgs = h.bodies()[1]["messages"]                         # 두 번째 요청
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "system", "user"]   # 기록 순서(cn 대화형)
    assert msgs[3]["content"] == "Permission denied by user"   # 도구 결과
    assert msgs[4]["content"] == "[Tool canceled - please tell Continue what to do differently]"   # system 글 원문


def test_reject_once_then_next_tool_in_batch_is_asked(tmp_path):   # 한 응답에 도구 둘: 3번 거부 뒤 다음 도구도 묻는다(cn 대화형)
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "a.txt", "content": "a"}},   # 첫 Write(거부)
                             {"name": "Write", "arguments": {"filepath": "b.txt", "content": "b"}}]},   # 둘째 Write(승인)
             {"content": "알겠어요"}]                          # 다음 질문의 답
    h = Harness(tmp_path, steps)                             # 준비
    ide = FakeIDE(["reject_once", "allow_once"])             # 3 아니오 → 1 계속

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("둘 다")])   # 질문
        assert r.stop_reason == "end_turn" and r.field_meta == {"ncn": {"stoppedByRejection": True}}   # 멈춤 표시 확인
        assert len(h.bodies()) == 1                          # 중단 → 더 요청하지 않음
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("다음")])   # 다음 질문
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert len(ide.permissions) == 2                         # 둘째 도구도 물었음(executeStreamedToolCalls 권한 단계 순서)
    assert not (h.work / "a.txt").exists() and (h.work / "b.txt").read_text() == "b"   # 거부한 것만 안 씀
    msgs = h.bodies()[1]["messages"]                         # 두 번째 요청
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool", "system", "user"]   # system 글은 한 번
    assert msgs[3]["content"] == "Permission denied by user" and "Successfully" in msgs[4]["content"]   # 결과 순서 확인


def test_write_old_text_from_ide_buffer(tmp_path):         # fs 위임이면 Write 카드 oldText 는 fs/read_text_file 로
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "w.txt", "content": "NEW"}}]}, {"content": "끝"}]   # 덮어쓰기
    h = Harness(tmp_path, steps, {"w.txt": "DISK"})          # 디스크 내용
    buf = str(h.work / "w.txt")                              # 버퍼 경로
    ide = FakeIDE(["allow_once"], fs_files={buf: "BUF"})      # 저장 안 한 버퍼

    async def go():                                          # 시나리오
        caps = ClientCapabilities(fs=FileSystemCapabilities(read_text_file=True, write_text_file=True))   # fs 능력
        await h.start(ide, caps)                             # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("써")])   # 질문
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    card = [u for u in ide.of("tool_call") if u["kind"] == "edit"][0]   # Write 카드
    assert card["content"][0]["oldText"] == "BUF" and card["content"][0]["newText"] == "NEW"   # 버퍼 기준 원래 내용
    assert ide.permissions[0]["tool_call"]["content"][0]["oldText"] == "BUF"   # 승인 요청 diff 도
    assert ide.fs_files[buf] == "NEW"                        # 위임 쓰기


def test_allow_always_not_asked_again(tmp_path):            # allow_always 뒤 다시 안 묻기
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "a.txt", "content": "a\n"}}]},   # 첫 Write
             {"tool_calls": [{"name": "Write", "arguments": {"filepath": "b.txt", "content": "b\n"}}]},   # 둘째 Write
             {"content": "끝"}]                               # 답
    h = Harness(tmp_path, steps)                             # 준비
    ide = FakeIDE(["allow_always"])                          # 2 계속+다시 묻지 않기

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("만들어")])   # 질문
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert len(ide.permissions) == 1                         # 한 번만 물음
    assert (h.work / "a.txt").read_text() == "a\n" and (h.work / "b.txt").read_text() == "b\n"   # 둘 다 씀
    assert "Write" in (h.cnhome / "permissions.yaml").read_text(encoding="utf-8")   # 규칙 저장
    w = [u for u in ide.of("tool_call") if u["kind"] == "edit"][0]   # Write 카드
    assert w["content"][0]["type"] == "diff" and "oldText" not in w["content"][0] and w["content"][0]["newText"] == "a\n"   # 새 파일 diff


def test_edit_tool_delegates_fs(tmp_path):                 # V3: fs 능력이 있으면 Edit 가 IDE 버퍼를 읽고 IDE 에 쓴다
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "c.py"}}]},   # Read(버퍼 기준)
             {"tool_calls": [{"name": "Edit", "arguments": {"file_path": "c.py", "old_string": "BUF", "new_string": "NEW"}}]},   # 버퍼에만 있는 글을 바꿈
             {"content": "끝"}]                               # 답
    h = Harness(tmp_path, steps, {"c.py": "DISK\n"})          # 디스크 내용
    buf = str(h.work / "c.py")                               # 버퍼 경로
    ide = FakeIDE(["allow_once"], fs_files={buf: "BUF\n"})    # 저장 안 한 버퍼

    async def go():                                          # 시나리오
        caps = ClientCapabilities(fs=FileSystemCapabilities(read_text_file=True, write_text_file=True))   # fs 능력
        await h.start(ide, caps)                             # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("고쳐")])   # 질문
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert ide.fs_files[buf] == "NEW\n" and (h.work / "c.py").read_text() == "DISK\n"   # IDE 버퍼만 바뀜
    assert ("write", buf) in ide.fs_log                      # fs/write_text_file 위임
    assert "BUF" in h.bodies()[1]["messages"][-1]["content"]   # Read 결과도 버퍼 기준


class _SlowLLM(BaseHTTPRequestHandler):                     # 글을 천천히 보내는 가짜 LLM(취소 시험)
    protocol_version = "HTTP/1.1"                            # 연결 유지
    requests: list = []                                      # 받은 요청

    def log_message(self, *a):                               # 접속 로그 끔
        pass                                                 # 없음

    def do_POST(self):                                       # 스트리밍 응답
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))   # 요청
        type(self).requests.append({"path": self.path, "headers": {}, "body": body})   # 기록
        text, calls = scripted_message({"content": "가" * 400}, 0)   # 긴 글
        self.send_response(200)                              # SSE 시작
        self.send_header("Content-Type", "text/event-stream")   # 형식
        self.send_header("Connection", "close")              # 끝 = 연결 종료
        self.end_headers()                                   # 헤더 끝
        try:                                                # 클라이언트가 끊을 수 있음
            for c in sse_chunks(body.get("model", ""), text, calls, {}, False):   # 조각
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())   # 조각 하나
                self.wfile.flush()                           # 내보냄
                time.sleep(0.05)                             # 천천히(전체 약 2.5초)
            self.wfile.write(b"data: [DONE]\n\n")            # 끝
        except OSError:                                      # 끊김
            pass                                            # 무시
        self.close_connection = True                         # 연결 닫기


class _Rec:                                                 # _SlowLLM 기록 보기
    @property   # 속성으로 노출
    def requests(self):                                      # 요청 목록
        return _SlowLLM.requests                             # 클래스 기록


def test_cancel_during_stream_and_permission(tmp_path):     # 취소(스트림 중 · 승인 중)
    _SlowLLM.requests = []                                   # 기록 초기화
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowLLM)    # 느린 서버
    threading.Thread(target=srv.serve_forever, daemon=True).start()   # 백그라운드
    h = Harness(tmp_path / "s", [], server=(srv, _Rec()))    # 준비
    ide = FakeIDE()                                          # IDE 항목

    async def go():                                          # 스트림 중 취소
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        task = asyncio.ensure_future(h.conn.prompt(session_id=s.session_id, prompt=[text_block("길게")]))   # 질문
        for _ in range(200):                                 # 첫 조각을 기다림
            if ide.text():                                   # 조각이 왔으면
                break                                       # 취소하러
            await asyncio.sleep(0.02)                        # 잠깐
        t0 = time.monotonic()                                # 시각
        await h.conn.cancel(session_id=s.session_id)         # session/cancel 항목
        r = await task                                       # 응답
        assert r.stop_reason == "cancelled" and time.monotonic() - t0 < 2.0   # 바로 cancelled
        assert len(ide.text()) < 400                         # 중간에 멈춤
        await h.stop()                                       # 종료

    run(go())                                                # 실행

    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "c.txt", "content": "c"}}]}, {"content": "x"}]   # 승인 중 취소
    h2 = Harness(tmp_path / "p", steps)                      # 준비
    ide2 = FakeIDE()                                         # IDE 항목

    async def on_perm(session_id):                           # 승인 요청이 오면
        await h2.conn.cancel(session_id=session_id)          # 취소를 보내고
        await asyncio.sleep(0.2)                             # 잠시 뒤
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))   # 규격대로 cancelled 응답

    ide2.on_permission = on_perm                             # 훅 등록

    async def go2():                                         # 시나리오
        await h2.start(ide2)                                 # 시작
        s = await h2.new()                                   # 세션
        r = await h2.conn.prompt(session_id=s.session_id, prompt=[text_block("써")])   # 질문
        assert r.stop_reason == "cancelled"                  # 취소
        await h2.stop()                                      # 종료

    run(go2())                                               # 실행
    assert not (h2.work / "c.txt").exists() and len(h2.bodies()) == 1   # 안 쓰고 더 요청 안 함


def test_mode_switch_plan_blocks_write(tmp_path):           # 모드 전환(plan 에서 Write 막힘)
    steps = [{"tool_calls": [{"name": "Write", "arguments": {"filepath": "p.txt", "content": "p"}}]}, {"content": "막혔네요"}]   # Write 시도
    h = Harness(tmp_path, steps)                             # 준비
    ide = FakeIDE()                                          # IDE 항목

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        await h.conn.set_session_mode(session_id=s.session_id, mode_id="plan")   # plan 으로
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("써")])   # 질문
        assert r.stop_reason == "end_turn"                   # 끝
        cfg = await h.conn.set_config_option(config_id="mode", session_id=s.session_id, value="normal")   # 설정 선택지로 복귀
        opt = cfg.config_options[0]                          # 모드 설정
        assert getattr(opt, "root", opt).current_value == "normal"   # 지금 값
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert [u["currentModeId"] for u in ide.of("current_mode_update")] == ["plan", "normal"]   # 모드 알림
    assert ide.of("config_option_update")                     # 설정 알림
    body = h.bodies()[0]                                     # 첫 요청
    assert "Write" not in tool_names(body) and "planMode" in body["messages"][0]["content"]   # plan 도구·시스템 메시지
    assert h.bodies()[1]["messages"][-1]["content"] == "Command blocked by security policy"   # 정책 거부 글
    assert ide.permissions == [] and not (h.work / "p.txt").exists()   # 묻지 않고 막힘


def test_ask_question_elicitation_and_fallback(tmp_path):   # AskQuestion 항목
    steps = [{"tool_calls": [{"name": "AskQuestion", "arguments": {"question": "어느 쪽?", "options": ["A", "B"]}}]}, {"content": "좋아요"}]   # 질문 도구
    h = Harness(tmp_path / "e", steps)                       # elicitation 능력
    ide = FakeIDE(elicit={"choice": "B"})                    # B 선택

    async def go():                                          # 시나리오
        caps = ClientCapabilities(elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities()))   # elicitation 능력
        await h.start(ide, caps)                             # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("물어봐")])   # 질문
        await h.stop()                                       # 종료

    run(go())                                                # 실행
    assert ide.elicitations[0]["message"] == "어느 쪽?"       # 질문 글
    assert ide.elicitations[0]["mode"]["toolCallId"] == "call_0_0"   # 카드에 연결
    assert h.bodies()[1]["messages"][-1]["content"] == 'User selected option 2: "B"'   # cn 결과 글

    h2 = Harness(tmp_path / "f", steps)                      # 능력 없음 → 승인 형식
    ide2 = FakeIDE(["answer_0"])                             # 첫 선택지

    async def go2():                                         # 시나리오
        await h2.start(ide2)                                 # 시작
        s = await h2.new()                                   # 세션
        await h2.conn.prompt(session_id=s.session_id, prompt=[text_block("물어봐")])   # 질문
        await h2.stop()                                      # 종료

    run(go2())                                               # 실행
    assert [o["name"] for o in ide2.permissions[0]["options"]] == ["A", "B", "Skip"]   # 선택지
    assert h2.bodies()[1]["messages"][-1]["content"] == 'User selected option 1: "A"'   # cn 결과 글


def test_resources_checklist_usage_and_load(tmp_path):      # 리소스 첨부·plan·사용량·session/load·fs 위임
    steps = [{"tool_calls": [{"name": "Checklist", "arguments": {"checklist": "- [x] 읽기\n- [ ] 고치기"}}],   # Checklist 항목
              "usage": {"prompt_tokens": 10, "completion_tokens": 3}},   # 사용량
             {"content": "계획 세움", "usage": {"prompt_tokens": 20, "completion_tokens": 5}},   # 답
             {"content": "이어서 답함"}]                       # 불러온 뒤 답
    h = Harness(tmp_path, steps, {"coupon.py": "r = 0.9\n", "a.py": "A\n"})   # 준비
    buf = str(h.work / "coupon.py")                          # IDE 버퍼 경로
    ide = FakeIDE(fs_files={buf: "r = 0.95  # 저장 안 함\n"})   # 저장 안 한 버퍼

    async def go():                                          # 시나리오
        caps = ClientCapabilities(fs=FileSystemCapabilities(read_text_file=True, write_text_file=True))   # fs 위임 능력
        await h.start(ide, caps)                             # 시작
        s = await h.new()                                    # 세션
        blocks = [text_block("이 파일 "), resource_link_block(name="coupon.py", uri=f"file://{buf}"), text_block(" 봐줘"),   # @파일
                  resource_block(embedded_text_resource(f"file://{h.work / 'a.py'}#L1-1", "A"))]   # 선택 영역
        r = await h.conn.prompt(session_id=s.session_id, prompt=blocks)   # 질문
        assert r.usage.input_tokens == 30 and r.usage.output_tokens == 8 and r.usage.total_tokens == 38   # 턴 사용량 합
        await h.stop()                                       # 종료
        return s.session_id                                  # id 반환

    sid = run(go())                                          # 실행
    user = h.bodies()[0]["messages"][1]["content"]           # 사용자 메시지(contextItems 가 앞에)
    assert user == ('<context name="coupon.py">\nr = 0.95  # 저장 안 함\n\n</context>\n\n'   # IDE 버퍼 내용(위임 읽기)
                    '<context name="a.py#L1-1">\nA\n</context>\n\n이 파일 @coupon.py 봐줘')   # 선택 영역 + @경로
    assert ("read", buf) in ide.fs_log                       # fs/read_text_file 위임
    plan = ide.of("plan")[0]["entries"]                      # plan 항목
    assert [(e["content"], e["status"]) for e in plan] == [("읽기", "completed"), ("고치기", "pending")]   # 체크 줄

    ide2 = FakeIDE()                                         # 새 IDE(재시작)
    h.server, h.rec = serve(steps[2:])                       # 새 가짜 LLM
    h.cfg.write_text(config_yaml(h.server.server_port), encoding="utf-8")   # 주소 갱신
    h.raw_lines = []                                         # stdout 기록 초기화

    async def go2():                                         # session/load 항목
        await h.start(ide2)                                  # 시작
        await h.conn.load_session(cwd=str(h.work), session_id=sid, mcp_servers=[])   # 불러오기
        assert ide2.of("user_message_chunk")[0]["content"]["text"] == "이 파일 @coupon.py 봐줘"   # 되살린 사용자 글
        assert ide2.text() == "계획 세움"                      # 되살린 응답
        r = await h.conn.prompt(session_id=sid, prompt=[text_block("계속")])   # 이어서 질문
        assert r.stop_reason == "end_turn"                   # 끝
        await h.stop()                                       # 종료

    run(go2())                                               # 실행
    roles = [m["role"] for m in h.bodies()[0]["messages"]]    # 이어진 요청
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]   # 이전 기록 포함


# ─────────────────────────────────────────────────────────────────────────────
# 단위 시험(변환·fsio)
# ─────────────────────────────────────────────────────────────────────────────


def test_convert_units():                                   # 변환 함수
    assert [convert.tool_kind(n) for n in ("Read", "Edit", "MultiEdit", "Write", "Bash", "Search", "Fetch", "List")] == \
        ["read", "edit", "edit", "edit", "execute", "search", "fetch", "other"]   # kind 매핑
    assert convert.tool_title("Bash", {"command": "ls\npwd"}) == "Bash(ls...)"   # 여러 줄은 첫 줄 + ...
    assert convert.tool_title("Checklist", {}) == "Checklist"   # 인자 없음
    assert convert.uri_to_path("file:///C:/a%20b/x.cs") == "C:/a b/x.cs"   # Windows 드라이브
    assert convert.uri_to_path("file://server/share/x") == "//server/share/x"   # UNC 확인
    assert convert.uri_to_path("untitled:1") is None           # file 아님
    assert convert.get_last_n_path_parts("C:\\a\\b\\c.txt", 2) == "b/c.txt"   # 끝 두 조각
    assert convert.public_args({"a": 1, "_ncnRedo": 2}) == {"a": 1}   # 내부 키 제거
    assert convert.answer_from_elicitation({"choice": "A", "custom": "  직접 "}, None) == ("직접", True)   # 직접 입력 우선


def test_fsio_backends(tmp_path):                           # fsio: 기본 디스크, 위임, 실패 시 디스크
    f = tmp_path / "x.txt"                                    # 파일
    f.write_text("disk", encoding="utf-8")                    # 디스크 내용

    class Conn:                                              # 가짜 연결
        def __init__(self):                                  # 생성
            self.writes = {}                                 # 쓴 것

        async def read_text_file(self, session_id, path):    # 읽기
            if path.endswith("x.txt"):                       # x.txt 만 버퍼에 있음
                return ReadTextFileResponse(content="buffer")   # 버퍼 내용
            raise RuntimeError("no")                          # 그 밖은 거절

        async def write_text_file(self, session_id, path, content):   # 쓰기
            if path.endswith("deny.txt"):                    # 거절
                raise RuntimeError("deny")                    # 오류
            self.writes[path] = content                       # 기록

    async def go():                                          # 시나리오
        assert await fsio.read_text_file(str(f)) == "disk"   # 기본은 디스크
        conn = Conn()                                        # 연결
        tok = fsio.set_backend(fsio.AcpFiles(conn, "s", can_read=True, can_write=True))   # 위임으로
        try:                                                # 위임 구간
            assert await fsio.read_text_file(str(f)) == "buffer"   # 버퍼 기준
            (tmp_path / "y.txt").write_text("y", encoding="utf-8")   # 버퍼에 없는 파일
            assert await fsio.read_text_file(str(tmp_path / "y.txt")) == "y"   # 거절 → 디스크
            await fsio.write_text_file(str(tmp_path / "w.txt"), "w")   # 위임 쓰기
            assert conn.writes == {str(tmp_path / "w.txt"): "w"} and not (tmp_path / "w.txt").exists()   # IDE 에만
            await fsio.write_text_file(str(tmp_path / "deny.txt"), "d")   # 거절
            assert (tmp_path / "deny.txt").read_text(encoding="utf-8") == "d"   # 디스크로
        finally:                                            # 되돌림
            fsio.reset_backend(tok)                          # 기본으로
        assert await fsio.read_text_file(str(f)) == "disk"   # 다시 디스크

    asyncio.run(go())                                        # 실행


def test_stdio_encode_message_single_line():                # 한 줄 직렬화
    from ncn.acp.stdio import encode_message                 # 대상
    b = encode_message({"jsonrpc": "2.0", "method": "x", "params": {"t": "줄\n바꿈\r\n"}})   # 줄바꿈 포함 글
    assert b.endswith(b"\n") and b.count(b"\n") == 1 and "줄".encode() in b   # 한 줄 UTF-8
