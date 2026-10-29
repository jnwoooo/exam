# ncn/permissions/matcher.py — 정책 일치 규칙과 판정. cn extensions/cli/src/permissions/permissionChecker.ts 를 그대로 옮긴 것
# 규칙: 정책을 순서대로 보고 처음 맞는 것(도구 패턴 + 인자 조건)을 쓴다. 맞는 것이 없으면 "ask"
# 동적 평가(evaluateToolCallPolicy, Bash의 위험 명령 판정)는 "disabled"일 때만 효력: exclude로 바꾼다. 그 밖에는 사용자 정책 그대로
from __future__ import annotations                           # 타입 주석 지연 평가

import math                                                  # NaN·무한대 판별(JS String 변환)
import re                                                    # 글롭 → 정규식
from typing import Any, Callable, Dict, List, Optional, Tuple   # 타입

from ncn.permissions.policies import ALLOW, ASK, EXCLUDE, ToolPermissionPolicy   # 정책 형식

_REGEX_META = re.compile(r"[.+^${}()|[\]\\]")                # 원문 /[.+^${}()|[\]\\]/g — * ? 는 이스케이프하지 않음
_JS_DOT = "[^\n\r\u2028\u2029]"                              # JS 정규식 . (줄 끝 문자 4개 제외 — Python . 는 \r를 포함하므로 명시)
_BASH_PATTERN = re.compile(r"Bash\((.+)\)")                  # /^Bash\((.+)\)$/ — fullmatch로 사용


def js_truthy(v: Any) -> bool:
    """JS 참/거짓 판정 — 빈 목록·빈 dict도 참(Python과 다름)."""
    if v is None or v is False:                              # null/undefined/false
        return False
    if isinstance(v, bool):                                  # true
        return True
    if isinstance(v, (int, float)):                          # 0, NaN 은 거짓
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))
    if isinstance(v, str):                                   # "" 은 거짓
        return v != ""
    return True                                              # 객체·배열은 참


def js_string(v: Any) -> str:
    """JS String(v) — 패턴 비교 전에 인자 값을 글로 바꾼다(배열은 쉼표 연결, 객체는 [object Object])."""
    if v is None:                                            # null → "null"(호출부에서 ?? "" 처리)
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):                                   # 정수
        return str(v)
    if isinstance(v, float):                                 # 실수
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v == int(v) and abs(v) < 1e21:                    # 3.0 → "3"
            return str(int(v))
        return repr(v)                                       # 그 밖(지수 표기 등 드문 차이 있음)
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):                         # Array.prototype.toString — null/undefined 원소는 ""
        return ",".join("" if x is None else js_string(x) for x in v)
    return "[object Object]"                                 # 일반 객체


def _js_strict_equal(a: Any, b: Any) -> bool:
    """JS === — 원시값은 타입까지 같아야 하고, 객체·배열은 같은 참조일 때만."""
    if isinstance(a, (dict, list, tuple)) or isinstance(b, (dict, list, tuple)):   # 참조 비교
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):           # true !== 1
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):   # 숫자끼리(NaN !== NaN 은 == 와 같음)
        return a == b
    return type(a) is type(b) and a == b                     # 글 등


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """원문 변환: 정규식 특수 문자 이스케이프 → * 는 .*, ? 는 . → ^...$ (JS . 의미 유지)."""
    escaped = _REGEX_META.sub(lambda m: "\\" + m.group(0), pattern)   # 메타 문자 이스케이프
    regex_pattern = escaped.replace("*", _JS_DOT + "*").replace("?", _JS_DOT)   # * ? 변환(이스케이프된 글에는 * ? 가 원래 그대로 있음)
    return re.compile(regex_pattern)                         # fullmatch로 사용(^...$)


def matches_tool_pattern(tool_name: str, pattern: str, tool_arguments: Optional[Dict[str, Any]] = None) -> bool:
    """matchesToolPattern — "*", 정확한 이름, "Bash(ls*)" 명령 패턴, "external_*" 글롭."""
    if pattern == "*":                                       # 전부
        return True
    if pattern == tool_name:                                 # 정확히 같음
        return True
    bash_match = _BASH_PATTERN.fullmatch(pattern)            # Bash(명령 패턴)
    if bash_match:
        is_bash_tool = tool_name == "Bash"                   # Bash 도구인지
        command = tool_arguments.get("command") if isinstance(tool_arguments, dict) else None   # toolArguments?.command
        if is_bash_tool and js_truthy(command):              # 명령이 있을 때만
            command_pattern = bash_match.group(1)            # 괄호 안
            if "*" in command_pattern or "?" in command_pattern:   # 글롭
                return glob_to_regex(command_pattern).fullmatch(js_string(command)) is not None   # regex.test(command)
            return _js_strict_equal(command, command_pattern)   # 정확히 같음
        return False                                         # Bash 패턴인데 Bash가 아니거나 명령 없음
    if "*" in pattern or "?" in pattern:                     # 일반 글롭(external_* 등)
        return glob_to_regex(pattern).fullmatch(tool_name) is not None
    return False


def matches_arguments(args: Optional[Dict[str, Any]], patterns: Optional[Dict[str, Any]] = None) -> bool:
    """matchesArguments — 모든 조건이 맞아야 참. "*"는 아무 값, 글롭은 글로 바꿔 비교, 그 밖은 === 비교."""
    if not patterns:                                         # 조건 없음(undefined)
        return True
    args = args if isinstance(args, dict) else {}            # 인자 없음 방어
    for key, pattern in patterns.items():                    # 조건마다
        arg_value = args.get(key)                            # 없으면 undefined
        if pattern == "*" and isinstance(pattern, str):      # 와일드카드
            continue
        if isinstance(pattern, str) and ("*" in pattern or "?" in pattern):   # 글롭(문자열 패턴만)
            string_value = "" if arg_value is None else js_string(arg_value)   # String(argValue ?? "")
            if glob_to_regex(pattern).fullmatch(string_value) is None:
                return False
        else:                                                # 정확히 같아야 함
            if not _js_strict_equal(arg_value, pattern):
                return False
    return True


def permission_policy_to_tool_policy(permission: str) -> str:
    """permissionPolicyToToolPolicy — CLI 권한 → terminal-security ToolPolicy."""
    if permission == ALLOW:
        return "allowedWithoutPermission"
    if permission == ASK:
        return "allowedWithPermission"
    if permission == EXCLUDE:
        return "disabled"
    return "allowedWithPermission"                           # 기본


def find_matching_policy(tool_name: str, args: Optional[Dict[str, Any]],
                         policies: List[ToolPermissionPolicy]) -> Tuple[str, Optional[ToolPermissionPolicy]]:
    """정적 정책만으로 판정 — 처음 맞는 정책의 권한(없으면 "ask")과 그 정책."""
    for policy in policies:                                  # 순서대로
        if matches_tool_pattern(tool_name, policy.tool, args) and matches_arguments(args or {}, policy.argument_matches):
            return policy.permission, policy                 # 첫 일치
    return ASK, None                                         # 기본 ask


def check_tool_permission(tool_name: str, args: Optional[Dict[str, Any]], policies: List[ToolPermissionPolicy],
                          evaluate_tool_call_policy: Optional[Callable[[str, Dict[str, Any]], str]] = None,
                          ) -> Tuple[str, Optional[ToolPermissionPolicy]]:
    """checkToolPermission — (permission, matchedPolicy).
    evaluate_tool_call_policy: cn은 ALL_BUILT_IN_TOOLS에서 같은 이름 도구를 찾아 그 함수를 쓴다(호출부가 넘김)."""
    args = args if args is not None else {}                  # 인자 없음 → {}
    base_permission, matched = find_matching_policy(tool_name, args, policies)   # 1) 정적 정책
    if evaluate_tool_call_policy is not None:                # 2) 동적 평가가 있는 도구(Bash)
        base_policy = permission_policy_to_tool_policy(base_permission)   # CLI 권한 → ToolPolicy
        evaluated = evaluate_tool_call_policy(base_policy, args)   # 평가
        if evaluated == "disabled":                          # 위험 명령은 사용자 설정과 무관하게 항상 차단
            return EXCLUDE, matched
        return base_permission, matched                      # 그 밖에는 사용자 정책 그대로(ask로 올리지 않음)
    return base_permission, matched                          # 동적 평가 없음
