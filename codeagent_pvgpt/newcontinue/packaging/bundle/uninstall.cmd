@echo off
rem uninstall.cmd - ncn user uninstall. Lines up to "chcp 65001" are ASCII only.
setlocal
rem Step 0: run from a copy in %TEMP% so deleting the install folder cannot break this batch file.
if not "%NCN_UNINSTALL_COPY%"=="1" (
  if not exist "%TEMP%\ncn-uninstall\" mkdir "%TEMP%\ncn-uninstall"
  copy /y "%~dp0uninstall.cmd" "%TEMP%\ncn-uninstall\uninstall.cmd" >nul
  copy /y "%~dp0uninstall.ps1" "%TEMP%\ncn-uninstall\uninstall.ps1" >nul
  set "NCN_UNINSTALL_COPY=1"
  "%TEMP%\ncn-uninstall\uninstall.cmd" %*
)
setlocal EnableDelayedExpansion
for /f "tokens=2 delims=:" %%a in ('chcp') do set "NCN_OLDCP=%%a"
chcp 65001 >nul
rem ── 여기부터 UTF-8 — 실제 작업은 uninstall.ps1. 사용법: uninstall.cmd [/q]
rem 0단계(위 영어 줄) 설명: 이 파일은 보통 %LOCALAPPDATA%\ncn 안에서 실행되는데, uninstall.ps1 이 그 폴더를 지우면
rem   cmd 가 실행 중인 배치 파일을 다시 읽지 못해 "The system cannot find the path specified." 가 나고 종료 코드가 1 이 된다(2026-10-30 CI).
rem   그래서 %TEMP%\ncn-uninstall\ 로 자기와 uninstall.ps1 을 복사한 뒤 그 사본으로 넘긴다.
rem   call 없이 다른 배치 파일을 부르면 제어가 넘어가고 돌아오지 않으므로 원래 파일은 다시 읽히지 않는다.
rem   uninstall.ps1 은 자기 위치가 아니라 고정 경로(%LOCALAPPDATA%\ncn)를 지우므로 사본에서 돌려도 결과가 같다.
rem   맨 위 setlocal: 표시 변수가 사용자의 명령 창에 남지 않게(남으면 같은 창에서 다시 제거할 때 복사를 건너뜀).
rem   사본인지는 경로 비교가 아니라 NCN_UNINSTALL_COPY=1 표시로 판단한다(%TEMP% 가 8.3 짧은 이름이면 경로 비교가 영원히 달라 무한 반복될 수 있음).
rem 코드 페이지 값(숫자)의 공백 정리
set "NCN_OLDCP=%NCN_OLDCP: =%"
rem /q 면 묻지 않는 모드
if /i "%~1"=="/q" set "NCN_QUIET=1"
rem uninstall.ps1 이 있는 폴더(= 이 사본의 폴더, 끝에 \ 포함)
set "NCN_PKG_DIR=%~dp0"
rem uninstall.ps1 실행(내용을 읽어 실행 — ExecutionPolicy 무관)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText($env:NCN_PKG_DIR + 'uninstall.ps1', [Text.Encoding]::UTF8); Invoke-Expression $s"
rem PowerShell 종료 코드 보관(0 성공 · 1 실패 · 3 실행 중이라 중단)
set "NCN_RC=!errorlevel!"
rem 대화형이면 결과를 읽을 수 있게 멈춤
if not "%NCN_QUIET%"=="1" pause
rem 코드 페이지 원래대로
if defined NCN_OLDCP chcp %NCN_OLDCP% >nul
rem PowerShell 의 종료 코드를 그대로 돌려줌
exit /b %NCN_RC%
