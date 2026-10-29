// vs/NcnVs/Ide/VsIdeBridge.cs — fs 위임 구현: 열린 문서는 RDT → ITextBuffer 에서 읽고·편집 후 저장(R1·R2), 아니면 디스크 (계획 14-2 V3)
using System;                                                       // 기본 형식
using System.IO;                                                    // Path·FileNotFoundException
using Microsoft.VisualStudio;                                       // VSConstants
using Microsoft.VisualStudio.ComponentModelHost;                    // IComponentModel
using Microsoft.VisualStudio.Editor;                                // IVsEditorAdaptersFactoryService
using Microsoft.VisualStudio.Shell;                                 // RunningDocumentTable·ThreadHelper
using Microsoft.VisualStudio.Shell.Interop;                         // IVsPersistDocData
using Microsoft.VisualStudio.Text;                                  // ITextBuffer
using Microsoft.VisualStudio.TextManager.Interop;                   // IVsTextBuffer·IVsTextLines
using NcnVs.Core.State;                                             // IIdeBridge
using NcnVs.Core.Text;                                              // MinimalEdit·TextFileIo
using Task = System.Threading.Tasks.Task;                           // Task 이름 충돌 방지

namespace NcnVs.Ide                                                 // IDE 연동 이름공간
{
    /// <summary>VS 파일 창구. 버퍼 접근은 UI 스레드, 디스크는 백그라운드.</summary>
    public sealed class VsIdeBridge : IIdeBridge                    // VS 창구
    {
        private readonly AsyncPackage _package;                     // 서비스 제공자
        private readonly OutputLog _log;                            // 로그
        private IVsEditorAdaptersFactoryService _adapters;          // IVsTextBuffer → ITextBuffer 변환

        public VsIdeBridge(AsyncPackage package, OutputLog log)     // 생성자
        {
            _package = package;                                     // 보관
            _log = log;                                             // 보관
        }

        /// <summary>열린 문서의 편집기 버퍼(없으면 null). UI 스레드에서.</summary>
        private async System.Threading.Tasks.Task<OpenBuffer> FindOpenBufferAsync(string path)   // 열린 버퍼 찾기
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드
            if (_adapters == null)                                  // 처음이면
            {
                var cm = await _package.GetServiceAsync(typeof(SComponentModel)) as IComponentModel;   // MEF 구성 요소
                _adapters = cm != null ? cm.GetService<IVsEditorAdaptersFactoryService>() : null;   // 어댑터 서비스
            }
            if (_adapters == null) return null;                     // 없으면 디스크로
            var rdt = new RunningDocumentTable(_package);           // 열린 문서 표(RDT)
            var docData = rdt.FindDocument(path);                   // 경로로 찾기(대소문자 무시)
            if (docData == null) return null;                       // 열려 있지 않음
            var vsBuffer = docData as IVsTextBuffer;                // 글 버퍼?
            if (vsBuffer == null)                                   // 아니면 공급자?(일부 편집기)
            {
                var provider = docData as IVsTextBufferProvider;    // 버퍼 공급자
                IVsTextLines lines;                                 // 글 줄
                if (provider != null && provider.GetTextBuffer(out lines) == VSConstants.S_OK) vsBuffer = lines;   // 얻기
            }
            if (vsBuffer == null) return null;                      // 글 문서가 아님(디자이너 등) → 디스크
            var buffer = _adapters.GetDocumentBuffer(vsBuffer);     // 편집기 버퍼
            if (buffer == null) return null;                        // 변환 실패 → 디스크
            return new OpenBuffer { Buffer = buffer, DocData = docData };   // 결과
        }

        private sealed class OpenBuffer                             // 찾은 버퍼
        {
            public ITextBuffer Buffer;                              // 편집기 버퍼
            public object DocData;                                  // 문서 자료(저장용)
        }

        private static string Normalize(string path)                // 경로 정규화
        {
            return Path.GetFullPath(path);                          // 절대·구분자 정리(ncn 은 절대 경로를 보냄)
        }

        /// <summary>R2: 열린 문서면 버퍼(저장 안 한 변경 포함), 아니면 디스크. 없으면 FileNotFoundException.</summary>
        public async System.Threading.Tasks.Task<string> ReadTextFileAsync(string path)   // 읽기
        {
            var full = Normalize(path);                             // 경로
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // 이 메서드의 나머지를 UI 스레드에서(버퍼는 UI 스레드 전용)
            var open = await FindOpenBufferAsync(full);             // 열린 버퍼
            if (open != null)                                       // 열려 있음
            {
                _log.Write("fs/read_text_file(버퍼): " + full);     // 로그
                return open.Buffer.CurrentSnapshot.GetText();       // 지금 글
            }
            return await System.Threading.Tasks.Task.Run(() =>      // 디스크는 스레드 풀에서
            {
                if (!File.Exists(full)) throw new FileNotFoundException("파일이 없습니다.", full);   // 없음 → -32002
                return TextFileIo.ReadAllText(full);                // 디스크(인코딩 감지)
            });
        }

        /// <summary>R1: 열린 문서면 버퍼를 최소 구간만 편집 → 문서 저장 → 그 뒤 반환. 아니면 디스크(인코딩 유지).</summary>
        public async Task WriteTextFileAsync(string path, string content)   // 쓰기
        {
            var full = Normalize(path);                             // 경로
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // 이 메서드의 나머지를 UI 스레드에서(편집·저장은 UI 스레드 전용)
            var open = await FindOpenBufferAsync(full);             // 열린 버퍼
            if (open != null)                                       // 열려 있음
            {
                var snapshot = open.Buffer.CurrentSnapshot;         // 지금 글
                var edit = MinimalEdit.Compute(snapshot.GetText(), content);   // 바뀐 구간
                if (edit != null)                                   // 바뀐 것 있음
                {
                    using (var e = open.Buffer.CreateEdit())        // 편집 묶음(Ctrl+Z 한 번)
                    {
                        e.Replace(edit.Start, edit.Length, edit.NewText);   // 구간 바꾸기
                        e.Apply();                                  // 적용
                    }
                }
                SaveDocument(open.DocData, full);                   // 저장(디스크 반영 — 같은 턴의 빌드가 새 내용을 봄)
                _log.Write("fs/write_text_file(버퍼 편집 + 저장): " + full);   // 로그
                return;                                             // 응답
            }
            await System.Threading.Tasks.Task.Run(() => TextFileIo.WriteAllText(full, content));   // 디스크(스레드 풀, 인코딩 유지)
            _log.Write("fs/write_text_file(디스크): " + full);       // 로그
        }

        private void SaveDocument(object docData, string full)      // 문서 저장(UI 스레드)
        {
            ThreadHelper.ThrowIfNotOnUIThread();                    // UI 스레드 확인
            var persist = docData as IVsPersistDocData;             // 저장 인터페이스
            if (persist != null)                                    // 있으면
            {
                string newMoniker;                                  // 새 이름(다른 이름 저장 아님 → 같음)
                int cancelled;                                      // 취소 여부
                var hr = persist.SaveDocData(VSSAVEFLAGS.VSSAVE_SilentSave, out newMoniker, out cancelled);   // 묻지 않고 저장
                if (ErrorHandler.Failed(hr) || cancelled != 0)      // 실패·취소
                    throw new IOException("문서를 저장하지 못했습니다(hr=0x" + hr.ToString("X8") + "): " + full);   // 오류 응답 → ncn 이 디스크에 씀
                return;                                             // 끝
            }
            new RunningDocumentTable(_package).SaveFileIfDirty(full);   // 대신 RDT 로 저장
        }
    }
}
