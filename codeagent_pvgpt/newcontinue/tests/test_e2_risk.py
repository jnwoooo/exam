# tests/test_e2_risk.py — [ncn E2] 인자 기반 위험 판정 시험(cn 원본 없음 — ncn 전용)
# 기본 규칙(CI·빌드 설정·마이그레이션·비밀 후보 쓰기, git push·rm -r 등)은 allow → ask 로만 올린다. 도구 목록(인자 {})은 그대로
import json                                                  # 차등 시나리오 읽기
import pathlib                                               # 경로

import pytest                                                # 시험 도구

from ncn.permissions.risk import DEFAULT_RISK_PATHS, RiskRules, command_segments   # 시험 대상
from ncn.permissions.service import PermissionService        # 권한 서비스
from ncn.policy import POLICY_ENV                            # 시험용 정책 경로
from ncn.tools.base import Tool                              # 공용 도구 형식

SCENARIOS = pathlib.Path(__file__).resolve().parent.parent / "parity" / "scenarios"   # 차등 시나리오 폴더


async def _run(args, ctx):                                   # 쓰지 않는 실행 함수
    return ""                                               # 빈 결과


def tool(name: str) -> Tool:                                # 이름만 있는 도구
    """Bash 면 서비스가 내장 위험 판정을 붙인다."""
    return Tool(name=name, display_name=name, description="", parameters={}, run=_run)   # 생성


def svc(tmp_path, **kw) -> PermissionService:               # 서비스(전역 폴더 = 임시)
    """헤드리스 + Write/Edit/MultiEdit 허용이 기본(위험 판정이 보이도록)."""
    kw.setdefault("headless", True)                          # 헤드리스(Bash 기본 allow)
    kw.setdefault("allow", ["Write", "Edit", "MultiEdit"])   # 쓰기 허용
    return PermissionService(global_dir=tmp_path / "gdir", **kw)   # 생성


def ncn_yaml(tmp_path, text: str) -> None:                  # ~/.continue/ncn.yaml 쓰기
    """전역 폴더에 ncn.yaml."""
    d = tmp_path / "gdir"                                    # 전역 폴더
    d.mkdir(exist_ok=True)                                   # 만들기
    (d / "ncn.yaml").write_text(text, encoding="utf-8")      # 쓰기


RISKY_COMMANDS = [                                           # 위험 명령(ask 로 올라감)
    "git push origin main", "git push --force", "git -C repo push", "cd a && git push", "git reset --hard HEAD~1",   # git
    "git clean -fdx", "rm -r build", "rm -rf node_modules", "rm -fr x", "rm -R x", "rm --recursive x",   # 지우기
    "del /s /q build", "DEL /S x", "rd /s /q out", "Remove-Item -Recurse -Force bin", "remove-item bin -r",   # Windows
    "msbuild App.sln /t:Publish", "MSBuild.exe a.csproj \"-target:Build;Publish\"", "dotnet publish -c Release",   # 배포
    "dotnet nuget push a.nupkg", "npm publish", "bash -c \"git push\"", "sudo rm -rf /tmp/x",   # 배포·감싼 명령
]                                                            # 목록 끝
SAFE_COMMANDS = [                                            # 안전 명령(allow 그대로)
    "git status", "git log --grep push", "git commit -m 'reset --hard later'", "rm -f readme.md", "rm file.txt",   # 비슷하지만 안전
    "ls -la", "dotnet build", "msbuild App.sln /t:Build", "echo pushed", "npm test", "del a.txt",   # 일반
]                                                            # 목록 끝


@pytest.mark.parametrize("command", RISKY_COMMANDS)          # 위험 명령마다
def test_risky_commands_raise_to_ask(tmp_path, command):     # 위험 명령 → ask
    """헤드리스 기본 Bash allow 가 ask 로(헤드리스에선 정책 거부)."""
    assert svc(tmp_path).check(tool("Bash"), {"command": command}) in ("ask", "exclude"), command   # rm -rf / 류는 cn 판정이 이미 exclude


@pytest.mark.parametrize("command", SAFE_COMMANDS)           # 안전 명령마다
def test_safe_commands_stay_allow(tmp_path, command):        # 안전 명령 → allow
    """비슷한 글이 있어도 하위 명령이 다르면 그대로."""
    assert svc(tmp_path).check(tool("Bash"), {"command": command}) == "allow", command   # allow 유지


@pytest.mark.parametrize("path,risky", [                     # 경로마다
    (".github/workflows/ci.yml", True), ("sub/.github/x.yml", True), (".gitlab-ci.yml", True),   # CI
    ("azure-pipelines.yml", True), ("App.sln", True), ("src/App.csproj", True), ("native/a.vcxproj", True),   # 빌드
    ("Directory.Build.props", True), ("directory.build.targets", True), ("package.json", True),   # 빌드(대소문자 무시)
    ("pyproject.toml", True), ("db/migrations/0001_init.py", True), ("schema.SQL", True),   # 빌드·마이그레이션
    (".env.local", True), ("certs/server.pem", True), ("a.key", True),   # 비밀 후보
    ("src/app.py", False), ("README.md", False), ("docs/ci.yml", False), ("src/package.py", False),   # 일반 파일
])                                                           # 목록 끝
def test_write_targets(tmp_path, monkeypatch, path, risky):  # 쓰기 대상 판정
    """Write(filepath)·Edit/MultiEdit(file_path) 모두 같은 규칙. 절대경로도 작업 폴더 기준."""
    monkeypatch.chdir(tmp_path)                              # 작업 폴더
    s = svc(tmp_path)                                        # 서비스
    expect = "ask" if risky else "allow"                     # 기대값
    assert s.check(tool("Write"), {"filepath": path}) == expect   # Write
    assert s.check(tool("Edit"), {"file_path": str(tmp_path / path)}) == expect   # Edit(절대경로)
    assert s.check(tool("MultiEdit"), {"file_path": path, "edits": []}) == expect   # MultiEdit


def test_tool_list_unchanged(tmp_path):                      # 요청 본문 불변
    """인자 {} 판정(도구 목록)에는 위험 판정이 없다 — cn과 같은 도구 목록."""
    s = svc(tmp_path)                                        # 서비스
    for name, expect in (("Bash", "allow"), ("Write", "allow"), ("Read", "allow")):   # 도구마다
        assert s.base_permission(name) == expect             # 그대로


def test_exclude_and_ask_unchanged(tmp_path):                # 내리지 않음
    """exclude·ask 는 그대로(위험 판정은 올리기만)."""
    s = svc(tmp_path, exclude=["Write"], ask=["Edit"])       # 사용자 금지·묻기
    assert s.check(tool("Write"), {"filepath": "a.sln"}) == "exclude"   # 금지 유지
    assert s.check(tool("Edit"), {"file_path": "a.py"}) == "ask"   # 묻기 유지
    assert s.check(tool("Bash"), {"command": "rm -rf /"}) == "exclude"   # cn 위험 명령 판정 유지


def test_auto_mode_disables_e2(tmp_path):                    # --auto·auto 모드
    """사용자가 전부 허용을 골랐으면 위험 판정 없음(관리자 force_ask 만 남음 — test_e1)."""
    assert svc(tmp_path, auto=True).check(tool("Bash"), {"command": "git push"}) == "allow"   # --auto
    s = svc(tmp_path, headless=False)                        # 대화형
    s.set_mode("auto")                                       # 모드 전환
    assert s.check(tool("Write"), {"filepath": "App.sln"}) == "allow"   # auto
    s.set_mode("normal")                                     # 되돌림
    assert s.check(tool("Bash"), {"command": "git push"}) == "ask"   # 다시 켜짐


def test_plan_mode_keeps_e2(tmp_path):                       # plan 모드
    """plan 의 Bash allow 도 위험하면 ask."""
    assert svc(tmp_path, mode="plan").check(tool("Bash"), {"command": "git push"}) == "ask"   # ask


def test_specific_user_allow_rule_is_respected(tmp_path):    # 콕 집은 사용자 허용
    """'다시 묻지 않기'로 저장한 Bash(git push origin main)·--allow 'Bash(git push*)' 는 그대로 allow."""
    d = tmp_path / "gdir"                                    # 전역 폴더
    d.mkdir()                                                # 만들기
    (d / "permissions.yaml").write_text("allow:\n  - Bash(git push origin main)\n", encoding="utf-8")   # 저장된 규칙
    s = svc(tmp_path, headless=False)                        # 대화형
    assert s.check(tool("Bash"), {"command": "git push origin main"}) == "allow"   # 콕 집은 규칙
    assert s.check(tool("Bash"), {"command": "git push origin dev"}) == "ask"   # 다른 명령은 기본 ask
    s2 = svc(tmp_path, allow=["Bash(git push*)"])            # CLI 패턴
    assert s2.check(tool("Bash"), {"command": "git push"}) == "allow"   # 그대로


def test_always_allow_flow_for_risky_command(tmp_path):      # 승인 '다시 묻지 않기' 흐름
    """한 번 '다시 묻지 않기' 하면 같은 명령은 다음부터 allow(정확 일치 규칙 — 부록B S1)."""
    s = svc(tmp_path, headless=False, allow=[])              # 대화형
    b = tool("Bash")                                         # Bash
    s.set_mode("normal")                                     # normal
    assert s.check(b, {"command": "git push origin main"}) == "ask"   # 처음엔 ask
    s.add_always_allow("Bash", {"command": "git push origin main"})   # 다시 묻지 않기
    assert s.check(b, {"command": "git push origin main"}) == "allow"   # 다음엔 allow


def test_ncn_yaml_rules(tmp_path):                           # ncn.yaml risk 설정
    """추가 규칙, defaults:false(기본 규칙만 끔), enabled:false(전부 끔)."""
    ncn_yaml(tmp_path, "risk:\n  commands: ['terraform apply*']\n  paths: ['deploy/']\n")   # 추가
    s = svc(tmp_path)                                        # 서비스
    assert s.check(tool("Bash"), {"command": "cd infra && terraform apply -auto-approve"}) == "ask"   # 조각 일치
    assert s.check(tool("Write"), {"filepath": "deploy/prod.cfg"}) == "ask"   # 경로
    assert s.check(tool("Bash"), {"command": "git push"}) == "ask"   # 기본 규칙도
    ncn_yaml(tmp_path, "risk:\n  defaults: false\n  commands: ['terraform apply*']\n")   # 기본 규칙 끔
    s = svc(tmp_path)                                        # 서비스
    assert s.check(tool("Bash"), {"command": "git push"}) == "allow"   # 기본 규칙 꺼짐
    assert s.check(tool("Bash"), {"command": "terraform apply"}) == "ask"   # 추가 규칙은 남음
    ncn_yaml(tmp_path, "risk:\n  enabled: false\n")          # 전부 끔
    assert svc(tmp_path).check(tool("Bash"), {"command": "git push"}) == "allow"   # 꺼짐
    ncn_yaml(tmp_path, "risk: [oops]\n")                     # 형식 오류(사용자 설정은 무시하고 기본값)
    assert svc(tmp_path).check(tool("Bash"), {"command": "git push"}) == "ask"   # 기본 규칙


def test_admin_risk_rules(tmp_path, monkeypatch):            # 관리자 risk 추가 규칙
    """policy.yaml risk 는 사용자 규칙처럼(auto 에선 꺼짐), 사용자 defaults:false 로는 안 꺼짐."""
    p = tmp_path / "policy.yaml"                              # 정책
    p.write_text("risk:\n  commands: ['kubectl delete*']\n", encoding="utf-8")   # 추가 규칙
    monkeypatch.setenv(POLICY_ENV, str(p))                    # 지정
    ncn_yaml(tmp_path, "risk:\n  defaults: false\n")          # 사용자 기본 규칙 끔
    assert svc(tmp_path).check(tool("Bash"), {"command": "kubectl delete ns x"}) == "ask"   # 관리자 규칙
    assert svc(tmp_path, auto=True).check(tool("Bash"), {"command": "kubectl delete ns x"}) == "allow"   # auto 에선 꺼짐


def test_compat_disables_e2(tmp_path, cn_compat_env):        # NCN_CN_COMPAT=1
    """cn 원래 동작: 위험 판정 없음."""
    assert svc(tmp_path).check(tool("Bash"), {"command": "git push"}) == "allow"   # 꺼짐


def test_command_segments():                                 # 조각 나누기
    """; && || | & 줄바꿈으로 나누고 소문자·공백 정리·sudo 떼기."""
    assert command_segments("A  &&  sudo B | c;d\ne || f & g") == ["a", "b", "c", "d", "e", "f", "g"]   # 조각


def test_rules_object_direct():                              # 규칙 객체 직접
    """빈 규칙은 아무것도 안 걸림, 다른 도구·인자 없음은 None."""
    assert RiskRules().match("Bash", {"command": "git push"}) is None   # 빈 규칙
    r = RiskRules(use_defaults=True)                         # 기본 규칙
    assert r.match("Read", {"filepath": ".github/x"}) is None   # Read 는 대상 아님
    assert r.match("Write", {}) is None                      # 인자 없음
    assert r.match("Write", {"filepath": 3}) is None         # 글 아님
    assert r.match("Bash", {"command": "git push"}) == "command:git push"   # 이유 글
    assert ".github/" in DEFAULT_RISK_PATHS                  # 기본 목록


def test_default_rules_do_not_touch_parity_scenarios(tmp_path, monkeypatch):   # 차등 시나리오와 충돌 없음
    """시나리오의 Bash·Write·Edit 호출 중 기본 위험 규칙에 걸리는 것은 cn 판정이 이미 exclude(09 rm -rf /)뿐이어야 한다."""
    monkeypatch.chdir(tmp_path)                              # 작업 폴더
    r = RiskRules(use_defaults=True)                         # 기본 규칙
    hits = []                                                # 걸린 호출
    for f in sorted(SCENARIOS.glob("*.json")):               # 시나리오마다
        data = json.loads(f.read_text(encoding="utf-8"))     # 읽기
        for turn in data.get("llm", []):                     # 응답마다
            for call in turn.get("tool_calls", []) or []:    # 도구 호출마다
                if r.match(call["name"], call.get("arguments") or {}):   # 걸리면
                    hits.append((f.stem, call["name"], call.get("arguments")))   # 기록
    assert [h[0] for h in hits] in ([], ["09_bash_dangerous"]), hits   # 09 만(cn에서도 exclude)
