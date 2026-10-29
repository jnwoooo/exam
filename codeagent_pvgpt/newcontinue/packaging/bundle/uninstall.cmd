@echo off
rem uninstall.cmd - ncn user uninstall. Lines up to "chcp 65001" are ASCII only.
setlocal
for /f "tokens=2 delims=:" %%a in ('chcp') do set "NCN_OLDCP=%%a"
chcp 65001 >nul
rem ── 여기부터 UTF-8 — 실제 작업은 uninstall.ps1(같은 폴더). 사용법: uninstall.cmd [/q]
rem 이 파일은 보통 %LOCALAPPDATA%\ncn 안에서 실행된다 → uninstall.ps1 이 그 폴더를 지우면 cmd 가 다음 줄을 못 읽는다.
rem 그래서 PowerShell 호출부터 종료까지를 한 줄(&)로 이어 썼다(한 줄은 미리 다 읽혀 있어 파일이 사라져도 끝까지 실행됨).
rem 이 줄 안의 값은 미리 풀려야 하므로 코드 페이지 값(숫자)을 먼저 정리해 둔다
set "NCN_OLDCP=%NCN_OLDCP: =%"
rem /q 면 묻지 않는 모드
if /i "%~1"=="/q" set "NCN_QUIET=1"
rem 스크립트 위치(끝에 \ 포함)
set "NCN_PKG_DIR=%~dp0"
rem uninstall.ps1 내용을 읽어 실행(ExecutionPolicy 무관) → 대화형이면 멈춤 → 코드 페이지 복원 → 종료(결과는 화면 글로 확인)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText($env:NCN_PKG_DIR + 'uninstall.ps1', [Text.Encoding]::UTF8); Invoke-Expression $s" & (if not "%NCN_QUIET%"=="1" pause) & (if defined NCN_OLDCP chcp %NCN_OLDCP% >nul) & exit /b
