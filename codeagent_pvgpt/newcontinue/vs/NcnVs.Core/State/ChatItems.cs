// vs/NcnVs.Core/State/ChatItems.cs — 대화 목록 항목(사용자 글·응답·도구 카드·승인·질문·안내) — 화면이 그대로 그린다 (cn 원본 없음)
using System;                                                       // Action
using System.Collections.Generic;                                   // 목록
using System.ComponentModel;                                        // INotifyPropertyChanged
using System.Runtime.CompilerServices;                              // CallerMemberName
using System.Text;                                                  // StringBuilder
using NcnVs.Core.Acp;                                               // ACP 모델

namespace NcnVs.Core.State                                          // 상태 이름공간
{
    /// <summary>속성 바뀜 알림 기본형.</summary>
    public abstract class ObservableObject : INotifyPropertyChanged // 알림 기본형
    {
        public event PropertyChangedEventHandler PropertyChanged;   // 속성 바뀜 사건

        protected bool Set<T>(ref T field, T value, [CallerMemberName] string name = null)   // 값 바꾸고 알림
        {
            if (EqualityComparer<T>.Default.Equals(field, value)) return false;   // 같으면 무시
            field = value;                                          // 바꿈
            OnPropertyChanged(name);                                // 알림
            return true;                                            // 바뀜
        }

        protected void OnPropertyChanged(string name)               // 알림 보내기
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));   // 사건
        }
    }

    /// <summary>대화 항목 기본형.</summary>
    public abstract class ChatItem : ObservableObject { }            // 항목 기본형

    /// <summary>사용자 질문(첨부 설명 포함).</summary>
    public sealed class UserMessageItem : ChatItem                  // 사용자 글
    {
        private string _text = "";                                  // 글
        public string Text { get => _text; set => Set(ref _text, value ?? ""); }   // 글(load 되살리기 땐 조각이 붙음)
        public List<string> Attachments { get; } = new List<string>();   // 첨부 설명("Coupon.cs", "Cart.cs#L10-20")
        public void Append(string s) { Text = Text + s; }           // 조각 붙이기
    }

    /// <summary>에이전트 응답 글(조각이 계속 붙는다). IsThought 면 생각·압축 안내.</summary>
    public sealed class AgentMessageItem : ChatItem                 // 응답 글
    {
        private readonly StringBuilder _sb = new StringBuilder();   // 모으는 글
        public bool IsThought { get; set; }                         // 생각 조각인지
        public string Text => _sb.ToString();                       // 지금까지 글
        public void Append(string s)                                // 조각 붙이기
        {
            if (string.IsNullOrEmpty(s)) return;                    // 빈 조각 무시
            _sb.Append(s);                                          // 붙임
            OnPropertyChanged(nameof(Text));                        // 알림
        }
    }

    /// <summary>도구 카드(접기/펴기). 상태는 pending → in_progress → completed/failed.</summary>
    public sealed class ToolCardItem : ChatItem                     // 도구 카드
    {
        private string _title = "";                                 // 제목
        private string _kind = "other";                             // 종류
        private string _status = "pending";                         // 상태
        private bool _expanded;                                     // 펼침
        private List<ToolContent> _content = new List<ToolContent>();   // 내용
        private string _rawInput;                                   // 인자(JSON 글)
        private string _rawOutput;                                  // 결과(글)

        public string ToolCallId { get; set; }                      // 카드 id
        public string Title { get => _title; set => Set(ref _title, value ?? ""); }   // 제목
        public string Kind { get => _kind; set => Set(ref _kind, value ?? "other"); }   // 종류
        public string Status { get => _status; set => Set(ref _status, value ?? "pending"); }   // 상태
        public bool IsExpanded { get => _expanded; set => Set(ref _expanded, value); }   // 펼침
        public List<ToolContent> Content { get => _content; set => Set(ref _content, value ?? new List<ToolContent>()); }   // 내용
        public string RawInput { get => _rawInput; set => Set(ref _rawInput, value); }   // 인자
        public string RawOutput { get => _rawOutput; set => Set(ref _rawOutput, value); }   // 결과

        /// <summary>내용 중 글(diff 제외)을 합친 것 — 카드 본문.</summary>
        public string OutputText                                    // 본문 글
        {
            get
            {
                var sb = new StringBuilder();                       // 합칠 곳
                foreach (var c in _content)                         // 내용마다
                {
                    if (c.IsDiff) continue;                         // diff 는 따로
                    if (string.IsNullOrEmpty(c.Text)) continue;     // 빈 글 무시
                    if (sb.Length > 0) sb.Append('\n');             // 구분
                    sb.Append(c.Text);                              // 붙임
                }
                if (sb.Length == 0 && !string.IsNullOrEmpty(_rawOutput)) sb.Append(_rawOutput);   // 내용이 없으면 rawOutput
                return sb.ToString();                               // 결과
            }
        }

        /// <summary>diff 목록.</summary>
        public List<ToolContent> Diffs                              // diff 들
        {
            get
            {
                var list = new List<ToolContent>();                 // 결과
                foreach (var c in _content) if (c.IsDiff) list.Add(c);   // diff 만
                return list;                                        // 결과
            }
        }

        /// <summary>업데이트 적용(null 필드는 그대로).</summary>
        public void Apply(ToolCallInfo info)                        // 갱신
        {
            if (info.Title != null) Title = info.Title;             // 제목
            if (info.Kind != null) Kind = info.Kind;                // 종류
            if (info.Status != null) Status = info.Status;          // 상태
            if (info.Content != null) Content = info.Content;       // 내용(통째로 바뀜 — ACP 규칙)
            if (info.RawInput != null) RawInput = info.RawInput.ToString(Newtonsoft.Json.Formatting.Indented);   // 인자(보기 좋게)
            if (info.RawOutput != null) RawOutput = info.RawOutput.Type == Newtonsoft.Json.Linq.JTokenType.String ? (string)info.RawOutput : info.RawOutput.ToString();   // 결과 글
            OnPropertyChanged(nameof(OutputText));                  // 본문도 다시
        }
    }

    /// <summary>승인 요청 하나. 버튼 3개(선택지 이름 그대로). 고르면 Resolved.</summary>
    public sealed class PermissionItem : ChatItem                   // 승인 항목
    {
        private bool _pending = true;                               // 기다리는 중
        private string _resultText;                                 // 결과 설명
        internal Action<PermissionOutcome> Resolver;                // 결과 전달(세션이 걸어 둠)

        public PermissionRequest Request { get; }                   // 요청
        public string Title => Request.ToolCall.Title ?? "";        // 도구 제목
        public bool IsPending { get => _pending; private set => Set(ref _pending, value); }   // 기다리는 중
        public string ResultText { get => _resultText; private set => Set(ref _resultText, value); }   // "선택: Continue" 등

        public PermissionItem(PermissionRequest request) { Request = request; }   // 생성자

        /// <summary>미리보기 글: 도구 내용 글, 없으면 rawInput(command 등).</summary>
        public string PreviewText                                   // 미리보기
        {
            get
            {
                var sb = new StringBuilder();                       // 합칠 곳
                if (Request.ToolCall.Content != null)               // 내용 있음
                    foreach (var c in Request.ToolCall.Content)     // 하나씩
                        if (!c.IsDiff && !string.IsNullOrEmpty(c.Text)) { if (sb.Length > 0) sb.Append('\n'); sb.Append(c.Text); }   // 글만
                if (sb.Length == 0 && Request.ToolCall.RawInput != null) sb.Append(Request.ToolCall.RawInput.ToString(Newtonsoft.Json.Formatting.Indented));   // 인자
                return sb.ToString();                               // 결과
            }
        }

        /// <summary>결과 표시(세션이 부름).</summary>
        internal void MarkResolved(PermissionOutcome outcome)       // 끝 표시
        {
            IsPending = false;                                      // 끝
            if (outcome.Cancelled) { ResultText = "취소됨"; return; }   // 취소
            foreach (var o in Request.Options)                      // 고른 선택지 이름 찾기
                if (o.OptionId == outcome.OptionId) { ResultText = "선택: " + o.Name; return; }   // 이름
            ResultText = "선택: " + outcome.OptionId;               // 모르는 id
        }
    }

    /// <summary>질문(AskQuestion). 선택지 버튼 + 직접 입력 + 건너뛰기.</summary>
    public sealed class QuestionItem : ChatItem                     // 질문 항목
    {
        private bool _pending = true;                               // 기다리는 중
        private string _resultText;                                 // 결과 설명
        internal Action<QuestionAnswer> Resolver;                   // 결과 전달

        public QuestionRequest Request { get; }                     // 질문
        public bool IsPending { get => _pending; private set => Set(ref _pending, value); }   // 기다리는 중
        public string ResultText { get => _resultText; private set => Set(ref _resultText, value); }   // 답 설명

        public QuestionItem(QuestionRequest request) { Request = request; }   // 생성자

        internal void MarkResolved(QuestionAnswer a)                // 끝 표시
        {
            IsPending = false;                                      // 끝
            if (a.Action != "accept") { ResultText = a.Action == "cancel" ? "취소됨" : "건너뜀"; return; }   // 거절·취소
            ResultText = "답: " + (!string.IsNullOrWhiteSpace(a.Text) ? a.Text : a.Choice);   // 답
        }
    }

    /// <summary>안내·오류 한 줄(재시작·중단·오류 등).</summary>
    public sealed class SystemNoteItem : ChatItem                   // 안내
    {
        public string Text { get; set; }                            // 글
        public bool IsError { get; set; }                           // 오류 표시
    }
}
