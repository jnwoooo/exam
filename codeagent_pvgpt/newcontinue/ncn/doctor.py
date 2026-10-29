# ncn/doctor.py — `ncn --doctor`: 설치·연결 점검(계획 8-2 5번). cn 원본 없음(ncn 전용 추가 기능, 계획 6장)
# 점검 항목: 버전·실행 형태 → 내장 자료(o200k) → 설정 파일 위치·읽기 → chat 모델·토큰 → 서버 /v1/models → MCP 접속 → 콘솔 한글 → rg → PATH
# 출력: 사람이 읽는 표(기본) 또는 `--format json`(현장 결과를 웹 세션에 붙여 넣기 좋게). 토큰 값은 절대 출력하지 않는다(앞 3글자·길이만).
# 종료 코드: "실패"가 하나라도 있으면 1, 아니면 0(경고는 0) — install.cmd 와 배치 파일이 이 값으로 성공 여부를 안다.
# 연결: ncn/app.py main() 이 argv 에 --doctor 가 있으면 main(argv) 를 부른다(배포 묶음의 진입점 packaging/ncn_entry.py 도 같은 함수).
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # --doctor 전용 옵션 해석
import asyncio                                               # 서버·MCP 점검(비동기)
import json                                                  # --format json 출력
import os                                                    # 환경변수·현재 폴더
import pathlib                                               # 경로
import platform                                              # OS·CPU 정보
import shutil                                                # rg·ncn 위치 찾기
import subprocess                                            # rg --version
import sys                                                   # 표준 출력·실행 형태
import unicodedata                                           # 표 정렬(한글 2칸 폭)
from dataclasses import asdict, dataclass, field             # 결과 형식
from typing import Any, Mapping, Optional, Sequence          # 타입

from ncn import __version__                                  # ncn 버전

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"          # 상태 값(JSON 에 그대로 — 기계가 읽는 값은 영어)
LABEL = {OK: "OK", WARN: "경고", FAIL: "실패", SKIP: "건너뜀"}   # 표에 보이는 상태 글
TOKEN_ENV = "CODEAGENT_TOKEN"                                # 코드에이전트 개인 토큰 변수 이름(continue/config.yaml 과 같음)
DEFAULT_TIMEOUT_S = 10.0                                     # 서버 /v1/models 시간 제한(초)
DEFAULT_MCP_TIMEOUT_S = 30.0                                 # MCP 전체 접속 시간 제한(초)
KOREAN_SAMPLE = "한글 출력 확인: 가나다 ㄱㄴㄷ"                 # 콘솔 한글 출력 시험 글


@dataclass                                                   # 결과 한 줄
class Check:                                                 # 점검 항목 하나의 결과
    """점검 결과 하나 — id(영어, 고정)·title(한국어)·status(ok/warn/fail/skip)·detail(내용)·hint(고치는 방법)·data(기계용 값)."""
    id: str                                                  # 항목 id(JSON·시험에서 찾는 키)
    title: str                                               # 표에 보이는 항목 이름
    status: str                                              # 상태(OK/WARN/FAIL/SKIP 중 하나)
    detail: str = ""                                         # 사람이 읽는 내용(한 줄 또는 여러 줄)
    hint: str = ""                                           # 실패·경고일 때 할 일
    data: dict = field(default_factory=dict)                 # 기계용 부가 값(경로·개수 등 — 비밀값 없음)


# ─────────────────────────────────────────────────────────────────────────────
# 도우미
# ─────────────────────────────────────────────────────────────────────────────


def mask_secret(v: Optional[str]) -> str:                    # 비밀값 가리기
    """토큰을 앞 3글자 + 길이로만 보여 준다("ca-…(40자)"). 비었으면 "(없음)"."""
    if not v:                                                # 값이 없으면
        return "(없음)"                                       # 없음 표시
    return f"{v[:3]}…({len(v)}자)"                            # 앞 3글자와 길이만


def is_unresolved_secret(v: Optional[str]) -> bool:          # 비밀 템플릿이 못 채워진 채 남았나
    """설정 읽기가 비밀을 못 찾으면 apiKey 가 "${{ secrets.… }}" 글 그대로 남는다(ncn/config/loader.py ModelConfig.api_key)."""
    return isinstance(v, str) and "${{" in v                 # 템플릿 표시가 남았으면 참


def _exc_text(e: BaseException) -> str:                      # 예외를 한 줄 글로
    """예외 종류와 글(비어 있으면 종류만)."""
    msg = str(e).strip()                                     # 예외 글
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__   # "종류: 글"


def node_arch() -> str:                                      # CPU 구조를 Node 표기로
    """x64 / arm64 / x86(묶음 이름·표시용)."""
    m = platform.machine().lower()                           # 파이썬이 본 CPU 이름
    return {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64", "x86": "x86", "i386": "x86", "i686": "x86"}.get(m, m)   # 표기 맞춤


# ─────────────────────────────────────────────────────────────────────────────
# 점검 함수(각각 Check 하나 또는 여러 개를 돌려준다 — 예외를 밖으로 내지 않는다)
# ─────────────────────────────────────────────────────────────────────────────


def check_version() -> Check:                                # 버전·실행 형태
    """ncn 버전, 파이썬 버전, OS·CPU, 실행 형태(PyInstaller 묶음인지 소스 실행인지)."""
    frozen = bool(getattr(sys, "frozen", False))             # PyInstaller 로 만든 실행 파일이면 참
    where = sys.executable if frozen else f"python {sys.executable}"   # 실행 파일 위치
    osname = f"{platform.system()} {platform.release()}" + (f" ({platform.version()})" if sys.platform == "win32" else "")   # OS 이름·버전(Windows 는 빌드 번호까지)
    detail = f"ncn {__version__} · Python {platform.python_version()} · {osname} · {node_arch()}"   # 한 줄 요약
    return Check("version", "버전", OK, detail + f"\n실행: {where}",   # 버전은 늘 OK(정보 제공)
                 data={"ncn": __version__, "python": platform.python_version(), "os": platform.system(),   # 기계용 값
                       "os_version": platform.version(), "arch": node_arch(), "frozen": frozen, "executable": sys.executable})   # (이어짐)


def check_data() -> Check:                                   # 내장 자료(토큰 순위표)
    """ncn/data/o200k_base.tiktoken 이 묶음에 들어 있고 해시가 맞는지 — 실제로 한 번 토큰을 세 본다."""
    from ncn import tokenizer                                # 토큰 세기(순위표를 읽음)
    path = tokenizer.DATA_FILE                               # 순위표 위치(묶음이면 _internal/ncn/data 아래)
    if not path.is_file():                                   # 파일이 없으면
        return Check("data", "내장 자료", FAIL, f"토큰 순위표 없음: {path}",   # 실패
                     "배포 묶음이 손상되었습니다. zip 을 다시 받아 설치하세요(packaging/ncn.spec 의 datas 확인).", {"path": str(path)})   # 할 일
    try:                                                     # 읽기·해시·인코딩 시험
        n = len(tokenizer.encode("hello 안녕"))               # 실제로 세어 봄(해시 불일치면 예외)
    except Exception as e:                                   # 해시 불일치·손상
        return Check("data", "내장 자료", FAIL, _exc_text(e), "배포 묶음이 손상되었습니다. zip 을 다시 받아 설치하세요.", {"path": str(path)})   # 실패
    return Check("data", "내장 자료", OK, f"토큰 순위표 정상({n} 토큰 시험)", data={"path": str(path)})   # 정상


def config_location(config_path: Optional[str], environ: Mapping[str, str], cwd: pathlib.Path) -> pathlib.Path:   # 읽을 설정 파일 위치
    """--config 가 있으면 그 경로(cwd 기준), 없으면 <전역 폴더>/config.yaml(ncn/config/loader.py load_config 와 같은 순서)."""
    from ncn.config import paths as cpaths                   # 전역 폴더 규칙
    if config_path:                                          # --config 지정
        p = pathlib.Path(config_path)                        # 지정 경로
        return p if p.is_absolute() else cwd / p             # 상대경로면 cwd 기준
    return cpaths.global_config_yaml(cpaths.global_dir(environ, cwd))   # ~/.continue/config.yaml


def check_config(config_path: Optional[str], environ: Mapping[str, str], cwd: pathlib.Path) -> tuple[Check, Any]:   # 설정 파일 위치·읽기
    """설정 파일 위치·존재·읽기. 돌려주는 값 = (결과, LoadedConfig 또는 None)."""
    from ncn.config.loader import ConfigError, load_config   # 설정 읽기(헤드리스와 같은 함수)
    from ncn.config import paths as cpaths                   # 전역 폴더
    loc = config_location(config_path, environ, cwd)          # 읽을 파일
    gdir = cpaths.global_dir(environ, cwd)                   # 전역 폴더(~/.continue)
    data = {"path": str(loc), "global_dir": str(gdir), "exists": loc.is_file()}   # 기계용 값
    if not loc.is_file():                                    # 파일이 없으면
        return Check("config", "설정 파일", FAIL, f"없음: {loc}",   # 실패
                     f"install.cmd 를 다시 실행하거나 config.template.yaml 을 {gdir / 'config.yaml'} 로 복사하세요.", data), None   # 할 일
    try:                                                     # 읽기(헤드리스와 같은 규칙: 모델이 없으면 오류)
        cfg = load_config(config_path, cwd=cwd, global_dir=gdir, environ=environ, headless=True)   # 설정 읽기
    except ConfigError as e:                                 # 설정 오류(cn 원문 글)
        return Check("config", "설정 파일", FAIL, f"{loc}\n읽기 실패: {e.message}", "YAML 문법(들여쓰기·탭)과 models 항목을 확인하세요.", data), None   # 실패
    except Exception as e:                                   # 그 밖의 오류
        return Check("config", "설정 파일", FAIL, f"{loc}\n읽기 실패: {_exc_text(e)}", "YAML 문법(들여쓰기·탭)을 확인하세요.", data), None   # 실패
    data.update({"models": len(cfg.models), "mcp_servers": len([s for s in cfg.mcp_servers if s]), "warnings": list(cfg.warnings)})   # 개수·경고
    detail = f"{loc}\n모델 {len(cfg.models)}개 · MCP 서버 {data['mcp_servers']}개 · 규칙 {len(cfg.rule_texts)}개"   # 요약
    if cfg.warnings or cfg.errors:                           # 경고가 있으면
        lines = [str(w) for w in cfg.warnings] + [str(d.get("message")) for d in cfg.errors if isinstance(d, dict)]   # 경고 글들
        return Check("config", "설정 파일", WARN, detail + "\n" + "\n".join(lines), "경고 내용을 확인하세요(동작은 계속됨).", data), cfg   # 경고
    return Check("config", "설정 파일", OK, detail, data=data), cfg   # 정상


def check_token_file(environ: Mapping[str, str], cwd: pathlib.Path) -> Check:   # 토큰 저장 위치
    """CODEAGENT_TOKEN 을 어디서 찾는지(환경변수 → ~/.continue/.env → <cwd>/.continue/.env → <cwd>/.env — ncn/config/secrets.py 순서)."""
    from ncn.config import env as cenv                       # .env 읽기
    from ncn.config import paths as cpaths                   # 전역 폴더
    gdir = cpaths.global_dir(environ, cwd)                   # 전역 폴더
    if environ.get(TOKEN_ENV):                               # 환경변수에 있으면(빈 글은 없는 것)
        return Check("token", "개인 토큰", OK, f"환경변수 {TOKEN_ENV} = {mask_secret(environ[TOKEN_ENV])}", data={"source": "env"})   # 정상
    for p in (gdir / ".env", cwd / ".continue" / ".env", cwd / ".env"):   # 찾는 순서대로
        parsed = cenv.read_dotenv_file(str(p)) or {}         # .env 읽기(없으면 빈 사전)
        if parsed.get(TOKEN_ENV):                            # 값이 있으면
            return Check("token", "개인 토큰", OK, f"{p} : {TOKEN_ENV} = {mask_secret(parsed[TOKEN_ENV])}", data={"source": str(p)})   # 정상
    return Check("token", "개인 토큰", WARN, f"{TOKEN_ENV} 를 찾지 못함({gdir / '.env'} 등)",   # 경고(설정이 토큰을 안 쓸 수도 있으므로)
                 f"{gdir / '.env'} 에 {TOKEN_ENV}=ca-... 한 줄을 넣거나 install.cmd 를 다시 실행하세요.", {"source": None})   # 할 일


def check_chat_model(cfg: Any) -> Check:                     # chat 모델
    """고른 chat 모델 이름·apiBase, apiKey 가 채워졌는지(비밀 템플릿이 남았는지)."""
    if cfg is None:                                          # 설정을 못 읽었으면
        return Check("chat_model", "chat 모델", SKIP, "설정을 읽지 못해 건너뜀")   # 건너뜀
    m = cfg.chat_model                                       # 고른 모델
    if m is None:                                            # 못 골랐으면
        return Check("chat_model", "chat 모델", FAIL, cfg.chat_model_error or "No models were found.", "config.yaml 의 models 에 roles: [chat] 모델을 넣으세요.")   # 실패
    detail = f"{m.name} (model={m.model}, provider={m.provider})\napiBase: {m.api_base}"   # 요약
    data = {"name": m.name, "model": m.model, "provider": m.provider, "api_base": m.api_base}   # 기계용 값(키 제외)
    if not m.api_base:                                       # 주소가 없으면
        return Check("chat_model", "chat 모델", FAIL, detail, "모델에 apiBase(예: http://서버:8200/v1)를 적으세요.", data)   # 실패
    if is_unresolved_secret(m.api_key):                      # 비밀을 못 찾음
        return Check("chat_model", "chat 모델", FAIL, detail + f"\napiKey: 비밀값을 찾지 못함({m.api_key})",   # 실패
                     f"~/.continue/.env 에 {TOKEN_ENV}=ca-... 를 넣으세요(install.cmd 가 물어봄).", data)   # 할 일
    return Check("chat_model", "chat 모델", OK, detail + f"\napiKey: {mask_secret(m.api_key)}", data=data)   # 정상


async def check_server(cfg: Any, timeout: float = DEFAULT_TIMEOUT_S) -> Check:   # 서버 /v1/models
    """chat 모델의 apiBase 로 GET /models — 연결·토큰 확인. LLMClient 와 같은 TLS·프록시 설정, 같은 Authorization 헤더."""
    m = getattr(cfg, "chat_model", None) if cfg is not None else None   # 고른 모델
    if m is None or not m.api_base or is_unresolved_secret(m.api_key):   # 앞 단계 실패
        return Check("server", "서버 연결", SKIP, "chat 모델·토큰이 준비되지 않아 건너뜀")   # 건너뜀
    import httpx                                             # HTTP
    from ncn.llm.client import LLMClient, build_chat_url, build_headers   # 요청 주소·헤더·클라이언트(같은 규칙)
    url = build_chat_url(m.api_base, "/models")              # {apiBase}/models(localhost → 127.0.0.1 도 같게)
    llm = LLMClient(api_base=m.api_base, api_key=m.api_key, model=m.model or "", request_options=m.request_options)   # TLS·프록시 설정을 그대로
    data: dict = {"url": url}                                # 기계용 값
    try:                                                     # 요청
        headers = build_headers(m.api_key, m.request_options, retry_count=0, timeout_ms=None)   # 같은 인증 헤더
        resp = await llm.http.get(url, headers=headers, timeout=timeout)   # GET /models
    except httpx.TimeoutException as e:                      # 시간 초과
        return Check("server", "서버 연결", FAIL, f"{url}\n시간 초과({timeout:g}초): {_exc_text(e)}", "서버 주소·방화벽·프록시(HTTP_PROXY/NO_PROXY)를 확인하세요.", data)   # 실패
    except httpx.HTTPError as e:                             # 연결 거부·DNS·TLS
        hint = "서버가 켜져 있는지, 주소·포트(:8200)가 맞는지 확인하세요."   # 기본 안내
        if "CERTIFICATE" in str(e).upper() or "SSL" in str(e).upper():   # 인증서 문제
            hint = "사내 인증서: 모델 requestOptions.caBundlePath 에 CA 파일을 지정하세요."   # TLS 안내
        return Check("server", "서버 연결", FAIL, f"{url}\n연결 실패: {_exc_text(e)}", hint, data)   # 실패
    except Exception as e:                                   # 그 밖의 오류
        return Check("server", "서버 연결", FAIL, f"{url}\n오류: {_exc_text(e)}", "", data)   # 실패
    finally:                                                 # 언제나
        await llm.aclose()                                   # 연결 닫기
    data["status"] = resp.status_code                        # 응답 코드
    if resp.status_code in (401, 403):                       # 인증 거부
        return Check("server", "서버 연결", FAIL, f"{url}\nHTTP {resp.status_code} — 토큰 거부",   # 실패
                     f"토큰이 틀렸거나 만료되었습니다. 관리자에게 새 토큰(manage issue-token)을 받아 {TOKEN_ENV} 를 바꾸세요.", data)   # 할 일
    if resp.status_code != 200:                              # 그 밖의 오류 응답
        return Check("server", "서버 연결", FAIL, f"{url}\nHTTP {resp.status_code}: {resp.text[:200]}", "apiBase 가 /v1 로 끝나는지 확인하세요.", data)   # 실패
    try:                                                     # 모델 목록 해석
        ids = [str(x.get("id")) for x in (resp.json().get("data") or []) if isinstance(x, dict)]   # 모델 id 들
    except Exception:                                        # JSON 이 아니면
        ids = []                                             # 빈 목록
    data["models"] = ids[:20]                                # 기계용(최대 20개)
    shown = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")   # 표에는 5개까지
    return Check("server", "서버 연결", OK, f"{url}\nHTTP 200 · 모델 {len(ids)}개: {shown}", data=data)   # 정상


async def check_mcp(cfg: Any, environ: Mapping[str, str], timeout: float = DEFAULT_MCP_TIMEOUT_S) -> list[Check]:   # MCP 접속
    """설정의 mcpServers 마다 실제 접속(ncn/mcp/service.py 그대로) → 도구 개수·오류. 서버마다 한 줄."""
    if cfg is None:                                          # 설정을 못 읽었으면
        return [Check("mcp", "MCP", SKIP, "설정을 읽지 못해 건너뜀")]   # 건너뜀
    servers = [s for s in (cfg.mcp_servers or []) if s]      # 실패 블록(None) 제외
    if not servers:                                          # 없으면
        return [Check("mcp", "MCP", SKIP, "mcpServers 없음(사내 도구를 쓰지 않는 설정)")]   # 건너뜀
    from ncn.mcp.service import MCPService                   # MCP 연결 관리(헤드리스와 같은 코드)
    svc = MCPService(environ)                                # 환경변수(stdio 자식에게)
    out: list[Check] = []                                    # 결과
    try:                                                     # 접속
        try:                                                 # 전체 시간 제한
            await asyncio.wait_for(svc.initialize(servers, headless=False, wait=True), timeout)   # 병렬 접속(실패는 상태로 남음 — headless=False)
        except asyncio.TimeoutError:                         # 너무 오래 걸림
            out.append(Check("mcp", "MCP", FAIL, f"접속 시간 초과({timeout:g}초)", "MCP 서버 주소·방화벽을 확인하세요."))   # 실패
        for conn in svc.connections.values():                # 서버마다
            name = conn.name or "(이름 없음)"                   # 서버 이름
            where = conn.config.get("url") or conn.config.get("command") or ""   # 주소 또는 명령
            data = {"name": name, "target": where, "status": conn.status, "tools": len(conn.tools)}   # 기계용 값
            if conn.status == "connected":                   # 연결됨
                warn = "\n".join(conn.warnings)              # 경고(목록 실패·비밀 등)
                st = WARN if warn else OK                    # 경고가 있으면 경고
                out.append(Check(f"mcp:{name}", f"MCP {name}", st, f"{where}\n도구 {len(conn.tools)}개" + (f"\n{warn}" if warn else ""), data=data))   # 결과
            else:                                            # 실패(error) 또는 접속 중 멈춤
                err = conn.error or f"상태 {conn.status}"      # 오류 글
                hint = "토큰(Authorization)이 맞는지 확인하세요." if "401" in err or "403" in err else "서버 주소(:8200/mcp)·서버 상태를 확인하세요."   # 할 일
                out.append(Check(f"mcp:{name}", f"MCP {name}", FAIL, f"{where}\n{err}", hint, data))   # 실패
    except Exception as e:                                   # 접속 코드 자체 오류(설정 형식 등)
        out.append(Check("mcp", "MCP", FAIL, _exc_text(e), "mcpServers 설정 형식을 확인하세요."))   # 실패
    finally:                                                 # 언제나
        try:                                                 # 정리 실패는 무시
            await svc.shutdown()                             # 연결 닫기(stdio 자식 종료)
        except Exception:                                    # 정리 오류
            pass                                             # 무시(점검 결과가 중요)
    return out                                               # 서버별 결과


def _windows_console_cp() -> Optional[int]:                  # Windows 콘솔 출력 코드 페이지
    """GetConsoleOutputCP() — 콘솔이 없거나 Windows 가 아니면 None."""
    if sys.platform != "win32":                              # Windows 만
        return None                                          # 해당 없음
    try:                                                     # ctypes 호출
        import ctypes                                        # Win32 API
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()     # type: ignore[attr-defined] — 0 이면 콘솔 없음
        return int(cp) or None                               # 0 → None
    except Exception:                                        # 실패
        return None                                          # 모름


def check_console(stream: Any = None) -> Check:              # 콘솔 한글 출력
    """stdout 인코딩으로 한글을 쓸 수 있는지 + Windows 콘솔 코드 페이지. 표 맨 아래 시험 글이 깨지면 글꼴 문제."""
    s = stream if stream is not None else sys.stdout         # 검사할 출력
    enc = getattr(s, "encoding", None) or "?"                # 출력 인코딩
    tty = bool(getattr(s, "isatty", lambda: False)())        # 화면(콘솔)인지 파일·파이프인지
    cp = _windows_console_cp()                               # Windows 콘솔 코드 페이지
    data = {"encoding": enc, "isatty": tty, "console_cp": cp}   # 기계용 값
    detail = f"stdout 인코딩 {enc} · {'콘솔' if tty else '파일/파이프'}" + (f" · 코드 페이지 {cp}" if cp else "")   # 요약
    try:                                                     # 한글을 이 인코딩으로 쓸 수 있나
        KOREAN_SAMPLE.encode(enc if enc != "?" else "utf-8")   # 인코딩 시험(실제 출력은 표 맨 아래)
    except (LookupError, UnicodeEncodeError) as e:           # 못 씀
        return Check("console", "콘솔 한글", FAIL, detail + f"\n{_exc_text(e)}", "chcp 65001 후 다시 실행하거나 PYTHONIOENCODING=utf-8 을 지정하세요.", data)   # 실패
    hint = ""                                                # 기본 안내 없음
    st = OK                                                  # 기본 정상
    if sys.platform == "win32" and tty and cp not in (65001, 949, None):   # 한국어도 UTF-8 도 아닌 코드 페이지
        st, hint = WARN, "한글이 깨지면 콘솔 글꼴을 '돋움체'·'Consolas' 등 한글 지원 글꼴로 바꾸세요."   # 경고
    return Check("console", "콘솔 한글", st, detail + f"\n시험 글: {KOREAN_SAMPLE}", hint, data)   # 결과


def check_rg() -> Check:                                     # ripgrep 유무
    """rg 가 PATH 에 있으면 버전, 없으면 경고(Grep 도구가 내장 검색으로 대신함 — 계획 부록 B)."""
    p = shutil.which("rg")                                   # rg 위치
    if not p:                                                # 없음
        return Check("rg", "rg(검색)", WARN, "PATH 에 rg 없음 — 내장 검색 사용(느릴 수 있음)", "빠른 검색이 필요하면 rg.exe 를 PATH 폴더(예: %LOCALAPPDATA%\\ncn)에 두세요.", {"path": None})   # 경고
    try:                                                     # 버전 확인
        r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=10,   # rg --version
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # Windows: 콘솔 창 번쩍임 방지(V6)
        first = (r.stdout or "").splitlines()[0] if r.stdout else "?"   # 첫 줄(버전)
    except Exception as e:                                   # 실행 실패(차단·손상)
        return Check("rg", "rg(검색)", WARN, f"{p}\n실행 실패: {_exc_text(e)}", "rg.exe 가 백신에 차단되었는지 확인하세요.", {"path": p})   # 경고
    return Check("rg", "rg(검색)", OK, f"{first} ({p})", data={"path": p})   # 정상


def check_path() -> Check:                                   # ncn 이 PATH 에 있는지
    """새 명령 창에서 `ncn` 이 이 실행 파일로 잡히는지(설치 직후엔 창을 새로 열어야 함)."""
    p = shutil.which("ncn")                                  # PATH 에서 찾은 ncn
    me = sys.executable if getattr(sys, "frozen", False) else None   # 지금 실행 중인 ncn.exe(소스 실행이면 비교 안 함)
    data = {"which": p, "self": me}                          # 기계용 값
    if not p:                                                # PATH 에 없음
        return Check("path", "PATH", WARN, "PATH 에서 ncn 을 찾지 못함", "install.cmd 실행 후 명령 창(또는 VS)을 새로 여세요.", data)   # 경고
    if me and os.path.normcase(os.path.realpath(p)) != os.path.normcase(os.path.realpath(me)):   # 다른 ncn 이 먼저 잡힘
        return Check("path", "PATH", WARN, f"PATH 의 ncn: {p}\n실행 중: {me}", "예전 설치본이 PATH 앞쪽에 있습니다. 하나만 남기세요.", data)   # 경고
    return Check("path", "PATH", OK, p, data=data)           # 정상


# ─────────────────────────────────────────────────────────────────────────────
# 전체 실행·출력
# ─────────────────────────────────────────────────────────────────────────────


async def run_checks(config_path: Optional[str] = None, *, cwd: Optional[pathlib.Path] = None,   # 모든 점검 실행
                     environ: Optional[Mapping[str, str]] = None, timeout: float = DEFAULT_TIMEOUT_S,   # (인자 이어짐)
                     mcp_timeout: float = DEFAULT_MCP_TIMEOUT_S, stream: Any = None) -> list[Check]:   # (인자 이어짐)
    """정해진 순서로 점검. 한 항목이 실패해도 나머지는 계속(앞 단계가 필요한 것은 SKIP)."""
    cwd = cwd or pathlib.Path(os.getcwd())                   # 작업 폴더
    env = dict(os.environ if environ is None else environ)   # 환경변수 사본(점검이 바꾸지 않게)
    out: list[Check] = [check_version()]                     # 1) 버전
    try:                                                     # 2) 내장 자료(예외 보호)
        out.append(check_data())                             # 실행
    except Exception as e:                                   # 예상 밖 오류(import 실패 등)
        out.append(Check("data", "내장 자료", FAIL, _exc_text(e)))   # 실패로 기록
    cfg_check, cfg = check_config(config_path, env, cwd)      # 3) 설정 파일
    out.append(cfg_check)                                    # 기록
    out.append(check_token_file(env, cwd))                   # 4) 토큰 위치
    out.append(check_chat_model(cfg))                        # 5) chat 모델
    out.append(await check_server(cfg, timeout))             # 6) 서버 /v1/models
    out.extend(await check_mcp(cfg, env, mcp_timeout))       # 7) MCP
    out.append(check_console(stream))                        # 8) 콘솔 한글
    out.append(check_rg())                                   # 9) rg
    out.append(check_path())                                 # 10) PATH
    return out                                               # 전체 결과


def overall_ok(checks: Sequence[Check]) -> bool:             # 전체 판정
    """실패가 하나도 없으면 참(경고·건너뜀은 통과)."""
    return not any(c.status == FAIL for c in checks)         # FAIL 유무


def _width(s: str) -> int:                                   # 화면 폭(한글·전각 2칸)
    """동아시아 전각(W·F) 글자는 2칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)   # 글자마다 폭


def _pad(s: str, w: int) -> str:                             # 폭 맞춰 오른쪽 공백
    """화면 폭 w 가 되도록 공백을 붙인다."""
    return s + " " * max(0, w - _width(s))                   # 모자란 만큼 공백


def format_table(checks: Sequence[Check]) -> str:            # 사람이 읽는 표
    """| 상태 | 항목 | 내용 | 표 + 실패·경고의 할 일 목록 + 종합 한 줄."""
    rows = [(LABEL.get(c.status, c.status), c.title, c.detail.splitlines() or [""]) for c in checks]   # (상태, 항목, 내용 줄들)
    w1 = max(_width("상태"), *(_width(r[0]) for r in rows))   # 상태 칸 폭
    w2 = max(_width("항목"), *(_width(r[1]) for r in rows))   # 항목 칸 폭
    lines = [f"ncn --doctor (ncn {__version__})", ""]        # 제목
    lines.append(f"{_pad('상태', w1)}  {_pad('항목', w2)}  내용")   # 머리줄
    lines.append(f"{'-' * w1}  {'-' * w2}  {'-' * 40}")      # 구분선
    for st, title, detail in rows:                           # 항목마다
        lines.append(f"{_pad(st, w1)}  {_pad(title, w2)}  {detail[0]}")   # 첫 줄
        for more in detail[1:]:                              # 나머지 내용 줄
            lines.append(f"{' ' * w1}  {' ' * w2}  {more}")   # 들여쓰기
    todo = [c for c in checks if c.status in (FAIL, WARN) and c.hint]   # 할 일이 있는 항목
    if todo:                                                 # 있으면
        lines += ["", "할 일:"]                               # 제목
        lines += [f"- [{LABEL[c.status]}] {c.title}: {c.hint}" for c in todo]   # 항목별 안내
    nf = sum(c.status == FAIL for c in checks)               # 실패 수
    nw = sum(c.status == WARN for c in checks)               # 경고 수
    lines += ["", f"종합: {'정상' if nf == 0 else '문제 있음'} (실패 {nf} · 경고 {nw}) — 결과 전체를 복사해 문의하세요: ncn --doctor --format json"]   # 종합
    return "\n".join(lines) + "\n"                           # 표 글


def to_json(checks: Sequence[Check]) -> str:                 # --format json
    """{"ncn": 버전, "ok": 참/거짓, "checks": [...]} — 한글은 그대로(ensure_ascii=False), 들여쓰기 2."""
    return json.dumps({"ncn": __version__, "ok": overall_ok(checks), "checks": [asdict(c) for c in checks]},   # 전체 결과
                      ensure_ascii=False, indent=2) + "\n"   # 사람이 봐도 읽히게


def build_parser() -> argparse.ArgumentParser:               # --doctor 옵션 해석기
    """ncn --doctor [--config 경로] [--format json] [--timeout 초]. 나머지 옵션은 무시(app.py 의 공통 옵션과 섞여 와도 됨)."""
    p = argparse.ArgumentParser(prog="ncn --doctor", add_help=False)   # 도움말은 app.py 가 담당
    p.add_argument("--doctor", action="store_true")          # 이 모드 표시(있어도 없어도 됨)
    p.add_argument("--config")                               # 설정 파일(헤드리스 --config 와 같은 뜻)
    p.add_argument("--format")                               # json 이면 JSON 출력
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)   # 서버 시간 제한(초)
    p.add_argument("--verbose", action="store_true")         # 내부 로그를 stderr 로(기본은 끔 — 표만 보이게)
    return p                                                 # 해석기


def main(argv: Optional[Sequence[str]] = None) -> int:       # `ncn --doctor` 진입 함수
    """점검을 돌려 표 또는 JSON 을 stdout 에 쓴다. 종료 코드: 실패 있으면 1, 없으면 0, 옵션 오류 2."""
    argv = list(sys.argv[1:] if argv is None else argv)      # 명령줄
    a, _rest = build_parser().parse_known_args(argv)         # 모르는 옵션은 무시
    if a.format not in (None, "json"):                       # json 만 지원
        sys.stderr.write(f"ncn --doctor: --format 은 json 만 지원합니다(받은 값: {a.format})\n")   # 안내
        return 2                                             # 옵션 오류
    for s in (sys.stdout, sys.stderr):                       # 출력 인코딩(app.py _stdout_utf8 과 같게 — 파일로 저장해도 UTF-8)
        try:                                                 # 재설정 실패는 무시
            s.reconfigure(encoding="utf-8", newline="\n")    # type: ignore[attr-defined] — UTF-8
        except Exception:                                    # 재설정 불가 스트림
            pass                                             # 무시
    import logging                                           # 내부 로그(MCP 접속 실패 등 — 결과는 이미 표에 있음)
    root = logging.getLogger()                               # 루트 로거
    if a.verbose:                                            # --verbose 면
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")   # stderr 에 DEBUG
    else:                                                    # 아니면
        root.addHandler(logging.NullHandler())               # 마지막 수단 처리기(stderr 경고)도 막음
        root.setLevel(logging.CRITICAL + 1)                  # 모든 로그 차단(app.py _configure_logging 과 같음)
    cwd = pathlib.Path(os.getcwd())                          # 작업 폴더
    from ncn.config.env import apply_dotenv                  # <cwd>/.env 반영(헤드리스와 같음 — 이미 있는 키는 그대로)
    apply_dotenv(os.environ, os.fspath(cwd))                 # 반영
    checks = asyncio.run(run_checks(a.config, cwd=cwd, timeout=a.timeout))   # 점검 실행
    sys.stdout.write(to_json(checks) if a.format == "json" else format_table(checks))   # 출력
    sys.stdout.flush()                                       # 바로 내보내기
    return 0 if overall_ok(checks) else 1                    # 종료 코드


if __name__ == "__main__":                                   # python -m ncn.doctor 로 직접 실행(app.py 연결 전 시험용)
    sys.exit(main())                                         # 종료 코드
