# ncn/policy.py — [ncn E1] 관리자 정책 파일 + [ncn E2] 위험 판정을 권한 판정 위에 얹는 층(cn 원본 없음 — ncn 전용, 계획 6장 E1·E2)
# 정책 파일 위치: Windows %ProgramData%\ncn\policy.yaml, 그 밖 /etc/ncn/policy.yaml. 시험용으로 환경변수 NCN_POLICY_FILE
#   (단, 시스템 정책 파일이 있으면 NCN_POLICY_FILE 은 무시 — 사용자가 환경변수로 관리자 정책을 바꿔 끼우지 못하게)
# 정책 파일 형식(permissions.yaml 과 같은 규칙 문법 + 추가 키):
#   allow/ask/exclude: ["Write", "Bash(git push*)", "mcp_*"]   — 우선순위 맨 위(사용자 permissions.yaml·--allow·--auto·plan/auto 모드보다 먼저)
#   force_ask: {paths: [...], commands: [...]}                 — E2 형식 위험 규칙, --auto·auto 모드·NCN_CN_COMPAT 에서도 ask
#   risk: {paths: [...], commands: [...]}                      — E2 규칙 추가(사용자 규칙과 같이 --auto 에서는 꺼짐)
#   deny_modes: [auto]                                         — 쓸 수 없는 모드(auto·plan). --auto 로 시작하면 실행 거부, 모드 전환에서는 건너뜀
#   secrets: {enforce: true, env_patterns: [...], block_files: [...]}   — E7(ncn/security/secrets.py 가 읽음)
#   예약(이름만 — 다른 기능이 읽음): max_iterations(E5 반복 상한), audit: {server: ...}(E6 감사 기록 전송)
# 정책 파일이 깨졌으면(읽기 실패·YAML 오류·형식 오류) ncn 실행 거부(PolicyError) — 이유는 load_admin_policy 주석.
from __future__ import annotations                           # 타입 주석 지연 평가

import logging                                               # 경고(stdout 금지 — ACP)
import os                                                    # 환경변수·경로
import pathlib                                               # 경로
import sys                                                   # 실행 OS
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Dict, List, Mapping, Optional, Tuple   # 타입

from ncn.permissions.policies import ALLOW, ASK, EXCLUDE, MODE_AUTO, MODE_NORMAL, PERMISSION_MODES, ToolPermissionPolicy   # 정책 형식·모드
from ncn.permissions.risk import RiskRules, command_segments, parse_risk_rules   # E2 규칙

log = logging.getLogger("ncn.policy")                        # 로거

POLICY_ENV = "NCN_POLICY_FILE"                               # 시험용 정책 파일 경로 환경변수
POLICY_FILE_NAME = "policy.yaml"                             # 정책 파일 이름
RULE_KEYS = ("allow", "ask", "exclude")                      # permissions.yaml 과 같은 규칙 키
KNOWN_KEYS = RULE_KEYS + ("force_ask", "risk", "deny_modes", "secrets")   # 이 파일이 해석하는 키
RESERVED_KEYS = ("max_iterations", "audit")                  # 예약 키(E5·E6 가 읽음 — 여기서는 원래 값 보관만)
# 관리자 정책 규칙의 인자 이름 — 실제 도구 스키마 이름(cn permissions.yaml 표는 Read/Write 가 file_path 라 안 맞음: cn 버그 후보 S9)
POLICY_ARG_NAMES = {                                         # 도구 → 대표 인자
    "Write": "filepath",                                     # write_file.py
    "Read": "filepath",                                      # read_file.py
    "Edit": "file_path",                                     # edit.py
    "MultiEdit": "file_path",                                # multi_edit.py
    "List": "dirpath",                                       # list_files.py
    "Search": "pattern",                                     # search.py
    "Bash": "command",                                       # bash.py
    "Fetch": "url",                                          # fetch.py
}                                                            # 표 끝


class PolicyError(Exception):                                # 정책 파일이 깨졌거나 정책이 금지한 동작
    """관리자 정책 오류 — message 는 사용자에게 그대로 보여 줄 글."""

    def __init__(self, message: str):                       # 오류 글을 받는 생성자
        super().__init__(message)                           # 예외 글
        self.message = message                              # 화면·stderr 에 쓸 글


class PolicyModeError(PolicyError, ValueError):              # 정책이 금지한 모드로 바꾸려 함(ValueError 로도 잡힘 — set_mode 기존 오류와 같은 종류)
    """deny_modes 에 있는 모드."""


def system_policy_path(environ: Optional[Mapping[str, str]] = None) -> pathlib.Path:   # 시스템 정책 파일 위치
    """Windows: %ProgramData%\\ncn\\policy.yaml(없으면 C:\\ProgramData), 그 밖: /etc/ncn/policy.yaml."""
    env = os.environ if environ is None else environ         # 환경변수
    if sys.platform == "win32":                              # Windows
        base = env.get("ProgramData") or env.get("PROGRAMDATA") or r"C:\ProgramData"   # 모든 사용자 공용 폴더
        return pathlib.Path(base) / "ncn" / POLICY_FILE_NAME   # %ProgramData%\ncn\policy.yaml
    return pathlib.Path("/etc/ncn") / POLICY_FILE_NAME       # /etc/ncn/policy.yaml


def policy_path(environ: Optional[Mapping[str, str]] = None) -> Optional[pathlib.Path]:   # 쓸 정책 파일(없으면 None)
    """시스템 정책 파일이 있으면 그것(NCN_POLICY_FILE 무시), 없으면 NCN_POLICY_FILE(시험용), 둘 다 없으면 None."""
    env = os.environ if environ is None else environ         # 환경변수
    sys_path = system_policy_path(env)                       # 시스템 위치
    if os.path.lexists(sys_path):                            # 있으면(읽을 수 없는 파일·깨진 링크도 '있음' — 아래에서 깨짐으로 처리)
        if env.get(POLICY_ENV):                              # 시험용 경로도 지정됐으면
            log.warning("%s is ignored because the system policy file exists: %s", POLICY_ENV, sys_path)   # 무시 경고
        return sys_path                                      # 시스템 정책
    override = env.get(POLICY_ENV)                           # 시험용 경로
    if override:                                             # 지정됐으면
        return pathlib.Path(override)                        # 그 경로(없으면 깨짐으로 처리 — 지정했는데 못 읽음)
    return None                                              # 정책 없음


def _policy_rule(pattern: Any, permission: str, where: str) -> ToolPermissionPolicy:   # 규칙 글 하나 → 정책
    """permissions.yaml 과 같은 '도구(인자)' 문법. 인자 이름은 실제 도구 인자(POLICY_ARG_NAMES)."""
    from ncn.permissions.yaml_loader import parse_tool_pattern   # 같은 문법 해석기
    if not isinstance(pattern, str) or not pattern.strip():  # 글이 아니거나 빈 글
        raise ValueError(f"{where}: rule must be a non-empty string, got {pattern!r}")   # 형식 오류
    policy = parse_tool_pattern(pattern, permission)         # 해석(형식 오류면 예외)
    if policy.argument_matches:                              # 인자 조건이 있으면
        (value,) = policy.argument_matches.values()          # 괄호 안 패턴
        policy.argument_matches = {POLICY_ARG_NAMES.get(policy.tool, "pattern"): value}   # 실제 인자 이름으로
    return policy                                            # 정책 반환


@dataclass                                                  # 자료 클래스
class AdminPolicy:                                          # 해석된 관리자 정책
    """정책 파일 한 개의 내용. rules 는 exclude → ask → allow 순서(permissions.yaml 과 같게 엄격한 것 먼저)."""
    path: pathlib.Path                                       # 읽은 파일
    rules: List[ToolPermissionPolicy] = field(default_factory=list)   # 규칙(첫 일치)
    force_ask: RiskRules = field(default_factory=RiskRules)   # 모드 무관 ask
    risk: RiskRules = field(default_factory=RiskRules)       # E2 추가 규칙
    deny_modes: List[str] = field(default_factory=list)      # 금지 모드
    secrets: Dict[str, Any] = field(default_factory=dict)    # E7 설정(원래 값)
    raw: Dict[str, Any] = field(default_factory=dict)        # 파일 전체(예약 키를 다른 기능이 읽게)

    def reserved(self, key: str, default: Any = None) -> Any:   # 예약 키 값 읽기(E5·E6 용)
        """max_iterations·audit 등 이 파일이 해석하지 않는 키의 원래 값."""
        return self.raw.get(key, default)                    # 없으면 기본값

    def match(self, tool_name: str, args: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[ToolPermissionPolicy]]:   # 정책 규칙 판정
        """첫 일치 규칙의 (권한, 규칙). 없으면 (None, None).
        Bash(패턴)의 exclude·ask 는 명령 전체뿐 아니라 조각(; && || | 로 나눈 것)마다도 비교한다
        ('cd x && git push' 도 'Bash(git push*)' 에 걸리게 — 더 엄격한 쪽만 넓힘, allow 는 명령 전체로만)."""
        from ncn.permissions.matcher import matches_arguments, matches_tool_pattern   # cn 일치 규칙
        a = args if isinstance(args, dict) else {}           # 인자(없으면 {})
        for rule in self.rules:                              # 순서대로
            if not matches_tool_pattern(tool_name, rule.tool, a):   # 도구 패턴이 안 맞으면
                continue                                    # 다음 규칙
            if matches_arguments(a, rule.argument_matches):  # 인자 조건까지 맞으면
                return rule.permission, rule                # 첫 일치
            cmd = a.get("command")                           # Bash 명령
            if (rule.permission in (ASK, EXCLUDE) and tool_name == "Bash" and isinstance(cmd, str)   # 엄격한 Bash 규칙
                    and rule.argument_matches and "command" in rule.argument_matches):   # 명령 패턴이 있으면
                for seg in _raw_segments(cmd):               # 조각마다
                    if matches_arguments({**a, "command": seg}, rule.argument_matches):   # 조각이 맞으면
                        return rule.permission, rule        # 걸림
        return None, None                                    # 일치 없음


def _raw_segments(command: str) -> List[str]:               # 원래 대소문자를 살린 명령 조각
    """command_segments 와 같은 나눔이지만 소문자로 바꾸지 않는다(permissions.yaml 글롭은 대소문자 구분)."""
    import re                                                # 정규식
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"&&|\|\||[;|&\r\n]", command)]   # 나누고 공백 정리
    return [p for p in parts if p]                           # 빈 조각 제외


def parse_admin_policy(data: Any, path: pathlib.Path) -> AdminPolicy:   # YAML 값 → 정책(형식 오류는 ValueError)
    """정책 파일 내용 해석. 아는 키의 형식이 틀리면 ValueError(→ 실행 거부). 모르는 키는 경고만(나중 판의 키와 호환)."""
    pol = AdminPolicy(path=path)                             # 빈 정책
    if data is None:                                         # 빈 파일
        return pol                                          # 규칙 없음(정책 없음과 같음)
    if not isinstance(data, dict):                           # 최상위가 사전이 아니면
        raise ValueError("top level must be a mapping")      # 형식 오류
    pol.raw = dict(data)                                     # 원래 값 보관
    for key in data:                                         # 키마다
        if key not in KNOWN_KEYS and key not in RESERVED_KEYS:   # 모르는 키
            log.warning("policy.yaml: unknown key %r ignored (%s)", key, path)   # 경고만
    for key, permission in (("exclude", EXCLUDE), ("ask", ASK), ("allow", ALLOW)):   # 엄격한 것 먼저
        items = data.get(key)                                # 목록
        if items is None:                                    # 없음
            continue                                        # 다음
        if not isinstance(items, list):                      # 목록이 아니면
            raise ValueError(f"{key} must be a list")        # 형식 오류
        for item in items:                                   # 항목마다
            pol.rules.append(_policy_rule(item, permission, key))   # 정책 추가
    pol.force_ask = parse_risk_rules(data.get("force_ask"), where="force_ask", strict=True)   # 모드 무관 ask
    pol.risk = parse_risk_rules(data.get("risk"), where="risk", strict=True)   # E2 추가 규칙
    modes = data.get("deny_modes")                           # 금지 모드
    if modes is not None:                                    # 있으면
        if not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):   # 글 목록이 아니면
            raise ValueError("deny_modes must be a list of mode names")   # 형식 오류
        for m in modes:                                      # 모드마다
            if m not in PERMISSION_MODES or m == MODE_NORMAL:   # 모르는 모드·normal(금지하면 쓸 모드가 없을 수 있음)
                raise ValueError(f"deny_modes: invalid mode {m!r} (allowed: plan, auto)")   # 형식 오류
        pol.deny_modes = list(modes)                         # 저장
    sec = data.get("secrets")                                # E7 설정
    if sec is not None:                                      # 있으면
        if not isinstance(sec, dict):                        # 사전이 아니면
            raise ValueError("secrets must be a mapping")    # 형식 오류
        for k in ("env_patterns", "env_allow", "block_files"):   # 목록 키
            v = sec.get(k)                                   # 값
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):   # 글 목록이 아니면
                raise ValueError(f"secrets.{k} must be a list of strings")   # 형식 오류
        if "enforce" in sec and not isinstance(sec["enforce"], bool):   # 참거짓이 아니면
            raise ValueError("secrets.enforce must be true or false")   # 형식 오류
        pol.secrets = dict(sec)                              # 저장
    return pol                                               # 정책 반환


def load_admin_policy(environ: Optional[Mapping[str, str]] = None) -> Optional[AdminPolicy]:   # 정책 파일 읽기
    """정책 파일이 없으면 None(cn과 같은 동작). 있는데 못 읽거나 깨졌으면 PolicyError.
    깨졌을 때 '실행 거부'를 고른 이유: 파일이 무엇을 막으려 했는지(exclude 한 MCP 도구·Fetch, deny_modes 등) 알 수 없다.
    '모든 쓰기·Bash 를 ask' 로 대신하면 Read·Fetch·MCP 같은 다른 도구의 금지가 빠지고, 헤드리스에선 ask=거부라
    조용히 다른 동작이 된다. 관리자가 고칠 때까지 멈추는 쪽이 어느 경우에도 정책보다 느슨해지지 않는다."""
    path = policy_path(environ)                              # 쓸 파일
    if path is None:                                         # 정책 없음
        return None                                         # cn과 같음
    try:                                                    # 읽기·해석
        import yaml                                         # PyYAML
        text = path.read_bytes().decode("utf-8-sig")         # BOM 있는 UTF-8(Windows 메모장 저장)도 읽음
        data = yaml.safe_load(text)                          # YAML 해석
        return parse_admin_policy(data, path)                # 형식 해석
    except Exception as e:                                   # 못 읽음·문법 오류·형식 오류
        raise PolicyError(f"Administrator policy file is invalid or unreadable: {path} ({e}). "   # 실행 거부 글
                          "ncn will not run until the administrator fixes it.") from e   # (글 이어짐)


# ─────────────────────────────────────────────────────────────────────────────
# 권한 판정 위에 얹는 층 — PermissionService·SubagentPermissions 가 같이 쓴다
# ─────────────────────────────────────────────────────────────────────────────


def _is_specific(policy: Optional[ToolPermissionPolicy]) -> bool:   # 인자까지 지정한 규칙인지
    """'Bash(git push*)'(permissions.yaml 인자 조건 또는 --allow 패턴 글)처럼 인자를 지정한 규칙이면 참 — 사용자가 그 호출을 콕 집어 허용."""
    return policy is not None and (bool(policy.argument_matches) or "(" in (policy.tool or ""))   # 인자 조건 또는 'Bash(…)' 패턴


def is_default_policy(policy: Optional[ToolPermissionPolicy]) -> bool:   # 기본 정책표에서 온 규칙인지
    """일치 규칙이 없거나(기본 ask) 기본 정책표(get_default_tool_policies) 출신이면 참 — resolve_permission_precedence 가 표시한다."""
    return policy is None or bool(getattr(policy, "_ncn_default", False))   # 표시 확인


@dataclass                                                  # 자료 클래스
class PermissionLayer:                                      # cn 판정 결과에 E1·E2 를 얹는 것
    """admin: 관리자 정책(None 이면 E1 없음), user_risk: ncn.yaml 위험 규칙(기본 규칙 포함 여부 포함), risk_enabled: E2 사용 여부."""
    admin: Optional[AdminPolicy] = None                      # 관리자 정책
    user_risk: RiskRules = field(default_factory=lambda: RiskRules(use_defaults=True))   # 사용자 E2 규칙(기본 규칙 켬)
    risk_enabled: bool = True                                # E2 사용(ncn.yaml risk.enabled: false·NCN_CN_COMPAT=1 이면 끔)

    def mode_allowed(self, mode: str) -> bool:               # 정책이 그 모드를 허용하는지
        """deny_modes 에 없으면 참."""
        return self.admin is None or mode not in self.admin.deny_modes   # 정책 없거나 금지 목록에 없음

    def apply(self, tool_name: str, args: Optional[Dict[str, Any]], permission: str,   # 최종 판정
              matched: Optional[ToolPermissionPolicy], mode: str) -> str:   # (인자 이어짐)
        """apply_with_source 의 판정만 돌려준다(기존 호출부용)."""
        return self.apply_with_source(tool_name, args, permission, matched, mode)[0]   # 판정만

    def apply_with_source(self, tool_name: str, args: Optional[Dict[str, Any]], permission: str,   # 최종 판정 + 출처
                          matched: Optional[ToolPermissionPolicy], mode: str) -> Tuple[str, Optional[str]]:   # (판정, "E1"|"E2"|None)
        """cn 판정(permission, matched — 사용자 CLI·yaml·기본표·모드 정책의 첫 일치) 위에:
        ① E1 정책 규칙: exclude → exclude / ask → 사용자가 exclude 면 exclude, 아니면 ask /
           allow → 사용자 판정이 기본표의 ask 일 때만 allow(사용자가 직접 정한 ask·exclude·plan 금지는 그대로 — 더 엄격한 쪽)
        ② E1 force_ask: 걸리면 ask(exclude 는 그대로) — 모든 모드
        ③ E2 위험 판정: allow 인데 위험하면 ask — auto 모드 제외, 사용자가 그 호출을 콕 집어 허용한 규칙이면 제외."""
        a = args if isinstance(args, dict) else {}           # 인자
        perm = permission                                    # 시작은 cn 판정
        pol_perm, pol_rule = (self.admin.match(tool_name, a) if self.admin is not None else (None, None))   # ① 정책 규칙
        if pol_perm == EXCLUDE:                              # 정책 금지
            return EXCLUDE, "E1"                             # 무엇으로도 못 바꿈(출처: 관리자 정책)
        src: Optional[str] = None                            # 출처(None = cn 판정 그대로)
        if pol_perm == ASK:                                  # 정책 묻기
            if perm != EXCLUDE:                              # 사용자 금지가 아니면
                perm, src = ASK, "E1"                        # 관리자 정책이 ask 로 정함
        elif pol_perm == ALLOW and perm == ASK and is_default_policy(matched):   # 정책 허용 + 기본표 ask
            perm, src = ALLOW, "E1"                          # 관리자가 미리 승인
        if perm == EXCLUDE:                                  # 이미 금지
            return EXCLUDE, src                              # 더 볼 것 없음
        if self.admin is not None and self.admin.force_ask.match(tool_name, a):   # ② 모드 무관 ask
            return ASK, "E1"                                 # 묻기(헤드리스면 정책 거부)
        if perm != ALLOW:                                    # ask 는 그대로
            return perm, src                                 # 반환
        if not self.risk_enabled or mode == MODE_AUTO:       # ③ E2 꺼짐 또는 auto(사용자가 전부 허용)
            return perm, src                                 # allow 그대로
        if _is_specific(matched) or (pol_perm == ALLOW and _is_specific(pol_rule)):   # 그 호출을 콕 집어 허용한 규칙
            return perm, src                                 # allow 그대로(예: '다시 묻지 않기'로 저장한 Bash(git push origin main))
        reason = self.user_risk.match(tool_name, a)          # 사용자(기본 포함) 규칙
        if reason is None and self.admin is not None:        # 없으면 관리자 추가 규칙
            reason = self.admin.risk.match(tool_name, a)     # 관리자 risk
        if reason is not None:                               # 위험
            log.debug("Risky call raised to ask: %s %s", tool_name, reason)   # 로그
            return ASK, "E2"                                 # 묻기로 올림(출처: 위험 판정)
        return perm, src                                     # allow 그대로


def build_permission_layer(global_dir: Any, environ: Optional[Mapping[str, str]] = None) -> PermissionLayer:   # 층 만들기
    """관리자 정책(깨졌으면 PolicyError) + ~/.continue/ncn.yaml 의 risk 설정.
    ncn.yaml: risk: {enabled: true, defaults: true, paths: [...], commands: [...]} — enabled:false 면 E2 끔, defaults:false 면 기본 규칙만 끔.
    NCN_CN_COMPAT=1 이면 E2(사용자 기본 규칙)는 끈다. E1(관리자 정책)은 그대로 — 사용자가 환경변수로 관리자 정책을 끄지 못하게."""
    from ncn.compat import cn_compat                         # cn 원래 동작 스위치
    admin = load_admin_policy(environ)                       # E1(깨졌으면 예외 → 실행 거부)
    raw = _ncn_yaml_raw(global_dir)                          # ncn.yaml 원래 값
    risk_cfg = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}   # risk 절
    if raw.get("risk") is not None and not isinstance(raw.get("risk"), dict):   # 형식 오류
        log.warning("ncn.yaml: risk must be a mapping — ignored")   # 경고
    user = parse_risk_rules(risk_cfg, where="ncn.yaml risk", strict=False)   # 추가 규칙(틀린 항목은 무시)
    user.use_defaults = risk_cfg.get("defaults", True) is not False   # 기본 규칙 사용
    enabled = risk_cfg.get("enabled", True) is not False and not cn_compat()   # E2 사용 여부
    return PermissionLayer(admin=admin, user_risk=user, risk_enabled=enabled)   # 층 반환


def _ncn_yaml_raw(global_dir: Any) -> Dict[str, Any]:       # ncn.yaml 원래 값(사전)
    """ncn.subagent.load_ncn_settings(같은 파일·같은 읽기 함수)의 raw. 없거나 못 읽으면 {}."""
    try:                                                    # 읽기 실패는 기본값
        from ncn.subagent import load_ncn_settings          # ncn.yaml 읽기 함수
        raw = getattr(load_ncn_settings(global_dir), "raw", None)   # 원래 값
        return raw if isinstance(raw, dict) else {}          # 사전만
    except Exception as e:                                   # 실패
        log.warning("ncn.yaml: cannot read risk settings — %s", e)   # 경고
        return {}                                            # 기본값


def current_admin_policy_or_none(environ: Optional[Mapping[str, str]] = None) -> Optional[AdminPolicy]:   # 예외 없이 정책 읽기
    """다른 기능(E5·E6·E7)이 예약 키를 읽을 때 — 깨졌으면 None(실행 거부는 권한 서비스가 이미 한다)."""
    try:                                                    # 읽기
        return load_admin_policy(environ)                   # 정책
    except PolicyError:                                     # 깨짐
        return None                                         # 없음으로


__all__ = ["AdminPolicy", "PermissionLayer", "PolicyError", "PolicyModeError", "POLICY_ENV", "build_permission_layer",   # 공개 이름
           "command_segments", "current_admin_policy_or_none", "is_default_policy", "load_admin_policy",   # (이어짐)
           "parse_admin_policy", "policy_path", "system_policy_path"]   # (이어짐)
