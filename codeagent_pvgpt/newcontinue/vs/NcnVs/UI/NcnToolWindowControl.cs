// vs/NcnVs/UI/NcnToolWindowControl.cs — ncn 도구 창 화면(순수 WPF, 코드로 구성): 모드·새 대화·중단·계획·대화 목록·첨부·입력창·상태 줄
// XAML 대신 코드로 만든 이유: 고전 VSIX 프로젝트의 XAML 컴파일(임시 _wpftmp 프로젝트·PackageReference 복원) 위험을 없애고, CI 첫 빌드를 단순하게.
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 목록
using System.Collections.Specialized;                               // CollectionChanged
using System.Diagnostics;                                           // 로그 폴더 열기
using System.IO;                                                    // Path
using System.Windows;                                               // WPF 기본
using System.Windows.Controls;                                      // 컨트롤
using System.Windows.Input;                                         // 키
using System.Windows.Threading;                                     // DispatcherPriority
using Microsoft.VisualStudio.PlatformUI;                            // EnvironmentColors
using Microsoft.VisualStudio.Shell;                                 // ThreadHelper·VsResourceKeys
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Acp;                                               // ContentBlocks
using NcnVs.Core.Host;                                              // NcnProcess
using NcnVs.Core.Json;                                              // JsonRpcException
using NcnVs.Core.State;                                             // AgentSession
using NcnVs.Ide;                                                    // VS 연동

namespace NcnVs.UI                                                  // 화면 이름공간
{
    /// <summary>도구 창 하나 = 세션 하나. 처음 보일 때 ncn 을 띄운다.</summary>
    public sealed class NcnToolWindowControl : UserControl, IChatActions   // 도구 창 화면
    {
        private readonly ComboBox _mode;                            // 모드 선택
        private readonly Button _newChat;                           // 새 대화
        private readonly Button _cancel;                            // 중단
        private readonly Button _send;                              // 보내기
        private readonly TextBox _input;                            // 입력창
        private readonly CheckBox _attachFile;                      // 현재 파일 첨부
        private readonly CheckBox _attachSelection;                 // 선택 영역 첨부
        private readonly StackPanel _list;                          // 대화 목록
        private readonly ScrollViewer _scroll;                      // 대화 스크롤
        private readonly Expander _plan;                            // 계획
        private readonly StackPanel _planList;                      // 계획 항목
        private readonly TextBlock _status;                         // 상태 줄
        private AgentSession _session;                              // 세션(처음 보일 때 만듦)
        private VsDiffPresenter _diff;                              // 비교 창
        private bool _started;                                      // 시작했는지
        private bool _updatingMode;                                 // 모드 상자를 코드로 바꾸는 중

        public NcnToolWindowControl()                               // 생성자: 화면 구성
        {
            SetResourceReference(BackgroundProperty, EnvironmentColors.ToolWindowBackgroundBrushKey);   // 테마 바탕
            SetResourceReference(ForegroundProperty, EnvironmentColors.ToolWindowTextBrushKey);         // 테마 글자(아래로 상속)
            var root = new Grid { Margin = new Thickness(6) };      // 바탕 격자
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 0: 도구 줄
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 1: 계획
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });   // 2: 대화
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 3: 입력
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 4: 상태 줄

            // ── 0: 도구 줄 ──
            var bar = new WrapPanel { Margin = new Thickness(0, 0, 0, 4) };   // 도구 줄
            bar.Children.Add(new TextBlock { Text = "모드", VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 4, 0) });   // 이름표
            _mode = new ComboBox { MinWidth = 90, Margin = new Thickness(0, 0, 8, 0), ToolTip = "normal: 필요한 도구는 묻기 · plan: 읽기 전용 · auto: 묻지 않음" };   // 모드
            _mode.SetResourceReference(StyleProperty, VsResourceKeys.ComboBoxStyleKey);   // VS 모양
            _mode.SelectionChanged += OnModeChanged;                // 바꾸면 session/set_mode
            bar.Children.Add(_mode);                                // 추가
            _newChat = Theme.StyleButton(new Button { Content = "새 대화", Padding = new Thickness(8, 1, 8, 1), Margin = new Thickness(0, 0, 4, 0) });   // 새 대화
            _newChat.Click += (s, e) => Run(NewConversationAsync);  // 누르면
            bar.Children.Add(_newChat);                             // 추가
            _cancel = Theme.StyleButton(new Button { Content = "중단", Padding = new Thickness(8, 1, 8, 1), Margin = new Thickness(0, 0, 4, 0), IsEnabled = false });   // 중단
            _cancel.Click += (s, e) => Run(async () => { if (_session != null) await _session.CancelAsync(); });   // session/cancel
            bar.Children.Add(_cancel);                              // 추가
            var logs = Theme.StyleButton(new Button { Content = "로그", Padding = new Thickness(8, 1, 8, 1), ToolTip = "로그 폴더 열기(출력 창 'ncn' 에도 같은 내용)" });   // 로그
            logs.Click += (s, e) => OpenLogFolder();                // 폴더 열기
            bar.Children.Add(logs);                                 // 추가
            Grid.SetRow(bar, 0);                                    // 0행
            root.Children.Add(bar);                                 // 추가

            // ── 1: 계획(Checklist) ──
            _planList = new StackPanel { Margin = new Thickness(16, 2, 0, 2) };   // 항목들
            _plan = new Expander { Header = "계획", Content = _planList, IsExpanded = true, Visibility = Visibility.Collapsed, Margin = new Thickness(0, 0, 0, 4) };   // 접기/펴기
            Grid.SetRow(_plan, 1);                                  // 1행
            root.Children.Add(_plan);                               // 추가

            // ── 2: 대화 목록 ──
            _list = new StackPanel();                               // 항목들
            _scroll = new ScrollViewer { Content = _list, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };   // 세로 스크롤
            _scroll.SetResourceReference(StyleProperty, VsResourceKeys.ScrollViewerStyleKey);   // VS 스크롤 모양
            var listBorder = new Border { Child = _scroll, BorderThickness = new Thickness(1), Padding = new Thickness(4) };   // 테두리
            Theme.ApplyBorder(listBorder);                          // 테마 테두리
            Grid.SetRow(listBorder, 2);                             // 2행
            root.Children.Add(listBorder);                          // 추가

            // ── 3: 첨부 + 입력 ──
            var inputArea = new StackPanel { Margin = new Thickness(0, 4, 0, 0) };   // 입력 영역
            var attach = new WrapPanel { Margin = new Thickness(0, 0, 0, 2) };   // 첨부 줄
            _attachFile = new CheckBox { Content = "현재 파일", Margin = new Thickness(0, 0, 10, 0), ToolTip = "활성 문서를 resource_link 로 첨부(ncn 이 @파일 로 내용을 붙임)" };   // 현재 파일
            _attachSelection = new CheckBox { Content = "선택 영역", ToolTip = "편집기 선택 글을 resource(#L시작-끝)로 첨부" };   // 선택 영역
            _attachFile.SetResourceReference(StyleProperty, VsResourceKeys.CheckBoxStyleKey);   // VS 모양
            _attachSelection.SetResourceReference(StyleProperty, VsResourceKeys.CheckBoxStyleKey);   // VS 모양
            attach.Children.Add(_attachFile);                       // 추가
            attach.Children.Add(_attachSelection);                  // 추가
            inputArea.Children.Add(attach);                         // 첨부 줄
            var row = new DockPanel();                              // 입력 줄
            _send = Theme.StyleButton(new Button { Content = "보내기", Padding = new Thickness(10, 2, 10, 2), Margin = new Thickness(4, 0, 0, 0), VerticalAlignment = VerticalAlignment.Bottom });   // 보내기
            _send.Click += (s, e) => Run(SendAsync);                // 누르면
            DockPanel.SetDock(_send, Dock.Right);                   // 오른쪽
            row.Children.Add(_send);                                // 추가
            _input = Theme.StyleInput(new TextBox                   // 입력창
            {
                AcceptsReturn = true,                               // Shift+Enter 줄바꿈(Enter 는 아래에서 가로챔)
                TextWrapping = TextWrapping.Wrap,                   // 줄 바꿈
                MinHeight = 48,                                     // 최소 높이
                MaxHeight = 200,                                    // 최대 높이
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,   // 길면 스크롤
                ToolTip = "Enter 보내기 · Shift+Enter 줄바꿈 · /compact 대화 압축",   // 안내
            });
            _input.PreviewKeyDown += OnInputKeyDown;                // Enter 처리
            row.Children.Add(_input);                               // 추가
            inputArea.Children.Add(row);                            // 입력 줄
            Grid.SetRow(inputArea, 3);                              // 3행
            root.Children.Add(inputArea);                           // 추가

            // ── 4: 상태 줄 ──
            _status = new TextBlock { Foreground = Theme.Muted, Margin = new Thickness(0, 4, 0, 0), TextTrimming = TextTrimming.CharacterEllipsis };   // 상태
            Grid.SetRow(_status, 4);                                // 4행
            root.Children.Add(_status);                             // 추가

            Content = root;                                         // 화면
            Loaded += (s, e) => { if (!_started) { _started = true; Run(StartAsync); } };   // 처음 보일 때 시작
        }

        // ─────────────────────────── 시작·세션 ───────────────────────────

        private static NcnPackage Package => NcnPackage.Instance;  // 패키지

        private void Run(Func<System.Threading.Tasks.Task> work)   // UI 동작을 비동기로(예외는 로그·안내)
        {
            _ = ThreadHelper.JoinableTaskFactory.RunAsync(async () => // VS 권장 방식
            {
                try { await work(); }                               // 실행
                catch (Exception e)                                 // 오류
                {
                    Package?.Log?.Write("화면 동작 오류: " + e);    // 로그
                    if (_session != null) _session.State.AddNote(e.Message, true);   // 안내(이미 UI 스레드)
                    else _status.Text = "오류: " + e.Message;       // 세션 전이면 상태 줄
                }
            });
        }

        private async System.Threading.Tasks.Task StartAsync()      // 세션 만들고 ncn 시작
        {
            var pkg = Package;                                      // 패키지
            if (pkg == null) { _status.Text = "패키지가 아직 로드되지 않았습니다. 창을 다시 여세요."; return; }   // 드문 경우
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();   // UI 스레드
            var opts = pkg.Options;                                 // 옵션
            _attachFile.IsChecked = opts.AttachCurrentFileByDefault;   // 첨부 기본값
            _attachSelection.IsChecked = opts.AttachSelectionByDefault;   // 첨부 기본값
            _diff = new VsDiffPresenter(pkg, pkg.Log);              // 비교 창
            var cwd = await VsContext.GetSolutionDirectoryAsync(pkg);   // 솔루션 폴더
            var launch = await VsContext.BuildLaunchOptionsAsync(pkg, cwd);   // ncn 실행 설정
            pkg.Log.Write("ncn 실행: " + launch.ExePath + " " + CommandLine.Join(launch.BuildArguments()) + " (cwd " + cwd + ")");   // 로그
            var dispatcher = Dispatcher;                            // UI 디스패처
            _session = new AgentSession(new AgentSessionOptions     // 세션
            {
                ProcessFactory = () => NcnProcess.Start(launch, pkg.Lifetime, line => pkg.Log.Write("[ncn] " + line)),   // 띄우기(재시작 때도)
                Ide = new VsIdeBridge(pkg, pkg.Log),                // fs 위임(버퍼·저장)
                Post = a => dispatcher.BeginInvoke(DispatcherPriority.Normal, a),   // UI 스레드로(순서 보장)
                Log = pkg.Log.Write,                                // 로그
            });
            Bind(_session);                                         // 화면 연결
            if (!File.Exists(launch.ExePath))                       // ncn.exe 없음
            {
                _session.State.AddNote("ncn.exe 를 찾지 못했습니다: " + launch.ExePath + "\n도구 → 옵션 → ncn → 일반 에서 경로를 지정하세요.", true);   // 안내
                _session.State.StatusText = "ncn 없음";             // 상태
                return;                                             // 시작 안 함
            }
            try
            {
                await _session.StartAsync(cwd);                     // initialize + session/new
            }
            catch (JsonRpcException e)                              // 설정 오류 등(-32603)
            {
                _session.State.AddNote("ncn 시작 실패: " + e.DisplayMessage, true);   // cn 원문 오류
                _session.State.StatusText = "시작 실패";            // 상태
            }
            catch (Exception e)                                     // 실행 실패
            {
                pkg.Log.Write("ncn 시작 실패: " + e);               // 로그
                _session.State.AddNote("ncn 시작 실패: " + e.Message + " (출력 창 'ncn' 참고)", true);   // 안내
                _session.State.StatusText = "시작 실패";            // 상태
            }
        }

        private async System.Threading.Tasks.Task NewConversationAsync()   // 새 대화
        {
            if (_session == null) { await StartAsync(); return; }   // 아직 없으면 시작
            var cwd = await VsContext.GetSolutionDirectoryAsync(Package);   // 지금 솔루션 폴더(바뀌었을 수 있음)
            await _session.NewConversationAsync(cwd);               // session/new(연결이 없으면 다시 띄움)
        }

        /// <summary>세션 상태 ↔ 화면 연결.</summary>
        private void Bind(AgentSession s)                           // 연결
        {
            var st = s.State;                                       // 상태
            st.Items.CollectionChanged += OnItemsChanged;           // 대화 목록
            st.Plan.CollectionChanged += (o, e) => RenderPlan();    // 계획
            st.Modes.CollectionChanged += (o, e) => RenderModes();  // 모드 목록
            st.PropertyChanged += (o, e) => RenderState();          // 상태
            s.PermissionRequested += OnPermissionRequested;         // 승인 → 비교 창
            foreach (var it in st.Items) _list.Children.Add(ItemViews.Create(it, this));   // 이미 있는 항목
            RenderModes();                                          // 처음 그리기
            RenderPlan();                                           // 처음 그리기
            RenderState();                                          // 처음 그리기
        }

        private void OnItemsChanged(object sender, NotifyCollectionChangedEventArgs e)   // 목록 바뀜(UI 스레드)
        {
            var atBottom = _scroll.VerticalOffset >= _scroll.ScrollableHeight - 24;   // 맨 아래 보고 있었는지
            switch (e.Action)                                       // 종류별
            {
                case NotifyCollectionChangedAction.Add:             // 추가
                    var i = e.NewStartingIndex;                     // 위치
                    foreach (ChatItem it in e.NewItems)             // 새 항목
                        _list.Children.Insert(i++, ItemViews.Create(it, this));   // 화면 추가
                    break;                                          // 끝
                case NotifyCollectionChangedAction.Remove:          // 제거(_ncn/retry)
                    for (var k = 0; k < e.OldItems.Count; k++) _list.Children.RemoveAt(e.OldStartingIndex);   // 같은 위치에서 지움
                    break;                                          // 끝
                default:                                            // Reset 등
                    _list.Children.Clear();                         // 다시 그리기
                    foreach (var it in _session.State.Items) _list.Children.Add(ItemViews.Create(it, this));   // 전체
                    break;                                          // 끝
            }
            if (atBottom) Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() => _scroll.ScrollToEnd()));   // 따라 내려감
        }

        private void RenderPlan()                                   // 계획 그리기
        {
            var plan = _session.State.Plan;                         // 항목들
            _planList.Children.Clear();                             // 비우기
            var done = 0;                                           // 끝난 수
            foreach (var p in plan)                                 // 항목마다
            {
                if (p.IsDone) done++;                               // 셈
                _planList.Children.Add(new TextBlock               // 한 줄
                {
                    Text = (p.IsDone ? "☑ " : p.Status == "in_progress" ? "◐ " : "☐ ") + p.Content,   // 표시 + 할 일
                    TextWrapping = TextWrapping.Wrap,               // 줄 바꿈
                    Opacity = p.IsDone ? 0.6 : 1.0,                 // 끝난 것은 흐리게
                });
            }
            _plan.Header = "계획 (" + done + "/" + plan.Count + ")"; // 진행
            _plan.Visibility = plan.Count > 0 ? Visibility.Visible : Visibility.Collapsed;   // 있을 때만
        }

        private void RenderModes()                                  // 모드 목록 그리기
        {
            _updatingMode = true;                                   // 사건 무시
            try
            {
                _mode.Items.Clear();                                // 비우기
                foreach (var m in _session.State.Modes)             // 모드마다
                    _mode.Items.Add(new ComboBoxItem { Content = m.Name ?? m.Id, Tag = m.Id, ToolTip = m.Description });   // 항목
                SelectMode(_session.State.CurrentModeId);           // 현재 모드
            }
            finally { _updatingMode = false; }                      // 다시 받기
        }

        private void SelectMode(string id)                          // 모드 상자 맞추기
        {
            foreach (ComboBoxItem item in _mode.Items)              // 항목마다
                if ((string)item.Tag == id) { _mode.SelectedItem = item; return; }   // 찾으면 선택
        }

        private void RenderState()                                  // 상태 그리기
        {
            var st = _session.State;                                // 상태
            _send.IsEnabled = !st.IsBusy;                           // 진행 중엔 보내기 잠금(같은 세션 prompt 중복 방지)
            _cancel.IsEnabled = st.IsBusy;                          // 진행 중에만 중단
            _status.Text = st.StatusText + (string.IsNullOrEmpty(st.WorkingDirectory) ? "" : " · " + st.WorkingDirectory);   // 상태 · 작업 폴더
            _updatingMode = true;                                   // 사건 무시
            try { SelectMode(st.CurrentModeId); }                   // 모드 표시
            finally { _updatingMode = false; }                      // 다시 받기
        }

        private void OnModeChanged(object sender, SelectionChangedEventArgs e)   // 모드 고름
        {
            if (_updatingMode || _session == null) return;          // 코드로 바꾼 것·세션 없음
            var item = _mode.SelectedItem as ComboBoxItem;          // 고른 항목
            if (item == null) return;                               // 없음
            var id = (string)item.Tag;                              // 모드 id
            if (id == _session.State.CurrentModeId) return;         // 같으면 무시
            Run(() => _session.SetModeAsync(id));                   // session/set_mode
        }

        // ─────────────────────────── 보내기 ───────────────────────────

        private void OnInputKeyDown(object sender, KeyEventArgs e)  // Enter 보내기·Shift+Enter 줄바꿈
        {
            if (e.Key != Key.Enter) return;                         // Enter 만(한글 조합 중 Enter 는 ImeProcessed 로 와서 여기 안 옴)
            if ((Keyboard.Modifiers & ModifierKeys.Shift) != 0) return;   // Shift+Enter → TextBox 가 줄바꿈
            e.Handled = true;                                       // 기본 동작 막기
            Run(SendAsync);                                         // 보내기
        }

        private async System.Threading.Tasks.Task SendAsync()       // 질문 보내기
        {
            var text = _input.Text;                                 // 입력 글
            if (string.IsNullOrWhiteSpace(text)) return;            // 비었으면 무시
            if (_session == null) await StartAsync();               // 세션이 없으면 시작
            if (_session == null || _session.IsBusy) return;        // 진행 중이면 무시
            var attachments = new List<JObject>();                  // 첨부 블록
            var labels = new List<string>();                        // 첨부 설명
            var wantFile = _attachFile.IsChecked == true;           // 현재 파일
            var wantSel = _attachSelection.IsChecked == true;       // 선택 영역
            if (wantFile || wantSel)                                // 첨부할 것
            {
                var doc = await VsContext.GetActiveDocumentAsync(Package);   // 현재 문서
                if (doc != null)                                    // 있으면
                {
                    var name = Path.GetFileName(doc.Path);          // 이름
                    if (wantFile) { attachments.Add(ContentBlocks.ResourceLink(doc.Path)); labels.Add(name); }   // resource_link
                    if (wantSel && !string.IsNullOrEmpty(doc.SelectedText))   // 선택 있음
                    {
                        attachments.Add(ContentBlocks.Resource(doc.Path, doc.SelectedText, doc.StartLine, doc.EndLine));   // resource(#L시작-끝)
                        labels.Add(name + "#L" + doc.StartLine + "-" + doc.EndLine);   // 설명
                    }
                }
            }
            _input.Clear();                                         // 입력창 비우기
            await _session.SendAsync(text, attachments, labels);    // session/prompt
        }

        // ─────────────────────────── IChatActions ───────────────────────────

        private void OnPermissionRequested(PermissionItem item)    // 승인 요청 → 비교 창 자동
        {
            if (Package == null || !Package.Options.OpenDiffOnPermission) return;   // 옵션 끔
            var d = item.Request.ToolCall.FirstDiff();              // diff
            if (d != null) ShowDiff(d, item.Title);                 // 비교 창
        }

        void IChatActions.Choose(PermissionItem item, string optionId)   // 승인 버튼
        {
            _session?.ChoosePermission(item, optionId);             // optionId 그대로
        }

        void IChatActions.Answer(QuestionItem item, QuestionAnswer answer)   // 질문 답
        {
            _session?.AnswerQuestion(item, answer);                 // 답
        }

        public void ShowDiff(ToolContent diff, string title)        // 비교 창 열기
        {
            if (_diff == null || diff == null) return;              // 준비 안 됨
            _ = ThreadHelper.JoinableTaskFactory.RunAsync(() => _diff.ShowAsync(diff.Path, diff.OldText, diff.NewText, title));   // 비동기
        }

        private void OpenLogFolder()                                // 로그 폴더 열기
        {
            var dir = Package?.Log?.Directory;                      // 로그 폴더
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;   // 없음
            try { Process.Start("explorer.exe", "\"" + dir + "\""); } catch { }   // 탐색기
        }

        /// <summary>도구 창 정리(VS 종료).</summary>
        public void Shutdown()                                      // 정리
        {
            try { _session?.Dispose(); } catch { }                  // stdin 닫기 → ncn 종료
            _session = null;                                        // 비움
        }
    }
}
