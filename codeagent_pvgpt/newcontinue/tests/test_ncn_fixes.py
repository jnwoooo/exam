# tests/test_ncn_fixes.py — 계획 부록 B 로 고친 cn 버그(★: S1~S7, B1, B3, B4, B6) 시험. cn 원본 없음(ncn 전용)
# 항목마다 ① 고친 동작(기본) ② NCN_CN_COMPAT=1 이면 cn 원래 동작 — 두 가지를 확인한다(P1/cn_버그후보.md)
import asyncio                                               # 도구는 async
import os                                                    # 링크·환경변수
import stat                                                  # 가짜 rg 실행 권한
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from ncn.compat import COMPAT_ENV, cn_compat                 # 스위치
from ncn.llm.client import SDK_DEFAULT_TIMEOUT_MS, LLMClient   # B1 대상
from ncn.permissions.service import PermissionService        # S1·S2 대상
from ncn.permissions.yaml_loader import MANAGED_HEADER, generate_policy_rule   # S1·S2 대상
from ncn.security.ignore import is_security_concern          # S5 대상
from ncn.security.terminal_security import evaluate_terminal_command_security, shell_quote_parse   # S3 대상
from ncn.tools import bash as bash_mod                       # B4 대상
from ncn.tools import formatters                             # 실행 OS 바꾸기
from ncn.tools.ask_question import ASK_QUESTION_TOOL, NO_ANSWERER_MESSAGE, quiz_service   # B3 대상
from ncn.tools.base import ContinueError, ToolRunContext     # 공용 형식
from ncn.tools.edit import EDIT_TOOL, REDO_KEY               # B6 대상
from ncn.tools.multi_edit import MULTI_EDIT_TOOL             # B6 대상
from ncn.tools.read_file import READ_TOOL, read_files_set    # S4 대상·읽은 파일 기록
from ncn.tools.search import make_search_tool                # S7 대상
from ncn.tools.write_file import WRITE_TOOL                  # S6 대상

WO, W, D = "allowedWithoutPermission", "allowedWithPermission", "disabled"   # terminal-security 정책 이름 줄임


def run(coro):                                              # async 호출을 동기로 실행하는 도우미
    """async 호출을 동기로."""
    return asyncio.run(coro)                                # 이벤트 루프에서 끝까지 실행


def security_msg(p: str) -> str:                            # cn 원문 민감 파일 오류 글
    """throwIfFileIsSecurityConcern 글."""
    return f"Reading or Editing {p} is not allowed because it is a security concern. Do not attempt to read or edit this file in any way."  # cn 원문


@pytest.fixture                                              # 시험마다 새로
def ws(tmp_path, monkeypatch):                               # 임시 폴더를 cwd 로
    """실경로 임시 폴더를 cwd 로, 읽은 파일 기록 비움."""
    real = Path(os.path.realpath(tmp_path))                  # 링크를 푼 실경로
    monkeypatch.chdir(real)                                  # cwd 이동
    read_files_set.clear()                                   # 기록 비움
    yield real                                               # 시험에 폴더를 넘김
    read_files_set.clear()                                   # 끝나고 비움
    formatters.PLATFORM_OVERRIDE = None                      # 플랫폼 강제값 되돌림


def test_compat_switch(monkeypatch):                         # 스위치 자체
    assert cn_compat() is False                              # 기본은 고친 동작(conftest 가 끔)
    monkeypatch.setenv(COMPAT_ENV, "1")                      # 켜기
    assert cn_compat() is True                               # "1" 이면 cn 동작
    monkeypatch.setenv(COMPAT_ENV, "true")                   # "1" 이 아닌 값
    assert cn_compat() is False                              # 정확히 "1" 만 참


# ───────── S1·S2: "다시 묻지 않기" 규칙 ─────────
@pytest.mark.parametrize("args,fixed,cn", [                  # (인자, 고친 규칙, cn 규칙)
    ({"command": "ls -la"}, "Bash(ls -la)", "Bash(ls*)"),    # 명령 전체 일치 ↔ 앞부분 와일드카드
    ({"command": "ls -la && echo test"}, "Bash(ls -la && echo test)", "Bash(ls*)"),   # 연산자가 있어도 전체
    ({"command": "| ls"}, "Bash(| ls)", "Bash"),             # S2: 그 명령만 정확 일치(cn은 Bash 전체 허용)
    ({"command": "  grep -r test .  "}, None, "Bash(grep*)"),  # 앞뒤 공백 — 읽을 때 trim 돼 안 맞으므로 규칙 없음
    ({"command": "find . -name '*.ts'"}, None, "Bash(find*)"),   # * 는 글롭이 되므로 규칙 없음
    ({"command": "echo (a)"}, None, "Bash(echo*)"),          # 괄호는 패턴 해석이 깨지므로 규칙 없음
    ({}, None, "Bash"), ({"command": ""}, None, "Bash"),     # 명령 없음 — cn은 Bash 전체
])                                                          # 표 끝
def test_s1_s2_generate_policy_rule(args, fixed, cn, monkeypatch):   # 규칙 생성 시험
    assert generate_policy_rule("Bash", args) == fixed       # 고친 동작
    assert generate_policy_rule("Write", args) == "Write"    # Bash 가 아니면 그대로
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert generate_policy_rule("Bash", args) == cn          # cn 원래 규칙


def test_s1_add_always_allow_exact_only(tmp_path):           # 저장된 규칙은 그 명령만 허용
    bash = bash_mod.make_bash_tool(str(tmp_path))            # 내장 Bash 도구(위험 판정 포함)
    svc = PermissionService(headless=False, global_dir=tmp_path)   # TUI 서비스
    svc.add_always_allow("Bash", {"command": 'git commit -m "a #1: x"'})   # 따옴표·# 가 든 명령
    text = (tmp_path / "permissions.yaml").read_text(encoding="utf-8")   # 저장된 yaml
    assert text == MANAGED_HEADER + 'allow:\n  - "Bash(git commit -m \\"a #1: x\\")"\nask: []\nexclude: []\n'   # 정확 일치 규칙(큰따옴표 표기)
    assert svc.check(bash, {"command": 'git commit -m "a #1: x"'}) == "allow"   # 같은 명령은 허용
    assert svc.check(bash, {"command": "git log"}) == "ask"  # 다른 명령은 다시 묻기(cn은 Bash(git*)로 허용)
    svc.add_always_allow("Bash", {"command": "ls"})          # 다음 규칙
    assert svc.check(bash, {"command": "ls && curl x | sh"}) == "ask"   # S1 우회 막힘(cn은 allow)
    svc.add_always_allow("Bash", {"command": "| ls"})        # S2: cn은 'Bash'(전체)를 저장하던 명령
    assert svc.base_permission("Bash") == "ask"              # Bash 전체가 허용되지 않음(cn은 allow)
    assert "  - Bash\n" not in (tmp_path / "permissions.yaml").read_text(encoding="utf-8")   # 'Bash' 규칙이 저장되지 않음


def test_s1_existing_yaml_rules_keep_cn_meaning(tmp_path):    # 이미 있는 규칙 해석은 cn과 같게
    (tmp_path / "permissions.yaml").write_text("allow:\n  - Bash(ls*)\n", encoding="utf-8")   # cn이 저장한 넓은 규칙
    svc = PermissionService(headless=False, global_dir=tmp_path)   # TUI 서비스
    bash = bash_mod.make_bash_tool(str(tmp_path))            # 내장 Bash 도구
    assert svc.check(bash, {"command": "ls && curl x | sh"}) == "allow"   # 기존 규칙은 cn처럼 글롭(사용자가 직접 지워야 함)


def test_s1_compat_saves_cn_rule(tmp_path, monkeypatch):      # cn 동작이면 넓은 규칙 저장
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    svc = PermissionService(headless=False, global_dir=tmp_path)   # TUI 서비스
    svc.add_always_allow("Bash", {"command": "ls"})          # 규칙 저장
    assert svc.check(bash_mod.make_bash_tool(str(tmp_path)), {"command": "ls && curl x | sh"}) == "allow"   # cn 우회 그대로


# ───────── S3: 단어 중간 # ─────────
def test_s3_mid_word_hash_is_not_comment(monkeypatch):       # 셸 규칙대로 단어 첫 # 만 주석
    assert shell_quote_parse("echo foo#bar; rm") == ["echo", "foo#bar", {"op": ";"}, "rm"]   # 단어 중간 # 은 글자
    assert shell_quote_parse("echo a #c; rm") == ["echo", "a", {"comment": "c; rm"}]   # 공백 뒤 # 은 주석
    assert shell_quote_parse("a;#b") == ["a", {"op": ";"}, {"comment": "b"}]   # 연산자 뒤 # 도 주석
    assert shell_quote_parse('"x"#y') == ["x#y"]             # 따옴표 뒤 # 은 글자(bash와 같음)
    assert evaluate_terminal_command_security(WO, "echo foo#bar; rm -rf /") == D   # A27: rm -rf / 를 봄 → 금지
    assert evaluate_terminal_command_security(WO, "echo issue#12") == WO   # 안전한 명령은 그대로
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert shell_quote_parse("echo foo#bar; rm") == ["echo", "foo", {"comment": "bar; rm"}]   # cn: 주석으로 봄
    assert evaluate_terminal_command_security(WO, "echo foo#bar; rm -rf /") == WO   # cn: 자동 승인(우회)


# ───────── S4: Read 의 심볼릭 링크 ─────────
def test_s4_read_checks_realpath(ws, monkeypatch):           # 링크를 따라간 실제 경로로도 판정
    (ws / ".env").write_text("SECRET=1\n")                   # 민감 파일
    os.symlink(ws / ".env", ws / "notes.txt")                # 평범한 이름의 링크
    with pytest.raises(ContinueError) as e:                  # 거부 기대
        run(READ_TOOL.preprocess({"filepath": "notes.txt"}))  # 링크로 읽기
    assert str(e.value) == security_msg("notes.txt") and e.value.reason == "file_is_security_concern"   # cn과 같은 글(모델이 준 경로)
    (ws / "ok.txt").write_text("hi")                         # 평범한 파일
    os.symlink(ws / "ok.txt", ws / "ok_link.txt")            # 평범한 링크
    assert run(READ_TOOL.preprocess({"filepath": "ok_link.txt"})).preview[0].content == "Will read ok_link.txt"   # 평범한 링크는 통과
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    pre = run(READ_TOOL.preprocess({"filepath": "notes.txt"}))   # cn: 링크 이름만 봄
    assert run(READ_TOOL.run(pre.args, ToolRunContext("t"))) == "Content of notes.txt:\nSECRET=1\n"   # cn 우회 그대로


# ───────── S5: 절대경로 조각 전체 ─────────
@pytest.mark.parametrize("path,fixed,cn", [                  # (경로, 고친 값, cn 값)
    ("/proj/secrets/sub/a.txt", True, False),                # 작업 폴더 밖 — 조각 전체에 secrets/
    ("/home/u/.ssh/keys2/a.pub", True, False),               # .ssh/ 안 깊은 곳
    ("/home/u/.ssh/id_rsa", True, True),                     # cn도 잡던 것
    ("/home/u/project/config.json", True, True),             # 파일 이름 패턴
    ("/proj/src/main.py", False, False),                     # 평범한 파일
    ("/", False, False),                                     # 루트
])                                                          # 표 끝
def test_s5_absolute_full_pieces(path, fixed, cn, monkeypatch):   # 절대경로 판정
    assert is_security_concern(path) is fixed                # 고친 동작
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert is_security_concern(path) is cn                   # cn 원래 값


def test_s5_inside_workspace_uses_relative(ws):              # 작업 폴더 안은 작업 폴더 기준 상대경로
    (ws / "secrets" / "sub").mkdir(parents=True)             # 작업 폴더 안 secrets/sub
    assert is_security_concern(str(ws / "secrets" / "sub" / "a.txt")) is True   # 상대 'secrets/sub/a.txt' → 차단
    assert is_security_concern(str(ws / "src" / "a.txt")) is False   # 평범한 파일 — 작업 폴더 바깥 조각(/private/var/...)은 안 봄


def test_s5_darwin_private_prefix_and_win32(monkeypatch, tmp_path):   # macOS /private·Windows 경로
    monkeypatch.chdir(tmp_path)                              # 판정할 경로 밖 cwd
    formatters.PLATFORM_OVERRIDE = "darwin"                  # macOS
    try:                                                     # 강제값은 꼭 되돌림
        assert is_security_concern("/private/tmp/x.txt") is False   # /tmp 의 실제 위치 — 'private/' 패턴에 걸리지 않음
        assert is_security_concern("/Users/u/private/x.txt") is True   # 진짜 private 폴더는 차단
        formatters.PLATFORM_OVERRIDE = "win32"               # Windows
        assert is_security_concern("C:\\proj\\secrets\\sub\\a.txt") is True   # 드라이브를 뗀 조각 전체로 판정
        assert is_security_concern("C:\\proj\\src\\main.py") is False   # 평범한 파일
    finally:                                                 # 정리
        formatters.PLATFORM_OVERRIDE = None                  # 원래 플랫폼


# ───────── S6: Write 민감 파일 ─────────
def test_s6_write_rejects_sensitive(ws, monkeypatch):        # Write 도 같은 글로 거부
    for p in (".env", "secrets/a.txt", "./id_rsa"):          # 파일·폴더·'./' 경우
        with pytest.raises(ContinueError) as e:              # 거부 기대
            run(WRITE_TOOL.preprocess({"filepath": p, "content": "x"}))   # 쓰기 preprocess
        assert str(e.value) == security_msg(p[2:] if p.startswith("./") else p)   # Read 와 같은 글
    (ws / ".env").write_text("A=1")                          # 민감 파일
    os.symlink(ws / ".env", ws / "plain.txt")                # 링크
    with pytest.raises(ContinueError):                       # 링크로 쓰기도 거부
        run(WRITE_TOOL.preprocess({"filepath": "plain.txt", "content": "x"}))   # 링크 대상 판정
    assert run(WRITE_TOOL.preprocess({"filepath": "ok.txt", "content": "x"})).args == {"filepath": "ok.txt", "content": "x"}   # 평범한 파일은 통과
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    pre = run(WRITE_TOOL.preprocess({"filepath": ".env", "content": "B=2"}))   # cn: 판정 없음
    assert run(WRITE_TOOL.run(pre.args, ToolRunContext("t"))).startswith("Successfully wrote to file: .env")   # cn: 덮어씀


# ───────── S7: Search 를 셸 없이 ─────────
@pytest.fixture                                              # 가짜 rg 준비
def fake_rg(ws, monkeypatch):                                # 받은 패턴을 그대로 출력하는 rg
    """rg --version 은 성공, 검색은 패턴($5) 이 '!' 면 종료 코드 2(stderr), 아니면 'x.txt:1:<받은 패턴>'."""
    bin_dir = ws / "bin"                                     # 가짜 실행 파일 폴더
    bin_dir.mkdir()                                          # 폴더 만들기
    rg = bin_dir / "rg"                                      # 가짜 rg
    rg.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ripgrep 14"; exit 0; fi\n'   # 설치 확인 응답
                  'if [ "$5" = "!" ]; then echo "regex parse error" >&2; exit 2; fi\n'   # 오류 경우
                  'printf "x.txt:1:%s\\n" "$5"\n')           # 다섯째 인자(패턴)를 그대로 출력
    rg.chmod(rg.stat().st_mode | stat.S_IEXEC)               # 실행 권한
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")   # 앞에 끼움
    monkeypatch.setenv("NCN_FIX_SECRET", "leaked")            # 셸이 풀면 드러나는 값
    return make_search_tool(str(ws))                         # 작업 폴더 기준 Search


@pytest.mark.skipif(os.name == "nt", reason="가짜 rg 는 sh 스크립트")   # Windows 에서는 건너뜀
def test_s7_search_no_shell(fake_rg, monkeypatch):           # 패턴이 글자 그대로 rg 에 감
    pattern = "$NCN_FIX_SECRET`echo hi`"                     # 셸이면 변수·명령 치환
    out = run(fake_rg.run({"pattern": pattern}))             # 고친 동작
    assert out == f'Search results for pattern "{pattern}":\n\nx.txt:1:{pattern}\n'   # 글자 그대로(출력 형식은 cn과 같음)
    with pytest.raises(Exception) as e:                      # 종료 코드 2
        run(fake_rg.run({"pattern": "!"}))                   # 오류 경우
    fixed_err = str(e.value)                                 # 고친 동작 오류 글
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert run(fake_rg.run({"pattern": pattern})) == f'Search results for pattern "{pattern}":\n\nx.txt:1:leakedhi\n'   # cn: 셸이 풀어 버림
    with pytest.raises(Exception) as e:                      # 같은 오류
        run(fake_rg.run({"pattern": "!"}))                   # cn 동작 오류
    assert str(e.value) == fixed_err and fixed_err.startswith('Error executing search: Command failed: rg --line-number --with-filename --color never "!"')   # 오류 글은 cn과 같음


# ───────── B1: requestOptions.timeout 초 단위 ─────────
def test_b1_timeout_seconds(monkeypatch):                    # 초 → 밀리초
    c = LLMClient(api_base="http://127.0.0.1:1/v1", api_key="k", model="m", request_options={"timeout": 60})   # 60초
    assert c.sdk_timeout_ms == 60000 and c.socket_timeout_s == 60   # SDK 60000ms, 소켓 60초
    assert LLMClient(api_base="http://127.0.0.1:1/v1", api_key="k", model="m").sdk_timeout_ms == SDK_DEFAULT_TIMEOUT_MS   # 없으면 기본 10분
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert LLMClient(api_base="http://127.0.0.1:1/v1", api_key="k", model="m", request_options={"timeout": 60}).sdk_timeout_ms == 60   # cn: 60ms


# ───────── B3: 헤드리스 AskQuestion ─────────
def test_b3_ask_question_without_answerer(monkeypatch):      # 답할 화면이 없으면 즉시 오류
    quiz_service.on_question_requested.clear()               # 듣는 화면 없음(헤드리스)
    with pytest.raises(ContinueError) as e:                  # 즉시 오류 기대
        run(asyncio.wait_for(ASK_QUESTION_TOOL.run({"question": "Q?", "options": []}), 1))   # 1초 안에 끝나야 함
    assert str(e.value) == NO_ANSWERER_MESSAGE               # 안내 글

    async def with_answerer() -> str:                        # 화면이 있으면 cn처럼 기다렸다 답
        quiz_service.on_question_requested.append(lambda pq: asyncio.get_running_loop().call_soon(   # 화면 흉내: 질문이 오면 다음 차례에 답
            quiz_service.answer_question, pq.request_id, "a"))   # 답 'a'
        try:                                                 # 화면 정리 보장
            return await ASK_QUESTION_TOOL.run({"question": "Q?", "options": ["a"]})   # 질문
        finally:                                             # 끝나면
            quiz_service.on_question_requested.clear()       # 화면 떼기
    assert run(with_answerer()) == 'User selected option 1: "a"'   # cn과 같은 답 글
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    with pytest.raises(asyncio.TimeoutError):                # cn: 끝없이 기다림
        run(asyncio.wait_for(ASK_QUESTION_TOOL.run({"question": "Q?", "options": []}), 0.05))   # 0.05초 안에 안 끝남
    quiz_service.initialize()                                # 남은 대기 질문 정리


# ───────── B4: Bash 출력 해석 ─────────
def test_b4_powershell_utf8_prefix(monkeypatch):             # Windows 명령 앞부분
    formatters.PLATFORM_OVERRIDE = "win32"                   # win32 흉내
    try:                                                     # 강제값은 꼭 되돌림
        shell, args = bash_mod.get_shell_command("dir")      # 고친 동작
        assert shell == "powershell.exe" and args[:4] == ["-NoLogo", "-ExecutionPolicy", "Bypass", "-Command"]   # cn과 같은 셸·옵션
        assert args[4] == bash_mod.PS_UTF8_PREFIX + "dir" and "UTF8Encoding $false" in args[4]   # BOM 없는 UTF-8 강제 + 원래 명령
        monkeypatch.setenv(COMPAT_ENV, "1")                  # cn 동작
        assert bash_mod.get_shell_command("dir")[1][4] == "dir"   # cn: 앞부분 없음
    finally:                                                 # 정리
        formatters.PLATFORM_OVERRIDE = None                  # 원래 플랫폼


def test_b4_hybrid_decoder():                                # 증분 해석·cp949 대체
    data = "한글 출력".encode("utf-8")                         # 여러 바이트 글자
    dec = bash_mod._HybridDecoder("cp949")                   # 대체 cp949
    assert dec.decode(data[:1]) + dec.decode(data[1:4]) + dec.decode(data[4:]) + dec.decode(b"", True) == "한글 출력"   # 경계가 글자 중간이어도 온전
    raw = "오류: 파일 없음".encode("cp949")                    # cp949 로 직접 쓰는 exe 출력
    dec = bash_mod._HybridDecoder("cp949")                   # 새 해석기
    assert dec.decode(b"ok\n") + dec.decode(raw[:3]) + dec.decode(raw[3:]) + dec.decode(b"", True) == "ok\n오류: 파일 없음"   # UTF-8 이 아니면 cp949 로 넘어감


async def _read_via_handle(chunks: list) -> str:             # 조각을 손잡이 _read 로 흘려 모은 글
    """가짜 StreamReader 에 조각을 넣고 _BashChildHandle._read 로 읽는다(조각 경계를 마음대로 정하려고)."""
    reader = asyncio.StreamReader()                          # 가짜 스트림
    out: list = []                                           # 받은 글
    h = bash_mod._BashChildHandle.__new__(bash_mod._BashChildHandle)   # 프로세스 없이 손잡이만
    h.on_stdout = out.append                                 # 받는 곳
    h.on_stderr = None                                       # stderr 없음

    async def feed() -> None:                                # 조각을 하나씩 넣는 작업
        for c in chunks:                                     # 조각마다
            reader.feed_data(c)                              # 넣기
            await asyncio.sleep(0)                           # 읽는 쪽이 따로 읽게 양보
        reader.feed_eof()                                    # 끝
    await asyncio.gather(feed(), h._read(reader, "stdout"))  # 넣기와 읽기 동시에
    return "".join(out)                                      # 모은 글


def test_b4_bash_handle_chunk_boundary(monkeypatch):         # 실제 손잡이 흐름에서 경계 처리
    data = "가".encode("utf-8")                                # 3바이트 글자
    assert run(_read_via_handle([data[:2], data[2:]])) == "가"   # 고친 동작: 온전
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    assert run(_read_via_handle([data[:2], data[2:]])) == "\ufffd\ufffd"   # cn: 조각마다 해석해 깨짐(앞 2바이트 1개·끝 1바이트 1개)


@pytest.mark.skipif(os.name == "nt", reason="printf 는 posix 셸")   # Windows 에서는 건너뜀
def test_b4_real_bash_run_korean(ws):                        # 실제 셸로 한글 출력(macOS)
    tool = bash_mod.make_bash_tool(str(ws))                  # Bash 도구
    out = run(tool.run({"command": "printf '한글\\n'"}, ToolRunContext("t")))   # 실행
    assert out == "한글\n"                                    # 온전한 한글


# ───────── B6: Edit·MultiEdit 승인 뒤 다시 읽기 ─────────
@pytest.fixture                                              # Edit 시험 파일
def edit_file(ws):                                           # Read 까지 마친 파일
    p = ws / "e.txt"                                         # 파일 경로
    p.write_text("alpha\nbeta\ngamma\n")                     # 원본
    run(READ_TOOL.run({"filepath": "e.txt"}, ToolRunContext("r")))   # Read 기록(Edit 전제)
    return p                                                 # 경로 반환


def test_b6_edit_reapplies_on_change(edit_file, monkeypatch):   # 바뀐 내용에 다시 적용
    pre = run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "beta", "new_string": "BETA"}))   # 미리 계산
    assert pre.args[REDO_KEY] == {"old_string": "beta", "new_string": "BETA", "replace_all": False}   # 재료가 run 인자에
    edit_file.write_text("alpha\nbeta\ngamma\ndelta (사용자 추가)\n")   # 승인 대기 중 사용자가 고침
    out = run(EDIT_TOOL.run(pre.args, ToolRunContext("t")))  # 승인 뒤 실행
    assert edit_file.read_text() == "alpha\nBETA\ngamma\ndelta (사용자 추가)\n"   # 사용자 수정 보존 + 편집 적용
    assert out.startswith(f"Successfully edited {edit_file}\nDiff:\n") and "+delta" not in out   # diff 기준은 지금 내용
    pre = run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "gamma", "new_string": "G"}))   # 다음 편집
    edit_file.write_text("alpha\nBETA\n")                    # 바꿀 글을 사용자가 지움
    with pytest.raises(ContinueError) as e:                  # 다시 찾기 실패
        run(EDIT_TOOL.run(pre.args, ToolRunContext("t")))    # 실행
    assert str(e.value) == 'Edit at index 0: string not found in file: "gamma"' and edit_file.read_text() == "alpha\nBETA\n"   # cn 형식 오류, 파일 안 바뀜


def test_b6_edit_compat_overwrites(edit_file, monkeypatch):  # cn 동작은 덮어씀
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    pre = run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "beta", "new_string": "BETA"}))   # 미리 계산
    assert REDO_KEY not in pre.args                          # cn 인자 모양 그대로
    edit_file.write_text("alpha\nbeta\ngamma\ndelta\n")      # 사용자가 고침
    run(EDIT_TOOL.run(pre.args, ToolRunContext("t")))        # 실행
    assert edit_file.read_text() == "alpha\nBETA\ngamma\n"   # cn: 사용자 수정(delta) 사라짐


def test_b6_multi_edit(edit_file, monkeypatch):              # MultiEdit 도 같게
    edits = [{"old_string": "alpha", "new_string": "A"}, {"old_string": "gamma", "new_string": "G"}]   # 두 편집
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": str(edit_file), "edits": edits}))   # 미리 계산
    edit_file.write_text("alpha\nbeta\ngamma\nzeta\n")       # 사용자가 고침
    out = run(MULTI_EDIT_TOOL.run(pre.args, ToolRunContext("t")))   # 실행
    assert edit_file.read_text() == "A\nbeta\nG\nzeta\n" and out.startswith(f"Successfully edited {edit_file} with 2 edits\n")   # 다시 적용
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": str(edit_file), "edits": [{"old_string": "beta", "new_string": "B"}, {"old_string": "zeta", "new_string": "Z"}]}))   # 다음
    edit_file.write_text("A\nbeta\nG\n")                     # 둘째 편집 대상이 사라짐
    with pytest.raises(ContinueError) as e:                  # 전부 아니면 전무
        run(MULTI_EDIT_TOOL.run(pre.args, ToolRunContext("t")))   # 실행
    assert str(e.value) == 'Edit at index 1: string not found in file: "zeta"' and edit_file.read_text() == "A\nbeta\nG\n"   # cn 형식 오류, 안 씀
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 동작
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": str(edit_file), "edits": [{"old_string": "beta", "new_string": "B"}]}))   # 미리 계산
    edit_file.write_text("A\nbeta\nG\nnew\n")                # 사용자가 고침
    run(MULTI_EDIT_TOOL.run(pre.args, ToolRunContext("t")))  # 실행
    assert edit_file.read_text() == "A\nB\nG\n"              # cn: 사용자 수정 사라짐
