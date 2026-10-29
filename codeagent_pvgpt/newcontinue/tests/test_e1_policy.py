# tests/test_e1_policy.py — [ncn E1] 관리자 정책 파일 시험(cn 원본 없음 — ncn 전용)
# 정책은 NCN_POLICY_FILE(시험용)로 준다. conftest 가 시스템 정책 위치를 없는 경로로 바꿔 둔다
import asyncio                                               # 비동기 판정 실행
import logging                                               # 경고 확인
import pathlib                                               # 경로

import pytest                                                # 시험 도구

import ncn.policy as policy_mod                              # 정책 모듈(시스템 위치 바꿔 끼우기)
from ncn.loop.tool_calls import POLICY_DENIED, PreprocessedToolCall, ToolCall, check_tool_permission_approval   # 헤드리스 거부 경로
from ncn.permissions.service import PermissionService        # 권한 서비스
from ncn.policy import (POLICY_ENV, PolicyError, PolicyModeError, load_admin_policy, policy_path,   # 시험 대상
                        system_policy_path)                  # 원래 함수(conftest 가 바꾸기 전에 가져옴)
from ncn.tools.base import Tool                              # 공용 도구 형식


async def _run(args, ctx):                                   # 쓰지 않는 실행 함수
    return ""                                               # 빈 결과


def tool(name: str) -> Tool:                                # 동적 평가 없는 도구
    """이름만 있는 도구(Bash 면 서비스가 내장 위험 판정을 붙인다)."""
    return Tool(name=name, display_name=name, description="", parameters={}, run=_run)   # 도구 생성


@pytest.fixture                                              # 정책 파일 쓰기 도우미
def policy(tmp_path, monkeypatch):                           # 글을 받아 정책 파일로
    """policy("yaml 글") → 그 파일을 NCN_POLICY_FILE 로 지정."""
    def write(text: str, *, raw: bytes = None) -> pathlib.Path:   # 쓰기 함수
        p = tmp_path / "policy.yaml"                         # 정책 파일
        p.write_bytes(raw if raw is not None else text.encode("utf-8"))   # 바이트로(줄바꿈 변환 없음)
        monkeypatch.setenv(POLICY_ENV, str(p))               # 시험용 경로 지정
        return p                                             # 경로 반환
    return write                                             # 도우미 반환


def svc(tmp_path, **kw) -> PermissionService:               # 권한 서비스 만들기
    """전역 폴더를 임시 폴더로."""
    kw.setdefault("headless", False)                         # 기본 대화형
    return PermissionService(global_dir=tmp_path / "gdir", **kw)   # 생성


def test_no_policy_is_cn_behavior(tmp_path):                 # 정책 없으면 cn과 같음
    """정책 파일이 없으면 admin=None, 기본표 그대로."""
    s = svc(tmp_path)                                        # 서비스
    assert s.layer.admin is None                             # 정책 없음
    assert s.base_permission("Write") == "ask"               # 기본표
    assert s.base_permission("Read") == "allow"              # 기본표


def test_system_policy_path_by_platform(monkeypatch):       # 플랫폼별 위치
    """Windows %ProgramData%\\ncn\\policy.yaml, 그 밖 /etc/ncn/policy.yaml."""
    monkeypatch.setattr(policy_mod.sys, "platform", "win32")   # Windows 흉내
    assert str(system_policy_path({"ProgramData": "D:\\PD"})).replace("/", "\\").endswith("D:\\PD\\ncn\\policy.yaml")   # ProgramData 기준
    assert "ProgramData" in str(system_policy_path({}))      # 없으면 C:\ProgramData
    monkeypatch.setattr(policy_mod.sys, "platform", "linux")  # 리눅스 흉내
    assert system_policy_path({}) == pathlib.Path("/etc/ncn/policy.yaml")   # /etc


def test_system_policy_wins_over_env(tmp_path, monkeypatch, caplog):   # 시스템 정책이 있으면 환경변수 무시
    """사용자가 NCN_POLICY_FILE 로 관리자 정책을 바꿔 끼우지 못한다."""
    sys_file = tmp_path / "sys.yaml"                          # 시스템 정책 흉내
    sys_file.write_text("exclude: [Fetch]\n", encoding="utf-8")   # 내용
    monkeypatch.setattr(policy_mod, "system_policy_path", lambda environ=None: sys_file)   # 위치 바꿈
    other = tmp_path / "mine.yaml"                            # 사용자 파일
    other.write_text("{}\n", encoding="utf-8")               # 빈 정책
    with caplog.at_level(logging.WARNING, logger="ncn.policy"):   # 경고 수집
        assert policy_path({POLICY_ENV: str(other)}) == sys_file   # 시스템 정책
    assert "ignored" in caplog.text                          # 무시 경고


def test_exclude_beats_everything(tmp_path, policy):         # 정책 exclude 는 무엇으로도 못 바꿈
    """--allow·auto 모드·plan 모드·permissions.yaml allow 로도 exclude 유지 + 도구 목록에서 빠짐."""
    policy("exclude:\n  - Fetch\n  - mcp_*\n")              # 정책
    gdir = tmp_path / "gdir"                                  # 전역 폴더
    gdir.mkdir()                                             # 만들기
    (gdir / "permissions.yaml").write_text("allow: [Fetch]\n", encoding="utf-8")   # 사용자 allow
    for kw in ({}, {"allow": ["Fetch"]}, {"auto": True}, {"mode": "plan"}, {"headless": True}):   # 여러 경우
        s = svc(tmp_path, **kw)                              # 서비스
        assert s.check(tool("Fetch"), {"url": "http://x"}) == "exclude", kw   # 실행 시점
        assert s.base_permission("Fetch") == "exclude"       # 목록용
        assert not s.is_tool_offered("Fetch")                # 목록에서 빠짐
        assert s.check(tool("mcp_db_query"), {}) == "exclude"   # 글롭
    s = svc(tmp_path)                                        # 모드 전환 뒤에도
    s.set_mode("auto")                                       # auto 로
    assert s.check(tool("Fetch"), {}) == "exclude"           # 그대로


def test_policy_ask_applies_in_auto_and_keeps_user_exclude(tmp_path, policy):   # 정책 ask
    """--auto 여도 ask, 사용자가 exclude 한 것은 exclude(더 엄격한 쪽)."""
    policy("ask: [Write]\n")                                 # 정책
    assert svc(tmp_path, auto=True).check(tool("Write"), {"filepath": "a.py"}) == "ask"   # auto 에서도 ask
    assert svc(tmp_path, exclude=["Write"]).check(tool("Write"), {"filepath": "a.py"}) == "exclude"   # 사용자 금지 유지
    hs = svc(tmp_path, headless=True, allow=["Write"])       # 헤드리스 + --allow Write
    assert not hs.is_tool_offered("Write")                   # 헤드리스 ask → 목록에서 빠짐


def test_bash_pattern_checks_each_segment(tmp_path, policy):   # Bash(패턴) 은 조각마다
    """'cd x && git push' 도 Bash(git push*) exclude 에 걸린다. allow 는 명령 전체로만."""
    policy("exclude: ['Bash(git push*)']\nallow: ['Bash(ls*)']\n")   # 정책
    s = svc(tmp_path, headless=True)                         # 헤드리스(Bash 기본 allow)
    b = tool("Bash")                                         # Bash
    assert s.check(b, {"command": "git push origin main"}) == "exclude"   # 전체 일치
    assert s.check(b, {"command": "cd repo && git push"}) == "exclude"    # 조각 일치
    assert s.check(b, {"command": "git status"}) == "allow"   # 무관
    assert s.base_permission("Bash") == "allow"              # 인자 없으면 목록에 남음


def test_policy_allow_only_overrides_default_ask(tmp_path, policy):   # 정책 allow
    """기본표 ask 는 allow 로, 사용자가 직접 정한 ask·plan 금지는 그대로."""
    policy("allow: [Write]\n")                               # 정책
    assert svc(tmp_path).check(tool("Write"), {"filepath": "src/a.py"}) == "allow"   # 기본 ask → allow
    assert svc(tmp_path, ask=["Write"]).check(tool("Write"), {"filepath": "src/a.py"}) == "ask"   # 사용자 ask 유지
    assert svc(tmp_path, mode="plan").check(tool("Write"), {"filepath": "a.py"}) == "exclude"   # plan 금지 유지
    assert svc(tmp_path).check(tool("Write"), {"filepath": "App.csproj"}) == "ask"   # E2 위험 판정은 여전히


def test_policy_arg_names_are_real_tool_args(tmp_path, policy):   # 정책 규칙 인자 이름
    """Write(*.pem) 은 Write 의 실제 인자 filepath 와 비교(cn permissions.yaml 표는 file_path — S9)."""
    policy("exclude: ['Write(*.lock)', 'Edit(*.lock)']\n")   # 정책
    s = svc(tmp_path, allow=["Write", "Edit"])               # 사용자 allow
    assert s.check(tool("Write"), {"filepath": "a/yarn.lock"}) == "exclude"   # filepath
    assert s.check(tool("Edit"), {"file_path": "yarn.lock"}) == "exclude"   # file_path
    assert s.check(tool("Write"), {"filepath": "a.py"}) == "allow"   # 무관


def test_force_ask_ignores_auto_and_compat(tmp_path, policy, monkeypatch):   # force_ask
    """--auto·auto 모드·NCN_CN_COMPAT=1 에서도 ask, 헤드리스면 정책 거부 글."""
    policy("force_ask:\n  commands: ['git push*']\n  paths: ['*.sln']\n")   # 정책
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 동작 스위치
    s = svc(tmp_path, auto=True)                             # --auto
    assert s.check(tool("Bash"), {"command": "git push"}) == "ask"   # 명령
    assert s.check(tool("Write"), {"filepath": "A.SLN"}) == "ask"   # 경로(대소문자 무시)
    assert s.check(tool("Bash"), {"command": "git status"}) == "allow"   # 무관
    hs = svc(tmp_path, headless=True)                        # 헤드리스
    call = PreprocessedToolCall(call=ToolCall(id="c1", name="Bash", arguments={"command": "git push"}), tool=tool("Bash"))   # 호출
    ok, reason = asyncio.run(check_tool_permission_approval(call, lambda t, a: hs.check(t, a), None, None, True))   # 판정
    assert (ok, reason) == (False, "policy")                 # 정책 거부 → 모델에게 POLICY_DENIED 글
    assert POLICY_DENIED == "Command blocked by security policy"   # cn 원문 글


def test_deny_modes(tmp_path, policy):                       # deny_modes
    """auto 금지: --auto 시작 거부, set_mode 거부, Shift+Tab 순환에서 건너뜀."""
    policy("deny_modes: [auto]\n")                           # 정책
    with pytest.raises(PolicyModeError):                     # --auto
        svc(tmp_path, auto=True)                             # 시작 거부
    s = svc(tmp_path)                                        # normal
    with pytest.raises(ValueError):                          # ValueError 로도 잡힘
        s.set_mode("auto")                                   # 전환 거부
    assert s.mode == "normal"                                # 그대로
    assert s.cycle_mode() == "plan"                          # normal → plan
    assert s.cycle_mode() == "normal"                        # plan → (auto 건너뜀) → normal


@pytest.mark.parametrize("text", [                           # 깨진 정책들
    "allow: [Write\n",                                       # YAML 문법 오류
    "- just a list\n",                                       # 최상위가 목록
    "allow: Write\n",                                        # 목록 아님
    "exclude: ['Bash(a(b))']\n",                             # 패턴 오류
    "deny_modes: [normal]\n",                                # normal 금지 불가
    "force_ask: [git push]\n",                               # 사전 아님
    "secrets: {enforce: yes-please}\n",                      # 참거짓 아님
])                                                           # 목록 끝
def test_broken_policy_refuses_to_run(tmp_path, policy, text):   # 깨진 정책 → 실행 거부
    """PolicyError — 권한 서비스를 만들 수 없다(헤드리스: stderr 오류·종료 1, 대화형·ACP: 세션 열기 실패)."""
    p = policy(text)                                         # 정책
    with pytest.raises(PolicyError) as ei:                   # 실행 거부
        svc(tmp_path)                                        # 생성
    assert str(p) in ei.value.message                        # 파일 경로를 알려 줌


def test_missing_env_policy_file_is_error(tmp_path, monkeypatch):   # 지정했는데 없는 파일
    """NCN_POLICY_FILE 이 가리키는 파일이 없으면 깨짐으로 처리."""
    monkeypatch.setenv(POLICY_ENV, str(tmp_path / "nope.yaml"))   # 없는 파일
    with pytest.raises(PolicyError):                         # 실행 거부
        load_admin_policy()                                  # 읽기


def test_bom_empty_unknown_and_reserved_keys(tmp_path, policy, caplog):   # BOM·빈 파일·모르는 키·예약 키
    """Windows 메모장 BOM 도 읽음, 빈 파일은 규칙 없음, 모르는 키는 경고, 예약 키는 원래 값 보관."""
    policy("", raw="\ufeffexclude: [Fetch]\r\nmax_iterations: 30\r\naudit: {server: 'http://audit'}\r\nfuture_key: 1\r\n".encode("utf-8"))   # BOM·CRLF
    with caplog.at_level(logging.WARNING, logger="ncn.policy"):   # 경고 수집
        pol = load_admin_policy()                            # 읽기
    assert pol.rules[0].tool == "Fetch"                      # 규칙
    assert pol.reserved("max_iterations") == 30              # E5 예약 키
    assert pol.reserved("audit") == {"server": "http://audit"}   # E6 예약 키
    assert "future_key" in caplog.text                       # 모르는 키 경고
    policy("")                                               # 빈 파일
    assert load_admin_policy().rules == []                   # 규칙 없음


def test_compat_does_not_disable_admin_policy(tmp_path, policy, cn_compat_env):   # NCN_CN_COMPAT 로 정책을 끌 수 없음
    """사용자 환경변수로 관리자 정책을 끄지 못하게 E1 은 compat 에서도 적용."""
    policy("exclude: [Fetch]\n")                             # 정책
    assert svc(tmp_path).check(tool("Fetch"), {}) == "exclude"   # 그대로 금지


def test_subagent_permissions_follow_policy(tmp_path, policy, monkeypatch):   # 하위 대화에도 적용
    """하위 대화(cn: 전부 allow)도 정책 exclude·force_ask 를 따른다."""
    from ncn.subagent import SubagentPermissions             # 하위 대화 권한
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(tmp_path / "g"))   # 전역 폴더
    policy("exclude: [Fetch]\nforce_ask: {commands: ['git push*']}\n")   # 정책
    sp = SubagentPermissions(None)                           # 전부 allow
    assert sp.base_permission("Fetch") == "exclude"          # 목록에서 빠짐
    assert sp.check(tool("Bash"), {"command": "git push"}) == "ask"   # ask(하위 대화에선 거부)
    assert sp.check(tool("Read"), {"filepath": "a"}) == "allow"   # 무관


def test_chat_session_reports_policy_error(tmp_path, policy, monkeypatch):   # 대화형·ACP 세션 열기 실패
    """open_chat_core 가 SessionOpenError(정책 글)로 바꿔 올린다."""
    import argparse                                          # 명령줄 흉내
    from ncn import chat_session                             # 대화형 공용 조립
    policy("allow: [\n")                                     # 깨진 정책
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(tmp_path / "g"))   # 전역 폴더
    from ncn.app import build_parser                         # 명령줄 해석기
    a = build_parser().parse_args([])                        # 기본 인자
    with pytest.raises(chat_session.SessionOpenError) as ei:   # 세션 열기 실패
        chat_session.open_chat_core(a, environ=dict(__import__("os").environ), cwd=str(tmp_path),   # 조립
                                    select_session=lambda m: ("x", []), on_event=lambda e: None,   # 쓰이지 않는 콜백
                                    ask_user=None, get_signal=lambda: None)   # (이어짐)
    assert "Administrator policy" in ei.value.message        # 정책 글
