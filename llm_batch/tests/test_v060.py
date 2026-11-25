"""0.6.0 에서 더한 기능의 시험 가운데 0.9.0 에 남은 것.

사용: python test_v060.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  DB 접근 기술과 HMI 구성 요소의 기본 규칙: 헤더, 클래스, 연결 문자열로 알아내 설정 항목과 수동 검토로 낸다.
  선행 컴파일 소스(.pc)도 소스로 본다.
배열 -> 구조체의 시험은 test_v090.py 로 옮겼다. 환경값 축에 따른 확인 목록은 0.9.0 에서 뺐다.
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


# ------------------------------------------------------------------ DB·HMI 기본 규칙
pkg3 = os.path.join(work, "DBHMI")
write(pkg3, "Src/Db.pc", ['#include "sqlca.h"', "void Load(void)", "{", "    EXEC SQL COMMIT;", "    char* p = strdup(\"x\");", "}"])
write(pkg3, "Src/Dao.cpp", ["#include <afxdao.h>", '#import "c:\\Program Files\\Common Files\\System\\ado\\msado15.dll" no_namespace rename("EOF", "adoEOF")',
                            "CDaoDatabase g_db;", 'const char* g_cs = "Provider=SQLOLEDB;Data Source=srv";', "// CDaoRecordset 는 주석이라 잡지 않는다"])
write(pkg3, "Src/Hmi.cpp", ['#include "opcda.h"', '#import "mscomm32.ocx"', "#include <gl\\glaux.h>", "void Help(CWnd* w) { ::WinHelp(w->m_hWnd, \"app.hlp\", HELP_CONTENTS, 0); }",
                            "IOPCServer* g_srv;"])
code, text = cli("run", "--package", pkg3, "--from-vs", "2008", "--to-vs", "2022", "--no-llm", "--no-compile", "--deprecated", "fix", "--out", os.path.join(work, "r3"))
settings, manual3, pc = out_of(os.path.join(work, "r3"), "project_settings.md"), out_of(os.path.join(work, "r3"), "manual_review.md"), out_of(os.path.join(work, "r3"), "changed", "Src", "Db.pc")
check("DB 규칙: 임베디드 SQL, ADO 가져오기를 설정 항목으로 냄", "[DB] 임베디드 SQL" in settings and "[DB] ADO 형식 라이브러리" in settings and "선행 컴파일러" in settings)
check("DB 규칙: DAO 와 SQL Server 의 구 공급자(연결 문자열)를 수동 검토로, 주석은 잡지 않음",
      "R-T3-DB-DAO" in manual3 and "R-T3-DB-SQLNCLI" in manual3 and "2곳(첫 위치 1줄)" in manual3)
check("HMI 규칙: OPC DA, ActiveX(OCX)를 설정 항목으로, GLAUX 와 WinHelp 를 수동 검토로",
      "[HMI] OPC DA" in settings and "KB5004442" in settings and "[HMI] ActiveX" in settings and "[HMI] Visual Basic 6" in settings
      and "R-T3-HMI-GLAUX" in manual3 and "R-T3-HMI-WINHELP" in manual3)
check("선행 컴파일 소스(.pc)도 소스로 보고 규칙을 적용함(strdup -> _strdup, EXEC SQL 은 그대로)",
      "_strdup(\"x\")" in pc and "EXEC SQL COMMIT;" in pc)
code, text = cli("rules", "--from-vs", "2008", "--to-vs", "2022", "--platform", "x64")
line = {l.split()[0]: l for l in text.splitlines() if l.startswith("R-")}
check("x64 에서만 켜지는 DB·HMI 규칙(ODBC 길이 인자, VB6 컨트롤)", "켜짐" in line.get("R-T3-DB-ODBC-LEN", "") and "켜짐" in line.get("R-T3-HMI-VB6-OCX", ""))
code, text = cli("rules", "--from-vs", "2008", "--to-vs", "2022")
line = {l.split()[0]: l for l in text.splitlines() if l.startswith("R-")}
check("x86 에서는 그 규칙이 꺼짐", "꺼짐" in line.get("R-T3-DB-ODBC-LEN", "") and "꺼짐" in line.get("R-T3-HMI-VB6-OCX", ""))

for name, okv in checks:
    print("[%s] %s" % ("통과" if okv else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, okv in checks if okv)))
sys.exit(0 if all(okv for _n, okv in checks) else 1)
