# ncn/security/secrets.py — [ncn E7] 비밀 보호(cn 원본 없음 — ncn 전용, 계획 6장 E7)
# ① Bash·MCP stdio 자식 프로세스 환경에서 비밀 환경변수(이름 패턴)를 뺀다 — cn은 process.env 를 그대로 물려줌
#    (모델이 `env`·`echo $OPENAI_API_KEY`·`Get-ChildItem env:` 로 사내 LLM 키·토큰을 읽어 낼 수 있음)
# ② Read·Edit·Write 가 막는 민감 파일(cn core/indexing/ignore.ts 목록)에 키 파일 패턴을 더한다(ncn/security/ignore.py 가 부름)
# 설정(둘 다 선택): ~/.continue/ncn.yaml  secrets: {enabled: true, env_patterns: [...], env_allow: [...], block_files: [...]}
#                  관리자 policy.yaml      secrets: {enforce: true, env_patterns: [...], env_allow: [...], block_files: [...]}
#   enabled: false(사용자) 또는 NCN_CN_COMPAT=1 이면 끔 — 단 관리자 정책 enforce: true 면 끌 수 없다.
#   관리자 env_patterns·block_files 는 사용자 것과 합쳐진다. env_allow(예외)는 사용자 것만 관리자 enforce 가 없을 때 적용.
from __future__ import annotations                           # 타입 주석 지연 평가

import fnmatch                                               # 이름 글롭
import logging                                               # 로그(stdout 금지 — ACP)
import os                                                    # 환경변수
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Dict, List, Mapping, Optional        # 타입

log = logging.getLogger("ncn.secrets")                       # 로거

# 비밀 환경변수 이름 패턴(대소문자 무시 — Windows 환경변수는 대소문자 구분 없음)
DEFAULT_SECRET_ENV_PATTERNS = [                              # 기본 패턴
    "*_TOKEN",                                               # GITHUB_TOKEN, NPM_TOKEN, HF_TOKEN …
    "*_TOKEN_*",                                             # *_TOKEN_FILE 이 아닌 변형(예 AZURE_TOKEN_VALUE)
    "*_KEY",                                                 # *_API_KEY, AWS_SECRET_ACCESS_KEY …
    "*_KEY_ID",                                              # AWS_ACCESS_KEY_ID
    "*_SECRET",                                              # CLIENT_SECRET …
    "*_SECRET_*",                                            # *_SECRET_VALUE 등
    "*PASSWORD*",                                            # DB_PASSWORD, PGPASSWORD …
    "*PASSWD*",                                              # 줄임 표기
    "*_PAT",                                                 # 개인 접근 토큰(Azure DevOps 등)
    "*CONNECTION_STRING*",                                   # DB·스토리지 연결 문자열
    "*_CREDENTIALS",                                         # 자격 증명
    "CODEAGENT_TOKEN",                                       # 사내 코드에이전트 서버 토큰
    "OPENAI_API_KEY",                                        # OpenAI 호환 키(위 *_KEY 와 겹치지만 명시)
    "ANTHROPIC_API_KEY",                                     # 명시
    "CONTINUE_API_KEY",                                      # Continue 키
]                                                            # 목록 끝

# Read·Edit·Write 차단에 더하는 키 파일 패턴(gitignore 문법 — cn 목록에 없는 것만)
EXTRA_BLOCK_FILES = [                                        # 추가 민감 파일
    "id_rsa*",                                               # id_rsa_work 등 변형(cn은 id_rsa 정확히만)
    "id_dsa*",                                               # DSA 키 변형
    "id_ecdsa*",                                             # ECDSA 키 변형
    "id_ed25519*",                                           # ED25519 키 변형
    "*.p8",                                                  # PKCS8 개인 키
    "*.kdbx",                                                # KeePass 데이터베이스
    ".netrc",                                                # 자격 증명(posix)
    "_netrc",                                                # 자격 증명(Windows)
    ".git-credentials",                                      # git 저장 자격 증명
    ".npmrc",                                                # npm 토큰
    ".pypirc",                                               # PyPI 토큰
    "credentials",                                           # ~/.aws/credentials 등
    "credentials.json",                                      # 구글 등 자격 증명
    ".continue/.env",                                        # Continue 비밀 파일(.env* 로도 걸리지만 명시)
]                                                            # 목록 끝


@dataclass                                                  # 자료 클래스
class SecretSettings:                                       # 지금 적용할 E7 설정
    """enabled: 켬 여부, env_patterns: 뺄 이름 패턴, env_allow: 예외 이름 패턴, block_files: 더 막을 파일 패턴."""
    enabled: bool = True                                     # 켬
    env_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_SECRET_ENV_PATTERNS))   # 이름 패턴
    env_allow: List[str] = field(default_factory=list)       # 예외
    block_files: List[str] = field(default_factory=lambda: list(EXTRA_BLOCK_FILES))   # 파일 패턴


def _str_list(v: Any) -> List[str]:                         # 글 목록만 받아들임
    """글 목록이면 그대로, 아니면 빈 목록."""
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []   # 글만


def load_secret_settings(environ: Optional[Mapping[str, str]] = None) -> SecretSettings:   # 설정 읽기
    """ncn.yaml(전역 폴더)·관리자 정책 secrets 절을 합친다. 읽기 실패는 기본값(켬)."""
    from ncn.compat import cn_compat                         # cn 원래 동작 스위치
    env = os.environ if environ is None else environ         # 환경변수
    user: Dict[str, Any] = {}                                # 사용자 secrets 절
    try:                                                    # ncn.yaml 읽기
        from ncn.config import paths as cpaths               # 전역 폴더
        from ncn.subagent import load_ncn_settings           # 같은 읽기 함수
        raw = getattr(load_ncn_settings(cpaths.global_dir(env)), "raw", None) or {}   # 원래 값
        user = raw.get("secrets") if isinstance(raw.get("secrets"), dict) else {}   # secrets 절
    except Exception as e:                                   # 실패
        log.debug("ncn.yaml secrets not read: %s", e)        # 기본값으로
    admin: Dict[str, Any] = {}                               # 관리자 secrets 절
    try:                                                    # 정책 읽기(깨졌으면 권한 서비스가 이미 실행 거부)
        from ncn.policy import current_admin_policy_or_none   # 예외 없는 읽기
        pol = current_admin_policy_or_none(env)              # 정책
        admin = dict(pol.secrets) if pol is not None else {}   # secrets 절
    except Exception as e:                                   # 실패
        log.debug("policy secrets not read: %s", e)          # 무시
    enforce = admin.get("enforce") is True                   # 관리자 강제
    s = SecretSettings()                                     # 기본값
    s.enabled = enforce or (user.get("enabled", True) is not False and not cn_compat())   # 켬 여부
    s.env_patterns += _str_list(user.get("env_patterns")) + _str_list(admin.get("env_patterns"))   # 패턴 합침
    s.block_files += _str_list(user.get("block_files")) + _str_list(admin.get("block_files"))   # 파일 합침
    s.env_allow = _str_list(admin.get("env_allow")) + ([] if enforce else _str_list(user.get("env_allow")))   # 예외(강제면 관리자 것만)
    return s                                                 # 설정 반환


def _matches_any(name: str, patterns: List[str]) -> bool:   # 이름이 패턴 중 하나에 맞는지
    """대소문자 무시 글롭 비교."""
    up = name.upper()                                        # 대문자로
    return any(fnmatch.fnmatchcase(up, p.upper()) for p in patterns)   # 하나라도 맞으면 참


def is_secret_env_name(name: str, settings: Optional[SecretSettings] = None) -> bool:   # 비밀 환경변수 이름인지
    """패턴에 맞고 예외(env_allow)에 없으면 참."""
    s = settings or SecretSettings()                         # 설정(없으면 기본)
    return _matches_any(name, s.env_patterns) and not _matches_any(name, s.env_allow)   # 판정


def strip_secret_env(environ: Mapping[str, str], settings: Optional[SecretSettings] = None) -> Dict[str, str]:   # 비밀을 뺀 사본
    """비밀 이름을 뺀 환경변수 사본."""
    s = settings or SecretSettings()                         # 설정
    return {k: v for k, v in environ.items() if not is_secret_env_name(k, s)}   # 비밀 아닌 것만


def bash_child_env() -> Optional[dict]:                     # Bash 자식 환경변수(ncn/tools/bash.py ENV_PROVIDER 기본값)
    """꺼져 있거나 뺄 것이 없으면 None(= cn처럼 그대로 물려받음), 있으면 비밀을 뺀 사본."""
    s = load_secret_settings()                               # 지금 설정
    if not s.enabled:                                        # 꺼짐
        return None                                         # 그대로 물려받음
    env = dict(os.environ)                                   # 현재 환경
    stripped = strip_secret_env(env, s)                      # 뺀 사본
    if len(stripped) == len(env):                            # 뺀 것 없음
        return None                                         # cn과 같은 실행
    log.debug("Removed %d secret environment variable(s) from Bash child", len(env) - len(stripped))   # 개수만 기록(이름·값은 안 남김)
    return stripped                                          # 뺀 사본


def mcp_inherited_env(environ: Mapping[str, str]) -> Mapping[str, str]:   # MCP stdio 자식에게 물려줄 환경(설정 env 는 별도로 유지)
    """꺼져 있으면 그대로, 켜져 있으면 비밀을 뺀 사본. 서버 설정의 env 에 직접 적은 값은 호출한 쪽이 그대로 넣는다."""
    s = load_secret_settings()                               # 지금 설정
    return strip_secret_env(environ, s) if s.enabled else environ   # 뺀 사본 또는 그대로


_block_cache: Dict[tuple, Any] = {}                         # (켬, 패턴들) → 판정기


def extra_block_ignore() -> Any:                            # 추가 민감 파일 판정기(꺼져 있으면 None)
    """ncn/security/ignore.py is_security_concern 이 cn 목록 다음에 확인한다."""
    s = load_secret_settings()                               # 지금 설정
    if not s.enabled or not s.block_files:                   # 꺼짐·패턴 없음
        return None                                         # 추가 판정 없음
    key = tuple(s.block_files)                               # 저장 열쇠
    ig = _block_cache.get(key)                               # 저장된 판정기
    if ig is None:                                           # 없으면
        from ncn.security.ignore import Ignore              # npm ignore 와 같은 판정기(대소문자 무시)
        ig = Ignore().add(s.block_files)                     # 만들기
        _block_cache[key] = ig                               # 저장
    return ig                                                # 판정기
