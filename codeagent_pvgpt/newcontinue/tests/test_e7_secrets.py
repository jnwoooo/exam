# tests/test_e7_secrets.py — [ncn E7] 비밀 보호 시험(cn 원본 없음 — ncn 전용)
# ① Bash·MCP stdio 자식 환경에서 비밀 환경변수 제거 ② Read·Edit·Write 의 키 파일 차단(cn 오류 글 그대로)
import asyncio                                               # 도구 실행
import os                                                    # 환경변수
import sys                                                   # 플랫폼

import pytest                                                # 시험 도구

from ncn.mcp import transports                               # MCP stdio 환경
from ncn.policy import POLICY_ENV                            # 시험용 정책 경로
from ncn.security import secrets                             # 시험 대상
from ncn.security.ignore import is_security_concern, throw_if_file_is_security_concern   # 민감 파일 판정
from ncn.tools import bash as bash_mod                       # Bash 도구
from ncn.tools.base import ContinueError, ToolRunContext     # 공용 형식


@pytest.fixture(autouse=True)                                # 시험마다
def _gdir(tmp_path, monkeypatch):                            # 전역 폴더를 임시로(사용자 ncn.yaml 영향 없음)
    """CONTINUE_GLOBAL_DIR = 임시 폴더."""
    d = tmp_path / "gdir"                                    # 전역 폴더
    d.mkdir()                                                # 만들기
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(d))        # 지정
    return d                                                 # 경로


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GITHUB_TOKEN", "db_password", "CODEAGENT_TOKEN", "AWS_SECRET_ACCESS_KEY",   # 비밀
                                  "AWS_ACCESS_KEY_ID", "CLIENT_SECRET", "PGPASSWORD", "AZURE_DEVOPS_PAT", "SQL_CONNECTION_STRING"])   # (이어짐)
def test_secret_names(name):                                 # 비밀 이름
    """기본 패턴(대소문자 무시)."""
    assert secrets.is_secret_env_name(name)                  # 비밀


@pytest.mark.parametrize("name", ["PATH", "HOME", "PWD", "OLDPWD", "TOKENIZERS_PARALLELISM", "KEYBOARD", "USERPROFILE",   # 비밀 아님
                                  "SystemRoot", "SSH_AUTH_SOCK", "LANG"])   # (이어짐)
def test_non_secret_names(name):                             # 비밀 아님
    """흔한 환경변수는 남는다."""
    assert not secrets.is_secret_env_name(name)              # 비밀 아님


def test_bash_child_env_none_when_nothing_to_strip(monkeypatch):   # 뺄 것이 없으면 None
    """None = cn처럼 그대로 물려받음(spawn 기본)."""
    for k in list(os.environ):                               # 현재 환경에서
        if secrets.is_secret_env_name(k):                    # 비밀 이름이면
            monkeypatch.delenv(k)                            # 지움
    assert secrets.bash_child_env() is None                  # 그대로
    assert bash_mod.ENV_PROVIDER is secrets.bash_child_env   # Bash 연결점 기본값


def test_bash_child_env_strips(monkeypatch):                 # 비밀 제거
    """비밀 이름만 빠지고 나머지는 그대로."""
    monkeypatch.setenv("NCN_T_API_KEY", "sk-1")              # 비밀
    monkeypatch.setenv("NCN_T_PLAIN", "ok")                  # 일반
    env = secrets.bash_child_env()                           # 자식 환경
    assert "NCN_T_API_KEY" not in env and env["NCN_T_PLAIN"] == "ok"   # 판정
    assert "PATH" in env or "Path" in env                    # 실행에 필요한 것은 남음


@pytest.mark.skipif(sys.platform == "win32", reason="posix 셸")   # Windows 는 PowerShell
def test_bash_tool_cannot_see_secret(tmp_path, monkeypatch):   # 실제 Bash 실행
    """모델이 echo 로 비밀을 읽을 수 없다."""
    monkeypatch.setenv("SHELL", "/bin/sh")                   # 빠른 셸
    monkeypatch.setenv("NCN_T_TOKEN", "leak-me")             # 비밀
    monkeypatch.setenv("NCN_T_VISIBLE", "seen")              # 일반
    t = bash_mod.make_bash_tool(tmp_path)                    # 도구
    out = asyncio.run(t.run({"command": 'echo "[$NCN_T_TOKEN][$NCN_T_VISIBLE]"'}, ToolRunContext("t1")))   # 실행
    assert out.strip() == "[][seen]"                         # 비밀은 빈 값


def test_settings_allow_disable_and_enforce(_gdir, tmp_path, monkeypatch):   # 설정
    """env_allow 예외, enabled:false 끔, 관리자 enforce 면 못 끔, compat 은 끔(enforce 없을 때)."""
    monkeypatch.setenv("NCN_T_TOKEN", "x")                   # 비밀
    (_gdir / "ncn.yaml").write_text("secrets:\n  env_allow: ['NCN_T_TOKEN']\n  env_patterns: ['MYCORP_*']\n", encoding="utf-8")   # 예외·추가
    s = secrets.load_secret_settings()                       # 설정
    assert not secrets.is_secret_env_name("NCN_T_TOKEN", s)  # 예외
    assert secrets.is_secret_env_name("MYCORP_ANYTHING", s)  # 추가 패턴
    (_gdir / "ncn.yaml").write_text("secrets:\n  enabled: false\n", encoding="utf-8")   # 끔
    assert secrets.bash_child_env() is None                  # 그대로 물려받음
    pol = tmp_path / "policy.yaml"                            # 관리자 정책
    pol.write_text("secrets:\n  enforce: true\n  env_patterns: ['CORP_*']\n", encoding="utf-8")   # 강제
    monkeypatch.setenv(POLICY_ENV, str(pol))                  # 지정
    env = secrets.bash_child_env()                           # 자식 환경
    assert env is not None and "NCN_T_TOKEN" not in env      # 사용자가 꺼도 강제
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 동작 스위치
    assert secrets.bash_child_env() is not None              # 강제는 compat 도 무시
    monkeypatch.delenv(POLICY_ENV)                           # 정책 없앰
    (_gdir / "ncn.yaml").write_text("{}\n", encoding="utf-8")   # 기본 설정
    assert secrets.bash_child_env() is None                  # compat → 끔


def test_mcp_stdio_env_strips_inherited_only(monkeypatch):   # MCP stdio
    """설정 env 에 직접 적은 비밀은 유지, 물려받는 것만 뺀다(빈 값 제외 규칙은 cn 그대로)."""
    env = transports.stdio_env({"env": {"SERVER_TOKEN": "mine"}}, {"GITHUB_TOKEN": "g", "A": "1", "B": "", "SERVER_TOKEN": "x"})   # 계산
    assert env == {"SERVER_TOKEN": "mine", "A": "1"}         # 판정


def test_mcp_stdio_env_compat(cn_compat_env):                # compat
    """NCN_CN_COMPAT=1 이면 cn처럼 전부 물려줌."""
    assert transports.stdio_env({}, {"GITHUB_TOKEN": "g"}) == {"GITHUB_TOKEN": "g"}   # 그대로


@pytest.mark.parametrize("path", ["id_rsa_work", "keys2/id_ed25519.bak2", ".netrc", "_netrc", ".git-credentials",   # 추가 키 파일
                                  ".npmrc", ".pypirc", "vault.kdbx", "AuthKey_X.p8", "/home/u/proj/credentials.json"])   # (이어짐)
def test_extra_key_files_blocked(path):                      # 추가 차단
    """cn 목록에 없던 키 파일도 막는다."""
    assert is_security_concern(path)                         # 민감


@pytest.mark.parametrize("path", ["server.pem", "a.key", "cert.pfx", "x.p12", "id_rsa", ".continue/.env"])   # cn 목록(원래 막힘)
def test_cn_key_files_still_blocked(path):                   # cn 목록 그대로
    """cn 목록은 그대로."""
    assert is_security_concern(path)                         # 민감


def test_extra_blocking_off_in_compat(cn_compat_env):        # compat
    """cn 원래 동작에서는 추가 패턴 없음."""
    assert not is_security_concern("id_rsa_work")            # 통과
    assert is_security_concern("id_rsa")                     # cn 목록은 그대로


def test_user_block_files_and_error_text(_gdir):             # 사용자 block_files·오류 글
    """ncn.yaml block_files 추가, 오류 글은 cn 원문 형식."""
    (_gdir / "ncn.yaml").write_text("secrets:\n  block_files: ['*.license']\n", encoding="utf-8")   # 추가
    assert is_security_concern("dist/app.license")           # 민감
    with pytest.raises(ContinueError) as ei:                 # 오류
        throw_if_file_is_security_concern("id_rsa_work")     # 추가 패턴
    assert ei.value.reason == "file_is_security_concern"     # 이유 코드
    assert str(ei.value.message if hasattr(ei.value, "message") else ei.value) == (   # cn 원문
        "Reading or Editing id_rsa_work is not allowed because it is a security concern. Do not attempt to read or edit this file in any way.")   # (이어짐)
    assert not is_security_concern("src/app.py")             # 일반 파일
