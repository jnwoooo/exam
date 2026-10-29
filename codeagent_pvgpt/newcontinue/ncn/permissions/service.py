# ncn/permissions/service.py — 도구 권한 서비스. cn extensions/cli/src/services/ToolPermissionService.ts + permissions/precedenceResolver.ts 를 옮긴 것
# 우선순위(일반 모드): CLI 플래그(--allow/--ask/--exclude) > ~/.continue/permissions.yaml > 기본 정책표. plan/auto 모드는 모드 정책만(완전 대체)
# 사용 순서(cn stream/streamChatResponse.helpers.ts · handleToolCalls.ts 와 같게):
#   - 도구 목록 만들기: base_permission(name) 이 allow 이거나 (ask 이고 대화형)인 도구만 LLM에 보낸다(getRequestTools, 인자 {}로 판정)
#   - 실행 직전: check(tool, args) — 첫 일치 정책 → 그 도구의 evaluate_tool_call_policy(Bash 위험 판정)가 "disabled"면 exclude
#     allow → 바로 실행 / ask → 헤드리스면 정책 거부, 대화형이면 사용자에게 묻기 / exclude → 정책 거부("Command blocked by security policy")
#   - 승인 "계속 + 다시 묻지 않기": add_always_allow(name, args) — permissions.yaml allow에 규칙 추가 후 다시 읽기
from __future__ import annotations                           # 타입 주석 지연 평가

import logging                                               # 로그(stdout 금지 — ACP)
import pathlib                                               # 경로
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence   # 타입

from ncn.permissions.matcher import check_tool_permission, find_matching_policy   # 판정
from ncn.permissions.policies import (AUTO_MODE_POLICIES, MODE_AUTO, MODE_NORMAL, MODE_PLAN, PERMISSION_MODES,
                                      PLAN_MODE_POLICIES, ToolPermissionPolicy, command_line_flags_to_policies,
                                      convert_legacy_mode_flags, get_default_tool_policies)   # 정책표
from ncn.permissions.yaml_loader import (add_policy_to_yaml, ensure_permissions_yaml_exists, generate_policy_rule,
                                         load_permissions_yaml, permissions_yaml_path, yaml_config_to_policies)   # 파일
from ncn.security.terminal_security import evaluate_terminal_command_security   # Bash 위험 판정

if TYPE_CHECKING:                                            # 타입 검사 때만(순환 import 방지)
    from ncn.tools.base import Tool

logger = logging.getLogger("ncn.permissions")                # 로거


def _builtin_bash_policy(base_policy: str, args: Dict) -> str:
    """runTerminalCommand.ts evaluateToolCallPolicy — evaluateTerminalCommandSecurity(basePolicy, parsedArgs.command)."""
    return evaluate_terminal_command_security(base_policy, args.get("command") if isinstance(args, dict) else None)


def resolve_permission_precedence(*, global_dir: pathlib.Path, allow: Optional[Sequence[str]] = None,
                                  ask: Optional[Sequence[str]] = None, exclude: Optional[Sequence[str]] = None,
                                  is_headless: bool = False, personal_settings: bool = True,
                                  use_defaults: bool = True) -> List[ToolPermissionPolicy]:
    """precedenceResolver.ts resolvePermissionPrecedence — CLI 플래그 → permissions.yaml → 기본 정책 순으로 이어 붙인다(첫 일치가 이김)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    policies.extend(command_line_flags_to_policies(list(allow) if allow else None, list(ask) if ask else None,
                                                   list(exclude) if exclude else None))   # 1) CLI 플래그(가장 높음)
    if personal_settings:                                    # 2) ~/.continue/permissions.yaml
        yaml_config = load_permissions_yaml(permissions_yaml_path(global_dir))
        if yaml_config:
            policies.extend(yaml_config_to_policies(yaml_config))   # 패턴 오류면 예외(원문처럼 잡지 않음)
    if use_defaults:                                         # 3) 기본 정책(가장 낮음)
        policies.extend(get_default_tool_policies(is_headless))
    return policies


class PermissionService:
    """ToolPermissionService — 정책 목록 하나를 진실의 원천으로 두고 모드 전환·다시 읽기를 처리한다."""

    def __init__(self, *, mode: str = MODE_NORMAL, allow: Sequence[str] = (), ask: Sequence[str] = (),
                 exclude: Sequence[str] = (), headless: bool, global_dir: pathlib.Path,
                 readonly: bool = False, auto: bool = False):
        legacy = convert_legacy_mode_flags(readonly, auto)   # --readonly → plan, --auto → auto(둘 다면 ValueError)
        mode = legacy or mode or MODE_NORMAL                 # 플래그가 있으면 그 모드
        if mode not in PERMISSION_MODES:                     # 잘못된 모드
            raise ValueError(f"Unknown permission mode: {mode}")
        self.mode: str = mode                                # 현재 모드(currentMode)
        self.headless: bool = headless                       # 헤드리스 여부(isHeadless)
        self.global_dir = pathlib.Path(global_dir)           # ~/.continue (CONTINUE_GLOBAL_DIR)
        self.yaml_path = permissions_yaml_path(self.global_dir)   # permissions.yaml 위치
        self._allow = list(allow)                            # CLI --allow
        self._ask = list(ask)                                # CLI --ask
        self._exclude = list(exclude)                        # CLI --exclude
        self.policies: List[ToolPermissionPolicy] = []       # 현재 정책 목록(permissions.policies)
        self._mode_policy_count = 0                          # 모드 정책 수(modePolicyCount)
        self._original_policies: Optional[List[ToolPermissionPolicy]] = None   # normal을 처음 떠날 때 저장한 정책
        ensure_permissions_yaml_exists(self.yaml_path)       # doInitialize: 기본 파일 만들기
        self._initialize()                                   # initializeSync

    # ───────── 초기화·모드 ─────────
    def _generate_mode_policies(self) -> List[ToolPermissionPolicy]:
        """generateModePolicies — plan/auto는 모드 정책, normal은 없음."""
        if self.mode == MODE_PLAN:
            return list(PLAN_MODE_POLICIES)
        if self.mode == MODE_AUTO:
            return list(AUTO_MODE_POLICIES)
        return []

    def _initialize(self) -> None:
        """initializeSync — (agent 파일 정책은 미지원) plan/auto면 모드 정책만, normal이면 우선순위대로 합친다."""
        mode_policies = self._generate_mode_policies()       # 모드 정책
        if self.mode in (MODE_PLAN, MODE_AUTO):              # 완전 대체(사용자 설정 무시)
            all_policies = list(mode_policies)
        else:                                                # normal: CLI + yaml + 기본
            all_policies = resolve_permission_precedence(global_dir=self.global_dir, allow=self._allow,
                                                         ask=self._ask, exclude=self._exclude,
                                                         is_headless=self.headless)
        self.policies = all_policies                         # 저장
        self._mode_policy_count = len(mode_policies)         # 모드 정책 수

    def set_mode(self, mode: str) -> None:
        """switchMode — normal을 처음 떠날 때 정책을 저장하고, normal로 돌아오면 그대로 복원한다."""
        if mode not in PERMISSION_MODES:                     # 잘못된 모드
            raise ValueError(f"Unknown permission mode: {mode}")
        current = self.mode                                  # 이전 모드
        logger.debug("Switching from mode '%s' to '%s'", current, mode)
        if current == MODE_NORMAL and mode != MODE_NORMAL and self._original_policies is None:   # 처음 떠날 때만 저장
            self._original_policies = list(self.policies)
        self.mode = mode                                     # 모드 바꿈
        mode_policies = self._generate_mode_policies()       # 새 모드 정책
        if mode in (MODE_PLAN, MODE_AUTO):                   # 완전 대체
            all_policies = list(mode_policies)
        elif self._original_policies is not None:            # normal: 저장한 정책 복원(저장본은 지우지 않음 — 원문)
            all_policies = list(self._original_policies)
        else:                                                # 저장본 없음: 기존 목록에서 모드 정책 수만큼 뺀 나머지
            prev = self._mode_policy_count or 0
            all_policies = list(self.policies[prev:]) if len(self.policies) > prev else []
        self.policies = all_policies
        self._mode_policy_count = len(mode_policies)

    def cycle_mode(self) -> str:
        """UserInput.tsx cycleModes — normal → plan → auto → normal (Shift+Tab)."""
        idx = PERMISSION_MODES.index(self.mode) if self.mode in PERMISSION_MODES else -1   # 현재 위치
        nxt = PERMISSION_MODES[(idx + 1) % len(PERMISSION_MODES)]   # 다음 모드
        self.set_mode(nxt)
        return nxt

    def update_permissions(self, new_policies: List[ToolPermissionPolicy]) -> None:
        """updatePermissions — 정책 전체 교체."""
        self.policies = list(new_policies)
        self._mode_policy_count = 0

    def reload_permissions(self) -> None:
        """reloadPermissions — normal 모드에서만 파일에서 다시 읽는다.
        원문 그대로 CLI 플래그와 헤드리스 여부를 넘기지 않는다(→ 기본표는 TUI 기준, --allow 등은 사라짐: cn 버그 후보)."""
        if self.mode != MODE_NORMAL:                         # 다른 모드는 완전 대체라 건너뜀
            logger.debug("Skipping permission reload in non-normal mode")
            return
        self.policies = resolve_permission_precedence(global_dir=self.global_dir)   # personalSettings + defaults(isHeadless 미지정 = False)
        self._mode_policy_count = 0

    # ───────── 판정 ─────────
    def base_permission(self, tool_name: str, args: Optional[Dict] = None) -> str:
        """정적 정책의 첫 일치 권한(없으면 "ask"). 도구 목록 필터용(cn getRequestTools는 인자 {}로 판정).
        인자 {}면 Bash 동적 평가는 base를 그대로 돌려주므로 결과가 checkToolPermission과 같다."""
        permission, _ = find_matching_policy(tool_name, args if args is not None else {}, self.policies)
        return permission

    def is_tool_offered(self, tool_name: str) -> bool:
        """getRequestTools 조건 — allow 이거나 (ask 이고 헤드리스가 아님)이면 LLM에 보낸다."""
        p = self.base_permission(tool_name, {})
        return p == "allow" or (p == "ask" and not self.headless)

    def check(self, tool: "Tool", args: Dict) -> str:
        """실행 시점 최종 판정(checkToolPermission) — "allow" | "ask" | "exclude".
        cn은 ALL_BUILT_IN_TOOLS에서 "이름으로" 찾은 내장 도구의 evaluateToolCallPolicy를 쓴다(그런 내장 도구는 Bash 하나).
        그래서 이름이 Bash면 MCP 도구여도 Bash 위험 판정을 받는다 — 원문 그대로."""
        evaluate = tool.evaluate_tool_call_policy            # 도구가 가진 동적 평가(내장 Bash)
        if evaluate is None and tool.name == "Bash":         # 이름만 Bash인 도구 → 내장 Bash 평가(runTerminalCommand.ts 연결과 같게)
            evaluate = _builtin_bash_policy
        permission, _ = check_tool_permission(tool.name, args if args is not None else {}, self.policies, evaluate)
        return permission

    def add_always_allow(self, tool_name: str, args: Dict) -> None:
        """useChat.ts handleToolPermissionResponse(createPolicy) — 규칙 생성 → permissions.yaml allow에 추가 → 다시 읽기.
        실패해도 승인은 그대로 진행(원문: 오류만 기록)."""
        try:
            rule = generate_policy_rule(tool_name, args)     # 예: "Bash(npm*)", "Write"
            add_policy_to_yaml(self.yaml_path, rule)         # 파일에 추가
            logger.debug("Policy created: %s", rule)
            self.reload_permissions()                        # 재시작 없이 반영
        except Exception as error:                           # 실패는 기록만
            logger.error("Failed to create policy or reload permissions: %s", error)
