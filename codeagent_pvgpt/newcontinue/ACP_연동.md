# ncn `--stdio` ACP 연동 안내(VS 2022 확장 개발자용)

> 대상: `ncn --stdio`를 자식 프로세스로 띄우는 IDE 확장(C#/WPF). 규격: Agent Client Protocol v1(`protocolVersion: 1`), Python SDK `agent-client-protocol` 0.12.1.
> 구현: `ncn/acp/server.py`(세션·승인·업데이트), `ncn/acp/convert.py`(형식 변환), `ncn/acp/stdio.py`(stdout 확보), `ncn/fsio.py`(fs 위임).
> 시험: `tests/test_acp.py`(가짜 IDE가 ncn을 실제 자식 프로세스로 띄움).

## 1. 프로세스와 전송

| 항목 | 내용 |
|---|---|
| 실행 | `ncn.exe --stdio [--config <path>] [--allow T] [--ask T] [--exclude T] [--readonly\|--auto] [--rule R] [--verbose]` |
| 작업 폴더 | 아무 곳에서나 띄워도 된다. `session/new`의 `cwd`로 이동한다(도구의 상대 경로 기준) |
| stdin | 줄 단위 JSON-RPC 2.0(UTF-8, 한 메시지 = 한 줄, `\n` 또는 `\r\n`) |
| stdout | **프로토콜 JSON 줄만**. UTF-8, 줄 끝 `\n`(CRLF 없음). 한글은 이스케이프 없이 그대로 |
| stderr | 로그·경고(기본 WARNING, `--verbose`면 DEBUG). 사용자에게 보일 필요 없음, 진단용으로 모아 두면 좋다 |
| 종료 | stdin을 닫으면(EOF) 정리 후 종료 코드 0. 옵션 오류는 시작 시 stderr 안내 + 종료 코드 1(`-p`와 같이 씀 등), 미구현 옵션(`--org`·`--agent`·`--mcp`)은 2 |
| 권장 | Job Object로 VS와 함께 종료(V6), VS 개발자 환경변수 전달(V7) |

ncn은 시작하자마자 진짜 stdout을 복제해 프로토콜 전용으로 쓰고, 파일 기술자 1과 `print`는 stderr로 돌린다. 자식 프로세스(Bash 도구)가 stdout에 써도 프로토콜이 깨지지 않는다.

## 2. 메시지 순서

```
IDE                                   ncn
 │ initialize ─────────────────────────▶│
 │◀──────────────────── InitializeResponse
 │ session/new (cwd, mcpServers) ──────▶│   또는 session/load(sessionId) → 기록 되살리기 알림들 → 응답
 │◀──────── {sessionId, modes, configOptions}
 │ session/prompt (블록들) ────────────▶│
 │◀── session/update agent_message_chunk (글 조각, 여러 번)
 │◀── session/update tool_call (pending, 카드)
 │◀── session/request_permission ──────│   (ask 정책 도구만) 
 │ {outcome: selected allow_once} ─────▶│
 │◀── session/update tool_call_update (in_progress → completed|failed)
 │◀── session/update plan (Checklist 도구)
 │◀── elicitation/create (AskQuestion) │
 │◀────────────── PromptResponse {stopReason, usage}
 │ session/cancel (알림, 아무 때나) ───▶│   → 진행 중 prompt 가 stopReason "cancelled" 로 끝남
 │ session/set_mode / set_config_option ▶│   → current_mode_update, config_option_update
```

규칙: 같은 세션에 prompt가 돌고 있으면 새 prompt는 오류(-32600). 알림(session/update)은 항상 그 요청의 응답보다 먼저 도착한다. 도구 카드(`tool_call`)는 그 도구의 승인 요청보다 먼저 도착한다.

## 3. 메시지 예시

### 3-1. initialize
```json
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":1,
 "clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true},"elicitation":{"form":{}},"_meta":{"ncn":{"ask":true}}},
 "clientInfo":{"name":"ncn-vs","version":"0.1.0"}}}
```
```json
{"jsonrpc":"2.0","id":0,"result":{"protocolVersion":1,
 "agentCapabilities":{"loadSession":true,"promptCapabilities":{"image":false,"audio":false,"embeddedContext":true}, "...":"..."},
 "authMethods":[],"agentInfo":{"name":"ncn","title":"ncn (Continue CLI compatible)","version":"0.0.1"}}}
```
- `fs.readTextFile/writeTextFile`: 알리면 파일 도구(Read·Edit·MultiEdit·Write)가 `fs/read_text_file`·`fs/write_text_file`로 IDE에 위임한다(열린 버퍼 기준, Ctrl+Z 가능). 오류 응답이면 ncn이 디스크로 되돌아간다.
- `elicitation.form`: 알리면 AskQuestion을 `elicitation/create`로 묻는다.
- `_meta.ncn.ask: true`: (elicitation이 없을 때) AskQuestion을 확장 요청 `_ncn/ask`로 묻는다.

### 3-2. session/new
```json
{"jsonrpc":"2.0","id":1,"method":"session/new","params":{"cwd":"C:\\src\\shop","mcpServers":[]}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{"sessionId":"6f0c…",
 "modes":{"currentModeId":"normal","availableModes":[
   {"id":"normal","name":"normal","description":"Ask before running tools that need permission"},
   {"id":"plan","name":"plan","description":"Start in plan mode (read-only tools)"},
   {"id":"auto","name":"auto","description":"Start in auto mode (all tools allowed)"}]},
 "configOptions":[{"id":"mode","name":"Mode","category":"mode","type":"select","currentValue":"normal","options":[{"value":"normal","name":"normal","description":"…"},"…"]}]}}
```
- `mcpServers`는 받기만 하고 무시한다(MCP 연결은 P3, stderr에 경고).
- `sessionId`는 cn 세션 id와 같다(`<전역>/sessions/<id>.json`, cn과 같은 형식으로 저장 → `cn --resume`과도 호환).

### 3-3. session/load
```json
{"jsonrpc":"2.0","id":2,"method":"session/load","params":{"sessionId":"6f0c…","cwd":"C:\\src\\shop","mcpServers":[]}}
```
응답 전에 저장된 기록을 `user_message_chunk` / `agent_message_chunk` / `tool_call`(completed·failed) 알림으로 되살린다. 응답은 `{modes, configOptions}`. 세션 파일이 없으면 오류 -32002(Resource not found).

### 3-4. session/prompt
```json
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"6f0c…","prompt":[
 {"type":"text","text":"이 파일 "},
 {"type":"resource_link","name":"Coupon.cs","uri":"file:///C:/src/shop/Coupon.cs"},
 {"type":"text","text":" 할인율을 20%로"},
 {"type":"resource","resource":{"uri":"file:///C:/src/shop/Cart.cs#L10-20","text":"…선택 영역 글…","mimeType":"text/x-csharp"}}]}}
```
모델에 가는 사용자 메시지(cn 대화형 `@파일` 첨부와 같은 형식):
```
<context name="Coupon.cs">
(Coupon.cs 내용 — fs 위임이면 열린 버퍼 내용)
</context>

<context name="Cart.cs#L10-20">
…선택 영역 글…
</context>

이 파일 @Coupon.cs 할인율을 20%로
```
| 블록 | 처리 |
|---|---|
| `text` | 그대로 이어 붙임(구분자 없음) |
| `resource_link`(file) | 글에 `@경로`(작업 폴더 안이면 상대) + 파일 내용을 첨부. 없는 파일이면 내용 = 경로(cn과 같음) |
| `resource`(내장 글) | **IDE 맥락(현재 파일·선택 영역)용**. 글에는 넣지 않고 첨부만. URI 조각 `#L10-20`·`#L10:20`·`#10`이 있으면 이름 뒤에 `#L시작-끝` |
| image·audio·blob | 무시(stderr 경고). 능력에도 알리지 않는다 |

응답:
```json
{"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn","usage":{"totalTokens":38,"inputTokens":30,"outputTokens":8}}}
```
| stopReason | 뜻 |
|---|---|
| `end_turn` | 정상 끝. 승인 3번(아니오)으로 멈춘 경우도 이것 + `"_meta":{"ncn":{"stoppedByRejection":true}}` |
| `cancelled` | `session/cancel`을 받음 |

`usage`는 그 턴의 LLM 사용량 합(서버가 사용량을 줄 때만).

### 3-5. session/update 종류
```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"6f0c…","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"할인율을 "}}}}
```
```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"6f0c…","update":{"sessionUpdate":"tool_call",
 "toolCallId":"call_abc","title":"Edit(Coupon.cs)","kind":"edit","status":"pending",
 "content":[{"type":"diff","path":"C:\\src\\shop\\Coupon.cs","oldText":"…전체 이전 내용…","newText":"…전체 새 내용…"}],
 "locations":[{"path":"C:\\src\\shop\\Coupon.cs"}],"rawInput":{"file_path":"Coupon.cs","old_string":"0.9","new_string":"0.8"}}}}
```
```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"6f0c…","update":{"sessionUpdate":"tool_call_update",
 "toolCallId":"call_abc","status":"completed","content":[{"type":"diff","…":"…"},{"type":"content","content":{"type":"text","text":"Successfully edited …"}}],
 "rawOutput":"Successfully edited …"}}}
```
```json
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"6f0c…","update":{"sessionUpdate":"plan","entries":[
 {"content":"읽기","priority":"medium","status":"completed"},{"content":"고치기","priority":"medium","status":"pending"}]}}}
```
| 항목 | 규칙 |
|---|---|
| `kind` | Read→read, Edit/MultiEdit/Write→edit, Bash→execute, Search→search, Fetch→fetch, 그 밖(List·Checklist·AskQuestion·Skills…)→other |
| `title` | cn 화면 제목 그대로: `표시이름(첫 인자)` — 경로는 작업 폴더 기준 상대, 여러 줄 글은 첫 줄+`...`, 인자 없으면 이름만 |
| `status` | pending(카드) → in_progress(승인 직후) → completed / failed(오류·거부). 묻지 않는 도구는 pending → completed |
| `content` | Edit/MultiEdit/Write: `diff{path, oldText(새 파일이면 없음), newText}` — **파일 전체 글**. 그 밖: 결과 글 |
| `rawInput` | 모델이 준 인자(`_ncn`으로 시작하는 내부 키는 뺌) |
| `plan` | Checklist 성공 시. `- [ ]`→pending, `- [x]`→completed(cn ChecklistDisplay 정규식) |
| `current_mode_update`·`config_option_update` | 모드가 바뀔 때 |

### 3-6. session/request_permission(ncn → IDE)
```json
{"jsonrpc":"2.0","id":7,"method":"session/request_permission","params":{"sessionId":"6f0c…",
 "toolCall":{"toolCallId":"call_abc","title":"Edit(Coupon.cs)","kind":"edit","status":"pending","content":[{"type":"diff","…":"…"}],"rawInput":{"…":"…"}},
 "options":[{"optionId":"allow_once","name":"Continue","kind":"allow_once"},
            {"optionId":"allow_always","name":"Continue + don't ask again","kind":"allow_always"},
            {"optionId":"reject_once","name":"No, and tell Continue what to do differently","kind":"reject_once"}]}}
```
IDE 응답: `{"jsonrpc":"2.0","id":7,"result":{"outcome":{"outcome":"selected","optionId":"allow_once"}}}` 또는 `{"outcome":{"outcome":"cancelled"}}`.

| 선택 | cn 대화형 동작(그대로) |
|---|---|
| allow_once | 실행 |
| allow_always | `<전역>/permissions.yaml` allow에 규칙 추가 후 다시 읽음 → 같은 규칙은 이후 묻지 않음 |
| reject_once | 모델에 `Permission denied by user`, **응답 중단**(같은 응답의 남은 도구는 계속 묻는다 — 두 번째 거부부터는 중단·system 글 없음), 기록에 system `[Tool canceled - please tell Continue what to do differently]`(다음 질문 때 모델에 보임). prompt는 `end_turn` + `_meta.ncn.stoppedByRejection` |
| cancelled | 거부(`Permission denied by user`), system 글 없음 |

`diff`의 `oldText/newText`로 VS 내장 비교 창(`IVsDifferenceService`)을 띄우면 된다(V4). diff가 없는 도구(Bash 등)는 cn 승인 미리보기 글이 있으면 content text로 실리고, 없으면 `rawInput`(예: `command`)을 보여 주면 된다.

### 3-7. AskQuestion
우선순위: ① `elicitation.form` 능력 → `elicitation/create` ② `_meta.ncn.ask` → `_ncn/ask` ③ 승인 요청 형식.
```json
{"jsonrpc":"2.0","id":8,"method":"elicitation/create","params":{"sessionId":"6f0c…","toolCallId":"call_q","mode":"form","message":"어느 쪽?",
 "requestedSchema":{"type":"object","properties":{
   "choice":{"type":"string","title":"어느 쪽?","oneOf":[{"const":"A","title":"A"},{"const":"B","title":"B"}]},
   "custom":{"type":"string","title":"Custom answer (optional)"}}}}}
```
응답 `{"action":"accept","content":{"choice":"B"}}` → 모델에 `User selected option 2: "B"`. `custom`이 비어 있지 않으면 그것이 답(`User provided custom answer: "…"`). 선택지가 없으면 스키마는 `answer` 하나(필수). decline·cancel이면 기본 답(`defaultAnswer`, 없으면 빈 글).

`_ncn/ask` 요청 params: `{sessionId, toolCallId, question, options, defaultAnswer}` → 결과 `{"answer": "…", "isCustom": false}`.

승인 형식 대체: `options` = 선택지마다 `{optionId:"answer_<번호>", name:<선택지>, kind:"allow_once"}` + `{optionId:"skip", name:"Skip", kind:"reject_once"}`.

### 3-8. 모드
```json
{"jsonrpc":"2.0","id":9,"method":"session/set_mode","params":{"sessionId":"6f0c…","modeId":"plan"}}
{"jsonrpc":"2.0","id":10,"method":"session/set_config_option","params":{"sessionId":"6f0c…","configId":"mode","value":"normal"}}
```
plan = 쓰기 도구(Edit·MultiEdit·Write) 목록에서 빠지고 호출돼도 `Command blocked by security policy`, 시스템 메시지에 planMode 블록. auto = 전부 묻지 않음. 턴 도중에 바꿔도 다음 LLM 호출부터 반영(cn Shift+Tab과 같음).

### 3-9. session/cancel
`{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"6f0c…"}}` — LLM 스트림·재시도를 멈추고, 기다리던 승인·질문을 풀고(규격상 IDE는 걸린 승인 요청에 `cancelled`로 답해야 함), 도구가 1초 안에 안 끝나면 턴 작업을 취소한다. 받은 데까지의 응답 글은 기록에 넣지 않는다(cn Esc와 같음). prompt 응답은 `cancelled`.

## 4. VS 확장 요구사항
| # | 요구 | 이유 |
|---|---|---|
| R1 | **`fs/write_text_file`을 받으면 텍스트 버퍼를 고친 뒤 그 문서를 저장(디스크 반영)하고 나서 응답할 것** | ACP 규격상 "파일에 쓰기" 요청. 저장 전에 응답하면 같은 턴의 Bash·Search·빌드(`msbuild`·`dotnet test`)가 옛 디스크 내용을 본다. 버퍼 편집이므로 Ctrl+Z는 그대로 가능 |
| R2 | `fs/read_text_file`은 열린 문서면 버퍼(저장 안 한 변경 포함), 아니면 디스크 내용을 줄 것. 파일이 없으면 오류 응답 | Read·Edit·Write 원래 내용(diff `oldText`)이 IDE 화면과 같게. 오류면 ncn이 디스크로 다시 읽는다 |
| R3 | 걸린 `session/request_permission`·`elicitation/create`는 `session/cancel`을 보낸 뒤 `cancelled`로 답할 것 | 규격 |
| R4 | 한 응답에 도구가 여러 개면 승인 요청이 연달아 올 수 있다(3번 "아니오" 뒤에도 같은 묶음의 다음 도구를 묻는다 — cn 대화형과 같음) | cn executeStreamedToolCalls 권한 단계 |

## 5. ncn 확장(규격 밖)
| 이름 | 방향 | 내용 |
|---|---|---|
| `clientCapabilities._meta.ncn.ask` | IDE→ncn | `_ncn/ask`로 질문 받기 |
| `_ncn/ask`(요청) | ncn→IDE | AskQuestion(3-7) |
| `_ncn/retry`(알림) | ncn→IDE | `{sessionId, attempt, delayMs, error}` — LLM 재시도 직전. **이번 LLM 호출로 받은 agent_message_chunk를 화면에서 지워야 한다**(ncn은 그 조각을 버리고 다시 받음, 부록 B) |
| `_ncn/system_message`(알림) | ncn→IDE | `{sessionId, message}` — 압축 안내 등 |
| `PromptResponse._meta.ncn.stoppedByRejection` | ncn→IDE | 승인 3번으로 멈춤 |
| 모드 id | — | `normal`·`plan`·`auto`(cn PermissionMode 그대로), config option id `mode` |

## 6. 오류 처리
| 상황 | ncn 응답 |
|---|---|
| 설정 파일 오류·chat 모델 없음(session/new·load) | -32603 `Internal error`, `data.message` = cn 원문 오류 글 |
| LLM 오류(재시도 끝난 뒤) | prompt에 -32603, `message`·`data.message` = cn 오류 글(헤드리스 stderr JSON의 message와 같음) |
| 없는 세션 id | -32002 Resource not found |
| 모르는 모드·설정 id, 없는 cwd | -32602 Invalid params |
| 같은 세션에 prompt 중복 | -32600 Invalid request |
| 모르는 메서드 | -32601 Method not found |
| fs 위임 요청이 오류 | ncn이 디스크로 대신 읽기·쓰기(쓰기는 stderr 경고) |
| JSON이 아닌 입력 줄 | 버리고 stderr 로그(응답 없음) |
| 프로세스가 죽음 | 확장이 재시작 후 `session/load`(마지막 세션 id)로 이어 가기 |

## 7. 제한(현재)
- MCP 서버(`mcpServers`)·`terminal/*`·이미지 입력 미지원. 세션은 여러 개 열 수 있지만 작업 폴더(프로세스 cwd)는 하나라 **동시에 다른 폴더의 세션을 돌리면 안 된다**(prompt마다 그 세션 폴더로 이동).
- Write·Edit·MultiEdit 카드의 `oldText`는 fs 위임이 켜졌으면 `fs/read_text_file`(IDE 버퍼) 기준, 아니면 디스크 기준.
- 디스크 일관성은 R1(쓰기 후 저장)에 달려 있다.
