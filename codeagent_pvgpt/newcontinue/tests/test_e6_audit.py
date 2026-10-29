# tests/test_e6_audit.py — [ncn E6] 감사 기록 시험: 로컬 JSONL·인자 요약(원문 없음)·권한/승인 기록·서버 전송·재전송(cn 원본 없음 — ncn/audit.py)
import asyncio                                               # 비동기 실행
import datetime as dt                                        # 고정 시각
import json                                                  # JSON 줄
import subprocess                                            # 헤드리스 실행
import sys                                                   # 파이썬 실행 파일
import threading                                             # 가짜 서버
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # 가짜 감사 서버

import pytest                                                # 시험 도구

from ncn import audit as au                                  # 시험 대상
from ncn.subagent import NcnSettings, parse_ncn_settings     # ncn.yaml 해석
from parity.fake_llm import serve                            # 녹화형 가짜 LLM
from parity.run import config_yaml, isolated_env             # 시험 설정·격리 환경

FIXED = dt.datetime(2026, 10, 30, 23, 59, 58, 123000, tzinfo=dt.timezone.utc)   # 고정 UTC 시각


def lines(path):                                             # JSONL 파일 → 사건 목록
    """빈 줄 제외."""
    return [json.loads(ln) for ln in path.read_bytes().decode("utf-8").splitlines() if ln.strip()]   # 한 줄 한 사건


class FakeTool:                                              # 이름만 있는 가짜 도구
    """wrap_check 시험용."""
    def __init__(self, name):                                # 이름
        self.name = name                                     # 도구 이름


class FakePerms:                                             # base_permission 만 있는 가짜 권한 서비스
    """정적 판정은 늘 allow."""
    mode = "normal"                                          # 모드

    def base_permission(self, name, args=None):              # 정적 판정
        return "allow"                                      # 늘 allow


# ── 설정 ──

def test_parse_audit_settings():                             # ncn.yaml audit 절
    s = NcnSettings()                                        # 기본값
    assert (s.audit_enabled, s.audit_server, s.audit_token_env, s.audit_batch) == (True, None, "CODEAGENT_TOKEN", 20)   # 로컬만
    s = parse_ncn_settings({"audit": {"server": "http://h:8200/", "token_env": "TOK", "batch": 5, "enabled": True}})   # 서버 전송
    assert (s.audit_server, s.audit_token_env, s.audit_batch) == ("http://h:8200", "TOK", 5)   # 끝 / 제거
    s = parse_ncn_settings({"audit": {"server": "ftp://x", "batch": 0, "enabled": "no"}})   # 잘못된 값
    assert (s.audit_server, s.audit_batch, s.audit_enabled) == (None, 20, True)   # 기본값 유지


def test_make_audit_disabled_and_compat(tmp_path, monkeypatch):   # 끄기·cn 원래 동작
    off = parse_ncn_settings({"audit": {"enabled": False}})   # 사용자가 끔
    assert au.make_audit(tmp_path, {}, off, ui="headless") is None   # 기록 안 함
    assert au.make_audit(tmp_path, {}, NcnSettings(), ui="headless") is not None   # 기본은 켬
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                  # cn 원래 동작
    assert au.make_audit(tmp_path, {}, NcnSettings(), ui="headless") is None   # 꺼짐


def test_policy_audit_overrides_user(tmp_path):              # 관리자 정책 audit 이 사용자 enabled:false 를 이김
    from ncn import policy                                   # 정책 모듈(E1)
    if policy.system_policy_path().is_file():                # 실제 시스템 정책이 있으면
        pytest.skip("system policy file present")            # NCN_POLICY_FILE 무시되므로
    p = tmp_path / "policy.yaml"                             # 시험 정책
    p.write_text("audit:\n  server: http://audit.local:8200\n  batch: 7\n", encoding="utf-8")   # 정책 서버
    off = parse_ncn_settings({"audit": {"enabled": False}})   # 사용자는 끔
    a = au.make_audit(tmp_path, {"NCN_POLICY_FILE": str(p), "CODEAGENT_TOKEN": "t"}, off, ui="acp")   # 정책 적용
    assert a is not None and a.server == "http://audit.local:8200" and a.batch == 7 and a.token == "t"   # 정책 값
    a.closed = True                                          # 전송 없이 끝(atexit 은 close 가 해제)
    a.server = None                                          # 스레드 시작 안 함


# ── 요약(원문 없음) ──

def test_summarize_args_no_content():                        # 경로·명령 첫 줄·인자 이름만
    s = au.summarize_args("Write", {"filepath": "src/a.py", "content": "SECRET_CODE = 1\n"})   # 파일 내용
    assert s == {"filepath": "src/a.py", "keys": ["content", "filepath"]}   # 내용 없음
    s = au.summarize_args("Bash", {"command": "export TOKEN=abc123 && make\nrm -rf x", "timeout": 5})   # 명령 여러 줄
    assert s["command"] == "export TOKEN=*** && make" and "rm" not in json.dumps(s)   # 첫 줄 + 비밀 가림
    s = au.summarize_args("Fetch", {"url": "https://x/y?token=1#z"})   # URL 질의
    assert s["url"] == "https://x/y"                         # 질의 제거
    s = au.summarize_args("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"})   # Edit
    assert s == {"file_path": "a.py", "keys": ["file_path", "new_string", "old_string"]}   # 코드 없음
    assert au.first_line("Authorization: Bearer abc.def") == "Authorization: ***"   # 키=값 모양
    assert au.first_line("curl -H 'X: Bearer abc'") == "curl -H 'X: Bearer ***"   # Bearer


# ── 로컬 기록 ──

def test_record_writes_utc_day_file(tmp_path):               # <전역>/ncn-audit/YYYY-MM-DD.jsonl(UTC)
    a = au.AuditLog(tmp_path, ui="interactive", session_id=lambda: "s-1", mode=lambda: "plan", now=lambda: FIXED)   # 기록기
    a.on_event({"type": "turn_start"})                       # 턴 시작
    a.on_event({"type": "tool_start", "name": "Read", "arguments": {"filepath": "a.txt"}, "tool_call_id": "c1"})   # 도구 시작
    a.on_event({"type": "tool_start", "name": "Read", "arguments": {"filepath": "a.txt"}, "tool_call_id": "c1"})   # 한 번 더(사전 처리 실패 흉내)
    a.on_event({"type": "tool_result", "name": "Read", "result": "Content of a.txt:\nTOPSECRET", "status": "done", "tool_call_id": "c1"})   # 결과
    a.on_event({"type": "tool_error", "name": "Edit", "error": "Error executing tool Edit: boom\nline2", "tool_call_id": "c2"})   # 오류
    a.on_event({"type": "usage", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "model": "m"}})   # 사용량
    a.on_event({"type": "content", "text": "TOPSECRET"})     # 글 조각(남기지 않음)
    a.on_event({"type": "turn_end", "aborted": False, "stopped_by_rejection": False, "stopped_by_loop_guard": None})   # 턴 끝
    f = tmp_path / "ncn-audit" / "2026-10-30.jsonl"          # UTC 날짜 파일
    evs = lines(f)                                           # 사건들
    assert [e["kind"] for e in evs] == ["turn_start", "tool_call", "tool_result", "tool_error", "usage", "turn_end"]   # 종류(tool_call 한 번)
    assert evs[0]["ts"] == "2026-10-30T23:59:58.123Z" and evs[0]["session"] == "s-1" and evs[0]["mode"] == "plan"   # 공통 필드
    assert evs[0]["ui"] == "interactive" and len(evs[0]["id"]) == 32   # 화면·id
    assert evs[1]["args"] == {"filepath": "a.txt", "keys": ["filepath"]}   # 인자 요약
    assert evs[3]["error"] == "Error executing tool Edit: boom"   # 첫 줄만
    assert evs[4]["total_tokens"] == 12                      # 토큰
    assert b"TOPSECRET" not in f.read_bytes()                # 결과 글·응답 글은 안 남음
    assert b"\r\n" not in f.read_bytes()                     # 줄바꿈 \n(Windows 도 같게)


def test_wrap_check_and_ask_record_decisions(tmp_path):      # 권한 판정(누가)·사용자 선택
    a = au.AuditLog(tmp_path, ui="acp", now=lambda: FIXED)    # 기록기
    perms = FakePerms()                                      # 정적 판정 allow
    check = a.wrap_check(lambda tool, args: "ask", perms)    # 동적 판정이 ask 로 바꿈
    assert asyncio.run(check(FakeTool("Bash"), {"command": "rm -rf /"})) == "ask"   # 판정 그대로
    assert asyncio.run(a.wrap_check(lambda t, x: "allow", perms)(FakeTool("Read"), {})) == "allow"   # 정책 그대로
    def e2_check(t, x):                                      # 판정하면서 출처를 남기는 판정(PermissionService.check 흉내)
        perms.last_decision_source = "E2"                    # E1·E2 가 출처를 남기면
        return "ask"                                        # 묻기
    asyncio.run(a.wrap_check(e2_check, perms)(FakeTool("Write"), {}))   # 그 값을 씀

    async def asker(name, args, preview, tcid):              # 화면 승인
        return "reject"                                     # 거부
    assert asyncio.run(a.wrap_ask(asker)("Bash", {}, None, "c9")) == "reject"   # 답 그대로
    assert a.wrap_ask(None) is None                          # 헤드리스는 None
    evs = lines(tmp_path / "ncn-audit" / "2026-10-30.jsonl")   # 사건들
    assert [(e["kind"], e.get("decision") or e.get("choice"), e["by"]) for e in evs] == [   # 판정·출처
        ("permission", "ask", "bash_security"), ("permission", "allow", "policy"), ("permission", "ask", "E2"),   # 판정
        ("approval", "reject", "user")]                       # 사용자 선택


def test_real_permission_service_records_e1_e2_sources(tmp_path, monkeypatch):   # 실제 PermissionService 가 출처를 남김
    from ncn import policy                                   # 정책 모듈(E1)
    from ncn.permissions.service import PermissionService    # 권한 서비스
    from ncn.tools.base import Tool                          # 공용 도구 형식
    if policy.system_policy_path().is_file():                # 실제 시스템 정책이 있으면
        pytest.skip("system policy present")                 # 환경변수 정책이 무시되므로 건너뜀
    pf = tmp_path / "policy.yaml"                            # 관리자 정책 파일
    pf.write_bytes(b"exclude: [Fetch]\nforce_ask: {commands: ['npm publish*']}\n")   # E1: Fetch 금지·npm publish 묻기
    monkeypatch.setenv(policy.POLICY_ENV, str(pf))           # 정책 지정
    monkeypatch.delenv("NCN_CN_COMPAT", raising=False)       # E2 켬

    async def run(args, ctx):                                # 쓰지 않는 실행 함수
        return ""                                           # 빈 결과
    t = lambda n: Tool(name=n, display_name=n, description="", parameters={}, run=run)   # 이름만 있는 도구
    perms = PermissionService(global_dir=tmp_path / "gdir", headless=True, allow=["Write"])   # 헤드리스(Bash 기본 allow)
    a = au.AuditLog(tmp_path / "log", ui="headless", now=lambda: FIXED)   # 기록기
    check = a.wrap_check(lambda tool, args: perms.check(tool, args), perms)   # 실제 판정 포장
    cases = [(t("Fetch"), {"url": "http://x"}), (t("Bash"), {"command": "npm publish"}),   # E1 금지·E1 묻기
             (t("Bash"), {"command": "git push origin main"}), (t("Write"), {"filepath": "a"}),   # E2 위험·CLI 허용
             (t("Read"), {"filepath": "a"}), (t("Bash"), {"command": "sudo rm -rf /"})]   # 기본표·Bash 위험 차단
    for tool_, args in cases:                                # 차례로 판정
        asyncio.run(check(tool_, args))                      # 판정
    perms.set_mode("plan")                                   # plan 모드
    asyncio.run(check(t("Read"), {"filepath": "a"}))         # 모드 정책표
    evs = lines(tmp_path / "log" / "ncn-audit" / "2026-10-30.jsonl")   # 사건들
    assert [(e["tool"], e["decision"], e["by"]) for e in evs] == [   # 판정·출처
        ("Fetch", "exclude", "E1"), ("Bash", "ask", "E1"), ("Bash", "ask", "E2"), ("Write", "allow", "user"),   # E1·E2·사용자
        ("Read", "allow", "default"), ("Bash", "exclude", "bash_security"), ("Read", "allow", "mode")]   # 기본·위험 차단·모드


def test_record_never_raises_on_disk_error(tmp_path):        # 디스크 오류는 대화를 막지 않음
    bad = tmp_path / "file"                                  # 폴더 자리에 파일
    bad.write_text("x", encoding="utf-8")                    # 파일
    a = au.AuditLog(bad, ui="headless")                      # 쓸 수 없는 위치
    assert a.record("turn_start") is not None                # 예외 없음


# ── 서버 전송 ──

class _Srv:                                                  # 가짜 감사 서버(POST /audit/events)
    """status 로 응답 코드를 바꾼다. 받은 본문·머리를 모은다."""

    def __init__(self):                                      # 시작
        self.status = 200                                    # 응답 코드
        self.bodies = []                                     # 받은 본문
        self.headers = []                                    # 받은 머리
        srv = self                                          # 처리기에서 쓰기

        class H(BaseHTTPRequestHandler):                     # 처리기
            def log_message(self, *a):                       # 접속 로그 끄기
                pass                                        # 무시

            def do_POST(self):                               # POST
                n = int(self.headers.get("Content-Length") or 0)   # 길이
                srv.bodies.append((self.path, json.loads(self.rfile.read(n))))   # 본문
                srv.headers.append(dict(self.headers))       # 머리
                self.send_response(srv.status)               # 응답 코드
                self.send_header("Content-Length", "2")      # 길이
                self.end_headers()                           # 머리 끝
                self.wfile.write(b"{}")                      # 본문
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)   # 서버
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()   # 백그라운드
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"   # 주소

    def close(self):                                         # 끝
        self.httpd.shutdown()                                # 종료


@pytest.fixture                                              # 시험마다 새 서버
def srv():                                                   # 가짜 감사 서버
    s = _Srv()                                               # 시작
    yield s                                                  # 시험
    s.close()                                                # 종료


def test_send_batches_and_retry_after_failure(tmp_path, srv):   # 실패하면 outbox 에 남고 다음에 다시
    a = au.AuditLog(tmp_path, ui="headless", server=srv.url, token="tok-1", batch=2, now=lambda: FIXED)   # 서버 전송
    a._signal = lambda: None                                 # 백그라운드 스레드 대신 직접 보낸다(결정적 시험)
    for i in range(3):                                       # 사건 3개
        a.record("tool_call", tool=f"T{i}")                  # 기록
    outbox = tmp_path / "ncn-audit" / au.OUTBOX              # 보낼 목록
    assert len(lines(outbox)) == 3                           # 쌓임
    srv.status = 500                                         # 서버 오류
    assert a.send_pending() == 0                             # 못 보냄
    assert len(lines(outbox)) == 3                           # 그대로 남음
    srv.status = 200                                         # 서버 회복
    assert a.send_pending() == 3                             # 다 보냄
    assert not outbox.exists() and not list((tmp_path / "ncn-audit").glob("*.sending"))   # 정리
    sent = [b for p, b in srv.bodies if p == au.ENDPOINT]    # 받은 본문
    assert [len(b["events"]) for b in sent][-2:] == [2, 1]   # batch 2 씩
    assert srv.headers[-1]["Authorization"] == "Bearer tok-1"   # 개인 토큰
    assert [e["tool"] for b in sent[-2:] for e in b["events"]] == ["T0", "T1", "T2"]   # 순서 그대로
    assert len(lines(tmp_path / "ncn-audit" / "2026-10-30.jsonl")) == 3   # 로컬 기록은 그대로


def test_unreachable_server_keeps_outbox(tmp_path):          # 서버가 꺼져 있어도 대화는 계속, outbox 유지
    a = au.AuditLog(tmp_path, ui="headless", server="http://127.0.0.1:9", batch=1)   # 닫힌 포트
    a._signal = lambda: None                                 # 직접 보낸다
    a.record("turn_start")                                   # 기록
    assert a.send_pending() == 0                             # 연결 실패
    assert len(lines(tmp_path / "ncn-audit" / au.OUTBOX)) == 1   # 남음


def test_background_send_on_turn_end_and_close(tmp_path, srv):   # 턴 끝에 백그라운드 전송 → close 가 기다림
    a = au.AuditLog(tmp_path, ui="interactive", server=srv.url, token="t", batch=50)   # 묶음 큼
    a.on_event({"type": "turn_start"})                       # 쌓임(아직 안 보냄)
    a.on_event({"type": "turn_end", "aborted": False})       # 턴 끝 → 전송 신호
    a.close()                                                # 마지막 전송까지 기다림
    got = [e["kind"] for p, b in srv.bodies for e in b["events"]]   # 받은 사건
    assert got == ["turn_start", "turn_end"]                 # 둘 다
    assert not (tmp_path / "ncn-audit" / au.OUTBOX).exists()   # 비움
    assert a.record("x") is None                             # 닫힌 뒤는 무시


def test_previous_outbox_resent_on_start(tmp_path, srv):     # 앞 실행에서 못 보낸 것은 다음 실행이 보냄
    d = tmp_path / "ncn-audit"                               # 폴더
    d.mkdir()                                                # 생성
    (d / au.OUTBOX).write_bytes(b'{"id":"old1","kind":"turn_start"}\n')   # 남은 사건
    a = au.AuditLog(tmp_path, ui="headless", server=srv.url, token="t")   # 시작하면 바로 보냄
    a.close()                                                # 기다림
    assert [e["id"] for p, b in srv.bodies for e in b["events"]] == ["old1"]   # 다시 보냄


# ── 헤드리스 끝까지 ──

def test_headless_writes_audit_without_content(tmp_path):    # ncn -p → 감사 파일, 파일 내용·응답 글 없음
    work, cnhome = tmp_path / "work", tmp_path / "cnhome"     # 작업·전역 폴더
    for d in (work, cnhome, tmp_path / "home"):              # 폴더
        d.mkdir(parents=True, exist_ok=True)                 # 생성
    (work / "secret.txt").write_text("TOPSECRET-CONTENT\n", encoding="utf-8")   # 작업 파일
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "secret.txt"}}]}, {"content": "다 읽음 TOPSECRET-ANSWER"}]   # 읽고 답
    server, rec = serve(steps)                               # 가짜 LLM
    cfg = cnhome / "config.yaml"                             # 설정
    cfg.write_text(config_yaml(server.server_port), encoding="utf-8", newline="")   # 가짜 LLM 설정
    try:                                                    # 서버 종료 보장
        proc = subprocess.run([sys.executable, "-m", "ncn", "-p", "읽어줘", "--config", str(cfg)], cwd=work,   # ncn -p
                              env=isolated_env(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, timeout=60)   # 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")   # 정상
    files = list((cnhome / "ncn-audit").glob("*.jsonl"))     # 감사 파일
    assert len(files) == 1                                   # 하루 파일 하나
    raw = files[0].read_bytes()                              # 원문
    assert b"TOPSECRET" not in raw                           # 내용·답 없음
    evs = lines(files[0])                                    # 사건
    kinds = [e["kind"] for e in evs]                         # 종류
    assert kinds[0] == "turn_start" and kinds[-1] == "turn_end"   # 턴 경계
    assert {"tool_call", "permission", "tool_result", "usage"} <= set(kinds)   # 도구·판정·결과·토큰
    call = next(e for e in evs if e["kind"] == "tool_call")  # 도구 호출
    assert call["tool"] == "Read" and call["args"]["filepath"] == "secret.txt"   # 경로만
    perm = next(e for e in evs if e["kind"] == "permission")   # 판정
    assert perm["decision"] == "allow" and perm["by"] and perm["ui"] == "headless"   # 판정·출처
    assert {e["session"] for e in evs} == {"parity-0001"}    # 세션 id(저장 파일 이름과 같음)
