"""0.10.2 의 시험: 요청문의 의미 분석 답과 의도별 처리 건수, 처리 단계별 건수가 로그와 보고서에 남는가.

사용: python test_v102.py <vupdate.py 가 있는 폴더> <작업 폴더>

확인하는 것
  1. 의미 분석의 물음과 답 원문이 run.log 에 남는다(답| 줄). 의도마다 지시, 이름, 낱말, 구문, 파일이 남는다
  2. 답을 그대로 쓰지 않고 바꾸거나 버린 것(모르는 종류, 식별자 꼴이 아닌 이름)이 남는다
  3. out/ask_trace.md: 요청 원문, 답 원문, 해석된 의도, 의도별 집계, 미수정 비고, sLLM 작업 목록
  4. out/ask_plan.json 의 analysis 에 답 원문이 있다
  5. 보고서의 "요청문 처리 추적"과 "처리 단계별 집계", run.log 의 집계 줄
  6. 답을 쓸 수 없을 때(한 번 다시 묻는다)와 두 번 모두 쓸 수 없을 때의 기록
  7. 캐시에서 답한 물음도 파일과 기록에 남는다
  8. 긴 답은 limits.ask_log_chars 에서 잘라 적는다
  9. 요청문이 없으면 추적 파일을 만들지 않는다
 10. 답에 파일에 쓸 수 없는 글자가 있어도 멈추지 않는다
모델은 이 파일 안의 모의 서버다. 실제 sLLM 의 응답 품질은 이 시험으로 확인되지 않는다.
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


def edit_fix(user):
    """WriteLog 호출에 __LINE__ 을 붙이고, gets 를 fgets 로 바꾼다."""
    out = []
    for line in code_of(user).split("\n"):
        if "WriteLog(" in line and '");' in line:
            out.append(block(line, line.replace('");', '", __LINE__);')))
        m = re.search(r"\bgets\((\w+)\)", line)
        if m:
            out.append(block(line, line.replace("gets(%s)" % m.group(1), "fgets(%s, sizeof(%s), stdin)" % (m.group(1), m.group(1)))))
    return "\n".join(out) or "NO_CHANGE"


def run(tag, pkg, mock, keep=None, **over):
    base = dict(from_vs="2010", no_compile=True)
    base.update(over)
    ctx = V._make_ctx(V._selftest_ns(os.path.join(work, tag), pkg, mock.url, **base))
    try:
        code = V.run_pipeline(ctx, [s for s, _n in V.STAGES])
        if keep:
            keep(ctx)
        res = {"code": code, "run": ctx.run_dir, "calls": ctx.llm_calls}
    finally:
        ctx.store.close()
        V._SINK.close()
    for name, rel in (("log", ("logs", "run.log")), ("trace", ("out", "ask_trace.md")), ("report", ("out", "report.md")), ("plan", ("out", "ask_plan.json"))):
        p = os.path.join(res["run"], *rel)
        res[name] = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""
    return res


# ------------------------------------------------------------------ 예제 패키지
pkg = os.path.join(work, "PKG")
write(pkg, "Src/Log.h", ["#pragma once", "void WriteLog(const char* msg, int line = 0);"])
write(pkg, "Src/Ui.cpp", ["#include <stdio.h>", "#include <stdlib.h>", "#include <string.h>", "#include \"Log.h\"", "void OnOpen()", "{",
                          "    WriteLog(\"open\");", "}", "void OnClose()", "{", "    WriteLog(\"close\");", "}",
                          "void ReadName(char* pOut, int n)", "{", "    char buf[32];", "    char num[16];", "    gets(buf);",
                          "    strcpy(num, \"0\");", "    itoa(n, num, 10);", "    strcpy(pOut, buf);", "}"])
ASK = "WriteLog 호출에 줄 번호 인자를 추가한다.\nData 배열의 숫자 오프셋 처리를 기존 TC 구조체의 필드 접근으로 개선한다.\n주석은 바꾸지 않는다."
GOOD = {"intents": [
    {"type": "modify", "instruction": "WriteLog 호출에 줄 번호 인자를 추가한다", "names": ["WriteLog"], "words": [], "construct": "call", "files": []},
    {"type": "array_to_struct", "instruction": "Data 배열의 숫자 오프셋 처리를 기존 TC 구조체의 필드 접근으로 개선한다",
     "names": ["Data", "TC", "m_st.szLot"], "words": [], "construct": "any", "files": []},
    {"type": "constraint", "instruction": "주석은 바꾸지 않는다", "names": [], "words": [], "construct": "any", "files": []},
    {"type": "refactor", "instruction": "ReadName 을 정리한다", "names": ["ReadName"], "words": ["rd"], "construct": "block", "files": []}]}
RAW_GOOD = "요청을 다음과 같이 나눴습니다.\n" + json.dumps(GOOD, ensure_ascii=False, indent=1) + "\n이상입니다."

# ------------------------------------------------------------------ 1~5. 의도 여러 개
m = Mock(lambda n, user: RAW_GOOD, edit_fix)
r = run("a", pkg, m, ask=[ASK], compat_llm=3, deprecated="fix")
m.close()
log, trace, report = r["log"], r["trace"], r["report"]
check("의미 분석의 물음이 로그에 남음(요청문 글자 수, 알려 준 이름)", "요청 ASK-1 의미 분석 물음: 요청문 %d자" % len(ASK) in log and "WriteLog" in log.split("의미 분석 물음", 1)[1].split("\n", 1)[0])
check("의미 분석의 답 원문이 로그에 줄마다 남음", "요청 ASK-1 의미 분석 답(1차, 호출" in log and "요청 ASK-1 답| 요청을 다음과 같이 나눴습니다." in log
      and '요청 ASK-1 답|    "type": "array_to_struct",' in log and "요청 ASK-1 답| 이상입니다." in log)
check("의도마다 지시와 이름·낱말·구문·파일이 로그에 남음", "요청 ASK-1-1 [modify] 지시: WriteLog 호출에 줄 번호 인자를 추가한다" in log
      and "요청 ASK-1-1 [modify] 이름: WriteLog / 낱말: - / 구문: call / 파일: -" in log and "요청 ASK-1-2 [array_to_struct] 이름: Data, TC" in log)
check("답을 그대로 쓰지 않고 바꾼 것이 로그에 남음(모르는 종류, 모르는 구문, 식별자 꼴이 아닌 이름, 짧은 낱말)",
      "4번째 의도: 종류 'refactor' 은(는) 모르는 값이어서 modify 로 봄" in log and "4번째 의도: 구문 'block' 은(는) 모르는 값이어서 any 로 봄" in log
      and "2번째 의도: 이름 'm_st.szLot' 은(는) 영문 식별자 꼴이 아니어서 버림" in log and "4번째 의도: 낱말 'rd' 은(는) 영문 3글자 이상이 아니어서 버림" in log)
check("추적 파일: 요청 원문, 답 원문, 요청문 1건 -> 의도 4개", "> WriteLog 호출에 줄 번호 인자를 추가한다." in trace and "~~~~text\n요청을 다음과 같이 나눴습니다." in trace
      and "**해석된 의도 4개**(요청문 1건 -> 의도 4개)" in trace and "- 상태: 최종" in trace and "- 1차 답: 호출," in trace and "쓸 수 있음" in trace)
check("추적 파일: 해석하면서 바꾼 것", "  - 해석하면서 바꾼 것: 4번째 의도: 종류 'refactor'" in trace)
sec1 = trace.split("### ASK-1-1 [modify]", 1)[1].split("### ASK-1-2", 1)[0] if "### ASK-1-1 [modify]" in trace else ""
check("추적 파일: 수정 의도의 집계(대상 3: 호출 2곳과 선언 1곳, 모두 sLLM 에 보냄, 호출 2곳을 고치고 선언은 고칠 필요 없다고 봄)와 작업 목록",
      "- 집계: 대상 3 / 규칙 수정 0 / sLLM 수정 요청 3(보낸 것 3, 작업 2, 호출 2) / sLLM 수정 2 / 미수정 0 / sLLM 이 고칠 필요가 없다고 본 곳 1" in sec1
      and "수정 요청(sLLM 작업)" in sec1 and "| Src/Ui.cpp |" in sec1 and "| Src/Log.h |" in sec1)
sec2 = trace.split("### ASK-1-2 [array_to_struct]", 1)[1].split("### ASK-1-3", 1)[0] if "### ASK-1-2 [array_to_struct]" in trace else ""
check("추적 파일: 대상을 찾지 못한 의도는 그 건수와 비고(이름, 사유)가 남음", "대상을 찾지 못한 이름·요청" in sec2 and "| 대상 없음 |" in sec2)
check("추적 파일: 조건 의도는 처리 경로가 남음", "### ASK-1-3 [constraint]" in trace and "모든 수정 요청에 조건으로 붙임" in trace)
plan = json.loads(r["plan"]) if r["plan"] else {}
att = (((plan.get("asks") or [{}])[0].get("analysis") or {}).get("attempts") or [{}])[0]
check("ask_plan.json 의 analysis 에 답 원문과 호출 정보가 있음", att.get("raw") == RAW_GOOD and att.get("usable") is True and att.get("source") == "호출")
check("보고서: 요청문 처리 추적 표와 미수정 비고", "### 요청문 처리 추적" in report and "| ASK-1 | ASK-1-1 | modify | WriteLog | 3 | 0 | 3(2/2) | 2 | 0 |" in report and "out/ask_trace.md" in report)
check("보고서: 처리 단계별 집계 표", "### 처리 단계별 집계(--compat-llm)" in report and "| 1단계 기계적 변경(규칙) |" in report and "| 3단계 sLLM 수정 |" in report
      and "지금 단계는 --compat-llm 3 이다" in report)
check("로그: 의도별 집계 줄", "요청 ASK-1-1 [modify] 집계: 대상 3 / 규칙 수정 0 / sLLM 수정 요청 3(보낸 것 3, 작업 2, 호출 2) / sLLM 수정 2 / 미수정 0" in log
      and "요청 ASK-1-2 [array_to_struct] 집계: 대상 0" in log and "대상을 찾지 못한 이름·요청 1" in log
      and "요청 ASK-1-3 [constraint] 결과: 모든 수정 요청에 조건으로 붙임" in log)
check("로그: 처리 단계별 집계 줄(1단계 itoa, 2단계 strcpy, 3단계 gets 가 sLLM 으로 고쳐짐)",
      "처리 단계별 집계(환경 호환성 규칙)" in log and re.search(r"1단계 기계적 변경\(규칙\): 대상 1 / 규칙 수정 1 ", log) is not None
      and re.search(r"2단계 선언을 읽어 판정하는 기계적 변환\(규칙\): 대상 1 / 규칙 수정 1 ", log) is not None
      and re.search(r"3단계 sLLM 수정: 대상 4 / 규칙 수정 0 / sLLM 수정 요청 4\(보낸 것 3, 작업 \d+, 호출 \d+\) / sLLM 수정 1 / 미수정 0 / "
                    r"sLLM 이 고칠 필요가 없다고 본 곳 2 / 다른 수정에 포함 1", log) is not None)
check("로그: 보안 CRT 경고 줄 가운데 2단계가 바꾸지 않은 줄은 3단계의 sLLM 대상이라고 적고, 이름만 바꾼 곳(_itoa)은 보안 CRT 경고가 남는다고 적음",
      "보안 CRT 경고에 걸린 줄" in log.split("처리 단계별 집계", 1)[1] and "3단계의 sLLM 대상이다" in log
      and "POSIX 이름을 밑줄 이름으로 바꾼 1곳 가운데 1곳(_itoa, _ltoa, _ultoa, _strlwr, _strupr)은 이름의 경고만 없어지고" in log)

# ------------------------------------------------------------------ 5-2. 단계 1: 3단계 대상은 보내지 않고 비고와 함께 남는다
m = Mock(lambda n, user: RAW_GOOD, edit_fix)
r1 = run("b", pkg, m, compat_llm=1, deprecated="fix")
m.close()
check("단계 1: 3단계 대상(gets)은 보내지 않았다고 집계되고 비고가 붙음",
      re.search(r"3단계 sLLM 수정: 대상 1 / 규칙 수정 0 / sLLM 수정 요청 1\(보낸 것 0, 작업 0, 호출 0\) / sLLM 수정 0 / 미수정 1", r1["log"]) is not None
      and re.search(r"미수정 비고\(1건\) R-T3-GETS-REMOVED: ", r1["log"]) is not None and "지금 단계는 --compat-llm 1 이다" in r1["report"])
check("요청문이 없으면 추적 파일과 보고서의 추적 절을 만들지 않음", r1["trace"] == "" and "### 요청문 처리 추적" not in r1["report"] and r1["code"] in (0, 1))

# ------------------------------------------------------------------ 6. 답을 쓸 수 없을 때
m = Mock(lambda n, user: "죄송합니다. 요청을 이해하지 못했습니다." if n == 1 else RAW_GOOD, edit_fix)
r2 = run("c", pkg, m, ask=[ASK])
m.close()
check("1차 답을 쓸 수 없으면 사유와 원문을 남기고 한 번 다시 묻는다",
      "요청 ASK-1 답| 죄송합니다. 요청을 이해하지 못했습니다." in r2["log"] and "의미 분석 답(1차)을 쓸 수 없음: intents 목록이 든 JSON 이 아님. 한 번 다시 묻는다" in r2["log"]
      and "의미 분석 답(2차, 호출" in r2["log"] and "- 1차 답: 호출," in r2["trace"] and "쓸 수 없음(intents 목록이 든 JSON 이 아님)" in r2["trace"] and "- 2차 답: 호출," in r2["trace"])
m = Mock(lambda n, user: "모르겠습니다 %d" % n, edit_fix)
r3 = run("d", pkg, m, ask=[ASK])
m.close()
check("두 번 모두 쓸 수 없으면 분석하지 못함으로 남고 두 답의 원문이 모두 남음",
      "요청 ASK-1 답| 모르겠습니다 1" in r3["log"] and "요청 ASK-1 답| 모르겠습니다 2" in r3["log"] and "요청 ASK-1 집계: 분석하지 못함" in r3["log"]
      and "- **분석하지 못함**:" in r3["trace"] and "| ASK-1 | - | 분석하지 못함 |" in r3["report"])

# ------------------------------------------------------------------ 7. 캐시에서 답한 물음
probe = {}


def keep(ctx):
    before = ctx.llm_calls
    ok1, c1 = V._ask_model(ctx, V.ANALYZE_SYSTEM, "[요청 원문]\n캐시 확인", "probe_1")
    ok2, c2 = V._ask_model(ctx, V.ANALYZE_SYSTEM, "[요청 원문]\n캐시 확인", "probe_2")
    probe.update(ok=ok1 and ok2, same=c1 == c2, calls=ctx.llm_calls - before, src=[x["source"] for x in ctx._ask_calls[-2:]],
                 file2=open(os.path.join(ctx.run_dir, "logs", "llm", "probe_2.txt"), encoding="utf-8").read())


m = Mock(lambda n, user: RAW_GOOD, edit_fix)
run("e", pkg, m, keep=keep, ask=[ASK])
m.close()
check("캐시에서 답한 물음도 파일과 기록에 남고 호출 수는 늘지 않음",
      probe.get("ok") and probe.get("same") and probe.get("calls") == 1 and probe.get("src", ["", ""])[0] == "호출" and probe["src"][1].startswith("캐시")
      and "[RESPONSE ok=True 0ms 캐시" in probe.get("file2", "") and RAW_GOOD in probe.get("file2", ""))

# ------------------------------------------------------------------ 8. 긴 답
LONG = RAW_GOOD + "\n" + "\n".join("덧붙임 %04d %s" % (i, "가" * 60) for i in range(200))
m = Mock(lambda n, user: LONG, edit_fix)
r5 = run("f", pkg, m, ask=[ASK])
m.close()
check("긴 답은 정한 글자 수에서 잘라 적고 원문 파일을 알려 준다(추적 파일에는 전체가 있다)",
      "(전체 %d자 가운데 6000자만 적음. 원문: logs/llm/ask_ASK-1_analyze_1.txt)" % len(LONG) in r5["log"] and "덧붙임 0199" not in r5["log"] and "덧붙임 0199" in r5["trace"])

# ------------------------------------------------------------------ 10. 파일에 쓸 수 없는 글자가 든 답
m = Mock(lambda n, user: "설명 \ud83d 끝\n" + json.dumps(GOOD, ensure_ascii=False), edit_fix)
try:
    r6 = run("g", pkg, m, ask=[ASK])
except Exception as e:
    r6 = {"log": "", "trace": "", "err": repr(e)}
m.close()
check("답에 짝 없는 대리 문자가 있어도 멈추지 않고 그 글자만 ? 로 적는다", "요청 ASK-1 답| 설명 ? 끝" in r6["log"] and "**해석된 의도 4개**" in r6["trace"])

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", n))
print("합계: %d개 중 %d개 통과" % (len(checks), len(checks) - len(bad)))
sys.exit(1 if bad else 0)
