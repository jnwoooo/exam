@echo off
rem install.cmd - ncn user install (no admin). Lines up to "chcp 65001" are ASCII only.
setlocal
for /f "tokens=2 delims=:" %%a in ('chcp') do set "NCN_OLDCP=%%a"
chcp 65001 >nul
rem ── 여기부터 UTF-8(코드 페이지 65001) — 위 줄은 cmd 가 cp949 로 읽어도 깨지지 않게 영어만 썼다
rem 사용법: install.cmd        (대화형: 서버 주소·토큰을 묻는다)
rem         install.cmd /q     (묻지 않음: NCN_SERVER·NCN_TOKEN 환경변수를 쓰고, 없으면 기본값·기존 값)
rem 실제 작업은 같은 폴더의 install.ps1 이 한다(cmd 로는 PATH 를 안전하게 고치기 어렵다 — setx 1024자 잘림).
rem install.ps1 을 -File 로 돌리지 않고 내용을 읽어 실행(iex)하는 이유: 그룹 정책이 스크립트 실행을 막아도(ExecutionPolicy) 동작하게.
rem 압축을 푼 폴더(끝에 \ 포함)를 install.ps1 에 넘긴다
set "NCN_PKG_DIR=%~dp0"
rem /q 면 묻지 않는 모드
if /i "%~1"=="/q" set "NCN_QUIET=1"
rem PowerShell 5.1(Windows 10 기본)로 install.ps1 실행 — 프로필을 읽지 않고, 파일은 UTF-8(BOM)로 읽음
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText($env:NCN_PKG_DIR + 'install.ps1', [Text.Encoding]::UTF8); Invoke-Expression $s"
rem install.ps1 의 종료 코드(0 성공, 1 실패, 3 실행 중)
set "NCN_RC=%ERRORLEVEL%"
rem 원래 코드 페이지로 되돌림(숫자만 남기고 공백 제거)
set "NCN_OLDCP=%NCN_OLDCP: =%"
rem 대화형이면 창이 바로 닫히지 않게 잠시 멈춤(탐색기에서 두 번 눌러 실행한 경우)
if not "%NCN_QUIET%"=="1" pause
rem 코드 페이지 복원(값을 못 읽었으면 건너뜀)
if defined NCN_OLDCP chcp %NCN_OLDCP% >nul
rem 종료 코드를 그대로 돌려줌
exit /b %NCN_RC%
