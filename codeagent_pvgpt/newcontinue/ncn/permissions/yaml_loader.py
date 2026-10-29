# ncn/permissions/yaml_loader.py — ~/.continue/permissions.yaml 읽기·쓰기. cn extensions/cli/src/permissions/permissionsYamlLoader.ts · policyWriter.ts 를 그대로 옮긴 것
# 파일 형식: allow / ask / exclude 세 목록. 항목은 "Write" 또는 "Bash(npm install)"처럼 "도구(인자 패턴)"
# "계속 + 다시 묻지 않기"는 generate_policy_rule로 규칙을 만들어 allow 목록 끝에 붙인다(Bash는 cn "Bash(<명령 이름>*)" → ncn 명령 전체 일치, 부록B S1)
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 큰따옴표 글 만들기(YAML 출력)
import logging                                               # 로그(stdout 금지 — ACP)
import os                                                    # 경로 정규화
import re                                                    # 패턴 해석
from pathlib import Path                                     # 경로
from typing import Any, Dict, List, Optional                 # 타입

import yaml                                                  # pyyaml  # yaml 해석 라이브러리

from ncn.compat import cn_compat                             # [ncn 부록B] cn 원래 동작 스위치
from ncn.permissions.matcher import js_truthy                # JS 참/거짓
from ncn.permissions.policies import ALLOW, ASK, EXCLUDE, ToolPermissionPolicy   # 정책 형식
from ncn.security.terminal_security import JS_WS, js_trim    # JS 공백 집합·trim

logger = logging.getLogger("ncn.permissions")                # 로거

PERMISSIONS_YAML_NAME = "permissions.yaml"                   # 파일 이름
_VALID_KEYS = ("allow", "ask", "exclude")                    # 허용 키
_TOOL_PATTERN = re.compile(r"([^(]+)(?:\(([^)]*)\))?")       # /^([^(]+)(?:\(([^)]*)\))?$/ — fullmatch로 사용
# 도구별 대표 인자 이름(parseToolPattern toolArgMappings, 원문 그대로 — Read/List/Search/Edit/Write는 실제 인자 이름과 다름: cn 버그 후보)
TOOL_ARG_MAPPINGS = {                                       # 도구별 대표 인자 이름 표
    "Write": "file_path",                                   # Write: 파일 경로
    "Edit": "file_path",                                    # Edit: 파일 경로
    "Read": "file_path",                                    # Read: 파일 경로
    "List": "path",                                         # List: 경로
    "Search": "query",                                      # Search: 검색어
    "Bash": "command",                                      # Bash: 명령
    "Fetch": "url",                                         # Fetch: 주소
    "Diff": "file_path",                                    # Diff: 파일 경로
}                                                           # 표 끝

# ensurePermissionsYamlExists 가 만드는 기본 내용(원문 그대로)
DEFAULT_PERMISSIONS_YAML = """# cn tool permissions

# Tools that are automatically allowed without prompting
allow: []

# Tools that require user confirmation before execution
ask: []

# Tools that are completely excluded (model won't know they exist)
exclude: []
"""

# addPolicyToYaml 이 붙이는 머리말(원문 그대로)
MANAGED_HEADER = """# Continue CLI Permissions Configuration
# This file is managed by the Continue CLI and should not be edited manually.
# Use the TUI to modify permissions interactively.

"""


def permissions_yaml_path(global_dir: Path) -> Path:        # permissions.yaml 절대 경로를 구함
    """PERMISSIONS_YAML_PATH = path.resolve(continueHome/permissions.yaml)."""
    return Path(os.path.abspath(os.path.join(str(global_dir), PERMISSIONS_YAML_NAME)))   # resolve(심볼릭 링크는 풀지 않음 — Node path.resolve와 같게)


def load_permissions_yaml(path: Path) -> Optional[Dict[str, Any]]:  # yaml 파일을 읽어 검증하는 함수
    """loadPermissionsYaml — 없거나 해석 실패·형식 오류면 None."""
    try:                                                    # 읽기·해석 실패를 한꺼번에 처리
        if not path.exists():                                # 파일 없음
            logger.debug("Permissions YAML file does not exist: %s", path)  # 없음 로그
            return None                                     # None으로 종료
        content = path.read_text(encoding="utf-8")           # 읽기
        parsed = yaml.safe_load(content)                     # 해석
        if isinstance(parsed, (dict, list)):                # 파싱 결과가 객체·배열인지 — parsed && typeof parsed === "object"
            if isinstance(parsed, list):                     # 배열: 키가 "0","1".. 이라 비어 있을 때만 통과(빈 설정으로 취급)
                if len(parsed) == 0:                        # 빈 배열이면
                    return {}                               # 빈 설정으로 통과
            else:                                           # dict인 경우
                valid = all(isinstance(k, str) and k in _VALID_KEYS  # 모든 키가 allow/ask/exclude이고 값이 목록인지
                            and (not js_truthy(parsed[k]) or isinstance(parsed[k], list)) for k in parsed)   # 키와 값 형식 확인
                if valid:                                   # 형식이 맞으면
                    logger.debug("Loaded permissions from YAML: allow=%s ask=%s exclude=%s",  # 읽은 개수 로그
                                 len(parsed.get("allow") or []), len(parsed.get("ask") or []),  # allow·ask 개수
                                 len(parsed.get("exclude") or []))  # exclude 개수
                    return parsed                           # 읽은 설정 반환
        logger.warning("Invalid permissions YAML structure: %s", path)   # 형식 오류
        return None                                         # 형식 오류 → None
    except Exception as error:                               # 읽기·해석 실패
        logger.error("Failed to load permissions YAML: %s (%s)", error, path)  # 실패 로그
        return None                                         # 실패면 None


def parse_tool_pattern(pattern: str, permission: str) -> ToolPermissionPolicy:  # '도구(인자)' 문자열을 정책으로 바꾸는 함수
    """parseToolPattern — "Write" → {tool}, "Bash(npm install)" → {tool:"Bash", argumentMatches:{command:"npm install"}}.
    형식이 틀리면 원문처럼 예외(설정 읽기 단계에서 잡지 않음)."""
    if not isinstance(pattern, str):                         # JS: pattern.match 가 TypeError
        raise TypeError("pattern.match is not a function")  # 문자열이 아니면 TypeError
    match = _TOOL_PATTERN.fullmatch(pattern)                 # 도구(인자)
    if not match:                                           # 형식에 안 맞으면
        raise ValueError(f"Invalid tool pattern: {pattern}")   # 원문 오류 글
    tool_name, args = match.group(1), match.group(2)         # 이름, 괄호 안
    normalized = js_trim(tool_name)                          # 이름 trim
    policy = ToolPermissionPolicy(tool=normalized, permission=permission)   # 기본 정책
    if args:                                                 # 괄호 안이 비어 있지 않으면
        trimmed = js_trim(args)                             # 괄호 안 공백 제거 — trim
        if trimmed:                                         # 인자가 공백뿐이면 조건 없음
            arg_key = TOOL_ARG_MAPPINGS.get(normalized) or "pattern"   # 대표 인자(없으면 "pattern")
            policy.argument_matches = {arg_key: trimmed}     # 인자 조건
    return policy                                           # 만든 정책 반환


def yaml_config_to_policies(config: Dict[str, Any]) -> List[ToolPermissionPolicy]:  # yaml 설정 전체를 정책 목록으로 바꾸는 함수
    """yamlConfigToPolicies — exclude → ask → allow 순서(더 엄격한 것 먼저)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    for key, permission in (("exclude", EXCLUDE), ("ask", ASK), ("allow", ALLOW)):   # 원문 순서
        items = config.get(key)                              # 목록
        if js_truthy(items):                                 # 있을 때만
            for pattern in items:                           # 항목마다
                policies.append(parse_tool_pattern(pattern, permission))  # 패턴을 정책으로 해석해 추가
    return policies                                         # 정책 목록 반환


def ensure_permissions_yaml_exists(path: Path) -> None:     # 기본 permissions.yaml이 없으면 만드는 함수
    """ensurePermissionsYamlExists — 폴더와 기본 파일을 만든다(이미 있으면 그대로)."""
    path.parent.mkdir(parents=True, exist_ok=True)           # 폴더
    if not path.exists():                                    # 파일 없음
        path.write_text(DEFAULT_PERMISSIONS_YAML, encoding="utf-8", newline="")   # 기본 내용(줄바꿈 변환 없음 — Windows 에서도 \n)
        logger.info("Created default permissions.yaml: %s", path)  # 생성 로그


_EXACT_RULE_UNSAFE = re.compile(r"[()*?]")                    # [ncn 부록B S1] 정확 일치 규칙에 넣으면 뜻이 바뀌는 글자(괄호=패턴 해석 실패, * ?=글롭)


def generate_policy_rule(tool_name: str, tool_args: Any) -> Optional[str]:  # '항상 허용' 규칙 문자열을 만드는 함수
    """policyWriter.ts generatePolicyRule — Bash는 첫 명령 이름으로 "Bash(<이름>*)", 나머지는 도구 이름 그대로.
    [ncn 부록B S1·S2] 기본(고친 동작): Bash는 명령 전체 정확 일치 "Bash(<명령 전체>)". 정확 일치로 적을 수 없는 명령
    (괄호·* ?·앞뒤 공백·명령 없음)은 규칙을 만들지 않고 None(이번 한 번만 허용). NCN_CN_COMPAT=1 이면 cn 원래 동작."""
    normalized = tool_name                                   # 정규화 없음
    command = tool_args.get("command") if isinstance(tool_args, dict) else None  # 인자에서 command 꺼냄 — toolArgs?.command
    if normalized == "Bash" and not cn_compat():             # [ncn 부록B S1·S2] 고친 동작: 명령 앞부분 와일드카드·Bash 전체 규칙을 만들지 않음
        # cn 원래 동작: "ls -la && curl x | sh" → "Bash(ls*)"(뒤 명령까지 전부 허용), "| ls" → "Bash"(Bash 전체 허용)
        if not isinstance(command, str) or not js_trim(command):   # 명령이 없거나 글이 아니면
            return None                                     # 규칙 없음(이번 한 번만 허용) — cn은 "Bash"(전체 허용)를 저장
        if command != js_trim(command) or _EXACT_RULE_UNSAFE.search(command):   # 앞뒤 공백(읽을 때 trim돼 안 맞음)·괄호·글롭 글자
            return None                                     # 정확 일치로 적을 수 없음 → 규칙 없음
        return f"Bash({command})"                           # 명령 전체 정확 일치 규칙(matches_arguments의 === 비교로 판정)
    if normalized == "Bash" and js_truthy(command):          # Bash + 명령 있음
        if not isinstance(command, str):                     # JS: command.trim 이 TypeError
            raise TypeError("toolArgs.command.trim is not a function")  # 문자열 아니면 TypeError
        command = js_trim(command)                          # 명령 앞뒤 공백 제거 — trim
        first = js_trim(re.split(r"[;&|]|&&|\|\|", command)[0])   # 첫 명령(;, &, | 앞 — &&·||도 한 글자 분리에 먼저 걸림)
        if first:                                           # 첫 명령이 있으면
            command_name = re.split("[" + JS_WS + "]+", first)[0]   # 명령 이름(공백 앞)
            return f"Bash({command_name}*)"                 # 예: Bash(npm*)
    return normalized                                        # 그 밖: 도구 이름


def _yaml_scalar(value: Any) -> str:                        # 값 하나를 yaml 글로 표기
    """npm yaml(2.x) stringify의 글 하나 표기 — 그냥 써도 같은 글로 읽히면 따옴표 없이, 아니면 큰따옴표."""
    if value is None:                                       # None이면
        return "null"                                       # yaml의 null 표기 — yaml null
    if isinstance(value, bool):                             # 불리언이면
        return "true" if value else "false"                 # true/false 소문자
    if isinstance(value, (int, float)):                     # 숫자면
        return json.dumps(value)                            # JSON 표기와 같음
    if not isinstance(value, str):                           # 그 밖(드묾)
        return json.dumps(value, ensure_ascii=False)        # JSON 문자열로
    plain_ok = (value != "" and value == value.strip() and "\n" not in value  # 평문 가능 조건 1: 비어 있지 않고 공백 없음
                and value[0] not in "-?:,[]{}#&*!|>'\"%@`" and ": " not in value and " #" not in value  # 조건 2: 특수문자로 시작하지 않고 ': ' 없음
                and not value.endswith(":"))                 # 평문으로 써도 안전한지(보수적으로)
    if plain_ok:                                            # 평문 가능하면
        try:                                                # 다시 읽어 같은지 확인
            plain_ok = yaml.safe_load("- " + value) == [value]   # 숫자·true 등으로 읽히지 않는지
        except Exception:                                   # 해석 오류면
            plain_ok = False                                # 평문 불가로 표시
    return value if plain_ok else json.dumps(value, ensure_ascii=False)  # 평문 또는 큰따옴표 표기 반환


def _yaml_stringify(config: Dict[str, Any]) -> str:         # 설정 dict를 yaml 글로 바꿈
    """YAML.stringify(config) 대응 — 최상위 키 순서 유지, 목록은 들여쓴 "  - 값", 빈 목록은 []."""
    lines: List[str] = []                                    # 출력 줄
    for key, value in config.items():                        # 키 순서 그대로
        if isinstance(value, list):                          # 목록
            if not value:                                   # 빈 목록이면
                lines.append(f"{key}: []")                  # '키: []' 형식
            else:                                           # 값이 있으면
                lines.append(f"{key}:")                     # '키:' 줄
                lines.extend(f"  - {_yaml_scalar(v)}" for v in value)  # 항목마다 '  - 값' 줄
        else:                                                # 목록이 아닌 값(null 등)
            lines.append(f"{key}: {_yaml_scalar(value)}")   # 단일 값 줄
    return "\n".join(lines) + "\n" if lines else "{}\n"      # 빈 객체는 {}


def add_policy_to_yaml(path: Path, policy_rule: str) -> None:  # allow 목록에 규칙을 추가해 파일을 다시 쓰는 함수
    """policyWriter.ts addPolicyToYaml — allow 목록에 없으면 붙이고 머리말과 함께 파일 전체를 다시 쓴다."""
    try:                                                    # 실패는 로그 후 다시 던짐
        config = load_permissions_yaml(path) or {}           # 기존 설정(없으면 빈 것)
        if not js_truthy(config.get("allow")):               # allow 목록 보장
            config["allow"] = []                            # allow가 없으면 빈 목록 생성
        if policy_rule in config["allow"]:                   # 이미 있음
            logger.debug("Policy rule already exists: %s", policy_rule)  # 중복 로그(추가 안 함)
        else:                                               # 없으면
            config["allow"].append(policy_rule)              # 끝에 추가
            final = MANAGED_HEADER + _yaml_stringify(config)  # 머리말 + YAML
            path.write_text(final, encoding="utf-8", newline="")   # 쓰기(줄바꿈 변환 없음, 폴더는 이미 있다고 가정 — 원문과 같음)
            logger.info("Added policy rule to permissions.yaml: %s", policy_rule)  # 추가 로그
    except Exception as error:                               # 실패 기록 후 다시 던짐(원문)
        logger.error("Failed to add policy to permissions.yaml: %s (%s)", error, policy_rule)  # 실패 로그
        raise                                               # 원문처럼 예외 전파
