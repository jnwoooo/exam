"""0.7.1 에서 더한 기능의 시험: Windows SDK 경로 직접 지정(--sdk-path, 환경변수 VUPDATE_SDK_PATH).

사용: python test_v071.py <vupdate.py 가 있는 폴더> <작업 폴더>
가짜 SDK 폴더로 경로 해석과 전달을 확인한다. 실제 컴파일러를 부르는 것은 Windows 에서만 확인할 수 있다.
"""
import json, os, shutil, subprocess, sys

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def touch(*parts):
    p = os.path.join(work, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def cli(*args, env_add=None):
    en = dict(os.environ)
    for k in ("VUPDATE_LLM_ENDPOINT", "VUPDATE_VS_PATH", "VUPDATE_SDK_PATH", "VUPDATE_SKD_PATH"):
        en.pop(k, None)
    en.update(env_add or {})
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def md(root):
    hits = [os.path.join(b, "preflight.md") for b, _d, fs in os.walk(root) if "preflight.md" in fs]
    return open(hits[0], encoding="utf-8").read() if hits else ""


for ver in ("10.0.9999.0", "10.0.17763.0", "10.0.19041.0"):
    touch("kits10", "Include", ver, "ucrt", "math.h")
    touch("kits10", "Include", ver, "um", "windows.h")
    touch("kits10", "Include", ver, "shared", "winerror.h")
touch("kits10", "Include", "10.0.19041.0", "winrt", "wrl.h")
touch("kits81", "Include", "um", "windows.h")
touch("kits81", "Include", "shared", "winerror.h")
touch("v71a", "Include", "windows.h")
os.makedirs(os.path.join(work, "empty"))
k10, inc = os.path.join(work, "kits10"), os.path.join(work, "kits10", "Include")

a = V.resolve_sdk(k10)
check("키트 폴더: 가장 높은 판을 숫자로 견줘 고르고(19041 > 9999) ucrt, shared, um, winrt 순서로 냄",
      a["version"] == "10.0.19041.0" and [os.path.basename(x) for x in a["include_dirs"]] == ["ucrt", "shared", "um", "winrt"]
      and a["math_h"].endswith(os.path.join("10.0.19041.0", "ucrt", "math.h")) and a["windows_h"].endswith(os.path.join("um", "windows.h")))
check("Include 폴더를 줘도 같은 결과", V.resolve_sdk(inc)["include_dirs"] == a["include_dirs"])
b = V.resolve_sdk(os.path.join(inc, "10.0.17763.0"))
check("판 폴더를 주면 그 판을 씀", b["version"] == "10.0.17763.0" and len(b["include_dirs"]) == 3 and "17763" in b["include_dirs"][0])
c = V.resolve_sdk(os.path.join(work, "kits81"))
check("판 폴더가 없는 키트(8.1)", [os.path.basename(x) for x in c["include_dirs"]] == ["shared", "um"] and not c["math_h"] and c["windows_h"])
d = V.resolve_sdk(os.path.join(work, "v71a"))
check("구 SDK(Include 에 windows.h)", d["include_dirs"] == [os.path.join(work, "v71a", "Include")] and d["windows_h"])
check("헤더가 없는 폴더와 빈 값", V.resolve_sdk(os.path.join(work, "empty"))["include_dirs"] == [] and V.resolve_sdk("")["include_dirs"] == [])
check("따옴표가 붙은 경로도 받음", V.resolve_sdk('"%s"' % k10)["version"] == "10.0.19041.0")

t = V.find_tools("", k10)
check("find_tools: 지정한 SDK 를 도구 정보에 담음", t["sdk_include"] == a["include_dirs"] and t["sdk_version"] == "10.0.19041.0" and t["sdk_math_h"] == a["math_h"])
os.environ["INCLUDE"] = "X_OLD"
V._VS_ENV.clear()
env = V.get_vs_env({"cl_in_path": "cl", "sdk_include": a["include_dirs"]}, "x86")
check("컴파일 환경: 지정한 SDK 의 헤더 폴더가 INCLUDE 맨 앞에 들어감",
      env["INCLUDE"] == os.pathsep.join(a["include_dirs"] + ["X_OLD"]) and os.environ["INCLUDE"] == "X_OLD")
V._VS_ENV.clear()
check("SDK 를 지정하지 않으면 INCLUDE 를 건드리지 않음", V.get_vs_env({"cl_in_path": "cl"}, "x86")["INCLUDE"] == "X_OLD")

pkg = os.path.join(work, "PKG")
V.write_bytes(os.path.join(pkg, "a.cpp"), b"int f() { return 1; }\r\n")
code, text = cli("preflight", "--package", pkg, "--sdk-path", k10, "--out", os.path.join(work, "p1"))
check("preflight --sdk-path: 결과에 지정한 경로와 판이 나옴", "직접 지정" in md(os.path.join(work, "p1")) and "10.0.19041.0" in md(os.path.join(work, "p1")))
code, text = cli("preflight", "--package", pkg, "--out", os.path.join(work, "p2"), env_add={"VUPDATE_SDK_PATH": os.path.join(inc, "10.0.17763.0")})
check("환경변수 VUPDATE_SDK_PATH 로도 됨", "직접 지정" in md(os.path.join(work, "p2")) and "10.0.17763.0" in md(os.path.join(work, "p2")))
code, text = cli("preflight", "--package", pkg, "--sdk-path", k10, "--out", os.path.join(work, "p3"), env_add={"VUPDATE_SDK_PATH": os.path.join(work, "kits81")})
check("옵션이 환경변수보다 우선", "10.0.19041.0" in md(os.path.join(work, "p3")))
code, text = cli("preflight", "--package", pkg, "--out", os.path.join(work, "p4"), env_add={"VUPDATE_SKD_PATH": k10})
check("오타 이름(VUPDATE_SKD_PATH)도 받아 주고 경고함", "10.0.19041.0" in md(os.path.join(work, "p4")) and "오타" in text)
code, text = cli("preflight", "--package", pkg, "--sdk-path", os.path.join(work, "empty"), "--out", os.path.join(work, "p5"))
check("헤더가 없는 경로를 주면 경고하고 자동 탐지로 넘어감", "찾지 못했습니다" in text and "찾지 못해 자동 탐지" in md(os.path.join(work, "p5")))
code, text = cli("preflight", "--package", pkg, "--out", os.path.join(work, "p6"), env_add={"VUPDATE_SDK_PATH": "", "VUPDATE_VS_PATH": ""})
check("빈 환경변수는 지정하지 않은 것과 같음", "직접 지정" not in md(os.path.join(work, "p6")))
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--no-llm", "--out", os.path.join(work, "r1"),
                 env_add={"VUPDATE_SDK_PATH": k10, "VUPDATE_VS_PATH": os.path.join(work, "vs2022")})
check("run 에서도 두 환경변수를 읽음(SDK 는 로그에, 종료 코드 0)", code == 0 and "지정한 Windows SDK 경로를 씁니다" in text)

for name, okv in checks:
    print("[%s] %s" % ("통과" if okv else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, okv in checks if okv)))
sys.exit(0 if all(okv for _n, okv in checks) else 1)
