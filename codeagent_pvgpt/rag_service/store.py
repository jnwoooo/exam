# rag_service/store.py — Qdrant(로컬 docker)에 삽입·검색. 권한 필터는 여기서 반드시 붙는다(호출자에게서 받지 않음)
import uuid                                                  # 결정적 점 ID 만들기

from qdrant_client import QdrantClient, models               # Qdrant 클라이언트·요청 모델

COLLECTIONS = ("code", "docs")                               # 코드·문서는 청킹·payload가 달라 컬렉션을 나눈다
TEXT_LIMIT = 4000                                            # payload에 저장할 원문 길이 상한(미리보기·request_fix 참고용)


class Store:
    def __init__(self, url: str, api_key: str | None = None):
        # 운영: http://localhost:6333(로컬 docker) — 접속 키는 RAG 서비스만 가진다(2판 md D4)
        # 테스트: ":memory:" — Qdrant 없이 프로세스 안에서 같은 API로 동작
        self.client = QdrantClient(":memory:") if url == ":memory:" else QdrantClient(url=url, api_key=api_key)

    def ensure(self, collection: str, dim: int) -> None:
        """컬렉션이 없으면 만들고, 필터에 쓰는 필드에 payload 인덱스를 건다."""
        if self.client.collection_exists(collection):        # 이미 있으면
            info = self.client.get_collection(collection)    # 차원이 맞는지 확인(모델을 바꾸면 달라짐)
            size = info.config.params.vectors.size           # 기존 컬렉션의 벡터 길이
            if size != dim:                                  # 다르면 섞어 쓸 수 없다
                raise RuntimeError(f"컬렉션 {collection} 차원 {size} ≠ 모델 {dim} — 새 컬렉션으로 다시 색인하세요")
            return
        self.client.create_collection(collection, vectors_config=models.VectorParams(
            size=dim, distance=models.Distance.COSINE))      # 코사인 거리(정규화 벡터)
        for f in ("scope", "repo", "path"):                  # 권한 필터·파일 단위 삭제에 쓰는 필드
            self.client.create_payload_index(collection, field_name=f,
                                             field_schema=models.PayloadSchemaType.KEYWORD)  # 정확 일치 인덱스

    def replace_file(self, collection: str, repo: str, path: str, scope: str, commit: str,
                     chunks: list[dict], vectors: list[list[float]]) -> int:
        """파일 하나의 옛 청크를 전부 지우고 새로 넣는다 — 함수가 늘거나 줄어도 정확."""
        self.delete_file(collection, repo, path)             # 옛 청크 삭제
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{collection}/{repo}/{path}#{c['start']}-{c['end']}")),  # 같은 위치=같은 ID
                vector=v,                                    # 임베딩 벡터
                payload={"repo": repo, "path": path, "scope": scope, "commit": commit,   # 위치·권한·시점
                         "symbol": c["symbol"], "start": c["start"], "end": c["end"],   # 함수명·줄 범위
                         "text": c["body"][:TEXT_LIMIT]})    # 원문 앞부분
            for c, v in zip(chunks, vectors)]                # 청크와 벡터를 짝지어
        if points:                                           # 넣을 게 있으면
            self.client.upsert(collection, points=points, wait=True)   # 삽입(완료까지 대기)
        return len(points)                                   # 넣은 개수

    def delete_file(self, collection: str, repo: str, path: str) -> None:
        """repo+path가 같은 점을 모두 지운다."""
        self.client.delete(collection, points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="repo", match=models.MatchValue(value=repo)),   # 저장소 일치
            models.FieldCondition(key="path", match=models.MatchValue(value=path))])), wait=True)  # 경로 일치

    def search(self, collection: str, vector: list[float], scopes: frozenset[str], k: int,
               exclude_path: str | None = None):
        """★ scopes가 비면 호출 자체를 안 한다 — 필터 없이 검색하면 전체가 노출된다."""
        if not scopes:                                       # 권한 없음
            return []
        must = [models.FieldCondition(key="scope", match=models.MatchAny(any=sorted(scopes)))]  # 허용 항목 안에서만
        must_not = [models.FieldCondition(key="path", match=models.MatchValue(value=exclude_path))] if exclude_path else []  # 제외 경로
        return self.client.query_points(collection, query=vector,
                                        query_filter=models.Filter(must=must, must_not=must_not),  # 검색 도중 필터 적용
                                        limit=k, with_payload=True).points   # 상위 k개 + payload
