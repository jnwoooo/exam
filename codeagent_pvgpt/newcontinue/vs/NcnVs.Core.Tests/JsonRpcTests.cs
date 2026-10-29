// vs/NcnVs.Core.Tests/JsonRpcTests.cs — JSON-RPC 왕복 시험(요청·응답·오류·알림 순서·JSON 아닌 줄·날짜 글 보존)
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 목록
using System.IO;                                                    // IOException
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Json;                                              // 시험 대상
using Xunit;                                                        // 시험 도구

namespace NcnVs.Core.Tests                                          // 시험 이름공간
{
    public class JsonRpcTests                                       // JSON-RPC 시험
    {
        private static (JsonRpcConnection a, JsonRpcConnection b, LinePipe ab, LinePipe ba) Pair()   // 서로 이어진 연결 두 개
        {
            var ab = new LinePipe();                                // a → b
            var ba = new LinePipe();                                // b → a
            var a = new JsonRpcConnection(ba.Reader, ab.Writer);    // a 쪽
            var b = new JsonRpcConnection(ab.Reader, ba.Writer);    // b 쪽
            return (a, b, ab, ba);                                  // 결과
        }

        [Fact]
        public async Task Request_roundtrip_returns_result()        // 요청 → 결과
        {
            var (a, b, ab, _) = Pair();                             // 연결
            b.RequestHandler = (m, p) => Task.FromResult<JToken>(new JObject { ["echo"] = p["x"], ["method"] = m });   // 되돌려 주기
            a.Start(); b.Start();                                   // 시작
            var r = await a.SendRequestAsync("test/echo", new JObject { ["x"] = "한글 값" });   // 요청
            Assert.Equal("한글 값", (string)r["echo"]);              // 값 그대로
            Assert.Equal("test/echo", (string)r["method"]);         // 메서드
            var line = ab.Snapshot()[0];                            // 실제로 나간 줄
            Assert.Contains("\"jsonrpc\":\"2.0\"", line);            // 규격 표시
            Assert.Contains("한글 값", line);                        // 한글 이스케이프 없음
            Assert.DoesNotContain("\n", line);                      // 한 줄
        }

        [Fact]
        public async Task Error_response_becomes_exception()        // 오류 응답 → 예외
        {
            var (a, b, _, _) = Pair();                              // 연결
            b.RequestHandler = (m, p) => throw new JsonRpcException(-32602, "Invalid params", new JObject { ["message"] = "모르는 모드" });   // 오류
            a.Start(); b.Start();                                   // 시작
            var e = await Assert.ThrowsAsync<JsonRpcException>(() => a.SendRequestAsync("session/set_mode", new JObject()));   // 예외
            Assert.Equal(-32602, e.Code);                           // 코드
            Assert.Equal("모르는 모드", e.DisplayMessage);           // data.message 우선
        }

        [Fact]
        public async Task Unknown_method_without_handler_is_method_not_found()   // 처리기 없음 → -32601
        {
            var (a, b, _, _) = Pair();                              // 연결
            a.Start(); b.Start();                                   // 시작(b 처리기 없음)
            var e = await Assert.ThrowsAsync<JsonRpcException>(() => a.SendRequestAsync("nope", null));   // 요청
            Assert.Equal(JsonRpcException.MethodNotFound, e.Code);  // -32601
        }

        [Fact]
        public async Task Notifications_arrive_in_order_before_response()   // 알림은 응답보다 먼저, 순서대로
        {
            var (a, b, _, _) = Pair();                              // 연결
            var seen = new List<string>();                          // 받은 알림
            a.NotificationHandler = (m, p) => { lock (seen) seen.Add((string)p["n"]); };   // 기록
            b.RequestHandler = async (m, p) =>                      // 요청 처리 중 알림 3개
            {
                for (var i = 0; i < 3; i++) await b.SendNotificationAsync("session/update", new JObject { ["n"] = i.ToString() });   // 알림
                return new JObject { ["done"] = true };             // 응답
            };
            a.Start(); b.Start();                                   // 시작
            await a.SendRequestAsync("session/prompt", new JObject());   // 요청
            lock (seen) Assert.Equal(new[] { "0", "1", "2" }, seen); // 응답 시점에 이미 순서대로 다 받음
        }

        [Fact]
        public async Task Non_json_line_is_ignored_and_dates_stay_strings()   // JSON 아닌 줄 무시, 날짜 글 보존
        {
            var (a, b, ab, ba) = Pair();                            // 연결
            string got = null;                                      // 받은 값
            var logs = new List<string>();                          // 로그
            a.Log = s => { lock (logs) logs.Add(s); };              // 로그 수집
            a.NotificationHandler = (m, p) => got = (string)p["t"]; // 값 기록
            a.Start(); b.Start();                                   // 시작
            await ba.Writer.WriteAsync("이건 JSON 이 아님\n");       // 잘못된 줄
            await b.SendNotificationAsync("x", new JObject { ["t"] = "2026-10-30T12:00:00Z" });   // 날짜 모양 글
            await a.SendRequestAsync("ping", null).ContinueWith(_ => { });   // 왕복으로 처리 완료 기다림(-32601 무시)
            Assert.Equal("2026-10-30T12:00:00Z", got);             // 글 그대로(DateTime 변환 없음)
            lock (logs) Assert.Contains(logs, l => l.Contains("JSON 이 아닌 줄"));   // 로그에 남음
        }

        [Fact]
        public async Task Pending_request_fails_when_connection_closes()   // 끊기면 대기 요청 실패
        {
            var (a, b, _, ba) = Pair();                             // 연결
            var closed = new TaskCompletionSource<bool>();          // 닫힘 신호
            a.Closed += _ => closed.TrySetResult(true);             // 닫힘 감지
            b.RequestHandler = (m, p) => new TaskCompletionSource<JToken>().Task;   // 영원히 응답 안 함
            a.Start(); b.Start();                                   // 시작
            var pending = a.SendRequestAsync("session/prompt", new JObject());   // 요청
            ba.Close();                                             // 상대 stdout EOF
            await Assert.ThrowsAsync<IOException>(() => pending);   // 실패로 끝남
            Assert.True(await closed.Task);                         // 닫힘 사건
        }
    }
}
