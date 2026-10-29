// vs/NcnVs/NcnToolWindow.cs — ncn 도구 창(ToolWindowPane). 닫기(X)는 숨김이라 세션이 유지되고, VS 종료 때 ncn 을 끝낸다
using System;                                                       // 기본 형식
using System.Runtime.InteropServices;                               // Guid
using Microsoft.VisualStudio.Shell;                                 // ToolWindowPane
using NcnVs.UI;                                                     // 화면

namespace NcnVs                                                     // 확장 이름공간
{
    /// <summary>도구 창 틀. 화면은 NcnToolWindowControl(순수 WPF).</summary>
    [Guid("f2453397-1316-407d-8980-66db94a622c8")]                  // 도구 창 GUID
    public class NcnToolWindow : ToolWindowPane                     // 도구 창
    {
        private readonly NcnToolWindowControl _control;             // 화면

        public NcnToolWindow() : base(null)                         // 생성자(VS 가 부름)
        {
            Caption = "ncn";                                        // 창 제목
            _control = new NcnToolWindowControl();                  // 화면 만들기
            Content = _control;                                     // 넣기
        }

        protected override void Dispose(bool disposing)             // 정리(VS 종료·창 파괴)
        {
            if (disposing)                                          // 관리 자원
            {
                try { _control.Shutdown(); } catch (Exception) { }  // ncn 종료(stdin 닫기)
            }
            base.Dispose(disposing);                                // 기본 정리
        }
    }
}
