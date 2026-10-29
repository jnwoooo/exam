# agent_server/manage.py — 관리 명령: 사용자·개인 토큰·팀·항목(권한) 등록. Postgres를 직접 고친다
# 예) uv run python -m agent_server.manage add-user emp-001 --name 김개발 --admin
#     uv run python -m agent_server.manage issue-token emp-001 --label 노트북     (토큰은 이때 한 번만 보임)
#     uv run python -m agent_server.manage add-team A --name 결제팀
#     uv run python -m agent_server.manage join A emp-001
#     uv run python -m agent_server.manage add-scope shop-all shop               (저장소 shop 전체)
#     uv run python -m agent_server.manage add-scope shop-coupon shop --prefix coupon/
#     uv run python -m agent_server.manage grant A shop-all
#     uv run python -m agent_server.manage list
import argparse                                              # 명령줄 해석
import secrets                                               # 안전한 무작위 토큰
import sys                                                   # 종료 코드

import psycopg                                               # Postgres

from common.db import hash_token                             # 토큰 해시
from common.settings import get_settings                     # 설정


def invalidate_perm_cache(redis_url: str) -> None:
    """권한이 바뀌면 RAG의 권한 캐시(Redis)를 지운다 — 바로 반영되게."""
    try:
        import redis
        r = redis.Redis.from_url(redis_url)
        keys = list(r.scan_iter("perm:scopes:*"))            # 권한 캐시 키 전부
        if keys:
            r.delete(*keys)
    except Exception as e:                                   # Redis가 꺼져 있어도 DB 변경은 유지(TTL 뒤 반영)
        print(f"(권한 캐시 삭제 실패 — 최대 TTL 뒤 반영: {e})")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="manage")               # 명령 정의
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user"); a.add_argument("user_id"); a.add_argument("--name", default=""); a.add_argument("--admin", action="store_true")
    a = sub.add_parser("deactivate-user"); a.add_argument("user_id")
    a = sub.add_parser("issue-token"); a.add_argument("user_id"); a.add_argument("--label", default="")
    a = sub.add_parser("revoke-tokens"); a.add_argument("user_id")
    a = sub.add_parser("add-team"); a.add_argument("team_id"); a.add_argument("--name", default="")
    a = sub.add_parser("join"); a.add_argument("team_id"); a.add_argument("user_id")
    a = sub.add_parser("leave"); a.add_argument("team_id"); a.add_argument("user_id")
    a = sub.add_parser("add-scope"); a.add_argument("scope_id"); a.add_argument("repo"); a.add_argument("--prefix", default="")
    a = sub.add_parser("grant"); a.add_argument("team_id"); a.add_argument("scope_id")
    a = sub.add_parser("revoke"); a.add_argument("team_id"); a.add_argument("scope_id")
    sub.add_parser("list")
    args = p.parse_args(argv)                                # 해석
    s = get_settings()
    perm_changed = args.cmd in {"deactivate-user", "join", "leave", "grant", "revoke", "add-scope"}   # 권한 캐시를 지울 명령

    with psycopg.connect(s.database_url, autocommit=True) as con:   # 명령 하나 = 즉시 반영
        if args.cmd == "add-user":
            con.execute("INSERT INTO users(user_id, name, is_admin) VALUES (%s,%s,%s) "
                        "ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, is_admin = EXCLUDED.is_admin, active = TRUE",
                        (args.user_id, args.name, args.admin))   # 있으면 갱신
            print(f"사용자 {args.user_id} 등록(관리자={args.admin})")
        elif args.cmd == "deactivate-user":
            con.execute("UPDATE users SET active = FALSE WHERE user_id = %s", (args.user_id,))
            print(f"사용자 {args.user_id} 비활성화 — 토큰·검색 모두 거부(서버 캐시 최대 60초)")
        elif args.cmd == "issue-token":
            token = "ca-" + secrets.token_urlsafe(32)        # 새 토큰(접두어로 용도 구분)
            con.execute("INSERT INTO api_tokens(token_hash, user_id, label) VALUES (%s,%s,%s)",
                        (hash_token(token), args.user_id, args.label))   # 해시만 저장
            print(f"{args.user_id}의 새 토큰(지금 한 번만 표시 — Continue 설정 secrets에 넣으세요):\n{token}")
        elif args.cmd == "revoke-tokens":
            n = con.execute("UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                            (args.user_id,)).rowcount
            print(f"{args.user_id}의 토큰 {n}개 폐기(서버 캐시 최대 60초)")
        elif args.cmd == "add-team":
            con.execute("INSERT INTO teams VALUES (%s,%s) ON CONFLICT (team_id) DO UPDATE SET name = EXCLUDED.name",
                        (args.team_id, args.name))
            print(f"팀 {args.team_id} 등록")
        elif args.cmd == "join":
            con.execute("INSERT INTO team_members VALUES (%s,%s) ON CONFLICT DO NOTHING", (args.team_id, args.user_id))
            print(f"{args.user_id} → 팀 {args.team_id}")
        elif args.cmd == "leave":
            con.execute("DELETE FROM team_members WHERE team_id = %s AND user_id = %s", (args.team_id, args.user_id))
            print(f"{args.user_id} ← 팀 {args.team_id} 탈퇴")
        elif args.cmd == "add-scope":
            con.execute("INSERT INTO scopes VALUES (%s,%s,%s) ON CONFLICT (scope_id) DO UPDATE "
                        "SET repo = EXCLUDED.repo, path_prefix = EXCLUDED.path_prefix",
                        (args.scope_id, args.repo, args.prefix))
            print(f"항목 {args.scope_id} = {args.repo}/{args.prefix or '(전체)'} — 이미 색인된 파일에 반영하려면 다시 색인")
        elif args.cmd == "grant":
            con.execute("INSERT INTO team_scopes VALUES (%s,%s) ON CONFLICT DO NOTHING", (args.team_id, args.scope_id))
            print(f"팀 {args.team_id} → 항목 {args.scope_id} 허용")
        elif args.cmd == "revoke":
            con.execute("DELETE FROM team_scopes WHERE team_id = %s AND scope_id = %s", (args.team_id, args.scope_id))
            print(f"팀 {args.team_id} → 항목 {args.scope_id} 회수")
        elif args.cmd == "list":
            for title, sql in [
                ("사용자", "SELECT u.user_id, u.name, u.is_admin, u.active, count(t.token_hash) FILTER (WHERE t.revoked_at IS NULL) "
                          "FROM users u LEFT JOIN api_tokens t USING (user_id) GROUP BY 1,2,3,4 ORDER BY 1"),
                ("팀 소속", "SELECT team_id, string_agg(user_id, ', ' ORDER BY user_id) FROM team_members GROUP BY 1 ORDER BY 1"),
                ("항목", "SELECT scope_id, repo, path_prefix FROM scopes ORDER BY 1"),
                ("팀 → 항목", "SELECT team_id, string_agg(scope_id, ', ' ORDER BY scope_id) FROM team_scopes GROUP BY 1 ORDER BY 1")]:
                print(f"== {title}")                         # 표 제목
                for row in con.execute(sql).fetchall():
                    print("  ", " | ".join(str(v) for v in row))   # 한 줄씩
    if perm_changed:
        invalidate_perm_cache(s.redis_url)                   # 권한 캐시 즉시 무효화
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
