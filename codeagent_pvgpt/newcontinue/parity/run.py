# parity/run.py — 차등 시험 실행기: 같은 시나리오를 cn과 ncn에 돌려 LLM이 받은 요청·파일 결과를 비교한다
# 근거: P0/소스확인_cn_실행_차등시험.md 2.5(격리 실행 템플릿)·3.6(비결정 요소 정규화)
# 사용:
#   uv run python -m parity.run --cn "node .ref/continue/extensions/cli/dist/cn.js" --ncn "uv run ncn"          # 전부
#   uv run python -m parity.run --cn "..." 02_read_then_answer                                                # 하나만, cn 기준 자료만 녹화
# 결과: parity/out/<시나리오>/<cn|ncn>.json(정규화된 기록) + 화면에 일치/불일치 요약
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄
import json                                                  # 기록
import os                                                    # 환경변수
import re                                                    # 정규화
import shlex                                                 # 명령 문자열 → 목록
import subprocess                                            # 하네스 실행
import sys                                                   # 이 파이썬 경로({PYTHON} 자리표시자)
import tempfile                                              # 임시 폴더
from pathlib import Path                                     # 경로

from parity.fake_llm import serve                            # 녹화형 가짜 LLM

HERE = Path(__file__).parent                                 # parity/
SCEN_DIR = HERE / "scenarios"                                # 시나리오 폴더
OUT_DIR = HERE / "out"                                       # 결과 폴더(git 제외)
MODEL = "fake-model"                                         # gpt-5*/o[0-9]* 이름 금지(cn이 /responses로 보냄 — P0 0장 4번)
KEEP_HEADERS = {"authorization", "content-type"}             # 비교할 헤더(SDK 고유 헤더 User-Agent·X-Stainless-* 제외)
DATE_LINE = re.compile(r"(Today's date: )\d{4}-\d{2}-\d{2}")   # 시스템 메시지의 날짜 줄(UTC)


def config_yaml(port: int, scen: dict | None = None) -> str:
    """cn·ncn이 같이 읽을 시험용 config.yaml. 모델은 인라인(uses·secrets 쓰면 Hub 호출 — P0 2.1).
    시나리오 선택 키: model_options{contextLength, maxTokens}(압축 시험), config_append(설정 원문 덧붙이기 — MCP 등)."""
    opts = (scen or {}).get("model_options", {})             # 시나리오의 모델 옵션 덮어쓰기(없으면 기본값)
    return (
        "name: parity\nversion: 0.0.1\nschema: v1\n"         # 설정 파일 본문 시작(이름·버전·스키마)
        "models:\n"                                          # models 목록
        "  - name: fake\n"                                   # 모델 이름
        "    provider: openai\n"                             # 공급자
        f"    model: {MODEL}\n"                              # 가짜 모델명
        f"    apiBase: http://127.0.0.1:{port}/v1/\n"        # 가짜 LLM 주소
        "    apiKey: sk-parity\n"                            # 키(아무 값)
        "    roles: [chat]\n"                                # 역할
        "    capabilities: [tool_use]\n"                     # 도구 사용 능력
        "    defaultCompletionOptions:\n"                    # 완성 옵션
        f"      contextLength: {opts.get('contextLength', 32768)}\n"   # 기본은 넉넉히(압축은 시나리오에서 줄여서 시험)
        f"      maxTokens: {opts.get('maxTokens', 4096)}\n"            # 출력 토큰 상한
    ) + expand_placeholders((scen or {}).get("config_append", ""))   # 시나리오가 덧붙이는 설정 원문(예: mcpServers 블록)


def expand_placeholders(text: str) -> str:
    """config_append 자리표시자: {PYTHON} = 이 실행기의 파이썬(mcp SDK 가 깔린 .venv), {PARITY_DIR} = parity 폴더 절대 경로."""
    return text.replace("{PYTHON}", sys.executable).replace("{PARITY_DIR}", str(HERE.resolve()))   # 두 하네스가 같은 경로를 쓰게


def isolated_env(tmp: Path) -> dict:
    """env -i 처럼 깨끗한 환경. OTEL_*·ANTHROPIC_API_KEY·CONTINUE_USE_AI_SDK·프록시 변수는 넣지 않는다(P0 2.3~2.5)."""
    return {                                                 # 환경변수 사전
        "PATH": os.environ.get("PATH", ""),                  # 실행 파일 찾기(rg 유무도 PATH로 같게)
        "HOME": str(tmp / "home"),                           # ~/.claude/settings.json 등 실제 홈 차단
        "CONTINUE_GLOBAL_DIR": str(tmp / "cnhome"),          # ~/.continue 대신
        "CONTINUE_API_BASE": "http://127.0.0.1:9/",          # 실수로 일어난 Hub 호출은 즉시 실패
        "CONTINUE_CLI_AUTO_UPDATED": "true",                 # 자동 업데이트 설치 막기
        "CONTINUE_METRICS_ENABLED": "0",                     # 텔레메트리 끄기
        "CONTINUE_CLI_ENABLE_TELEMETRY": "0",
        "CONTINUE_CLI_TEST_SESSION_ID": "parity-0001",       # 세션 id 고정
        "NO_PROXY": "127.0.0.1,localhost",                   # 가짜 LLM은 프록시 없이
        "LANG": "en_US.UTF-8",                               # 출력 인코딩 고정
    }


def normalize_text(s: str, repl: dict[str, str]) -> str:
    """글 안의 임시 경로·날짜를 자리표시자로 바꾼다."""
    for real, tag in sorted(repl.items(), key=lambda kv: -len(kv[0])):   # 긴 경로부터(겹침 방지)
        s = s.replace(real, tag)                             # 경로 치환
    return DATE_LINE.sub(r"\g<1><DATE>", s)                  # 날짜


def normalize_value(v, repl):
    """JSON 값 전체를 재귀로 정규화."""
    if isinstance(v, str):                                   # 문자열이면 정규화
        return normalize_text(v, repl)                       # 치환 결과 반환
    if isinstance(v, list):                                  # 목록이면 원소마다
        return [normalize_value(x, repl) for x in v]         # 각 원소 재귀
    if isinstance(v, dict):                                  # 사전이면 값마다
        return {k: normalize_value(x, repl) for k, x in v.items()}  # 각 값 재귀
    return v                                                 # 그 밖의 값은 그대로


def normalize_request(r: dict, repl: dict[str, str]) -> dict:
    """요청 하나 → 비교용 형태: 경로·화이트리스트 헤더·정규화된 본문. 도구 인자 문자열은 파싱해서 비교."""
    body = normalize_value(r["body"], repl)                  # 경로·날짜 치환
    for m in body.get("messages", []):                       # 도구 인자 문자열은 JSON 값으로
        for tc in m.get("tool_calls") or []:                 # 호출 항목마다
            fn = tc.get("function") or {}                    # 함수 부분
            try:                                             # 파싱 시도
                fn["arguments"] = json.loads(fn.get("arguments") or "{}")  # 문자열 → JSON 값
            except ValueError:                               # 실패해도 무시
                pass                                         # 파싱 안 되면 문자열 그대로 비교
    headers = {k.lower(): v for k, v in r["headers"].items() if k.lower() in KEEP_HEADERS}   # 헤더 화이트리스트
    return {"path": r["path"], "headers": headers, "body": body}  # 비교용 요청 반환


def make_workspace(root: Path, files: dict[str, str]) -> None:
    """시나리오의 파일을 만든다(git 아님 → 시스템 메시지의 git 상태가 두 하네스에서 같음)."""
    for rel, text in files.items():                          # 파일마다
        p = root / rel                                       # 대상 경로
        p.parent.mkdir(parents=True, exist_ok=True)          # 하위 폴더
        p.write_bytes(text.encode("utf-8"))                  # 줄바꿈 변환 없이 그대로


def read_workspace(root: Path) -> dict[str, str]:
    """실행 후 작업 폴더의 파일 내용(.continue 등 숨김 폴더 제외)."""
    out = {}                                                 # 결과 사전
    for p in sorted(root.rglob("*")):                        # 모든 파일 순회
        rel = p.relative_to(root).as_posix()                 # 작업 폴더 기준 상대 경로
        if p.is_file() and not rel.startswith("."):          # 숨김 폴더(.continue 등) 제외
            out[rel] = p.read_bytes().decode("utf-8", "replace")  # 내용 읽기
    return out                                               # 파일 내용 반환


def run_one(scen: dict, cmd: list[str], timeout: int = 120) -> dict:
    """시나리오 하나를 하네스 하나로 실행 → 정규화된 결과."""
    with tempfile.TemporaryDirectory(prefix="parity-") as t:  # 실행 후 자동 삭제되는 임시 폴더
        tmp = Path(t).resolve()                              # macOS는 /var → /private/var 실제 경로
        work, home, cnhome = tmp / "work", tmp / "home", tmp / "cnhome"  # 작업·홈·cn홈 폴더
        for d in (work, home, cnhome):                       # 세 폴더 만들기
            d.mkdir()                                        # 폴더 생성
        make_workspace(work, scen["workspace"]["files"])     # 작업 폴더
        server, rec = serve(scen["llm"])                     # 가짜 LLM(빈 포트)
        try:                                                 # 서버 종료를 보장하려 try
            cfg = cnhome / "config.yaml"                     # 설정 파일 경로
            cfg.write_text(config_yaml(server.server_port, scen), encoding="utf-8")   # 설정(시나리오 덮어쓰기 반영)
            argv = cmd + ["-p", scen["prompt"], "--config", str(cfg)] + scen.get("args", [])   # cn과 같은 옵션
            proc = subprocess.run(argv, cwd=work, env=isolated_env(tmp), stdin=subprocess.DEVNULL,  # 하네스 실행(입력 없음)
                                  capture_output=True, timeout=timeout)   # 실행
        finally:                                             # 예외가 나도
            server.shutdown()                                # 가짜 LLM 종료
        repl = {str(work): "<CWD>", str(home): "<HOME>", str(cnhome): "<CNHOME>", str(tmp): "<TMP>"}   # 치환표
        return {                                             # 결과 사전 반환
            "exit_code": proc.returncode,                    # 종료 코드(정상 0)
            "stdout": normalize_text(proc.stdout.decode("utf-8", "replace"), repl),   # 최종 응답
            "requests": [normalize_request(r, repl) for r in rec.requests],           # LLM이 받은 요청
            "files": read_workspace(work),                   # 파일 결과
            "stderr_tail": proc.stderr.decode("utf-8", "replace")[-2000:],           # 진단용(비교 안 함)
        }


def diff(a, b, path="") -> list[str]:
    """두 JSON 값의 차이 목록(경로: a ≠ b)."""
    if type(a) is not type(b):                               # 형식이 다르면 바로 차이
        return [f"{path}: 형식 {type(a).__name__} ≠ {type(b).__name__}"]  # 형식 불일치 보고
    if isinstance(a, dict):                                  # 사전이면
        out = []                                             # 차이 목록
        for k in sorted(set(a) | set(b)):                    # 키 합집합 순회
            if k not in a or k not in b:                     # 한쪽에만 있는 키
                out.append(f"{path}.{k}: {'cn에만' if k in a else 'ncn에만'} 있음")  # 어느 쪽에 있는지 보고
            else:                                            # 양쪽에 있으면
                out += diff(a[k], b[k], f"{path}.{k}")       # 값을 재귀 비교
        return out                                           # 차이 목록 반환
    if isinstance(a, list):                                  # 목록이면
        out = [f"{path}: 길이 {len(a)} ≠ {len(b)}"] if len(a) != len(b) else []  # 길이가 다르면 보고
        for i, (x, y) in enumerate(zip(a, b)):               # 원소 쌍 비교
            out += diff(x, y, f"{path}[{i}]")                # 재귀 비교
        return out                                           # 차이 목록 반환
    return [] if a == b else [f"{path}: {str(a)[:80]!r} ≠ {str(b)[:80]!r}"]  # 스칼라 값 비교


def check_expect(scen: dict, res: dict) -> list[str]:
    """시나리오의 기대값(파일 결과·요청 수)과 비교."""
    errs = []                                                # 오류 목록
    if res["files"] != scen["expect"]["files"]:              # 파일 결과 비교
        errs.append("파일 결과가 기대와 다름")                         # 불일치 보고
    if len(res["requests"]) != scen["expect"]["requests"]:   # 요청 수 비교
        errs.append(f"요청 수 {len(res['requests'])} ≠ 기대 {scen['expect']['requests']}")  # 불일치 보고
    return errs                                              # 오류 목록 반환


def absolutize(cmd: list[str]) -> list[str]:
    """명령 속 상대 경로(지금 폴더에 실제로 있는 것)를 절대 경로로 — 하네스는 임시 작업 폴더에서 실행되므로."""
    return [str(Path(c).resolve()) if not c.startswith("-") and Path(c).is_file() else c for c in cmd]   # 파일만(폴더 이름 ncn 등은 그대로)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="cn ↔ ncn 차등 시험")  # 인자 파서
    ap.add_argument("--cn", help='cn 실행 명령(예: "node .ref/continue/extensions/cli/dist/cn.js")')  # cn 명령 옵션
    ap.add_argument("--ncn", help='ncn 실행 명령(예: "uv run ncn")')  # ncn 명령 옵션
    ap.add_argument("names", nargs="*", help="시나리오 이름(비우면 전부)")  # 시나리오 이름 목록
    a = ap.parse_args(argv)                                  # 인자 해석
    harnesses = {k: absolutize(shlex.split(v)) for k, v in (("cn", a.cn), ("ncn", a.ncn)) if v}   # 돌릴 하네스
    if not harnesses:                                        # 하네스가 하나도 없으면
        ap.error("--cn 또는 --ncn 중 하나 이상 필요")                 # 사용법 오류 종료
    files = sorted(SCEN_DIR.glob("*.json"))                  # 시나리오 파일 목록
    if a.names:                                              # 이름 지정이 있으면
        files = [f for f in files if f.stem in a.names]      # 해당 시나리오만
    total_ok = 0                                             # 일치 건수
    for f in files:                                          # 시나리오마다
        scen = json.loads(f.read_text(encoding="utf-8"))     # 시나리오 읽기
        results = {}                                         # 하네스별 결과
        for name, cmd in harnesses.items():                  # 하네스별 실행·저장
            res = run_one(scen, cmd)
            results[name] = res                              # 결과 보관
            out = OUT_DIR / scen["name"]                     # 출력 폴더
            out.mkdir(parents=True, exist_ok=True)           # 폴더 생성
            (out / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")  # 기록 파일 저장
        line = [scen["name"]]                                # 요약 줄 시작
        for name, res in results.items():                   # 기대값 검사
            errs = check_expect(scen, res)                   # 기대값 확인
            line.append(f"{name}: {'기대 일치' if not errs else '; '.join(errs)} (exit {res['exit_code']})")  # 요약에 추가
        if len(results) == 2:                                # 두 하네스 비교
            keys = ("exit_code", "stdout", "requests", "files")
            d = diff({k: results["cn"][k] for k in keys}, {k: results["ncn"][k] for k in keys})  # 비교 대상 키만 비교
            line.append("cn=ncn 일치" if not d else f"차이 {len(d)}건 → parity/out/{scen['name']}/diff.txt")  # 요약에 일치 여부
            (OUT_DIR / scen["name"] / "diff.txt").write_text("\n".join(d), encoding="utf-8")  # 차이 파일 저장
            total_ok += not d                                # 일치면 1 가산
        print(" | ".join(line))                              # 요약 한 줄
    if len(harnesses) == 2:                                  # 둘 다 돌렸을 때만
        print(f"일치 {total_ok}/{len(files)}")              # 일치율
    return 0                                                 # 정상 종료 코드


if __name__ == "__main__":
    raise SystemExit(main())                                 # 종료 코드로 main 결과 사용
