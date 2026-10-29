// vs/NcnVs.Core.Tests/FakeAgent.cs — 시험 도구: 메모리 줄 파이프, 가짜 에이전트 프로세스, 가짜 ncn(JSON-RPC 상대편) (tests/test_acp.py 의 가짜 IDE 를 거꾸로 한 것)
using System;                                                       // 기본 형식
using System.Collections.Concurrent;                                // 줄 대기열
using System.Collections.Generic;                                   // 목록
using System.IO;                                                    // TextReader·TextWriter
using System.Text;                                                  // StringBuilder
using System.Threading;                                             // 세마포어
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Host;                                              // IAgentProcess
using NcnVs.Core.Json;                                              // JsonRpcConnection

namespace NcnVs.Core.Tests                                          // 시험 이름공간
{
    /// <summary>한 방향 줄 파이프. Writer 로 쓴 줄이 Reader 에서 나온다. 지나간 줄은 History 에 남는다(순서 검사용).</summary>
    public sealed class LinePipe                                    // 줄 파이프
    {
        private readonly ConcurrentQueue<string> _q = new ConcurrentQueue<string>();   // 대기 줄
        private readonly SemaphoreSlim _sem = new SemaphoreSlim(0);  // 줄 수 신호
        private volatile bool _closed;                              // 닫힘
        public List<string> History { get; } = new List<string>();  // 지나간 줄(잠금으로 보호)
        public TextReader Reader { get; }                           // 읽는 쪽
        public TextWriter Writer { get; }                           // 쓰는 쪽

        public LinePipe()                                           // 생성자
        {
            Reader = new PipeReader(this);                          // 읽개
            Writer = new PipeWriter(this);                          // 쓰개
        }

        internal void Push(string line)                             // 줄 넣기
        {
            lock (History) History.Add(line);                       // 기록
            _q.Enqueue(line);                                       // 대기열
            _sem.Release();                                         // 신호
        }

        public void Close()                                         // 닫기(읽는 쪽에 EOF)
        {
            _closed = true;                                         // 표시
            _sem.Release();                                         // 깨움
        }

        internal async Task<string> ReadLineAsync()                 // 줄 하나 기다림
        {
            while (true)                                            // 줄 또는 닫힘까지
            {
                string line;                                        // 줄
                if (_q.TryDequeue(out line)) return line;           // 있으면 돌려줌
                if (_closed) return null;                           // 닫혔으면 EOF
                await _sem.WaitAsync().ConfigureAwait(false);       // 기다림
            }
        }

        public List<string> Snapshot()                              // 기록 복사
        {
            lock (History) return new List<string>(History);        // 복사본
        }

        private sealed class PipeReader : TextReader                // 읽개
        {
            private readonly LinePipe _p;                           // 파이프
            public PipeReader(LinePipe p) { _p = p; }               // 생성자
            public override Task<string> ReadLineAsync() => _p.ReadLineAsync();   // 비동기 줄 읽기
            public override string ReadLine() => _p.ReadLineAsync().GetAwaiter().GetResult();   // 동기 줄 읽기
        }

        private sealed class PipeWriter : TextWriter                // 쓰개
        {
            private readonly LinePipe _p;                           // 파이프
            private readonly StringBuilder _sb = new StringBuilder();   // 모으는 줄
            public PipeWriter(LinePipe p) { _p = p; }               // 생성자
            public override Encoding Encoding => Encoding.UTF8;    // 인코딩(표시용)
            public override void Write(char value)                  // 글자 하나
            {
                lock (_sb)                                          // 섞임 방지
                {
                    if (value == '\n') { _p.Push(_sb.ToString()); _sb.Clear(); }   // 줄 끝 → 넣기
                    else _sb.Append(value);                         // 모음
                }
            }
            public override void Write(string value) { if (value == null) return; foreach (var c in value) Write(c); }   // 글
            public override Task WriteAsync(string value) { Write(value); return Task.CompletedTask; }   // 비동기 글
            public override Task FlushAsync() => Task.CompletedTask;   // 비울 것 없음
            protected override void Dispose(bool disposing) { _p.Close(); base.Dispose(disposing); }   // 닫으면 EOF
        }
    }

    /// <summary>가짜 ncn: IAgentProcess(클라이언트가 보는 쪽) + JsonRpcConnection(에이전트 쪽).</summary>
    public sealed class FakeAgent : IAgentProcess                   // 가짜 에이전트
    {
        public LinePipe ToAgent { get; } = new LinePipe();          // 클라이언트 → 에이전트
        public LinePipe ToClient { get; } = new LinePipe();         // 에이전트 → 클라이언트
        public JsonRpcConnection Conn { get; }                      // 에이전트 쪽 연결
        public ConcurrentQueue<(string method, JObject prms)> Notifications { get; } = new ConcurrentQueue<(string, JObject)>();   // 받은 알림
        public ConcurrentQueue<(string method, JObject prms)> Requests { get; } = new ConcurrentQueue<(string, JObject)>();   // 받은 요청
        public Dictionary<string, Func<JObject, Task<JToken>>> Handlers { get; } = new Dictionary<string, Func<JObject, Task<JToken>>>();   // 메서드별 처리기
        public string SessionId { get; set; } = "sess-1";          // 돌려줄 세션 id
        private volatile bool _exited;                              // 끝남
        public bool HasExited => _exited;                           // 끝났는지
        public int? ExitCode { get; private set; }                  // 종료 코드
        public event Action<int?> Exited;                           // 끝남 사건
        public TextReader Output => ToClient.Reader;                // 클라이언트가 읽음
        public TextWriter Input => ToAgent.Writer;                  // 클라이언트가 씀

        public FakeAgent()                                          // 생성자: 기본 처리기
        {
            Conn = new JsonRpcConnection(ToAgent.Reader, ToClient.Writer);   // 에이전트 쪽
            Handlers["initialize"] = p => Task.FromResult<JToken>(new JObject   // initialize 응답
            {
                ["protocolVersion"] = 1,                            // 버전
                ["agentCapabilities"] = new JObject { ["loadSession"] = true },   // 능력
                ["agentInfo"] = new JObject { ["name"] = "fake-ncn", ["version"] = "0" },   // 정보
                ["authMethods"] = new JArray(),                     // 인증 없음
            });
            Handlers["session/new"] = p => Task.FromResult<JToken>(OpenResult(SessionId, "normal"));   // 새 세션
            Handlers["session/load"] = p => Task.FromResult<JToken>(OpenResult(null, "normal"));   // 이어 가기
            Handlers["session/set_mode"] = p => Task.FromResult<JToken>(new JObject());   // 모드
            Conn.RequestHandler = async (m, p) =>                   // 요청 처리
            {
                var o = p as JObject ?? new JObject();              // 인자
                Requests.Enqueue((m, o));                           // 기록
                Func<JObject, Task<JToken>> h;                      // 처리기
                if (!Handlers.TryGetValue(m, out h)) throw new JsonRpcException(JsonRpcException.MethodNotFound, "Method not found");   // 모름
                return await h(o);                                  // 처리
            };
            Conn.NotificationHandler = (m, p) => Notifications.Enqueue((m, p as JObject));   // 알림 기록
            Conn.Start();                                           // 읽기 시작
        }

        public static JObject OpenResult(string sessionId, string mode)   // session/new·load 응답
        {
            var r = new JObject                                     // 응답
            {
                ["modes"] = new JObject                             // 모드 상태
                {
                    ["currentModeId"] = mode,                       // 현재
                    ["availableModes"] = new JArray(                // 목록
                        new JObject { ["id"] = "normal", ["name"] = "normal" },
                        new JObject { ["id"] = "plan", ["name"] = "plan" },
                        new JObject { ["id"] = "auto", ["name"] = "auto" }),
                },
            };
            if (sessionId != null) r["sessionId"] = sessionId;      // new 만 id
            return r;                                               // 결과
        }

        /// <summary>session/update 알림 보내기.</summary>
        public Task UpdateAsync(JObject update)                     // 업데이트
        {
            return Conn.SendNotificationAsync("session/update", new JObject { ["sessionId"] = SessionId, ["update"] = update });   // 보내기
        }

        /// <summary>agent_message_chunk 하나.</summary>
        public Task ChunkAsync(string text)                         // 글 조각
        {
            return UpdateAsync(new JObject { ["sessionUpdate"] = "agent_message_chunk", ["content"] = new JObject { ["type"] = "text", ["text"] = text } });   // 보내기
        }

        /// <summary>비정상 종료 흉내(stdout EOF + 종료 코드).</summary>
        public void Crash(int code = 1)                             // 죽기
        {
            ExitCode = code;                                        // 코드
            _exited = true;                                         // 끝남
            ToClient.Close();                                       // 클라이언트에 EOF
            Exited?.Invoke(code);                                   // 사건
        }

        public void Dispose()                                       // 정리
        {
            if (!_exited) { _exited = true; ExitCode = 0; }         // 정상 종료
            ToClient.Close();                                       // EOF
        }
    }
}
