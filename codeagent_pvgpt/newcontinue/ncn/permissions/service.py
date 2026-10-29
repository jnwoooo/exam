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
from ncn.permissions.policies import (AUTO_MODE_POLICIES, MODE_AUTO, MODE_NORMAL, MODE_PLAN, PERMISSION_MODES,  # 정책 형식·모드·모드별 정책표 가져오기
                                      PLAN_MODE_POLICIES, ToolPermissionPolicy, command_line_flags_to_policies,  # plan·auto 정책표와 CLI 플래그 변환
                                      convert_legacy_mode_flags, get_default_tool_policies)   # 정책표
from ncn.permissions.yaml_loader import (add_policy_to_yaml, ensure_permissions_yaml_exists, generate_policy_rule,  # yaml 읽기·쓰기 함수 가져오기
                                         load_permissions_yaml, permissions_yaml_path, yaml_config_to_policies)   # 파일
from ncn.security.terminal_security import evaluate_terminal_command_security   # Bash 위험 판정

if TYPE_CHECKING:                                            # 타입 검사 때만(순환 import 방지)
    from ncn.tools.base import Tool                         # 도구 타입(순환 import 피하려 지연)

logger = logging.getLogger("ncn.permissions")                # 로거


def _builtin_bash_policy(base_policy: str, args: Dict) -> str:  # Bash 도구의 위험 명령 동적 평가 함수
    """runTerminalCommand.ts evaluateToolCallPolicy — evaluateTerminalCommandSecurity(basePolicy, parsedArgs.command)."""
    return evaluate_terminal_command_security(base_policy, args.get("command") if isinstance(args, dict) else None)  # command 인자만 꺼내 보안 판정에 넘김


def resolve_permission_precedence(*, global_dir: pathlib.Path, allow: Optional[Sequence[str]] = None,  # CLI 플래그 → yaml → 기본표 순으로 정책을 합치는 함수
                                  ask: Optional[Sequence[str]] = None, exclude: Optional[Sequence[str]] = None,  # CLI의 ask/exclude 목록(선택)
                                  is_headless: bool = False, personal_settings: bool = True,  # 헤드리스 여부와 yaml 사용 여부
                                  use_defaults: bool = True) -> List[ToolPermissionPolicy]:  # 기본표 사용 여부와 반환형
    """precedenceResolver.ts resolvePermissionPrecedence — CLI 플래그 → permissions.yaml → 기본 정책 순으로 이어 붙인다(첫 일치가 이김)."""
    policies: List[ToolPermissionPolicy] = []                # 결과
    policies.extend(command_line_flags_to_policies(list(allow) if allow else None, list(ask) if ask else None,  # CLI 플래그 정책을 맨 앞에 붙임(빈 목록은 None)
                                                   list(exclude) if exclude else None))   # 1) CLI 플래그(가장 높음)
    if personal_settings:                                   # yaml 설정 사용 여부 — 2) ~/.continue/permissions.yaml
        yaml_config = load_permissions_yaml(permissions_yaml_path(global_dir))  # yaml 파일 읽기(실패면 None)
        if yaml_config:                                     # yaml이 유효할 때만
            policies.extend(yaml_config_to_policies(yaml_config))   # 패턴 오류면 예외(원문처럼 잡지 않음)
    if use_defaults:                                         # 3) 기본 정책(가장 낮음)
        policies.extend(get_default_tool_policies(is_headless))  # 기본 정책을 맨 뒤에 붙임
    return policies                                         # 합쳐진 정책 목록 반환


class PermissionService:                                    # 권한 서비스 — 정책 목록과 모드를 관리
    """ToolPermissionService — 정책 목록 하나를 진실의 원천으로 두고 모드 전환·다시 읽기를 처리한다."""

    def __init__(self, *, mode: str = MODE_NORMAL, allow: Sequence[str] = (), ask: Sequence[str] = (),  # 생성자: 모드와 CLI 플래그
                 exclude: Sequence[str] = (), headless: bool, global_dir: pathlib.Path,  # exclude·헤드리스·전역 폴더
                 readonly: bool = False, auto: bool = False):  # 구형 --readonly/--auto 플래그
        legacy = convert_legacy_mode_flags(readonly, auto)   # --readonly → plan, --auto → auto(둘 다면 ValueError)
        mode = legacy or mode or MODE_NORMAL                 # 플래그가 있으면 그 모드
        if mode not in PERMISSION_MODES:                     # 잘못된 모드
            raise ValueError(f"Unknown permission mode: {mode}")  # 알 수 없는 모드 오류
        self.mode: str = mode                                # 현재 모드(currentMode)
        self.headless: bool = headless                       # 헤드리스 여부(isHeadless)
        self.global_dir = pathlib.Path(global_dir)          # 전역 설정 폴더 경로 — ~/.continue (CONTINUE_GLOBAL_DIR)
        self.yaml_path = permissions_yaml_path(self.global_dir)   # permissions.yaml 위치
        self._allow = list(allow)                           # CLI 허용 목록 보관 — CLI --allow
        self._ask = list(ask)                               # CLI 묻기 목록 보관 — CLI --ask
        self._exclude = list(exclude)                       # CLI 금지 목록 보관 — CLI --exclude
        self.policies: List[ToolPermissionPolicy] = []       # 현재 정책 목록(permissions.policies)
        self._mode_policy_count = 0                          # 모드 정책 수(modePolicyCount)
        self._original_policies: Optional[List[ToolPermissionPolicy]] = None   # normal을 처음 떠날 때 저장한 정책
        ensure_permissions_yaml_exists(self.yaml_path)       # doInitialize: 기본 파일 만들기
        self._initialize()                                  # 정책 초기 구성 — initializeSync

    # ───────── 초기화·모드 ─────────
    def _generate_mode_policies(self) -> List[ToolPermissionPolicy]:  # 현재 모드에 맞는 모드 정책 생성
        """generateModePolicies — plan/auto는 모드 정책, normal은 없음."""
        if self.mode == MODE_PLAN:                          # plan이면
            return list(PLAN_MODE_POLICIES)                 # plan 정책표 복사본
        if self.mode == MODE_AUTO:                          # auto이면
            return list(AUTO_MODE_POLICIES)                 # auto 정책표 복사본
        return []                                           # normal은 모드 정책 없음

    def _initialize(self) -> None:                          # 정책 초기화(생성자에서 한 번 호출)
        """initializeSync — (agent 파일 정책은 미지원) plan/auto면 모드 정책만, normal이면 우선순위대로 합친다."""
        mode_policies = self._generate_mode_policies()       # 모드 정책
        if self.mode in (MODE_PLAN, MODE_AUTO):              # 완전 대체(사용자 설정 무시)
            all_policies = list(mode_policies)              # 모드 정책만 사용
        else:                                                # normal: CLI + yaml + 기본
            all_policies = resolve_permission_precedence(global_dir=self.global_dir, allow=self._allow,  # normal: 우선순위 규칙으로 합침
                                                         ask=self._ask, exclude=self._exclude,  # CLI ask/exclude 전달
                                                         is_headless=self.headless)  # 헤드리스 여부 전달
        self.policies = all_policies                         # 저장
        self._mode_policy_count = len(mode_policies)         # 모드 정책 수

    def set_mode(self, mode: str) -> None:                  # 모드 전환
        """switchMode — normal을 처음 떠날 때 정책을 저장하고, normal로 돌아오면 그대로 복원한다."""
        if mode not in PERMISSION_MODES:                     # 잘못된 모드
            raise ValueError(f"Unknown permission mode: {mode}")  # 알 수 없는 모드 오류
        current = self.mode                                  # 이전 모드
        logger.debug("Switching from mode '%s' to '%s'", current, mode)  # 전환 로그
        if current == MODE_NORMAL and mode != MODE_NORMAL and self._original_policies is None:   # 처음 떠날 때만 저장
            self._original_policies = list(self.policies)   # normal 정책 백업
        self.mode = mode                                     # 모드 바꿈
        mode_policies = self._generate_mode_policies()       # 새 모드 정책
        if mode in (MODE_PLAN, MODE_AUTO):                   # 완전 대체
            all_policies = list(mode_policies)              # 모드 정책만 사용
        elif self._original_policies is not None:            # normal: 저장한 정책 복원(저장본은 지우지 않음 — 원문)
            all_policies = list(self._original_policies)    # 백업한 normal 정책 복원
        else:                                                # 저장본 없음: 기존 목록에서 모드 정책 수만큼 뺀 나머지
            prev = self._mode_policy_count or 0             # 이전 모드 정책 개수
            all_policies = list(self.policies[prev:]) if len(self.policies) > prev else []  # 모드 정책 앞부분을 잘라냄
        self.policies = all_policies                        # 새 정책 목록 반영
        self._mode_policy_count = len(mode_policies)        # 새 모드 정책 수 기록

    def cycle_mode(self) -> str:                            # Shift+Tab 모드 순환
        """UserInput.tsx cycleModes — normal → plan → auto → normal (Shift+Tab)."""
        idx = PERMISSION_MODES.index(self.mode) if self.mode in PERMISSION_MODES else -1   # 현재 위치
        nxt = PERMISSION_MODES[(idx + 1) % len(PERMISSION_MODES)]   # 다음 모드
        self.set_mode(nxt)                                  # 다음 모드로 전환
        return nxt                                          # 새 모드 이름 반환

    def update_permissions(self, new_policies: List[ToolPermissionPolicy]) -> None:  # 정책 전체 교체
        """updatePermissions — 정책 전체 교체."""
        self.policies = list(new_policies)                  # 새 목록 복사해 저장
        self._mode_policy_count = 0                         # 모드 정책 수 초기화

    def reload_permissions(self) -> None:                   # permissions.yaml 다시 읽기
        """reloadPermissions — normal 모드에서만 파일에서 다시 읽는다.
        원문 그대로 CLI 플래그와 헤드리스 여부를 넘기지 않는다(→ 기본표는 TUI 기준, --allow 등은 사라짐: cn 버그 후보)."""
        if self.mode != MODE_NORMAL:                         # 다른 모드는 완전 대체라 건너뜀
            logger.debug("Skipping permission reload in non-normal mode")  # 건너뜀 로그
            return                                          # 다시 읽지 않고 종료
        self.policies = resolve_permission_precedence(global_dir=self.global_dir)   # personalSettings + defaults(isHeadless 미지정 = False)
        self._mode_policy_count = 0                         # 모드 정책 수 초기화

    # ───────── 판정 ─────────
    def base_permission(self, tool_name: str, args: Optional[Dict] = None) -> str:  # 도구의 정적 권한 조회
        """정적 정책의 첫 일치 권한(없으면 "ask"). 도구 목록 필터용(cn getRequestTools는 인자 {}로 판정).
        인자 {}면 Bash 동적 평가는 base를 그대로 돌려주므로 결과가 checkToolPermission과 같다."""
        permission, _ = find_matching_policy(tool_name, args if args is not None else {}, self.policies)  # 정책표에서 첫 일치를 찾음
        return permission                                   # 권한 문자열 반환

    def is_tool_offered(self, tool_name: str) -> bool:      # 모델에 도구를 보여줄지 판정
        """getRequestTools 조건 — allow 이거나 (ask 이고 헤드리스가 아님)이면 LLM에 보낸다."""
        p = self.base_permission(tool_name, {})             # 인자 없이 기본 권한 조회
        return p == "allow" or (p == "ask" and not self.headless)  # allow이거나 TUI에서 ask면 제공

    def check(self, tool: "Tool", args: Dict) -> str:       # 실행 직전 최종 권한 판정
        """실행 시점 최종 판정(checkToolPermission) — "allow" | "ask" | "exclude".
        cn은 ALL_BUILT_IN_TOOLS에서 "이름으로" 찾은 내장 도구의 evaluateToolCallPolicy를 쓴다(그런 내장 도구는 Bash 하나).
        그래서 이름이 Bash면 MCP 도구여도 Bash 위험 판정을 받는다 — 원문 그대로."""
        evaluate = tool.evaluate_tool_call_policy            # 도구가 가진 동적 평가(내장 Bash)
        if evaluate is None and tool.name == "Bash":         # 이름만 Bash인 도구 → 내장 Bash 평가(runTerminalCommand.ts 연결과 같게)
            evaluate = _builtin_bash_policy                 # 이름이 Bash면 내장 평가 사용
        permission, _ = check_tool_permission(tool.name, args if args is not None else {}, self.policies, evaluate)  # 정적 정책 + 동적 평가로 판정
        return permission                                   # allow|ask|exclude 반환

    def add_always_allow(self, tool_name: str, args: Dict) -> None:  # '항상 허용' 규칙을 yaml에 저장
        """useChat.ts handleToolPermissionResponse(createPolicy) — 규칙 생성 → permissions.yaml allow에 추가 → 다시 읽기.
        실패해도 승인은 그대로 진행(원문: 오류만 기록)."""
        try:                                                # 실패해도 승인 흐름은 계속
            rule = generate_policy_rule(tool_name, args)     # 예: "Bash(npm*)", "Write"
            add_policy_to_yaml(self.yaml_path, rule)         # 파일에 추가
            logger.debug("Policy created: %s", rule)        # 생성 로그
            self.reload_permissions()                        # 재시작 없이 반영
        except Exception as error:                           # 실패는 기록만
            logger.error("Failed to create policy or reload permissions: %s", error)  # 실패 로그
