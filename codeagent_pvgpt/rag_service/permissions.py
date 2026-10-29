# rag_service/permissions.py — "이 사람이 볼 수 있는 항목(scope)"을 답한다(2판 md 5-6 방식 B)
# 원본: Postgres(team_members·team_scopes·scopes), 캐시: Redis(프로세스가 여러 개여도 공유) — 없으면 캐시 없이 동작
import json                                                  # Redis에 집합을 문자열로 저장

from psycopg_pool import ConnectionPool                      # Postgres 연결 풀(동기)

CACHE_PREFIX = "perm:scopes:"                                # Redis 키 앞부분


class Permissions:
    def __init__(self, pool: ConnectionPool, redis=None, ttl_seconds: int = 300):
        self.pool = pool                                     # Postgres 연결 풀
        self.redis = redis                                   # Redis 클라이언트(동기) 또는 None
        self.ttl = ttl_seconds                               # 캐시 유지 시간 = 권한 변경이 늦게 반영되는 최대 시간

    def allowed_scopes(self, user_id: str) -> frozenset[str]:
        """사람 → 소속 팀들 → 팀들이 볼 수 있는 항목의 '합집합'. 소속이 없거나 비활성이면 빈 집합."""
        key = CACHE_PREFIX + user_id                         # 캐시 키
        if self.redis is not None:                           # 캐시가 있으면
            cached = self.redis.get(key)                     # 먼저 찾아본다
            if cached is not None:
                return frozenset(json.loads(cached))         # 캐시 적중
        with self.pool.connection() as con:                  # DB 조회
            rows = con.execute(
                """SELECT DISTINCT ts.scope_id
                   FROM team_members tm
                   JOIN team_scopes ts ON ts.team_id = tm.team_id
                   JOIN users u ON u.user_id = tm.user_id
                   WHERE tm.user_id = %s AND u.active""", (user_id,)).fetchall()   # 활성 사용자만
        scopes = frozenset(r[0] for r in rows)               # 합집합(중복은 DISTINCT로 제거)
        if self.redis is not None:
            self.redis.set(key, json.dumps(sorted(scopes)), ex=self.ttl)   # TTL과 함께 캐시
        return scopes

    def invalidate(self, user_id: str | None = None) -> int:
        """권한 변경 알림을 받으면 캐시를 지운다 — None이면 전체. 지운 개수를 돌려준다."""
        if self.redis is None:                               # 캐시가 없으면 지울 것도 없음
            return 0
        if user_id is not None:
            return self.redis.delete(CACHE_PREFIX + user_id)  # 한 사람
        keys = list(self.redis.scan_iter(CACHE_PREFIX + "*"))  # 전체 키 찾기
        return self.redis.delete(*keys) if keys else 0       # 한 번에 삭제

    def scope_for(self, repo: str, path: str) -> str | None:
        """색인할 파일이 어느 항목에 속하는지 — 경로 접두어가 가장 긴 것(예: billing/coupon/ > billing/)."""
        with self.pool.connection() as con:
            rows = con.execute("SELECT scope_id, path_prefix FROM scopes WHERE repo = %s", (repo,)).fetchall()  # 이 저장소의 항목들
        best = [(len(prefix), sid) for sid, prefix in rows if path.startswith(prefix)]   # 접두어가 맞는 것들
        return max(best)[1] if best else None                # 가장 긴 접두어 / 없으면 None → 색인 안 함
