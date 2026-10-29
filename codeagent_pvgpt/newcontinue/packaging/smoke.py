# packaging/smoke.py — 빌드한 ncn 실행 파일 스모크 시험(로컬·CI 공용). cn 원본 없음(배포 전용)
# 실행: cd newcontinue && uv run python packaging/smoke.py --exe dist/ncn/ncn(.exe) [--report dist/smoke.json]
# 시험: ① --version(시작 시간 측정) ② 가짜 LLM(parity/fake_llm.py)으로 -p 한 번 ③ --stdio 에 initialize 한 줄 → JSON 응답
#       ④ --doctor --format json(가짜 서버로 설정·서버 점검 통과) — 하나라도 실패하면 종료 코드 1
# 실행 파일은 격리 환경(임시 HOME·CONTINUE_GLOBAL_DIR)에서 돈다 — 개발자·러너의 ~/.continue 를 건드리지 않는다.
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄
import json                                                  # 응답 해석·보고서
import os                                                    # 환경변수
import subprocess                                            # 실행 파일 실행
import sys                                                   # 경로·종료
import tempfile                                              # 임시 폴더
import threading                                             # stdio 응답 읽기 시간 제한
import time                                                  # 시간 측정
from pathlib import Path                                     # 경로

ROOT = Path(__file__).resolve().parent.parent                # newcontinue 폴더
sys.path.insert(0, str(ROOT))                                # parity 패키지 import 용
from parity import fake_llm                                  # noqa: E402 — 가짜 OpenAI 호환 LLM(표준 라이브러리만)

ANSWER = "스모크 시험 응답 OK"                                  # 가짜 LLM 이 돌려줄 글(한글 — 출력 인코딩 확인 겸)
TIMEOUT = 60                                                 # 명령 하나 시간 제한(초) — 첫 실행은 백신 검사로 느릴 수 있음


def isolated_env(tmp: Path) -> dict:                         # 격리 환경변수
    """실제 홈·설정을 막고, 프록시를 끄고, 출력 인코딩을 고정한다(parity/run.py isolated_env 와 같은 생각)."""
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA", "PROCESSOR_ARCHITECTURE")}   # 꼭 필요한 OS 변수만
    env.update({                                             # 격리 값
        "HOME": str(tmp / "home"), "USERPROFILE": str(tmp / "home"),   # 홈(posix·Windows)
        "CONTINUE_GLOBAL_DIR": str(tmp / "cnhome"),          # ~/.continue 대신
        "NO_PROXY": "127.0.0.1,localhost",                   # 가짜 LLM 은 프록시 없이
        "LANG": "en_US.UTF-8",                               # 인코딩 고정(posix)
    })                                                       # (이어짐)
    return env                                               # 환경변수


def write_config(cnhome: Path, port: int) -> Path:           # 시험 설정
    """가짜 LLM 을 가리키는 config.yaml(parity/run.py config_yaml 과 같은 모양)."""
    cnhome.mkdir(parents=True, exist_ok=True)                # 폴더
    p = cnhome / "config.yaml"                               # 전역 설정 위치(--doctor 도 여기서 찾음)
    p.write_text("name: smoke\nversion: 0.0.1\nschema: v1\nmodels:\n  - name: fake\n    provider: openai\n"   # 모델 블록
                 f"    model: fake-model\n    apiBase: http://127.0.0.1:{port}/v1/\n    apiKey: sk-smoke\n"   # (이어짐)
                 "    roles: [chat]\n    capabilities: [tool_use]\n", encoding="utf-8")   # (이어짐)
    return p                                                 # 경로


def run(cmd: list[str], **kw) -> tuple[subprocess.CompletedProcess, float]:   # 실행 + 걸린 시간
    """UTF-8 로 출력 받기, 시간 제한, 입력 없음."""
    t0 = time.perf_counter()                                 # 시작
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, stdin=subprocess.DEVNULL, **kw)   # 실행
    return r, time.perf_counter() - t0                       # 결과·초


def t_version(exe: str, env: dict, cwd: Path, version: str) -> dict:   # ① --version
    """`ncn --version` → "ncn <버전>". 두 번 재서 두 번째(디스크 캐시 후)를 시작 시간으로 본다."""
    r1, s1 = run([exe, "--version"], env=env, cwd=cwd)       # 첫 실행(백신 검사·캐시 없음)
    r2, s2 = run([exe, "--version"], env=env, cwd=cwd)       # 두 번째
    ok = r2.returncode == 0 and r2.stdout.strip() == f"ncn {version}"   # 기대 출력
    return {"name": "version", "ok": ok, "stdout": r2.stdout.strip(), "stderr": r2.stderr[-2000:],   # 결과
            "first_seconds": round(s1, 3), "warm_seconds": round(s2, 3)}   # 시간


def t_print(exe: str, env: dict, cwd: Path, cfg: Path, rec) -> dict:   # ② -p 한 번
    """가짜 LLM 이 ANSWER 를 돌려주면 stdout 에 그 글, 종료 코드 0, 요청 1건."""
    before = len(rec.requests)                               # 이전 요청 수
    r, s = run([exe, "-p", "안녕? 한 줄로 답해줘", "--config", str(cfg)], env=env, cwd=cwd)   # 헤드리스 실행
    n = len(rec.requests) - before                           # 이번 요청 수
    ok = r.returncode == 0 and ANSWER in r.stdout and n == 1   # 기대
    return {"name": "print", "ok": ok, "exit": r.returncode, "stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:],   # 결과
            "requests": n, "seconds": round(s, 3)}           # 요청 수·시간


def t_stdio(exe: str, env: dict, cwd: Path, cfg: Path) -> dict:   # ③ --stdio initialize
    """ACP initialize 요청 한 줄 → 첫 stdout 줄이 같은 id 의 JSON-RPC 결과(protocolVersion 포함)."""
    req = {"jsonrpc": "2.0", "id": 0, "method": "initialize",   # JSON-RPC 요청
           "params": {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}}   # 최소 능력
    t0 = time.perf_counter()                                 # 시작
    p = subprocess.Popen([exe, "--stdio", "--config", str(cfg)], cwd=cwd, env=env,   # ACP 모드
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)   # 바이너리 파이프
    box: dict = {}                                           # 읽은 줄
    def reader():                                            # stdout 첫 줄 읽기(스레드 — 시간 제한용)
        box["line"] = p.stdout.readline()                    # 한 줄
    th = threading.Thread(target=reader, daemon=True)        # 읽기 스레드
    th.start()                                               # 시작
    p.stdin.write((json.dumps(req) + "\n").encode("utf-8"))  # 요청 한 줄
    p.stdin.flush()                                          # 보내기
    th.join(TIMEOUT)                                         # 응답 기다림
    secs = time.perf_counter() - t0                          # 응답까지 시간
    line = box.get("line", b"")                              # 받은 줄
    try:                                                     # 정리
        p.stdin.close()                                      # 입력 닫기 → ncn 이 끝나야 함
        p.wait(timeout=15)                                   # 종료 기다림
    except Exception:                                        # 안 끝나면
        p.kill()                                             # 강제 종료
    err = p.stderr.read().decode("utf-8", "replace")[-2000:] if p.stderr else ""   # stderr 끝부분
    try:                                                     # JSON 해석
        msg = json.loads(line)                               # 응답
        ok = msg.get("id") == 0 and isinstance(msg.get("result"), dict) and "protocolVersion" in msg["result"]   # 기대 모양
    except Exception:                                        # JSON 아님(stdout 오염 등)
        msg, ok = None, False                                # 실패
    return {"name": "stdio", "ok": ok, "line": line.decode("utf-8", "replace")[:2000], "stderr": err,   # 결과
            "exit": p.returncode, "seconds": round(secs, 3)}   # 종료 코드·시간


def t_doctor(exe: str, env: dict, cwd: Path) -> dict:        # ④ --doctor --format json
    """JSON 하나가 나오고 설정·chat 모델·서버 항목이 ok."""
    r, s = run([exe, "--doctor", "--format", "json"], env=env, cwd=cwd)   # 점검
    try:                                                     # 해석
        j = json.loads(r.stdout)                             # JSON
        st = {c["id"]: c["status"] for c in j["checks"]}     # id → 상태
        ok = all(st.get(k) == "ok" for k in ("version", "data", "config", "chat_model", "server"))   # 필수 항목
    except Exception:                                        # JSON 아님
        st, ok = {}, False                                   # 실패
    return {"name": "doctor", "ok": ok, "exit": r.returncode, "statuses": st, "stdout": r.stdout[-3000:] if not ok else "",   # 결과
            "stderr": r.stderr[-2000:], "seconds": round(s, 3)}   # 시간


def main(argv: list[str] | None = None) -> int:              # 진입
    for stream in (sys.stdout, sys.stderr):                  # Windows 콘솔·CI 로그가 cp1252/cp949 여도 →·한글 출력에서 죽지 않게
        try:                                                 # 재설정이 안 되는 스트림은 그대로
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined] — UTF-8 로 출력
        except Exception:                                    # 재설정 불가
            pass                                             # 무시
    """네 시험을 돌리고 요약 출력·보고서(JSON) 저장."""
    ap = argparse.ArgumentParser(description="ncn 실행 파일 스모크 시험")   # 명령줄
    ap.add_argument("--exe", required=True, help="빌드한 ncn 실행 파일 경로")   # 실행 파일
    ap.add_argument("--report", help="결과 JSON 저장 경로")    # 보고서
    a = ap.parse_args(argv)                                  # 해석
    exe = str(Path(a.exe).resolve())                         # 절대 경로
    sys.path.insert(0, str(ROOT / "packaging"))              # build.py 의 버전 읽기 재사용
    from build import read_version                           # 버전
    version = read_version()                                 # 기대 버전
    srv, rec = fake_llm.serve([{"content": ANSWER}] * 4)     # 가짜 LLM(응답 넉넉히)
    results = []                                             # 결과
    try:                                                     # 서버 정리 보장
        with tempfile.TemporaryDirectory(prefix="ncn-smoke-") as t:   # 임시 폴더
            tmp = Path(t).resolve()                          # 실제 경로
            work = tmp / "work"                              # 작업 폴더
            work.mkdir()                                     # 만들기
            (tmp / "home").mkdir()                           # 홈
            env = isolated_env(tmp)                          # 격리 환경
            cfg = write_config(tmp / "cnhome", srv.server_port)   # 설정
            for fn in (lambda: t_version(exe, env, work, version), lambda: t_print(exe, env, work, cfg, rec),   # 시험들
                       lambda: t_stdio(exe, env, work, cfg), lambda: t_doctor(exe, env, work)):   # (이어짐)
                try:                                         # 시험 하나
                    results.append(fn())                     # 실행
                except Exception as e:                       # 시간 초과·실행 실패
                    results.append({"name": "?", "ok": False, "error": f"{type(e).__name__}: {e}"})   # 실패 기록
    finally:                                                 # 언제나
        srv.shutdown()                                       # 서버 끄기
    for r in results:                                        # 요약
        extra = {k: v for k, v in r.items() if k.endswith("seconds")}   # 시간 값
        print(f"[{'OK' if r['ok'] else 'FAIL'}] {r['name']} {extra}", flush=True)   # 한 줄
        if not r["ok"]:                                      # 실패면 자세히
            print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)   # 전체
    if a.report:                                             # 보고서
        Path(a.report).write_text(json.dumps({"exe": exe, "version": version, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")   # 저장
    return 0 if all(r["ok"] for r in results) else 1         # 종료 코드


if __name__ == "__main__":                                   # 직접 실행
    sys.exit(main())                                         # 종료 코드
