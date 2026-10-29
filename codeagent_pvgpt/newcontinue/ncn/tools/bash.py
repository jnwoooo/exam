# ncn/tools/bash.py — Bash 도구(터미널 명령 실행). cn extensions/cli/src/tools/runTerminalCommand.ts 를 그대로 옮긴 것
# 셸: Windows = powershell.exe -NoLogo -ExecutionPolicy Bypass -Command, WSL/그 밖 = $SHELL(없으면 /bin/bash) -l -c
# 시간 제한은 "출력이 없는 시간" 기준(출력이 올 때마다 다시 셈), 기본 180초·최대 600초. 출력은 뒷부분을 남기고 5만 자/1천 줄
# [ncn 부록B B4] 출력 해석: cn은 조각마다 따로 UTF-8 해석(경계의 여러 바이트 글자가 깨짐)·Windows CP949 출력도 UTF-8로 봄.
#   ncn은 증분 해석 + Windows 에서 PowerShell 출력 인코딩을 UTF-8 로 강제(앞부분 추가) + UTF-8 이 아닌 바이트가 오면 OEM 코드 페이지로 해석.
#   NCN_CN_COMPAT=1 이면 cn 동작
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 실행·타이머
import codecs                                                # [ncn 부록B B4] 증분 해석기
import locale                                                # [ncn 부록B B4] 대체 인코딩(시스템 코드 페이지)
import math                                                  # NaN 판정
import os                                                    # 환경변수
from typing import Any, Callable, Optional                   # 타입

from ncn.compat import cn_compat                             # [ncn 부록B B4] cn 원래 동작 스위치
from ncn.security.secrets import bash_child_env            # [ncn E7] 비밀 환경변수를 뺀 자식 환경(뺄 것이 없거나 꺼져 있으면 None)
from ncn.tools.background_jobs import (                      # 백그라운드 작업(Ctrl+B)
    ChildHandle, background_job_service, background_signal_manager, spawn_child, spawn_error_message,  # 손잡이·작업 서비스·Ctrl+B 신호·프로세스 띄우기·오류 메시지
)                                                            # import 목록 끝
from ncn.tools.base import PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.formatters import node_platform, to_locale_string   # Node process.platform 값, toLocaleString
from ncn.tools.truncate import js_len, parse_env_number, truncate_output_from_start   # 자르기(cn truncateOutput.ts)

DEFAULT_BASH_MAX_CHARS = 50000                               # ~12.5k 토큰
DEFAULT_BASH_MAX_LINES = 1000                                # 줄 한도
_TIMEOUT_MAX_MS = 2 ** 31 - 1                                # Node setTimeout 최대(넘거나 1 미만·NaN이면 1ms)

# ── 루프·화면이 끼워 넣는 연결점(cn의 전역 서비스 대응) ──
TOOL_OUTPUT_LISTENER: Optional[Callable[[str, str, str], None]] = None   # services.chatHistory.addToolResult(id, 출력, "calling") 대응 — 실행 중 출력 보여주기
BASH_EVENTS_LISTENER: Optional[Callable[[str], None]] = None   # emitBashToolStarted/Ended("started"/"ended") 대응(대화형 화면용)
ENV_PROVIDER: Optional[Callable[[], Optional[dict]]] = bash_child_env   # [ncn E7] 자식 프로세스 환경변수(비밀 제거). None 으로 바꾸면 cn처럼 그대로 물려받음


class BashCommandError(Exception):                           # Bash 실패를 알리는 예외(루프가 'Error executing tool Bash'로 감쌈)
    """cn은 reject(문자열)로 실패를 알린다 — 루프가 'Error executing tool Bash: {메시지}'로 감싼다."""


def is_running_in_wsl() -> bool:                             # WSL 여부 판정 함수
    """isRunningInWsl — linux 이고 WSL_DISTRO_NAME 이 있거나 /proc/version 에 microsoft·wsl 이 있으면 참."""
    if node_platform() != "linux":                           # 리눅스가 아니면 WSL 아님
        return False                                         # 리눅스가 아니면 거짓
    if os.environ.get("WSL_DISTRO_NAME"):                    # 환경변수(빈 글은 거짓 — JS와 같음)
        return True                                          # WSL 환경변수가 있으면 참
    try:                                                     # /proc/version 읽기 실패 대비
        with open("/proc/version", encoding="utf-8") as f:   # 커널 버전 글
            proc_version = f.read().lower()                  # 소문자로
        return "microsoft" in proc_version or "wsl" in proc_version   # WSL 흔적
    except Exception:                                        # 못 읽으면 아님
        return False                                         # 못 읽으면 WSL 아님


def _get_bash_max_chars() -> int:                            # Bash 출력 글자 한도 읽기(환경변수 우선)
    """CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS(없으면 50000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS"), DEFAULT_BASH_MAX_CHARS)  # 환경변수 값이 없으면 기본 50000


def _get_bash_max_lines() -> int:                            # Bash 출력 줄 한도 읽기(환경변수 우선)
    """CONTINUE_CLI_BASH_MAX_OUTPUT_LINES(없으면 1000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_BASH_MAX_OUTPUT_LINES"), DEFAULT_BASH_MAX_LINES)  # 환경변수 값이 없으면 기본 1000


# [ncn 부록B B4] PowerShell 명령 앞에 붙이는 UTF-8 출력 강제(BOM 없는 UTF-8). 실패해도(콘솔 없음 등) 무시하고 사용자 명령은 그대로 실행
PS_UTF8_PREFIX = ("try{[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false}catch{};"   # 콘솔 출력 코드 페이지를 65001로(네이티브 exe 출력도 UTF-8)
                  "$OutputEncoding=New-Object System.Text.UTF8Encoding $false;")   # 파이프로 네이티브 exe 에 넘길 때도 UTF-8


def get_shell_command(command: str) -> tuple[str, list[str]]:  # 플랫폼별 (셸, 인자) 결정 함수
    """getShellCommand — (셸, 인자). Windows는 PowerShell, WSL·Unix·macOS는 로그인 셸(-l -c).
    [ncn 부록B B4] Windows 에서는 명령 앞에 PS_UTF8_PREFIX 를 붙인다(LLM 이 본 명령 글은 그대로 — 실행하는 글만). NCN_CN_COMPAT=1 이면 cn 그대로."""
    if node_platform() == "win32":                           # Windows: PowerShell(5.1 기본 — 계획 14-5)
        # 고른 이유: 콘솔 코드 페이지(cp949)로만 해석하면 cp949 에 없는 글자(이모지·일부 한자)는 PowerShell 이 '?' 로 바꿔 되돌릴 수 없다.
        # UTF-8 로 강제하면 PowerShell cmdlet 출력·콘솔 코드 페이지를 따르는 exe 출력이 손실 없이 온다. 그래도 cp949 로 직접 쓰는 exe 가 있어
        # 해석기는 UTF-8 이 깨지면 OEM 코드 페이지로 넘어간다(_HybridDecoder) — 두 방법을 겹쳐 가장 견고하게
        script = command if cn_compat() else PS_UTF8_PREFIX + command   # [ncn 부록B B4] cn 원래 동작: 앞부분 없음(출력이 cp949)
        return "powershell.exe", ["-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", script]  # PowerShell로 명령 실행
    if is_running_in_wsl():                                  # WSL: bash 가 늘 있음
        wsl_shell = os.environ.get("SHELL") or "/bin/bash"  # WSL에서도 SHELL 환경변수 우선, 없으면 bash
        return wsl_shell, ["-l", "-c", command]              # WSL은 로그인 셸로 실행
    user_shell = os.environ.get("SHELL") or "/bin/bash"      # Unix/macOS: .bashrc/.zshrc 등을 읽게 로그인 셸
    return user_shell, ["-l", "-c", command]                 # Unix/macOS도 로그인 셸(-l -c)로 실행


def _creationflags() -> int:                                 # Windows 콘솔 창 숨김 플래그 함수
    """[추가 V6] Windows 에서 콘솔 창이 번쩍이지 않게 CREATE_NO_WINDOW(출력·동작은 같음). 그 밖은 0."""
    if node_platform() == "win32":                           # Windows 에서만
        import subprocess                                    # 상수
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)    # Windows 전용 상수(0x08000000)
    return 0                                                 # 그 밖의 플랫폼은 플래그 없음


def _fallback_encoding() -> str:                             # [ncn 부록B B4] UTF-8 이 아닐 때 쓸 인코딩
    """Windows: OEM 코드 페이지(한국어 Windows = cp949 — 콘솔 프로그램 기본), 못 구하면 시스템 ANSI 코드 페이지. 그 밖: utf-8."""
    if node_platform() != "win32":                           # Windows 가 아니면 대체 없음
        return "utf-8"                                       # UTF-8 그대로(깨진 바이트는 U+FFFD — Node 와 같음)
    try:                                                     # ctypes 로 OEM 코드 페이지 조회(Windows 전용)
        import ctypes                                        # Windows API 호출
        cp = ctypes.windll.kernel32.GetOEMCP()               # 예: 949
        name = f"cp{cp}"                                     # 파이썬 코덱 이름
        codecs.lookup(name)                                  # 파이썬이 아는 코덱인지 확인
        return name                                          # OEM 코드 페이지 반환
    except Exception:                                        # macOS 시험·조회 실패
        pass                                                 # 아래 대체로
    enc = locale.getpreferredencoding(False) or "cp949"      # 시스템 ANSI 코드 페이지(없으면 cp949)
    try:                                                     # 코덱 확인
        codecs.lookup(enc)                                   # 아는 코덱인지
        return enc                                           # 시스템 인코딩 반환
    except LookupError:                                      # 모르는 이름
        return "cp949"                                       # 현장 기본값(한국어 Windows)


class _HybridDecoder:                                        # [ncn 부록B B4] 증분 UTF-8 해석 + 깨지면 대체 인코딩으로 전환
    """조각 경계에서 글자가 잘려도 이어 붙여 해석(증분). UTF-8 로 해석할 수 없는 바이트가 처음 나오면 그 뒤로는 대체 인코딩(증분)으로."""

    def __init__(self, fallback: str):                       # 대체 인코딩 이름으로 만든다
        self._fallback_name = fallback                       # 대체 인코딩(utf-8 이면 전환해도 U+FFFD 로 바꿈)
        self._utf8 = codecs.getincrementaldecoder("utf-8")("strict")   # 엄격 UTF-8(깨짐을 알아채려고)
        self._fb = codecs.getincrementaldecoder(fallback)("replace")   # 대체 해석기(못 읽는 바이트는 U+FFFD)
        self._use_fallback = False                           # 대체로 넘어갔는지

    def decode(self, data: bytes, final: bool = False) -> str:   # 조각 하나를 글로
        """조각 → 글. 아직 덜 온 여러 바이트 글자는 다음 조각까지 들고 있는다."""
        if self._use_fallback:                               # 이미 대체 모드면
            return self._fb.decode(data, final)              # 대체 인코딩으로 해석
        pending = self._utf8.getstate()[0]                   # 앞 조각에서 남은(덜 온) 바이트
        try:                                                 # UTF-8 로 해석 시도
            return self._utf8.decode(data, final)            # 성공하면 그대로
        except UnicodeDecodeError:                           # UTF-8 이 아님(예: cp949 로 직접 쓰는 exe)
            self._use_fallback = True                        # 이후 전부 대체 인코딩으로
            return self._fb.decode(pending + data, final)    # 남은 바이트부터 다시 해석


class _BashChildHandle(ChildHandle):                         # [ncn 부록B B4] 출력 해석만 바꾼 손잡이
    """ChildHandle 과 같고 _read 의 해석기만 다르다 — 고친 동작: _HybridDecoder, cn 동작: 조각마다 따로 UTF-8(data.toString())."""

    async def _read(self, stream, which: str) -> None:       # 스트림 하나를 끝까지 읽는 코루틴
        """스트림 하나를 끝까지 읽어 받는 곳에 넘김(ChildHandle._read 와 같은 흐름)."""
        if stream is None:                                   # 파이프 없음
            return                                           # 읽을 것이 없다
        compat = cn_compat()                                 # cn 원래 동작인지(시작할 때 한 번)
        dec = None if compat else _HybridDecoder(_fallback_encoding())   # 고친 동작의 해석기
        while True:                                          # 스트림 끝까지 반복
            chunk = await stream.read(65536)                 # 조각 읽기
            if not chunk:                                    # 끝
                rest = dec.decode(b"", final=True) if dec is not None else ""   # 남은 바이트 마무리
                if rest:                                     # 남은 글이 있을 때만
                    self._emit(which, rest)                  # 마지막 글 전달
                return                                       # 읽기 종료
            text = chunk.decode("utf-8", errors="replace") if dec is None else dec.decode(chunk)   # cn: 조각마다 따로(경계 글자 깨짐) / ncn: 증분
            if text:                                         # 빈 글은 넘기지 않음
                self._emit(which, text)                      # 받는 곳에 전달


def _child_env() -> Optional[dict]:                          # 자식 프로세스 환경변수 결정 함수
    """자식 환경변수 — 연결점이 없으면 None(=물려받음, cn spawn 기본)."""
    return ENV_PROVIDER() if ENV_PROVIDER else None          # E7 자리


async def run_command_in_background(command: str, *, cwd: Optional[str] = None) -> dict:  # 명령을 백그라운드 작업으로 띄우는 코루틴
    """runCommandInBackground — {success, jobId?, error?}. 한도(5개)면 실패."""
    job = background_job_service.create_job(command)         # 작업 만들기
    if not job:                                              # 한도
        return {"success": False, "error": "Cannot create background job: limit of 5 concurrent jobs reached"}  # 한도(5개) 초과 시 실패 결과
    shell, args = get_shell_command(command)                 # 셸
    child = await background_job_service.start_job(job.id, shell, args, cwd=cwd, env=_child_env(),  # 작업 시작(셸·인자·환경 전달)
                                                   creationflags=_creationflags())   # 띄우기
    if not child:                                            # 시작 못 함
        return {"success": False, "error": f"Failed to start background job {job.id}"}  # 시작 실패 결과
    return {"success": True, "jobId": job.id}                # 성공


def _js_number(v: Any) -> float:                             # JS ToNumber 대응 함수
    """JS 숫자 변환(Math.min 이 인자에 하는 ToNumber). null→0, true→1, "" →0, 숫자 아닌 글→NaN."""
    if v is None:                                            # null이면 0
        return 0.0                                           # null은 0
    if isinstance(v, bool):                                  # bool은 int의 하위형이라 먼저 처리
        return 1.0 if v else 0.0                             # true는 1, false는 0
    if isinstance(v, (int, float)):                          # 숫자
        return float(v)                                      # 숫자는 그대로 실수로
    if isinstance(v, str):                                   # 글
        s = v.strip()                                        # 앞뒤 공백 무시
        if s == "":                                          # 빈 글 = 0
            return 0.0                                       # 빈 문자열은 0
        try:                                                 # 숫자 변환 실패(ValueError) 대비
            if s.lower().startswith(("0x", "0o", "0b")):      # 16·8·2진 표기
                return float(int(s, 0))                      # 진수 접두어 정수 변환
            if s in ("Infinity", "+Infinity"):               # JS 무한대 표기
                return math.inf                              # JS 무한대
            if s == "-Infinity":                             # JS 음의 무한대 표기
                return -math.inf                             # JS 음의 무한대
            if s.lower() in ("inf", "+inf", "-inf", "nan", "infinity", "+infinity", "-infinity"):   # Python만 받는 표기는 NaN
                return math.nan                              # 파이썬 전용 표기는 JS에서 NaN
            return float(s)                                  # 보통 숫자
        except ValueError:                                   # 숫자 아님
            return math.nan                                  # 숫자가 아니면 NaN
    return math.nan                                          # 배열·객체 등


def _js_num_str(x: float) -> str:                            # JS String(number) 대응 함수
    """JS 숫자 → 글(String(n)). 정수는 소수점 없이, NaN/Infinity 표기."""
    if math.isnan(x):                                        # NaN 판정
        return "NaN"                                         # NaN 표기
    if math.isinf(x):                                        # 무한대
        return "Infinity" if x > 0 else "-Infinity"          # 무한대 표기
    if x == int(x) and abs(x) < 1e21:                        # 정수 값
        return str(int(x))                                   # 정수는 소수점 없이
    return repr(x)                                           # 소수(짧은 표기 — JS와 대부분 같음)


def _js_substring(s: str, end: int) -> str:                  # UTF-16 기준 substring 대응 함수
    """s.substring(0, end) — UTF-16 위치 기준."""
    if js_len(s) == len(s):                                  # BMP만 있으면 그냥
        return s[:end]                                       # BMP만 있으면 파이썬 슬라이스로 충분
    u = s.encode("utf-16-le", errors="surrogatepass")        # UTF-16 으로
    return u[: end * 2].decode("utf-16-le", errors="surrogatepass")   # 앞 end 개 단위


def _emit_bash_event(name: str) -> None:                     # Bash 시작/끝 이벤트 알림 함수
    """emitBashToolStarted/Ended."""
    if BASH_EVENTS_LISTENER:                                 # 연결돼 있으면
        try:                                                 # 화면 오류가 도구를 깨지 않게 방어
            BASH_EVENTS_LISTENER(name)                       # 알림
        except Exception:                                    # 화면 오류는 무시
            pass                                             # 화면 쪽 오류는 무시


def _evaluate_tool_call_policy(base_policy: str, parsed_args: dict) -> str:  # 명령 위험도로 정책을 올리는 함수
    """evaluateToolCallPolicy — 명령의 위험도로 정책을 올림(@continuedev/terminal-security). 지연 import(시험에서 바꿔 끼울 수 있게)."""
    from ncn.security import terminal_security               # 다른 작업자 모듈(모듈째 가져와 monkeypatch 가 먹게)
    return terminal_security.evaluate_terminal_command_security(base_policy, parsed_args.get("command"))  # 터미널 보안 평가 결과를 정책으로 반환


async def _preprocess(args: dict) -> PreprocessResult:       # 인자 검사·미리보기 만드는 코루틴
    """preprocess — 명령이 비었거나 글이 아니면 오류, 미리보기 'Will run: …'(60자 넘으면 자르고 ...)."""
    command = args.get("command")                            # 명령
    if not command or not isinstance(command, str):          # command 필수 검사(비었거나 글이 아님)
        raise Exception("command arg is required and must be a non-empty string")  # 명령이 비었거나 글이 아니면 오류
    truncated_cmd = _js_substring(command, 60) + "..." if js_len(command) > 60 else command   # 60자
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Will run: {truncated_cmd}")])  # 'Will run: ...' 미리보기 결과 반환


def make_bash_tool(cwd: str | os.PathLike | None = None) -> Tool:  # Bash 도구를 만드는 팩토리 함수
    """Bash 도구를 만든다. 설명의 작업 폴더는 cn의 process.cwd() 자리 — ncn은 넘겨받은 cwd(없으면 os.getcwd())."""
    work_dir = str(cwd) if cwd is not None else os.getcwd()  # 작업 폴더

    async def run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # 도구 실행 본체 코루틴
        """run — 명령을 돌려 출력 글을 돌려줌. 0 아닌 종료 + stderr 있음 → 오류."""
        command = args.get("command")                        # 명령
        has_timeout = "timeout" in args                      # timeout !== undefined(null 도 '있음' — JS 그대로)
        timeout = args.get("timeout")                        # 초
        parallel_count = (context.parallel_tool_call_count if context else 1) or 1   # 동시 실행 수
        base_max_chars = _get_bash_max_chars()               # 한도(글자)
        base_max_lines = _get_bash_max_lines()               # 한도(줄)
        max_chars = base_max_chars // parallel_count         # Math.floor(나눗셈)
        max_lines = base_max_lines // parallel_count         # Math.floor(나눗셈)

        _emit_bash_event("started")                          # 시작 이벤트 알림

        loop = asyncio.get_running_loop()                    # 이벤트 루프
        fut: asyncio.Future = loop.create_future()           # Promise 대응
        shell, sh_args = get_shell_command(command)          # 셸
        state = {"stdout": "", "stderr": "", "resolved": False, "timer": None}   # 공유 상태

        # 시간 제한 결정: timeout(최대 600초) → 시험용 환경변수 → 기본 180초
        timeout_ms: float = 180000.0                         # 기본 180초
        if has_timeout:                                      # 인자가 있으면
            t = _js_number(timeout)                          # 인자를 JS 숫자로 변환
            capped = math.nan if math.isnan(t) else min(t, 600.0)   # Math.min(timeout, 600) — NaN 이면 NaN
            timeout_ms = capped * 1000                       # 밀리초
        elif os.environ.get("NODE_ENV") == "test" and os.environ.get("TEST_TERMINAL_TIMEOUT"):   # 시험용(cn과 같은 이름)
            try:                                             # 환경변수 정수 해석 실패 대비
                timeout_ms = float(int(os.environ["TEST_TERMINAL_TIMEOUT"].strip().split()[0]))  # parseInt처럼 첫 토큰을 정수로 해석
            except Exception:                                # 해석 실패 시
                timeout_ms = math.nan                        # parseInt 실패 = NaN
        delay_ms = timeout_ms if (not math.isnan(timeout_ms) and 1 <= timeout_ms <= _TIMEOUT_MAX_MS) else 1   # Node setTimeout 보정

        def append_parallel_limit_note(output: str) -> str:  # 동시 실행 한도 안내를 붙이는 보조 함수
            """동시 실행으로 한도가 줄었을 때 안내 한 줄."""
            if parallel_count > 1:                           # 동시 실행이면
                return (output + f"\n\n(Note: output limit reduced due to {parallel_count} parallel tool calls. "  # 한도 축소 안내 문구 덧붙임(1/2)
                        f"Single-tool limit: {to_locale_string(base_max_chars)} characters or {to_locale_string(base_max_lines)} lines.)")  # 단일 도구 한도 안내(2/2)
            return output                                    # 동시 실행이 아니면 그대로 반환

        def finish(output: str) -> str:                      # 출력을 자르는 보조 함수
            """잘라서 잘렸으면 안내를 붙임."""
            r = truncate_output_from_start(output, {"maxChars": max_chars, "maxLines": max_lines})   # 뒷부분 남김
            return append_parallel_limit_note(r.output) if r.was_truncated else r.output  # 잘렸으면 안내를 붙여 반환

        def settle(result: Optional[str] = None, error: Optional[str] = None) -> None:  # Future를 한 번만 확정하는 함수
            """resolve/reject 한 번만."""
            if fut.done():                                   # 이미 끝남
                return                                       # 중복 확정 방지
            if error is not None:                            # 오류가 있으면 reject
                fut.set_exception(BashCommandError(error))  # 오류로 확정(reject)
            else:                                            # 아니면 resolve
                fut.set_result(result)                       # 정상 결과로 확정(resolve)

        def clear_timer() -> None:                           # 타이머 해제 함수
            """clearTimeout."""
            if state["timer"] is not None:                   # 걸려 있으면
                state["timer"].cancel()                      # 취소
                state["timer"] = None                        # 타이머 참조 비움

        try:                                                 # 프로세스 띄우기 실패(OSError) 대비
            spawned = await spawn_child(shell, sh_args, cwd=work_dir, env=_child_env(), creationflags=_creationflags())  # 셸 프로세스 띄우기(작업 폴더·환경·플래그 전달)
            child = _BashChildHandle(spawned.proc)           # [ncn 부록B B4] 같은 프로세스를 출력 해석만 다른 손잡이로(읽기는 아래 start() 에서 시작)
        except OSError as e:                                 # 'error' 이벤트(셸 없음 등)
            raise BashCommandError(f"Error: {spawn_error_message(shell, e)}")  # Node 형식 오류 메시지로 실패 처리

        def show_current_output() -> None:                   # 진행 중 출력을 화면에 알리는 함수
            """실행 중 출력을 대화 기록에 'calling' 으로 반영(연결점이 있을 때)."""
            if not context or not context.tool_call_id:      # id 없으면 안 함
                return                                       # tool_call_id가 없으면 알릴 수 없음
            if TOOL_OUTPUT_LISTENER is None:                 # 연결 안 됨
                return                                       # 리스너가 없으면 알리지 않음
            try:                                             # 리스너 오류 방어
                current = state["stdout"] + (f"\nStderr: {state['stderr']}" if state["stderr"] else "")   # 지금까지
                TOOL_OUTPUT_LISTENER(context.tool_call_id, current, "calling")   # 알림
            except Exception:                                # 무시
                pass                                         # 무시하고 계속

        def on_timeout() -> None:                            # 무출력 시간 초과 처리 함수
            """출력 없는 시간이 한도를 넘음 → 죽이고 지금까지 출력 + 안내."""
            if state["resolved"]:                            # 이미 끝났으면 무시
                return                                       # 중복 처리 방지
            state["resolved"] = True                         # 끝남 표시
            child.kill()                                     # 프로세스 종료(SIGTERM)
            output = state["stdout"] + (f"\nStderr: {state['stderr']}" if state["stderr"] else "")   # 지금까지
            output += f"\n\n[Command timed out after {_js_num_str(timeout_ms / 1000)} seconds of no output]"   # 안내
            settle(finish(output))                           # 자르고 끝냄

        def reset_timeout() -> None:                         # 타이머 재설정 함수
            """출력이 올 때마다 타이머를 다시 건다."""
            clear_timer()                                    # 이전 것 취소
            state["timer"] = loop.call_later(delay_ms / 1000, on_timeout)   # 새 타이머

        def move_to_background() -> None:                    # Ctrl+B 백그라운드 전환 함수
            """Ctrl+B — 프로세스를 백그라운드 작업으로 넘기고 지금까지 출력을 돌려줌."""
            if state["resolved"]:                            # 이미 끝났으면 무시
                return                                       # 중복 처리 방지
            state["resolved"] = True                         # 끝남 표시
            clear_timer()                                    # 타이머 끔
            background_signal_manager.off(move_to_background)   # 신호 떼기
            child.on_stdout = None                           # 이 도구의 듣기 떼기(removeListener)
            child.on_stderr = None                           # stderr 듣기도 해제
            job = background_job_service.create_job_with_process(command, child, state["stdout"])   # 작업으로(stderr 는 안 넘김 — cn 그대로)
            if job:                                          # 넘김 성공
                r = truncate_output_from_start(state["stdout"], {"maxChars": max_chars, "maxLines": max_lines})  # 지금까지 출력을 한도에 맞게 자름
                output_so_far = append_parallel_limit_note(r.output) if r.was_truncated else r.output  # 잘렸으면 안내 추가
                settle(f"Command moved to background. Job ID: {job.id}\nOutput so far:\n{output_so_far}\n"  # 백그라운드 전환 안내(작업 id와 출력)
                       f"Use CheckBackgroundJob(\"{job.id}\") to check status.")  # CheckBackgroundJob 사용 안내
            else:                                            # 한도
                settle(f"Failed to move to background (job limit reached). Command continues in foreground.\n"  # 한도 초과로 전환 실패 → 전경 유지 안내
                       f"Output so far: {state['stdout']}")  # 지금까지 출력 포함

        background_signal_manager.on(move_to_background)     # Ctrl+B 듣기

        reset_timeout()                                      # 첫 타이머

        def on_stdout(data: str) -> None:                    # stdout 조각 받는 함수
            """stdout 조각."""
            state["stdout"] += data                          # 붙이기
            reset_timeout()                                  # 시간 다시
            show_current_output()                            # 보여 주기

        def on_stderr(data: str) -> None:                    # stderr 조각 받는 함수
            """stderr 조각."""
            state["stderr"] += data                          # stderr에 덧붙임
            reset_timeout()                                  # 무출력 타이머 재설정
            show_current_output()                            # 진행 출력 화면 반영

        def on_close(code: Optional[int]) -> None:           # 프로세스 종료 처리 함수
            """'close' — 종료 코드로 결과 결정."""
            if state["resolved"]:                            # 이미 끝났으면 무시
                return                                       # 중복 처리 방지
            state["resolved"] = True                         # 끝남 표시
            clear_timer()                                    # 타이머 끔
            background_signal_manager.remove_listener(move_to_background)   # 신호 떼기
            if code != 0 and state["stderr"]:                # 0 아님 + stderr 있음 → 오류(신호 종료는 code=null)
                settle(error=f"Error (exit code {'null' if code is None else code}): {state['stderr']}")  # 0이 아닌 종료 + stderr → 오류로 확정
                return                                       # 오류 확정 후 종료
            output = state["stdout"]                         # 출력
            if state["stderr"]:                              # stderr 붙이기
                output = state["stdout"] + f"\nStderr: {state['stderr']}"  # stderr를 뒤에 붙임
            settle(finish(output))                           # 자르고 끝냄

        def on_error(msg: str) -> None:                      # 읽기 실패 등 오류 처리 함수
            """'error' — 읽기 실패 등."""
            if state["resolved"]:                            # 이미 끝났으면 무시
                return                                       # 중복 처리 방지
            state["resolved"] = True                         # 끝남 표시
            clear_timer()                                    # 타이머 해제
            background_signal_manager.off(move_to_background)  # 신호 구독 해제
            settle(error=f"Error: {msg}")                    # 오류로 확정

        child.on_stdout = on_stdout                          # 듣기 연결
        child.on_stderr = on_stderr                          # stderr 받는 곳 연결
        child.on_close = on_close                            # 종료 받는 곳 연결
        child.on_error = on_error                            # 오류 받는 곳 연결
        child.start()                                        # 읽기 시작

        try:                                                 # 실행 취소(Esc) 대비
            terminal_output = await fut                      # 결과 기다림(오류면 여기서 던짐 — cn도 emitBashToolEnded 를 건너뜀)
        except asyncio.CancelledError:                       # 도구 실행 취소(Esc 등) — 프로세스 정리
            clear_timer()                                    # 타이머 해제
            background_signal_manager.off(move_to_background)  # 신호 구독 해제
            if not state["resolved"]:                        # 아직 끝나지 않았으면
                child.kill()                                 # 프로세스 종료
            raise                                            # 취소 예외를 그대로 다시 던짐
        _emit_bash_event("ended")                            # 종료 이벤트 알림(오류 때는 건너뜀 — cn과 같음)
        return terminal_output                               # 출력 글 반환

    return Tool(                                             # 도구 객체 반환
        name="Bash",                                         # 도구 이름
        display_name="Bash",                                 # 화면 표시 이름
        description=(                                        # 도구 설명 원문(cn 그대로)
            "Executes a terminal command and returns the output\n"
            "\n"
            f"Commands are automatically executed from the current working directory ({work_dir}), so there's no need to change directories with 'cd' commands.\n"  # 설명 원문에 작업 폴더를 끼워 넣음
            "\n"
            "IMPORTANT: To edit files, use Edit/MultiEdit tools instead of bash commands (sed, awk, etc).\n"
        ),                                                   # 설명 끝
        parameters={                                         # 인자 JSON 스키마
            "type": "object",                                # 객체 타입
            "required": ["command"],                         # 필수 인자: command
            "properties": {                                  # 인자 속성 정의
                "command": {                                 # command 인자
                    "type": "string",                        # 문자열 타입
                    "description": "The command to execute in the terminal.",  # 인자 설명 원문
                },                                           # command 끝
                "timeout": {                                 # timeout 인자
                    "type": "number",                        # 숫자 타입
                    "description": "Optional timeout in seconds (max 600). Use this parameter for commands that take longer than the default 180 second timeout.",  # 인자 설명 원문(최대 600초)
                },                                           # timeout 끝
            },                                               # 속성 끝
        },                                                   # 스키마 끝
        run=run,                                             # 실행 함수 연결
        preprocess=_preprocess,                              # 입력 검사·미리보기 연결
        readonly=False,                                      # 읽기 전용 아님
        is_built_in=True,                                    # 내장 도구 표시
        evaluate_tool_call_policy=_evaluate_tool_call_policy,  # 위험도 정책 평가 함수 연결
    )                                                        # 도구 정의 끝
