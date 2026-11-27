"""0.10.8 의 시험: 실코드 시험(0.10.5)에서 나온 두 가지 누락.

사용: python test_v108.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 병렬 배열 묶음의 채우는 문장: 값이 상수 매크로인 것(memset(a, SPACE, sizeof(a))), 함수 꼴 매크로로 감싼 것(INIT_SPACE(a)),
     세미콜론 없이 쓴 매크로, 길이를 숫자로 준 것, 값이 서로 다른 것(레코드별 반복문), 못 바꾸는 꼴의 사유, 변환 전후의 동작(g++ 가 있을 때)
  2. 같은 이름의 구조체 정의가 서로 다른 헤더에 있고 구조체 안에 구조체가 정의된 경우: 파일마다 그 파일이 include 하는 정의로
     배치를 계산해 필드 접근으로 바꾸고, sLLM 에 주는 필드 표와 [참고: 선언]에 다른 쪽 정의가 붙지 않는다. 가리지 못하면 사유를 남긴다
  3. 구조체 표의 단위 확인: 안쪽 구조체·공용체·열거형, 이름 없는 안쪽 구조체, 상속, 함수가 든 구조체, 배치가 같은 정의
모델은 이 파일 안의 모의 서버이고 컴파일러는 g++ 다. 실제 sLLM 의 응답 품질과 실제 cl 의 동작은 이 시험으로 확인되지 않는다.
"""
import json, os, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def make_pkg(name, files):
    pkg = os.path.join(work, name)
    for rel, text in files:
        V.write_bytes(os.path.join(pkg, *rel.split("/")), text.replace("\n", "\r\n").encode("cp949"))
    return pkg


def request(name, feats):
    p = os.path.join(work, name)
    V.write_bytes(p, json.dumps({"features": feats}, ensure_ascii=False).encode("utf-8"))
    return p


class Mock:
    """모의 sLLM. 수정 물음에는 edit(물음)이 답을 돌려준다. 받은 물음을 prompts 에 모은다."""

    def __init__(self, edit):
        self.prompts = []
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
                outer.prompts.append(user)
                self._send({"choices": [{"message": {"content": edit(user)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def run(tag, pkg, mock=None, **over):
    base = dict(from_vs="2010", no_compile=True)
    base.update(over)
    ctx = V._make_ctx(V._selftest_ns(os.path.join(work, tag), pkg, mock.url if mock else "", **base))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        st = ctx.store
        res = {"code": code, "run": ctx.run_dir,
               "manual": [dict(r) for r in st.q("SELECT path, line, rule_id, title, note FROM issues WHERE status='manual' ORDER BY id")]}
    finally:
        ctx.store.close()
        V._SINK.close()
    for name, rel in (("log", ("logs", "run.log")), ("report", ("out", "report.md")), ("scan", ("out", "struct_scan.md"))):
        p = os.path.join(res["run"], *rel)
        res[name] = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""
    return res


def changed(res, rel):
    p = os.path.join(res["run"], "out", "changed", *rel.split("/"))
    return open(p, "rb").read().decode("cp949").replace("\r\n", "\n") if os.path.isfile(p) else ""


def gxx(tag, files, sources):
    """files(이름 -> 글)를 폴더에 풀어 g++ 로 컴파일하고 실행한 출력. 컴파일에 실패하면 '컴파일 실패: ...'"""
    d = os.path.join(work, "gxx_" + tag)
    for name, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
        open(os.path.join(d, name), "w", encoding="utf-8", newline="\n").write(text)
    p = subprocess.run(["g++", "-w", "-o", os.path.join(d, "t")] + [os.path.join(d, x) for x in sources], capture_output=True)
    if p.returncode != 0:
        return "컴파일 실패: " + p.stderr.decode("utf-8", "replace")[:800]
    return subprocess.run([os.path.join(d, "t")], capture_output=True, cwd=d).stdout.decode("utf-8", "replace")


def fn_body(src, name):
    """src 에서 함수 name 의 글(머리부터 다음 함수 앞까지)"""
    a = src.find("int %s(" % name)
    b = src.find("\nint ", a + 1)
    return src[a:b if b > 0 else len(src)] if a >= 0 else ""


# ------------------------------------------------------------------ 1. 묶음의 채우는 문장: 상수 매크로와 함수 꼴 매크로
COMMON_H = """#pragma once
#include <stdio.h>
#include <string.h>
#define MAX_ITEM 4
#define SPACE 0x20
#define SP_CH ' '
#define SP2 SPACE
#define NUL 0
#define PAD ' '
#define INIT_SPACE(x) memset((x), 0x20, sizeof(x))   /* 공백으로 채운다 */
#define SET_SPACE(x, n) memset(x, SPACE, n)
#define CLR(x) do { memset(x, 0, sizeof(x)); } while (0)
#define FILL_SP(x) memset(x, ' ', sizeof(x));
#define TWO(x) memset(x, ' ', sizeof(x)); x[0][0] = 'A'
"""
OTHER_H = """#pragma once
#define PAD '0'
enum { cVal = 0x20 };
"""
TAIL = """    for (i = 0; i < nCount; i++)
    {
        szLot[i][0] = 'L';
        szItem[i][1] = 'I';
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szLot[i], 11); nPos += 11;
        memcpy(pOut + nPos, szItem[i], 11); nPos += 11;
    }
    return nPos;
}
"""
FILL_CPP = """#include "Common.h"

int FnA(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    char szCode[MAX_ITEM][5];
    char cFlag[MAX_ITEM];
    int  nQty[MAX_ITEM];
    int  i, nPos = 0;
    memset(szLot, SPACE, sizeof(szLot));
    memset(szItem, SPACE, sizeof(szItem));
    memset(szCode, SPACE, sizeof(szCode));
    memset(cFlag, SPACE, sizeof(cFlag));
    memset(nQty, SPACE, sizeof(nQty));
    for (i = 0; i < nCount; i++)
    {
        szLot[i][0] = 'L';
        szItem[i][0] = 'I';
        szCode[i][0] = 'C';
        cFlag[i] = 'Y';
        nQty[i] = i;
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szLot[i], 11); nPos += 11;
        memcpy(pOut + nPos, szItem[i], 11); nPos += 11;
        memcpy(pOut + nPos, szCode[i], 5); nPos += 5;
        pOut[nPos++] = cFlag[i];
        nPos += sprintf(pOut + nPos, "%08X", nQty[i]);
    }
    return nPos;
}

int FnB(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    INIT_SPACE(szLot);      // 로트
    INIT_SPACE(szItem);
""" + TAIL + """
int FnC(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  nQty[MAX_ITEM];
    int  i, nPos = 0;
    SET_SPACE(szLot, sizeof(szLot));
    SET_SPACE(szItem, sizeof(szItem));
    memset(nQty, NUL, sizeof(nQty));
    for (i = 0; i < nCount; i++)
    {
        szLot[i][0] = 'L';
        szItem[i][1] = 'I';
        nQty[i] = i + 7;
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szLot[i], 11); nPos += 11;
        memcpy(pOut + nPos, szItem[i], 11); nPos += 11;
        nPos += sprintf(pOut + nPos, "%04d", nQty[i]);
    }
    return nPos;
}

int FnD(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    char szCode[MAX_ITEM][5];
    char cFlag[MAX_ITEM];
    int  i, nPos = 0;
    memset(szLot, ' ', sizeof(szLot));
    memset(szItem, 0x20, sizeof(szItem));
    memset(szCode, SP2, sizeof(szCode[0]) * MAX_ITEM);
    memset(cFlag, (char)SP_CH, sizeof(cFlag));
    for (i = 0; i < nCount; i++)
    {
        szLot[i][0] = 'L';
        szItem[i][1] = 'I';
        szCode[i][2] = 'C';
        cFlag[i] = 'Y';
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szLot[i], 11); nPos += 11;
        memcpy(pOut + nPos, szItem[i], 11); nPos += 11;
        memcpy(pOut + nPos, szCode[i], 5); nPos += 5;
        pOut[nPos++] = cFlag[i];
    }
    return nPos;
}

int FnE(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    SET_SPACE(szLot, MAX_ITEM * 11);
    SET_SPACE(szItem, 44);
""" + TAIL + """
int FnF(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char cFlag[MAX_ITEM];
    int  nQty[MAX_ITEM];
    int  i, nPos = 0;
    INIT_SPACE(szLot);
    INIT_SPACE(cFlag);
    for (i = 0; i < MAX_ITEM; i++)
    {
        nQty[i] = i * 3;
        if (i < nCount)
        {
            szLot[i][0] = 'L';
            cFlag[i] = 'Y';
        }
    }
    for (i = 0; i < MAX_ITEM; i++)
    {
        memcpy(pOut + nPos, szLot[i], 11); nPos += 11;
        pOut[nPos++] = cFlag[i];
        nPos += sprintf(pOut + nPos, "%04d", nQty[i]);
    }
    return nPos;
}

int FnG(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    FILL_SP(szLot)
    FILL_SP(szItem)
""" + TAIL + """
int FnH(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    CLR(szLot);
    CLR(szItem);
""" + TAIL + """
int FnX(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    TWO(szLot);
    memset(szItem, SPACE, sizeof(szItem));
""" + TAIL + """
int FnY(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    char cVal = (char)('a' + nCount);
    int  i, nPos = 0;
    memset(szLot, cVal, sizeof(szLot));
    memset(szItem, SPACE, sizeof(szItem));
""" + TAIL + """
int FnZ(char* pOut, int nCount)
{
    char szLot[MAX_ITEM][11];
    char szItem[MAX_ITEM][11];
    int  i, nPos = 0;
    memset(szLot, PAD, sizeof(szLot));
    memset(szItem, PAD, 2);
""" + TAIL
FILL_MAIN = """#include "Common.h"
int FnA(char*, int); int FnB(char*, int); int FnC(char*, int); int FnD(char*, int); int FnE(char*, int); int FnF(char*, int);
int FnG(char*, int); int FnH(char*, int); int FnX(char*, int); int FnY(char*, int);
int main()
{
    int (*fns[])(char*, int) = { FnA, FnB, FnC, FnD, FnE, FnF, FnG, FnH, FnX, FnY };
    char out[1024];
    for (int k = 0; k < 10; k++)
    {
        int n = fns[k](out, 2);
        printf("%d %d [", k, n);
        for (int j = 0; j < n; j++) printf("%02X", (unsigned char)out[j]);
        printf("]\\n");
    }
    return 0;
}
"""
pk1 = make_pkg("FILL", (("Src/Common.h", COMMON_H), ("Src/Other.h", OTHER_H), ("Src/Fill.cpp", FILL_CPP)))
r1 = run("fill", pk1, None, no_llm=True, request=request("req1.json", [{"id": "REQ-REC", "type": "record_group"}]))
src1, scan1 = changed(r1, "Src/Fill.cpp"), r1["scan"]
A, B, C, D, E, F, G, H = [fn_body(src1, "Fn" + x) for x in "ABCDEFGH"]
check("상수 매크로 값(0.10.5 실코드의 꼴): memset(배열, SPACE, sizeof(배열)) 5문장이 든 묶음을 구조체의 배열로 바꾸고 채우는 문장을 하나로",
      "struct FnARec" in A and A.count("memset(stRec, SPACE, sizeof(stRec));") == 1 and "memset(szLot" not in A and "memset(nQty" not in A
      and "stRec[i].nQty = i;" in A and "char cFlag;" in A)
check("함수 꼴 매크로: INIT_SPACE(배열) 은 매크로를 그대로 두고 이름만 바꿈(줄 끝의 주석은 남음)",
      "struct FnBRec" in B and B.count("INIT_SPACE(stRec);") == 1 and "INIT_SPACE(szItem)" not in B and "// 로트" in B)
check("값이 서로 다른 묶음(공백과 0): 레코드별 반복문으로. 배열 필드는 매크로 그대로, 값 하나인 필드는 & 를 붙인 memset 으로",
      "for (int iRec = 0; iRec < (MAX_ITEM); iRec++)" in C and "SET_SPACE(stRec[iRec].szLot, sizeof(stRec[iRec].szLot));" in C
      and "SET_SPACE(stRec[iRec].szItem, sizeof(stRec[iRec].szItem));" in C and "memset(&stRec[iRec].nQty, NUL, sizeof(stRec[iRec].nQty));" in C)
check("값의 글이 달라도 값이 같으면(' ', 0x20, SP2, (char)SP_CH) 한 문장으로. 길이를 sizeof(배열[0]) * 개수로 적은 것도 읽음", "struct FnDRec" in D and D.count("memset(stRec, ' ', sizeof(stRec));") == 1 and "SP2" not in D)
check("길이를 숫자로 준 매크로(SET_SPACE(배열, MAX_ITEM * 11)): 이름만 바꾸면 길이가 틀리므로 펼친 호출로 씀",
      "struct FnERec" in E and E.count("memset(stRec, SPACE, sizeof(stRec));") == 1 and "SET_SPACE" not in E)
check("묶음의 일부만 채우는 매크로: 반복문 안에서 배열 필드는 INIT_SPACE(필드), 글자 하나인 필드는 펼친 memset(&필드, 0x20, ..)",
      "INIT_SPACE(stRec[iRec].szLot);" in F and "memset(&stRec[iRec].cFlag, 0x20, sizeof(stRec[iRec].cFlag));" in F and "INIT_SPACE(cFlag)" not in F)
check("세미콜론 없이 줄마다 쓴 매크로(FILL_SP(배열)): 문장 하나씩으로 읽어 하나로 합침", "struct FnGRec" in G and G.count("FILL_SP(stRec)") == 1 and "FILL_SP(szItem)" not in G)
check("do { } while (0) 로 감싼 0 지우기 매크로(CLR): 하나로", "struct FnHRec" in H and H.count("CLR(stRec);") == 1 and "CLR(szItem)" not in H)
check("못 바꾸는 꼴은 묶음을 그대로 두고 사유를 적음: 문장이 둘인 매크로, 변수 값, 값이 헤더마다 다른 매크로, 일부만 채우는 길이",
      "TWO(szLot);" in src1 and "memset(szLot, cVal, sizeof(szLot));" in src1 and "memset(szLot, PAD, sizeof(szLot));" in src1
      and "매크로 TWO 를 문장 하나로 펼치지 못함" in scan1 and "채우는 값(cVal)이 상수인지 확인하지 못함" in scan1
      and "채우는 값(PAD)이 상수인지 확인하지 못함" in scan1 and "길이(2)가 배열 전체의 크기인지 확인하지 못함" in scan1
      and sum(1 for x in r1["manual"] if "병렬 배열 묶음" in x["title"]) == 3)
check("값의 이름이 이 함수의 지역 변수면 상수로 보지 않음(같은 이름의 enum 항목 cVal = 0x20 이 다른 헤더에 있어도)",
      "memset(szLot, cVal, sizeof(szLot));" in fn_body(src1, "FnY") and "struct FnYRec" not in src1)
check("결과 문서: 값으로 채우는 문장으로 세고, 바꾼 묶음 8개와 바꾸지 않은 묶음 3개가 나옴",
      "값으로 채우는 문장 5" in scan1 and scan1.count("- 결과: 바꿈") == 8 and scan1.count("- 결과: 바꾸지 않음") == 3)
if shutil.which("g++"):
    o0 = gxx("fill0", {"Common.h": COMMON_H, "Fill.cpp": FILL_CPP, "Main.cpp": FILL_MAIN}, ["Main.cpp", "Fill.cpp"])
    o1 = gxx("fill1", {"Common.h": COMMON_H, "Fill.cpp": src1, "Main.cpp": FILL_MAIN}, ["Main.cpp", "Fill.cpp"])
    check("동작(묶음): 변환한 결과를 g++ 로 컴파일해 실행한 출력이 원본과 같음", o0 and not o0.startswith("컴파일 실패") and o0 == o1 and o0.count("\n") == 10)
    if o0 != o1:
        print(o0[:600], "\n----\n", o1[:600])
else:
    print("[건너뜀] g++ 가 없어 변환 전후의 동작 비교는 하지 않음")

# ------------------------------------------------------------------ 2. 같은 이름의 정의가 다른 헤더에 있고, 구조체 안에 구조체가 정의된 경우
MES_H = """#pragma once
#include <stdio.h>
#include <string.h>
typedef struct _TC_HEAD { char szTc[5]; char szSeq[4]; } TC_HEAD;
typedef struct _TC_TAIL { char szCrc[2]; char szEtx[3]; } TC_TAIL;
typedef struct _TC_BODY
{
    TC_HEAD stHead;
    char    szLot[10];
    struct
    {
        char szCode[4];
        char szQty[6];
    } stItem;
    union
    {
        char szRaw[4];
        struct { char szA[2]; char szB[2]; };
    } uExt;
    char    szEnd[2];
} TC_BODY;
extern TC_BODY g_stMes;
"""
PLC_H = """#pragma once
#include <stdio.h>
#include <string.h>
typedef struct _TC_HEAD { char szTc[3]; char szLen[4]; } TC_HEAD;
typedef struct _TC_TAIL { char szCrc[2]; char szEtx[3]; } TC_TAIL;
typedef struct _TC_BODY
{
    TC_HEAD stHead;
    char    szAddr[8];
    char    szVal[8];
} TC_BODY;
extern TC_BODY g_stPlc;
"""
MES_CPP = """#include "MesDef.h"

TC_BODY g_stMes;

int ParseMes(const char* pBuf, char* szLot, char* szCode, char* szQty, char* szEnd)
{
    char szTc[6];
    memcpy(szTc, pBuf, 5);
    szTc[5] = 0;
    memcpy(szLot, pBuf + 9, 10);
    memcpy(szCode, pBuf + 19, 4);
    memcpy(szQty, pBuf + 23, 6);
    memcpy(szEnd, pBuf + 33, 2);
    return (int)strlen(szTc);
}

int PeekMes(const char* pBuf, char* szLot, char* szSeq)
{
    memcpy(szSeq, pBuf + 5, 4);
    memcpy(szLot, pBuf + 9, 10);
    return 14;
}

int MesOverlay(char* pBuf)
{
    TC_BODY* pBody = (TC_BODY*)pBuf;
    memcpy(pBuf + 9, "LOT0000001", 10);
    memcpy(pBuf + 19, "CD01", 4);
    pBody->szEnd[0] = 'E';
    return 35;
}

int MesVar(const char* pBuf, int nLen)
{
    int nPos = nLen;
    memcpy(g_stMes.szLot, pBuf + nPos, 10);
    nPos += 10;
    memcpy(g_stMes.stItem.szCode, pBuf + nPos, 4);
    return nPos + 4;
}
"""
PLC_CPP = """#include "PlcDef.h"

TC_BODY g_stPlc;

int ParsePlc(const char* pBuf, char* szAddr, char* szVal)
{
    char szTc[4];
    memcpy(szTc, pBuf, 3);
    szTc[3] = 0;
    memcpy(szAddr, pBuf + 7, 8);
    memcpy(szVal, pBuf + 15, 8);
    return (int)strlen(szTc);
}

int PlcCopy(const char* pSrc)
{
    strcpy(g_stPlc.szAddr, pSrc);
    return (int)strlen(g_stPlc.szAddr);
}
"""
ETC_CPP = """#include <string.h>
struct _TC_BODY;
struct _TC_TAIL;

int EtcBody(char* pBuf, char* szA, char* szB)
{
    TC_BODY* pBody = (TC_BODY*)pBuf;
    memcpy(szA, pBuf + 9, 10);
    memcpy(szB, pBuf + 19, 4);
    return 0;
}

int EtcTail(char* pBuf, char* szCrc, char* szEtx)
{
    TC_TAIL* pTail = (TC_TAIL*)pBuf;
    memcpy(szCrc, pBuf, 2);
    memcpy(szEtx, pBuf + 2, 3);
    return 5;
}
"""
DUP_MAIN = """#include <stdio.h>
#include <string.h>
int ParseMes(const char*, char*, char*, char*, char*); int PeekMes(const char*, char*, char*); int MesOverlay(char*); int MesVar(const char*, int);
int ParsePlc(const char*, char*, char*); int PlcCopy(const char*);
int main()
{
    char in[64], a[32], b[32], c[32], d[32];
    int i, n;
    for (i = 0; i < 64; i++) in[i] = (char)('a' + i % 26);
    memset(a, 0, 32); memset(b, 0, 32); memset(c, 0, 32); memset(d, 0, 32);
    n = ParseMes(in, a, b, c, d); printf("%d [%.10s][%.4s][%.6s][%.2s]\\n", n, a, b, c, d);
    n = PeekMes(in, a, b); printf("%d [%.10s][%.4s]\\n", n, a, b);
    memset(in, '.', 40); n = MesOverlay(in); printf("%d [%.35s]\\n", n, in);
    for (i = 0; i < 64; i++) in[i] = (char)('A' + i % 26);
    n = MesVar(in, 3); printf("%d\\n", n);
    n = ParsePlc(in, a, b); printf("%d [%.8s][%.8s]\\n", n, a, b);
    n = PlcCopy("ADDR1"); printf("%d\\n", n);
    return 0;
}
"""
pk2 = make_pkg("DUP", (("mes/MesDef.h", MES_H), ("mes/MesProc.cpp", MES_CPP), ("plc/PlcDef.h", PLC_H), ("plc/PlcProc.cpp", PLC_CPP), ("etc/Other.cpp", ETC_CPP)))
mock = Mock(lambda user: "NO_CHANGE")
r2 = run("dup", pk2, mock, request=request("req2.json", [{"id": "REQ-BUF", "type": "buffer_fields", "cast": "add"}]))
mock.close()
mes, plc, etc, scan2 = changed(r2, "mes/MesProc.cpp"), changed(r2, "plc/PlcProc.cpp"), changed(r2, "etc/Other.cpp"), r2["scan"]
check("같은 이름의 정의: 이름을 버리지 않고 파일마다 고른다고 로그에 남김", "같은 이름의 정의가 여러 곳에 있는 이름" in r2["log"] and "TC_BODY 2곳" in r2["log"])
check("mes 쪽 파일: mes 헤더의 정의로 배치를 계산함(안쪽 구조체의 필드, 구조체 타입 멤버의 필드까지). 로컬 복사로 바꿈",
      "TC_BODY msg_pBuf;" in fn_body(mes, "ParseMes") and "memcpy(szTc, msg_pBuf.stHead.szTc, 5);" in mes and "memcpy(szLot, msg_pBuf.szLot, 10);" in mes
      and "memcpy(szCode, msg_pBuf.stItem.szCode, 4);" in mes and "memcpy(szQty, msg_pBuf.stItem.szQty, 6);" in mes and "memcpy(szEnd, msg_pBuf.szEnd, 2);" in mes)
check("mes 쪽 파일: 끝까지 읽지 않는 함수는 요청의 cast: add 로 겹쳐 보는 포인터를 넣어 바꿈",
      "const TC_BODY* pmsg_pBuf = (const TC_BODY*)pBuf;" in fn_body(mes, "PeekMes") and "memcpy(szSeq, pmsg_pBuf->stHead.szSeq, 4);" in mes
      and "memcpy(szLot, pmsg_pBuf->szLot, 10);" in mes)
check("mes 쪽 파일: 이미 있는 겹쳐 보기 포인터(같은 이름이 두 곳에 정의된 타입)를 알아보고 그 필드로 바꿈",
      'memcpy(pBody->szLot, "LOT0000001", 10);' in mes and 'memcpy(pBody->stItem.szCode, "CD01", 4);' in mes)
check("plc 쪽 파일: plc 헤더의 정의로 바꿈(mes 쪽 필드가 섞이지 않음)",
      "TC_BODY msg_pBuf;" in plc and "memcpy(szTc, msg_pBuf.stHead.szTc, 3);" in plc and "memcpy(szAddr, msg_pBuf.szAddr, 8);" in plc
      and "memcpy(szVal, msg_pBuf.szVal, 8);" in plc and "szLot" not in plc)
check("결과 문서: 같은 이름의 두 정의를 쓰는 파일과 고른 근거와 함께 따로 적고, 공용체는 통째로 한 자리로 둠",
      "| TC_BODY | mes/MesDef.h:6 | 35 |" in scan2 and "| TC_BODY | plc/PlcDef.h:6 | 23 |" in scan2 and "이 파일이 include 하는 헤더의 정의(쓰는 파일 mes/MesProc.cpp)" in scan2
      and "쓰는 파일 plc/PlcProc.cpp" in scan2 and "stItem.szCode[19..23)" in scan2 and "uExt[29..33)" in scan2 and "szEnd[33..35)" in scan2 and "szA[" not in scan2)
var_p = [p for p in mock.prompts if "pBuf + nPos" in p and "MesVar" in p]
check("sLLM 에 주는 필드 표: mes 파일의 물음에 mes 헤더의 정의만 붙고(정의 파일을 적음) plc 쪽 필드는 없음",
      var_p and "TC_BODY(크기 35, 정의 mes/MesDef.h): stHead.szTc[0..5) stHead.szSeq[5..9) szLot[9..19) stItem.szCode[19..23) stItem.szQty[23..29) uExt[29..33) szEnd[33..35)" in var_p[0]
      and "szAddr" not in var_p[0])
ref_p = [p for p in mock.prompts if "strcpy(g_stPlc.szAddr, pSrc)" in p and "[참고: 선언]" in p]
check("[참고: 선언]: plc 파일의 물음에 plc 헤더의 정의가 붙음(0.10.7 까지는 경로 순서로 처음인 mes 쪽 정의가 붙었다)",
      ref_p and "TC_BODY 의 정의(plc/PlcDef.h. 같은 이름의 정의 2곳 가운데 이 파일이 include 하는 헤더의 정의)" in ref_p[0]
      and "char szAddr[8];" in ref_p[0] and "szLot" not in ref_p[0].split("[코드 조각]")[0])
check("가리지 못하는 파일(어느 헤더도 include 하지 않음, 폴더도 다름): 어느 정의도 쓰지 않고 사유를 남김",
      not etc.count("pBody->") and "이 파일에서 보이는 것을 하나로 정하지 못함(mes/MesDef.h:6, plc/PlcDef.h:6)" in scan2)
check("가리지 못해도 두 정의의 배치가 같으면(TC_TAIL) 그 배치로 바꿈", "memcpy(szCrc, pTail->szCrc, 2);" in etc and "memcpy(szEtx, pTail->szEtx, 3);" in etc
      and "같은 이름의 정의 2곳의 배치가 같음" in scan2)
if shutil.which("g++"):
    srcs = ["Main.cpp", "mes/MesProc.cpp", "plc/PlcProc.cpp"]
    o0 = gxx("dup0", {"Main.cpp": DUP_MAIN, "mes/MesDef.h": MES_H, "mes/MesProc.cpp": MES_CPP, "plc/PlcDef.h": PLC_H, "plc/PlcProc.cpp": PLC_CPP}, srcs)
    o1 = gxx("dup1", {"Main.cpp": DUP_MAIN, "mes/MesDef.h": MES_H, "mes/MesProc.cpp": mes, "plc/PlcDef.h": PLC_H, "plc/PlcProc.cpp": plc}, srcs)
    check("동작(전문 버퍼): 변환한 결과를 g++ 로 컴파일해 실행한 출력이 원본과 같음", o0 and not o0.startswith("컴파일 실패") and o0 == o1 and o0.count("\n") == 6)
    if o0 != o1:
        print(o0[:600], "\n----\n", o1[:600])

# ------------------------------------------------------------------ 3. 구조체 표의 단위 확인
UNIT_H = """#pragma once
#define N_ROW 3
#pragma pack(push, 1)
typedef struct _U_PACK
{
    char szId[4];
    struct { int nNo; short sKind; } stKey;
    enum { K_A, K_B } eKind;
    struct ROW { char szV[2]; };
    ROW aRow[N_ROW];
    char szTail[1];
} U_PACK;
#pragma pack(pop)
typedef struct _U_ANON
{
    char szHead[2];
    struct
    {
        char szX[3];
        char szY[4];
    };
    char szEnd[1];
} U_ANON;
struct U_BASE { char szB[2]; };
struct U_DERIVED : public U_BASE { char szD[3]; };
struct U_FUNC { char szF[2]; void Clear() { memset(this, 0, sizeof(*this)); } };
struct U_NOPACK { char szN[3]; struct { int nV; } stIn; };
struct U_PTR { char szP[3]; struct { char* p; } stIn; };
typedef union _U_VAL { char szRaw[6]; struct { char szHi[3]; char szLo[3]; } stPair; } U_VAL;
typedef struct _U_HOLD { char szKey[2]; U_VAL uVal; char szEnd[1]; } U_HOLD;
"""


def vcxproj(src, inc):
    return "\n".join([
        '<?xml version="1.0" encoding="utf-8"?>', '<Project DefaultTargets="Build" ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">',
        ' <ItemGroup Label="ProjectConfigurations">',
        '  <ProjectConfiguration Include="Release|Win32"><Configuration>Release</Configuration></ProjectConfiguration>', ' </ItemGroup>',
        ' <PropertyGroup Condition="\'$(Configuration)|$(Platform)\'==\'Release|Win32\'" Label="Configuration">',
        '  <CharacterSet>MultiByte</CharacterSet><PlatformToolset>v100</PlatformToolset>', ' </PropertyGroup>',
        ' <ItemDefinitionGroup Condition="\'$(Configuration)|$(Platform)\'==\'Release|Win32\'">',
        '  <ClCompile><AdditionalIncludeDirectories>%s;%%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories></ClCompile>' % inc,
        ' </ItemDefinitionGroup>', ' <ItemGroup><ClCompile Include="%s" /></ItemGroup>' % src, '</Project>', ""])


TC_A = "#pragma once\ntypedef struct _TC_X { char szA[3]; char szB[5]; } TC_X;\n"
TC_B = "#pragma once\ntypedef struct _TC_X { char szP[4]; char szQ[4]; char szR[2]; } TC_X;\n"
USE_TC = '#include "Tc.h"\nint Use(char* p) { return p[0]; }\n'
pk3 = make_pkg("UNIT", (("Inc/Unit.h", UNIT_H), ("Src/Use.cpp", '#include "../Inc/Unit.h"\nint Use(char* p) { return p[0]; }\n'),
                        ("Src/Unit.h", "#pragma once\n#define LOCAL_ONLY 1\n"), ("Src/Use2.cpp", '#include "Unit.h"\nint Use2(char* p) { return p[1]; }\n'),
                        ("IncA/Tc.h", TC_A), ("IncB/Tc.h", TC_B), ("SrcA/UseA.cpp", USE_TC), ("SrcB/UseB.cpp", USE_TC), ("SrcC/UseC.cpp", USE_TC),
                        ("ProjA/A.vcxproj", vcxproj("..\\SrcA\\UseA.cpp", "..\\IncA")), ("ProjB/B.vcxproj", vcxproj("..\\SrcB\\UseB.cpp", "$(ProjectDir)..\\IncB"))))
ctx2 = V._make_ctx(V._selftest_ns(os.path.join(work, "unit2"), pk2, "", from_vs="2010", no_compile=True, no_llm=True, until="S2"))
V.run_pipeline(ctx2, [s for s, _n in V.STAGES])
V._SINK.close()
ctx = V._make_ctx(V._selftest_ns(os.path.join(work, "unit"), pk3, "", from_vs="2010", no_compile=True, no_llm=True, until="S2"))
try:
    V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    sx = V._sx_structs(ctx)
    bn = sx["by_name"]
    up = bn["U_PACK"]
    check("안쪽 정의: pack(1) 안의 구조체에서 안쪽 구조체·열거형·이름 있는 안쪽 타입의 배열까지 위치를 계산함(크기 21)",
          up["ok"] and up["size"] == 21 and [(x["path"], x["off"], x["size"]) for x in up["nodes"]] ==
          [("szId", 0, 4), ("stKey", 4, 6), ("stKey.nNo", 4, 4), ("stKey.sKind", 8, 2), ("eKind", 10, 4), ("aRow", 14, 6), ("szTail", 20, 1)])
    ua = bn["U_ANON"]
    check("이름 없는 안쪽 구조체(선언자 없음): 그 멤버가 바깥의 멤버로 들어감", ua["ok"] and ua["size"] == 10
          and [(x["path"], x["off"]) for x in ua["nodes"]] == [("szHead", 0), ("szX", 2), ("szY", 5), ("szEnd", 9)])
    check("확정하지 않는 것과 그 사유: 상속, 함수 본문, pack(1) 밖의 정수 멤버, 안쪽의 포인터",
          not bn["U_DERIVED"]["ok"] and "상속" in bn["U_DERIVED"]["why"] and not bn["U_FUNC"]["ok"] and "함수 본문" in bn["U_FUNC"]["why"]
          and not bn["U_NOPACK"]["ok"] and "padding" in bn["U_NOPACK"]["why"] and not bn["U_PTR"]["ok"] and "포인터" in bn["U_PTR"]["why"])
    check("include 를 경로로 가림: 이름이 같은 헤더(Unit.h)가 두 폴더에 있어도 적힌 경로의 것만 따라감",
          "Inc/Unit.h" in V._inc_reach(ctx, "Src/Use.cpp") and "Src/Unit.h" not in V._inc_reach(ctx, "Src/Use.cpp")
          and "Src/Unit.h" in V._inc_reach(ctx, "Src/Use2.cpp") and "Inc/Unit.h" not in V._inc_reach(ctx, "Src/Use2.cpp")
          and V._sx_sees(ctx, "Src/Use.cpp", up, V.get_doc(ctx, "Src/Use.cpp"), 0) and not V._sx_sees(ctx, "Src/Use2.cpp", up, V.get_doc(ctx, "Src/Use2.cpp"), 0))
    check("채우는 호출 읽기: memset 말고도 ZeroMemory·FillMemory 계열, 포인터 캐스팅·&·괄호를 벗겨 읽고, 배열의 일부를 가리키면 받지 않음",
          V._sx_fill_parse("FillMemory(szLot, sizeof(szLot), 0x20)", "szLot") == ("FillMemory", "0x20", "sizeof(szLot)")
          and V._sx_fill_parse("::RtlZeroMemory(&stRec, sizeof(stRec))", "stRec") == ("RtlZeroMemory", "0", "sizeof(stRec)")
          and V._sx_fill_parse("memset((void*)(szLot), ' ', sizeof szLot)", "szLot") == ("memset", "' '", "sizeof szLot")
          and V._sx_fill_parse("memset(szLot + 1, ' ', 3)", "szLot") is None and V._sx_fill_parse("memset(szLot[0], ' ', 3)", "szLot") is None
          and V._sx_len_whole("sizeof szLot", "szLot", 44, {}) and V._sx_len_whole("sizeof(szLot[0]) * 4", "szLot", 44, {}, [11, 1])
          and not V._sx_len_whole("sizeof(szLot[0])", "szLot", 44, {}, [11, 1]))
    uh = bn["U_HOLD"]
    check("이름으로 쓴 공용체 타입의 멤버: 크기만 구해 통째로 한 자리로 두고 뒤의 필드 위치를 맞춤",
          uh["ok"] and uh["size"] == 9 and [(x["path"], x["off"], x["size"]) for x in uh["nodes"]] == [("szKey", 0, 2), ("uVal", 2, 6), ("szEnd", 8, 1)])
    va, vb, vc = [V._sx_view(ctx, sx, r) for r in ("SrcA/UseA.cpp", "SrcB/UseB.cpp", "SrcC/UseC.cpp")]
    check("프로젝트의 추가 include 폴더: 이름이 같은 헤더(Tc.h)가 두 폴더에 있을 때, 그 파일을 실은 프로젝트의 include 폴더에 있는 것을 고름",
          list(V._inc_reach(ctx, "SrcA/UseA.cpp")) == ["SrcA/UseA.cpp", "IncA/Tc.h"] and list(V._inc_reach(ctx, "SrcB/UseB.cpp")) == ["SrcB/UseB.cpp", "IncB/Tc.h"]
          and va["by_name"]["TC_X"]["rel"] == "IncA/Tc.h" and va["by_name"]["TC_X"]["size"] == 8
          and vb["by_name"]["TC_X"]["rel"] == "IncB/Tc.h" and vb["by_name"]["TC_X"]["size"] == 10)
    check("컴파일 검사의 프로젝트 고르기: ..\\SrcB\\UseB.cpp 로 적힌 항목도 그 파일을 실은 프로젝트(ProjB)의 설정을 씀(전에는 첫 프로젝트의 것을 썼다)",
          V._compile_flags(ctx, "SrcB/UseB.cpp")[1] == "ProjB" and V._compile_flags(ctx, "SrcA/UseA.cpp")[1] == "ProjA"
          and (V._compile_flags(ctx, "SrcB/UseB.cpp")[0] or {}).get("includes") == ["$(ProjectDir)..\\IncB"])
    check("프로젝트에 실리지 않은 파일: 두 헤더 가운데 어느 것인지 알 수 없어 그 이름을 쓰지 않고 까닭을 남김",
          "TC_X" not in vc["by_name"] and "하나로 정하지 못함(IncA/Tc.h:2, IncB/Tc.h:2)" in vc["amb"]["TC_X"] and "TC_X" not in bn)
    check("채우는 값의 풀이: 문자·정수 상수, 캐스팅, _T(), 매크로의 매크로는 값으로, 변수와 식은 None",
          V._sx_fill_value(ctx, "' '") == 32 and V._sx_fill_value(ctx, "(char)0x20") == 32 and V._sx_fill_value(ctx, "_T(' ')") == 32
          and V._sx_fill_value(ctx, "'\\0'") == 0 and V._sx_fill_value(ctx, "-1") == -1 and V._sx_fill_value(ctx, "N_ROW") == 3
          and V._sx_fill_value(ctx, "nVal") is None and V._sx_fill_value(ctx, "a + 1") is None)
    if shutil.which("g++"):
        # 도구가 계산한 필드의 위치와 크기를 컴파일러(g++)의 offsetof, sizeof 와 견준다(도구의 계산과 독립된 확인)
        mes_view = V._sx_view(ctx2, V._sx_structs(ctx2), "mes/MesProc.cpp")
        plc_view = V._sx_view(ctx2, V._sx_structs(ctx2), "plc/PlcProc.cpp")
        for tag, hdr, structs in (("unit", UNIT_H, [up, ua]), ("mes", MES_H, [mes_view["by_name"]["TC_BODY"], mes_view["by_name"]["TC_HEAD"]]),
                                  ("plc", PLC_H, [plc_view["by_name"]["TC_BODY"]])):
            want, lines = [], []
            for e in structs:
                nm = e["names"][-1]
                want.append("%s %d" % (nm, e["size"]))
                lines.append('    printf("%s %%d\\n", (int)sizeof(%s));' % (nm, nm))
                for x in e["nodes"]:
                    want.append("%s.%s %d %d" % (nm, x["path"], x["off"], x["size"]))
                    lines.append('    printf("%s.%s %%d %%d\\n", (int)offsetof(%s, %s), (int)sizeof(((%s*)0)->%s));' % (nm, x["path"], nm, x["path"], nm, x["path"]))
            prog = '#include <stdio.h>\n#include <string.h>\n#include <stddef.h>\n#include "Def.h"\nint main()\n{\n%s\n    return 0;\n}\n' % "\n".join(lines)
            got = gxx("layout_" + tag, {"Def.h": hdr, "Main.cpp": prog}, ["Main.cpp"])
            check("배치의 독립 확인(%s): 도구가 계산한 구조체 %d개의 크기와 필드 %d개의 위치·크기가 g++ 의 offsetof, sizeof 와 같음" % (tag, len(structs), len(want) - len(structs)), got.strip().split("\n") == want)
            if got.strip().split("\n") != want:
                print(got[:500], "\n----\n", "\n".join(want)[:500])
finally:
    ctx.store.close()
    V._SINK.close()
    ctx2.store.close()

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
