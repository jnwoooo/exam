"""동시 호출 시험: 동시 수 1, 4, 8로 같은 패키지를 처리해 결과가 같은지, 함수가 깨지지 않는지 본다.

사용: python test_parallel.py <vupdate.py 가 있는 폴더> <작업 폴더>
확인하는 것
  1. 모의 서버에 동시에 걸린 요청 수가 설정한 동시 수를 넘지 않는다.
  2. 동시 수가 달라도 결과 파일이 바이트 단위로 같다.
  3. 같은 함수 안의 떨어진 두 지점, 가까운 두 지점이 모두 고쳐지고 괄호 짝이 유지된다.
  4. CP949 와 CRLF 가 유지된다.
"""
import hashlib, json, os, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
shutil.rmtree(work, ignore_errors=True)
pkg = os.path.join(work, "PAR_PKG")
os.makedirs(os.path.join(pkg, "Src"))


def make_file(idx):
    lines = ["// 동시 호출 시험용 파일 %d (CP949, CRLF)" % idx, "#include <windows.h>", ""]
    n = 0
    for f in range(8):                                   # 함수 8개, 함수마다 수정 지점 1개
        lines += ["void Attach%d_%d(HWND hWnd, void* self%d)" % (idx, f, f), "{"]
        lines += ["    int pad%d = %d;  // 채움 줄" % (k, k) for k in range(20)]
        lines.append("    SetWindowLong(hWnd, GWL_USERDATA, (LONG)self%d);  // 지점 %d-%d" % (f, idx, f))
        lines += ["    pad0 += %d;" % k for k in range(20)]
        lines += ["}", ""]
        n += 1
    lines += ["void Long%d(HWND hA, HWND hB, HWND hC, void* pa, void* pb, void* pc)" % idx, "{"]
    for label, far in (("hA", "pa"), ("hB", "pb"), ("hC", "pc")):   # 한 함수 안의 떨어진 지점 3개
        lines += ["    if (%s) { int v = 0; v++; }  // 긴 함수의 채움 줄" % label for _ in range(40)]
        lines.append("    SetWindowLong(%s, GWL_USERDATA, (LONG)%s);" % (label, far))
        n += 1
    lines += ["    int tail = 0;" for _ in range(40)]
    lines.append("    SetWindowLong(hA, GWL_WNDPROC, (LONG)pb);")   # 가까운 지점 2개(한 조각)
    lines.append("    tail++;")
    lines.append("    SetWindowLong(hB, GWL_WNDPROC, (LONG)pc);")
    lines += ["}", ""]
    n += 2
    data = "\r\n".join(lines).encode("cp949")
    with open(os.path.join(pkg, "Src", "File%d.cpp" % idx), "wb") as fh:
        fh.write(data)
    return n


TOTAL_SITES = sum(make_file(i) for i in range(3))
prof = os.path.join(work, "p.json")
with open(prof, "w", encoding="utf-8") as fh:
    json.dump({"from": {"vs": 2008}, "to": {"vs": 2022, "platform": "x64"}}, fh)
scfg = os.path.join(work, "server.json")              # 서버가 허용하는 동시 수를 8로 둔다(--max-parallel 은 이 값 이하로만 적용된다)
with open(scfg, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"max_parallel": 8}}, fh)

stat = {"cur": 0, "max": 0, "calls": 0}
lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        with lock:
            stat["cur"] += 1
            stat["calls"] += 1
            stat["max"] = max(stat["max"], stat["cur"])
        time.sleep(0.25)                                 # 모델 응답 시간 흉내
        user = body["messages"][-1]["content"]
        code = user.split("-----BEGIN CODE-----", 1)[1].split("-----END CODE-----", 1)[0]
        blocks = []
        for line in code.split("\n"):
            if "SetWindowLong(" in line:
                new = (line.replace("SetWindowLong(", "SetWindowLongPtr(").replace("GWL_USERDATA", "GWLP_USERDATA")
                       .replace("GWL_WNDPROC", "GWLP_WNDPROC").replace("(LONG)", "(LONG_PTR)"))
                blocks.append("<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (line, new))
        content = "\n".join(blocks) if blocks else "NO_CHANGE"
        data = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        with lock:
            stat["cur"] -= 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/v1" % srv.server_address[1]


def tree_hash(root):
    out = {}
    for base, _d, files in os.walk(root):
        for f in sorted(files):
            p = os.path.join(base, f)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


results = {}
for n in (1, 4, 8):
    with lock:
        stat.update(cur=0, max=0, calls=0)
    out = os.path.join(work, "r%d" % n)
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "run", "--package", pkg, "--profile", prof,
                        "--server-config", scfg, "--llm-endpoint", url, "--max-parallel", str(n), "--out", out],
                       capture_output=True)
    sec = time.time() - t0
    run_dir = [os.path.join(b, d) for b, ds, _f in os.walk(out) for d in ds if d == "out"][0]
    results[n] = {"code": p.returncode, "sec": sec, "max": stat["max"], "calls": stat["calls"],
                  "hash": tree_hash(os.path.join(run_dir, "changed")), "changed": os.path.join(run_dir, "changed")}
srv.shutdown()

checks = []
for n, r in results.items():
    print("동시 수 %d: 종료 코드 %d, %.1f초, 호출 %d회, 서버에 동시에 걸린 최대 요청 %d"
          % (n, r["code"], r["sec"], r["calls"], r["max"]))
    checks.append(("동시 수 %d: 최대 동시 요청이 설정 이하" % n, r["max"] <= n))
checks.append(("동시 수 8에서 실제로 여러 요청이 동시에 나감", results[8]["max"] >= 4))
checks.append(("동시 수가 달라도 결과 파일이 바이트 단위로 같음",
               results[1]["hash"] == results[4]["hash"] == results[8]["hash"] and len(results[1]["hash"]) == 3))
checks.append(("동시 수 8이 1보다 빠름", results[8]["sec"] < results[1]["sec"]))
left = fixed = 0
ok_bal = ok_crlf = True
for name in sorted(results[8]["hash"]):
    raw = open(os.path.join(results[8]["changed"], name), "rb").read()
    text = raw.decode("cp949")
    left += text.count("SetWindowLong(")
    fixed += text.count("SetWindowLongPtr(")
    ok_bal = ok_bal and text.count("{") == text.count("}") and text.count("(") == text.count(")")
    ok_crlf = ok_crlf and raw.count(b"\r\n") == raw.count(b"\n")
checks.append(("수정 지점 %d곳이 모두 고쳐짐" % TOTAL_SITES, left == 0 and fixed == TOTAL_SITES))
checks.append(("괄호 짝 유지", ok_bal))
checks.append(("CP949 로 읽히고 CRLF 유지", ok_crlf))
print("\n===== 동시 호출 시험 결과 =====")
for label, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", label))
sys.exit(0 if all(ok for _l, ok in checks) else 1)
