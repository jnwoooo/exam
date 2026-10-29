# common/settings.py — 두 서버(RAG·코드에이전트)와 색인기가 함께 쓰는 설정. 값은 전부 환경변수에서 읽는다
import os                                                    # 환경변수 읽기
import re                                                    # 줄 끝 주석 제거
from dataclasses import dataclass, field                     # 설정 묶음을 클래스로
from pathlib import Path                                     # .env 파일 경로

ROOT = Path(__file__).resolve().parent.parent                # 프로젝트 루트(codeagent_pvgpt/)


def load_env_file(path: Path = ROOT / ".env") -> None:
    """개발 편의: 루트의 .env 파일을 읽어 환경변수에 넣는다(이미 있는 값은 덮지 않음).
    운영에서는 .env 없이 실제 환경변수로 넣는다."""
    if not path.exists():                                    # 파일이 없으면 아무것도 안 함
        return
    for line in path.read_text(encoding="utf-8").splitlines():   # 한 줄씩
        line = line.strip()                                  # 앞뒤 공백 제거
        if not line or line.startswith("#") or "=" not in line:  # 빈 줄·주석·형식 아닌 줄은 건너뜀
            continue
        key, value = line.split("=", 1)                      # 첫 '='에서 나눔(값에 '='가 있어도 됨)
        value = re.split(r"\s+#", value, maxsplit=1)[0]      # 값 뒤의 ' # 주석' 제거(값 안의 '#'은 공백이 앞에 없으면 유지)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))  # 따옴표 제거, 기존 값 우선


def _split(name: str) -> set[str]:
    """쉼표로 나열된 환경변수 → 집합(빈 값 제외)."""
    return {v.strip() for v in os.environ.get(name, "").split(",") if v.strip()}


@dataclass
class Settings:
    # ── 공용 인프라(로컬 docker) ──
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://llmv3:llmv3@localhost:5432/codeagent"))   # Postgres(사용자·권한·사용량)
    redis_url: str = field(default_factory=lambda: os.environ.get(
        "REDIS_URL", "redis://localhost:6379/0"))                               # Redis(한도 카운터·권한 캐시)
    qdrant_url: str = field(default_factory=lambda: os.environ.get(
        "QDRANT_URL", "http://localhost:6333"))                                 # Qdrant(벡터 저장소)
    qdrant_api_key: str | None = field(default_factory=lambda: os.environ.get("QDRANT_API_KEY") or None)  # 키(없으면 None)

    # ── 임베딩(RAG 서비스 프로세스 안, 로컬 Python) ──
    embed_model: str = field(default_factory=lambda: os.environ.get("EMBED_MODEL", "nlpai-lab/KoE5"))  # 모델 이름 또는 폴더
    embed_device: str | None = field(default_factory=lambda: os.environ.get("EMBED_DEVICE") or None)  # mps/cpu/cuda, 비우면 자동

    # ── RAG 서비스 ──
    rag_url: str = field(default_factory=lambda: os.environ.get("RAG_URL", "http://localhost:8100"))  # 코드에이전트 서버가 부를 주소
    index_tokens: set[str] = field(default_factory=lambda: _split("INDEX_TOKENS"))    # RAG가 받는 색인용 서비스 토큰들
    search_tokens: set[str] = field(default_factory=lambda: _split("SEARCH_TOKENS"))  # RAG가 받는 검색용 서비스 토큰들
    search_token: str = field(default_factory=lambda: os.environ.get("SEARCH_TOKEN", ""))  # 코드에이전트 서버가 보낼 검색 토큰
    index_token: str = field(default_factory=lambda: os.environ.get("INDEX_TOKEN", ""))    # 색인기가 보낼 색인 토큰
    perm_cache_ttl: int = field(default_factory=lambda: int(os.environ.get("PERM_CACHE_TTL", "300")))  # 권한 캐시 유지(초)

    # ── 외부 LLM(gemma4) ──
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "LLM_BASE_URL", "http://0.0.0.0:8000/v1"))           # OpenAI 호환 주소 — 실제 외부 호스트로 바꿀 것
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))   # 외부 LLM 키(서버만 가짐)
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "gemma4-26b-32k:latest"))  # 모델 이름
    llm_timeout: float = field(default_factory=lambda: float(os.environ.get("LLM_TIMEOUT", "180")))  # LLM 호출 시간 제한(초)

    # ── 사용량 한도(Redis) ──
    limit_rpm: int = field(default_factory=lambda: int(os.environ.get("LIMIT_RPM", "60")))            # 사용자별 분당 LLM 요청 수
    limit_daily_tokens: int = field(default_factory=lambda: int(os.environ.get("LIMIT_DAILY_TOKENS", "2000000")))  # 하루 토큰

    # ── 코드에이전트 서버 ──
    agent_host: str = field(default_factory=lambda: os.environ.get("AGENT_HOST", "127.0.0.1"))  # 바인드 주소
    agent_port: int = field(default_factory=lambda: int(os.environ.get("AGENT_PORT", "8200")))  # 포트
    fix_max_code_chars: int = field(default_factory=lambda: int(os.environ.get("FIX_MAX_CODE_CHARS", "20000")))  # request_fix 코드 상한
    fix_max_retries: int = field(default_factory=lambda: int(os.environ.get("FIX_MAX_RETRIES", "2")))  # 검증 실패 시 재생성 횟수

    # ── 색인기(로컬 실행) ──
    index_repos: str = field(default_factory=lambda: os.environ.get("INDEX_REPOS", ""))  # "이름|git주소|브랜치|컬렉션;..."
    index_cache_dir: str = field(default_factory=lambda: os.environ.get(
        "INDEX_CACHE_DIR") or str(ROOT / ".repos"))            # git clone 받을 로컬 폴더
    index_state: str = field(default_factory=lambda: os.environ.get(
        "INDEX_STATE") or str(ROOT / ".repos" / "index_state.json"))   # 마지막 색인 커밋 기록 파일


def get_settings() -> Settings:
    """.env를 먼저 읽고 설정을 만든다 — 호출할 때마다 현재 환경변수를 반영."""
    load_env_file()                                          # .env가 있으면 환경변수로
    return Settings()                                        # 환경변수 → 설정 객체
