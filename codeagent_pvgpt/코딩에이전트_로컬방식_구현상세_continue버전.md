# 코딩 에이전트 구현 상세 — 폐쇄망 사내 배포판 (2판: Continue 기반)

> 폐쇄망에서 코딩 에이전트를 구축하는 **구현 설계서**다. 1판은 하네스(엔진)와 IntelliJ 플러그인을 직접 만들었고(단위 테스트 53개 통과), **2판은 IDE 확장 Continue를 하네스로 쓰고, 직접 만드는 것을 MCP 서비스·RAG 서비스·권한 DB로 옮긴다.** 전환 이유와 번복한 결정은 0장에, 1판 구성의 처리는 10-3에 있다.
> **2-1판(2026-10-05) 구현 반영**: 코드는 `codeagent_pvgpt/`에 있다. 2판 설계에서 바뀐 점은 0-5(D9~D13)에 모았다 — LLM 프록시(b 방식), 로컬 docker 인프라, 권한 DB Postgres, 감시·사용량, request_fix 가치 버전. 질답·진행은 `진행기록_질답.md`, 실행은 `README.md`. **4·5장의 예제 코드는 설계 당시 버전이고, 실제 코드는 각 파일이 기준이다.**
> 관련 문서: 기준 문서 `코딩에이전트_로컬방식_구현.md`(이하 **"md"**, 요청 하나의 처리 흐름은 md 부록 A), 「pgvector·Qdrant 빠른 사용법」(이하 **"빠른 사용법"**), 「LLM 단계별 학습서」(이하 **"학습서"**).

## 이 문서 읽는 법
| 표시 | 뜻 |
|---|---|
| **유지** | 1판 판단·코드를 그대로 씀 |
| **변경** | 1판과 달라짐 — 이유를 함께 적음 |
| **추가** | 2판에서 새로 생김 |
| **버림** | 구현하지 않거나 제거 — 「10. 버린 것」에 모아 정리 |
| **미확인** | Continue 등 외부 제품의 동작 중 직접 확인하지 못한 것 — 3-5의 확인 목록에 모음 |

코드의 모든 줄에 초심자 기준 주석을 단다. **2판 예제 코드는 실제로 실행해 검증했다**(9장 — 실제 HTTP로 MCP 서버를 띄워 토큰 → 사번 → 권한 필터까지 확인).

## 목차
```
0. 2판 전환 — 전제와 결정
1. 구성 — 무엇이 어디서 도나
2. 프로젝트 구조
3. Continue 정리 (외부 제품 — 별도 장)
   3-1 정체와 역할  3-2 메시지 흐름  3-3 할 수 있는 것  3-4 못 하는 것·한계
   3-5 미확인 항목과 확인 방법  3-6 도구 정책 설정안  3-7 요청 종류별 흐름
   3-8 설정 파일  3-9 토큰 관점
4. MCP 서비스 — 얇은 어댑터
5. RAG 서비스
   5-1 역할 나누기  5-2 임베딩(KoE5)  5-3 청킹  5-4 색인 작업  5-5 API
   5-6 권한  5-7 저장소(Qdrant 서버)  5-8 검색 결과는 "힌트"
6. LLM 서빙과 게이트웨이
7. 폐쇄망 배포
8. 평가
9. 테스트
10. 버린 것
11. 진행 상태
```

---

## 0. 2판 전환 — 전제와 결정

### 0-1. 전제
| # | 전제 | 성격 |
|---|---|---|
| P1 | **폐쇄망**에 구축한다 — 외부 API·외부 다운로드 없음 | 고정 요건 |
| P2 | **편집점은 개발자 PC의 로컬 소스**인데, **구조 검색은 서버 RAG**로 한다 — 둘의 최신성이 다를 수 있는 모순을 **알고 받아들인다** | 선택(단점 감수) |
| P3 | 로컬 수정·승인 화면은 **IDE의 Continue**로 해결해 **개발 비용을 줄인다** | 선택 |
| P4 | 서버 색인은 **항상 최신일 필요가 없다**(main 기준, 주기적 증분) | 선택(단점 감수) |

### 0-2. 결정 기록
| ID | 결정 | 이유 | 감수하는 것 |
|---|---|---|---|
| D1 | **Continue = 로컬 하네스** | 에이전트 루프·파일 수정·diff 승인·대화 유지·IDE 통합(VS Code·JetBrains)을 만들지 않아도 됨 | PC 안 행동을 **Continue 기능 범위로만** 통제(3-4) |
| D2 | **Qdrant는 서버에** | PC 배포물이 Continue뿐, 색인을 한 번만, 여러 저장소 검색 가능 | 로컬 미커밋 변경은 검색에 반영 안 됨(0-3) |
| D3 | **한 컬렉션 + payload `scope`로 팀 구분**, 정책은 **권한 DB**에 | 권한이 바뀌어도 점(청크)을 고치지 않음 | 권한 DB 운영·동기화(5-6) |
| D4 | **Continue는 Qdrant·RAG를 직접 부르지 않고 MCP만** | Qdrant 키 노출 방지, 권한 강제를 서버에서 | 요청이 한 단계 더 거침 |
| D5 | **MCP는 얇은 어댑터, 권한 필터는 RAG 서비스가 만든다** | RAG를 IDE 외(헬프데스크·평가)에서도 재사용, 데이터에 가까운 곳에서 강제 | 서비스가 하나 더 늘어남 |
| D6 | **RAG 서비스 = 파싱·청킹·임베딩(KoE5, 프로세스 안) + Qdrant 삽입·검색 엔드포인트** | 구조가 가장 단순 | 워커마다 모델이 메모리에 올라감(5-2) |
| D7 | **Continue용 LLM과 `request_fix` 안쪽 LLM은 같은 vLLM** | 서빙 하나로 운영 | `request_fix`의 가치는 "Continue LLM이 못 하는 일"이 있을 때만(4-3) |
| D8 | **MCP 도구는 짧고 결정적으로**, 판단은 Continue의 LLM에 | 루프 안의 루프 → 시간 제한·토큰·디버깅 부담 | 복잡한 다단계 처리는 별도 검토(4-4) |

### 0-5. 2-1판 결정(2026-10-05, 진행기록 2장)
| ID | 결정 | 2판과 달라진 점 |
|---|---|---|
| D9 | **Continue의 모델 apiBase = 코드에이전트 서버 `/v1`**(OpenAI 호환 프록시, b 방식) | D7의 "Continue → 게이트웨이 직접"을 대신함. 외부 LLM 키는 서버만, 토큰 → 사번 → 한도 → 사용량 기록을 서버에서 |
| D10 | LLM = 외부 **gemma4-26b-32k**(다른 곳의 FastAPI로 구동) | 6장 vLLM 대신. **32k 컨텍스트**가 토큰 설계 제약. OpenAI 호환 여부 확인 필요(진행기록 N1) |
| D11 | Qdrant·Redis·Postgres = **로컬 docker**, RAG·KoE5·서버·색인기 = **로컬 Python** | 개발·검증 구성. 서버 배치(D2)의 구조는 그대로 — 주소만 바뀜 |
| D12 | 권한 DB·토큰·사용량·감사 = **Postgres**, 권한 캐시·한도 카운터 = **Redis**, 감시 = `/admin` | 5-6의 SQLite·프로세스 메모리 캐시 대신 |
| D13 | request_fix = **가치 버전**: RAG(규칙·관련 코드) 참고 + SEARCH/REPLACE 블록 + 적용·문법 검사 + 재생성 | 4-3의 "가치 조건"을 채우는 구현. 효과는 8장 평가로 확인 |

### 0-3. Qdrant 로컬 vs 서버 — 트레이드오프(D2)
| 관점 | 로컬(개발자 PC) | 서버(사내망) — 선택 |
|---|---|---|
| 최신성 | 내 작업 사본 즉시(미커밋 포함) | 마지막 색인 시점의 main 기준 |
| 사람마다 다른 상태 | 개인별 색인 | 공용 색인 하나 |
| 색인 비용 | PC마다 각자, 큰 저장소면 느림 | 한 번(색인 작업), PC 부담 없음 |
| PC 구성 | 로컬 MCP + Qdrant 로컬 필요 | **Continue만** |
| 운영 | 서버 운영 없음(PC별 잠금·용량 문제) | Qdrant 서버 운영·백업·모니터링 |
| 보안 | 코드 조각이 이미 코드가 있는 PC에만 | 코드 조각이 중앙에 → **검색 권한 = 저장소 권한**이어야 함 |
| 장애 영향 | 내 PC만 | 모두의 의미 검색(로컬 파일 읽기·수정은 계속 동작) |
| 검색 범위 | 연 저장소 하나 | 여러 저장소 |
| 결과의 성격 | 지금 파일과 일치 | **힌트** — 로컬 파일로 확인해야 함(5-8) |

### 0-4. 1판에서 번복한 판단
| 1판 판단(10-2) | 2판 | 번복 이유 |
|---|---|---|
| 오픈소스(Continue 등) 도입 — 버림("PC 안 행동을 코드로 강제할 수 없음, 학습 목적과 맞지 않음") | **채택**(D1) | 목표가 학습에서 **사내 배포 비용 절감**으로 옮겨 감. 코드로 강제하던 규칙(읽기 금지·백업·감사)은 Continue 기능 범위로 낮추는 것을 **감수**(3-4, 10-3) |
| 하네스를 서버에 두는 구조 — 버림 | 하네스는 여전히 **PC**(Continue) | 판단 유지. 서버에는 하네스가 아니라 **도구(MCP)와 데이터(RAG)**를 둔다 |
| 코드 색인은 Qdrant 로컬 | **서버**(D2) | Continue가 PC 쪽을 맡으면서 PC 배포물을 최소화. 최신성은 P4로 포기 |

---

## 1. 구성 — 무엇이 어디서 도나
```
[개발자 PC]
  IDE + Continue (= 로컬 하네스: 에이전트 루프 · 파일 읽기·수정 · diff 승인 · 대화 기록)
     │ ① 대화·판단                  │ ② 도구 호출(개인 토큰)
     ▼                              ▼
[사내망]
  LLM 게이트웨이 ──▶ vLLM        MCP 서비스(얇은 어댑터) ──③ 수정안 생성──▶ (같은 vLLM, 게이트웨이 경유)
   (개인 키·한도)  (채팅 모델)      │ search_code · search_docs · request_fix
                                   │ ④ 검색(서비스 토큰 + 사번)
                                   ▼
                              RAG 서비스 (Python: 파싱·청킹·KoE5 임베딩·권한 필터·결과 정리)
                                   ├─ 권한 DB (사람 → 팀 → 항목)  ← Git 서버 그룹 동기화
                                   └─ Qdrant 서버 (code · docs 컬렉션)
                              색인 작업(main 푸시·주기) ──⑤ /index──▶ RAG 서비스
                                   ↑ Git 서버(main 사본)
```
| 구성 요소 | 위치 | 직접 만드나 | 장 |
|---|---|---|---|
> **2-1판 구성(실제 구현)**: Continue → 코드에이전트 서버(:8200, `/v1` 프록시 · `/mcp` 도구 · `/admin` 감시) → 외부 gemma4 / RAG 서비스(:8100) → Qdrant. 그림은 `진행기록_질답.md` 3-2.

| Continue | PC | 아니오(설정만) | 3 |
| LLM 게이트웨이 + vLLM | 사내망 | 설정·배포 | 6 |
| MCP 서비스 | 사내망 | **예** | 4 |
| RAG 서비스 + 권한 DB + 색인 작업 | 사내망 | **예** | 5 |
| Qdrant 서버 | 사내망 | 설치·운영 | 5-7 |

---

## 2. 프로젝트 구조
```
codeagent_pvgpt/                    # [변경] 새 경로(1판 codeagent/와 분리, legacy_engine 없음 — 1판은 ../../codeagent/에 그대로)
├── 코딩에이전트_로컬방식_구현상세_continue버전.md   # 이 문서
├── 진행기록_질답.md                   # 질답·결정·진행 상태(재개용)
├── README.md                          # 실행 순서
├── pyproject.toml · .env.example      # uv 프로젝트(파이썬 3.13), 설정 예시
├── continue/config.yaml               # 3-8 Continue 설정 — apiBase·MCP 모두 :8200 [변경 D9]
├── common/                            # settings(환경변수) · db(DB 생성·스키마·토큰 해시) [추가]
├── db/schema.sql                      # 사용자·토큰·권한·사용량·도구 호출·색인 기록 [추가 D12]
├── agent_server/                      # 코드에이전트 서버 — 4장 mcp_server를 대체·확장 [변경]
│   ├── app.py                         # 조립(/v1 + /mcp + /admin)
│   ├── proxy.py                       # LLM 프록시 [추가 D9]
│   ├── mcp_tools.py · fixer.py        # MCP 도구 3개 · request_fix 가치 버전 [D13]
│   ├── auth.py · usage.py             # 토큰→사번 · 한도·사용량 기록
│   ├── admin.py                       # 감시 화면 [추가]
│   └── manage.py                      # 사용자·토큰·팀·항목 관리 CLI [추가]
├── rag_service/                       # 5장 — embedder · chunking · store · permissions(Postgres) · app · indexer(git 주소)
├── tools/fake_llm.py                  # 외부 LLM 연결 전 시험용 가짜 LLM [추가]
└── tests/test_all.py                  # 9장 — 9개
```

---

## 3. Continue 정리 (외부 제품 — 별도 장)
> 이 장은 **우리가 만들지 않는 외부 제품**의 동작을 정리한다. 확인 근거는 Continue 공식 문서와 이슈 기록이며, 버전마다 바뀔 수 있다. 직접 확인하지 못한 것은 **미확인**으로 표시하고 3-5에 모았다.

### 3-1. 정체와 역할
Continue는 VS Code·JetBrains용 오픈소스 AI 코딩 확장이다. **Agent 모드에서 에이전트 루프를 IDE 안에서 돌리는 하네스**다(md 1장·학습서 09-16의 "하네스"에 해당).

| 주체 | 하는 일 | 하지 않는 일 |
|---|---|---|
| 사용자 | 요청 입력, 도구 실행 허락 | — |
| **Continue**(하네스) | 대화를 LLM에 보냄, LLM이 요청한 도구를 실행하거나 MCP에 맡김, 결과를 LLM에 돌려줌, 대화 기록 보관 | 다음에 무엇을 할지 **판단하지 않음** |
| **Continue의 LLM**(게이트웨이 → vLLM) | 대화와 도구 목록을 보고 "이 도구를 이 인자로 불러 달라"는 **요청서**를 씀(tool calling) | 직접 실행하지 않음, MCP와 직접 통신하지 않음 |
| **MCP 서비스** | 도구 목록을 알려 주고, 받은 도구 하나를 실행해 결과를 돌려줌 | 대화를 보지 않음, 다음 도구를 정하지 않음 |

### 3-2. 메시지 흐름
```
[Continue가 켜질 때 — 사용자가 부르지 않아도 자동]
Continue → MCP     "무슨 도구 있어?"(tools/list)       ← 메뉴판 받기. 실행이 아님
MCP → Continue     search_code, search_docs, request_fix

[요청 하나]
1. 사용자 → Continue      요청
2. Continue → LLM         규칙 + 대화 + 도구 목록(내장 + MCP, Excluded 제외)
3. LLM → Continue         도구 요청서(tool calling)
4. Continue               도구 정책 확인 → Ask First면 사용자에게 허락 받기
5. Continue → MCP         "이 도구를 이 인자로 실행해"(tools/call) — 내장 도구면 PC에서 직접 실행
6. Continue → LLM         대화 + 도구 결과(오류도 결과로)
7. LLM → Continue         다음 요청서 또는 도구 없는 답변(= 턴 종료)
```
- **LLM과 MCP는 서로 직접 통신하지 않는다.** 둘 사이를 Continue가 중계한다.
- **LLM이 두 군데** 있을 수 있다: Continue의 LLM(어떤 도구를 부를지 결정)과 MCP 안쪽 LLM(`request_fix`의 수정안 생성). 같은 vLLM이어도 역할은 별개이고, 서로의 존재를 모른다.
- **도구 결과는 언제나 Continue의 LLM으로 돌아간다.** MCP가 수정 코드를 돌려줘도 파일은 바뀌지 않고, LLM이 내장 편집 도구를 다시 불러야 반영된다.

### 3-3. 할 수 있는 것
| 기능 | 내용 |
|---|---|
| 모드 | Chat(도구 없음) / Plan(읽기 전용 도구) / Agent(모든 도구) |
| 내장 도구 | 파일 읽기(`read_file`), 새 파일(`create_new_file`), 파일 수정(`edit_existing_file`·`single_find_and_replace`), 터미널 명령(`run_terminal_command`, 작업 폴더 루트), 파일 이름 검색(`file_glob_search`), git diff(`view_diff`), 열린 파일 읽기, 규칙 파일 만들기 등 |
| 사용자 확인 | 기본은 도구마다 허락 요청(Continue / Cancel) |
| 도구 정책 | 도구별 **Ask First**(기본) / **Automatic** / **Excluded**(모델에 보내지 않음) |
| 내장 도구 대체 | 내장 도구를 Excluded로 끄고 MCP 도구로 대신하는 실사례 있음 |
| MCP | 로컬(명령 실행, stdio)·원격(SSE, streamable-http), 원격은 요청 옵션·첫 연결 시간 제한 설정 |
| 규칙·시스템 메시지 | rules 블록, 시스템 메시지 변경 |
| 모델 | OpenAI 호환 등 다양한 공급자. Agent 모드는 모델의 도구 호출(`tool_use`) 지원 필요, 요청 시간 제한 설정 |
| 오류 처리 | 도구 오류 대부분을 잡아 LLM에 돌려줌 |
| 오프라인·직접 RAG | 공식 문서에 인터넷 없이 실행하기, 직접 만든 코드 RAG 붙이기 안내가 있음 |
| CLI(`cn`) | 터미널용. 도구별 allow/ask/exclude, 헤드리스 모드(ask 도구 제외), 되묻기 도구 |

### 3-4. 못 하는 것·한계
| 한계 | 내용 | 이 설계에서의 영향 |
|---|---|---|
| 순서 강제 불가 | 다음 도구는 Continue의 LLM이 정한다. 작업 순서를 강제하는 장치가 없다 | 규칙(rules)으로 **유도만** 가능 |
| 정책은 도구 단위 | 인자 내용(예: CI 파일인지)을 보고 판정하지 못한다 | md 4-5의 "위험 경로만 항상 확인"을 그대로 옮길 수 없음 |
| 정책은 사용자 PC에 저장 | 관리자가 중앙에서 강제하는 구조가 아니다 | 1판 관리자 정책 파일(3-2)의 역할을 대신하지 못함 |
| 터미널 상태 유지 안 됨 | 셸이 이전 명령을 기억하지 않는다 | 명령마다 완결된 형태로 |
| 자체 코드 색인 중단 | 임베딩 색인(`@Codebase`)은 지원 중단 | 코드 색인은 우리 RAG(5장) |
| MCP 호출 시간 제한 | 기본 시간 제한이 있고 사용자 조절 옵션이 추가됨(기본값 **미확인**) | `request_fix`처럼 오래 걸리는 도구는 실패 가능 |
| 모델 호환성 | 모델에 따라 Agent 모드가 제대로 동작하지 않은 사례 | 사내 모델로 반드시 사전 시험 |
| 중앙 감사 기록 | 누가 무엇을 바꿨는지 중앙에 남기는 기능 **미확인** | 1판 감사 기록(3-12)을 대신하지 못함 — 게이트웨이·MCP·RAG 기록으로 일부 보완 |
| 도구 목록 최적화 | 필요한 도구만 그때그때 보여 주는 방식은 확인 못 함 | 매 호출에 도구 목록 전체 전송(3-9) |

### 3-5. 미확인 항목과 확인 방법
| # | 미확인 항목 | 왜 중요한가 | 확인 방법 |
|---|---|---|---|
| U1 | MCP 도구 호출 기본 시간 제한, 설정 키 | `request_fix`가 수십 초 걸리면 실패 | 30·60·120초를 일부러 기다리는 MCP 도구로 실험 |
| U2 | 승인 화면에서 Cancel 했을 때: 거부를 결과로 LLM에 돌려주고 계속하나, 턴을 멈추나 | 거부 이유 전달 방식이 달라짐(3-7 경우 3-2) | 편집 도구 승인 화면에서 Cancel 후 동작 관찰 |
| U3 | 반복 횟수 상한·같은 도구 반복 방지 | 작은 모델의 폭주 | 일부러 실패하는 도구를 두고 관찰 |
| U4 | MCP elicitation·sampling·진행 알림 지원 | 서버가 도중에 사용자에게 묻기 | 지원 여부 문서·실험 |
| U5 | 내장 정확 검색(grep) 도구 유무 | 없으면 정확 검색은 터미널 명령(`rg`) → 승인 필요 | 도구 목록 확인 |
| U6 | 긴 대화를 줄이는 방식 | 멀티턴 품질 | 긴 세션에서 관찰 |
| U7 | 사용 통계 등 외부 전송 끄기 설정 위치 | 폐쇄망 필수 | 오프라인 실행 문서 + 네트워크 감시 |

### 3-6. 도구 정책 설정안
도구 정책의 기본값은 **모든 도구 Ask First**다. 아래처럼 바꾼다.
| 도구 | 종류 | 정책 | 이유 |
|---|---|---|---|
| `read_file`, `file_glob_search`, 열린 파일 읽기 | 내장 | Automatic | 읽기만 함 |
| `edit_existing_file`, `single_find_and_replace` | 내장 | **Ask First** | 실제 파일이 바뀜 — diff 확인 |
| `run_terminal_command` | 내장 | **Ask First**(또는 Excluded) | PC에서 명령 실행 |
| `create_new_file` | 내장 | Ask First | 새 파일 생성 |
| 그 밖의 내장 도구 | 내장 | Excluded | 메뉴판에서 빼기(토큰 절약) |
| `search_code`, `search_docs` | MCP | Automatic | 검색만, 파일 안 바뀜 |
| `request_fix` | MCP | Automatic | 제안 코드만 돌려줌, 파일 안 바뀜 |
정책이 사용자 PC에 저장되므로(3-4), 배포 시 기본 설정을 함께 나눠 주고 **사용자가 바꿀 수 있다는 점을 전제**로 서버 쪽 방어(권한 필터·서비스 토큰)를 둔다.

### 3-7. 요청 종류별 흐름
흐름을 정하는 장치는 하나뿐이다 — "LLM이 도구 선택 → Continue가 정책대로 묻거나 실행 → 결과를 LLM에". 요청 종류에 따라 바뀌는 것은 **LLM이 고르는 도구**와 **도구 정책이 정하는 승인 지점**이다.

**경우 1. 조회 — "쿠폰 할인은 어디서 계산해?"**
| # | 주체 | 하는 일 | Continue의 판정 |
|---|---|---|---|
| 1 | 사용자 | 요청 | — |
| 2 | Continue | LLM 호출(대화 + 도구 목록) | — |
| 3 | LLM | `search_code` 요청서 | — |
| 4 | Continue → MCP → RAG | 실행 → 경로·함수명·커밋 | Automatic |
| 5 | LLM | (필요하면) `read_file`로 로컬 확인 | Automatic |
| 6 | LLM | 도구 없이 답변 → 턴 종료 | — |

**경우 2. 수정 — "쿠폰 버그 고쳐줘"**
| # | 주체 | 하는 일 | Continue의 판정 |
|---|---|---|---|
| 1~4 | | 경우 1과 같이 `search_code` | Automatic |
| 5 | LLM → Continue | `read_file`(로컬) | Automatic |
| 6 | LLM → Continue → MCP | `request_fix(코드, 요청)` → MCP 안쪽 LLM이 제안 코드 | Automatic(인자에 코드 전체) |
| 7 | LLM → Continue | `edit_existing_file` 요청서 | **Ask First** → diff 표시, 대기 |
| 8 | 사용자 | 승인 → 파일 수정 | — |
| 9 | LLM → Continue | (켜 뒀다면) 테스트 명령 | **Ask First** |
| 10 | LLM | 실패면 5·7로, 통과면 요약 → 턴 종료 | 반복 상한 **미확인(U3)** |
LLM 호출은 6~7번이고 매번 대화 전체가 간다. 같은 코드가 읽기 결과 → `request_fix` 인자 → 제안 → 편집 인자로 네 번 실린다(3-9).

**경우 3. 사람이 필요한 멀티턴**
| 상황 | 흐름 | 비고 |
|---|---|---|
| 3-1 정보 부족("결제 에러 고쳐줘") | LLM이 바로 되묻거나, 검색해 본 뒤 되묻고 턴 종료 → 사용자가 답하면 Continue가 **이전 대화 전체**를 붙여 다음 턴 진행 | 어느 쪽일지는 LLM이 정함 — 규칙으로 유도(3-8) |
| 3-2 수정 거부(Cancel) | 거부가 결과로 LLM에 돌아가 계속되거나, 턴이 멈추고 사용자의 다음 메시지를 기다림(**미확인 U2**). 이유는 승인 화면이 아니라 **다음 메시지로** 전달 | md 하네스의 "거부 이유를 도구 결과로" 방식이 그대로 된다고 가정하지 않는다 |
| 3-3 이어서("테스트도 추가해줘") | 앞 턴의 대화(위치·코드·수정·결과)를 그대로 붙여 진행 | **주의**: 앞 턴에서 고친 내용은 로컬에만 있고 서버 색인에는 없다 → `search_code`가 고치기 전 내용을 돌려줄 수 있음(5-8) |
| 3-4 MCP·RAG 실패 | 오류가 결과로 LLM에 돌아가고, 재시도·로컬 검색 대체·사용자 안내 중 LLM이 선택 | 재시도 상한 **미확인** |
멀티턴 기억은 **Continue**가, 도구 안쪽 처리는 **MCP**가 맡는다. MCP는 요청마다 받은 인자만 본다.

### 3-8. 설정 파일
```yaml
# ~/.continue/config.yaml — 폐쇄망 배포용 예시(키 이름·구조는 Continue 버전별 문서로 확인)
name: 사내 코딩 에이전트
version: 1.0.0
schema: v1

models:
  - name: 사내 코더
    provider: openai                      # OpenAI 호환 주소면 provider는 openai
    model: coder                          # 게이트웨이에 등록한 모델 이름(vLLM --served-model-name)
    apiBase: https://llm.사내도메인/v1     # LLM 게이트웨이(개인 키·한도·기록)
    apiKey: ${{ secrets.LLM_KEY }}        # 개인 키 — 파일에 평문으로 적지 않는다(비밀값 기능 사용)
    roles: [chat, edit, apply]
    capabilities: [tool_use]              # Agent 모드에 필요 — 자동 감지가 안 되는 사내 모델은 직접 적는다
    requestOptions:
      timeout: 180                        # 모델 요청 시간 제한(초)

mcpServers:
  - name: code-assist
    type: streamable-http                 # 원격 MCP(사내망)
    url: https://mcp.사내도메인/mcp
    requestOptions:
      headers:
        Authorization: Bearer ${{ secrets.MCP_TOKEN }}   # ★ 이 토큰으로 MCP가 사번을 확인 → 권한 필터
      timeout: 120                        # MCP 도구 호출 시간 제한 — request_fix가 이보다 오래 걸리면 실패

rules:
  - 코드 수정 전에는 반드시 로컬 파일을 읽어 현재 내용을 확인한다.
  - search_code 결과는 서버 색인(main 기준) 힌트다. 로컬 파일과 다르면 로컬을 따른다.
  - 범위가 넓은 요청(예 "결제 에러 고쳐줘")은 먼저 오류 메시지나 재현 방법을 묻는다.
  - 비밀번호·토큰·.env 내용은 읽거나 출력하지 않는다.

# 텔레메트리(사용 통계 전송)는 끈다 — 설정 위치는 Continue 문서의 오프라인 실행 안내를 따른다(3-8)
```
- 키·토큰은 Continue의 비밀값 기능으로 넣고 파일에 평문으로 적지 않는다.
- `mcpServers`의 `Authorization` 헤더가 MCP 서비스의 신원 확인(4장) → RAG 권한 필터(5-6)로 이어진다.
- 설정 키 이름·구조는 Continue 버전마다 다를 수 있으므로 도입 시 공식 문서(config.yaml Reference)로 확인한다.

### 3-9. 토큰 관점
| 항목 | Continue에서 |
|---|---|
| 매 LLM 호출에 도구 목록 전체 + 대화 전체 전송 | 그렇다 — 학습서의 점진적 공개 같은 최적화는 기대하지 않는다 |
| 쓰지 않는 도구 빼기 | 가능(Excluded) |
| MCP 도구 수·설명 길이 | 우리 몫 — 도구 3개, 설명 짧게(학습서 09-10) |
| 반복되는 앞부분의 계산 | vLLM 프리픽스 캐싱으로 절감(학습서 09-5, 14-4) |
| 컨텍스트 창 차지 | 줄일 수 없음 — 캐싱은 계산만 아낀다 |

---

## 4. MCP 서비스 — 얇은 어댑터

### 4-1. 역할과 원칙
| 원칙 | 내용 | 이유 |
|---|---|---|
| 얇게 | 신원 확인, RAG·LLM 호출, 결과를 LLM이 읽기 좋은 글로 바꾸기만 | 검색 로직·권한은 RAG 서비스(D5) |
| 신원은 헤더에서만 | `Authorization` 헤더의 개인 토큰 → 사번. 모델이 넘긴 인자로 신원을 정하지 않음 | 학습서 12-1 |
| 필터를 만들지 않음 | RAG에 **사번만** 넘기고, 허용 항목·필터는 RAG가 만든다 | 어댑터가 뚫려도 필터를 조작할 수 없게 |
| 짧고 결정적으로 | 도구 하나 = 요청 한 번. 내부 다단계 판단은 기본으로 넣지 않음 | 시간 제한(U1)·토큰·디버깅(D8) |
| 도구는 세 개 | `search_code`, `search_docs`, `request_fix` | 매 호출 토큰(3-9), 도구 선택 정확도 |
| 상태 없음 | 요청마다 독립 처리(`stateless_http`) | 여러 프로세스로 늘리기 쉬움. 멀티턴 기억은 Continue 몫 |

### 4-2. 코드
```python
# mcp_server/server.py — 얇은 MCP 어댑터: 신원 확인 → RAG·LLM 호출 → LLM이 읽기 좋은 글로 반환 (4장)
# MCP 파이썬 SDK 2.x 기준(MCPServer). 1.x에서는 같은 클래스가 FastMCP라는 이름이었다.
import os
import httpx
from mcp.server.mcpserver import MCPServer, Context

RAG_URL = os.environ.get("RAG_URL", "http://rag:8100")
SEARCH_TOKEN = os.environ.get("SEARCH_TOKEN", "")            # RAG가 이 어댑터를 알아보는 서비스 토큰
LLM_URL = os.environ.get("LLM_URL", "http://gateway:4000/v1")  # Continue와 같은 vLLM(게이트웨이 경유)
LLM_MODEL = os.environ.get("LLM_MODEL", "coder")
LLM_KEY = os.environ.get("LLM_KEY", "")
MAX_CODE_CHARS = 30_000                                      # request_fix로 받을 코드 상한(컨텍스트·시간 보호)

mcp = MCPServer(
    name="code-assist",
    instructions="사내 코드·문서 검색과 수정안 생성 도구. 검색 결과는 서버 색인(main 기준) 힌트이므로 "
                 "수정 전 반드시 로컬 파일을 읽어 확인한다.",   # Continue가 LLM에게 서버 설명으로 전달할 수 있는 글
)


def verify_token(token: str) -> str | None:
    """개인 토큰 → 사번. 예제는 환경변수 표(USER_TOKENS="tok-a:emp-001,tok-b:emp-002").
    운영에서는 게이트웨이(LiteLLM)의 키 조회 API나 사내 인증 서버로 확인한다."""
    table = dict(pair.split(":", 1) for pair in os.environ.get("USER_TOKENS", "").split(",") if ":" in pair)
    return table.get(token)


def current_user(ctx: Context) -> str | None:
    """★ 신원은 HTTP 요청의 Authorization 헤더에서만 꺼낸다 — 모델이 넘긴 인자는 믿지 않는다(학습서 12-1)."""
    request = getattr(ctx.request_context, "request", None)     # streamable-http로 들어온 원래 HTTP 요청
    if request is None:
        return None
    auth = request.headers.get("authorization", "")
    return verify_token(auth.removeprefix("Bearer ").strip())


async def post_rag(path: str, body: dict, user: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{RAG_URL}{path}", json=body,
                              headers={"X-Service-Token": SEARCH_TOKEN, "X-User-Id": user})
        r.raise_for_status()
        return r.json()


def format_hits(data: dict) -> str:
    if data.get("denied"):
        return "검색할 수 있는 항목이 없습니다(권한 없음). 로컬 파일 검색을 쓰세요."
    if not data["results"]:
        return "관련 결과가 없습니다. 이름을 알면 로컬 파일 검색을 쓰세요."
    lines = []
    for h in data["results"]:
        sym = f" · {h['symbol']}" if h["symbol"] else ""
        lines.append(f"{h['repo']}/{h['path']}{sym} ({h['start']}-{h['end']}줄) [main@{h['commit']}]\n    "
                     + h["preview"].replace("\n", "\n    "))
    return "\n".join(lines) + "\n\n위치는 서버 색인 기준 힌트입니다. 수정 전 read_file로 로컬 파일을 확인하세요."


@mcp.tool()
async def search_code(query: str, ctx: Context, k: int = 8) -> str:
    """이름을 모를 때 자연어로 사내 코드 위치를 찾는다(의미 검색). 결과는 경로·함수명·커밋.
    정확한 이름을 알면 이 도구 대신 로컬 검색을 쓴다."""
    user = current_user(ctx)
    if user is None:
        return "인증 정보가 없습니다. Continue 설정의 MCP 토큰을 확인하세요."
    return format_hits(await post_rag("/search", {"collection": "code", "query": query, "k": k}, user))


@mcp.tool()
async def search_docs(query: str, ctx: Context, k: int = 5) -> str:
    """사내 개발 가이드·설계 문서를 검색한다. 코드 규칙·절차를 확인할 때 쓴다."""
    user = current_user(ctx)
    if user is None:
        return "인증 정보가 없습니다."
    return format_hits(await post_rag("/search", {"collection": "docs", "query": query, "k": k}, user))


@mcp.tool()
async def request_fix(path: str, code: str, instruction: str, ctx: Context) -> str:
    """로컬에서 읽은 파일 내용(code)과 요청(instruction)으로 수정된 전체 코드를 '제안'한다.
    파일은 바뀌지 않는다 — 적용은 편집 도구로 하고, 사용자가 diff를 확인한다."""
    user = current_user(ctx)
    if user is None:
        return "인증 정보가 없습니다."
    if len(code) > MAX_CODE_CHARS:
        return f"코드가 너무 깁니다({len(code)}자). 수정할 함수 부분만 보내세요."
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{LLM_URL}/chat/completions", headers={"Authorization": f"Bearer {LLM_KEY}"}, json={
            "model": LLM_MODEL, "temperature": 0.2, "user": user,          # user = 게이트웨이 사용량 기록용
            "messages": [
                {"role": "system", "content": "당신은 코드 수정기다. 요청을 반영한 '전체 코드'만 출력한다. "
                                              "설명·마크다운 금지. 요청 밖의 부분은 바꾸지 않는다."},
                {"role": "user", "content": f"파일: {path}\n요청: {instruction}\n\n<code>\n{code}\n</code>"}]})
        r.raise_for_status()
    fixed = r.json()["choices"][0]["message"]["content"].strip()
    return f"제안 코드({path}) — 아직 적용되지 않음:\n<proposed>\n{fixed}\n</proposed>\n편집 도구로 적용하세요."


def create_app():
    return mcp.streamable_http_app(stateless_http=True)     # 요청마다 독립 처리 → 여러 프로세스로 늘리기 쉬움

# 실행: uvicorn mcp_server.server:create_app --factory --host 0.0.0.0 --port 8200
```
- **SDK 버전**: MCP 파이썬 SDK 2.x에서 `FastMCP`가 `MCPServer`로 이름이 바뀌었다(이 코드는 2.x 기준). 1.x로 고정하면 `from mcp.server.fastmcp import FastMCP`로 같은 구조를 쓴다.
- **신원 확인 경로를 이렇게 정한 이유**: HTTP 미들웨어에서 사번을 꺼내 컨텍스트 변수에 담는 방식도 있지만, MCP 서버는 요청을 받은 뒤 별도 작업(task)에서 도구를 실행할 수 있어 값이 전달되지 않을 위험이 있다. 도구 안에서 `ctx.request_context.request`로 원래 HTTP 요청의 헤더를 직접 읽는 방식을 **실제 HTTP 호출로 확인**했다(9장).
- **운영에서의 토큰 확인**: 예제의 환경변수 표 대신 게이트웨이의 키 조회 API나 사내 인증 서버로 확인한다. SDK의 인증 기능(`token_verifier`)을 쓰는 방법도 있다.

### 4-3. `request_fix`의 정의와 한계
- **정의**: "로컬에서 읽은 코드 + 요청 → 수정된 전체 코드 **제안**". 원격 MCP는 PC 파일에 닿지 못하므로 **파일은 바뀌지 않고**, Continue의 LLM이 내장 편집 도구로 적용하며 사용자가 diff를 승인한다(3-2).
- **입력에 코드가 실린다**: 같은 코드가 여러 번 오가 토큰·시간이 는다. 그래서 상한(`MAX_CODE_CHARS`)을 두고, 넘으면 "함수 부분만 보내라"고 돌려준다.
- **가치 조건(D7)**: Continue의 LLM과 같은 모델이면 Continue의 LLM도 수정 코드를 직접 쓸 수 있다. `request_fix`가 의미를 가지려면 **Continue의 LLM이 못 하는 일**을 해야 한다 — 서버 RAG로 관련 코드·규칙을 더 붙이기, 사내 코딩 규칙 적용, 형식·문법 검증(4-4) 등. 이 조건을 못 채우면 도구를 빼는 것이 토큰상 이득이다.

### 4-4. LangGraph가 필요해지는 경우
이 구조에서 에이전트 루프는 Continue가 돌리므로 **LangGraph는 기본적으로 필요 없다**. 도구 **안쪽**에서 "판단하고 다시 시도"하는 여러 단계가 생길 때만 검토한다.
| 경우 | 예 |
|---|---|
| `request_fix` 검증 루프 | 수정안 생성 → 문법·정적 검사 → 실패하면 오류를 붙여 다시 생성(최대 N회) |
| 다단계 검색 | 검색 → 충분한지 판단 → 질문을 바꿔 재검색 |
| 서버 조사 에이전트 | `investigate(질문)` — 서버 데이터를 여러 단계로 조사해 결론만 반환 |
루프 안의 루프가 되므로 시간 제한(U1)을 먼저 확인한다. 단계가 두세 개뿐이면 일반 반복문이 더 단순하다. 검증 루프 버전의 설계는 **검토 중**이다.

---

## 5. RAG 서비스

### 5-1. 역할 나누기
```
[색인]  (Python)  파일 → 파싱 → 청킹 → "passage: " + KoE5 임베딩
        (Qdrant)  벡터 + payload(scope·repo·path·symbol·commit·text) 저장
[검색]  (Python)  사번 → 허용 항목 → "query: " + KoE5 임베딩 → 권한 필터
        (Qdrant)  유사도 계산 + 필터 적용 → 후보
        (Python)  (선택) 재순위화 → 결과 정리 → MCP로
```
| 일 | 어디서 |
|---|---|
| 파싱·청킹, 문서·**질문** 임베딩, 권한 필터 만들기, 재순위화, 결과 정리 | Python(RAG 서비스) |
| 저장, 유사도 검색, 필터 적용 | Qdrant 서버 |
Qdrant는 글을 벡터로 바꾸지 않는다 — 질문도 Python에서 **문서와 같은 모델로** 임베딩해 보낸다.

### 5-2. 임베딩 — KoE5
```python
# rag_service/embedder.py — KoE5 임베딩. 문서는 "passage: ", 질문은 "query: "를 붙인다(E5 계열 규칙)
import os


class KoE5Embedder:
    """sentence_transformers로 KoE5를 RAG 서비스 프로세스 안에서 돌린다."""

    def __init__(self, model_name: str | None = None, device: str | None = None):
        from sentence_transformers import SentenceTransformer   # 무거운 패키지라 실제로 만들 때만 불러온다
        name = model_name or os.environ.get("EMBED_MODEL", "nlpai-lab/KoE5")
        # 폐쇄망: name에 미리 반입한 모델 폴더 경로를 넣고 HF_HUB_OFFLINE=1 로 인터넷 접근을 막는다(7장)
        self.model = SentenceTransformer(name, device=device)   # device=None이면 GPU가 있으면 GPU
        self.dim = self.model.get_sentence_embedding_dimension()   # 컬렉션 size에 그대로 쓴다(모델마다 다름)
        self.max_tokens = self.model.max_seq_length                # 이보다 긴 입력은 잘려서 벡터에 반영되지 않는다

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """색인할 청크들 → 벡터들(같은 순서). 정규화해서 코사인 비교에 맞춘다."""
        vecs = self.model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True, batch_size=32)
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        """검색 질문 → 벡터. 문서와 같은 모델이어야 같은 좌표계에서 비교된다."""
        return self.model.encode([f"query: {text}"], normalize_embeddings=True)[0].tolist()

    def count_tokens(self, text: str) -> int:
        """청크 길이를 이 모델의 토크나이저로 잰다(글자 수가 아니라 토큰 수가 기준, 학습서 06-1)."""
        return len(self.model.tokenizer.encode(text, add_special_tokens=False))
```
| 챙길 것 | 내용 |
|---|---|
| 접두어 | 문서 `passage: `, 질문 `query: ` — 빠뜨리면 품질 저하(학습서 06-2). 모델 카드로 확인 |
| 정규화 + 코사인 | `normalize_embeddings=True`, 컬렉션 COSINE |
| 입력 길이 | 보통 512토큰 — 넘는 부분은 잘림 → 청킹에서 토큰으로 재서 맞춤(5-3) |
| 차원 | `get_sentence_embedding_dimension()`으로 확인해 컬렉션 `size`에 |
| 코드에 쓸 때 | KoE5는 한국어 일반 문장 모델이라 코드 의미에는 코드 전용 모델보다 약할 수 있다 → 청크에 경로·함수명·주석 단서를 붙이고(5-3), 골든셋으로 코드 전용 모델과 비교(8장) |
| 프로세스 안 실행 | 구조는 단순하지만 워커마다 모델이 메모리에 올라감 → 워커 1~2개로 시작, 부하가 크면 임베딩만 따로 서빙 |

### 5-3. 청킹
```python
# rag_service/chunking.py — 파일을 검색 단위(청크)로 자른다
import ast

WINDOW, OVERLAP = 60, 10          # 줄 묶음 청킹: 60줄씩, 10줄 겹침(학습서 06-1)
MAX_FUNC_LINES = 150              # 이보다 긴 함수·클래스는 줄 묶음으로 다시 자른다


def chunk_lines(lines: list[str], offset: int = 0) -> list[dict]:
    """줄 목록 → [{start, end, symbol, body}] (줄 번호는 1부터)."""
    out, step = [], WINDOW - OVERLAP
    for i in range(0, max(len(lines), 1), step):
        seg = lines[i:i + WINDOW]
        if any(line.strip() for line in seg):              # 빈 줄뿐인 조각은 버림
            out.append({"start": offset + i + 1, "end": offset + i + len(seg), "symbol": "", "body": "\n".join(seg)})
        if i + WINDOW >= len(lines):
            break
    return out


def chunk_python(text: str) -> list[dict]:
    """파이썬은 함수·클래스 경계로 자르고 이름(symbol)을 남긴다 — 검색 결과에 함수명을 돌려주기 위해."""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)                             # 코드를 문법 구조로 해석
    except SyntaxError:
        return chunk_lines(lines)                          # 문법 오류 파일은 줄 묶음으로
    out, covered = [], set()
    for node in tree.body:                                 # 파일 최상위의 함수·클래스만
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])   # 데코레이터 줄 포함
            end = node.end_lineno
            if end - start + 1 > MAX_FUNC_LINES:
                parts = chunk_lines(lines[start - 1:end], offset=start - 1)
                for p in parts:
                    p["symbol"] = node.name                # 잘게 나눠도 어느 함수의 일부인지 남긴다
                out += parts
            else:
                out.append({"start": start, "end": end, "symbol": node.name, "body": "\n".join(lines[start - 1:end])})
            covered.update(range(start, end + 1))
    run: list[int] = []                                    # 함수 밖 코드(임포트·상수)도 연속 구간별로
    for n in range(1, len(lines) + 2):
        if n <= len(lines) and n not in covered:
            run.append(n)
        elif run:
            out += chunk_lines(lines[run[0] - 1:run[-1]], offset=run[0] - 1)
            run = []
    return sorted(out, key=lambda c: c["start"])


def chunk_markdown(text: str) -> list[dict]:
    """문서는 '## 섹션' 단위로 자르고 섹션 제목을 symbol에 남긴다(헬프데스크 예제와 같은 방식)."""
    out, title, start, buf = [], "", 1, []
    lines = text.splitlines()
    for i, line in enumerate(lines + ["## "], 1):          # 끝에 가짜 제목을 붙여 마지막 섹션도 마무리
        if line.startswith("## "):
            body_lines = [b for b in buf if b.strip() and not b.startswith("# ")]   # 문서 제목(# …)만 있는 머리말은 버림
            if body_lines:
                out.append({"start": start, "end": i - 1, "symbol": title, "body": "\n".join(buf)})
            title, start, buf = line[3:].strip(), i, []
        else:
            buf.append(line)
    return out


def chunk_file(path: str, text: str) -> list[dict]:
    if path.endswith(".py"):
        return chunk_python(text)
    if path.endswith(".md"):
        return chunk_markdown(text)
    return chunk_lines(text.splitlines())                  # 그 밖의 언어는 줄 묶음(언어별 파서는 범위 밖)


def embed_text(repo: str, path: str, chunk: dict) -> str:
    """임베딩할 글 = 위치 헤더 + 본문. 청크만 떼어 봐도 어디 코드인지 알게(학습서 06-1 자기완결성)."""
    sym = f" · {chunk['symbol']}" if chunk["symbol"] else ""
    return f"# {repo}/{path}{sym} (lines {chunk['start']}-{chunk['end']})\n{chunk['body']}"


def fit_to_tokens(chunks: list[dict], count_tokens, max_tokens: int) -> list[dict]:
    """모델 입력 한도를 넘는 청크는 반으로 나눈다 — 넘는 부분은 임베딩에서 조용히 잘려 버리기 때문."""
    out = []
    for c in chunks:
        lines = c["body"].splitlines()
        if count_tokens(c["body"]) <= max_tokens or len(lines) < 2:
            out.append(c)
            continue
        mid = len(lines) // 2
        first = {**c, "end": c["start"] + mid - 1, "body": "\n".join(lines[:mid])}
        second = {**c, "start": c["start"] + mid, "body": "\n".join(lines[mid:])}
        out += fit_to_tokens([first, second], count_tokens, max_tokens)
    return out
```
- md 4-12의 코드 청킹(파이썬 AST·줄 묶음)을 옮기고, **함수명(symbol)을 payload에 남기도록** 바꿨다 — 서버 색인은 줄 번호가 밀릴 수 있으므로 결과에 함수명을 줘서 로컬에서 다시 찾게 한다(5-8).
- 문서는 `##` 섹션 단위(헬프데스크 예제와 같음), 제목만 있는 머리말은 버린다.
- 모델 입력 한도를 넘는 청크는 반으로 나눈다(`fit_to_tokens`).

### 5-4. 색인 작업
```python
# rag_service/indexer.py — 색인 작업: 서버의 저장소 사본(main)에서 바뀐 파일만 RAG /index로 보낸다
# 실행 예: main에 푸시될 때(CI) 또는 10분마다 — python -m rag_service.indexer /srv/repos/shop-backend shop-backend
import fnmatch, json, pathlib, subprocess, sys

INDEX_EXT = {".py", ".java", ".kt", ".ts", ".js", ".go", ".sql", ".md", ".yaml", ".yml"}
DENY = [".env", ".env.*", "*.pem", "*.key", "*.p12", "id_rsa*", "*.jks"]   # 비밀 파일은 색인하지 않는다
MAX_BYTES = 200_000                                                        # 생성 코드·데이터 같은 큰 파일 제외
BATCH = 50                                                                 # 한 번에 보낼 파일 수


def git(repo_dir: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True).stdout


def wanted(path: str) -> bool:
    p = pathlib.PurePosixPath(path)
    return p.suffix in INDEX_EXT and not any(fnmatch.fnmatch(p.name, pat) for pat in DENY)


def read_text(full: pathlib.Path) -> str | None:
    if full.stat().st_size > MAX_BYTES:
        return None
    raw = full.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp949"):        # 국내 레거시 파일(CP949)도 읽는다
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None                                        # 이진 파일 등은 건너뜀


def index_repo(repo_dir: str, repo: str, post, state_file: str, collection: str = "code") -> dict:
    """post(json) = RAG /index 호출 함수. state_file에 마지막으로 색인한 커밋을 기록한다."""
    head = git(repo_dir, "rev-parse", "HEAD").strip()
    state = pathlib.Path(state_file)
    last = json.loads(state.read_text()).get(repo) if state.exists() else None
    changed, deleted = [], []
    if last is None:                                   # 처음이면 추적 중인 파일 전체
        changed = [p for p in git(repo_dir, "ls-files").splitlines() if wanted(p)]
    else:                                              # 이후에는 지난 색인 커밋과의 차이만(학습서 07-7 증분 색인)
        for line in git(repo_dir, "diff", "--name-status", "--no-renames", last, head).splitlines():
            status, path = line.split("\t", 1)
            if not wanted(path):
                continue
            (deleted if status == "D" else changed).append(path)
    sent = 0
    for i in range(0, max(len(changed), 1), BATCH):
        files = []
        for p in changed[i:i + BATCH]:
            text = read_text(pathlib.Path(repo_dir) / p)
            if text is not None:
                files.append({"path": p, "content": text})
        post({"collection": collection, "repo": repo, "commit": head[:12], "files": files,
              "deleted": deleted if i == 0 else []})
        sent += len(files)
    all_state = json.loads(state.read_text()) if state.exists() else {}
    all_state[repo] = head                             # 다 보낸 뒤에만 기록 — 중간에 실패하면 다음에 다시 보낸다
    state.write_text(json.dumps(all_state))
    return {"head": head[:12], "sent_files": sent, "deleted": len(deleted), "full": last is None}


if __name__ == "__main__":
    import os, httpx
    rag_url, token = os.environ["RAG_URL"], os.environ["INDEX_TOKEN"]
    def post(body):
        r = httpx.post(f"{rag_url}/index", json=body, headers={"X-Service-Token": token}, timeout=600)
        r.raise_for_status()
    print(index_repo(sys.argv[1], sys.argv[2], post, os.environ.get("INDEX_STATE", "index_state.json")))
```
| 결정 | 내용 |
|---|---|
| 대상 | 서버의 저장소 사본(main). 사본 갱신(`git pull`)은 색인 작업 앞에서 |
| 시점 | main 푸시 시(CI) 또는 주기(예: 10분) — P4로 즉시성은 포기 |
| 증분 | 지난 색인 커밋과의 `git diff`로 바뀐·삭제된 파일만(학습서 07-7) |
| 실패 처리 | 다 보낸 뒤에만 상태 파일에 커밋 기록 → 중간 실패 시 다음에 다시 보냄(결정적 ID라 중복 없음) |
| 제외 | 비밀 파일, 큰 파일, 대상 외 확장자. 인코딩은 UTF-8 → BOM → CP949 순(1판 3-5 유지) |
| 브랜치 | 2판은 main 하나. 브랜치별 색인은 비용이 커서 **버림**(10-4) |

### 5-5. API — `/index`, `/search`
```python
# rag_service/app.py — RAG 서비스 API: 색인(/index)과 검색(/search)을 엔드포인트로 나눈다
import os
from typing import Annotated, Literal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from rag_service.chunking import chunk_file, embed_text, fit_to_tokens
from rag_service.permissions import Permissions
from rag_service.store import Store, COLLECTIONS

Collection = Literal["code", "docs"]


class FileIn(BaseModel):
    path: str
    content: str


class IndexIn(BaseModel):                          # 색인 작업(5-4)이 보내는 요청
    collection: Collection
    repo: str
    commit: str                                    # 이 내용이 어느 커밋 기준인지 — 검색 결과에 함께 돌려준다
    files: list[FileIn] = []                       # 추가·변경된 파일
    deleted: list[str] = []                        # 삭제된 파일 경로


class SearchIn(BaseModel):                         # MCP 어댑터가 보내는 요청 — ★ 필터는 받지 않는다
    collection: Collection
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(8, ge=1, le=20)


def create_app(store: Store, embedder, perms: Permissions) -> FastAPI:
    app = FastAPI(title="RAG service")
    index_tokens = set(filter(None, os.environ.get("INDEX_TOKENS", "").split(",")))     # 색인 작업용 서비스 토큰
    search_tokens = set(filter(None, os.environ.get("SEARCH_TOKENS", "").split(",")))   # MCP 어댑터용 서비스 토큰
    for c in COLLECTIONS:
        store.ensure(c, embedder.dim)

    def check(token: str | None, allowed: set[str]):
        if not token or token not in allowed:      # 내부망이라도 아무나 부를 수 없게(5-5)
            raise HTTPException(401, "invalid service token")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/index")
    def index(body: IndexIn, x_service_token: Annotated[str | None, Header()] = None):
        check(x_service_token, index_tokens)
        for path in body.deleted:
            store.delete_file(body.collection, body.repo, path)
        stored, skipped = 0, []
        for f in body.files:
            scope = perms.scope_for(body.repo, f.path)     # ★ 항목은 RAG가 권한 DB로 정한다(보내는 쪽이 정하지 않음)
            if scope is None:
                skipped.append(f.path)                     # 어느 항목에도 속하지 않으면 색인하지 않는다(실수로 전체 공개 방지)
                continue
            chunks = fit_to_tokens(chunk_file(f.path, f.content), embedder.count_tokens, embedder.max_tokens)
            vectors = embedder.embed_passages([embed_text(body.repo, f.path, c) for c in chunks]) if chunks else []
            stored += store.replace_file(body.collection, body.repo, f.path, scope, body.commit, chunks, vectors)
        return {"stored_chunks": stored, "deleted_files": len(body.deleted), "skipped_no_scope": skipped}

    @app.post("/search")
    def search(body: SearchIn, x_service_token: Annotated[str | None, Header()] = None,
               x_user_id: Annotated[str | None, Header()] = None):
        check(x_service_token, search_tokens)
        if not x_user_id:
            raise HTTPException(400, "user required")
        scopes = perms.allowed_scopes(x_user_id)            # 사람 → 팀 → 항목(합집합)
        if not scopes:
            return {"results": [], "denied": True}          # 볼 수 있는 항목이 없으면 검색하지 않는다
        hits = store.search(body.collection, embedder.embed_query(body.query), scopes, body.k)
        return {"denied": False, "results": [
            {"repo": h.payload["repo"], "path": h.payload["path"], "symbol": h.payload["symbol"],
             "start": h.payload["start"], "end": h.payload["end"], "commit": h.payload["commit"],
             "score": round(h.score, 3), "preview": "\n".join(h.payload["text"].splitlines()[:3])}
            for h in hits]}

    return app
```
- **엔드포인트를 나눈 이유**: 색인(색인 작업이 부름)과 검색(MCP가 부름)은 호출자·빈도·권한이 다르다. **서비스 토큰도 따로**(`INDEX_TOKENS`·`SEARCH_TOKENS`).
- **검색은 필터를 받지 않는다**: 요청에는 컬렉션·질문·k만 있고 사번은 헤더로 받는다. 허용 항목과 필터는 RAG가 권한 DB로 만든다 — 내부망에서 이 API를 부를 수 있는 누구든 "전체" 필터를 넣는 사고를 막는다.
- **항목(scope)도 RAG가 정한다**: 색인 요청이 scope를 보내는 게 아니라, RAG가 경로로 권한 DB의 항목을 찾는다. 어느 항목에도 속하지 않는 파일은 **색인하지 않는다**(실수로 전체 공개되는 것 방지).

### 5-6. 권한
**방식 비교 — 2판은 B(D3)**
| | A. 점에 "볼 수 있는 팀" 저장 | **B. 점에 항목(scope)만 + 팀→항목은 권한 DB** |
|---|---|---|
| 별도 권한 DB | 팀 정보가 로그인 토큰에 있으면 불필요 | 필요 |
| 권한 변경 시 | 해당 청크 payload 대량 수정 | DB 한 줄, 캐시 시간 내 반영 |
| 맞는 경우 | 팀이 적고 거의 안 바뀜 | 팀·권한이 많거나 자주 바뀜 |
**예**: 팀A = item-1~5, 팀B = item-4~8. 김(팀A) → `scope ∈ {1..5}`, 박(팀A+B) → 합집합 `{1..8}`, 소속 없음 → **검색하지 않음**. 겹치는 4·5는 두 팀 목록에 각각 있을 뿐 따로 처리하지 않는다.
```python
# rag_service/permissions.py — "이 사람이 볼 수 있는 항목(scope)"을 답하는 권한 DB (5-6, 방식 B)
# 예제는 SQLite, 운영은 Postgres(같은 SQL). 원본은 Git 서버의 그룹·멤버 정보를 동기화해 채운다.
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS teams(team_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS team_members(team_id TEXT, user_id TEXT, PRIMARY KEY(team_id, user_id));
CREATE TABLE IF NOT EXISTS scopes(scope_id TEXT PRIMARY KEY, repo TEXT, path_prefix TEXT);  -- 항목 = 저장소 + 경로 접두어
CREATE TABLE IF NOT EXISTS team_scopes(team_id TEXT, scope_id TEXT, PRIMARY KEY(team_id, scope_id));
CREATE TABLE IF NOT EXISTS sync_log(source TEXT, synced_at TEXT, status TEXT);              -- 언제 원본과 맞췄나(감사용)
"""


class Permissions:
    def __init__(self, db_path: str, ttl_seconds: int = 300):
        self.con = sqlite3.connect(db_path, check_same_thread=False)   # 예제: 한 연결을 여러 요청이 공유
        self.con.executescript(SCHEMA)
        self.ttl = ttl_seconds                                         # 캐시 유지 시간(권한 변경이 늦게 반영되는 최대 시간)
        self._cache: dict[str, tuple[float, frozenset[str]]] = {}      # user_id → (만든 시각, 허용 scope들)

    def allowed_scopes(self, user_id: str) -> frozenset[str]:
        """사람 → 소속 팀들 → 팀들이 볼 수 있는 항목의 '합집합'. 소속이 없으면 빈 집합."""
        hit = self._cache.get(user_id)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        rows = self.con.execute(
            """SELECT DISTINCT ts.scope_id FROM team_members tm
               JOIN team_scopes ts ON ts.team_id = tm.team_id
               WHERE tm.user_id = ?""", (user_id,)).fetchall()
        scopes = frozenset(r[0] for r in rows)
        self._cache[user_id] = (time.time(), scopes)
        return scopes

    def invalidate(self, user_id: str | None = None):
        """권한이 바뀌었다는 알림(Git 서버 웹훅)을 받으면 캐시를 지운다 — None이면 전체."""
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    def scope_for(self, repo: str, path: str) -> str | None:
        """색인할 파일이 어느 항목에 속하는지 — 경로 접두어가 가장 긴 것을 고른다(예: billing/coupon/ > billing/)."""
        rows = self.con.execute("SELECT scope_id, path_prefix FROM scopes WHERE repo = ?", (repo,)).fetchall()
        best = [(len(prefix), sid) for sid, prefix in rows if path.startswith(prefix)]
        return max(best)[1] if best else None          # 어느 항목에도 속하지 않으면 None → 색인하지 않음
```
| 결정 | 내용 |
|---|---|
| 원본 | Git 서버의 그룹·멤버 — "검색 권한 = 저장소 접근 권한". 웹훅 + 주기 동기화로 DB를 채우고 `sync_log`에 기록 |
| 캐시 | 사람별 허용 항목을 TTL(예: 5분) 캐시, 권한 변경 웹훅이 오면 `invalidate` |
| 빈 권한 | **검색하지 않고 빈 결과** — 필터를 빼고 검색하면 전체 노출(가장 흔한 사고) |
| 예제 DB | SQLite(운영은 Postgres, 같은 SQL) |

### 5-7. 저장소 — Qdrant 서버
```python
# rag_service/store.py — Qdrant 서버에 저장·검색. 권한 필터는 여기서 반드시 붙는다(호출자에게서 받지 않음)
import uuid
from qdrant_client import QdrantClient, models

COLLECTIONS = ("code", "docs")    # 코드와 문서는 payload 구조·청킹이 달라 컬렉션을 나눈다


class Store:
    def __init__(self, url: str, api_key: str | None = None):
        # 운영: QdrantClient(url="http://qdrant:6333", api_key=...) — 키는 RAG 서비스만 가진다
        # 테스트: url=":memory:"
        self.client = QdrantClient(":memory:") if url == ":memory:" else QdrantClient(url=url, api_key=api_key)

    def ensure(self, collection: str, dim: int):
        """컬렉션이 없으면 만들고, 필터에 쓰는 필드에 payload 인덱스를 건다(빠른 사용법 2-1)."""
        if self.client.collection_exists(collection):
            return
        self.client.create_collection(collection, vectors_config=models.VectorParams(
            size=dim, distance=models.Distance.COSINE))
        for field in ("scope", "repo", "path"):
            self.client.create_payload_index(collection, field_name=field,
                                             field_schema=models.PayloadSchemaType.KEYWORD)

    def replace_file(self, collection: str, repo: str, path: str, scope: str, commit: str,
                     chunks: list[dict], vectors: list[list[float]]) -> int:
        """파일 하나의 옛 청크를 전부 지우고 새로 넣는다(학습서 07-7 — 함수가 늘거나 줄어도 정확)."""
        self.delete_file(collection, repo, path)
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{collection}/{repo}/{path}#{c['start']}-{c['end']}")),
                vector=v,
                payload={"repo": repo, "path": path, "scope": scope, "commit": commit,
                         "symbol": c["symbol"], "start": c["start"], "end": c["end"],
                         "text": c["body"][:4000]})             # 원문은 결과 미리보기용으로 앞부분만
            for c, v in zip(chunks, vectors)]
        if points:
            self.client.upsert(collection, points=points, wait=True)
        return len(points)

    def delete_file(self, collection: str, repo: str, path: str):
        self.client.delete(collection, points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="repo", match=models.MatchValue(value=repo)),
            models.FieldCondition(key="path", match=models.MatchValue(value=path))])), wait=True)

    def search(self, collection: str, vector: list[float], scopes: frozenset[str], k: int):
        """★ scopes가 비어 있으면 호출 자체를 하지 않는다 — 필터 없이 검색하면 전체가 노출된다."""
        if not scopes:
            return []
        flt = models.Filter(must=[models.FieldCondition(
            key="scope", match=models.MatchAny(any=sorted(scopes)))])   # 허용 항목 안에서만(검색 도중 적용)
        return self.client.query_points(collection, query=vector, query_filter=flt, limit=k,
                                        with_payload=True).points
```
**payload 구조**
| 필드 | 예 | 쓰임 |
|---|---|---|
| `scope` | `item-4` | **권한 필터**(payload 인덱스) |
| `repo`, `path` | `shop`, `coupon/apply.py` | 파일 단위 삭제·재삽입(인덱스) |
| `symbol` | `apply_coupon` | 결과 표시, 로컬에서 다시 찾기 |
| `start`, `end` | `12`, `40` | 결과 표시(서버 사본 기준) |
| `commit` | `a1b2c3d4e5f6` | 색인 시점 표시 |
| `text` | 앞 4,000자 | 미리보기 |
- 컬렉션은 `code`·`docs` 둘(payload·청킹이 다름). Qdrant 접속 키는 **RAG 서비스만** 가진다(D4).
- 백업은 스냅샷(빠른 사용법 2-5), 모델 교체는 새 컬렉션 + 별칭 전환(학습서 07-9).

### 5-8. 검색 결과는 "힌트"
P2·P4의 모순을 다루는 규칙이다.
| 규칙 | 구현 위치 |
|---|---|
| 결과에 **경로 + 함수명 + 커밋**을 준다(줄 번호는 참고) | RAG 결과 형식, MCP `format_hits` |
| "서버 색인 기준 힌트 — 수정 전 로컬 파일 확인"을 결과에 덧붙인다 | MCP `format_hits` |
| 같은 규칙을 Continue rules에도 둔다 | 3-8 |
| 앞 턴에서 고친 파일은 서버 색인에 없다 — 멀티턴에서 특히 로컬 확인 | 3-7 경우 3-3 |

---

## 6. LLM 서빙과 게이트웨이
| 항목 | 내용 |
|---|---|
| vLLM | 하나(D7): Continue의 채팅 모델 + `request_fix` 안쪽 호출. 도구 호출 파서(`--enable-auto-tool-choice`, 모델에 맞는 `--tool-call-parser`) 필수 — 없으면 Continue Agent 모드가 동작하지 않음 |
| 프리픽스 캐싱 | 매 호출 같은 앞부분(규칙·도구 목록)을 다시 계산하지 않게(3-9) |
| 게이트웨이 | 개인 키·사용량 한도·기록(md 6장). `request_fix`도 게이트웨이를 거쳐 `user` 필드로 사용자를 남긴다 |
| 임베딩 | KoE5는 RAG 서비스 프로세스 안(D6) — vLLM과 별개 |
| 기록 원칙 | 게이트웨이·MCP·RAG 로그에 **코드 원문을 남기지 않음**(사용자·시각·도구·토큰 수만) |

---

## 7. 폐쇄망 배포
| 대상 | 방식 |
|---|---|
| Continue 확장 | 인터넷망에서 VS Code용 `.vsix`·JetBrains용 플러그인 파일을 받아 반입 → 사내 확장 저장소 또는 수동 설치 |
| Continue 설정 | 3-8 `config.yaml` + 도구 정책 기본값을 배포(사용자가 바꿀 수 있음을 전제) |
| 외부 전송 차단 | 사용 통계 끄기(U7) + PC 방화벽에서 외부 차단 — 설정만 믿지 않고 네트워크로도 막음 |
| MCP·RAG 서비스 | 컨테이너 이미지 반입. 의존성은 wheel 반입 또는 사내 PyPI 미러 |
| KoE5 모델 | 모델 폴더 반입, `HF_HUB_OFFLINE=1`, `EMBED_MODEL=/models/KoE5` |
| Qdrant | 공식 이미지 반입, 볼륨·스냅샷 경로 지정 |
| 사내 CA | 서비스 간 HTTPS에 사내 인증서(`SSL_CERT_FILE`) |
| 검증 | **네트워크를 끊은 VM**에서 설치·실행 절차서(1판 6장 유지) |

---

## 8. 평가
| 대상 | 방법 |
|---|---|
| RAG 검색 품질 | 질문 + 정답 위치(파일·함수) 30~50개로 `/search`를 직접 호출해 상위 k 적중률 — MCP·Continue 없이 RAG만 평가(D5의 이점) |
| 임베딩 모델 | 같은 질문 세트로 KoE5 vs 코드 전용 모델(5-2) |
| 권한 | 팀별 사용자로 같은 질문 → 허용 밖 결과가 0건인지(회귀 테스트로 고정) |
| 전체 흐름 | Continue로 골든 작업(md 7장) — 숨긴 테스트 통과율, 도구 호출 수, 토큰 |
| `request_fix` 가치 | 같은 골든 작업을 `request_fix` 있음/없음(Excluded)으로 비교(4-3) |

---

## 9. 테스트
LLM·KoE5 없이 가짜 임베딩으로 도는 테스트 5개, **모두 통과**. 마지막 테스트는 실제 HTTP로 MCP 서비스를 띄우고 MCP 클라이언트로 호출한다.
| 테스트 | 확인 내용 |
|---|---|
| `test_chunking` | 파이썬 함수명 추출, 문서 섹션 분리(제목만 있는 머리말 제외), 토큰 한도 초과 청크 분할 |
| `test_permissions_union_and_empty` | 두 팀 소속의 합집합, 소속 없음 = 빈 집합, 경로 접두어로 항목 찾기 |
| `test_index_and_filtered_search` | 서비스 토큰 없으면 401, 항목 없는 파일은 색인 안 함, **팀A·팀B·두 팀·무소속의 결과가 정확히 다름**, 커밋 표시 |
| `test_incremental_indexer` | 첫 실행은 전체(비밀 파일 제외), 다음은 `git diff`로 삭제·추가만 |
| `test_mcp_end_to_end_over_http` | 도구 목록 3개, **토큰 → 사번 → 권한 필터**(김은 item-8을 못 보고 이는 봄), 토큰 없으면 인증 안내, 결과에 커밋·로컬 확인 안내 |
```python
# tests/test_services.py — LLM·KoE5 없이 도는 테스트(가짜 임베딩). 실제 HTTP로 MCP까지 끝까지 확인한다
import asyncio, hashlib, math, os, re, subprocess, threading, time, json

import pytest
from fastapi.testclient import TestClient

from rag_service.chunking import chunk_python, chunk_markdown, fit_to_tokens
from rag_service.permissions import Permissions
from rag_service.store import Store
from rag_service.app import create_app
from rag_service.indexer import index_repo


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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_TOKENS", "idx-secret")
    monkeypatch.setenv("SEARCH_TOKENS", "mcp-secret")
    perms = Permissions(str(tmp_path / "perm.db"))
    perms.con.executescript("""
      INSERT INTO teams VALUES ('A','팀A'),('B','팀B');
      INSERT INTO team_members VALUES ('A','kim'),('B','lee'),('A','park'),('B','park');
      INSERT INTO scopes VALUES ('item-1','shop','billing/'),('item-4','shop','coupon/'),('item-8','shop','admin/');
      INSERT INTO team_scopes VALUES ('A','item-1'),('A','item-4'),('B','item-4'),('B','item-8');
    """)                                            # 팀A: item-1·4 / 팀B: item-4·8 (4는 겹침) / park은 두 팀
    app = create_app(Store(":memory:"), FakeEmbedder(), perms)
    return TestClient(app), perms


def test_chunking():
    py = "import os\n\ndef apply_coupon(o):\n    return o\n\nclass Cart:\n    def total(self):\n        return 1\n"
    assert [c["symbol"] for c in chunk_python(py)] == ["", "apply_coupon", "Cart"]
    md = "# 가이드\n## 쿠폰\n쿠폰 규칙\n## 결제\n결제 규칙\n"
    assert [c["symbol"] for c in chunk_markdown(md)] == ["쿠폰", "결제"]
    long = {"start": 1, "end": 4, "symbol": "f", "body": "a b c\nd e f\ng h i\nj k l"}
    parts = fit_to_tokens([long], lambda t: len(t.split()), 6)
    assert len(parts) == 2 and parts[1]["start"] == 3          # 토큰 한도를 넘으면 반으로 나눈다


def test_permissions_union_and_empty(env):
    _, perms = env
    assert perms.allowed_scopes("kim") == {"item-1", "item-4"}
    assert perms.allowed_scopes("park") == {"item-1", "item-4", "item-8"}   # 두 팀의 합집합
    assert perms.allowed_scopes("nobody") == frozenset()
    assert perms.scope_for("shop", "coupon/apply.py") == "item-4"
    assert perms.scope_for("shop", "misc/x.py") is None


def test_index_and_filtered_search(env):
    client, _ = env
    files = [{"path": "billing/pay.py", "content": "def charge_payment(o):\n    return pg_approve(o)\n"},
             {"path": "coupon/apply.py", "content": "def apply_coupon(order):\n    return order.total * 0.9\n"},
             {"path": "admin/ops.py", "content": "def reset_coupon_cache():\n    pass\n"},
             {"path": "misc/tmp.py", "content": "def coupon_temp():\n    pass\n"}]
    assert client.post("/index", json={"collection": "code", "repo": "shop", "commit": "a1b2c3", "files": files}
                       ).status_code == 401                    # 서비스 토큰 없으면 거부
    r = client.post("/index", headers={"X-Service-Token": "idx-secret"},
                    json={"collection": "code", "repo": "shop", "commit": "a1b2c3", "files": files}).json()
    assert r["skipped_no_scope"] == ["misc/tmp.py"]            # 항목이 없는 파일은 색인하지 않는다

    def search(user):
        return client.post("/search", headers={"X-Service-Token": "mcp-secret", "X-User-Id": user},
                           json={"collection": "code", "query": "coupon", "k": 10}).json()
    paths = lambda d: sorted({x["path"] for x in d["results"]})
    assert paths(search("kim")) == ["billing/pay.py", "coupon/apply.py"]        # 팀A: item-1·4
    assert paths(search("lee")) == ["admin/ops.py", "coupon/apply.py"]          # 팀B: item-4·8
    assert len(paths(search("park"))) == 3                                      # 두 팀
    assert search("nobody") == {"results": [], "denied": True}                  # 권한 없음 → 검색 안 함
    assert search("kim")["results"][0]["commit"] == "a1b2c3"


def test_incremental_indexer(env, tmp_path):
    client, _ = env
    repo = tmp_path / "shop"; (repo / "coupon").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (repo / "coupon" / "apply.py").write_text("def apply_coupon(o):\n    return o\n")
    (repo / ".env").write_text("SECRET=1\n")
    run("add", "-A"); run("commit", "-qm", "1")
    posted = []
    def post(body):
        posted.append(body)
        assert client.post("/index", headers={"X-Service-Token": "idx-secret"}, json=body).status_code == 200
    st = str(tmp_path / "state.json")
    first = index_repo(str(repo), "shop", post, st)
    assert first["full"] and [f["path"] for f in posted[0]["files"]] == ["coupon/apply.py"]   # .env 제외
    (repo / "coupon" / "apply.py").unlink(); (repo / "coupon" / "new.py").write_text("def new_rule():\n    pass\n")
    run("add", "-A"); run("commit", "-qm", "2")
    second = index_repo(str(repo), "shop", post, st)
    assert not second["full"] and second["deleted"] == 1 and posted[-1]["deleted"] == ["coupon/apply.py"]


def test_mcp_end_to_end_over_http(env, monkeypatch):
    """실제 HTTP로 MCP 서버를 띄우고 MCP 클라이언트로 호출 — 토큰 → 사번 → 권한 필터가 이어지는지."""
    client, _ = env
    client.post("/index", headers={"X-Service-Token": "idx-secret"}, json={
        "collection": "code", "repo": "shop", "commit": "a1b2c3",
        "files": [{"path": "coupon/apply.py", "content": "def apply_coupon(order):\n    return order\n"},
                  {"path": "admin/ops.py", "content": "def reset_coupon_cache():\n    pass\n"}]})
    monkeypatch.setenv("USER_TOKENS", "tok-kim:kim,tok-lee:lee")
    import mcp_server.server as srv
    async def fake_post_rag(path, body, user):                # RAG 호출을 위 TestClient로 연결
        return client.post(path, json=body, headers={"X-Service-Token": "mcp-secret", "X-User-Id": user}).json()
    monkeypatch.setattr(srv, "post_rag", fake_post_rag)

    import uvicorn
    server = uvicorn.Server(uvicorn.Config(srv.create_app(), host="127.0.0.1", port=8766, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(50):
        if server.started: break
        time.sleep(0.1)

    async def call(token):
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"} if token else {})
        async with streamable_http_client("http://127.0.0.1:8766/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("search_code", {"query": "coupon"})
                return names, res.content[0].text
    names, kim = asyncio.run(call("tok-kim"))
    _, lee = asyncio.run(call("tok-lee"))
    _, anon = asyncio.run(call(None))
    server.should_exit = True
    assert names == ["search_code", "search_docs", "request_fix"]
    assert "coupon/apply.py" in kim and "admin/ops.py" not in kim       # 김(팀A)은 admin(item-8)을 못 봄
    assert "admin/ops.py" in lee                                          # 이(팀B)는 봄
    assert "인증 정보가 없습니다" in anon                                  # 토큰 없음
    assert "[main@a1b2c3]" in kim and "로컬 파일을 확인" in kim
```

---

## 10. 버린 것

### 10-1. 1판 구현(2026-10-01)에서 제거 — 유지
| 제거 | 왜 만들었었나 | 왜 버리나 |
|---|---|---|
| FastAPI 서버·웹 채팅 화면(`server/`, `web/`, `registry.py`) | "uvicorn 배포"를 웹 서버로 해석 | 로컬 에이전트는 하네스가 PC에서 돈다. 화면은 터미널과 IDE로 충분하고, 사내 배포에 웹 서버는 불필요 |
| `interrupt()` + 체크포인트 재개 승인(approve 노드) | HTTP는 답이 나중에 올 수 있어서 | md 4-6: 확인 화면이 같은 사람·같은 순간이면 그 자리에서 기다리는 편이 단순 |
| LLM 역할 분담(Claude·ChatGPT·Gemma)과 외부 API 자동 대체 | 처음 요구사항 | 폐쇄망 sLLM 전제 — 외부 API 불가 |
| 2차 리뷰 노드(`reviewer.py`) | 다른 계열 모델의 교차 검증 | 사내 모델 하나뿐 |
| 로컬 요약 보조(`assist.py`) | 로컬 Gemma 역할 | 모델 하나 구조에서는 불필요 |
| 시스템 프롬프트 세션 고정 + 대화 압축(compact) | Claude 사고 블록 보존 | sLLM에는 해당 없음 |
| 명령 종료 코드를 artifact로 넘기기 | 출력 요약 시 판정 깨짐 | 요약을 없앴으므로 불필요 |
| LLM 재시도 정책(`RetryPolicy`) | 무료 Gemini 429 대응 | 클라이언트 재시도로 충분 |
| `.env` 자동 로딩(python-dotenv) | 키를 파일에서 읽기 | 키는 OS 자격 증명 저장소·환경변수로(Q5) |

### 10-2. 1판 설계에서 택하지 않은 것 — 유지(번복은 표시)
| 버린 선택지 | 이유 |
|---|---|
| 하네스를 서버에 두고 PC는 MCP로 호출 | 서버의 도구는 PC 파일을 못 만진다. **2판에서도 유지** — 서버에는 도구·데이터만(0-4) |
| ~~오픈소스(Continue·Cline·Aider) 도입~~ | **2판에서 번복 → 채택(D1)**. 1판 이유("PC 안 행동을 코드로 강제할 수 없음, 학습 목적과 맞지 않음")는 사실이지만, 목표가 배포 비용 절감으로 바뀌어 감수(0-4) |
| 데스크톱 앱(Electron 등) | 개발자 대상이면 IDE가 나음 |
| VS Code 확장 우선 | 2판은 Continue가 VS Code·JetBrains를 모두 지원해 해당 없음 |
| FIM 자동완성 | 요구사항이 아님 |
| RAG만으로 코드 검색 | 상위 k개라 전부 찾기를 보장 못 함 → 2판도 **RAG는 위치 힌트**, 확인은 로컬 파일로(5-8) |
| RRF 대신 단순 합집합 | 2판은 서버 RAG 단독이라 해당 없음 |
| IDE에서 엔진이 디스크에 직접 쓰기 | 2판은 Continue가 편집기로 적용 — 해당 없음 |
| 감사 기록을 수집 서버 필수로 | 2판은 PC 감사 기록 자체를 보류(10-3) |
| Java 등 다른 언어의 AST 청킹 | 2판도 줄 묶음 유지 |
| `prompt_toolkit` 비동기 입력 | 2판은 터미널 화면 없음 — 해당 없음 |
| 사용자 계정 기반 감사(SSO 연동) | 게이트웨이 쪽 일 |

### 10-3. 2판 전환으로 보류한 1판 구성
| 1판 모듈 | 2판 처리 | 비고 |
|---|---|---|
| 3-11 그래프, 3-14 엔진 공통, 3-15 CLI, 3-8 화면 | **Continue가 대체** | 루프·대화·화면·승인 |
| 3-16 stdio 모드, 4장 통신 규약, 5장 IntelliJ 플러그인 | **불필요** | Continue가 IDE 통합 |
| 3-4 LLM 연결, 3-1 설정 | Continue `config.yaml`(3-8) | — |
| 3-10 지침·스킬·신뢰 | Continue rules | AGENTS.md를 그대로 쓰는지 확인 필요 |
| 3-5 작업 공간(경계·읽기 금지·백업·인코딩·줄바꿈) | **보류** | Continue 편집 도구로 대체. CP949·CRLF 유지는 Continue 동작 확인 필요 — 깨지면 로컬 MCP로 재도입 후보 |
| 3-6 명령 실행기(비밀 환경변수 제거·Docker) | **보류** | Continue 터미널은 Ask First, 필요 시 로컬 MCP 셸 도구로 대체(3-3의 대체 사례) |
| 3-7 권한 정책(모드 × 인자 기반 위험도) | **보류** | Continue 정책은 도구 단위(3-4) |
| 3-2 관리자 정책, 3-3 키 관리 | **보류** | 정책은 서버 쪽 방어(권한·토큰)로, 키는 Continue 비밀값 기능 |
| 3-12 감사 기록 | **보류** | 게이트웨이·MCP·RAG 기록으로 일부 대체. PC 변경 기록은 git |
| 3-13 코드 색인(로컬) | **서버 RAG로 이전**(5장) | 청킹 코드는 재사용 |
보류 모듈은 `legacy_engine/`에 보관한다. **Continue로 지킬 수 없는 사내 규칙이 확인되면** 해당 기능을 로컬 MCP 도구로 감싸 재도입하는 것이 1순위 후보다.

### 10-4. 2판에서 택하지 않은 것
| 버린 선택지 | 이유 |
|---|---|
| Qdrant 로컬(PC) | PC 배포물이 늘고 색인을 PC마다 반복(0-3, D2) |
| Continue가 Qdrant·RAG를 직접 호출 | Qdrant 키 노출, 권한을 클라이언트에 맡기게 됨(D4) |
| 검색 필터를 호출자(MCP)에게서 받기 | 내부망 호출자가 "전체" 필터를 넣을 수 있음(5-5) |
| 점에 팀 목록 저장(권한 방식 A) | 권한 변경마다 대량 수정(5-6) |
| 브랜치별 색인 | 비용 대비 효과 낮음, P4로 최신성 포기 |
| PC 중계 MCP(서버 하네스가 PC에 rg·파일 읽기를 시키는 구조) | PC마다 배포·양방향 연결·재연결 처리 필요, 도구 하나 승인으로 내부 단계 전부 통과(3-4), 시간 제한(U1) |
| 서버 저장소 사본에서 rg까지 하는 수정 도구 | 로컬 미커밋 변경과 어긋남 — 2판은 로컬 확인을 Continue 내장 도구로 |
| MCP 도구 안 LangGraph 루프를 기본으로 | 루프 안의 루프 — 필요할 때만(4-4) |
| Continue 자체 코드 색인(`@Codebase`) | 지원 중단 |
| 문서 RAG를 별도 MCP 서버로 | 인증·권한·임베딩·감사가 같아 도구만 나누고 서버는 하나로(D5). 담당·부하가 달라지면 분리 |

---

## 11. 진행 상태
| 단계 | 상태 |
|---|---|
| 1판 엔진 + 단위 테스트 53개 | 완료(보관 — 10-3) |
| 2판 전환 결정(0장) | 완료 |
| Continue 정리(3장) | 완료 — 미확인 7개(3-5) |
| MCP 서비스·RAG 서비스·권한·색인 작업 예제 코드(4·5장) | 완료 — 테스트 5개 통과(실제 HTTP로 MCP 확인 포함) |
| 2-1판 구현(D9~D13): LLM 프록시·MCP·RAG(git 증분)·권한(Postgres)·감시 | **완료**(10-05) — 테스트 9개 통과 |
| 실제 KoE5·Qdrant(docker)·Postgres·Redis 연결 확인 | **완료**(10-05) — LLM만 가짜 |
| 외부 gemma4 연결(OpenAI 호환·tool_calls·include_usage) | **다음** — 진행기록 8장 N1~N3 |
| Continue 미확인 항목 실험(U1~U7) | **다음** — U1(MCP 시간 제한)·U2(Cancel 동작)·U7(외부 전송)부터 |
| RAG 검색 품질·권한 회귀 평가(8장) | 다음 |
| 폐쇄망 배포 묶음(7장) | 다음 |
