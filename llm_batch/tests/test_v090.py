"""0.9.0 의 시험. 0.8.0 의 시험 가운데 남은 기능(구조 분석, 배열 변환, 참고 영역, 작업 나누기, 호출 예산, 수용 검사)도 여기에 있다.

사용: python test_v090.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 구조 분석: 블록 트리, 제어문 범위, 선언 찾기
  2. 배열·표 -> 구조체: 사용처 전수 분류, 기계적으로 못 바꾸는 곳은 sLLM 에, 실패하면 요청 전체를 되돌림, 큰 표
  3. 참고 영역과 작업 나누기
  4. 호출 예산, 대기, 이어서 실행
  5. 수용 검사: 새 이름, 선언 변화, 크기 인자, 중복 선언
  6. 요청문(--ask): 원문을 sLLM 이 의도별로 나누고, 도구가 대상을 찾아 의도별로 수정 요청을 보냄
  7. 환경 호환성 항목의 처리 깊이(--compat-llm 1|2|3. 기본 1)
  8. OS, DB 의 원본/대상 옵션(--from-os, --to-os, --from-db, --to-db)
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
    for n in dict.fromkeys(gets_targets(user)):
        line = line_with(user, "gets(%s);" % n)
        if line is None:
            continue
        rep = how(n, line) if how else None
        out.append(block(line, rep if rep is not None else line.replace("gets(%s);" % n, "fgets(%s, sizeof(%s), stdin);" % (n, n))))
    return "\n".join(out) or "NO_CHANGE"


BASE = ["run", "--from-vs", "2008", "--to-vs", "2022", "--no-compile"]
BASEC = BASE + ["--compat-llm", "3"]                   # 규칙의 sLLM 대상(gets 등)을 보내야 하는 시험에 쓴다

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
sites, manual, report = out_of(os.path.join(work, "a1"), "request_REQ-G_sites.md"), out_of(os.path.join(work, "a1"), "manual_review.md"), out_of(os.path.join(work, "a1"), "report.md")
check("배열: sLLM 을 쓸 수 없으면 기계적으로 못 바꾸는 사용처가 남으므로 요청 전체를 적용하지 않음",
      not out_of(os.path.join(work, "a1"), "changed", "Src", "Data.cpp") and not out_of(os.path.join(work, "a1"), "changed", "Inc", "Data.h")
      and "적용하지 않음" in report)
check("배열: 사용처를 전수 분류해 목록으로 냄(같은 이름의 멤버·지역 변수는 다른 기호로 가름)",
      "변수 인덱스" in sites and "다른 기호" in sites and re.search(r"이름이 나온 곳 (\d+)곳, 분류한 곳 \1곳", sites) is not None)


def arr_responder(user):
    """배열 요청에서 맡은 사용처: 변수 인덱스는 요소 포인터로, 통째로 넘기는 곳은 주소로 바꾼다."""
    out = []
    for x in re.finditer(r"- \d+번째 줄 \[[^\]]+\]: (.+)", user):
        line = line_with(user, x.group(1).strip())
        if not line:
            continue
        new = line.replace("g_data[i]", "((int*)&g_data)[i]").replace("Send(g_data)", "Send((int*)&g_data)")
        if new != line and not any(line in b for b in out):
            out.append(block(line, new))
    return "\n".join(out) or "NO_CHANGE"


m = Mock(arr_responder)
code, text = cli(*BASE, "--package", pkg, "--request", req, "--llm-endpoint", m.url, "--out", os.path.join(work, "a2"))
m.close()
src, hdr, report = out_of(os.path.join(work, "a2"), "changed", "Src", "Data.cpp"), out_of(os.path.join(work, "a2"), "changed", "Inc", "Data.h"), out_of(os.path.join(work, "a2"), "report.md")
check("배열: 선언(헤더의 extern 포함)과 상수 인덱스는 규칙으로, 변수 인덱스와 통째 사용은 sLLM 이 고침",
      "G_DATA_T g_data = {0};" in src and "extern G_DATA_T g_data;" in hdr and "g_data.temp = 20;" in src and "g_data.count + ((int*)&g_data)[i]" in src
      and "Send((int*)&g_data);" in src and "memset(&g_data, 0, sizeof(g_data));" in src and m.calls >= 1)
check("배열: sLLM 에 보낸 지침에 대응표와 바꾸는 꼴이 들어감", any("g_data[IDX_COUNT] -> g_data.count" in p and "((int*)&g_data)[인덱스]" in p for p in m.prompts))
check("배열: 같은 이름의 멤버와 지역 변수는 건드리지 않고, 잔여 재탐지를 통과",
      "struct Dev { int g_data; };" in src and "int g_data = 3; return g_data + 1;" in src and "d->g_data;" in src and "결과가 어긋남" not in report)
if shutil.which("g++"):
    tree = os.path.join(work, "a2_tree")
    shutil.copytree(pkg, tree)
    for rel in ("Src/Data.cpp", "Inc/Data.h"):
        V.write_bytes(os.path.join(tree, *rel.split("/")), out_of(os.path.join(work, "a2"), "changed", *rel.split("/")).encode("cp949"))
    r = subprocess.run(["g++", "-fsyntax-only", "-x", "c++", os.path.join(tree, "Src", "Data.cpp")], capture_output=True)
    check("배열: 바꾼 결과가 g++ 문법 검사를 통과(g++ 가 있을 때만 확인)", r.returncode == 0)
else:
    print("[건너뜀] g++ 가 없어 바꾼 결과의 문법 검사는 하지 않음")
m = Mock(lambda user: "잘 모르겠습니다.")
code, text = cli(*BASE, "--package", pkg, "--request", req, "--llm-endpoint", m.url, "--out", os.path.join(work, "a2f"))
m.close()
check("배열(원자성): sLLM 에 맡긴 사용처가 실패하면 그 요청의 수정을 전부 되돌림",
      not out_of(os.path.join(work, "a2f"), "changed", "Src", "Data.cpp") and "적용하지 않음" in out_of(os.path.join(work, "a2f"), "report.md"))

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
check("큰 표: 함수 안의 2차원 표(20만 자 이상)를 행 구조체의 배열로. 선언 머리와 사용처만 바꾸고 데이터는 한 글자도 바꾸지 않음(sLLM 호출 없음)",
      len(data0) > 200000 and "static const TBL_ROW_T TBL[12000] = {" in src and data0 in src and "return TBL[r].min + TBL[r].max;" in src
      and "typedef struct TBL_ROW_T" in src)

# ------------------------------------------------------------------ 3. 참고 영역과 작업 나누기
pkg = os.path.join(work, "REF")
write(pkg, "Src/Fill.cpp", ["#include <stdio.h>", "typedef struct { char name[32]; int id; } USER_T;", "void Fill(USER_T* user)", "{", "    char line[64];",
                            "    int k = 0;"] + ["    k += %d;" % i for i in range(45)] + ["    gets(line);", "    user->id = k;", "}"])
m = Mock(fix_gets)
code, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "f1"))
m.close()
ref = m.prompts[-1].split("[참고: 선언]")[1].split("[코드 조각]")[0] if m.prompts and "[참고: 선언]" in m.prompts[-1] else ""
check("참고 영역(V5): 조각 밖에 있는 변수 선언을 [참고: 선언]으로 붙임", "char line[64]" in ref and "char line[64];" not in code_of(m.prompts[-1]))
check("참고 영역(V5): 선언을 근거로 sizeof(배열) 수정을 수용", "fgets(line, sizeof(line), stdin);" in out_of(os.path.join(work, "f1"), "changed", "Src", "Fill.cpp"))

pkg = os.path.join(work, "MANY")
names = ["b%02d" % i for i in range(1, 21)]
write(pkg, "Src/Many.cpp", ["#include <stdio.h>", "void Big()", "{"] + ["    char %s[16];" % n for n in names] +
      [x for n in names for x in ("    gets(%s);" % n, "    puts(%s);" % n)] + ["}"])
m = Mock(fix_gets)
code, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "m1"))
m.close()
src = out_of(os.path.join(work, "m1"), "changed", "Src", "Many.cpp")
check("작업 나누기(V7): 한 함수의 독립 수정 20곳을 8곳 이하의 작업 3건으로 나눠 보냄", m.calls == 3 and src.count("fgets(") == 20 and "gets(b" not in src.replace("fgets(b", ""))


def fail_b10(user):
    return "잘 모르겠습니다." if "b10" in gets_targets(user) else fix_gets(user)


m = Mock(fail_b10)
code, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "m2"))
m.close()
src = out_of(os.path.join(work, "m2"), "changed", "Src", "Many.cpp")
check("작업 나누기(V8): 한 작업이 실패해도 그 작업의 8곳만 수동 검토로 가고 나머지 12곳은 고쳐짐",
      src.count("fgets(") == 12 and "gets(b10);" in src and "fgets(b01," in src and "fgets(b20," in src)

# ------------------------------------------------------------------ 4. 호출 예산, 대기, 이어서 실행
m = Mock(fix_gets)
code1, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--max-calls", "1", "--out", os.path.join(work, "b1"))
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
code1, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfgb, "--out", os.path.join(work, "b2"))
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
code0, text0 = cli(*BASEC, "--platform", "x64", "--package", pkgw, "--llm-endpoint", m.url, "--server-config", cfgw, "--out", os.path.join(work, "w0"))
n_parts = m.calls
m.close()
src0 = out_of(os.path.join(work, "w0"), "changed", "Src", "Wide.cpp")
m = Mock(fix_swl)
code1, text1 = cli(*BASEC, "--platform", "x64", "--package", pkgw, "--llm-endpoint", m.url, "--server-config", cfgw, "--max-calls", "1", "--out", os.path.join(work, "w1"))
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
    json.dump({"features": [ft]}, fh)
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
      code2 != 4 and "G_DATA_T g_data = {0};" in src and "((int*)&g_data)[i]" in src and "Send((int*)&g_data);" in src and "g_data.temp = 20;" in src)
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
code, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfg1, "--out", os.path.join(work, "c1"))
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
code, text = cli(*BASEC, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfg2, "--only", "Src/Read.cpp", "--out", os.path.join(work, "c2"))
m.close()
src2, manual2 = out_of(os.path.join(work, "c2"), "changed", "Src", "Read.cpp"), out_of(os.path.join(work, "c2"), "manual_review.md")
check("새 이름: new_name_check 가 reject 이면 기각하고 수동 검토로", "SafeReadLine" not in src2 and "gets(name);" in src2 and "어디에도 없는 이름" in manual2)

# ------------------------------------------------------------------ 6. 요청문(--ask)의 의미 분석
pkg = os.path.join(work, "SAY")
write(pkg, "Src/Svc.cpp", [
    "#include <stdio.h>", "void WriteLog(const char* s);", "void TraceMsg(const char* s);", "void ShowDialog(int id);", "void Catalog(int n);", "",
    "int Work(int a)", "{", "    WriteLog(\"start\");", "    if (a > 3) {", "        WriteLog(\"big\");", "        TraceMsg(\"t1\");",
    "    } else {", "        ShowDialog(1);", "    }", "    for (int i = 0; i < a; i++) {", "        TraceMsg(\"loop\");", "        if (i == 2)",
    "            WriteLog(\"two\");", "    }", "    Catalog(a);", "    printf(\"log: %d\\n\", a);", "    return a;", "}"])


def is_analysis(user):
    return "[요청 원문]" in user and "BEGIN CODE" not in user


def intent(typ, ins, names=(), words=(), construct="any", files=()):
    return {"type": typ, "instruction": ins, "names": list(names), "words": list(words), "construct": construct, "files": list(files)}


def edit_targets(user, fn):
    """[수정 대상 줄]마다 fn(줄) 로 바꾼 수정 블록을 낸다. 바뀌지 않는 줄은 건너뛴다."""
    out = []
    for x in re.finditer(r"- \d+번째 줄 \[[^\]]+\]: (.+)", user):
        line = line_with(user, x.group(1).strip())
        if line and fn(line) != line and not any(line in b for b in out):
            out.append(block(line, fn(line)))
    return "\n".join(out) or "NO_CHANGE"


def ask_mock(intents, edit=None, keep=(), bad_first=False):
    """요청문 시험용 모의 모델. 분석 요청에는 intents 를, 후보 확인에는 keep 에 든 이름의 번호를, 수정 요청에는 edit(줄) 의 결과를 낸다."""
    state = {"n": 0}

    def responder(user):
        if "[후보]" in user:
            nums = [int(x.group(1)) for x in re.finditer(r"(?m)^(\d+)\. (\w+) \(", user) if x.group(2) in keep]
            return json.dumps({"keep": nums + [99]})
        if is_analysis(user):
            state["n"] += 1
            if bad_first and state["n"] == 1:
                return "네, 요청을 확인했습니다. 곧 처리하겠습니다."
            return "```json\n%s\n```" % json.dumps({"intents": intents}, ensure_ascii=False)
        return edit_targets(user, edit) if edit else "NO_CHANGE"
    return Mock(responder)


ASK1 = "if 문 안의 WriteLog 호출을 WriteLog2 로 바꿔 줘"
m = ask_mock([intent("modify", "if 문 안의 WriteLog 호출을 WriteLog2 로 바꾼다", names=["WriteLog", "WriteLog2"], construct="if")],
             edit=lambda l: l.replace("WriteLog(", "WriteLog2("))
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", ASK1, "--out", os.path.join(work, "s1"))
m.close()
src = out_of(os.path.join(work, "s1"), "changed", "Src", "Svc.cpp")
plan = json.loads(out_of(os.path.join(work, "s1"), "ask_plan.json") or "{}")
i0 = ((plan.get("asks") or [{}])[0].get("intents") or [{}])[0]
a_prompt = next((p for p in m.prompts if is_analysis(p)), "")
e_prompt = next((p for p in m.prompts if "BEGIN CODE" in p), "")
check("요청문: 원문을 그대로 분석 호출에 보내고, 원문의 이름 가운데 코드에 있는 것만 알려 줌(코드 조각은 보내지 않음)",
      ASK1 in a_prompt and "- WriteLog (함수 호출" in a_prompt and "- WriteLog2" not in a_prompt and "void Catalog" not in a_prompt)
check("요청문: 모델이 낸 이름 가운데 코드에 없는 것은 버리고, 구문 조건(if 문 안)에 맞는 줄만 대상으로 삼음",
      i0.get("names") == ["WriteLog"] and i0.get("names_dropped") == ["WriteLog2"] and i0.get("sites") == 2
      and 'WriteLog2("big");' in src and 'WriteLog2("two");' in src and 'WriteLog("start");' in src and "void WriteLog(const char* s);" in src)
check("요청문: 수정 요청에 그 의도의 요청문과 원문이 함께 들어감(분석 1회 + 수정 1회)",
      "사용자 요청: if 문 안의 WriteLog 호출을 WriteLog2 로 바꾼다" in e_prompt and "요청 원문: " + ASK1 in e_prompt and m.calls == 2)

ASK2 = "if 문 안에서 로그를 남기는 함수를 찾아 주고, Catalog 호출의 인자에 1을 더해 줘. 주석은 바꾸지 마."
m = ask_mock([intent("find", "if 문 안에서 로그를 남기는 함수 호출을 찾는다", words=["log", "trace", "ab"], construct="if"),
              intent("modify", "Catalog 호출의 인자에 1을 더한다", names=["Catalog"], construct="call"),
              intent("constraint", "주석은 바꾸지 않는다")],
             edit=lambda l: l.replace("Catalog(a)", "Catalog(a + 1)"))
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", ASK2, "--out", os.path.join(work, "s2"))
m.close()
src, found = out_of(os.path.join(work, "s2"), "changed", "Src", "Svc.cpp"), out_of(os.path.join(work, "s2"), "found_ASK-1-1.md")
plan = json.loads(out_of(os.path.join(work, "s2"), "ask_plan.json") or "{}")
its = (plan.get("asks") or [{}])[0].get("intents") or []
e_prompt = next((p for p in m.prompts if "BEGIN CODE" in p), "")
check("요청문: 한 요청에 섞인 일을 의도별로 나눠 처리(찾기, 수정, 조건)",
      [i.get("type") for i in its] == ["find", "modify", "constraint"] and [i.get("id") for i in its] == ["ASK-1-1", "ASK-1-2", "ASK-1-3"])
check("요청문(찾기): 낱말이 낱말 단위로 든 호출 이름을 고르고(Dialog, Catalog 는 아님) 고치지 않고 목록만 냄",
      its and its[0].get("targets") == ["WriteLog", "TraceMsg"] and its[0].get("words") == ["log", "trace"]
      and "찾은 곳: 3곳" in found and '"big"' in found and '"t1"' in found and '"two"' in found and '"start"' not in found)
check("요청문(수정): 의도별로 대상을 찾아 고치고, 조건 의도는 모든 수정 요청에 붙음",
      "Catalog(a + 1);" in src and "void Catalog(int n);" in src and "사용자 요청: Catalog 호출의 인자에 1을 더한다" in e_prompt
      and "[공통 요청]" in e_prompt and "주석은 바꾸지 않는다" in e_prompt and "WriteLog2" not in src)
check("요청문: 모델이 고칠 필요가 없다고 본 줄(선언)은 수동 검토로 올리지 않음(종료 코드 0)",
      code == 0 and "Catalog" not in out_of(os.path.join(work, "s2"), "manual_review.md"))

m = ask_mock([intent("modify", "Catalog 호출의 인자에 1을 더한다", names=["Catalog"], construct="call")],
             edit=lambda l: l.replace("Catalog(a)", "Catalog(a + 1)"))
code1, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--max-calls", "1", "--ask", "Catalog 호출의 인자에 1을 더해 줘", "--out", os.path.join(work, "s2r"))
n1 = m.calls
code2, text = cli("run", "--resume", run_dir(os.path.join(work, "s2r")))
m.close()
check("요청문과 호출 예산: 분석 호출도 예산에 들어가고, 이어서 실행하면 분석 결과를 다시 묻지 않고 남은 수정 요청만 보냄",
      code1 == 4 and n1 == 1 and code2 == 0 and m.calls == 2 and "Catalog(a + 1);" in out_of(os.path.join(work, "s2r"), "changed", "Src", "Svc.cpp"))

m = ask_mock([intent("modify", "if 문의 조건식을 괄호로 한 번 더 감싼다", construct="if")])
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", "모든 if 문의 조건식을 괄호로 한 번 더 감싸 줘", "--out", os.path.join(work, "s2c"))
m.close()
plan = json.loads(out_of(os.path.join(work, "s2c"), "ask_plan.json") or "{}")
i0 = ((plan.get("asks") or [{}])[0].get("intents") or [{}])[0]
check("요청문: 이름 없이 구문만 받으면 그 구문의 머리 줄이 대상이 되고, 모델이 고치지 않으면 그대로 둠",
      i0.get("sites") == 2 and code == 0 and any("if (a > 3) {" in p and "if (i == 2)" in p for p in m.prompts if "BEGIN CODE" in p))

cfgk = os.path.join(work, "check_over.json")
with open(cfgk, "w", encoding="utf-8") as fh:
    json.dump({"limits": {"ask_check_over": 1}}, fh)
m = ask_mock([intent("find", "로그를 남기는 함수 호출을 찾는다", words=["log", "trace"])], keep=("WriteLog",), bad_first=True)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--server-config", cfgk, "--ask", "로그를 남기는 함수 호출을 찾아 줘", "--out", os.path.join(work, "s3"))
m.close()
plan = json.loads(out_of(os.path.join(work, "s3"), "ask_plan.json") or "{}")
i0 = ((plan.get("asks") or [{}])[0].get("intents") or [{}])[0]
check("요청문: 형식이 틀린 분석 답은 한 번 다시 묻고, 후보가 많으면 번호로 추리며 범위 밖 번호는 버림(분석 2회 + 후보 확인 1회)",
      m.calls == 3 and i0.get("candidates") == 2 and i0.get("targets") == ["WriteLog"]
      and "찾은 곳: 4곳" in out_of(os.path.join(work, "s3"), "found_ASK-1-1.md"))

code, text = cli(*BASE, "--package", pkg, "--no-llm", "--ask", ASK1, "--out", os.path.join(work, "s4"))
report = out_of(os.path.join(work, "s4"), "report.md")
check("요청문: sLLM 을 쓸 수 없으면 분석하지 못한 요청을 수동 검토로 남기고, 호출이 없는 이유를 보고서에 적음(종료 코드 1)",
      code == 1 and "요청을 분석하지 못함" in out_of(os.path.join(work, "s4"), "manual_review.md")
      and "수정 요청을 한 건도 보내지 않았다" in report and "분석하지 못했거나 대상을 찾지 못한 것 1건" in report)

m = ask_mock([intent("modify", "NoSuchFunc 호출을 지운다", names=["NoSuchFunc"])])
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", "NoSuchFunc 호출을 지워 줘", "--out", os.path.join(work, "s5"))
m.close()
check("요청문: 모델이 낸 이름이 코드에 없으면 대상을 만들지 않고 이유를 남김",
      "코드에 없음" in out_of(os.path.join(work, "s5"), "manual_review.md") and m.calls == 1 and not out_of(os.path.join(work, "s5"), "changed", "Src", "Svc.cpp"))

pkg = os.path.join(work, "ARR8")
ASK3 = "환경 호환성 변경과 명시한 배열->구조체 개선 수행한다."
two = [intent("compat", "환경 호환성 변경을 수행한다"), intent("array_to_struct", "명시한 배열을 구조체로 바꾼다")]
m = ask_mock(two, edit=None)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", ASK3, "--out", os.path.join(work, "s6"))
m.close()
report = out_of(os.path.join(work, "s6"), "report.md")
check("요청문: 대상을 가리키지 않는 요청은 조용히 끝나지 않고, 호출이 없는 이유를 알림(환경 호환성은 규칙과 --compat-llm, 배열은 이름 필요)",
      m.calls == 1 and "수정 요청을 한 건도 보내지 않았다" in report and "환경 호환성 변경은 요청문으로 처리하지 않음" in report
      and "배열 이름을 받지 못함" in out_of(os.path.join(work, "s6"), "manual_review.md"))
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [ft]}, fh)


def arr_and_analysis(user):
    if is_analysis(user):
        return json.dumps({"intents": two}, ensure_ascii=False)
    return arr_responder(user)


m = Mock(arr_and_analysis)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", ASK3, "--request", req, "--out", os.path.join(work, "s7"))
m.close()
src = out_of(os.path.join(work, "s7"), "changed", "Src", "Data.cpp")
check("요청문 + 요청 파일: 같은 문장이라도 배열을 요청 파일로 명시하면 선언은 도구가, 남은 사용처는 sLLM 이 고침",
      "G_DATA_T g_data = {0};" in src and "((int*)&g_data)[i]" in src and "Send((int*)&g_data);" in src and m.calls >= 2)


def named_array(user):
    if is_analysis(user):
        return json.dumps({"intents": [intent("array_to_struct", "g_data 배열을 구조체로 바꾼다", names=["g_data"])]}, ensure_ascii=False)
    return arr_responder(user)


m = Mock(named_array)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--ask", "g_data 배열을 구조체로 바꿔 줘", "--out", os.path.join(work, "s8"))
m.close()
src, hdr = out_of(os.path.join(work, "s8"), "changed", "Src", "Data.cpp"), out_of(os.path.join(work, "s8"), "changed", "Inc", "Data.h")
check("요청문(배열): 문장에 배열 이름이 있으면 요청 파일 없이도 처리. 필드 이름은 인덱스 상수의 이름에서 만듦(IDX_TEMP -> temp)",
      "G_DATA_T g_data = {0};" in src and "g_data.temp = 20;" in src and "g_data.count + ((int*)&g_data)[i]" in src
      and "int temp;" in (src + hdr) and "int count;" in (src + hdr) and "extern G_DATA_T g_data;" in hdr)

# ------------------------------------------------------------------ 7. 환경 호환성 항목의 sLLM 호출(--compat-llm)
pkg = os.path.join(work, "CPT")
write(pkg, "Src/Copy.cpp", ["#include <string.h>", "#include <stdio.h>", "void CopyArr(const char* src)", "{", "    char buf[32];", "    strcpy(buf, src);",
                            "    gets(buf);", "}", "", "void CopyPtr(char* dst, const char* src)", "{", "    strcpy(dst, src);", "}"])


def compat_responder(user):
    def fn(line):
        if "sizeof(dst)" in user and "strcpy(dst" in line:      # 앞 답이 기각된 이유(포인터)를 받았으면 그 줄은 고치지 않는다
            return line
        x = re.search(r"strcpy\(([^,]+), ([^)]+)\);", line)
        if x:
            return line.replace(x.group(0), "strcpy_s(%s, sizeof(%s), %s);" % (x.group(1), x.group(1), x.group(2)))
        return re.sub(r"gets\((\w+)\);", r"fgets(\1, sizeof(\1), stdin);", line)
    return edit_targets(user, fn)


m = Mock(compat_responder)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "k1"))
m.close()
report, manual = out_of(os.path.join(work, "k1"), "report.md"), out_of(os.path.join(work, "k1"), "manual_review.md")
check("환경 호환성(기본): --compat-llm 을 주지 않으면 sLLM 을 부르지 않고 규칙의 sLLM 대상은 수동 검토로 둠",
      m.calls == 0 and code == 1 and "R-T3-GETS-REMOVED" in manual and "--compat-llm" in manual
      and "수정 요청을 한 건도 보내지 않았다" in report and "_CRT_SECURE_NO_WARNINGS" in out_of(os.path.join(work, "k1"), "project_settings.md"))
m = Mock(compat_responder)
code, text = cli(*BASE, "--compat-llm", "2", "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "k2"))
m.close()
src, report, manual = out_of(os.path.join(work, "k2"), "changed", "Src", "Copy.cpp"), out_of(os.path.join(work, "k2"), "report.md"), out_of(os.path.join(work, "k2"), "manual_review.md")
check("환경 호환성(2단계): sLLM 을 부르지 않고, 지역 배열에 쓰는 strcpy 를 규칙으로 바꿈. 규칙의 sLLM 대상(gets)은 수동 검토로 둠",
      m.calls == 0 and "strcpy_s(buf, sizeof(buf), src);" in src and "gets(buf);" in src and "strcpy(dst, src);" in src
      and "R-T3-GETS-REMOVED" in manual and "2단계" in report and code == 1)
m = Mock(compat_responder)
code, text = cli(*BASE, "--compat-llm", "3", "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "k3"))
m.close()
src, report, manual = out_of(os.path.join(work, "k3"), "changed", "Src", "Copy.cpp"), out_of(os.path.join(work, "k3"), "report.md"), out_of(os.path.join(work, "k3"), "manual_review.md")
check("환경 호환성(3단계): 2단계의 기계적 변환 뒤에 남은 것(gets, 포인터에 쓰는 strcpy)을 sLLM 에 보냄. gets 는 고쳐지고, 포인터에 sizeof 를 쓴 "
      "수정은 그 블록만 받지 않아 strcpy 는 그대로 남음(0.10.3)",
      m.calls >= 1 and "strcpy_s(buf, sizeof(buf), src);" in src and "fgets(buf, sizeof(buf), stdin);" in src and "strcpy(dst, src);" in src
      and "크기 인자에 포인터의 sizeof 를 썼다" in manual and "보안 CRT 함수의 기계적 변환" in report and "지역 배열이 아님" in report and code == 1
      and "strcpy(dst, src);" in "\n".join(re.findall(r"- \d+번째 줄 \[[^\]]+\]: (.+)", "\n".join(m.prompts))))
code, text = cli(*BASE, "--compat-llm", "--package", pkg, "--out", os.path.join(work, "k4"))
code2, text2 = cli(*BASE, "--compat-llm", "5", "--package", pkg, "--out", os.path.join(work, "k4"))
check("환경 호환성: --compat-llm 에 값을 주지 않거나 1, 2, 3 이 아닌 값을 주면 입력 오류(단계의 뜻을 알려 줌)",
      code == 3 and code2 == 3 and "1, 2, 3" in text and "받은 값: 5" in text2)

# ------------------------------------------------------------------ 8. OS, DB 의 원본/대상(--from-os, --to-os, --from-db, --to-db)
import argparse
e = V.build_env(argparse.Namespace(from_vs="2008", to_vs="2022", from_os="Windows 7", to_os="Windows  11", from_db="Oracle 11g", to_db="Oracle 19c"))
lines = V.env_summary_lines(e)
check("OS·DB 옵션: 원본/대상 값을 받아 환경 요약에 넣음(주지 않으면 줄이 없음)",
      "OS: Windows 7 -> Windows 11" in lines and "DB: Oracle 11g -> Oracle 19c" in lines
      and not any(l.startswith(("OS:", "DB:")) for l in V.env_summary_lines(V.build_env(argparse.Namespace(from_vs="2008", to_vs="2022")))))
rule = {"id": "X", "tier": "T3", "title": "t", "pattern": "x", "when": {"db": "oracle", "os_changed": True}}
e2 = V.build_env(argparse.Namespace(from_vs="2008", to_vs="2022", to_os="Windows 11", to_db="MySQL 8.0"))
check("OS·DB 옵션: 규칙의 조건으로 쓸 수 있음(값에 낱말이 있을 때, 원본과 대상이 다를 때)",
      V.rule_state(rule, e)[0] and not V.rule_state(rule, e2)[0] and "DB~oracle" in V.rule_when_text(rule) and "OS 변경" in V.rule_when_text(rule))
pkg = os.path.join(work, "OSDB")
write(pkg, "Src/Ver.cpp", ["#define WINVER 0x0400", "#include <stdio.h>", "void Log(const char* s);", "void F() { Log(\"a\"); }"])
m = ask_mock([intent("modify", "Log 호출의 문자열 앞에 DB 이름을 붙인다", names=["Log"], construct="call")], edit=lambda l: l)
code, text = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--to-os", "Windows 11", "--from-db", "Oracle 11g", "--to-db", "Oracle 19c",
                 "--ask", "Log 호출의 문자열 앞에 DB 이름을 붙여 줘", "--out", os.path.join(work, "o1"))
m.close()
src, report = out_of(os.path.join(work, "o1"), "changed", "Src", "Ver.cpp"), out_of(os.path.join(work, "o1"), "report.md")
check("OS·DB 옵션: 낮은 WINVER 를 대상 OS 의 값으로 올리고, 분석 요청과 수정 요청의 [환경]과 보고서에 들어감",
      "#define WINVER 0x0A00" in src and "DB: Oracle 11g -> Oracle 19c" in report and "OS: Windows 11" in report
      and len(m.prompts) >= 2 and all("DB: Oracle 11g -> Oracle 19c" in p and "OS: Windows 11" in p for p in m.prompts))
code, text = cli(*BASE, "--package", pkg, "--no-llm", "--out", os.path.join(work, "o2"))
check("OS·DB 옵션: 대상 OS 를 주지 않으면 WINVER 는 Windows 7 값으로", "#define WINVER 0x0601" in out_of(os.path.join(work, "o2"), "changed", "Src", "Ver.cpp"))

n_ok = sum(1 for _n, ok in checks if ok)
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), n_ok))
sys.exit(0 if n_ok == len(checks) else 1)
