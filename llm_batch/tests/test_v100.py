"""0.10.0 의 시험: 구조 분석(전문 버퍼의 필드 접근, 지역 병렬 배열의 묶음).

사용: python test_v100.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 전문 버퍼: 이미 겹쳐 보는 포인터가 있으면 그 필드로, 읽기만 하는 고정 구간은 로컬 구조체 복사로, 그 밖은 대응만 확인하고 그대로 둔다
     (요청의 cast: add 가 있을 때만 겹쳐 보는 포인터를 추가한다). 실행 중에 정해지는 길이는 바꾸지 않고 사유를 남긴다
  2. 지역 병렬 배열: 같은 레코드 번호(클래스 멤버)로 쓰는 지역 배열을 구조체의 배열로. 통째로 쓰는 곳이 있으면 묶음 전체를 바꾸지 않는다
  3. 집계와 위치: 전문 문장과 병렬 묶음을 따로 세고, 미처리에 파일·줄과 사유가 남는다
  4. 요청문(일반 명칭)에서 구조 분석으로 넘어가는 경로, 옛 변환기가 뜻이 틀린 변환을 만들지 않는 것
  5. 변환 전후의 동작이 같은가(g++ 가 있을 때만. 없으면 건너뛴다)
예제는 전달받은 설명에 맞춰 만든 합성 코드다. 모델은 이 파일 안의 모의 서버다. 실제 코드와 실제 cl 에서의 동작은 이 시험으로 확인되지 않는다.
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
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args), capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def run_dir(root):
    hits = sorted(b for b, _d, fs in os.walk(root) if "state.db" in fs)
    return hits[-1] if hits else ""


def out_text(root, *parts):
    p = os.path.join(run_dir(root), "out", *parts)
    if not os.path.isfile(p):
        return ""
    return open(p, "rb").read().decode("cp949" if parts[0] == "changed" else "utf-8")


def rows(root, sql, args=()):
    import sqlite3
    con = sqlite3.connect(os.path.join(run_dir(root), "state.db"))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


class Mock:
    """모의 sLLM. 요청문 분석에는 '배열 -> 구조체' 의도와 names 를 돌려주고, 수정 요청에는 NO_CHANGE 로 답한다."""

    def __init__(self, names):
        self.calls, outer = 0, self

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
                out = "NO_CHANGE"
                if "[요청 원문]" in user:
                    out = json.dumps({"intents": [{"type": "array_to_struct", "instruction": "MES 전문 Data 배열을 TC 별 구조체로 바꾼다", "names": names,
                                                   "words": [], "construct": "any", "files": []}]}, ensure_ascii=False)
                self._send({"choices": [{"message": {"content": out}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v1" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


DEF_H = r'''#pragma once
// 합성 예제: MES 전문 처리 (CP949, CRLF)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ITEM     50
#define LEN_CODE     10
#define HDR_OLD_LEN  50
#define HDR_NEW_LEN  100

// 송신 본문. 송신 코드는 버퍼를 이 구조체로 겹쳐 보고 필드에 쓴다
typedef struct _MES_BODY
{
    char szTc[5];
    char szLot[10];
    char szTime[14];
    char szPeriod[8];
    char szQty[6];
    char szEtx[1];
} MES_BODY;

// 수신 헤더(50바이트)
typedef struct _MES_HDR50
{
    char szTc[5];
    char szSeq[6];
    char szLen[4];
    char szSrc[10];
    char szDst[10];
    char szRsv[15];
} MES_HDR50;

// 내부 헤더(100바이트)
typedef struct _MES_HDR100
{
    char szTc[5];
    char szSeq[6];
    char szLen[4];
    char szSrc[20];
    char szDst[20];
    char szTime[14];
    char szRsv[31];
} MES_HDR100;

// 검사 결과 전문
typedef struct _MES_INSP
{
    char szLot[10];
    char szItem[10];
    char szResult[2];
    char szValue[8];
} MES_INSP;

struct ItemDto
{
    char lot[11];
    char item[11];
    char code[11];
    char unit[11];
    int  qty;
};

class CMesProc
{
public:
    int     m_nIdx;                 // 반복문의 인덱스(클래스 멤버)
    int     m_nCount;
    char    m_szLot[11];
    char    m_szItem[11];
    char    m_szResult[3];
    char    m_szValue[9];
    ItemDto m_dto[MAX_ITEM];

    CMesProc();
    int  MakeSendBody(char* pBuf, const char* pLot, long lTime, int nQty);
    int  ParseInsp(const char* pBuf, int nLen);
    int  ConvHeader(char* pDst, const char* pSrc, int nLen);
    int  MakeTestMsg(char* pBuf, const char* pItems[], const int nItemLen[], int nItems);
    int  BuildList(char* pOut);
    int  BuildListBlocked(char* pOut);

private:
    void ConvTime(char* pDst, long lTime);
    void ConvPeriod(char* pDst, long lFrom, long lTo);
    int  SumQty(const int* pQty, int n);
};
'''

PROC_CPP = r'''// 합성 예제: MES 전문 처리 (CP949, CRLF)
#include "MesDef.h"

CMesProc::CMesProc()
{
    m_nIdx = 0;
    m_nCount = 0;
    memset(m_dto, 0x00, sizeof(m_dto));
}

void CMesProc::ConvTime(char* pDst, long lTime)
{
    char szTmp[32];
    sprintf(szTmp, "%014ld", lTime);
    memcpy(pDst, szTmp, 14);
}

void CMesProc::ConvPeriod(char* pDst, long lFrom, long lTo)
{
    char szTmp[32];
    sprintf(szTmp, "%08ld", lTo - lFrom);
    memcpy(pDst, szTmp, 8);
}

int CMesProc::SumQty(const int* pQty, int n)
{
    int i, s = 0;
    for (i = 0; i < n; i++)
        s += pQty[i];
    return s;
}

// 송신 본문: 호출자가 준 버퍼를 구조체로 겹쳐 보고 이름 있는 필드에 쓴다. 시간과 기간은 보조 함수가 바꾼다
int CMesProc::MakeSendBody(char* pBuf, const char* pLot, long lTime, int nQty)
{
    MES_BODY* pBody = (MES_BODY*)pBuf;
    char szTmp[16];
    memset(pBuf, ' ', sizeof(MES_BODY));
    memcpy(pBody->szTc, "PA007", 5);
    memcpy(pBody->szLot, pLot, sizeof(pBody->szLot));
    ConvTime(pBody->szTime, lTime);
    ConvPeriod(pBody->szPeriod, lTime, lTime + 3600);
    sprintf(szTmp, "%06d", nQty);
    memcpy(pBuf + 37, szTmp, 6);        // 숫자 위치로 쓰는 곳
    pBuf[43] = 0x03;
    return sizeof(MES_BODY);
}

// 특정 전문 해석: 버퍼 포인터와 정수 커서. 필드 길이만큼 복사하고 커서를 올린다
int CMesProc::ParseInsp(const char* pBuf, int nLen)
{
    int nPos = 0;
    if (nLen < (int)sizeof(MES_INSP))
        return -1;
    memset(m_szLot, 0x00, sizeof(m_szLot));
    memset(m_szItem, 0x00, sizeof(m_szItem));
    memset(m_szResult, 0x00, sizeof(m_szResult));
    memset(m_szValue, 0x00, sizeof(m_szValue));
    memcpy(m_szLot, pBuf + nPos, 10);
    nPos += 10;
    memcpy(m_szItem, &pBuf[nPos], 10);
    nPos += 10;
    memcpy(m_szResult, pBuf + nPos, 2);
    nPos += 2;
    memcpy(m_szValue, pBuf + nPos, 8);
    nPos += 8;
    return nPos;
}

// 수신 헤더 변환: 고정 위치에 필드를 쓰고 본문은 포인터 위치로 복사한다
int CMesProc::ConvHeader(char* pDst, const char* pSrc, int nLen)
{
    memset(pDst, ' ', HDR_NEW_LEN);
    memcpy(pDst, pSrc, 5);
    memcpy(pDst + 5, pSrc + 5, 6);
    memcpy(pDst + 11, pSrc + 11, 4);
    memcpy(pDst + 15, pSrc + 15, 10);
    memcpy(pDst + 35, pSrc + 25, 10);
    ConvTime(pDst + 55, 20261126);
    memcpy(pDst + HDR_NEW_LEN, pSrc + HDR_OLD_LEN, nLen - HDR_OLD_LEN);
    return nLen + (HDR_NEW_LEN - HDR_OLD_LEN);
}

// 시험 전문 조립: 화면에서 받은 항목 길이로 복사하고 그만큼 옮긴다(길이가 실행 중에 정해진다)
int CMesProc::MakeTestMsg(char* pBuf, const char* pItems[], const int nItemLen[], int nItems)
{
    int nPos = 0;
    int i;
    for (i = 0; i < nItems; i++)
    {
        memcpy(pBuf + nPos, pItems[i], nItemLen[i]);
        nPos += nItemLen[i];
    }
    memcpy(pBuf + nPos, "\r\n", 2);
    return nPos + 2;
}

// 병렬 배열: 선언, memset 초기화, DTO 에서 적재(복사 + 정수 대입). 반복문의 인덱스는 클래스 멤버다
int CMesProc::BuildList(char* pOut)
{
    char szLot[MAX_ITEM][LEN_CODE + 1];     // 로트
    char szItem[MAX_ITEM][LEN_CODE + 1];    // 품목
    char szCode[MAX_ITEM][LEN_CODE + 1];    // 코드
    char szUnit[MAX_ITEM][LEN_CODE + 1];    // 단위
    int  nQty[MAX_ITEM];                    // 수량
    int  nPos = 0;

    memset(szLot, 0x00, sizeof(szLot));
    memset(szItem, 0x00, sizeof(szItem));
    memset(szCode, 0x00, sizeof(szCode));
    memset(szUnit, 0x00, sizeof(szUnit));
    memset(nQty, 0x00, sizeof(nQty));

    for (m_nIdx = 0; m_nIdx < m_nCount; m_nIdx++)
    {
        strcpy(szLot[m_nIdx], m_dto[m_nIdx].lot);
        strcpy(szItem[m_nIdx], m_dto[m_nIdx].item);
        memcpy(szCode[m_nIdx], m_dto[m_nIdx].code, LEN_CODE);
        strncpy(szUnit[m_nIdx], m_dto[m_nIdx].unit, LEN_CODE);
        nQty[m_nIdx] = m_dto[m_nIdx].qty;
    }
    for (m_nIdx = 0; m_nIdx < m_nCount; m_nIdx++)
    {
        if (nQty[m_nIdx] <= 0)
            continue;
        memcpy(pOut + nPos, szLot[m_nIdx], LEN_CODE);
        nPos += LEN_CODE;
        memcpy(pOut + nPos, szItem[m_nIdx], LEN_CODE);
        nPos += LEN_CODE;
        memcpy(pOut + nPos, szCode[m_nIdx], LEN_CODE);
        nPos += LEN_CODE;
        if (szUnit[m_nIdx][0] != 0)
        {
            memcpy(pOut + nPos, szUnit[m_nIdx], LEN_CODE);
            nPos += LEN_CODE;
        }
        sprintf(pOut + nPos, "%06d", nQty[m_nIdx]);
        nPos += 6;
    }
    return nPos;
}

// 병렬 배열이지만 배열 하나를 통째로 다른 함수에 넘긴다(기계적으로 못 바꾸는 꼴)
int CMesProc::BuildListBlocked(char* pOut)
{
    char szName[MAX_ITEM][LEN_CODE + 1];
    int  nVal[MAX_ITEM];
    memset(szName, 0x00, sizeof(szName));
    memset(nVal, 0x00, sizeof(nVal));
    for (m_nIdx = 0; m_nIdx < m_nCount; m_nIdx++)
    {
        strcpy(szName[m_nIdx], m_dto[m_nIdx].lot);
        nVal[m_nIdx] = m_dto[m_nIdx].qty;
    }
    sprintf(pOut, "%s:%d", szName[0], SumQty(nVal, m_nCount));
    return (int)strlen(pOut);
}
'''

TABLE_CPP = r'''// 합성 예제: 파일 범위의 배열(옛 변환기의 대상이 아닌 꼴)
#include "MesDef.h"

char g_szName[MAX_ITEM][LEN_CODE + 1];
int  g_nCnt[MAX_ITEM];

void ResetTable(int n)
{
    g_nCnt[0] = 0;
    g_nCnt[1] = 0;
    g_nCnt[n] = 1;
    if (g_szName[n][0] == 0)
        g_szName[0][0] = 0;
}
'''

MAIN_CPP = r'''// 시험용: 변환 전과 후의 동작을 글로 찍어 비교한다
#include "MesDef.h"

static void dump(const char* tag, const char* p, int n)
{
    int i;
    printf("%s[%d]:", tag, n);
    for (i = 0; i < n; i++)
        printf("%02x", (unsigned char)p[i]);
    printf("\n");
}

int main()
{
    CMesProc proc;
    char buf[4096];
    char src[256];
    const char* items[3] = { "AB", "CDEFG", "H" };
    const int lens[3] = { 2, 5, 1 };
    int n, k;
    memset(buf, '.', sizeof(buf));
    n = proc.MakeSendBody(buf, "LOT-A00001", 20261126, 42);
    dump("body", buf, n + 2);
    n = proc.ParseInsp("LOT-B00002ITEM-00003OK00012.50ZZZ", 33);
    printf("insp %d [%s][%s][%s][%s]\n", n, proc.m_szLot, proc.m_szItem, proc.m_szResult, proc.m_szValue);
    for (k = 0; k < 80; k++)
        src[k] = (char)('A' + (k % 26));
    memset(buf, '.', sizeof(buf));
    n = proc.ConvHeader(buf, src, 80);
    dump("hdr", buf, n + 2);
    memset(buf, '.', sizeof(buf));
    n = proc.MakeTestMsg(buf, items, lens, 3);
    dump("test", buf, n + 1);
    proc.m_nCount = 6;
    for (k = 0; k < proc.m_nCount; k++)
    {
        sprintf(proc.m_dto[k].lot, "L%09d", k);
        sprintf(proc.m_dto[k].item, "I%09d", k * 3);
        sprintf(proc.m_dto[k].code, "C%09d", k * 7);
        strcpy(proc.m_dto[k].unit, (k % 2) ? "EA" : "");
        proc.m_dto[k].qty = (k == 3) ? 0 : k * 11 + 1;
    }
    memset(buf, '.', sizeof(buf));
    n = proc.BuildList(buf);
    dump("list", buf, n);
    n = proc.BuildListBlocked(buf);
    printf("blocked %d %s\n", n, buf);
    return 0;
}
'''


def make_pkg(name):
    pkg = os.path.join(work, name)
    for rel, body in (("Src/MesDef.h", DEF_H), ("Src/MesProc.cpp", PROC_CPP), ("Src/Table.cpp", TABLE_CPP)):
        V.write_bytes(os.path.join(pkg, *rel.split("/")), body.replace("\n", "\r\n").encode("cp949"))
    return pkg


def request(name, feats):
    p = os.path.join(work, name)
    V.write_bytes(p, json.dumps({"features": feats}, ensure_ascii=False).encode("utf-8"))
    return p


def build(tag, pkg, changed_root):
    """원본(또는 바뀐 파일)을 UTF-8 로 풀어 g++ 로 컴파일하고 실행한 출력. 컴파일러가 없으면 None"""
    if not shutil.which("g++"):
        return None
    d = os.path.join(work, "build_" + tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f in ("MesDef.h", "MesProc.cpp"):
        src = os.path.join(pkg, "Src", f)
        if changed_root:
            alt = os.path.join(run_dir(changed_root), "out", "changed", "Src", f)
            src = alt if os.path.isfile(alt) else src
        open(os.path.join(d, f), "w", encoding="utf-8", newline="\n").write(open(src, "rb").read().decode("cp949").replace("\r\n", "\n"))
    open(os.path.join(d, "main.cpp"), "w", encoding="utf-8", newline="\n").write(MAIN_CPP)
    p = subprocess.run(["g++", "-w", "-o", os.path.join(d, "t"), os.path.join(d, "main.cpp"), os.path.join(d, "MesProc.cpp")], capture_output=True)
    if p.returncode != 0:
        return "컴파일 실패: " + p.stderr.decode("utf-8", "replace")[:600]
    return subprocess.run([os.path.join(d, "t")], capture_output=True, cwd=d).stdout.decode("utf-8", "replace")


BASE = ["--from-vs", "2010", "--to-vs", "2022", "--no-compile", "--no-llm"]
pkg = make_pkg("MESPKG")
base_out = build("orig", pkg, None)

# ---------------------------------------------------------------- 1. 요청 파일(기본값)
reqA = request("reqA.json", [{"id": "REQ-BUF", "type": "buffer_fields"}, {"id": "REQ-REC", "type": "record_group"}])
cA, tA = cli("run", "--package", pkg, "--request", reqA, "--out", os.path.join(work, "A"), *BASE)
srcA = out_text(os.path.join(work, "A"), "changed", "Src", "MesProc.cpp")
scanA = out_text(os.path.join(work, "A"), "struct_scan.md")
repA = out_text(os.path.join(work, "A"), "report.md")
manA = out_text(os.path.join(work, "A"), "manual_review.md")
check("실행: 종료 코드 1(검토 필요)이고 결과 파일이 CP949, CRLF 그대로", cA == 1 and "\r\n" in srcA and "\n" not in srcA.replace("\r\n", "") and "로트" in srcA)
check("전문 버퍼(이미 겹쳐 봄): 숫자 위치의 접근을 있는 포인터의 필드로 바꿈. 캐스팅을 더 넣지 않음",
      "memcpy(pBody->szQty, szTmp, 6);" in srcA and "pBody->szEtx[0] = 0x03;" in srcA and srcA.count("(MES_BODY*)pBuf") == 1)
check("전문 버퍼(이미 겹쳐 봄): 이미 필드로 쓰는 문장과 버퍼 전체를 다루는 문장은 그대로",
      'memcpy(pBody->szTc, "PA007", 5);' in srcA and "ConvTime(pBody->szTime, lTime);" in srcA and "memset(pBuf, ' ', sizeof(MES_BODY));" in srcA)
check("전문 버퍼(읽기만 하는 고정 구간): 로컬 구조체에 한 번 복사하고 필드로 읽음. 커서 계산과 반환값은 그대로",
      "MES_INSP msg_pBuf;" in srcA and "memcpy(&msg_pBuf, pBuf, sizeof(msg_pBuf));" in srcA and "memcpy(m_szLot, msg_pBuf.szLot, 10);" in srcA
      and "memcpy(m_szItem, msg_pBuf.szItem, 10);" in srcA and "memcpy(m_szValue, msg_pBuf.szValue, 8);" in srcA and "nPos += 8;" in srcA
      and srcA.index("return -1;") < srcA.index("memcpy(&msg_pBuf") < srcA.index("memcpy(m_szLot, msg_pBuf"))
check("전문 버퍼(겹쳐 보는 포인터가 없음): 대응은 확인하되 캐스팅을 넣지 않고 그대로 둠",
      "memcpy(pDst + 5, pSrc + 5, 6);" in srcA and "MES_HDR50*" not in srcA and "대응 확인: MES_HDR50.szSeq" in scanA)
check("전문 버퍼(필드의 일부만 채움): 길이가 필드와 달라 대응시키지 않음", "위치와 길이가 맞는 기존 구조체가 없음" in scanA and "MES_HDR100*" not in srcA)
check("전문 버퍼(실행 중에 정해지는 길이): 바꾸지 않고 사유를 남김",
      "memcpy(pBuf + nPos, pItems[i], nItemLen[i]);" in srcA and "길이 모름" in scanA and "위치 모름" in scanA and "반복 뒤" in scanA)
check("전문 버퍼: 버퍼 선언, 전문 길이, 반환값을 건드리지 않음",
      "return sizeof(MES_BODY);" in srcA and "return nLen + (HDR_NEW_LEN - HDR_OLD_LEN);" in srcA and "int CMesProc::MakeSendBody(char* pBuf, const char* pLot, long lTime, int nQty)" in srcA)
check("병렬 배열: 지역 배열 5개를 구조체의 배열 하나로(선언 한 곳, 주석은 필드 줄로)",
      "struct BuildListRec" in srcA and "} stRec[MAX_ITEM];" in srcA and "char szLot[LEN_CODE + 1];  // 로트" in srcA and "int nQty;  // 수량" in srcA
      and "char szLot[MAX_ITEM][LEN_CODE + 1];" not in srcA)
check("병렬 배열: 0 으로 지우는 문장 5개를 하나로, 첨자 접근은 레코드의 필드로(인덱스가 클래스 멤버여도)",
      srcA.count("memset(stRec, 0x00, sizeof(stRec));") == 1 and "memset(szLot" not in srcA
      and "strcpy(stRec[m_nIdx].szLot, m_dto[m_nIdx].lot);" in srcA and "stRec[m_nIdx].nQty = m_dto[m_nIdx].qty;" in srcA
      and "if (stRec[m_nIdx].szUnit[0] != 0)" in srcA and 'sprintf(pOut + nPos, "%06d", stRec[m_nIdx].nQty);' in srcA)
check("병렬 배열: 통째로 쓰는 곳이 있는 묶음은 하나도 바꾸지 않고, 사유와 줄을 남김",
      "char szName[MAX_ITEM][LEN_CODE + 1];" in srcA and "SumQty(nVal, m_nCount)" in srcA and "이름을 통째로 사용(nVal)" in scanA
      and re.search(r"Src/MesProc.cpp:\d+ CMesProc::BuildListBlocked\(\) 묶음 szName, nVal", repA) is not None)
check("병렬 배열: 전문 버퍼와 따로 처리(버퍼 pOut 은 그대로, 구조체 멤버 배열 m_dto 는 대상이 아님)",
      "memcpy(pOut + nPos, stRec[m_nIdx].szLot, LEN_CODE);" in srcA and "ItemDto m_dto[MAX_ITEM];" in open(os.path.join(pkg, "Src", "MesDef.h"), "rb").read().decode("cp949")
      and not out_text(os.path.join(work, "A"), "changed", "Src", "MesDef.h"))
ed = rows(os.path.join(work, "A"), "SELECT origin, ref, status, review, COUNT(*) AS n FROM edits GROUP BY origin, ref, status, review")
check("수정 레코드: 전부 요청에서 나온 규칙 수정이고 검토 표시가 붙음(sLLM 수정 없음)",
      ed and all(e["origin"] == "request" and e["review"] == 1 and e["status"] == "verified" for e in ed) and {e["ref"] for e in ed} == {"REQ-BUF", "REQ-REC"})
iss = rows(os.path.join(work, "A"), "SELECT path, line, title, note FROM issues WHERE source='request' AND status='manual'")
check("미처리: 수동 검토 이슈마다 파일과 줄이 있음(빈 경로 없음)", iss and all(i["path"] and i["line"] > 0 for i in iss))
check("집계: 보고서에 전문 문장과 병렬 묶음을 따로 센 표가 있고 sLLM 요청은 0건",
      "### 구조 분석(전문 버퍼의 필드 접근, 지역 병렬 배열의 묶음)" in repA and "| REQ-BUF | 전문 처리 문장 |" in repA and "| REQ-REC | 병렬 배열 묶음 |" in repA
      and "미처리 위치(파일:줄, 사유):" in repA and "sLLM 에 수정 요청을 한 건도 보내지 않습니다" in tA)
check("집계: struct_scan.md 에 버퍼별 문장 표, 겹쳐 보는 포인터, 묶음, 구조체 배치가 나옴",
      "버퍼 `pBuf` 를 `MES_BODY* pBody` 로 겹쳐 본다. 필드 접근 5곳(유지)" in scanA and "ParseInsp() 의 버퍼 `pBuf`" in scanA
      and "로컬 구조체에 복사해 필드로 / 구조체 `MES_INSP`" in scanA and "## 2. 지역 병렬 배열의 묶음" in scanA and "szLot[0..10)" in scanA)
outA = build("A", pkg, os.path.join(work, "A"))
if base_out is None:
    print("[건너뜀] g++ 가 없어 변환 전후의 동작 비교는 하지 않음")
else:
    check("동작: 변환한 결과를 g++ 로 컴파일해 실행한 출력이 원본과 같음", base_out and not base_out.startswith("컴파일 실패") and outA == base_out)

# ---------------------------------------------------------------- 2. cast: add, 구조체 지정, 이름 지정, 대상 좁히기
reqB = request("reqB.json", [{"id": "REQ-BUF", "type": "buffer_fields", "cast": "add", "struct": {"ConvHeader": "MES_HDR50"}},
                             {"id": "REQ-REC", "type": "record_group", "targets": ["BuildList"], "var_name": "aItem", "struct_name": "ITEM_REC"}])
cB, tB = cli("run", "--package", pkg, "--request", reqB, "--out", os.path.join(work, "B"), *BASE)
srcB = out_text(os.path.join(work, "B"), "changed", "Src", "MesProc.cpp")
scanB = out_text(os.path.join(work, "B"), "struct_scan.md")
check("cast: add: 대응이 확인된 버퍼에 겹쳐 보는 포인터를 하나 넣고 필드로 바꿈(읽는 버퍼는 const)",
      "const MES_HDR50* pmsg_pSrc = (const MES_HDR50*)pSrc;" in srcB and "pmsg_pSrc->szSeq, 6);" in srcB and "pmsg_pSrc->szTc, 5);" in srcB)
check("구조체 지정: 맞는 접근만 바꾸고, 필드의 경계와 어긋나는 접근은 사유와 함께 그대로 둠",
      "MES_HDR50* pmsg_pDst = (MES_HDR50*)pDst;" in srcB and "memcpy(pmsg_pDst->szSeq, pmsg_pSrc->szSeq, 6);" in srcB
      and "memcpy(pDst + 35, pmsg_pSrc->szDst, 10);" in srcB and "필드의 경계와 다름(위치 35, 길이 10" in scanB)
check("이름 지정과 대상 좁히기: var_name, struct_name 을 쓰고 targets 밖의 묶음은 요청 대상이 아님",
      "struct ITEM_REC" in srcB and "} aItem[MAX_ITEM];" in srcB and "aItem[m_nIdx].nQty" in srcB and "바꿀 수 없음(요청 대상이 아님)" in scanB)
outB = build("B", pkg, os.path.join(work, "B"))
if base_out is not None:
    check("동작: cast: add 의 결과도 g++ 로 컴파일해 실행한 출력이 원본과 같음", outB == base_out)

# ---------------------------------------------------------------- 3. apply: false, 입력 오류
reqC = request("reqC.json", [{"id": "REQ-BUF", "type": "buffer_fields", "apply": False}, {"id": "REQ-REC", "type": "record_group", "apply": False}])
cC, tC = cli("run", "--package", pkg, "--request", reqC, "--out", os.path.join(work, "C"), *BASE)
check("apply: false: 탐지와 보고만 하고 아무것도 바꾸지 않음",
      not out_text(os.path.join(work, "C"), "changed", "Src", "MesProc.cpp") and "대응 확인(요청의 apply 가 꺼져 있어 바꾸지 않음)" in out_text(os.path.join(work, "C"), "struct_scan.md")
      and not rows(os.path.join(work, "C"), "SELECT id FROM edits"))
cD, tD = cli("run", "--package", pkg, "--request", request("reqD.json", [{"id": "X", "type": "buffer_fields", "cast": "always"}]), "--out", os.path.join(work, "D"), *BASE)
check("입력 오류: 모르는 cast 값은 종료 코드 3", cD == 3 and "cast" in tD)

# ---------------------------------------------------------------- 4. 다른 수정과 겹치면 묶음 전체를 바꾸지 않는다
cE, tE = cli("run", "--package", pkg, "--request", reqA, "--out", os.path.join(work, "E"), "--until", "S4", *BASE)
rd = run_dir(os.path.join(work, "E"))
text = open(os.path.join(pkg, "Src", "MesProc.cpp"), "rb").read().decode("cp949").replace("\r\n", "\n")
pos = text.index("szLot[m_nIdx], m_dto")
import sqlite3
con = sqlite3.connect(os.path.join(rd, "state.db"))
con.execute("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("Src/MesProc.cpp", "snapshot", pos, pos + 5, "szLot", "szLot", "rule", "TEST", "", 0, "accepted", "시험용: 겹치는 수정"))
con.commit()
con.close()
cE2, tE2 = cli("run", "--resume", rd, "--no-llm")
srcE = out_text(os.path.join(work, "E"), "changed", "Src", "MesProc.cpp")
check("겹침: 다른 수정과 범위가 겹치면 그 묶음은 하나도 바꾸지 않음(전문 버퍼의 수정은 그대로 들어감)",
      "struct BuildListRec" not in srcE and "char szLot[MAX_ITEM][LEN_CODE + 1];" in srcE and "pBody->szQty" in srcE
      and "다른 수정(규칙 수정 등)과 범위가 겹침" in out_text(os.path.join(work, "E"), "struct_scan.md"))

# ---------------------------------------------------------------- 5. 요청문: 일반 명칭이면 탐지된 전체를 대상으로
mock = Mock(["MES", "Data", "TC"])
cF, tF = cli("run", "--package", pkg, "--ask", "MES 전문 Data 배열을 TC 별 구조체로 개선해 줘", "--out", os.path.join(work, "F"), "--llm-endpoint", mock.url,
             "--from-vs", "2010", "--to-vs", "2022", "--no-compile")
srcF = out_text(os.path.join(work, "F"), "changed", "Src", "MesProc.cpp")
planF = out_text(os.path.join(work, "F"), "ask_plan.json")
check("요청문(일반 명칭): 배열 의도가 구조 분석으로 넘어가 요청 파일과 같은 결과가 나옴. 모델 호출은 분석 1회",
      srcF == srcA and mock.calls == 1 and "구조 분석으로 처리" in planF and "ASK-1-1-buf" in planF and "코드에서 탐지된 전체" in planF)
issF = rows(os.path.join(work, "F"), "SELECT path, line FROM issues WHERE source='request' AND status='manual'")
check("요청문(일반 명칭): 미처리 기록에 파일과 줄이 남음", issF and all(i["path"] and i["line"] > 0 for i in issF))
mock.close()
mock = Mock(["BuildList", "TC"])
cG, tG = cli("run", "--package", pkg, "--ask", "BuildList 의 배열을 구조체로 개선해 줘", "--out", os.path.join(work, "G"), "--llm-endpoint", mock.url,
             "--from-vs", "2010", "--to-vs", "2022", "--no-compile")
srcG = out_text(os.path.join(work, "G"), "changed", "Src", "MesProc.cpp")
check("요청문(함수 이름): 그 함수만 대상으로 좁힘", "struct BuildListRec" in srcG and "pBody->szQty" not in srcG and "msg_pBuf" not in srcG)
mock.close()

# ---------------------------------------------------------------- 6. 옛 변환기: 뜻이 틀린 변환을 만들지 않는다
mock = Mock(["g_szName", "g_nCnt"])
cH, tH = cli("run", "--package", pkg, "--ask", "g_szName, g_nCnt 배열을 구조체로 바꾼다", "--out", os.path.join(work, "H"), "--llm-endpoint", mock.url,
             "--from-vs", "2010", "--to-vs", "2022", "--no-compile")
issH = rows(os.path.join(work, "H"), "SELECT path, line, note FROM issues WHERE source='request' AND status='manual'")
check("옛 변환기: 문자열 배열을 글자 필드로 쪼개지 않고, 레코드 번호를 필드 번호로 삼지 않음(수정 0건, 위치와 사유를 남김)",
      not rows(os.path.join(work, "H"), "SELECT id FROM edits") and len(issH) == 2 and all(i["path"] == "Src/Table.cpp" and i["line"] > 0 for i in issH)
      and any("문자열의 배열로 보임" in i["note"] for i in issH) and any("레코드 번호로 보임" in i["note"] for i in issH))
mock.close()

# ---------------------------------------------------------------- 7. 분석 함수 단위 확인
ns = V._selftest_ns(os.path.join(work, "U"), pkg, "", from_vs="2010", no_compile=True, no_llm=True, until="S2")
ctx = V._make_ctx(ns)
try:
    V.run_pipeline(ctx, [s for s, _n in V.STAGES])
    files = [r["path"] for r in ctx.store.q("SELECT path FROM files WHERE kind='source' ORDER BY path")]
    sx = V._sx_structs(ctx)
    res = V._sx_scan(ctx, files)
    hdr = sx["by_name"]["MES_HDR100"]
    check("구조체 표: char 만 있는 구조체의 배치를 확정하고(크기 100), 클래스와 char 가 아닌 멤버가 있는 구조체는 확정하지 않음",
          hdr["ok"] and hdr["size"] == 100 and [x["off"] for x in hdr["nodes"]] == [0, 5, 11, 15, 35, 55, 69]
          and not sx["by_name"]["CMesProc"]["ok"] and not sx["by_name"]["ItemDto"]["ok"] and "padding" in sx["by_name"]["ItemDto"]["why"])
    by = {(f["func"].split("::")[-1], r["line"]): r for f in res["fns"] for r in f["fn"].rows}
    st_ = sorted({(k[0], V._sx_state(r)) for k, r in by.items()})
    check("커서 추적: 상수 증가는 확정, 반복 안은 상대 위치, 실행 중 길이와 반복 뒤는 모름",
          ("ParseInsp", "ok") in st_ and ("MakeTestMsg", "nolen") in st_ and ("MakeTestMsg", "nopos") in st_ and ("BuildList", "rel") in st_
          and ("BuildList", "nopos") in st_ and ("ConvHeader", "nolen") in st_)
    g = {x["func"].split("::")[-1]: x for x in res["groups"]}
    check("묶음 탐지: 지역 배열만 묶고(5개, 2개), 파일 범위 배열은 묶지 않음",
          sorted(len(x["arrays"]) for x in res["groups"]) == [2, 5] and not g["BuildList"]["blocked"] and g["BuildListBlocked"]["blocked"])
finally:
    ctx.store.close()
    V._SINK.close()

# ---------------------------------------------------------------- 8. 경계 조건(C 파일, C89 로도 컴파일되는가)
EDGE_H = r"""#ifndef EDGE_DEF_H
#define EDGE_DEF_H
/* 합성 예제: 경계 조건 (C 파일) */
#include <stdio.h>
#include <string.h>
#define EDGE_HDR_LEN 20
typedef struct { char szTc[5]; char szSeq[6]; char szFlag[1]; char szLen[8]; } EDGE_HDR;
typedef struct { char szLot[10]; char szQty[6]; char cEnd; } EDGE_BODY;
#pragma pack(push, 1)
typedef struct { char szId[4]; int nValue; short nKind; } EDGE_BIN;
#pragma pack(pop)
typedef struct { char szId[4]; int nValue; } EDGE_PAD;
typedef struct { char szA[4]; char szB[6]; } EDGE_ALIAS, *LPEDGE_ALIAS;
#endif
"""
EDGE_ALL_H = r"""#ifndef EDGE_ALL_H
#define EDGE_ALL_H
/* 다른 헤더를 거쳐 include 하는 경우 */
#include "EdgeDef.h"
#endif
"""
EDGE_VIA_C = r"""/* 합성 예제: 구조체의 정의를 다른 헤더를 거쳐 include 한다 */
#include "EdgeAll.h"

int EdgeVia(const char* pBuf, char* pTc, char* pSeq, char* pFlag, char* pLen)
{
    int nPos = 0;
    memcpy(pTc, pBuf + nPos, 5);
    nPos += 5;
    memcpy(pSeq, pBuf + nPos, 6);
    nPos += 6;
    memcpy(pFlag, pBuf + nPos, 1);
    nPos += 1;
    memcpy(pLen, pBuf + nPos, 8);
    nPos += 8;
    return nPos;
}
"""
EDGE_C = r"""/* 합성 예제: 경계 조건 (C 파일) */
#include "EdgeDef.h"

/* 겹쳐 보는 구조체가 버퍼의 중간에 놓인다 */
int EdgeOffsetOverlay(char* pBuf, const char* pLot, int nQty)
{
    EDGE_BODY* pBody = (EDGE_BODY*)(pBuf + EDGE_HDR_LEN);
    char szTmp[16];
    memcpy(pBody->szLot, pLot, 10);
    sprintf(szTmp, "%06d", nQty);
    memcpy(pBuf + EDGE_HDR_LEN + 10, szTmp, 6);
    pBuf[EDGE_HDR_LEN + 16] = 0x03;
    pBuf[EDGE_HDR_LEN + 1] = 'X';
    memcpy(pBuf + EDGE_HDR_LEN + 10, szTmp, 5);
    sprintf(pBuf + EDGE_HDR_LEN + 10, "%05d", nQty);
    memcpy(pBuf + 5, "000001", 6);
    return EDGE_HDR_LEN + 17;
}

/* 겹쳐 보는 포인터가 갈래 안에서만 보인다 */
int EdgeBranchOverlay(char* pBuf, int nType)
{
    if (nType == 1)
    {
        EDGE_HDR* pHdr = (EDGE_HDR*)pBuf;
        memcpy(pHdr->szTc, "TC001", 5);
        memcpy(pBuf + 5, "000001", 6);
    }
    memcpy(pBuf + 11, "Y", 1);
    memcpy(pBuf + 12, "00000020", 8);
    return 20;
}

/* 읽기 전용으로 겹쳐 본다 */
int EdgeConstOverlay(const char* pBuf, char* pOut)
{
    const EDGE_HDR* pHdr = (const EDGE_HDR*)pBuf;
    memcpy(pOut, pHdr->szTc, 5);
    memcpy(pOut + 5, pBuf + 5, 6);
    memcpy(pOut + 11, pBuf + 11, 1);
    return 12;
}

/* char 가 아닌 멤버가 있는 구조체(pack 1) */
int EdgeBin(const char* pBuf, EDGE_BIN* pOut)
{
    int nPos = 0;
    memcpy(pOut->szId, pBuf + nPos, 4);
    nPos += 4;
    memcpy(&pOut->nValue, pBuf + nPos, sizeof(int));
    nPos += sizeof(int);
    memcpy(&pOut->nKind, pBuf + nPos, sizeof(short));
    nPos += sizeof(short);
    return nPos;
}

/* 지역 배열의 묶음: 한 문장에 여럿 선언, sizeof, 0 으로 초기화한 선언 */
int EdgeGroup(char* pOut, int nCount)
{
    char szKey[8][4] = {0}, szVal[8][12] = {0};
    int nLen[8] = {0};
    int i, n = 0;
    for (i = 0; i < nCount && i < (int)(sizeof(nLen) / sizeof(nLen[0])); i++)
    {
        sprintf(szKey[i], "K%02d", i);
        sprintf(szVal[i], "VALUE-%03d", i * 7);
        nLen[i] = (int)strlen(szVal[i]);
    }
    for (i = 0; i < nCount && i < 8; i++)
        n += sprintf(pOut + n, "%s=%s(%d,%d);", szKey[i], szVal[i], nLen[i], (int)sizeof(szVal[i]));
    return n + (int)(sizeof(szKey) + sizeof(szVal));
}

/* 포인터 별명(typedef ... *LPNAME)으로 겹쳐 본다 */
int EdgeAlias(char* pBuf, const char* pA)
{
    LPEDGE_ALIAS pMsg = (LPEDGE_ALIAS)pBuf;
    memcpy(pMsg->szA, pA, 4);
    memcpy(pBuf + 4, "BBBBBB", 6);
    return 10;
}

/* 0 으로 지우는 문장 사이에 묶음이 아닌 변수의 문장이 끼어 있다 */
int EdgeGroup2(char* pOut, int nCount)
{
    char szName[4][8];
    char szTmp[32];
    int  nNo[4];
    int  i, n = 0;
    memset(szName, 0, sizeof(szName));
    memset(szTmp, 0, sizeof(szTmp));
    memset(nNo, 0, sizeof(nNo));
    for (i = 0; i < nCount && i < 4; i++)
    {
        sprintf(szName[i], "N%d", i);
        nNo[i] = i * 3;
    }
    for (i = 0; i < nCount && i < 4; i++)
    {
        sprintf(szTmp, "%s#%d,", szName[i], nNo[i]);
        n += sprintf(pOut + n, "%s", szTmp);
    }
    return n;
}

/* 한 함수에 묶음이 둘(레코드 수가 다르다) */
int EdgeTwoGroups(char* pOut)
{
    char szA[3][6];
    int  nA[3];
    char szB[2][6];
    int  nB[2];
    int  i, n = 0;
    for (i = 0; i < 3; i++)
    {
        sprintf(szA[i], "A%d", i);
        nA[i] = i + 10;
    }
    for (i = 0; i < 2; i++)
    {
        sprintf(szB[i], "B%d", i);
        nB[i] = i + 20;
    }
    for (i = 0; i < 2; i++)
        n += sprintf(pOut + n, "%s%d%s%d;", szA[i], nA[i], szB[i], nB[i]);
    return n;
}

/* 막히는 묶음: 일부만 0 으로 지운다 */
int EdgeBlockedPart(char* pOut)
{
    char szP[3][6];
    int  nP[3];
    int  i, n = 0;
    memset(szP, 0, sizeof(szP));
    for (i = 0; i < 3; i++)
    {
        sprintf(szP[i], "P%d", i);
        nP[i] = i;
    }
    for (i = 0; i < 3; i++)
        n += sprintf(pOut + n, "%s%d", szP[i], nP[i]);
    return n;
}

/* 막히는 묶음: 0 이 아닌 초기화 목록 */
int EdgeBlockedInit(char* pOut)
{
    int nId[3] = {7, 8, 9};
    int nVal[3];
    int i, n = 0;
    for (i = 0; i < 3; i++)
        nVal[i] = nId[i] * 2;
    for (i = 0; i < 3; i++)
        n += sprintf(pOut + n, "%d:%d;", nId[i], nVal[i]);
    return n;
}

/* 막히는 묶음: 행 하나를 가리키며 배열 전체의 크기를 준다(메모리가 이어져 있다고 보는 코드) */
int EdgeBlockedSpan(char* pOut)
{
    char szS[2][6];
    int  nS[2];
    int  i, n = 0;
    memset(szS[0], 'x', sizeof(szS));
    for (i = 0; i < 2; i++)
    {
        szS[i][5] = 0;
        nS[i] = i;
    }
    for (i = 0; i < 2; i++)
        n += sprintf(pOut + n, "%s%d", szS[i], nS[i]);
    return n;
}

/* 막히는 묶음: 매크로가 배열을 이름으로 가리킨다 */
#define EDGE_AT(i) szM[i]
int EdgeBlockedMacro(char* pOut)
{
    char szM[2][6];
    int  nM[2];
    int  i, n = 0;
    for (i = 0; i < 2; i++)
    {
        sprintf(szM[i], "M%d", i);
        nM[i] = i;
    }
    for (i = 0; i < 2; i++)
        n += sprintf(pOut + n, "%s%d", EDGE_AT(i), nM[i]);
    return n;
}

/* 막히는 묶음: 조건문 안에서 배열 하나만 지운다 */
int EdgeBlockedCond(char* pOut, int bReset)
{
    char szC[2][6];
    int  nC[2];
    int  i, n = 0;
    for (i = 0; i < 2; i++)
    {
        sprintf(szC[i], "C%d", i);
        nC[i] = i;
    }
    if (bReset)
        memset(nC, 0, sizeof(nC));
    for (i = 0; i < 2; i++)
        n += sprintf(pOut + n, "%s%d", szC[i], nC[i]);
    return n;
}
"""
EDGE_MAIN = r"""/* 시험용 */
#include "EdgeDef.h"
int EdgeOffsetOverlay(char* pBuf, const char* pLot, int nQty);
int EdgeBranchOverlay(char* pBuf, int nType);
int EdgeConstOverlay(const char* pBuf, char* pOut);
int EdgeBin(const char* pBuf, EDGE_BIN* pOut);
int EdgeGroup(char* pOut, int nCount);
int EdgeAlias(char* pBuf, const char* pA);
int EdgeGroup2(char* pOut, int nCount);
int EdgeVia(const char* pBuf, char* pTc, char* pSeq, char* pFlag, char* pLen);
int EdgeTwoGroups(char* pOut);
int EdgeBlockedPart(char* pOut);
int EdgeBlockedInit(char* pOut);
int EdgeBlockedCond(char* pOut, int bReset);
int EdgeBlockedSpan(char* pOut);
int EdgeBlockedMacro(char* pOut);
static void dump(const char* tag, const char* p, int n)
{
    int i;
    printf("%s[%d]:", tag, n);
    for (i = 0; i < n; i++)
        printf("%02x", (unsigned char)p[i]);
    printf("\n");
}
int main(void)
{
    char buf[512], out[512];
    EDGE_BIN bin;
    int n;
    memset(buf, '.', sizeof(buf));
    n = EdgeOffsetOverlay(buf, "LOT-000001", 4321);
    dump("off", buf, n + 2);
    memset(buf, '.', sizeof(buf));
    n = EdgeBranchOverlay(buf, 1);
    dump("br1", buf, n + 2);
    memset(buf, '.', sizeof(buf));
    n = EdgeBranchOverlay(buf, 2);
    dump("br2", buf, n + 2);
    memset(out, '.', sizeof(out));
    n = EdgeConstOverlay("TC00965432199999999", out);
    dump("const", out, n + 2);
    n = EdgeBin("ID01\x01\x02\x03\x04\x05\x06ZZ", &bin);
    printf("bin %d %.4s %d %d\n", n, bin.szId, bin.nValue, (int)bin.nKind);
    n = EdgeGroup(out, 5);
    printf("group %d %s\n", n, out);
    memset(buf, '.', sizeof(buf));
    n = EdgeAlias(buf, "AAAA");
    dump("alias", buf, n + 2);
    n = EdgeGroup2(out, 3);
    printf("group2 %d %s\n", n, out);
    memset(out, 0, sizeof(out));
    n = EdgeVia("TC00965432199999999ZZ", out, out + 10, out + 20, out + 30);
    printf("via %d %s %s %s %s\n", n, out, out + 10, out + 20, out + 30);
    n = EdgeTwoGroups(out);
    printf("two %d %s\n", n, out);
    n = EdgeBlockedPart(out);
    printf("part %d %s\n", n, out);
    n = EdgeBlockedInit(out);
    printf("init %d %s\n", n, out);
    n = EdgeBlockedCond(out, 1);
    printf("cond %d %s\n", n, out);
    n = EdgeBlockedSpan(out);
    printf("span %d %s\n", n, out);
    n = EdgeBlockedMacro(out);
    printf("macro %d %s\n", n, out);
    return 0;
}
"""
epkg = os.path.join(work, "EDGEPKG")
V.write_bytes(os.path.join(epkg, "Src", "EdgeDef.h"), EDGE_H.replace("\n", "\r\n").encode("cp949"))
V.write_bytes(os.path.join(epkg, "Src", "MesEdge.c"), EDGE_C.replace("\n", "\r\n").encode("cp949"))
V.write_bytes(os.path.join(epkg, "Src", "EdgeAll.h"), EDGE_ALL_H.replace("\n", "\r\n").encode("cp949"))
V.write_bytes(os.path.join(epkg, "Src", "EdgeVia.c"), EDGE_VIA_C.replace("\n", "\r\n").encode("cp949"))


def build_c(tag, changed_root, std):
    if not shutil.which("gcc"):
        return None
    d = os.path.join(work, "buildc_" + tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f in ("EdgeDef.h", "EdgeAll.h", "MesEdge.c", "EdgeVia.c"):
        src = os.path.join(epkg, "Src", f)
        if changed_root:
            alt = os.path.join(run_dir(changed_root), "out", "changed", "Src", f)
            src = alt if os.path.isfile(alt) else src
        open(os.path.join(d, f), "w", encoding="utf-8", newline="\n").write(open(src, "rb").read().decode("cp949").replace("\r\n", "\n"))
    open(os.path.join(d, "main.c"), "w", encoding="utf-8", newline="\n").write(EDGE_MAIN)
    p = subprocess.run(["gcc", "-std=" + std, "-pedantic-errors", "-w", "-o", os.path.join(d, "t"), os.path.join(d, "main.c"), os.path.join(d, "MesEdge.c"), os.path.join(d, "EdgeVia.c")], capture_output=True)
    if p.returncode != 0:
        return "컴파일 실패: " + p.stderr.decode("utf-8", "replace")[:800]
    return subprocess.run([os.path.join(d, "t")], capture_output=True, cwd=d).stdout.decode("utf-8", "replace")


cJ, tJ = cli("run", "--package", epkg, "--request", reqA, "--out", os.path.join(work, "J"), *BASE)
srcJ = out_text(os.path.join(work, "J"), "changed", "Src", "MesEdge.c")
scanJ = out_text(os.path.join(work, "J"), "struct_scan.md")
check("경계: 겹쳐 보는 구조체가 버퍼 중간에 놓여도 그 위치를 빼고 대응시킴(char 하나짜리 필드, 필드 안의 글자 포함)",
      "memcpy(pBody->szQty, szTmp, 6);" in srcJ and "pBody->cEnd = 0x03;" in srcJ and "pBody->szLot[1] = 'X';" in srcJ)
check("경계: 길이가 필드와 다른 접근, NUL 을 더 쓰는 sprintf, 구조체 범위 밖의 접근은 그대로 두고 사유를 남김",
      "memcpy(pBuf + EDGE_HDR_LEN + 10, szTmp, 5);" in srcJ and 'sprintf(pBuf + EDGE_HDR_LEN + 10, "%05d", nQty);' in srcJ and 'memcpy(pBuf + 5, "000001", 6);' in srcJ
      and "필드의 경계와 다름(위치 10, 길이 5" in scanJ and "sprintf 는 끝에 NUL 을 더 써서 필드 밖으로 나감" in scanJ and "구조체의 범위(17바이트) 밖" in scanJ)
check("경계: 갈래 안에서 선언한 포인터는 그 갈래 안의 접근에만 쓰고, 밖의 접근은 그대로 둠",
      'memcpy(pHdr->szSeq, "000001", 6);' in srcJ and 'memcpy(pBuf + 11, "Y", 1);' in srcJ and "보이는 범위 밖" in scanJ)
check("경계: const 로 겹쳐 본 버퍼의 읽기는 그 포인터의 필드로",
      "memcpy(pOut + 5, pHdr->szSeq, 6);" in srcJ and "memcpy(pOut + 11, pHdr->szFlag, 1);" in srcJ)
check("경계: pack(1) 안의 구조체는 char 가 아닌 멤버도 대응시키고(주소 연산자), 채움이 있을 수 있는 구조체는 쓰지 않음",
      "EDGE_BIN msg_pBuf;" in srcJ and "memcpy(&pOut->nValue, &msg_pBuf.nValue, sizeof(int));" in srcJ and "memcpy(&pOut->nKind, &msg_pBuf.nKind, sizeof(short));" in srcJ
      and "EDGE_PAD" not in srcJ)
check("경계: 한 문장에 여럿 선언한 배열, 0 으로 초기화한 선언, sizeof(이름)도 묶음으로 바꿈",
      "struct EdgeGroupRec" in srcJ and "} stRec[8] = {0};" in srcJ and "i < (int)(sizeof(stRec) / sizeof(stRec[0]));" in srcJ
      and "(sizeof(stRec[0].szKey) * (8)) + (sizeof(stRec[0].szVal) * (8))" in srcJ and "stRec[i].szVal, stRec[i].nLen" in srcJ)
check("경계: 포인터 별명(LPNAME)으로 겹쳐 본 버퍼도 그 포인터의 필드로", 'memcpy(pMsg->szB, "BBBBBB", 6);' in srcJ)
check("경계: 0 으로 지우는 문장 사이에 묶음과 무관한 문장이 있어도 하나로 합침(그 문장은 그대로)",
      "struct EdgeGroup2Rec" in srcJ and srcJ.count("memset(stRec, 0, sizeof(stRec));") == 1 and "memset(szTmp, 0, sizeof(szTmp));" in srcJ
      and "memset(nNo" not in srcJ and "char szTmp[32];" in srcJ)
check("경계: 한 함수에 묶음이 둘이면 이름이 겹치지 않게 따로 만듦",
      "struct EdgeTwoGroupsRec" in srcJ and "} stRec[3];" in srcJ and "struct EdgeTwoGroupsRec2" in srcJ and "} stRec2[2];" in srcJ
      and "stRec[i].szA, stRec[i].nA, stRec2[i].szB, stRec2[i].nB" in srcJ)
check("경계: 일부만 0 으로 지우는 묶음, 0 이 아닌 초기화 목록, 조건문 안의 memset 은 묶음 전체를 그대로 두고 사유를 남김",
      "char szP[3][6];" in srcJ and "int nId[3] = {7, 8, 9};" in srcJ and "memset(nC, 0, sizeof(nC));" in srcJ and "char szC[2][6];" in srcJ
      and "묶음의 일부만 0 으로 지움(szP)" in scanJ and "초기화 목록이 있는 선언(nId)" in scanJ and "이름을 통째로 사용(nC)" in scanJ)
check("경계: 배열 전체를 이어진 메모리로 다루는 문장, 매크로가 이름으로 가리키는 배열이 있으면 묶음을 그대로 둠",
      "char szS[2][6];" in srcJ and "memset(szS[0], 'x', sizeof(szS));" in srcJ and "char szM[2][6];" in srcJ
      and "행 하나(szS)에 행보다 긴 길이를 줌" in scanJ and "이름 szM 이(가) 매크로의 본문에 나옴" in scanJ)
viaJ = out_text(os.path.join(work, "J"), "changed", "Src", "EdgeVia.c")
check("경계: 구조체의 정의를 다른 헤더를 거쳐 include 해도 로컬 구조체 복사로 바꿈",
      "EDGE_HDR msg_pBuf;" in viaJ and "memcpy(pLen, msg_pBuf.szLen, 8);" in viaJ)
c_base, c_new = build_c("orig", None, "c99"), build_c("J", os.path.join(work, "J"), "c99")
if c_base is None:
    print("[건너뜀] gcc 가 없어 C 파일의 동작 비교는 하지 않음")
else:
    check("동작(C): 변환한 결과를 gcc 로 컴파일해 실행한 출력이 원본과 같음", c_base and not c_base.startswith("컴파일 실패") and c_new == c_base)
    c90 = build_c("J90", os.path.join(work, "J"), "c90")
    check("동작(C): 넣은 선언의 자리가 C89 규칙(선언이 문장보다 앞)에도 맞음", c90 == c_base)

# ---------------------------------------------------------------- 9. 커서 값을 잘못 알 수 있는 꼴: 틀린 위치로 바꾸지 않는다
HAZ_CPP = r"""// 합성 예제: 커서 값을 따라가기 어려운 꼴. 값을 잘못 알면 엉뚱한 필드로 바꾸게 되는 문장에 표시(H번호)를 달았다
#include <stdio.h>
#include <string.h>

typedef struct { char szA[5]; char szB[6]; char szC[10]; } HAZ_MSG;

static void Advance(int& n, int k) { n += k; }
static void Bump(int* pn) { *pn += 5; }
#define STEP(n, k) ((n) += (k))

int HazOk(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 5);
    n += 5;
    memcpy(pBuf + n, p, 6);      // H01
    return n;
}

int HazSwitch(char* pBuf, const char* p, int nType)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
    switch (nType)
    {
    case 1:
        n += 5;
        memcpy(pBuf + n, p, 6);  // H02
        break;
    case 2:
        memcpy(pBuf + n, p, 6);  // H03
        n += 11;
        memcpy(pBuf + n, p, 10); // H04
        break;
    case 3:
        n += 5;
    case 4:
        memcpy(pBuf + n, p, 6);  // H05
        break;
    }
    memcpy(pBuf + n, p, 6);      // H06
    return n;
}

int HazRef(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
    Advance(n, 5);
    memcpy(pBuf + n, p, 5);      // H07
    return n;
}

int HazMacro(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
    STEP(n, 5);
    memcpy(pBuf + n, p, 5);      // H08
    return n;
}

int HazAddr(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    int* pn = &n;
    memcpy(pMsg->szA, p, 1);
    *pn += 5;
    memcpy(pBuf + n, p, 5);      // H09
    Bump(&n);
    return n;
}

int HazTry(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
    try
    {
        n += 5;
        memcpy(pBuf + n, p, 6);  // H10
    }
    catch (...)
    {
        n = 0;
    }
    memcpy(pBuf + n, p, 5);      // H11
    return n;
}

int HazGoto(char* pBuf, const char* p, int nRetry)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
again:
    memcpy(pBuf + n, p, 5);      // H12
    n += 5;
    if (nRetry-- > 0)
        goto again;
    return n;
}

int HazComma(char* pBuf, const char* p)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0, m = 0;
    memcpy(pMsg->szA, p, 1);
    m = 1, n += 5;
    memcpy(pBuf + n, p, 5);      // H13
    return n + m;
}

int HazBranch(char* pBuf, const char* p, int bLong)
{
    HAZ_MSG* pMsg = (HAZ_MSG*)pBuf;
    int n = 0;
    memcpy(pMsg->szA, p, 1);
    if (bLong)
        n += 5;
    memcpy(pBuf + n, p, 5);      // H14
    return n;
}
"""
HAZ_MAIN = r"""
static void dump(const char* tag, const char* p, int n)
{
    int i;
    printf("%s:", tag);
    for (i = 0; i < n; i++)
        printf("%c", p[i]);
    printf("\n");
}
int main()
{
    char buf[64];
    const char* src = "abcdefghijklmnop";
    int k;
    memset(buf, '.', sizeof(buf)); HazOk(buf, src); dump("ok", buf, 24);
    for (k = 1; k <= 5; k++) { memset(buf, '.', sizeof(buf)); HazSwitch(buf, src, k); dump("sw", buf, 24); }
    memset(buf, '.', sizeof(buf)); HazRef(buf, src); dump("ref", buf, 24);
    memset(buf, '.', sizeof(buf)); HazMacro(buf, src); dump("mac", buf, 24);
    memset(buf, '.', sizeof(buf)); HazAddr(buf, src); dump("adr", buf, 24);
    memset(buf, '.', sizeof(buf)); HazTry(buf, src); dump("try", buf, 24);
    memset(buf, '.', sizeof(buf)); HazGoto(buf, src, 2); dump("goto", buf, 24);
    memset(buf, '.', sizeof(buf)); HazComma(buf, src); dump("com", buf, 24);
    for (k = 0; k <= 1; k++) { memset(buf, '.', sizeof(buf)); HazBranch(buf, src, k); dump("br", buf, 24); }
    return 0;
}
"""
hpkg = os.path.join(work, "HAZPKG")
V.write_bytes(os.path.join(hpkg, "Src", "Haz.cpp"), HAZ_CPP.replace("\n", "\r\n").encode("cp949"))
cK, tK = cli("run", "--package", hpkg, "--request", reqA, "--out", os.path.join(work, "K"), *BASE)
srcK = out_text(os.path.join(work, "K"), "changed", "Src", "Haz.cpp")
scanK = out_text(os.path.join(work, "K"), "struct_scan.md")
mark = {m.group(1): line for line in srcK.split("\r\n") for m in [re.search(r"// (H\d\d)", line)] if m}
kept = lambda k: "pBuf + n" in mark.get(k, "")
check("커서: 순서대로 읽히는 곳은 바꿈(대조), switch 의 갈래는 들어올 때의 값에서 다시 시작, try 안은 순서대로",
      "pMsg->szB" in mark.get("H01", "") and "pMsg->szB" in mark.get("H02", "") and "pMsg->szC" in mark.get("H04", "") and "pMsg->szB" in mark.get("H10", ""))
check("커서: 값을 확신할 수 없는 곳은 바꾸지 않음(이어지는 case, switch 뒤, 참조 인자, 매크로, 주소를 넘긴 변수, try 뒤, 레이블, 쉼표 식, 분기 뒤)",
      all(kept(k) for k in ("H03", "H05", "H06", "H07", "H08", "H09", "H11", "H12", "H13", "H14")))
check("커서: 바꾸지 않은 곳마다 사유가 남음",
      all(w in scanK for w in ("switch 의 갈래가 이어짐", "switch 뒤", "함수 Advance 에 넘긴 뒤", "함수 STEP 에 넘긴 뒤", "주소를 다른 곳에 넘긴 뒤",
                               "레이블 뒤", "읽지 못한 꼴의 대입", "분기에 따라 값이 다름")) and "필드의 경계와 다름(위치 0, 길이 6" in scanK
      and ("try 블록 뒤" in scanK or "읽지 않은 블록 안에서 바뀜" in scanK))
if shutil.which("g++"):
    outs = []
    for tag, text in (("hz_orig", HAZ_CPP), ("hz_new", srcK.replace("\r\n", "\n"))):
        d = os.path.join(work, "build_" + tag)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "haz.cpp"), "w", encoding="utf-8", newline="\n").write(text + HAZ_MAIN)
        p = subprocess.run(["g++", "-w", "-o", os.path.join(d, "t"), os.path.join(d, "haz.cpp")], capture_output=True)
        outs.append(subprocess.run([os.path.join(d, "t")], capture_output=True, cwd=d).stdout.decode("utf-8", "replace") if p.returncode == 0
                    else "컴파일 실패: " + p.stderr.decode("utf-8", "replace")[:500])
    check("커서: 위 예제의 변환 전후 출력이 같음(g++)", outs[0] and not outs[0].startswith("컴파일 실패") and outs[0] == outs[1])

ok = sum(1 for _n, c in checks if c)
for name, cond in checks:
    print("[%s] %s" % ("통과" if cond else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), ok))
sys.exit(0 if ok == len(checks) else 1)
