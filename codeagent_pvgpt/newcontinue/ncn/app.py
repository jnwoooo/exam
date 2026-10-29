# ncn/app.py — 명령줄 해석과 헤드리스(-p) 실행 조립. cn extensions/cli/src/index.ts(옵션·검증·stdin·프롬프트 확인)
#   + shared-options.ts(공통 옵션) + flags/flagValidator.ts·flagProcessor.ts + commands/chat.ts(chat·runHeadlessMode·processMessage)
#   + services/index.ts(초기화 순서) + util/cli.ts(hasSuppliedPrompt) + util/stdin.ts(readStdinSync) + util/promptProcessor.ts
# 헤드리스 조립 순서(cn과 같게): .env 적용 → 플래그 검증 → stdin → 프롬프트 확인 → 설정 읽기 → chat 모델 → LLMClient
#   → 권한(PermissionService) → 도구 목록(registry) → 시스템 메시지 → 세션(--resume/--fork) → run_turn → 출력·종료 코드
# stdout에는 최종 응답만(cn safeStdout), 오류는 stderr에 {"status":"error","message":…}(cn chat.ts:618-626).
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄 해석
import asyncio                                               # 비동기 루프
import logging                                               # 로그(stdout 금지)
import os                                                    # 환경변수·현재 폴더
import pathlib                                               # 경로
import sys                                                   # 표준 입출력·종료
from typing import Any, Optional                             # 타입

from ncn import __version__                                  # 버전
from ncn import headless as hl                               # 출력 형식·종료 코드

log = logging.getLogger("ncn.app")                           # 로거

SUBCOMMANDS = ("ls", "serve", "checks", "review")            # cn 하위 명령(ncn은 아직 미구현)
NOT_IMPLEMENTED = "ncn {ver}: {what} 은(는) 아직 미구현입니다."   # P2 이후 기능 안내(stderr)


# ─────────────────────────────────────────────────────────────────────────────
# 명령줄(commander 흉내)
# ─────────────────────────────────────────────────────────────────────────────


class CommanderError(Exception):
    """commander 해석 오류 — 글은 commander 형식("error: …"), 종료 코드 1."""


class _Parser(argparse.ArgumentParser):
    """argparse 오류를 commander 글로 바꾼다(종료 코드 2 → 1)."""

    def error(self, message: str):                           # argparse 가 부르는 오류 처리
        if "expected one argument" in message:               # 값 없는 옵션: "argument --config: expected one argument"
            opt = message.split(":", 1)[0].replace("argument", "", 1).strip().split("/")[-1]
            flag = _OPTION_SPECS.get(opt, opt)
            raise CommanderError(f"error: option '{flag}' argument missing")
        raise CommanderError(f"error: {message}")


_OPTION_SPECS = {                                            # commander 표기(오류 글용)
    "--config": "--config <path>", "--org": "--org <slug>", "--rule": "--rule <rule>", "--mcp": "--mcp <slug>",
    "--model": "--model <slug>", "--prompt": "--prompt <prompt>", "--allow": "--allow <tool>",
    "--ask": "--ask <tool>", "--exclude": "--exclude <tool>", "--agent": "--agent <slug>",
    "--format": "--format <format>", "--fork": "--fork <sessionId>",
}


def build_parser() -> argparse.ArgumentParser:
    """cn 루트 명령의 옵션(shared-options.ts addCommonOptions + index.ts) + ncn 전용(--stdio, --version)."""
    p = _Parser(prog="ncn", add_help=True,
                description="Continue CLI - AI-powered development assistant. Starts an interactive session by default, "
                            "use -p/--print for non-interactive output.")
    p.add_argument("prompt", nargs="?", help="Optional prompt to send to the assistant")   # [prompt]
    # ── 공통 옵션(shared-options.ts 순서) ──
    p.add_argument("--config", help="Configuration file for the assistant (can be a file path or hub slug)")
    p.add_argument("--org", help="Organization slug to use for this session (supported only in headless mode)")
    p.add_argument("--readonly", action="store_true", default=None, help="Start in plan mode (read-only tools)")
    p.add_argument("--auto", action="store_true", default=None, help="Start in auto mode (all tools allowed)")
    p.add_argument("--verbose", action="store_true", default=None, help="Enable verbose logging")
    p.add_argument("--beta-status-tool", action="store_true", default=None, help="Enable beta status tool")
    p.add_argument("--rule", action="append", default=[], help="Add a rule (can be a file path, hub slug, or string content). Can be specified multiple times.")
    p.add_argument("--mcp", action="append", default=[], help="Add an MCP server from the hub (slug in format 'owner/package'). Can be specified multiple times.")
    p.add_argument("--model", action="append", default=[], help="Add a model from the hub (slug in format 'owner/package'). Can be specified multiple times.")
    p.add_argument("--prompt", dest="prompts", action="append", default=[], help="Add to the initial user message (can be a file path, hub slug, or string content). Can be specified multiple times.")
    p.add_argument("--allow", action="append", default=[], help="Allow specified tool (overrides default policies). Can be specified multiple times.")
    p.add_argument("--ask", action="append", default=[], help="Ask for permission before using specified tool (overrides default policies). Can be specified multiple times.")
    p.add_argument("--exclude", action="append", default=[], help="Exclude specified tool from use (overrides default policies). Can be specified multiple times.")
    p.add_argument("--agent", help="Load agent file from the hub (slug in format 'owner/package')")
    # ── 루트 명령 전용(index.ts) ──
    p.add_argument("-p", "--print", dest="print", action="store_true", default=None, help="Print response and exit (useful for pipes)")
    p.add_argument("--format", help="Output format for headless mode (json). Only works with -p/--print flag.")
    p.add_argument("--silent", action="store_true", default=None, help="Strip <think></think> tags and excess whitespace from output. Only works with -p/--print flag.")
    p.add_argument("--resume", action="store_true", default=None, help="Resume from last session")
    p.add_argument("--fork", help="Fork from an existing session ID")
    p.add_argument("--beta-subagent-tool", action="store_true", default=None, help="Enable beta Subagent tool for invoking subagents")
    # ── ncn 전용 ──
    p.add_argument("--stdio", action="store_true", help="ACP stdio 모드(P2) — VS 2022 확장용")
    p.add_argument("-v", "--version", action="version", version=f"ncn {__version__}", help="Display version number")
    return p


def validate_flags(a: argparse.Namespace) -> list[str]:
    """flagValidator.ts validateFlags — 오류 글 목록(cn 원문, 검사 순서 그대로)."""
    errs: list[str] = []
    if a.format and not a.print:                             # validateFormatFlag
        errs.append("Error: --format flag can only be used with -p/--print flag")
    if a.silent and not a.print:                             # validateSilentFlag
        errs.append("Error: --silent flag can only be used with -p/--print flag")
    if a.format and a.format != "json":                      # validateFormatValue
        errs.append("Error: --format currently only supports 'json'")
    if a.readonly and a.auto:                                # validateModeFlags
        errs.append("Error: Cannot use both --readonly and --auto flags together")
    if a.resume and a.fork:                                  # validateSessionFlags
        errs.append("Error: Cannot use both --resume and --fork flags together")
    if a.fork is not None and (not a.fork or not hl.js_trim(a.fork)):
        errs.append("Error: --fork requires a session ID (e.g., --fork abc123)")
    if any(not hl.js_trim(t) for t in a.allow):              # validatePermissionFlags
        errs.append("Error: --allow requires a tool name (e.g., --allow readFile)")
    if any(not hl.js_trim(t) for t in a.ask):
        errs.append("Error: --ask requires a tool name (e.g., --ask writeFile)")
    if any(not hl.js_trim(t) for t in a.exclude):
        errs.append("Error: --exclude requires a tool name (e.g., --exclude Write)")
    return errs


def validation_error_text(errs: list[str]) -> str:
    """handleValidationErrors — 글마다 한 줄, 2개 이상이면 개수 줄."""
    out = "".join(e + "\n" for e in errs)
    if len(errs) > 1:
        out += f"\nFound {len(errs)} validation errors.\n"
    return out


PROMPT_REQUIRED = ("Error: A prompt is required when using the -p/--print flag, unless --prompt, --agent, or --resume is provided.\n\n"
                   "Usage examples:\n"
                   '  cn -p "please review my current git diff"\n'
                   '  echo "hello" | cn -p\n'
                   '  cn -p "analyze the code in src/"\n'
                   "  cn -p --agent my-org/my-agent\n"
                   "  cn -p --prompt my-org/my-prompt\n"
                   "  cn -p --resume\n")                    # index.ts:279-291 원문(stderr)

_FLAGS_WITH_VALUES = {"--config", "--model", "--output", "--mode", "--workflow", "--agent", "-m", "-c", "-o"}   # cli.ts 원문 목록


def is_headless_mode(argv: list[str]) -> bool:
    """cli.ts isHeadlessMode — 인자에 -p 또는 --print."""
    return "-p" in argv or "--print" in argv


def has_supplied_prompt(argv: list[str]) -> bool:
    """cli.ts hasSuppliedPrompt — -p 뒤에 플래그가 아닌 인자가 있거나 --prompt 가 있으면 참(값 받는 플래그 목록은 cn 그대로)."""
    idx = next((i for i, x in enumerate(argv) if x in ("-p", "--print")), -1)
    if idx == -1:
        return False
    if "--prompt" in argv:
        return True
    rest = argv[idx + 1:]
    i = 0
    while i < len(rest):
        x = rest[i]
        if x in _FLAGS_WITH_VALUES:                          # 값까지 건너뜀
            i += 2
            continue
        if x.startswith("-"):                                # 다른 플래그
            i += 1
            continue
        return True                                          # 프롬프트 발견
    return False


def read_stdin_sync(argv: list[str], environ=os.environ) -> Optional[str]:
    """stdin.ts readStdinSync — 파이프 입력이면 전부 읽어 trim. 단말·시험 환경·프롬프트가 있는 헤드리스면 None."""
    try:
        if (environ.get("NODE_ENV") == "test" or environ.get("VITEST") == "true"
                or environ.get("JEST_WORKER_ID") is not None or environ.get("CONTINUE_CLI_TEST") == "true"):
            return None
        if is_headless_mode(argv) and has_supplied_prompt(argv):   # 프롬프트가 있으면 읽지 않음
            return None
        if environ.get("FORCE_NO_TTY") == "true":
            return None
        if sys.stdin is None or sys.stdin.isatty():          # 단말
            return None
        data = sys.stdin.buffer.read().decode("utf-8", "replace")   # readFileSync(0, "utf8")
        return hl.js_trim(data)
    except Exception:                                        # 못 읽으면 없는 것
        return None


def prepend_prompt(prepend: Optional[str], original: Optional[str]) -> Optional[str]:
    """promptProcessor.ts prependPrompt — 둘 다 trim 해서 빈 줄로 잇고 다시 trim. 비면 None."""
    s = hl.js_trim(f"{hl.js_trim(prepend or '')}\n\n{hl.js_trim(original or '')}")
    return s or None


def process_and_combine_prompts(additional: list[str], initial: Optional[str], cwd: str) -> Optional[str]:
    """promptProcessor.ts processAndCombinePrompts — --prompt 를 processRule 로 읽어 앞에 붙인다(실패는 경고만)."""
    from ncn.sysmsg import process_rule                      # 규칙 읽기(파일 또는 글)
    if not additional:
        return initial
    done = []
    for spec in additional:
        try:
            done.append(process_rule(spec, cwd))
        except Exception as e:                               # logger.warn
            log.warning('Failed to process prompt "%s": %s', spec, e)
    if not done:
        return initial
    combined = "\n\n".join(done)
    return f"{combined}\n\n{initial}" if initial else combined


# ─────────────────────────────────────────────────────────────────────────────
# 헤드리스 실행(commands/chat.ts runHeadlessMode)
# ─────────────────────────────────────────────────────────────────────────────


class HeadlessExit(Exception):
    """gracefulExit(code) 대신 — 위로 올려 종료 코드를 정한다."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code                                     # 종료 코드


def _hard_exit(code: int) -> None:
    """Exit 도구용 process.exit — 출력 버퍼를 비우고 즉시 끝낸다(남은 작업·세션 저장 없음 — cn과 같음)."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)


def _prune_last_message(items: list[dict]) -> list[dict]:
    """compaction.ts pruneLastMessage — 끝에서 두 번째가 도구 호출 assistant 또는 user 면 둘, 아니면 하나를 뺀다."""
    if not items:
        return items
    if len(items) == 1:
        return []
    second = items[-2].get("message", {})
    if second.get("role") == "assistant" and len(second.get("toolCalls") or []) > 0:
        return items[:-2]
    if second.get("role") == "user":
        return items[:-2]
    return items[:-1]


def make_validate_context(model_raw: dict):
    """streamChatResponse.ts processStreamingResponse 앞부분 — 한도를 넘으면 끝부터 잘라 맞추고, 그래도 넘으면 오류."""
    from ncn.tokenizer import validate_context_length       # 토큰 세기(cn 규칙)
    safety = 100                                             # SAFETY_BUFFER

    def validate(items: list, system_message: str, tools: list) -> list:
        v = validate_context_length(chat_history=items, model=model_raw, safety_buffer=safety,
                                    system_message=system_message, tools=tools)
        while len(items) > 1 and not v["isValid"]:           # 잘라 가며 다시 검사
            pruned = _prune_last_message(items)
            if len(pruned) == len(items):
                break
            items = pruned
            v = validate_context_length(chat_history=items, model=model_raw, safety_buffer=safety,
                                        system_message=system_message, tools=tools)
        if not v["isValid"]:
            raise Exception(f"Context length validation failed: {v['error']}")
        return items

    return validate


async def run_headless(prompt: Optional[str], a: argparse.Namespace, *, cwd: pathlib.Path) -> int:
    """runHeadlessMode + processMessage(헤드리스 한 턴). 돌려주는 값: 종료 코드. 오류는 예외로 올린다(chat() 이 stderr JSON)."""
    from ncn.config import paths as cpaths                   # 전역 폴더
    from ncn.config.loader import ConfigError, load_config   # 설정
    from ncn.llm.client import LLMClient                     # LLM
    from ncn.loop import events as ev                        # 이벤트
    from ncn.loop.agent import LoopDeps, run_turn            # 루프
    from ncn.loop.history import ChatHistory, find_compaction_index   # 기록
    from ncn.permissions.service import PermissionService    # 권한
    from ncn.session import SessionManager, history_manager_load   # 세션
    from ncn.sysmsg import build_system_message              # 시스템 메시지
    from ncn.tools import exit as exit_tool                  # Exit 도구 종료 방법
    from ncn.tools import registry                           # 도구 목록

    environ = os.environ                                     # dotenv 적용된 process.env
    gdir = cpaths.global_dir(environ, cwd)                   # CONTINUE_GLOBAL_DIR
    if a.beta_subagent_tool:                                 # initializeServices: 베타 도구
        registry.set_beta_subagent_tool_enabled(True)
    # ANTHROPIC_API_KEY + --config 없음이면 cn은 config.yaml 을 만들어 덮어쓴다(services/index.ts:79-94) — ncn은 하지 않음(폐쇄망, 보고됨)

    # ── 권한(TOOL_PERMISSIONS) ── processCommandFlags → toolPermissionService.initialize
    mode = "plan" if a.readonly else "auto" if a.auto else None
    perms = PermissionService(mode=mode or "normal", allow=a.allow, ask=a.ask, exclude=a.exclude,
                              headless=True, global_dir=gdir)

    # ── 설정(CONFIG) → 모델(MODEL) ──
    try:
        cfg = load_config(a.config, cwd=cwd, global_dir=gdir, environ=environ, headless=True,
                          rules=a.rule, mcp=a.mcp, models=a.model, prompts=a.prompts)
    except ConfigError as e:
        raise Exception(e.message) from e
    if cfg.chat_model is None:                               # getLlmApi 실패 글
        raise Exception(cfg.chat_model_error or "No models were found.")
    model = cfg.chat_model
    llm = LLMClient(api_base=model.api_base, api_key=model.api_key, model=model.model,
                    default_completion_options=model.raw.get("defaultCompletionOptions"),
                    request_options=model.request_options)

    # ── 대화 기록·세션(--resume / --fork) ──
    sessions = SessionManager(environ, os.fspath(cwd))       # SessionManager 싱글턴 자리
    initial_items: list = []                                 # initializeChatHistory
    if a.fork:                                               # --fork: 그 기록으로 새 세션(못 찾아도 빈 기록 — cn 그대로)
        forked = history_manager_load(a.fork, environ)
        initial_items = sessions.start_new(forked.get("history") or [])["history"]
    elif a.resume:                                           # --resume: 가장 최근 세션
        s = sessions.load_latest()
        initial_items = s.get("history") or [] if s else []
    history = ChatHistory()                                  # 헤드리스: sessionId "" (Subagent 실패 재현 — P0 0장 3번)
    if a.resume or a.fork:
        history.set_history(initial_items)
        _ = find_compaction_index(initial_items)             # 압축 위치(루프가 다시 읽으므로 결과 같음)

    # ── 첫 사용자 입력 ──
    initial = process_and_combine_prompts(a.prompts, prepend_prompt(None, prompt), os.fspath(cwd))
    if not initial or not hl.js_trim(initial):               # 프롬프트 없음
        if a.resume or a.fork:                               # 기록만 불러오고 0으로 끝
            await llm.aclose()
            return hl.EXIT_OK
        raise Exception(hl.HEADLESS_REQUIRES_PROMPT)

    # ── 루프 의존성 ──
    model_name = model.model or ""                           # 루프의 model_name = model.model(사용량 기록의 model 값 — cn 그대로)
    overrides = (model.chat_options or {}).get("toolOverrides")   # model.chatOptions.toolOverrides

    def all_tools():                                         # getAllAvailableTools(true) — 매 반복 다시(덮어쓰기 없음: 실행용 조회)
        return registry.get_builtin_tools(model_name=model.name or "", model=model.model, headless=True,
                                          mode=perms.mode, cwd=cwd, global_dir=gdir)

    def system_message():                                    # services.systemMessage.getSystemMessage(currentMode)
        return build_system_message(mode=perms.mode, headless=True, format_json=a.format == "json", cwd=cwd,
                                    global_dir=gdir, config=cfg, additional_rules=a.rule, environ=environ)

    def on_event(e: dict) -> None:                           # 사용량 → 세션 저장(trackSessionUsage)
        if e.get("type") == "usage" and isinstance(e.get("usage"), dict):
            sessions.track_usage(e["usage"])

    deps = LoopDeps(
        llm=llm, tools=all_tools,
        check_permission=lambda tool, args: perms.check(tool, args),          # 실행 시점(동적 평가 포함)
        list_permission=lambda tool, args: perms.base_permission(tool.name, {}),   # 목록용(정적 정책, 인자 {})
        ask_user=None,                                       # 헤드리스는 묻지 않음
        system_message=system_message, on_event=on_event,
        validate_context=make_validate_context(model.raw),
        apply_tool_overrides=(lambda defs: registry.apply_chat_completion_tool_overrides(defs, overrides)),
    )
    exit_tool.EXIT_HANDLER = _hard_exit                      # Exit 도구 = process.exit(1)

    try:
        result = await run_turn(history, initial, deps, headless=True, model_name=model_name)   # processMessage
        if result.manual_compaction is not None:             # /compact(P3) — 결과 줄
            sys.stdout.write(hl.format_manual_compaction_line(history_length=len(history.get_history())))
        out = hl.format_headless_output(result.response, silent=bool(a.silent), format=a.format)   # processAndOutputResponse
        if out is not None:
            sys.stdout.write(out)
            sys.stdout.flush()
        sessions.update_history(history.get_history())       # updateSessionHistory
    finally:
        await llm.aclose()
    return hl.EXIT_OK                                        # gracefulExit(0)


# ─────────────────────────────────────────────────────────────────────────────
# 진입점(index.ts action + chat())
# ─────────────────────────────────────────────────────────────────────────────


def _configure_logging(verbose: bool) -> None:
    """cn 헤드리스는 콘솔 출력을 막고 파일 로그만 쓴다. ncn: --verbose 면 stderr DEBUG, 아니면 아무 데도 안 찍음(stdout 금지)."""
    root = logging.getLogger()
    if verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    else:
        root.addHandler(logging.NullHandler())               # 마지막 수단 처리기(stderr 경고)도 막음
        root.setLevel(logging.CRITICAL + 1)


def _stdout_utf8() -> None:
    """stdout/stderr 를 UTF-8·줄바꿈 변환 없이(Windows cp949·\\r\\n 방지 — 계획 14-2 V5)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", newline="\n")    # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    """명령줄을 해석해 모드를 고른다. 헤드리스(-p)는 실행, 나머지(대화형·--stdio·하위 명령·P2 옵션)는 미구현 안내 후 2."""
    argv = list(sys.argv[1:] if argv is None else argv)      # process.argv.slice(2)
    _stdout_utf8()
    cwd = pathlib.Path(os.getcwd())                          # process.cwd()
    from ncn.config.env import apply_dotenv                  # dotenv.config() — 모듈 로드 시점(가장 먼저)
    apply_dotenv(os.environ, os.fspath(cwd))

    if argv and argv[0] in SUBCOMMANDS:                      # cn 하위 명령
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=f"'{argv[0]}' 하위 명령") + "\n")
        return 2
    try:
        a, extra = build_parser().parse_known_args(argv)     # 알 수 없는 것은 따로
        if extra:                                            # commander: 알 수 없는 옵션 / 인자 초과
            opt = next((x for x in extra if x.startswith("-")), None)
            if opt is not None:
                raise CommanderError(f"error: unknown option '{opt}'")
            raise CommanderError(f"error: too many arguments. Expected 1 argument but got {1 + len(extra)}.")
    except CommanderError as e:                              # cn은 -p 가 있으면 init.ts 가 stdout/stderr 를 막아 이 글도 안 보인다
        if not is_headless_mode(argv):
            sys.stderr.write(str(e) + "\n")
        return 1

    if a.stdio:                                              # ACP(P2)
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what="--stdio(ACP) 모드") + "\n")
        return 2
    if not a.print:                                          # 대화형(TUI)은 아직
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what="대화형 모드(-p 없이 실행)") + "\n")
        return 2

    _configure_logging(bool(a.verbose))                      # configureConsoleForHeadless
    errs = validate_flags(a)                                 # validateFlags
    if errs:
        sys.stderr.write(validation_error_text(errs))
        return 1

    pending = [name for name, v in (("--org", a.org), ("--agent", a.agent), ("--mcp", a.mcp)) if v]   # P2 이후(Hub·MCP)
    if pending:
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=", ".join(pending)) + "\n")
        return 2

    prompt = a.prompt                                        # 헤드리스 stdin(프롬프트가 없을 때만 읽힘)
    stdin_input = read_stdin_sync(argv)
    if stdin_input:
        prompt = f"<stdin>\n{stdin_input}\n</stdin>\n\n{prompt}" if prompt else stdin_input
    if not prompt and not a.agent and not a.resume:          # 프롬프트 필수
        sys.stderr.write(PROMPT_REQUIRED)
        return 1

    try:                                                     # chat(): 헤드리스 경로
        code = asyncio.run(run_headless(prompt, a, cwd=cwd))
    except HeadlessExit as e:
        code = e.code
    except KeyboardInterrupt:                                # SIGINT
        return hl.EXIT_SIGINT
    except Exception as e:                                   # 오류 → stderr JSON, 1
        log.debug("headless error", exc_info=True)
        sys.stderr.write(hl.format_error_output(hl.error_message(e)))
        return hl.EXIT_ERROR
    return hl.final_exit_code(code, False)
