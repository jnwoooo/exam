# ncn VS 2022 확장(VSIX) — 화면만

`ncn --stdio`(ACP 에이전트)를 자식 프로세스로 띄우고, VS 안에서 대화·도구 승인·비교 창·열린 문서 편집을 한다.
하네스 로직(루프·도구·권한·압축)은 전부 ncn. 확장은 화면과 IDE 연동만 한다(계획 14장, 규격은 `../ACP_연동.md`).

## 1. 구조

| 경로 | 내용 |
|---|---|
| `NcnVs.Core/` | netstandard2.0, VS 의존 없음. 줄 단위 JSON-RPC(`Json/`), ACP 모델·클라이언트(`Acp/`), 화면 상태·세션 조정자(`State/`), ncn 프로세스·Job Object·개발자 환경(`Host/`), 마크다운·최소 편집·인코딩(`Text/`) |
| `NcnVs.Core.Tests/` | net8.0 xunit. 가짜 에이전트(메모리 파이프)로 왕복·업데이트·승인(R3·R4)·fs(R1·R2)·질문·재시작 시험 + 실제 ncn 통합 시험 1개(`NCN_EXE` 있을 때만) |
| `NcnVs/` | net472 VSIX(고전 VSIX 프로젝트). `NcnPackage`(AsyncPackage), `NcnToolWindow`, `UI/`(순수 WPF, 코드로 구성), `Ide/`(RDT 버퍼 fs 위임·비교 창·솔루션/선택 영역·로그), 옵션 페이지 |
| `gallery/make_feed.py` | 파일 서버용 사설 갤러리 Atom 피드 생성(표준 라이브러리만) |
| `../../../.github/workflows/ncn-vsix.yml` | Windows 러너 빌드(ncn.exe → 시험 → VSIX → 아티팩트) |

주요 선택
| 항목 | 선택 | 이유 |
|---|---|---|
| JSON | Newtonsoft.Json 13.0.1 참조만, VSIX 에서 뺌(`SuppressFromVsix`) | VS 2022 가 13.0.0.0 을 이미 싣고 있다. System.Text.Json 은 의존 DLL 을 VSIX 에 넣어야 해 VS 쪽과 겹칠 위험 |
| SDK | `Microsoft.VisualStudio.SDK` 17.0.32112.339 | 가장 낮은 버전으로 컴파일 → 17.0 부터 로드 |
| 화면 | XAML 없이 코드로 만든 WPF | 고전 프로젝트의 XAML 컴파일 위험 제거 |
| 설치 대상 | Community/Pro/Enterprise `[17.0,)` × amd64·arm64 | VS 2026(18.x)도 범위 안 |

## 2. 빌드(CI)

이 저장소의 개발 PC(macOS)에는 .NET·VS 가 없다 → **GitHub Actions `ncn-vsix`** 에서 빌드한다.

| 단계 | 내용 |
|---|---|
| 트리거 | `newcontinue/vs/**`·`newcontinue/packaging/**` 변경 푸시, 태그 `ncn-v*`, 손으로 실행 |
| 1 | `packaging/build_win.ps1 <폴더> -NoZip` → `<폴더>\ncn\ncn.exe` + `_internal\` |
| 2 | `dotnet test NcnVs.Core.Tests`(`NCN_EXE` 지정 → 실제 ncn 통합 시험 포함) |
| 3 | `msbuild NcnVs.csproj /restore /p:Configuration=Release /p:NcnDistDir=<폴더>` |
| 4 | VSIX 안 필수 파일 확인(`NcnVs.dll`·`NcnVs.Core.dll`·`NcnVs.pkgdef`·`ncn/ncn.exe`) |
| 결과 | 아티팩트 `ncn-vsix-<번호>-<커밋>`: `NcnVs.vsix`, `make_feed.py`, `feed.example.xml` |

로컬(Windows + VS 2022 "Visual Studio 확장 개발" 워크로드): `NcnVs.sln` 을 열고 F5(실험 인스턴스).
ncn 폴더가 없으면 경고만 나고 VSIX 는 만들어진다(이때는 옵션에서 ncn.exe 경로 지정).

## 3. 설치

| 방법 | 순서 |
|---|---|
| VSIX 직접 | VS 를 모두 닫고 `NcnVs.vsix` 더블클릭 → 설치. "서명 없음" 표시는 정상(서명하지 않음). 사용자 단위 설치라 관리자 권한 불필요 |
| 사설 갤러리 | ① 파일 서버 폴더(예: `\\fs01\tools\ncn-vs`)에 VSIX 복사 ② 그 폴더에서 `python make_feed.py \\fs01\tools\ncn-vs` → `feed.xml` ③ VS: **도구 → 옵션 → 환경 → 확장 → 추가 확장 갤러리 → 추가**, URL = `\\fs01\tools\ncn-vs\feed.xml` → 확장 관리자 → 온라인 → 그 갤러리에서 설치 |
| 업데이트 | `source.extension.vsixmanifest` 의 `Version` 을 올려 빌드 → 새 VSIX 를 같은 폴더에 두고 `make_feed.py` 다시 실행(같은 id 는 가장 높은 버전만 피드에 들어감) → VS 가 업데이트를 알려 준다 |
| 제거 | 확장 관리자 → 설치됨 → ncn → 제거 |

## 4. 화면 사용법

열기: **보기 → 다른 창 → ncn** 또는 **도구 → ncn 에이전트 열기**. 처음 열 때 ncn 을 띄운다(작업 폴더 = 솔루션 폴더, 솔루션이 없으면 사용자 폴더).

| 요소 | 동작 |
|---|---|
| 입력창 | **Enter 보내기**, Shift+Enter 줄바꿈. `/compact` = 대화 압축(ncn 이 처리) |
| 첨부 | "현재 파일" = 활성 문서를 `resource_link` 로(ncn 이 `@파일` 첨부), "선택 영역" = 선택 글을 `resource`(`#L시작-끝`)로. 선택이 비면 보내지 않음 |
| 모드 | normal(필요한 도구는 묻기)·plan(읽기 전용)·auto(묻지 않음) → `session/set_mode` |
| 중단 | `session/cancel` 을 보낸 뒤 걸린 승인·질문은 `cancelled` 로 답함(R3) |
| 새 대화 | `session/new`(솔루션을 바꿨으면 그 폴더로). ncn 이 멈춰 있으면 다시 띄움 |
| 도구 카드 | ○ 대기 · ◐ 실행 · ✔ 완료 · ✖ 실패. 펼치면 결과 글·인자·diff 의 "비교 창 열기" |
| 승인 | 버튼 3개는 ncn 이 준 이름 그대로(`Continue` / `Continue + don't ask again` / `No, and tell Continue what to do differently`). Edit·Write 는 VS 비교 창(현재 ↔ 제안)이 자동으로 열림(옵션) |
| 질문(AskQuestion) | 선택지 버튼, 직접 입력 + "답하기", "건너뛰기"(기본 답) |
| 계획 | Checklist 도구 결과가 위쪽 "계획 (끝/전체)" 에 보임 |
| 상태 줄 | 준비됨·응답 중·승인 대기·토큰 사용량·LLM 재시도 회차 · 작업 폴더 |
| 파일 편집 | ncn 의 Read/Edit/Write 는 `fs/*` 로 확장에 위임: 열린 문서는 **버퍼에서 읽고, 버퍼를 고친 뒤 저장**(R1, Ctrl+Z 가능), 아니면 디스크(인코딩·BOM 유지) |

옵션: **도구 → 옵션 → ncn → 일반**
| 옵션 | 기본 | 뜻 |
|---|---|---|
| ncn.exe 경로 | 비움 = VSIX 안 `ncn\ncn.exe` | 다른 빌드를 쓸 때 전체 경로(`%변수%` 허용) |
| config 경로 | 비움 = `~/.continue/config.yaml` | `--config` |
| 추가 인자 | 비움 | 예: `--allow Bash --rule "한국어로 답할 것"` |
| 자세한 로그 | 끔 | `--verbose`(stderr DEBUG) |
| VS 개발자 환경 전달 | 켬 | `VSINSTALLDIR`·`DevEnvDir`·`VisualStudioVersion`·PATH 앞에 msbuild(amd64/arm64)·vstest·VS git |
| 현재 파일 / 선택 영역 첨부 기본값 | 끔 / 켬 | 새 창의 확인란 처음 값 |
| 승인 때 비교 창 자동 | 켬 | |

옵션은 **ncn 을 띄울 때** 읽는다 → 바꾼 뒤에는 VS 를 다시 시작하거나 ncn 이 다시 떠야 반영된다.

## 5. 문제 해결

| 증상 | 확인 |
|---|---|
| 아무 응답 없음·시작 실패 | **출력 창 → "ncn"** 또는 로그 파일 `%LOCALAPPDATA%\ncn-vs\logs\ncn-vs-날짜.log`(도구 창 "로그" 버튼). ncn stderr 는 `[ncn]` 로 시작 |
| `ncn.exe 를 찾지 못했습니다` | VSIX 에 ncn 이 안 들어감(CI 경고 확인) → 옵션에서 경로 지정 |
| `ncn 시작 실패: …` | 설정 파일 오류·chat 모델 없음(cn 원문 글). `--config` 경로·모델 확인. 옵션 "자세한 로그" 켜고 다시 |
| "ncn 이 종료되었습니다 … 다시 시작합니다" | 비정상 종료 → 자동 재시작 후 `session/load`(5분에 3번까지). 계속되면 "중지됨" → 로그 확인 후 "새 대화" |
| msbuild 를 못 찾음(Bash 도구) | 옵션 "VS 개발자 환경 전달" 켬. cl.exe 등 C++ 도구가 필요하면 시스템 PATH 로 해결 |
| VS 종료 뒤 ncn 이 남음 | 로그에 `Job Object` 경고가 있는지(정책상 Job 을 못 만드는 환경) |
| 비교 창 임시 파일 | `%TEMP%\ncn-vs\diff\`(하루 지나면 확장이 지움) |

## 6. 현장 확인 목록

| # | 확인 | 결과 |
|---|---|---|
| 1 | VS 2022 **17.0**(가장 낮은 버전)에서 설치·로드·도구 창 열림 | |
| 2 | VS 2022 **최신 17.x** 에서 같은 확인 | |
| 3 | **VS 2026** 에서 설치·로드(범위 `[17.0,)`) | |
| 4 | **x64** Windows 10/11 | |
| 5 | **arm64** Windows 11 + arm64 VS(ncn.exe 는 x64 에뮬레이션) | |
| 6 | 한글: 입력(IME 조합 중 Enter 는 조합 확정만), 응답 표시, 한글 경로 첨부(`%xx` URI), CP949 파일 읽기·쓰기 인코딩 유지 | |
| 7 | **R1**: 열린 문서를 Edit → 버퍼가 바뀌고 **저장됨**(탭 `*` 없음) → 같은 턴 `msbuild`/`dotnet test` 가 새 내용으로 빌드. Ctrl+Z 로 되돌림 | |
| 8 | R2: 저장 안 한 변경이 있는 문서를 Read → 버퍼 내용 기준 | |
| 9 | 승인 3버튼·비교 창·"아니오" 뒤 다음 도구 승인(R4)·중단(R3) | |
| 10 | 모드 plan/auto 전환, `/compact`, AskQuestion | |
| 11 | ncn 강제 종료(작업 관리자) → 자동 재시작·대화 이어 가기 | |
| 12 | VS 강제 종료 → ncn·powershell 이 남지 않음(Job Object) | |
| 13 | 사설 갤러리(UNC feed.xml) 등록·업데이트 알림 | |
