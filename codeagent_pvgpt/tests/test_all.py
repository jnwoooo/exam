# tests/test_all.py — KoE5·실제 LLM 없이 도는 테스트. 가짜 임베딩·가짜 LLM + 로컬 docker의 Postgres·Redis 사용
# 세 서버(가짜 LLM, RAG, 코드에이전트)를 실제 HTTP로 띄워 Continue가 부르는 경로(/v1, /mcp)를 끝까지 확인한다
# 필요: docker의 Postgres(5432)·Redis(6379). DB는 codeagent_test, Redis는 15번 DB를 쓴다(운영 데이터와 분리)
import asyncio, datetime as dt, hashlib, json, math, os, re, subprocess, threading, time   # 표준 라이브러리

import httpx                                                 # HTTP 호출
import psycopg                                               # Postgres
import pytest                                                # 테스트
import redis                                                 # Redis
import uvicorn                                               # 서버 실행
from psycopg_pool import ConnectionPool                      # RAG용 풀

from agent_server import auth                                # 토큰 캐시 비우기
from agent_server.app import create_app as create_agent      # 코드에이전트 서버
from agent_server.fixer import apply_blocks, parse_blocks, validate   # 수정안 처리
from common.db import apply_schema, ensure_database, hash_token       # DB 준비
from common.settings import Settings                         # 설정
from rag_service.app import create_app as create_rag         # RAG 서비스
from rag_service.chunking import chunk_markdown, chunk_python, fit_to_tokens   # 청킹
from rag_service.indexer import index_repo, parse_repos, sync_repo   # 색인기
from rag_service.permissions import Permissions              # 권한
from rag_service.store import Store                          # Qdrant(메모리)
from tools.fake_llm import app as fake_llm_app               # 가짜 LLM

DB = os.environ.get("TEST_DATABASE_URL", "postgresql://llmv3:llmv3@localhost:5432/codeagent_test")   # 테스트 DB(환경변수로 바꿀 수 있음)
REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")   # 테스트 Redis DB
PORTS = {"llm": 18300, "rag": 18100, "agent": 18200}         # 테스트 포트
TOK = {"kim": "tok-kim", "lee": "tok-lee", "boss": "tok-boss", "heavy": "tok-heavy"}   # 사용자별 개인 토큰


class FakeEmbedder:
    """KoE5 대신: 겹치는 단어가 많을수록 비슷한 벡터. 인터페이스는 KoE5Embedder와 같다."""
    dim, max_tokens = 64, 40
    def _vec(self, t):
        v = [0.0] * self.dim
        for w in re.findall(r"[가-힣A-Za-z_]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]
    def embed_passages(self, texts): return [self._vec(t) for t in texts]
    def embed_query(self, text): return self._vec(text)
    def count_tokens(self, text): return len(text.split())


def serve(app, port):
    """앱을 백그라운드 스레드에서 실제 HTTP로 띄운다."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):                                     # 뜰 때까지 최대 10초
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError(f"server on {port} did not start")


@pytest.fixture(scope="session")
def stack():
    """DB 초기화 → 사용자·팀·항목 → 세 서버 기동. 테스트 전체가 공유."""
    try:                                                     # Postgres·Redis가 없는 환경(웹 샌드박스 등)이면 건너뜀
        psycopg.connect(DB.rsplit("/", 1)[0] + "/postgres", connect_timeout=3).close()   # 서버 접속만 확인
        redis.Redis.from_url(REDIS, socket_connect_timeout=3).ping()
    except Exception as e:
        pytest.skip(f"Postgres/Redis 없음 — 통합 테스트 건너뜀({type(e).__name__})")
    ensure_database(DB); apply_schema(DB)                    # 테스트 DB 준비
    with psycopg.connect(DB, autocommit=True) as con:        # 이전 실행 흔적 지우고 데이터 넣기
        con.execute("TRUNCATE users, api_tokens, teams, team_members, scopes, team_scopes, llm_usage, tool_calls, index_runs CASCADE")
        con.execute("""
          INSERT INTO users(user_id, is_admin) VALUES ('kim', false), ('lee', false), ('boss', true), ('heavy', false);
          INSERT INTO teams VALUES ('A','팀A'),('B','팀B');
          INSERT INTO team_members VALUES ('A','kim'),('B','lee'),('A','boss'),('B','boss');
          INSERT INTO scopes VALUES ('item-1','shop','billing/'),('item-4','shop','coupon/'),('item-8','shop','admin/'),('guide','guide',''),('git-all','gitrepo','');
          INSERT INTO team_scopes VALUES ('A','item-1'),('A','item-4'),('B','item-4'),('B','item-8'),('A','guide'),('B','guide');""")
        for user, tok in TOK.items():                        # 토큰은 해시로
            con.execute("INSERT INTO api_tokens(token_hash, user_id) VALUES (%s,%s)", (hash_token(tok), user))
    r = redis.Redis.from_url(REDIS); r.flushdb()             # 테스트 Redis 비우기
    auth.clear_cache()

    perms = Permissions(ConnectionPool(DB, min_size=1, max_size=3, open=True), r, ttl_seconds=300)
    rag = create_rag(Store(":memory:"), FakeEmbedder(), perms, {"idx-secret"}, {"mcp-secret"})
    s = Settings()                                           # 코드에이전트 설정을 테스트용으로 덮어쓴다
    s.database_url, s.redis_url = DB, REDIS
    s.rag_url, s.search_token = f"http://127.0.0.1:{PORTS['rag']}", "mcp-secret"
    s.llm_base_url, s.llm_api_key = f"http://127.0.0.1:{PORTS['llm']}/v1", ""
    s.agent_host, s.limit_rpm, s.limit_daily_tokens = "127.0.0.1", 1000, 1_000_000
    servers = [serve(fake_llm_app, PORTS["llm"]), serve(rag, PORTS["rag"]), serve(create_agent(s), PORTS["agent"])]
    yield {"rag": f"http://127.0.0.1:{PORTS['rag']}", "agent": f"http://127.0.0.1:{PORTS['agent']}",
           "perms": perms, "redis": r}
    for sv in servers:
        sv.should_exit = True                                # 정리


def index(stack, body):
    """RAG /index를 색인 토큰으로 호출."""
    return httpx.post(stack["rag"] + "/index", json=body, headers={"X-Service-Token": "idx-secret"}, timeout=30)


@pytest.fixture(scope="session")
def indexed(stack):
    """코드·문서를 색인해 둔다."""
    files = [{"path": "billing/pay.py", "content": "def charge_payment(o):\n    return pg_approve(o)\n"},
             {"path": "coupon/apply.py", "content": "def apply_coupon(order):\n    return order.total * 0.9\n"},
             {"path": "admin/ops.py", "content": "def reset_coupon_cache():\n    pass\n"},
             {"path": "misc/tmp.py", "content": "def coupon_temp():\n    pass\n"}]
    r = index(stack, {"collection": "code", "repo": "shop", "commit": "a1b2c3", "files": files}).json()
    index(stack, {"collection": "docs", "repo": "guide", "commit": "d0c0001",
                  "files": [{"path": "rules.md", "content": "# 가이드\n## 쿠폰 규칙\n쿠폰 할인 금액은 Decimal로 계산한다\n"}]})
    return r


# ── RAG 파이프라인 ─────────────────────────────────────────

def test_chunking():
    py = "import os\n\ndef apply_coupon(o):\n    return o\n\nclass Cart:\n    def total(self):\n        return 1\n"
    assert [c["symbol"] for c in chunk_python(py)] == ["", "apply_coupon", "Cart"]   # 함수·클래스 경계
    md = "# 가이드\n## 쿠폰\n쿠폰 규칙\n## 결제\n결제 규칙\n"
    assert [c["symbol"] for c in chunk_markdown(md)] == ["쿠폰", "결제"]               # 섹션 단위, 머리말 제외
    long = {"start": 1, "end": 4, "symbol": "f", "body": "a b c\nd e f\ng h i\nj k l"}
    parts = fit_to_tokens([long], lambda t: len(t.split()), 6)
    assert len(parts) == 2 and parts[1]["start"] == 3                                 # 토큰 한도 초과 → 반으로


def test_permissions_union_and_empty(stack):
    p = stack["perms"]
    assert p.allowed_scopes("kim") == {"item-1", "item-4", "guide"}
    assert p.allowed_scopes("boss") == {"item-1", "item-4", "item-8", "guide"}        # 두 팀의 합집합
    assert p.allowed_scopes("nobody") == frozenset()                                   # 소속 없음
    assert p.scope_for("shop", "coupon/apply.py") == "item-4" and p.scope_for("shop", "misc/x.py") is None


def test_index_and_filtered_search(stack, indexed):
    assert index(stack, {"collection": "code", "repo": "shop", "commit": "x", "files": []}).status_code == 200
    assert httpx.post(stack["rag"] + "/index", json={"collection": "code", "repo": "shop", "commit": "x"}).status_code == 401
    assert indexed["skipped_no_scope"] == ["misc/tmp.py"]                             # 항목 없는 파일은 색인 안 함

    def search(user):
        return httpx.post(stack["rag"] + "/search", json={"collection": "code", "query": "coupon", "k": 10},
                          headers={"X-Service-Token": "mcp-secret", "X-User-Id": user}).json()
    paths = lambda d: sorted({x["path"] for x in d["results"]})
    assert paths(search("kim")) == ["billing/pay.py", "coupon/apply.py"]              # 팀A
    assert paths(search("lee")) == ["admin/ops.py", "coupon/apply.py"]                # 팀B
    assert search("nobody") == {"results": [], "denied": True}                        # 권한 없음 → 검색 안 함


def test_incremental_indexer_from_git_url(stack, tmp_path):
    origin = tmp_path / "origin"; (origin / "coupon").mkdir(parents=True)             # '원격' 저장소(로컬 경로 git 주소)
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "coupon" / "apply.py").write_text("def apply_coupon(o):\n    return o\n")
    (origin / ".env").write_text("SECRET=1\n")
    (origin / "쿠폰규칙.md").write_text("## 규칙\n할인은 Decimal\n")                       # 한글 파일명(git 경로 인코딩 확인)
    run("add", "-A"); run("commit", "-qm", "1")
    spec = parse_repos(f"gitrepo|{origin}|main|code")[0]                                 # 환경변수 형식 해석
    assert (spec.name, spec.branch, spec.collection) == ("gitrepo", "main", "code")   # 공유 색인의 shop과 겹치지 않게
    posted = []
    def post(body):
        posted.append(body); assert index(stack, body).status_code == 200
    st = str(tmp_path / "state.json")
    repo_dir = sync_repo(spec, str(tmp_path / "cache"))                               # clone
    first = index_repo(repo_dir, "gitrepo", post, st)
    assert first["full"] and sorted(f["path"] for f in posted[0]["files"]) == ["coupon/apply.py", "쿠폰규칙.md"]   # .env 제외, 한글 이름 그대로
    (origin / "coupon" / "apply.py").unlink(); (origin / "coupon" / "new.py").write_text("def new_rule():\n    pass\n")
    run("add", "-A"); run("commit", "-qm", "2")
    second = index_repo(sync_repo(spec, str(tmp_path / "cache")), "gitrepo", post, st)  # pull 후 증분
    assert not second["full"] and second["deleted"] == 1 and posted[-1]["deleted"] == ["coupon/apply.py"]


# ── request_fix 처리 ───────────────────────────────────────

def test_fixer_blocks_and_validation():
    code = "def f(x):\n    return x\n"
    blocks = parse_blocks("<<<<<<< SEARCH\n    return x\n=======\n    return x + 1\n>>>>>>> REPLACE")
    new, errors = apply_blocks(code, blocks)
    assert not errors and "x + 1" in new and validate("a.py", code, new) is None     # 적용·검사 통과
    _, errors = apply_blocks(code, parse_blocks("<<<<<<< SEARCH\nnope\n=======\nx\n>>>>>>> REPLACE"))
    assert "원본에 없습니다" in errors[0]                                              # 없는 글
    assert "문법 오류" in validate("a.py", code, "def f(x:\n")                         # 파이썬 문법
    assert "괄호" in validate("A.java", "f(a);", "f(a;")                               # 그 밖의 언어: 괄호 균형


# ── 코드에이전트 서버: /v1 프록시 ─────────────────────────────

TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {}}}}]


def chat(stack, token, stream, content="안녕", tools=None):
    body = {"model": "아무거나", "stream": stream, "messages": [{"role": "user", "content": content}]}
    if tools:
        body["tools"] = tools                                # Continue Agent 모드처럼 도구 목록 포함
    return httpx.post(stack["agent"] + "/v1/chat/completions", timeout=30,
                      headers={"Authorization": f"Bearer {token}"} if token else {}, json=body)


def last_usage(user):
    with psycopg.connect(DB) as con:
        return con.execute("SELECT source, stream, total_tokens, estimated, status FROM llm_usage "
                           "WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user,)).fetchone()


def test_proxy_auth_stream_usage_and_limit(stack):
    assert chat(stack, None, False).status_code == 401                                # 토큰 없음
    r = chat(stack, TOK["kim"], False)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"].startswith("가짜 응답")
    assert last_usage("kim")[:2] == ("chat", False) and last_usage("kim")[2] > 0     # 사용량 기록
    r = chat(stack, TOK["kim"], True, "TOOL 써줘", TOOLS)                                      # 스트리밍 + 도구 호출 그대로 전달
    body = r.text
    assert '"tool_calls"' in body and body.strip().endswith("data: [DONE]")
    time.sleep(0.3)                                                                    # 기록은 스트림 종료 직후
    src, stream, total, est, status = last_usage("kim")
    assert stream and total > 0 and not est and status == 200                          # include_usage로 실제 수 수집
    stack["redis"].set(f"rl:tok:heavy:{dt.datetime.now():%Y%m%d}", 10**9)              # 일일 한도를 다 쓴 사용자
    r = chat(stack, TOK["heavy"], False)
    assert r.status_code == 429 and "일일 토큰 한도" in r.json()["error"]["message"]


# ── 코드에이전트 서버: /mcp 도구 ─────────────────────────────

async def mcp_session(stack, token, fn):
    """MCP 클라이언트로 접속해 fn(session)을 실행."""
    import httpx2                                            # MCP SDK 2.x가 쓰는 HTTP 클라이언트
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)
    async with streamable_http_client(stack["agent"] + "/mcp", http_client=http) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            return await fn(s)


def test_mcp_search_permissions(stack, indexed):
    async def go(s):
        names = [t.name for t in (await s.list_tools()).tools]
        res = await s.call_tool("search_code", {"query": "coupon"})
        return names, res.content[0].text
    names, kim = asyncio.run(mcp_session(stack, TOK["kim"], go))
    _, lee = asyncio.run(mcp_session(stack, TOK["lee"], go))
    _, anon = asyncio.run(mcp_session(stack, None, go))
    assert names == ["search_code", "search_docs", "request_fix"]
    assert "coupon/apply.py" in kim and "admin/ops.py" not in kim                      # 김(팀A)은 admin 못 봄
    assert "admin/ops.py" in lee                                                       # 이(팀B)는 봄
    assert "인증 정보가 없습니다" in anon
    assert "[main@a1b2c3]" in kim and "로컬 파일을 확인" in kim


def test_mcp_request_fix_with_refs_and_retry(stack, indexed):
    code = "def apply_coupon(order):\n    return order.total * 0.9\n"
    async def go(s):
        ok = await s.call_tool("request_fix", {"path": "coupon/apply.py", "code": code, "instruction": "쿠폰 할인 규칙 적용"})
        retry = await s.call_tool("request_fix", {"path": "coupon/apply.py", "code": code, "instruction": "FAIL_ONCE 쿠폰"})
        return ok.content[0].text, retry.content[0].text
    ok, retry = asyncio.run(mcp_session(stack, TOK["kim"], go))
    assert "찾을 글:" in ok and "# 수정됨" in ok and "파이썬 문법 통과(생성 1회)" in ok
    assert "guide/rules.md" in ok                                                      # 사내 규칙 문서를 참고함
    assert "생성 2회" in retry                                                         # 형식 위반 → 오류 붙여 재생성
    with psycopg.connect(DB) as con:
        n = con.execute("SELECT count(*) FROM llm_usage WHERE user_id='kim' AND source='request_fix'").fetchone()[0]
    assert n == 3                                                                      # 안쪽 LLM 호출 1 + 2


# ── 감시 ──────────────────────────────────────────────────

def test_admin_summary(stack, indexed):
    url = stack["agent"] + "/admin/api/summary"
    assert httpx.get(url, headers={"Authorization": f"Bearer {TOK['kim']}"}).status_code == 403   # 일반 사용자 거부
    d = httpx.get(url, headers={"Authorization": f"Bearer {TOK['boss']}"}).json()
    assert {"by_user", "by_day", "by_tool", "recent_tools", "index_runs"} <= set(d)
    assert "<title>코드에이전트 감시</title>" in httpx.get(stack["agent"] + "/admin").text
