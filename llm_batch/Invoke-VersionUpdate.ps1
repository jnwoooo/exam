<#
.SYNOPSIS
  C++ 버전 업데이트 대응 자동화의 단일 진입 명령.
.DESCRIPTION
  같은 폴더의 vupdate.exe (없으면 python vupdate.py) 를 호출한다.
  처리는 전부 vupdate 가 한다. 이 스크립트는 인자를 넘기고 종료 코드를 돌려줄 뿐이다.
  종료 코드: 0 완료 / 1 완료(수동 검토 있음) / 2 단계 실패 / 3 입력 오류
             4 sLLM 에 보내지 못한 작업이 대기로 남음(-MaxCalls 를 다 썼거나 서버가 계속 바쁨). -Resume 으로 이어서 보낸다

  sLLM(또는 중계 서버) 주소는 이 스크립트에 적지 않는다. 다음 순서로 정해진다.
    1) -LlmEndpoint  2) 환경변수 VUPDATE_LLM_ENDPOINT  3) config\server.json 의 llm.endpoint
  서버 점검(llmcheck)은 vupdate 를 직접 부른다. 예: vupdate.exe llmcheck
  Visual Studio 자동 탐지가 틀리면 -VsPath 또는 환경변수 VUPDATE_VS_PATH 로 설치 폴더를 지정한다.
  Windows SDK 는 -SdkPath 또는 환경변수 VUPDATE_SDK_PATH 로 지정한다(예: C:\Program Files (x86)\Windows Kits\10).
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2015 -ToVs 2022 -Platform x64 -Only "Src/Net/*" -NoLlm
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -FromOs "Windows 7" -ToOs "Windows 11" -FromDb "Oracle 11g" -ToDb "Oracle 19c"
  (OS 와 DB 의 원본/대상. 선택이다. sLLM 요청의 [환경]과 보고서에 들어가고, 낮은 WINVER 를 대상 OS 의 값으로 올린다)
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -Backups keep
  (백업으로 보이는 폴더와 파일도 처리한다. 주지 않으면 skip: 빼고 out\backup_excluded.md 에 목록을 낸다)
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Resume D:\work\runs\PKG_A\20261124_101500
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -MaxCalls 200
  (sLLM 호출을 200회까지만 한다. 남은 작업이 있으면 종료 코드 4 로 끝나고, -Resume 으로 이어서 보낸다)
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -Ask "WriteLog 호출에 줄 번호 인자를 추가하고 g_data 배열을 구조체로 바꾼다"
  (요청문. 형식 없이 글로 적는다. sLLM 이 의도별로 나누고 도구가 대상을 찾아 수정 요청을 보낸다. 결과는 out\ask_plan.json)
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -Only "Src/Net/*" -UntilStage S5 -Ask "if 문 안에서 로그를 남기는 함수를 찾아 줘"
  (대상 판정까지만 돌려 out\ask_trace.md 로 모델의 답과 어떻게 이해했는지 먼저 본다)
.EXAMPLE
  .\Invoke-VersionUpdate.ps1 -Package D:\src\PKG_A -FromVs 2008 -ToVs 2022 -CompatLlm 3 -MaxCalls 50
  (환경 호환성 항목의 처리 깊이. 1: 기계적 변경만(기본), 2: 1 + 선언을 읽어 판정하는 기계적 변환(지역 배열에 쓰는 보안 CRT 호출),
   3: 2 + 기계적 변경 뒤에 남은 것을 sLLM 에. 옵션별 사용 예는 vupdate examples)
#>
param(
    [string]$Package,
    [string]$FromVs,
    [string]$ToVs,
    [string]$CppStandard,
    [string]$Platform,
    [string]$Conformance,
    [string]$Charset,
    [string]$Deprecated,
    [string]$FromOs,
    [string]$ToOs,
    [string]$FromDb,
    [string]$ToDb,
    [string]$Request,
    [string[]]$Ask,
    [string]$AskFile,
    [string]$FromCppStandard,
    [string]$Exceptions,
    [string[]]$DisableRule,
    [string[]]$Only,
    [string[]]$Exclude,
    [string]$Backups,
    [string]$AskMatch,
    [string]$LlmEndpoint,
    [int]$MaxParallel = 0,
    [int]$CompileParallel = -1,
    [int]$MaxCalls = 0,
    [string]$VsPath,
    [string]$SdkPath,
    [string]$Out,
    [string]$UntilStage,
    [string]$Resume,
    [switch]$DryRun,
    [switch]$NoCompile,
    [switch]$NoLlm,
    [int]$CompatLlm = 0
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "vupdate.exe"
$py = Join-Path $here "vupdate.py"

$a = @("--console-encoding", "utf-8", "run")
if ($Resume) {
    $a += @("--resume", $Resume)
} else {
    if (-not $Package) { Write-Host "오류: -Package 로 대상 패키지 폴더를 지정하세요."; exit 3 }
    $a += @("--package", $Package)
}
if ($Ask)     { foreach ($x in $Ask) { $a += @("--ask", $x) } }
if ($AskFile) { $a += @("--ask-file", $AskFile) }
if ($FromCppStandard) { $a += @("--from-cpp-standard", $FromCppStandard) }
if ($Exceptions)  { $a += @("--exceptions", $Exceptions) }
if ($DisableRule) { $a += @("--disable-rule", ($DisableRule -join ",")) }
if ($FromVs)      { $a += @("--from-vs", $FromVs) }
if ($ToVs)        { $a += @("--to-vs", $ToVs) }
if ($CppStandard) { $a += @("--cpp-standard", $CppStandard) }
if ($Platform)    { $a += @("--platform", $Platform) }
if ($Conformance) { $a += @("--conformance", $Conformance) }
if ($Charset)     { $a += @("--charset", $Charset) }
if ($Deprecated)  { $a += @("--deprecated", $Deprecated) }
if ($FromOs)      { $a += @("--from-os", $FromOs) }
if ($ToOs)        { $a += @("--to-os", $ToOs) }
if ($FromDb)      { $a += @("--from-db", $FromDb) }
if ($ToDb)        { $a += @("--to-db", $ToDb) }
if ($Request)     { $a += @("--request", $Request) }
foreach ($p in $Only)    { if ($p) { $a += @("--only", $p) } }
foreach ($p in $Exclude) { if ($p) { $a += @("--exclude", $p) } }
if ($Backups)     { $a += @("--backups", $Backups) }
if ($AskMatch)    { $a += @("--ask-match", $AskMatch) }
if ($LlmEndpoint) { $a += @("--llm-endpoint", $LlmEndpoint) }
if ($MaxParallel -gt 0) { $a += @("--max-parallel", "$MaxParallel") }
if ($CompileParallel -ge 0) { $a += @("--compile-parallel", "$CompileParallel") }
if ($MaxCalls -gt 0) { $a += @("--max-calls", "$MaxCalls") }
if ($VsPath)      { $a += @("--vs-path", $VsPath) }
if ($SdkPath)     { $a += @("--sdk-path", $SdkPath) }
if ($Out)         { $a += @("--out", $Out) }
if ($UntilStage)  { $a += @("--until", $UntilStage) }
if ($DryRun)      { $a += "--dry-run" }
if ($NoCompile)   { $a += "--no-compile" }
if ($NoLlm)       { $a += "--no-llm" }
if ($CompatLlm -ne 0) { $a += @("--compat-llm", "$CompatLlm") }

if (Test-Path $exe) {
    & $exe @a
} elseif (Test-Path $py) {
    & python $py @a
} else {
    Write-Host "오류: vupdate.exe 또는 vupdate.py 가 이 스크립트와 같은 폴더에 없습니다."
    exit 3
}
exit $LASTEXITCODE
