// vs/NcnVs.Core/Host/DevEnvironment.cs — VS 개발자 환경변수(VSINSTALLDIR·DevEnvDir·PATH 앞 msbuild 등) 만들기 (계획 14-2 V7)
using System;                                                       // StringComparison
using System.Collections.Generic;                                   // 사전
using System.IO;                                                    // Path

namespace NcnVs.Core.Host                                           // 호스트 이름공간
{
    /// <summary>
    /// VsDevCmd.bat 전체를 흉내 내지 않고, Bash 도구가 msbuild·dotnet test·devenv 를 찾는 데 필요한 것만 넣는다.
    /// (VC 컴파일러 cl.exe 까지 필요하면 사용자가 옵션 "추가 인자"가 아니라 시스템 PATH 로 해결 — README 문제 해결 참고)
    /// </summary>
    public static class DevEnvironment                              // 개발자 환경
    {
        /// <summary>devenv.exe 경로(…\Common7\IDE\devenv.exe) → 설치 폴더(끝에 \). 모르면 null.</summary>
        public static string InstallDirFromDevenv(string devenvPath)   // 설치 폴더 찾기
        {
            if (string.IsNullOrEmpty(devenvPath)) return null;      // 없음
            var parts = devenvPath.Replace('/', '\\').Split('\\');   // 조각(Path 는 OS 마다 구분자가 달라 직접 자름)
            if (parts.Length < 4) return null;                      // 설치폴더\Common7\IDE\devenv.exe 보다 짧음
            if (!string.Equals(parts[parts.Length - 2], "IDE", StringComparison.OrdinalIgnoreCase)) return null;   // …\IDE\devenv.exe 가 아님
            if (!string.Equals(parts[parts.Length - 3], "Common7", StringComparison.OrdinalIgnoreCase)) return null;   // …\Common7\IDE 가 아님
            var root = string.Join("\\", parts, 0, parts.Length - 3); // 설치 폴더
            if (root.Length == 0) return null;                      // 모양이 다름
            return root.EndsWith("\\") ? root : root + "\\";        // VsDevCmd 처럼 끝에 \
        }

        /// <summary>
        /// 넘길 환경변수 사전. installDir = 설치 폴더(끝 \), version = "17.0" 등, arm64 = ARM64 VS 인지, currentPath = 지금 PATH.
        /// </summary>
        public static Dictionary<string, string> Build(string installDir, string version, bool arm64, string currentPath)   // 만들기
        {
            var env = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);   // 결과(Windows 는 대소문자 무시)
            if (string.IsNullOrEmpty(installDir)) return env;       // 설치 폴더 모름 → 없음
            if (!installDir.EndsWith("\\")) installDir += "\\";     // 끝 \ 보장
            var ideDir = installDir + "Common7\\IDE\\";             // devenv 폴더
            env["VSINSTALLDIR"] = installDir;                       // VS 설치 폴더
            env["DevEnvDir"] = ideDir;                              // devenv 폴더
            env["VSCMD_ARG_HOST_ARCH"] = arm64 ? "arm64" : "x64";   // 호스트 아키텍처
            env["VSCMD_ARG_TGT_ARCH"] = arm64 ? "arm64" : "x64";    // 대상 아키텍처
            if (!string.IsNullOrEmpty(version)) env["VisualStudioVersion"] = version;   // 17.0 / 18.0
            var msbuildBin = installDir + "MSBuild\\Current\\Bin\\" + (arm64 ? "arm64" : "amd64");   // 아키텍처별 msbuild
            var front = new List<string>                            // PATH 앞에 넣을 폴더(순서대로)
            {
                msbuildBin,                                         // msbuild.exe
                installDir + "MSBuild\\Current\\Bin",               // 32비트 msbuild(없을 수 있음)
                ideDir + "CommonExtensions\\Microsoft\\TestWindow", // vstest.console.exe
                ideDir + "CommonExtensions\\Microsoft\\TeamFoundation\\Team Explorer\\Git\\cmd",   // VS 동봉 git
                installDir + "Common7\\Tools",                      // VsDevCmd 등
                ideDir,                                             // devenv.exe
            };
            var existing = currentPath ?? "";                       // 지금 PATH
            var parts = new List<string>();                         // 새 PATH 조각
            foreach (var f in front)                                // 앞 폴더마다
                if (existing.IndexOf(f, StringComparison.OrdinalIgnoreCase) < 0) parts.Add(f);   // 이미 있으면 빼기
            if (existing.Length > 0) parts.Add(existing);           // 원래 PATH 뒤에
            env["PATH"] = string.Join(";", parts);                  // 합침
            return env;                                             // 결과
        }
    }
}
