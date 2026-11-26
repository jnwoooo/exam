"""토큰 한도와 실행 옵션 시험 (0.3.0 에서 더한 기능).

사용: python test_limits.py <vupdate.py 가 있는 폴더> <작업 폴더>
모의 sLLM 서버만 쓴다. 실제 모델의 동작을 확인하는 시험이 아니다.

확인하는 것
  A. 서버가 2,000토큰짜리 공통 프롬프트를 덧붙이는데 py 는 모르는 경우: 거절된 요청을 다시 보내지 않고,
     실제 값으로 서버 몫을 알아내 끝까지 처리한다.
  B. 같은 상황에서 예약량을 설정에 적어 둔 경우: 거절이 한 번도 없다.
  C. 서버가 수치도 실제 토큰 수도 알려 주지 않는 경우: 그래도 같은 요청을 반복하지 않고 줄여서 보낸다.
  D. 서버가 요청 자체를 거절(HTTP 400)하는 경우: 다시 보내지 않고 수동 검토로 보낸다.
  K. 서버가 모델 목록으로 한도를 알려 주는 경우: 보내기 전에 한도를 낮춰 거절이 없다.
  M. 분할 순차 도중(둘째 조각)에 거절되는 경우: 끝난 앞 조각은 두고 남은 부분만 다시 나눠 끝까지 처리한다.
  L. 호출자 표시가 요청 머리말에 실린다.
  E~J. 실행 옵션(--no-llm, --only, --exclude, --max-parallel 상한, 이어서 실행할 때 옵션 유지,
       rules 명령, 환경변수 주소).
"""
import json, os, shutil, subprocess, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def make_pkg(name, pads=(5, 20, 40, 60, 80, 100)):
    """함수마다 주변 줄의 길이가 다른 패키지를 만든다(요청 크기가 여러 가지가 되도록). 수정 지점은 함수당 2곳."""
    pkg = os.path.join(work, name)
    L = ["// 토큰 한도 시험 (CP949, CRLF)", "#include <windows.h>", ""]
    for k, n in enumerate(pads):
        pad = "/* %s */" % ("채움 " * n)
        L += ["void Fn%d(HWND h, void* a, void* b)" % k, "{"]
        L += ["    int v%d = %d; %s" % (i, i, pad) for i in range(30)]
        L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)a);", "    SetWindowLong(h, GWL_USERDATA, (LONG)b);"]
        L += ["    v0 += %d; %s" % (i, pad) for i in range(30)]
        L += ["}", ""] + ["// 간격 %d" % i for i in range(40)]
    V.write_bytes(os.path.join(pkg, "Src", "A_main.cpp"), "\r\n".join(L).encode("cp949"))
    V.write_bytes(os.path.join(pkg, "Src", "B_util.cpp"), "\r\n".join(L[:69]).encode("cp949") + b"\r\n")
    V.write_bytes(os.path.join(pkg, "ThirdParty", "lib.cpp"),
                  b"#include <windows.h>\r\nvoid T(HWND h, void* p)\r\n{\r\n    SetWindowLong(h, GWL_USERDATA, (LONG)p);\r\n}\r\n")
    return pkg, len(pads) * 2


def run_inproc(pkg, url, llm=None, **ns_over):
    """vupdate 를 같은 프로세스에서 돌린다. 돌려주는 값: (종료 코드, 통계, 결과 본문, 작업 상태, 호출 기록)"""
    ctx = V._make_ctx(V._selftest_ns(work, pkg, url, platform="x64", only=["Src/A_*"], **ns_over))
    V._selftest_llm_defaults(ctx, **(llm or {}))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        stats = ctx.store.get_meta("llm_stats", {})
        tasks = {r["status"]: r["n"] for r in ctx.store.q("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        calls = [dict(r) for r in ctx.store.q("SELECT task_id, ok, kind, tok_in FROM llm_calls ORDER BY id")]
        stats["_rejected_edits"] = ctx.store.one("SELECT COUNT(*) FROM edits WHERE status='rejected'")
    finally:
        ctx.store.close()
        V._SINK.close()
    out = os.path.join(ctx.run_dir, "out", "changed", "Src", "A_main.cpp")
    txt = V.read_bytes(out).decode("cp949") if os.path.isfile(out) else ""
    return code, stats, txt, tasks, calls


def url_of(srv):
    return "http://127.0.0.1:%d/v1" % srv.server_address[1]


FIXED = "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)"
pkg, N = make_pkg("PKG")

# --- A. 서버가 2,000토큰을 덧붙이는데 py 는 모른다 ---
srv = V._selftest_mock_server(tok_scale=1.0, tok_extra=2000, limit_total=8000)
code, stats, txt, tasks, calls = run_inproc(pkg, url_of(srv), llm={"max_total_tokens": 8000, "max_output_tokens": 800})
srv.shutdown()
print("A: 통계 %s, 작업 %s, 거절 %d회" % (stats, tasks, len(srv.rejected)))
check("A: 거절이 실제로 일어남(시험 조건 확인)", len(srv.rejected) >= 1)
check("A: 거절된 요청을 그대로 다시 보내지 않음", all(srv.seen.count(p) == 1 for p in srv.rejected))
check("A: 거절 횟수가 작업 수보다 적음(매번 실패하고 다시 보내는 방식이 아님)", len(srv.rejected) < N // 2)
check("A: 서버 몫을 실제 값으로 알아냄(1,900~2,300토큰)", 1900 <= stats.get("overhead", 0) <= 2300)
check("A: 비율은 1.0 근처로 유지(0.9~1.15)", 0.9 <= stats.get("ratio", 0) <= 1.15)
check("A: %d곳이 모두 고쳐짐" % N, txt.count(FIXED) == N and "SetWindowLong(" not in txt)

# --- B. 예약량을 설정에 적어 둔 경우 ---
srv = V._selftest_mock_server(tok_scale=1.0, tok_extra=2000, limit_total=8000)
code, stats, txt, tasks, calls = run_inproc(pkg, url_of(srv), llm={
    "max_total_tokens": 8000, "max_output_tokens": 800, "server_prompt_reserve_tokens": 2000})
srv.shutdown()
print("B: 통계 %s, 작업 %s, 거절 %d회" % (stats, tasks, len(srv.rejected)))
check("B: 예약량을 적어 두면 거절이 한 번도 없음", len(srv.rejected) == 0 and stats.get("replans") == 0)
check("B: %d곳이 모두 고쳐짐" % N, txt.count(FIXED) == N)


# --- C. 서버가 수치도, 실제 토큰 수도 알려 주지 않는다 ---
class Silent(BaseHTTPRequestHandler):
    """추정의 1.3배로 세고, 한도(5000)를 넘으면 수치 없는 문구로 거절한다. 성공 응답에도 토큰 수를 담지 않는다."""
    seen, rejected = [], []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        prompt = body["messages"][-1]["content"]
        Silent.seen.append(prompt)
        n_in = int(V.est_tokens("".join(m["content"] for m in body["messages"])) * 1.3)
        if n_in + body["max_tokens"] > 5000:
            Silent.rejected.append(prompt)
            data, status = json.dumps({"error": "Input is too long: context length exceeded."}).encode(), 400
        else:
            code_part = prompt.split("-----BEGIN CODE-----\n", 1)[-1].split("\n-----END CODE-----", 1)[0]
            blocks = ["<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (
                l, l.replace("SetWindowLong(", "SetWindowLongPtr(").replace("GWL_USERDATA", "GWLP_USERDATA")
                .replace("(LONG)", "(LONG_PTR)")) for l in code_part.split("\n") if "SetWindowLong(" in l]
            data, status = json.dumps({"choices": [{"message": {"content": "\n".join(blocks) or "NO_CHANGE"}}]}).encode(), 200
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Silent)
threading.Thread(target=srv.serve_forever, daemon=True).start()
code, stats, txt, tasks, calls = run_inproc(pkg, url_of(srv), llm={"max_total_tokens": 5000, "max_output_tokens": 800})
srv.shutdown()
print("C: 통계 %s, 작업 %s, 받은 요청 %d건 중 거절 %d건" % (stats, tasks, len(Silent.seen), len(Silent.rejected)))
check("C: 거절이 실제로 일어남(시험 조건 확인)", len(Silent.rejected) >= 1)
check("C: 수치가 없어도 같은 요청을 다시 보내지 않음", len(set(Silent.seen)) == len(Silent.seen))
check("C: 표본이 없으므로 보정 표본 0개로 기록됨", stats.get("samples") == 0)
check("C: 모든 작업이 끝남(고쳐졌거나 수동 검토)", not tasks.get("pending") and txt.count(FIXED) + 2 * tasks.get("manual", 0) >= N)
check("C: 거절 횟수가 제한됨(작업당 2회 이하)", len(Silent.rejected) <= 2 * N // 2)


# --- D. 서버가 요청 자체를 거절한다(HTTP 400, 한도와 무관) ---
class Bad(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        Bad.seen.append(body["messages"][-1]["content"])
        data = json.dumps({"error": {"message": "unknown parameter 'foo'"}}).encode()
        self.send_response(400)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Bad)
threading.Thread(target=srv.serve_forever, daemon=True).start()
code, stats, txt, tasks, calls = run_inproc(pkg, url_of(srv))
srv.shutdown()
print("D: 작업 %s, 받은 요청 %d건" % (tasks, len(Bad.seen)))
check("D: 거절된 요청을 다시 보내지 않음(작업마다 한 번씩만 보냄)", len(set(Bad.seen)) == len(Bad.seen) >= 1)
check("D: 전부 수동 검토로 보내고 실행은 끝까지 감", code == V.EXIT_REVIEW and not tasks.get("done")
      and tasks.get("manual", 0) == N // 2 and len(Bad.seen) <= N // 2)

# --- K. 서버가 모델 목록으로 한도를 알려 준다 / L. 호출자 표시 ---
srv = V._selftest_mock_server(tok_scale=1.0, tok_extra=0, limit_total=5000)
callers = []
orig_post = srv.RequestHandlerClass.do_POST


def do_get(self):
    self._send(200, {"object": "list", "data": [{"id": "gemma4-31b", "max_model_len": 5000}]})


def do_post(self):
    callers.append(self.headers.get("X-Vupdate-Caller"))
    orig_post(self)


srv.RequestHandlerClass.do_GET, srv.RequestHandlerClass.do_POST = do_get, do_post
code, stats, txt, tasks, calls = run_inproc(pkg, url_of(srv), llm={"max_total_tokens": 9000, "max_output_tokens": 800},
                                            caller="시험PC\\홍길동")
srv.shutdown()
print("K: 통계 %s, 작업 %s, 거절 %d회" % (stats, tasks, len(srv.rejected)))
check("K: 서버가 알려 준 한도(5000)를 보내기 전에 적용해 거절이 없음",
      stats.get("limit") == 5000 and stats.get("limit_config") == 9000 and len(srv.rejected) == 0 and txt.count(FIXED) == N)
check("L: 호출자 표시가 요청 머리말에 실림(한글 포함)",
      callers and all(urllib.parse.unquote(c or "") == "시험PC\\홍길동" for c in callers))

# --- M. 분할 순차의 둘째 조각이 거절되는 경우 ---
pkg2 = os.path.join(work, "WIDE")
L = ["// 분할 순차 도중의 한도 초과 시험 (CP949, CRLF)", "#include <windows.h>", "", "void WideFn(HWND h, void** p)", "{"]
for k in range(40):
    L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)p[%d]);" % k, "    Sleep(%d);" % k, "    // 채움 주석 %d %s" % (k, "가나다라 " * 30)]
V.write_bytes(os.path.join(pkg2, "Src", "A_main.cpp"), "\r\n".join(L + ["}", ""]).encode("cp949"))
small = {"max_total_tokens": 8000, "max_output_tokens": 800}
srv = V._selftest_mock_server(tok_scale=1.0)                       # 1) 한도 없이 돌려 조각별 실제 크기를 잰다
code, stats, txt, tasks, calls = run_inproc(pkg2, url_of(srv), llm=small)
srv.shutdown()
n1, n2 = calls[0]["tok_in"], calls[1]["tok_in"]
limit = (n1 + n2) // 2 + 800                                       # 2) 첫 조각은 통과하고 둘째 조각(앞 조각 내용이 붙어 더 큼)은 넘는 한도
srv = V._selftest_mock_server(tok_scale=1.0, limit_total=limit)
code, stats, txt, tasks, calls = run_inproc(pkg2, url_of(srv), llm=small)
srv.shutdown()
print("M: 첫 조각 %d토큰, 둘째 조각 %d토큰, 서버 한도 %d. 통계 %s, 작업 %s" % (n1, n2, limit, stats, tasks))
check("M: 둘째 조각이 첫 조각보다 커서 둘째에서 거절됨(시험 조건 확인)",
      n2 > n1 and len(srv.rejected) >= 1 and calls[0]["ok"] == 1 and calls[1]["kind"] == "over_limit")
check("M: 거절된 조각을 그대로 다시 보내지 않고 남은 부분만 다시 나눔",
      all(srv.seen.count(q) == 1 for q in srv.rejected) and tasks.get("replanned", 0) >= 1 and not tasks.get("cancelled"))
check("M: 40곳이 한 번씩만 고쳐지고(겹친 수정 없음) 괄호 짝 유지",
      txt.count(FIXED + "p[") == 40 and "SetWindowLong(" not in txt and "PtrPtr" not in txt
      and stats.get("_rejected_edits") == 0 and txt.count("(") == txt.count(")") and txt.count("{") == txt.count("}"))


# --- E~J. 실행 옵션(명령줄로 실행) ---
def cli(*args, env=None):
    e = dict(os.environ)
    e.pop("VUPDATE_LLM_ENDPOINT", None)
    e.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=e)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def last_run(out):
    return sorted(os.path.join(b, d) for b, ds, _f in os.walk(out) for d in ds if d == "out")[-1][:-4]


def db(run_dir, sql):
    st = V.Store(os.path.join(run_dir, "state.db"))
    try:
        return [tuple(r) for r in st.q(sql)]
    finally:
        st.close()


stat = {"cur": 0, "max": 0, "n": 0}
lock = threading.Lock()


class Count(BaseHTTPRequestHandler):
    def do_POST(self):
        import time
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        with lock:
            stat["cur"] += 1
            stat["n"] += 1
            stat["max"] = max(stat["max"], stat["cur"])
        time.sleep(0.15)
        code_part = body["messages"][-1]["content"].split("-----BEGIN CODE-----\n", 1)[-1].split("\n-----END CODE-----", 1)[0]
        blocks = ["<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (
            l, l.replace("SetWindowLong(", "SetWindowLongPtr(").replace("GWL_USERDATA", "GWLP_USERDATA")
            .replace("(LONG)", "(LONG_PTR)")) for l in code_part.split("\n") if "SetWindowLong(" in l]
        data = json.dumps({"choices": [{"message": {"content": "\n".join(blocks) or "NO_CHANGE"}}]}).encode()
        with lock:
            stat["cur"] -= 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Count)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = url_of(srv)
scfg = os.path.join(work, "server.json")
with open(scfg, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"max_parallel": 2}}, fh)

# E. --no-llm
out = os.path.join(work, "rE")
code, log = cli("run", "--compat-llm", "3", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--platform", "x64", "--server-config", scfg,
                "--llm-endpoint", url, "--no-llm", "--out", out)
rows = db(last_run(out), "SELECT status, note, COUNT(*) FROM issues WHERE tier='T3' AND llm=1 GROUP BY status, note")
check("E: --no-llm 이면 sLLM 을 한 번도 부르지 않고 대상 이슈를 수동 검토로 보냄",
      stat["n"] == 0 and code == 1 and len(rows) == 1 and rows[0][0] == "manual" and "--no-llm" in rows[0][1])

# F. --only / --exclude, G. --max-parallel 상한, J. 환경변수 주소
out = os.path.join(work, "rF")
code, log = cli("run", "--compat-llm", "3", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--platform", "x64", "--server-config", scfg,
                "--only", "Src/*,ThirdParty/*", "--exclude", "thirdparty", "--exclude", "B_util.cpp",
                "--max-parallel", "8", "--out", out, env={"VUPDATE_LLM_ENDPOINT": url})
rd = last_run(out)
scope = dict(db(rd, "SELECT path, status FROM files WHERE kind='source'"))
n_issue_out = db(rd, "SELECT COUNT(*) FROM issues WHERE path!='Src/A_main.cpp'")[0][0]
check("F: --only 와 --exclude 로 정한 범위 밖의 파일은 탐지하지 않음",
      scope.get("Src/B_util.cpp") == "범위제외" and scope.get("ThirdParty/lib.cpp") == "범위제외"
      and scope.get("Src/A_main.cpp") in ("변경됨", "부분수정") and n_issue_out == 0)
check("G: --max-parallel 8 을 줘도 서버 설정의 허용 동시 수(2)를 넘지 않음",
      1 <= stat["max"] <= 2 and "허용 동시 수 2" in log)
check("J: 환경변수 VUPDATE_LLM_ENDPOINT 의 주소로 호출함", stat["n"] >= N // 2 and code == 0 and "환경변수 VUPDATE_LLM_ENDPOINT" in log)
rep_txt = open(os.path.join(rd, "out", "report.md"), encoding="utf-8").read()
check("F: 보고서에 처리 범위와 호출자가 적힘", "처리 범위: only=src/*, thirdparty/*" in rep_txt and "호출자: " in rep_txt)

# H. 이어서 실행할 때 옵션 유지
out = os.path.join(work, "rH")
with lock:
    stat.update(cur=0, max=0, n=0)
code1, _ = cli("run", "--compat-llm", "3", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--platform", "x64", "--server-config", scfg,
               "--llm-endpoint", url, "--only", "Src/A_*", "--no-llm", "--until", "S2", "--out", out)
rd = last_run(out)
code2, log2 = cli("run", "--resume", rd, "--server-config", scfg, "--llm-endpoint", url, "--only", "Src/*")
opts = json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))["opts"]
scope = dict(db(rd, "SELECT path, status FROM files WHERE kind='source'"))
check("H: 이어서 실행해도 처음의 --no-llm, --only 가 유지됨",
      opts["no_llm"] and opts["only"] == ["src/a_*"] and stat["n"] == 0
      and scope.get("Src/B_util.cpp") == "범위제외" and "처리 범위(--only, --exclude)를 바꿀 수 없습니다" in log2
      and code2 == 1)

# I. rules 명령
code, text = cli("rules", "--from-vs", "2015", "--to-vs", "2022")
line = {l.split()[0]: l for l in text.splitlines() if l.startswith("R-")}
check("I: rules 명령이 환경값에서 켜지는 규칙과 꺼진 이유를 보여 줌",
      code == 0 and "꺼짐" in line.get("R-T2-MAKE-PAIR", "") and "원본 VS 2015 에서 이미 겪은 문제" in line.get("R-T2-MAKE-PAIR", "")
      and "켜짐" in line.get("R-T0-CRT-SECURE", "") and "규칙 68개 중 켜짐" in text)
code, text = cli("run", "--compat-llm", "3", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--platform", "arm")
check("I: 잘못된 --platform 값은 입력 오류(종료 코드 3)", code == 3 and "platform" in text)
srv.shutdown()

print("\n===== 토큰 한도와 실행 옵션 시험 결과 =====")
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, ok in checks if ok)))
sys.exit(0 if all(ok for _n, ok in checks) else 1)
