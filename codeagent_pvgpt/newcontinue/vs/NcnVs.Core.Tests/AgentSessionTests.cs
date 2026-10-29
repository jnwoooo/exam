// vs/NcnVs.Core.Tests/AgentSessionTests.cs — 세션 조정자 시험: 업데이트 해석·승인 흐름(R3·R4)·fs 위임(R1·R2)·질문·재시작 후 session/load
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 사전
using System.IO;                                                    // FileNotFoundException
using System.Linq;                                                  // 질의
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Acp;                                               // ACP
using NcnVs.Core.Json;                                              // JSON-RPC
using NcnVs.Core.State;                                             // 시험 대상
using Xunit;                                                        // 시험 도구

namespace NcnVs.Core.Tests                                          // 시험 이름공간
{
    /// <summary>메모리 IDE 버퍼(경로 → 글). 쓰기 기록을 남긴다.</summary>
    internal sealed class MemoryIde : IIdeBridge                    // 가짜 IDE 창구
    {
        public Dictionary<string, string> Files { get; } = new Dictionary<string, string>();   // 열린 문서
        public List<string> Writes { get; } = new List<string>();   // 쓴 경로

        public Task<string> ReadTextFileAsync(string path)          // 읽기
        {
            string s;                                               // 글
            if (!Files.TryGetValue(path, out s)) throw new FileNotFoundException("없음", path);   // 없으면 예외
            return Task.FromResult(s);                              // 버퍼 글
        }

        public Task WriteTextFileAsync(string path, string content) // 쓰기
        {
            Files[path] = content;                                  // 버퍼 반영
            Writes.Add(path);                                       // 기록
            return Task.CompletedTask;                              // 끝
        }
    }

    public class AgentSessionTests                                  // 세션 시험
    {
        private static readonly TimeSpan Timeout = TimeSpan.FromSeconds(10);   // 기다림 한도

        private static async Task<T> WithTimeout<T>(Task<T> t)      // 한도 안에 끝나야 함
        {
            var done = await Task.WhenAny(t, Task.Delay(Timeout));  // 먼저 끝난 것
            Assert.True(done == t, "시간 초과");                     // 시간 초과면 실패
            return await t;                                         // 결과
        }

        private static async Task WithTimeout(Task t)               // 한도 안에 끝나야 함(결과 없음)
        {
            var done = await Task.WhenAny(t, Task.Delay(Timeout));  // 먼저 끝난 것
            Assert.True(done == t, "시간 초과");                     // 시간 초과면 실패
            await t;                                                // 예외 전파
        }

        private static (AgentSession s, List<FakeAgent> agents) NewSession(IIdeBridge ide = null)   // 세션 + 가짜 에이전트들(재시작 때마다 하나씩)
        {
            var agents = new List<FakeAgent>();                     // 만든 에이전트
            var s = new AgentSession(new AgentSessionOptions        // 세션
            {
                ProcessFactory = () => { var a = new FakeAgent(); lock (agents) agents.Add(a); return a; },   // 띄울 때마다 새 가짜
                Ide = ide ?? new MemoryIde(),                       // 파일 창구
                Post = a => a(),                                    // 시험: 바로 실행
                RestartDelay = TimeSpan.FromMilliseconds(10),       // 빠른 재시작
            });
            return (s, agents);                                     // 결과
        }

        private static JObject ToolCall(string id, string status, string title = "Edit(a.cs)")   // tool_call 알림 본문
        {
            return new JObject                                      // 카드
            {
                ["sessionUpdate"] = "tool_call", ["toolCallId"] = id, ["title"] = title, ["kind"] = "edit", ["status"] = status,   // 기본 필드
                ["content"] = new JArray(new JObject { ["type"] = "diff", ["path"] = "C:\\w\\a.cs", ["oldText"] = "x = 1\n", ["newText"] = "x = 2\n" }),   // diff
                ["rawInput"] = new JObject { ["file_path"] = "a.cs" },   // 인자
            };
        }

        private static JArray Options()                             // 승인 선택지 3개(cn 원문)
        {
            return new JArray(
                new JObject { ["optionId"] = "allow_once", ["name"] = "Continue", ["kind"] = "allow_once" },
                new JObject { ["optionId"] = "allow_always", ["name"] = "Continue + don't ask again", ["kind"] = "allow_always" },
                new JObject { ["optionId"] = "reject_once", ["name"] = "No, and tell Continue what to do differently", ["kind"] = "reject_once" });
        }

        [Fact]
        public async Task Start_sends_initialize_with_capabilities_then_session_new()   // 시작 순서·능력
        {
            var (s, agents) = NewSession();                         // 세션
            await WithTimeout(s.StartAsync("C:\\w"));             // 시작
            var reqs = agents[0].Requests.ToArray();                // 받은 요청
            Assert.Equal("initialize", reqs[0].method);             // 첫 요청
            Assert.True((bool)reqs[0].prms["clientCapabilities"]["fs"]["writeTextFile"]);   // fs 위임 능력
            Assert.NotNull(reqs[0].prms["clientCapabilities"]["elicitation"]["form"]);   // 폼 질문 능력
            Assert.Equal(1, (int)reqs[0].prms["protocolVersion"]);  // 규격 1
            Assert.Equal("session/new", reqs[1].method);            // 둘째
            Assert.Equal("C:\\w", (string)reqs[1].prms["cwd"]);     // 작업 폴더
            Assert.Equal("sess-1", s.State.SessionId);              // 세션 id
            Assert.Equal(new[] { "normal", "plan", "auto" }, s.State.Modes.Select(m => m.Id).ToArray());   // 모드 목록
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Updates_build_messages_cards_plan_and_mode()   // 업데이트 해석
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            a.Handlers["session/prompt"] = async p =>               // 턴 흉내
            {
                await a.ChunkAsync("할인율을 ");                     // 조각 1
                await a.ChunkAsync("**20%**로");                    // 조각 2
                await a.UpdateAsync(ToolCall("call_1", "pending")); // 카드
                await a.UpdateAsync(new JObject { ["sessionUpdate"] = "tool_call_update", ["toolCallId"] = "call_1", ["status"] = "completed",   // 카드 끝
                    ["content"] = new JArray(new JObject { ["type"] = "content", ["content"] = new JObject { ["type"] = "text", ["text"] = "Successfully edited" } }) });
                await a.UpdateAsync(new JObject { ["sessionUpdate"] = "plan", ["entries"] = new JArray(   // 계획
                    new JObject { ["content"] = "읽기", ["priority"] = "medium", ["status"] = "completed" },
                    new JObject { ["content"] = "고치기", ["priority"] = "medium", ["status"] = "pending" }) });
                await a.UpdateAsync(new JObject { ["sessionUpdate"] = "current_mode_update", ["currentModeId"] = "plan" });   // 모드
                return new JObject { ["stopReason"] = "end_turn", ["usage"] = new JObject { ["totalTokens"] = 38, ["inputTokens"] = 30, ["outputTokens"] = 8 } };   // 응답
            };
            var r = await WithTimeout(s.SendAsync("이 파일 할인율을 20%로"));   // 보내기
            Assert.Equal("end_turn", r.StopReason);                 // 정상 끝
            var items = s.State.Items;                              // 대화 목록
            Assert.IsType<UserMessageItem>(items[0]);               // 사용자 글
            Assert.Equal("할인율을 **20%**로", ((AgentMessageItem)items[1]).Text);   // 조각이 한 항목으로
            var card = Assert.IsType<ToolCardItem>(items[2]);       // 카드
            Assert.Equal("completed", card.Status);                 // 갱신된 상태
            Assert.Equal("Edit(a.cs)", card.Title);                 // 제목 유지(갱신에 없음)
            Assert.Equal("Successfully edited", card.OutputText);   // 결과 글
            Assert.Equal(2, s.State.Plan.Count);                    // 계획 2개
            Assert.True(s.State.Plan[0].IsDone);                    // 첫 항목 완료
            Assert.Equal("plan", s.State.CurrentModeId);            // 모드 바뀜
            Assert.Contains("38", s.State.StatusText);              // 사용량 표시
            var prompt = agents[0].Requests.First(q => q.method == "session/prompt").prms;   // 보낸 prompt
            Assert.Equal("text", (string)prompt["prompt"][0]["type"]);   // 글 블록
            Assert.Equal("sess-1", (string)prompt["sessionId"]);    // 세션
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Retry_notification_removes_partial_agent_text()   // _ncn/retry → 조각 지움
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            a.Handlers["session/prompt"] = async p =>               // 재시도 흉내
            {
                await a.ChunkAsync("깨진 조각");                     // 버려질 조각
                await a.Conn.SendNotificationAsync("_ncn/retry", new JObject { ["sessionId"] = "sess-1", ["attempt"] = 1, ["delayMs"] = 10, ["error"] = "503" });   // 재시도
                await a.ChunkAsync("다시 받은 답");                  // 새 조각
                return new JObject { ["stopReason"] = "end_turn" }; // 응답
            };
            await WithTimeout(s.SendAsync("질문"));                 // 보내기
            var texts = s.State.Items.OfType<AgentMessageItem>().Select(m => m.Text).ToArray();   // 응답 글들
            Assert.Equal(new[] { "다시 받은 답" }, texts);           // 깨진 조각은 사라짐
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Permission_flow_sends_selected_option_id()   // 승인: 고른 optionId 그대로
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            JToken answer = null;                                   // ncn 이 받은 답
            a.Handlers["session/prompt"] = async p =>               // 턴 흉내
            {
                await a.UpdateAsync(ToolCall("call_1", "pending")); // 카드 먼저(규칙)
                var tc = ToolCall("call_1", "pending"); tc.Remove("sessionUpdate");   // 승인 요청의 toolCall
                answer = await a.Conn.SendRequestAsync("session/request_permission", new JObject { ["sessionId"] = "sess-1", ["toolCall"] = tc, ["options"] = Options() });   // 승인 요청
                return new JObject { ["stopReason"] = "end_turn" }; // 응답
            };
            PermissionItem seen = null;                             // 받은 승인 항목
            s.PermissionRequested += item => { seen = item; s.ChoosePermission(item, "allow_always"); };   // 두 번째 버튼
            await WithTimeout(s.SendAsync("고쳐 줘"));              // 보내기
            Assert.Equal("selected", (string)answer["outcome"]["outcome"]);   // 선택
            Assert.Equal("allow_always", (string)answer["outcome"]["optionId"]);   // 그 id
            Assert.Equal(new[] { "Continue", "Continue + don't ask again", "No, and tell Continue what to do differently" }, seen.Request.Options.Select(o => o.Name).ToArray());   // 이름 그대로
            Assert.Equal("x = 2\n", seen.Request.ToolCall.FirstDiff().NewText);   // 비교 창용 diff
            Assert.False(seen.IsPending);                           // 끝남
            Assert.Equal("선택: Continue + don't ask again", seen.ResultText);   // 결과 표시
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Cancel_sends_session_cancel_before_cancelled_permission_reply()   // R3 순서
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            JToken answer = null;                                   // ncn 이 받은 답
            a.Handlers["session/prompt"] = async p =>               // 턴 흉내
            {
                await a.UpdateAsync(ToolCall("call_1", "pending")); // 카드
                answer = await a.Conn.SendRequestAsync("session/request_permission", new JObject { ["sessionId"] = "sess-1", ["toolCall"] = new JObject { ["toolCallId"] = "call_1" }, ["options"] = Options() });   // 승인 요청
                return new JObject { ["stopReason"] = "cancelled" };   // 취소로 끝
            };
            s.PermissionRequested += item => { var _ = s.CancelAsync(); };   // 승인 대신 중단 버튼
            var r = await WithTimeout(s.SendAsync("고쳐 줘"));      // 보내기
            Assert.Equal("cancelled", r.StopReason);                // 취소로 끝
            Assert.Equal("cancelled", (string)answer["outcome"]["outcome"]);   // 승인 답 = cancelled
            var lines = a.ToAgent.Snapshot();                       // ncn 이 받은 줄 순서
            var cancelIdx = lines.FindIndex(l => l.Contains("\"session/cancel\""));   // 중단 알림 위치
            var replyIdx = lines.FindIndex(l => l.Contains("\"cancelled\""));   // 승인 답 위치
            Assert.True(cancelIdx >= 0 && cancelIdx < replyIdx, "session/cancel 이 승인 답보다 먼저여야 함");   // R3
            Assert.Contains(s.State.Items.OfType<SystemNoteItem>(), n => n.Text.Contains("중단"));   // 안내
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Two_permissions_in_one_response_are_asked_in_turn()   // R4: 연달아 오는 승인
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            var answers = new List<string>();                       // 받은 답
            a.Handlers["session/prompt"] = async p =>               // 턴 흉내
            {
                foreach (var id in new[] { "call_1", "call_2" })    // 도구 두 개
                {
                    var r = await a.Conn.SendRequestAsync("session/request_permission", new JObject { ["sessionId"] = "sess-1", ["toolCall"] = new JObject { ["toolCallId"] = id, ["title"] = id }, ["options"] = Options() });   // 승인 요청
                    answers.Add((string)r["outcome"]["optionId"]);  // 기록
                }
                return new JObject { ["stopReason"] = "end_turn", ["_meta"] = new JObject { ["ncn"] = new JObject { ["stoppedByRejection"] = true } } };   // 거부로 멈춤
            };
            s.PermissionRequested += item => s.ChoosePermission(item, item.Title == "call_1" ? "reject_once" : "allow_once");   // 첫째 거부, 둘째 허용
            var res = await WithTimeout(s.SendAsync("두 개"));      // 보내기
            Assert.Equal(new[] { "reject_once", "allow_once" }, answers);   // 차례로
            Assert.True(res.StoppedByRejection);                    // 확장 표시
            Assert.Contains(s.State.Items.OfType<SystemNoteItem>(), n => n.Text.Contains("거부"));   // 안내
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Fs_read_uses_ide_buffer_and_missing_file_is_error()   // R2
        {
            var ide = new MemoryIde();                              // 가짜 IDE
            ide.Files["C:\\w\\a.cs"] = "l1\nl2\nl3\n";              // 저장 안 한 버퍼
            var (s, agents) = NewSession(ide);                      // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var a = agents[0];                                      // 에이전트
            var whole = await WithTimeout(a.Conn.SendRequestAsync("fs/read_text_file", new JObject { ["sessionId"] = "sess-1", ["path"] = "C:\\w\\a.cs" }));   // 전체 읽기
            Assert.Equal("l1\nl2\nl3\n", (string)whole["content"]); // 버퍼 글
            var part = await WithTimeout(a.Conn.SendRequestAsync("fs/read_text_file", new JObject { ["sessionId"] = "sess-1", ["path"] = "C:\\w\\a.cs", ["line"] = 2, ["limit"] = 1 }));   // 2번째 줄만
            Assert.Equal("l2\n", (string)part["content"]);          // 줄 범위
            var e = await Assert.ThrowsAsync<JsonRpcException>(() => a.Conn.SendRequestAsync("fs/read_text_file", new JObject { ["sessionId"] = "sess-1", ["path"] = "C:\\w\\none.cs" }));   // 없는 파일
            Assert.Equal(JsonRpcException.ResourceNotFound, e.Code);   // -32002 → ncn 이 디스크로
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Fs_write_completes_before_reply()          // R1: 쓰기 끝난 뒤 응답
        {
            var ide = new MemoryIde();                              // 가짜 IDE
            var (s, agents) = NewSession(ide);                      // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var r = await WithTimeout(agents[0].Conn.SendRequestAsync("fs/write_text_file", new JObject { ["sessionId"] = "sess-1", ["path"] = "C:\\w\\b.cs", ["content"] = "새 글\n" }));   // 쓰기 요청
            Assert.Equal(JTokenType.Object, r.Type);                // 빈 객체 응답
            Assert.Equal("새 글\n", ide.Files["C:\\w\\b.cs"]);       // 응답 시점에 이미 반영
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Elicitation_choice_is_returned_as_form_content()   // AskQuestion(elicitation)
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            QuestionItem q = null;                                  // 받은 질문
            s.QuestionRequested += item => { q = item; s.AnswerQuestion(item, new QuestionAnswer { Choice = item.Request.Choices[1].Value }); };   // 둘째 선택
            var schema = new JObject                                // ncn convert.question_schema 모양
            {
                ["type"] = "object",
                ["properties"] = new JObject
                {
                    ["choice"] = new JObject { ["type"] = "string", ["title"] = "어느 쪽?", ["oneOf"] = new JArray(new JObject { ["const"] = "A", ["title"] = "A" }, new JObject { ["const"] = "B", ["title"] = "B" }) },
                    ["custom"] = new JObject { ["type"] = "string", ["title"] = "Custom answer (optional)" },
                },
            };
            var r = await WithTimeout(agents[0].Conn.SendRequestAsync("elicitation/create", new JObject { ["sessionId"] = "sess-1", ["toolCallId"] = "call_q", ["mode"] = "form", ["message"] = "어느 쪽?", ["requestedSchema"] = schema }));   // 질문
            Assert.Equal("accept", (string)r["action"]);            // 수락
            Assert.Equal("B", (string)r["content"]["choice"]);      // 고른 값
            Assert.Null(r["content"]["custom"]);                    // 직접 입력 없음
            Assert.Equal("어느 쪽?", q.Request.Message);             // 질문 글
            Assert.Equal("custom", q.Request.TextKey);              // 직접 입력 칸
            Assert.Equal("call_q", q.Request.ToolCallId);           // 카드 연결
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Ncn_ask_custom_answer_wins()               // _ncn/ask 직접 입력
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            s.QuestionRequested += item => s.AnswerQuestion(item, new QuestionAnswer { Choice = "A", Text = "  직접 " });   // 둘 다
            var r = await WithTimeout(agents[0].Conn.SendRequestAsync("_ncn/ask", new JObject { ["sessionId"] = "sess-1", ["toolCallId"] = "c", ["question"] = "?", ["options"] = new JArray("A", "B"), ["defaultAnswer"] = "A" }));   // 질문
            Assert.Equal("직접", (string)r["answer"]);               // 앞뒤 공백 뺀 직접 입력
            Assert.True((bool)r["isCustom"]);                       // 직접 입력 표시
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Crash_restarts_process_and_loads_last_session()   // 비정상 종료 → 재시작 + session/load
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            var restarted = new TaskCompletionSource<bool>();       // 재시작 신호
            s.Restarted += () => restarted.TrySetResult(true);      // 기다림
            agents[0].Crash(3);                                     // 죽기
            await WithTimeout(restarted.Task);                      // 재시작 완료
            Assert.Equal(2, agents.Count);                          // 새 프로세스
            var reqs = agents[1].Requests.ToArray();                // 새 에이전트가 받은 요청
            Assert.Equal("initialize", reqs[0].method);             // 다시 initialize
            Assert.Equal("session/load", reqs[1].method);           // 그리고 load
            Assert.Equal("sess-1", (string)reqs[1].prms["sessionId"]);   // 마지막 세션
            Assert.Equal("sess-1", s.State.SessionId);              // id 유지
            Assert.Contains(s.State.Items.OfType<SystemNoteItem>(), n => n.Text.Contains("이어 갑니다"));   // 안내
            s.Dispose();                                            // 정리
        }

        [Fact]
        public async Task Prompt_error_shows_cn_message()            // -32603 data.message 표시
        {
            var (s, agents) = NewSession();                         // 세션
            await s.StartAsync("C:\\w");                            // 시작
            agents[0].Handlers["session/prompt"] = p => throw new JsonRpcException(-32603, "Internal error", new JObject { ["message"] = "Error: 401 Unauthorized" });   // LLM 오류
            var r = await WithTimeout(s.SendAsync("질문"));         // 보내기
            Assert.Null(r);                                         // 실패
            Assert.Contains(s.State.Items.OfType<SystemNoteItem>(), n => n.IsError && n.Text == "Error: 401 Unauthorized");   // cn 원문
            Assert.False(s.State.IsBusy);                           // 바쁨 풀림
            s.Dispose();                                            // 정리
        }
    }
}
