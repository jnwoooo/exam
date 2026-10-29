// vs/NcnVs/Ide/VsDiffPresenter.cs — 승인 diff 를 VS 내장 비교 창(IVsDifferenceService)으로 보이기: 임시 파일 두 개(현재 ↔ 제안) (계획 14-2 V4)
using System;                                                       // 기본 형식
using System.IO;                                                    // 임시 파일
using System.Text;                                                  // UTF-8
using Microsoft.VisualStudio.Shell;                                 // ThreadHelper·AsyncPackage
using Microsoft.VisualStudio.Shell.Interop;                         // IVsDifferenceService
using Task = System.Threading.Tasks.Task;                           // Task 이름 충돌 방지

namespace NcnVs.Ide                                                 // IDE 연동 이름공간
{
    /// <summary>비교 창 열기. 임시 파일은 %TEMP%\ncn-vs\diff\ 아래, 확장이 시작할 때 하루 지난 것을 지운다.</summary>
    public sealed class VsDiffPresenter                             // 비교 창
    {
        private readonly AsyncPackage _package;                     // 서비스 제공자
        private readonly OutputLog _log;                            // 로그
        private static int _counter;                                // 파일 이름 번호
        private static readonly string TempDir = Path.Combine(Path.GetTempPath(), "ncn-vs", "diff");   // 임시 폴더

        public VsDiffPresenter(AsyncPackage package, OutputLog log) // 생성자
        {
            _package = package;                                     // 보관
            _log = log;                                             // 보관
            CleanupOld();                                           // 옛 임시 파일 정리
        }

        private static void CleanupOld()                            // 하루 지난 임시 파일 지우기
        {
            try
            {
                if (!Directory.Exists(TempDir)) return;             // 폴더 없음
                foreach (var f in Directory.GetFiles(TempDir))      // 파일마다
                    if (File.GetLastWriteTimeUtc(f) < DateTime.UtcNow.AddDays(-1)) { try { File.Delete(f); } catch { } }   // 오래된 것
            }
            catch { }                                               // 정리 실패는 무시
        }

        /// <summary>
        /// path = 원래 파일(확장자로 색칠), oldText = 현재(새 파일이면 null → 빈 글), newText = 제안. title = 도구 제목.
        /// </summary>
        public async Task ShowAsync(string path, string oldText, string newText, string title)   // 비교 창 열기
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드
            try
            {
                Directory.CreateDirectory(TempDir);                 // 임시 폴더
                var name = Path.GetFileName(path ?? "file.txt");    // 원래 이름(확장자 유지 → 구문 색)
                var n = System.Threading.Interlocked.Increment(ref _counter);   // 번호
                var stamp = DateTime.Now.ToString("HHmmss") + "_" + n;   // 겹치지 않게
                var left = Path.Combine(TempDir, stamp + "_before_" + name);  // 왼쪽(현재) — 파일 이름은 ASCII 로
                var right = Path.Combine(TempDir, stamp + "_after_" + name);   // 오른쪽(제안)
                var utf8 = new UTF8Encoding(true);                  // BOM 있는 UTF-8(VS 가 한글을 바로 인식)
                File.WriteAllText(left, oldText ?? "", utf8);       // 현재 글
                File.WriteAllText(right, newText ?? "", utf8);      // 제안 글
                var diff = await _package.GetServiceAsync(typeof(SVsDifferenceService)) as IVsDifferenceService;   // 비교 서비스
                if (diff == null) { _log.Write("IVsDifferenceService 없음 — 비교 창을 열 수 없음"); return; }   // 없음
                var options = (uint)(__VSDIFFSERVICEOPTIONS.VSDIFFOPT_LeftFileIsTemporary | __VSDIFFSERVICEOPTIONS.VSDIFFOPT_RightFileIsTemporary);   // 둘 다 임시 파일
                var caption = "ncn: " + (title ?? name);            // 창 제목
                var frame = diff.OpenComparisonWindow2(left, right, caption, path ?? name,   // 왼쪽·오른쪽·제목·툴팁
                    "현재: " + name, "제안: " + name, caption, null, options);   // 왼쪽 이름·오른쪽 이름·한 창 이름·역할·옵션
                if (frame != null) frame.Show();                    // 보이기
            }
            catch (Exception e)                                     // 실패해도 승인은 화면 버튼으로 가능
            {
                _log.Write("비교 창 열기 실패: " + e.Message);       // 로그
            }
        }
    }
}
