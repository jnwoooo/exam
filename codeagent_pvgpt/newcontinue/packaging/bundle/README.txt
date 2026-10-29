ncn — 사내 코딩 에이전트(Continue CLI 호환) 사용 안내
====================================================

ncn 은 Continue CLI(cn)와 같은 방식으로 동작하는 명령줄 코딩 에이전트입니다.
설정은 Continue 와 같은 파일(%USERPROFILE%\.continue\config.yaml)을 함께 씁니다.
모델과 사내 도구는 코드에이전트 서버(:8200 의 /v1, /mcp)를 거쳐 씁니다.
Python·Node 등 따로 설치할 것은 없습니다(이 폴더에 다 들어 있음).

지원: Windows 10 1909 이상 / Windows 11 (x64, arm64). 관리자 권한 필요 없음.


1. 설치
-------
1) zip 을 아무 폴더에 "압축 풀기" 합니다(zip 안에서 바로 실행하지 마세요).
2) install.cmd 를 두 번 누릅니다(또는 명령 창에서 install.cmd).
3) 물어보는 것
   - 코드에이전트 서버 주소: 관리자에게 받은 주소(예: http://10.1.2.3:8200).
     config.yaml 이 이미 있으면 묻지 않고 기존 설정을 그대로 씁니다.
   - 개인 토큰(CODEAGENT_TOKEN): 관리자에게 받은 ca-... 값. 입력 내용은 화면에 보이지 않습니다.
     붙여 넣기는 마우스 오른쪽 단추 또는 Ctrl+V.
4) 마지막에 "ncn --doctor" 점검 표가 나옵니다. "종합: 정상" 이면 끝입니다.

설치되는 곳
   %LOCALAPPDATA%\ncn\ncn.exe        실행 파일(+ _internal 폴더)
   사용자 PATH 에 %LOCALAPPDATA%\ncn 추가(이미 있으면 그대로)
   %USERPROFILE%\.continue\config.yaml   설정(없을 때만 만듦)
   %USERPROFILE%\.continue\.env          토큰(CODEAGENT_TOKEN=...)

설치 후에는 "새로 연" 명령 창·Visual Studio 에서 ncn 이 잡힙니다.
(설치 전에 열려 있던 창은 다시 열어야 합니다.)

묻지 않는 설치(배포 도구용)
   set NCN_SERVER=http://10.1.2.3:8200
   set NCN_TOKEN=ca-...
   install.cmd /q


2. 실행
-------
   cd D:\work\shop                     작업 폴더(저장소 루트)로 이동
   ncn -p "빌드 실패 원인 요약"          답만 출력하고 종료(배치 파일·CI)
   ncn -p "쿠폰 계산 위치 찾아줘" --auto  도구를 묻지 않고 실행
   ncn --resume -p "이어서 해줘"         지난 대화 이어서
   ncn --doctor                        설치·연결 점검
   ncn --doctor --format json          점검 결과를 JSON 으로(문의할 때 붙여 넣기)
   ncn --version                       버전
   ncn --stdio                         IDE 연동용(Visual Studio 확장이 자동으로 씀 — 직접 실행할 일 없음)

대화형 화면(-p 없이 실행)은 아직 준비 중입니다. Visual Studio 확장 또는 -p 를 쓰세요.


3. Visual Studio 에서 쓰기
--------------------------
(1) Visual Studio 2022 확장(VSIX)이 있으면 그것을 쓰세요 — 확장이 ncn 을 알아서 띄웁니다.

(2) 외부 도구로 등록(확장 없이, VS 2010~2022 공통)
   도구 → 외부 도구 → 추가
     제목       ncn
     명령       cmd.exe
     인수       /k ncn --version
     초기 디렉터리  $(SolutionDir)
   → 도구 메뉴에서 "ncn" 을 누르면 솔루션 폴더에서 명령 창이 열립니다. 그 창에서 ncn -p "..." 로 씁니다.
   한 번에 질문까지 하려면 인수를  /k ncn -p "$(ItemFileName) 파일 설명해줘"  처럼 적을 수 있습니다.

(3) 보기 → 터미널(개발자 PowerShell)에서 ncn 실행
   msbuild·dotnet 이 PATH 에 잡힌 상태라 ncn 의 Bash 도구가 빌드·테스트를 실행할 수 있습니다.

ncn 이 파일을 고치면 Visual Studio 가 "외부에서 변경됨"으로 다시 불러옵니다.


4. 문제 해결
------------
먼저 새 명령 창에서  ncn --doctor  를 실행하고 "할 일" 을 따르세요.

| 증상 | 원인·해결 |
|---|---|
| 'ncn'은(는) 내부 또는 외부 명령... 이 아닙니다 | 설치 후 창을 새로 열지 않음 → 명령 창·VS 를 다시 엽니다. 그래도 안 되면 %LOCALAPPDATA%\ncn\ncn.exe --doctor 로 직접 실행 |
| 설정 파일 실패 | %USERPROFILE%\.continue\config.yaml 이 없거나 YAML 문법 오류(탭 문자·들여쓰기). %LOCALAPPDATA%\ncn\config.template.yaml 을 복사해 다시 만듭니다 |
| chat 모델: 비밀값을 찾지 못함 | 토큰이 없음 → %USERPROFILE%\.continue\.env 에 CODEAGENT_TOKEN=ca-... 한 줄(또는 install.cmd 다시 실행) |
| 서버 연결: 연결 실패 / 시간 초과 | 서버 주소·포트(:8200)·방화벽 확인. 사내 프록시를 거치면 안 되는 주소는 NO_PROXY 에 넣습니다 |
| 서버 연결: HTTP 401 / 403 | 토큰이 틀렸거나 만료 → 관리자에게 새 토큰을 받아 .env 를 고칩니다 |
| 인증서 오류(CERTIFICATE) | 사내 HTTPS 인증서 → config.yaml 의 모델 requestOptions 에 caBundlePath: C:\경로\ca.pem |
| MCP 실패 | /mcp 주소·토큰 확인. 서버 담당자에게 MCP 가 켜져 있는지 문의 |
| 콘솔 한글이 깨짐 | 명령 창 속성 → 글꼴을 "돋움체"·"굴림체" 등 한글 글꼴로. 또는 chcp 65001 후 실행 |
| rg(검색) 경고 | 문제 아님(내장 검색 사용). 빠른 검색이 필요하면 rg.exe 를 %LOCALAPPDATA%\ncn 에 복사 |
| 설치 중 "ncn 이 실행 중입니다" | Visual Studio(확장)·명령 창의 ncn 을 끄고 다시 install.cmd |

문의할 때: ncn --doctor --format json > doctor.json 으로 저장해 내용을 전달하세요.
(토큰 값은 앞 3글자와 길이만 들어 있어 그대로 보내도 됩니다.)


5. Windows 보안 경고(SmartScreen·백신) — 서명 없음 안내
------------------------------------------------------
ncn.exe 는 아직 코드 서명이 되어 있지 않습니다(사내 배포용, 결정 사항).
그래서 다음 경고가 나올 수 있으며, 사내 파일 서버에서 받은 정식 배포본이면 진행해도 됩니다.

- "Windows의 PC 보호"(SmartScreen) 파란 창: "추가 정보" → "실행".
  install.cmd 가 설치 폴더의 "인터넷에서 받은 파일" 표시를 지우므로 설치 후에는 보통 다시 나오지 않습니다.
- zip 을 풀기 전에 zip 파일 속성 → "차단 해제" 를 체크하면 경고가 줄어듭니다.
- 백신이 ncn.exe 를 격리하는 경우: 보안 담당자에게 %LOCALAPPDATA%\ncn 폴더 예외 등록을 요청하세요.
  ncn 은 압축을 풀어 실행하는 방식(onefile)이 아니라 폴더에 그대로 두는 방식(onedir)이라 오탐이 적은 편입니다.
- 받은 파일이 정본인지 확인: 관리자가 함께 둔 .sha256 파일의 값과 비교합니다.
     PowerShell:  Get-FileHash .\ncn-<버전>-windows-x64.zip -Algorithm SHA256
- 파일 속성 → 자세히 에서 제품 이름 ncn, 버전을 확인할 수 있습니다("게시자: 알 수 없음" 은 서명이 없어서입니다).


6. 업데이트·제거
----------------
업데이트: 새 버전 zip 을 풀고 install.cmd 를 다시 실행합니다(설정·토큰은 그대로, 묻는 토큰은 Enter 로 유지).
          먼저 Visual Studio 와 ncn 을 끄세요(실행 중이면 덮어쓸 수 없음).
현재 버전: ncn --version  또는 %LOCALAPPDATA%\ncn\VERSION.txt
제거: %LOCALAPPDATA%\ncn\uninstall.cmd — 설치 폴더와 PATH 항목만 지우고 %USERPROFILE%\.continue 는 남깁니다.


7. macOS·Linux
--------------
압축을 풀고  sh install.sh  — ~/.local/share/ncn 에 설치, ~/.local/bin/ncn 링크.
macOS 에서 "확인되지 않은 개발자" 경고가 나오면: Finder 에서 ncn 을 우클릭 → 열기, 또는
   xattr -dr com.apple.quarantine ~/.local/share/ncn
