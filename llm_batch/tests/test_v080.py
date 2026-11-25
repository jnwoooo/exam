"""0.8.0 에서 더한 기능의 시험.

사용: python test_v080.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것(번호는 15_구분별_검토현황과_구현명세.md 8절의 시나리오)
  1. 구조 분석: 블록 트리, 제어문 범위, 선언 찾기
  2. 배열·표 -> 구조체: 사용처 전수 분류, 못 바꾸는 곳이 있으면 아무것도 적용하지 않음(V1), 정책으로 바꿈(V2), 큰 표(V3)
  3. 참고 영역과 작업 나누기(V5, V7, V8)
  4. 호출 예산, 대기, 이어서 실행(V11)
  5. 수용 검사 추가: 새 이름, 선언 변화, 크기 인자, 중복 선언
  6. 문장 요청의 해석(V10)
모델은 이 파일 안의 모의 서버다. 실제 sLLM 의 응답 품질은 이 시험으로 확인되지 않는다.
"""
import json, os, re, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def cli(*args):
    en = dict(os.environ)
    en.pop("VUPDATE_LLM_ENDPOINT", None)
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def out_of(root, *parts):
    hits = [os.path.join(b, parts[-1]) for b, _d, fs in os.walk(root) if parts[-1] in fs and b.replace("\\", "/").endswith("/".join(("out",) + parts[:-1]))]
    return open(hits[0], "rb").read().decode("cp949" if parts[0] == "changed" else "utf-8") if hits else ""


def run_dir(root):
    hits = [b for b, _d, fs in os.walk(root) if "state.db" in fs]
    return hits[0] if hits else ""


def write(pkg, rel, lines):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), "\r\n".join(lines + [""]).encode("cp949"))


def block(search, replace):
    return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (search, replace)


class Mock:
    """모의 sLLM. responder(요청문) 이 답의 본문을 돌려준다. ("busy",) 를 돌려주면 503 을 낸다."""

    def __init__(self, responder):
        self.calls, self.prompts, self.lock = 0, [], threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(200, {"data": [{"id": "mock"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                user = body["messages"][-1]["content"]
                with outer.lock:
                    outer.calls += 1
                    outer.prompts.append(user)
                out = responder(user)
                if isinstance(out, tuple):
                    body = json.dumps({"error": {"message": "server busy"}}).encode()
                    self.send_response(503)
                    self.send_header("Retry-After", "0.2")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                n_in = sum(len(m["content"]) for m in body["messages"]) // 3
                self._send(200, {"choices": [{"message": {"content": out}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": n_in, "completion_tokens": len(out) // 3}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def code_of(user):
    return user.split("-----BEGIN CODE-----\n", 1)[1].split("\n-----END CODE-----")[0]


def gets_targets(user):
    return re.findall(r"- \d+번째 줄 \[[^\]]+\]: gets\((\w+)\);", user)


def line_with(user, text):
    return next((l for l in code_of(user).split("\n") if text in l), None)


def fix_gets(user, how=None):
    """대상 줄의 gets(이름); 을 고친 수정 블록. how(이름, 줄) 이 있으면 그 결과를 REPLACE 로 쓴다."""
    out = []
    for n in gets_targets(user):
        line = line_with(user, "gets(%s);" % n)
        if line is None:
            continue
        rep = how(n, line) if how else None
        out.append(block(line, rep if rep is not None else line.replace("gets(%s);" % n, "fgets(%s, sizeof(%s), stdin);" % (n, n))))
    return "\n".join(out) or "NO_CHANGE"


BASE = ["run", "--from-vs", "2008", "--to-vs", "2022", "--no-compile"]

# ------------------------------------------------------------------ 1. 구조 분석
code1 = "\n".join([
    "static const int T[2][2] = { {1, 2}, {3, 4} };", "class K { public: int m; void f(); };",
    "int Work(char* out, int n)", "{", "    char line[64];", "    if (n > 0) {", "        int inner = 1;", "        n += inner;", "    } else if (n < 0)",
    "        n = 0;", "    for (int i = 0; i < n; i++) {", "        while (n) { n--; }", "    }", "    return line[0] + out[0];", "}", ""])
st1 = V.mask_preprocessor(V.mask_code(code1)[0])
blocks, ok = V.block_tree(st1)
kinds = [b["kind"] for b in blocks]
check("블록 트리: 함수, 클래스, 초기화 목록, 제어문을 가르고 표의 행은 블록으로 남기지 않음",
      ok and kinds.count("init") == 1 and kinds.count("func") == 1 and kinds.count("container") == 1 and "if" in kinds and "for" in kinds and "while" in kinds)
ifs, bad = V.ctrl_ranges(st1, kinds=("if",))
loops, _b = V.ctrl_ranges(st1, kinds=("loop",))
check("제어문 범위: else if 는 앞 if 의 범위에 넣고(1개), 중괄호 없는 본문도 범위를 정함. 반복문은 for 와 while",
      len(ifs) == 1 and bad == 0 and "n = 0;" in code1[ifs[0]["start"]:ifs[0]["end"]] and len(loops) == 2)
pos = code1.index("return line[0]")
d1, d2, d3 = V.find_decl(st1, pos, "line"), V.find_decl(st1, pos, "out"), V.find_decl(st1, pos, "inner")
check("선언 찾기: 지역 배열, 포인터 인자를 가르고, 이미 닫힌 블록 안의 선언은 범위 밖으로 봄",
      d1 and d1["kind"] == "local" and d1["is_array"] and d2 and d2["kind"] == "param" and d2["is_pointer"] and (d3 is None or d3["kind"] == "outside"))

# ------------------------------------------------------------------ 2. 배열·표 -> 구조체
pkg = os.path.join(work, "ARR8")
write(pkg, "Inc/Data.h", ["#pragma once", "enum { IDX_TEMP, IDX_COUNT, IDX_SPEED, IDX_MODE, IDX_MAX };", "extern int g_data[IDX_MAX];"])
write(pkg, "Src/Data.cpp", [
    "#include <string.h>", "#include \"../Inc/Data.h\"", "int g_data[IDX_MAX] = {0};", "struct Dev { int g_data; };", "void Send(int* p);",
    "void Init() { memset(g_data, 0, sizeof(g_data)); g_data[IDX_TEMP] = 20; }",
    "int Sum(int i) { return g_data[IDX_COUNT] + g_data[i]; }", "void Push() { Send(g_data); }",
    "int Local() { int g_data = 3; return g_data + 1; }", "int Member(Dev* d) { return d->g_data; }"])
req = os.path.join(work, "req_arr8.json")
ft = {"id": "REQ-G", "type": "array_to_struct", "target": "g_data", "struct_name": "G_DATA_T",
      "mapping": {"IDX_TEMP": "temp", "IDX_COUNT": "count", "IDX_SPEED": "speed", "IDX_MODE": "mode"}}
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [ft]}, fh)
code, text = cli(*BASE, "--package", pkg, "--request", req, "--no-llm", "--out", os.path.join(work, "a1"))
sites, manual = out_of(os.path.join(work, "a1"), "request_REQ-G_sites.md"), out_of(os.path.join(work, "a1"), "manual_review.md")
check("배열(V1): 기계적으로 못 바꾸는 사용처(변수 인덱스, 통째 사용)가 있으면 아무것도 적용하지 않음",
      not out_of(os.path.join(work, "a1"), "changed", "Src", "Data.cpp") and not out_of(os.path.join(work, "a1"), "changed", "Inc", "Data.h")
      and "적용: 하지 않음" in sites and "적용하지 않음" in manual)
check("배열(V1): 사용처를 전수 분류해 목록으로 냄(같은 이름의 멤버·지역 변수는 다른 기호로 가름)",
      "변수 인덱스" in sites and "다른 기호" in sites and re.search(r"이름이 나온 곳 (\d+)곳, 분류한 곳 \1곳", sites) is not None)
ft2 = dict(ft, variable_index="cast", bare_use="cast")
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [ft2]}, fh)
code, text = cli(*BASE, "--package", pkg, "--request", req, "--no-llm", "--out", os.path.join(work, "a2"))
src, hdr, report = out_of(os.path.join(work, "a2"), "changed", "Src", "Data.cpp"), out_of(os.path.join(work, "a2"), "changed", "Inc", "Data.h"), out_of(os.path.join(work, "a2"), "report.md")
check("배열(V2): 정책 cast 면 선언(헤더의 extern 포함)과 사용처를 함께 바꿈",
      "G_DATA_T g_data = {0};" in src and "extern G_DATA_T g_data;" in hdr and "g_data.temp = 20;" in src and "g_data.count + ((int*)&g_data)[i]" in src
      and "Send(((int*)&g_data));" in src and "memset(&g_data, 0, sizeof(g_data));" in src)
check("배열(V2): 같은 이름의 멤버와 지역 변수는 건드리지 않고, 잔여 재탐지를 통과",
      "struct Dev { int g_data; };" in src and "int g_data = 3; return g_data + 1;" in src and "d->g_data;" in src and "결과가 어긋남" not in report)
if shutil.which("g++"):
    tree = os.path.join(work, "a2_tree")
    shutil.copytree(pkg, tree)
    for rel in ("Src/Data.cpp", "Inc/Data.h"):
        V.write_bytes(os.path.join(tree, *rel.split("/")), out_of(os.path.join(work, "a2"), "changed", *rel.split("/")).encode("cp949"))
    r = subprocess.run(["g++", "-fsyntax-only", "-x", "c++", os.path.join(tree, "Src", "Data.cpp")], capture_output=True)
    check("배열(V2): 바꾼 결과가 g++ 문법 검사를 통과(g++ 가 있을 때만 확인)", r.returncode == 0)
else:
    print("[건너뜀] g++ 가 없어 바꾼 결과의 문법 검사는 하지 않음")

pkg = os.path.join(work, "BIG")
rows = ["        {%d, %d, %d}," % (i, i * 2, i * 3 + 1) for i in range(12000)]
write(pkg, "Src/Tbl.cpp", ["enum { COL_ID, COL_MIN, COL_MAX };", "int Lookup(int r)", "{", "    static const int TBL[12000][3] = {"] + rows +
      ["    };", "    return TBL[r][COL_MIN] + TBL[r][COL_MAX];", "}"])
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [{"id": "REQ-T", "type": "array_to_struct", "target": "TBL", "struct_name": "TBL_ROW_T",
                             "mapping": {"COL_ID": "id", "COL_MIN": "min", "COL_MAX": "max"}}]}, fh)
code, text = cli(*BASE, "--package", pkg, "--request", req, "--no-llm", "--out", os.path.join(work, "a3"))
src = out_of(os.path.join(work, "a3"), "changed", "Src", "Tbl.cpp")
orig = open(os.path.join(pkg, "Src", "Tbl.cpp"), "rb").read().decode("cp949")
data0 = orig[orig.index("= {") + 3:orig.index("    };")]
check("큰 표(V3): 함수 안의 2차원 표(20만 자 이상)를 행 구조체의 배열로. 선언 머리와 사용처만 바꾸고 데이터는 한 글자도 바꾸지 않음",
      len(data0) > 200000 and "static const TBL_ROW_T TBL[12000] = {" in src and data0 in src and "return TBL[r].min + TBL[r].max;" in src
      and "typedef struct TBL_ROW_T" in src)

# ------------------------------------------------------------------ 3. 참고 영역과 작업 나누기
pkg = os.path.join(work, "REF")
write(pkg, "Src/Fill.cpp", ["#include <stdio.h>", "typedef struct { char name[32]; int id; } USER_T;", "void Fill(USER_T* user)", "{", "    char line[64];",
                            "    int k = 0;"] + ["    k += %d;" % i for i in range(45)] + ["    gets(line);", "    user->id = k;", "}"])
m = Mock(fix_gets)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "f1"))
m.close()
ref = m.prompts[-1].split("[참고: 선언]")[1].split("[코드 조각]")[0] if m.prompts and "[참고: 선언]" in m.prompts[-1] else ""
check("참고 영역(V5): 조각 밖에 있는 변수 선언을 [참고: 선언]으로 붙임", "char line[64]" in ref and "char line[64];" not in code_of(m.prompts[-1]))
check("참고 영역(V5): 선언을 근거로 sizeof(배열) 수정을 수용", "fgets(line, sizeof(line), stdin);" in out_of(os.path.join(work, "f1"), "changed", "Src", "Fill.cpp"))

pkg = os.path.join(work, "MANY")
names = ["b%02d" % i for i in range(1, 21)]
write(pkg, "Src/Many.cpp", ["#include <stdio.h>", "void Big()", "{"] + ["    char %s[16];" % n for n in names] +
      [x for n in names for x in ("    gets(%s);" % n, "    puts(%s);" % n)] + ["}"])
m = Mock(fix_gets)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "m1"))
m.close()
src = out_of(os.path.join(work, "m1"), "changed", "Src", "Many.cpp")
check("작업 나누기(V7): 한 함수의 독립 수정 20곳을 8곳 이하의 작업 3건으로 나눠 보냄", m.calls == 3 and src.count("fgets(") == 20 and "gets(b" not in src.replace("fgets(b", ""))


def fail_b10(user):
    return "잘 모르겠습니다." if "b10" in gets_targets(user) else fix_gets(user)


m = Mock(fail_b10)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "m2"))
m.close()
src = out_of(os.path.join(work, "m2"), "changed", "Src", "Many.cpp")
check("작업 나누기(V8): 한 작업이 실패해도 그 작업의 8곳만 수동 검토로 가고 나머지 12곳은 고쳐짐",
      src.count("fgets(") == 12 and "gets(b10);" in src and "fgets(b01," in src and "fgets(b20," in src)

# ------------------------------------------------------------------ 4. 호출 예산, 대기, 이어서 실행
m = Mock(fix_gets)
code1, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--max-calls", "1", "--out", os.path.join(work, "b1"))
rd = run_dir(os.path.join(work, "b1"))
src1, rep1 = out_of(os.path.join(work, "b1"), "changed", "Src", "Many.cpp"), out_of(os.path.join(work, "b1"), "report.md")
check("호출 예산(V11): --max-calls 를 다 쓰면 남은 작업을 대기로 두고 종료 코드 4",
      code1 == 4 and m.calls == 1 and src1.count("fgets(") == 8 and "아직 끝나지 않은 실행" in rep1 and "수동 검토 0건" in text)
code2, text = cli("run", "--resume", rd)
src2, rep2 = out_of(os.path.join(work, "b1"), "changed", "Src", "Many.cpp"), out_of(os.path.join(work, "b1"), "report.md")
m.close()
check("이어서 실행(V11): 주소를 다시 주지 않아도 이어서 보내고, 받아 둔 응답은 다시 보내지 않음(호출 합계 3회)",
      code2 in (0, 1) and m.calls == 3 and src2.count("fgets(") == 20 and "아직 끝나지 않은 실행" not in rep2)


def busy_b10(user):
    return ("busy",) if "b10" in gets_targets(user) else fix_gets(user)


cfgb = os.path.join(work, "busy.json")
with open(cfgb, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"busy_retries": 1}}, fh)
m = Mock(busy_b10)
code1, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfgb, "--out", os.path.join(work, "b2"))
m.close()
rd = run_dir(os.path.join(work, "b2"))
src1, manual1 = out_of(os.path.join(work, "b2"), "changed", "Src", "Many.cpp"), out_of(os.path.join(work, "b2"), "manual_review.md")
check("과부하: 서버가 끝까지 바쁘다고 답한 작업은 수동 검토가 아니라 대기로 둠(종료 코드 4)",
      code1 == 4 and src1.count("fgets(") == 12 and "gets(b10);" in src1 and "b10" not in manual1)
m = Mock(fix_gets)
code2, text = cli("run", "--resume", rd, "--llm-endpoint", m.url)
m.close()
check("과부하 뒤 이어서 실행: 대기였던 작업만 새로 보냄(1회)", m.calls == 1 and out_of(os.path.join(work, "b2"), "changed", "Src", "Many.cpp").count("fgets(") == 20)

pkgw = os.path.join(work, "WIDE")
Lw = ["#include <windows.h>", "", "void WideFn(HWND h, void** p)", "{"]
for k in range(40):
    Lw += ["    SetWindowLong(h, GWL_USERDATA, (LONG)p[%d]);" % k, "    Sleep(%d);" % k, "    // 채움 주석 %d %s" % (k, "가나다라 " * 30)]
write(pkgw, "Src/Wide.cpp", Lw + ["}"])
cfgw = os.path.join(work, "wide.json")
with open(cfgw, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"max_total_tokens": 8000, "max_output_tokens": 800, "calibrate_first": False}, "limits": {"task_max_targets": 0, "task_max_lines": 0}}, fh)


def fix_swl(user):
    out = []
    for x in re.finditer(r"- \d+번째 줄 \[[^\]]+\]: (SetWindowLong\(.+)", user):
        line = line_with(user, x.group(1).strip())
        if line:
            out.append(block(line, line.replace("SetWindowLong(h, GWL_USERDATA, (LONG)", "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)")))
    return "\n".join(out) or "NO_CHANGE"


m = Mock(fix_swl)
code0, text0 = cli(*BASE, "--platform", "x64", "--package", pkgw, "--llm-endpoint", m.url, "--server-config", cfgw, "--out", os.path.join(work, "w0"))
n_parts = m.calls
m.close()
src0 = out_of(os.path.join(work, "w0"), "changed", "Src", "Wide.cpp")
m = Mock(fix_swl)
code1, text1 = cli(*BASE, "--platform", "x64", "--package", pkgw, "--llm-endpoint", m.url, "--server-config", cfgw, "--max-calls", "1", "--out", os.path.join(work, "w1"))
src1 = out_of(os.path.join(work, "w1"), "changed", "Src", "Wide.cpp")
code2, text2 = cli("run", "--resume", run_dir(os.path.join(work, "w1")), "--server-config", cfgw)
src2 = out_of(os.path.join(work, "w1"), "changed", "Src", "Wide.cpp")
m.close()
check("분할 순차와 대기: 한 구간의 뒤 조각이 대기 중이면 앞 조각의 수정도 결과에 넣지 않음(시험 조건: 조각 2개 이상)",
      n_parts >= 2 and src0.count("SetWindowLongPtr(") == 40 and code1 == 4 and "SetWindowLongPtr(" not in src1)
check("분할 순차와 대기: 이어서 실행하면 끊지 않은 실행과 같은 결과가 되고 호출 합계도 같음",
      src2 == src0 and m.calls == n_parts and code2 != 4)

pkg = os.path.join(work, "ARR8")
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [dict(ft, variable_index="llm", bare_use="cast")]}, fh)


def arr_responder(user):
    line = line_with(user, "g_data[i]")
    return block(line, line.replace("g_data[i]", "((int*)&g_data)[i]")) if line else "NO_CHANGE"


m = Mock(lambda user: ("busy",))
code1, text = cli(*BASE, "--package", pkg, "--request", req, "--llm-endpoint", m.url, "--server-config", cfgb, "--out", os.path.join(work, "h1"))
m.close()
rd = run_dir(os.path.join(work, "h1"))
check("요청의 원자성과 대기: sLLM 에 맡긴 사용처가 대기 중이면 그 요청의 수정을 결과에 넣지 않음(반쯤 바뀐 결과를 내지 않음)",
      code1 == 4 and not out_of(os.path.join(work, "h1"), "changed", "Src", "Data.cpp") and "대기 중" in out_of(os.path.join(work, "h1"), "report.md"))
m = Mock(arr_responder)
code2, text = cli("run", "--resume", rd, "--llm-endpoint", m.url)
m.close()
src = out_of(os.path.join(work, "h1"), "changed", "Src", "Data.cpp")
check("요청의 원자성과 대기: 이어서 실행해 사용처가 고쳐지면 요청 전체를 적용",
      code2 != 4 and "G_DATA_T g_data = {0};" in src and "((int*)&g_data)[i]" in src and "g_data.temp = 20;" in src)
m = Mock(lambda user: "잘 모르겠습니다.")
code3, text = cli(*BASE, "--package", pkg, "--request", req, "--llm-endpoint", m.url, "--out", os.path.join(work, "h2"))
m.close()
check("요청의 원자성: sLLM 에 맡긴 사용처가 실패하면 그 요청의 수정을 전부 되돌림(on_unresolved 기본값 stop)",
      not out_of(os.path.join(work, "h2"), "changed", "Src", "Data.cpp") and "적용하지 않음" in out_of(os.path.join(work, "h2"), "report.md"))

# ------------------------------------------------------------------ 5. 수용 검사 추가
pkg = os.path.join(work, "CHK")
write(pkg, "Src/Read.cpp", [
    "#include <stdio.h>", "#include <string.h>", "",
    "void ReadPtr(char* out)", "{", "    gets(out);", "}", "",
    "void ReadOk()", "{", "    char line[64];", "    gets(line);", "    puts(line);", "}", "",
    "void ReadNew()", "{", "    char name[32];", "    gets(name);", "    puts(name);", "}", "",
    "void ReadDecl()", "{", "    char buf[32];", "    int n = 0;", "    gets(buf);", "    n = (int)strlen(buf);", "}", "",
    "void ReadDrop()", "{", "    char data[16];", "    gets(data);", "    puts(data);", "}", "",
    "void ReadTwice()", "{", "    char a1[16];", "    char a2[16];", "    gets(a1);", "    puts(a1);", "    gets(a2);", "    puts(a2);", "}"])


def chk_responder(user):
    def how(n, line):
        ind = line[:len(line) - len(line.lstrip())]
        if n == "name":
            return ind + "SafeReadLine(name, sizeof(name));"
        if n == "buf":
            return ind + "int n = 5;\n" + ind + "fgets(buf, sizeof(buf), stdin);"
        if n in ("a1", "a2"):
            return ind + "size_t got = 0;\n" + ind + "fgets(%s, sizeof(%s), stdin);" % (n, n)
        return None
    if "gets(data);" in "\n".join("gets(%s);" % n for n in gets_targets(user)) and gets_targets(user) == ["data"]:
        decl = line_with(user, "char data[16];")
        use = line_with(user, "gets(data);")
        if decl and use:
            return block(decl + "\n" + use, use.replace("gets(data);", "fgets(data, 16, stdin);"))
    return fix_gets(user, how)


cfg1 = os.path.join(work, "one_target.json")
with open(cfg1, "w", encoding="utf-8") as fh:
    json.dump({"limits": {"task_max_targets": 1, "cluster_gap_lines": 0}}, fh)
m = Mock(chk_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfg1, "--out", os.path.join(work, "c1"))
m.close()
src, manual, report = out_of(os.path.join(work, "c1"), "changed", "Src", "Read.cpp"), out_of(os.path.join(work, "c1"), "manual_review.md"), out_of(os.path.join(work, "c1"), "report.md")
check("크기 인자: 포인터 인자에 sizeof 를 쓴 수정은 기각하고 수동 검토로(원문 유지)", "gets(out);" in src and "포인터" in manual)
check("정상 수정은 수용하고 변경 범위를 기록", "fgets(line, sizeof(line), stdin);" in src and "범위① 함수 안" in report)
check("새 이름: 기본은 기각하지 않고 검토 메모를 남김", "SafeReadLine(name, sizeof(name));" in src and "조각과 패키지에 없던 이름: SafeReadLine" in report)
check("선언 변화: 이미 있는 이름을 다시 선언한 수정은 기각", "gets(buf);" in src and "int n = 5;" not in src and "이미 선언돼" in manual)
check("선언 변화: 선언을 없앴는데 그 이름을 쓰는 수정은 기각", "char data[16];" in src and "gets(data);" in src and "선언을 없앤 이름 data" in manual)
check("중복 선언: 같은 블록에 같은 이름을 선언한 두 수정 가운데 뒤 것을 뺌",
      src.count("size_t got = 0;") == 1 and "fgets(a1, sizeof(a1), stdin);" in src and "gets(a2);" in src and "중복 선언" in manual)
cfg2 = os.path.join(work, "reject_new.json")
with open(cfg2, "w", encoding="utf-8") as fh:
    json.dump({"limits": {"task_max_targets": 1, "cluster_gap_lines": 0, "new_name_check": "reject"}}, fh)
m = Mock(chk_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfg2, "--only", "Src/Read.cpp", "--out", os.path.join(work, "c2"))
m.close()
src2, manual2 = out_of(os.path.join(work, "c2"), "changed", "Src", "Read.cpp"), out_of(os.path.join(work, "c2"), "manual_review.md")
check("새 이름: new_name_check 가 reject 이면 기각하고 수동 검토로", "SafeReadLine" not in src2 and "gets(name);" in src2 and "어디에도 없는 이름" in manual2)

# ------------------------------------------------------------------ 6. 위치 조건, 찾기 전용, 문장 요청
pkg = os.path.join(work, "SAY")
write(pkg, "Src/Svc.cpp", [
    "#include <stdio.h>", "void WriteLog(const char* s);", "void TraceMsg(const char* s);", "void ShowDialog(int id);", "void Catalog(int n);", "",
    "int Work(int a)", "{", "    WriteLog(\"start\");", "    if (a > 3) {", "        WriteLog(\"big\");", "        TraceMsg(\"t1\");",
    "    } else {", "        ShowDialog(1);", "    }", "    for (int i = 0; i < a; i++) {", "        TraceMsg(\"loop\");", "        if (i == 2)",
    "            WriteLog(\"two\");", "    }", "    Catalog(a);", "    printf(\"log: %d\\n\", a);", "    return a;", "}"])
state = {"interpret": 0}


def say_responder(user):
    if "[후보]" in user:
        nums = [x.group(1) for x in re.finditer(r"^(\d+)\. (\w+) \(", user, re.M) if x.group(2) in ("WriteLog", "TraceMsg")]
        return "KEEP: %s, 99" % ", ".join(nums)
    if "[요청 문장]" in user:
        state["interpret"] += 1
        if state["interpret"] == 1:
            return "네, 로그 함수를 찾아 보겠습니다."
        return "```json\n{\"action\": \"find\", \"where\": \"if\", \"words\": [\"log\", \"trace\", \"ab\"]}\n```"
    out = []
    for x in re.finditer(r"- \d+번째 줄 \[[^\]]+\]: (.+)", user):
        line = line_with(user, x.group(1).strip())
        if line and "WriteLog(" in line:
            out.append(block(line, line.replace("WriteLog(", "WriteLog2(")))
    return "\n".join(out) or "NO_CHANGE"


m = Mock(say_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", "[if] WriteLog: WriteLog2 로 바꾼다", "--out", os.path.join(work, "s1"))
m.close()
src = out_of(os.path.join(work, "s1"), "changed", "Src", "Svc.cpp")
check("위치 조건 [if]: if 문 안의 호출만 대상이 됨(밖의 호출과 선언은 그대로)",
      'WriteLog2("big");' in src and 'WriteLog2("two");' in src and 'WriteLog("start");' in src and "void WriteLog(const char* s);" in src)
code, text = cli(*BASE, "--package", pkg, "--no-llm", "--ask", "[if, find] WriteLog, TraceMsg: 로그 호출 목록",
                 "--ask", "[string, find] /log/: 문자열 속의 log", "--out", os.path.join(work, "s2"))
f1, f2 = out_of(os.path.join(work, "s2"), "found_ASK-1.md"), out_of(os.path.join(work, "s2"), "found_ASK-2.md")
check("찾기 전용 [find]: 고치지 않고 목록만 냄(if 안의 3곳), 수동 검토도 만들지 않음(종료 코드 0)",
      code == 0 and "찾은 곳: 3곳" in f1 and '"big"' in f1 and '"t1"' in f1 and '"two"' in f1 and '"start"' not in f1 and '"loop"' not in f1
      and not out_of(os.path.join(work, "s2"), "changed", "Src", "Svc.cpp"))
check("위치 조건 [string]: 문자열 안에서만 찾음", "찾은 곳: 1곳" in f2 and "printf" in f2)
code, text = cli(*BASE, "--package", pkg, "--no-llm", "--say", "if 문 안의 WriteLog 호출을 찾아 줘", "--out", os.path.join(work, "s3"))
res = json.loads(out_of(os.path.join(work, "s3"), "request_resolved.json") or "{}")
a0 = (res.get("asks") or [{}])[0]
check("문장 요청: 문장에 패키지의 이름이 있으면 모델 없이 대상, 위치, 동작을 정함",
      a0.get("targets") == ["WriteLog"] and a0.get("where") == "if" and a0.get("action") == "find" and a0.get("from") == "sentence"
      and "찾은 곳: 2곳" in out_of(os.path.join(work, "s3"), "found_SAY-1.md"))
m = Mock(say_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--say", "조건문 안에서 로그를 남기는 함수를 찾아 줘", "--out", os.path.join(work, "s4"))
m.close()
res = json.loads(out_of(os.path.join(work, "s4"), "request_resolved.json") or "{}")
a0 = (res.get("asks") or [{}])[0]
check("문장 요청: 이름이 없으면 모델에게 낱말을 받아 낱말 단위로 이름을 고르고 번호로 추림(Dialog, Catalog 는 후보가 아님)",
      a0.get("targets") == ["WriteLog", "TraceMsg"] and a0.get("from") == "model" and a0.get("words") == ["log", "trace"]
      and "ShowDialog" not in json.dumps(res) and "Catalog" not in json.dumps(res))
check("문장 요청: 형식이 틀린 답은 한 번 다시 묻고(해석 2회, 추리기 1회), 범위 밖 번호는 버림", m.calls == 3 and len(a0.get("targets") or []) == 2)
check("문장 요청: 모델에 코드를 보내지 않음(해석 요청에는 문장만)", all("BEGIN CODE" not in p for p in m.prompts) and "WriteLog" not in m.prompts[0])
check("문장 요청: 찾은 곳 목록", "찾은 곳: 3곳" in out_of(os.path.join(work, "s4"), "found_SAY-1.md"))
resolved = os.path.join(run_dir(os.path.join(work, "s4")), "out", "request_resolved.json")
code, text = cli(*BASE, "--package", pkg, "--no-llm", "--request", resolved, "--out", os.path.join(work, "s5"))
check("해석 결과 파일을 --request 로 주면 모델 없이 같은 대상을 씀", "찾은 곳: 3곳" in out_of(os.path.join(work, "s5"), "found_ASK-1.md"))
code, text = cli(*BASE, "--package", pkg, "--no-llm", "--say", "로그를 남기는 함수를 찾아 줘", "--out", os.path.join(work, "s6"))
check("문장 요청: 모델을 쓸 수 없고 이름도 없으면 수동 검토로 남김(종료 코드 1)",
      code == 1 and "문장 요청을 해석하지 못함" in out_of(os.path.join(work, "s6"), "manual_review.md"))
m = Mock(say_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--say", "WriteLog를 WriteLog2로 바꿔줘", "--out", os.path.join(work, "s7"))
m.close()
src = out_of(os.path.join(work, "s7"), "changed", "Src", "Svc.cpp")
check("문장 요청(수정): '로' 앞의 이름은 바꾼 결과로 보고 대상에서 빼며, 대상이 쓰인 곳을 모두 sLLM 에 맡김",
      src.count("WriteLog2(") == 4 and "WriteLog(" not in src)


n_ok = sum(1 for _n, ok in checks if ok)
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), n_ok))
sys.exit(0 if n_ok == len(checks) else 1)
