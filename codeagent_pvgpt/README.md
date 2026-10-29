# codeagent_pvgpt — 사용법

Continue(IntelliJ)를 하네스로 쓰고, **코드에이전트 서버**(LLM 프록시 + MCP 도구 + 감시)와 **RAG 서비스**(추출·청킹·KoE5 임베딩·Qdrant 삽입·권한 검색)를 로컬에서 돌린다.
결정·질답은 `진행기록_질답.md`, 설계 배경은 `코딩에이전트_로컬방식_구현상세_continue버전.md`.

```
IntelliJ + Continue ──/v1 (채팅)──▶ 코드에이전트 서버 :8200 ──▶ 외부 LLM (gemma4-26b-32k)
                    └─/mcp (도구)─▶   ├ search_code / search_docs / request_fix
                                      └ /admin 감시          ──▶ RAG 서비스 :8100 ──▶ Qdrant(docker)
색인기 CLI: INDEX_REPOS의 git 주소 → .repos/ 에 clone/pull → 바뀐 파일만 ──▶ RAG /index
Postgres(docker): 사용자·토큰·권한·사용량·도구 기록   Redis(docker): 한도 카운터·권한 캐시
```

## 1. 처음 한 번
```bash
cd codeagent_pvgpt
uv sync                                   # 의존성(파이썬 3.13 가상환경 .venv)
cp .env.example .env                      # 설정 — 서비스 토큰을 아무 긴 문자열로 바꾸고 LLM_BASE_URL을 실제 주소로
(cd ../../docker/qdrant && docker compose up -d)     # Qdrant :6333
(cd ../../docker/redis && docker compose up -d)      # Redis  :6379
(cd ../../docker/postgres && docker compose up -d)   # Postgres :5432
uv run python -m common.db                # DB 'codeagent' 생성 + 스키마
```

## 2. 사용자·권한 등록
```bash
M="uv run python -m agent_server.manage"
$M add-user emp-001 --name 김개발 --admin        # --admin이면 /admin 감시 화면 접근 가능
$M issue-token emp-001 --label 노트북            # ★ 출력된 토큰(ca-...)은 이때만 보인다
$M add-team dev --name 개발팀
$M join dev emp-001
$M add-scope shop-all shop                       # 항목 = 저장소 shop 전체 (--prefix coupon/ 처럼 경로 단위도 가능)
$M add-scope guide-all guide
$M grant dev shop-all
$M grant dev guide-all
$M list
```
**항목(scope)이 없는 저장소·경로의 파일은 색인하지 않는다**(실수로 전체 공개 방지). 색인 전에 항목부터 등록한다.

## 3. 서버 실행(터미널 2개)
```bash
uv run uvicorn rag_service.app:main_app --factory --port 8100   # RAG(시작 시 KoE5 로딩, 첫 실행은 모델 다운로드)
uv run python -m agent_server.app                               # 코드에이전트 :8200
```
외부 LLM 연결 전에는 가짜 LLM으로 시험할 수 있다:
```bash
uv run python -m tools.fake_llm                                 # :8300
LLM_BASE_URL=http://127.0.0.1:8300/v1 uv run python -m agent_server.app
```

## 4. 색인
`.env`의 `INDEX_REPOS`에 `이름|git주소|브랜치|컬렉션`을 `;`로 나열한다(이름 = 2장의 항목 저장소 이름).
```bash
uv run python -m rag_service.indexer            # 전부(첫 실행은 전체, 이후는 git diff 증분)
uv run python -m rag_service.indexer shop       # 하나만
```
주기 실행이 필요하면 cron 등으로 10분마다 위 명령을 돌린다.

## 5. Continue 연결(IntelliJ)
1. `continue/config.yaml`을 `~/.continue/config.yaml`로 복사
2. `~/.continue/.env`에 `CODEAGENT_TOKEN=ca-...`(2장에서 발급한 토큰)
3. IntelliJ Continue 패널 → Agent 모드 → 모델 "gemma4 (코드에이전트 경유)" 선택, MCP 도구 3개가 보이는지 확인
4. 도구 정책: `read_file`·검색류·MCP 도구는 Automatic, 편집·터미널은 Ask First(2판 md 3-6)

## 6. 감시
브라우저에서 `http://localhost:8200/admin` → 관리자 토큰 입력 → 사용자별 토큰·일별 사용량·도구 호출·LLM 오류·색인 기록.
한도: `LIMIT_RPM`(분당 요청), `LIMIT_DAILY_TOKENS`(하루 토큰) — 넘으면 Continue에 429 메시지가 뜬다.
ncn 감사 기록: `POST /audit/events`(Bearer 개인 토큰, 본문 `{"events":[...]}`) — ncn이 `~/.continue/ncn.yaml`의 `audit: {server: "http://<서버>:8200"}`로 보내면 `ncn_audit` 테이블에 쌓이고 `/admin` 아래쪽에 사용자별·도구별·거부 수·최근 사건이 보인다(`newcontinue/추가기능_가이드.md` E6).

## 7. 외부 LLM 연결 확인 · 참고 구현(llm_server)
```bash
uv run python -m llm_server.check_compat                         # .env의 LLM_BASE_URL이 OpenAI 호환인지 ①②③ 확인
uv run python -m llm_server.check_compat --base-url http://호스트:포트/v1
```
`llm_server/`는 "외부 호스트에 떠 있을 FastAPI"를 예측해 만든 참고 구현이다(Ollama의 gemma4를 OpenAI 호환 `/v1`로 감쌈).
외부 서버가 체크를 통과하지 못하면 그 서버를 이 모양으로 고치거나, 그 서버 앞/뒤에 `llm_server`를 둔다.
```bash
uv run python -m tools.fake_ollama                               # Ollama가 없을 때 가짜(:11434)
uv run python -m llm_server.app                                  # :8000 → 코드에이전트 .env의 LLM_BASE_URL=http://0.0.0.0:8000/v1 과 맞음
```

## 8. 테스트
```bash
uv run pytest -q        # 14개. 가짜 임베딩·가짜 LLM·가짜 Ollama + docker Postgres(codeagent_test DB)·Redis(15번)
```

## 구조
```
codeagent_pvgpt/
├── common/            settings.py(환경변수·.env) · db.py(DB 생성·스키마·토큰 해시)
├── db/schema.sql      사용자·토큰·팀·항목·사용량·도구 호출·색인 기록·ncn 감사
├── rag_service/       embedder(KoE5) · chunking · store(Qdrant) · permissions(Postgres+Redis) · app(/index·/search) · indexer(git)
├── agent_server/      app(조립) · proxy(/v1) · mcp_tools(/mcp) · fixer(request_fix 검증) · auth · usage · admin · audit(/audit/events) · manage(CLI)
├── llm_server/        (참고 구현) 외부 LLM 앞의 FastAPI: app(OpenAI /v1 → Ollama) · convert(형식 변환) · check_compat(①②③ 확인)
├── tools/             fake_llm.py(가짜 OpenAI 호환 LLM) · fake_ollama.py(가짜 Ollama)
├── continue/config.yaml
└── tests/             test_all.py · test_llm_server.py
```
