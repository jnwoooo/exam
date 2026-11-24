"""0.5.0 에서 더한 기능의 시험.

사용: python test_v050.py <vupdate.py 가 있는 폴더> <작업 폴더>
모의 sLLM 서버, 중계 서버 참고 구현(tools/relay_reference.py), 합성 예제만 쓴다.

확인하는 것
  1. 중계 서버(OpenAI 호환 주소)를 거쳐 llmcheck 가 정상으로 나온다. 호출자가 기록된다.
  2. MCP 경유(llm.transport = mcp)로 llmcheck 와 run 이 된다. 한도 초과와 작업 보고가 전달된다.
  3. 중계 서버의 약속: 공통 프롬프트는 앞에만 붙고 본문은 그대로, 동시 수를 넘으면 busy, 키가 틀리면 거절.
  4. 서버가 알려 준 허용 동시 수를 쓴다. extra_body 가 요청 본문에 들어간다.
  5. 명령줄: --from-cpp-standard, --disable-rule, --exceptions, --build-log 의 입력 검사.
  6. .vcxproj 가 있는 패키지를 돌리면 프로젝트 점검이 project_settings.md 에 나온다.
  7. 규칙에 붙인 시험 예(rules --test), 실행 사이의 응답 캐시(llm.shared_cache_file).
"""
import json, os, shutil, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

vdir, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, vdir)
sys.path.insert(0, os.path.join(vdir, "tools"))
import vupdate as V
import relay_reference as R

shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def cli(*args, env_add=None):
    en = dict(os.environ)
    en.pop("VUPDATE_LLM_ENDPOINT", None)
    en.update(env_add or {})
    p = subprocess.run([sys.executable, os.path.join(vdir, "vupdate.py"), "--console-encoding", "utf-8"] + list(args),
                       capture_output=True, env=en, cwd=work)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def start_relay(upstream_port, **over):
    cfg = dict(R.DEFAULTS, listen_host="127.0.0.1", listen_port=0, upstream_endpoint="http://127.0.0.1:%d/v1" % upstream_port,
               log_file=os.path.join(work, "relay_%d.jsonl" % len(os.listdir(work))), _common="")
    cfg.update(over)
    srv = R.make_server(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def read_log(srv):
    p = srv.cfg["log_file"]
    return [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.isfile(p) else []


def find_md(root, name):
    hits = [os.path.join(b, f) for b, _d, fs in os.walk(root) for f in fs if f == name]
    return open(hits[0], encoding="utf-8").read() if hits else ""


ROWS = ("기본 호출", "토큰 수 반환", "한도 초과 거절", "출력 형식", "같은 입력에 같은 응답")

# ------------------------------------------------------------------ 1. OpenAI 호환 중계
up = V._selftest_mock_server(tok_scale=1.0, tok_extra=0, limit_total=24000)
relay = start_relay(up.server_address[1])
rport = relay.server_address[1]
code, text = cli("llmcheck", "--llm-endpoint", "http://127.0.0.1:%d/v1" % rport, "--out", os.path.join(work, "lc_http"), "--caller", "PC01\\홍길동")
md = find_md(os.path.join(work, "lc_http"), "llm_check.md")
log = read_log(relay)
check("중계(OpenAI 호환): llmcheck 의 주요 항목이 정상", code in (0, 1) and all(("| %s | 정상 |" % k) in md for k in ROWS))
check("중계: 호출자와 토큰 수를 기록하고 한도 초과를 over_limit 으로 남김",
      any(r.get("caller") == "PC01\\홍길동" and r.get("prompt_tokens") for r in log) and any(r.get("status") == "over_limit" for r in log))
check("중계: 모델 목록으로 한도와 허용 동시 수를 알려 줌", "모델 목록의 토큰 한도 | 정상" in md)

# ------------------------------------------------------------------ 2. MCP 경유
scfg = os.path.join(work, "server_mcp.json")
with open(scfg, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"transport": "mcp", "endpoint": "http://127.0.0.1:%d/mcp" % rport, "mcp_job_tools": True, "max_parallel": 4}}, fh)
n0 = len(read_log(relay))
code, text = cli("llmcheck", "--server-config", scfg, "--out", os.path.join(work, "lc_mcp"))
md = find_md(os.path.join(work, "lc_mcp"), "llm_check.md")
check("MCP 경유: llmcheck 의 주요 항목이 정상(토큰 수, 한도 초과 거절 포함)",
      code in (0, 1) and all(("| %s | 정상 |" % k) in md for k in ROWS))
pkg = os.path.join(work, "PKG")
V.write_bytes(os.path.join(pkg, "Src", "T.h"), "typedef unsigned char uint8_t;\r\ntypedef int int32_t;\r\nint f(void);\r\n".encode("cp949"))
vcx = ('<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><ItemGroup>'
       '<ProjectConfiguration Include="Release|Win32"/></ItemGroup>'
       '<PropertyGroup Condition="\'$(Configuration)|$(Platform)\'==\'Release|Win32\'"><PlatformToolset>v120_xp</PlatformToolset>'
       '<UseOfMfc>Static</UseOfMfc><CharacterSet>MultiByte</CharacterSet></PropertyGroup>'
       '<ItemDefinitionGroup><ClCompile><TreatWarningAsError>true</TreatWarningAsError></ClCompile></ItemDefinitionGroup>'
       '<ItemGroup><ClInclude Include="..\\Src\\T.h"/></ItemGroup></Project>')
V.write_bytes(os.path.join(pkg, "Proj", "P.vcxproj"), vcx.encode("utf-8"))
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--server-config", scfg, "--no-compile",
                 "--out", os.path.join(work, "runs_mcp"))
out_h = [os.path.join(b, f) for b, _d, fs in os.walk(os.path.join(work, "runs_mcp")) for f in fs if f == "T.h" and "changed" in b]
changed = open(out_h[0], "rb").read().decode("cp949") if out_h else ""
log = read_log(relay)[n0:]
check("MCP 경유: run 이 sLLM 수정까지 끝남(typedef 를 가드로 감쌈, CRLF 유지)",
      code == 0 and "#if !defined(_MSC_VER) || _MSC_VER < 1600\r\ntypedef unsigned char uint8_t;" in changed)
check("MCP 경유: 작업 시작·진행·종료가 서버에 기록되고 모델 호출이 mcp 로 남음",
      {"job_open", "job_progress", "job_close"} <= {r.get("event") for r in log} and any(r.get("via") == "mcp" and r.get("status") == "ok" for r in log))
settings = find_md(os.path.join(work, "runs_mcp"), "project_settings.md")
check(".vcxproj: 프로젝트 점검이 project_settings.md 에 나옴(XP 툴셋, 경고를 오류로, MFC 멀티바이트)",
      "P.vcxproj" in settings and "XP" in settings and "경고를 오류로" in settings and "멀티바이트" in settings)

# ------------------------------------------------------------------ 3. 중계의 약속
cfg = dict(R.DEFAULTS, _common="공통 원칙")
msgs = R.build_messages(cfg, "작업 규칙", "본문 그대로")
check("중계: 공통 프롬프트는 system 앞에만 붙고 본문은 그대로", msgs[0]["content"] == "공통 원칙\n\n작업 규칙" and msgs[1]["content"] == "본문 그대로")
gate = R.Gate(dict(R.DEFAULTS, max_parallel=1, busy_mode="reject"))
gate.enter("a")
try:
    gate.enter("b")
    busy = False
except R.RelayError as e:
    busy = e.code == "busy" and e.extra.get("retry_after")
gate.leave("a")
gate.enter("b")
check("중계: 동시 수를 넘으면 busy 로 알리고, 자리가 나면 받음", busy)
gate2 = R.Gate(dict(R.DEFAULTS, max_parallel=4, per_caller_parallel=1, busy_mode="reject"))
gate2.enter("a")
try:
    gate2.enter("a")
    per = False
except R.RelayError:
    per = True
gate2.enter("b")
check("중계: 호출자별 동시 수를 따로 지킴", per)
relay2 = start_relay(up.server_address[1], api_keys={"good-key": "팀A"})
llm = dict(V.DEFAULTS["llm"] if hasattr(V, "DEFAULTS") else {}, endpoint="http://127.0.0.1:%d/v1" % relay2.server_address[1], model="m",
           max_output_tokens=100, timeout_sec=10, busy_retries=0, api_key="bad-key", use_system_role=True)
ok1, c1, _ms, info1 = V.llm_chat(llm, "s", "u")
ok2, c2, _ms, info2 = V.llm_chat(dict(llm, api_key="good-key"), "s", "u")
check("중계: 키가 틀리면 거절(다시 보내지 않는 오류), 맞으면 통과하고 키의 이름으로 기록",
      not ok1 and info1.get("kind") == "bad_request" and ok2 and any(r.get("caller") == "팀A" for r in read_log(relay2)))
relay2.shutdown()

# ------------------------------------------------------------------ 4. 서버가 알려 준 값, extra_body
relay3 = start_relay(up.server_address[1], per_caller_parallel=2, reserve_tokens=700)
llm3 = dict(llm, endpoint="http://127.0.0.1:%d/v1" % relay3.server_address[1], api_key="", model="gemma4-31b")
lim, ids = V.llm_probe(llm3)
check("서버가 알려 준 한도, 허용 동시 수, 예약량을 받음",
      lim == 24000 and llm3.get("_server_info") == {"max_parallel": 2, "reserve_tokens": 700, "max_output_tokens": 3000})
relay3.shutdown()
got = {}


class Cap(BaseHTTPRequestHandler):
    def do_POST(self):
        got.update(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")))
        data = json.dumps({"choices": [{"message": {"content": "NO_CHANGE"}, "finish_reason": "stop"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


cap = HTTPServer(("127.0.0.1", 0), Cap)
threading.Thread(target=cap.serve_forever, daemon=True).start()
V.llm_chat(dict(llm, endpoint="http://127.0.0.1:%d/v1" % cap.server_address[1], api_key="",
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}), "s", "u")
cap.shutdown()
check("extra_body 가 요청 본문에 들어감(추론 모드 끄기 등)", got.get("chat_template_kwargs") == {"enable_thinking": False} and got.get("temperature") == 0)

# ------------------------------------------------------------------ 5. 명령줄
code, text = cli("rules", "--from-vs", "2017", "--to-vs", "2022", "--cpp-standard", "c++17", "--from-cpp-standard", "c++17")
check("--from-cpp-standard: 원본도 c++17 이면 c++17 규칙이 꺼지고 이유가 나옴", code == 0 and "원본도 이미 언어 표준" in text)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--from-cpp-standard", "17", "--dry-run")
check("잘못된 --from-cpp-standard 는 입력 오류(종료 코드 3)", code == 3)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--build-log", os.path.join(work, "none.log"), "--dry-run")
check("없는 --build-log 는 입력 오류(종료 코드 3)", code == 3 and "빌드 로그" in text)
bad = os.path.join(work, "bad_exc.json")
with open(bad, "w", encoding="utf-8") as fh:
    json.dump({"skip": [{"rules": ["R-X"]}]}, fh)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--exceptions", bad, "--dry-run")
check("형식이 틀린 예외 파일은 입력 오류(종료 코드 3)", code == 3 and "path" in text)
code, text = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--disable-rule", "R-T3-STDINT-TYPEDEF", "--no-llm",
                 "--no-compile", "--out", os.path.join(work, "runs_off"))
check("--disable-rule: 끈 규칙은 탐지하지 않음(수동 검토 0건, 종료 코드 0)", code == 0 and "R-T3-STDINT-TYPEDEF" in find_md(os.path.join(work, "runs_off"), "report.md"))

# ------------------------------------------------------------------ 6. 규칙 시험 예, 실행 사이의 응답 캐시
code, text = cli("rules", "--test")
check("rules --test: 내장 규칙의 시험 예가 모두 통과", code == 0 and "30건 중 30건 통과" in text)
rj = os.path.join(work, "rules_t.json")
with open(rj, "w", encoding="utf-8") as fh:
    json.dump([{"id": "R-T1-MY", "tier": "T1", "title": "이름이 바뀐 함수", "pattern": "\\bOldName\\s*\\(",
                "action": {"type": "replace", "text": "NewName("},
                "tests": [{"code": "x = OldName(1);", "hits": 1, "after": "x = NewName(1);"}, {"code": "x = OldName2(1);", "hits": 1}]}], fh)
code, text = cli("rules", "--test", "--rules", rj)
check("rules --test: 외부 규칙의 시험 예를 돌리고, 틀린 기대는 실패로 알림(종료 코드 1)",
      code == 1 and "[통과] R-T1-MY #1" in text and "[실패] R-T1-MY #2" in text)
sc = os.path.join(work, "server_cache.json")
with open(sc, "w", encoding="utf-8") as fh:
    json.dump({"llm": {"endpoint": "http://127.0.0.1:%d/v1" % up.server_address[1], "shared_cache_file": os.path.join(work, "cache", "llm.db")}}, fh)
n_before = len(up.prompts)
c1, _t = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--server-config", sc, "--no-compile", "--out", os.path.join(work, "runs_c1"))
n_mid = len(up.prompts)
c2, _t = cli("run", "--package", pkg, "--from-vs", "2008", "--to-vs", "2022", "--server-config", sc, "--no-compile", "--out", os.path.join(work, "runs_c2"))
check("실행 사이의 응답 캐시: 같은 조각은 두 번째 실행에서 서버에 다시 묻지 않음",
      c1 == 0 and c2 == 0 and n_mid > n_before and len(up.prompts) == n_mid and os.path.isfile(os.path.join(work, "cache", "llm.db")))

relay.shutdown()
up.shutdown()
for name, okv in checks:
    print("[%s] %s" % ("통과" if okv else "실패", name))
print("합계: %d개 중 %d개 통과" % (len(checks), sum(1 for _n, okv in checks if okv)))
sys.exit(0 if all(okv for _n, okv in checks) else 1)
