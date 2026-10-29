// vs/NcnVs/Ide/OutputLog.cs — 로그: 출력 창 "ncn" 창 + 파일(%LOCALAPPDATA%\ncn-vs\logs\ncn-vs-날짜.log). ncn stderr 도 여기로 모은다
using System;                                                       // 기본 형식
using System.IO;                                                    // 파일
using System.Text;                                                  // UTF-8
using Microsoft.VisualStudio.Shell;                                 // AsyncPackage·ThreadHelper
using Microsoft.VisualStudio.Shell.Interop;                         // IVsOutputWindow
using Task = System.Threading.Tasks.Task;                           // Task 이름 충돌 방지

namespace NcnVs.Ide                                                 // IDE 연동 이름공간
{
    /// <summary>스레드 안전 로그(어느 스레드에서 불러도 됨).</summary>
    public sealed class OutputLog : IDisposable                     // 로그
    {
        private static readonly Guid PaneGuid = new Guid("f2be56f8-be4b-473e-b5a3-d9a3baece74a");   // 출력 창 칸 GUID
        private readonly object _lock = new object();               // 파일 잠금
        private StreamWriter _file;                                 // 로그 파일
        private IVsOutputWindowPane _pane;                          // 출력 창 칸

        public string FilePath { get; }                             // 로그 파일 경로
        public string Directory { get; }                            // 로그 폴더

        public OutputLog()                                          // 생성자: 파일 열기
        {
            Directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ncn-vs", "logs");   // 폴더
            FilePath = Path.Combine(Directory, "ncn-vs-" + DateTime.Now.ToString("yyyyMMdd") + ".log");   // 날짜별 파일
            try
            {
                System.IO.Directory.CreateDirectory(Directory);     // 폴더 만들기
                var fs = new FileStream(FilePath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);   // 이어 쓰기(VS 여러 개 허용)
                _file = new StreamWriter(fs, new UTF8Encoding(false)) { AutoFlush = true };   // 바로 기록
            }
            catch
            {
                _file = null;                                       // 파일을 못 열면 출력 창만
            }
        }

        /// <summary>출력 창 "ncn" 칸 만들기(UI 스레드).</summary>
        public async Task AttachOutputWindowAsync(AsyncPackage package)   // 출력 창 붙이기
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드
            var ow = await package.GetServiceAsync(typeof(SVsOutputWindow)) as IVsOutputWindow;   // 출력 창 서비스
            if (ow == null) return;                                 // 없으면 그만
            var guid = PaneGuid;                                    // ref 인자용
            ow.CreatePane(ref guid, "ncn", 1, 1);                   // 칸 만들기(보임·닫을 때 지움)
            IVsOutputWindowPane pane;                               // 칸
            ow.GetPane(ref guid, out pane);                         // 칸 얻기
            _pane = pane;                                           // 보관
        }

        /// <summary>한 줄 기록(시각 붙임).</summary>
        public void Write(string message)                           // 기록
        {
            var line = DateTime.Now.ToString("HH:mm:ss.fff") + " " + message;   // 시각 + 글
            lock (_lock)                                            // 파일 잠금
            {
                try { _file?.WriteLine(line); } catch { }           // 파일
            }
            #pragma warning disable VSTHRD010 // OutputStringThreadSafe 는 문서상 어느 스레드에서나 부를 수 있는 함수(로그는 백그라운드 스레드에서도 쓴다)
            try { _pane?.OutputStringThreadSafe(line + Environment.NewLine); } catch { }   // 출력 창(스레드 안전 함수)
            #pragma warning restore VSTHRD010
        }

        public void Dispose()                                       // 정리
        {
            lock (_lock)                                            // 파일 잠금
            {
                try { _file?.Dispose(); } catch { }                 // 닫기
                _file = null;                                       // 비움
            }
        }
    }
}
