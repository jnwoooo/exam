# ncn/permissions/yaml_loader.py — ~/.continue/permissions.yaml 읽기·쓰기. cn extensions/cli/src/permissions/permissionsYamlLoader.ts · policyWriter.ts 를 그대로 옮긴 것
# 파일 형식: allow / ask / exclude 세 목록. 항목은 "Write" 또는 "Bash(npm install)"처럼 "도구(인자 패턴)"
# "계속 + 다시 묻지 않기"는 generate_policy_rule로 규칙을 만들어 allow 목록 끝에 붙인다(Bash는 "Bash(<명령 이름>*)")
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 큰따옴표 글 만들기(YAML 출력)
import logging                                               # 로그(stdout 금지 — ACP)
import os                                                    # 경로 정규화
import re                                                    # 패턴 해석
from pathlib import Path                                     # 경로
from typing import Any, Dict, List, Optional                 # 타입

import yaml                                                  # pyyaml

from ncn.permissions.matcher import js_truthy                # JS 참/거짓
from ncn.permissions.policies import ALLOW, ASK, EXCLUDE, ToolPermissionPolicy   # 정책 형식
from ncn.security.terminal_security import JS_WS, js_trim    # JS 공백 집합·trim

logger = logging.getLogger("ncn.permissions")                # 로거

PERMISSIONS_YAML_NAME = "permissions.yaml"                   # 파일 이름
_VALID_KEYS = ("allow", "ask", "exclude")                    # 허용 키
_TOOL_PATTERN = re.compile(r"([^(]+)(?:\(([^)]*)\))?")       # /^([^(]+)(?:\(([^)]*)\))?$/ — fullmatch로 사용
# 도구별 대표 인자 이름(parseToolPattern toolArgMappings, 원문 그대로 — Read/List/Search/Edit/Write는 실제 인자 이름과 다름: cn 버그 후보)
TOOL_ARG_MAPPINGS = {
    "Write": "file_path",
    "Edit": "file_path",
    "Read": "file_path",
    "List": "path",
    "Search": "query",
    "Bash": "command",
    "Fetch": "url",
    "Diff": "file_path",
}

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


def permissions_yaml_path(global_dir: Path) -> Path:
    """PERMISSIONS_YAML_PATH = path.resolve(continueHome/permissions.yaml)."""
    return Path(os.path.abspath(os.path.join(str(global_dir), PERMISSIONS_YAML_NAME)))   # resolve(심볼릭 링크는 풀지 않음 — Node path.resolve와 같게)


def load_permissions_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """loadPermissionsYaml — 없거나 해석 실패·형식 오류면 None."""
    try:
        if not path.exists():                                # 파일 없음
            logger.debug("Permissions YAML file does not exist: %s", path)
            return None
        content = path.read_text(encoding="utf-8")           # 읽기
        parsed = yaml.safe_load(content)                     # 해석
        if isinstance(parsed, (dict, list)):                 # parsed && typeof parsed === "object"
            if isinstance(parsed, list):                     # 배열: 키가 "0","1".. 이라 비어 있을 때만 통과(빈 설정으로 취급)
                if len(parsed) == 0:
                    return {}
            else:
                valid = all(isinstance(k, str) and k in _VALID_KEYS
                            and (not js_truthy(parsed[k]) or isinstance(parsed[k], list)) for k in parsed)   # 키와 값 형식 확인
                if valid:
                    logger.debug("Loaded permissions from YAML: allow=%s ask=%s exclude=%s",
                                 len(parsed.get("allow") or []), len(parsed.get("ask") or []),
                                 len(parsed.get("exclude") or []))
                    return parsed
        logger.warning("Invalid permissions YAML structure: %s", path)   # 형식 오류
        return None
    except Exception as error:                               # 읽기·해석 실패
        logger.error("Failed to load permissions YAML: %s (%s)", error, path)
        return None


def parse_tool_pattern(pattern: str, permission: str) -> ToolPermissionPolicy:
    """parseToolPattern — "Write" → {tool}, "Bash(npm install)" → {tool:"Bash", argumentMatches:{command:"npm install"}}.
    형식이 틀리면 원문처럼 예외(설정 읽기 단계에서 잡지 않음)."""
    if not isinstance(pattern, str):                         # JS: pattern.match 가 TypeError
        raise TypeError("pattern.match is not a function")
    match = _TOOL_PATTERN.fullmatch(pattern)                 # 도구(인자)
    if not match:
        raise ValueError(f"Invalid tool pattern: {pattern}")   # 원문 오류 글
    tool_name, args = match.group(1), match.group(2)         # 이름, 괄호 안
    normalized = js_trim(tool_name)                          # 이름 trim
    policy = ToolPermissionPolicy(tool=normalized, permission=permission)   # 기본 정책
    if args:                                                 # 괄호 안이 비어 있지 않으면
        trimmed = js_trim(args)                              # trim
        if trimmed:
            arg_key = TOOL_ARG_MAPPINGS.get(normalized) or "pattern"   # 대표 인자(없으면 "pattern")
            policy.argument_matches = {arg_key: trimmed}     # 인자 조건
    return policy


def yaml_config_to_policies(config: Dict[str, Any]) -> List[ToolPermissionPolicy]:
    """yamlConfigToPolicies — exclude → ask → allow 순서(더 엄격한 것 먼저)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    for key, permission in (("exclude", EXCLUDE), ("ask", ASK), ("allow", ALLOW)):   # 원문 순서
        items = config.get(key)                              # 목록
        if js_truthy(items):                                 # 있을 때만
            for pattern in items:
                policies.append(parse_tool_pattern(pattern, permission))
    return policies


def ensure_permissions_yaml_exists(path: Path) -> None:
    """ensurePermissionsYamlExists — 폴더와 기본 파일을 만든다(이미 있으면 그대로)."""
    path.parent.mkdir(parents=True, exist_ok=True)           # 폴더
    if not path.exists():                                    # 파일 없음
        path.write_text(DEFAULT_PERMISSIONS_YAML, encoding="utf-8")   # 기본 내용
        logger.info("Created default permissions.yaml: %s", path)


def generate_policy_rule(tool_name: str, tool_args: Any) -> str:
    """policyWriter.ts generatePolicyRule — Bash는 첫 명령 이름으로 "Bash(<이름>*)", 나머지는 도구 이름 그대로."""
    normalized = tool_name                                   # 정규화 없음
    command = tool_args.get("command") if isinstance(tool_args, dict) else None   # toolArgs?.command
    if normalized == "Bash" and js_truthy(command):          # Bash + 명령 있음
        if not isinstance(command, str):                     # JS: command.trim 이 TypeError
            raise TypeError("toolArgs.command.trim is not a function")
        command = js_trim(command)                           # trim
        first = js_trim(re.split(r"[;&|]|&&|\|\|", command)[0])   # 첫 명령(;, &, | 앞 — &&·||도 한 글자 분리에 먼저 걸림)
        if first:
            command_name = re.split("[" + JS_WS + "]+", first)[0]   # 명령 이름(공백 앞)
            return f"Bash({command_name}*)"
    return normalized                                        # 그 밖: 도구 이름


def _yaml_scalar(value: Any) -> str:
    """npm yaml(2.x) stringify의 글 하나 표기 — 그냥 써도 같은 글로 읽히면 따옴표 없이, 아니면 큰따옴표."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if not isinstance(value, str):                           # 그 밖(드묾)
        return json.dumps(value, ensure_ascii=False)
    plain_ok = (value != "" and value == value.strip() and "\n" not in value
                and value[0] not in "-?:,[]{}#&*!|>'\"%@`" and ": " not in value and " #" not in value
                and not value.endswith(":"))                 # 평문으로 써도 안전한지(보수적으로)
    if plain_ok:
        try:
            plain_ok = yaml.safe_load("- " + value) == [value]   # 숫자·true 등으로 읽히지 않는지
        except Exception:
            plain_ok = False
    return value if plain_ok else json.dumps(value, ensure_ascii=False)


def _yaml_stringify(config: Dict[str, Any]) -> str:
    """YAML.stringify(config) 대응 — 최상위 키 순서 유지, 목록은 들여쓴 "  - 값", 빈 목록은 []."""
    lines: List[str] = []                                    # 출력 줄
    for key, value in config.items():                        # 키 순서 그대로
        if isinstance(value, list):                          # 목록
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_yaml_scalar(v)}" for v in value)
        else:                                                # 목록이 아닌 값(null 등)
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n" if lines else "{}\n"      # 빈 객체는 {}


def add_policy_to_yaml(path: Path, policy_rule: str) -> None:
    """policyWriter.ts addPolicyToYaml — allow 목록에 없으면 붙이고 머리말과 함께 파일 전체를 다시 쓴다."""
    try:
        config = load_permissions_yaml(path) or {}           # 기존 설정(없으면 빈 것)
        if not js_truthy(config.get("allow")):               # allow 목록 보장
            config["allow"] = []
        if policy_rule in config["allow"]:                   # 이미 있음
            logger.debug("Policy rule already exists: %s", policy_rule)
        else:
            config["allow"].append(policy_rule)              # 끝에 추가
            final = MANAGED_HEADER + _yaml_stringify(config)  # 머리말 + YAML
            path.write_text(final, encoding="utf-8")         # 쓰기(폴더는 이미 있다고 가정 — 원문과 같음)
            logger.info("Added policy rule to permissions.yaml: %s", policy_rule)
    except Exception as error:                               # 실패 기록 후 다시 던짐(원문)
        logger.error("Failed to add policy to permissions.yaml: %s (%s)", error, policy_rule)
        raise
