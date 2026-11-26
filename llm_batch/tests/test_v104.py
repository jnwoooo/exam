"""0.10.4 의 시험: 이름 없는 구조체가 있는 패키지에서 S5 가 멈추지 않는가, 기록 단계의 오류가 실행을 멈추지 않는가.

사용: python test_v104.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 이름 없는 구조체(struct { ... } 변수;)와 포인터 별명만 있는 구조체(typedef struct { ... } *PX;)가 있어도 끝까지 돈다.
     sLLM 에 주는 필드 표에는 이름 있는 구조체만 들어간다
  2. 맞는 필드를 가진 구조체가 이름 없는 것뿐이면 그 자리는 보내지 않고 사유를 남긴다
  3. 기록(집계 로그, 추적 파일, 보고서의 덧붙이는 절)을 만들다 오류가 나도 실행과 보고서는 끝까지 간다
  4. (0.10.5) 이름이 없어 뺀 구조체를 조용히 빼지 않고 로그, 결과 문서, 보고서에 남긴다
모델은 이 파일 안의 모의 서버다.
"""
import json, os, re, shutil, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def write(pkg, rel, lines):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), "\r\n".join(lines + [""]).encode("cp949"))


def block(search, replace):
    return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (search, replace)


def code_of(user):
    return user.split("-----BEGIN CODE-----\n", 1)[1].split("\n-----END CODE-----")[0]


class Mock:
    """모의 sLLM. analyze(몇 번째 분석 호출인가, 요청문) 과 edit(요청문) 이 답의 본문을 돌려준다."""

    def __init__(self, analyze, edit):
        self.n_an, self.n_edit = 0, 0
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
                if "[요청 원문]" in user:
                    outer.n_an += 1
                    out = analyze(outer.n_an, user)
                elif "[후보]" in user:
                    out = '{"keep": [1]}'
                else:
                    outer.n_edit += 1
                    out = edit(user)
                self._send({"choices": [{"message": {"content": out}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()




def run(tag, pkg, mock, patch=None, **over):
    base = dict(from_vs="2010", no_compile=True)
    base.update(over)
    ctx = V._make_ctx(V._selftest_ns(os.path.join(work, tag), pkg, mock.url if mock else "", **base))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        st = ctx.store
        res = {"code": code, "run": ctx.run_dir,
               "manual": [dict(r) for r in st.q("SELECT path, line, rule_id, title, note FROM issues WHERE status='manual' ORDER BY id")],
               "edits": [dict(r) for r in st.q("SELECT path, origin, ref, status, before, after FROM edits ORDER BY id")],
               "files": {r["path"]: (r["kind"], r["status"]) for r in st.q("SELECT path, kind, status FROM files")},
               "l3": {r["path"]: (r["result"], r["detail"]) for r in st.q("SELECT path, result, detail FROM verifications WHERE level='L3' ORDER BY id")}}
    finally:
        ctx.store.close()
        V._SINK.close()
    for name, rel in (("log", ("logs", "run.log")), ("trace", ("out", "ask_trace.md")), ("report", ("out", "report.md")), ("plan", ("out", "ask_plan.json")),
                      ("scan", ("out", "struct_scan.md")), ("bk", ("out", "backup_excluded.md"))):
        p = os.path.join(res["run"], *rel)
        res[name] = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""
    return res


def changed(res, rel):
    p = os.path.join(res["run"], "out", "changed", *rel.split("/"))
    return open(p, "rb").read().decode("cp949").replace("\r\n", "\n") if os.path.isfile(p) else ""


def an(*intents):
    return lambda n, user: json.dumps({"intents": [dict({"instruction": "", "names": [], "words": [], "construct": "any", "files": []}, **i) for i in intents]},
                                      ensure_ascii=False)



HDR = """#pragma once
#include <string.h>
%s
typedef struct { char szA[3]; char szB[4]; } *PONLY;
class CProc
{
public:
    %s m_stBody;
    struct { char szTc[5]; char szLot[10]; } m_stLast;
    union { char raw[8]; struct { char szTc[5]; char szLot[3]; } part; } m_u;
    int Parse(const char* pBuf, int nLen);
};
"""
SRC = """#include "Def.h"
int CProc::Parse(const char* pBuf, int nLen)
{
    int nPos = nLen;
    memcpy(m_stBody.szTc, pBuf + nPos, 5);
    nPos += 5;
    memcpy(m_stBody.szLot, pBuf + nPos, 10);
    return nPos + 10;
}

int Fix(CProc* p, const char* pBuf)
{
    memcpy(p->m_stLast.szTc, &pBuf[0], 5);
    memcpy(p->m_stLast.szLot, pBuf + 5, 10);
    return 15;
}
"""
ASK = "버퍼의 위치 접근을 기존 구조체의 필드로 개선한다"
AN = an({"type": "array_to_struct", "instruction": ASK})
prompts = []


def keep_prompt(user):
    prompts.append(user)
    return "NO_CHANGE"


def make(name, named):
    pkg = os.path.join(work, name)
    hdr = HDR % (("typedef struct _MES_BODY { char szTc[5]; char szLot[10]; char szQty[6]; } MES_BODY;", "MES_BODY") if named
                 else ("struct BodyHolder { int n; };", "struct { char szTc[5]; char szLot[10]; char szQty[6]; }"))
    for rel, t in (("Src/Def.h", hdr), ("Src/Proc.cpp", SRC)):
        V.write_bytes(os.path.join(pkg, *rel.split("/")), t.replace("\n", "\r\n").encode("cp949"))
    return pkg


# ------------------------------------------------------------------ 1. 이름 없는 구조체가 섞여 있을 때
m = Mock(AN, keep_prompt)
try:
    r1 = run("a", make("P1", True), m, ask=[ASK], compat_llm=1)
except Exception as e:
    r1 = {"code": 99, "log": repr(e), "report": "", "trace": "", "manual": [], "scan": ""}
m.close()
check("이름 없는 구조체와 포인터 별명만 있는 구조체가 있어도 멈추지 않고 끝까지 돎(보고서와 추적 파일이 나옴)",
      r1["code"] in (0, 1) and "IndexError" not in r1["log"] and r1["report"] and r1["trace"])
check("필드 표에는 이름 있는 구조체만 들어감", m.n_edit >= 1 and any("MES_BODY(크기 21): szTc[0..5) szLot[5..15) szQty[15..21)" in p for p in prompts)
      and not any(re.search(r"(?:^|; |: )\(크기 \d+\)", p) for p in prompts))

check("뺀 구조체를 로그와 보고서에 남김(0.10.5): 어느 파일 몇째 줄의 이름 없는 구조체를 sLLM 에 줄 후보에서 뺐는지",
      "이름 없는 구조체 Src/Def.h:9(필드 szTc, szLot) 를 sLLM 에 줄 구조체 후보에서 뺐습니다" in r1["log"]
      and "이름 없는 구조체 Src/Def.h:9(필드 szTc, szLot)" in r1["report"] and "sLLM 에 줄 구조체 후보에서 뺌(이름으로 가리킬 수 없음)" in r1["report"])

# ------------------------------------------------------------------ 2. 맞는 구조체가 이름 없는 것뿐일 때
prompts[:] = []
m = Mock(AN, keep_prompt)
try:
    r2 = run("b", make("P2", False), m, ask=[ASK], compat_llm=1)
except Exception as e:
    r2 = {"code": 99, "log": repr(e), "report": "", "trace": "", "manual": [], "scan": ""}
m.close()
check("맞는 필드를 가진 구조체가 이름 없는 것뿐이면 그 자리는 sLLM 에 보내지 않고 사유를 남김",
      r2["code"] in (0, 1) and m.n_edit == 0 and "필드 표를 줄 기존 구조체 후보가 없어 sLLM 에 보내지 않음" in r2["scan"]
      and any("위치 모름" in (x["note"] or "") for x in r2["manual"]))

check("뺀 구조체를 결과 문서에 남김(0.10.5): 보내지 않은 사유에 '필드 이름이 맞는 구조체는 있으나 이름이 없다'와 그 위치를 적음",
      "필드 이름이 맞는 구조체는 있으나 이름이 없다: Src/Def.h:" in r2["scan"] and "이름을 붙이면 대상이 된다" in r2["scan"]
      and "를 sLLM 에 줄 구조체 후보에서 뺐습니다" in r2["log"])
check("규칙 쪽도 남김(0.10.5): 배치가 맞는 구조체가 이름 없는 것뿐이면 사유에 그 위치를 적고 로그에 남김",
      "배치가 맞는 구조체는 있으나 이름이 없어 쓰지 못함: Src/Def.h:" in r2["scan"] and "를 대응시킬 구조체 후보에서 뺐습니다" in r2["log"])
check("같은 구조체를 같은 자리에서 뺀 로그는 한 번만 적음",
      r1["log"].count("이름 없는 구조체 Src/Def.h:9(필드 szTc, szLot) 를 sLLM 에 줄 구조체 후보에서 뺐습니다") == 1)

# ------------------------------------------------------------------ 3. 기록 단계의 오류
keep = (V._stage_tally, V._guarantee_lines, V._write_ask_trace)


def boom(*a, **k):
    raise RuntimeError("시험용 오류")


V._stage_tally = V._write_ask_trace = boom
m = Mock(AN, keep_prompt)
try:
    r3 = run("c", make("P3", True), m, ask=[ASK], compat_llm=1)
except Exception as e:
    r3 = {"code": 99, "log": repr(e), "report": "", "trace": "", "manual": [], "scan": ""}
m.close()
V._stage_tally, V._guarantee_lines, V._write_ask_trace = keep
check("기록(집계 로그, 추적 파일, 보고서의 단계별 집계)을 만들다 오류가 나도 실행과 보고서는 끝까지 감(경고를 남김)",
      r3["code"] in (0, 1) and "## 4. 이슈(탐지 결과)" in r3["report"] and "## 8. 남은 일" in r3["report"]
      and "만들지 못했습니다(RuntimeError: 시험용 오류). 나머지 결과에는 영향이 없습니다" in r3["log"])
V.find_tools = lambda vs_path="", sdk_path="": {"platform": "win32(모의)", "cl_all": ["fake"], "cl_modern": "fake-cl.exe", "cl_2008": "", "sdk_math_h": "fake",
                                                  "mfc_afxwin_h": "", "vs_root": "fake", "vsdevcmd": "fake", "cl_in_path": "", "vs_path_given": vs_path}
V.smoke_test_compiler = lambda ctx: (True, "정상(모의 컴파일러)")
V.get_vs_env = lambda tools, arch="x86": {"PATH": ""}
V.run_cl_syntax = lambda tools, src_abs, cwd, flags, timeout, arch="x86": (True, [], "")
V._guarantee_lines = boom
try:
    r4 = run("d", make("P4", True), None, no_llm=True, no_compile=False, compat_llm=1)
except Exception as e:
    r4 = {"code": 99, "log": repr(e), "report": ""}
V._guarantee_lines = keep[1]
check("보장 범위의 문장을 만들다 오류가 나면 고정 문장으로 되돌아가지 않고 '만들지 못했다'고 적음",
      "보장 범위의 문장을 만들지 못했다(오류)" in r4["report"] and "새 컴파일 오류가 없음을 확인했다" not in r4["report"])

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
