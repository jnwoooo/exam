// vs/NcnVs.Core/Host/NcnProcess.cs — `ncn --stdio` 자식 프로세스(stdin/stdout = 프로토콜, stderr = 로그 수집) (ACP_연동.md 1장)
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 목록·사전
using System.Diagnostics;                                           // Process
using System.IO;                                                    // 스트림
using System.Text;                                                  // UTF-8

namespace NcnVs.Core.Host                                           // 호스트 이름공간
{
    /// <summary>에이전트 프로세스 추상화(실제 ncn 또는 시험용 가짜).</summary>
    public interface IAgentProcess : IDisposable                    // 에이전트 프로세스
    {
        TextReader Output { get; }                                  // 에이전트 stdout(우리가 읽음)
        TextWriter Input { get; }                                   // 에이전트 stdin(우리가 씀)
        bool HasExited { get; }                                     // 끝났는지
        int? ExitCode { get; }                                      // 종료 코드(모르면 null)
        event Action<int?> Exited;                                  // 끝남 사건
    }

    /// <summary>ncn 실행 설정.</summary>
    public sealed class NcnLaunchOptions                            // 실행 설정
    {
        public string ExePath { get; set; }                         // ncn.exe 경로
        public string WorkingDirectory { get; set; }                // 작업 폴더(솔루션 폴더)
        public string ConfigPath { get; set; }                      // --config(비우면 없음)
        public bool Verbose { get; set; }                           // --verbose(stderr DEBUG 로그)
        public List<string> ExtraArgs { get; set; } = new List<string>();   // 추가 인자(--allow 등)
        public Dictionary<string, string> Environment { get; set; } = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);   // 덮어쓸 환경변수(null 값 = 지움)

        /// <summary>명령줄 인자 목록: --stdio [--config p] [--verbose] [추가…].</summary>
        public List<string> BuildArguments()                        // 인자 만들기
        {
            var args = new List<string> { "--stdio" };              // ACP 모드
            if (!string.IsNullOrWhiteSpace(ConfigPath)) { args.Add("--config"); args.Add(ConfigPath); }   // 설정 파일
            if (Verbose) args.Add("--verbose");                     // 자세한 로그
            if (ExtraArgs != null) args.AddRange(ExtraArgs);        // 추가 인자
            return args;                                            // 결과
        }
    }

    /// <summary>실제 ncn 프로세스.</summary>
    public sealed class NcnProcess : IAgentProcess                  // ncn 프로세스
    {
        private readonly Process _process;                          // 프로세스
        private readonly Action<string> _stderr;                    // stderr 줄 받는 곳
        private int _exitRaised;                                    // Exited 한 번만

        public TextReader Output { get; }                           // stdout 읽개
        public TextWriter Input { get; }                            // stdin 쓰개
        public int ProcessId => _process.Id;                        // pid(로그용)
        public event Action<int?> Exited;                           // 끝남 사건

        public bool HasExited                                       // 끝났는지
        {
            get { try { return _process.HasExited; } catch { return true; } }   // 핸들 오류면 끝난 것으로
        }

        public int? ExitCode                                        // 종료 코드
        {
            get { try { return _process.HasExited ? (int?)_process.ExitCode : null; } catch { return null; } }   // 모르면 null
        }

        private NcnProcess(Process process, Action<string> stderr)  // 생성자(Start 로만)
        {
            _process = process;                                     // 보관
            _stderr = stderr;                                       // 보관
            Output = new StreamReader(process.StandardOutput.BaseStream, new UTF8Encoding(false), false, 1 << 16);   // stdout 을 UTF-8 로 직접(BOM 감지 안 함)
            Input = new StreamWriter(process.StandardInput.BaseStream, new UTF8Encoding(false), 1 << 16) { AutoFlush = false, NewLine = "\n" };   // stdin 을 BOM 없는 UTF-8 로 직접
        }

        /// <summary>
        /// 띄우기. 창 없이(CreateNoWindow), 환경변수 덮어쓰기, Job Object 에 넣기, stderr 비동기 수집.
        /// 첫 줄로 빈 줄 하나를 보낸다: .NET Framework 는 StandardInput 을 콘솔 인코딩으로 만드는데,
        /// 시스템 코드 페이지가 65001(UTF-8 베타 설정)이면 BOM 을 먼저 써 버린다 → BOM 만 있는 줄을 ncn 이 버리게 한다.
        /// </summary>
        public static NcnProcess Start(NcnLaunchOptions options, IProcessLifetimeGuard guard, Action<string> stderrLine)   // 시작
        {
            if (string.IsNullOrEmpty(options.ExePath) || !File.Exists(options.ExePath))   // 실행 파일 확인
                throw new FileNotFoundException("ncn 실행 파일이 없습니다: " + options.ExePath, options.ExePath);   // 없음
            var psi = new ProcessStartInfo                          // 시작 정보
            {
                FileName = options.ExePath,                         // ncn.exe
                Arguments = CommandLine.Join(options.BuildArguments()),   // 인자 한 줄
                WorkingDirectory = Directory.Exists(options.WorkingDirectory ?? "") ? options.WorkingDirectory : System.Environment.CurrentDirectory,   // 작업 폴더(없으면 현재)
                UseShellExecute = false,                            // 파이프 쓰려면 false
                RedirectStandardInput = true,                       // stdin 파이프
                RedirectStandardOutput = true,                      // stdout 파이프
                RedirectStandardError = true,                       // stderr 파이프
                CreateNoWindow = true,                              // 콘솔 창 없음
                StandardOutputEncoding = Encoding.UTF8,             // (BaseStream 을 쓰지만 기본값도 UTF-8)
                StandardErrorEncoding = Encoding.UTF8,              // stderr 해독(PYTHONIOENCODING 과 맞춤)
            };
            psi.Environment["PYTHONIOENCODING"] = "utf-8";          // 파이썬 stderr 도 UTF-8(한글 로그)
            psi.Environment["PYTHONUTF8"] = "1";                    // 파이썬 UTF-8 모드
            if (options.Environment != null)                        // 덮어쓸 환경변수
                foreach (var kv in options.Environment)             // 하나씩
                {
                    if (kv.Value == null) psi.Environment.Remove(kv.Key);   // null = 지움
                    else psi.Environment[kv.Key] = kv.Value;        // 값 넣기
                }
            var p = new Process { StartInfo = psi, EnableRaisingEvents = true };   // 프로세스(Exited 사건 켜기)
            if (!p.Start()) throw new InvalidOperationException("ncn 을 시작하지 못했습니다: " + options.ExePath);   // 시작
            try { (guard ?? new NullLifetimeGuard()).Attach(p); }   // Job Object 에 넣기
            catch (Exception e) { stderrLine?.Invoke("[ncn-vs] Job Object 연결 실패: " + e.Message); }   // 실패해도 계속(경고만)
            var np = new NcnProcess(p, stderrLine);                 // 감싸기
            p.ErrorDataReceived += (s, e) => { if (e.Data != null) np._stderr?.Invoke(e.Data); };   // stderr 한 줄씩
            p.Exited += (s, e) => np.RaiseExited();                 // 끝남
            p.BeginErrorReadLine();                                 // stderr 비동기 읽기 시작
            np.Input.Write("\n");                                   // BOM 방지용 빈 줄(위 설명)
            np.Input.Flush();                                       // 내보냄
            if (p.HasExited) np.RaiseExited();                      // 이미 끝났으면(사건 놓침 대비)
            return np;                                              // 결과
        }

        private void RaiseExited()                                  // 끝남 알림(한 번만)
        {
            if (System.Threading.Interlocked.Exchange(ref _exitRaised, 1) != 0) return;   // 이미 알림
            Exited?.Invoke(ExitCode);                               // 사건
        }

        public void Dispose()                                       // 정리: stdin 닫고 잠깐 기다린 뒤 강제 종료
        {
            try { Input.Dispose(); } catch { }                      // stdin 닫기 → ncn 은 EOF 로 정상 종료
            try
            {
                if (!_process.WaitForExit(3000)) _process.Kill();   // 3초 안에 안 끝나면 강제 종료
            }
            catch { }                                               // 이미 끝남 등
            try { _process.Dispose(); } catch { }                   // 핸들 정리
        }
    }
}
