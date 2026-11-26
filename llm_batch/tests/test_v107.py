"""0.10.7 의 시험: 요청문의 이름을 단계별로 넓혀 찾는가(--ask-match), sLLM 단계의 진행이 로그에 남는가.

사용: python test_v107.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 단계 1(기본): 정확히 같은 이름만. 2: 대소문자만 다른 이름. 3: 그 낱말이 든 모든 이름(낱말 검색도 모든 이름으로). 4: 그 글자가 든 이름
  2. 넓혀 찾은 이름과 그 수가 로그와 추적 파일에 남는다. 많으면 많이 쓰인 것만 대상으로 하고 그 사실을 적는다
  3. 넓혀 찾은 이름을 표 변환(배열을 구조체로)의 대상으로 삼지 않는다
  4. 옵션 값 검사, 설정값(limits.ask_match_level)
  5. sLLM 단계: 응답을 받는 대로 진행 상황을 적고, 받은 답을 바로 캐시에 넣는다
  6. 3단계의 남은 경고 줄만 든 작업은 답이 걸러져도 다시 묻지 않는다
모델은 이 파일 안의 모의 서버다. 실제 모델이 어떤 이름을 내는지는 이 시험으로 확인되지 않는다.
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
typedef struct _MES_TC { char szTc[5]; char szLot[10]; } MES_TC;
void SendTc(const char* Data, int nLen);
"""
SRC = """#include "Def.h"
int g_nTcTable[4];
int ParseTc(const char* Data, char* pOut)
{
    char TC[6];
    MES_TC st;
    int nBatch = 0, nLotCount = 1;
    memcpy(TC, Data, 5);
    memcpy(st.szTc, Data, 5);
    memcpy(st.szLot, Data + 5, 10);
    g_nTcTable[0] = 1;
    g_nTcTable[1] = nBatch + nLotCount;
    strcpy(pOut, TC);
    return (int)strlen(TC);
}
"""
pkg = os.path.join(work, "PKG")
for rel, t in (("Src/Def.h", HDR), ("Src/Proc.cpp", SRC)):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), t.replace("\n", "\r\n").encode("cp949"))
AN = an({"type": "modify", "instruction": "tc 처리를 고친다", "names": ["tc"]},
        {"type": "modify", "instruction": "lot 을 다루는 곳을 고친다", "names": [], "words": ["lot"]},
        {"type": "modify", "instruction": "bat 을 고친다", "names": ["bat"]})
prompts = []


def keep_prompt(user):
    prompts.append(user)
    return "NO_CHANGE"


res = {}
for lv in (1, 2, 3, 4):
    m = Mock(AN, keep_prompt)
    res[lv] = run("lv%d" % lv, pkg, m, ask=["tc 처리와 lot 처리를 고친다"], compat_llm=1, ask_match=str(lv), until="S5")
    m.close()
    res[lv]["intents"] = json.loads(res[lv]["plan"])["asks"][0]["intents"] if res[lv]["plan"] else [{}, {}, {}]
n1, n2, n3, n4 = [res[k]["intents"][0].get("names") or [] for k in (1, 2, 3, 4)]
check("단계 1(기본): 정확히 같은 이름만. tc 는 코드에 없어 버림", n1 == [] and res[1]["intents"][0].get("names_dropped") == ["tc"]
      and "요청문의 이름을 찾는 범위" not in res[1]["log"])
check("단계 2: 대소문자만 다른 이름까지(tc -> TC)", n2 == ["TC"] and not res[2]["intents"][0].get("names_dropped"))
check("단계 3: 그 낱말이 든 모든 이름까지(tc -> TC, szTc, ParseTc, SendTc, MES_TC, g_nTcTable). 좁은 단계에서 찾은 것이 앞에 옴",
      n3[0] == "TC" and set(n3) == {"TC", "szTc", "ParseTc", "SendTc", "MES_TC", "_MES_TC", "g_nTcTable"})
check("단계 4: 그 글자가 든 이름까지. 2글자 이름(tc)은 글자 포함으로 넓히지 않고, 3글자 이름(bat)은 넓힘(nBatch)",
      set(n4) == set(n3) and (res[4]["intents"][2].get("names") or []) == ["nBatch"] and (res[3]["intents"][2].get("names") or []) == [])
w1, w3 = res[1]["intents"][1], res[3]["intents"][1]
check("낱말 검색: 단계 1, 2 는 함수 이름만 보아 lot 을 못 찾고, 단계 3 은 변수와 필드까지 보아 찾음(szLot, nLotCount)",
      not (w1.get("targets") or []) and "대상을 찾지 못함" in (w1.get("result") or "") and set(w3.get("targets") or []) == {"szLot", "nLotCount"})
check("기록: 넓혀 찾은 이름, 맞춘 방식, 쓰인 횟수가 로그와 추적 파일에 남음",
      "이름 tc 은(는) 코드에 없어 넓혀 찾음(--ask-match 3): TC(대소문자만 다름, " in res[3]["log"] and "szTc(낱말이 같음, " in res[3]["log"]
      and "- 넓혀 찾은 이름(--ask-match 3): tc -> TC(대소문자만 다름" in res[3]["trace"] and "- 이름을 찾는 범위(--ask-match): 3(그 낱말이 든 모든 이름까지)" in res[3]["trace"])

# 많이 찾으면 많이 쓰인 것만 대상으로 하고 그 사실을 적는다(설정 limits.ask_widen_max)
keep_cap = V.DEFAULT_CONFIG["limits"].get("ask_widen_max") if hasattr(V, "DEFAULT_CONFIG") else None
m = Mock(AN, keep_prompt)
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "cap"), pkg, m.url, from_vs="2010", no_compile=True, compat_llm=1, ask=["tc"], ask_match="3", until="S5"))
ctx.cfg["limits"]["ask_widen_max"] = 3
try:
    V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    capped = ctx.store.get_meta("ask_plan", [])[0]["intents"][0]
finally:
    ctx.store.close()
    V._SINK.close()
m.close()
check("넓혀 찾은 이름이 상한을 넘으면 많이 쓰인 것만 대상으로 하고 전체 수를 적음",
      len(capped["names"]) == 3 and capped["names"][0] == "TC" and "[찾은 이름 7개 가운데 많이 쓰인 3개만 대상]" in capped["names_widened"]["tc"])

# 설정값으로도 줄 수 있다(옵션이 없을 때)
m = Mock(AN, keep_prompt)
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "cfg"), pkg, m.url, from_vs="2010", no_compile=True, compat_llm=1, ask=["tc"], until="S5"))
ctx.cfg["limits"]["ask_match_level"] = 2
try:
    V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    by_cfg = ctx.store.get_meta("ask_plan", [])[0]["intents"][0]["names"]
finally:
    ctx.store.close()
    V._SINK.close()
m.close()
check("설정 limits.ask_match_level 로도 단계를 줄 수 있음(옵션이 없을 때)", by_cfg == ["TC"])

# ------------------------------------------------------------------ 3. 넓혀 찾은 이름은 표 변환의 대상이 아니다
m = Mock(an({"type": "array_to_struct", "instruction": "tc 표를 구조체로 바꾼다", "names": ["tc"]}), keep_prompt)
r3 = run("arr", pkg, m, ask=["tc 표를 구조체로 바꾼다"], compat_llm=1, ask_match="3")
m.close()
check("넓혀 찾은 이름(g_nTcTable)을 배열 -> 구조체 변환의 대상으로 삼지 않음(선언이 그대로이고 그 변환의 수정 레코드가 없음)",
      "int g_nTcTable[4];" in (changed(r3, "Src/Proc.cpp") or SRC) and not [e for e in r3["edits"] if "g_nTcTable_T" in (e["after"] or "")])

# ------------------------------------------------------------------ 4. 옵션 값 검사
import subprocess
p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8", "run", "--package", pkg, "--from-vs", "2010", "--to-vs", "2022",
                    "--no-llm", "--no-compile", "--ask-match", "5", "--out", os.path.join(work, "bad")], capture_output=True)
check("--ask-match 에 1~4 밖의 값을 주면 입력 오류(종료 코드 3)", p.returncode == 3 and "--ask-match 는 1, 2, 3, 4 가운데 하나" in (p.stdout + p.stderr).decode("utf-8", "replace"))

# ------------------------------------------------------------------ 5. sLLM 단계의 진행 로그와 캐시
pk5 = os.path.join(work, "PK5")
for k in range(6):
    V.write_bytes(os.path.join(pk5, "Src", "F%d.cpp" % k), ("#include <stdio.h>\nvoid F%d(void)\n{\n    char buf[16];\n    gets(buf);\n}\n" % k).replace("\n", "\r\n").encode("cp949"))
seen_cache = []


def slow_fix(user):
    return "\n".join(block(l, l.replace("gets(buf)", "fgets(buf, sizeof(buf), stdin)")) for l in code_of(user).split("\n") if "gets(buf)" in l) or "NO_CHANGE"


m = Mock(an(), slow_fix)
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "prog"), pk5, m.url, from_vs="2010", no_compile=True, compat_llm=3))
ctx.cfg["limits"]["progress_every"] = 2
try:
    code5 = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    n_cache = ctx.store.one("SELECT COUNT(*) FROM llm_cache")
    run5 = ctx.run_dir
finally:
    ctx.store.close()
    V._SINK.close()
m.close()
log5 = open(os.path.join(run5, "logs", "run.log"), encoding="utf-8").read()
prog = re.findall(r"(\d+)차 요청 진행: (\d+)/(\d+)건 응답\(실패 (\d+)건\), 경과 \d+분 \d+초", log5)
check("sLLM 단계: 응답을 받는 대로 진행 상황을 적음(몇 건 가운데 몇 건, 실패 수, 경과 시간, 남은 예상)",
      len(prog) >= 2 and prog[-1][1] == prog[-1][2] and "남은 예상 약" in log5 and "받은 답을 검사합니다" in log5)
check("sLLM 단계: 받은 답이 캐시에 들어가고 결과는 예전과 같음(gets 6곳이 모두 고쳐짐)", n_cache >= 6 and code5 == 0
      and all("fgets(buf, sizeof(buf), stdin);" in open(os.path.join(run5, "out", "changed", "Src", "F%d.cpp" % k), "rb").read().decode("cp949") for k in range(6)))

# ------------------------------------------------------------------ 6. 남은 경고 줄만 든 작업은 답이 걸러져도 다시 묻지 않는다
pk6 = os.path.join(work, "PK6")
V.write_bytes(os.path.join(pk6, "Src", "P.cpp"), "#include <string.h>\r\nvoid P(char* dst, const char* src)\r\n{\r\n    strcpy(dst, src);\r\n}\r\n".encode("cp949"))
bad_fix = lambda user: block("    strcpy(dst, src);", "    strcpy_s(dst, sizeof(dst), src);") if "strcpy(dst, src);" in user else "NO_CHANGE"
m = Mock(an(), bad_fix)
r6 = run("soft", pk6, m, compat_llm=3)
m.close()
m2 = Mock(an(), bad_fix)
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "soft2"), pk6, m2.url, from_vs="2010", no_compile=True, compat_llm=3))
ctx.cfg["limits"]["soft_retry"] = 1
try:
    V.run_pipeline(ctx, [s for s, _n in V.STAGES])
finally:
    ctx.store.close()
    V._SINK.close()
m2.close()
check("3단계의 남은 경고 줄만 든 작업: 답이 모두 걸러지면 다시 묻지 않고(호출 1회) 사유와 함께 남김. limits.soft_retry 를 1 로 주면 다시 물음",
      m.n_edit == 1 and any("다시 묻지 않음" in (x["note"] or "") and "포인터의 sizeof" in (x["note"] or "") for x in r6["manual"]) and m2.n_edit >= 2)

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
