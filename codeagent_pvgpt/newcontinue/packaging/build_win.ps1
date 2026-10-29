# packaging/build_win.ps1 — Windows 에서 ncn.exe 빌드(PyInstaller onedir) + 배포 zip. CI(ncn.yml·ncn-vsix.yml)와 로컬 공용
# 사용법:  powershell -NoProfile -ExecutionPolicy Bypass -File codeagent_pvgpt\newcontinue\packaging\build_win.ps1 <출력 폴더> [-NoZip] [-Smoke]
#   인자 1(위치 0) OutDir : 출력 폴더(상대경로면 지금 폴더 기준, 기본 newcontinue\dist). 없으면 만든다.
#   -NoZip               : onedir 만(VSIX 작업용 — 묶음 폴더·zip 생략)
#   -Smoke               : 빌드 후 packaging\smoke.py 로 스모크 시험(가짜 LLM)
# 결과(이 경로·이름은 고정 — VSIX 워크플로가 의존한다):
#   <OutDir>\ncn\ncn.exe, <OutDir>\ncn\_internal\   ← VSIX 에는 이 ncn 폴더를 통째로 넣는다(ncn.exe 만 넣으면 실행 안 됨)
#   <OutDir>\ncn-<버전>-windows-<x64|arm64>.zip, .zip.sha256, <OutDir>\build_info.json
# 필요: uv(PATH 에). 파이썬은 uv 가 받는다(기본 .python-version = 3.14, 바꾸려면 환경변수 UV_PYTHON).
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $OutDir = '',               # 출력 폴더
    [switch] $NoZip,                                               # onedir 만
    [switch] $Smoke                                                # 스모크 시험
)
$ErrorActionPreference = 'Stop'                                    # 오류면 중단

$Root = Split-Path -Parent $PSScriptRoot                           # newcontinue 폴더(이 파일의 상위)
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist' }             # 기본 출력 폴더
$OutDir = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutDir))   # 절대 경로(지금 폴더 기준 — 이미 절대면 그대로)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {         # uv 확인
    throw 'uv 가 PATH 에 없습니다. CI 는 astral-sh/setup-uv, 로컬은 https://docs.astral.sh/uv/ 설치 후 다시 실행하세요.'   # 안내
}
$buildArgs = @('run', '--with', 'pyinstaller', 'python', (Join-Path $PSScriptRoot 'build.py'), '--out', $OutDir)   # PyInstaller 는 빌드 때만 얹음(프로젝트 의존성 아님)
if ($NoZip) { $buildArgs += '--no-zip' }                           # onedir 만
if ($Smoke) { $buildArgs += '--smoke' }                            # 스모크 시험
Push-Location $Root                                                # uv 가 newcontinue 프로젝트를 찾게
try {
    Write-Host "+ uv $($buildArgs -join ' ')"                      # 실행 명령 표시
    & uv @buildArgs                                                # 빌드
    if ($LASTEXITCODE -ne 0) { throw "build.py 실패(종료 코드 $LASTEXITCODE)" }   # 실패 전달
    Write-Host "결과: $OutDir\ncn\ncn.exe"                          # 결과 위치
} finally {
    Pop-Location                                                   # 원래 폴더로
}
