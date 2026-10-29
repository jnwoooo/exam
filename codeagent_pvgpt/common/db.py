# common/db.py — Postgres 준비: DB가 없으면 만들고 스키마(db/schema.sql)를 적용한다
# 실행: uv run python -m common.db        (DATABASE_URL의 DB 이름으로 생성 + 스키마 적용)
import hashlib                                               # 토큰 해시
import sys                                                   # 종료 코드
from urllib.parse import urlparse, urlunparse                # DB 주소에서 DB 이름만 바꾸기

import psycopg                                               # Postgres 드라이버
from psycopg import sql                                      # DB 이름을 안전하게 SQL에 넣기

from common.settings import ROOT, get_settings               # 설정·루트 경로

SCHEMA_FILE = ROOT / "db" / "schema.sql"                     # 스키마 파일 위치


def hash_token(token: str) -> str:
    """개인 토큰 → SHA-256 16진 문자열. DB에는 이 값만 저장·비교한다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_database(database_url: str) -> bool:
    """DATABASE_URL의 DB가 없으면 만든다. 만들었으면 True."""
    parts = urlparse(database_url)                           # 주소를 부분으로 나눔
    dbname = parts.path.lstrip("/")                          # 경로 부분 = DB 이름
    admin_url = urlunparse(parts._replace(path="/postgres"))  # 기본 DB 'postgres'로 접속해서 만든다
    with psycopg.connect(admin_url, autocommit=True) as con:  # CREATE DATABASE는 트랜잭션 밖에서만 가능 → autocommit
        exists = con.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()  # 이미 있나
        if exists:
            return False                                     # 있으면 그대로
        con.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))  # 없으면 생성(이름은 식별자로 안전하게)
        return True


def apply_schema(database_url: str) -> None:
    """스키마 파일을 실행한다 — IF NOT EXISTS라 여러 번 실행해도 안전."""
    with psycopg.connect(database_url, autocommit=True) as con:   # 대상 DB에 접속
        con.execute(SCHEMA_FILE.read_text(encoding="utf-8"))      # 파일 전체를 한 번에 실행


def main() -> int:
    url = get_settings().database_url                        # 설정의 DB 주소
    created = ensure_database(url)                           # DB 생성(필요하면)
    apply_schema(url)                                        # 스키마 적용
    print(f"DB {'생성' if created else '확인'} + 스키마 적용 완료: {urlparse(url).path.lstrip('/')}")  # 결과 출력(비밀번호 없이)
    return 0


if __name__ == "__main__":
    sys.exit(main())                                         # 명령줄 실행
