# ncn/permissions/policies.py — 권한 정책 형식과 기본 정책표. cn extensions/cli/src/permissions/types.ts · defaultPolicies.ts 를 그대로 옮긴 것
# 정책 목록은 순서대로 평가되고 처음 맞는 것이 이긴다(first match wins)
from __future__ import annotations                           # 타입 주석 지연 평가

from dataclasses import dataclass                            # 자료 구조
from typing import Any, Dict, List, Optional                 # 타입

ALLOW = "allow"                                              # PermissionPolicy: 묻지 않고 실행
ASK = "ask"                                                  # PermissionPolicy: 사용자에게 묻기
EXCLUDE = "exclude"                                          # PermissionPolicy: 금지(모델에게 도구 자체를 숨김)
PERMISSION_POLICIES = (ALLOW, ASK, EXCLUDE)                  # types.ts PermissionPolicy 값들

MODE_NORMAL = "normal"                                       # PermissionMode: 일반
MODE_PLAN = "plan"                                           # PermissionMode: 계획(쓰기 금지)
MODE_AUTO = "auto"                                           # PermissionMode: 전부 허용
PERMISSION_MODES = (MODE_NORMAL, MODE_PLAN, MODE_AUTO)       # Shift+Tab 순환 순서(UserInput.tsx cycleModes)


@dataclass
class ToolPermissionPolicy:
    """types.ts ToolPermissionPolicy — 도구 이름 패턴 + 권한 + (선택) 인자 일치 조건."""
    tool: str                                                # 도구 이름 패턴("*", "Bash", "Bash(npm *)", "external_*")
    permission: str                                          # "allow" | "ask" | "exclude"
    argument_matches: Optional[Dict[str, Any]] = None        # argumentMatches — 없으면 그 도구의 모든 호출에 적용


def get_default_tool_policies(is_headless: bool = False) -> List[ToolPermissionPolicy]:
    """defaultPolicies.ts getDefaultToolPolicies — 내장 도구 기본 권한표(순서 그대로)."""
    policies = [
        # 쓰기 도구
        ToolPermissionPolicy("Edit", ASK),
        ToolPermissionPolicy("MultiEdit", ASK),
        ToolPermissionPolicy("Write", ASK),
        ToolPermissionPolicy("CheckBackgroundJob", ALLOW),
        ToolPermissionPolicy("AskQuestion", ALLOW),
        ToolPermissionPolicy("Checklist", ALLOW),
        ToolPermissionPolicy("Diff", ALLOW),
        ToolPermissionPolicy("Skills", ALLOW),
        ToolPermissionPolicy("Exit", ALLOW),                 # Exit은 대체로 안전(헤드리스 전용)
        ToolPermissionPolicy("Fetch", ALLOW),                # 엄밀히 읽기 전용은 아님(원문 주석)
        ToolPermissionPolicy("List", ALLOW),
        ToolPermissionPolicy("Read", ALLOW),
        ToolPermissionPolicy("Search", ALLOW),
        ToolPermissionPolicy("Status", ALLOW),
        ToolPermissionPolicy("ReportFailure", ALLOW),
        ToolPermissionPolicy("UploadArtifact", ALLOW),
    ]
    if is_headless:                                          # MCP와 Bash: 헤드리스는 허용
        policies.append(ToolPermissionPolicy("Bash", ALLOW))
        policies.append(ToolPermissionPolicy("*", ALLOW))
    else:                                                    # TUI는 묻기
        policies.append(ToolPermissionPolicy("Bash", ASK))
        policies.append(ToolPermissionPolicy("*", ASK))
    return policies


# plan 모드: 완전 대체 — 쓰기 도구 금지, 읽기와 Bash만 허용(defaultPolicies.ts PLAN_MODE_POLICIES)
PLAN_MODE_POLICIES: List[ToolPermissionPolicy] = [
    ToolPermissionPolicy("Edit", EXCLUDE),
    ToolPermissionPolicy("MultiEdit", EXCLUDE),
    ToolPermissionPolicy("Write", EXCLUDE),
    ToolPermissionPolicy("Bash", ALLOW),                     # 원문 TODO: Bash 읽기 전용 문제는 미해결
    ToolPermissionPolicy("CheckBackgroundJob", ALLOW),
    ToolPermissionPolicy("AskQuestion", ALLOW),
    ToolPermissionPolicy("Checklist", ALLOW),
    ToolPermissionPolicy("Diff", ALLOW),
    ToolPermissionPolicy("Exit", ALLOW),
    ToolPermissionPolicy("Fetch", ALLOW),
    ToolPermissionPolicy("List", ALLOW),
    ToolPermissionPolicy("Read", ALLOW),
    ToolPermissionPolicy("ReportFailure", ALLOW),
    ToolPermissionPolicy("Search", ALLOW),
    ToolPermissionPolicy("Skills", ALLOW),
    ToolPermissionPolicy("Status", ALLOW),
    ToolPermissionPolicy("UploadArtifact", ALLOW),
    ToolPermissionPolicy("*", ALLOW),                        # MCP 도구 허용
]

# auto 모드: 완전 대체 — 전부 묻지 않고 허용(AUTO_MODE_POLICIES)
AUTO_MODE_POLICIES: List[ToolPermissionPolicy] = [
    ToolPermissionPolicy("*", ALLOW),
]


def command_line_flags_to_policies(allow: Optional[List[str]] = None, ask: Optional[List[str]] = None,
                                   exclude: Optional[List[str]] = None) -> List[ToolPermissionPolicy]:
    """precedenceResolver.ts commandLineFlagsToPolicies — exclude → ask → allow 순서(이름은 그대로, 정규화 없음).
    --allow "Bash(npm *)" 같은 값도 tool 패턴 문자열 그대로 들어간다(인자 조건으로 바꾸지 않음 — permissions.yaml과 다름)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    for tool in exclude or []:                               # 금지 먼저
        policies.append(ToolPermissionPolicy(tool, EXCLUDE))
    for tool in ask or []:                                   # 묻기
        policies.append(ToolPermissionPolicy(tool, ASK))
    for tool in allow or []:                                 # 허용
        policies.append(ToolPermissionPolicy(tool, ALLOW))
    return policies


def convert_legacy_mode_flags(readonly: bool = False, auto: bool = False) -> Optional[str]:
    """flags/flagProcessor.ts convertLegacyModeFlags — --readonly → plan, --auto → auto, 둘 다면 오류."""
    if readonly and auto:                                    # 함께 쓸 수 없음(원문 오류 글)
        raise ValueError("Cannot use both --readonly and --auto flags together")
    if readonly:
        return MODE_PLAN
    if auto:
        return MODE_AUTO
    return None                                              # 지정 없음
