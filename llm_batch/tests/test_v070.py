"""0.7.0 에서 더한 기능의 시험: 사용자 요청문(--ask, --ask-file, 요청 파일의 asks).

사용: python test_v070.py <vupdate.py 가 있는 폴더> <작업 폴더>
모의 sLLM 은 SetWindowLong 이 있는 줄을 SetWindowLongPtr 로 고치는 것만 흉내 낸다. 그래서 이 시험이 확인하는 것은
"요청문을 읽어 대상이 쓰인 곳을 찾고, 요청문을 지침으로 sLLM 에 보내고, 응답을 반영하는 흐름"이다.
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


srv = V._selftest_mock_server()
URL = "http://127.0.0.1:%d/v1" % srv.server_address[1]


def cli(*args):
    en = dict(os.environ)
    en.pop("VUPDATE_LLM_ENDPOINT", None)
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def result(root, *parts):
    hits = [os.path.join(b, parts[-1]) for b, _d, fs in os.walk(root) if parts[-1] in fs and b.replace("\\", "/").endswith("/".join(("out",) + parts[:-1]))]
    return open(hits[0], "rb").read().decode("cp949" if parts[0] == "changed" else "utf-8") if hits else ""


call = "    SetWindowLong(h, GWL_USERDATA, (LONG)p);"
fixed = "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p);"
pkg = os.path.join(work, "PKG")
body = ["#include <windows.h>", "void A(HWND h, void* p)", "{", call, "}"] + ["// 빈 줄 %d" % i for i in range(60)] + ["void B(HWND h, void* p)", "{", call, "}", ""]
V.write_bytes(os.path.join(pkg, "Src", "Ui.cpp"), "\r\n".join(body).encode("cp949"))
V.write_bytes(os.path.join(pkg, "Lib", "Old.cpp"), "\r\n".join(body[:5] + [""]).encode("cp949"))
base = ["run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--llm-endpoint", URL, "--no-compile"]

n0 = len(srv.prompts)
code, text = cli(*base, "--ask", "SetWindowLong: SetWindowLongPtr 로 바꾸고 인덱스와 형 변환도 맞춘다", "--ask", "결과에 설명을 붙이지 않는다", "--out", os.path.join(work, "r1"))
ui, old = result(os.path.join(work, "r1"), "changed", "Src", "Ui.cpp"), result(os.path.join(work, "r1"), "changed", "Lib", "Old.cpp")
sent = srv.prompts[n0:]
check("--ask: 대상이 쓰인 곳을 모든 파일에서 찾아 고침(멀리 떨어진 두 곳, 다른 파일)", ui.count(fixed) == 2 and fixed in old and code == 0)
check("--ask: 요청문이 지침으로, 대상 없는 문장은 공통 요청으로 프롬프트에 들어감",
      any("사용자 요청: SetWindowLongPtr 로 바꾸고" in p and "대상은 SetWindowLong 이다" in p for p in sent)
      and all("[공통 요청]" in p and "결과에 설명을 붙이지 않는다" in p for p in sent))

askf = os.path.join(work, "asks.txt")
with open(askf, "w", encoding="cp949") as fh:
    fh.write("# 주석 줄\n\n@Src/* /SetWindow(?:Long|Word)\\b/: Ptr 가 붙은 함수로 바꾼다\n")
code, text = cli(*base, "--ask-file", askf, "--out", os.path.join(work, "r2"))
check("--ask-file: 줄 단위로 읽고(CP949, 주석·빈 줄 건너뜀) 정규식 대상과 파일 범위를 지킴",
      result(os.path.join(work, "r2"), "changed", "Src", "Ui.cpp").count(fixed) == 2 and not result(os.path.join(work, "r2"), "changed", "Lib", "Old.cpp"))

req = os.path.join(work, "req.json")
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"common": ["주석은 그대로 둔다"], "asks": [{"target": "SetWindowLong", "files": "Lib/*", "text": "SetWindowLongPtr 로 바꾼다"}]}, fh, ensure_ascii=False)
cfg = os.path.join(work, "server.json")
with open(cfg, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"endpoint": URL}, "limits": {"ask_max_sites": 1}}, fh)
code, text = cli(*base[:7], "--no-compile", "--server-config", cfg, "--request", req, "--ask", "@Src/* SetWindowLong: SetWindowLongPtr 로 바꾼다", "--out", os.path.join(work, "r3"))
ui3, old3, manual3, report3 = (result(os.path.join(work, "r3"), "changed", "Src", "Ui.cpp"), result(os.path.join(work, "r3"), "changed", "Lib", "Old.cpp"),
                               result(os.path.join(work, "r3"), "manual_review.md"), result(os.path.join(work, "r3"), "report.md"))
check("요청 파일의 asks 와 명령줄의 요청문을 함께 처리(요청마다 ID 가 다름)", fixed in old3 and "ASK-1" in report3 and "ASK-2" in report3)
check("요청 하나가 다루는 줄의 상한: 넘는 곳은 고치지 않고 수동 검토로(종료 코드 1)",
      ui3.count(fixed) == 1 and ui3.count("SetWindowLong(h, GWL_USERDATA") == 1 and "상한(1)을 넘음" in manual3 and code == 1)
check("보고서의 변경 계약에 요청문과 처리 건수가 나옴", "사용자 요청문" in report3 and "요청문: SetWindowLongPtr 로 바꾼다" in report3)

code, text = cli(*base, "--ask", "/Set(Window/: 잘못된 정규식", "--out", os.path.join(work, "r4"))
check("잘못된 정규식의 요청문은 입력 오류(종료 코드 3)", code == 3 and "정규식" in text)
code, text = cli(*base, "--ask-file", os.path.join(work, "none.txt"), "--out", os.path.join(work, "r5"))
check("없는 요청문 파일은 입력 오류(종료 코드 3)", code == 3)
code, text = cli(*base, "--ask", "NoSuchName: 없는 이름", "--out", os.path.join(work, "r6"))
check("대상을 찾지 못한 요청은 경고만 내고 끝남(종료 코드 0)", code == 0 and "찾지 못했습니다" in text)
asks, common = V.parse_asks(["a, b: x 를 y 로", "*: 공통 문장", "한글로 시작하는 문장: 콜론이 있어도 공통", "@Inc/* T_NAME: z"])
check("요청문 줄 해석: 대상 여럿, '*:' 와 대상이 아닌 머리말은 공통 요청, 파일 범위",
      [a["targets"] for a in asks] == [["a", "b"], ["T_NAME"]] and asks[1]["files"] == "inc/*" and len(common) == 2)

srv.shutdown()
for name, okv in checks:
    print("[%s] %s" % ("통과" if okv else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, okv in checks if okv)))
sys.exit(0 if all(okv for _n, okv in checks) else 1)
