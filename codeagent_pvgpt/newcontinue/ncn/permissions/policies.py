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


@dataclass                                                  # 정책 한 건을 담는 단순 자료 클래스
class ToolPermissionPolicy:                                 # types.ts ToolPermissionPolicy 대응
    """types.ts ToolPermissionPolicy — 도구 이름 패턴 + 권한 + (선택) 인자 일치 조건."""
    tool: str                                                # 도구 이름 패턴("*", "Bash", "Bash(npm *)", "external_*")
    permission: str                                          # "allow" | "ask" | "exclude"  # 허용·묻기·금지 중 하나
    argument_matches: Optional[Dict[str, Any]] = None        # argumentMatches — 없으면 그 도구의 모든 호출에 적용


def get_default_tool_policies(is_headless: bool = False) -> List[ToolPermissionPolicy]:  # 내장 도구 기본 권한표를 만드는 함수(헤드리스 여부로 Bash·MCP만 달라짐)
    """defaultPolicies.ts getDefaultToolPolicies — 내장 도구 기본 권한표(순서 그대로)."""
    policies = [                                            # 앞에서 뒤로 평가되는 기본 정책 목록
        # 쓰기 도구
        ToolPermissionPolicy("Edit", ASK),                  # 파일 수정은 묻는다
        ToolPermissionPolicy("MultiEdit", ASK),             # 여러 곳 수정도 묻는다
        ToolPermissionPolicy("Write", ASK),                 # 새 파일 쓰기도 묻는다
        ToolPermissionPolicy("CheckBackgroundJob", ALLOW),  # 백그라운드 작업 조회는 허용
        ToolPermissionPolicy("AskQuestion", ALLOW),         # 사용자에게 질문하는 도구는 허용
        ToolPermissionPolicy("Checklist", ALLOW),           # 체크리스트는 허용
        ToolPermissionPolicy("Diff", ALLOW),                # diff 보기는 허용
        ToolPermissionPolicy("Skills", ALLOW),              # 스킬 호출은 허용
        ToolPermissionPolicy("Exit", ALLOW),                 # Exit은 대체로 안전(헤드리스 전용)
        ToolPermissionPolicy("Fetch", ALLOW),                # 엄밀히 읽기 전용은 아님(원문 주석)
        ToolPermissionPolicy("List", ALLOW),                # 목록 조회는 읽기라 허용
        ToolPermissionPolicy("Read", ALLOW),                # 파일 읽기 허용
        ToolPermissionPolicy("Search", ALLOW),              # 검색 허용
        ToolPermissionPolicy("Status", ALLOW),              # 상태 조회 허용
        ToolPermissionPolicy("ReportFailure", ALLOW),       # 실패 보고 허용
        ToolPermissionPolicy("UploadArtifact", ALLOW),      # 산출물 업로드 허용
    ]                                                       # 기본 목록 끝
    if is_headless:                                          # MCP와 Bash: 헤드리스는 허용
        policies.append(ToolPermissionPolicy("Bash", ALLOW))  # 헤드리스: Bash 허용
        policies.append(ToolPermissionPolicy("*", ALLOW))   # 헤드리스: 그 밖의 도구(MCP)도 허용
    else:                                                    # TUI는 묻기
        policies.append(ToolPermissionPolicy("Bash", ASK))  # TUI: Bash는 묻는다
        policies.append(ToolPermissionPolicy("*", ASK))     # TUI: 그 밖의 도구(MCP)도 묻는다
    return policies                                         # 완성된 기본 정책 목록 반환


# plan 모드: 완전 대체 — 쓰기 도구 금지, 읽기와 Bash만 허용(defaultPolicies.ts PLAN_MODE_POLICIES)
PLAN_MODE_POLICIES: List[ToolPermissionPolicy] = [          # plan 모드 정책표(기본표를 통째로 대체)
    ToolPermissionPolicy("Edit", EXCLUDE),                  # plan: 수정 도구는 모델에게 숨김
    ToolPermissionPolicy("MultiEdit", EXCLUDE),             # plan: 다중 수정 숨김
    ToolPermissionPolicy("Write", EXCLUDE),                 # plan: 쓰기 숨김
    ToolPermissionPolicy("Bash", ALLOW),                     # 원문 TODO: Bash 읽기 전용 문제는 미해결
    ToolPermissionPolicy("CheckBackgroundJob", ALLOW),      # plan: 백그라운드 조회 허용
    ToolPermissionPolicy("AskQuestion", ALLOW),             # plan: 질문 허용
    ToolPermissionPolicy("Checklist", ALLOW),               # plan: 체크리스트 허용
    ToolPermissionPolicy("Diff", ALLOW),                    # plan: diff 허용
    ToolPermissionPolicy("Exit", ALLOW),                    # plan: Exit 허용
    ToolPermissionPolicy("Fetch", ALLOW),                   # plan: Fetch 허용
    ToolPermissionPolicy("List", ALLOW),                    # plan: List 허용
    ToolPermissionPolicy("Read", ALLOW),                    # plan: Read 허용
    ToolPermissionPolicy("ReportFailure", ALLOW),           # plan: 실패 보고 허용
    ToolPermissionPolicy("Search", ALLOW),                  # plan: 검색 허용
    ToolPermissionPolicy("Skills", ALLOW),                  # plan: 스킬 허용
    ToolPermissionPolicy("Status", ALLOW),                  # plan: 상태 허용
    ToolPermissionPolicy("UploadArtifact", ALLOW),          # plan: 업로드 허용
    ToolPermissionPolicy("*", ALLOW),                        # MCP 도구 허용
]                                                           # plan 정책표 끝

# auto 모드: 완전 대체 — 전부 묻지 않고 허용(AUTO_MODE_POLICIES)
AUTO_MODE_POLICIES: List[ToolPermissionPolicy] = [          # auto 모드 정책표(전부 허용으로 대체)
    ToolPermissionPolicy("*", ALLOW),                       # 모든 도구를 묻지 않고 허용
]                                                           # auto 정책표 끝


def command_line_flags_to_policies(allow: Optional[List[str]] = None, ask: Optional[List[str]] = None,  # 명령줄 --allow/--ask/--exclude 값을 정책 목록으로 바꾸는 함수
                                   exclude: Optional[List[str]] = None) -> List[ToolPermissionPolicy]:  # 세 번째 인자(exclude)와 반환형 — 줄 나눔
    """precedenceResolver.ts commandLineFlagsToPolicies — exclude → ask → allow 순서(이름은 그대로, 정규화 없음).
    --allow "Bash(npm *)" 같은 값도 tool 패턴 문자열 그대로 들어간다(인자 조건으로 바꾸지 않음 — permissions.yaml과 다름)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    for tool in exclude or []:                               # 금지 먼저
        policies.append(ToolPermissionPolicy(tool, EXCLUDE))  # 금지 정책 추가
    for tool in ask or []:                                   # 묻기
        policies.append(ToolPermissionPolicy(tool, ASK))    # 묻기 정책 추가
    for tool in allow or []:                                 # 허용
        policies.append(ToolPermissionPolicy(tool, ALLOW))  # 허용 정책 추가
    return policies                                         # exclude → ask → allow 순서의 목록 반환


def convert_legacy_mode_flags(readonly: bool = False, auto: bool = False) -> Optional[str]:  # --readonly/--auto 구형 플래그를 모드 이름으로 바꾸는 함수
    """flags/flagProcessor.ts convertLegacyModeFlags — --readonly → plan, --auto → auto, 둘 다면 오류."""
    if readonly and auto:                                    # 함께 쓸 수 없음(원문 오류 글)
        raise ValueError("Cannot use both --readonly and --auto flags together")  # 둘 다 주면 오류
    if readonly:                                            # 읽기 전용이면
        return MODE_PLAN                                    # 계획 모드로
    if auto:                                                # auto 플래그면
        return MODE_AUTO                                    # 전부 허용 모드로
    return None                                              # 지정 없음
