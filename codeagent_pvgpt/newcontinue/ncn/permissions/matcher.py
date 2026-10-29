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


def js_truthy(v: Any) -> bool:                              # JS 참/거짓 규칙을 흉내 내는 함수
    """JS 참/거짓 판정 — 빈 목록·빈 dict도 참(Python과 다름)."""
    if v is None or v is False:                              # null/undefined/false  # null·false는 거짓
        return False                                        # 거짓 반환
    if isinstance(v, bool):                                  # true  # true 불리언
        return True                                         # 참 반환
    if isinstance(v, (int, float)):                          # 0, NaN 은 거짓
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))  # 0과 NaN만 거짓
    if isinstance(v, str):                                   # "" 은 거짓
        return v != ""                                      # 빈 글만 거짓
    return True                                              # 객체·배열은 참


def js_string(v: Any) -> str:                               # JS String(v) 변환 함수
    """JS String(v) — 패턴 비교 전에 인자 값을 글로 바꾼다(배열은 쉼표 연결, 객체는 [object Object])."""
    if v is None:                                            # null → "null"(호출부에서 ?? "" 처리)
        return "null"                                       # JS의 null 문자열
    if v is True:                                           # 불리언 true
        return "true"                                       # JS 표기 소문자
    if v is False:                                          # 불리언 false
        return "false"                                      # JS 표기 소문자
    if isinstance(v, int):                                   # 정수
        return str(v)                                       # 정수는 그대로 글로
    if isinstance(v, float):                                 # 실수
        if math.isnan(v):                                   # NaN 판별
            return "NaN"                                    # JS 표기 NaN
        if math.isinf(v):                                   # 무한대 판별
            return "Infinity" if v > 0 else "-Infinity"     # 부호에 따라 JS 표기
        if v == int(v) and abs(v) < 1e21:                    # 3.0 → "3"  # 정수값 실수는 소수점 없이
            return str(int(v))                              # 정수 글로 변환
        return repr(v)                                       # 그 밖(지수 표기 등 드문 차이 있음)
    if isinstance(v, str):                                  # 글은 그대로
        return v                                            # 변환 없이 반환
    if isinstance(v, (list, tuple)):                         # Array.prototype.toString — null/undefined 원소는 ""
        return ",".join("" if x is None else js_string(x) for x in v)  # 원소를 쉼표로 이어 붙임
    return "[object Object]"                                 # 일반 객체


def _js_strict_equal(a: Any, b: Any) -> bool:               # JS === 비교 함수
    """JS === — 원시값은 타입까지 같아야 하고, 객체·배열은 같은 참조일 때만."""
    if isinstance(a, (dict, list, tuple)) or isinstance(b, (dict, list, tuple)):   # 참조 비교
        return a is b                                       # 참조 동일성만 인정
    if isinstance(a, bool) or isinstance(b, bool):           # true !== 1  # 불리언은 숫자와 다른 타입
        return isinstance(a, bool) and isinstance(b, bool) and a == b  # 둘 다 불리언이고 값이 같을 때만 참
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):   # 숫자끼리(NaN !== NaN 은 == 와 같음)
        return a == b                                       # 숫자 값 비교
    return type(a) is type(b) and a == b                     # 글 등


def glob_to_regex(pattern: str) -> "re.Pattern[str]":       # 글롭 패턴을 정규식으로 바꾸는 함수
    """원문 변환: 정규식 특수 문자 이스케이프 → * 는 .*, ? 는 . → ^...$ (JS . 의미 유지)."""
    escaped = _REGEX_META.sub(lambda m: "\\" + m.group(0), pattern)   # 메타 문자 이스케이프
    regex_pattern = escaped.replace("*", _JS_DOT + "*").replace("?", _JS_DOT)   # * ? 변환(이스케이프된 글에는 * ? 가 원래 그대로 있음)
    return re.compile(regex_pattern)                         # fullmatch로 사용(^...$)


def matches_tool_pattern(tool_name: str, pattern: str, tool_arguments: Optional[Dict[str, Any]] = None) -> bool:  # 도구 이름이 정책의 도구 패턴에 맞는지 판정
    """matchesToolPattern — "*", 정확한 이름, "Bash(ls*)" 명령 패턴, "external_*" 글롭."""
    if pattern == "*":                                       # 전부
        return True                                         # 와일드카드는 항상 일치
    if pattern == tool_name:                                 # 정확히 같음
        return True                                         # 이름이 정확히 같으면 일치
    bash_match = _BASH_PATTERN.fullmatch(pattern)            # Bash(명령 패턴)
    if bash_match:                                          # Bash(...) 형태 패턴이면
        is_bash_tool = tool_name == "Bash"                   # Bash 도구인지
        command = tool_arguments.get("command") if isinstance(tool_arguments, dict) else None   # toolArguments?.command  # 인자에서 command 꺼냄(없으면 None)
        if is_bash_tool and js_truthy(command):              # 명령이 있을 때만
            command_pattern = bash_match.group(1)            # 괄호 안
            if "*" in command_pattern or "?" in command_pattern:   # 글롭
                return glob_to_regex(command_pattern).fullmatch(js_string(command)) is not None   # regex.test(command)  # 글롭이면 정규식으로 명령 전체 일치 검사
            return _js_strict_equal(command, command_pattern)   # 정확히 같음
        return False                                         # Bash 패턴인데 Bash가 아니거나 명령 없음
    if "*" in pattern or "?" in pattern:                     # 일반 글롭(external_* 등)
        return glob_to_regex(pattern).fullmatch(tool_name) is not None  # external_* 같은 이름 글롭 일치 검사
    return False                                            # 어떤 규칙에도 안 맞음


def matches_arguments(args: Optional[Dict[str, Any]], patterns: Optional[Dict[str, Any]] = None) -> bool:  # 인자 조건(argumentMatches)이 모두 맞는지 판정
    """matchesArguments — 모든 조건이 맞아야 참. "*"는 아무 값, 글롭은 글로 바꿔 비교, 그 밖은 === 비교."""
    if not patterns:                                         # 조건 없음(undefined)
        return True                                         # 조건이 없으면 항상 통과
    args = args if isinstance(args, dict) else {}            # 인자 없음 방어
    for key, pattern in patterns.items():                    # 조건마다
        arg_value = args.get(key)                            # 없으면 undefined
        if pattern == "*" and isinstance(pattern, str):      # 와일드카드
            continue                                        # * 는 어떤 값이든 통과
        if isinstance(pattern, str) and ("*" in pattern or "?" in pattern):   # 글롭(문자열 패턴만)
            string_value = "" if arg_value is None else js_string(arg_value)   # String(argValue ?? "")  # 인자가 없으면 빈 글로 비교
            if glob_to_regex(pattern).fullmatch(string_value) is None:  # 글롭에 안 맞으면
                return False                                # 조건 불일치
        else:                                                # 정확히 같아야 함
            if not _js_strict_equal(arg_value, pattern):    # 엄격 동등이 아니면
                return False                                # 조건 불일치
    return True                                             # 모든 조건 통과


def permission_policy_to_tool_policy(permission: str) -> str:  # CLI 권한 값을 terminal-security 정책 이름으로 바꾸는 함수
    """permissionPolicyToToolPolicy — CLI 권한 → terminal-security ToolPolicy."""
    if permission == ALLOW:                                 # allow이면
        return "allowedWithoutPermission"                   # 묻지 않고 허용
    if permission == ASK:                                   # ask이면
        return "allowedWithPermission"                      # 허가를 받아야 허용
    if permission == EXCLUDE:                               # exclude이면
        return "disabled"                                   # 금지(disabled)
    return "allowedWithPermission"                           # 기본


def find_matching_policy(tool_name: str, args: Optional[Dict[str, Any]],  # 정적 정책만으로 판정하는 함수
                         policies: List[ToolPermissionPolicy]) -> Tuple[str, Optional[ToolPermissionPolicy]]:  # 반환: (권한, 일치한 정책)
    """정적 정책만으로 판정 — 처음 맞는 정책의 권한(없으면 "ask")과 그 정책."""
    for policy in policies:                                  # 순서대로
        if matches_tool_pattern(tool_name, policy.tool, args) and matches_arguments(args or {}, policy.argument_matches):  # 도구 패턴과 인자 조건이 모두 맞으면
            return policy.permission, policy                 # 첫 일치
    return ASK, None                                         # 기본 ask


def check_tool_permission(tool_name: str, args: Optional[Dict[str, Any]], policies: List[ToolPermissionPolicy],  # 동적 평가까지 포함한 최종 권한 판정 함수
                          evaluate_tool_call_policy: Optional[Callable[[str, Dict[str, Any]], str]] = None,  # Bash 등 도구가 제공하는 동적 평가 함수(선택)
                          ) -> Tuple[str, Optional[ToolPermissionPolicy]]:  # 반환형 — 줄 나눔
    """checkToolPermission — (permission, matchedPolicy).
    evaluate_tool_call_policy: cn은 ALL_BUILT_IN_TOOLS에서 같은 이름 도구를 찾아 그 함수를 쓴다(호출부가 넘김)."""
    args = args if args is not None else {}                  # 인자 없음 → {}
    base_permission, matched = find_matching_policy(tool_name, args, policies)   # 1) 정적 정책
    if evaluate_tool_call_policy is not None:                # 2) 동적 평가가 있는 도구(Bash)
        base_policy = permission_policy_to_tool_policy(base_permission)   # CLI 권한 → ToolPolicy
        evaluated = evaluate_tool_call_policy(base_policy, args)   # 평가
        if evaluated == "disabled":                          # 위험 명령은 사용자 설정과 무관하게 항상 차단
            return EXCLUDE, matched                         # 위험 명령은 exclude로 바꾼다
        return base_permission, matched                      # 그 밖에는 사용자 정책 그대로(ask로 올리지 않음)
    return base_permission, matched                          # 동적 평가 없음
