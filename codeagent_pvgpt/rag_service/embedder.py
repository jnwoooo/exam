# rag_service/embedder.py — KoE5 임베딩(로컬 Python, sentence_transformers). 문서는 "passage: ", 질문은 "query: "(E5 규칙)
import os                                                    # 환경변수(오프라인 설정)


class KoE5Embedder:
    """KoE5를 RAG 서비스 프로세스 안에서 돌린다(2판 md D6). 인터페이스: dim·max_tokens·embed_passages·embed_query·count_tokens."""

    def __init__(self, model_name: str = "nlpai-lab/KoE5", device: str | None = None):
        from sentence_transformers import SentenceTransformer    # 무거운 패키지 — 실제로 만들 때만 불러온다(테스트는 가짜 사용)
        # 폐쇄망: model_name에 미리 받아 둔 모델 폴더 경로를 넣고 HF_HUB_OFFLINE=1로 인터넷 접근을 막는다
        self.model = SentenceTransformer(model_name, device=device)  # device=None이면 mps(Mac)/cuda/cpu 중 자동
        self.dim = self.model.get_sentence_embedding_dimension()     # 벡터 길이 → Qdrant 컬렉션 size에 그대로
        self.max_tokens = self.model.max_seq_length                  # 입력 한도(보통 512) — 넘으면 잘려서 벡터에 반영 안 됨

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """색인할 청크들 → 벡터들(같은 순서). 정규화해서 코사인 비교에 맞춘다."""
        vecs = self.model.encode([f"passage: {t}" for t in texts],   # 문서 쪽 접두어
                                 normalize_embeddings=True,          # 길이 1로 정규화
                                 batch_size=32)                      # 32개씩 묶어 계산
        return vecs.tolist()                                         # numpy → 파이썬 리스트

    def embed_query(self, text: str) -> list[float]:
        """검색 질문 → 벡터. 문서와 같은 모델이어야 같은 좌표계에서 비교된다."""
        return self.model.encode([f"query: {text}"], normalize_embeddings=True)[0].tolist()  # 질문 쪽 접두어

    def count_tokens(self, text: str) -> int:
        """청크 길이를 이 모델의 토크나이저로 잰다(글자 수가 아니라 토큰 수가 기준)."""
        return len(self.model.tokenizer.encode(text, add_special_tokens=False))  # 특수 토큰 제외 개수


def load_embedder(model_name: str, device: str | None):
    """설정값으로 임베더를 만든다. 모델 폴더 경로면 오프라인 모드를 켠다."""
    if os.path.isdir(model_name):                                    # 로컬 폴더를 가리키면
        os.environ.setdefault("HF_HUB_OFFLINE", "1")                 # 허브 접속 시도 자체를 막는다(폐쇄망)
    return KoE5Embedder(model_name, device)                          # 임베더 생성(모델 로딩)
