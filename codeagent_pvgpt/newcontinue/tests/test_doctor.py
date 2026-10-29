# tests/test_doctor.py — `ncn --doctor` 점검(ncn/doctor.py) 시험. cn 원본 없음(ncn 전용)
# 가짜 LLM(parity/fake_llm.py)·가짜 MCP(parity/fake_mcp.py)·임시 전역 폴더로 외부 서비스 없이 확인한다.
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 점검 실행
import io                                                    # 가짜 출력 스트림
import json                                                  # JSON 출력 확인
import socket                                                # 빈 포트(연결 거부 시험)
import sys                                                   # 파이썬 경로(가짜 MCP 실행)
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # 401 돌려주는 서버
from pathlib import Path                                     # 경로
import threading                                             # 서버 스레드

import pytest                                                # 시험 도구

from ncn import doctor                                       # 시험 대상
from parity import fake_llm                                  # 가짜 LLM(/v1/models 응답 있음)

PARITY_DIR = Path(__file__).resolve().parent.parent / "parity"   # 가짜 MCP 위치


def write_config(gdir: Path, api_base: str, api_key: str = "${{ secrets.CODEAGENT_TOKEN }}", mcp: str = "") -> Path:   # 시험용 config.yaml
    """continue/config.yaml 과 같은 모양(토큰은 secrets 로)."""
    gdir.mkdir(parents=True, exist_ok=True)                  # 전역 폴더
    p = gdir / "config.yaml"                                 # 설정 파일
    p.write_text("name: t\nversion: 0.0.1\nschema: v1\nmodels:\n  - name: fake\n    provider: openai\n"   # 모델 블록
                 f"    model: fake-model\n    apiBase: {api_base}\n    apiKey: {api_key}\n    roles: [chat]\n" + mcp,   # (이어짐) + MCP
                 encoding="utf-8")                           # UTF-8
    return p                                                 # 경로


def env_for(gdir: Path, home: Path, **extra) -> dict:        # 격리 환경변수
    """실제 ~/.continue 를 보지 않게 CONTINUE_GLOBAL_DIR·HOME 을 임시로."""
    env = {"CONTINUE_GLOBAL_DIR": str(gdir), "HOME": str(home), "USERPROFILE": str(home), "PATH": "", "NO_PROXY": "127.0.0.1,localhost"}   # 기본
    env.update(extra)                                        # 추가
    return env                                               # 환경변수


def by_id(checks, cid):                                      # id 로 결과 찾기
    """결과 목록에서 id 가 같은 첫 항목."""
    return next(c for c in checks if c.id == cid)            # 첫 항목


def run(tmp_path: Path, env: dict, config: str | None = None, **kw):   # 점검 실행 도우미
    """run_checks 를 이벤트 루프에서."""
    work = tmp_path / "work"                                 # 작업 폴더
    work.mkdir(exist_ok=True)                                # 만들기
    return asyncio.run(doctor.run_checks(config, cwd=work, environ=env, stream=io.StringIO(), **kw))   # 실행


def test_mask_secret():                                      # 토큰 가리기
    """앞 3글자 + 길이만 — 값 전체가 나오면 안 된다."""
    assert doctor.mask_secret("ca-abcdef123456") == "ca-…(15자)"   # 가림
    assert doctor.mask_secret("") == "(없음)"                 # 빈 값
    assert doctor.mask_secret(None) == "(없음)"               # 없음


def test_no_config(tmp_path):                                # 설정 파일이 없을 때
    """설정 실패 → chat 모델·서버·MCP 는 건너뜀, 종합 실패."""
    env = env_for(tmp_path / "g", tmp_path / "h")            # 빈 전역 폴더
    checks = run(tmp_path, env)                              # 실행
    assert by_id(checks, "config").status == doctor.FAIL     # 설정 실패
    assert by_id(checks, "chat_model").status == doctor.SKIP   # 건너뜀
    assert by_id(checks, "server").status == doctor.SKIP     # 건너뜀
    assert by_id(checks, "data").status == doctor.OK         # 순위표는 정상
    assert by_id(checks, "version").data["ncn"]              # 버전 값
    assert not doctor.overall_ok(checks)                     # 종합 실패


def test_all_ok_with_fake_llm_and_mcp(tmp_path):             # 정상 경로(서버·MCP 모두 붙음)
    """가짜 LLM /v1/models 200 + 가짜 MCP(stdio) 도구 4개 → 실패 없음. 토큰은 ~/.continue/.env 에서."""
    srv, rec = fake_llm.serve([])                            # 가짜 LLM
    try:                                                     # 끝나면 서버 닫기
        g = tmp_path / "g"                                   # 전역 폴더
        mcp = (f"mcpServers:\n  - name: fake\n    command: \"{sys.executable}\"\n"   # 가짜 MCP(stdio)
               f"    args: [\"{PARITY_DIR / 'fake_mcp.py'}\"]\n")   # (이어짐)
        write_config(g, f"http://127.0.0.1:{srv.server_port}/v1/", mcp=mcp)   # 설정
        (g / ".env").write_text("CODEAGENT_TOKEN=ca-secret-token-123\n", encoding="utf-8")   # 토큰
        checks = run(tmp_path, env_for(g, tmp_path / "h"))   # 실행
    finally:                                                 # 정리
        srv.shutdown()                                       # 서버 끄기
    assert by_id(checks, "config").status == doctor.OK       # 설정 정상
    assert by_id(checks, "token").status == doctor.OK        # 토큰 찾음
    assert by_id(checks, "chat_model").status == doctor.OK   # 모델 정상
    server = by_id(checks, "server")                         # 서버 결과
    assert server.status == doctor.OK, server.detail         # 서버 정상
    assert "parity-model" in server.data["models"]           # 모델 목록
    m = by_id(checks, "mcp:fake")                            # MCP 결과
    assert m.status == doctor.OK, m.detail                   # 접속 정상
    assert m.data["tools"] >= 1                              # 도구 받음
    assert doctor.overall_ok(checks)                         # 실패 없음(rg·PATH 는 경고일 수 있음)
    text = doctor.format_table(checks) + doctor.to_json(checks)   # 두 출력
    assert "ca-secret-token-123" not in text                 # 토큰 값이 새지 않음
    assert "ca-…(19자)" in text                               # 가린 값은 보임


def test_unresolved_secret(tmp_path):                        # 토큰이 없을 때
    """apiKey 의 secrets 를 못 찾으면 chat 모델 실패 + 서버는 건너뜀."""
    g = tmp_path / "g"                                       # 전역 폴더
    write_config(g, "http://127.0.0.1:9/v1/")                # 토큰 없는 설정
    checks = run(tmp_path, env_for(g, tmp_path / "h"))       # 실행
    assert by_id(checks, "token").status == doctor.WARN      # 토큰 없음 경고
    assert by_id(checks, "chat_model").status == doctor.FAIL   # 비밀 못 찾음
    assert by_id(checks, "server").status == doctor.SKIP     # 건너뜀


def _free_port() -> int:                                     # 쓰지 않는 포트
    """잠깐 열었다 닫아 빈 포트 번호를 얻는다(연결 거부 시험)."""
    with socket.socket() as s:                               # 소켓
        s.bind(("127.0.0.1", 0))                             # 자동 포트
        return s.getsockname()[1]                            # 번호


def test_server_refused(tmp_path):                           # 서버가 꺼져 있을 때
    """연결 거부 → 서버 실패 + 할 일 안내."""
    g = tmp_path / "g"                                       # 전역 폴더
    write_config(g, f"http://127.0.0.1:{_free_port()}/v1/", api_key="ca-x")   # 꺼진 주소
    checks = run(tmp_path, env_for(g, tmp_path / "h"), timeout=5)   # 실행
    s = by_id(checks, "server")                              # 서버 결과
    assert s.status == doctor.FAIL                           # 실패
    assert "연결 실패" in s.detail and s.hint                 # 원인·할 일


def test_server_401(tmp_path):                               # 토큰 거부
    """서버가 401 → '토큰 거부' 실패. Authorization 헤더는 Bearer <apiKey>."""
    seen = {}                                                # 받은 헤더

    class H(BaseHTTPRequestHandler):                         # 401 만 돌려주는 처리기
        def log_message(self, *a):                           # 접속 로그 끄기
            pass                                             # 아무것도 안 함

        def do_GET(self):                                    # GET 처리
            seen["auth"] = self.headers.get("Authorization")   # 헤더 기록
            self.send_response(401)                          # 인증 실패
            self.send_header("Content-Length", "0")          # 본문 없음
            self.end_headers()                               # 헤더 끝

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)           # 서버
    threading.Thread(target=srv.serve_forever, daemon=True).start()   # 백그라운드
    try:                                                     # 끝나면 닫기
        g = tmp_path / "g"                                   # 전역 폴더
        write_config(g, f"http://127.0.0.1:{srv.server_port}/v1", api_key="ca-bad")   # 설정
        checks = run(tmp_path, env_for(g, tmp_path / "h"))   # 실행
    finally:                                                 # 정리
        srv.shutdown()                                       # 서버 끄기
    s = by_id(checks, "server")                              # 서버 결과
    assert s.status == doctor.FAIL and "401" in s.detail     # 토큰 거부
    assert seen["auth"] == "Bearer ca-bad"                   # 같은 인증 헤더


def test_mcp_fail(tmp_path):                                 # MCP 접속 실패
    """--fail 로 바로 끝나는 가짜 MCP → 그 서버 줄이 실패."""
    srv, _ = fake_llm.serve([])                              # 서버는 정상
    try:                                                     # 끝나면 닫기
        g = tmp_path / "g"                                   # 전역 폴더
        mcp = (f"mcpServers:\n  - name: broken\n    command: \"{sys.executable}\"\n"   # 실패하는 MCP
               f"    args: [\"{PARITY_DIR / 'fake_mcp.py'}\", \"--fail\"]\n")   # (이어짐)
        write_config(g, f"http://127.0.0.1:{srv.server_port}/v1/", api_key="ca-x", mcp=mcp)   # 설정
        checks = run(tmp_path, env_for(g, tmp_path / "h"))   # 실행
    finally:                                                 # 정리
        srv.shutdown()                                       # 서버 끄기
    m = by_id(checks, "mcp:broken")                          # MCP 결과
    assert m.status == doctor.FAIL                           # 실패
    assert not doctor.overall_ok(checks)                     # 종합 실패


def test_console_encoding():                                 # 콘솔 한글
    """UTF-8 스트림은 정상, ASCII 스트림은 한글을 못 써서 실패."""
    ok = doctor.check_console(io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))   # UTF-8
    bad = doctor.check_console(io.TextIOWrapper(io.BytesIO(), encoding="ascii"))   # ASCII
    assert ok.status == doctor.OK                            # 정상
    assert bad.status == doctor.FAIL and bad.hint            # 실패 + 안내


def test_rg_missing(monkeypatch):                            # rg 없음
    """PATH 에 rg 가 없으면 경고(실패 아님 — 내장 검색)."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)   # 아무것도 못 찾게
    c = doctor.check_rg()                                    # 실행
    assert c.status == doctor.WARN                           # 경고


def test_table_and_json_format():                            # 출력 모양
    """표: 머리줄·상태 글·할 일·종합. JSON: ncn·ok·checks(상태는 영어 값)."""
    checks = [doctor.Check("a", "항목가", doctor.OK, "내용1\n내용2"),   # 정상(여러 줄)
              doctor.Check("b", "항목나", doctor.FAIL, "x", "고치세요")]   # 실패 + 할 일
    t = doctor.format_table(checks)                          # 표
    assert "상태" in t and "실패" in t and "- [실패] 항목나: 고치세요" in t   # 할 일 줄
    assert "종합: 문제 있음 (실패 1 · 경고 0)" in t           # 종합 줄
    lines = t.splitlines()                                   # 줄들
    second = next(i for i, l in enumerate(lines) if l.endswith("내용1"))   # 첫 항목 줄
    assert lines[second + 1].strip() == "내용2"              # 둘째 줄은 들여쓰기
    j = json.loads(doctor.to_json(checks))                   # JSON
    assert j["ok"] is False and [c["status"] for c in j["checks"]] == ["ok", "fail"]   # 값


def test_main_json_and_exit_code(tmp_path, monkeypatch, capsys):   # main() 진입
    """--doctor --format json → stdout 이 JSON 하나, 실패가 있으면 종료 코드 1. --format 이 이상하면 2."""
    import logging                                           # main 이 루트 로거를 끄므로 시험 뒤 되돌린다
    root = logging.getLogger()                               # 루트 로거
    monkeypatch.setattr(root, "handlers", list(root.handlers))   # 처리기 목록 사본(시험 뒤 원래 목록으로)
    monkeypatch.setattr(root, "level", root.level)           # 수준(시험 뒤 원래 값으로)
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(tmp_path / "g"))   # 빈 전역 폴더
    monkeypatch.chdir(tmp_path)                              # 작업 폴더
    code = doctor.main(["--doctor", "--format", "json"])     # 실행
    out = capsys.readouterr().out                            # 출력
    j = json.loads(out)                                      # JSON 하나여야 함
    assert code == 1 and j["ok"] is False                    # 설정 없음 → 실패
    assert doctor.main(["--doctor", "--format", "text"]) == 2   # 지원 안 하는 형식


@pytest.mark.parametrize("v,unresolved", [("${{ secrets.CODEAGENT_TOKEN }}", True), ("ca-1", False), (None, False)])   # 여러 값
def test_is_unresolved_secret(v, unresolved):                # 비밀 템플릿 판정
    """'${{' 가 남아 있으면 못 찾은 비밀."""
    assert doctor.is_unresolved_secret(v) is unresolved      # 판정
