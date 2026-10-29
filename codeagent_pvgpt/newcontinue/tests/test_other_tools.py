# tests/test_other_tools.py — Bash·CheckBackgroundJob·Search·Fetch·Checklist·Exit·AskQuestion·Skills·Status 등 시험
# cn runTerminalCommand.test.ts·searchCode.test.ts·fetch.test.ts·writeChecklist.test.ts·skills.test.ts 대표 사례
# + Fetch 의 마크다운 변환은 cn 원본 라이브러리(node + jsdom/readability/node-html-markdown)로 만든 기대값과 비교
import asyncio                                               # 도구는 async
import json                                                  # JSON 결과 비교
import os                                                    # 환경변수·경로
import stat                                                  # 가짜 rg 실행 권한
import sys                                                   # 플랫폼
from pathlib import Path                                     # 임시 폴더

import pytest                                                # 시험 틀

pytest.importorskip("ncn.tools.truncate")                    # 공용 자르기(다른 작업자) 가 없으면 건너뜀

from ncn.tools import background_jobs as bj                  # 백그라운드 작업
from ncn.tools import bash as bash_mod                       # Bash
from ncn.tools import exit as exit_mod                       # Exit
from ncn.tools import fetch as fetch_mod                     # Fetch
from ncn.tools import formatters                             # 플랫폼 바꾸기
from ncn.tools import search as search_mod                   # Search
from ncn.tools.ask_question import ASK_QUESTION_TOOL, quiz_service   # AskQuestion
from ncn.tools.base import ContinueError, ToolRunContext     # 공용 형식
from ncn.tools.checklist import CHECKLIST_TOOL               # Checklist
from ncn.tools.misc import (REPORT_FAILURE_TOOL, STATUS_TOOL, UPLOAD_ARTIFACT_TOOL, generate_subagent_tool_description,
                            get_agent_id_from_args, make_subagent_tool)   # 기타
from ncn.tools.skills import make_skills_tool, parse_markdown_rule   # Skills  # Skills 도구와 머리말 파서

UNIX = sys.platform != "win32"                               # 셸 명령 시험은 Unix 에서만


def run(coro):                                              # run: 코루틴을 동기로 실행하는 도우미
    """async 실행 도우미."""
    return asyncio.run(coro)                                # 새 이벤트 루프에서 끝까지 실행


@pytest.fixture(autouse=True)                               # 시험마다 자동 적용되는 환경 초기화
def _clean_env(monkeypatch):                                # _clean_env: 환경변수·작업 목록 정리
    """시험마다 한도 환경변수·내장 검색 끄고, 작업 목록 비우기. 로그인 셸 대신 /bin/sh(빠르고 사용자 설정 영향 없음)."""
    for k in ("CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS", "CONTINUE_CLI_BASH_MAX_OUTPUT_LINES", "NCN_BUILTIN_SEARCH",  # 한도 관련 환경변수를 모두 돌며
              "CONTINUE_CLI_SEARCH_CODE_MAX_RESULTS", "CONTINUE_CLI_SEARCH_CODE_MAX_RESULT_CHARS", "CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH"):  # (이어지는 변수 이름들)
        monkeypatch.delenv(k, raising=False)                # 있으면 지움
    monkeypatch.setenv("SHELL", "/bin/sh")                  # 셸을 /bin/sh 로 고정
    bj.background_job_service.__init__()                     # 작업 목록 초기화
    yield                                                   # 시험 실행 구간
    formatters.PLATFORM_OVERRIDE = None                      # 플랫폼 원래대로


# ───────────────────────── Bash ─────────────────────────
def test_bash_metadata(tmp_path):                           # Bash 메타데이터 시험
    """이름·설명(작업 폴더 포함)·인자 스키마가 cn 원문과 같다."""
    t = bash_mod.make_bash_tool(tmp_path)                   # 작업 폴더로 Bash 도구 생성
    assert t.name == "Bash" and t.display_name == "Bash" and t.readonly is False and t.is_built_in is True  # 이름·표시 이름·읽기전용 여부 확인
    assert t.description == (f"Executes a terminal command and returns the output\n\nCommands are automatically executed from the current working directory ({tmp_path}), "  # 설명에 작업 폴더가 들어감(cn 원문)
                             "so there's no need to change directories with 'cd' commands.\n\nIMPORTANT: To edit files, use Edit/MultiEdit tools instead of bash commands (sed, awk, etc).\n")
    assert list(t.parameters) == ["type", "required", "properties"]   # 키 순서
    assert list(t.parameters["properties"]) == ["command", "timeout"]  # 인자 이름 순서
    assert t.parameters["properties"]["timeout"]["description"] == "Optional timeout in seconds (max 600). Use this parameter for commands that take longer than the default 180 second timeout."  # timeout 설명 원문


def test_bash_shell_command_by_platform(monkeypatch, cn_compat_env):  # 플랫폼별 셸 명령 시험(cn 명령 모양 — [ncn 부록B B4] 고친 win32 앞부분은 test_ncn_fixes)
    """Windows 는 powershell.exe, 그 밖은 $SHELL -l -c(없으면 /bin/bash)."""
    formatters.PLATFORM_OVERRIDE = "win32"                  # win32 로 가장
    assert bash_mod.get_shell_command("dir") == ("powershell.exe", ["-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", "dir"])  # powershell 명령 형태
    formatters.PLATFORM_OVERRIDE = "darwin"                 # darwin 으로 가장
    monkeypatch.setenv("SHELL", "/bin/zsh")                 # SHELL 지정
    assert bash_mod.get_shell_command("ls") == ("/bin/zsh", ["-l", "-c", "ls"])  # $SHELL -l -c 형태
    monkeypatch.delenv("SHELL")                             # SHELL 없애기
    assert bash_mod.get_shell_command("ls") == ("/bin/bash", ["-l", "-c", "ls"])  # 없으면 /bin/bash
    assert bash_mod.is_running_in_wsl() is False             # linux 가 아니면 WSL 아님


def test_bash_preprocess():                                 # Bash 미리보기 시험
    """미리보기 'Will run: …'(60자 넘으면 …), 빈 명령은 오류."""
    t = bash_mod.make_bash_tool("/")                        # Bash 도구 생성
    r = run(t.preprocess({"command": "echo hi"}))           # 짧은 명령 전처리
    assert r.preview[0].content == "Will run: echo hi" and r.args == {"command": "echo hi"}  # 미리보기 글과 인자 확인
    long = "x" * 70                                         # 70자짜리 명령
    assert run(t.preprocess({"command": long})).preview[0].content == "Will run: " + "x" * 60 + "..."  # 60자 뒤에 ... 붙여 자름
    with pytest.raises(Exception, match="command arg is required and must be a non-empty string"):  # 빈 명령은 오류
        run(t.preprocess({"command": ""}))                  # 빈 명령 전처리 시도


def test_bash_policy_uses_terminal_security(monkeypatch):   # 정책이 terminal_security 를 호출하는지 시험
    """evaluate_tool_call_policy 는 terminal_security 를 부른다(바꿔 끼울 수 있음)."""
    ts = pytest.importorskip("ncn.security.terminal_security")  # 보안 모듈이 없으면 건너뜀
    seen = {}                                               # 호출 인자 기록용
    monkeypatch.setattr(ts, "evaluate_terminal_command_security", lambda base, cmd: seen.setdefault("x", (base, cmd)) and "disabled")  # 보안 평가 함수를 가짜로 바꿈
    t = bash_mod.make_bash_tool("/")                        # Bash 도구 생성
    assert t.evaluate_tool_call_policy("allowedWithPermission", {"command": "rm -rf /"}) == "disabled"  # 가짜 결과 disabled 가 반환됨
    assert seen["x"] == ("allowedWithPermission", "rm -rf /")  # 인자가 그대로 전달됐는지 확인


@pytest.mark.skipif(not UNIX, reason="Unix 셸")              # Unix 셸에서만 실행
def test_bash_run_basic(tmp_path):                          # Bash 기본 실행 시험
    """echo·pwd·stderr 붙이기·실패 종료·출력 없는 실패(cn 예: 'hello world', 'Darwin/Linux')."""
    t = bash_mod.make_bash_tool(tmp_path)                   # 작업 폴더로 도구 생성
    ctx = ToolRunContext("t1")                              # 실행 컨텍스트
    assert run(t.run({"command": 'echo "hello world"'}, ctx)).strip() == "hello world"  # echo 결과 확인
    assert run(t.run({"command": "pwd"}, ctx)).strip() == os.path.realpath(tmp_path)   # cwd 에서 실행
    assert run(t.run({"command": "uname -s"}, ctx)).strip() in ("Darwin", "Linux")  # OS 이름 확인
    assert run(t.run({"command": "echo out; echo err 1>&2"}, ctx)) == "out\n\nStderr: err\n"  # stderr 는 'Stderr:' 로 덧붙음
    with pytest.raises(bash_mod.BashCommandError) as ei:     # 0 아님 + stderr → 오류
        run(t.run({"command": "echo bad 1>&2; exit 3"}, ctx))  # 실패하는 명령 실행
    assert str(ei.value) == "Error (exit code 3): bad\n"    # 오류 글 형식 확인
    assert run(t.run({"command": "echo ok; exit 2"}, ctx)) == "ok\n"   # stderr 없으면 출력 그대로
    with pytest.raises(bash_mod.BashCommandError, match=r"Error \(exit code|not found"):   # cn 시험: 없는 명령
        run(t.run({"command": "definitely-not-a-real-command-xyz123"}, ctx))  # 없는 명령 실행


@pytest.mark.skipif(not UNIX, reason="Unix 셸")              # Unix 셸에서만 실행
def test_bash_timeout_no_output():                          # 출력 없이 시간 초과 시험
    """출력 없이 시간 초과 → 죽이고 안내 글."""
    t = bash_mod.make_bash_tool("/")                        # Bash 도구 생성
    out = run(t.run({"command": "echo start; sleep 5", "timeout": 1}, ToolRunContext("t")))  # 1초 timeout 으로 오래 걸리는 명령 실행
    assert out == "start\n\n\n[Command timed out after 1 seconds of no output]"  # 시간 초과 안내 글 확인


@pytest.mark.skipif(not UNIX, reason="Unix 셸")              # Unix 셸에서만 실행
def test_bash_truncation_and_parallel_note(monkeypatch):    # 출력 자르기·병렬 안내 시험
    """줄 한도 → '(previous N lines truncated)'. 동시 실행이면 한도를 나누고 안내(천 단위 쉼표)."""
    t = bash_mod.make_bash_tool("/")                        # Bash 도구 생성
    out = run(t.run({"command": "seq 1 1500"}, ToolRunContext("t")))  # 1500줄 출력
    assert out.startswith("(previous 501 lines truncated)\n\n502\n") and out.endswith("1500\n")   # 끝 빈 줄도 한 줄
    out2 = run(t.run({"command": "seq 1 1500"}, ToolRunContext("t", parallel_tool_call_count=2)))  # 병렬 2개로 실행
    assert out2.startswith("(previous 1001 lines truncated)")  # 한도가 절반이라 더 많이 잘림
    assert out2.endswith("\n\n(Note: output limit reduced due to 2 parallel tool calls. Single-tool limit: 50,000 characters or 1,000 lines.)")  # 병렬 감소 안내 글
    monkeypatch.setenv("CONTINUE_CLI_BASH_MAX_OUTPUT_LINES", "10")  # 줄 한도를 10으로
    assert run(t.run({"command": "seq 1 20"}, ToolRunContext("t"))).startswith("(previous 11 lines truncated)\n\n12\n")  # 작은 한도로 잘림 확인


@pytest.mark.skipif(not UNIX, reason="Unix 셸")              # Unix 셸에서만 실행
def test_bash_move_to_background_and_check_job():           # 백그라운드 전환 시험
    """Ctrl+B 신호 → 백그라운드 작업으로 넘기고 CheckBackgroundJob 으로 확인."""
    t = bash_mod.make_bash_tool("/")                        # Bash 도구 생성
    before = bj.background_signal_manager.listener_count()   # (cn: 시간 초과 경로는 듣기를 떼지 않아 남을 수 있음 — 그대로 옮김)

    async def scenario():                                   # 시나리오 코루틴
        task = asyncio.ensure_future(t.run({"command": "echo first; sleep 0.5; echo second"}, ToolRunContext("t")))  # 명령을 비동기로 시작
        await asyncio.sleep(0.3)                             # 첫 출력이 올 때까지
        bj.background_signal_manager.signal_background()     # Ctrl+B  # Ctrl+B 신호 보냄
        res = await task                                    # 전환 결과 기다림
        job_id = res.split("Job ID: ")[1].split("\n")[0]    # 결과에서 작업 ID 추출
        assert res == f'Command moved to background. Job ID: {job_id}\nOutput so far:\nfirst\n\nUse CheckBackgroundJob("{job_id}") to check status.'  # 안내 글 확인
        await asyncio.sleep(0.8)                             # 끝날 때까지
        info = json.loads(await bj.CHECK_BACKGROUND_JOB_TOOL.run({"job_id": job_id}))  # 작업 상태 조회
        return job_id, info                                 # ID 와 상태 반환
    job_id, info = run(scenario())                          # 시나리오 실행
    assert info["job_id"] == job_id and info["status"] == "completed" and info["exit_code"] == 0  # 완료·종료 코드 0
    assert info["output"] == "first\nsecond\n"              # 전체 출력 확인
    assert list(info) == ["job_id", "status", "command", "output", "started_at", "ended_at", "duration_seconds", "exit_code"]   # 키 순서
    assert bj.background_signal_manager.listener_count() == before   # 이 명령의 듣기는 해제


# ───────────────────────── BackgroundJobService ─────────────────────────
def test_background_job_limits_and_output():                # 작업 서비스 한도·출력 시험
    """동시 5개 한도, 출력 1000줄 유지, 완료·취소 상태."""
    svc = bj.BackgroundJobService()                         # 새 서비스
    jobs = [svc.create_job(f"cmd{i}") for i in range(5)]    # 작업 5개 만들기
    assert all(jobs) and svc.create_job("x") is None         # 6번째 실패
    assert jobs[0].id.startswith("bg-1-") and jobs[4].id.startswith("bg-5-")  # 작업 ID 접두사 확인
    svc.append_output(jobs[0].id, "\n".join(str(i) for i in range(1500)))  # 1500줄 출력 추가
    assert jobs[0].output.split("\n")[0] == "500" and len(jobs[0].output.split("\n")) == 1000  # 1000줄만 유지
    svc.complete_job(jobs[0].id, 0)                         # 첫 작업 정상 완료
    svc.complete_job(jobs[1].id, 2)                         # 둘째 작업 실패 종료
    assert jobs[0].status == "completed" and jobs[1].status == "failed" and jobs[1].exit_code == 2  # 상태 확인
    assert svc.cancel_job(jobs[2].id) and jobs[2].status == "cancelled"  # 세 번째 취소
    svc.complete_job(jobs[2].id, 0)                          # 취소된 작업은 그대로
    assert jobs[2].status == "cancelled" and svc.cancel_job("nope") is False  # 상태·없는 ID 취소 결과
    assert svc.get_running_job_count() == 2                 # 실행 중인 작업 수


def test_check_background_job_not_found():                  # 없는 작업 조회 시험
    """없는 작업 → 한 줄 JSON + 목록(명령은 50자에서 자름)."""
    svc = bj.background_job_service                         # 전역 작업 서비스
    svc.create_job("a" * 60)                                # 60자 명령의 작업 생성
    out = run(bj.CHECK_BACKGROUND_JOB_TOOL.run({"job_id": "bg-9"}))  # 없는 ID 조회
    d = json.loads(out)                                     # JSON 해석
    assert out.startswith('{"error":"Job bg-9 not found","available_jobs":[{"id":"bg-1-')   # 공백 없는 JSON
    assert d["available_jobs"][0]["command"] == "a" * 50 + "..." and d["available_jobs"][0]["status"] == "pending"  # 명령은 50자에서 자름
    assert bj.CHECK_BACKGROUND_JOB_TOOL.parameters["properties"]["job_id"]["description"] == "The ID of the background job to check (e.g., bg-1-1234567890)"  # 인자 설명 원문


def test_iso_string():                                      # ISO 문자열 형식 시험
    """toISOString 형식."""
    from datetime import datetime, timezone                 # 날짜 클래스 가져옴
    assert bj.iso_string(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)) == "2026-01-02T03:04:05.678Z"  # 밀리초와 Z 로 끝나는 형식


# ───────────────────────── Search ─────────────────────────
def _make_tree(root: Path) -> None:                         # 검색용 파일 트리 만들기
    """검색용 파일들."""
    (root / "src").mkdir()                                  # src 폴더
    (root / "src" / "a.py").write_text("def foo():\n    return 'bar'\n", encoding="utf-8")  # 파이썬 파일
    (root / "src" / "b.ts").write_text("const foo = 1;\r\nlet x = foo;\n", encoding="utf-8")  # CRLF 가 낀 ts 파일
    (root / "build").mkdir()                                # build 폴더(무시 대상)
    (root / "build" / "out.py").write_text("foo = 'built'\n", encoding="utf-8")  # build 안 파일
    (root / ".hidden").write_text("foo hidden\n", encoding="utf-8")  # 숨김 파일
    (root / "bin.dat").write_bytes(b"foo\x00bar")           # 이진 파일
    (root / ".gitignore").write_text("# c\nbuild/\n!keep\n\n", encoding="utf-8")  # .gitignore 파일


def test_search_metadata_and_preprocess():                  # Search 메타데이터·미리보기 시험
    """이름·설명·인자, 미리보기(50자)."""
    t = search_mod.make_search_tool("/")                    # Search 도구 생성
    assert t.description == "Search the codebase using ripgrep (rg) for a specific pattern" and t.readonly is True  # 설명·읽기전용 확인
    assert list(t.parameters["properties"]) == ["pattern", "path", "file_pattern"]  # 인자 이름 순서
    assert run(t.preprocess({"pattern": "foo"})).preview[0].content == 'Will search for: "foo"'  # 짧은 패턴 미리보기
    assert run(t.preprocess({"pattern": "p" * 55})).preview[0].content == 'Will search for: "' + "p" * 50 + '..."'  # 50자 뒤 ... 로 자름


def test_gitignore_patterns(tmp_path):                      # .gitignore 패턴 읽기 시험
    """가장 가까운 .gitignore — 주석·빈 줄·! 제외."""
    _make_tree(tmp_path)                                    # 파일 트리 생성
    assert search_mod.get_gitignore_patterns(str(tmp_path / "src")) == ["build/"]  # 주석·빈 줄·! 제외한 패턴


def test_builtin_search_rg_format(tmp_path, monkeypatch):   # 내장 검색의 rg 형식 시험
    """[추가] 내장 검색: rg 와 같은 '경로:줄:내용', 숨김·이진·.gitignore(-g !) 제외, \\r 유지, 이름순."""
    _make_tree(tmp_path)                                    # 파일 트리 생성
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")           # 내장 검색 켬
    monkeypatch.setattr(search_mod, "check_if_ripgrep_is_installed", lambda cwd=None: _false())  # rg 없음으로 속임
    t = search_mod.make_search_tool(tmp_path)               # Search 도구 생성
    out = run(t.run({"pattern": "foo", "path": "."}))       # foo 검색
    assert out == ('Search results for pattern "foo":\n\n'  # 결과 헤더와 내용 확인
                   "./src/a.py:1:def foo():\n./src/b.ts:1:const foo = 1;\r\n./src/b.ts:2:let x = foo;\n")  # CRLF 의 \r 유지, 이름순
    out2 = run(t.run({"pattern": "foo", "path": "src", "file_pattern": "*.py"}))  # 파일 패턴 지정 검색
    assert out2 == 'Search results for pattern "foo" in files matching "*.py":\n\nsrc/a.py:1:def foo():\n'  # 헤더에 파일 패턴이 들어감
    assert run(t.run({"pattern": "zzz"})) == 'No matches found for pattern "zzz".'  # 일치 없음 안내
    assert run(t.run({"pattern": "zzz", "file_pattern": "*.ts"})) == 'No matches found for pattern "zzz" in files matching "*.ts".'  # 파일 패턴 있는 일치 없음 안내
    with pytest.raises(ContinueError, match="Path does not exist: nope"):  # 없는 경로는 오류
        run(t.run({"pattern": "x", "path": "nope"}))        # 없는 경로 검색 시도


async def _false():                                         # rg 없음을 알리는 비동기 도우미
    """rg 없음."""
    return False                                            # 항상 False


def test_search_limits(tmp_path, monkeypatch):              # 검색 결과 한도 시험
    """100개 넘으면 잘림 안내, 1000자 넘는 줄은 버림(cn searchCode.test.ts)."""
    lines = [f"const foo = 'bar'; // {i}" for i in range(150)] + ["foo" + "a" * 1001]  # 151개 일치 줄 만들기
    (tmp_path / "f.ts").write_text("\n".join(lines) + "\n", encoding="utf-8")  # 파일로 저장
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")           # 내장 검색 켬
    monkeypatch.setattr(search_mod, "check_if_ripgrep_is_installed", lambda cwd=None: _false())  # rg 없음으로 속임
    t = search_mod.make_search_tool(tmp_path)               # Search 도구 생성
    out = run(t.run({"pattern": "foo", "path": "."}))       # 검색 실행
    assert out.endswith("\n\n[Results truncated: showing 100 of 151 matches]")   # 끝 빈 줄 포함 151(cn 그대로)
    body = out.split("\n\n")[1].split("\n")                 # 결과 본문 줄
    assert len(body) == 100 and "a" * 1001 not in out       # 100줄만, 긴 줄은 버림


@pytest.mark.skipif(not UNIX, reason="가짜 rg 는 sh 스크립트")     # Unix 에서만(가짜 rg 는 sh 스크립트)
def test_search_with_fake_rg_command(tmp_path, monkeypatch):  # 가짜 rg 로 명령 글 시험
    """rg 가 있으면 cn 과 같은 명령 글(큰따옴표, -g 제외 패턴)로 셸 실행 — 가짜 rg 가 받은 인자를 출력."""
    _make_tree(tmp_path)                                    # 파일 트리 생성
    bindir = tmp_path / "bin"                               # 가짜 rg 폴더
    bindir.mkdir()                                          # 폴더 만들기
    rg = bindir / "rg"                                      # 가짜 rg 경로
    rg.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ripgrep 14"; exit 0; fi\n'  # 가짜 rg 스크립트 작성
                  'if [ "$5" = "nomatch" ]; then exit 1; fi\n'  # nomatch 면 종료 코드 1
                  'if [ "$5" = "boom" ]; then echo "bad regex" 1>&2; exit 2; fi\n'  # boom 이면 stderr 와 종료 코드 2
                  'printf "%s|" "$@"; echo\n', encoding="utf-8")  # 그 밖엔 받은 인자를 출력
    rg.chmod(rg.stat().st_mode | stat.S_IEXEC)              # 실행 권한 부여
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")  # PATH 앞에 가짜 rg 폴더
    assert run(search_mod.check_if_ripgrep_is_installed(str(tmp_path))) is True  # rg 설치 확인이 True
    t = search_mod.make_search_tool(tmp_path)               # Search 도구 생성
    out = run(t.run({"pattern": "foo", "path": "src", "file_pattern": "*.py"}))  # 인자가 그대로 전달되는 검색
    assert out == ('Search results for pattern "foo" in files matching "*.py":\n\n'  # 결과 헤더 확인
                   "--line-number|--with-filename|--color|never|foo|-g|*.py|-g|!build/|src|\n")  # 가짜 rg 가 받은 인자 확인
    assert run(t.run({"pattern": "nomatch"})) == 'No matches found for pattern "nomatch".'   # 종료 코드 1
    with pytest.raises(Exception) as ei:                     # 종료 코드 2 → exec 오류 글
        run(t.run({"pattern": "boom", "path": "src"}))      # 오류 나는 검색
    assert str(ei.value) == f'Error executing search: Command failed: rg --line-number --with-filename --color never "boom" -g "!build/" "src"\nbad regex\n'  # 오류 글 형식 확인


# ───────────────────────── Fetch ─────────────────────────
def test_fetch_metadata_and_preprocess():                   # Fetch 메타데이터·미리보기 시험
    """cn fetch.test.ts — 메타데이터·미리보기."""
    t = fetch_mod.FETCH_TOOL                                # Fetch 도구
    assert t.name == "Fetch" and t.display_name == "Fetch" and t.readonly is True and t.is_built_in is True  # 이름·읽기전용 확인
    assert t.description == "Fetches content from a URL, converts to markdown, and handles long content with truncation"  # 설명 원문
    assert t.parameters == {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "description": "The URL to fetch content from"}}}  # 인자 스키마 원문
    r = run(t.preprocess({"url": "https://example.com"}))   # 미리보기 전처리
    assert r.preview[0].type == "text" and r.preview[0].content == "Will fetch: https://example.com"  # 미리보기 글 확인


def test_fetch_run_with_mocked_items(monkeypatch):          # 모의 항목으로 Fetch 실행 시험
    """cn fetch.test.ts — 경고 항목 제외, 여러 항목 이어 붙이기, 빈 결과 오류, 오류 감싸기."""
    async def impl(items):                                  # 항목을 돌려주는 가짜 구현 만들기
        async def f(args):                                  # 가짜 구현 함수
            if isinstance(items, Exception):                # 예외면
                raise items                                 # 던짐
            return items                                    # 아니면 항목 반환
        return f                                            # 가짜 구현 반환
    t = fetch_mod.FETCH_TOOL                                # Fetch 도구
    cases = [                                               # (항목, 기대 결과) 사례들
        ([{"name": "Example Page", "content": "# Example Page\n\nThis is some example content from a webpage."}], "# Example Page\n\nThis is some example content from a webpage."),  # 단일 항목
        ([{"name": "Long Page", "content": "This is the main content that was truncated."},  # 본문 + 경고 항목
          {"name": "Truncation warning", "content": "The content ... was truncated"}], "This is the main content that was truncated."),  # 경고 항목은 제외됨
        ([{"name": "Page 1", "content": "Content from page 1"}, {"name": "Page 2", "content": "Content from page 2"}], "Content from page 1\n\nContent from page 2"),  # 여러 항목은 빈 줄로 이어 붙임
    ]                                                       # 사례 끝
    for items, expected in cases:                           # 사례마다
        monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl(items)))  # 가짜 구현 주입
        assert run(t.run({"url": "https://example.com"})) == expected  # 결과 비교
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl([])))  # 빈 결과 구현 주입
    with pytest.raises(ContinueError, match="Could not fetch content from https://example.com"):  # 빈 결과는 오류
        run(t.run({"url": "https://example.com"}))          # 가져오기 실행
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl(Exception("Network error"))))  # 예외 구현 주입
    with pytest.raises(Exception, match="^Error: Network error$"):  # 오류 글로 감싸짐
        run(t.run({"url": "https://example.com"}))          # 가져오기 실행
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl([{"name": "x", "content": "a\n" * 15000}])))   # CLI 자르기
    out = run(t.run({"url": "https://example.com"}))        # 가져오기 실행
    assert out.endswith("characters of fetched content truncated)") and len(out) < 20100  # 잘림 안내와 길이 확인


def test_fetch_http_errors(monkeypatch):                    # HTTP 오류 글 시험
    """HTTP 오류·잘못된 주소·연결 실패 글(cn: 'Error: HTTP 404 Not Found', 'Error: Invalid URL', 'Error: fetch failed')."""
    async def not_found(url):                               # 404 를 돌려주는 가짜
        return 404, "Not Found", b""                        # 404 응답
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", not_found)  # 가짜 주입
    with pytest.raises(Exception, match="^Error: HTTP 404 Not Found$"):  # HTTP 404 오류 글
        run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com/x"}))  # 실행
    with pytest.raises(Exception, match="^Error: Invalid URL$"):  # 잘못된 주소 오류
        run(fetch_mod.FETCH_TOOL.run({"url": "not a url"}))  # 실행

    async def fail(url):                                    # 연결 실패 가짜
        raise Exception("fetch failed")                     # 예외 발생
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", fail)      # 가짜 주입
    with pytest.raises(Exception, match="^Error: fetch failed$"):  # 연결 실패 오류 글
        run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com"}))  # 실행


def test_fetch_full_pipeline_and_core_truncation(monkeypatch):  # HTML→마크다운 전체 흐름·core 자르기 시험
    """HTML → Readability → 마크다운. 2만 자 넘으면 core 가 자르고 경고 항목(CLI 는 경고를 뺌)."""  # 짧은 HTML 페이지
    page = ("<html><head><title>T</title></head><body><h1>Hello</h1><p>Para <b>bold</b> and "
            "<a href=\"/x_y\">link</a>.</p></body></html>").encode()  # (이어서) 링크 포함

    async def ok(url):                                      # 정상 응답 가짜
        return 200, "OK", page                              # 200 응답 반환
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", ok)        # 가짜 주입
    assert run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com/a"})) == "## Hello\n\nPara **bold** and [link](/x%5Fy)."   # node 기대값
    big = ("<html><body>" + "".join(f"<p>Paragraph number {i}, with some words, commas, and more words to be long.</p>" for i in range(600)) + "</body></html>").encode()  # 2만 자 넘는 큰 페이지

    async def big_page(url):                                # 큰 페이지 응답 가짜
        return 200, "OK", big                               # 200 응답 반환
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", big_page)  # 가짜 주입
    items = run(fetch_mod.fetch_url_content_impl({"url": "https://example.com"}))  # 항목 직접 얻기
    assert len(items) == 2 and len(items[0]["content"]) == 20000  # 본문 항목과 경고 항목, 본문은 2만 자
    assert items[1]["content"] == ("The content from https://example.com was truncated because it exceeded the 20000 character limit. "  # 경고 항목 글 확인
                                   "If you need more content, consider fetching specific sections or using a more targeted approach.")


# 기대값: cn 원본 라이브러리(node-html-markdown 1.3.0 / @mozilla/readability 0.6.0 + jsdom)로 만든 결과
_NHM_CASES = [                                              # node-html-markdown 기대값 사례 표
    ('<h1>Title</h1><p>Hello <b>bold</b> and <i>it</i> <code>x`y</code> <a href="https://e.com/a_b(c)">link</a> <a href="https://e.com">https://e.com</a></p>\n'
     '<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul><ol><li>first</li><li>second</li></ol>\n'
     '<pre><code class="language-py">def f():\n    return 1 * 2\n</code></pre><blockquote><p>quote line</p><p>second</p></blockquote>\n'
     '<table><caption>Cap</caption><thead><tr><th>A</th><th>Bee</th></tr></thead><tbody><tr><td>1</td><td>x | y</td></tr><tr><td>long cell</td><td></td></tr></tbody></table>\n'
     '<p>1. not list * star _under_ [br]<br>next line</p><hr><img src="a.png" alt="alt" title="t"><img src="data:x">\n'
     '<p>  spaced   text  </p><p><strong> </strong>empty strong</p><h3>Sub &amp; head</h3><p>- dash + plus # hash</p><del>gone</del> <s>s</s>\n',
     "# Title\n\nHello **bold** and _it_ `` x`y `` [link](https://e.com/a%5Fb%28c%29) <https://e.com>\n\n* one\n* two  \n   * nested\n1. first\n2. second\n\n"
     "```py\ndef f():\n    return 1 * 2\n\n```\n\n> quote line\n> \n> second\n\n__Cap__\n| A         | Bee    |\n| --------- | ------ |\n| 1         | x \\| y |\n"
     "| long cell |        |\n\n1\\. not list \\* star \\_under\\_ \\[br\\]  \nnext line\n\n---\n\n![alt](a.png \"t\") \n\n spaced text \n\nempty strong\n\n"
     "### Sub & head\n\n\\- dash + plus # hash\n\n~~gone~~ ~~s~~ "),
    ('<ol><li><p>Para one</p><p>Para two</p></li><li>Second <strong>bold <em>both</em></strong></li><li> </li><li>after empty</li></ol>\n'
     '<p>Text with `tick` and <code>``double``</code> <a href="/x" title="T">titled</a> <a>nohref</a></p>\n'
     '<div>div text<span> span</span><br><br>after br</div><p>a<br>b</p>\n'
     '<table><tr><td>only</td></tr></table><table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>\n'
     '<p>&lt;tag&gt; &amp; &nbsp; &copy; 3. three</p><pre>  raw  <b>x</b>\nline2</pre><h2>## already</h2><p>=equals &gt;gt</p><p><b>bold</b><b>bold2</b> **x**</p>\n'
     '<dl><dt>term</dt><dd>def</dd></dl><blockquote>multi<br>line</blockquote><p>snake_case_name and a*b*c</p>\n',
     "1. Para one  \nPara two\n2. Second **bold _both_**\n3. after empty\n\nText with \\`tick\\` and ``` ``double`` ``` [titled](/x \"T\") nohref\n\n"
     "div text span  \n  \nafter br\n\na  \nb\n\n| only |\n| ---- |\n\n| a | b |\n| - | - |\n| c |   |\n\n<tag> & © 3\\. three\n\n  raw  **x**\nline2\n\n"
     "## \\## already\n\n\\=equals >gt\n\n**bold** **bold2** \\*\\*x\\*\\*\n\nterm\n\ndef\n\n> multi  \n> line\n\nsnake\\_case\\_name and a\\*b\\*c"),
]


@pytest.mark.parametrize("html,expected", _NHM_CASES)       # 각 사례를 돌림
def test_node_html_markdown_parity(html, expected):         # node-html-markdown 이식 일치 시험
    """node-html-markdown 이식 — cn 원본과 글자 하나까지 같다."""
    assert fetch_mod.html_string_to_markdown(html) == expected  # 변환 결과 비교


_READABILITY_PAGE = """<!DOCTYPE html><html><head><title>My Great Article - Site Name</title><style>p{}</style><script>var x=1;</script></head>
<body><div class="header"><nav><a href="/">Home</a> <a href="/about">About</a></nav></div>
<div id="main" class="content"><h1>My Great Article</h1><p class="byline">By John</p>
<p>This is the first paragraph of the article, which contains enough text, commas, and words to be scored as content by readability.</p>
<p>Second paragraph, with more text. It continues to describe things in a detailed manner so that the length threshold is surely passed by the scorer.</p>
<h2>Section</h2><p>Third paragraph has a <a href="https://example.com/x">link</a> and <code>code</code>. More words here to make it longer than eighty characters total.</p>
<pre><code class="language-js">console.log("hi");</code></pre>
<ul><li>Item one</li><li>Item two</li></ul>
<div class="share">Share this</div><form><input type="text"><button>Go</button></form></div>
<div class="footer sidebar">Copyright 2024. All rights reserved.</div></body></html>
"""


def test_readability_parity():                              # Readability 이식 일치 시험
    """Readability(축약 이식) + 마크다운 — cn 원본 결과와 같다(짧은 문서라 재시도 경로까지 탐)."""
    doc = fetch_mod.parse_html(_READABILITY_PAGE)           # HTML 해석
    art = fetch_mod._Readability(doc).parse()               # Readability 로 본문 추출
    assert fetch_mod.html_to_markdown(art["content"]) == (  # 마크다운 변환 결과 비교
        "By John\n\nThis is the first paragraph of the article, which contains enough text, commas, and words to be scored as content by readability.\n\n"
        "Second paragraph, with more text. It continues to describe things in a detailed manner so that the length threshold is surely passed by the scorer.\n\n"
        "## Section\n\nThird paragraph has a [link](https://example.com/x) and `code`. More words here to make it longer than eighty characters total.\n\n"
        "```\nconsole.log(\"hi\");\n```\n\n* Item one\n* Item two\n\nShare this\n\nCopyright 2024\\. All rights reserved.")
    assert fetch_mod._Readability(fetch_mod.parse_html("")).parse() is None   # 빈 문서 → null → 내용 ""


# ───────────────────────── Checklist·AskQuestion·Exit ─────────────────────────
def test_checklist():                                       # Checklist 시험
    """cn writeChecklist.test.ts."""
    checklist = "- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3"  # 체크리스트 글
    assert run(CHECKLIST_TOOL.run({"checklist": checklist})) == f"Task list status:\n{checklist}"  # 실행하면 상태 글이 돌아옴
    assert CHECKLIST_TOOL.name == "Checklist" and CHECKLIST_TOOL.readonly is False and "checklist" in CHECKLIST_TOOL.parameters["required"]  # 이름·읽기전용·필수 인자 확인
    assert run(CHECKLIST_TOOL.preprocess({"checklist": checklist})).preview[0].type == "checklist"  # 미리보기 종류는 checklist


def test_ask_question_answers(cn_compat_env):               # AskQuestion 답 처리 시험(듣는 화면 없이 답함 — [ncn 부록B B3] cn 동작으로)
    """선택지 번호·직접 답·자유 답 글."""
    async def ask(args, answer):                            # 질문하고 답하는 도우미
        task = asyncio.ensure_future(ASK_QUESTION_TOOL.run(args))  # 질문을 비동기로 시작
        await asyncio.sleep(0)                               # 질문이 걸릴 때까지
        pq = quiz_service.pending_question                  # 대기 중인 질문
        assert pq is not None and pq.request_id.startswith("quiz-")  # 질문 ID 형식 확인
        assert quiz_service.answer_question(pq.request_id, answer)  # 답을 전달
        return await task                                   # 도구 결과 반환
    assert run(ask({"question": "Q?", "options": ["a", "b"]}, "b")) == 'User selected option 2: "b"'  # 선택지 번호로 답함
    assert run(ask({"question": "Q?", "options": ["a", "b"]}, "c")) == 'User provided custom answer: "c"'  # 선택지 밖은 직접 답
    assert run(ask({"question": "Q?", "options": []}, "free")) == 'User answered: "free"'  # 선택지 없으면 자유 답
    assert quiz_service.answer_question("quiz-0", "x") is False  # 없는 질문 ID 는 False
    assert list(ASK_QUESTION_TOOL.parameters["properties"]) == ["question", "options", "defaultAnswer"]  # 인자 이름 순서


def test_exit_tool(monkeypatch):                            # Exit 도구 시험
    """Exit — 작업을 정리하고 종료 코드 1. 인자 스키마에 required 없음."""
    codes = []                                              # 종료 코드 기록 목록
    monkeypatch.setattr(exit_mod, "EXIT_HANDLER", codes.append)  # 종료 처리기를 기록용으로 교체
    bj.background_job_service.create_job("x")               # 정리할 작업 하나 생성
    assert run(exit_mod.EXIT_TOOL.run({})) == "" and codes == [1]  # 빈 결과와 종료 코드 1
    assert exit_mod.EXIT_TOOL.parameters == {"type": "object", "properties": {}}  # required 없는 스키마


# ───────────────────────── Skills ─────────────────────────
def test_parse_markdown_rule():                             # 머리말 나누기 시험
    """머리말 나누기(본문 안의 --- 는 유지), 머리말 없으면 원문."""
    fm, md = parse_markdown_rule("---\r\nname: a\r\ndescription: yes\r\n---\n\nbody\n---\nmore\n")  # CRLF 머리말 해석
    assert fm == {"name": "a", "description": "yes"} and md == "body\n---\nmore"   # yes 는 글(YAML 1.2)
    assert parse_markdown_rule("no front") == ({}, "no front")  # 머리말 없으면 원문


def test_skills_tool(tmp_path):                             # Skills 도구 시험
    """cn skills.test.ts — 설명에 이름, 미리보기, 본문 태그, 부속 파일, 없는 기술 오류."""
    cwd = tmp_path / "proj"                                 # 프로젝트 폴더
    gdir = tmp_path / "global"                              # 전역 폴더
    s1 = cwd / ".continue" / "skills" / "one"               # 프로젝트 기술 폴더
    s1.mkdir(parents=True)                                  # 폴더 만들기
    (s1 / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\nSkill content here\n", encoding="utf-8")  # 기술 파일 작성
    s2 = gdir / "skills" / "two"                            # 전역 기술 폴더
    (s2 / "sub").mkdir(parents=True)                        # 하위 폴더 만들기
    (s2 / "SKILL.md").write_text("---\nname: skill-with-files\ndescription: Skill with extra files\n---\nAnother skill", encoding="utf-8")  # 부속 파일 있는 기술
    (s2 / "b.ts").write_text("x", encoding="utf-8")         # 부속 파일 1
    (s2 / "sub" / "a.ts").write_text("x", encoding="utf-8")  # 부속 파일 2
    bad = cwd / ".claude" / "skills" / "bad"                # 이름 없는 잘못된 기술
    bad.mkdir(parents=True)                                 # 폴더 만들기
    (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")   # 이름 없음 → 오류(목록에서 빠짐)
    tool = make_skills_tool(str(cwd), str(gdir))            # Skills 도구 생성
    assert tool.description == ("Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks. "  # 설명에 기술 목록이 들어감
                                "The skill name should match one of the available skills listed below:\n"  # (이어서) 안내 글
                                "\nname: test-skill\ndescription: A test skill\n,\nname: skill-with-files\ndescription: Skill with extra files\n")  # 기술 목록
    assert run(tool.preprocess({"skill_name": "test-skill"})).preview[0].content == "Reading skill: test-skill"  # 미리보기 글
    assert run(tool.run({"skill_name": "test-skill"})) == ("<skill_name>test-skill</skill_name>\n<skill_description>A test skill</skill_description>\n"  # 본문 태그 형식 확인
                                                           "<skill_content>Skill content here</skill_content>")  # (이어서) 내용 태그
    out = run(tool.run({"skill_name": "skill-with-files"}))  # 부속 파일 있는 기술 실행
    assert f"<skill_files>{s2 / 'b.ts'},{s2 / 'sub' / 'a.ts'}</skill_files>" in out   # cwd 밖이면 절대 경로, 이름순
    assert out.endswith("<other_instructions>Use the read file tool to access skill files as needed.</other_instructions>")  # 마지막 안내 태그
    with pytest.raises(ContinueError) as ei:                # 없는 기술은 ContinueError
        run(tool.run({"skill_name": "nonexistent"}))        # 없는 기술 실행
    assert ei.value.reason == "SkillNotFound" and str(ei.value) == 'Skill "nonexistent" not found. Available skills: test-skill, skill-with-files'  # 원인·오류 글 확인
    empty = make_skills_tool(str(tmp_path / "none"), str(tmp_path / "none2"))  # 기술 없는 도구
    assert empty.description.endswith("listed below:\n")    # 설명이 안내 글로 끝남
    with pytest.raises(ContinueError, match="Available skills: none"):  # 기술 없음 오류
        run(empty.run({"skill_name": "x"}))                 # 실행


def test_skill_files_relative_inside_cwd(tmp_path):         # cwd 안 부속 파일 상대 경로 시험
    """cwd 안의 부속 파일은 './' 없는 상대 경로(path.join 정규화)."""
    s = tmp_path / ".continue" / "skills" / "k"             # 기술 폴더
    s.mkdir(parents=True)                                   # 폴더 만들기
    (s / "SKILL.md").write_text("---\nname: k\ndescription: d\n---\nc", encoding="utf-8")  # 기술 파일 작성
    (s / "ref.md").write_text("r", encoding="utf-8")        # 부속 파일
    out = run(make_skills_tool(str(tmp_path), str(tmp_path / "g")).run({"skill_name": "k"}))  # 도구 실행
    assert f"<skill_files>{os.path.join('.continue', 'skills', 'k', 'ref.md')}</skill_files>" in out  # ./ 없는 상대 경로 확인


# ───────────────────────── Status·ReportFailure·UploadArtifact·Subagent ─────────────────────────
def test_agent_only_tools_without_id(monkeypatch):          # --id 없을 때 에이전트 전용 도구 오류 시험
    """--id 없으면 cn 과 같은 오류 글."""
    monkeypatch.setattr(sys, "argv", ["ncn"])               # argv 에 --id 없음
    assert get_agent_id_from_args() is None and get_agent_id_from_args(["x", "--id", "A1"]) == "A1"  # 인자 해석 확인
    with pytest.raises(ContinueError, match=r"^Agent ID is required\. Please use the --id flag with cn serve\.$"):  # Status 는 에이전트 ID 필요
        run(STATUS_TOOL.run({"status": "DONE"}))            # Status 실행
    with pytest.raises(ContinueError, match="^errorMessage is required to report a failure.$"):  # 빈 오류 메시지는 오류
        run(REPORT_FAILURE_TOOL.run({"errorMessage": "  "}))  # ReportFailure 실행
    with pytest.raises(ContinueError, match="Please use the --id flag"):  # ID 없음 오류
        run(REPORT_FAILURE_TOOL.run({"errorMessage": "x"}))  # ReportFailure 실행
    with pytest.raises(ContinueError, match=r"^Agent ID is required\. This tool only works with 'cn serve --id <agentId>'\.$"):  # UploadArtifact 도 ID 필요
        run(UPLOAD_ARTIFACT_TOOL.run({"filePath": "/tmp/x"}))  # UploadArtifact 실행
    monkeypatch.setattr(sys, "argv", ["ncn", "--id", "A1"])  # --id A1 지정
    with pytest.raises(ContinueError, match="^Artifact upload requires authentication, which has been removed.$"):  # 인증 제거 안내 오류
        run(UPLOAD_ARTIFACT_TOOL.run({"filePath": "/tmp/x"}))  # UploadArtifact 실행
    with pytest.raises(Exception, match="^Error setting status: Request failed: fetch failed$"):  # Status 요청 실패 오류
        run(STATUS_TOOL.run({"status": "DONE"}))            # Status 실행


def test_subagent_tool():                                   # Subagent 도구 시험
    """설정의 subagent 역할 모델로 설명·인자 설명, 모르는 이름은 오류."""
    models = [{"name": "main", "roles": ["chat"]},          # 모델 설정 목록
              {"name": "explorer", "roles": ["subagent"], "chatOptions": {"baseSystemMessage": "코드 탐색 전담"}},  # subagent 역할 모델
              {"name": "nosys", "roles": ["subagent"]}]     # 시스템 메시지 없는 subagent
    assert generate_subagent_tool_description(models) == "Launch a specialized subagent to handle a specific task.\n\nHere are the available subagents:\n  - explorer: 코드 탐색 전담\n"  # 설명 글 확인
    t = make_subagent_tool(models)                          # Subagent 도구 생성
    assert t.parameters["properties"]["subagent_name"]["description"] == "The type of specialized agent to use for this task. Available agents: explorer"  # subagent_name 설명에 가능한 이름 나열
    assert list(t.parameters["properties"]) == ["description", "prompt", "subagent_name"]  # 인자 이름 순서
    assert run(t.preprocess({"description": "find", "prompt": "p", "subagent_name": "explorer"})).preview[0].content == "Spawning explorer to: find"  # 미리보기 글 확인
    with pytest.raises(Exception, match="^Unknown agent type: nope. Available agents: explorer$"):  # 모르는 이름은 오류
        run(t.preprocess({"description": "d", "prompt": "p", "subagent_name": "nope"}))  # 전처리 실행
    with pytest.raises(Exception, match="^No active session found$"):   # 헤드리스 cn 과 같은 결과(P3에서 실행 구현)
        run(t.run({"description": "d", "prompt": "p", "subagent_name": "explorer"}))  # 실행
