// vs/NcnVs.Core/Json/JsonRpcConnection.cs — 줄 단위 JSON-RPC 2.0 양방향 연결(한 메시지 = 한 줄, UTF-8, '\n') (cn 원본 없음 — ACP 전송층)
using System;                                                       // 기본 형식
using System.Collections.Concurrent;                                // 대기 중 요청 표
using System.IO;                                                    // TextReader·TextWriter
using System.Threading;                                             // 잠금·취소
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json;                                              // JsonTextReader·Formatting
using Newtonsoft.Json.Linq;                                         // JToken·JObject

namespace NcnVs.Core.Json                                           // JSON-RPC 이름공간
{
    /// <summary>
    /// stdin/stdout 한 쌍 위의 JSON-RPC 2.0. 우리가 보내는 요청(응답 대기), 상대가 보내는 요청(처리기 호출 후 응답),
    /// 알림(양방향)을 다룬다. 알림 처리기는 읽기 루프에서 **받은 순서대로 동기 호출**한다(ACP: 알림은 응답보다 먼저 도착).
    /// 요청 처리기는 첫 await 까지 동기로 돌고 나머지는 비동기(승인처럼 오래 걸려도 읽기 루프를 막지 않음).
    /// </summary>
    public sealed class JsonRpcConnection : IDisposable             // 연결 형식
    {
        private readonly TextReader _reader;                        // 상대 출력(우리가 읽음)
        private readonly TextWriter _writer;                        // 상대 입력(우리가 씀)
        private readonly SemaphoreSlim _writeLock = new SemaphoreSlim(1, 1);   // 한 줄씩 쓰기(섞임 방지)
        private readonly ConcurrentDictionary<long, TaskCompletionSource<JToken>> _pending =   // 응답 기다리는 요청
            new ConcurrentDictionary<long, TaskCompletionSource<JToken>>();     // id → 완료 원천
        private long _nextId = -1;                                  // 다음 요청 id(0 부터)
        private int _closed;                                        // 닫힘 표시(0/1)
        private Task _readTask;                                     // 읽기 루프 작업

        /// <summary>상대가 보낸 요청 처리기(method, params) → result. JsonRpcException 을 던지면 그 오류로 응답.</summary>
        public Func<string, JToken, Task<JToken>> RequestHandler { get; set; }   // 요청 처리기
        /// <summary>상대가 보낸 알림 처리기(method, params). 읽기 스레드에서 순서대로 불린다.</summary>
        public Action<string, JToken> NotificationHandler { get; set; }          // 알림 처리기
        /// <summary>진단 로그(JSON 아닌 줄, 처리기 오류 등).</summary>
        public Action<string> Log { get; set; }                     // 로그 함수
        /// <summary>읽기 루프가 끝남(EOF 면 null, 오류면 그 예외).</summary>
        public event Action<Exception> Closed;                      // 닫힘 사건

        public bool IsClosed => Volatile.Read(ref _closed) != 0;    // 닫혔는지

        public JsonRpcConnection(TextReader reader, TextWriter writer)   // 생성자
        {
            _reader = reader ?? throw new ArgumentNullException(nameof(reader));   // 읽기 쪽 필수
            _writer = writer ?? throw new ArgumentNullException(nameof(writer));   // 쓰기 쪽 필수
        }

        /// <summary>읽기 루프 시작(처리기를 다 건 뒤 한 번 부른다).</summary>
        public void Start()                                         // 시작
        {
            if (_readTask != null) return;                          // 두 번 시작 방지
            _readTask = Task.Run(() => ReadLoopAsync());                 // 스레드 풀에서 읽기
        }

        /// <summary>줄 하나를 JSON 으로 해석. 날짜처럼 보이는 글도 글 그대로 둔다(DateParseHandling.None).</summary>
        public static JToken ParseLine(string line)                 // 줄 해석
        {
            using (var sr = new StringReader(line))                 // 글 읽개
            using (var jr = new JsonTextReader(sr))                 // JSON 읽개
            {
                jr.DateParseHandling = DateParseHandling.None;      // "2026-10-30T…" 를 DateTime 으로 바꾸지 않음
                jr.FloatParseHandling = FloatParseHandling.Double;  // 실수는 double
                return JToken.ReadFrom(jr);                         // 토큰 하나
            }
        }

        /// <summary>토큰 → 한 줄 JSON(줄바꿈 없음, 한글 이스케이프 없음).</summary>
        public static string Serialize(JToken token)                // 직렬화
        {
            return token.ToString(Formatting.None);                 // 들여쓰기 없이(글 속 줄바꿈은 \n 으로 이스케이프됨)
        }

        private async Task ReadLoopAsync()                          // 읽기 루프
        {
            Exception error = null;                                 // 끝난 이유
            try
            {
                while (true)                                        // 줄마다
                {
                    var line = await _reader.ReadLineAsync().ConfigureAwait(false);   // 한 줄 읽기(\r\n 도 처리됨)
                    if (line == null) break;                        // EOF → 끝
                    if (line.Trim().Length == 0) continue;          // 빈 줄(시작 시 BOM 방지용 줄 포함)은 건너뜀
                    HandleLine(line);                               // 처리
                }
            }
            catch (Exception e)                                     // 읽기 실패(파이프 끊김 등)
            {
                error = e;                                          // 기록
            }
            finally
            {
                MarkClosed(error);                                  // 정리
            }
        }

        private void MarkClosed(Exception error)                    // 닫힘 처리(한 번만)
        {
            if (Interlocked.Exchange(ref _closed, 1) != 0) return;  // 이미 닫힘
            var ex = new IOException("ncn 연결이 끊어졌습니다.", error);   // 대기 중 요청에 줄 예외
            foreach (var kv in _pending)                            // 기다리던 요청마다
            {
                TaskCompletionSource<JToken> tcs;                   // 완료 원천
                if (_pending.TryRemove(kv.Key, out tcs)) tcs.TrySetException(ex);   // 실패로 끝냄
            }
            try { Closed?.Invoke(error); }                          // 닫힘 알림
            catch (Exception e) { Log?.Invoke("Closed 처리기 오류: " + e); }   // 처리기 오류는 로그만
        }

        private void HandleLine(string line)                        // 받은 줄 하나
        {
            JObject msg;                                            // 메시지
            try
            {
                msg = ParseLine(line) as JObject;                   // 객체여야 함
            }
            catch (JsonException e)                                 // JSON 아님
            {
                Log?.Invoke("JSON 이 아닌 줄을 버림: " + Truncate(line) + " (" + e.Message + ")");   // 진단만
                return;                                             // 버림
            }
            if (msg == null) { Log?.Invoke("객체가 아닌 JSON 을 버림: " + Truncate(line)); return; }   // 배열 등은 버림
            var method = msg.Value<string>("method");               // 메서드(요청·알림)
            var id = msg["id"];                                     // id(요청·응답)
            if (method != null)                                     // 요청 또는 알림
            {
                var prms = msg["params"];                           // 인자
                if (id == null || id.Type == JTokenType.Null)       // id 없음 → 알림
                {
                    try { NotificationHandler?.Invoke(method, prms); }   // 순서대로 동기 호출
                    catch (Exception e) { Log?.Invoke("알림 처리 오류(" + method + "): " + e); }   // 루프는 계속
                }
                else
                {
                    var _ = HandleRequestAsync(id, method, prms);   // 요청 → 처리 후 응답(기다리지 않음)
                }
                return;                                             // 끝
            }
            if (id == null || id.Type != JTokenType.Integer)        // 우리 요청 id 는 정수
            {
                Log?.Invoke("알 수 없는 응답을 버림: " + Truncate(line));   // 진단
                return;                                             // 버림
            }
            TaskCompletionSource<JToken> waiter;                    // 기다리던 요청
            if (!_pending.TryRemove((long)id, out waiter))  // 표에서 꺼냄
            {
                Log?.Invoke("기다리지 않던 응답: " + Truncate(line)); // 이미 끝났거나 모르는 id
                return;                                             // 버림
            }
            var err = msg["error"] as JObject;                      // 오류 응답?
            if (err != null)                                        // 오류
            {
                var code = err.Value<int?>("code") ?? JsonRpcException.InternalError;   // 코드
                var text = err.Value<string>("message") ?? "error"; // 글
                waiter.TrySetException(new JsonRpcException(code, text, err["data"]));   // 예외로 끝냄
            }
            else
            {
                waiter.TrySetResult(msg["result"] ?? JValue.CreateNull());   // 결과(없으면 null)
            }
        }

        private async Task HandleRequestAsync(JToken id, string method, JToken prms)   // 상대 요청 처리
        {
            JObject reply;                                          // 보낼 응답
            try
            {
                var handler = RequestHandler;                       // 처리기
                if (handler == null) throw new JsonRpcException(JsonRpcException.MethodNotFound, "Method not found");   // 없으면 -32601
                var result = await handler(method, prms).ConfigureAwait(false);   // 처리
                reply = new JObject { ["jsonrpc"] = "2.0", ["id"] = id.DeepClone(), ["result"] = result ?? JValue.CreateNull() };   // 성공 응답
            }
            catch (JsonRpcException e)                              // 정해진 오류
            {
                reply = ErrorReply(id, e.Code, e.Message, e.ErrorData);  // 그 오류로
            }
            catch (Exception e)                                     // 그 밖 오류
            {
                Log?.Invoke("요청 처리 오류(" + method + "): " + e);   // 진단
                reply = ErrorReply(id, JsonRpcException.InternalError, e.Message, null);   // -32603
            }
            try { await WriteAsync(reply).ConfigureAwait(false); } // 응답 보내기
            catch (Exception e) { Log?.Invoke("응답 쓰기 실패(" + method + "): " + e.Message); }   // 연결 끊김 등
        }

        private static JObject ErrorReply(JToken id, int code, string message, JToken data)   // 오류 응답 만들기
        {
            var err = new JObject { ["code"] = code, ["message"] = message ?? "" };   // 코드·글
            if (data != null) err["data"] = data.DeepClone();       // 자료(있으면)
            return new JObject { ["jsonrpc"] = "2.0", ["id"] = id.DeepClone(), ["error"] = err };   // 응답
        }

        /// <summary>요청 보내고 응답(result)을 기다린다. 오류 응답이면 JsonRpcException.</summary>
        public async Task<JToken> SendRequestAsync(string method, JToken prms, CancellationToken ct = default(CancellationToken))   // 요청
        {
            if (IsClosed) throw new IOException("ncn 연결이 닫혀 있습니다.");   // 닫혔으면 바로 실패
            var id = Interlocked.Increment(ref _nextId);            // 새 id
            var tcs = new TaskCompletionSource<JToken>(TaskCreationOptions.RunContinuationsAsynchronously);   // 완료 원천(읽기 루프에서 이어 실행 막음)
            _pending[id] = tcs;                                     // 표에 등록
            var msg = new JObject { ["jsonrpc"] = "2.0", ["id"] = id, ["method"] = method };   // 요청
            if (prms != null) msg["params"] = prms;                 // 인자
            try
            {
                await WriteAsync(msg).ConfigureAwait(false);        // 보내기
            }
            catch
            {
                TaskCompletionSource<JToken> dummy;                 // 버릴 원천
                _pending.TryRemove(id, out dummy);                  // 표에서 뺌
                throw;                                              // 쓰기 오류 그대로
            }
            if (IsClosed && _pending.TryRemove(id, out tcs))        // 보내는 사이 닫힘
                throw new IOException("ncn 연결이 닫혀 있습니다.");  // 실패
            using (ct.Register(() => tcs.TrySetCanceled()))        // 취소 토큰(응답은 버림)
            {
                return await tcs.Task.ConfigureAwait(false);        // 응답 기다림
            }
        }

        /// <summary>알림 보내기(응답 없음).</summary>
        public Task SendNotificationAsync(string method, JToken prms)   // 알림
        {
            var msg = new JObject { ["jsonrpc"] = "2.0", ["method"] = method };   // 알림 본문
            if (prms != null) msg["params"] = prms;                 // 인자
            return WriteAsync(msg);                                 // 보내기
        }

        private async Task WriteAsync(JObject msg)                  // 한 줄 쓰기
        {
            var line = Serialize(msg) + "\n";                       // 한 줄 + LF(CRLF 아님)
            await _writeLock.WaitAsync().ConfigureAwait(false);     // 한 번에 하나
            try
            {
                await _writer.WriteAsync(line).ConfigureAwait(false);   // 쓰기
                await _writer.FlushAsync().ConfigureAwait(false);   // 바로 내보냄(상대가 줄을 기다림)
            }
            finally
            {
                _writeLock.Release();                               // 잠금 풀기
            }
        }

        private static string Truncate(string s)                    // 로그용 자르기
        {
            return s.Length <= 200 ? s : s.Substring(0, 200) + "…"; // 200자까지
        }

        public void Dispose()                                       // 정리
        {
            try { _writer.Dispose(); } catch { }                    // 상대 stdin 닫기 → ncn 은 EOF 로 종료
            MarkClosed(null);                                       // 기다리던 요청 정리
        }
    }
}
