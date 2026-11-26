"""0.10.6 의 시험: 구조 분석 요청을 처리하다 난 오류가 실행 전체를 멈추지 않는가.

사용: python test_v106.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 버퍼 하나의 계획에서 오류가 나면 그 버퍼만 바꾸지 않고, 같은 요청의 다른 함수는 바뀐다
  2. 요청 하나를 처리하다 오류가 나면 그 요청이 넣은 수정을 모두 되돌리고(일부만 바뀐 결과를 내지 않음), 다른 요청은 처리한다
  3. 오류가 없을 때의 결과는 0.10.5 와 같다(같은 예제에서 수정 레코드가 같다)
오류는 시험이 일부러 넣는다(내부 함수를 바꿔치기한다). sLLM 은 쓰지 않는다.
"""
import json, os, shutil, sys

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


HDR = """#pragma once
#include <string.h>
typedef struct _HDR { char szId[4]; char szLen[6]; char szCode[3]; } HDR;
"""
SRC = """#include "Def.h"
int One(char* p)
{
    HDR* h = (HDR*)p;
    memcpy(h->szId, "AAAA", 4);
    memcpy(p + 4, "000010", 6);
    memcpy(p + 10, "XYZ", 3);
    return 13;
}
int Two(char* p)
{
    HDR* h = (HDR*)p;
    memcpy(h->szId, "BBBB", 4);
    memcpy(p + 4, "000020", 6);
    memcpy(p + 10, "QRS", 3);
    return 13;
}
int Grp(char* pOut)
{
    char szA[3][4];
    int  nA[3];
    int  i, n = 0;
    memset(szA, 0, sizeof(szA));
    memset(nA, 0, sizeof(nA));
    for (i = 0; i < 3; i++) { szA[i][0] = 'a'; nA[i] = i; }
    for (i = 0; i < 3; i++) n += szA[i][0] + nA[i];
    pOut[0] = (char)n;
    return n;
}
"""
pkg = os.path.join(work, "PKG")
for rel, t in (("Src/Def.h", HDR), ("Src/Proc.cpp", SRC)):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), t.replace("\n", "\r\n").encode("cp949"))
req = os.path.join(work, "req.json")
V.write_bytes(req, json.dumps({"features": [{"id": "REQ-BUF", "type": "buffer_fields"}, {"id": "REQ-REC", "type": "record_group"}]}).encode("utf-8"))


def run(tag):
    ctx = V._make_ctx(V._selftest_ns(os.path.join(work, tag), pkg, "", from_vs="2010", no_compile=True, no_llm=True, compat_llm=1, request=req))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        st = ctx.store
        res = {"code": code, "run": ctx.run_dir,
               "edits": sorted((r["ref"], r["start"], r["after"]) for r in st.q("SELECT ref, start, after FROM edits WHERE status IN ('accepted','applied','verified')")),
               "manual": [dict(r) for r in st.q("SELECT rule_id, title, note FROM issues WHERE status='manual'")]}
    except Exception as e:
        res = {"code": 99, "run": ctx.run_dir, "edits": [], "manual": [], "err": repr(e)}
    finally:
        ctx.store.close()
        V._SINK.close()
    for name, rel in (("log", ("logs", "run.log")), ("report", ("out", "report.md")), ("src", ("out", "changed", "Src", "Proc.cpp"))):
        p = os.path.join(res["run"], *rel)
        res[name] = open(p, "rb").read().decode("cp949" if name == "src" else "utf-8", "replace") if os.path.isfile(p) else ""
    return res


base = run("base")
check("기준: 오류가 없을 때 두 함수의 위치 접근과 묶음이 모두 바뀜", "memcpy(h->szLen, \"000010\", 6);" in base["src"] and "memcpy(h->szCode, \"QRS\", 3);" in base["src"]
      and "struct GrpRec" in base["src"] and base["code"] in (0, 1))

# ---- 1. 버퍼 하나의 계획에서 오류
orig_plan = V._sx_plan_buffer


def plan_boom(ctx, f, buf, opts, sx):
    if f["func"] == "One":
        raise KeyError("시험용 오류")
    return orig_plan(ctx, f, buf, opts, sx)


V._sx_plan_buffer = plan_boom
r1 = run("a")
V._sx_plan_buffer = orig_plan
check("버퍼 하나의 오류: 실행이 끝까지 가고, 오류가 난 함수(One)는 그대로 두며 다른 함수(Two)와 묶음은 바뀜",
      r1["code"] in (0, 1) and "memcpy(p + 4, \"000010\", 6);" in r1["src"] and "memcpy(h->szLen, \"000020\", 6);" in r1["src"] and "struct GrpRec" in r1["src"])
check("버퍼 하나의 오류: 로그에 경고가, 수동 검토에 사유가 남음",
      "One() 버퍼 p: 처리 계획을 세우다 실패했습니다(KeyError" in r1["log"] and any("분석 중 오류로 바꾸지 않음" in (x["note"] or "") for x in r1["manual"]))

# ---- 2. 요청 하나를 처리하다 오류(수정을 일부 넣은 뒤에 난다)
orig_hit, n_hit = V._sx_hit, {"n": 0}


def hit_boom(targets, rel, func, names):
    n_hit["n"] += 1
    if func == "Two":
        raise ValueError("시험용 오류 2")
    return orig_hit(targets, rel, func, names)


V._sx_hit = hit_boom
r2 = run("b")
V._sx_hit = orig_hit
check("요청 하나의 오류: 실행이 끝까지 가고, 그 요청이 먼저 넣은 수정(One)도 되돌려 일부만 바뀐 결과를 내지 않음",
      r2["code"] in (0, 1) and "memcpy(p + 4, \"000010\", 6);" in r2["src"] and "memcpy(p + 4, \"000020\", 6);" in r2["src"] and not [e for e in r2["edits"] if e[0] == "REQ-BUF"])
check("요청 하나의 오류: 다른 요청(묶음)은 처리됨", "struct GrpRec" in r2["src"] and [e for e in r2["edits"] if e[0] == "REQ-REC"])
check("요청 하나의 오류: 로그에 오류와 호출 경로가, 수동 검토와 보고서에 '적용하지 않음'이 남음",
      "요청 REQ-BUF (buffer_fields): 구조 분석 요청을 처리하다 오류가 나서 이 요청은 적용하지 않음(ValueError: 시험용 오류 2)" in r2["log"]
      and any("도구의 오류" in (x["title"] or "") for x in r2["manual"]) and "구조 분석 요청을 처리하다 오류가 나서" in r2["report"])

# ---- 3. 오류가 없을 때의 결과는 그대로
again = run("c")
check("오류가 없을 때: 같은 예제를 다시 돌리면 수정 레코드가 같음", again["edits"] == base["edits"] and len(base["edits"]) > 5)

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
