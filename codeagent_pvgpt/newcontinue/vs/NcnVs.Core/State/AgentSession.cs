// vs/NcnVs.Core/State/AgentSession.cs — 세션 조정자: ncn 프로세스·ACP 연결·화면 상태를 묶고 승인·질문·fs 위임·중단·재시작(session/load)을 처리 (ACP_연동.md 2장·4장 R1~R4·6장)
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 목록
using System.IO;                                                    // IOException·FileNotFoundException
using System.Threading;                                             // 잠금
using System.Threading.Tasks;                                       // 비동기
using Newtonsoft.Json.Linq;                                         // JObject
using NcnVs.Core.Acp;                                               // ACP
using NcnVs.Core.Host;                                              // 프로세스
using NcnVs.Core.Json;                                              // JSON-RPC
using NcnVs.Core.Text;                                              // 글 도우미

namespace NcnVs.Core.State                                          // 상태 이름공간
{
    /// <summary>IDE 파일 창구(fs 위임). VS: 열린 문서는 버퍼, 아니면 디스크. 시험: 메모리.</summary>
    public interface IIdeBridge                                     // IDE 파일 창구
    {
        /// <summary>파일 전체 글(열린 버퍼면 저장 안 한 변경 포함). 없으면 FileNotFoundException.</summary>
        Task<string> ReadTextFileAsync(string path);                // 읽기
        /// <summary>파일 전체 쓰기. 열린 문서면 버퍼 편집 **후 저장**까지 끝내고 돌아온다(R1).</summary>
        Task WriteTextFileAsync(string path, string content);       // 쓰기
    }

    /// <summary>디스크만 쓰는 창구(기본·시험용).</summary>
    public sealed class DiskIdeBridge : IIdeBridge                  // 디스크 창구
    {
        public Task<string> ReadTextFileAsync(string path)          // 읽기
        {
            return Task.Run(() => TextFileIo.ReadAllText(path));    // 디스크(인코딩 감지)
        }

        public Task WriteTextFileAsync(string path, string content) // 쓰기
        {
            return Task.Run(() => TextFileIo.WriteAllText(path, content));   // 디스크(인코딩 유지)
        }
    }

    /// <summary>세션 조정자 설정.</summary>
    public sealed class AgentSessionOptions                         // 설정
    {
        public Func<IAgentProcess> ProcessFactory { get; set; }     // ncn 프로세스 띄우기(재시작 때도 부름)
        public IIdeBridge Ide { get; set; } = new DiskIdeBridge();   // 파일 창구
        public Action<Action> Post { get; set; } = a => a();        // UI 스레드로 넘기기(순서 보장 필요 — WPF Dispatcher.BeginInvoke)
        public Action<string> Log { get; set; } = s => { };         // 로그
        public ClientOptions Client { get; set; } = new ClientOptions();   // initialize 능력
        public int MaxRestarts { get; set; } = 3;                   // 재시작 한도(RestartWindow 안에서)
        public TimeSpan RestartWindow { get; set; } = TimeSpan.FromMinutes(5);   // 한도 기간
        public TimeSpan RestartDelay { get; set; } = TimeSpan.FromMilliseconds(500);   // 재시작 전 기다림(횟수마다 늘어남)
    }

    /// <summary>
    /// 한 도구 창 = 한 AgentSession. 공개 메서드는 UI 스레드에서 불러도 되고(내부에서 기다림), State 는 Post 로만 고친다.
    /// </summary>
    public sealed class AgentSession : IAcpClientHandler, IDisposable   // 세션 조정자
    {
        private readonly AgentSessionOptions _o;                    // 설정
        private readonly object _gate = new object();               // 잠금
        private readonly Dictionary<ChatItem, Action> _pendingAsks = new Dictionary<ChatItem, Action>();   // 대기 중 승인·질문 → 취소 함수
        private readonly List<DateTime> _restarts = new List<DateTime>();   // 재시작 시각들
        private AcpClient _client;                                  // 지금 연결
        private AcpClient _established;                             // initialize 까지 끝난 연결(이것이 끊기면 비정상 종료로 봄)
        private IAgentProcess _process;                             // 지금 프로세스
        private string _sessionId;                                  // 지금 세션 id
        private string _cwd;                                        // 작업 폴더
        private int _busy;                                          // 턴 진행 중(0/1)
        private bool _disposed;                                     // 정리됨
        private bool _stopped;                                      // 재시작 포기

        public SessionState State { get; } = new SessionState();    // 화면 상태
        public string SessionId { get { lock (_gate) return _sessionId; } }   // 세션 id
        public bool IsBusy => Volatile.Read(ref _busy) != 0;        // 진행 중
        public bool IsConnected { get { lock (_gate) return _client != null && !_client.Connection.IsClosed; } }   // 연결됨

        /// <summary>승인 항목이 목록에 추가된 직후(UI 스레드). VS: diff 가 있으면 비교 창 열기.</summary>
        public event Action<PermissionItem> PermissionRequested;    // 승인 요청됨
        /// <summary>질문 항목이 추가된 직후(UI 스레드).</summary>
        public event Action<QuestionItem> QuestionRequested;        // 질문됨
        /// <summary>재시작 성공(시험·로그용).</summary>
        public event Action Restarted;                              // 재시작됨

        public AgentSession(AgentSessionOptions options)            // 생성자
        {
            _o = options ?? throw new ArgumentNullException(nameof(options));   // 설정 필수
            if (_o.ProcessFactory == null) throw new ArgumentException("ProcessFactory 가 필요합니다.");   // 프로세스 필수
            _o.Post(() => State.EnsureDefaultModes());              // 기본 모드 목록
        }

        // ─────────────────────────── 시작·세션 ───────────────────────────

        /// <summary>ncn 띄우고 initialize → session/new(또는 resumeSessionId 로 session/load).</summary>
        public async Task StartAsync(string cwd, string resumeSessionId = null)   // 시작
        {
            lock (_gate) { _cwd = cwd; _stopped = false; }          // 작업 폴더
            Post(() => { State.WorkingDirectory = cwd; State.StatusText = "ncn 시작 중…"; });   // 상태 줄
            await ConnectAsync().ConfigureAwait(false);             // 프로세스 + initialize
            await OpenSessionAsync(resumeSessionId).ConfigureAwait(false);   // 세션 열기
        }

        /// <summary>새 대화(같은 프로세스에서 session/new). 진행 중이면 먼저 중단.</summary>
        public async Task NewConversationAsync(string cwd)          // 새 대화
        {
            if (IsBusy) await CancelAsync().ConfigureAwait(false); // 진행 중 턴 중단
            lock (_gate) { _cwd = cwd ?? _cwd; _sessionId = null; } // 새 폴더
            Post(() => { State.Clear(); State.WorkingDirectory = cwd; });   // 화면 비우기
            if (!IsConnected) { await StartAsync(cwd).ConfigureAwait(false); return; }   // 연결이 없으면 처음부터
            await OpenSessionAsync(null).ConfigureAwait(false);    // 새 세션
        }

        private async Task ConnectAsync()                           // 프로세스 띄우고 initialize
        {
            var proc = _o.ProcessFactory();                         // 띄우기(실패하면 예외)
            var conn = new JsonRpcConnection(proc.Output, proc.Input) { Log = _o.Log };   // 전송
            var client = new AcpClient(conn, this);                 // ACP 클라이언트
            lock (_gate) { _client = client; _process = proc; }     // 지금 연결로
            conn.Closed += ex => OnConnectionClosed(client, proc, ex);   // 끊김 감지
            conn.Start();                                           // 읽기 시작
            await client.InitializeAsync(_o.Client).ConfigureAwait(false);   // initialize(실패하면 예외 → 부른 쪽이 알림)
            lock (_gate) _established = client;                     // 이제부터 끊기면 재시작 대상
            var info = client.AgentInfo;                            // 에이전트 정보
            _o.Log("initialize 완료: " + (info != null ? info.ToString(Newtonsoft.Json.Formatting.None) : "(agentInfo 없음)"));   // 로그
        }

        private async Task OpenSessionAsync(string resumeSessionId) // session/new 또는 load
        {
            var client = CurrentClient();                           // 연결
            string cwd;                                             // 작업 폴더
            lock (_gate) cwd = _cwd;                                // 읽기
            SessionOpenResult r;                                    // 결과
            if (!string.IsNullOrEmpty(resumeSessionId))             // 이어 가기
            {
                try
                {
                    r = await client.LoadSessionAsync(resumeSessionId, cwd).ConfigureAwait(false);   // 되살리기 알림 뒤 응답
                }
                catch (JsonRpcException e) when (e.Code == JsonRpcException.ResourceNotFound)   // 저장된 세션 없음
                {
                    _o.Log("session/load 실패(-32002) → 새 세션: " + resumeSessionId);   // 로그
                    Post(() => State.AddNote("이전 세션을 찾지 못해 새 대화를 시작합니다."));   // 안내
                    r = await client.NewSessionAsync(cwd).ConfigureAwait(false);   // 새 세션
                }
            }
            else
            {
                r = await client.NewSessionAsync(cwd).ConfigureAwait(false);   // 새 세션
            }
            lock (_gate) _sessionId = r.SessionId;                  // id 보관
            Post(() => { State.ApplyOpen(r); State.StatusText = "준비됨"; });   // 화면 반영
        }

        private AcpClient CurrentClient()                           // 지금 연결(없으면 예외)
        {
            lock (_gate)
            {
                if (_client == null) throw new InvalidOperationException("ncn 이 연결되어 있지 않습니다.");   // 없음
                return _client;                                     // 연결
            }
        }

        // ─────────────────────────── 질문 한 턴 ───────────────────────────

        /// <summary>
        /// session/prompt. text 블록 + 첨부 블록(resource_link·resource). 진행 중이면 null(같은 세션 prompt 중복은 ncn 이 -32600).
        /// </summary>
        public async Task<PromptResult> SendAsync(string text, IList<JObject> attachments = null, IList<string> attachmentLabels = null)   // 보내기
        {
            if (Interlocked.CompareExchange(ref _busy, 1, 0) != 0) return null;   // 이미 진행 중
            try
            {
                var blocks = new JArray { ContentBlocks.Text(text) };   // 글 블록 먼저(3-4 예시처럼)
                if (attachments != null) foreach (var a in attachments) blocks.Add(a);   // 첨부
                var user = new UserMessageItem { Text = text };     // 화면 항목
                if (attachmentLabels != null) user.Attachments.AddRange(attachmentLabels);   // 첨부 설명
                Post(() => { State.Items.Add(user); State.IsBusy = true; State.StatusText = "응답 중…"; });   // 화면
                if (!IsConnected) await RecoverAsync().ConfigureAwait(false);   // 끊겨 있으면 다시 연결
                var client = CurrentClient();                       // 연결
                var sid = SessionId;                                // 세션
                if (sid == null) { await OpenSessionAsync(null).ConfigureAwait(false); sid = SessionId; }   // 세션이 없으면 새로
                var r = await client.PromptAsync(sid, blocks).ConfigureAwait(false);   // 턴 끝까지
                Post(() => FinishTurn(r));                          // 결과 표시
                return r;                                           // 결과
            }
            catch (JsonRpcException e)                              // ncn 오류(LLM·설정)
            {
                Post(() => { State.AddNote(e.DisplayMessage, true); State.StatusText = "오류"; });   // cn 원문 오류 글
                return null;                                        // 실패
            }
            catch (Exception e)                                     // 연결 끊김 등
            {
                _o.Log("prompt 실패: " + e);                        // 로그
                Post(() => { State.AddNote("질문을 끝내지 못했습니다: " + e.Message, true); State.StatusText = "오류"; });   // 안내
                return null;                                        // 실패
            }
            finally
            {
                Volatile.Write(ref _busy, 0);                       // 진행 끝
                Post(() => State.IsBusy = false);                   // 화면
            }
        }

        private void FinishTurn(PromptResult r)                     // 턴 결과 표시(UI 스레드)
        {
            if (r.StopReason == "cancelled") State.AddNote("중단했습니다.");   // 중단
            else if (r.StoppedByRejection) State.AddNote("승인 거부로 멈췄습니다. 다르게 할 일을 입력하세요.");   // 3번(아니오)
            else if (r.StopReason != null && r.StopReason != "end_turn") State.AddNote("멈춘 이유: " + r.StopReason);   // max_tokens 등
            State.StatusText = r.TotalTokens.HasValue                // 상태 줄
                ? string.Format("준비됨 · 토큰 {0:N0}(입력 {1:N0} / 출력 {2:N0})", r.TotalTokens, r.InputTokens ?? 0, r.OutputTokens ?? 0)   // 사용량
                : "준비됨";                                          // 사용량 없음
        }

        /// <summary>중단(R3: session/cancel 을 먼저 보내고, 걸린 승인·질문은 cancelled 로 답).</summary>
        public async Task CancelAsync()                             // 중단
        {
            AcpClient client;                                       // 연결
            string sid;                                             // 세션
            lock (_gate) { client = _client; sid = _sessionId; }    // 읽기
            if (client != null && sid != null)                      // 연결·세션 있음
            {
                try { await client.CancelAsync(sid).ConfigureAwait(false); }   // session/cancel 알림
                catch (Exception e) { _o.Log("session/cancel 보내기 실패: " + e.Message); }   // 끊김 등
            }
            CancelPendingAsks();                                    // 그 다음 대기 중 요청 정리
            Post(() => State.StatusText = "중단 중…");              // 상태 줄
        }

        /// <summary>모드 바꾸기(normal·plan·auto). ncn 이 current_mode_update 로 확인해 준다.</summary>
        public async Task SetModeAsync(string modeId)               // 모드
        {
            var client = CurrentClient();                           // 연결
            var sid = SessionId;                                    // 세션
            if (sid == null) return;                                // 세션 없음
            try
            {
                await client.SetModeAsync(sid, modeId).ConfigureAwait(false);   // session/set_mode
                Post(() => State.CurrentModeId = modeId);           // 화면(알림이 와도 같은 값)
            }
            catch (JsonRpcException e)                              // 모르는 모드 등
            {
                Post(() => State.AddNote("모드를 바꾸지 못했습니다: " + e.DisplayMessage, true));   // 안내
            }
        }

        // ─────────────────────────── 승인·질문 (UI 가 부름) ───────────────────────────

        /// <summary>승인 버튼(optionId 그대로).</summary>
        public void ChoosePermission(PermissionItem item, string optionId)   // 승인 선택
        {
            ResolvePermission(item, PermissionOutcome.Selected(optionId));   // 선택 결과
        }

        /// <summary>질문 답.</summary>
        public void AnswerQuestion(QuestionItem item, QuestionAnswer answer)   // 질문 답
        {
            ResolveQuestion(item, answer ?? QuestionAnswer.Decline());   // 답(없으면 건너뛰기)
        }

        private void ResolvePermission(PermissionItem item, PermissionOutcome outcome)   // 승인 끝내기
        {
            lock (_gate) { if (!_pendingAsks.Remove(item)) return; }   // 이미 끝남
            Post(() => item.MarkResolved(outcome));                 // 화면
            item.Resolver?.Invoke(outcome);                         // ncn 에 응답
        }

        private void ResolveQuestion(QuestionItem item, QuestionAnswer answer)   // 질문 끝내기
        {
            lock (_gate) { if (!_pendingAsks.Remove(item)) return; }   // 이미 끝남
            Post(() => item.MarkResolved(answer));                  // 화면
            item.Resolver?.Invoke(answer);                          // ncn 에 응답
        }

        private void CancelPendingAsks()                            // 대기 중 요청 모두 취소
        {
            List<Action> cancels;                                   // 취소 함수들
            lock (_gate) { cancels = new List<Action>(_pendingAsks.Values); }   // 복사
            foreach (var c in cancels) c();                         // 하나씩(각자 Remove 함)
        }

        // ─────────────────────────── IAcpClientHandler (읽기 스레드에서 불림) ───────────────────────────

        void IAcpClientHandler.OnSessionUpdate(string sessionId, SessionUpdate update)   // session/update
        {
            if (!IsCurrentSession(sessionId)) return;               // 다른(옛) 세션 알림은 무시
            Post(() => State.Apply(update));                        // UI 스레드에서 적용(순서 유지)
        }

        void IAcpClientHandler.OnExtNotification(string method, JObject prms)   // 확장 알림
        {
            if (method == AcpMethods.NcnRetry)                      // LLM 재시도 직전
            {
                var attempt = prms.Value<int?>("attempt");          // 회차
                var err = prms.Value<string>("error");              // 오류 글
                Post(() =>
                {
                    State.RemoveTrailingAgentText();                // 이번 호출 조각 지우기(부록 B)
                    State.StatusText = "LLM 재시도 " + attempt + "회차 · " + err;   // 상태 줄
                });
            }
            else if (method == AcpMethods.NcnSystemMessage)         // 압축 안내 등
            {
                var msg = prms.Value<string>("message");            // 글
                if (!string.IsNullOrEmpty(msg)) Post(() => State.StatusText = msg);   // 상태 줄(글은 agent_thought_chunk 로도 옴)
            }
            else
            {
                _o.Log("모르는 알림: " + method);                   // 로그만
            }
        }

        async Task<PermissionOutcome> IAcpClientHandler.RequestPermissionAsync(PermissionRequest request)   // 승인 요청
        {
            var item = new PermissionItem(request);                 // 화면 항목
            var tcs = new TaskCompletionSource<PermissionOutcome>(TaskCreationOptions.RunContinuationsAsynchronously);   // 답 기다림
            item.Resolver = o => tcs.TrySetResult(o);               // 답 전달
            lock (_gate)                                            // 대기 목록
            {
                if (_disposed) return PermissionOutcome.Cancel;     // 정리 중이면 바로 취소
                _pendingAsks[item] = () => ResolvePermission(item, PermissionOutcome.Cancel);   // 취소 함수
            }
            Post(() => { State.Items.Add(item); State.StatusText = "승인 대기: " + item.Title; PermissionRequested?.Invoke(item); });   // 화면 + 사건
            var outcome = await tcs.Task.ConfigureAwait(false);     // 사용자 답(R4: 여러 개면 차례로 옴)
            Post(() => { if (State.IsBusy) State.StatusText = "응답 중…"; });   // 상태 줄 되돌림
            return outcome;                                         // 응답
        }

        async Task<QuestionAnswer> IAcpClientHandler.AskAsync(QuestionRequest question)   // 질문
        {
            var item = new QuestionItem(question);                  // 화면 항목
            var tcs = new TaskCompletionSource<QuestionAnswer>(TaskCreationOptions.RunContinuationsAsynchronously);   // 답 기다림
            item.Resolver = a => tcs.TrySetResult(a);               // 답 전달
            lock (_gate)                                            // 대기 목록
            {
                if (_disposed) return QuestionAnswer.Cancel();      // 정리 중
                _pendingAsks[item] = () => ResolveQuestion(item, QuestionAnswer.Cancel());   // 취소 함수
            }
            Post(() => { State.Items.Add(item); State.StatusText = "질문 대기"; QuestionRequested?.Invoke(item); });   // 화면 + 사건
            return await tcs.Task.ConfigureAwait(false);            // 사용자 답
        }

        async Task<string> IAcpClientHandler.ReadTextFileAsync(ReadTextFileRequest request)   // fs/read_text_file(R2)
        {
            try
            {
                var text = await _o.Ide.ReadTextFileAsync(request.Path).ConfigureAwait(false);   // 버퍼 또는 디스크
                return TextSlicing.Slice(text, request.Line, request.Limit);   // 줄 범위
            }
            catch (FileNotFoundException)                           // 없는 파일
            {
                throw new JsonRpcException(JsonRpcException.ResourceNotFound, "Resource not found", new JObject { ["path"] = request.Path });   // -32002 → ncn 이 디스크로
            }
            catch (DirectoryNotFoundException)                      // 없는 폴더
            {
                throw new JsonRpcException(JsonRpcException.ResourceNotFound, "Resource not found", new JObject { ["path"] = request.Path });   // -32002
            }
        }

        Task IAcpClientHandler.WriteTextFileAsync(WriteTextFileRequest request)   // fs/write_text_file(R1)
        {
            return _o.Ide.WriteTextFileAsync(request.Path, request.Content);   // 버퍼 편집 + 저장(끝난 뒤 응답)
        }

        private bool IsCurrentSession(string sessionId)             // 지금 세션의 알림인지
        {
            lock (_gate) return _sessionId == null || sessionId == null || sessionId == _sessionId;   // load 중(id 미확정)이면 받음
        }

        // ─────────────────────────── 끊김·재시작 ───────────────────────────

        private void OnConnectionClosed(AcpClient client, IAgentProcess proc, Exception error)   // 연결 끊김(읽기 스레드)
        {
            lock (_gate)
            {
                if (_disposed || client != _client) return;         // 정리 중이거나 옛 연결
                _client = null;                                     // 연결 없음
                if (client != _established)                         // initialize 전에 끊김 → 시작 실패(예외가 부른 쪽으로 감)
                {
                    _o.Log("ncn 이 initialize 전에 끊김: " + (error != null ? error.Message : "EOF"));   // 로그
                    return;                                         // 재시작하지 않음
                }
            }
            CancelPendingAsks();                                    // 걸린 승인·질문 정리
            var _ = HandleCrashAsync(proc, error);                  // 재시작(기다리지 않음)
        }

        private async Task HandleCrashAsync(IAgentProcess proc, Exception error)   // 비정상 종료 처리
        {
            for (var i = 0; i < 20 && !proc.HasExited; i++) await Task.Delay(100).ConfigureAwait(false);   // 종료 코드 기다림(최대 2초)
            var code = proc.ExitCode;                               // 종료 코드
            _o.Log("ncn 연결 끊김: 종료 코드 " + (code.HasValue ? code.Value.ToString() : "?") + (error != null ? " / " + error.Message : ""));   // 로그
            try { proc.Dispose(); } catch { }                       // 옛 프로세스 정리
            Post(() => State.AddNote("ncn 이 종료되었습니다(코드 " + (code.HasValue ? code.Value.ToString() : "?") + "). 다시 시작합니다…", true));   // 안내
            await RecoverAsync().ConfigureAwait(false);            // 재시작
        }

        /// <summary>재시작 후 마지막 세션을 session/load 로 이어 간다(6장 "프로세스가 죽음").</summary>
        private async Task RecoverAsync()                           // 재시작
        {
            string sid;                                             // 이어 갈 세션
            int attempt;                                            // 이번 회차
            lock (_gate)
            {
                if (_disposed || _stopped) return;                  // 정리됨·포기함
                if (_client != null) return;                        // 이미 다시 붙음
                var now = DateTime.UtcNow;                          // 지금
                _restarts.RemoveAll(t => now - t > _o.RestartWindow);   // 오래된 기록 빼기
                if (_restarts.Count >= _o.MaxRestarts)              // 한도 넘음
                {
                    _stopped = true;                                // 포기
                    sid = null;                                     // (안 씀)
                    attempt = -1;                                   // 표시
                }
                else
                {
                    _restarts.Add(now);                             // 기록
                    sid = _sessionId;                               // 마지막 세션
                    attempt = _restarts.Count;                      // 회차
                }
            }
            if (attempt < 0)                                        // 포기
            {
                Post(() => { State.AddNote("ncn 이 계속 종료됩니다. 출력 창(ncn)·로그를 확인한 뒤 '새 대화'로 다시 시작하세요.", true); State.StatusText = "중지됨"; });   // 안내
                return;                                             // 끝
            }
            try
            {
                await Task.Delay(TimeSpan.FromMilliseconds(_o.RestartDelay.TotalMilliseconds * attempt)).ConfigureAwait(false);   // 잠깐 기다림
                await ConnectAsync().ConfigureAwait(false);         // 새 프로세스 + initialize
                if (sid != null) Post(() => State.Clear());         // load 되살리기 전에 화면 비움(중복 방지)
                await OpenSessionAsync(sid).ConfigureAwait(false);  // session/load(없으면 new)
                Post(() => State.AddNote(sid != null ? "ncn 을 다시 시작해 대화를 이어 갑니다." : "ncn 을 다시 시작했습니다."));   // 안내
                Restarted?.Invoke();                                // 사건
            }
            catch (Exception e)                                     // 재시작 실패
            {
                _o.Log("재시작 실패: " + e);                        // 로그
                lock (_gate) { _client = null; }                    // 연결 없음
                Post(() => { State.AddNote("ncn 을 다시 시작하지 못했습니다: " + e.Message, true); State.StatusText = "중지됨"; });   // 안내
            }
        }

        private void Post(Action a)                                 // UI 스레드로
        {
            _o.Post(a);                                             // 설정된 방법으로
        }

        public void Dispose()                                       // 정리(도구 창 닫힘·VS 종료)
        {
            AcpClient client;                                       // 연결
            IAgentProcess proc;                                     // 프로세스
            lock (_gate)
            {
                if (_disposed) return;                              // 한 번만
                _disposed = true;                                   // 표시
                client = _client;                                   // 꺼냄
                proc = _process;                                    // 꺼냄
                _client = null;                                     // 비움
                _process = null;                                    // 비움
            }
            List<Action> cancels;                                   // 걸린 요청 취소 함수
            lock (_gate) cancels = new List<Action>(_pendingAsks.Values);   // 복사
            foreach (var c in cancels) { try { c(); } catch { } }   // 걸린 요청 취소
            try { client?.Dispose(); } catch { }                    // stdin 닫기(ncn EOF 종료)
            if (proc != null) Task.Run(() => { try { proc.Dispose(); } catch { } });   // 3초 기다리고 강제 종료(UI 를 막지 않음)
        }
    }
}
