# codeagent_pvgpt — 작업 규칙(Claude Code 공용: 로컬 PC · Claude Code on the web)

## 먼저 읽을 것(순서대로)
1. `진행기록_질답.md` — 결정·질답·다음 할 일(이전 대화의 전부가 여기 있다)
2. `README.md` — 서버 쪽(코드에이전트 서버 + RAG) 사용법
3. `newcontinue/계획.md` — ncn(Continue 호환 CLI 하네스 + VS 2022 확장) 계획. **14장(4차 결정)이 앞 장보다 우선**
4. `newcontinue/P0/*.md` — cn 소스 분석 결과(이식 명세)

## 구성
- `agent_server/` `rag_service/` `common/` `llm_server/` `tools/` — 서버 쪽(Python 3.13, uv). Continue/ncn이 붙는 `/v1`(LLM 프록시)·`/mcp`(도구)
- `newcontinue/` — **ncn**: 별도 uv 프로젝트(Python ≥3.10, 개발 기준 3.14). 사용자 PC에 배포되는 클라이언트
  - `ncn/` 하네스 · `parity/` 차등 시험(녹화형 가짜 LLM·시나리오 JSON) · `tests/`
  - VS 2022 확장(C#, P6)은 `newcontinue/vs/`에 둘 예정

## 반드시 지킬 것
- **모든 코드에 줄 단위 한국어 주석**(사용자 고정 요청). 기존 파일의 주석 밀도·말투를 따른다.
- 문서·답변은 한국어, 짧은 문장, 표 활용.
- ncn은 **cn(Continue CLI) 동작을 소스 기준으로 그대로**: 기준 커밋 `5522c6f`. 시스템 메시지·도구 이름/인자/결과 글·권한·압축·토큰 세기는 원문과 같게. 다르게 할 것은 `계획.md` 부록 B와 6장(E1~E8)에만.
- ncn `--stdio`(ACP) 모드에서 **stdout에는 프로토콜 JSON 줄만**. 로그·경고는 stderr/파일(계획 14-2 V5).
- 결정된 것(계획 14-4·14-5): Windows 7 제외, Windows 10 1909+/11 x64·arm64, stdio = ACP, VS 확장 화면 = 순수 WPF, Python 하나로(공식 `mcp`·`agent-client-protocol` SDK 사용).
- 비밀값(`.env`, 토큰)은 커밋하지 않는다. `.env.example`만 갱신.
- 결정·질답이 생기면 `진행기록_질답.md`에 기록(세션이 끊겨도 이어지게).

## 기준 소스(cn) 받기 — git에 포함하지 않음(`newcontinue/.ref/`는 무시 목록)
```bash
cd newcontinue && mkdir -p .ref && cd .ref
git clone --filter=blob:none --no-checkout https://github.com/continuedev/continue.git && cd continue && git checkout 5522c6f
# 빌드(차등 시험용, Node 18+): cd extensions/cli && npm run build:local-deps && npm run build && node dist/cn.js --version
```

## 테스트
```bash
uv run pytest -q                      # 서버 쪽. Postgres·Redis가 없으면 통합 테스트는 자동 skip(TEST_DATABASE_URL·TEST_REDIS_URL로 주소 변경)
cd newcontinue && uv run pytest -q    # ncn 쪽(외부 서비스 필요 없음)
```

## 환경별로 할 수 있는 것
| 작업 | 개발 PC(macOS) | Claude Code on the web | 현장 PC(Windows + VS 2022, 사내망) |
|---|---|---|---|
| 코드 수정·단위 테스트 | ○ | ○ | ○ |
| docker 통합 테스트 | ○ | △(서비스 없으면 skip) | 환경에 따라 |
| 실제 LLM(gemma4)·사내 서버 연결 | ✗ | ✗ | **○ — 실제 시험은 여기서** |
| VSIX 빌드(VS 2022 SDK) | ✗ | ✗ | **○** |
| PowerShell·CP949·CRLF 실제 파일 | ✗ | ✗ | **○** |

## 현장 시험 결과를 고칠 때(웹 세션)
- 사용자가 현장 PC의 결과(명령, 출력, `ncn --doctor` 결과, 로그 파일)를 붙여 넣거나 `newcontinue/field_reports/`에 올린다.
- 재현할 수 있으면 먼저 실패하는 테스트(또는 `parity/scenarios/` 시나리오)를 추가하고 고친다 — 현장 PC 없이도 다시 확인 가능하게.
- 현장에서만 확인 가능한 수정이면 무엇을 확인할지 답변에 적는다.
