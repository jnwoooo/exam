// vs/NcnVs.Core/Host/ProcessLifetime.cs — 자식 프로세스를 VS 와 함께 끝내는 장치(Windows Job Object, 그 밖 OS 는 아무것도 안 함) (계획 14-2 V6)
using System;                                                       // IntPtr
using System.Diagnostics;                                           // Process
using System.Runtime.InteropServices;                               // P/Invoke

namespace NcnVs.Core.Host                                           // 호스트 이름공간
{
    /// <summary>자식 프로세스 수명 묶개. 묶은 프로세스(와 그 자식들)는 묶개가 닫히거나 VS 가 죽으면 함께 끝난다.</summary>
    public interface IProcessLifetimeGuard : IDisposable            // 수명 묶개
    {
        void Attach(Process process);                               // 프로세스 묶기
    }

    /// <summary>아무것도 안 하는 묶개(Windows 가 아닐 때·시험용).</summary>
    public sealed class NullLifetimeGuard : IProcessLifetimeGuard   // 빈 묶개
    {
        public void Attach(Process process) { }                     // 무시
        public void Dispose() { }                                   // 무시
    }

    /// <summary>
    /// JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 가 걸린 Job Object. 핸들은 이 객체(=VS 프로세스)가 갖고 있으므로
    /// VS 가 정상 종료·강제 종료되면 커널이 핸들을 닫고 → ncn 과 ncn 이 띄운 powershell 등이 모두 끝난다.
    /// </summary>
    public sealed class WindowsJobObject : IProcessLifetimeGuard    // Job Object 묶개
    {
        private IntPtr _job;                                        // Job 핸들

        /// <summary>지금 OS 에 맞는 묶개(Windows 면 Job Object, 실패하거나 아니면 빈 묶개).</summary>
        public static IProcessLifetimeGuard CreateForCurrentOs(Action<string> log = null)   // 만들기
        {
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return new NullLifetimeGuard();   // Windows 아님
            try { return new WindowsJobObject(); }                  // Job 만들기
            catch (Exception e)                                     // 실패(권한 등)
            {
                log?.Invoke("Job Object 를 만들지 못함(ncn 이 VS 종료 뒤 남을 수 있음): " + e.Message);   // 경고
                return new NullLifetimeGuard();                     // 빈 묶개로
            }
        }

        public WindowsJobObject()                                   // 생성자
        {
            _job = CreateJobObject(IntPtr.Zero, null);              // 이름 없는 Job
            if (_job == IntPtr.Zero) throw new InvalidOperationException("CreateJobObject 실패: " + Marshal.GetLastWin32Error());   // 실패
            var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();  // 확장 제한 정보
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;   // 핸들 닫히면 모두 종료
            var size = (uint)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));   // 구조체 크기
            if (!SetInformationJobObject(_job, JobObjectExtendedLimitInformation, ref info, size))   // 설정
            {
                var err = Marshal.GetLastWin32Error();              // 오류 코드
                CloseHandle(_job);                                  // 핸들 정리
                _job = IntPtr.Zero;                                 // 비움
                throw new InvalidOperationException("SetInformationJobObject 실패: " + err);   // 실패
            }
        }

        public void Attach(Process process)                         // 프로세스 묶기
        {
            if (_job == IntPtr.Zero || process == null) return;     // 없으면 무시
            if (!AssignProcessToJobObject(_job, process.Handle))    // Job 에 넣기(그 뒤 만드는 자식도 자동 포함)
                throw new InvalidOperationException("AssignProcessToJobObject 실패: " + Marshal.GetLastWin32Error());   // 실패
        }

        public void Dispose()                                       // 정리(= 묶인 프로세스 모두 종료)
        {
            if (_job == IntPtr.Zero) return;                        // 이미 닫힘
            CloseHandle(_job);                                      // 핸들 닫기
            _job = IntPtr.Zero;                                     // 비움
        }

        // ── Win32 선언(kernel32) ──
        private const int JobObjectExtendedLimitInformation = 9;    // JOBOBJECTINFOCLASS 값
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;   // 제한 플래그

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION            // 기본 제한
        {
            public long PerProcessUserTimeLimit;                    // LARGE_INTEGER
            public long PerJobUserTimeLimit;                        // LARGE_INTEGER
            public uint LimitFlags;                                 // 플래그
            public UIntPtr MinimumWorkingSetSize;                   // SIZE_T
            public UIntPtr MaximumWorkingSetSize;                   // SIZE_T
            public uint ActiveProcessLimit;                         // DWORD
            public UIntPtr Affinity;                                // ULONG_PTR
            public uint PriorityClass;                              // DWORD
            public uint SchedulingClass;                            // DWORD
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS                                  // 입출력 계수
        {
            public ulong ReadOperationCount;                        // 읽기 횟수
            public ulong WriteOperationCount;                       // 쓰기 횟수
            public ulong OtherOperationCount;                       // 기타 횟수
            public ulong ReadTransferCount;                         // 읽은 바이트
            public ulong WriteTransferCount;                        // 쓴 바이트
            public ulong OtherTransferCount;                        // 기타 바이트
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION         // 확장 제한
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;   // 기본 제한
            public IO_COUNTERS IoInfo;                              // 입출력
            public UIntPtr ProcessMemoryLimit;                      // SIZE_T
            public UIntPtr JobMemoryLimit;                          // SIZE_T
            public UIntPtr PeakProcessMemoryUsed;                   // SIZE_T
            public UIntPtr PeakJobMemoryUsed;                       // SIZE_T
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);   // Job 만들기

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, uint cbInfoLength);   // 제한 설정

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);   // 프로세스 넣기

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);     // 핸들 닫기
    }
}
