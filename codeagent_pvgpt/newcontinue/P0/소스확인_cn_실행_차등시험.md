# 소스 확인: cn 헤드리스 실행 · 요청 본문 · 차등 시험 준비

- 대상: `.ref/continue` 커밋 `5522c6f` (2026-07-20, "docs: remove Sign in link (login flow retired)")
- 경로 표기: 따로 적지 않으면 `extensions/cli/src/` 기준입니다. `core/…`, `packages/…`는 저장소 루트 기준입니다.
- 방법: 소스를 읽기만 했습니다. 빌드·설치·실행은 하지 않았습니다. "(추정)"이 붙은 항목은 저장소에 없는 외부 라이브러리(openai-node) 동작이라 소스로 확인하지 못한 내용입니다.

---

## 0. 차등 시험에 바로 영향을 주는 사실 (먼저 읽을 것)

1. **이 커밋에서는 Hub 로그인이 제거됐습니다.** `auth/workos.ts:1-151`은 전부 no-op 스텁이고, `AuthService`는 항상 `authConfig: null`을 돌려줍니다(`services/AuthService.ts:19-27`). 그래서 로컬 config.yaml만으로 동작합니다.
2. **`isFirstMessage` 버그 때문에 헤드리스에서는 제목 생성 LLM 호출이 일어나지 않습니다.** `chat.ts:522`에서 `isFirstMessage = false`를 먼저 하고, `chat.ts:533`에서 `firstAssistantResponse: isFirstMessage && …`를 넘기므로 값이 항상 false입니다. 따라서 `-p` 1회 실행 중 가짜 LLM으로 가는 요청은 대화 루프 요청뿐입니다. 자동 압축 조건에 걸리면 압축 요청이 추가됩니다.
3. **헤드리스 `cn -p`에서는 Subagent 도구가 항상 실패합니다.** ChatHistoryService가 `sessionId: ""`로 초기화되고(`services/ChatHistoryService.ts:95-101`, `services/index.ts:295`), 이 값을 세팅하는 곳은 `loadSession`(`ChatHistoryService.ts:387-392`)과 `serve`(`commands/serve.ts:158`)뿐입니다. 그래서 `tools/subagent.ts:75-78`에서 `"No active session found"` 예외가 납니다. 도구 결과는 `"Error executing tool Subagent: No active session found"`가 됩니다(`stream/streamChatResponse.helpers.ts:591-593`). ncn도 같은 문자열을 내야 합니다.
4. **모델 이름이 `gpt-5*` 또는 `o[0-9]*`이면 apiBase와 상관없이 `/responses` 엔드포인트로 갑니다.** `util/exponentialBackoff.ts:185-191`이 `isResponsesModel(params.model)`만 확인하고 `llmApi.responsesStream`을 직접 부르기 때문입니다. 정규식은 `/^(?:gpt-5|gpt-5-codex|o[0-9])/i`(`packages/openai-adapters/src/apis/openaiResponses.ts:37`)입니다. OpenAIApi 내부의 "공식 API일 때만" 검사(`apis/OpenAI.ts:90-96`)를 거치지 않습니다. **시험용 모델 이름은 `fake-model`처럼 이 정규식에 걸리지 않는 이름을 쓰십시오.**
5. **`ANTHROPIC_API_KEY`가 환경에 있고 `--config`가 없으면** 헤드리스 실행이 `$CONTINUE_GLOBAL_DIR/config.yaml`을 덮어씁니다(`services/index.ts:79-94`). 시험 환경에서는 이 변수를 unset하고 항상 `--config`를 주십시오.
6. **`version.ts`는 모듈을 불러오는 순간 `https://api.continue.dev/cn/info?id=<machine-id>`를 무조건 fetch합니다**(`version.ts:29-62, 65-75`). 끄는 환경변수가 없고, `CONTINUE_API_BASE`로 주소를 바꿀 수도 없습니다(하드코딩). 실패해도 조용히 무시되지만, 폐쇄망이 아니면 외부로 나갑니다. → 네트워크 격리로 막아야 합니다(2.5절).
7. **`dotenv.config()`가 세 군데에서 cwd의 `.env`를 읽습니다**(`env.ts:4-6`, `stream/streamChatResponse.ts:41`, `packages/openai-adapters/src/index.ts:29`). 시험 작업 폴더에 `.env`가 있으면 환경이 바뀝니다. 예: `CONTINUE_USE_AI_SDK`가 켜지면 다른 HTTP 경로로 갑니다(`packages/openai-adapters/src/index.ts:85-96`).

---

## 1. 패키지 이름·버전

| 항목 | 값 | 근거 |
|---|---|---|
| 패키지 이름 | `@continuedev/cli` | `extensions/cli/package.json:2` |
| 버전 | `0.0.0-dev` (배포 시 semantic-release가 채움) | `package.json:3`, `:34-35` |
| 실행 파일 | `cn` → `dist/cn.js` | `package.json:8-10` |
| 번들 | esbuild, 진입점 `src/index.ts`, `dist/cn.js`는 `runCli()`를 부르는 래퍼 | `extensions/cli/build.mjs:36-37, 92-96` |
| LLM 어댑터 | `@continuedev/openai-adapters` = `file:../../packages/openai-adapters` | `package.json:55` |
| openai SDK | 어댑터 쪽 `openai@5.23.2` (CLI 최상위 lock에는 4.104.0도 있지만, 어댑터는 자기 node_modules 것을 씀) | `packages/openai-adapters/package-lock.json:12313-12315`, `extensions/cli/package-lock.json:15470-15472` |

- 버전 문자열은 런타임에 `dist/../package.json`에서 읽습니다(`version.ts:9-20`). 소스에서 빌드하면 `0.0.0-dev`가 되고, 이 경우 업데이트 확인을 건너뜁니다(`services/UpdateService.ts:54-60`).

---

## 2. 헤드리스 `cn -p "..."` 실행 조건

### 2.1 로그인·Hub 없이 로컬 config만으로 되는가 → **됩니다(조건 있음)**

설정 출처 우선순위는 `configLoader.ts:84-100`에 있습니다.
1. `--config <path>`. 경로 형태(`.`, `/`, `~`로 시작하거나 `.yaml`/`.yml`/`.json`을 포함)이면 로컬 파일로 읽습니다(`configLoader.ts:191-217, 511-525`).
2. 없으면 `$CONTINUE_GLOBAL_DIR/config.yaml`을 씁니다(`configLoader.ts:95-98, 307-321`).
3. 그것도 없으면 `remote-default-config`, 즉 `apiClient.getAssistant(continuedev/default-cli-config)`로 네트워크 호출을 합니다(`configLoader.ts:99, 326-355`). 폐쇄망에서는 실패하고 종료 코드 1이 됩니다.

네트워크를 타지 않으려면 지켜야 할 것:
- 모델 블록을 `uses:` 없이 인라인으로 적으십시오(`provider: openai`, `model`, `apiBase`, `apiKey`). `uses:`/slug는 Hub 레지스트리로 갑니다(`configLoader.ts:392-428`의 `unrollAssistant` + `CLIPlatformClient`).
- `${{ secrets.X }}`를 쓰지 마십시오. 값은 리터럴로 적습니다. `renderSecrets: true`로 platformClient를 부릅니다(`configLoader.ts:407-413`).
- chat 역할 모델이 하나도 없으면 `anthropic/claude-sonnet-4-6`을 Hub에서 받아오려 하고, 헤드리스에서는 실패합니다(`services/ConfigService.ts:31-38, 214-248`).
- `--rule`/`--prompt`/`--mcp`/`--model`/`--agent`에 slug(`owner/pkg`)를 넣으면 Hub 호출이 일어납니다(`services/ConfigService.ts:74-206`). 문자열 규칙은 로컬에서 처리합니다.
- `apiClient`는 항상 `CONTINUE_API_BASE`(기본 `https://api.continue.dev/`)로 만들어집니다(`config.ts:90-99`, `env.ts:9`). 위 경우가 아니면 호출되지 않습니다.

예시 config (시험용):
```yaml
name: difftest
version: 0.0.1
schema: v1
models:
  - name: fake
    provider: openai
    model: fake-model          # gpt-5*/o[0-9]* 금지 (0장 4번)
    apiBase: http://127.0.0.1:18080/v1/
    apiKey: sk-test
    roles: [chat]
    # defaultCompletionOptions: {maxTokens: 512, temperature: 0}
```

### 2.2 `--config` 옵션
- 정의: `shared-options.ts:6-9` (`--config <path>`: 파일 경로 또는 hub slug).
- `--config`를 주면 이 값이 저장 상태보다 항상 우선합니다(`services/index.ts:206-214`).
- 설정 URI를 저장하는 `updateConfigUri`는 no-op입니다(`auth/workos.ts:90-92`). 따라서 auth.json에 아무것도 쓰지 않습니다.

### 2.3 관련 환경변수

| 변수 | 효과 | 근거 |
|---|---|---|
| `CONTINUE_GLOBAL_DIR` | 홈 폴더(`~/.continue`)를 바꿉니다. config.yaml, sessions/, logs/cn.log, index/globalContext.json, permissions.yaml, rules/, skills/, settings.json 위치가 모두 따라갑니다. 상대경로는 cwd 기준 절대경로로 바뀝니다. | `env.ts:10-11`, `session.ts:48-51`, `core/util/paths.ts:27-35, 69-110`, `util/logger.ts:17,30`, `permissions/permissionsYamlLoader.ts:12`, `systemMessage.ts:93`, `hooks/hookConfig.ts:75-77` |
| `HOME` | **`~/.claude/settings.json`은 CONTINUE_GLOBAL_DIR과 상관없이 실제 홈에서 읽습니다**(hook 설정). 이 커밋에서 hook은 로드만 되고 발화되지 않습니다(`hooks/fireHook.ts`의 fire* 함수를 hooks/ 밖에서 import하는 곳이 없음). 그래도 격리를 위해 HOME도 임시 폴더로 두기를 권장합니다. | `hooks/hookConfig.ts:73-89`, `hooks/HookService.ts:44-49` |
| `CONTINUE_API_BASE` | Hub API 주소입니다. 죽은 주소(`http://127.0.0.1:9/`)로 두면 실수로 일어난 Hub 호출이 빨리 실패합니다. 버전 확인 URL에는 적용되지 않습니다. | `env.ts:9` |
| `ANTHROPIC_API_KEY` | `--config` 없이 헤드리스로 돌면 config.yaml을 생성·덮어씁니다. **unset 필수.** | `services/index.ts:79-94` |
| `CONTINUE_CLI_TEST_SESSION_ID` | 세션 ID를 고정합니다(uuid 대신). | `session.ts:96-98` |
| `CONTINUE_CLI_TEST` + `HOME` | 세션 폴더를 `$HOME/.continue/sessions`로 바꿉니다. 단, 실제 저장(`core/util/history.ts`)은 CONTINUE_GLOBAL_DIR 경로를 쓰므로 두 경로가 어긋날 수 있습니다. stdin 읽기도 끕니다. **쓰지 않기를 권장합니다.** | `session.ts:37-46`, `util/stdin.ts:13-20` |
| `CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE` | 시스템 메시지의 commitSignature 블록을 뺍니다. | `systemMessage.ts:223-229` |
| `CONTINUE_USER_ID` | machine-id 대신 쓰는 고유 ID입니다. 이 ID를 LLM 헤더에 넣는 `_mergeUserAgentIntoRequestOptions`는 어디서도 호출되지 않아 LLM 요청과는 무관합니다. | `util/uniqueId.ts:9-18`, `config.ts:28-39` |
| `CONTINUE_USE_AI_SDK` | 켜면 openai/anthropic 제공자가 Vercel AI SDK 경로로 바뀝니다. **unset 필수.** | `packages/openai-adapters/src/index.ts:85-96` |
| `HTTP(S)_PROXY`, `NO_PROXY` | LLM fetch에 적용됩니다. 가짜 LLM이 127.0.0.1이면 `NO_PROXY=127.0.0.1,localhost`를 주거나 프록시 변수를 unset하십시오. | `packages/fetch/src/util.ts:8-42`, `packages/fetch/src/fetch.ts:94-107` |
| `VERBOSE_FETCH` | 요청·응답을 curl 형식으로 로그합니다. 헤드리스에서는 console이 막혀서 보이지 않습니다(`init.ts:41-45`). | `packages/fetch/src/fetch.ts:158-161` |
| `FEATURE_FLAG_DISABLE_CUSTOM_FETCH` | 어댑터의 customFetch(프록시·헤더 병합)를 끕니다. | `packages/openai-adapters/src/util.ts:158-160` |
| `CONTINUE_CLI_*_MAX_*` | 도구 출력 잘라내기 한도(Bash/Read/Search/Fetch/Diff)입니다. 두 구현이 같은 값을 써야 합니다. | `tools/runTerminalCommand.ts:53,60`, `tools/readFile.ts:17,24`, `tools/searchCode.ts:91,98`, `tools/fetch.ts:17`, `tools/viewDiff.ts:17` |
| `NODE_ENV=test` | 업데이트 확인을 끄지만, stdin 읽기와 Bash 동작(`tools/runTerminalCommand.ts:205`)도 바꿉니다. **권장하지 않습니다.** | `services/UpdateService.ts:37`, `util/stdin.ts:14` |

### 2.4 텔레메트리·버전 확인·자동 업데이트 끄기

| 대상 | 동작 | 끄는 방법 |
|---|---|---|
| OTel 메트릭 | `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`, `OTEL_METRICS_EXPORTER` 중 하나라도 있어야 켜집니다(`telemetry/telemetryService.ts:68-96`). | OTEL_* 변수를 unset합니다. 확실히 하려면 `CONTINUE_METRICS_ENABLED=0` 또는 `CONTINUE_CLI_ENABLE_TELEMETRY=0`도 줍니다(`:76-82`). |
| PostHog/Sentry | 소스에서 찾지 못했습니다. grep 결과 0건입니다. | 해당 없음 |
| 버전 확인 fetch | import 시점에 `api.continue.dev/cn/info?id=<machineId>`를 부릅니다(`version.ts:29-75`). machine-id 계산(`node-machine-id`)도 이때 일어납니다. | **환경변수로는 끌 수 없습니다.** 네트워크를 격리하십시오. |
| 자동 업데이트 | `updateService.initialize()`가 headless 인자 없이 호출됩니다(`services/index.ts:187-191`). 그래서 헤드리스에서도 `!headless`가 참이 되어 확인을 시작합니다(`services/UpdateService.ts:35-39`). 버전이 `0.0.0-dev`이면 건너뜁니다(`:54-60`). 배포판이면 새 버전이 있을 때 `npm i -g @continuedev/cli`를 실행하고 자기 자신을 다시 spawn합니다(`:92-98`, `:140-238`). | (a) `CONTINUE_CLI_AUTO_UPDATED=true`로 설치를 막습니다(`:96`). (b) `$CONTINUE_GLOBAL_DIR/index/globalContext.json`에 `{"autoUpdateCli": false}`를 미리 넣습니다(`:46-47`, `core/util/paths.ts:94-96`). (c) 네트워크를 격리합니다. 소스 빌드판(0.0.0-dev)을 쓰면 가장 깔끔합니다. |
| 세션 메타 업로드 | `--id`가 argv에 있을 때만 동작합니다(`util/exit.ts:104-137`, `util/metadata.ts:109-116`). | `--id`를 쓰지 않습니다. |
| git-ai 확인 | 초기화 때 `git-ai --version`을 exec합니다(`services/GitAiIntegrationService.ts:36-60`). | 무해합니다(로컬 exec). |

### 2.5 시험 재현용 실행 템플릿(제안)
```sh
env -i PATH="$PATH" HOME="$T/home" CONTINUE_GLOBAL_DIR="$T/cnhome" \
    CONTINUE_API_BASE=http://127.0.0.1:9/ CONTINUE_CLI_AUTO_UPDATED=true \
    CONTINUE_METRICS_ENABLED=0 CONTINUE_CLI_ENABLE_TELEMETRY=0 \
    CONTINUE_CLI_TEST_SESSION_ID=difftest-0001 NO_PROXY=127.0.0.1,localhost \
  cn -p "프롬프트" --config "$T/config.yaml" < /dev/null
```
- `env -i`로 OTEL_*, ANTHROPIC_API_KEY, CONTINUE_USE_AI_SDK, 프록시 변수를 일괄 제거합니다.
- cwd는 `.env`, `AGENTS.md`/`AGENT.md`/`CLAUDE.md`/`CODEX.md`, `.continue/rules`, `.continue/skills`, `.claude/skills`, `.continue/settings*.json`이 없는(또는 고정된) 픽스처 폴더로 둡니다.
- 버전 확인 fetch까지 막으려면 네트워크를 격리합니다. Linux는 `unshare -rn`(루프백만 남김. 가짜 LLM도 같은 netns 안에서 띄움), macOS는 `sandbox-exec` 프로필이나 pf로 외부 차단, 또는 `/etc/hosts`에 `api.continue.dev → 127.0.0.1`.
- stdin: `-p`와 프롬프트 인자가 함께 있으면 stdin을 읽지 않습니다(`util/stdin.ts:22-26`). 프롬프트 없이 파이프로 주면 stdin 전체가 프롬프트가 됩니다. 인자와 파이프가 둘 다 있는 경우는 `isHeadlessMode() && hasSuppliedPrompt()`라서 stdin을 무시합니다. `index.ts:264-275`의 `<stdin>` 결합 분기는 사실상 실행되지 않습니다.

### 2.6 권한 옵션

- 정의: `--readonly`, `--auto`(`shared-options.ts:14-15`), `--allow/--ask/--exclude <tool>`(여러 번 가능, `:58-87`).
- `--readonly`와 `--auto`를 함께 쓰면 오류입니다(`flags/flagValidator.ts:95-106`, `flags/flagProcessor.ts:23-40`). `--readonly`는 `plan`, `--auto`는 `auto` 모드가 됩니다.
- 우선순위(normal 모드): CLI 플래그(exclude → ask → allow 순서로 push) > `$CONTINUE_GLOBAL_DIR/permissions.yaml` > 기본 정책. 첫 번째로 일치한 정책이 이깁니다(`permissions/precedenceResolver.ts:33-95`, `permissions/permissionChecker.ts:128-150`).
- **plan/auto 모드에서는 모드 정책만 씁니다. `--allow/--ask/--exclude`와 permissions.yaml은 무시됩니다**(`services/ToolPermissionService.ts:207-229`). auto는 `[{tool:"*",permission:"allow"}]`입니다(`permissions/defaultPolicies.ts:69-71`).
- 헤드리스 기본 정책(`permissions/defaultPolicies.ts:7-40`): Edit/MultiEdit/Write는 `ask`, 나머지 내장 도구는 `allow`, `Bash`와 `*`는 헤드리스에서 `allow`입니다.
- **헤드리스에서 `ask`인 도구는 요청의 `tools` 목록에서 빠집니다**(`stream/handleToolCalls.ts:172-196`, 특히 `:187-189`). 기본 `cn -p`에서는 Write/Edit/MultiEdit가 LLM에 보이지 않습니다. 쓰기 시험을 하려면 `--allow Write` 같은 플래그나 `--auto`가 필요합니다.
- 실행 시점에 `ask` 또는 `exclude`가 걸리면(예: Bash 보안 평가가 `disabled`를 반환) 거부됩니다. 결과 문자열은 `"Command blocked by security policy"`, status는 canceled입니다(`stream/streamChatResponse.helpers.ts:113-137, 525-551`). 헤드리스에서 거부가 하나라도 있으면 **그 턴은 즉시 끝납니다**(`stream/handleToolCalls.ts:158-163`).
- 패턴 문법: `*`, 글롭(`mcp_*`), `Bash(npm *)` 형태(명령 인자 매칭)(`permissions/permissionChecker.ts:17-60`). Bash는 `evaluateTerminalCommandSecurity`로 동적 평가합니다(`tools/runTerminalCommand.ts:144-152`, `packages/terminal-security/src/evaluateTerminalCommandSecurity.ts:32`).
- 도구 이름: Read, Write, List, Bash, Fetch, Checklist, CheckBackgroundJob, AskQuestion, Search, Edit, MultiEdit, Exit, Skills, Subagent, (Diff, Status, ReportFailure, UploadArtifact) — 출처는 `tools/*.ts`의 `name:`입니다.

### 2.7 출력 형식 옵션
- `--format json`은 `-p`와 함께만 쓸 수 있고 값은 `json`만 허용됩니다(`index.ts:185-188`, `flags/flagValidator.ts:46-89`).
  - 시스템 메시지에 JSON 모드 지시가 추가됩니다(`systemMessage.ts:239-249`). **요청 본문이 달라지므로** 차등 시험의 독립 케이스로 다뤄야 합니다.
  - 최종 응답이 JSON으로 파싱되면 그대로 출력합니다. 아니면 `{"response":…,"status":"success","note":"Response was not valid JSON, so it was wrapped in a JSON object"}`로 감쌉니다(`commands/chat.ts:44-60`).
- `--silent`는 `<think>…</think>`를 제거하고 3줄 이상 연속된 빈 줄을 2줄로 줄인 뒤 trim합니다(`commands/chat.ts:67-78`).
- 출력은 최종 응답 + `"\n"`을 원래 stdout에 씁니다(`commands/chat.ts:197-219`, `init.ts:97-105`). 최종 응답은 마지막 반복의 content입니다(`stream/streamChatResponse.ts:43-55, 588-590`). 응답이 비면 아무것도 출력하지 않습니다.
- 헤드리스에서는 console.* 와 일반 stdout/stderr 쓰기를 전부 무력화합니다(`init.ts:41-80`).
- 헤드리스 시스템 메시지에는 "IMPORTANT: You are running in headless mode…" 문구가 항상 붙습니다(`systemMessage.ts:232-237`).

### 2.8 종료 코드

| 상황 | 코드 | 근거 |
|---|---|---|
| 정상 종료 | 0 | `commands/chat.ts:543` |
| 처리되지 않은 예외나 거부가 있었는데 0으로 끝나려는 경우 | 1로 바뀜 | `util/exit.ts:214-219`, `index.ts:120-164` |
| 실행 중 오류(설정·모델·LLM 오류 등). stderr에 `{"status":"error","message":…}` 출력 | 1 | `commands/chat.ts:618-636` |
| 플래그 검증 실패 | 1 | `flags/flagValidator.ts:213-224` |
| `-p`인데 프롬프트가 없음(--agent, --resume 없이) | 1 | `index.ts:279-291` |
| `--resume`/`--fork`에 새 입력이 없음 | 0 | `commands/chat.ts:492-499` |
| `--fork` 세션을 못 찾음 | 1 | `commands/chat.ts:101-104` |
| LLM이 `Exit` 도구를 호출 | 1 | `tools/exit.ts:5-34` |
| SIGINT | 130 / SIGTERM 0 | `index.ts:167-169, 388-390` |
| 알 수 없는 하위 명령 | 1 | `index.ts:364-368` |
| 헤드리스 권한 거부로 턴이 조기 종료됨 | 0 (출력은 그때까지의 content) | `stream/handleToolCalls.ts:158-163` |

---

## 3. openai 제공자로 보내는 실제 HTTP 요청

### 3.1 경로
1. `commands/chat.ts:436-544` `runHeadlessMode` → `processMessage`(`:342-434`) → `streamChatResponse`(`stream/streamChatResponse.ts:423-591`)
2. 반복마다 시스템 메시지를 새로 만듭니다(`:449-451`). 도구 목록도 다시 계산합니다(`:454-458`).
3. `processStreamingResponse`(`:197-419`): `convertFromUnifiedHistoryWithSystemMessage`(`core/util/messageConversion.ts:361-378`)로 messages를 만들고, `chatCompletionStreamWithBackoff`(`util/exponentialBackoff.ts:169-241`)를 호출합니다.
4. `OpenAIApi.chatCompletionStream`(`packages/openai-adapters/src/apis/OpenAI.ts:146-176`) → `this.openai.chat.completions.create(modifyChatBody(body))`. openai-node v5 SDK가 `fetch: customFetch(requestOptions)`로 요청을 보냅니다(`:43-49`) → `@continuedev/fetch`의 `fetchwithRequestOptions`(node-fetch 패치판)(`packages/fetch/src/fetch.ts:83-200`).

### 3.2 엔드포인트
- `POST {apiBase}chat/completions`. apiBase 기본값은 `https://api.openai.com/v1/`입니다(`apis/OpenAI.ts:37-40`). URL 조립은 SDK가 합니다 → `http://127.0.0.1:18080/v1/chat/completions`.
- 호스트가 `localhost`이면 `127.0.0.1`로 바꿉니다(`packages/fetch/src/fetch.ts:88-90, 139-141`).
- 예외: 모델 이름이 `gpt-5*` 또는 `o[0-9]*`이면 `POST {apiBase}responses`(0장 4번).
- 헤드리스 1회 실행에서 다른 LLM 엔드포인트(`/models` 등)는 호출하지 않습니다.

### 3.3 헤더
- SDK가 붙이는 것 (추정, openai-node 5.x 기본 동작): `Authorization: Bearer <apiKey>`, `Content-Type: application/json`, `Accept: application/json`, `User-Agent: OpenAI/JS 5.23.2`, `X-Stainless-Lang: js`, `X-Stainless-Package-Version`, `X-Stainless-OS`, `X-Stainless-Arch`, `X-Stainless-Runtime: node`, `X-Stainless-Runtime-Version`, `X-Stainless-Retry-Count`, `X-Stainless-Timeout`(timeout 설정 시).
- 그 위에 `requestOptions.headers`를 덮어씁니다(`packages/fetch/src/fetch.ts:134-137`). 커스텀 `Authorization`/`x-api-key`가 있으면 기본 인증 헤더를 지웁니다(`packages/openai-adapters/src/util.ts:162-198`).
- `requestOptions.extraBodyProperties`가 있으면 본문 JSON에 병합합니다(`packages/fetch/src/fetch.ts:143-154`).
- CLI 고유 헤더(`user-agent: Continue-CLI/…`, `x-continue-unique-id`)는 **보내지 않습니다.** 그 헬퍼가 호출되지 않습니다(`config.ts:28-39`, grep 결과 사용처 0건).
- `x-api-key` 헤더는 fim/rerank 경로(`getHeaders`, `apis/OpenAI.ts:120-127`)에서만 씁니다. chat 경로는 SDK 헤더를 씁니다.

### 3.4 본문 필드
`stream/streamChatResponse.ts:265-275` + `stream/streamChatResponse.types.ts:22-33` + `apis/OpenAI.ts:51-88`:
```jsonc
{
  "model": "<model.model>",
  "messages": [ {"role":"system","content":"<시스템 메시지>"}, ...히스토리 ],
  "stream": true,
  "tools": [ {"type":"function","function":{"name","description","parameters":{"type":"object","required":[...],"properties":{...}}}} ],
  // defaultCompletionOptions가 있을 때만 (값이 undefined인 키는 JSON에서 빠짐):
  "max_tokens": ..., "temperature": ..., "frequency_penalty": ..., "presence_penalty": ..., "top_p": ...,
  "stream_options": {"include_usage": true}     // modifyChatBody가 마지막에 추가
}
```
- `tools`는 항상 들어갑니다(빈 배열일 수도 있음). `tool_choice`와 `parallel_tool_calls`는 보내지 않습니다. `parallel_tool_calls:false`는 공식 OpenAI apiBase일 때만 붙습니다(`apis/OpenAI.ts:68-86`).
- 공식 API + o*/gpt-5이면 `max_completion_tokens`로 바꾸고 system을 developer로 바꿉니다(`:67-82`). apiBase나 모델에 deepseek가 들어 있으면 max_completion_tokens로 바꿉니다(`:57-65`). 가짜 LLM에는 해당하지 않습니다.
- 도구의 parameters에는 `type`, `required`, `properties` 세 키만 들어갑니다(`tools/index.tsx:175-190`). 정의에 `required`가 없는 도구(예: Exit)는 `required` 키가 undefined라서 빠집니다.
- `model.chatOptions.toolOverrides`가 있으면 도구 정의를 덮어씁니다(`stream/streamChatResponse.ts:455-458`).
- 키 순서: model, messages, stream, tools, (옵션들), stream_options. 바이트 단위 비교는 피하고 파싱 후 비교하십시오.

**messages 변환 규칙**(`core/util/messageConversion.ts:76-121, 304-354`):
- user: `{"role":"user","content":<문자열>}`. contextItems가 있으면 `<context name="…">…</context>\n\n`을 앞에 붙입니다. CLI 헤드리스에서는 비어 있습니다(`services/ChatHistoryService.ts:107-125`).
- assistant: `{"role":"assistant","content":<문자열>, "tool_calls":[{"id","type":"function","function":{"name","arguments"}}]}`. 도구 호출이 있으면 content는 `""`입니다(`stream/handleToolCalls.ts:80-92`).
  - **`arguments`는 LLM이 보낸 원문이 아닙니다.** `JSON.parse`한 결과를 다시 `JSON.stringify`한 compact JSON입니다(`stream/handleToolCalls.ts:72,88`, `stream/streamChatResponse.helpers.ts:231-242`). 파싱에 실패하면 `{}` → `"{}"`가 됩니다.
- 각 tool_call 뒤에는 `{"role":"tool","content":…,"tool_call_id":…}`가 이어집니다. output이 없으면 content는 `"Tool cancelled"`입니다(`core/util/messageConversion.ts:334-349`). 여러 output은 `\n`으로 join합니다.
- usage는 히스토리에만 저장하고 요청에는 넣지 않습니다.

**tools 목록과 순서**(`tools/index.tsx:65-143`):
Read, Write, List, Bash, Fetch, Checklist, CheckBackgroundJob, AskQuestion, [Search: `rg --version`이 성공할 때만], [MultiEdit: 모델 name/model에 gemini|claude|gpt|o\d|kimi|qwen|llama|nemotron|grok|mistral이 들어 있을 때. 아니면 Edit](`utils/modelCapability.ts:4-36`), [Exit: 헤드리스], [Subagent: `--beta-subagent-tool`], Skills, MCP 도구 순입니다. 그다음 권한 필터를 거칩니다(2.6절). 예: `fake-model`이면 Edit, `gpt-4o-mini`면 MultiEdit(정규식 `o\d`·`gpt`에 걸림)가 들어갑니다. 헤드리스 기본 권한에서는 둘 다 `ask`라서 결국 빠집니다.
- Skills 도구 설명은 `${skills.map(...)}`로 배열을 템플릿에 직접 넣기 때문에 **원소 사이에 쉼표(`,`)가 들어갑니다**(`tools/skills.ts:33-37`). 스킬이 없으면 "listed below:\n"으로 끝납니다. ncn은 이 형식을 그대로 재현해야 합니다.

### 3.5 재시도 (요청 횟수에 영향)
- CLI 층(`util/exponentialBackoff.ts:24-31, 50-53, 117-145, 150-165, 251-330`): 최대 10회 재시도, 1초부터 2배씩 늘려 최대 30초, **지터 있음(Math.random)**. 429/502/503/504와 연결 오류가 재시도 대상입니다. 스트림 도중 오류가 나도 전체를 다시 요청합니다.
- SDK 층 (추정): openai-node 기본 `maxRetries: 2`(408/409/429/5xx/연결 오류). OpenAI 클라이언트를 만들 때 maxRetries를 지정하지 않습니다(`apis/OpenAI.ts:43-49`).
- 따라서 가짜 LLM은 시험 중 오류 응답(특히 429/5xx)을 내지 않아야 합니다. 오류 경로를 시험할 때는 400 같은 재시도 대상이 아닌 코드를 쓰십시오.

### 3.6 비결정적 요소와 정규화 방법

| 요소 | 위치 | 정규화·통제 방법 |
|---|---|---|
| 작업 폴더 `Working directory: ${cwd}` | 시스템 메시지(`systemMessage.ts:50`), **Bash 도구 설명**(`tools/runTerminalCommand.ts:123`) | 같은 절대경로 픽스처에서 둘 다 실행하거나, 정규식으로 `<CWD>` 치환(시스템 메시지와 tools[].description 둘 다) |
| 오늘 날짜 `Today's date: YYYY-MM-DD` (**UTC**, toISOString) | `systemMessage.ts:53` | `\d{4}-\d{2}-\d{2}` → `<DATE>`. ncn도 UTC 날짜를 써야 합니다. |
| 플랫폼 `process.platform` (darwin/linux) | `systemMessage.ts:52` | 같은 기계에서 실행합니다. ncn은 `sys.platform`을 Node 값으로 매핑(`win32` 등)합니다. |
| git 여부·`git status --porcelain` 스냅샷 | `systemMessage.ts:16-41, 51, 58-60` | 비 git 폴더에서 실행하면 `false` / `Not a git repository`. git 폴더라면 같은 상태로 고정합니다. 이 문자열은 **모듈 로드 시점에 한 번** 계산됩니다(`:43-61`). |
| AGENTS.md/AGENT.md/CLAUDE.md/CODEX.md(cwd, 처음 찾은 하나) | `systemMessage.ts:167-183` | 픽스처로 통제합니다. |
| `.continue/rules`, `$CONTINUE_GLOBAL_DIR/rules`의 md 규칙(readdir 순서) | `systemMessage.ts:89-151`, `services/ConfigService.ts:262-271` | 규칙 파일을 두지 않거나 하나만 둡니다. readdir 순서는 OS마다 다를 수 있습니다. |
| 스킬 목록(cwd/.continue/skills, cwd/.claude/skills, 홈/skills) | `util/loadMarkdownSkills.ts:92-98`, `tools/skills.ts:33-37` | 픽스처로 통제합니다. |
| ripgrep 설치 여부 → Search 도구 유무 | `tools/index.tsx:84-87`, `tools/searchCode.ts:29-36` | 같은 PATH를 씁니다. ncn도 `rg --version`으로 판정합니다. |
| 도구 호출 id | LLM 응답의 id를 그대로 씁니다(`stream/streamChatResponse.helpers.ts:184-224`). CLI가 생성하지 않습니다. | 가짜 LLM 스크립트가 고정 id(`call_1`…)를 내게 합니다. id 없이 index만 오면 매핑이 안 되어 버려집니다(`:197-207`). |
| tool arguments 재직렬화 | `stream/handleToolCalls.ts:72,88` | ncn은 `json.dumps(obj, separators=(",",":"), ensure_ascii=False)`로 맞춥니다. 숫자 표기(1.0 → 1), 키 순서 유지에 주의합니다. 비교할 때는 arguments를 파싱해 비교하는 옵션도 둡니다. |
| 도구 실행 결과(경로, 파일 목록 순서, Bash 출력, 시간) | 각 도구 | 결정적인 픽스처와 명령만 씁니다. 출력에 들어가는 cwd 절대경로를 치환합니다. 병렬 도구 결과는 원래 순서로 조립됩니다(`stream/streamChatResponse.helpers.ts:640-644`). 동시 실행 중의 부수효과 순서는 비결정적입니다. |
| 헤더 User-Agent / X-Stainless-* | SDK | 비교 대상에서 빼거나, ncn이 JS 값을 흉내 내게 합니다. 비교 대상: `Authorization`, `Content-Type`, 메서드, 경로, requestOptions.headers |
| 본문 키 순서·공백 | SDK `JSON.stringify` | 파싱 후 정규 JSON(`sort_keys`)으로 비교합니다. |
| 재시도 지터·SDK 재시도 | 3.5절 | 오류를 주입하지 않습니다. |
| 세션 ID(uuid v4), 세션 파일 시각, dateCreated | `session.ts:96-98`, `core/util/history.ts:167-174` | `CONTINUE_CLI_TEST_SESSION_ID`로 고정합니다. 요청 본문에는 들어가지 않습니다. |
| 시스템 메시지 모드 블록(plan/commitSignature/headless/json) | `systemMessage.ts:217-249` | 케이스마다 플래그를 똑같이 줍니다. |
| 자동 압축(컨텍스트 80% 근접 시 추가 LLM 호출) | `commands/chat.ts:221-301`, `compaction.ts:18-20, 261-290`, `util/tokenizer.ts:9-33` (기본 contextLength 200,000) | 짧은 대화만 쓰거나 `contextLength`를 크게 둡니다. 압축 시험은 따로 만듭니다. |
| cliSelectedModel(globalContext) | `util/modelPersistence.ts:7-19`, `services/ModelService.ts:59-123` | 매번 빈 CONTINUE_GLOBAL_DIR을 씁니다. |

권장 정규화 파이프라인(요청 단위):
1. 요청 로그를 `{method, path, headers(화이트리스트), body(JSON 파싱)}`로 저장합니다.
2. body 문자열 전체에 `cwd → <CWD>`, `\b\d{4}-\d{2}-\d{2}\b`(시스템 메시지의 "Today's date:" 줄 한정) `→ <DATE>`, 임시 홈 경로 → `<HOME>` 치환을 적용합니다.
3. `messages[*].tool_calls[*].function.arguments`는 JSON을 파싱한 다음 다시 정규화합니다.
4. `tools`는 순서 그대로 비교합니다(순서도 재현 대상). 따로 "이름 집합만 비교" 모드를 두면 진단이 쉬워집니다.
5. 요청 개수와 순서가 같은지도 비교합니다(3.5의 재시도, 2번의 제목 생성 없음).

---

## 4. 하위 에이전트(Subagent) 동시 실행과 전역 서비스 교체 충돌

### 4.1 전제: 동시 실행은 실제로 일어납니다
- 한 응답 안의 여러 tool_call은 권한 확인만 순차로 하고, 실행은 즉시 시작해 `Promise.all`로 병렬 처리합니다(`stream/streamChatResponse.helpers.ts:469-482, 496-559, 638`).
- 단, **헤드리스 `cn -p`에서는 Subagent가 `executeSubAgent`에 들어가기 전에 "No active session found"로 실패합니다**(0장 3번). 따라서 아래 충돌은 sessionId가 세팅되는 `cn serve`(`commands/serve.ts:158`)나, 앞으로 이 버그가 고쳐진 경우에 해당합니다. ncn이 cn을 "똑같이" 재현한다면 헤드리스에서는 같은 오류를 내야 합니다.

### 4.2 `executeSubAgent`가 바꾸는 전역 상태(`subagent/executor.ts:58-213`)

| 전역 상태 | 바꾸는 곳 | 복원하는 곳 |
|---|---|---|
| ① 서비스 컨테이너의 TOOL_PERMISSIONS 상태 → `[{tool:"*",permission:"allow"}]` | `:63-66`에서 원본을 읽고 `:81-89`에서 set | `:196-199`에서 읽어 둔 원본으로 set |
| ② `services.systemMessage.getSystemMessage` 인스턴스 메서드를 클로저로 교체 | `:95`에서 원본 저장, `:105-107`에서 교체 | `:186-188` |
| ③ `services.chatHistory.isReady`를 `() => false`로 교체 | `:98-102`에서 원본 저장, `:110-112`에서 교체 | `:191-193` |

이 셋 모두 **프로세스 전역 싱글턴**입니다(`services/index.ts:40-56, 364-383`, `services/ServiceContainer.ts:88-93, 221-223`). 잠금이나 참조 카운트 없이 "저장 → 교체 → finally에서 저장값으로 복원"만 합니다. 그래서 실행 구간이 겹치면 다음 문제가 실제로 생깁니다. A가 먼저 시작하고 B가 나중에 시작해 A가 먼저 끝나는 경우입니다.

1. **권한 상승이 영구화됩니다.** B는 `:63-66`에서 이미 A가 바꾼 allow-all 상태를 "원본"으로 읽습니다. A가 끝나며 진짜 원본을 복원해도, B가 끝나면서 allow-all을 다시 set합니다. **이후 메인 에이전트의 권한이 계속 `*: allow`가 되어** `--exclude`나 기본 ask 정책이 무력화됩니다. 실행 중에도 같은 배치의 다른 도구(예: Bash, Write)가 권한 확인을 할 때 컨테이너에서 이 allow-all 상태를 읽습니다(`stream/streamChatResponse.helpers.ts:509-518`, `stream/handleToolCalls.ts:175-178`). 그래서 차단돼야 할 호출이 통과할 수 있습니다.
2. **시스템 메시지가 오염되고 영구적으로 굳습니다.** B의 `buildAgentSystemMessage`(`:34-52`)는 A가 바꿔 둔 `getSystemMessage`를 부르므로, B의 시스템 메시지 = A의 서브에이전트 메시지 + B 프롬프트가 됩니다. B가 저장한 "원본"도 A의 클로저입니다. A가 끝나 진짜 원본이 돌아오면, B의 남은 반복은 `streamChatResponse.ts:449-451`에서 메인 시스템 메시지를 받게 됩니다. B가 끝날 때 A의 클로저를 복원하므로, **이후 메인 에이전트의 모든 요청이 A의 서브에이전트 시스템 메시지로 나갑니다.**
3. **히스토리가 섞이고 영구 비활성화됩니다.** B가 저장한 `isReady` "원본"은 A의 `() => false`입니다. A가 끝나 진짜 isReady가 돌아오면, B의 `refreshChatHistoryFromService`(`stream/streamChatResponse.ts:74-92`)와 `handleToolCalls`(`stream/handleToolCalls.ts:41-49, 94-100`)가 **메인 ChatHistoryService를 읽고 씁니다.** 그 결과 B의 하위 대화가 메인 히스토리에 섞이고, B의 다음 요청 messages에 메인 대화가 들어갑니다. B가 끝나면 `() => false`가 복원되어 **메인의 ChatHistoryService가 계속 "not ready"가 됩니다.** 그 뒤 메인 루프는 로컬 배열에 기록하고(fallback), 서비스 기반 저장과 표시가 끊깁니다.
4. 부수 문제: 하위 에이전트의 응답은 `chatHistory.at(-1)`을 씁니다(`:164-169`). 3번처럼 서비스 경로로 기록되면 로컬 배열이 갱신되지 않아 응답이 엉뚱한 값(원래 user 프롬프트)이 될 수 있습니다. 또 Subagent 도구는 하위 에이전트 안에서도 목록에 다시 나타납니다(베타 플래그가 전역이라서, `tools/index.tsx:131-133`, `tools/toolsConfig.ts:6-23`). 그래서 중첩 호출도 같은 문제를 겪습니다.

- 결론: **동시 Subagent 2개 이상은 코드상 경쟁 상태이고, 권한·시스템 메시지·히스토리가 영구적으로 오염될 수 있습니다.** 단일 Subagent는 finally 복원으로 안전합니다(단, 실행 중 같은 배치의 다른 도구는 allow-all로 권한 확인을 받습니다).
- ncn 설계 제안: 하위 에이전트마다 권한·시스템 메시지·히스토리를 담은 **독립 컨텍스트 객체**를 넘기고 전역을 바꾸지 마십시오. 차등 시험에서는 (a) 헤드리스 cn의 "No active session found" 동작을 재현하는 케이스, (b) cn 버그 재현은 목표로 삼지 않고 단일 Subagent 경로만 비교하는 케이스(serve 모드 또는 패치판 기준)로 나누기를 권장합니다.

---

## 5. 대화 세션 저장 위치·형식(`--resume`)

- **위치**: `$CONTINUE_GLOBAL_DIR/sessions/<sessionId>.json` + 목록 파일 `sessions/sessions.json`. 기본은 `~/.continue/sessions`입니다(`session.ts:35-59`, `core/util/paths.ts:79-110`, `core/util/history.ts:111-134`). `CONTINUE_CLI_TEST`+`HOME`이면 읽는 쪽(`session.ts`)만 `$HOME/.continue/sessions`로 바뀝니다(`session.ts:37-46`).
- **ID**: uuid v4, 또는 `CONTINUE_CLI_TEST_SESSION_ID` 값(`session.ts:93-108`).
- **저장 시점**: 헤드리스에서는 ChatHistoryService가 `isRemoteMode=true`로 초기화되어 자동 저장을 건너뜁니다(`services/index.ts:295`, `services/ChatHistoryService.ts:66-70, 77-101`). 대신 응답이 끝날 때마다 `updateSessionHistory`로 저장합니다(`commands/chat.ts:403-404`, `session.ts:116-120`). 사용량 추적 때도 저장합니다(`session.ts:149-178`). 히스토리에 system이 아닌 메시지가 없으면 저장하지 않습니다(`session.ts:263-273, 279-295`).
- **형식**: `JSON.stringify(…, undefined, 2)`. 키 순서는 sessionId, title, workspaceDirectory, history, [mode], [chatModelTitle], [usage]입니다(`core/util/history.ts:115-134`).
  ```jsonc
  {
    "sessionId": "…", "title": "Untitled Session", "workspaceDirectory": "<cwd>",
    "history": [
      {"message":{"role":"user","content":"…"},"contextItems":[],"editorState":"…(user만: content 복사)"},
      {"message":{"role":"assistant","content":"","toolCalls":[{"id","type":"function","function":{"name","arguments"}}],"usage":{…}},
       "contextItems":[],
       "toolCallStates":[{"toolCallId","toolCall":{…},"status":"done|errored|canceled|…","parsedArgs":{…},"output":[{"content","name","description"}]}]}
    ],
    "usage": {"totalCost":0,"promptTokens":0,"completionTokens":0,"promptTokensDetails":{"cachedTokens":0,"cacheWriteTokens":0}}
  }
  ```
  - system 역할 항목은 저장 전에 빠집니다. user 항목에는 `editorState`가 붙습니다(`session.ts:211-231`). 기본 제목은 `"Untitled Session"`입니다(`constants/session.ts`). 헤드리스에서는 제목 생성이 일어나지 않아 이 값이 유지됩니다(0장 2번).
  - `sessions.json`: `[{sessionId,title,dateCreated:String(Date.now()),workspaceDirectory,messageCount(assistant 수)}]`(`core/util/history.ts:136-181`).
- **`--resume`**: sessions 폴더에서 `sessions.json`을 제외한 `*.json` 중 **mtime이 가장 최근인 파일**을 불러옵니다(`session.ts:301-332`). **cwd나 작업공간으로 거르지 않습니다.** 불러온 세션을 현재 세션으로 삼아 같은 파일에 이어 씁니다(`session.ts:325-326`). 헤드리스에서는 `services.chatHistory.setHistory`로 주입하고 압축 인덱스를 복원합니다(`commands/chat.ts:107-116, 463-469`). 새 프롬프트가 없으면 종료 코드 0으로 끝납니다(`:492-499`).
  - 다시 불러온 히스토리로 요청을 만들 때 toolCallStates의 output이 tool 메시지가 됩니다(3.4절). 저장 형식을 ncn이 그대로 읽고 쓰면 교차 resume 시험(cn이 저장 → ncn이 resume, 그 반대)이 가능합니다.
- **`--fork <id>`**: `historyManager.load(id)`(`session.ts:504-512`) 후 그 히스토리로 새 세션을 만듭니다(`commands/chat.ts:93-105`). `--resume`과 함께 쓸 수 없습니다(`flags/flagValidator.ts:111-129`).
- **`cn ls [--json]`**: 세션 목록을 보여 줍니다(`index.ts:300-308`, `session.ts:450-500`).
