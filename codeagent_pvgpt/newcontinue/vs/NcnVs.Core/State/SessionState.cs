// vs/NcnVs.Core/State/SessionState.cs — 한 세션의 화면 상태(대화 목록·계획·모드·바쁨·상태 줄)와 session/update 적용 규칙 (ACP_연동.md 3-5·5장)
using System.Collections.Generic;                                   // 목록
using System.Collections.ObjectModel;                               // ObservableCollection
using NcnVs.Core.Acp;                                               // ACP 모델

namespace NcnVs.Core.State                                          // 상태 이름공간
{
    /// <summary>화면 상태. **UI 스레드에서만** 고친다(AgentSession 이 dispatch 로 넘겨 줌).</summary>
    public sealed class SessionState : ObservableObject             // 세션 상태
    {
        private string _sessionId;                                  // 세션 id
        private string _modeId = "normal";                          // 현재 모드
        private bool _busy;                                         // 턴 진행 중
        private string _status = "시작 전";                         // 상태 줄
        private string _cwd;                                        // 작업 폴더

        public ObservableCollection<ChatItem> Items { get; } = new ObservableCollection<ChatItem>();   // 대화 목록
        public ObservableCollection<PlanEntry> Plan { get; } = new ObservableCollection<PlanEntry>();   // 계획
        public ObservableCollection<SessionModeInfo> Modes { get; } = new ObservableCollection<SessionModeInfo>();   // 모드 목록
        public string SessionId { get => _sessionId; set => Set(ref _sessionId, value); }   // 세션 id
        public string CurrentModeId { get => _modeId; set => Set(ref _modeId, value); }   // 현재 모드
        public bool IsBusy { get => _busy; set => Set(ref _busy, value); }   // 진행 중
        public string StatusText { get => _status; set => Set(ref _status, value ?? ""); }   // 상태 줄
        public string WorkingDirectory { get => _cwd; set => Set(ref _cwd, value); }   // 작업 폴더

        /// <summary>모드 목록 기본값(ncn 이 주지 않을 때) — normal·plan·auto.</summary>
        public void EnsureDefaultModes()                            // 기본 모드
        {
            if (Modes.Count > 0) return;                            // 이미 있음
            Modes.Add(new SessionModeInfo { Id = "normal", Name = "normal" });   // 보통
            Modes.Add(new SessionModeInfo { Id = "plan", Name = "plan" });       // 계획(읽기 전용)
            Modes.Add(new SessionModeInfo { Id = "auto", Name = "auto" });       // 자동(묻지 않음)
        }

        /// <summary>세션 열기 결과 반영.</summary>
        public void ApplyOpen(SessionOpenResult r)                  // 열기 결과
        {
            SessionId = r.SessionId;                                // id
            if (r.Modes.Count > 0)                                  // 모드 목록 있음
            {
                Modes.Clear();                                      // 비우고
                foreach (var m in r.Modes) Modes.Add(m);            // 채움
            }
            EnsureDefaultModes();                                   // 없으면 기본
            if (!string.IsNullOrEmpty(r.CurrentModeId)) CurrentModeId = r.CurrentModeId;   // 현재 모드
        }

        /// <summary>대화 비우기(새 대화·재시작 후 load 되살리기 전).</summary>
        public void Clear()                                         // 비우기
        {
            Items.Clear();                                          // 대화
            Plan.Clear();                                           // 계획
        }

        public void AddNote(string text, bool error = false)        // 안내 추가
        {
            Items.Add(new SystemNoteItem { Text = text, IsError = error });   // 한 줄
        }

        /// <summary>id 로 카드 찾기(없으면 null).</summary>
        public ToolCardItem FindCard(string toolCallId)             // 카드 찾기
        {
            if (string.IsNullOrEmpty(toolCallId)) return null;      // id 없음
            for (var i = Items.Count - 1; i >= 0; i--)              // 뒤에서부터(최근 카드가 많음)
            {
                var c = Items[i] as ToolCardItem;                   // 카드?
                if (c != null && c.ToolCallId == toolCallId) return c;   // 찾음
            }
            return null;                                            // 없음
        }

        /// <summary>session/update 하나 적용.</summary>
        public void Apply(SessionUpdate u)                          // 적용
        {
            switch (u.Kind)                                         // 종류별
            {
                case UpdateKinds.UserMessageChunk:                  // 사용자 글(load 되살리기)
                    var lastUser = Items.Count > 0 ? Items[Items.Count - 1] as UserMessageItem : null;   // 마지막이 사용자 글?
                    if (lastUser != null) lastUser.Append(u.Text);  // 이어 붙임
                    else Items.Add(new UserMessageItem { Text = u.Text });   // 새 항목
                    break;                                          // 끝
                case UpdateKinds.AgentMessageChunk:                 // 응답 조각
                case UpdateKinds.AgentThoughtChunk:                 // 생각 조각
                    var thought = u.Kind == UpdateKinds.AgentThoughtChunk;   // 생각인지
                    var lastAgent = Items.Count > 0 ? Items[Items.Count - 1] as AgentMessageItem : null;   // 마지막이 응답?
                    if (lastAgent == null || lastAgent.IsThought != thought)   // 이어 붙일 수 없으면
                    {
                        lastAgent = new AgentMessageItem { IsThought = thought };   // 새 항목
                        Items.Add(lastAgent);                       // 추가
                    }
                    lastAgent.Append(u.Text);                       // 조각 붙임
                    break;                                          // 끝
                case UpdateKinds.ToolCall:                          // 카드 시작
                case UpdateKinds.ToolCallUpdate:                    // 카드 갱신
                    if (u.ToolCall == null) break;                  // 내용 없음
                    var card = FindCard(u.ToolCall.ToolCallId);     // 기존 카드
                    if (card == null)                               // 없으면 새로
                    {
                        card = new ToolCardItem { ToolCallId = u.ToolCall.ToolCallId };   // 카드
                        card.Apply(u.ToolCall);                     // 내용
                        Items.Add(card);                            // 추가
                    }
                    else
                    {
                        card.Apply(u.ToolCall);                     // 갱신
                    }
                    break;                                          // 끝
                case UpdateKinds.Plan:                              // 계획(통째로 바뀜)
                    Plan.Clear();                                   // 비우고
                    if (u.PlanEntries != null) foreach (var e in u.PlanEntries) Plan.Add(e);   // 채움
                    break;                                          // 끝
                case UpdateKinds.CurrentModeUpdate:                 // 모드
                case UpdateKinds.ConfigOptionUpdate:                // 설정 선택지(mode)
                    if (!string.IsNullOrEmpty(u.ModeId)) CurrentModeId = u.ModeId;   // 현재 모드
                    break;                                          // 끝
            }
        }

        /// <summary>
        /// _ncn/retry: 이번 LLM 호출로 받은 응답 조각을 지운다(ncn 은 그 조각을 버리고 다시 받음).
        /// = 목록 끝에서부터 응답·생각 항목을 지우되, 사용자 글·도구 카드·승인·안내를 만나면 멈춘다.
        /// </summary>
        public int RemoveTrailingAgentText()                        // 끝 응답 지우기
        {
            var removed = 0;                                        // 지운 수
            while (Items.Count > 0 && Items[Items.Count - 1] is AgentMessageItem)   // 끝이 응답이면
            {
                Items.RemoveAt(Items.Count - 1);                    // 지움
                removed++;                                          // 셈
            }
            return removed;                                         // 결과
        }

        /// <summary>대기 중 승인·질문 항목들(취소 시 정리용).</summary>
        public List<ChatItem> PendingPrompts()                      // 대기 항목
        {
            var list = new List<ChatItem>();                        // 결과
            foreach (var it in Items)                               // 전체
            {
                var p = it as PermissionItem;                       // 승인?
                if (p != null && p.IsPending) list.Add(p);          // 대기 중
                var q = it as QuestionItem;                         // 질문?
                if (q != null && q.IsPending) list.Add(q);          // 대기 중
            }
            return list;                                            // 결과
        }
    }
}
