// vs/NcnVs/Ide/VsContext.cs — VS 맥락 읽기: 솔루션 폴더, 현재 문서·선택 영역(첨부용), 확장 폴더의 ncn.exe, 개발자 환경변수 (계획 14-2 V7·V8)
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 사전
using System.Diagnostics;                                           // Process(devenv 경로)
using System.IO;                                                    // Path
using System.Runtime.InteropServices;                               // RuntimeInformation
using EnvDTE;                                                       // TextSelection
using EnvDTE80;                                                     // DTE2
using Microsoft.VisualStudio.Shell;                                 // ThreadHelper
using Microsoft.VisualStudio.Shell.Interop;                         // IVsSolution
using NcnVs.Core.Host;                                              // DevEnvironment·NcnLaunchOptions
using Task = System.Threading.Tasks.Task;                           // Task 이름 충돌 방지

namespace NcnVs.Ide                                                 // IDE 연동 이름공간
{
    /// <summary>현재 문서 정보(첨부 블록 재료).</summary>
    public sealed class ActiveDocumentInfo                          // 현재 문서
    {
        public string Path { get; set; }                            // 전체 경로
        public string SelectedText { get; set; }                    // 선택 글(없으면 빈 글)
        public int StartLine { get; set; }                          // 선택 시작 줄(1부터)
        public int EndLine { get; set; }                            // 선택 끝 줄(1부터)
    }

    /// <summary>UI 스레드에서 부르는 VS 맥락 함수들.</summary>
    public static class VsContext                                   // VS 맥락
    {
        /// <summary>솔루션 폴더(폴더 열기 모드면 그 폴더). 없으면 사용자 폴더.</summary>
        public static async System.Threading.Tasks.Task<string> GetSolutionDirectoryAsync(AsyncPackage package)   // 솔루션 폴더
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드
            var sol = await package.GetServiceAsync(typeof(SVsSolution)) as IVsSolution;   // 솔루션 서비스
            if (sol != null)                                        // 있으면
            {
                string dir, file, user;                             // 결과들
                if (sol.GetSolutionInfo(out dir, out file, out user) == 0 && !string.IsNullOrEmpty(dir) && Directory.Exists(dir))   // S_OK + 폴더 있음
                    return dir.TrimEnd('\\');                       // 끝 \ 빼기
            }
            return Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);   // 솔루션 없음 → 사용자 폴더
        }

        /// <summary>현재 문서와 선택 영역(문서가 없으면 null). 선택이 다음 줄 첫 칸에서 끝나면 그 줄은 뺀다.</summary>
        public static async System.Threading.Tasks.Task<ActiveDocumentInfo> GetActiveDocumentAsync(AsyncPackage package)   // 현재 문서
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드(DTE)
            try
            {
                var dte = await package.GetServiceAsync(typeof(DTE)) as DTE2;   // 자동화 개체
                var doc = dte != null ? dte.ActiveDocument : null;  // 마지막 활성 문서(도구 창에 초점이 있어도 유지)
                if (doc == null || string.IsNullOrEmpty(doc.FullName)) return null;   // 문서 없음
                var info = new ActiveDocumentInfo { Path = doc.FullName, SelectedText = "" };   // 경로
                var sel = doc.Selection as TextSelection;           // 글 선택(디자이너 등은 null)
                if (sel != null && !sel.IsEmpty)                    // 선택 있음
                {
                    info.SelectedText = sel.Text ?? "";             // 선택 글
                    var top = sel.TopPoint.Line;                    // 시작 줄
                    var bottom = sel.BottomPoint.Line;              // 끝 줄
                    if (bottom > top && sel.BottomPoint.LineCharOffset == 1) bottom--;   // 다음 줄 첫 칸에서 끝난 줄 선택
                    info.StartLine = top;                           // 시작
                    info.EndLine = bottom;                          // 끝
                }
                return info;                                        // 결과
            }
            catch (Exception)                                       // DTE 오류(특수 문서)
            {
                return null;                                        // 첨부 안 함
            }
        }

        /// <summary>이 확장(VSIX)이 설치된 폴더.</summary>
        public static string ExtensionDirectory                     // 확장 폴더
        {
            get { return System.IO.Path.GetDirectoryName(typeof(VsContext).Assembly.Location); }   // NcnVs.dll 폴더
        }

        /// <summary>쓸 ncn.exe: 옵션 값이 있으면 그것, 없으면 VSIX 안 ncn\ncn.exe.</summary>
        public static string ResolveNcnExe(string optionPath)       // ncn 경로
        {
            if (!string.IsNullOrWhiteSpace(optionPath)) return Environment.ExpandEnvironmentVariables(optionPath.Trim().Trim('"'));   // 사용자 지정(%변수% 허용)
            return System.IO.Path.Combine(ExtensionDirectory, "ncn", "ncn.exe");   // 내장
        }

        /// <summary>VS 개발자 환경변수(devenv 위치·DTE 버전·ARM64 여부로 만든다).</summary>
        public static async System.Threading.Tasks.Task<Dictionary<string, string>> GetDeveloperEnvironmentAsync(AsyncPackage package)   // 개발자 환경
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드(DTE)
            string version = null;                                  // VS 버전
            try
            {
                var dte = await package.GetServiceAsync(typeof(DTE)) as DTE2;   // 자동화 개체
                version = dte != null ? dte.Version : null;         // "17.0" / "18.0"
            }
            catch { }                                               // 몰라도 됨
            string devenv;                                          // devenv.exe 경로
            using (var self = System.Diagnostics.Process.GetCurrentProcess()) devenv = self.MainModule.FileName;   // 지금 프로세스 = devenv
            var install = DevEnvironment.InstallDirFromDevenv(devenv);   // 설치 폴더
            var arm64 = RuntimeInformation.ProcessArchitecture == Architecture.Arm64;   // ARM64 VS 인지
            return DevEnvironment.Build(install, version, arm64, Environment.GetEnvironmentVariable("PATH"));   // 사전
        }

        /// <summary>옵션 + 맥락 → ncn 실행 설정.</summary>
        public static async System.Threading.Tasks.Task<NcnLaunchOptions> BuildLaunchOptionsAsync(NcnPackage package, string cwd)   // 실행 설정
        {
            var o = package.Options;                                // 옵션
            var launch = new NcnLaunchOptions                       // 설정
            {
                ExePath = ResolveNcnExe(o.NcnExePath),              // ncn.exe
                WorkingDirectory = cwd,                             // 솔루션 폴더
                ConfigPath = string.IsNullOrWhiteSpace(o.ConfigPath) ? null : Environment.ExpandEnvironmentVariables(o.ConfigPath.Trim().Trim('"')),   // 설정 파일
                Verbose = o.Verbose,                                // 자세한 로그
                ExtraArgs = CommandLine.Split(o.ExtraArguments),    // 추가 인자
            };
            if (o.PassDeveloperEnvironment)                         // 개발자 환경 전달
            {
                try
                {
                    foreach (var kv in await GetDeveloperEnvironmentAsync(package)) launch.Environment[kv.Key] = kv.Value;   // 넣기
                }
                catch (Exception e)                                 // 실패해도 실행은 함
                {
                    package.Log.Write("개발자 환경을 만들지 못함: " + e.Message);   // 로그
                }
            }
            return launch;                                          // 결과
        }
    }
}
