// vs/NcnVs/NcnPackage.cs — VSSDK AsyncPackage: 도구 창·메뉴 명령·옵션 페이지 등록, 공용 서비스(로그·Job Object) 보관 (계획 14-2 V9)
using System;                                                       // 기본 형식
using System.ComponentModel.Design;                                 // CommandID·MenuCommand
using System.Runtime.InteropServices;                               // Guid
using System.Threading;                                             // CancellationToken
using Microsoft.VisualStudio.Shell;                                 // AsyncPackage·특성
using NcnVs.Core.Host;                                              // Job Object
using NcnVs.Ide;                                                    // 로그
using Task = System.Threading.Tasks.Task;                           // Task 이름 충돌 방지(Microsoft.VisualStudio.Shell.Task)

namespace NcnVs                                                     // 확장 이름공간
{
    /// <summary>
    /// 패키지. 백그라운드 로드(AllowsBackgroundLoading) 후 도구 창을 열 때 초기화된다.
    /// 메뉴: 보기 → 다른 창 → ncn, 도구 → ncn 에이전트 열기.
    /// </summary>
    [PackageRegistration(UseManagedResourcesOnly = true, AllowsBackgroundLoading = true)]   // 관리 리소스·비동기 로드
    [InstalledProductRegistration("ncn", "ncn — Continue 호환 에이전트(ACP)", "0.1.0")]       // 도움말 → 정보 표시
    [ProvideMenuResource("Menus.ctmenu", 1)]                        // VSCT 메뉴(NcnVsPackage.vsct)
    [ProvideToolWindow(typeof(NcnToolWindow), Style = VsDockStyle.Tabbed, Window = "3ae79031-e1bc-11d0-8f78-00a0c9110057")]   // 솔루션 탐색기 옆 탭
    [ProvideOptionPage(typeof(NcnOptionsPage), "ncn", "일반", 0, 0, true)]   // 도구 → 옵션 → ncn → 일반
    [Guid(PackageGuidString)]                                       // 패키지 GUID
    public sealed class NcnPackage : AsyncPackage                   // 패키지
    {
        public const string PackageGuidString = "df57f35a-6e02-467f-bc82-4b361c36f3a9";   // 패키지 GUID(vsct 와 같음)
        public static readonly Guid CommandSet = new Guid("9719a81e-c84e-4dd5-a7ca-adc90464212f");   // 명령 집합 GUID(vsct 와 같음)
        public const int ShowWindowViewCmdId = 0x0100;              // 보기 → 다른 창 → ncn
        public const int ShowWindowToolsCmdId = 0x0101;             // 도구 → ncn 에이전트 열기

        /// <summary>로드된 패키지(도구 창이 서비스를 얻을 때 씀).</summary>
        public static NcnPackage Instance { get; private set; }     // 현재 패키지

        /// <summary>로그(출력 창 "ncn" + 파일).</summary>
        public OutputLog Log { get; private set; }                  // 로그

        /// <summary>ncn 프로세스들을 VS 와 함께 끝낼 Job Object(패키지 수명 = VS 수명).</summary>
        public IProcessLifetimeGuard Lifetime { get; private set; } // 수명 묶개

        protected override async Task InitializeAsync(CancellationToken cancellationToken, IProgress<ServiceProgressData> progress)   // 초기화
        {
            Instance = this;                                        // 보관
            Log = new OutputLog();                                  // 파일 로그 먼저(출력 창은 UI 스레드에서)
            Lifetime = WindowsJobObject.CreateForCurrentOs(Log.Write);   // Job Object
            await JoinableTaskFactory.SwitchToMainThreadAsync(cancellationToken);   // 메뉴·출력 창은 UI 스레드
            await Log.AttachOutputWindowAsync(this);                // 출력 창 "ncn" 붙이기
            var mcs = await GetServiceAsync(typeof(IMenuCommandService)) as OleMenuCommandService;   // 메뉴 명령 서비스
            if (mcs != null)                                        // 있으면
            {
                mcs.AddCommand(new MenuCommand(OnShowToolWindow, new CommandID(CommandSet, ShowWindowViewCmdId)));   // 보기 메뉴
                mcs.AddCommand(new MenuCommand(OnShowToolWindow, new CommandID(CommandSet, ShowWindowToolsCmdId)));  // 도구 메뉴
            }
            Log.Write("ncn-vs 패키지 로드됨. 로그 파일: " + Log.FilePath);   // 시작 기록
        }

        private void OnShowToolWindow(object sender, EventArgs e)   // 메뉴 → 도구 창 열기
        {
            _ = JoinableTaskFactory.RunAsync(async () =>            // 비동기로(기다리지 않음)
            {
                try
                {
                    var window = await ShowToolWindowAsync(typeof(NcnToolWindow), 0, true, DisposalToken);   // 만들고 보이기
                    if (window == null || window.Frame == null) throw new NotSupportedException("ncn 도구 창을 만들지 못했습니다.");   // 실패
                }
                catch (Exception ex)                                // 오류
                {
                    Log?.Write("도구 창 열기 실패: " + ex);         // 로그
                }
            });
        }

        /// <summary>옵션 페이지 값.</summary>
        public NcnOptionsPage Options => (NcnOptionsPage)GetDialogPage(typeof(NcnOptionsPage));   // 옵션

        protected override void Dispose(bool disposing)             // 정리(VS 종료)
        {
            if (disposing)                                          // 관리 자원
            {
                try { Lifetime?.Dispose(); } catch { }              // Job 닫기 → 남은 ncn 종료
                try { Log?.Dispose(); } catch { }                   // 로그 닫기
            }
            base.Dispose(disposing);                                // 기본 정리
        }
    }
}
