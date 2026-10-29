# rag_service/app.py — RAG 서비스 API(:8100). 색인(/index)과 검색(/search)을 엔드포인트로 나눈다(2판 md 5-5)
# 실행: uv run uvicorn rag_service.app:main_app --factory --port 8100
from typing import Annotated, Literal                        # 헤더 주입·컬렉션 이름 제한

from fastapi import FastAPI, Header, HTTPException           # 웹 프레임워크
from pydantic import BaseModel, Field                        # 요청 본문 검증

from rag_service.chunking import chunk_file, embed_text, fit_to_tokens   # 청킹·임베딩 입력 만들기
from rag_service.permissions import Permissions              # 권한 DB
from rag_service.store import COLLECTIONS, Store             # Qdrant 저장소

Collection = Literal["code", "docs"]                         # 허용 컬렉션 이름


class FileIn(BaseModel):                                     # 색인할 파일 하나(추출 결과)
    path: str                                                # 저장소 안 경로
    content: str                                             # 파일 내용(텍스트)


class IndexIn(BaseModel):                                    # 색인기가 보내는 요청
    collection: Collection                                   # 넣을 컬렉션
    repo: str                                                # 저장소 이름
    commit: str                                              # 어느 커밋 기준인지 — 검색 결과에 함께 돌려준다
    files: list[FileIn] = []                                 # 추가·변경된 파일
    deleted: list[str] = []                                  # 삭제된 파일 경로


class SearchIn(BaseModel):                                   # 코드에이전트 서버가 보내는 요청 — ★ 필터는 받지 않는다
    collection: Collection                                   # 검색할 컬렉션
    query: str = Field(min_length=1, max_length=1000)        # 질문
    k: int = Field(8, ge=1, le=20)                           # 결과 개수
    with_text: bool = False                                  # True면 미리보기 대신 저장된 원문(최대 4000자) — request_fix 참고용
    exclude_path: str | None = None                          # 이 경로는 결과에서 제외(수정 중인 파일 자신)


class InvalidateIn(BaseModel):                               # 권한 변경 알림
    user_id: str | None = None                               # None이면 전체 캐시 삭제


def create_app(store: Store, embedder, perms: Permissions, index_tokens: set[str],
               search_tokens: set[str]) -> FastAPI:
    """부품을 받아 앱을 만든다 — 테스트는 가짜 임베더·메모리 Qdrant를 넣는다."""
    app = FastAPI(title="RAG service")                       # 앱
    for c in COLLECTIONS:                                    # 컬렉션 준비
        store.ensure(c, embedder.dim)                        # 없으면 생성(차원 = 임베딩 모델)

    def check(token: str | None, allowed: set[str]) -> None:
        """서비스 토큰 확인 — 로컬·내부망이라도 아무나 부를 수 없게."""
        if not token or token not in allowed:
            raise HTTPException(401, "invalid service token")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "dim": embedder.dim}             # 살아 있는지 + 임베딩 차원

    @app.post("/index")
    def index(body: IndexIn, x_service_token: Annotated[str | None, Header()] = None):
        check(x_service_token, index_tokens)                 # 색인 토큰만 허용
        for path in body.deleted:                            # 삭제된 파일
            store.delete_file(body.collection, body.repo, path)   # 그 파일의 점 전부 삭제
        stored, skipped = 0, []                              # 넣은 청크 수, 항목 없어 건너뛴 파일
        for f in body.files:                                 # 파일마다
            scope = perms.scope_for(body.repo, f.path)       # ★ 항목은 RAG가 권한 DB로 정한다(보내는 쪽이 정하지 않음)
            if scope is None:                                # 어느 항목에도 속하지 않으면
                skipped.append(f.path)                       # 색인하지 않는다(실수로 전체 공개 방지)
                continue
            chunks = fit_to_tokens(chunk_file(f.path, f.content),       # 청킹
                                   embedder.count_tokens, embedder.max_tokens)   # 모델 한도에 맞춤
            vectors = embedder.embed_passages([embed_text(body.repo, f.path, c) for c in chunks]) if chunks else []  # 임베딩
            stored += store.replace_file(body.collection, body.repo, f.path, scope, body.commit, chunks, vectors)  # 삽입
        return {"stored_chunks": stored, "deleted_files": len(body.deleted), "skipped_no_scope": skipped}

    @app.post("/search")
    def search(body: SearchIn, x_service_token: Annotated[str | None, Header()] = None,
               x_user_id: Annotated[str | None, Header()] = None):
        check(x_service_token, search_tokens)                # 검색 토큰만 허용
        if not x_user_id:                                    # 사번은 헤더로만 받는다
            raise HTTPException(400, "user required")
        scopes = perms.allowed_scopes(x_user_id)             # 사람 → 팀 → 항목(합집합)
        if not scopes:                                       # 볼 수 있는 항목이 없으면
            return {"results": [], "denied": True}           # 검색하지 않는다
        hits = store.search(body.collection, embedder.embed_query(body.query), scopes, body.k,
                            exclude_path=body.exclude_path)  # 질문 임베딩 → 권한 필터 검색
        return {"denied": False, "results": [
            {"repo": h.payload["repo"], "path": h.payload["path"], "symbol": h.payload["symbol"],   # 위치
             "start": h.payload["start"], "end": h.payload["end"], "commit": h.payload["commit"],   # 줄 범위·커밋
             "score": round(h.score, 3),                                                            # 유사도
             "text": h.payload["text"] if body.with_text                                            # 원문 또는
             else "\n".join(h.payload["text"].splitlines()[:3])}                                    # 앞 3줄 미리보기
            for h in hits]}

    @app.post("/permissions/invalidate")
    def invalidate(body: InvalidateIn, x_service_token: Annotated[str | None, Header()] = None):
        check(x_service_token, index_tokens)                 # 관리 작업이라 색인 토큰으로
        return {"removed": perms.invalidate(body.user_id)}   # 권한 캐시 삭제(권한 변경 직후 바로 반영)

    return app


def main_app() -> FastAPI:
    """실제 구성: 설정 → Postgres·Redis·Qdrant·KoE5 연결 → 앱."""
    import redis                                             # 권한 캐시
    from psycopg_pool import ConnectionPool                  # Postgres 풀
    from common.settings import get_settings                 # 설정
    from rag_service.embedder import load_embedder           # KoE5
    s = get_settings()                                       # 환경변수 읽기
    pool = ConnectionPool(s.database_url, min_size=1, max_size=5, open=True)   # DB 연결 풀
    perms = Permissions(pool, redis.Redis.from_url(s.redis_url), s.perm_cache_ttl)   # 권한
    return create_app(Store(s.qdrant_url, s.qdrant_api_key),  # Qdrant(로컬 docker)
                      load_embedder(s.embed_model, s.embed_device),   # KoE5(시작할 때 한 번 로딩)
                      perms, s.index_tokens, s.search_tokens)  # 권한·서비스 토큰
