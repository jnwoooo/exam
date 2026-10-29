# 하위 에이전트(Subagent) 설정 예시 — 계획 13-5 추천 구성

> 구현: `ncn/subagent.py`(부록B B16 — 하위 대화마다 독립 객체). 켜기: `ncn --beta-subagent-tool`(cn과 같은 베타 플래그).
> 설정은 두 파일로 나눈다. **`config.yaml` = Continue 설정 그대로**(cn·IDE 확장도 읽음), **`ncn.yaml` = ncn 전용 제한**(Continue가 모르는 키라 따로 — 계획 13-6).

## 1. `~/.continue/config.yaml` — 하위 에이전트 = `roles: [subagent]` 모델

| 항목 | 뜻 |
|---|---|
| `name` | 하위 에이전트 이름 = `Subagent` 도구의 `subagent_name` 값 |
| `roles: [subagent]` | 이 역할이 있어야 목록에 들어감 |
| `chatOptions.baseSystemMessage` | **필수**(없으면 목록에서 빠짐). 도구 설명의 `- 이름: …` 줄 + 하위 대화 시스템 메시지 끝에 붙는 지시 |

```yaml
name: local
version: 0.0.1
schema: v1
models:
  - name: gemma4                       # 메인(chat) 모델
    provider: openai
    model: gemma4-26b-32k:latest
    apiBase: http://localhost:8200/v1
    apiKey: ${{ secrets.CODEAGENT_TOKEN }}
    roles: [chat, edit, apply]
    capabilities: [tool_use]
    defaultCompletionOptions:
      contextLength: 32768
      maxTokens: 4096

  - name: explorer                     # 탐색 전담(읽기 전용) — 작은 모델이 있으면 그것으로
    provider: openai
    model: gemma4-26b-32k:latest
    apiBase: http://localhost:8200/v1
    apiKey: ${{ secrets.CODEAGENT_TOKEN }}
    roles: [subagent]
    capabilities: [tool_use]
    defaultCompletionOptions:
      contextLength: 32768
      maxTokens: 2048
    chatOptions:
      baseSystemMessage: 코드 탐색 전담. 파일을 수정하지 않는다. 결과는 경로·함수·줄 범위와 근거만 1000토큰 이내로.

  - name: runner                       # 빌드·테스트 실행 후 실패 분석
    provider: openai
    model: gemma4-26b-32k:latest
    apiBase: http://localhost:8200/v1
    apiKey: ${{ secrets.CODEAGENT_TOKEN }}
    roles: [subagent]
    capabilities: [tool_use]
    chatOptions:
      baseSystemMessage: 빌드·테스트 실행 전담. 실패한 테스트 이름, 오류 위치(파일:줄), 원인 요약만 돌려준다. 코드는 고치지 않는다.

mcpServers:
  - name: codeagent                    # search_code·search_docs 를 주는 사내 MCP
    type: streamable-http
    url: http://localhost:8200/mcp
```

## 2. `~/.continue/ncn.yaml` — ncn 전용 제한(선택)

| 키 | 기본 | 뜻 |
|---|---|---|
| `subagents.<이름>.tools` | 없음 = 전부 allow(cn과 같음) | 허용 도구 목록. 권한 패턴 문법(`Read`, `search_*`, `Bash(npm test*)`). 목록 밖 도구는 요청에서 빠지고, 불러도 `Command blocked by security policy` |
| `subagents.<이름>.max_result_chars` | 없음 | 메인에 돌려줄 응답 최대 글자 수(넘으면 자르고 `[ncn: subagent result truncated to N characters]`) |
| `subagent_max_parallel` | 2 | 메인이 한 응답에서 여러 개를 불렀을 때 동시에 도는 수(중첩 호출은 세지 않음) |

```yaml
subagent_max_parallel: 2               # LLM 서버 동시 처리 수에 맞춘다(외부 서버가 순차 처리면 1)
subagents:
  explorer:                            # 읽기 전용
    tools: [Read, List, Search, search_code, search_docs]
    max_result_chars: 4000
  runner:                              # 테스트·빌드 명령만 + 로그 읽기
    tools: ["Bash(npm test*)", "Bash(npm run build*)", "Bash(dotnet test*)", "Bash(dotnet build*)", "Bash(uv run pytest*)", Read]
    max_result_chars: 3000
```

- 파일이 없거나 문법이 틀리면 경고만 남기고 cn과 같게 동작(전부 allow, 제한 없음). 틀린 항목은 그 항목만 무시.
- `Bash(...)` 패턴은 명령 전체 일치(글롭). `Bash` 위험 명령 판정(`rm -rf /` 등 차단)은 목록과 무관하게 그대로.

## 3. 동작 요약(cn과 다른 점 — 부록 B)

| 항목 | cn(5522c6f) | ncn 기본 | `NCN_CN_COMPAT=1` |
|---|---|---|---|
| 헤드리스 `-p` | `No active session found`로 실패 | **실제로 실행** | cn과 같게 실패 |
| 동시 실행 | 전역 권한·시스템 메시지·기록을 바꿔 끼움 → 섞이고 굳음(P0 4장) | 하위 대화마다 독립 객체(기록·권한·시스템 메시지·LLMClient) | 독립 객체(경쟁 버그는 재현 안 함), 제한 없음 |
| 권한 | 전부 allow | `ncn.yaml` 제한(없으면 전부 allow) | 전부 allow |
| ACP 취소 | 하위 대화와 연결 안 됨(Esc 이벤트만) | `session/cancel` → 하위 대화도 중단 | 연결 안 됨(턴 작업 취소로만 끝남) |
| ACP 진행 표시 | (TUI 기록 갱신) | 부모 `tool_call` 카드 `content` + `_meta.ncn.subagent{name, outputChars}` | 같음 |
| 요청 본문(도구 이름·설명·인자) | — | cn과 같음(차등 시험 `25_subagent_compat`) | 같음 |

- 하위 대화 시스템 메시지 = 메인 시스템 메시지(지금 모드) + 빈 줄 + `baseSystemMessage`. 첫 사용자 메시지 = `prompt`.
- 결과 글 = 하위 대화 마지막 메시지 + `<task_metadata>\nstatus: completed|failed\n</task_metadata>`(cn 원문 형식).
- 대화형(ACP) 기본 권한에서 `Subagent`는 ask → IDE에 "Spawning explorer to: …" 승인을 묻는다(`--auto` 또는 permissions.yaml `allow: [Subagent]`로 생략).
