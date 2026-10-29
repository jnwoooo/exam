// vs/NcnVs.Core/Acp/SessionUpdate.cs — session/update 알림 하나를 종류별로 해석(ACP_연동.md 3-5) (cn 원본 없음)
using System.Collections.Generic;                                   // 목록
using Newtonsoft.Json.Linq;                                         // JObject

namespace NcnVs.Core.Acp                                            // ACP 이름공간
{
    /// <summary>sessionUpdate 값(종류).</summary>
    public static class UpdateKinds                                 // 종류 상수
    {
        public const string UserMessageChunk = "user_message_chunk";     // 사용자 글 조각(load 되살리기)
        public const string AgentMessageChunk = "agent_message_chunk";   // 응답 글 조각
        public const string AgentThoughtChunk = "agent_thought_chunk";   // 생각·압축 안내
        public const string ToolCall = "tool_call";                      // 도구 카드 시작
        public const string ToolCallUpdate = "tool_call_update";         // 도구 카드 갱신
        public const string Plan = "plan";                               // 계획
        public const string CurrentModeUpdate = "current_mode_update";   // 모드 바뀜
        public const string ConfigOptionUpdate = "config_option_update"; // 설정 선택지 바뀜
        public const string AvailableCommandsUpdate = "available_commands_update";   // 명령 목록(무시)
    }

    /// <summary>해석한 업데이트 하나. 종류에 맞는 필드만 채워진다.</summary>
    public sealed class SessionUpdate                               // 업데이트
    {
        public string Kind { get; set; }                            // sessionUpdate 값
        public string Text { get; set; }                            // 글 조각(…_chunk)
        public ToolCallInfo ToolCall { get; set; }                  // 도구(tool_call·tool_call_update)
        public List<PlanEntry> PlanEntries { get; set; }            // 계획(plan)
        public string ModeId { get; set; }                          // 모드(current_mode_update·config_option_update)
        public JObject Raw { get; set; }                            // 원본(모르는 종류 대비)

        /// <summary>params.update 객체 → 업데이트.</summary>
        public static SessionUpdate Parse(JObject update)           // 해석
        {
            var u = new SessionUpdate { Kind = update.Value<string>("sessionUpdate"), Raw = update };   // 종류·원본
            switch (u.Kind)                                         // 종류별
            {
                case UpdateKinds.UserMessageChunk:                  // 글 조각들
                case UpdateKinds.AgentMessageChunk:
                case UpdateKinds.AgentThoughtChunk:
                    var content = update["content"] as JObject;     // 내용 블록 하나
                    if (content != null && content.Value<string>("type") == "text")   // 글 블록이면
                        u.Text = content.Value<string>("text") ?? "";   // 글
                    else if (content != null && content.Value<string>("type") == "resource_link")   // 파일 링크(load 되살리기)
                        u.Text = "@" + (content.Value<string>("name") ?? content.Value<string>("uri"));   // @이름
                    else                                            // 그 밖(이미지 등)
                        u.Text = "";                                // 표시 안 함
                    break;                                          // 끝
                case UpdateKinds.ToolCall:                          // 카드 시작
                case UpdateKinds.ToolCallUpdate:                    // 카드 갱신
                    u.ToolCall = ToolCallInfo.FromJson(update);     // 같은 필드 구조
                    break;                                          // 끝
                case UpdateKinds.Plan:                              // 계획
                    u.PlanEntries = new List<PlanEntry>();          // 목록
                    var entries = update["entries"] as JArray;      // 항목들
                    if (entries != null)                            // 있으면
                        foreach (var e in entries)                  // 하나씩
                        {
                            var eo = e as JObject;                  // 객체
                            if (eo == null) continue;               // 아니면 건너뜀
                            u.PlanEntries.Add(new PlanEntry { Content = eo.Value<string>("content"), Priority = eo.Value<string>("priority"), Status = eo.Value<string>("status") });   // 추가
                        }
                    break;                                          // 끝
                case UpdateKinds.CurrentModeUpdate:                 // 모드 바뀜
                    u.ModeId = update.Value<string>("currentModeId");   // 새 모드
                    break;                                          // 끝
                case UpdateKinds.ConfigOptionUpdate:                // 설정 선택지 바뀜
                    var opts = update["configOptions"] as JArray;   // 선택지 목록
                    if (opts != null)                               // 있으면
                        foreach (var o in opts)                     // 하나씩
                        {
                            var oo = o as JObject;                  // 객체
                            if (oo != null && oo.Value<string>("id") == "mode")   // mode 선택지
                                u.ModeId = oo.Value<string>("currentValue");   // 현재 값
                        }
                    break;                                          // 끝
            }
            return u;                                               // 결과
        }
    }
}
