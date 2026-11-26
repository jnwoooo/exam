"""0.9.2, 0.9.3 의 시험.

사용: python test_v092.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. --compat-llm 의 세 단계: 1(기계적 변경만), 2(1 + 선언을 읽어 판정하는 기계적 변환), 3(2 + 남은 것을 sLLM 에)
  2. 보안 CRT 의 기계적 변환(규칙 R-T2-CRT-SECURE-LOCAL): 바꾸는 줄과 바꾸지 않는 줄(사유별)
  3. 변환 결과의 꼴, 끄는 방법, 검토 표시
  4. 없는 _s 함수 이름 기각
  5. 컴파일 검사가 치명적 오류로 막힌 경우를 통과로 치지 않는다
  6. 실제 코드에서 나온 결함과 같은 꼴의 수정(21_실코드시험_결함분석.md)이 들어가지 않는다
  7. 옵션별 사용 예(vupdate examples): 표의 명령이 모두 받아들여지고, 문서 23 과 같다
모델은 이 파일 안의 모의 서버이고 컴파일러도 모의다. 실제 sLLM 의 응답 품질과 실제 cl 의 동작은 이 시험으로 확인되지 않는다.
"""
import json, os, re, shlex, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def cli(*args, cwd=None):
    en = dict(os.environ)
    en.pop("VUPDATE_LLM_ENDPOINT", None)
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=cwd or work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def out_of(root, *parts):
    hits = [os.path.join(b, parts[-1]) for b, _d, fs in os.walk(root) if parts[-1] in fs and b.replace("\\", "/").endswith("/".join(("out",) + parts[:-1]))]
    return open(hits[0], "rb").read().decode("cp949" if parts[0] == "changed" else "utf-8") if hits else ""


def write(pkg, rel, lines):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), "\r\n".join(lines + [""]).encode("cp949"))


def block(search, replace):
    return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (search, replace)


def code_of(user):
    return user.split("-----BEGIN CODE-----\n", 1)[1].split("\n-----END CODE-----")[0]


def targets_of(user):
    return [x.group(1).strip() for x in re.finditer(r"- \d+번째 줄 \[[^\]]+\]: (.+)", user)]


class Mock:
    """모의 sLLM. responder(요청문) 이 답의 본문을 돌려준다."""

    def __init__(self, responder):
        self.calls, self.prompts = 0, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send({"data": [{"id": "mock"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                user = body["messages"][-1]["content"]
                outer.calls += 1
                outer.prompts.append(user)
                out = responder(user)
                self._send({"choices": [{"message": {"content": out}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": len(user) // 3, "completion_tokens": len(out) // 3}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def good_fix(line):
    """도구가 허용하는 꼴: 이름 뒤에 _s, 첫 인자 뒤에 sizeof(첫 인자). gets 는 fgets 로."""
    x = re.search(r"\b(strcpy|strcat|sprintf)\((\w+), ", line)
    if x:
        return line.replace(x.group(0), "%s_s(%s, sizeof(%s), " % (x.group(1), x.group(2), x.group(2)))
    return re.sub(r"gets\((\w+)\);", r"fgets(\1, sizeof(\1), stdin);", line)


def by_targets(fn):
    """[수정 대상 줄]마다 fn(줄) 로 바꾼 수정 블록을 내는 응답기."""
    def responder(user):
        code, out = code_of(user).split("\n"), []
        for t in targets_of(user):
            line = next((l for l in code if t in l), None)
            if line and fn(line) != line and not any(line in b for b in out):
                out.append(block(line, fn(line)))
        return "\n".join(out) or "NO_CHANGE"
    return responder


BASE = ["run", "--from-vs", "2010", "--to-vs", "2022", "--no-compile"]

# ------------------------------------------------------------------ 1. 세 단계
pkg = os.path.join(work, "LV")
write(pkg, "Src/Lv.cpp", ["#include <stdio.h>", "#include <string.h>", "void Read(const char* src)", "{", "    char buf[32];", "    char path[64];",
                          "    gets(buf);", "    strcpy(path, src);", "    strcat(path, \".dat\");", "    sprintf(buf, \"%s:%d\", src, 3);", "}", "",
                          "void Copy(char* dst, const char* src)", "{", "    strcpy(dst, src);", "    strncpy(dst, src, 8);", "}"])
res = {}
for lv in ("1", "2", "3"):
    m = Mock(by_targets(good_fix))
    code, text = cli(*BASE, "--compat-llm", lv, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "lv" + lv))
    m.close()
    res[lv] = (code, m.calls, out_of(os.path.join(work, "lv" + lv), "changed", "Src", "Lv.cpp"), out_of(os.path.join(work, "lv" + lv), "report.md"),
               out_of(os.path.join(work, "lv" + lv), "manual_review.md"), "\n".join(m.prompts))
m = Mock(by_targets(good_fix))
code0, _t = cli(*BASE, "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "lv0"))
m.close()
MECH = ("strcpy_s(path, sizeof(path), src);", "strcat_s(path, sizeof(path), \".dat\");", "sprintf_s(buf, sizeof(buf), \"%s:%d\", src, 3);")
check("1단계(기본): 기계적 변경만. sLLM 을 부르지 않고 보안 CRT 줄도 그대로 둠. 규칙의 sLLM 대상(gets)은 수동 검토. 옵션을 주지 않은 것과 같음",
      res["1"][1] == 0 and res["1"][0] == 1 and not res["1"][2] and "R-T3-GETS-REMOVED" in res["1"][4] and "1단계" in res["1"][3]
      and m.calls == 0 and code0 == 1)
check("2단계: sLLM 을 부르지 않고, 지역 char 배열에 쓰는 strcpy, strcat, sprintf 를 규칙으로 바꿈. gets 는 수동 검토로 남음",
      res["2"][1] == 0 and all(x in res["2"][2] for x in MECH) and "gets(buf);" in res["2"][2] and "R-T3-GETS-REMOVED" in res["2"][4]
      and "2단계" in res["2"][3] and res["2"][0] == 1)
src3 = res["3"][2]
check("3단계: 2단계의 기계적 변환 뒤에 남은 것(gets 와 2단계의 조건에 맞지 않은 보안 CRT 줄)을 sLLM 에 보내고, gets 가 고쳐짐(0.10.3)",
      res["3"][1] >= 1 and all(x in src3 for x in MECH) and "fgets(buf, sizeof(buf), stdin);" in src3
      and sorted(set(targets_of(res["3"][5]))) == ["gets(buf);", "strcpy(dst, src);", "strncpy(dst, src, 8);"] and "3단계" in res["3"][3])
check("2단계: 포인터에 쓰는 strcpy 와 strncpy 는 바꾸지 않고 수동 검토로도 올리지 않음. 3단계: sLLM 이 포인터에 sizeof 를 쓴 수정을 내면 "
      "그 블록만 받지 않고 사유와 함께 남김(같은 작업의 gets 수정은 받음). 보고서에 사유별 줄 수가 나옴",
      all("strcpy(dst, src);" in res[k][2] and "strncpy(dst, src, 8);" in res[k][2] for k in ("2", "3")) and "strcpy(dst" not in res["2"][4]
      and "크기 인자에 포인터의 sizeof 를 썼다: dst" in res["3"][4] and "strncpy(dst" not in res["3"][4]
      and "보안 CRT 경고에 걸린 줄 5곳 가운데 조건에 맞는 3곳을 규칙으로 바꿨다" in res["2"][3] and "대상 함수가 아님" in res["2"][3]
      and "지역 배열이 아님" in res["2"][3])
check("3단계: sLLM 이 받는 조각에는 이미 기계적 변환이 반영돼 있음(기계적 변경 뒤에 맡김)",
      "strcpy_s(path, sizeof(path), src);" in res["3"][5] and "L-CRT-SECURE" not in res["3"][5])

# ------------------------------------------------------------------ 2. 보안 CRT 기계적 변환의 대상 선정(사유별)
pkg = os.path.join(work, "SEL")
write(pkg, "Inc/Sel.h", ["#pragma once", "#define NAME_LEN 16", "typedef struct { char tc[5]; char seq[6]; char flag[1]; } MSG;", "extern char g_name[32];"])
SEL = [
    ("", "#include \"Sel.h\""), ("", "char g_name[32];"), ("", "void Send(const char* p, int n);"),
    ("", "void F(MSG* m, MSG v, char* out, char arr[], const char* s, int n)"), ("", "{"),
    ("", "    char a[32];"), ("", "    char seq[6];"), ("", "    char one[1];"), ("", "    char nm[NAME_LEN];"), ("", "    char pth[MAX_PATH];"),
    ("", "    char un[UNKNOWN_LEN];"), ("", "    char raw[16];"), ("", "    char two[4][8];"), ("", "    unsigned char ub[16];"), ("", "    char own[20];"),
    ("", "    char* p;"), ("", "    const char* fmt = \"%d\";"),
    ("ok", "    strcpy(a, s);"), ("ok", "    strcat(a, \"/x\");"), ("ok", "    sprintf(a, \"%s-%04d\", s, n);"),
    ("ok", "    if (n > 0) strcpy(a, s);"), ("ok", "    else strcat(a, \"e\");"), ("ok", "    strcpy(nm, s);"), ("ok", "    strcpy(pth, s);"),
    ("ok", "    sprintf(seq, \"%05d\", n);"), ("ok", "    strcpy(a, \"한글\");"),
    ("func", "    strncpy(a, s, 5);"), ("func", "    strncat(a, s, 3);"), ("func", "    FILE* fp = fopen(s, \"a\");"),
    ("member", "    strcpy(m->tc, s);"), ("member", "    sprintf(v.seq, \"%06d\", n);"),
    ("offset", "    strcpy(a + 2, s);"), ("offset", "    strcpy(&a[2], s);"),
    ("value", "    p = strcpy(a, s);"), ("value", "    n = sprintf(a, \"%d\", n);"), ("value", "    Send(strcat(a, s), 3);"),
    ("notlocal", "    strcpy(out, s);"), ("notlocal", "    strcpy(arr, s);"), ("notlocal", "    strcpy(g_name, s);"), ("notlocal", "    strcpy(p, s);"),
    ("type", "    strcpy(two, s);"), ("type", "    strcpy(ub, s);"),
    ("size", "    strcpy(un, s);"),
    ("raw", "    strcpy(raw, s);"), ("", "    Send(raw, 16);"), ("", "    memcpy(out, raw, 16);"),
    ("raw", "    strcpy(own, s);"), ("", "    SendPacket(own, sizeof(own));"),
    ("format", "    sprintf(a, fmt, n);"),
    ("small", "    sprintf(seq, \"%06d\", n);"), ("small", "    sprintf(one, \"%d\", n);"), ("small", "    strcpy(seq, \"ABCDEF\");"),
    ("small", "    strcpy(seq, \"한글이\");"),
    ("line", "    strcpy(a, s); strcpy(out, s);"),
    ("", "}"),
]
write(pkg, "Src/Sel.cpp", [l for _k, l in SEL])
V.write_bytes(os.path.join(work, "sel_server.json"), json.dumps({"limits": {"scrt_raw_calls": ["SendPacket"]}}).encode("utf-8"))
ns = V._selftest_ns(os.path.join(work, "sel"), pkg, "", from_vs="2010", compat_llm=2, no_compile=True, no_llm=True,
                    server_config=os.path.join(work, "sel_server.json"))
ctx = V._make_ctx(ns)
try:
    V.run_pipeline(ctx, [s_ for s_, _n in V.STAGES])
    done = {r["line"] for r in ctx.store.q("SELECT line FROM issues WHERE rule_id='R-T2-CRT-SECURE-LOCAL' AND status='fixed'")}
    cf = ctx.store.get_meta("scrt_stat", {}) or {}
    sel_out = V.read_bytes(os.path.join(ctx.run_dir, "out", "changed", "Src", "Sel.cpp")).decode("cp949").split("\r\n")
    sel_report = open(os.path.join(ctx.run_dir, "out", "report.md"), encoding="utf-8").read()
    d = V.get_doc(ctx, "Src/Sel.cpp")
    rx = re.compile(ctx.rule_by_id["R-T0-CRT-SECURE"]["pattern"])
    V._DET["ctx"] = ctx                                      # 사유를 직접 물어볼 때도 패키지 전체의 #define 과 설정을 쓰게 한다
    got = {}
    for mm in rx.finditer(d.code):
        ln = d.line_of(mm.start())
        if ln not in got:
            got[ln] = V._scrt_site(d, d.get_blocks(), list(d.inits or []), mm)[0]
finally:
    V._DET["ctx"] = None
    ctx.store.close()
    V._SINK.close()
want = {i + 1: k for i, (k, _l) in enumerate(SEL) if k}
bad = {ln: (want[ln], got.get(ln)) for ln in want if (want[ln] == "ok") != (ln in done)}
bad2 = {ln: (want[ln], got.get(ln)) for ln in want if want[ln] not in ("ok", "line") and got.get(ln) != want[ln]}
kept = [ln for ln in want if want[ln] != "ok" and sel_out[ln - 1] != SEL[ln - 1][1]]
if bad or bad2 or kept:
    print("대상 선정 불일치:", bad, bad2, kept)
check("대상 선정: 바꿔야 하는 9줄은 모두 바꾸고, 바꾸지 않아야 하는 25줄은 한 글자도 바뀌지 않음", not bad and not kept and len(done) == 9)
check("대상 선정: 바꾸지 않는 사유가 줄마다 기대와 같음(함수, 멤버, 위치, 반환값, 지역 배열 아님, 타입, 크기, 바이트 단위 사용, 형식, 길이)",
      not bad2 and cf.get("seen") == len(want) and sum(cf.get("why", {}).values()) == len(want) - 9 and cf.get("why", {}).get("line") == 1
      and "바꾸지 않은 사유" in sel_report)
check("대상 선정: 배열 크기를 패키지의 #define(NAME_LEN)과 널리 쓰이는 상수(MAX_PATH)에서 읽고, 설정의 scrt_raw_calls(SendPacket)를 반영",
      "    strcpy_s(nm, sizeof(nm), s);" in sel_out and "    strcpy_s(pth, sizeof(pth), s);" in sel_out
      and got.get(next(i for i, (k, l) in enumerate(SEL, 1) if "strcpy(own" in l)) == "raw")
L = V._lit_min_len
check("형식의 최소 출력 길이: %06d 는 6, %d 는 1, %s 는 0, %% 는 1, 폭과 정밀도, 한글은 CP949 로 2바이트, 읽지 못하는 형식은 None",
      L(["%06d"], "cp949", True) == 6 and L(["%d"], "cp949", True) == 1 and L(["%s"], "cp949", True) == 0 and L(["AB%%"], "cp949", True) == 3
      and L(["%5.2f"], "cp949", True) == 5 and L(["%.3d"], "cp949", True) == 3 and L(["%-10s|"], "cp949", True) == 11
      and L(["한글"], "cp949", False) == 4 and L(["a\\n\\x41"], "cp949", False) == 3 and L(["%!"], "cp949", True) is None)

# ------------------------------------------------------------------ 3. 변환 결과의 꼴, 끄는 방법, 검토 표시
pkg = os.path.join(work, "FORM")
write(pkg, "Src/Form.c", ["#include <stdio.h>", "#include <string.h>", "void Fm(const char* src, int n)", "{", "    char buf[64];",
                          "    strcpy( buf , src );", "    sprintf(buf,", "            \"%s/%03d\",", "            src, n);  /* 여러 줄 */",
                          "    if (n) strcat(buf, src); else strcpy(buf, \"\");", "    strcpy_s(buf, sizeof(buf), src);", "}"])
code, _t = cli(*BASE, "--compat-llm", "2", "--package", pkg, "--out", os.path.join(work, "f1"))
f1, f1r = out_of(os.path.join(work, "f1"), "changed", "Src", "Form.c"), out_of(os.path.join(work, "f1"), "report.md")
check("변환의 꼴: 이름 뒤에 _s, 첫 인자 뒤에 sizeof(첫 인자)만 넣고 공백·줄바꿈·주석·나머지 인자는 그대로 둠. 이미 보안 함수인 줄은 건드리지 않음",
      "    strcpy_s( buf, sizeof(buf) , src );" in f1 and "    sprintf_s(buf, sizeof(buf),\r\n            \"%s/%03d\",\r\n            src, n);  /* 여러 줄 */" in f1
      and "    if (n) strcat_s(buf, sizeof(buf), src); else strcpy_s(buf, sizeof(buf), \"\");" in f1
      and f1.count("strcpy_s(buf, sizeof(buf), src);") == 1 and code == 0)
check("변환의 표시: 바꾼 곳은 보고서의 사람이 확인해야 하는 수정 목록과 규칙별 표(R-T2-CRT-SECURE-LOCAL)에 나옴",
      "R-T2-CRT-SECURE-LOCAL" in f1r and "적용했지만 사람이 확인해야 하는 수정" in f1r and "strcpy_s( buf, sizeof(buf)" in f1r)
code, _t = cli(*BASE, "--compat-llm", "3", "--disable-rule", "R-T2-CRT-SECURE-LOCAL", "--package", pkg, "--out", os.path.join(work, "f2"))
f2 = out_of(os.path.join(work, "f2"), "changed", "Src", "Form.c")
V.write_bytes(os.path.join(work, "f3_exc.json"), json.dumps({"skip": [{"path": "Src/Form.c", "rules": ["R-T2-CRT-SECURE-LOCAL"], "lines": "6"}]}).encode("utf-8"))
code3, t3 = cli(*BASE, "--compat-llm", "2", "--exceptions", os.path.join(work, "f3_exc.json"), "--package", pkg, "--out", os.path.join(work, "f3"))
f3 = out_of(os.path.join(work, "f3"), "changed", "Src", "Form.c")
check("끄는 방법: --disable-rule R-T2-CRT-SECURE-LOCAL 을 주면 3단계에서도 보안 CRT 변환을 하지 않음. 예외 파일로 줄 단위로도 뺄 수 있음",
      not f2 and code == 0 and "    strcpy( buf , src );" in f3 and "sprintf_s(buf, sizeof(buf)," in f3)
c_r, t_r = cli("rules", "--from-vs", "2010", "--to-vs", "2022")
c_r2, t_r2 = cli("rules", "--from-vs", "2010", "--to-vs", "2022", "--compat-llm", "2")
line1 = next((l for l in t_r.split("\n") if l.startswith("R-T2-CRT-SECURE-LOCAL")), "")
line2 = next((l for l in t_r2.split("\n") if l.startswith("R-T2-CRT-SECURE-LOCAL")), "")
check("규칙 목록: 보안 CRT 변환 규칙은 1단계에서 꺼짐(이유가 나옴), 2단계 이상에서 켜짐",
      "꺼짐" in line1 and "--compat-llm 2 이상에서만" in line1 and "켜짐" in line2)

# ------------------------------------------------------------------ 4. 없는 _s 함수 이름
pkg = os.path.join(work, "NM")
write(pkg, "Src/Nm.cpp", ["#include <stdio.h>", "#include <string.h>", "int my_copy_s(char* d, int n, const char* s);", "void H(const char* s)", "{",
                          "    char buf[32];", "    gets(buf);", "}"])


def nm(tag, fixed):
    m = Mock(lambda user: block("    gets(buf);", "    " + fixed))
    code, _t = cli(*BASE, "--compat-llm", "3", "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, tag))
    m.close()
    return code, out_of(os.path.join(work, tag), "changed", "Src", "Nm.cpp"), out_of(os.path.join(work, tag), "manual_review.md"), "\n".join(m.prompts)


n1, n2, n3, n4 = nm("n1", "_gets_s(buf, sizeof(buf));"), nm("n2", "gets_s(buf, sizeof(buf));"), nm("n3", "my_copy_s(buf, sizeof(buf), s);"), nm("n4", "_strncat_s(buf, sizeof(buf), s, 3);")
check("없는 이름: _gets_s, _strncat_s 처럼 C 런타임에 없는 _s 이름은 기각하고(사유를 붙여 다시 요청) 끝내 수동 검토로 보냄",
      not n1[1] and not n4[1] and n1[0] == 1 and "없는 함수 이름을 썼다: _gets_s" in n1[3] and "없는 함수 이름을 썼다: _strncat_s" in n4[3]
      and "R-T3-GETS-REMOVED" in n1[2])
check("없는 이름: 실제로 있는 이름(gets_s)과 패키지에 선언된 이름(my_copy_s)은 받음",
      "gets_s(buf, sizeof(buf));" in n2[1] and "my_copy_s(buf, sizeof(buf), s);" in n3[1])

# ------------------------------------------------------------------ 5. 컴파일 검사가 막힌 경우
pkg_b = os.path.join(work, "CB")
write(pkg_b, "Src/Blk.cpp", ["#include <stdio.h>", "#include \"vendor_missing.h\"", "void K()", "{", "    char buf[32];", "    gets(buf);", "}"])
write(pkg_b, "Src/Ok.cpp", ["#include <stdio.h>", "int OLD_ERR = missing_symbol;", "void K2()", "{", "    char buf[32];", "    gets(buf);", "}"])
write(pkg_b, "Src/New.cpp", ["#include <stdio.h>", "void K3()", "{", "    char buf[32];", "    gets(buf);", "}"])


def fake_cl(tools, src_abs, cwd, flags, timeout, arch="x86"):
    """모의 컴파일러. 열 수 없는 헤더를 만나면 실제 cl 처럼 치명적 오류 하나만 내고 그 파일의 검사를 끝낸다."""
    diags, merged = [], (os.sep + "merged" + os.sep) in src_abs
    for no, line in enumerate(open(src_abs, encoding="cp949").read().split("\n"), 1):
        if "vendor_missing.h" in line:
            diags = [{"file": src_abs, "line": no, "kind": "fatal error", "code": "C1083", "msg": "포함 파일을 열 수 없습니다. 'vendor_missing.h'"}]
            break
        if "OLD_ERR" in line:
            diags.append({"file": src_abs, "line": no, "kind": "error", "code": "C2065", "msg": "'missing_symbol': 선언되지 않은 식별자"})
        if "fgets(" in line and merged and os.path.basename(src_abs) == "New.cpp":
            diags.append({"file": src_abs, "line": no, "kind": "fatal error", "code": "C1083", "msg": "포함 파일을 열 수 없습니다. 'added.h'"})
    return True, diags, "\n".join("%s(%d): %s %s: %s" % (x["file"], x["line"], x["kind"], x["code"], x["msg"]) for x in diags)


V.find_tools = lambda vs_path="", sdk_path="": {"platform": "win32(모의)", "cl_all": ["fake"], "cl_modern": "fake-cl.exe", "cl_2008": "",
                                                  "sdk_math_h": "fake", "mfc_afxwin_h": "", "vs_root": "fake", "vsdevcmd": "fake",
                                                  "cl_in_path": "", "vs_path_given": vs_path}
V.smoke_test_compiler = lambda ctx: (True, "정상(모의 컴파일러)")
V.get_vs_env = lambda tools, arch="x86": {"PATH": ""}
V.run_cl_syntax = fake_cl
m = Mock(by_targets(good_fix))
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "cb"), pkg_b, m.url, from_vs="2010", compat_llm=3, no_compile=False))
try:
    code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    files = {r["path"]: (r["status"], r["verify_level"]) for r in ctx.store.q("SELECT path, status, verify_level FROM files WHERE kind='source'")}
    ver = {r["path"]: (r["result"], r["detail"]) for r in ctx.store.q("SELECT path, result, detail FROM verifications WHERE level='L3'")}
    report = open(os.path.join(ctx.run_dir, "out", "report.md"), encoding="utf-8").read()
finally:
    ctx.store.close()
    V._SINK.close()
    m.close()
check("컴파일 검사: 원본이 치명적 오류(C1083)로 중단되는 파일은 통과가 아니라 미수행으로 기록하고 검증 수준을 L2 로 둠",
      ver.get("Src/Blk.cpp", ("", ""))[0].startswith("미수행") and "C1083" in ver["Src/Blk.cpp"][1] and files.get("Src/Blk.cpp") == ("변경됨", "L2"))
check("컴파일 검사: 원본에도 있던 보통 오류는 새 오류로 치지 않고 통과(L3). 수정 때문에 치명적 오류가 새로 생기면 불합격으로 수정을 취소",
      ver.get("Src/Ok.cpp", ("", ""))[0].startswith("통과") and files.get("Src/Ok.cpp") == ("변경됨", "L3") and files.get("Src/New.cpp", ("", ""))[0] == "수동검토")
check("컴파일 검사: 보고서 첫머리에 검사하지 못한 파일 수와 해야 할 일을 적음",
      "컴파일 검사를 하지 못한 변경 파일이 1개 있다" in report and "Src/Blk.cpp" in report and "include 경로" in report)

# ------------------------------------------------------------------ 6. 실제 코드에서 나온 결함과 같은 꼴의 수정
pkg = os.path.join(work, "RD")
write(pkg, "Src/mes_msg.h", ["#pragma once", "typedef struct {", "    char tc[5];", "    char rsp[2];", "    char len[4];", "    char flag[1];", "    char seq[6];",
                              "    char file[12];", "} MES_MSG;"])
RD = ["#include <stdio.h>", "#include <string.h>", "#include \"mes_msg.h\"", "void BuildMsg(MES_MSG *m, char *buf, const char *tc, const char *rsp, int seq, const char *name, int ok)",
      "{", "    char path[64];", "    strncpy(m->tc, tc, 5);", "    strncpy(buf + 5, rsp, 2);", "    sprintf(m->flag, \"%d\", ok);", "    sprintf(m->seq, \"%06d\", seq);",
      "    strncpy(m->file, name, 12);", "    strcpy(path, \"D:\\\\MES\\\\\");", "    strncat(path, name, 12);", "}", "", "FILE *OpenLog(const char *path)", "{",
      "    FILE *fp;", "    fp = fopen(path, \"a\");", "    return fp;", "}"]
write(pkg, "Src/mes_send.c", RD)
BAD = [("    strncpy(m->tc, tc, 5);", "    strncpy_s(m->tc, 5, tc, _TRUNCATE);"), ("    strncpy(buf + 5, rsp, 2);", "    strncpy_s(buf + 5, 3, rsp, 2);"),
       ("    sprintf(m->flag, \"%d\", ok);", "    sprintf_s(m->flag, sizeof(m->flag), \"%d\", ok);"),
       ("    sprintf(m->seq, \"%06d\", seq);", "    sprintf_s(m->seq, sizeof(m->seq), \"%06d\", seq);"),
       ("    strncpy(m->file, name, 12);", "    strncpy_s(m->file, sizeof(m->file), name, 12);"),
       ("    strncat(path, name, 12);", "    _strncat_s(path, sizeof(path), name, 12);"), ("    fp = fopen(path, \"a\");", "    fopen_s(&fp, path, \"a\");")]


def rd_responder(user):
    code = code_of(user).split("\n")
    return "\n".join(block(a, b) for a, b in BAD if a in code) or "NO_CHANGE"


m = Mock(rd_responder)
code, _t = cli(*BASE, "--compat-llm", "3", "--package", pkg, "--llm-endpoint", m.url, "--out", os.path.join(work, "rd"))
m.close()
src, report = out_of(os.path.join(work, "rd"), "changed", "Src", "mes_send.c"), out_of(os.path.join(work, "rd"), "report.md")
got_lines = src.split("\r\n")
man_rd = out_of(os.path.join(work, "rd"), "manual_review.md")
check("실코드 결함: 3단계는 남은 줄을 sLLM 에 보내지만, 실제 코드에서 나온 결함과 같은 수정(고정 길이 필드의 복사·서식, strncat, fopen)은 "
      "받지 않고 사유와 함께 남김(8곳 가운데 1곳만 규칙으로 바꿈, 7곳은 미수정)",
      m.calls >= 1 and "8곳 가운데 조건에 맞는 1곳을 규칙으로 바꿨다" in report and man_rd.count("| R-T3-CRT-SECURE-REST |") == 7
      and "보안 함수로 바꾼 꼴을 받을 수 없다" in man_rd)
check("실코드 결함: 결과에서 바뀐 줄은 지역 배열의 strcpy 하나뿐이고, 나머지 줄은 원본과 같음",
      code == 1 and [i for i, (a, b) in enumerate(zip(RD, got_lines)) if a != b] == [RD.index("    strcpy(path, \"D:\\\\MES\\\\\");")]
      and "    strcpy_s(path, sizeof(path), \"D:\\\\MES\\\\\");" in got_lines)

# ------------------------------------------------------------------ 7. 옵션별 사용 예
p = V.build_parser()
bad_parse = []
for _title, rows in V.USAGE:
    for _opt, _case, cmd, _note in rows:
        argv = shlex.split(V._ex_full(cmd).replace("\\", "\\\\"))[1:]
        try:
            if argv != ["--version"]:
                p.parse_args(argv)
        except SystemExit:
            bad_parse.append(cmd)
check("사용 예: 표의 명령 %d개가 모두 명령줄 형식에 맞음" % sum(len(r) for _t, r in V.USAGE), not bad_parse)

pkg = os.path.join(work, "EX")
for rel in ("Src/Net/Sock.cpp", "Src/Ui/View.cpp", "ThirdParty/Lib.cpp"):
    write(pkg, rel, ["#include <windows.h>", "#include <stdio.h>", "void %s(HWND h, void* p)" % os.path.basename(rel)[:-4], "{", "    char buf[32];",
                     "    gets(buf);", "    SetWindowLong(h, GWL_USERDATA, (LONG)p);", "}"])
V.write_bytes(os.path.join(work, "ex_rules.json"), json.dumps([{"id": "R-X-EXAMPLE", "tier": "T3", "title": "예시 규칙", "pattern": "OldVendorCall\\s*\\("}],
                                                               ensure_ascii=False).encode("utf-8"))
mock = Mock(lambda user: "NO_CHANGE")
c0, t0 = cli("run", "--package", pkg, "--from-vs", "2010", "--to-vs", "2022", "--no-compile", "--until", "S4", "--out", os.path.join(work, "ex_seed"))
run_seed = [b for b, _d, fs in os.walk(os.path.join(work, "ex_seed")) if "state.db" in fs][0]
fill = {"PKG": pkg, "OUT": os.path.join(work, "ex_out"), "ASKFILE": os.path.join(vdir, "config", "EXAMPLE_asks.txt"),
        "REQ": os.path.join(vdir, "config", "requests", "EXAMPLE.json"), "SERVER": os.path.join(vdir, "config", "server.json"),
        "RULES": os.path.join(work, "ex_rules.json"), "EXC": os.path.join(vdir, "config", "EXAMPLE_exceptions.json"), "RUN": run_seed}
ran, bad_run = 0, []
for _title, rows in V.USAGE:
    for _opt, _case, cmd, _note in rows:
        full = V._ex_full(cmd)
        argv = shlex.split(full.replace("\\", "\\\\"))[1:]
        if argv[0] in ("llmcheck", "selftest"):
            continue                                        # 서버 점검과 자체 시험은 여기서 돌리지 않는다(형식만 위에서 확인)
        argv = [mock.url if a.startswith("http://10.") else a for a in argv]
        argv = [a.format(**fill) if "{" in a else a for a in argv]
        if argv[0] == "run":
            if "--out" not in argv and "--resume" not in argv:
                argv += ["--out", os.path.join(work, "ex_out")]
            if "--resume" not in argv:
                argv += ["--no-llm", "--no-compile"]        # 예시의 옵션이 받아들여지는지만 본다(서버와 컴파일러는 쓰지 않는다)
                if "--until" not in argv and "--dry-run" not in argv:
                    argv.append("--dry-run")
        code, text = cli(*argv)
        ran += 1
        if code not in (0, 1, 4) or "입력 오류" in text:
            bad_run.append((cmd, code, text[-300:]))
mock.close()
if bad_run:
    print("실행하지 못한 예:", bad_run)
check("사용 예: 실행할 수 있는 명령 %d개를 실제로 돌려 입력 오류나 실패가 없음" % ran, ran >= 50 and not bad_run)
doc = os.path.join(vdir, "docs", "23_옵션별_사용예시.md")
doc = doc if os.path.isfile(doc) else os.path.join(vdir, "23_옵션별_사용예시.md")
check("사용 예: 문서 23 의 표가 vupdate examples --md 의 출력과 같음",
      os.path.isfile(doc) and V.usage_text(md=True).strip() in open(doc, encoding="utf-8").read())
c_ex, t_ex = cli("examples")
check("사용 예: vupdate examples 가 옵션별 예를 내고, run --help 가 --compat-llm 의 세 단계를 설명함",
      c_ex == 0 and "--compat-llm 3" in t_ex and "[환경 호환성 항목의 처리 깊이]" in t_ex and "1(기본)" in cli("run", "--help")[1] and "기계적 변경만" in t_ex)

ok = sum(1 for _n, c in checks if c)
for name, cond in checks:
    print("[%s] %s" % ("통과" if cond else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), ok))
sys.exit(0 if ok == len(checks) else 1)
