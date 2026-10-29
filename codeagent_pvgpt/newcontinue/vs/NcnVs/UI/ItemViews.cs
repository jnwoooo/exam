// vs/NcnVs/UI/ItemViews.cs — 대화 항목별 WPF 화면: 사용자 글·응답(마크다운)·도구 카드(접기/펴기)·승인 버튼 3개·질문·안내
using System;                                                       // 기본 형식
using System.ComponentModel;                                        // PropertyChangedEventArgs
using System.Windows;                                               // FrameworkElement
using System.Windows.Controls;                                      // 컨트롤
using System.Windows.Input;                                         // Key
using System.Windows.Threading;                                     // DispatcherPriority
using NcnVs.Core.Acp;                                               // ToolContent·QuestionAnswer
using NcnVs.Core.State;                                             // 항목 모델

namespace NcnVs.UI                                                  // 화면 이름공간
{
    /// <summary>항목 화면이 도구 창에 요청하는 동작.</summary>
    internal interface IChatActions                                 // 화면 동작
    {
        void Choose(PermissionItem item, string optionId);         // 승인 버튼
        void Answer(QuestionItem item, QuestionAnswer answer);      // 질문 답
        void ShowDiff(ToolContent diff, string title);              // 비교 창 열기
    }

    /// <summary>항목 → 화면 만들기. 각 화면은 항목의 PropertyChanged 를 받아 스스로 고친다(UI 스레드).</summary>
    internal static class ItemViews                                 // 화면 공장
    {
        public static FrameworkElement Create(ChatItem item, IChatActions actions)   // 만들기
        {
            if (item is UserMessageItem u) return User(u);          // 사용자 글
            if (item is AgentMessageItem a) return Agent(a);        // 응답
            if (item is ToolCardItem c) return Card(c, actions);    // 도구 카드
            if (item is PermissionItem p) return Permission(p, actions);   // 승인
            if (item is QuestionItem q) return Question(q, actions);   // 질문
            if (item is SystemNoteItem n) return Note(n);           // 안내
            return new TextBlock { Text = item.ToString() };        // 모르는 항목
        }

        private static Border Box(Thickness margin)                 // 공통 상자
        {
            var b = new Border { Margin = margin, Padding = new Thickness(8, 6, 8, 6), CornerRadius = new CornerRadius(4), BorderThickness = new Thickness(1) };   // 상자
            Theme.ApplyBorder(b);                                   // 테마 테두리
            return b;                                               // 결과
        }

        private static void AddCopyMenu(FrameworkElement e, Func<string> text)   // 오른쪽 클릭 → 복사
        {
            var menu = new ContextMenu();                           // 메뉴
            var copy = new MenuItem { Header = "복사" };            // 항목
            copy.Click += (s, a) => { try { Clipboard.SetText(text() ?? ""); } catch { } };   // 클립보드(잠겨 있으면 무시)
            menu.Items.Add(copy);                                   // 추가
            e.ContextMenu = menu;                                   // 붙임
        }

        private static FrameworkElement User(UserMessageItem u)    // 사용자 글
        {
            var box = Box(new Thickness(24, 6, 0, 2));              // 오른쪽으로 들인 상자
            box.Background = Theme.UserBackground;                  // 옅은 파랑
            var panel = new StackPanel();                           // 내용
            var text = new TextBlock { TextWrapping = TextWrapping.Wrap, Text = u.Text };   // 글
            panel.Children.Add(text);                               // 추가
            if (u.Attachments.Count > 0)                            // 첨부 있음
                panel.Children.Add(new TextBlock { Text = "첨부: " + string.Join(", ", u.Attachments), Foreground = Theme.Muted, FontSize = 11, Margin = new Thickness(0, 2, 0, 0) });   // 첨부 줄
            box.Child = panel;                                      // 넣기
            u.PropertyChanged += (s, e) => text.Text = u.Text;      // load 되살리기 조각
            AddCopyMenu(box, () => u.Text);                         // 복사
            return box;                                             // 결과
        }

        private static FrameworkElement Agent(AgentMessageItem a)  // 응답(마크다운)
        {
            var box = new Border { Margin = new Thickness(0, 4, 8, 2), Padding = new Thickness(2) };   // 테두리 없는 상자
            if (a.IsThought) box.Opacity = 0.7;                     // 생각·압축 안내는 흐리게
            var scheduled = false;                                  // 다시 그리기 예약됨
            Action render = () => { scheduled = false; box.Child = MarkdownView.Render(a.Text); };   // 다시 그리기
            render();                                               // 처음 그리기
            a.PropertyChanged += (s, e) =>                          // 조각이 붙을 때마다
            {
                if (scheduled) return;                              // 이미 예약
                scheduled = true;                                   // 예약
                box.Dispatcher.BeginInvoke(DispatcherPriority.Background, render);   // 한가할 때 한 번(조각 여러 개를 묶어 그림)
            };
            AddCopyMenu(box, () => a.Text);                         // 복사(마크다운 원문)
            return box;                                             // 결과
        }

        private static string Glyph(string status)                  // 상태 기호
        {
            switch (status)                                         // 상태별
            {
                case "completed": return "✔";                       // 완료
                case "failed": return "✖";                          // 실패
                case "in_progress": return "◐";                     // 실행 중
                default: return "○";                                // 대기
            }
        }

        private static FrameworkElement Card(ToolCardItem c, IChatActions actions)   // 도구 카드
        {
            var glyph = new TextBlock { Margin = new Thickness(0, 0, 6, 0), FontWeight = FontWeights.Bold };   // 상태 기호
            var title = new TextBlock { TextTrimming = TextTrimming.CharacterEllipsis };   // 제목
            var header = new StackPanel { Orientation = Orientation.Horizontal };   // 머리
            header.Children.Add(glyph);                             // 기호
            header.Children.Add(title);                             // 제목
            var body = new StackPanel { Margin = new Thickness(18, 4, 0, 4) };   // 펼친 내용
            var exp = new Expander { Header = header, Content = body, IsExpanded = c.IsExpanded, Margin = new Thickness(0, 2, 8, 2) };   // 접기/펴기
            exp.Expanded += (s, e) => c.IsExpanded = true;          // 펼침 기억
            exp.Collapsed += (s, e) => c.IsExpanded = false;        // 접힘 기억
            Action refresh = () =>                                  // 다시 그리기
            {
                glyph.Text = Glyph(c.Status);                       // 기호
                glyph.Foreground = c.Status == "completed" ? Theme.Ok : c.Status == "failed" ? Theme.Fail : Theme.Muted;   // 색
                title.Text = c.Title;                               // 제목
                exp.ToolTip = c.Kind + " · " + c.Status;            // 종류·상태
                body.Children.Clear();                              // 내용 비우고
                foreach (var d in c.Diffs)                          // diff 마다
                {
                    var diff = d;                                   // 람다용 복사
                    var row = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };   // 줄
                    var btn = Theme.StyleButton(new Button { Content = "비교 창 열기", Padding = new Thickness(8, 1, 8, 1), Margin = new Thickness(6, 0, 0, 0) });   // 비교 버튼
                    btn.Click += (s, e) => actions.ShowDiff(diff, c.Title);   // 비교 창
                    DockPanel.SetDock(btn, Dock.Right);             // 오른쪽
                    row.Children.Add(btn);                          // 버튼
                    row.Children.Add(new TextBlock { Text = (diff.OldText == null ? "새 파일: " : "변경: ") + diff.Path, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center });   // 경로
                    body.Children.Add(row);                         // 추가
                }
                var output = c.OutputText;                          // 결과 글
                if (!string.IsNullOrEmpty(output)) body.Children.Add(Theme.ReadOnlyCode(output, true, 320));   // 결과(높이 제한)
                if (!string.IsNullOrEmpty(c.RawInput))              // 인자
                {
                    var raw = new Expander { Header = "인자", Content = Theme.ReadOnlyCode(c.RawInput, true, 200), Margin = new Thickness(0, 4, 0, 0) };   // 접힌 인자
                    body.Children.Add(raw);                         // 추가
                }
            };
            refresh();                                              // 처음 그리기
            c.PropertyChanged += (s, e) => { if (e.PropertyName != nameof(ToolCardItem.IsExpanded)) refresh(); };   // 바뀌면 다시
            return exp;                                             // 결과
        }

        private static FrameworkElement Permission(PermissionItem p, IChatActions actions)   // 승인 요청
        {
            var box = Box(new Thickness(0, 4, 8, 4));               // 상자
            box.Background = Theme.AskBackground;                   // 옅은 주황
            var panel = new StackPanel();                           // 내용
            panel.Children.Add(new TextBlock { Text = "승인 요청: " + p.Title, FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap });   // 제목
            var preview = p.PreviewText;                            // 미리보기 글
            if (!string.IsNullOrEmpty(preview)) panel.Children.Add(Theme.ReadOnlyCode(preview, true, 200));   // 미리보기(명령 등)
            var diff = p.Request.ToolCall.FirstDiff();              // diff
            var buttons = new WrapPanel { Margin = new Thickness(0, 6, 0, 0) };   // 버튼 줄
            if (diff != null)                                       // diff 있음
            {
                var show = Theme.StyleButton(new Button { Content = "비교 창 보기", Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(0, 0, 12, 4) });   // 비교 버튼
                show.Click += (s, e) => actions.ShowDiff(diff, p.Title);   // 비교 창
                buttons.Children.Add(show);                         // 추가
            }
            foreach (var o in p.Request.Options)                    // ACP 선택지마다(이름 그대로)
            {
                var opt = o;                                        // 람다용 복사
                var b = Theme.StyleButton(new Button { Content = opt.Name, Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(0, 0, 6, 4), ToolTip = opt.OptionId });   // 버튼
                b.Click += (s, e) => actions.Choose(p, opt.OptionId);   // 고르기
                buttons.Children.Add(b);                            // 추가
            }
            panel.Children.Add(buttons);                            // 버튼 줄
            var result = new TextBlock { Foreground = Theme.Muted, Visibility = Visibility.Collapsed };   // 결과 줄
            panel.Children.Add(result);                             // 추가
            box.Child = panel;                                      // 넣기
            PropertyChangedEventHandler update = (s, e) =>          // 끝나면
            {
                buttons.IsEnabled = p.IsPending;                    // 버튼 잠금(비교 창 버튼 포함 — 끝난 승인은 diff 도 카드에 있음)
                result.Text = p.ResultText ?? "";                   // 결과 글
                result.Visibility = p.IsPending ? Visibility.Collapsed : Visibility.Visible;   // 보이기
            };
            p.PropertyChanged += update;                            // 구독
            update(null, null);                                     // 처음 상태
            return box;                                             // 결과
        }

        private static FrameworkElement Question(QuestionItem q, IChatActions actions)   // 질문
        {
            var box = Box(new Thickness(0, 4, 8, 4));               // 상자
            box.Background = Theme.AskBackground;                   // 옅은 주황
            var panel = new StackPanel();                           // 내용
            panel.Children.Add(new TextBlock { Text = q.Request.Message, FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap });   // 질문 글
            var controls = new StackPanel { Margin = new Thickness(0, 6, 0, 0) };   // 입력들
            var choices = new WrapPanel();                          // 선택지 버튼
            foreach (var c in q.Request.Choices)                    // 선택지마다
            {
                var choice = c;                                     // 람다용 복사
                var b = Theme.StyleButton(new Button { Content = choice.Title, Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(0, 0, 6, 4) });   // 버튼
                b.Click += (s, e) => actions.Answer(q, new QuestionAnswer { Choice = choice.Value });   // 고르기
                choices.Children.Add(b);                            // 추가
            }
            controls.Children.Add(choices);                         // 선택지 줄
            if (q.Request.TextKey != null)                          // 직접 입력 가능
            {
                var row = new DockPanel { Margin = new Thickness(0, 2, 0, 0) };   // 입력 줄
                var send = Theme.StyleButton(new Button { Content = "답하기", Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(6, 0, 0, 0) });   // 보내기
                DockPanel.SetDock(send, Dock.Right);                // 오른쪽
                var input = Theme.StyleInput(new TextBox { ToolTip = q.Request.TextTitle });   // 입력칸
                Action submit = () => { if (!string.IsNullOrWhiteSpace(input.Text)) actions.Answer(q, new QuestionAnswer { Text = input.Text }); };   // 직접 입력 답
                send.Click += (s, e) => submit();                   // 버튼
                input.KeyDown += (s, e) => { if (e.Key == Key.Enter) { e.Handled = true; submit(); } };   // Enter
                row.Children.Add(send);                             // 버튼
                row.Children.Add(input);                            // 입력칸
                controls.Children.Add(new TextBlock { Text = q.Request.TextTitle ?? "", Foreground = Theme.Muted, FontSize = 11 });   // 설명
                controls.Children.Add(row);                         // 줄
            }
            var skip = Theme.StyleButton(new Button { Content = "건너뛰기", Padding = new Thickness(8, 2, 8, 2), Margin = new Thickness(0, 6, 0, 0), HorizontalAlignment = HorizontalAlignment.Left });   // 거절
            skip.Click += (s, e) => actions.Answer(q, QuestionAnswer.Decline());   // 건너뛰기(기본 답)
            controls.Children.Add(skip);                            // 추가
            panel.Children.Add(controls);                           // 입력들
            var result = new TextBlock { Foreground = Theme.Muted, Visibility = Visibility.Collapsed };   // 결과 줄
            panel.Children.Add(result);                             // 추가
            box.Child = panel;                                      // 넣기
            PropertyChangedEventHandler update = (s, e) =>          // 끝나면
            {
                controls.IsEnabled = q.IsPending;                   // 잠금
                result.Text = q.ResultText ?? "";                   // 결과 글
                result.Visibility = q.IsPending ? Visibility.Collapsed : Visibility.Visible;   // 보이기
            };
            q.PropertyChanged += update;                            // 구독
            update(null, null);                                     // 처음 상태
            return box;                                             // 결과
        }

        private static FrameworkElement Note(SystemNoteItem n)     // 안내
        {
            var t = new TextBlock                                   // 한 줄
            {
                Text = n.Text,                                      // 글
                TextWrapping = TextWrapping.Wrap,                   // 줄 바꿈
                FontStyle = FontStyles.Italic,                      // 기울임
                Foreground = n.IsError ? Theme.Fail : Theme.Muted,  // 오류는 빨강
                Margin = new Thickness(0, 4, 8, 4),                 // 여백
            };
            AddCopyMenu(t, () => n.Text);                           // 복사
            return t;                                               // 결과
        }
    }
}
