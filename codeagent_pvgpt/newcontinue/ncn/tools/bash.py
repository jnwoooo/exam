# ncn/tools/bash.py — Bash 도구(터미널 명령 실행). cn extensions/cli/src/tools/runTerminalCommand.ts 를 그대로 옮긴 것
# 셸: Windows = powershell.exe -NoLogo -ExecutionPolicy Bypass -Command, WSL/그 밖 = $SHELL(없으면 /bin/bash) -l -c
# 시간 제한은 "출력이 없는 시간" 기준(출력이 올 때마다 다시 셈), 기본 180초·최대 600초. 출력은 뒷부분을 남기고 5만 자/1천 줄
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 실행·타이머
import math                                                  # NaN 판정
import os                                                    # 환경변수
from typing import Any, Callable, Optional                   # 타입

from ncn.tools.background_jobs import (                      # 백그라운드 작업(Ctrl+B)
    background_job_service, background_signal_manager, spawn_child, spawn_error_message,
)
from ncn.tools.base import PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.formatters import node_platform, to_locale_string   # Node process.platform 값, toLocaleString
from ncn.tools.truncate import js_len, parse_env_number, truncate_output_from_start   # 자르기(cn truncateOutput.ts)

DEFAULT_BASH_MAX_CHARS = 50000                               # ~12.5k 토큰
DEFAULT_BASH_MAX_LINES = 1000                                # 줄 한도
_TIMEOUT_MAX_MS = 2 ** 31 - 1                                # Node setTimeout 최대(넘거나 1 미만·NaN이면 1ms)

# ── 루프·화면이 끼워 넣는 연결점(cn의 전역 서비스 대응) ──
TOOL_OUTPUT_LISTENER: Optional[Callable[[str, str, str], None]] = None   # services.chatHistory.addToolResult(id, 출력, "calling") 대응 — 실행 중 출력 보여주기
BASH_EVENTS_LISTENER: Optional[Callable[[str], None]] = None   # emitBashToolStarted/Ended("started"/"ended") 대응(대화형 화면용)
ENV_PROVIDER: Optional[Callable[[], dict]] = None            # [추가 자리 E7] 자식 프로세스 환경변수(비밀 제거 등). None 이면 cn처럼 그대로 물려받음


class BashCommandError(Exception):
    """cn은 reject(문자열)로 실패를 알린다 — 루프가 'Error executing tool Bash: {메시지}'로 감싼다."""


def is_running_in_wsl() -> bool:
    """isRunningInWsl — linux 이고 WSL_DISTRO_NAME 이 있거나 /proc/version 에 microsoft·wsl 이 있으면 참."""
    if node_platform() != "linux":                           # 리눅스가 아니면 WSL 아님
        return False
    if os.environ.get("WSL_DISTRO_NAME"):                    # 환경변수(빈 글은 거짓 — JS와 같음)
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:   # 커널 버전 글
            proc_version = f.read().lower()                  # 소문자로
        return "microsoft" in proc_version or "wsl" in proc_version   # WSL 흔적
    except Exception:                                        # 못 읽으면 아님
        return False


def _get_bash_max_chars() -> int:
    """CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS(없으면 50000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_BASH_MAX_OUTPUT_CHARS"), DEFAULT_BASH_MAX_CHARS)


def _get_bash_max_lines() -> int:
    """CONTINUE_CLI_BASH_MAX_OUTPUT_LINES(없으면 1000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_BASH_MAX_OUTPUT_LINES"), DEFAULT_BASH_MAX_LINES)


def get_shell_command(command: str) -> tuple[str, list[str]]:
    """getShellCommand — (셸, 인자). Windows는 PowerShell, WSL·Unix·macOS는 로그인 셸(-l -c)."""
    if node_platform() == "win32":                           # Windows: PowerShell(5.1 기본 — 계획 14-5)
        return "powershell.exe", ["-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", command]
    if is_running_in_wsl():                                  # WSL: bash 가 늘 있음
        wsl_shell = os.environ.get("SHELL") or "/bin/bash"   # process.env.SHELL || "/bin/bash"
        return wsl_shell, ["-l", "-c", command]
    user_shell = os.environ.get("SHELL") or "/bin/bash"      # Unix/macOS: .bashrc/.zshrc 등을 읽게 로그인 셸
    return user_shell, ["-l", "-c", command]


def _creationflags() -> int:
    """[추가 V6] Windows 에서 콘솔 창이 번쩍이지 않게 CREATE_NO_WINDOW(출력·동작은 같음). 그 밖은 0."""
    if node_platform() == "win32":                           # Windows 에서만
        import subprocess                                    # 상수
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)    # 0x08000000
    return 0


def _child_env() -> Optional[dict]:
    """자식 환경변수 — 연결점이 없으면 None(=물려받음, cn spawn 기본)."""
    return ENV_PROVIDER() if ENV_PROVIDER else None          # E7 자리


async def run_command_in_background(command: str, *, cwd: Optional[str] = None) -> dict:
    """runCommandInBackground — {success, jobId?, error?}. 한도(5개)면 실패."""
    job = background_job_service.create_job(command)         # 작업 만들기
    if not job:                                              # 한도
        return {"success": False, "error": "Cannot create background job: limit of 5 concurrent jobs reached"}
    shell, args = get_shell_command(command)                 # 셸
    child = await background_job_service.start_job(job.id, shell, args, cwd=cwd, env=_child_env(),
                                                   creationflags=_creationflags())   # 띄우기
    if not child:                                            # 시작 못 함
        return {"success": False, "error": f"Failed to start background job {job.id}"}
    return {"success": True, "jobId": job.id}                # 성공


def _js_number(v: Any) -> float:
    """JS 숫자 변환(Math.min 이 인자에 하는 ToNumber). null→0, true→1, "" →0, 숫자 아닌 글→NaN."""
    if v is None:                                            # null
        return 0.0
    if isinstance(v, bool):                                  # true/false
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):                          # 숫자
        return float(v)
    if isinstance(v, str):                                   # 글
        s = v.strip()                                        # 앞뒤 공백 무시
        if s == "":                                          # 빈 글 = 0
            return 0.0
        try:
            if s.lower().startswith(("0x", "0o", "0b")):      # 16·8·2진 표기
                return float(int(s, 0))
            if s in ("Infinity", "+Infinity"):               # JS 무한대 표기
                return math.inf
            if s == "-Infinity":
                return -math.inf
            if s.lower() in ("inf", "+inf", "-inf", "nan", "infinity", "+infinity", "-infinity"):   # Python만 받는 표기는 NaN
                return math.nan
            return float(s)                                  # 보통 숫자
        except ValueError:                                   # 숫자 아님
            return math.nan
    return math.nan                                          # 배열·객체 등


def _js_num_str(x: float) -> str:
    """JS 숫자 → 글(String(n)). 정수는 소수점 없이, NaN/Infinity 표기."""
    if math.isnan(x):                                        # NaN
        return "NaN"
    if math.isinf(x):                                        # 무한대
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e21:                        # 정수 값
        return str(int(x))
    return repr(x)                                           # 소수(짧은 표기 — JS와 대부분 같음)


def _js_substring(s: str, end: int) -> str:
    """s.substring(0, end) — UTF-16 위치 기준."""
    if js_len(s) == len(s):                                  # BMP만 있으면 그냥
        return s[:end]
    u = s.encode("utf-16-le", errors="surrogatepass")        # UTF-16 으로
    return u[: end * 2].decode("utf-16-le", errors="surrogatepass")   # 앞 end 개 단위


def _emit_bash_event(name: str) -> None:
    """emitBashToolStarted/Ended."""
    if BASH_EVENTS_LISTENER:                                 # 연결돼 있으면
        try:
            BASH_EVENTS_LISTENER(name)                       # 알림
        except Exception:                                    # 화면 오류는 무시
            pass


def _evaluate_tool_call_policy(base_policy: str, parsed_args: dict) -> str:
    """evaluateToolCallPolicy — 명령의 위험도로 정책을 올림(@continuedev/terminal-security). 지연 import(시험에서 바꿔 끼울 수 있게)."""
    from ncn.security import terminal_security               # 다른 작업자 모듈(모듈째 가져와 monkeypatch 가 먹게)
    return terminal_security.evaluate_terminal_command_security(base_policy, parsed_args.get("command"))


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 명령이 비었거나 글이 아니면 오류, 미리보기 'Will run: …'(60자 넘으면 자르고 ...)."""
    command = args.get("command")                            # 명령
    if not command or not isinstance(command, str):          # !command || typeof !== "string"
        raise Exception("command arg is required and must be a non-empty string")
    truncated_cmd = _js_substring(command, 60) + "..." if js_len(command) > 60 else command   # 60자
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Will run: {truncated_cmd}")])


def make_bash_tool(cwd: str | os.PathLike | None = None) -> Tool:
    """Bash 도구를 만든다. 설명의 작업 폴더는 cn의 process.cwd() 자리 — ncn은 넘겨받은 cwd(없으면 os.getcwd())."""
    work_dir = str(cwd) if cwd is not None else os.getcwd()  # 작업 폴더

    async def run(args: dict, context: Optional[ToolRunContext] = None) -> str:
        """run — 명령을 돌려 출력 글을 돌려줌. 0 아닌 종료 + stderr 있음 → 오류."""
        command = args.get("command")                        # 명령
        has_timeout = "timeout" in args                      # timeout !== undefined(null 도 '있음' — JS 그대로)
        timeout = args.get("timeout")                        # 초
        parallel_count = (context.parallel_tool_call_count if context else 1) or 1   # 동시 실행 수
        base_max_chars = _get_bash_max_chars()               # 한도(글자)
        base_max_lines = _get_bash_max_lines()               # 한도(줄)
        max_chars = base_max_chars // parallel_count         # Math.floor(나눗셈)
        max_lines = base_max_lines // parallel_count         # Math.floor(나눗셈)

        _emit_bash_event("started")                          # emitBashToolStarted

        loop = asyncio.get_running_loop()                    # 이벤트 루프
        fut: asyncio.Future = loop.create_future()           # Promise 대응
        shell, sh_args = get_shell_command(command)          # 셸
        state = {"stdout": "", "stderr": "", "resolved": False, "timer": None}   # 공유 상태

        # 시간 제한 결정: timeout(최대 600초) → 시험용 환경변수 → 기본 180초
        timeout_ms: float = 180000.0                         # 기본 180초
        if has_timeout:                                      # 인자가 있으면
            t = _js_number(timeout)                          # ToNumber
            capped = math.nan if math.isnan(t) else min(t, 600.0)   # Math.min(timeout, 600) — NaN 이면 NaN
            timeout_ms = capped * 1000                       # 밀리초
        elif os.environ.get("NODE_ENV") == "test" and os.environ.get("TEST_TERMINAL_TIMEOUT"):   # 시험용(cn과 같은 이름)
            try:
                timeout_ms = float(int(os.environ["TEST_TERMINAL_TIMEOUT"].strip().split()[0]))   # parseInt
            except Exception:
                timeout_ms = math.nan                        # parseInt 실패 = NaN
        delay_ms = timeout_ms if (not math.isnan(timeout_ms) and 1 <= timeout_ms <= _TIMEOUT_MAX_MS) else 1   # Node setTimeout 보정

        def append_parallel_limit_note(output: str) -> str:
            """동시 실행으로 한도가 줄었을 때 안내 한 줄."""
            if parallel_count > 1:                           # 동시 실행이면
                return (output + f"\n\n(Note: output limit reduced due to {parallel_count} parallel tool calls. "
                        f"Single-tool limit: {to_locale_string(base_max_chars)} characters or {to_locale_string(base_max_lines)} lines.)")
            return output

        def finish(output: str) -> str:
            """잘라서 잘렸으면 안내를 붙임."""
            r = truncate_output_from_start(output, {"maxChars": max_chars, "maxLines": max_lines})   # 뒷부분 남김
            return append_parallel_limit_note(r.output) if r.was_truncated else r.output

        def settle(result: Optional[str] = None, error: Optional[str] = None) -> None:
            """resolve/reject 한 번만."""
            if fut.done():                                   # 이미 끝남
                return
            if error is not None:                            # reject
                fut.set_exception(BashCommandError(error))
            else:                                            # resolve
                fut.set_result(result)

        def clear_timer() -> None:
            """clearTimeout."""
            if state["timer"] is not None:                   # 걸려 있으면
                state["timer"].cancel()                      # 취소
                state["timer"] = None

        try:
            child = await spawn_child(shell, sh_args, cwd=work_dir, env=_child_env(), creationflags=_creationflags())   # spawn
        except OSError as e:                                 # 'error' 이벤트(셸 없음 등)
            raise BashCommandError(f"Error: {spawn_error_message(shell, e)}")   # reject(`Error: ${error.message}`)

        def show_current_output() -> None:
            """실행 중 출력을 대화 기록에 'calling' 으로 반영(연결점이 있을 때)."""
            if not context or not context.tool_call_id:      # id 없으면 안 함
                return
            if TOOL_OUTPUT_LISTENER is None:                 # 연결 안 됨
                return
            try:
                current = state["stdout"] + (f"\nStderr: {state['stderr']}" if state["stderr"] else "")   # 지금까지
                TOOL_OUTPUT_LISTENER(context.tool_call_id, current, "calling")   # 알림
            except Exception:                                # 무시
                pass

        def on_timeout() -> None:
            """출력 없는 시간이 한도를 넘음 → 죽이고 지금까지 출력 + 안내."""
            if state["resolved"]:
                return
            state["resolved"] = True
            child.kill()                                     # child.kill()
            output = state["stdout"] + (f"\nStderr: {state['stderr']}" if state["stderr"] else "")   # 지금까지
            output += f"\n\n[Command timed out after {_js_num_str(timeout_ms / 1000)} seconds of no output]"   # 안내
            settle(finish(output))                           # 자르고 끝냄

        def reset_timeout() -> None:
            """출력이 올 때마다 타이머를 다시 건다."""
            clear_timer()                                    # 이전 것 취소
            state["timer"] = loop.call_later(delay_ms / 1000, on_timeout)   # 새 타이머

        def move_to_background() -> None:
            """Ctrl+B — 프로세스를 백그라운드 작업으로 넘기고 지금까지 출력을 돌려줌."""
            if state["resolved"]:
                return
            state["resolved"] = True
            clear_timer()                                    # 타이머 끔
            background_signal_manager.off(move_to_background)   # 신호 떼기
            child.on_stdout = None                           # 이 도구의 듣기 떼기(removeListener)
            child.on_stderr = None
            job = background_job_service.create_job_with_process(command, child, state["stdout"])   # 작업으로(stderr 는 안 넘김 — cn 그대로)
            if job:                                          # 넘김 성공
                r = truncate_output_from_start(state["stdout"], {"maxChars": max_chars, "maxLines": max_lines})
                output_so_far = append_parallel_limit_note(r.output) if r.was_truncated else r.output
                settle(f"Command moved to background. Job ID: {job.id}\nOutput so far:\n{output_so_far}\n"
                       f"Use CheckBackgroundJob(\"{job.id}\") to check status.")
            else:                                            # 한도
                settle(f"Failed to move to background (job limit reached). Command continues in foreground.\n"
                       f"Output so far: {state['stdout']}")

        background_signal_manager.on(move_to_background)     # Ctrl+B 듣기

        reset_timeout()                                      # 첫 타이머

        def on_stdout(data: str) -> None:
            """stdout 조각."""
            state["stdout"] += data                          # 붙이기
            reset_timeout()                                  # 시간 다시
            show_current_output()                            # 보여 주기

        def on_stderr(data: str) -> None:
            """stderr 조각."""
            state["stderr"] += data
            reset_timeout()
            show_current_output()

        def on_close(code: Optional[int]) -> None:
            """'close' — 종료 코드로 결과 결정."""
            if state["resolved"]:
                return
            state["resolved"] = True
            clear_timer()                                    # 타이머 끔
            background_signal_manager.remove_listener(move_to_background)   # 신호 떼기
            if code != 0 and state["stderr"]:                # 0 아님 + stderr 있음 → 오류(신호 종료는 code=null)
                settle(error=f"Error (exit code {'null' if code is None else code}): {state['stderr']}")
                return
            output = state["stdout"]                         # 출력
            if state["stderr"]:                              # stderr 붙이기
                output = state["stdout"] + f"\nStderr: {state['stderr']}"
            settle(finish(output))                           # 자르고 끝냄

        def on_error(msg: str) -> None:
            """'error' — 읽기 실패 등."""
            if state["resolved"]:
                return
            state["resolved"] = True
            clear_timer()
            background_signal_manager.off(move_to_background)
            settle(error=f"Error: {msg}")

        child.on_stdout = on_stdout                          # 듣기 연결
        child.on_stderr = on_stderr
        child.on_close = on_close
        child.on_error = on_error
        child.start()                                        # 읽기 시작

        try:
            terminal_output = await fut                      # 결과 기다림(오류면 여기서 던짐 — cn도 emitBashToolEnded 를 건너뜀)
        except asyncio.CancelledError:                       # 도구 실행 취소(Esc 등) — 프로세스 정리
            clear_timer()
            background_signal_manager.off(move_to_background)
            if not state["resolved"]:
                child.kill()
            raise
        _emit_bash_event("ended")                            # emitBashToolEnded
        return terminal_output

    return Tool(
        name="Bash",
        display_name="Bash",
        description=(
            "Executes a terminal command and returns the output\n"
            "\n"
            f"Commands are automatically executed from the current working directory ({work_dir}), so there's no need to change directories with 'cd' commands.\n"
            "\n"
            "IMPORTANT: To edit files, use Edit/MultiEdit tools instead of bash commands (sed, awk, etc).\n"
        ),
        parameters={
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute in the terminal.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Optional timeout in seconds (max 600). Use this parameter for commands that take longer than the default 180 second timeout.",
                },
            },
        },
        run=run,
        preprocess=_preprocess,
        readonly=False,
        is_built_in=True,
        evaluate_tool_call_policy=_evaluate_tool_call_policy,
    )
