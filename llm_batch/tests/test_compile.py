"""컴파일 검사(L3) 흐름 시험: 모의 컴파일러로 S8 의 병렬 검사와 되돌림을 확인한다.

사용: python test_compile.py <vupdate.py 가 있는 폴더> <작업 폴더>
주의: 실제 cl.exe 를 부르지 않는다. run_cl_syntax 를 모의 함수로 바꿔 S8 의 흐름(미리 병렬 검사, 새 오류만 판정,
      불합격 파일의 수정 취소)만 본다. Visual Studio 탐지와 cl 호출 자체는 Windows PC 에서 따로 확인해야 한다.

확인하는 것
  1. 변경 파일의 컴파일 검사가 동시에 돌아가고, 동시 수가 설정을 넘지 않는다.
  2. 원본에도 있던 오류는 새 오류로 치지 않는다(검증 수준 L3 통과).
  3. 수정 때문에 새 오류가 생긴 파일은 수정을 취소하고 수동 검토로 보낸다.
  4. 플랫폼이 x64 면 x64 용 환경으로 검사한다.
  5. --compile-parallel 1 이면 한 번에 하나씩 돈다.
"""
import os, shutil, sys, threading, time

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
pkg = os.path.join(work, "CL_PKG")
for name in ["Good%d.cpp" % i for i in range(5)] + ["Bad.cpp"]:
    src = "\r\n".join(["// 컴파일 검사 시험 (CP949)", "#include <string.h>", "int OLD_ERR_undeclared = missing_symbol;",
                       "char* Dup(const char* s) { return strdup(s); }", ""])
    V.write_bytes(os.path.join(pkg, "Src", name), src.encode("cp949"))

stat = {"cur": 0, "max": 0, "n": 0, "arch": set()}
lock = threading.Lock()


def fake_cl(tools, src_abs, cwd, flags, timeout, arch="x86"):
    """모의 컴파일러. OLD_ERR 줄은 원본에도 있는 오류, Bad.cpp 의 _strdup 줄은 수정 때문에 생긴 새 오류로 보고한다."""
    with lock:
        stat["cur"] += 1
        stat["n"] += 1
        stat["max"] = max(stat["max"], stat["cur"])
        stat["arch"].add(arch)
    time.sleep(0.2)
    diags = []
    for no, line in enumerate(open(src_abs, encoding="cp949").read().split("\n"), 1):
        if "OLD_ERR" in line:
            diags.append({"file": src_abs, "line": no, "kind": "error", "code": "C2065", "msg": "'missing_symbol': 선언되지 않은 식별자"})
        if "_strdup(" in line and os.path.basename(src_abs) == "Bad.cpp":
            diags.append({"file": src_abs, "line": no, "kind": "error", "code": "C3861", "msg": "'_strdup': 식별자를 찾을 수 없습니다"})
    with lock:
        stat["cur"] -= 1
    return True, diags, "\n".join("%s(%d): error %s: %s" % (d["file"], d["line"], d["code"], d["msg"]) for d in diags)


V.find_tools = lambda vs_path="", sdk_path="": {"platform": "win32(모의)", "cl_all": ["fake"], "cl_modern": "fake-cl.exe", "cl_2008": "",
                                   "sdk_math_h": "fake", "mfc_afxwin_h": "", "vs_root": "fake", "vsdevcmd": "fake",
                                   "cl_in_path": "", "vs_path_given": vs_path}
V.smoke_test_compiler = lambda ctx: (True, "정상(모의 컴파일러)")
V.get_vs_env = lambda tools, arch="x86": {"PATH": ""}
V.run_cl_syntax = fake_cl


def run(**over):
    with lock:
        stat.update(cur=0, max=0, n=0, arch=set())
    ctx = V._make_ctx(V._selftest_ns(work, pkg, "", deprecated="fix", no_llm=True, **over))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        files = {r["path"]: (r["status"], r["verify_level"]) for r in ctx.store.q("SELECT * FROM files WHERE kind='source'")}
        ver = [tuple(r) for r in ctx.store.q("SELECT path, level, result, detail FROM verifications WHERE level='L3'")]
    finally:
        ctx.store.close()
        V._SINK.close()
    return code, files, ver, ctx


checks = []
code, files, ver, ctx = run(compile_parallel=4, platform="x64")
good = [files.get("Src/Good%d.cpp" % i) for i in range(5)]
bad_txt = V.read_bytes(os.path.join(ctx.run_dir, "work", "merged", "Src", "Bad.cpp")).decode("cp949")
good_txt = V.read_bytes(os.path.join(ctx.run_dir, "out", "changed", "Src", "Good0.cpp")).decode("cp949")
print("파일 상태:", files)
print("컴파일 호출 %d회, 동시에 돈 최대 수 %d, 대상 %s" % (stat["n"], stat["max"], sorted(stat["arch"])))
checks.append(("환경 등급이 '나'로 판정되어 컴파일 검사가 수행됨", ctx.grade == "나" and len(ver) >= 6))
checks.append(("컴파일 검사가 동시에 돌고 동시 수가 설정(4)을 넘지 않음", 2 <= stat["max"] <= 4))
checks.append(("원본에도 있던 오류는 새 오류로 치지 않음(변경됨, 검증 L3)", all(g == ("변경됨", "L3") for g in good)
               and "_strdup(s)" in good_txt))
checks.append(("새 오류가 생긴 파일은 수정을 취소하고 수동 검토로 보냄", files.get("Src/Bad.cpp", ("",))[0] == "수동검토"
               and "return strdup(s);" in bad_txt and any(v[0] == "Src/Bad.cpp" and "불합격" in v[2] for v in ver)))
checks.append(("플랫폼 x64 면 x64 용 환경으로 검사", stat["arch"] == {"x64"}))
checks.append(("파일마다 원본과 결과를 한 번씩만 검사(12회)", stat["n"] == 12))
code, files, ver, ctx = run(compile_parallel=1)
checks.append(("--compile-parallel 1 이면 한 번에 하나씩 검사", stat["max"] == 1 and stat["arch"] == {"x86"}))
code, files, ver, ctx = run(no_compile=True)
checks.append(("--no-compile 이면 컴파일 검사를 하지 않음(검증 L2)", stat["n"] == 0 and files.get("Src/Bad.cpp") == ("변경됨", "L2")))

print("\n===== 컴파일 검사 흐름 시험 결과(모의 컴파일러) =====")
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
sys.exit(0 if all(ok for _n, ok in checks) else 1)
