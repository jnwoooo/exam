"""0.6.0 에서 더한 기능의 시험.

사용: python test_v060.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 배열 -> 구조체: 선언을 구조체 선언으로 바꾸고 정의를 만든다. 메모리 함수의 인자에는 & 를 붙인다.
     인덱스 이름의 값을 찾지 못하면 선언을 수동 검토로 남긴다(이유와 함께).
  2. DB 접근 기술과 HMI 구성 요소의 기본 규칙: 헤더, 클래스, 연결 문자열로 알아내 설정 항목과 수동 검토로 낸다.
     선행 컴파일 소스(.pc)도 소스로 본다.
  3. 환경값 축에 적은 제품 이름에 따라 확인 목록을 project_settings.md 5절에 낸다.
"""
import json, os, shutil, subprocess, sys

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


def write(pkg, rel, lines):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), "\r\n".join(lines + [""]).encode("cp949"))


# ------------------------------------------------------------------ 1. 배열 -> 구조체
pkg = os.path.join(work, "ARR")
write(pkg, "Src/Tbl.cpp", ["#include <string.h>", "enum { IDX_A, IDX_B = 2, IDX_C };", "#define TBL_SIZE 4", "static unsigned int g_tbl[TBL_SIZE] = {0};",
                           "void Send(unsigned int* p);", "void f(int i)", "{", "    memset(g_tbl, 0, sizeof(g_tbl));", "    g_tbl[IDX_A] = 1;",
                           "    g_tbl[IDX_C] = g_tbl[IDX_B] + 1;", "    Send(g_tbl);", "    g_tbl[i] = 0;", "}"])
req = os.path.join(work, "req_arr.json")
with open(req, "w", encoding="utf-8") as fh:
    json.dump({"features": [{"id": "REQ-A", "type": "array_to_struct", "target": "g_tbl", "struct_name": "TBL_T",
                             "mapping": {"IDX_A": "a", "IDX_B": "b", "IDX_C": "c"}, "llm_other_uses": False}]}, fh)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--request", req, "--no-llm", "--no-compile", "--out", os.path.join(work, "r1"))
src, manual, report = out_of(os.path.join(work, "r1"), "changed", "Src", "Tbl.cpp"), out_of(os.path.join(work, "r1"), "manual_review.md"), out_of(os.path.join(work, "r1"), "report.md")
check("배열 선언을 구조체 선언으로: 정의를 만들고(인덱스 순서, 빈 자리는 reserved) 초기값과 static 을 유지",
      "typedef struct TBL_T\r\n{\r\n    unsigned int a;\r\n    unsigned int reserved_1;\r\n    unsigned int b;\r\n    unsigned int c;\r\n} TBL_T;\r\n"
      "static TBL_T g_tbl = {0};" in src)
check("배열: 상수 인덱스는 필드로, 메모리 함수의 인자에는 & 를 붙이고 sizeof 는 그대로",
      "g_tbl.a = 1;" in src and "g_tbl.c = g_tbl.b + 1;" in src and "memset(&g_tbl, 0, sizeof(g_tbl));" in src)
check("배열: 통째로 넘기는 곳과 변수 인덱스는 그대로 두고 수동 검토로, 보고서에 변경 계약과 새 정의",
      "Send(g_tbl);" in src and "g_tbl[i] = 0;" in src and "배열 전체 사용" in manual and "변수 인덱스" in manual
      and "변경 계약" in report and "typedef struct TBL_T" in report and code == 1)
pkg2 = os.path.join(work, "ARR2")
write(pkg2, "Src/T.cpp", ["int g_v[8];", "void f() { g_v[IDX_X] = 1; }"])
req2 = os.path.join(work, "req_arr2.json")
with open(req2, "w", encoding="utf-8") as fh:
    json.dump({"features": [{"id": "REQ-B", "type": "array_to_struct", "target": "g_v", "mapping": {"IDX_X": "x"}}]}, fh)
code, text = cli("run", "--package", pkg2, "--from-vs", "2008", "--to-vs", "2022", "--request", req2, "--no-llm", "--no-compile", "--out", os.path.join(work, "r2"))
src2, manual2 = out_of(os.path.join(work, "r2"), "changed", "Src", "T.cpp"), out_of(os.path.join(work, "r2"), "manual_review.md")
# 0.8.0 부터: 선언을 못 바꾸면 사용처도 바꾸지 않는다(선언과 사용처가 어긋난 결과는 컴파일되지 않는다). 바뀐 파일이 없어야 한다
check("배열: 인덱스 이름의 값을 찾지 못하면 아무것도 적용하지 않고 이유를 수동 검토에 남김",
      src2 == "" and "적용하지 않은 이유" in manual2 and "IDX_X 의 값을 패키지에서 찾지 못함" in manual2)

# ------------------------------------------------------------------ 2, 3. DB·HMI 기본 규칙과 환경값별 확인 목록
pkg3 = os.path.join(work, "DBHMI")
write(pkg3, "Src/Db.pc", ['#include "sqlca.h"', "void Load(void)", "{", "    EXEC SQL COMMIT;", "    char* p = strdup(\"x\");", "}"])
write(pkg3, "Src/Dao.cpp", ["#include <afxdao.h>", '#import "c:\\Program Files\\Common Files\\System\\ado\\msado15.dll" no_namespace rename("EOF", "adoEOF")',
                            "CDaoDatabase g_db;", 'const char* g_cs = "Provider=SQLOLEDB;Data Source=srv";', "// CDaoRecordset 는 주석이라 잡지 않는다"])
write(pkg3, "Src/Hmi.cpp", ['#include "opcda.h"', '#import "mscomm32.ocx"', "#include <gl\\glaux.h>", "void Help(CWnd* w) { ::WinHelp(w->m_hWnd, \"app.hlp\", HELP_CONTENTS, 0); }",
                            "IOPCServer* g_srv;"])
code, text = cli("run", "--package", pkg3, "--from-vs", "2008", "--to-vs", "2022", "--no-llm", "--no-compile", "--deprecated", "fix",
                 "--axis", "os=Windows XP->Windows 10", "--axis", "db=Oracle 10g->Oracle 19c", "--axis", "hmi=iFIX 5.8", "--out", os.path.join(work, "r3"))
settings, manual3, pc = out_of(os.path.join(work, "r3"), "project_settings.md"), out_of(os.path.join(work, "r3"), "manual_review.md"), out_of(os.path.join(work, "r3"), "changed", "Src", "Db.pc")
check("DB 규칙: 임베디드 SQL, ADO 가져오기를 설정 항목으로 냄", "[DB] 임베디드 SQL" in settings and "[DB] ADO 형식 라이브러리" in settings and "선행 컴파일러" in settings)
check("DB 규칙: DAO 와 SQL Server 의 구 공급자(연결 문자열)를 수동 검토로, 주석은 잡지 않음",
      "R-T3-DB-DAO" in manual3 and "R-T3-DB-SQLNCLI" in manual3 and "2곳(첫 위치 1줄)" in manual3)
check("HMI 규칙: OPC DA, ActiveX(OCX)를 설정 항목으로, GLAUX 와 WinHelp 를 수동 검토로",
      "[HMI] OPC DA" in settings and "KB5004442" in settings and "[HMI] ActiveX" in settings and "[HMI] Visual Basic 6" in settings
      and "R-T3-HMI-GLAUX" in manual3 and "R-T3-HMI-WINHELP" in manual3)
check("선행 컴파일 소스(.pc)도 소스로 보고 규칙을 적용함(strdup -> _strdup, EXEC SQL 은 그대로)",
      "_strdup(\"x\")" in pc and "EXEC SQL COMMIT;" in pc)
check("환경값에 따른 확인 목록: OS, Oracle, HMI 의 항목이 5절에 나옴",
      "## 5. 환경값에 따른 확인 목록" in settings and "SysWOW64" in settings and "ORA-28040" in settings and "KO16MSWIN949" in settings
      and "HMI 제품의 대상 판" in settings and "32비트 전용인 경우가 많다" in settings)
code, text = cli("rules", "--from-vs", "2008", "--to-vs", "2022", "--platform", "x64")
line = {l.split()[0]: l for l in text.splitlines() if l.startswith("R-")}
check("x64 에서만 켜지는 DB·HMI 규칙(ODBC 길이 인자, VB6 컨트롤)", "켜짐" in line.get("R-T3-DB-ODBC-LEN", "") and "켜짐" in line.get("R-T3-HMI-VB6-OCX", ""))
code, text = cli("rules", "--from-vs", "2008", "--to-vs", "2022")
line = {l.split()[0]: l for l in text.splitlines() if l.startswith("R-")}
check("x86 에서는 그 규칙이 꺼짐", "꺼짐" in line.get("R-T3-DB-ODBC-LEN", "") and "꺼짐" in line.get("R-T3-HMI-VB6-OCX", ""))
notes = V.axis_notes({"axes": {"db": {"from": "MS-SQL 2008", "to": "SQL Server 2022"}, "hmi": {"from": "", "to": "Qt 5.15"}}})
check("환경값별 확인 목록: SQL Server 와 Qt", any("TrustServerCertificate" in x for _l, _v, items in notes for x in items)
      and any("moc" in x for _l, _v, items in notes for x in items))

for name, okv in checks:
    print("[%s] %s" % ("통과" if okv else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, okv in checks if okv)))
sys.exit(0 if all(okv for _n, okv in checks) else 1)
