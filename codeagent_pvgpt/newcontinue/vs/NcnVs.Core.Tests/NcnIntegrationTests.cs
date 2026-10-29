// vs/NcnVs.Core.Tests/NcnIntegrationTests.cs — 실제 ncn 통합 시험 1개: NCN_EXE 가 있으면 `--stdio` 로 띄워 initialize·session/new 만(LLM 호출 없음)
using System;                                                       // 환경변수
using System.Collections.Generic;                                   // 목록
using System.IO;                                                    // 임시 폴더
using System.Threading.Tasks;                                       // 비동기
using NcnVs.Core.Acp;                                               // ClientOptions
using NcnVs.Core.Host;                                              // NcnProcess
using NcnVs.Core.State;                                             // AgentSession
using Xunit;                                                        // 시험 도구
using Xunit.Abstractions;                                           // 출력

namespace NcnVs.Core.Tests                                          // 시험 이름공간
{
    /// <summary>NCN_EXE 환경변수가 없으면 건너뛰는 Fact.</summary>
    public sealed class NcnExeFactAttribute : FactAttribute         // 조건부 Fact
    {
        public NcnExeFactAttribute()                                // 생성자
        {
            var exe = Environment.GetEnvironmentVariable("NCN_EXE"); // 실행 파일 경로
            if (string.IsNullOrEmpty(exe)) Skip = "NCN_EXE 가 없어 건너뜀(CI 에서 build_win.ps1 결과로 지정)";   // 없으면 건너뜀
            else if (!File.Exists(exe)) Skip = "NCN_EXE 파일이 없음: " + exe;   // 경로가 틀림
        }
    }

    public class NcnIntegrationTests                                // 통합 시험
    {
        private readonly ITestOutputHelper _out;                    // 시험 출력
        public NcnIntegrationTests(ITestOutputHelper output) { _out = output; }   // 생성자

        [NcnExeFact]
        public async Task Real_ncn_initialize_and_session_new()     // 실제 ncn: initialize + session/new
        {
            var exe = Environment.GetEnvironmentVariable("NCN_EXE"); // 실행 파일
            var tmp = Path.Combine(Path.GetTempPath(), "ncnvs-it-" + Guid.NewGuid().ToString("N"));   // 격리 폴더
            var work = Path.Combine(tmp, "work");                   // 작업 폴더
            Directory.CreateDirectory(work);                        // 만들기
            var config = Path.Combine(tmp, "config.yaml");          // 시험 설정(모델 주소는 닫힌 포트 — 호출 안 함)
            File.WriteAllText(config,                               // parity/run.py config_yaml 과 같은 모양
                "name: vs-it\nversion: 0.0.1\nschema: v1\nmodels:\n" +
                "  - name: fake\n    provider: openai\n    model: fake-model\n" +
                "    apiBase: http://127.0.0.1:9/v1/\n    apiKey: sk-test\n    roles: [chat]\n    capabilities: [tool_use]\n");
            var stderr = new List<string>();                        // ncn 로그
            var guard = WindowsJobObject.CreateForCurrentOs(s => _out.WriteLine(s));   // Windows 면 Job Object
            var launch = new NcnLaunchOptions                       // 실행 설정
            {
                ExePath = exe,                                      // ncn.exe
                WorkingDirectory = work,                            // 작업 폴더
                ConfigPath = config,                                // 시험 설정
            };
            launch.Environment["HOME"] = Path.Combine(tmp, "home");             // 실제 홈 차단(parity isolated_env 와 같은 생각)
            launch.Environment["USERPROFILE"] = Path.Combine(tmp, "home");      // Windows 홈
            launch.Environment["CONTINUE_GLOBAL_DIR"] = Path.Combine(tmp, "cnhome");   // ~/.continue 대신
            launch.Environment["CONTINUE_API_BASE"] = "http://127.0.0.1:9/";    // Hub 호출은 즉시 실패
            launch.Environment["CONTINUE_METRICS_ENABLED"] = "0";               // 텔레메트리 끔
            launch.Environment["CONTINUE_CLI_ENABLE_TELEMETRY"] = "0";          // 텔레메트리 끔
            var session = new AgentSession(new AgentSessionOptions  // 세션
            {
                ProcessFactory = () => NcnProcess.Start(launch, guard, line => { lock (stderr) stderr.Add(line); }),   // 실제 프로세스
                Log = s => { try { _out.WriteLine("[log] " + s); } catch { } },   // 로그(시험이 끝난 뒤 불려도 무시)
                Client = new ClientOptions(),                       // 기본 능력
                MaxRestarts = 0,                                    // 재시작 안 함(시험)
            });
            try
            {
                var start = session.StartAsync(work);               // initialize + session/new
                var done = await Task.WhenAny(start, Task.Delay(TimeSpan.FromSeconds(60)));   // PyInstaller 첫 실행은 느릴 수 있음
                Assert.True(done == start, "ncn 이 60초 안에 응답하지 않음");   // 시간 초과
                await start;                                        // 예외 전파
                Assert.False(string.IsNullOrEmpty(session.State.SessionId));   // 세션 id 받음
                Assert.Contains(session.State.Modes, m => m.Id == "plan");   // 모드 목록
                Assert.Equal("normal", session.State.CurrentModeId); // 기본 모드
            }
            finally
            {
                session.Dispose();                                  // stdin 닫기 → ncn 종료
                await Task.Delay(500);                              // 종료 여유
                lock (stderr) foreach (var l in stderr) _out.WriteLine("[ncn] " + l);   // ncn 로그 남기기
                guard.Dispose();                                    // 남은 프로세스 정리
                try { Directory.Delete(tmp, true); } catch { }      // 정리(잠겨 있으면 그대로)
            }
        }
    }
}
