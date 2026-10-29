// vs/NcnVs.Core/Acp/AcpClient.cs — ACP 클라이언트(IDE 쪽): IDE→ncn 요청 함수 + ncn→IDE 요청·알림을 처리기로 연결 (규격: ACP_연동.md 2~5장)
using System;                                                       // 기본 형식
using System.Threading;                                             // 취소
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Json;                                              // JSON-RPC

namespace NcnVs.Core.Acp                                            // ACP 이름공간
{
    /// <summary>ncn → IDE 방향을 처리하는 쪽(세션 화면). 요청 처리기는 오래 걸려도 된다(읽기 루프를 막지 않음).</summary>
    public interface IAcpClientHandler                              // IDE 처리기
    {
        void OnSessionUpdate(string sessionId, SessionUpdate update);   // session/update(순서대로 동기 호출)
        void OnExtNotification(string method, JObject prms);         // _ncn/retry·_ncn/system_message 등
        Task<PermissionOutcome> RequestPermissionAsync(PermissionRequest request);   // 승인 묻기
        Task<string> ReadTextFileAsync(ReadTextFileRequest request);   // 읽기 위임(없는 파일이면 예외)
        Task WriteTextFileAsync(WriteTextFileRequest request);       // 쓰기 위임(R1: 저장까지 끝내고 돌아올 것)
        Task<QuestionAnswer> AskAsync(QuestionRequest question);     // 질문(elicitation/_ncn/ask)
    }

    /// <summary>클라이언트 정보·능력(initialize 에 실림).</summary>
    public sealed class ClientOptions                               // initialize 설정
    {
        public string Name { get; set; } = "ncn-vs";                // clientInfo.name
        public string Version { get; set; } = "0.1.0";              // clientInfo.version
        public bool FileSystem { get; set; } = true;                // fs.readTextFile/writeTextFile 알림
        public bool Elicitation { get; set; } = true;               // elicitation.form 알림
        public bool NcnAsk { get; set; } = true;                    // _meta.ncn.ask 알림(elicitation 이 없을 때 쓰임)
    }

    /// <summary>JSON-RPC 연결 하나 위의 ACP 클라이언트.</summary>
    public sealed class AcpClient : IDisposable                     // 클라이언트
    {
        private readonly JsonRpcConnection _conn;                   // 전송
        private readonly IAcpClientHandler _handler;                // IDE 처리기

        public JsonRpcConnection Connection => _conn;               // 전송(닫힘 사건 구독용)
        public JObject AgentInfo { get; private set; }              // initialize 응답의 agentInfo
        public JObject AgentCapabilities { get; private set; }      // initialize 응답의 agentCapabilities

        public AcpClient(JsonRpcConnection conn, IAcpClientHandler handler)   // 생성자
        {
            _conn = conn ?? throw new ArgumentNullException(nameof(conn));   // 전송 필수
            _handler = handler ?? throw new ArgumentNullException(nameof(handler));   // 처리기 필수
            _conn.RequestHandler = OnRequestAsync;                  // ncn → IDE 요청
            _conn.NotificationHandler = OnNotification;             // ncn → IDE 알림
        }

        /// <summary>initialize 요청 본문(3-1 예시 그대로).</summary>
        public static JObject InitializeParams(ClientOptions o)     // initialize params
        {
            var caps = new JObject();                               // clientCapabilities
            if (o.FileSystem) caps["fs"] = new JObject { ["readTextFile"] = true, ["writeTextFile"] = true };   // fs 위임 능력
            if (o.Elicitation) caps["elicitation"] = new JObject { ["form"] = new JObject() };   // 폼 질문 능력
            if (o.NcnAsk) caps["_meta"] = new JObject { ["ncn"] = new JObject { ["ask"] = true } };   // ncn 확장 질문
            return new JObject                                      // params
            {
                ["protocolVersion"] = AcpMethods.ProtocolVersion,   // 1
                ["clientCapabilities"] = caps,                      // 능력
                ["clientInfo"] = new JObject { ["name"] = o.Name, ["version"] = o.Version },   // 클라이언트 정보
            };
        }

        public async Task<JObject> InitializeAsync(ClientOptions options, CancellationToken ct = default(CancellationToken))   // initialize
        {
            var r = await _conn.SendRequestAsync(AcpMethods.Initialize, InitializeParams(options ?? new ClientOptions()), ct).ConfigureAwait(false) as JObject;   // 요청
            AgentInfo = r != null ? r["agentInfo"] as JObject : null;   // 에이전트 정보
            AgentCapabilities = r != null ? r["agentCapabilities"] as JObject : null;   // 에이전트 능력
            return r;                                               // 원본
        }

        public async Task<SessionOpenResult> NewSessionAsync(string cwd, CancellationToken ct = default(CancellationToken))   // session/new
        {
            var p = new JObject { ["cwd"] = cwd, ["mcpServers"] = new JArray() };   // 작업 폴더·MCP(무시됨)
            var r = await _conn.SendRequestAsync(AcpMethods.SessionNew, p, ct).ConfigureAwait(false);   // 요청
            return SessionOpenResult.FromJson(r, null);             // 해석
        }

        public async Task<SessionOpenResult> LoadSessionAsync(string sessionId, string cwd, CancellationToken ct = default(CancellationToken))   // session/load
        {
            var p = new JObject { ["sessionId"] = sessionId, ["cwd"] = cwd, ["mcpServers"] = new JArray() };   // 이어 갈 세션
            var r = await _conn.SendRequestAsync(AcpMethods.SessionLoad, p, ct).ConfigureAwait(false);   // 요청(되살리기 알림이 먼저 옴)
            return SessionOpenResult.FromJson(r, sessionId);        // 응답엔 id 가 없으므로 요청 값
        }

        public async Task<PromptResult> PromptAsync(string sessionId, JArray blocks, CancellationToken ct = default(CancellationToken))   // session/prompt
        {
            var p = new JObject { ["sessionId"] = sessionId, ["prompt"] = blocks };   // 블록들
            var r = await _conn.SendRequestAsync(AcpMethods.SessionPrompt, p, ct).ConfigureAwait(false);   // 턴 끝까지 기다림
            return PromptResult.FromJson(r);                        // 해석
        }

        public Task CancelAsync(string sessionId)                   // session/cancel(알림)
        {
            return _conn.SendNotificationAsync(AcpMethods.SessionCancel, new JObject { ["sessionId"] = sessionId });   // 보내기
        }

        public Task SetModeAsync(string sessionId, string modeId, CancellationToken ct = default(CancellationToken))   // session/set_mode
        {
            return _conn.SendRequestAsync(AcpMethods.SessionSetMode, new JObject { ["sessionId"] = sessionId, ["modeId"] = modeId }, ct);   // 요청
        }

        private async Task<JToken> OnRequestAsync(string method, JToken prms)   // ncn → IDE 요청 분기
        {
            var p = prms as JObject ?? new JObject();               // 인자 객체
            switch (method)                                         // 메서드별
            {
                case AcpMethods.RequestPermission:                  // 승인
                    var outcome = await _handler.RequestPermissionAsync(PermissionRequest.FromJson(p)).ConfigureAwait(false);   // 묻기
                    return (outcome ?? PermissionOutcome.Cancel).ToJson();   // {"outcome":{…}}
                case AcpMethods.FsReadTextFile:                     // 읽기
                    var content = await _handler.ReadTextFileAsync(ReadTextFileRequest.FromJson(p)).ConfigureAwait(false);   // 버퍼 또는 디스크
                    return new JObject { ["content"] = content ?? "" };   // {"content":…}
                case AcpMethods.FsWriteTextFile:                    // 쓰기
                    await _handler.WriteTextFileAsync(WriteTextFileRequest.FromJson(p)).ConfigureAwait(false);   // 버퍼 편집 + 저장
                    return new JObject();                           // 빈 응답
                case AcpMethods.ElicitationCreate:                  // 폼 질문
                    var q = QuestionRequest.FromElicitation(p);     // 해석
                    var a = await _handler.AskAsync(q).ConfigureAwait(false);   // 묻기
                    return (a ?? QuestionAnswer.Cancel()).ToElicitationResult(q);   // {"action":…}
                case AcpMethods.NcnAsk:                             // 확장 질문
                    var nq = QuestionRequest.FromNcnAsk(p);         // 해석
                    var na = await _handler.AskAsync(nq).ConfigureAwait(false);   // 묻기
                    return (na ?? QuestionAnswer.Cancel()).ToNcnAskResult(nq);   // {"answer":…}
                default:                                            // terminal/* 등 미지원
                    throw new JsonRpcException(JsonRpcException.MethodNotFound, "Method not found", new JObject { ["method"] = method });   // -32601
            }
        }

        private void OnNotification(string method, JToken prms)    // ncn → IDE 알림 분기
        {
            var p = prms as JObject ?? new JObject();               // 인자 객체
            if (method == AcpMethods.SessionUpdate)                 // 진행 알림
            {
                var u = p["update"] as JObject;                     // 업데이트 본문
                if (u != null) _handler.OnSessionUpdate(p.Value<string>("sessionId"), SessionUpdate.Parse(u));   // 해석해 전달
                return;                                             // 끝
            }
            _handler.OnExtNotification(method, p);                  // 확장 알림(_ncn/…)
        }

        public void Dispose()                                       // 정리
        {
            _conn.Dispose();                                        // 연결 닫기(stdin EOF)
        }
    }
}
