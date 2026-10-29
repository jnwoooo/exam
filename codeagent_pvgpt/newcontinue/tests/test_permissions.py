# tests/test_permissions.py — 권한 정책·일치 규칙·우선순위·permissions.yaml·서비스 시험
# 근거: cn extensions/cli/src/permissions/*.test.ts(permissionChecker·precedenceResolver·permissionsYamlLoader·policyWriter·defaultPolicies)
#       services/ToolPermissionService.modes.test.ts, P0/소스확인_terminal_security.md 6.2절 표
import pytest                                                # 시험 도구

from ncn.permissions.matcher import check_tool_permission, matches_arguments, matches_tool_pattern   # 일치 규칙
from ncn.permissions.policies import (AUTO_MODE_POLICIES, PLAN_MODE_POLICIES, ToolPermissionPolicy as P,
                                      convert_legacy_mode_flags, get_default_tool_policies)   # 정책표
from ncn.permissions.service import PermissionService, resolve_permission_precedence   # 서비스
from ncn.permissions.yaml_loader import (DEFAULT_PERMISSIONS_YAML, MANAGED_HEADER, add_policy_to_yaml,
                                         generate_policy_rule, load_permissions_yaml, parse_tool_pattern,
                                         yaml_config_to_policies)   # 파일
from ncn.security.terminal_security import evaluate_terminal_command_security   # Bash 동적 평가
from ncn.tools.base import Tool                              # 공용 도구 형식


async def _run(args, ctx):                                   # 시험용 실행 함수(쓰지 않음)
    return ""


def bash_tool(**kw) -> Tool:
    """runTerminalCommand.ts 와 같은 동적 평가 연결: evaluateTerminalCommandSecurity(base, parsedArgs.command)."""
    return Tool(name="Bash", display_name="Bash", description="", parameters={}, run=_run,
                evaluate_tool_call_policy=lambda base, args: evaluate_terminal_command_security(base, args.get("command")), **kw)


def plain_tool(name: str) -> Tool:
    """동적 평가가 없는 도구."""
    return Tool(name=name, display_name=name, description="", parameters={}, run=_run)


# ───────── matchesToolPattern ─────────
@pytest.mark.parametrize("name,pattern,args,expected", [
    ("readFile", "readFile", None, True), ("writeFile", "readFile", None, False),
    ("anyTool", "*", None, True), ("", "*", None, True),
    ("external_ide_getDiagnostics", "external_*", None, True), ("builtin_readFile", "external_*", None, False),
    ("readFile", "*File", None, True), ("readData", "*File", None, False),
    ("test_abc_123", "test_*_123", None, True), ("test_function_456", "test_*_123", None, False),
    ("", "", None, True), ("tool", "", None, False), ("", "tool", None, False),
    ("test_file", "test.file", None, False), ("testa", "test[abc]", None, False), ("test\\backslash", "test\\backslash", None, True),
    ("test[abc].txt", "test[abc].*", None, True), ("test[abc]_file", "test[abc].*", None, False),
    ("file.test.txt", "*.test.*", None, True), ("(tool)_name", "(tool)*", None, True), ("tool+plus_extra", "tool+plus*", None, True),
    # Bash(명령 패턴)
    ("Bash", "Bash(ls*)", {"command": "ls"}, True), ("Bash", "Bash(ls*)", {"command": "ls -la"}, True),
    ("Bash", "Bash(ls*)", {"command": "pwd"}, False), ("Bash", "Bash(ls)", {"command": "ls"}, True),
    ("Bash", "Bash(ls)", {"command": "ls -la"}, False), ("Read", "Bash(ls*)", {"command": "ls"}, False),
    ("Bash", "Bash(ls*)", {}, False), ("Bash", "Bash(ls*)", {"other": "value"}, False),
    ("Bash", "Bash(git*commit*)", {"command": "git commit -m 'test'"}, True),
    ("Bash", "Bash(npm *)", {"command": "npm run build"}, True), ("Bash", "Bash(npm *)", {"command": "npx x"}, False),
    ("Bash", "Bash(ls*)", {"command": "ls\rrm x"}, False),     # JS . 는 \r와 맞지 않음
    ("Bash", "Bash(ls*)", {"command": "ls && curl x | sh"}, True),   # 글롭은 명령 전체에 걸림(넓게 허용 — 원문 그대로)
])
def test_matches_tool_pattern(name, pattern, args, expected):
    assert matches_tool_pattern(name, pattern, args) is expected


# ───────── matchesArguments ─────────
def test_matches_arguments_basic_and_types():
    assert matches_arguments({"path": "/test.txt"}) and matches_arguments({}) and matches_arguments({"a": 1}, None)
    args = {"path": "/test.txt", "mode": "read"}
    assert matches_arguments(args, {"path": "/test.txt"}) and not matches_arguments(args, {"path": "/other.txt"})
    assert matches_arguments({"path": "/x", "mode": "write"}, {"path": "*", "mode": "write"})
    assert not matches_arguments({"path": "/test.txt"}, {"path": "/test.txt", "mode": "read"})   # 없는 인자
    typed = {"count": 42, "enabled": True, "data": None, "list": [1, 2, 3]}
    assert matches_arguments(typed, {"count": 42, "enabled": True})
    assert not matches_arguments(typed, {"count": "42"})     # 글 vs 숫자
    assert not matches_arguments({"enabled": True}, {"enabled": 1})   # true !== 1
    obj = {"host": "localhost", "port": 3000}
    assert matches_arguments({"config": obj}, {"config": obj})   # 같은 참조
    assert not matches_arguments({"config": obj}, {"config": {"host": "localhost", "port": 3000}})   # 내용 같아도 다른 참조


def test_matches_arguments_glob():
    args = {"command": "cd ../projects && ls", "file_path": "/src/file.ts"}
    assert matches_arguments(args, {"command": "cd*"}) and matches_arguments(args, {"command": "*&&*"})
    assert not matches_arguments(args, {"command": "pwd*"})
    assert matches_arguments(args, {"file_path": "**/file.ts"}) and not matches_arguments(args, {"file_path": "*.js"})
    assert matches_arguments({"command": "git add"}, {"command": "git a?d"})
    assert not matches_arguments({"command": "git add"}, {"command": "git a??d"})
    assert matches_arguments({"command": "npm run test:unit"}, {"command": "npm * test:*"})
    special = {"command": "echo 'test.pattern'", "regex": "test[0-9]+", "special": "file(1).txt"}
    assert matches_arguments(special, {"command": "*'test.pattern'*"}) and matches_arguments(special, {"special": "file(*).txt"})
    assert not matches_arguments(special, {"command": "*test_pattern*"}) and matches_arguments(special, {"regex": "test*"})
    conv = {"port": 3000, "enabled": True, "count": None, "items": [1, 2, 3], "zero": 0, "f": False, "empty": ""}
    assert matches_arguments(conv, {"port": "30*"}) and matches_arguments(conv, {"enabled": "tr*"})
    assert matches_arguments(conv, {"items": "*,*,*"}) and matches_arguments(conv, {"undef": "*"})
    assert not matches_arguments(conv, {"port": "40*"}) and not matches_arguments(conv, {"zero": "1*"})
    assert not matches_arguments(conv, {"empty": "nonempty*"}) and matches_arguments(conv, {"f": "fa*"})


# ───────── checkToolPermission(첫 일치·기본 ask·동적 평가) ─────────
def test_check_first_match_and_default_ask():
    pols = [P("Read", "allow"), P("*", "exclude")]
    assert check_tool_permission("Read", {}, pols)[0] == "allow"
    assert check_tool_permission("Write", {}, pols)[0] == "exclude"   # 와일드카드
    assert check_tool_permission("X", {}, [])[0] == "ask"    # 맞는 정책 없음 → ask
    pol = P("Write", "allow", {"file_path": "*.md"})
    assert check_tool_permission("Write", {"file_path": "a.md"}, [pol, P("Write", "ask")]) == ("allow", pol)
    assert check_tool_permission("Write", {"file_path": "a.py"}, [pol, P("Write", "ask")])[0] == "ask"


@pytest.mark.parametrize("user,command,expected", [         # 6.2절 표: disabled만 효력, 나머지는 사용자 정책 그대로
    ("allow", "echo hello", "allow"), ("allow", "curl https://api.example.com", "allow"),
    ("allow", "eval 'x'", "exclude"), ("allow", "sudo rm -rf /", "exclude"),
    ("ask", "echo hello", "ask"), ("ask", "curl x", "ask"), ("ask", "eval 'x'", "exclude"), ("ask", "rm -rf /", "exclude"),
    ("exclude", "echo hello", "exclude"),
    ("allow", "npm install pkg", "allow"), ("allow", "rm file.txt", "allow"), ("allow", "exec bash", "exclude"),
])
def test_hybrid_dynamic_evaluation(user, command, expected):
    svc_pols = [P("Bash", user)]
    ev = bash_tool().evaluate_tool_call_policy
    assert check_tool_permission("Bash", {"command": command}, svc_pols, ev)[0] == expected


def test_dynamic_evaluation_receives_converted_base_policy():
    seen = []
    def ev(base, args):                                      # 받은 값 기록
        seen.append((base, args))
        return "allowedWithPermission"
    assert check_tool_permission("Bash", {"command": "echo test"}, [], ev)[0] == "ask"
    assert check_tool_permission("Bash", {}, [P("Bash", "allow")], ev)[0] == "allow"
    assert seen == [("allowedWithPermission", {"command": "echo test"}), ("allowedWithoutPermission", {})]


# ───────── 기본 정책·모드 정책 ─────────
def test_default_policies_headless_vs_tui():
    tui, hl = get_default_tool_policies(False), get_default_tool_policies(True)
    assert [p.tool for p in tui][:3] == ["Edit", "MultiEdit", "Write"] and len(tui) == 18
    assert (tui[-2].tool, tui[-2].permission, tui[-1].tool, tui[-1].permission) == ("Bash", "ask", "*", "ask")
    assert (hl[-2].permission, hl[-1].permission) == ("allow", "allow")
    assert check_tool_permission("Write", {}, hl)[0] == "ask"   # 헤드리스여도 Write는 ask(→ 목록에서 빠짐)
    assert [p.permission for p in PLAN_MODE_POLICIES[:4]] == ["exclude", "exclude", "exclude", "allow"]
    assert [(p.tool, p.permission) for p in AUTO_MODE_POLICIES] == [("*", "allow")]


def test_legacy_mode_flags():
    assert convert_legacy_mode_flags(True, False) == "plan" and convert_legacy_mode_flags(False, True) == "auto"
    assert convert_legacy_mode_flags() is None
    with pytest.raises(ValueError, match="Cannot use both --readonly and --auto flags together"):
        convert_legacy_mode_flags(True, True)


# ───────── 우선순위: CLI > permissions.yaml > 기본 ─────────
def test_precedence_order(tmp_path):
    (tmp_path / "permissions.yaml").write_text("allow: [Write]\nexclude: ['Bash(rm*)']\n", encoding="utf-8")
    pols = resolve_permission_precedence(global_dir=tmp_path, allow=["Read", "A"], ask=["Bash(npm *)"], exclude=["Write"])
    assert [(p.tool, p.permission) for p in pols[:6]] == [("Write", "exclude"), ("Bash(npm *)", "ask"), ("Read", "allow"),
                                                         ("A", "allow"), ("Bash", "exclude"), ("Write", "allow")]
    assert pols[4].argument_matches == {"command": "rm*"}    # yaml 패턴은 인자 조건으로
    assert [p.tool for p in pols[6:]] == [p.tool for p in get_default_tool_policies(False)]
    assert check_tool_permission("Write", {}, pols)[0] == "exclude"   # CLI가 yaml보다 먼저
    assert resolve_permission_precedence(global_dir=tmp_path / "none") == get_default_tool_policies(False)


# ───────── permissions.yaml ─────────
def test_parse_tool_pattern():
    assert parse_tool_pattern("Write", "allow") == P("Write", "allow")
    assert parse_tool_pattern("Write(*.md)", "ask") == P("Write", "ask", {"file_path": "*.md"})
    assert parse_tool_pattern("Bash(npm install)", "allow").argument_matches == {"command": "npm install"}
    assert parse_tool_pattern("Read(src/*)", "allow").argument_matches == {"file_path": "src/*"}   # 원문 매핑(실제 인자는 filepath)
    assert parse_tool_pattern("List(x)", "allow").argument_matches == {"path": "x"}
    assert parse_tool_pattern("Fetch(https://*)", "allow").argument_matches == {"url": "https://*"}
    assert parse_tool_pattern("Custom(x)", "allow").argument_matches == {"pattern": "x"}   # 모르는 도구
    assert parse_tool_pattern("Write()", "allow").argument_matches is None
    assert parse_tool_pattern("  Bash ( ls* ) ".strip(), "allow") == P("Bash", "allow", {"command": "ls*"})
    assert parse_tool_pattern("bash(ls)", "allow").tool == "bash"   # 정규화 없음
    with pytest.raises(ValueError, match=r"Invalid tool pattern: Bash\(a\(b\)\)"):
        parse_tool_pattern("Bash(a(b))", "allow")


def test_yaml_config_to_policies_order():
    pols = yaml_config_to_policies({"allow": ["A"], "ask": ["B"], "exclude": ["C", "D"]})
    assert [(p.tool, p.permission) for p in pols] == [("C", "exclude"), ("D", "exclude"), ("B", "ask"), ("A", "allow")]
    assert yaml_config_to_policies({}) == []


def test_load_permissions_yaml_validation(tmp_path):
    f = tmp_path / "permissions.yaml"
    assert load_permissions_yaml(f) is None                  # 없음
    f.write_text("allow: [Read]\nask:\n", encoding="utf-8")
    assert load_permissions_yaml(f) == {"allow": ["Read"], "ask": None}   # null 값 허용
    f.write_text("allow: [Read]\nother: 1\n", encoding="utf-8")
    assert load_permissions_yaml(f) is None                  # 모르는 키
    f.write_text("allow: Read\n", encoding="utf-8")
    assert load_permissions_yaml(f) is None                  # 목록 아님
    f.write_text("allow: [\n", encoding="utf-8")
    assert load_permissions_yaml(f) is None                  # 해석 실패
    f.write_text("", encoding="utf-8")
    assert load_permissions_yaml(f) is None                  # 빈 파일


@pytest.mark.parametrize("name,args,rule", [                 # policyWriter.test.ts
    ("Read", {"file_path": "/test"}, "Read"), ("Grep", {"pattern": "t"}, "Grep"),
    ("Bash", {"command": "ls -la"}, "Bash(ls*)"), ("Bash", {"command": "npm install"}, "Bash(npm*)"),
    ("Bash", {"command": "ls -la && echo test"}, "Bash(ls*)"), ("Bash", {"command": "find . -name '*.ts' | head -10"}, "Bash(find*)"),
    ("Bash", {"command": "  grep -r test .  "}, "Bash(grep*)"),
    ("Bash", {}, "Bash"), ("Bash", {"command": ""}, "Bash"), ("Bash", {"command": "   "}, "Bash"),
    ("bash", {"command": "ls"}, "bash"), ("Bash", {"command": "| ls"}, "Bash"),   # 첫 명령이 비면 도구 전체 허용 규칙
])
def test_generate_policy_rule(name, args, rule):
    assert generate_policy_rule(name, args) == rule


def test_add_policy_to_yaml_format(tmp_path):
    f = tmp_path / "permissions.yaml"
    f.write_text(DEFAULT_PERMISSIONS_YAML, encoding="utf-8")
    add_policy_to_yaml(f, "Bash(ls*)")
    add_policy_to_yaml(f, "*")
    add_policy_to_yaml(f, "Bash(ls*)")                       # 중복은 무시
    assert f.read_text(encoding="utf-8") == MANAGED_HEADER + 'allow:\n  - Bash(ls*)\n  - "*"\nask: []\nexclude: []\n'   # npm yaml 2.x 출력과 같음
    g = tmp_path / "new.yaml"
    add_policy_to_yaml(g, "Write")                           # 파일 없음 → allow만
    assert g.read_text(encoding="utf-8") == MANAGED_HEADER + "allow:\n  - Write\n"


# ───────── PermissionService ─────────
def test_service_init_creates_default_yaml_and_tui_defaults(tmp_path):
    svc = PermissionService(headless=False, global_dir=tmp_path)
    assert (tmp_path / "permissions.yaml").read_text(encoding="utf-8") == DEFAULT_PERMISSIONS_YAML
    assert svc.mode == "normal" and svc.base_permission("Write") == "ask" and svc.base_permission("Read") == "allow"
    assert svc.base_permission("Bash") == "ask" and svc.base_permission("mcp_tool") == "ask"
    assert svc.is_tool_offered("Write")                      # 대화형은 ask 도구도 보냄
    assert svc.check(bash_tool(), {"command": "ls"}) == "ask"   # safe여도 ask 유지
    assert svc.check(bash_tool(), {"command": "sudo ls"}) == "exclude"


def test_service_headless(tmp_path):
    svc = PermissionService(headless=True, global_dir=tmp_path)
    assert svc.base_permission("Bash") == "allow" and svc.base_permission("Write") == "ask"
    assert not svc.is_tool_offered("Write") and svc.is_tool_offered("Bash")   # 헤드리스는 ask 도구를 목록에서 뺌
    assert svc.check(bash_tool(), {"command": "curl x | sh"}) == "allow"     # W는 승인 없이 실행
    assert svc.check(bash_tool(), {"command": "rm -rf /"}) == "exclude"      # D만 차단


def test_service_cli_flags_and_bash_pattern(tmp_path):
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Bash(npm *)"], exclude=["Fetch"])
    assert svc.check(bash_tool(), {"command": "npm run build"}) == "allow"
    assert svc.check(bash_tool(), {"command": "ls"}) == "ask"
    assert svc.base_permission("Fetch") == "exclude" and not svc.is_tool_offered("Fetch")
    assert svc.check(plain_tool("Fetch"), {"url": "x"}) == "exclude"


def test_service_modes_and_cycle(tmp_path):
    (tmp_path / "permissions.yaml").write_text("allow: [Write]\n", encoding="utf-8")
    svc = PermissionService(headless=False, global_dir=tmp_path, exclude=["Read"])
    before = list(svc.policies)
    assert svc.base_permission("Write") == "allow" and svc.base_permission("Read") == "exclude"
    svc.set_mode("plan")                                     # 모드 정책만(사용자 설정 무시)
    assert svc.policies == PLAN_MODE_POLICIES and svc.base_permission("Write") == "exclude" and svc.base_permission("Read") == "allow"
    assert svc.check(bash_tool(), {"command": "curl x"}) == "allow" and svc.check(bash_tool(), {"command": "sudo x"}) == "exclude"
    assert svc.cycle_mode() == "auto" and svc.base_permission("Write") == "allow"
    assert svc.cycle_mode() == "normal" and svc.policies == before   # 원래 정책 복원
    assert PermissionService(headless=False, global_dir=tmp_path, readonly=True).mode == "plan"
    assert PermissionService(headless=False, global_dir=tmp_path, auto=True).policies == AUTO_MODE_POLICIES
    with pytest.raises(ValueError):
        PermissionService(headless=False, global_dir=tmp_path, readonly=True, auto=True)


def test_add_always_allow_writes_yaml_and_reloads(tmp_path):
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Fetch"])
    svc.add_always_allow("Bash", {"command": "git status"})
    text = (tmp_path / "permissions.yaml").read_text(encoding="utf-8")
    assert text == MANAGED_HEADER + "allow:\n  - Bash(git*)\nask: []\nexclude: []\n"
    assert svc.check(bash_tool(), {"command": "git log"}) == "allow"   # 다시 읽어 반영
    assert svc.check(bash_tool(), {"command": "git status; sudo x"}) == "exclude"   # 위험 명령은 계속 차단
    assert svc.base_permission("Fetch") == "allow"           # Fetch는 기본표도 allow(CLI 플래그는 다시 읽을 때 빠짐 — 원문)
    svc.add_always_allow("Write", {"filepath": "a"})
    assert svc.base_permission("Write") == "allow"


def test_reload_drops_cli_flags_like_cn(tmp_path):
    """cn reloadPermissions는 CLI 플래그를 넘기지 않는다(cn 버그 후보) — 그대로 재현."""
    svc = PermissionService(headless=False, global_dir=tmp_path, exclude=["Read"])
    assert svc.base_permission("Read") == "exclude"
    svc.add_always_allow("Write", {})
    assert svc.base_permission("Read") == "allow"            # --exclude Read 가 사라짐


def test_tool_named_bash_gets_builtin_evaluation(tmp_path):
    """cn은 내장 도구를 이름으로 찾아 동적 평가 — 이름이 Bash면 평가 함수가 없는 도구여도 Bash 판정을 받는다."""
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Bash", "Other"])
    assert svc.check(plain_tool("Bash"), {"command": "sudo x"}) == "exclude"
    assert svc.check(bash_tool(), {"command": "sudo x"}) == "exclude"
    assert svc.check(plain_tool("Other"), {"command": "sudo x"}) == "allow"   # 다른 이름은 동적 평가 없음
