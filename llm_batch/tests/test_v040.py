"""0.4.0 에서 더한 기능의 시험.

사용: python test_v040.py <vupdate.py 가 있는 폴더> <작업 폴더>
모의 sLLM 서버와 합성 예제만 쓴다.

확인하는 것
  1. 새 규칙 21개가 잡아야 할 코드를 잡고, 비슷하지만 다른 코드는 잡지 않는다.
  2. 규칙의 조건(환경값 축, 버전 구간, 방침)에 따라 켜지고 꺼진다.
  3. c++20 이상이면 준수 모드를 켠 것으로 본다.
  4. llmcheck 명령: 정상 서버, 토큰 수를 안 주는 서버, 연결할 수 없는 서버.
  6. 커버리지 표가 규칙 목록과 어긋나지 않는다.
"""
import argparse, json, os, shutil, subprocess, sys

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
import vupdate as V

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def env(f=2008, t=2022, std="c++14", platform="keep", conformance="off", deprecated="suppress", axes=None):
    return {"from_vs": f, "to_vs": t, "cpp_standard": std if t >= 2015 else None, "conformance": conformance,
            "platform": platform, "charset": "keep", "policy": {"deprecated_warnings": deprecated}, "axes": axes or {}}


def rule(rid):
    return next(r for r in V.RULES if r["id"] == rid)


def hits(rid, text, name="a.cpp", e=None):
    return V.rule_hits(rule(rid), V.make_doc(name, text.encode("utf-8")), e or env())


# ------------------------------------------------------------------ 1. 새 규칙: 잡는 것과 잡지 않는 것
CASES = [
    ("R-T2-AUTO-STORAGE", "void f() { auto int i = 0; auto unsigned u; }",
     "void f() { auto i = 0; auto& r = i; for (auto it = v.begin(); ;) {} }"),
    ("R-T2-WINABLE-H", "#include <WinAble.h>\n", "#include <winuser.h>\n// #include <winable.h>\n"),
    ("R-T3-OSVER-GLOBALS", "int v = _winmajor * 100 + _winminor;", "int my_winmajor = 1; obj._osver = 2;"),
    ("R-T0-ITER-DEBUG", "#define _SECURE_SCL 0\n", "#define MY_SECURE_SCL 0\n"),
    ("R-T2-MAKE-PAIR-NULL", "m.insert(std::make_pair(key, NULL));", "m.insert(std::make_pair(key, nullptr)); p = NULL;"),
    ("R-T2-WINVER-OLD", "#define WINVER 0x0400\n#define _WIN32_WINNT 0x0500\n",
     "#define WINVER 0x0501\n#define _WIN32_WINNT 0x0601\n#define _WIN32_IE 0x0400\n"),
    ("R-T0-KEYWORD-MACRO", "#define for if(0);else for\n#define inline __inline\n",
     "#define new DEBUG_NEW\n#define FOR_EACH(x) x\n#define format(x) x\n"),
    ("R-T3-GETVERSIONEX", "GetVersionEx(&vi); DWORD v = GetVersion();", "obj.GetVersion(); MyGetVersionEx(&vi); p->GetVersion();"),
    ("R-T2-C99-MATH-MACRO", "#define round(x) floor((x)+0.5)\n#define isnan(x) _isnan(x)\n",
     "#define ROUND(x) x\n#define rounding 1\n#if _MSC_VER < 1800\n#define round(x) floor((x)+0.5)\n#endif\n"),
    ("R-T3-C99-MATH-FUNC", "static double round(double x)\n{\n    return floor(x + 0.5);\n}\n",
     "double round(double x);\ndouble y = round(1.5);\ndouble my_round(double x) { return x; }\n"),
    ("R-T3-GETS-REMOVED", "char b[10]; gets(b);", "fgets(b, 10, stdin); gets_s(b, 10); obj.gets(b); _gets(b);"),
    ("R-T3-TIMESPEC", "struct timespec {\n    long tv_sec;\n    long tv_nsec;\n};\n",
     "struct timespec ts;\nstruct timespec* p;\n#if !defined(_MSC_VER) || _MSC_VER < 1900\nstruct timespec {\n long a;\n};\n#endif\n"),
    ("R-T3-CHAR16-TYPEDEF", "typedef unsigned short char16_t;\n",
     "char16_t c;\ntypedef char16_t my_char;\n#if _MSC_VER < 1900\ntypedef unsigned short char16_t;\n#endif\n"),
    ("R-T0-WCSTOK", 'p = wcstok(buf, L",");', 'p = wcstok_s(buf, L",", &ctx); p = my_wcstok(buf);'),
    ("R-T0-HASH-MAP", "stdext::hash_map<int, int> m;", "std::unordered_map<int, int> m; int hash_mapping;"),
    ("R-T0-EXP-FILESYSTEM", "#include <experimental/filesystem>\n", "#include <filesystem>\n"),
    ("R-T0-STD17-DEPRECATED", "class It : public std::iterator<std::forward_iterator_tag, int> {};",
     "std::vector<int>::iterator it; class It : public my::iterator<int> {};"),
    ("R-T0-STD-BYTE", "using namespace std;\n", "using std::string;\nusing namespace boost;\n"),
    ("R-T0-REGISTER", "register int i = 0;", "int registered = 0; void register_handler();"),
    ("R-T0-DYN-EXCEPT", "void f() throw(std::bad_alloc);\nvoid g() const throw(int, char) {}\n",
     "void f() throw();\nvoid h() { if (bad) throw (e); }\nvoid k() noexcept;\n"),
    ("R-T0-STRICT-STRINGS", 'char* p = "abc";\nstatic char *names[] = { "a", "b" };\nLPSTR s = "x";\n',
     'const char* p = "abc";\nchar buf[] = "abc";\nstd::string s = "x";\nchar* q = buf;\n'),
]
bad = []
for rid, pos, neg in CASES:
    n_pos, n_neg = len(hits(rid, pos)), len(hits(rid, neg))
    if not n_pos or n_neg:
        bad.append("%s(잡음 %d, 잘못 잡음 %d)" % (rid, n_pos, n_neg))
print("새 규칙 %d개 시험. 어긋난 것: %s" % (len(CASES), ", ".join(bad) or "없음"))
check("새 규칙 %d개: 잡아야 할 코드를 잡고 비슷한 다른 코드는 잡지 않음" % len(CASES), not bad and len(CASES) == 21)
check("내장 규칙이 66개(0.4.0 의 38개 + 0.6.0 의 DB·HMI 27개 + 0.9.3 의 보안 CRT 기계적 변환 1개)", len(V.RULES) == 66 and len({r["id"] for r in V.RULES}) == 66)

# 자동 수정 결과
h = hits("R-T2-AUTO-STORAGE", "auto int i;")[0]
check("auto 저장 클래스: 'auto ' 만 지움", h["after"] == "" and "auto int i;"[h["start"]:h["end"]] == "auto ")
h = hits("R-T2-C99-MATH-MACRO", "#define round(x) \\\n    floor((x) + 0.5)\nint a;\n")[0]
check("C99 매크로: 이어지는 줄까지 가드로 감쌈",
      h["after"] == "#if !defined(_MSC_VER) || _MSC_VER < 1800\n#define round(x) \\\n    floor((x) + 0.5)\n#endif")
h = hits("R-T1-SNPRINTF-MACRO", "#define vsnprintf _vsnprintf\n")
check("vsnprintf 매크로도 구 컴파일러 전용으로 감쌈", len(h) == 1 and "_MSC_VER < 1900" in h[0]["after"])
check(".c 파일에는 C++ 전용 규칙을 적용하지 않음", not hits("R-T0-REGISTER", "register int i;", name="a.c")
      and hits("R-T0-REGISTER", "register int i;", name="a.cpp"))
check("WINVER: Windows 7 값(0x0601)으로 올림", hits("R-T2-WINVER-OLD", "#define WINVER 0x0400\n")[0]["after"] == "0x0601")

n_on = lambda **kw: sum(1 for r in V.RULES if V.rule_state(r, env(**kw))[0])
print("켜지는 규칙 수: 2008->2022 %d개, 2015->2022 %d개, 2015->2022(c++17, 준수 모드) %d개, 2008->2022(x64) %d개"
      % (n_on(), n_on(f=2015), n_on(f=2015, std="c++17", conformance="on"), n_on(platform="x64")))
# ------------------------------------------------------------------ 3. 환경값
e20 = V.build_env(argparse.Namespace(from_vs="2015", to_vs="2022", cpp_standard="c++20"))
check("c++20 이상이면 준수 모드를 켠 것으로 처리", e20["conformance"] == "on")


def cli(*args, env_add=None):
    en = dict(os.environ)
    en.pop("VUPDATE_LLM_ENDPOINT", None)
    en.update(env_add or {})
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


pkg = os.path.join(work, "PKG")
V.write_bytes(os.path.join(pkg, "a.cpp"), b"int main() { return 0; }\n")
big_req = os.path.join(work, "big.json")
with open(big_req, "w", encoding="utf-8") as fh:
    json.dump({"common": ["가" * 1600]}, fh, ensure_ascii=False)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--request", big_req, "--dry-run")
check("너무 긴 공통 요청은 입력 오류(종료 코드 3)", code == 3 and "공통 요청" in text)

# ------------------------------------------------------------------ 4. llmcheck 명령
srv = V._selftest_mock_server(tok_scale=1.0, tok_extra=0, limit_total=24000)
code, text = cli("llmcheck", "--llm-endpoint", "http://127.0.0.1:%d/v1" % srv.server_address[1], "--out", os.path.join(work, "lc1"))
srv.shutdown()
rep = [os.path.join(b, f) for b, _d, fs in os.walk(os.path.join(work, "lc1")) for f in fs if f == "llm_check.md"]
md = open(rep[0], encoding="utf-8").read() if rep else ""
check("llmcheck: 정상 서버에서 토큰 수 반환, 한도 초과 거절, 출력 형식, 동시 요청이 정상으로 나옴",
      code in (0, 1) and all(("| %s | 정상 |" % k) in md for k in ("기본 호출", "토큰 수 반환", "한도 초과 거절", "출력 형식",
                                                             "같은 입력에 같은 응답")) and "동시 요청" in md)
srv = V._selftest_mock_server()                      # 토큰 수를 주지 않고 한도도 걸지 않는 서버
code, text = cli("llmcheck", "--llm-endpoint", "http://127.0.0.1:%d/v1" % srv.server_address[1], "--out", os.path.join(work, "lc2"))
srv.shutdown()
check("llmcheck: 토큰 수를 안 주고 한도를 넘겨도 받는 서버는 주의로 알림",
      code == 1 and "[주의] 토큰 수 반환" in text and "[주의] 한도 초과 거절" in text and "서버가 받아들였다" in text)
code, text = cli("llmcheck", "--llm-endpoint", "http://127.0.0.1:9/v1", "--out", os.path.join(work, "lc3"))
check("llmcheck: 연결할 수 없으면 실패(종료 코드 2)", code == 2 and "[실패] 기본 호출" in text)
code, text = cli("llmcheck", "--server-config", os.path.join(vdir, "config", "server.json"))
check("llmcheck: 주소가 없으면 입력 오류(종료 코드 3)", code == 3 and "sLLM 주소가 없습니다" in text)

vllm = ("This model's maximum context length is 24000 tokens. However, you requested 27100 tokens "
        "(24100 in the messages, 3000 in the completion). Please reduce the length of the messages or completion.")
check("오류 응답 분류: 한도 초과의 두 형식(vLLM 문구, 값), 과부하, 권한, 형식 오류, 장애",
      V.classify_llm_error(400, vllm) == ("over_limit", 24000, 24100)
      and V.classify_llm_error(400, '{"error": {"code": "over_limit", "limit": 24000, "input_tokens": 24100}}') == ("over_limit", 24000, 24100)
      and V.classify_llm_error(429, "")[0] == "busy" and V.classify_llm_error(503, "")[0] == "busy"
      and V.classify_llm_error(401, "no key")[0] == "bad_request" and V.classify_llm_error(422, "bad json")[0] == "bad_request"
      and V.classify_llm_error(500, "oops")[0] == "error" and V.classify_llm_error(504, "")[0] == "error")

# ------------------------------------------------------------------ 6. 커버리지 표
rows = V.coverage_rows(V.RULES)
cov = V.coverage_summary()
code, text = cli("rules", "--coverage")
used = {x.strip() for r in rows if r[3] in ("규칙", "설정") for x in r[4].split(",")}
print("커버리지: %s" % dict(cov))
check("커버리지 표: 규칙 ID 가 모두 실제 규칙이고, 모든 내장 규칙이 표에 있음",
      used == {r["id"] for r in V.RULES} and sum(cov.values()) == len(rows) and code == 0 and "알려진 변경 항목 %d건" % len(rows) in text)

print("\n===== 0.4.0 기능 시험 결과 =====")
for name, ok in checks:
    print("[%s] %s" % ("통과" if ok else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, ok in checks if ok)))
sys.exit(0 if all(ok for _n, ok in checks) else 1)
