-- db/schema.sql — 코드에이전트 공용 DB(PostgreSQL) 스키마. 여러 번 실행해도 안전하게 IF NOT EXISTS로 만든다
-- 담는 것: 사람·개인 토큰(인증), 팀·항목(검색 권한), LLM 사용량·도구 호출·색인 기록(감시)

-- ── 인증 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (                      -- 사람(사번 단위)
    user_id    TEXT PRIMARY KEY,                        -- 사번(예: emp-001). 모든 기록의 기준 키
    name       TEXT NOT NULL DEFAULT '',                -- 표시 이름
    is_admin   BOOLEAN NOT NULL DEFAULT FALSE,          -- 감시 화면(/admin)을 볼 수 있는지
    active     BOOLEAN NOT NULL DEFAULT TRUE,           -- FALSE면 토큰이 있어도 거부(퇴사·정지)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()       -- 만든 시각
);

CREATE TABLE IF NOT EXISTS api_tokens (                 -- 개인 토큰 — Continue 설정에 넣는 값
    token_hash   TEXT PRIMARY KEY,                      -- 토큰 원문이 아니라 SHA-256 값만 저장(DB가 새도 토큰은 안전)
    user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,  -- 누구의 토큰인지
    label        TEXT NOT NULL DEFAULT '',              -- 메모(예: "노트북")
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),    -- 발급 시각
    revoked_at   TIMESTAMPTZ,                           -- 폐기 시각(NULL이면 유효)
    last_used_at TIMESTAMPTZ                            -- 마지막 사용 시각(감시용)
);

-- ── 검색 권한(2판 md 5-6, 방식 B: 점에는 scope만, 팀→scope는 여기) ─────
CREATE TABLE IF NOT EXISTS teams (                      -- 팀
    team_id TEXT PRIMARY KEY,                           -- 팀 ID
    name    TEXT NOT NULL DEFAULT ''                    -- 팀 이름
);
CREATE TABLE IF NOT EXISTS team_members (               -- 사람 ↔ 팀(여러 팀 소속 가능)
    team_id TEXT REFERENCES teams(team_id) ON DELETE CASCADE,   -- 팀
    user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,   -- 사람
    PRIMARY KEY (team_id, user_id)                              -- 같은 소속을 두 번 넣지 않음
);
CREATE TABLE IF NOT EXISTS scopes (                     -- 항목 = 저장소 + 경로 접두어
    scope_id    TEXT PRIMARY KEY,                       -- 항목 ID(Qdrant payload의 scope 값)
    repo        TEXT NOT NULL,                          -- 저장소 이름(INDEX_REPOS의 이름)
    path_prefix TEXT NOT NULL DEFAULT ''                -- 경로 접두어(빈 문자열 = 저장소 전체)
);
CREATE TABLE IF NOT EXISTS team_scopes (                -- 팀 ↔ 볼 수 있는 항목
    team_id  TEXT REFERENCES teams(team_id) ON DELETE CASCADE,    -- 팀
    scope_id TEXT REFERENCES scopes(scope_id) ON DELETE CASCADE,  -- 항목
    PRIMARY KEY (team_id, scope_id)                               -- 중복 방지
);
CREATE TABLE IF NOT EXISTS sync_log (                   -- 권한 원본(Git 서버 그룹)과 맞춘 기록
    source    TEXT NOT NULL,                            -- 원본 이름
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),       -- 맞춘 시각
    status    TEXT NOT NULL                             -- 결과
);

-- ── 감시(사용량·도구·색인) — 코드 원문은 저장하지 않는다 ─────────────
CREATE TABLE IF NOT EXISTS llm_usage (                  -- LLM 호출 한 번 = 한 줄
    id                BIGSERIAL PRIMARY KEY,            -- 일련번호
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 호출 시각
    user_id           TEXT NOT NULL,                    -- 누가
    source            TEXT NOT NULL,                    -- 'chat'(Continue 채팅 프록시) / 'request_fix'(도구 안쪽)
    model             TEXT NOT NULL,                    -- 실제로 부른 모델 이름
    stream            BOOLEAN NOT NULL DEFAULT FALSE,   -- 스트리밍 여부
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,       -- 입력 토큰
    completion_tokens INTEGER NOT NULL DEFAULT 0,       -- 출력 토큰
    total_tokens      INTEGER NOT NULL DEFAULT 0,       -- 합계
    estimated         BOOLEAN NOT NULL DEFAULT FALSE,   -- LLM이 usage를 안 줘서 글자 수로 추정했는지
    latency_ms        INTEGER NOT NULL DEFAULT 0,       -- 걸린 시간
    status            INTEGER NOT NULL DEFAULT 200,     -- HTTP 상태(오류 감시용)
    error             TEXT NOT NULL DEFAULT ''          -- 오류 요약(원문 코드 없음)
);
CREATE INDEX IF NOT EXISTS llm_usage_user_ts ON llm_usage (user_id, ts);   -- 사용자별·기간별 집계를 빠르게

CREATE TABLE IF NOT EXISTS tool_calls (                 -- MCP 도구 호출 한 번 = 한 줄
    id         BIGSERIAL PRIMARY KEY,                   -- 일련번호
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 시각
    user_id    TEXT NOT NULL,                           -- 누가(인증 실패면 '-')
    tool       TEXT NOT NULL,                           -- 도구 이름
    latency_ms INTEGER NOT NULL DEFAULT 0,              -- 걸린 시간
    ok         BOOLEAN NOT NULL DEFAULT TRUE,           -- 성공 여부
    detail     TEXT NOT NULL DEFAULT ''                 -- 요약(결과 개수·재시도 횟수 등, 코드 원문 없음)
);
CREATE INDEX IF NOT EXISTS tool_calls_ts ON tool_calls (ts);   -- 최근 기록 조회용

CREATE TABLE IF NOT EXISTS index_runs (                 -- 색인 작업 한 번 = 한 줄
    id         BIGSERIAL PRIMARY KEY,                   -- 일련번호
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 시각
    repo       TEXT NOT NULL,                           -- 저장소 이름
    commit_sha TEXT NOT NULL DEFAULT '',                -- 색인한 커밋
    sent_files INTEGER NOT NULL DEFAULT 0,              -- 보낸 파일 수
    deleted    INTEGER NOT NULL DEFAULT 0,              -- 삭제 반영 수
    full_index BOOLEAN NOT NULL DEFAULT FALSE,          -- 전체 색인이었는지
    status     TEXT NOT NULL DEFAULT 'ok'               -- 'ok' 또는 오류 요약
);
