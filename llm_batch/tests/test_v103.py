"""0.10.3 의 시험: 실코드 시험(문서 27)에서 나온 문제의 수정과, 규칙이 못 정한 자리를 sLLM 에 맡기는 흐름.

사용: python test_v103.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 요청 연결(일반 낱말이 지역 글자 버퍼와 같은 이름일 때), 클래스 멤버 구조체의 sizeof, _s 함수, 값으로 채우는 묶음,
     규칙이 확정하지 못한 자리를 sLLM 에 보내는 것, 변환 전후의 동작(g++ 가 있을 때)
  2. 글자 버퍼를 글자 필드의 구조체로 쪼개지 않는다
  3. 구조 분석과 표 변환의 대상이 없으면 이름이 쓰인 줄을 일반 수정 요청으로 보낸다
  4. --compat-llm 3: 남은 사용 중단 경고 줄을 sLLM 에 보내고, 도구가 확인할 수 있는 꼴의 수정만 받는다
  5. 컴파일 검사: 패키지 밖을 가리키는 include 경로, 보고서의 보장 문구, 막힌 헤더의 이름과 분류(모의 컴파일러)
  6. 백업 제외: 되살린 헤더가 include 하는 헤더
모델은 이 파일 안의 모의 서버이고 컴파일러(cl)도 모의다. 실제 sLLM 의 응답 품질과 실제 cl 의 동작은 이 시험으로 확인되지 않는다.
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


# ------------------------------------------------------------------ 1. 요청 연결, 멤버 구조체의 sizeof, _s 함수, 값으로 채우는 묶음, 남은 자리의 sLLM 전달
DEF_H = """#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _MSC_VER
#define memcpy_s(d, ds, s, n) memcpy(d, s, n)
#endif
#define MAX_ITEM 4
typedef struct _MES_BODY { char szTc[5]; char szLot[10]; char szQty[6]; } MES_BODY;
typedef struct _MES_INSP { char szLot[12]; char szItem[10]; char szResult[2]; } MES_INSP;
struct ItemDto { char lot[11]; char item[11]; char flag; int qty; };
class CMesProc
{
public:
    int      m_nIdx;
    int      m_nCount;
    MES_INSP m_stInsp;
    MES_BODY m_stBody;
    ItemDto  m_dto[MAX_ITEM];
    int  ParseInsp(const char* pBuf);
    int  ParseTc(const char* pBuf, char* pOut);
    int  BuildList(char* pOut);
    int  BuildPair(char* pOut);
    int  CopySafe(char* pBuf, const char* Data);
    int  ParseVar(const char* pBuf, int nLen);
};
"""
PROC = """#include "MesDef.h"

int CMesProc::ParseInsp(const char* pBuf)
{
    int nPos = 0;
    memcpy(m_stInsp.szLot, pBuf + nPos, sizeof(m_stInsp.szLot));
    nPos += sizeof(m_stInsp.szLot);
    memcpy(m_stInsp.szItem, pBuf + nPos, sizeof(m_stInsp.szItem));
    nPos += sizeof(m_stInsp.szItem);
    memcpy(m_stInsp.szResult, pBuf + nPos, sizeof(m_stInsp.szResult));
    nPos += sizeof(m_stInsp.szResult);
    return nPos;
}

int CMesProc::ParseTc(const char* pBuf, char* pOut)
{
    char TC[6];
    memcpy(TC, pBuf, 5);
    TC[5] = 0;
    strcpy(pOut, TC);
    return (int)strlen(TC);
}

int CMesProc::BuildList(char* pOut)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    char cFlag[MAX_ITEM];
    int  nQty[MAX_ITEM];
    int  nPos = 0;
    memset(szLot, ' ', sizeof(szLot));
    memset(szItem, ' ', sizeof(szItem));
    memset(cFlag, ' ', sizeof(cFlag));
    for (m_nIdx = 0; m_nIdx < m_nCount; m_nIdx++)
    {
        memcpy(szLot[m_nIdx], m_dto[m_nIdx].lot, strlen(m_dto[m_nIdx].lot));
        memcpy(szItem[m_nIdx], m_dto[m_nIdx].item, strlen(m_dto[m_nIdx].item));
        cFlag[m_nIdx] = m_dto[m_nIdx].flag;
        nQty[m_nIdx] = m_dto[m_nIdx].qty;
    }
    for (m_nIdx = 0; m_nIdx < MAX_ITEM; m_nIdx++)
    {
        memcpy(pOut + nPos, szLot[m_nIdx], 10);
        nPos += 10;
        memcpy(pOut + nPos, szItem[m_nIdx], 10);
        nPos += 10;
        pOut[nPos++] = cFlag[m_nIdx];
        nPos += sprintf(pOut + nPos, "%06d", m_nIdx < m_nCount ? nQty[m_nIdx] : 0);
    }
    return nPos;
}

int CMesProc::BuildPair(char* pOut)
{
    char szA[MAX_ITEM][4];
    char szB[MAX_ITEM][4];
    int  i, nPos = 0;
    memset(szA, '-', sizeof(szA));
    memset(szB, '-', sizeof(szB));
    for (i = 0; i < m_nCount; i++)
    {
        szA[i][0] = 'A';
        szB[i][1] = 'B';
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szA[i], 4);
        nPos += 4;
        memcpy(pOut + nPos, szB[i], 4);
        nPos += 4;
    }
    return nPos;
}

int CMesProc::CopySafe(char* pBuf, const char* Data)
{
    MES_BODY* pBody = (MES_BODY*)pBuf;
    memcpy(pBody->szTc, Data, 5);
    memcpy_s(pBuf + 5, 10, Data, 10);
    memcpy_s(pBuf + 15, 6, Data, 6);
    return 21;
}

int CMesProc::ParseVar(const char* pBuf, int nLen)
{
    int nPos = nLen;
    memcpy(m_stBody.szTc, pBuf + nPos, 5);
    nPos += 5;
    memcpy(m_stBody.szLot, pBuf + nPos, 10);
    return nPos + 10;
}
"""
MAIN = """#include "MesDef.h"
int main()
{
    CMesProc p;
    char out[512], in[64];
    int i, n;
    memset(&p, 0, sizeof(p));
    for (i = 0; i < 64; i++) in[i] = (char)('a' + i % 26);
    p.m_nCount = 3;
    for (i = 0; i < 3; i++) { sprintf(p.m_dto[i].lot, "LOT%d", i); sprintf(p.m_dto[i].item, "IT%d", i * 7); p.m_dto[i].flag = (char)('X' + i); p.m_dto[i].qty = 100 + i; }
    n = p.BuildList(out); printf("%d [%.*s]\\n", n, n, out);
    n = p.BuildPair(out); printf("%d [%.*s]\\n", n, n, out);
    n = p.ParseInsp(in); printf("%d [%.12s][%.10s][%.2s]\\n", n, p.m_stInsp.szLot, p.m_stInsp.szItem, p.m_stInsp.szResult);
    memset(out, '.', 32); n = p.CopySafe(out, in); printf("%d [%.21s]\\n", n, out);
    n = p.ParseVar(in, 3); printf("%d [%.5s][%.10s]\\n", n, p.m_stBody.szTc, p.m_stBody.szLot);
    n = p.ParseTc(in, out); printf("%d [%s]\\n", n, out);
    return 0;
}
"""
pkg = os.path.join(work, "PKG")
for rel, text in (("Src/MesDef.h", DEF_H), ("Src/MesProc.cpp", PROC), ("Src/Main.cpp", MAIN)):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), text.replace("\n", "\r\n").encode("cp949"))
ASK = "data의 배열 및 숫자 오프셋 기반 처리를 기존 tc구조체와 필드 접근 기반으로 개선한다 송수신 경계의 바이트 버퍼는 필요하면 유지하고 내부 처리에서 구조체를 사용한다"
prompts = []


def keep_prompt(user):
    prompts.append(user)
    return "NO_CHANGE"


m = Mock(an({"type": "array_to_struct", "instruction": "Data 의 배열 및 숫자 오프셋 기반 처리를 기존 TC 구조체와 필드 접근 기반으로 개선한다", "names": ["Data", "TC"]},
            {"type": "constraint", "instruction": "송수신 경계의 바이트 버퍼는 필요하면 유지하고 내부 처리에서 구조체를 사용한다"}), keep_prompt)
r = run("a", pkg, m, ask=[ASK], compat_llm=1)
m.close()
src = changed(r, "Src/MesProc.cpp")
check("요청 연결: 일반 낱말 TC 가 지역 글자 버퍼의 이름과 같아도 구조 분석으로 넘어감(계획에 -buf, -rec 요청이 생기고 글자 버퍼는 참고로만 적힘)",
      "ASK-1-1-buf" in r["plan"] and "ASK-1-1-rec" in r["plan"] and "글자 버퍼로 보임" in r["plan"] and "char TC[6];" in src and "TC_T" not in src
      and not any(x["title"] == "요청의 대상을 찾지 못함" for x in r["manual"]))
check("멤버 구조체의 sizeof: 클래스 멤버 m_stInsp 의 타입을 클래스 정의에서 찾아 위치와 길이를 확정하고 로컬 구조체 복사로 바꿈",
      "MES_INSP msg_pBuf;" in src and "memcpy(&msg_pBuf, pBuf, sizeof(msg_pBuf));" in src
      and "memcpy(m_stInsp.szLot, msg_pBuf.szLot, sizeof(m_stInsp.szLot));" in src and "memcpy(m_stInsp.szResult, msg_pBuf.szResult, sizeof(m_stInsp.szResult));" in src)
check("_s 함수: memcpy_s 로 쓰는 위치 접근을 읽어 기존 겹쳐 보기 포인터의 필드로 바꿈",
      "memcpy_s(pBody->szLot, 10, Data, 10);" in src and "memcpy_s(pBody->szQty, 6, Data, 6);" in src)
check("값으로 채우는 묶음(일부만 채움): 공백으로 채우는 문장 3개를 레코드마다 도는 반복문 하나로 바꾸고 묶음을 구조체의 배열로",
      "struct BuildListRec" in src and "} stRec[MAX_ITEM];" in src and "for (int iRec = 0; iRec < (MAX_ITEM); iRec++)" in src
      and "memset(stRec[iRec].szLot, ' ', sizeof(stRec[iRec].szLot));" in src and "memset(&stRec[iRec].cFlag, ' ', sizeof(stRec[iRec].cFlag));" in src
      and "memset(szLot" not in src and "stRec[m_nIdx].nQty = m_dto[m_nIdx].qty;" in src)
check("값으로 채우는 묶음(모두 같은 값): 문장 하나로", "struct BuildPairRec" in src and src.count("memset(stRec, '-', sizeof(stRec));") == 1 and "memset(szA" not in src)
llm_issues = [e for e in prompts if "pBuf + nPos" in e and "ParseVar" in e]
check("남은 자리: 규칙이 위치를 확정하지 못한 접근(ParseVar)을 sLLM 에 보내고, 물음에 요청문과 기존 구조체의 필드 표를 넣음",
      m.n_edit >= 1 and llm_issues and "MES_BODY(크기 21): szTc[0..5) szLot[5..15) szQty[15..21)" in llm_issues[0]
      and "기존 TC 구조체와 필드 접근 기반으로 개선한다" in llm_issues[0] and "새 포인터나 캐스팅을 추가하지 않는다" in llm_issues[0]
      and "송수신 경계의 바이트 버퍼는 필요하면 유지" in llm_issues[0])
check("남은 자리: 집계에 sLLM 수정 요청 2곳이 잡히고, 모델이 그대로 둔 곳은 규칙이 남긴 사유(위치 모름)와 함께 미수정으로 남음",
      "요청 ASK-1-1 [array_to_struct] 집계: " in r["log"] and "sLLM 수정 요청 2(보낸 것 2" in r["log"] and "-> sLLM 에 수정 요청" in r["scan"] and "| 2곳 |" in r["report"]
      and sum(1 for x in r["manual"] if x["rule_id"] == "ASK-1-1-buf" and "sLLM 이 수정 불필요로 판단" in (x["note"] or "") and "규칙이 남긴 사유: 위치 모름" in (x["note"] or "")) == 2)
if shutil.which("g++"):
    outs = []
    for label, text in (("원본", PROC), ("변환", src)):
        d = os.path.join(work, "run_" + label)
        os.makedirs(d, exist_ok=True)
        for name, t in (("MesDef.h", DEF_H), ("MesProc.cpp", text), ("Main.cpp", MAIN)):
            open(os.path.join(d, name), "w", encoding="utf-8").write(t)
        import subprocess
        p = subprocess.run(["g++", "-w", "-o", os.path.join(d, "t"), os.path.join(d, "Main.cpp"), os.path.join(d, "MesProc.cpp")], capture_output=True)
        outs.append(subprocess.run([os.path.join(d, "t")], capture_output=True).stdout.decode() if p.returncode == 0 else "컴파일 실패: " + p.stderr.decode()[:300])
    check("동작: 변환한 결과를 g++ 로 컴파일해 실행한 출력이 원본과 같음", outs[0] and not outs[0].startswith("컴파일 실패") and outs[0] == outs[1])
    if outs[0] != outs[1]:
        print(outs[0][:400], "\n----\n", outs[1][:400])
else:
    print("[건너뜀] g++ 가 없어 변환 전후의 동작 비교는 하지 않음")

# 요청에 llm: off 를 주면 남은 자리를 보내지 않는다
req = os.path.join(work, "req.json")
V.write_bytes(req, json.dumps({"features": [{"id": "REQ-BUF", "type": "buffer_fields", "llm": "off"}]}).encode("utf-8"))
m = Mock(an(), keep_prompt)
r_off = run("a2", pkg, m, request=req, compat_llm=1)
m.close()
check("요청 파일의 llm: off: 남은 자리를 sLLM 에 보내지 않고 사유와 함께 수동 검토로 남김", m.n_edit == 0 and any("위치 모름" in (x["note"] or "") for x in r_off["manual"]))

# ------------------------------------------------------------------ 2. 글자 버퍼는 표 변환의 대상이 아니다 / 3. 대상이 없으면 이름이 쓰인 줄을 일반 수정으로
pk2 = os.path.join(work, "PK2")
V.write_bytes(os.path.join(pk2, "Src", "One.cpp"), ("#include <string.h>\nint Calc(int Data, int nTc)\n{\n    char TC[6];\n    TC[5] = 0;\n    TC[0] = (char)('0' + nTc);\n"
                                                    "    return Data + (int)strlen(TC);\n}\n").replace("\n", "\r\n").encode("cp949"))
m = Mock(an({"type": "array_to_struct", "instruction": "TC 배열을 구조체로 바꾼다", "names": ["TC"]}), keep_prompt)
r2 = run("b", pk2, m, ask=["TC 배열을 구조체로 바꾼다"], compat_llm=1)
m.close()
check("글자 버퍼: 숫자 인덱스를 쓰는 1차원 글자 배열을 글자 필드의 구조체로 쪼개지 않음(수정 0건, 사유를 남김)",
      not r2["edits"] and m.n_edit == 0 and any("글자 버퍼로 보임" in (x["note"] or "") for x in r2["manual"]))
prompts[:] = []
m = Mock(an({"type": "array_to_struct", "instruction": "Data 의 처리를 구조체 기반으로 개선한다", "names": ["Data"]}), keep_prompt)
r3 = run("c", pk2, m, ask=["Data 의 처리를 구조체 기반으로 개선한다"], compat_llm=1)
m.close()
check("대상이 없을 때: 구조 분석과 표 변환의 대상이 없으면 요청문의 이름이 쓰인 줄을 일반 수정 요청으로 sLLM 에 보냄",
      m.n_edit >= 1 and "일반 수정 요청으로 sLLM 에 맡김" in r3["plan"] and any("return Data + (int)strlen(TC);" in p for p in prompts))

# ------------------------------------------------------------------ 4. 3단계: 남은 사용 중단 경고 줄
pk4 = os.path.join(work, "PK4")
V.write_bytes(os.path.join(pk4, "Src", "Leg.h"), "#pragma once\nclass CLeg\n{\npublic:\n    char m_szName[32];\n    char* m_pName;\n    void Set(const char* p, char* pOut, int n);\n};\n".replace("\n", "\r\n").encode("cp949"))
LEG = ["#include <stdio.h>", "#include <stdlib.h>", "#include <string.h>", "#include \"Leg.h\"", "void CLeg::Set(const char* p, char* pOut, int n)", "{",
       "    char szNum[16];", "    itoa(n, szNum, 10);", "    strcpy(m_szName, p);", "    strcpy(m_pName, p);", "    strcpy(pOut, szNum);", "    strcat(pOut, \"!\");", "}"]
V.write_bytes(os.path.join(pk4, "Src", "Leg.cpp"), "\r\n".join(LEG + [""]).encode("cp949"))
FIX = {"    _itoa(n, szNum, 10);": "    _itoa_s(n, szNum, sizeof(szNum), 10);", "    strcpy(m_szName, p);": "    strcpy_s(m_szName, sizeof(m_szName), p);",
       "    strcpy(m_pName, p);": "    strcpy_s(m_pName, sizeof(m_pName), p);", "    strcpy(pOut, szNum);": "    strcpy_s(pOut, sizeof(pOut), szNum);"}


def leg_fix(user):
    prompts.append(user)
    return "\n".join(block(a, b) for a, b in FIX.items() if a in code_of(user).split("\n")) or "NO_CHANGE"


prompts[:] = []
m = Mock(an(), leg_fix)
r4 = run("d", pk4, m, compat_llm=3, deprecated="fix")
m.close()
leg = changed(r4, "Src/Leg.cpp")
check("3단계: 이름만 바꾼 _itoa 와 2단계의 조건에 맞지 않은 보안 CRT 줄을 sLLM 에 보냄(물음의 조각에는 1단계의 이름 변경이 반영돼 있음)",
      m.n_edit >= 1 and any("_itoa(n, szNum, 10);" in p and "strcpy(m_szName, p);" in p and "R-T3-CRT-SECURE-REST" in p for p in prompts))
check("3단계: 선언이 배열임을 확인한 대상(지역 배열, 클래스의 멤버 배열)에 sizeof 를 준 수정은 받음",
      "_itoa_s(n, szNum, sizeof(szNum), 10);" in leg and "strcpy_s(m_szName, sizeof(m_szName), p);" in leg)
check("3단계: 포인터(멤버 포인터, 함수 인자)에 sizeof 를 쓴 수정은 그 블록만 받지 않고 사유와 함께 남김. 모델이 그대로 둔 줄은 수동 검토로 올리지 않음",
      "strcpy(m_pName, p);" in leg and "strcpy(pOut, szNum);" in leg and "strcat(pOut, \"!\");" in leg
      and sum(1 for x in r4["manual"] if x["rule_id"] == "R-T3-CRT-SECURE-REST" and "포인터의 sizeof" in (x["note"] or "")) == 2
      and not any("strcat" in (x["title"] or "") + (x["note"] or "") for x in r4["manual"]))
check("3단계: 집계와 보고서가 3단계의 sLLM 대상이라고 적고, 이름만 바꾼 곳은 보안 CRT 경고가 남는다고 적음",
      re.search(r"3단계 sLLM 수정: 대상 5 / 규칙 수정 0 / sLLM 수정 요청 5\(보낸 것 5, 작업 \d+, 호출 \d+\) / sLLM 수정 2 / 미수정 2 / sLLM 이 고칠 필요가 없다고 본 곳 1", r4["log"]) is not None
      and "3단계의 sLLM 대상이다" in r4["report"] and "POSIX 이름을 밑줄 이름으로 바꾼 1곳 가운데 1곳" in r4["report"])
m = Mock(an(), leg_fix)
r4b = run("d2", pk4, m, compat_llm=2, deprecated="fix")
m.close()
check("2단계: 남은 줄을 보내지 않음(호출 0회, _itoa 는 이름만 바뀐 채 남고 보고서가 경고가 남는다고 적음)",
      m.n_edit == 0 and "_itoa(n, szNum, 10);" in changed(r4b, "Src/Leg.cpp") and "설정으로 경고만 끈다(--compat-llm 3 이면 sLLM 에 보낸다)" in r4b["report"])
r4c = run("d3", pk4, None, compat_llm=3, deprecated="fix", no_llm=True)
check("3단계인데 sLLM 을 쓰지 않을 때: 남은 사용 중단 경고 줄을 수동 검토 목록에 올리지 않음(설정으로 경고가 꺼진다)",
      not any(x["rule_id"] == "R-T3-CRT-SECURE-REST" for x in r4c["manual"]) and "보내지 않음: sLLM 단계를 건너뜀" in r4c["report"] + r4c["log"])

# ------------------------------------------------------------------ 5. 컴파일 검사: include 경로와 보고서의 보장 문구(모의 컴파일러)
tree = os.path.join(work, "tree")
pk5 = os.path.join(tree, "Apps", "PKG")
V.write_bytes(os.path.join(tree, "Common", "Inc", "Shared.h"), b"#pragma once\r\n#define SHARED 1\r\n")
V.write_bytes(os.path.join(pk5, "Src", "Main.cpp"), b'#include <stdlib.h>\r\n#include "Shared.h"\r\nvoid F(char* p, int n)\r\n{\r\n    itoa(n, p, 10);\r\n}\r\n')
V.write_bytes(os.path.join(pk5, "Src", "Blk.cpp"), b'#include <stdlib.h>\r\n#include "vendor_missing.h"\r\nvoid G(char* p, int n)\r\n{\r\n    itoa(n, p, 10);\r\n}\r\n')
V.write_bytes(os.path.join(pk5, "PKG.vcxproj"), ("""<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup Label="ProjectConfigurations"><ProjectConfiguration Include="Release|Win32"><Configuration>Release</Configuration><Platform>Win32</Platform></ProjectConfiguration></ItemGroup>
  <PropertyGroup Label="Configuration" Condition="'$(Configuration)|$(Platform)'=='Release|Win32'"><ConfigurationType>Application</ConfigurationType><PlatformToolset>v100</PlatformToolset></PropertyGroup>
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Release|Win32'"><ClCompile>
    <AdditionalIncludeDirectories>../../Common/Inc;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>
  </ClCompile></ItemDefinitionGroup>
  <ItemGroup><ClCompile Include="Src/Main.cpp" /><ClCompile Include="Src/Blk.cpp" /></ItemGroup>
</Project>
""").encode("utf-8"))


def fake_cl(tools, src_abs, cwd, flags, timeout, arch="x86"):
    dirs = [f[2:] for f in flags if f.startswith("/I")] + [os.path.dirname(src_abs)]
    diags = []
    for no, line in enumerate(open(src_abs, encoding="cp949").read().split("\n"), 1):
        hm = re.match(r'\s*#include "([^"]+)"', line)
        if hm and not any(os.path.isfile(os.path.join(d, hm.group(1))) for d in dirs):
            diags = [{"file": src_abs, "line": no, "kind": "fatal error", "code": "C1083", "msg": "포함 파일을 열 수 없습니다. '%s': No such file or directory" % hm.group(1)}]
            break
    return True, diags, ""


V.find_tools = lambda vs_path="", sdk_path="": {"platform": "win32(모의)", "cl_all": ["fake"], "cl_modern": "fake-cl.exe", "cl_2008": "", "sdk_math_h": "fake",
                                                  "mfc_afxwin_h": "", "vs_root": "fake", "vsdevcmd": "fake", "cl_in_path": "", "vs_path_given": vs_path}
V.smoke_test_compiler = lambda ctx: (True, "정상(모의 컴파일러)")
V.get_vs_env = lambda tools, arch="x86": {"PATH": ""}
V.run_cl_syntax = fake_cl
r5 = run("e", pk5, None, no_llm=True, no_compile=False, deprecated="fix")
rep5 = r5["report"]
check("include 경로: 프로젝트의 include 경로가 패키지 폴더 밖을 가리켜도 원본 위치에서 헤더를 찾아 컴파일 검사를 함",
      r5["l3"].get("Src/Main.cpp", ("", ""))[0].startswith("통과") and r5["l3"].get("Src/Blk.cpp", ("", ""))[0].startswith("미수행"))
check("보장 문구: 실제로 한 검사의 수로 적음(통과 1, 미수행 1). 통과한 것에만 '새 컴파일 오류가 없음'을 씀",
      "변경 파일 2개: 컴파일 검사 통과 1개, 미수행 1개" in rep5 and "통과한 1개는 새 컴파일 오류가 없음을 확인했다" in rep5
      and "나머지 1개의 보장 범위는 정적 검사까지다" in rep5 and "변경 파일에서 새 컴파일 오류가 없음을 확인했다." not in rep5)
check("막힌 헤더: 이름과 분류를 보고서에 적음", "컴파일 검사를 막은 헤더" in rep5 and "| vendor_missing.h | 1 | 패키지와 프로젝트의 include 경로에서 찾지 못했다" in rep5)
os.remove(os.path.join(pk5, "Src", "Main.cpp"))
r5b = run("e2", pk5, None, no_llm=True, no_compile=False, deprecated="fix")
check("보장 문구: 통과한 파일이 없으면 '컴파일 검사로 확인한 변경 파일이 없다'로 적음",
      "**컴파일 검사로 확인한 변경 파일이 없다.**" in r5b["report"] and "새 컴파일 오류가 없음을 확인했다" not in r5b["report"].split("## 3.")[0])

# ------------------------------------------------------------------ 6. 백업 제외: 되살린 헤더가 include 하는 헤더
pk6 = os.path.join(work, "PK6")
for rel, text in (("Src/Main.cpp", '#include "Shared.h"\nint f(void) { return SHARED + BASE; }\n'), ("Inc_bak/Shared.h", '#pragma once\n#include "Base.h"\n#define SHARED 1\n'),
                  ("Inc_bak/Base.h", "#pragma once\n#define BASE 2\n"), ("Inc_bak/Unused.h", "#pragma once\n#define UNUSED 3\n")):
    V.write_bytes(os.path.join(pk6, *rel.split("/")), text.replace("\n", "\r\n").encode("cp949"))
r6 = run("f", pk6, None, no_llm=True)
check("백업 제외: 되살린 헤더가 include 하는 같은 백업 폴더의 헤더도 되살리고, 쓰이지 않는 것만 뺌",
      r6["files"].get("Inc_bak/Shared.h", ("", ""))[0] == "source" and r6["files"].get("Inc_bak/Base.h", ("", ""))[0] == "source"
      and r6["files"].get("Inc_bak/Unused.h", ("", ""))[0] == "backup" and "되살린 헤더가 include 하고" in r6["bk"])

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
