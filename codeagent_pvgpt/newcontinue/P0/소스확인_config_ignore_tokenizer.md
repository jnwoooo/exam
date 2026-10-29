# 소스 확인: cn 설정 로딩 / 민감 파일 차단 / 토큰 세기 / Read 기록

- 기준: Continue 저장소 커밋 `5522c6f` (`.ref/continue`)
- 경로 표기: `cli/` = `extensions/cli/src/`, `cy/` = `packages/config-yaml/src/`, `core/` = `core/`
- 다룬 범위: cn이 실제로 타는 코드 경로만. IDE 확장 쪽(`core/config/yaml/LocalPlatformClient.ts` 등)은 cn이 쓰지 않으므로 참고로만 적음.
- (주의) `node_modules`가 체크아웃에 없어 외부 라이브러리(`ignore`, `gpt-tokenizer`, `dotenv`) 내부 동작은 버전 정보와 일반 지식으로 적었고, 해당 항목에 **[라이브러리·미검증]** 표시를 붙임.

---

## 1. 설정 로딩 (`--config`, config.yaml, 템플릿 변수, .env)

### 1.1 설정 원천 결정 순서 — `cli/configLoader.ts`

`determineConfigSource` (`cli/configLoader.ts:84-100`):

1. `--config` 값이 있으면 → `{ type: "cli-flag", path }` (`:90-92`)
2. 없으면 `path.join(env.continueHome, "config.yaml")`이 존재하면 → `local-config-yaml` (`:95-98`)
3. 둘 다 아니면 → `remote-default-config` (`:99`) = 허브에서 `continuedev/default-cli-config`를 받아옴 (`:326-355`, **네트워크 필요**)

`saved-uri`, `user-assistant`는 타입에는 있지만 `determineConfigSource`가 반환하지 않음(현재 쓰이지 않는 경로).

`env.continueHome` (`cli/env.ts:8-12`):

```ts
dotenv.config();

export const env = {
  apiBase: process.env.CONTINUE_API_BASE ?? "https://api.continue.dev/",
  continueHome:
    process.env.CONTINUE_GLOBAL_DIR || path.join(os.homedir(), ".continue"),
};
```

로드 후 `updateConfigUri(uri)`로 URI를 저장함 (`:69-73`, `getUriFromSource` `:530-543`: cli-flag 파일이면 `file://${path}`, 슬러그면 `slug://${path}`, 로컬이면 `file://<continueHome>/config.yaml`).

`--config` 옵션 정의 (`cli/shared-options.ts:6-9`): `"--config <path>"`, 설명 `"Configuration file for the assistant (can be a file path or hub slug)"`.

서비스 재로드 시에도 명시적 `--config`가 저장 상태보다 우선 (`cli/services/index.ts:207-214`의 `commandOptions.config || currentState.configPath`).

### 1.2 `--config` 경로 판정과 처리

파일 경로인지 허브 슬러그인지 판정 (`cli/configLoader.ts:511-525`):

```ts
function isFilePath(configPath: string): boolean {
  return (
    configPath.startsWith(".") ||
    configPath.startsWith("/") ||
    configPath.startsWith("~") ||
    // Windows absolute paths (C:\, D:\, etc.)
    /^[A-Za-z]:[/\\]/.test(configPath) ||
    // UNC paths (\\server\share)
    configPath.startsWith("\\\\") ||
    // Contains file extension
    configPath.includes(".yaml") ||
    configPath.includes(".yml") ||
    configPath.includes(".json")
  );
}
```

- 파일이면 `loadConfigYaml` → `unrollAssistantWithConfig({ fileUri: filePath, uriType: "file" })` (`:433-447`). 경로 문자열을 **그대로** 넘김(정규화·`~` 확장 없음).
- 이때 `RegistryClient({ rootPath: dirname(fileUri) })` (`:401-406`).
- 실제 파일 읽기 `cy/registryClient.ts:35-54`:
  - `file://`로 시작 → `fs.readFileSync(new URL(filepath))`
  - 절대경로 → 그대로 읽음
  - 상대경로 → `path.resolve(filepath)`(cwd 기준)가 있으면 그것, 없으면 `path.join(rootPath, filepath)`, rootPath 없으면 `"No rootPath provided for relative file path"` 오류
  - 따라서 `--config ~/x.yaml`을 셸이 확장하지 않고 넘기면(따옴표 등) cn 코드는 `~`를 확장하지 않음 → `<cwd>/~/x.yaml`로 해석됨. (`~` 확장은 `uses:` 문자열의 `decodePackageIdentifier`에서만 함, 1.6절)
- 슬러그(`owner/package`)면 `loadAssistantSlug` (`:452-506`). 형식 오류 시 `` `Invalid assistant slug format. Expected "owner/package", got: ${slug}` ``. 로그인 안 된 상태면 로컬 unroll 시도 → `RegistryClient`가 `"Slug-based package resolution is not supported"`를 던짐(`cy/registryClient.ts:26-27`).
- `--config`를 준 경우 onboarding에서 먼저 한 번 로드해 보고 실패 시 `` `Failed to load config from "${configPath}": ${errorMessage}` `` (`cli/onboarding.ts:128-143`). `--config`가 있으면 onboarding 대화형 흐름은 건너뜀(`:55-57`).
- unroll 결과에 config가 없으면 `` `Failed to load config:\n${errorDetails}` ``, config는 있고 비치명 오류가 있으면 콘솔에 `` `Warning: ${warnings}` `` (dim)만 찍고 계속 (`cli/configLoader.ts:416-425`).

### 1.3 unroll 전체 흐름 — `cy/load/unroll.ts:275-333` (`unrollAssistantFromContent`)

1. `parseMarkdownRuleOrConfigYaml(rawYaml, id)` — YAML 파싱 + `configYamlSchema.safeParse` (`:32-58`). 파일 확장자가 `.yaml/.yml`이면 실패 시 바로 오류, 아니면 markdown 규칙으로 재시도 (`:782-809`).
   - 오류 글: `` `Failed to parse config: ${...}` `` (zod 오류는 `path.join(".")+": "+message`를 `", "`로 이음, `formatZodError` `:844-854`)
2. `unrollBlocks` — `uses:` 블록 해석, 주입 블록 추가, 중복 제거 (`:367-684`)
3. `YAML.stringify` 후 `renderTemplateData(..., { secrets: extractFQSNMap(raw, [id]) })` — `${{ secrets.X }}`를 FQSN 형태 `${{ secrets.<부모슬러그들>/X }}`로 바꿈 (`:299-305`)
4. `renderSecrets: true`(cn은 항상 true, `cli/configLoader.ts:375,410`)이면 `extractRenderedSecretsMap` → `platformClient.resolveFQSNs` → 값으로 치환 (`:317-323`)
5. `parseAssistantUnrolled(renderedYaml)` — `assistantUnrolledSchema.parse`로 다시 검증 (`:60-71`). 실패 시 콘솔에 `` `Failed to parse unrolled assistant: ${e.message}\n\n${configYaml}` `` 출력 후 `Failed to parse config: ...` throw.

### 1.4 템플릿 변수 해석

정규식 원문 (`cy/load/unroll.ts:83`):

```ts
export const TEMPLATE_VAR_REGEX = /\${{[\s]*([^}\s]+)[\s]*}}/g;
```

치환 함수 (`:99-116`): 데이터에 키가 있으면 치환, **없으면 원래 표현식(`${{ ... }}`)을 그대로 둠**.

```ts
  return templatedYaml.replace(TEMPLATE_VAR_REGEX, (match, variableName) => {
    // Inject data
    if (variableName in data) {
      return data[variableName];
    }
    // If variable doesn't exist, return the original expression
    return match;
  });
```

템플릿 네임스페이스 (`:118-141`): `TemplateData = { inputs, secrets, continue: {} }`, 평탄화 시 `inputs.<key>`, `secrets.<key>`만 만듦(`continue`는 비어 있음). **`env.X` 같은 다른 네임스페이스는 해석기가 없음 → 원문 그대로 남음.** 최상위 `env:` 키는 스키마상 레코드일 뿐 템플릿 원천이 아님.

#### `${{ secrets.X }}` 처리 순서

1. FQSN 변환 (`secretToFQSNMap` `:143-156`): `parts = [...부모슬러그, secret].join("/")`. 로컬 파일의 슬러그 표기는 `"/"` (`cy/load/clientRender.ts:72-81`의 `packageIdentifierToShorthandSlug`에서 `case "file": return "/"`). 예: 최상위 config.yaml의 `${{ secrets.API_KEY }}` → `${{ secrets.//API_KEY }}`.
2. FQSN 디코드 (`cy/interfaces/slugs.ts:179-196`): `/`로 나눠 마지막 조각이 `secretName`. 즉 중첩 깊이와 무관하게 **실제 조회 키는 마지막 이름(`API_KEY`)**.
3. cn의 `CLIPlatformClient.resolveFQSNs` (`cli/CLIPlatformClient.ts:90-139`):
   - 먼저 모든 FQSN을 로컬에서 찾음(`findSecretInLocalEnvFiles`, `:57-88`)
   - 못 찾은 것만 `apiClient.syncSecrets(...)`로 허브 API 조회(`:108-136`) — **네트워크 호출**. 실패 시 `` `Error resolving FQSNs through API: ${...}` `` 경고만 찍고 계속.
4. 찾은 값은 `map[encodeFQSN(fqsn)] = value`로 치환 (`cy/load/unroll.ts:196-197`). 값이 없는(org/package 위치만 있는) 결과는 `${{ secrets.<secretLocation> }}`로 바뀜 (`:198-202`).
5. **못 찾은 비밀은 오류 없이 `${{ secrets.//X }}` 문자열이 그대로 설정값(apiKey 등)에 남음.** config 로드는 성공함.
   - MCP 서버만 연결 시 별도 검사 (`cli/services/MCPService.ts:252-268`): headless면 throw, TUI면 경고로 쌓음. 오류 글 원문:

```ts
        const message = `${serverConfig.name} MCP Server has unresolved secrets: ${unrendered.join(", ")}
For personal use you can set the secret in the hub at https://continue.dev/settings/secrets or pass it to the CLI environment.
Org-level secrets can only be used for MCP by Background Agents (https://docs.continue.dev/hub/agents/overview) when \"Include in Env\" is enabled for the secret.`;
```

   - 모델 apiKey에 남은 미해석 템플릿은 cn이 별도 검사하지 않음(그대로 `constructLlmApi`에 들어감, `cli/config.ts:44-58`).

#### `.env` 탐색 위치와 우선순위 — `cli/CLIPlatformClient.ts:57-88`

```ts
  private findSecretInLocalEnvFiles(fqsn: FQSN): SecretResult | undefined {
    // First check process.env (highest priority)
    const processEnvSecret = this.findSecretInProcessEnv(fqsn);
    if (processEnvSecret) {
      return processEnvSecret;
    }

    // Then check in priority order: ~/.continue/.env, <workspace>/.continue/.env, <workspace>/.env
    const workspaceDir = process.cwd();
    const envPaths = [
      path.join(env.continueHome, ".env"),
      path.join(workspaceDir, ".continue", ".env"),
      path.join(workspaceDir, ".env"),
    ];
```

순서:
1. `process.env[secretName]` (`:41-55`, 값이 truthy일 때만 — 빈 문자열은 미발견 취급)
2. `<continueHome>/.env` (기본 `~/.continue/.env`, `CONTINUE_GLOBAL_DIR`로 바뀜)
3. `<cwd>/.continue/.env`
4. `<cwd>/.env`
5. (로컬 미발견분) 허브 API `syncSecrets`

각 `.env`는 `dotenv.parse`로 파싱, 파일 없으면 건너뜀, 읽기 오류는 `` `Error reading .env file at ${filePath}: ${...}` `` 경고 후 건너뜀 (`:21-39`). 값이 truthy여야 채택.

**중요한 함정**: `cli/env.ts:6`의 `dotenv.config()`가 시작 시 `<cwd>/.env`를 `process.env`에 미리 적재함(기존 환경변수는 덮지 않음 — dotenv 기본 동작 **[라이브러리·미검증]**). 그래서 실제 유효 우선순위는:

`실제 셸 환경변수` > `<cwd>/.env` (dotenv.config로 process.env에 들어감) > `~/.continue/.env` > `<cwd>/.continue/.env` > `<cwd>/.env`(다시) > 허브 API

즉 코드 주석상 순서와 달리 **`<cwd>/.env`가 `~/.continue/.env`보다 먼저 이긴다.** ncn이 cn과 똑같이 하려면 이 동작까지 재현해야 함.

참고: IDE 쪽 `core/config/yaml/LocalPlatformClient.ts:16,62,100`은 `.env` 파일들을 먼저 보고 process.env를 나중에 봄(순서 반대). cn은 이 파일을 쓰지 않음.

#### `${{ inputs.X }}` 처리

- `uses:` 블록에서만 의미 있음. `resolveBlock` (`cy/load/unroll.ts:719-766`):
  - `with:` 값들을 `inputsToFQSNs`로 변환(값 안의 `${{ secrets.Y }}`도 블록 id 기준 FQSN으로) (`:811-830`, 문자열 아닌 값은 `` `Skipping input "${key}" with invalid value type: ${typeof value}. Expected string.` `` 경고 후 무시)
  - 블록 원문을 `inputs` + `secrets`(FQSN) 데이터로 치환 (`:735-738`)
  - 남은 `inputs.*`가 있으면 오류 (`:741-753`):

```ts
    throw new Error(
      `Missing required input(s) for block "${blockName}": ${missingInputNames.join(", ")}. ` +
        `Please provide these values in the "with" block.`,
    );
```

- 최상위 config.yaml 본문에 쓴 `${{ inputs.X }}`는 어떤 데이터로도 채워지지 않아 **원문 그대로 남음**(오류 없음).
- 주입 블록(`--rule`, `--mcp`, `--model` 등 `injectBlocks`)은 `replaceInputsWithSecrets`로 `inputs.X` → `secrets.X`로 바꾼 뒤 비밀로 해석 (`:246-256`, `:549-560`).

### 1.5 검증 스키마 — `cy/schemas/index.ts`, `cy/schemas/models.ts`, `cy/schemas/mcp/index.ts`

zod `z.object`는 기본(non-strict)이라 **모르는 키는 오류 없이 버려짐**.

최상위 공통 (`cy/schemas/index.ts:100-107`):

```ts
export const baseConfigYamlSchema = z.object({
  name: z.string(),
  version: z.string(),
  schema: z.string().optional(),
  metadata: z.record(z.string()).and(commonMetadataSchema.partial()).optional(),
  env: envRecord.optional(),
  requestOptions: requestOptionsSchema.optional(),
});
```

`name`, `version`은 **필수**. config.yaml 스키마 (`:113-153`): 위 + `models`, `context`, `data`, `mcpServers`, `rules`, `prompts`, `docs` (모두 optional 배열, 각 항목은 "직접 정의" 또는 `{ uses, with?, override? }`; `rules`의 uses 형태에는 `override` 없음).

```ts
export const configYamlSchema = baseConfigYamlSchema.extend({
  models: z
    .array(
      z.union([
        modelSchema,
        z.object({
          uses: modelsUsesSchema,
          with: z.record(z.string()).optional(),
          override: partialModelSchema.optional(),
        }),
      ]),
    )
    .optional(),
  context: z.array(blockOrSchema(contextSchema)).optional(),
  data: z.array(blockOrSchema(dataSchema)).optional(),
  mcpServers: z
    .array(
      z.union([
        mcpServerSchema,
        z.object({
          uses: defaultUsesSchema,
          with: z.record(z.string()).optional(),
          override: partialMcpServerSchema.optional(),
        }),
      ]),
    )
    .optional(),
  rules: z
    .array(
      z.union([
        ruleSchema,
        z.object({
          uses: defaultUsesSchema,
          with: z.record(z.string()).optional(),
        }),
      ]),
    )
    .optional(),
  prompts: z.array(blockOrSchema(promptSchema)).optional(),
  docs: z.array(blockOrSchema(docSchema)).optional(),
});
```

`context` 항목 (`:11-15`): `name?`, `provider`(필수), `params?: any`.
`prompts` 항목 (`:19-24`): `name`, `description?`, `prompt`, `sourceFile?`.
`docs` 항목 (`:28-35`): `name`, `startUrl`, `rootUrl?`, `faviconUrl?`, `useLocalCrawling?`, `sourceFile?`.
`rules` 항목 (`:39-49`): 문자열 또는

```ts
const ruleObjectSchema = z.object({
  name: z.string(),
  rule: z.string(),
  description: z.string().optional(),
  globs: z.union([z.string(), z.array(z.string())]).optional(),
  regex: z.union([z.string(), z.array(z.string())]).optional(),
  alwaysApply: z.boolean().optional(),
  invokable: z.boolean().optional(),
  sourceFile: z.string().optional(),
});
```

모델 항목 (`cy/schemas/models.ts:178-205`):

```ts
const baseModelFields = {
  name: z.string(),
  model: z.string(),
  apiKey: z.string().optional(),
  apiBase: z.string().optional(),
  contextLength: z.number().optional(),
  maxStopWords: z.number().optional(),
  roles: modelRolesSchema.array().optional(),
  capabilities: modelCapabilitySchema.array().optional(),
  defaultCompletionOptions: completionOptionsSchema.optional(),
  cacheBehavior: cacheBehaviorSchema.optional(),
  requestOptions: requestOptionsSchema.optional(),
  embedOptions: embedOptionsSchema.optional(),
  chatOptions: chatOptionsSchema.optional(),
  promptTemplates: promptTemplatesSchema.optional(),
  useLegacyCompletionsEndpoint: z.boolean().optional(),
  useResponsesApi: z.boolean().optional(),
  env: z
    .record(z.string(), z.union([z.string(), z.boolean(), z.number()]))
    .optional(),
  autocompleteOptions: autocompleteOptionsSchema.optional(),
};

export const modelSchema = z.object({
  ...baseModelFields,
  provider: z.string(),
  sourceFile: z.string().optional(),
});
```

필수: `name`, `model`, `provider`. roles 열거 (`:23-32`):

```ts
export const modelRolesSchema = z.enum([
  "chat",
  "autocomplete",
  "embed",
  "rerank",
  "edit",
  "apply",
  "summarize",
  "subagent",
]);
```

`defaultCompletionOptions` (`:47-63`): `contextLength, maxTokens, temperature, topP, topK, minP, presencePenalty, frequencyPenalty, stop[], n, reasoning, reasoningBudgetTokens, promptCaching, stream, keepAlive` (전부 optional).
`requestOptions` (`:12-21`): `timeout, verifySsl, caBundlePath(문자열|배열), proxy, headers, extraBodyProperties, noProxy[], clientCertificate{cert,key,passphrase?}`.
`capabilities` (`:37-42`): `"tool_use" | "image_input" | "next_edit" | 임의 문자열`.
`chatOptions` (`:117-123`): `baseSystemMessage, baseAgentSystemMessage, basePlanSystemMessage, toolOverrides{도구명: {description, displayTitle, wouldLikeTo, isCurrently, hasAlready, systemMessageDescription{prefix, exampleArgs}, disabled}}`.

**기본 roles**: 스키마에는 기본값이 없음(`roles` 생략 시 undefined). cn은 `roles`가 **없거나** `"chat"`을 포함하면 chat 모델로 취급:
- `cli/config.ts:68-71` (`getLlmApi`): `model?.roles?.includes("chat") || (model && model.roles === undefined)` — 첫 번째 일치 모델 사용. 없으면 `"No models found in the configured assistant"` / `"No models with the chat role found in the configured assistant"`.
- `cli/services/ModelService.ts:56,168,217,272,294` 동일 조건. `subagent`는 명시적으로 `roles`에 있어야 함(`:316`).
- chat 모델이 없으면 `anthropic/claude-sonnet-4-6@1.0.0` 슬러그를 주입 시도 (`cli/services/ConfigService.ts:31-38`, `:215-251`). 로컬 RegistryClient는 슬러그 미지원이라 실패 → headless면 `"No model specified in headless mode (and failed to load default model)"` throw, TUI면 로그만.

MCP 서버 (`cy/schemas/mcp/index.ts:4-35`): 공통 `name`(필수), `serverName?`, `faviconUrl?`, `sourceFile?`, `sourceSlug?`, `connectionTimeout?`(>0) + stdio형 `command`(필수), `type?: "stdio"`, `args?`, `env?: Record<string,string>`, `cwd?` 또는 원격형 `url`(필수), `type?: "sse" | "streamable-http"`, `apiKey?`, `requestOptions?`.

### 1.6 `uses:` / 블록 참조의 로컬 처리 — `cy/load/unroll.ts:367-684`

- 처리 섹션: `["models", "context", "data", "mcpServers", "prompts", "docs"]` + `rules` 별도 (`:392-401`, `:495-541`).
- `uses` 문자열 해석 `decodePackageIdentifier` (`cy/interfaces/slugs.ts:98-125`):
  - `.` 또는 `/`로 시작 → 파일
  - `file://` → 파일(접두사 제거)
  - `~` → 파일, 선행 `~`를 `HOME`(없으면 `USERPROFILE`)로 치환 (`:22-46`)
  - 그 외 → 허브 슬러그(`owner/package[@version]`, 버전 없으면 `latest`)
- 파일 블록 읽기: `RegistryClient.getContent` (1.2절 규칙, 상대경로는 cwd 먼저 → config 파일 디렉터리(rootPath)).
- 슬러그 블록: cn 로컬 경로에서는 `RegistryClient`가 `"Slug-based package resolution is not supported"` throw (`cy/registryClient.ts:26-27`).
- 블록 해석 실패는 **비치명** (`fatal: false`): 콘솔에 `` `Failed to unroll block ${JSON.stringify(unrolledBlock.uses)}: ${message}` `` 출력, 해당 항목은 `null`로 남음, 오류 메시지는 `` `${message}.\n> ${JSON.stringify(uses)}` `` (`:450-470`). rules는 `` `${message}:\n${rule.uses}` `` (`:509-521`). 이후 cn이 `Warning: ...`로 출력하고 계속.
- 블록 파일은 `blockSchema`로 검증: `name`, `version` + 섹션 하나에 **정확히 1개 항목** (`cy/schemas/index.ts:194-206`). 블록의 해당 섹션 첫 항목(`[section]?.[0]`)만 사용 (`unroll.ts:441`).
- `override:`는 얕은 키 덮어쓰기 (`mergeOverrides`, `:832-842`).
- 블록 파일이 `.yaml/.yml`이 아니고 YAML 파싱 실패하면 markdown 규칙(front matter)으로 해석 시도 (`:782-809`).
- 중복 제거 `BlockDuplicationDetector` (`cy/load/blockDuplicationDetector.ts`): rules는 문자열 자체 또는 `name`, context는 `name ?? params.title ?? provider`, 나머지는 `name` 기준으로 같은 섹션 안 **나중 항목을 버림**. 주입 블록 병합(`mergeUnrolledAssistants`, `cy/load/merge.ts:26-59`)은 `incoming`을 앞에 두고 dedupe → **주입 블록이 같은 이름의 기존 항목을 이김**.
- 블록 쪽 `${{ secrets.X }}`도 블록 id가 FQSN 접두로 붙지만 최종 조회는 마지막 이름 기준(1.4절)이라 로컬에선 동일하게 `.env`/환경변수에서 찾음.

---

## 2. 민감 파일 차단 — `core/indexing/ignore.ts`

### 2.1 차단 목록 원문 (보안용, cn Read/Edit 판정에 쓰임)

`core/indexing/ignore.ts:8-61`:

```ts
export const DEFAULT_SECURITY_IGNORE_FILETYPES = [
  // Environment and configuration files with secrets
  "*.env",
  "*.env.*",
  ".env*",
  "config.json",
  "config.yaml",
  "config.yml",
  "settings.json",
  "appsettings.json",
  "appsettings.*.json",

  // Certificate and key files
  "*.key",
  "*.pem",
  "*.p12",
  "*.pfx",
  "*.crt",
  "*.cer",
  "*.jks",
  "*.keystore",
  "*.truststore",

  // Database files that may contain sensitive data
  "*.db",
  "*.sqlite",
  "*.sqlite3",
  "*.mdb",
  "*.accdb",

  // Credential and secret files
  "*.secret",
  "*.secrets",
  "auth.json",
  "*.token",

  // Backup files that might contain sensitive data
  "*.bak",
  "*.backup",
  "*.old",
  "*.orig",

  // Docker secrets
  "docker-compose.override.yml",
  "docker-compose.override.yaml",

  // SSH and GPG
  "id_rsa",
  "id_dsa",
  "id_ecdsa",
  "id_ed25519",
  "*.ppk",
  "*.gpg",
];
```

`core/indexing/ignore.ts:63-91`:

```ts
export const DEFAULT_SECURITY_IGNORE_DIRS = [
  // Environment and configuration directories
  ".env/",
  "env/",

  // Cloud provider credential directories
  ".aws/",
  ".gcp/",
  ".azure/",
  ".kube/",
  ".docker/",

  // Secret directories
  "secrets/",
  ".secrets/",
  "private/",
  ".private/",
  "certs/",
  "certificates/",
  "keys/",
  ".ssh/",
  ".gnupg/",
  ".gpg/",

  // Temporary directories that might contain sensitive data
  "tmp/secrets/",
  "temp/secrets/",
  ".tmp/",
];
```

### 2.2 인덱싱 전용 목록 원문 (cn 도구 판정에는 **안 쓰임**, 참고)

`core/indexing/ignore.ts:94-162`:

```ts
export const ADDITIONAL_INDEXING_IGNORE_FILETYPES = [
  "*.DS_Store",
  "*-lock.json",
  "*.lock",
  "*.log",
  "*.ttf",
  "*.png",
  "*.jpg",
  "*.jpeg",
  "*.gif",
  "*.mp4",
  "*.svg",
  "*.ico",
  "*.pdf",
  "*.zip",
  "*.gz",
  "*.tar",
  "*.dmg",
  "*.tgz",
  "*.rar",
  "*.7z",
  "*.exe",
  "*.dll",
  "*.obj",
  "*.o",
  "*.o.d",
  "*.a",
  "*.lib",
  "*.so",
  "*.dylib",
  "*.ncb",
  "*.sdf",
  "*.woff",
  "*.woff2",
  "*.eot",
  "*.cur",
  "*.avi",
  "*.mpg",
  "*.mpeg",
  "*.mov",
  "*.mp3",
  "*.mkv",
  "*.webm",
  "*.jar",
  "*.onnx",
  "*.parquet",
  "*.pqt",
  "*.wav",
  "*.webp",
  "*.wasm",
  "*.plist",
  "*.profraw",
  "*.gcda",
  "*.gcno",
  "go.sum",
  "*.gitignore",
  "*.gitkeep",
  "*.continueignore",
  "*.csv",
  "*.uasset",
  "*.pdb",
  "*.bin",
  "*.pag",
  "*.swp",
  "*.jsonl",
  // "*.prompt", // can be incredibly confusing for the LLM to have another set of instructions injected into the prompt
  // Application specific
  ".continue/",
];
```

`core/indexing/ignore.ts:164-190`:

```ts
export const ADDITIONAL_INDEXING_IGNORE_DIRS = [
  ".git/",
  ".svn/",
  "node_modules/",
  "dist/",
  "build/",
  "Build/",
  "target/",
  "out/",
  "bin/",
  ".pytest_cache/",
  ".vscode-test/",
  "__pycache__/",
  "site-packages/",
  ".gradle/",
  ".mvn/",
  ".cache/",
  "gems/",
  "vendor/",

  ".venv/",
  "venv/",

  ".vscode/",
  ".idea/",
  ".vs/",
];
```

결합/glob (`:193-208`): `DEFAULT_IGNORE_FILETYPES = [...보안파일, ...추가파일]`, `DEFAULT_IGNORE_DIRS = [...보안디렉터리, ...추가디렉터리]`, `DEFAULT_IGNORES = [...둘 다]`, `` defaultIgnoresGlob = `!{${DEFAULT_IGNORES.join(",")}}` ``.

### 2.3 판정 함수 원문 — `core/indexing/ignore.ts:230-264`

```ts
export const defaultFileAndFolderSecurityIgnores = ignore()
  .add(defaultSecurityIgnoreFile)
  .add(defaultSecurityIgnoreDir);

export function isSecurityConcern(filePathOrUri: string) {
  if (!filePathOrUri) {
    return false;
  }
  let filepath = filePathOrUri;
  try {
    filepath = fileURLToPath(filePathOrUri);
  } catch {}
  if (path.isAbsolute(filepath)) {
    const dir = path.dirname(filepath).split(/\/|\\/).at(-1) ?? "";
    const basename = path.basename(filepath);
    filepath = `${dir ? dir + "/" : ""}${basename}`;
  }
  if (!filepath) {
    return false;
  }
  return defaultFileAndFolderSecurityIgnores.ignores(filepath);
}

export function throwIfFileIsSecurityConcern(filepath: string) {
  if (isSecurityConcern(filepath)) {
    throw new ContinueError(
      ContinueErrorReason.FileIsSecurityConcern,
      `Reading or Editing ${filepath} is not allowed because it is a security concern. Do not attempt to read or edit this file in any way.`,
    );
  }
}
```

판정 규칙 요약:
- 판정기는 npm `ignore` 패키지(`^7.0.5`, `core/package.json:88`) = **gitignore 문법**. 슬래시 없는 패턴(`config.yaml`, `*.env`)은 어느 깊이의 basename에도 일치, `dir/` 패턴은 경로 중 그 이름의 디렉터리가 있으면 그 아래 전부 일치.
- `file://` URI면 경로로 변환.
- **절대경로는 "바로 위 디렉터리 이름 + `/` + 파일명" 두 조각으로 줄여서 판정.** 예: `/home/u/.ssh/id_rsa` → `.ssh/id_rsa`(차단), `/proj/secrets/sub/a.txt` → `sub/a.txt`(**통과**).
- 상대경로는 전체 경로로 판정. 예: `secrets/sub/a.txt` → 차단.
- `ContinueErrorReason.FileIsSecurityConcern = "file_is_security_concern"` (`core/util/errors.ts:48`).
- **[라이브러리·미검증]** `ignore` v7은 `../x`, `./x`, `/x`처럼 `path.relative()` 형태가 아닌 경로를 받으면 `RangeError`(`path should be a \`path.relative()\`d string, but got "..."`)를 던짐. cn Read는 `./`만 벗기므로 `../foo` 같은 상대경로를 넣으면 이 RangeError 문구가 도구 오류로 모델에게 감.

### 2.4 cn 도구별 적용

| 도구 | 판정 대상 경로 | 위치 |
|---|---|---|
| Read (`readFileTool`, 이름 `"Read"`) | 인자 `filepath`에서 앞 `./`만 제거한 **원문 경로**(realpath 전) | `cli/tools/readFile.ts:51-56` (preprocess) |
| Edit (`"Edit"`) | `path.resolve(cwd, file_path)` 후 `fs.realpathSync` 한 **절대 실경로** → 위 "부모디렉터리/파일명" 규칙 | `cli/tools/edit.ts:20-58` (`validateAndResolveFilePath`, `:39`) |
| MultiEdit | Edit와 같은 `validateAndResolveFilePath` | `cli/tools/multiEdit.ts:13,106` |
| Write (`writeFile.ts`) | **판정 없음** (민감 파일 검사·Read 선행 검사 모두 없음) | `cli/tools/writeFile.ts:51-119` |

- Read는 심볼릭 링크를 따라가기 전 경로로 판정하므로, 링크 이름이 안전해 보이면 실제 대상이 민감 파일이어도 통과함. Edit는 실경로로 판정.
- cn에서 `core/indexing/ignore`를 import하는 곳은 위 두 파일뿐 (`grep` 결과). `.continueignore`/`.gitignore`는 도구 판정에 안 쓰임. (`cli/tools/searchCode.ts:14-25`는 `.gitignore` 패턴을 rg/grep 제외 인자로만 씀. `cli/util/filePatterns.ts:5-49`의 `FILE_IGNORE_PATTERNS`는 파일 인덱스/감시용.)

### 2.5 cn이 내는 관련 오류 글 원문

preprocess에서 던진 오류는 `error.message` 그대로 tool 메시지 내용이 됨 (`cli/stream/streamChatResponse.helpers.ts:421-456`의 `errorChatEntries.push({ role: "tool", tool_call_id, content: errorMessage })`).

```text
Reading or Editing ${filepath} is not allowed because it is a security concern. Do not attempt to read or edit this file in any way.
```
(`core/indexing/ignore.ts:261`. `${filepath}`는 Read면 `./` 뗀 인자 경로, Edit면 실경로)

Read 기타 (`cli/tools/readFile.ts`):
```text
File does not exist: ${filepath}
```
(`:80`)
```text
File is too large to read: ${filepath} (${charCount.toLocaleString()} characters, ${lineCount.toLocaleString()} lines). Maximum allowed: ${maxChars.toLocaleString()} characters or ${maxLines.toLocaleString()} lines.${parallelNote} Consider using terminal commands like 'head', 'tail', 'sed', or 'grep' to read targeted parts of the file.
```
(`:104-106`, `parallelNote` = `` ` (Note: limit reduced due to ${parallelCount} parallel tool calls. Single-tool limit: ${baseMaxChars.toLocaleString()} characters or ${baseMaxLines.toLocaleString()} lines.)` ``; 기본 한도 100000자 / 5000줄, 환경변수 `CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS` / `CONTINUE_CLI_READ_FILE_MAX_OUTPUT_LINES`, 병렬 호출 수로 나눔 `:11-27,86-91`)
```text
Error reading file: ${message}
```
(`:118-122`, ContinueError가 아닌 오류)
성공 출력: `` `Content of ${filepath}:\n${content}` `` (`:113`)

Edit (`cli/tools/edit.ts`):
```text
file_path is required
```
(`:29`)
```text
File ${file_path} does not exist
```
(`:45` — 단, 바로 앞 `:37`의 `fs.realpathSync`가 없는 파일에서 먼저 ENOENT를 던지므로 실제로는 Node의 `ENOENT: no such file or directory, realpath '...'` 문구가 나감)
```text
You must use the ${readFileTool.name} tool to read ${file_path} before editing it.
```
(`:53`, `readFileTool.name` = `Read` → `You must use the Read tool to read <file_path> before editing it.`; reason `edit_tool_file_not_yet_read`, `core/util/errors.ts:42`)

---

## 3. 토큰 세기 — `cli/util/tokenizer.ts`

### 3.1 인코딩

- `import { encode } from "gpt-tokenizer";` (`cli/util/tokenizer.ts:4`, `cli/compaction.ts:4`). 버전 `^3.0.1`, lock 3.0.1 (`extensions/cli/package.json:106`, `package-lock.json`).
- **[라이브러리·미검증]** gpt-tokenizer v3의 기본 export `encode`는 `o200k_base` 인코딩(GPT-4o 계열). Python에서는 `tiktoken.get_encoding("o200k_base")`가 대응. 또한 기본 설정에서 입력에 `<|endoftext|>` 같은 특수 토큰 문자열이 있으면 throw할 수 있음 → cn에선 `countChatHistoryItemTokens`의 catch로 빠져 "4자=1토큰" 추정으로 대체됨(아래 3.3).

### 3.2 모델별 보정 배수 — `core/llm/getAdjustedTokenCount.ts:7-38`

```ts
const ANTHROPIC_TOKEN_MULTIPLIER = 1.23;
const GEMINI_TOKEN_MULTIPLIER = 1.18;
const MISTRAL_TOKEN_MULTIPLIER = 1.26;

export function getAdjustedTokenCountFromModel(
  baseTokens: number,
  modelName: string,
): number {
  let multiplier = 1;
  const lowerModelName = modelName?.toLowerCase() ?? "";
  if (lowerModelName.includes("claude")) {
    multiplier = ANTHROPIC_TOKEN_MULTIPLIER;
  } else if (lowerModelName.includes("gemini")) {
    multiplier = GEMINI_TOKEN_MULTIPLIER;
  } else if (
    lowerModelName.includes("stral") ||
    lowerModelName.includes("mixtral")
  ) {
    // Mistral family models: mistral, mixtral, codestral, devstral, etc
    multiplier = MISTRAL_TOKEN_MULTIPLIER;
  }
  return Math.ceil(baseTokens * multiplier);
}
```

- 판정 대상은 `model.model`(모델 id 문자열, `name` 아님) (`cli/util/tokenizer.ts:45,58`).
- **배수는 메시지 `content`에만 적용**. 도구 호출, 도구 출력, 컨텍스트 항목, 시스템 메시지, 도구 정의, 고정 오버헤드에는 적용 안 됨.

### 3.3 고정값 전부

메시지 content (`cli/util/tokenizer.ts:39-62`):
- 문자열: `ceil(len(encode(content)) * 배수)`
- 배열(멀티모달): `type==="text"` 부분 `encode(text)` 합 + `type==="imageUrl"` 하나당 **1024** → 합계에 배수 적용 후 ceil
- 그 외 0

도구 호출 함수 하나 (`:67-78`):
```ts
  tokenCount += encode(toolCallFunction.name ?? "").length + 10; // Function name and structure overhead
  tokenCount += encode(toolCallFunction.arguments ?? "").length; // Arguments
```

도구 출력 (`:83-99`): 각 출력 항목마다 `encode(item.content)`(있으면) + **5**.

히스토리 항목 하나 `countChatHistoryItemTokens` (`:106-170`):
1. content 토큰(배수 적용)
2. 역할 **+2**
3. `message.toolCalls`가 있고 `historyItem.toolCallStates`가 **없을 때만** 각 호출에 `countToolCallFunctionTokens` (중복 방지)
4. `message.role === "tool"`이고 content 있으면 **+5**
5. 각 `contextItems`: `encode(content) + encode(name) + 5`
6. 각 `toolCallStates`: `countToolCallFunctionTokens(toolState.toolCall.function)` + `countToolOutputTokens(toolState.output)`
7. 예외 시: `Math.ceil((content.length + contextItems의 (content+name) 길이 합) / 4)` (content가 문자열 아니면 `JSON.stringify`)

히스토리 전체 (`:177-191`): 항목 합 + **항목 수 × 3**.

도구 정의 (`:212-295`):
```ts
  if (fieldType && typeof fieldType === "string") {
    tokens += 2; // Structure overhead for type
    tokens += encode(fieldType).length;
  }

  if (fieldDesc && typeof fieldDesc === "string") {
    tokens += 2; // Structure overhead for description
    tokens += encode(fieldDesc).length;
  }

  if (fieldEnum && Array.isArray(fieldEnum) && fieldEnum.length > 0) {
    tokens -= 3;
    for (const e of fieldEnum) {
      tokens += 3;
      tokens += typeof e === "string" ? encode(e).length : 5;
    }
  }
```
- 도구 하나: `encode(name)` + `encode(description)`(있으면) + `parameters.properties`의 각 키 `encode(key)` + 위 필드 토큰. (중첩 객체/`required`/`items`는 세지 않음, 최상위 properties만)
- 전체: 도구가 없으면 0, 있으면 **12** + Σ도구 + **12** (`:281-295`)

총 입력 `countTotalInputTokens` (`:313-335`):
- 히스토리 토큰
- `systemMessage`가 있고 히스토리에 `role==="system"` 항목이 없으면 `encode(systemMessage).length + 4` (배수 없음)
- 도구 정의 토큰

### 3.4 컨텍스트 길이·출력 예약 기본값 (`cli/util/tokenizer.ts:9-34`)

```ts
const DEFAULT_MAX_TOKENS_RATIO = 0.35;
const MAX_MAX_TOKENS = 64_000;
// Default context length when model config doesn't specify one
export const DEFAULT_CONTEXT_LENGTH = 200_000;

export function getModelContextLimit(model: ModelConfig): number {
  return (
    model.defaultCompletionOptions?.contextLength ?? DEFAULT_CONTEXT_LENGTH
  );
}

export function getModelMaxTokens(model: ModelConfig): number {
  const contextLimit = getModelContextLimit(model);
  const maxTokens = model.defaultCompletionOptions?.maxTokens;

  return maxTokens === undefined
    ? Math.ceil(
        Math.min(contextLimit * DEFAULT_MAX_TOKENS_RATIO, MAX_MAX_TOKENS),
      )
    : maxTokens;
}
```

- **주의: 컨텍스트 길이는 `defaultCompletionOptions.contextLength`만 봄.** 모델 최상위 `contextLength` 필드는 cn 토큰 계산에서 무시됨(cn 소스 전체에서 `contextLength` 참조는 이 줄과 `yamlConfigUpdater.ts`뿐).
- 기본: contextLimit 200000, maxTokens = `ceil(min(limit*0.35, 64000))` → 200k면 64000.

### 3.5 요청 직전 검증 `validateContextLength` (`:354-393`)

```ts
  const maxTokens = model.defaultCompletionOptions?.maxTokens || 0;

  // If maxTokens is not set, use 35% default reservation for output
  const reservedForOutput =
    maxTokens > 0 ? maxTokens : Math.ceil(contextLimit * 0.35);
  const totalRequired = inputTokens + reservedForOutput + safetyBuffer;
```

- 여기서는 64000 상한이 **없음**(`getModelMaxTokens`와 불일치). 200k 기본이면 70000 예약.
- 오류 글 원문: `` `Context length exceeded: input (${inputTokens.toLocaleString()}) + max_tokens (${reservedForOutput.toLocaleString()})${safetyBuffer > 0 ? ` + buffer (${safetyBuffer})` : ""} = ${totalRequired.toLocaleString()} > context_limit (${contextLimit.toLocaleString()})` `` (`:380`)
- 호출처: `cli/stream/streamChatResponse.ts:218-246` — `SAFETY_BUFFER = 100`, 초과 시 `pruneLastMessage`로 끝에서부터 잘라내며 재검증, 끝내 초과면 `` `Context length validation failed: ${validation.error}` `` throw. 도구 실행 후 `cli/stream/streamChatResponse.compactionHelpers.ts:96-168` — 같은 버퍼 100, 초과면 강제 압축, 압축 후에도 초과면 `` `Context limit exceeded even after compaction: ${...error}` ``, 압축 실패면 `"Context limit exceeded and compaction failed. Unable to continue."`.

### 3.6 자동 압축 임계 — `cli/compaction.ts:18-20, 266-315`

```ts
export const AUTO_COMPACT_BUFFER_CAP = 15_000;
export const AUTO_COMPACT_BUFFER_RATIO = 0.8;
```

```ts
  const inputTokens = countTotalInputTokens({ chatHistory, systemMessage, tools, model });
  const contextLimit = getModelContextLimit(model);
  const maxTokens = getModelMaxTokens(model);

  const ratioCompactionBuffer = Math.ceil(
    (1 - AUTO_COMPACT_BUFFER_RATIO) * (contextLimit - maxTokens),
  );
  const safeCompactionBuffer = Math.max(maxTokens, ratioCompactionBuffer);
  const compactionBuffer = Math.min(
    safeCompactionBuffer,
    AUTO_COMPACT_BUFFER_CAP,
  );

  const compactionThreshold = contextLimit - maxTokens - compactionBuffer;
  ...
  const shouldCompact = inputTokens >= compactionThreshold;
```

식으로 정리:

```
maxTokens  = defaultCompletionOptions.maxTokens ?? ceil(min(L*0.35, 64000))
buffer     = min( max(maxTokens, ceil(0.2*(L - maxTokens))), 15000 )
threshold  = L - maxTokens - buffer
압축 여부   = countTotalInputTokens(...) >= threshold
threshold <= 0 이면 throw "max_tokens is larger than context_length, which should not be possible. Please check your configuration."
```

- 예: L=200000, maxTokens 기본 64000 → buffer = min(max(64000, 27200), 15000) = 15000 → threshold = 121000.
- 예: L=32768, maxTokens 미지정 → maxTokens = ceil(11468.8)=11469 → buffer = min(max(11469, 4260), 15000) = 11469 → threshold = 9830.
- 압축 시 안내 문구 `` `Approaching context limit (${(limit / 1000).toFixed(0)}K tokens). Auto-compacting chat history...` `` (`:255-258`), 성공 `"Chat history auto-compacted successfully."`, 실패 `` `Warning: Auto-compaction error: ${formatError(error)}. Continuing without compaction...` `` (`cli/stream/streamChatResponse.autoCompaction.ts:64,96-101`).
- 압축 요청 자체의 입력 한도 (`cli/compaction.ts:73-114`): `available = L - getModelMaxTokens - (히스토리에 system 없으면 systemMessageTokens) - 150`(`COMPACTION_PROMPT_TOKENS`), 넘으면 `pruneLastMessage`로 잘라냄. `systemMessageTokens`는 시스템 메시지를 history 항목처럼 센 값(`countChatHistoryItemTokens`, 배수+2 포함, `autoCompaction.ts:177-187`).
- 압축 결과: `[system 메시지(있으면), { role: "assistant", content: 요약, conversationSummary: 요약 }]` (`cli/compaction.ts:141-160`).
- 압축 프롬프트 원문 (`cli/compaction.ts:40-41`):

```text
Please provide a concise summary of our conversation so far, capturing the key context, decisions made, and current state. Format this as a single comprehensive message that preserves all important information needed to continue our work. You do not need to recap the system message, as this will remain. Make sure it is clear what the current stream of work was at the very end prior to compaction so that you can continue exactly where you left off without missing any information.
```

- 자동 압축 확인 지점: API 호출 전(`handlePreApiCompaction`), 도구 실행 후 초과 시(`handlePostToolValidation`), 턴 후 일반 점검(`handleNormalAutoCompaction`) (`cli/stream/streamChatResponse.compactionHelpers.ts:27,75,176`). 수동 `/compact`는 `cli/commands/chat.ts:134`, `cli/ui/hooks/useChat.compaction.ts:47`.

---

## 4. "이번 대화에서 Read한 파일" 기록과 압축

정의 (`cli/tools/readFile.ts:29-33`):

```ts
// Track files that have been read in the current session
export const readFilesSet = new Set<string>();
export function markFileAsRead(filePath: string) {
  readFilesSet.add(filePath);
}
```

- Read 성공 시 `fs.realpathSync(filepath)` 실경로를 추가 (`:83,111`). 파일 크기 초과 등으로 실패하면 추가 안 됨.
- Edit/MultiEdit는 실경로로 `readFilesSet.has(resolvedPath)` 확인 (`cli/tools/edit.ts:37,50`).
- **모듈 전역 Set이고, cn 소스(테스트 제외)에 `clear()`/`delete()` 호출이 전혀 없음** (`grep readFilesSet` 결과: 정의·추가·조회만, `clear`는 `cli/tools/edit.test.ts:54` 테스트 전용).
- 따라서 **자동/수동 대화 압축 후에도 유지되고 초기화되지 않음.** `/clear`, 세션 재개(`--resume`), 모델/설정 전환 때도 초기화되지 않으며 **cn 프로세스가 살아 있는 동안 계속 누적**됨. (요약 후 모델이 내용을 잊어도 Edit 전제조건은 이미 충족된 상태)
- Write는 이 Set을 보지도, 추가하지도 않음 (`cli/tools/writeFile.ts`). 따라서 Write로 새로 만든 파일을 Edit하려면 Read가 먼저 필요함.
- 같은 프로세스 안의 서브에이전트도 같은 모듈 Set을 공유함(별도 분리 코드 없음).
