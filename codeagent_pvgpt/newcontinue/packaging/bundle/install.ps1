# install.ps1 — ncn 사용자 단위 설치(관리자 권한 불필요). install.cmd 가 이 파일 내용을 읽어 실행한다(계획 8-2)
# 하는 일: ① ncn\ 폴더를 %LOCALAPPDATA%\ncn 으로 복사 ② 사용자 PATH(레지스트리 HKCU\Environment)에 추가(이미 있으면 그대로)
#          ③ %USERPROFILE%\.continue\config.yaml 이 없을 때만 템플릿 복사(서버 주소 입력) ④ 개인 토큰을 물어 .continue\.env 에 기록
#          ⑤ ncn --doctor 로 점검
# setx 를 쓰지 않는 이유: setx PATH "..." 는 1024자에서 잘라 기존 PATH 를 망가뜨리고, %VAR% 를 풀어 REG_SZ 로 바꿔 버린다.
#   → 레지스트리 값을 "풀지 않은 원문"(DoNotExpandEnvironmentNames)으로 읽고 항목 하나만 붙여 REG_EXPAND_SZ 로 쓴다.
# 입력(환경변수 — install.cmd 가 넣음): NCN_PKG_DIR(압축 푼 폴더), NCN_QUIET=1(묻지 않음), NCN_SERVER(서버 주소), NCN_TOKEN(토큰)
# 종료 코드: 0 설치 성공(점검 실패여도 설치는 성공) · 1 설치 실패 · 3 ncn 이 실행 중이라 덮어쓸 수 없음
$ErrorActionPreference = 'Stop'                                  # 오류가 나면 바로 catch 로

$Src = $env:NCN_PKG_DIR                                          # 압축 푼 폴더(install.cmd 의 %~dp0)
if (-not $Src) { $Src = $PSScriptRoot }                          # install.ps1 을 직접 실행한 경우
$Src = $Src.TrimEnd('\')                                         # 끝의 \ 제거
$Quiet = ($env:NCN_QUIET -eq '1')                                # 묻지 않는 모드(배포 도구용)
$Dest = Join-Path $env:LOCALAPPDATA 'ncn'                        # 설치 폴더(사용자 단위)
$PathEntry = '%LOCALAPPDATA%\ncn'                                # PATH 에 넣을 글(사용자 이름이 바뀌어도 맞게 변수로)
$TokenName = 'CODEAGENT_TOKEN'                                   # 토큰 변수 이름(config.template.yaml 의 secrets 와 같음)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)         # BOM 없는 UTF-8(.env·config.yaml 용)

function Say([string]$m) { Write-Host $m }                       # 안내 출력
function Step([string]$m) { Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }   # 단계 제목

function Send-EnvChange {                                        # 새 PATH 를 탐색기 등 실행 중인 프로그램에 알림
    # WM_SETTINGCHANGE("Environment") 를 모든 창에 보내야 새로 연 명령 창·VS 가 새 PATH 를 받는다(안 보내면 로그오프 후 적용)
    try {                                                        # Add-Type(C# 컴파일)이 막힌 PC 도 있음
        if (-not ('Ncn.Win32' -as [type])) {                     # 이미 정의했으면 건너뜀
            Add-Type -Namespace Ncn -Name Win32 -MemberDefinition '[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'   # Win32 함수 선언
        }
        $r = [UIntPtr]::Zero                                     # 결과 자리
        [void][Ncn.Win32]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r)   # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG, 5초
    } catch {                                                    # 실패하면 setx 로 대신 알림(짧은 값이라 1024자 문제 없음)
        & setx.exe NCN_HOME $Dest | Out-Null                     # setx 는 값을 쓴 뒤 WM_SETTINGCHANGE 를 보낸다(NCN_HOME = 설치 폴더, uninstall 이 지움)
    }
}

function Normalize-PathItem([string]$p) {                        # PATH 항목 비교용 정규화
    ([Environment]::ExpandEnvironmentVariables($p.Trim().Trim('"'))).TrimEnd('\').ToLowerInvariant()   # 변수 풀고·따옴표·끝 \ 제거·소문자
}

function Add-UserPath([string]$entry, [string]$target) {         # 사용자 PATH 에 한 항목 추가(이미 있으면 그대로)
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)   # HKCU\Environment(쓰기)
    if ($null -eq $key) { $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment') }   # 없으면 만듦(드묾)
    try {
        $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)   # 풀지 않은 원문
        $items = @($raw -split ';' | Where-Object { $_.Trim() -ne '' })   # 빈 항목 제외
        $want = Normalize-PathItem $target                       # 비교 기준(실제 경로)
        foreach ($i in $items) {                                 # 이미 있는지
            if ((Normalize-PathItem $i) -eq $want) { return $false }   # 있으면 바꾸지 않음
        }
        $new = (@($items) + $entry) -join ';'                    # 맨 뒤에 추가(기존 순서 유지)
        $key.SetValue('Path', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString)   # REG_EXPAND_SZ 로 씀(%LOCALAPPDATA% 가 풀리게)
        return $true                                             # 추가함
    } finally {
        $key.Close()                                             # 레지스트리 닫기
    }
}

function Read-Secret([string]$prompt) {                          # 화면에 보이지 않게 입력 받기
    $sec = Read-Host -Prompt $prompt -AsSecureString             # 입력(별표 표시)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)   # 평문으로 꺼내기
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }   # 꺼내고 메모리 지움
}

try {
    $ver = if (Test-Path (Join-Path $Src 'VERSION.txt')) { (Get-Content -LiteralPath (Join-Path $Src 'VERSION.txt') -TotalCount 1) } else { 'ncn ?' }   # 묶음 버전
    Say "$ver 설치를 시작합니다. 설치 위치: $Dest"                  # 시작 안내

    # ── 1) 파일 복사 ──
    Step '1/5 파일 복사'                                          # 단계 제목
    $srcExe = Join-Path $Src 'ncn\ncn.exe'                       # 묶음 안 실행 파일
    if (-not (Test-Path -LiteralPath $srcExe)) { throw "ncn\ncn.exe 가 없습니다: $srcExe (zip 을 끝까지 풀었는지 확인)" }   # 묶음 확인
    $running = @(Get-Process -Name ncn -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dest, [StringComparison]::OrdinalIgnoreCase) })   # 설치본이 실행 중인지
    if ($running.Count -gt 0) {                                  # 실행 중이면 덮어쓸 수 없음
        Say "ncn 이 실행 중입니다(PID $($running.Id -join ', ')). Visual Studio·명령 창의 ncn 을 끈 뒤 다시 실행하세요."   # 안내
        exit 3                                                   # 종료 코드 3
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null   # 설치 폴더
    $internal = Join-Path $Dest '_internal'                      # 이전 버전의 라이브러리 폴더
    if (Test-Path -LiteralPath $internal) { Remove-Item -LiteralPath $internal -Recurse -Force }   # 지움(옛 파일이 섞이지 않게)
    Copy-Item -Path (Join-Path $Src 'ncn\*') -Destination $Dest -Recurse -Force   # ncn.exe + _internal
    foreach ($f in 'uninstall.cmd', 'uninstall.ps1', 'README.txt', 'VERSION.txt', 'config.template.yaml') {   # 함께 둘 파일
        $p = Join-Path $Src $f                                   # 원본
        if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination $Dest -Force }   # 있으면 복사
    }
    Get-ChildItem -LiteralPath $Dest -Recurse -File | Unblock-File -ErrorAction SilentlyContinue   # "인터넷에서 받은 파일" 표시 제거(SmartScreen 경고 줄임)
    Say "복사 완료: $Dest\ncn.exe"                                 # 결과

    # ── 2) 사용자 PATH ──
    Step '2/5 사용자 PATH'                                        # 단계 제목
    if (Add-UserPath $PathEntry $Dest) {                         # 추가했으면
        Send-EnvChange                                           # 실행 중인 프로그램에 알림
        Say "사용자 PATH 에 $PathEntry 를 추가했습니다(새로 여는 명령 창·Visual Studio 부터 적용)."   # 안내
    } else {                                                     # 이미 있음
        Say '사용자 PATH 에 이미 있습니다(변경 없음).'               # 안내
    }
    $env:Path = "$Dest;$env:Path"                                # 이 창에서도 바로 쓰게(점검용)

    # ── 3) 설정 파일 ──
    Step '3/5 설정 파일'                                          # 단계 제목
    $cdir = if ($env:CONTINUE_GLOBAL_DIR) { $env:CONTINUE_GLOBAL_DIR } else { Join-Path $env:USERPROFILE '.continue' }   # 전역 폴더(ncn·Continue 공용)
    New-Item -ItemType Directory -Force -Path $cdir | Out-Null   # 없으면 만듦
    $cfg = Join-Path $cdir 'config.yaml'                         # 설정 파일
    if (Test-Path -LiteralPath $cfg) {                           # 이미 있으면(Continue 사용자 등)
        Say "기존 설정을 그대로 씁니다: $cfg"                       # 건드리지 않음
    } else {                                                     # 없을 때만 템플릿 복사
        $server = $env:NCN_SERVER                                # 배포 도구가 준 주소
        if (-not $server -and -not $Quiet) { $server = Read-Host '코드에이전트 서버 주소(예: http://10.1.2.3:8200, 그냥 Enter = http://localhost:8200)' }   # 묻기
        if (-not $server) { $server = 'http://localhost:8200' }  # 기본값
        $server = $server.Trim().TrimEnd('/')                    # 끝 / 제거
        if ($server -match '/v1$') { $server = $server.Substring(0, $server.Length - 3) }   # /v1 까지 적었으면 뗌(템플릿이 /v1·/mcp 를 붙임)
        if ($server -notmatch '^https?://') { $server = "http://$server" }   # http:// 빠졌으면 붙임
        $text = [IO.File]::ReadAllText((Join-Path $Src 'config.template.yaml'), [Text.Encoding]::UTF8)   # 템플릿(BOM 있어도 됨)
        $text = $text.Replace('http://localhost:8200', $server)  # 서버 주소 바꾸기
        [IO.File]::WriteAllText($cfg, $text, $Utf8NoBom)         # BOM 없이 저장
        Say "설정을 만들었습니다: $cfg (서버 $server)"               # 결과
    }

    # ── 4) 개인 토큰 ──
    Step '4/5 개인 토큰'                                          # 단계 제목
    $envFile = Join-Path $cdir '.env'                            # 토큰 파일(~/.continue/.env)
    $lines = @()                                                 # 기존 줄
    if (Test-Path -LiteralPath $envFile) { $lines = @([IO.File]::ReadAllLines($envFile, [Text.Encoding]::UTF8)) }   # 읽기
    $pattern = '^\s*(export\s+)?' + $TokenName + '\s*='          # 토큰 줄 찾는 식
    $has = @($lines | Where-Object { $_ -match ($pattern + '\s*\S') }).Count -gt 0   # 값이 있는 토큰 줄이 있나
    $token = $env:NCN_TOKEN                                      # 배포 도구가 준 토큰
    if (-not $token -and -not $Quiet) {                          # 묻기
        $q = if ($has) { "개인 토큰($TokenName, 그냥 Enter = 기존 값 유지)" } else { "개인 토큰($TokenName, ca-로 시작, 관리자에게 받은 값)" }   # 질문
        $token = Read-Secret $q                                  # 보이지 않게 입력
    }
    if ($token) {                                                # 새 값이 있으면
        $token = $token.Trim()                                   # 앞뒤 공백 제거
        $keep = @($lines | Where-Object { $_ -notmatch $pattern })   # 기존 토큰 줄 뺀 나머지
        [IO.File]::WriteAllLines($envFile, [string[]](@($keep) + "$TokenName=$token"), $Utf8NoBom)   # 저장(BOM 없이)
        Say "토큰을 저장했습니다: $envFile"                          # 결과(값은 출력하지 않음)
    } elseif ($has) {                                            # 기존 값 유지
        Say "기존 토큰을 그대로 씁니다: $envFile"                    # 안내
    } else {                                                     # 없음
        Say "토큰이 없습니다. 나중에 $envFile 에 $TokenName=ca-... 한 줄을 넣으세요."   # 안내
    }

    # ── 5) 점검 ──
    Step '5/5 점검(ncn --doctor)'                                 # 단계 제목
    & (Join-Path $Dest 'ncn.exe') --doctor                       # 점검 실행(표 출력)
    $doc = $LASTEXITCODE                                         # 점검 결과
    Say ''                                                       # 빈 줄
    if ($doc -eq 0) { Say '설치·점검 완료. 새 명령 창에서 ncn --version 으로 확인하세요.' }   # 성공
    else { Say '설치는 끝났지만 점검에 실패한 항목이 있습니다. 위 "할 일"을 확인하고, 해결 후 ncn --doctor 를 다시 실행하세요.' }   # 점검 실패
    exit 0                                                       # 설치 자체는 성공
} catch {
    Write-Host ''                                                # 빈 줄
    Write-Host "설치 실패: $($_.Exception.Message)" -ForegroundColor Red   # 오류 글
    Write-Host 'README.txt 의 "문제 해결"을 보거나, 이 화면 내용을 관리자에게 전달하세요.'   # 안내
    exit 1                                                       # 실패
}
