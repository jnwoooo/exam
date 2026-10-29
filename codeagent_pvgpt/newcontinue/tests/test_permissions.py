# tests/test_permissions.py — 권한 정책·일치 규칙·우선순위·permissions.yaml·서비스 시험
# 근거: cn extensions/cli/src/permissions/*.test.ts(permissionChecker·precedenceResolver·permissionsYamlLoader·policyWriter·defaultPolicies)
#       services/ToolPermissionService.modes.test.ts, P0/소스확인_terminal_security.md 6.2절 표
import pytest                                                # 시험 도구

from ncn.permissions.matcher import check_tool_permission, matches_arguments, matches_tool_pattern   # 일치 규칙
from ncn.permissions.policies import (AUTO_MODE_POLICIES, PLAN_MODE_POLICIES, ToolPermissionPolicy as P,  # 정책 형식·기본표·모드표 가져오기
                                      convert_legacy_mode_flags, get_default_tool_policies)   # 정책표
from ncn.permissions.service import PermissionService, resolve_permission_precedence   # 서비스
from ncn.permissions.yaml_loader import (DEFAULT_PERMISSIONS_YAML, MANAGED_HEADER, add_policy_to_yaml,  # yaml 읽기·쓰기 함수 가져오기
                                         generate_policy_rule, load_permissions_yaml, parse_tool_pattern,  # 규칙 생성·yaml 읽기·패턴 해석 함수
                                         yaml_config_to_policies)   # 파일
from ncn.security.terminal_security import evaluate_terminal_command_security   # Bash 동적 평가
from ncn.tools.base import Tool                              # 공용 도구 형식


async def _run(args, ctx):                                   # 시험용 실행 함수(쓰지 않음)
    return ""                                               # 아무것도 안 함


def bash_tool(**kw) -> Tool:                                # 동적 평가가 연결된 Bash 도구 만들기
    """runTerminalCommand.ts 와 같은 동적 평가 연결: evaluateTerminalCommandSecurity(base, parsedArgs.command)."""
    return Tool(name="Bash", display_name="Bash", description="", parameters={}, run=_run,  # Tool 생성 — 이름·표시 이름·빈 설명
                evaluate_tool_call_policy=lambda base, args: evaluate_terminal_command_security(base, args.get("command")), **kw)  # 위험 명령 보안 판정을 동적 평가로 연결


def plain_tool(name: str) -> Tool:                          # 동적 평가 없는 일반 도구 만들기
    """동적 평가가 없는 도구."""
    return Tool(name=name, display_name=name, description="", parameters={}, run=_run)  # 평가 함수 없이 Tool 생성


# ───────── matchesToolPattern ─────────
@pytest.mark.parametrize("name,pattern,args,expected", [    # 도구 이름 패턴 일치 표 시험
    ("readFile", "readFile", None, True), ("writeFile", "readFile", None, False),  # 정확한 이름 일치·불일치
    ("anyTool", "*", None, True), ("", "*", None, True),    # * 와일드카드
    ("external_ide_getDiagnostics", "external_*", None, True), ("builtin_readFile", "external_*", None, False),  # external_* 글롭
    ("readFile", "*File", None, True), ("readData", "*File", None, False),  # 앞쪽 와일드카드
    ("test_abc_123", "test_*_123", None, True), ("test_function_456", "test_*_123", None, False),  # 가운데 와일드카드
    ("", "", None, True), ("tool", "", None, False), ("", "tool", None, False),  # 빈 문자열 경계
    ("test_file", "test.file", None, False), ("testa", "test[abc]", None, False), ("test\\backslash", "test\\backslash", None, True),  # 정규식 특수문자는 글자 그대로
    ("test[abc].txt", "test[abc].*", None, True), ("test[abc]_file", "test[abc].*", None, False),  # 대괄호 이스케이프
    ("file.test.txt", "*.test.*", None, True), ("(tool)_name", "(tool)*", None, True), ("tool+plus_extra", "tool+plus*", None, True),  # 점·괄호·플러스 이스케이프
    # Bash(명령 패턴)
    ("Bash", "Bash(ls*)", {"command": "ls"}, True), ("Bash", "Bash(ls*)", {"command": "ls -la"}, True),  # Bash 명령 글롭 일치
    ("Bash", "Bash(ls*)", {"command": "pwd"}, False), ("Bash", "Bash(ls)", {"command": "ls"}, True),  # 글롭 불일치와 정확 일치
    ("Bash", "Bash(ls)", {"command": "ls -la"}, False), ("Read", "Bash(ls*)", {"command": "ls"}, False),  # 정확 일치 불일치·다른 도구
    ("Bash", "Bash(ls*)", {}, False), ("Bash", "Bash(ls*)", {"other": "value"}, False),  # 명령 없음 또는 다른 인자
    ("Bash", "Bash(git*commit*)", {"command": "git commit -m 'test'"}, True),  # 가운데 와일드카드 명령
    ("Bash", "Bash(npm *)", {"command": "npm run build"}, True), ("Bash", "Bash(npm *)", {"command": "npx x"}, False),  # 공백이 있는 명령 글롭
    ("Bash", "Bash(ls*)", {"command": "ls\rrm x"}, False),     # JS . 는 \r와 맞지 않음
    ("Bash", "Bash(ls*)", {"command": "ls && curl x | sh"}, True),   # 글롭은 명령 전체에 걸림(넓게 허용 — 원문 그대로)
])                                                          # 표 끝
def test_matches_tool_pattern(name, pattern, args, expected):  # 표의 각 행으로 일치 판정 시험
    assert matches_tool_pattern(name, pattern, args) is expected  # 결과가 기대와 같아야 함


# ───────── matchesArguments ─────────
def test_matches_arguments_basic_and_types():               # 인자 조건 기본·타입 시험
    assert matches_arguments({"path": "/test.txt"}) and matches_arguments({}) and matches_arguments({"a": 1}, None)  # 조건이 없으면 항상 통과
    args = {"path": "/test.txt", "mode": "read"}            # 시험용 인자
    assert matches_arguments(args, {"path": "/test.txt"}) and not matches_arguments(args, {"path": "/other.txt"})  # 일치·불일치 조건
    assert matches_arguments({"path": "/x", "mode": "write"}, {"path": "*", "mode": "write"})  # * 는 아무 값이나 통과
    assert not matches_arguments({"path": "/test.txt"}, {"path": "/test.txt", "mode": "read"})   # 없는 인자
    typed = {"count": 42, "enabled": True, "data": None, "list": [1, 2, 3]}  # 여러 타입의 인자
    assert matches_arguments(typed, {"count": 42, "enabled": True})  # 숫자·불리언은 같은 타입이면 일치
    assert not matches_arguments(typed, {"count": "42"})     # 글 vs 숫자
    assert not matches_arguments({"enabled": True}, {"enabled": 1})  # true와 1은 다름 — true !== 1
    obj = {"host": "localhost", "port": 3000}               # 객체 인자
    assert matches_arguments({"config": obj}, {"config": obj})   # 같은 참조
    assert not matches_arguments({"config": obj}, {"config": {"host": "localhost", "port": 3000}})   # 내용 같아도 다른 참조


def test_matches_arguments_glob():                          # 인자 글롭 시험
    args = {"command": "cd ../projects && ls", "file_path": "/src/file.ts"}  # 명령·파일 경로 인자
    assert matches_arguments(args, {"command": "cd*"}) and matches_arguments(args, {"command": "*&&*"})  # 앞 글자와 && 글롭
    assert not matches_arguments(args, {"command": "pwd*"})  # 앞 글자 불일치
    assert matches_arguments(args, {"file_path": "**/file.ts"}) and not matches_arguments(args, {"file_path": "*.js"})  # 경로 글롭 일치·불일치
    assert matches_arguments({"command": "git add"}, {"command": "git a?d"})  # ? 는 한 글자
    assert not matches_arguments({"command": "git add"}, {"command": "git a??d"})  # ? 두 개는 불일치
    assert matches_arguments({"command": "npm run test:unit"}, {"command": "npm * test:*"})  # 공백 포함 글롭
    special = {"command": "echo 'test.pattern'", "regex": "test[0-9]+", "special": "file(1).txt"}  # 특수문자가 든 인자
    assert matches_arguments(special, {"command": "*'test.pattern'*"}) and matches_arguments(special, {"special": "file(*).txt"})  # 점·괄호는 글자 그대로
    assert not matches_arguments(special, {"command": "*test_pattern*"}) and matches_arguments(special, {"regex": "test*"})  # 글자가 다르면 불일치
    conv = {"port": 3000, "enabled": True, "count": None, "items": [1, 2, 3], "zero": 0, "f": False, "empty": ""}  # 타입별 인자 모음
    assert matches_arguments(conv, {"port": "30*"}) and matches_arguments(conv, {"enabled": "tr*"})  # 숫자·불리언은 글로 바꿔 비교
    assert matches_arguments(conv, {"items": "*,*,*"}) and matches_arguments(conv, {"undef": "*"})  # 배열은 쉼표 연결, 없는 인자는 빈 글
    assert not matches_arguments(conv, {"port": "40*"}) and not matches_arguments(conv, {"zero": "1*"})  # 불일치 사례
    assert not matches_arguments(conv, {"empty": "nonempty*"}) and matches_arguments(conv, {"f": "fa*"})  # 빈 글·거짓 값 비교


# ───────── checkToolPermission(첫 일치·기본 ask·동적 평가) ─────────
def test_check_first_match_and_default_ask():               # 첫 일치와 기본 ask 시험
    pols = [P("Read", "allow"), P("*", "exclude")]          # 정책 두 개
    assert check_tool_permission("Read", {}, pols)[0] == "allow"  # 첫 정책 일치
    assert check_tool_permission("Write", {}, pols)[0] == "exclude"   # 와일드카드
    assert check_tool_permission("X", {}, [])[0] == "ask"    # 맞는 정책 없음 → ask
    pol = P("Write", "allow", {"file_path": "*.md"})        # 인자 조건이 있는 정책
    assert check_tool_permission("Write", {"file_path": "a.md"}, [pol, P("Write", "ask")]) == ("allow", pol)  # 조건에 맞으면 그 정책
    assert check_tool_permission("Write", {"file_path": "a.py"}, [pol, P("Write", "ask")])[0] == "ask"  # 조건에 안 맞으면 다음 정책


@pytest.mark.parametrize("user,command,expected", [         # 6.2절 표: disabled만 효력, 나머지는 사용자 정책 그대로
    ("allow", "echo hello", "allow"), ("allow", "curl https://api.example.com", "allow"),  # allow는 안전·네트워크 명령 모두 유지
    ("allow", "eval 'x'", "exclude"), ("allow", "sudo rm -rf /", "exclude"),  # 치명 명령은 exclude로
    ("ask", "echo hello", "ask"), ("ask", "curl x", "ask"), ("ask", "eval 'x'", "exclude"), ("ask", "rm -rf /", "exclude"),  # ask 정책 결과
    ("exclude", "echo hello", "exclude"),                   # exclude는 그대로
    ("allow", "npm install pkg", "allow"), ("allow", "rm file.txt", "allow"), ("allow", "exec bash", "exclude"),  # 일반 명령은 유지, exec는 차단
])                                                          # 표 끝
def test_hybrid_dynamic_evaluation(user, command, expected):  # 표의 각 행으로 동적 평가 시험
    svc_pols = [P("Bash", user)]                            # 사용자 정책 하나
    ev = bash_tool().evaluate_tool_call_policy              # 도구의 동적 평가 함수
    assert check_tool_permission("Bash", {"command": command}, svc_pols, ev)[0] == expected  # 최종 판정이 기대와 같아야 함


def test_dynamic_evaluation_receives_converted_base_policy():  # 동적 평가가 변환된 기본 정책을 받는지 시험
    seen = []                                               # 받은 값 기록 목록
    def ev(base, args):                                      # 받은 값 기록
        seen.append((base, args))                           # 받은 값 저장
        return "allowedWithPermission"                      # 허가 필요 정책 반환
    assert check_tool_permission("Bash", {"command": "echo test"}, [], ev)[0] == "ask"  # 정책 없음 → ask 기반
    assert check_tool_permission("Bash", {}, [P("Bash", "allow")], ev)[0] == "allow"  # allow 정책 → 자동 허용 기반
    assert seen == [("allowedWithPermission", {"command": "echo test"}), ("allowedWithoutPermission", {})]  # 변환된 정책 이름 확인


# ───────── 기본 정책·모드 정책 ─────────
def test_default_policies_headless_vs_tui():                # 헤드리스·TUI 기본 정책 비교
    tui, hl = get_default_tool_policies(False), get_default_tool_policies(True)  # 두 가지 기본표
    assert [p.tool for p in tui][:3] == ["Edit", "MultiEdit", "Write"] and len(tui) == 18  # 앞 세 개는 수정 도구, 총 18개
    assert (tui[-2].tool, tui[-2].permission, tui[-1].tool, tui[-1].permission) == ("Bash", "ask", "*", "ask")  # TUI 끝은 Bash·* 모두 ask
    assert (hl[-2].permission, hl[-1].permission) == ("allow", "allow")  # 헤드리스 끝은 모두 allow
    assert check_tool_permission("Write", {}, hl)[0] == "ask"   # 헤드리스여도 Write는 ask(→ 목록에서 빠짐)
    assert [p.permission for p in PLAN_MODE_POLICIES[:4]] == ["exclude", "exclude", "exclude", "allow"]  # plan 정책 앞부분
    assert [(p.tool, p.permission) for p in AUTO_MODE_POLICIES] == [("*", "allow")]  # auto 정책은 전부 허용


def test_legacy_mode_flags():                               # 구형 모드 플래그 시험
    assert convert_legacy_mode_flags(True, False) == "plan" and convert_legacy_mode_flags(False, True) == "auto"  # 읽기 전용은 plan, auto는 auto
    assert convert_legacy_mode_flags() is None              # 지정 없음은 None
    with pytest.raises(ValueError, match="Cannot use both --readonly and --auto flags together"):  # 둘 다 주면 오류
        convert_legacy_mode_flags(True, True)               # 오류 발생 호출


# ───────── 우선순위: CLI > permissions.yaml > 기본 ─────────
def test_precedence_order(tmp_path):                        # 우선순위 시험
    (tmp_path / "permissions.yaml").write_text("allow: [Write]\nexclude: ['Bash(rm*)']\n", encoding="utf-8", newline="")  # yaml 파일 준비
    pols = resolve_permission_precedence(global_dir=tmp_path, allow=["Read", "A"], ask=["Bash(npm *)"], exclude=["Write"])  # CLI 플래그와 yaml 병합
    assert [(p.tool, p.permission) for p in pols[:6]] == [("Write", "exclude"), ("Bash(npm *)", "ask"), ("Read", "allow"),  # 앞 여섯 개의 순서 확인
                                                         ("A", "allow"), ("Bash", "exclude"), ("Write", "allow")]  # CLI → yaml 순서
    assert pols[4].argument_matches == {"command": "rm*"}    # yaml 패턴은 인자 조건으로
    assert [p.tool for p in pols[6:]] == [p.tool for p in get_default_tool_policies(False)]  # 나머지는 기본표
    assert check_tool_permission("Write", {}, pols)[0] == "exclude"   # CLI가 yaml보다 먼저
    assert resolve_permission_precedence(global_dir=tmp_path / "none") == get_default_tool_policies(False)  # 파일 없으면 기본표


# ───────── permissions.yaml ─────────
def test_parse_tool_pattern():                              # 도구 패턴 해석 시험
    assert parse_tool_pattern("Write", "allow") == P("Write", "allow")  # 이름만 있는 패턴
    assert parse_tool_pattern("Write(*.md)", "ask") == P("Write", "ask", {"file_path": "*.md"})  # 괄호 안은 인자 조건
    assert parse_tool_pattern("Bash(npm install)", "allow").argument_matches == {"command": "npm install"}  # Bash는 command
    assert parse_tool_pattern("Read(src/*)", "allow").argument_matches == {"file_path": "src/*"}   # 원문 매핑(실제 인자는 filepath)
    assert parse_tool_pattern("List(x)", "allow").argument_matches == {"path": "x"}  # List는 path
    assert parse_tool_pattern("Fetch(https://*)", "allow").argument_matches == {"url": "https://*"}  # Fetch는 url
    assert parse_tool_pattern("Custom(x)", "allow").argument_matches == {"pattern": "x"}   # 모르는 도구
    assert parse_tool_pattern("Write()", "allow").argument_matches is None  # 빈 괄호는 조건 없음
    assert parse_tool_pattern("  Bash ( ls* ) ".strip(), "allow") == P("Bash", "allow", {"command": "ls*"})  # 공백 정리
    assert parse_tool_pattern("bash(ls)", "allow").tool == "bash"   # 정규화 없음
    with pytest.raises(ValueError, match=r"Invalid tool pattern: Bash\(a\(b\)\)"):  # 잘못된 패턴은 오류
        parse_tool_pattern("Bash(a(b))", "allow")           # 오류 발생 호출


def test_yaml_config_to_policies_order():                   # yaml 설정 → 정책 순서 시험
    pols = yaml_config_to_policies({"allow": ["A"], "ask": ["B"], "exclude": ["C", "D"]})  # allow·ask·exclude 변환
    assert [(p.tool, p.permission) for p in pols] == [("C", "exclude"), ("D", "exclude"), ("B", "ask"), ("A", "allow")]  # exclude → ask → allow 순서
    assert yaml_config_to_policies({}) == []                # 빈 설정은 빈 목록


def test_load_permissions_yaml_validation(tmp_path):        # yaml 읽기 검증 시험
    f = tmp_path / "permissions.yaml"                       # 임시 yaml 경로
    assert load_permissions_yaml(f) is None                  # 없음
    f.write_text("allow: [Read]\nask:\n", encoding="utf-8", newline="")  # null 값이 든 파일 쓰기
    assert load_permissions_yaml(f) == {"allow": ["Read"], "ask": None}   # null 값 허용
    f.write_text("allow: [Read]\nother: 1\n", encoding="utf-8", newline="")  # 모르는 키 파일 쓰기
    assert load_permissions_yaml(f) is None                  # 모르는 키
    f.write_text("allow: Read\n", encoding="utf-8", newline="")         # 목록이 아닌 값 쓰기
    assert load_permissions_yaml(f) is None                  # 목록 아님
    f.write_text("allow: [\n", encoding="utf-8", newline="")            # 잘못된 yaml 쓰기
    assert load_permissions_yaml(f) is None                  # 해석 실패
    f.write_text("", encoding="utf-8", newline="")                      # 빈 파일 쓰기
    assert load_permissions_yaml(f) is None                  # 빈 파일


@pytest.mark.parametrize("name,args,rule", [                # 규칙 생성 표 시험 — policyWriter.test.ts
    ("Read", {"file_path": "/test"}, "Read"), ("Grep", {"pattern": "t"}, "Grep"),  # Bash 외 도구는 이름 그대로
    ("Bash", {"command": "ls -la"}, "Bash(ls*)"), ("Bash", {"command": "npm install"}, "Bash(npm*)"),  # Bash는 첫 명령 이름
    ("Bash", {"command": "ls -la && echo test"}, "Bash(ls*)"), ("Bash", {"command": "find . -name '*.ts' | head -10"}, "Bash(find*)"),  # 연산자 앞 첫 명령 사용
    ("Bash", {"command": "  grep -r test .  "}, "Bash(grep*)"),  # 앞뒤 공백 무시
    ("Bash", {}, "Bash"), ("Bash", {"command": ""}, "Bash"), ("Bash", {"command": "   "}, "Bash"),  # 명령 없으면 도구 이름
    ("bash", {"command": "ls"}, "bash"), ("Bash", {"command": "| ls"}, "Bash"),   # 첫 명령이 비면 도구 전체 허용 규칙
])                                                          # 표 끝
def test_generate_policy_rule(name, args, rule, cn_compat_env):  # 표의 각 행으로 규칙 생성 시험(cn 기대값 — [ncn 부록B S1·S2] 고친 동작은 test_ncn_fixes)
    assert generate_policy_rule(name, args) == rule         # 생성된 규칙이 기대와 같아야 함


def test_add_policy_to_yaml_format(tmp_path):               # yaml 쓰기 형식 시험
    f = tmp_path / "permissions.yaml"                       # 임시 yaml 경로
    f.write_text(DEFAULT_PERMISSIONS_YAML, encoding="utf-8", newline="")  # 기본 내용 쓰기
    add_policy_to_yaml(f, "Bash(ls*)")                      # 규칙 추가
    add_policy_to_yaml(f, "*")                              # 와일드카드 규칙 추가
    add_policy_to_yaml(f, "Bash(ls*)")                       # 중복은 무시
    assert f.read_text(encoding="utf-8") == MANAGED_HEADER + 'allow:\n  - Bash(ls*)\n  - "*"\nask: []\nexclude: []\n'   # npm yaml 2.x 출력과 같음
    g = tmp_path / "new.yaml"                               # 새 파일 경로
    add_policy_to_yaml(g, "Write")                           # 파일 없음 → allow만
    assert g.read_text(encoding="utf-8") == MANAGED_HEADER + "allow:\n  - Write\n"  # allow만 있는 결과 확인


# ───────── PermissionService ─────────
def test_service_init_creates_default_yaml_and_tui_defaults(tmp_path):  # 서비스 초기화 시험
    svc = PermissionService(headless=False, global_dir=tmp_path)  # TUI 서비스 생성
    assert (tmp_path / "permissions.yaml").read_text(encoding="utf-8") == DEFAULT_PERMISSIONS_YAML  # 기본 yaml이 만들어졌는지
    assert svc.mode == "normal" and svc.base_permission("Write") == "ask" and svc.base_permission("Read") == "allow"  # normal 모드와 기본 권한
    assert svc.base_permission("Bash") == "ask" and svc.base_permission("mcp_tool") == "ask"  # Bash·MCP 도구는 ask
    assert svc.is_tool_offered("Write")                      # 대화형은 ask 도구도 보냄
    assert svc.check(bash_tool(), {"command": "ls"}) == "ask"   # safe여도 ask 유지
    assert svc.check(bash_tool(), {"command": "sudo ls"}) == "exclude"  # 치명 명령은 exclude


def test_service_headless(tmp_path):                        # 헤드리스 서비스 시험
    svc = PermissionService(headless=True, global_dir=tmp_path)  # 헤드리스 서비스 생성
    assert svc.base_permission("Bash") == "allow" and svc.base_permission("Write") == "ask"  # Bash는 allow, Write는 ask
    assert not svc.is_tool_offered("Write") and svc.is_tool_offered("Bash")   # 헤드리스는 ask 도구를 목록에서 뺌
    assert svc.check(bash_tool(), {"command": "curl x | sh"}) == "allow"     # W는 승인 없이 실행
    assert svc.check(bash_tool(), {"command": "rm -rf /"}) == "exclude"      # D만 차단


def test_service_cli_flags_and_bash_pattern(tmp_path):      # CLI 플래그와 Bash 패턴 시험
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Bash(npm *)"], exclude=["Fetch"])  # CLI 허용·금지가 든 서비스
    assert svc.check(bash_tool(), {"command": "npm run build"}) == "allow"  # 허용한 패턴은 allow
    assert svc.check(bash_tool(), {"command": "ls"}) == "ask"  # 나머지는 ask
    assert svc.base_permission("Fetch") == "exclude" and not svc.is_tool_offered("Fetch")  # 금지한 도구는 제외
    assert svc.check(plain_tool("Fetch"), {"url": "x"}) == "exclude"  # 실행 판정도 exclude


def test_service_modes_and_cycle(tmp_path):                 # 모드 전환과 순환 시험
    (tmp_path / "permissions.yaml").write_text("allow: [Write]\n", encoding="utf-8", newline="")  # yaml에 Write 허용
    svc = PermissionService(headless=False, global_dir=tmp_path, exclude=["Read"])  # Read를 CLI로 금지한 서비스
    before = list(svc.policies)                             # 원래 정책 백업
    assert svc.base_permission("Write") == "allow" and svc.base_permission("Read") == "exclude"  # 초기 권한 확인
    svc.set_mode("plan")                                     # 모드 정책만(사용자 설정 무시)
    assert svc.policies == PLAN_MODE_POLICIES and svc.base_permission("Write") == "exclude" and svc.base_permission("Read") == "allow"  # plan 정책 확인
    assert svc.check(bash_tool(), {"command": "curl x"}) == "allow" and svc.check(bash_tool(), {"command": "sudo x"}) == "exclude"  # plan에서도 치명 명령은 차단
    assert svc.cycle_mode() == "auto" and svc.base_permission("Write") == "allow"  # 다음 모드 auto
    assert svc.cycle_mode() == "normal" and svc.policies == before   # 원래 정책 복원
    assert PermissionService(headless=False, global_dir=tmp_path, readonly=True).mode == "plan"  # readonly는 plan
    assert PermissionService(headless=False, global_dir=tmp_path, auto=True).policies == AUTO_MODE_POLICIES  # auto는 auto 정책
    with pytest.raises(ValueError):                         # 둘 다 주면 오류
        PermissionService(headless=False, global_dir=tmp_path, readonly=True, auto=True)  # 오류 발생 호출


def test_add_always_allow_writes_yaml_and_reloads(tmp_path, cn_compat_env):  # 항상 허용 규칙 저장·재로드 시험(cn 동작 — [ncn 부록B S1])
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Fetch"])  # Fetch를 CLI로 허용한 서비스
    svc.add_always_allow("Bash", {"command": "git status"})  # 규칙 저장
    text = (tmp_path / "permissions.yaml").read_text(encoding="utf-8")  # 저장된 yaml 읽기
    assert text == MANAGED_HEADER + "allow:\n  - Bash(git*)\nask: []\nexclude: []\n"  # 기대한 yaml 형식
    assert svc.check(bash_tool(), {"command": "git log"}) == "allow"   # 다시 읽어 반영
    assert svc.check(bash_tool(), {"command": "git status; sudo x"}) == "exclude"   # 위험 명령은 계속 차단
    assert svc.base_permission("Fetch") == "allow"           # Fetch는 기본표도 allow(CLI 플래그는 다시 읽을 때 빠짐 — 원문)
    svc.add_always_allow("Write", {"filepath": "a"})        # Write 규칙 추가
    assert svc.base_permission("Write") == "allow"          # Write가 allow로 바뀜


def test_reload_drops_cli_flags_like_cn(tmp_path):          # 재로드 시 CLI 플래그가 사라지는 동작 시험
    """cn reloadPermissions는 CLI 플래그를 넘기지 않는다(cn 버그 후보) — 그대로 재현."""
    svc = PermissionService(headless=False, global_dir=tmp_path, exclude=["Read"])  # Read를 금지한 서비스
    assert svc.base_permission("Read") == "exclude"         # 처음엔 exclude
    svc.add_always_allow("Write", {})                       # 규칙 추가로 재로드 유발
    assert svc.base_permission("Read") == "allow"            # --exclude Read 가 사라짐


def test_tool_named_bash_gets_builtin_evaluation(tmp_path):  # 이름이 Bash면 내장 평가를 받는지 시험
    """cn은 내장 도구를 이름으로 찾아 동적 평가 — 이름이 Bash면 평가 함수가 없는 도구여도 Bash 판정을 받는다."""
    svc = PermissionService(headless=False, global_dir=tmp_path, allow=["Bash", "Other"])  # Bash·Other를 허용한 서비스
    assert svc.check(plain_tool("Bash"), {"command": "sudo x"}) == "exclude"  # 평가 함수 없는 Bash도 치명 명령 차단
    assert svc.check(bash_tool(), {"command": "sudo x"}) == "exclude"  # 일반 Bash 도구도 차단
    assert svc.check(plain_tool("Other"), {"command": "sudo x"}) == "allow"   # 다른 이름은 동적 평가 없음
