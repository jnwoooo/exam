"""0.10.1 의 시험: 백업으로 보이는 폴더와 파일을 처리 대상에서 뺀다(--backups skip, 기본).

사용: python test_v101.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 이름 판정: 확실한 표시는 이름만으로, 약한 표시는 그 표시를 뗀 이름이 같은 폴더에 있을 때만 백업으로 본다
  2. 실행: 백업은 읽지 않는다(탐지, 수정, 스냅샷, 구조 분석). 목록과 까닭이 남는다
  3. 되살림: 프로젝트 파일이 참조하는 파일, 소스가 include 하는 헤더는 이름이 백업 같아도 처리한다
  4. 백업 때문에 구조체가 두 번 정의된 것으로 보이던 문제(구조 분석이 막힘)가 없어진다
  5. --backups keep 이면 전과 같이 전부 처리한다
예제는 합성 코드다. 실제 패키지의 이름 붙이는 방식이 다르면 판정이 달라질 수 있다.
"""
import json, os, shutil, sqlite3, subprocess, sys

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
    return open(p, "rb").read().decode("cp949" if parts[0] == "changed" else "utf-8") if os.path.isfile(p) else ""


def rows(root, sql, args=()):
    con = sqlite3.connect(os.path.join(run_dir(root), "state.db"))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def write(pkg, rel, text):
    V.write_bytes(os.path.join(pkg, *rel.split("/")), text.replace("\n", "\r\n").encode("cp949"))


# ---------------------------------------------------------------- 1. 이름 판정
CASES = [
    # (이름, 폴더인가, 같은 폴더의 이름들, 백업인가)
    ("Backup", True, [], True), ("backup_20230101", True, [], True), ("Src_bak", True, [], True), ("_old", True, [], True), ("OLD", True, [], True),
    ("Src - 복사본", True, [], True), ("백업_0312", True, [], True), ("Copy of Src", True, [], True),
    ("Net_20231010", True, ["net"], True), ("Net_20231010", True, [], False), ("Net_old", True, ["net"], True), ("Net_old", True, [], False),
    ("Src (2)", True, ["src"], True), ("BackupMgr", True, [], False), ("Threshold", True, [], False), ("Holder", True, [], False),
    ("Main_bak.cpp", False, [], True), ("Main.bak.cpp", False, [], True), ("Main.cpp.bak", False, [], True), ("Main - 복사본.cpp", False, [], True),
    ("복사본 - Main.cpp", False, [], True), ("Copy of Util.cpp", False, [], True), ("Util - Copy.cpp", False, [], True), ("Main_backup.cpp", False, [], True),
    ("bak_Main.cpp", False, [], True), ("Main.old.h", False, [], True), ("Main_백업.cpp", False, [], True), ("Main.cpp~", False, [], True),
    ("Util (2).cpp", False, ["util.cpp"], True), ("Util (2).cpp", False, [], False), ("Main_old.cpp", False, ["main.cpp"], True),
    ("Main_old.cpp", False, [], False), ("Main_org.h", False, ["main.h"], True), ("Main_20231010.cpp", False, ["main.cpp"], True),
    ("Main_20231010.cpp", False, [], False), ("Main_231010.cpp", False, ["main.cpp"], True), ("Main20231010.cpp", False, ["main.cpp"], True),
    ("Main_20231340.cpp", False, ["main.cpp"], False), ("Report_2023.cpp", False, ["report.cpp"], False), ("DbOld.cpp", False, ["db.cpp"], True),
    ("DbOld.cpp", False, [], False), ("DbBackup.cpp", False, ["db.cpp"], True), ("DbBackup.cpp", False, [], False), ("Sock_copy.cpp", False, ["sock.cpp"], True),
    ("Backup.cpp", False, [], False), ("BackupMgr.cpp", False, [], False), ("Threshold.cpp", False, ["thresh.cpp"], False),
    ("Callback.cpp", False, ["call.cpp"], False), ("FileCopy.cpp", False, ["file.cpp"], False), ("Main_new.cpp", False, ["main.cpp"], False),
    ("Main_v2.cpp", False, ["main.cpp"], False), ("Main2.cpp", False, ["main.cpp"], False), ("Gold.cpp", False, ["g.cpp"], False),
    ("stdafx.h", False, [], False), ("resource.h", False, [], False),
]
wrong = [(n, d, s) for n, d, s, want in CASES if bool(V.backup_reason(n, d, set(s))) != want]
if wrong:
    print("이름 판정이 어긋난 것:", wrong)
check("이름 판정: %d건이 모두 기대와 같음(확실한 표시는 이름만으로, 약한 표시는 같은 폴더에 원래 이름이 있을 때만)" % len(CASES), not wrong)

# ---------------------------------------------------------------- 2. 패키지
DEF_H = '''#pragma once
#include <stdio.h>
#include <string.h>
typedef struct { char szTc[5]; char szSeq[6]; char szLen[4]; } MES_HDR;
'''
MAIN = '''// 합성 예제 (CP949, CRLF)
#include "Def.h"
#include "Shared.h"

void ReadLine(void)
{
    char buf[32];
    gets(buf);
}

int ParseHdr(const char* pBuf, char* pTc, char* pSeq, char* pLen)
{
    int nPos = 0;
    memcpy(pTc, pBuf + nPos, 5);
    nPos += 5;
    memcpy(pSeq, pBuf + nPos, 6);
    nPos += 6;
    memcpy(pLen, pBuf + nPos, 4);
    nPos += 4;
    return nPos;
}
'''
OTHER = '''// 합성 예제
#include <stdio.h>
void %s(void)
{
    char buf[32];
    gets(buf);
}
'''
PROJ = '''<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
%s
  </ItemGroup>
</Project>
'''
pkg = os.path.join(work, "PKG")
LIVE = ["Src/Main.cpp", "Src/Def.h", "Src/Report_2023.cpp", "Src/Log_20231010.cpp", "Src/Cfg.cpp", "Src/Net/Sock.cpp", "Src/Backup.cpp", "Src/BackupMgr.cpp"]
BACKUP = ["Src/Main_bak.cpp", "Src/Main_old.cpp", "Src/Main - 복사본.cpp", "Src/Net_20231010/Sock.cpp", "Src/_old/Legacy.cpp",
          "Backup/PKG/Src/Main.cpp", "Backup/PKG/Src/Def.h", "Inc_bak/Unused.h", "Src/Def.h.bak"]
KEPT = ["Src/Cfg_old.cpp", "Inc_bak/Shared.h"]
write(pkg, "Src/Main.cpp", MAIN)
write(pkg, "Src/Def.h", DEF_H)
write(pkg, "Backup/PKG/Src/Main.cpp", MAIN)
write(pkg, "Backup/PKG/Src/Def.h", DEF_H.replace("char szLen[4];", "char szLen[4]; char szOld[2];"))
write(pkg, "Inc_bak/Shared.h", "#pragma once\n#define SHARED_VALUE 1\n")
write(pkg, "Inc_bak/Unused.h", "#pragma once\n#define UNUSED_VALUE 1\n")
write(pkg, "Src/Def.h.bak", DEF_H)
for rel in LIVE + BACKUP + KEPT:
    if not os.path.isfile(os.path.join(pkg, *rel.split("/"))):
        write(pkg, rel, OTHER % ("Fn_" + "".join(c if c.isalnum() else "_" for c in os.path.basename(rel))))
write(pkg, "PKG.vcxproj", PROJ % "\n".join('    <ClCompile Include="%s" />' % r.replace("/", "\\") for r in LIVE + ["Src/Cfg_old.cpp"] if r.endswith(".cpp")))
write(pkg, "Backup/PKG/PKG.vcxproj", PROJ % '    <ClCompile Include="Src\\Main.cpp" />')
req = os.path.join(work, "req.json")
V.write_bytes(req, json.dumps({"features": [{"id": "REQ-BUF", "type": "buffer_fields"}]}).encode("utf-8"))
BASE = ["--from-vs", "2010", "--to-vs", "2022", "--no-compile", "--no-llm", "--request", req]

# ---------------------------------------------------------------- 3. 기본값(skip)
cA, tA = cli("run", "--package", pkg, "--out", os.path.join(work, "A"), *BASE)
fA = {r["path"]: r for r in rows(os.path.join(work, "A"), "SELECT path, kind, status, note FROM files")}
listA = out_text(os.path.join(work, "A"), "backup_excluded.md")
repA = out_text(os.path.join(work, "A"), "report.md")
check("실행: 백업으로 보이는 파일은 종류 backup, 상태 '백업제외'가 되고 까닭이 적힘",
      all(fA[r]["kind"] == "backup" and fA[r]["status"] == "백업제외" and "백업으로 보여 처리하지 않음" in fA[r]["note"] for r in BACKUP + ["Backup/PKG/PKG.vcxproj"]))
check("실행: 이름이 비슷해도 백업이 아닌 파일은 그대로 처리함(Report_2023, 짝 없는 날짜 이름, Backup.cpp, BackupMgr.cpp)",
      all(fA[r]["kind"] == "source" and fA[r]["status"] not in ("백업제외", "제외") for r in LIVE))
check("되살림: 프로젝트 파일이 참조하는 파일과 소스가 include 하는 헤더는 이름이 백업 같아도 처리함",
      all(fA[r]["kind"] == "source" and fA[r]["status"] != "백업제외" for r in KEPT) and "Src/Cfg_old.cpp" in listA and "백업이 아닌 프로젝트 파일이 참조함" in listA
      and "Inc_bak/Shared.h" in listA and "백업이 아닌 소스가 include" in listA)
check("되살림: 백업 안의 프로젝트 파일이 참조하는 것은 되살리지 않고, 쓰이지 않는 백업 헤더도 빼 둠",
      fA["Backup/PKG/Src/Main.cpp"]["status"] == "백업제외" and fA["Inc_bak/Unused.h"]["status"] == "백업제외")
snap = os.path.join(run_dir(os.path.join(work, "A")), "snapshot")
check("실행: 백업은 스냅샷에 복사하지 않음", not os.path.exists(os.path.join(snap, "Backup")) and not os.path.isfile(os.path.join(snap, "Src", "Main_bak.cpp"))
      and os.path.isfile(os.path.join(snap, "Src", "Main.cpp")) and os.path.isfile(os.path.join(snap, "Inc_bak", "Shared.h")))
issA = rows(os.path.join(work, "A"), "SELECT DISTINCT path FROM issues WHERE path<>''")
check("실행: 백업에서는 탐지하지 않음(이슈가 모두 백업이 아닌 파일의 것)", issA and all(fA[i["path"]]["kind"] == "source" for i in issA)
      and "Src/Main.cpp" in {i["path"] for i in issA} and "Src/Main_bak.cpp" not in {i["path"] for i in issA})
check("목록: backup_excluded.md 에 폴더별 파일 수와 까닭, 폴더 밖의 파일, 되살린 파일이 나옴",
      "| Backup | 3 |" in listA and "폴더 이름이 old" in listA and "같은 폴더에 Net 이(가) 있고 이름 끝에 20231010" in listA
      and "Src/Main_bak.cpp" in listA and "Src/Main - 복사본.cpp" in listA and "같은 폴더에 Main.cpp 이(가) 있고 이름 끝에 old" in listA)
check("보고: 로그와 보고서에 뺀 수와 끄는 방법이 나옴",
      "백업으로 보이는 파일 10개(소스 8개" in tA and "--backups keep" in tA and "백업으로 보이는 파일 10개(소스 8개, 폴더 4곳 포함)를 처리 대상에서 뺐다" in repA
      and "백업제외" in repA and "backup_excluded.md" in repA)
srcA = out_text(os.path.join(work, "A"), "changed", "Src", "Main.cpp")
check("구조 분석: 백업의 같은 이름 구조체에 막히지 않고 바뀜", "MES_HDR msg_pBuf;" in srcA and "memcpy(pSeq, msg_pBuf.szSeq, 6);" in srcA)

# ---------------------------------------------------------------- 4. --backups keep
cB, tB = cli("run", "--package", pkg, "--out", os.path.join(work, "B"), "--backups", "keep", *BASE)
fB = {r["path"]: r for r in rows(os.path.join(work, "B"), "SELECT path, kind, status FROM files")}
issB = rows(os.path.join(work, "B"), "SELECT DISTINCT path FROM issues WHERE path<>''")
check("--backups keep: 백업도 전과 같이 처리하고(탐지 포함) 목록 파일은 만들지 않음",
      all(fB[r]["kind"] == "source" for r in BACKUP if r.endswith((".cpp", ".h"))) and "Src/Main_bak.cpp" in {i["path"] for i in issB}
      and not out_text(os.path.join(work, "B"), "backup_excluded.md") and "--backups keep 이어서 그대로 처리합니다" in tB
      and "그대로 처리했다(--backups keep)" in out_text(os.path.join(work, "B"), "report.md"))
srcB = out_text(os.path.join(work, "B"), "changed", "Src", "Main.cpp")
check("--backups keep: 백업의 구조체가 같은 이름으로 또 정의돼 있어 구조 분석이 그 구조체를 쓰지 못함(0.10.0 과 같은 동작)", "msg_pBuf" not in srcB)

# ---------------------------------------------------------------- 5. 이어서 실행, 입력 오류, 사용 예
cC, tC = cli("run", "--package", pkg, "--out", os.path.join(work, "C"), "--until", "S2", *BASE)
cC2, tC2 = cli("run", "--resume", run_dir(os.path.join(work, "C")), "--backups", "keep", "--no-llm")
fC = {r["path"]: r for r in rows(os.path.join(work, "C"), "SELECT path, status FROM files")}
check("이어서 실행: 처음 실행의 값을 그대로 쓰고, 바꾸려 하면 알려 줌", fC["Src/Main_bak.cpp"]["status"] == "백업제외" and "백업 처리(--backups)를 바꿀 수 없습니다" in tC2)
cD, tD = cli("run", "--package", pkg, "--out", os.path.join(work, "D"), "--backups", "maybe", *BASE)
check("입력 오류: 모르는 값은 받지 않음", cD != 0 and "maybe" in tD)
cE, tE = cli("examples")
ps1 = open(os.path.join(vdir, "Invoke-VersionUpdate.ps1"), "rb").read()
check("사용 예와 PowerShell: --backups keep 예가 있고, 스크립트에 -Backups 가 있으며 BOM 과 CRLF 가 그대로",
      "--backups keep" in tE and b"[string]$Backups" in ps1 and b'@("--backups", $Backups)' in ps1 and ps1[:3] == b"\xef\xbb\xbf" and ps1.count(b"\r\n") == ps1.count(b"\n"))

# ---------------------------------------------------------------- 6. 백업이 없는 패키지는 전과 같다
pkg2 = os.path.join(work, "PKG2")
write(pkg2, "Src/Main.cpp", OTHER % "Main")
write(pkg2, "Src/Util.cpp", OTHER % "Util")
cF, tF = cli("run", "--package", pkg2, "--out", os.path.join(work, "F"), "--from-vs", "2010", "--to-vs", "2022", "--no-compile", "--no-llm")
check("백업이 없는 패키지: 목록 파일과 경고를 내지 않음", not out_text(os.path.join(work, "F"), "backup_excluded.md") and "백업으로 보이는 파일" not in tF
      and "백업으로 보이는" not in out_text(os.path.join(work, "F"), "report.md"))

ok = sum(1 for _n, c in checks if c)
for name, cond in checks:
    print("[%s] %s" % ("통과" if cond else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), ok))
sys.exit(0 if ok == len(checks) else 1)
