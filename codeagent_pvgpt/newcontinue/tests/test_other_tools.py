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
from ncn.tools.skills import make_skills_tool, parse_markdown_rule   # Skills

UNIX = sys.platform != "win32"                               # 셸 명령 시험은 Unix 에서만


def run(coro):
    """async 실행 도우미."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """시험마다 한도 환경변수·내장 검색 끄고, 작업 목록 비우기. 로그인 셸 대신 /bin/sh(빠르고 사용자 설정 영향 없음)."""
    for k in ("CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS", "CONTINUE_CLI_BASH_MAX_OUTPUT_LINES", "NCN_BUILTIN_SEARCH",
              "CONTINUE_CLI_SEARCH_CODE_MAX_RESULTS", "CONTINUE_CLI_SEARCH_CODE_MAX_RESULT_CHARS", "CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SHELL", "/bin/sh")
    bj.background_job_service.__init__()                     # 작업 목록 초기화
    yield
    formatters.PLATFORM_OVERRIDE = None                      # 플랫폼 원래대로


# ───────────────────────── Bash ─────────────────────────
def test_bash_metadata(tmp_path):
    """이름·설명(작업 폴더 포함)·인자 스키마가 cn 원문과 같다."""
    t = bash_mod.make_bash_tool(tmp_path)
    assert t.name == "Bash" and t.display_name == "Bash" and t.readonly is False and t.is_built_in is True
    assert t.description == (f"Executes a terminal command and returns the output\n\nCommands are automatically executed from the current working directory ({tmp_path}), "
                             "so there's no need to change directories with 'cd' commands.\n\nIMPORTANT: To edit files, use Edit/MultiEdit tools instead of bash commands (sed, awk, etc).\n")
    assert list(t.parameters) == ["type", "required", "properties"]   # 키 순서
    assert list(t.parameters["properties"]) == ["command", "timeout"]
    assert t.parameters["properties"]["timeout"]["description"] == "Optional timeout in seconds (max 600). Use this parameter for commands that take longer than the default 180 second timeout."


def test_bash_shell_command_by_platform(monkeypatch):
    """Windows 는 powershell.exe, 그 밖은 $SHELL -l -c(없으면 /bin/bash)."""
    formatters.PLATFORM_OVERRIDE = "win32"
    assert bash_mod.get_shell_command("dir") == ("powershell.exe", ["-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", "dir"])
    formatters.PLATFORM_OVERRIDE = "darwin"
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert bash_mod.get_shell_command("ls") == ("/bin/zsh", ["-l", "-c", "ls"])
    monkeypatch.delenv("SHELL")
    assert bash_mod.get_shell_command("ls") == ("/bin/bash", ["-l", "-c", "ls"])
    assert bash_mod.is_running_in_wsl() is False             # linux 가 아니면 WSL 아님


def test_bash_preprocess():
    """미리보기 'Will run: …'(60자 넘으면 …), 빈 명령은 오류."""
    t = bash_mod.make_bash_tool("/")
    r = run(t.preprocess({"command": "echo hi"}))
    assert r.preview[0].content == "Will run: echo hi" and r.args == {"command": "echo hi"}
    long = "x" * 70
    assert run(t.preprocess({"command": long})).preview[0].content == "Will run: " + "x" * 60 + "..."
    with pytest.raises(Exception, match="command arg is required and must be a non-empty string"):
        run(t.preprocess({"command": ""}))


def test_bash_policy_uses_terminal_security(monkeypatch):
    """evaluate_tool_call_policy 는 terminal_security 를 부른다(바꿔 끼울 수 있음)."""
    ts = pytest.importorskip("ncn.security.terminal_security")
    seen = {}
    monkeypatch.setattr(ts, "evaluate_terminal_command_security", lambda base, cmd: seen.setdefault("x", (base, cmd)) and "disabled")
    t = bash_mod.make_bash_tool("/")
    assert t.evaluate_tool_call_policy("allowedWithPermission", {"command": "rm -rf /"}) == "disabled"
    assert seen["x"] == ("allowedWithPermission", "rm -rf /")


@pytest.mark.skipif(not UNIX, reason="Unix 셸")
def test_bash_run_basic(tmp_path):
    """echo·pwd·stderr 붙이기·실패 종료·출력 없는 실패(cn 예: 'hello world', 'Darwin/Linux')."""
    t = bash_mod.make_bash_tool(tmp_path)
    ctx = ToolRunContext("t1")
    assert run(t.run({"command": 'echo "hello world"'}, ctx)).strip() == "hello world"
    assert run(t.run({"command": "pwd"}, ctx)).strip() == os.path.realpath(tmp_path)   # cwd 에서 실행
    assert run(t.run({"command": "uname -s"}, ctx)).strip() in ("Darwin", "Linux")
    assert run(t.run({"command": "echo out; echo err 1>&2"}, ctx)) == "out\n\nStderr: err\n"
    with pytest.raises(bash_mod.BashCommandError) as ei:     # 0 아님 + stderr → 오류
        run(t.run({"command": "echo bad 1>&2; exit 3"}, ctx))
    assert str(ei.value) == "Error (exit code 3): bad\n"
    assert run(t.run({"command": "echo ok; exit 2"}, ctx)) == "ok\n"   # stderr 없으면 출력 그대로
    with pytest.raises(bash_mod.BashCommandError, match=r"Error \(exit code|not found"):   # cn 시험: 없는 명령
        run(t.run({"command": "definitely-not-a-real-command-xyz123"}, ctx))


@pytest.mark.skipif(not UNIX, reason="Unix 셸")
def test_bash_timeout_no_output():
    """출력 없이 시간 초과 → 죽이고 안내 글."""
    t = bash_mod.make_bash_tool("/")
    out = run(t.run({"command": "echo start; sleep 5", "timeout": 1}, ToolRunContext("t")))
    assert out == "start\n\n\n[Command timed out after 1 seconds of no output]"


@pytest.mark.skipif(not UNIX, reason="Unix 셸")
def test_bash_truncation_and_parallel_note(monkeypatch):
    """줄 한도 → '(previous N lines truncated)'. 동시 실행이면 한도를 나누고 안내(천 단위 쉼표)."""
    t = bash_mod.make_bash_tool("/")
    out = run(t.run({"command": "seq 1 1500"}, ToolRunContext("t")))
    assert out.startswith("(previous 501 lines truncated)\n\n502\n") and out.endswith("1500\n")   # 끝 빈 줄도 한 줄
    out2 = run(t.run({"command": "seq 1 1500"}, ToolRunContext("t", parallel_tool_call_count=2)))
    assert out2.startswith("(previous 1001 lines truncated)")
    assert out2.endswith("\n\n(Note: output limit reduced due to 2 parallel tool calls. Single-tool limit: 50,000 characters or 1,000 lines.)")
    monkeypatch.setenv("CONTINUE_CLI_BASH_MAX_OUTPUT_LINES", "10")
    assert run(t.run({"command": "seq 1 20"}, ToolRunContext("t"))).startswith("(previous 11 lines truncated)\n\n12\n")


@pytest.mark.skipif(not UNIX, reason="Unix 셸")
def test_bash_move_to_background_and_check_job():
    """Ctrl+B 신호 → 백그라운드 작업으로 넘기고 CheckBackgroundJob 으로 확인."""
    t = bash_mod.make_bash_tool("/")
    before = bj.background_signal_manager.listener_count()   # (cn: 시간 초과 경로는 듣기를 떼지 않아 남을 수 있음 — 그대로 옮김)

    async def scenario():
        task = asyncio.ensure_future(t.run({"command": "echo first; sleep 0.5; echo second"}, ToolRunContext("t")))
        await asyncio.sleep(0.3)                             # 첫 출력이 올 때까지
        bj.background_signal_manager.signal_background()     # Ctrl+B
        res = await task
        job_id = res.split("Job ID: ")[1].split("\n")[0]
        assert res == f'Command moved to background. Job ID: {job_id}\nOutput so far:\nfirst\n\nUse CheckBackgroundJob("{job_id}") to check status.'
        await asyncio.sleep(0.8)                             # 끝날 때까지
        info = json.loads(await bj.CHECK_BACKGROUND_JOB_TOOL.run({"job_id": job_id}))
        return job_id, info
    job_id, info = run(scenario())
    assert info["job_id"] == job_id and info["status"] == "completed" and info["exit_code"] == 0
    assert info["output"] == "first\nsecond\n"
    assert list(info) == ["job_id", "status", "command", "output", "started_at", "ended_at", "duration_seconds", "exit_code"]   # 키 순서
    assert bj.background_signal_manager.listener_count() == before   # 이 명령의 듣기는 해제


# ───────────────────────── BackgroundJobService ─────────────────────────
def test_background_job_limits_and_output():
    """동시 5개 한도, 출력 1000줄 유지, 완료·취소 상태."""
    svc = bj.BackgroundJobService()
    jobs = [svc.create_job(f"cmd{i}") for i in range(5)]
    assert all(jobs) and svc.create_job("x") is None         # 6번째 실패
    assert jobs[0].id.startswith("bg-1-") and jobs[4].id.startswith("bg-5-")
    svc.append_output(jobs[0].id, "\n".join(str(i) for i in range(1500)))
    assert jobs[0].output.split("\n")[0] == "500" and len(jobs[0].output.split("\n")) == 1000
    svc.complete_job(jobs[0].id, 0)
    svc.complete_job(jobs[1].id, 2)
    assert jobs[0].status == "completed" and jobs[1].status == "failed" and jobs[1].exit_code == 2
    assert svc.cancel_job(jobs[2].id) and jobs[2].status == "cancelled"
    svc.complete_job(jobs[2].id, 0)                          # 취소된 작업은 그대로
    assert jobs[2].status == "cancelled" and svc.cancel_job("nope") is False
    assert svc.get_running_job_count() == 2


def test_check_background_job_not_found():
    """없는 작업 → 한 줄 JSON + 목록(명령은 50자에서 자름)."""
    svc = bj.background_job_service
    svc.create_job("a" * 60)
    out = run(bj.CHECK_BACKGROUND_JOB_TOOL.run({"job_id": "bg-9"}))
    d = json.loads(out)
    assert out.startswith('{"error":"Job bg-9 not found","available_jobs":[{"id":"bg-1-')   # 공백 없는 JSON
    assert d["available_jobs"][0]["command"] == "a" * 50 + "..." and d["available_jobs"][0]["status"] == "pending"
    assert bj.CHECK_BACKGROUND_JOB_TOOL.parameters["properties"]["job_id"]["description"] == "The ID of the background job to check (e.g., bg-1-1234567890)"


def test_iso_string():
    """toISOString 형식."""
    from datetime import datetime, timezone
    assert bj.iso_string(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)) == "2026-01-02T03:04:05.678Z"


# ───────────────────────── Search ─────────────────────────
def _make_tree(root: Path) -> None:
    """검색용 파일들."""
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def foo():\n    return 'bar'\n", encoding="utf-8")
    (root / "src" / "b.ts").write_text("const foo = 1;\r\nlet x = foo;\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "out.py").write_text("foo = 'built'\n", encoding="utf-8")
    (root / ".hidden").write_text("foo hidden\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"foo\x00bar")
    (root / ".gitignore").write_text("# c\nbuild/\n!keep\n\n", encoding="utf-8")


def test_search_metadata_and_preprocess():
    """이름·설명·인자, 미리보기(50자)."""
    t = search_mod.make_search_tool("/")
    assert t.description == "Search the codebase using ripgrep (rg) for a specific pattern" and t.readonly is True
    assert list(t.parameters["properties"]) == ["pattern", "path", "file_pattern"]
    assert run(t.preprocess({"pattern": "foo"})).preview[0].content == 'Will search for: "foo"'
    assert run(t.preprocess({"pattern": "p" * 55})).preview[0].content == 'Will search for: "' + "p" * 50 + '..."'


def test_gitignore_patterns(tmp_path):
    """가장 가까운 .gitignore — 주석·빈 줄·! 제외."""
    _make_tree(tmp_path)
    assert search_mod.get_gitignore_patterns(str(tmp_path / "src")) == ["build/"]


def test_builtin_search_rg_format(tmp_path, monkeypatch):
    """[추가] 내장 검색: rg 와 같은 '경로:줄:내용', 숨김·이진·.gitignore(-g !) 제외, \\r 유지, 이름순."""
    _make_tree(tmp_path)
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")
    monkeypatch.setattr(search_mod, "check_if_ripgrep_is_installed", lambda cwd=None: _false())
    t = search_mod.make_search_tool(tmp_path)
    out = run(t.run({"pattern": "foo", "path": "."}))
    assert out == ('Search results for pattern "foo":\n\n'
                   "./src/a.py:1:def foo():\n./src/b.ts:1:const foo = 1;\r\n./src/b.ts:2:let x = foo;\n")
    out2 = run(t.run({"pattern": "foo", "path": "src", "file_pattern": "*.py"}))
    assert out2 == 'Search results for pattern "foo" in files matching "*.py":\n\nsrc/a.py:1:def foo():\n'
    assert run(t.run({"pattern": "zzz"})) == 'No matches found for pattern "zzz".'
    assert run(t.run({"pattern": "zzz", "file_pattern": "*.ts"})) == 'No matches found for pattern "zzz" in files matching "*.ts".'
    with pytest.raises(ContinueError, match="Path does not exist: nope"):
        run(t.run({"pattern": "x", "path": "nope"}))


async def _false():
    """rg 없음."""
    return False


def test_search_limits(tmp_path, monkeypatch):
    """100개 넘으면 잘림 안내, 1000자 넘는 줄은 버림(cn searchCode.test.ts)."""
    lines = [f"const foo = 'bar'; // {i}" for i in range(150)] + ["foo" + "a" * 1001]
    (tmp_path / "f.ts").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")
    monkeypatch.setattr(search_mod, "check_if_ripgrep_is_installed", lambda cwd=None: _false())
    t = search_mod.make_search_tool(tmp_path)
    out = run(t.run({"pattern": "foo", "path": "."}))
    assert out.endswith("\n\n[Results truncated: showing 100 of 151 matches]")   # 끝 빈 줄 포함 151(cn 그대로)
    body = out.split("\n\n")[1].split("\n")
    assert len(body) == 100 and "a" * 1001 not in out


@pytest.mark.skipif(not UNIX, reason="가짜 rg 는 sh 스크립트")
def test_search_with_fake_rg_command(tmp_path, monkeypatch):
    """rg 가 있으면 cn 과 같은 명령 글(큰따옴표, -g 제외 패턴)로 셸 실행 — 가짜 rg 가 받은 인자를 출력."""
    _make_tree(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rg = bindir / "rg"
    rg.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ripgrep 14"; exit 0; fi\n'
                  'if [ "$5" = "nomatch" ]; then exit 1; fi\n'
                  'if [ "$5" = "boom" ]; then echo "bad regex" 1>&2; exit 2; fi\n'
                  'printf "%s|" "$@"; echo\n', encoding="utf-8")
    rg.chmod(rg.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert run(search_mod.check_if_ripgrep_is_installed(str(tmp_path))) is True
    t = search_mod.make_search_tool(tmp_path)
    out = run(t.run({"pattern": "foo", "path": "src", "file_pattern": "*.py"}))
    assert out == ('Search results for pattern "foo" in files matching "*.py":\n\n'
                   "--line-number|--with-filename|--color|never|foo|-g|*.py|-g|!build/|src|\n")
    assert run(t.run({"pattern": "nomatch"})) == 'No matches found for pattern "nomatch".'   # 종료 코드 1
    with pytest.raises(Exception) as ei:                     # 종료 코드 2 → exec 오류 글
        run(t.run({"pattern": "boom", "path": "src"}))
    assert str(ei.value) == f'Error executing search: Command failed: rg --line-number --with-filename --color never "boom" -g "!build/" "src"\nbad regex\n'


# ───────────────────────── Fetch ─────────────────────────
def test_fetch_metadata_and_preprocess():
    """cn fetch.test.ts — 메타데이터·미리보기."""
    t = fetch_mod.FETCH_TOOL
    assert t.name == "Fetch" and t.display_name == "Fetch" and t.readonly is True and t.is_built_in is True
    assert t.description == "Fetches content from a URL, converts to markdown, and handles long content with truncation"
    assert t.parameters == {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "description": "The URL to fetch content from"}}}
    r = run(t.preprocess({"url": "https://example.com"}))
    assert r.preview[0].type == "text" and r.preview[0].content == "Will fetch: https://example.com"


def test_fetch_run_with_mocked_items(monkeypatch):
    """cn fetch.test.ts — 경고 항목 제외, 여러 항목 이어 붙이기, 빈 결과 오류, 오류 감싸기."""
    async def impl(items):
        async def f(args):
            if isinstance(items, Exception):
                raise items
            return items
        return f
    t = fetch_mod.FETCH_TOOL
    cases = [
        ([{"name": "Example Page", "content": "# Example Page\n\nThis is some example content from a webpage."}], "# Example Page\n\nThis is some example content from a webpage."),
        ([{"name": "Long Page", "content": "This is the main content that was truncated."},
          {"name": "Truncation warning", "content": "The content ... was truncated"}], "This is the main content that was truncated."),
        ([{"name": "Page 1", "content": "Content from page 1"}, {"name": "Page 2", "content": "Content from page 2"}], "Content from page 1\n\nContent from page 2"),
    ]
    for items, expected in cases:
        monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl(items)))
        assert run(t.run({"url": "https://example.com"})) == expected
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl([])))
    with pytest.raises(ContinueError, match="Could not fetch content from https://example.com"):
        run(t.run({"url": "https://example.com"}))
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl(Exception("Network error"))))
    with pytest.raises(Exception, match="^Error: Network error$"):
        run(t.run({"url": "https://example.com"}))
    monkeypatch.setattr(fetch_mod, "CONTEXT_ITEMS_IMPL", run(impl([{"name": "x", "content": "a\n" * 15000}])))   # CLI 자르기
    out = run(t.run({"url": "https://example.com"}))
    assert out.endswith("characters of fetched content truncated)") and len(out) < 20100


def test_fetch_http_errors(monkeypatch):
    """HTTP 오류·잘못된 주소·연결 실패 글(cn: 'Error: HTTP 404 Not Found', 'Error: Invalid URL', 'Error: fetch failed')."""
    async def not_found(url):
        return 404, "Not Found", b""
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", not_found)
    with pytest.raises(Exception, match="^Error: HTTP 404 Not Found$"):
        run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com/x"}))
    with pytest.raises(Exception, match="^Error: Invalid URL$"):
        run(fetch_mod.FETCH_TOOL.run({"url": "not a url"}))

    async def fail(url):
        raise Exception("fetch failed")
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", fail)
    with pytest.raises(Exception, match="^Error: fetch failed$"):
        run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com"}))


def test_fetch_full_pipeline_and_core_truncation(monkeypatch):
    """HTML → Readability → 마크다운. 2만 자 넘으면 core 가 자르고 경고 항목(CLI 는 경고를 뺌)."""
    page = ("<html><head><title>T</title></head><body><h1>Hello</h1><p>Para <b>bold</b> and "
            "<a href=\"/x_y\">link</a>.</p></body></html>").encode()

    async def ok(url):
        return 200, "OK", page
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", ok)
    assert run(fetch_mod.FETCH_TOOL.run({"url": "https://example.com/a"})) == "## Hello\n\nPara **bold** and [link](/x%5Fy)."   # node 기대값
    big = ("<html><body>" + "".join(f"<p>Paragraph number {i}, with some words, commas, and more words to be long.</p>" for i in range(600)) + "</body></html>").encode()

    async def big_page(url):
        return 200, "OK", big
    monkeypatch.setattr(fetch_mod, "FETCH_IMPL", big_page)
    items = run(fetch_mod.fetch_url_content_impl({"url": "https://example.com"}))
    assert len(items) == 2 and len(items[0]["content"]) == 20000
    assert items[1]["content"] == ("The content from https://example.com was truncated because it exceeded the 20000 character limit. "
                                   "If you need more content, consider fetching specific sections or using a more targeted approach.")


# 기대값: cn 원본 라이브러리(node-html-markdown 1.3.0 / @mozilla/readability 0.6.0 + jsdom)로 만든 결과
_NHM_CASES = [
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


@pytest.mark.parametrize("html,expected", _NHM_CASES)
def test_node_html_markdown_parity(html, expected):
    """node-html-markdown 이식 — cn 원본과 글자 하나까지 같다."""
    assert fetch_mod.html_string_to_markdown(html) == expected


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


def test_readability_parity():
    """Readability(축약 이식) + 마크다운 — cn 원본 결과와 같다(짧은 문서라 재시도 경로까지 탐)."""
    doc = fetch_mod.parse_html(_READABILITY_PAGE)
    art = fetch_mod._Readability(doc).parse()
    assert fetch_mod.html_to_markdown(art["content"]) == (
        "By John\n\nThis is the first paragraph of the article, which contains enough text, commas, and words to be scored as content by readability.\n\n"
        "Second paragraph, with more text. It continues to describe things in a detailed manner so that the length threshold is surely passed by the scorer.\n\n"
        "## Section\n\nThird paragraph has a [link](https://example.com/x) and `code`. More words here to make it longer than eighty characters total.\n\n"
        "```\nconsole.log(\"hi\");\n```\n\n* Item one\n* Item two\n\nShare this\n\nCopyright 2024\\. All rights reserved.")
    assert fetch_mod._Readability(fetch_mod.parse_html("")).parse() is None   # 빈 문서 → null → 내용 ""


# ───────────────────────── Checklist·AskQuestion·Exit ─────────────────────────
def test_checklist():
    """cn writeChecklist.test.ts."""
    checklist = "- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3"
    assert run(CHECKLIST_TOOL.run({"checklist": checklist})) == f"Task list status:\n{checklist}"
    assert CHECKLIST_TOOL.name == "Checklist" and CHECKLIST_TOOL.readonly is False and "checklist" in CHECKLIST_TOOL.parameters["required"]
    assert run(CHECKLIST_TOOL.preprocess({"checklist": checklist})).preview[0].type == "checklist"


def test_ask_question_answers():
    """선택지 번호·직접 답·자유 답 글."""
    async def ask(args, answer):
        task = asyncio.ensure_future(ASK_QUESTION_TOOL.run(args))
        await asyncio.sleep(0)                               # 질문이 걸릴 때까지
        pq = quiz_service.pending_question
        assert pq is not None and pq.request_id.startswith("quiz-")
        assert quiz_service.answer_question(pq.request_id, answer)
        return await task
    assert run(ask({"question": "Q?", "options": ["a", "b"]}, "b")) == 'User selected option 2: "b"'
    assert run(ask({"question": "Q?", "options": ["a", "b"]}, "c")) == 'User provided custom answer: "c"'
    assert run(ask({"question": "Q?", "options": []}, "free")) == 'User answered: "free"'
    assert quiz_service.answer_question("quiz-0", "x") is False
    assert list(ASK_QUESTION_TOOL.parameters["properties"]) == ["question", "options", "defaultAnswer"]


def test_exit_tool(monkeypatch):
    """Exit — 작업을 정리하고 종료 코드 1. 인자 스키마에 required 없음."""
    codes = []
    monkeypatch.setattr(exit_mod, "EXIT_HANDLER", codes.append)
    bj.background_job_service.create_job("x")
    assert run(exit_mod.EXIT_TOOL.run({})) == "" and codes == [1]
    assert exit_mod.EXIT_TOOL.parameters == {"type": "object", "properties": {}}


# ───────────────────────── Skills ─────────────────────────
def test_parse_markdown_rule():
    """머리말 나누기(본문 안의 --- 는 유지), 머리말 없으면 원문."""
    fm, md = parse_markdown_rule("---\r\nname: a\r\ndescription: yes\r\n---\n\nbody\n---\nmore\n")
    assert fm == {"name": "a", "description": "yes"} and md == "body\n---\nmore"   # yes 는 글(YAML 1.2)
    assert parse_markdown_rule("no front") == ({}, "no front")


def test_skills_tool(tmp_path):
    """cn skills.test.ts — 설명에 이름, 미리보기, 본문 태그, 부속 파일, 없는 기술 오류."""
    cwd = tmp_path / "proj"
    gdir = tmp_path / "global"
    s1 = cwd / ".continue" / "skills" / "one"
    s1.mkdir(parents=True)
    (s1 / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\nSkill content here\n", encoding="utf-8")
    s2 = gdir / "skills" / "two"
    (s2 / "sub").mkdir(parents=True)
    (s2 / "SKILL.md").write_text("---\nname: skill-with-files\ndescription: Skill with extra files\n---\nAnother skill", encoding="utf-8")
    (s2 / "b.ts").write_text("x", encoding="utf-8")
    (s2 / "sub" / "a.ts").write_text("x", encoding="utf-8")
    bad = cwd / ".claude" / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")   # 이름 없음 → 오류(목록에서 빠짐)
    tool = make_skills_tool(str(cwd), str(gdir))
    assert tool.description == ("Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks. "
                                "The skill name should match one of the available skills listed below:\n"
                                "\nname: test-skill\ndescription: A test skill\n,\nname: skill-with-files\ndescription: Skill with extra files\n")
    assert run(tool.preprocess({"skill_name": "test-skill"})).preview[0].content == "Reading skill: test-skill"
    assert run(tool.run({"skill_name": "test-skill"})) == ("<skill_name>test-skill</skill_name>\n<skill_description>A test skill</skill_description>\n"
                                                           "<skill_content>Skill content here</skill_content>")
    out = run(tool.run({"skill_name": "skill-with-files"}))
    assert f"<skill_files>{s2 / 'b.ts'},{s2 / 'sub' / 'a.ts'}</skill_files>" in out   # cwd 밖이면 절대 경로, 이름순
    assert out.endswith("<other_instructions>Use the read file tool to access skill files as needed.</other_instructions>")
    with pytest.raises(ContinueError) as ei:
        run(tool.run({"skill_name": "nonexistent"}))
    assert ei.value.reason == "SkillNotFound" and str(ei.value) == 'Skill "nonexistent" not found. Available skills: test-skill, skill-with-files'
    empty = make_skills_tool(str(tmp_path / "none"), str(tmp_path / "none2"))
    assert empty.description.endswith("listed below:\n")
    with pytest.raises(ContinueError, match="Available skills: none"):
        run(empty.run({"skill_name": "x"}))


def test_skill_files_relative_inside_cwd(tmp_path):
    """cwd 안의 부속 파일은 './' 없는 상대 경로(path.join 정규화)."""
    s = tmp_path / ".continue" / "skills" / "k"
    s.mkdir(parents=True)
    (s / "SKILL.md").write_text("---\nname: k\ndescription: d\n---\nc", encoding="utf-8")
    (s / "ref.md").write_text("r", encoding="utf-8")
    out = run(make_skills_tool(str(tmp_path), str(tmp_path / "g")).run({"skill_name": "k"}))
    assert f"<skill_files>{os.path.join('.continue', 'skills', 'k', 'ref.md')}</skill_files>" in out


# ───────────────────────── Status·ReportFailure·UploadArtifact·Subagent ─────────────────────────
def test_agent_only_tools_without_id(monkeypatch):
    """--id 없으면 cn 과 같은 오류 글."""
    monkeypatch.setattr(sys, "argv", ["ncn"])
    assert get_agent_id_from_args() is None and get_agent_id_from_args(["x", "--id", "A1"]) == "A1"
    with pytest.raises(ContinueError, match=r"^Agent ID is required\. Please use the --id flag with cn serve\.$"):
        run(STATUS_TOOL.run({"status": "DONE"}))
    with pytest.raises(ContinueError, match="^errorMessage is required to report a failure.$"):
        run(REPORT_FAILURE_TOOL.run({"errorMessage": "  "}))
    with pytest.raises(ContinueError, match="Please use the --id flag"):
        run(REPORT_FAILURE_TOOL.run({"errorMessage": "x"}))
    with pytest.raises(ContinueError, match=r"^Agent ID is required\. This tool only works with 'cn serve --id <agentId>'\.$"):
        run(UPLOAD_ARTIFACT_TOOL.run({"filePath": "/tmp/x"}))
    monkeypatch.setattr(sys, "argv", ["ncn", "--id", "A1"])
    with pytest.raises(ContinueError, match="^Artifact upload requires authentication, which has been removed.$"):
        run(UPLOAD_ARTIFACT_TOOL.run({"filePath": "/tmp/x"}))
    with pytest.raises(Exception, match="^Error setting status: Request failed: fetch failed$"):
        run(STATUS_TOOL.run({"status": "DONE"}))


def test_subagent_tool():
    """설정의 subagent 역할 모델로 설명·인자 설명, 모르는 이름은 오류."""
    models = [{"name": "main", "roles": ["chat"]},
              {"name": "explorer", "roles": ["subagent"], "chatOptions": {"baseSystemMessage": "코드 탐색 전담"}},
              {"name": "nosys", "roles": ["subagent"]}]
    assert generate_subagent_tool_description(models) == "Launch a specialized subagent to handle a specific task.\n\nHere are the available subagents:\n  - explorer: 코드 탐색 전담\n"
    t = make_subagent_tool(models)
    assert t.parameters["properties"]["subagent_name"]["description"] == "The type of specialized agent to use for this task. Available agents: explorer"
    assert list(t.parameters["properties"]) == ["description", "prompt", "subagent_name"]
    assert run(t.preprocess({"description": "find", "prompt": "p", "subagent_name": "explorer"})).preview[0].content == "Spawning explorer to: find"
    with pytest.raises(Exception, match="^Unknown agent type: nope. Available agents: explorer$"):
        run(t.preprocess({"description": "d", "prompt": "p", "subagent_name": "nope"}))
    with pytest.raises(Exception, match="^No active session found$"):   # 헤드리스 cn 과 같은 결과(P3에서 실행 구현)
        run(t.run({"description": "d", "prompt": "p", "subagent_name": "explorer"}))
