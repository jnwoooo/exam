// vs/NcnVs/UI/MarkdownView.cs — 최소 마크다운 그리기(제목·목록·코드 블록·굵게·인라인 코드)와 VS 테마 도우미 — 순수 WPF(외부 런타임 없음, 계획 14-5 Q8)
using System.Collections.Generic;                                   // 목록
using System.Windows;                                               // FrameworkElement·Thickness
using System.Windows.Controls;                                      // TextBlock·TextBox·StackPanel
using System.Windows.Documents;                                     // Run
using System.Windows.Input;                                         // MouseWheelEventArgs
using System.Windows.Media;                                         // Brush·FontFamily
using Microsoft.VisualStudio.PlatformUI;                            // EnvironmentColors
using Microsoft.VisualStudio.Shell;                                 // VsResourceKeys
using NcnVs.Core.Text;                                              // Markdown 해석기

namespace NcnVs.UI                                                  // 화면 이름공간
{
    /// <summary>VS 테마(밝게·어둡게·파랑)를 따르는 도우미.</summary>
    internal static class Theme                                     // 테마
    {
        public static readonly FontFamily Mono = new FontFamily("Cascadia Mono, Consolas, D2Coding, Courier New");   // 고정폭 글꼴(앞에서부터 있는 것)
        public static readonly Brush CodeBackground = Frozen(new SolidColorBrush(Color.FromArgb(0x28, 0x80, 0x80, 0x80)));   // 코드 바탕(두 테마 모두 보이는 반투명 회색)
        public static readonly Brush UserBackground = Frozen(new SolidColorBrush(Color.FromArgb(0x22, 0x3C, 0x8C, 0xDC)));   // 사용자 글 바탕(옅은 파랑)
        public static readonly Brush AskBackground = Frozen(new SolidColorBrush(Color.FromArgb(0x26, 0xE0, 0xA0, 0x30)));    // 승인·질문 바탕(옅은 주황)
        public static readonly Brush Ok = Frozen(new SolidColorBrush(Color.FromRgb(0x3F, 0xA3, 0x4D)));   // 완료(초록)
        public static readonly Brush Fail = Frozen(new SolidColorBrush(Color.FromRgb(0xE0, 0x52, 0x52))); // 실패(빨강)
        public static readonly Brush Muted = Frozen(new SolidColorBrush(Color.FromRgb(0x90, 0x90, 0x90))); // 흐린 글(회색)

        private static Brush Frozen(SolidColorBrush b) { b.Freeze(); return b; }   // 얼려서 스레드 안전·빠르게

        public static T StyleButton<T>(T b) where T : FrameworkElement   // VS 버튼 모양
        {
            b.SetResourceReference(FrameworkElement.StyleProperty, VsResourceKeys.ButtonStyleKey);   // VS 버튼 스타일
            return b;                                               // 같은 개체
        }

        public static T StyleInput<T>(T t) where T : FrameworkElement    // VS 입력칸 모양
        {
            t.SetResourceReference(FrameworkElement.StyleProperty, VsResourceKeys.TextBoxStyleKey);   // VS 입력칸 스타일
            return t;                                               // 같은 개체
        }

        public static void ApplyBorder(Border b)                       // VS 테두리 색
        {
            b.SetResourceReference(System.Windows.Controls.Border.BorderBrushProperty, EnvironmentColors.ToolWindowBorderBrushKey);   // 도구 창 테두리 색
        }

        public static void ApplyText(Control c)                        // VS 글자 색(읽기 전용 TextBox 등)
        {
            c.SetResourceReference(Control.ForegroundProperty, EnvironmentColors.ToolWindowTextBrushKey);   // 도구 창 글자 색
        }

        /// <summary>안쪽 TextBox 가 마우스 휠을 먹지 않게(바깥 대화 목록이 스크롤되게) 넘긴다.</summary>
        public static void ForwardWheel(UIElement e)                // 휠 넘기기
        {
            e.PreviewMouseWheel += (s, a) =>                        // 휠
            {
                if (a.Handled) return;                              // 이미 처리됨
                a.Handled = true;                                   // 안쪽은 처리 안 함
                var parent = (s as FrameworkElement)?.Parent as UIElement;   // 부모
                if (parent == null) return;                         // 없으면 그만
                parent.RaiseEvent(new MouseWheelEventArgs(a.MouseDevice, a.Timestamp, a.Delta) { RoutedEvent = UIElement.MouseWheelEvent, Source = s });   // 부모로 다시 올림
            };
        }

        /// <summary>읽기 전용·선택 가능한 고정폭 글 상자(코드·도구 결과).</summary>
        public static TextBox ReadOnlyCode(string text, bool wrap, double maxHeight = double.PositiveInfinity)   // 코드 상자
        {
            var tb = new TextBox                                    // 글 상자
            {
                Text = text ?? "",                                  // 내용
                IsReadOnly = true,                                  // 읽기 전용(선택·복사는 됨)
                FontFamily = Mono,                                  // 고정폭
                TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap,   // 줄 바꿈
                HorizontalScrollBarVisibility = wrap ? ScrollBarVisibility.Disabled : ScrollBarVisibility.Auto,   // 가로 스크롤
                VerticalScrollBarVisibility = double.IsInfinity(maxHeight) ? ScrollBarVisibility.Disabled : ScrollBarVisibility.Auto,   // 세로 스크롤(높이 제한 때만)
                MaxHeight = maxHeight,                              // 높이 제한
                BorderThickness = new Thickness(0),                 // 테두리 없음
                Background = CodeBackground,                        // 코드 바탕
                Padding = new Thickness(6, 4, 6, 4),                // 안쪽 여백
            };
            ApplyText(tb);                                          // 글자 색
            if (double.IsInfinity(maxHeight)) ForwardWheel(tb);     // 높이 제한이 없으면 휠은 바깥으로
            return tb;                                              // 결과
        }
    }

    /// <summary>마크다운 글 → WPF 요소.</summary>
    internal static class MarkdownView                              // 마크다운 그리기
    {
        public static FrameworkElement Render(string text)          // 그리기
        {
            var panel = new StackPanel();                           // 블록을 세로로
            foreach (var b in Markdown.Parse(text)) panel.Children.Add(RenderBlock(b));   // 블록마다
            return panel;                                           // 결과
        }

        private static UIElement RenderBlock(MdBlock b)             // 블록 하나
        {
            switch (b.Kind)                                         // 종류별
            {
                case MdBlockKind.CodeBlock:                         // 코드 블록
                    var code = Theme.ReadOnlyCode(b.Text, false);   // 가로 스크롤 코드 상자
                    code.Margin = new Thickness(0, 4, 0, 4);        // 위아래 여백
                    if (!string.IsNullOrEmpty(b.Language)) code.ToolTip = b.Language;   // 언어 표시
                    return code;                                    // 결과
                case MdBlockKind.Heading:                           // 제목
                    var h = Inline(b.Spans);                        // 글
                    h.FontWeight = FontWeights.Bold;                // 굵게
                    h.FontSize = b.Level <= 1 ? 17 : b.Level == 2 ? 15 : 13.5;   // 단계별 크기
                    h.Margin = new Thickness(0, 6, 0, 2);           // 여백
                    return h;                                       // 결과
                case MdBlockKind.ListItem:                          // 목록 항목
                    var row = new DockPanel { Margin = new Thickness(14 * b.Level, 1, 0, 1) };   // 들여쓰기
                    var marker = new TextBlock { Text = b.Marker + " ", MinWidth = 16 };   // 표시
                    DockPanel.SetDock(marker, Dock.Left);           // 왼쪽
                    row.Children.Add(marker);                       // 추가
                    row.Children.Add(Inline(b.Spans));              // 글
                    return row;                                     // 결과
                default:                                            // 문단
                    var p = Inline(b.Spans);                        // 글
                    p.Margin = new Thickness(0, 2, 0, 4);           // 여백
                    return p;                                       // 결과
            }
        }

        private static TextBlock Inline(List<MdSpan> spans)         // 인라인 조각 → TextBlock
        {
            var tb = new TextBlock { TextWrapping = TextWrapping.Wrap };   // 줄 바꿈
            foreach (var s in spans)                                // 조각마다
            {
                var run = new Run(s.Text);                          // 글 조각
                if (s.Bold) run.FontWeight = FontWeights.Bold;      // 굵게
                if (s.Code) { run.FontFamily = Theme.Mono; run.Background = Theme.CodeBackground; }   // 인라인 코드
                tb.Inlines.Add(run);                                // 추가
            }
            return tb;                                              // 결과
        }
    }
}
