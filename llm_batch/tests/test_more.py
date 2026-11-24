"""추가 시험: 큰 배열 근처의 sLLM 수정(생략 표식), 재요청, 정의 보강, 처리 시간."""
import argparse, json, os, shutil, sys, threading, time
sys.path.insert(0, sys.argv[1])
import vupdate as V
from http.server import BaseHTTPRequestHandler, HTTPServer

work = sys.argv[2]
shutil.rmtree(work, ignore_errors=True)
pkg = os.path.join(work, "BIG_PKG")
blob = ", ".join("0x%02X" % (i % 251) for i in range(270000))          # 약 162만 자
src = "\n".join([
    "// 큰 상수 배열을 가진 함수 (CP949)",
    "#include <windows.h>",
    "LRESULT CALLBACK HelperProc(HWND h, UINT m, WPARAM w, LPARAM l) { return 0; }",
    "void Attach(HWND hWnd, void* self)",
    "{",
    "    static const unsigned char blob[] = { %s };" % blob,
    "    SetWindowLong(hWnd, GWL_USERDATA, (LONG)self);",
    "    (void)blob;",
    "}", ""])
V.write_bytes(os.path.join(pkg, "Src", "Big.cpp"), src.replace("\n", "\r\n").encode("cp949"))
prof = os.path.join(work, "p.json")
V.write_text(prof, json.dumps({"from": {"vs": 2008}, "to": {"vs": 2022, "platform": "x64"}}))

state = {"n": 0, "prompts": []}
GOOD = ("<<<<<<< SEARCH\n    SetWindowLong(hWnd, GWL_USERDATA, (LONG)self);\n=======\n"
        "    SetWindowLongPtr(hWnd, GWLP_USERDATA, (LONG_PTR)self);\n>>>>>>> REPLACE")
REPLIES = ["SetWindowLongPtr 로 바꾸면 됩니다.",                       # 1: 형식 오류
           "NEED_CONTEXT: HelperProc",                                  # 2: 정의 요청
           GOOD.replace("(LONG)self);\n=", "(LONG) self);\n="),        # 3: 원문과 다른 SEARCH
           GOOD]                                                        # 4: 정상

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        state["prompts"].append(body["messages"][-1]["content"])
        content = REPLIES[min(state["n"], len(REPLIES) - 1)]
        state["n"] += 1
        data = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ns = argparse.Namespace(package=pkg, profile=prof, request=None, from_vs=None, to_vs=None, cpp_standard=None,
                        server_config=None, rules=None, out=os.path.join(work, "runs"), dry_run=False, resume=None,
                        until=None, max_parallel=2, no_compile=False,
                        llm_endpoint="http://127.0.0.1:%d/v1" % srv.server_address[1])
t0 = time.time()
ctx = V._make_ctx(ns)
code = V.run_pipeline(ctx, [s for s, _ in V.STAGES])
ctx.store.close(); srv.shutdown(); V._SINK.close()
el = time.time() - t0
out = os.path.join(ctx.run_dir, "out", "changed", "Src", "Big.cpp")
raw = V.read_bytes(out) if os.path.isfile(out) else b""
P = state["prompts"]
print("\n===== 추가 시험 결과 =====")
print("원본 크기 %d자, 전체 소요 %.1f초, 종료 코드 %d, sLLM 호출 %d회" % (len(src), el, code, state["n"]))
print("프롬프트 길이(자):", [len(p) for p in P])
checks = [
    ("프롬프트에 생략 표식이 있고 배열 본문은 없음", all(V.ELIDE_MARK in p and "0x10, 0x11, 0x12" not in p for p in P)),
    ("프롬프트가 5천 자 미만", all(len(p) < 5000 for p in P)),
    ("2차 프롬프트에 형식 오류 사유 전달", len(P) > 1 and "출력 형식이 맞지 않는다" in P[1]),
    ("3차 프롬프트에 요청한 정의(HelperProc) 포함", len(P) > 2 and "[추가 정의]" in P[2] and "HelperProc" in P[2].split("[추가 정의]")[1].split("[코드 조각]")[0]),
    ("4차 프롬프트에 SEARCH 불일치 사유 전달", len(P) > 3 and "원문과 일치하지 않음" in P[3]),
    ("최종 수정 반영", b"SetWindowLongPtr(hWnd, GWLP_USERDATA, (LONG_PTR)self);" in raw),
    ("큰 배열이 바이트 단위로 동일", blob.encode("ascii") in raw),
    ("CRLF 유지", b"\n" not in raw.replace(b"\r\n", b"")),
]
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
sys.exit(0 if all(ok for _n, ok in checks) else 1)
