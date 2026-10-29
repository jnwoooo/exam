# uninstall.ps1 — ncn 제거(관리자 권한 불필요). uninstall.cmd 가 이 파일 내용을 읽어 실행한다(계획 8-3)
# 하는 일: 사용자 PATH 에서 %LOCALAPPDATA%\ncn 항목만 빼기 → NCN_HOME 변수 지우기 → 설치 폴더 지우기
# 남기는 것: %USERPROFILE%\.continue(설정·토큰·대화 기록 — Continue 와 공용이므로)
# 입력(환경변수): NCN_QUIET=1 이면 확인 질문 없이 진행
# 종료 코드: 0 성공 · 1 실패 · 3 ncn 실행 중
$ErrorActionPreference = 'Stop'                                  # 오류가 나면 바로 catch 로
$Dest = Join-Path $env:LOCALAPPDATA 'ncn'                        # 설치 폴더
$Quiet = ($env:NCN_QUIET -eq '1')                                # 묻지 않는 모드

function Send-EnvChange {                                        # PATH 변경을 실행 중인 프로그램에 알림(install.ps1 과 같음)
    try {                                                        # Add-Type 이 막힌 PC 는 알림 없이(로그오프 후 적용)
        if (-not ('Ncn.Win32' -as [type])) {                     # 이미 정의했으면 건너뜀
            Add-Type -Namespace Ncn -Name Win32 -MemberDefinition '[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'   # Win32 함수 선언
        }
        $r = [UIntPtr]::Zero                                     # 결과 자리
        [void][Ncn.Win32]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r)   # WM_SETTINGCHANGE 방송
    } catch { }                                                  # 실패는 무시
}

function Normalize-PathItem([string]$p) {                        # PATH 항목 비교용 정규화(install.ps1 과 같음)
    ([Environment]::ExpandEnvironmentVariables($p.Trim().Trim('"'))).TrimEnd('\').ToLowerInvariant()   # 변수 풀고·끝 \ 제거·소문자
}

try {
    if (-not $Quiet) {                                           # 확인 질문
        $a = Read-Host "ncn 을 제거합니다($Dest). 설정(.continue)은 남깁니다. 계속할까요? [y/N]"   # 묻기
        if ($a -notmatch '^(y|yes|예|ㅇ)$') { Write-Host '취소했습니다.'; exit 0 }   # 아니면 끝
    }
    $running = @(Get-Process -Name ncn -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dest, [StringComparison]::OrdinalIgnoreCase) })   # 실행 중인 설치본
    if ($running.Count -gt 0) {                                  # 실행 중이면 지울 수 없음
        Write-Host "ncn 이 실행 중입니다(PID $($running.Id -join ', ')). Visual Studio·명령 창의 ncn 을 끈 뒤 다시 실행하세요."   # 안내
        exit 3                                                   # 종료 코드 3
    }

    # ── 사용자 PATH 에서 빼기(다른 항목·원문 %VAR% 는 그대로) ──
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)   # HKCU\Environment(쓰기)
    $changed = $false                                            # 바꿨는지
    if ($null -ne $key) {                                        # 키가 있으면
        try {
            $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)   # 풀지 않은 원문
            $want = Normalize-PathItem $Dest                     # 뺄 항목(실제 경로)
            $items = @($raw -split ';' | Where-Object { $_.Trim() -ne '' })   # 빈 항목 제외
            $keep = @($items | Where-Object { (Normalize-PathItem $_) -ne $want })   # 남길 항목
            if ($keep.Count -ne $items.Count) {                  # 뺀 것이 있으면
                $key.SetValue('Path', ($keep -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)   # REG_EXPAND_SZ 로 다시 씀
                $changed = $true                                 # 표시
            }
            if ($null -ne $key.GetValue('NCN_HOME', $null)) { $key.DeleteValue('NCN_HOME'); $changed = $true }   # install 의 알림용 변수 지우기
        } finally {
            $key.Close()                                         # 닫기
        }
    }
    if ($changed) { Send-EnvChange; Write-Host '사용자 PATH 에서 ncn 을 뺐습니다.' } else { Write-Host '사용자 PATH 에 ncn 이 없습니다.' }   # 결과

    # ── 설치 폴더 지우기 ──
    if (Test-Path -LiteralPath $Dest) {                          # 있으면
        Remove-Item -LiteralPath $Dest -Recurse -Force           # 폴더째 삭제(이 스크립트는 이미 메모리에 읽혀 있음)
        Write-Host "삭제했습니다: $Dest"                            # 결과
    } else {                                                     # 없으면
        Write-Host "설치 폴더가 없습니다: $Dest"                     # 안내
    }
    Write-Host "설정·토큰은 남겨 두었습니다: $env:USERPROFILE\.continue (필요 없으면 직접 지우세요)"   # 안내
    exit 0                                                       # 성공
} catch {
    Write-Host "제거 실패: $($_.Exception.Message)" -ForegroundColor Red   # 오류 글
    exit 1                                                       # 실패
}
