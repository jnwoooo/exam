# ncn/app.py — 명령줄 해석과 헤드리스(-p) 실행 조립. cn extensions/cli/src/index.ts(옵션·검증·stdin·프롬프트 확인)
#   + shared-options.ts(공통 옵션) + flags/flagValidator.ts·flagProcessor.ts + commands/chat.ts(chat·runHeadlessMode·processMessage)
#   + services/index.ts(초기화 순서) + util/cli.ts(hasSuppliedPrompt) + util/stdin.ts(readStdinSync) + util/promptProcessor.ts
# --stdio(ACP, VS 2022 확장용)는 run_stdio_mode → ncn/acp/server.py, 대화형(-p 없음)은 run_interactive_mode → ncn/interactive.py
#   (둘 다 대화형 cn 과 같은 동작 — 세션 조립은 ncn/chat_session.py 공용).
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


class CommanderError(Exception):                            # commander 형식 해석 오류 예외
    """commander 해석 오류 — 글은 commander 형식("error: …"), 종료 코드 1."""


class _Parser(argparse.ArgumentParser):                     # argparse 오류를 commander 글로 바꾸는 파서
    """argparse 오류를 commander 글로 바꾼다(종료 코드 2 → 1)."""

    def error(self, message: str):                           # argparse 가 부르는 오류 처리
        if "expected one argument" in message:               # 값 없는 옵션: "argument --config: expected one argument"
            opt = message.split(":", 1)[0].replace("argument", "", 1).strip().split("/")[-1]  # 오류 글에서 옵션 이름만 뽑음
            flag = _OPTION_SPECS.get(opt, opt)              # commander 표기(<path> 등)로 바꿈
            raise CommanderError(f"error: option '{flag}' argument missing")  # 값 없는 옵션 오류 글
        raise CommanderError(f"error: {message}")           # 그 밖의 오류 글


_OPTION_SPECS = {                                            # commander 표기(오류 글용)
    "--config": "--config <path>", "--org": "--org <slug>", "--rule": "--rule <rule>", "--mcp": "--mcp <slug>",  # 옵션별 commander 표기: config·org·rule·mcp
    "--model": "--model <slug>", "--prompt": "--prompt <prompt>", "--allow": "--allow <tool>",  # model·prompt·allow
    "--ask": "--ask <tool>", "--exclude": "--exclude <tool>", "--agent": "--agent <slug>",  # ask·exclude·agent
    "--format": "--format <format>", "--fork": "--fork <sessionId>",  # format·fork
}                                                           # 표 끝


def build_parser() -> argparse.ArgumentParser:              # 명령줄 파서를 만드는 함수
    """cn 루트 명령의 옵션(shared-options.ts addCommonOptions + index.ts) + ncn 전용(--stdio, --version)."""
    p = _Parser(prog="ncn", add_help=True,                  # prog 이름 ncn, 도움말 포함 파서 생성
                description="Continue CLI - AI-powered development assistant. Starts an interactive session by default, "  # 설명 글(cn 원문)
                            "use -p/--print for non-interactive output.")
    p.add_argument("prompt", nargs="?", help="Optional prompt to send to the assistant")   # [prompt] — 위치 인자: 선택적 프롬프트
    # ── 공통 옵션(shared-options.ts 순서) ──
    p.add_argument("--config", help="Configuration file for the assistant (can be a file path or hub slug)")  # --config: 설정 파일 경로 또는 허브 슬러그
    p.add_argument("--org", help="Organization slug to use for this session (supported only in headless mode)")  # --org: 조직 슬러그(헤드리스 전용)
    p.add_argument("--readonly", action="store_true", default=None, help="Start in plan mode (read-only tools)")  # --readonly: plan 모드(읽기 전용 도구)
    p.add_argument("--auto", action="store_true", default=None, help="Start in auto mode (all tools allowed)")  # --auto: auto 모드(모든 도구 허용)
    p.add_argument("--verbose", action="store_true", default=None, help="Enable verbose logging")  # --verbose: 상세 로그
    p.add_argument("--beta-status-tool", action="store_true", default=None, help="Enable beta status tool")  # --beta-status-tool: 베타 상태 도구
    p.add_argument("--rule", action="append", default=[], help="Add a rule (can be a file path, hub slug, or string content). Can be specified multiple times.")  # --rule: 규칙 추가(여러 번 가능)
    p.add_argument("--mcp", action="append", default=[], help="Add an MCP server from the hub (slug in format 'owner/package'). Can be specified multiple times.")  # --mcp: 허브 MCP 서버 추가(여러 번 가능)
    p.add_argument("--model", action="append", default=[], help="Add a model from the hub (slug in format 'owner/package'). Can be specified multiple times.")  # --model: 허브 모델 추가(여러 번 가능)
    p.add_argument("--prompt", dest="prompts", action="append", default=[], help="Add to the initial user message (can be a file path, hub slug, or string content). Can be specified multiple times.")  # --prompt: 첫 사용자 메시지에 덧붙임(여러 번 가능)
    p.add_argument("--allow", action="append", default=[], help="Allow specified tool (overrides default policies). Can be specified multiple times.")  # --allow: 도구 허용 지정
    p.add_argument("--ask", action="append", default=[], help="Ask for permission before using specified tool (overrides default policies). Can be specified multiple times.")  # --ask: 도구 실행 전 확인 지정
    p.add_argument("--exclude", action="append", default=[], help="Exclude specified tool from use (overrides default policies). Can be specified multiple times.")  # --exclude: 도구 제외 지정
    p.add_argument("--agent", help="Load agent file from the hub (slug in format 'owner/package')")  # --agent: 허브 에이전트 파일
    # ── 루트 명령 전용(index.ts) ──
    p.add_argument("-p", "--print", dest="print", action="store_true", default=None, help="Print response and exit (useful for pipes)")  # -p/--print: 헤드리스 실행 후 종료
    p.add_argument("--format", help="Output format for headless mode (json). Only works with -p/--print flag.")  # --format: 헤드리스 출력 형식(json)
    p.add_argument("--silent", action="store_true", default=None, help="Strip <think></think> tags and excess whitespace from output. Only works with -p/--print flag.")  # --silent: think 태그·여분 공백 제거
    p.add_argument("--resume", action="store_true", default=None, help="Resume from last session")  # --resume: 마지막 세션 이어서
    p.add_argument("--fork", help="Fork from an existing session ID")  # --fork: 기존 세션을 복제해 시작
    p.add_argument("--beta-subagent-tool", action="store_true", default=None, help="Enable beta Subagent tool for invoking subagents")  # --beta-subagent-tool: 베타 Subagent 도구
    # ── ncn 전용 ──
    p.add_argument("--stdio", action="store_true", help="ACP stdio 모드(P2) — VS 2022 확장용")  # --stdio: ACP 모드(ncn 전용, ncn/acp/server.py)
    p.add_argument("--doctor", action="store_true", help="설치·연결 점검(ncn 전용)")  # --doctor: 설정·서버·MCP·콘솔 점검(ncn/doctor.py)
    p.add_argument("-v", "--version", action="version", version=f"ncn {__version__}", help="Display version number")  # -v/--version: 버전 출력
    return p                                                # 파서 반환


def validate_flags(a: argparse.Namespace) -> list[str]:     # 플래그 조합 검사 함수(validateFlags)
    """flagValidator.ts validateFlags — 오류 글 목록(cn 원문, 검사 순서 그대로)."""
    errs: list[str] = []                                    # 오류 글 모음
    if a.format and not a.print:                             # validateFormatFlag — --format 은 -p 와 함께만
        errs.append("Error: --format flag can only be used with -p/--print flag")  # 오류 추가
    if a.silent and not a.print:                             # validateSilentFlag — --silent 는 -p 와 함께만
        errs.append("Error: --silent flag can only be used with -p/--print flag")  # 오류 추가
    if a.format and a.format != "json":                      # validateFormatValue — --format 값은 json 만
        errs.append("Error: --format currently only supports 'json'")  # 오류 추가
    if a.readonly and a.auto:                                # validateModeFlags — --readonly 와 --auto 는 동시에 못 씀
        errs.append("Error: Cannot use both --readonly and --auto flags together")  # 오류 추가
    if a.resume and a.fork:                                  # validateSessionFlags — --resume 과 --fork 는 동시에 못 씀
        errs.append("Error: Cannot use both --resume and --fork flags together")  # 오류 추가
    if a.fork is not None and (not a.fork or not hl.js_trim(a.fork)):  # --fork 값이 비면 오류
        errs.append("Error: --fork requires a session ID (e.g., --fork abc123)")  # 오류 추가
    if any(not hl.js_trim(t) for t in a.allow):              # validatePermissionFlags — --allow 값이 비면 오류
        errs.append("Error: --allow requires a tool name (e.g., --allow readFile)")  # 오류 추가
    if any(not hl.js_trim(t) for t in a.ask):               # --ask 값이 비면 오류
        errs.append("Error: --ask requires a tool name (e.g., --ask writeFile)")  # 오류 추가
    if any(not hl.js_trim(t) for t in a.exclude):           # --exclude 값이 비면 오류
        errs.append("Error: --exclude requires a tool name (e.g., --exclude Write)")  # 오류 추가
    return errs                                             # 오류 목록 반환


def validation_error_text(errs: list[str]) -> str:          # 검증 오류 출력 글 만드는 함수
    """handleValidationErrors — 글마다 한 줄, 2개 이상이면 개수 줄."""
    out = "".join(e + "\n" for e in errs)                   # 오류마다 한 줄
    if len(errs) > 1:                                       # 2개 이상이면
        out += f"\nFound {len(errs)} validation errors.\n"  # 개수 줄 추가
    return out                                              # 완성된 글 반환


PROMPT_REQUIRED = ("Error: A prompt is required when using the -p/--print flag, unless --prompt, --agent, or --resume is provided.\n\n"  # -p 인데 프롬프트가 없을 때의 안내 글
                   "Usage examples:\n"
                   '  cn -p "please review my current git diff"\n'
                   '  echo "hello" | cn -p\n'
                   '  cn -p "analyze the code in src/"\n'
                   "  cn -p --agent my-org/my-agent\n"
                   "  cn -p --prompt my-org/my-prompt\n"
                   "  cn -p --resume\n")                    # index.ts:279-291 원문(stderr)

_FLAGS_WITH_VALUES = {"--config", "--model", "--output", "--mode", "--workflow", "--agent", "-m", "-c", "-o"}   # cli.ts 원문 목록


def is_headless_mode(argv: list[str]) -> bool:              # 헤드리스 여부 함수
    """cli.ts isHeadlessMode — 인자에 -p 또는 --print."""
    return "-p" in argv or "--print" in argv                # -p 또는 --print 가 있으면 참


def has_supplied_prompt(argv: list[str]) -> bool:           # 프롬프트가 주어졌는지 확인하는 함수
    """cli.ts hasSuppliedPrompt — -p 뒤에 플래그가 아닌 인자가 있거나 --prompt 가 있으면 참(값 받는 플래그 목록은 cn 그대로)."""
    idx = next((i for i, x in enumerate(argv) if x in ("-p", "--print")), -1)  # -p 위치 찾기(없으면 -1)
    if idx == -1:                                           # -p 가 없으면
        return False                                        # 프롬프트 없음
    if "--prompt" in argv:                                  # --prompt 가 있으면
        return True                                         # 있음
    rest = argv[idx + 1:]                                   # -p 뒤의 인자들
    i = 0                                                   # 인덱스 초기화
    while i < len(rest):                                    # 인자를 차례로 훑음
        x = rest[i]                                         # 현재 인자
        if x in _FLAGS_WITH_VALUES:                          # 값까지 건너뜀
            i += 2                                          # 플래그와 그 값을 건너뜀
            continue                                        # 다음 인자로
        if x.startswith("-"):                                # 다른 플래그
            i += 1                                          # 플래그 하나 건너뜀
            continue                                        # 다음 인자로
        return True                                          # 프롬프트 발견
    return False                                            # 프롬프트 없음


def read_stdin_sync(argv: list[str], environ=os.environ) -> Optional[str]:  # 파이프 stdin 읽기 함수
    """stdin.ts readStdinSync — 파이프 입력이면 전부 읽어 trim. 단말·시험 환경·프롬프트가 있는 헤드리스면 None."""
    try:                                                    # 읽기 오류는 None 처리
        if (environ.get("NODE_ENV") == "test" or environ.get("VITEST") == "true"  # 시험 환경 표시 변수 확인(NODE_ENV·VITEST)
                or environ.get("JEST_WORKER_ID") is not None or environ.get("CONTINUE_CLI_TEST") == "true"):  # JEST·CONTINUE_CLI_TEST 도 확인
            return None                                     # 시험 환경이면 읽지 않음
        if is_headless_mode(argv) and has_supplied_prompt(argv):   # 프롬프트가 있으면 읽지 않음
            return None                                     # 읽지 않음
        if environ.get("FORCE_NO_TTY") == "true":           # FORCE_NO_TTY 면
            return None                                     # 읽지 않음
        if sys.stdin is None or sys.stdin.isatty():          # 단말
            return None                                     # 단말이면 읽지 않음
        data = sys.stdin.buffer.read().decode("utf-8", "replace")   # readFileSync(0, "utf8")  # stdin 전체를 UTF-8 로 읽음
        return hl.js_trim(data)                             # 공백 제거 후 반환
    except Exception:                                        # 못 읽으면 없는 것
        return None                                         # 실패하면 None


def prepend_prompt(prepend: Optional[str], original: Optional[str]) -> Optional[str]:  # 프롬프트 앞붙이기 함수
    """promptProcessor.ts prependPrompt — 둘 다 trim 해서 빈 줄로 잇고 다시 trim. 비면 None."""
    s = hl.js_trim(f"{hl.js_trim(prepend or '')}\n\n{hl.js_trim(original or '')}")  # 둘을 다듬어 빈 줄로 이어 붙이고 다시 다듬음
    return s or None                                        # 비면 None


def process_and_combine_prompts(additional: list[str], initial: Optional[str], cwd: str) -> Optional[str]:  # --prompt 들을 합치는 함수
    """promptProcessor.ts processAndCombinePrompts — --prompt 를 processRule 로 읽어 앞에 붙인다(실패는 경고만)."""
    from ncn.sysmsg import process_rule                      # 규칙 읽기(파일 또는 글)
    if not additional:                                      # 추가 프롬프트가 없으면
        return initial                                      # 첫 입력 그대로
    done = []                                               # 처리 결과 모음
    for spec in additional:                                 # --prompt 값마다
        try:                                                # 실패는 경고만 하고 건너뜀
            done.append(process_rule(spec, cwd))            # 파일이면 읽고 글이면 그대로
        except Exception as e:                               # logger.warn
            log.warning('Failed to process prompt "%s": %s', spec, e)  # 경고 기록
    if not done:                                            # 처리된 것이 없으면
        return initial                                      # 첫 입력 그대로
    combined = "\n\n".join(done)                            # 빈 줄로 이어 붙임
    return f"{combined}\n\n{initial}" if initial else combined  # 첫 입력이 있으면 뒤에 붙여 반환


# ─────────────────────────────────────────────────────────────────────────────
# 헤드리스 실행(commands/chat.ts runHeadlessMode)
# ─────────────────────────────────────────────────────────────────────────────


class HeadlessExit(Exception):                              # 종료 코드를 실어 올리는 예외
    """gracefulExit(code) 대신 — 위로 올려 종료 코드를 정한다."""

    def __init__(self, code: int):                          # 종료 코드를 받는 생성자
        super().__init__(code)                              # 예외 메시지로 코드 전달
        self.code = code                                     # 종료 코드


def _hard_exit(code: int) -> None:                          # Exit 도구용 즉시 종료 함수
    """Exit 도구용 process.exit — 출력 버퍼를 비우고 즉시 끝낸다(남은 작업·세션 저장 없음 — cn과 같음)."""
    try:                                                    # 버퍼를 비운 뒤 종료
        sys.stdout.flush()                                  # stdout 비움
        sys.stderr.flush()                                  # stderr 비움
    finally:
        os._exit(code)                                      # 정리 없이 즉시 프로세스 종료


def _prune_last_message(items: list[dict]) -> list[dict]:   # 마지막 메시지 잘라내기 함수
    """compaction.ts pruneLastMessage — 끝에서 두 번째가 도구 호출 assistant 또는 user 면 둘, 아니면 하나를 뺀다."""
    if not items:                                           # 기록이 비었으면
        return items                                        # 그대로 반환
    if len(items) == 1:                                     # 하나뿐이면
        return []                                           # 빈 목록
    second = items[-2].get("message", {})                   # 끝에서 두 번째 메시지
    if second.get("role") == "assistant" and len(second.get("toolCalls") or []) > 0:  # 도구 호출 assistant 면 둘 제거
        return items[:-2]                                   # 마지막 둘을 뺀 목록
    if second.get("role") == "user":                        # user 면 둘 제거
        return items[:-2]                                   # 마지막 둘을 뺀 목록
    return items[:-1]                                       # 그 밖에는 마지막 하나만 제거


def make_validate_context(model_raw: dict):                 # 컨텍스트 검증기 만드는 함수
    """streamChatResponse.ts processStreamingResponse 앞부분 — 한도를 넘으면 끝부터 잘라 맞추고, 그래도 넘으면 오류."""
    from ncn.tokenizer import validate_context_length       # 토큰 세기(cn 규칙)
    safety = 100                                             # SAFETY_BUFFER — 안전 여유 토큰

    def validate(items: list, system_message: str, tools: list) -> list:  # 검증 함수: 넘으면 잘라 맞춤
        v = validate_context_length(chat_history=items, model=model_raw, safety_buffer=safety,  # 한도 검사
                                    system_message=system_message, tools=tools)  # (인자 이어짐)
        while len(items) > 1 and not v["isValid"]:           # 잘라 가며 다시 검사
            pruned = _prune_last_message(items)             # 마지막 메시지를 잘라 봄
            if len(pruned) == len(items):                   # 더 줄일 수 없으면
                break                                       # 중단
            items = pruned                                  # 잘린 기록으로 교체
            v = validate_context_length(chat_history=items, model=model_raw, safety_buffer=safety,  # 다시 한도 검사
                                        system_message=system_message, tools=tools)  # (인자 이어짐)
        if not v["isValid"]:                                # 그래도 넘으면
            raise Exception(f"Context length validation failed: {v['error']}")  # 검증 실패 예외
        return items                                        # 맞춰진 기록 반환

    return validate                                         # 검증 함수 반환


async def run_headless(prompt: Optional[str], a: argparse.Namespace, *, cwd: pathlib.Path) -> int:  # 헤드리스 한 턴을 실행하는 비동기 함수
    """runHeadlessMode + processMessage(헤드리스 한 턴). 돌려주는 값: 종료 코드. 오류는 예외로 올린다(chat() 이 stderr JSON)."""
    from ncn.config import paths as cpaths                   # 전역 폴더
    from ncn.config.loader import ConfigError, load_config   # 설정
    from ncn.llm.client import LLMClient                     # LLM — LLM 클라이언트
    from ncn.loop import events as ev                        # 이벤트
    from ncn.loop.agent import LoopDeps, run_turn            # 루프
    from ncn.loop.history import ChatHistory, find_compaction_index   # 기록
    from ncn.permissions.service import PermissionService    # 권한
    from ncn.session import SessionManager, history_manager_load   # 세션
    from ncn.sysmsg import build_system_message              # 시스템 메시지
    from ncn.tools import exit as exit_tool                  # Exit 도구 종료 방법
    from ncn.tools import registry                           # 도구 목록

    environ = os.environ                                     # dotenv 적용된 process.env
    gdir = cpaths.global_dir(environ, cwd)                   # CONTINUE_GLOBAL_DIR — 전역 폴더 위치
    if a.beta_subagent_tool:                                 # initializeServices: 베타 도구
        registry.set_beta_subagent_tool_enabled(True)       # 베타 Subagent 도구 켜기
    # ANTHROPIC_API_KEY + --config 없음이면 cn은 config.yaml 을 만들어 덮어쓴다(services/index.ts:79-94) — ncn은 하지 않음(폐쇄망, 보고됨)

    # ── 권한(TOOL_PERMISSIONS) ── processCommandFlags → toolPermissionService.initialize
    mode = "plan" if a.readonly else "auto" if a.auto else None  # --readonly 면 plan, --auto 면 auto, 아니면 None
    perms = PermissionService(mode=mode or "normal", allow=a.allow, ask=a.ask, exclude=a.exclude,  # 권한 서비스 생성(모드·허용·확인·제외 목록)
                              headless=True, global_dir=gdir)  # 헤드리스·전역 폴더 전달

    # ── 설정(CONFIG) → 모델(MODEL) ──
    try:                                                    # 설정 읽기 실패는 일반 예외로 바꿈
        cfg = load_config(a.config, cwd=cwd, global_dir=gdir, environ=environ, headless=True,  # 설정 파일 읽기(옵션 값들을 함께 전달)
                          rules=a.rule, mcp=a.mcp, models=a.model, prompts=a.prompts)  # (인자 이어짐)
    except ConfigError as e:                                # 설정 오류
        raise Exception(e.message) from e                   # 오류 글로 예외를 올림(chat() 이 stderr JSON 으로 출력)
    if cfg.chat_model is None:                               # getLlmApi 실패 글
        raise Exception(cfg.chat_model_error or "No models were found.")  # chat 모델이 없으면 오류
    model = cfg.chat_model                                  # 채팅 모델
    llm = LLMClient(api_base=model.api_base, api_key=model.api_key, model=model.model,  # LLM 클라이언트 생성
                    default_completion_options=model.raw.get("defaultCompletionOptions"),  # 기본 완료 옵션
                    request_options=model.request_options)  # 요청 옵션

    # ── MCP(services/MCPService.ts) — 헤드리스는 다 붙을 때까지 기다리고, 하나라도 실패하면 오류(chat() 이 stderr JSON) ──
    from ncn.mcp.service import MCPService                   # MCP 연결 관리
    mcp = MCPService(environ)                                # process.env 를 stdio 자식에게
    try:                                                    # 실패하면 연결·LLM 정리 후 오류를 올림
        await mcp.initialize(cfg.mcp_servers, headless=True, has_agent_file=bool(a.agent))   # 병렬 접속
    except BaseException:                                   # 접속 실패·중단
        await mcp.shutdown()                                # 붙은 서버 정리
        await llm.aclose()                                  # LLM 클라이언트 닫기
        raise                                               # 위로(오류 글 그대로)

    # ── 대화 기록·세션(--resume / --fork) ──
    sessions = SessionManager(environ, os.fspath(cwd))       # SessionManager 싱글턴 자리
    from ncn import backup                                   # [ncn E4] 헤드리스는 백업만(--resume 한 대화형·ACP 에서 /undo)
    backup.set_session_resolver(lambda: sessions.get_current()["sessionId"])   # [ncn E4] 세션 파일과 같은 id 폴더
    initial_items: list = []                                 # initializeChatHistory — 초기 기록 목록
    if a.fork:                                               # --fork: 그 기록으로 새 세션(못 찾아도 빈 기록 — cn 그대로)
        forked = history_manager_load(a.fork, environ)      # --fork: 그 id 의 세션 기록 불러오기
        initial_items = sessions.start_new(forked.get("history") or [])["history"]  # 새 세션을 시작하고 기록 가져옴
    elif a.resume:                                           # --resume: 가장 최근 세션
        s = sessions.load_latest()                          # --resume: 가장 최근 세션 불러오기
        initial_items = s.get("history") or [] if s else []  # 기록 가져옴(없으면 빈 목록)
    history = ChatHistory()                                  # 헤드리스: sessionId "" (Subagent 실패 재현 — P0 0장 3번)
    if a.resume or a.fork:                                  # 이어받기·복제면
        history.set_history(initial_items)                  # 기록을 채워 넣음
        _ = find_compaction_index(initial_items)             # 압축 위치(루프가 다시 읽으므로 결과 같음)

    # ── 첫 사용자 입력 ──
    initial = process_and_combine_prompts(a.prompts, prepend_prompt(None, prompt), os.fspath(cwd))  # 첫 사용자 입력(--prompt 와 합침)
    if not initial or not hl.js_trim(initial):               # 프롬프트 없음
        if a.resume or a.fork:                               # 기록만 불러오고 0으로 끝
            await llm.aclose()                              # 리소스 정리
            await mcp.shutdown()                            # MCP 연결 정리
            return hl.EXIT_OK                               # 정상 종료 코드
        await mcp.shutdown()                                # MCP 연결 정리
        raise Exception(hl.HEADLESS_REQUIRES_PROMPT)        # 프롬프트가 없으면 오류

    # ── 루프 의존성 ──
    model_name = model.model or ""                           # 루프의 model_name = model.model(사용량 기록의 model 값 — cn 그대로)
    overrides = (model.chat_options or {}).get("toolOverrides")   # model.chatOptions.toolOverrides — 모델의 도구 덮어쓰기 설정

    def all_tools():                                         # getAllAvailableTools(true) — 매 반복 다시(덮어쓰기 없음: 실행용 조회)
        kw = dict(model_name=model.name or "", model=model.model, headless=True,   # 현재 모드의 내장 도구 목록 조회 인자
                  mode=perms.mode, cwd=cwd, global_dir=gdir,   # (인자 이어짐)
                  subagent_models=cfg.models, subagent_host=subagent_host)   # Subagent(베타): 설정 models + 하위 대화 호스트
        builtin = frozenset(t.name for t in registry.get_builtin_tools(**kw))   # [ncn 부록B M3] 이 세션에 실제로 있는 내장 도구 이름
        return registry.get_builtin_tools(**kw, extra_tools=mcp.get_tools(builtin_names=builtin))   # MCP 도구(Skills 뒤, 내장과 겹치면 내장 우선)

    def system_message():                                    # services.systemMessage.getSystemMessage(currentMode) — 시스템 메시지를 만드는 함수
        return build_system_message(mode=perms.mode, headless=True, format_json=a.format == "json", cwd=cwd,  # 현재 모드 기준으로 조립
                                    global_dir=gdir, config=cfg, additional_rules=a.rule, environ=environ)  # (인자 이어짐)

    def on_event(e: dict) -> None:                           # 사용량 → 세션 저장(trackSessionUsage)
        if e.get("type") == "usage" and isinstance(e.get("usage"), dict):  # 사용량 이벤트면
            sessions.track_usage(e["usage"])                # 세션에 사용량 누적·저장

    from ncn.llm.client import AbortSignal                   # 턴 중단 신호(하위 대화와 공유)
    from ncn.subagent import SubagentHost, load_ncn_settings  # 하위 에이전트 호스트·ncn.yaml
    turn_signal = AbortSignal()                              # 이 턴의 중단 신호(run_turn 에 넘김 — 없을 때 루프가 만드는 것과 같음)
    from ncn.audit import fan_out, make_audit                # [ncn E6] 감사 기록
    from ncn.loopguard import make_loop_guard                # [ncn E5] 반복 감시(헤드리스는 묻지 않고 멈춤)
    ncn_settings = load_ncn_settings(gdir)                   # ~/.continue/ncn.yaml(한 번 읽어 같이 씀)
    audit = make_audit(gdir, environ, ncn_settings, ui="headless",   # [ncn E6] 기록기(꺼짐이면 None)
                       session_id=lambda: sessions.get_current()["sessionId"], mode=lambda: perms.mode)   # [ncn E6] 세션 id(저장 파일 이름과 같음)·모드
    guard = make_loop_guard(ncn_settings, global_dir=gdir, environ=environ,   # [ncn E5] 감시기(꺼짐이면 None)
                            on_decision=audit.loop_decision if audit else None)   # [ncn E5] 결정은 감사 기록에
    on_event = fan_out(on_event, guard.observe if guard else None, audit.on_event if audit else None)   # [ncn E5·E6] 세션 사용량 + 감시 + 감사
    check = (lambda tool, args: perms.check(tool, args))     # 실행 시점(동적 평가 포함)
    if audit is not None:                                    # [ncn E6] 판정 기록
        check = audit.wrap_check(check, perms)               # [ncn E6] 판정은 그대로, 기록만
    subagent_host = SubagentHost(models=cfg.models, headless=True, tools=all_tools, system_message=system_message,  # [ncn 부록B B16] 하위 대화를 독립 객체로
                                 parent_history=history, get_signal=lambda: turn_signal, on_event=on_event,  # 부모 기록(세션 id "")·중단·사용량
                                 settings=ncn_settings)      # ~/.continue/ncn.yaml(추가 E)

    deps = LoopDeps(                                        # 루프 의존성 묶음 생성
        llm=llm, tools=all_tools,                           # LLM·도구 목록 함수
        check_permission=check,                              # 실행 시점(동적 평가 포함 — 감사 기록 포장)
        list_permission=lambda tool, args: perms.base_permission(tool.name, {}),   # 목록용(정적 정책, 인자 {})
        ask_user=None,                                       # 헤드리스는 묻지 않음
        loop_guard=guard,                                    # [ncn E5] 반복 상한·연속 실패 → 턴 끝
        system_message=system_message, on_event=on_event,   # 시스템 메시지·이벤트 콜백
        validate_context=make_validate_context(model.raw),  # 컨텍스트 검증기
        apply_tool_overrides=(lambda defs: registry.apply_chat_completion_tool_overrides(defs, overrides)),  # 모델별 도구 설정 덮어쓰기 적용
    )                                                       # (괄호 닫기)
    from ncn.compaction import make_compaction_hook          # 대화 압축(P3a)
    deps.compaction_hook = make_compaction_hook(history, model.raw, on_compacted=sessions.update_history)   # 압축 훅(압축 뒤 세션 저장)
    exit_tool.EXIT_HANDLER = _hard_exit                      # Exit 도구 = process.exit(1)

    try:                                                    # 한 턴 실행 중 어떤 경우에도 LLM 을 닫는다
        result = await run_turn(history, initial, deps, headless=True, model_name=model_name, signal=turn_signal)   # processMessage — 루프 한 턴 실행
        if result.manual_compaction is not None:             # /compact(P3) — 결과 줄
            sys.stdout.write(hl.format_manual_compaction_line(history_length=len(history.get_history())))  # /compact 결과 줄 출력
        out = hl.format_headless_output(result.response, silent=bool(a.silent), format=a.format)   # processAndOutputResponse — 출력할 최종 글 만들기
        if out is not None:                                 # 출력할 것이 있으면
            sys.stdout.write(out)                           # stdout 에 씀
            sys.stdout.flush()                              # 바로 내보냄
        sessions.update_history(history.get_history())       # updateSessionHistory — 세션에 기록 저장
    finally:                                                # (finally)
        await llm.aclose()                                  # LLM 클라이언트 닫기
        await mcp.shutdown()                                # MCP 연결 정리(cn cleanup — stdio 자식 종료)
        if audit is not None:                               # [ncn E6] 감사 기록이 있으면
            await asyncio.to_thread(audit.close)            # [ncn E6] 마지막 전송(최대 3초 — 못 보내면 outbox 에 남음)
    if result.stopped_by_loop_guard:                         # [ncn E5] 반복 감시로 멈춤
        from ncn.loopguard import EXIT_LOOP_GUARD, stop_message   # [ncn E5] 종료 코드·안내 글
        sys.stderr.write(stop_message(result.stopped_by_loop_guard) + "\n")   # [ncn E5] stderr 안내(stdout 은 응답만)
        return EXIT_LOOP_GUARD                               # [ncn E5] 종료 코드 3
    return hl.EXIT_OK                                        # gracefulExit(0)


# ─────────────────────────────────────────────────────────────────────────────
# 진입점(index.ts action + chat())
# ─────────────────────────────────────────────────────────────────────────────


def _configure_logging(verbose: bool) -> None:              # 로깅 설정 함수
    """cn 헤드리스는 콘솔 출력을 막고 파일 로그만 쓴다. ncn: --verbose 면 stderr DEBUG, 아니면 아무 데도 안 찍음(stdout 금지)."""
    root = logging.getLogger()                              # 루트 로거
    if verbose:                                             # --verbose 면
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")  # stderr 에 DEBUG 로그
    else:                                                   # 아니면
        root.addHandler(logging.NullHandler())               # 마지막 수단 처리기(stderr 경고)도 막음
        root.setLevel(logging.CRITICAL + 1)                 # 모든 로그 차단


def _stdout_utf8() -> None:                                 # 출력 인코딩 설정 함수
    """stdout/stderr 를 UTF-8·줄바꿈 변환 없이(Windows cp949·\\r\\n 방지 — 계획 14-2 V5)."""
    for s in (sys.stdout, sys.stderr):                      # stdout·stderr 둘 다
        try:                                                # 재설정 실패는 무시
            s.reconfigure(encoding="utf-8", newline="\n")    # type: ignore[attr-defined] — UTF-8, 줄바꿈 변환 없음
        except Exception:                                   # 재설정 불가 스트림
            pass                                            # 무시


def run_stdio_mode(a: argparse.Namespace) -> int:           # --stdio(ACP) 진입 함수
    """ncn --stdio — ACP 에이전트(ncn/acp/server.py). -p 전용 옵션(--format·--silent)·미구현 옵션은 stderr 안내 후 종료.
    stdout 확보는 server.run_stdio 가 가장 먼저 한다(그 전에는 stdout 에 아무것도 쓰지 않는다)."""
    errs = [e for e in validate_flags(a) if "--format" not in e and "--silent" not in e]   # 모드·권한 플래그 검증(-p 전용 것은 아래에서)
    if a.print or a.format or a.silent:                      # -p 계열과 함께 쓸 수 없음
        errs.append("Error: --stdio cannot be used with -p/--print, --format or --silent")   # ncn 전용 오류 글
    if errs:                                                # 오류가 있으면
        sys.stderr.write(validation_error_text(errs))       # stderr 로 안내
        return 1                                            # 종료 코드 1
    pending = [name for name, v in (("--org", a.org), ("--agent", a.agent)) if v]   # P3 이후(Hub). --mcp 는 설정 읽기가 처리(URL 은 바로 연결)
    if pending:                                             # 미지원 옵션이 있으면
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=", ".join(pending)) + "\n")  # 미구현 안내
        return 2                                            # 종료 코드 2
    from ncn.acp.server import run_stdio                    # ACP 서버(지연 import — 헤드리스 시작 시간 보호)
    return run_stdio(a)                                     # 연결이 끝날 때까지 실행


def run_interactive_mode(a: argparse.Namespace) -> int:     # 대화형(인자 없이 `ncn`, `ncn "첫 메시지"`) 진입 함수
    """cn index.ts action(대화형 경로) — 플래그 검증 → 미구현 옵션 안내 → ncn/interactive.py(세션 조립은 ACP 와 공용)."""
    _configure_logging(bool(a.verbose))                      # 로그(--verbose 면 stderr, 아니면 끔 — 입력 줄을 어지럽히지 않게)
    errs = validate_flags(a)                                 # validateFlags(대화형에서도 같은 검사 — --format·--silent 는 -p 전용 오류)
    if errs:                                                # 오류가 있으면
        sys.stderr.write(validation_error_text(errs))       # 오류 글 출력
        return 1                                            # 종료 코드 1
    pending = [name for name, v in (("--org", a.org), ("--agent", a.agent)) if v]   # Hub 옵션(미구현)
    if pending:                                             # 미지원 옵션이 있으면
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=", ".join(pending)) + "\n")  # 미구현 안내
        return 2                                            # 종료 코드 2
    from ncn.interactive import run_interactive             # 대화형(지연 import — 헤드리스 시작 시간 보호)
    return run_interactive(a, a.prompt)                     # 종료 코드


def main(argv: Optional[list[str]] = None) -> int:          # 프로그램 진입 함수
    """명령줄을 해석해 모드를 고른다. 헤드리스(-p)·--stdio(ACP)·대화형(P2b) 실행, 하위 명령·Hub 옵션은 미구현 안내 후 2."""
    argv = list(sys.argv[1:] if argv is None else argv)      # process.argv.slice(2) — 인자 목록(없으면 실제 명령줄)
    _stdout_utf8()                                          # 출력 인코딩 설정
    cwd = pathlib.Path(os.getcwd())                          # process.cwd()
    from ncn.config.env import apply_dotenv                  # dotenv.config() — 모듈 로드 시점(가장 먼저)
    apply_dotenv(os.environ, os.fspath(cwd))                # <cwd>/.env 를 환경변수에 반영

    if "--doctor" in argv:                                   # [ncn 추가] 설치·연결 점검은 다른 모드보다 먼저
        from ncn.doctor import main as doctor_main           # 점검 모듈(--config·--format json 등은 직접 해석)
        return doctor_main(argv)                             # 실패 있으면 1, 없으면 0, 옵션 오류 2
    if argv and argv[0] in SUBCOMMANDS:                      # cn 하위 명령
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=f"'{argv[0]}' 하위 명령") + "\n")  # 하위 명령은 미구현 안내
        return 2                                            # 종료 코드 2
    try:                                                    # 명령줄 해석 시도
        a, extra = build_parser().parse_known_args(argv)     # 알 수 없는 것은 따로
        if extra:                                            # commander: 알 수 없는 옵션 / 인자 초과
            opt = next((x for x in extra if x.startswith("-")), None)  # 알 수 없는 옵션 찾기
            if opt is not None:                             # 옵션이면
                raise CommanderError(f"error: unknown option '{opt}'")  # 알 수 없는 옵션 오류
            raise CommanderError(f"error: too many arguments. Expected 1 argument but got {1 + len(extra)}.")  # 인자 초과 오류
    except CommanderError as e:                              # cn은 -p 가 있으면 init.ts 가 stdout/stderr 를 막아 이 글도 안 보인다
        if not is_headless_mode(argv):                      # 헤드리스가 아니면
            sys.stderr.write(str(e) + "\n")                 # 오류 글 출력
        return 1                                            # 종료 코드 1

    if a.stdio:                                              # ACP(P2) — --stdio 면 대화형 cn 과 같은 동작을 ACP 로
        return run_stdio_mode(a)                             # stdout 은 프로토콜 전용(V5)
    if not a.print:                                          # 대화형(P2b) — 콘솔 입력 줄(ncn/interactive.py)
        return run_interactive_mode(a)                       # 종료할 때까지 대화

    _configure_logging(bool(a.verbose))                      # configureConsoleForHeadless — 로깅 설정
    errs = validate_flags(a)                                 # validateFlags — 플래그 검증
    if errs:                                                # 오류가 있으면
        sys.stderr.write(validation_error_text(errs))       # 오류 글 출력
        return 1                                            # 종료 코드 1

    pending = [name for name, v in (("--org", a.org), ("--agent", a.agent)) if v]   # P2 이후(Hub). --mcp 는 설정 읽기가 처리(URL 은 바로 연결)
    if pending:                                             # 미지원 옵션이 있으면
        sys.stderr.write(NOT_IMPLEMENTED.format(ver=__version__, what=", ".join(pending)) + "\n")  # 미구현 안내
        return 2                                            # 종료 코드 2

    prompt = a.prompt                                        # 헤드리스 stdin(프롬프트가 없을 때만 읽힘)
    stdin_input = read_stdin_sync(argv)                     # 파이프 입력 읽기
    if stdin_input:                                         # 입력이 있으면
        prompt = f"<stdin>\n{stdin_input}\n</stdin>\n\n{prompt}" if prompt else stdin_input  # <stdin> 으로 감싸 프롬프트 앞에 붙임
    if not prompt and not a.agent and not a.resume:          # 프롬프트 필수
        sys.stderr.write(PROMPT_REQUIRED)                   # 프롬프트 필수 안내 출력
        return 1                                            # 종료 코드 1

    try:                                                     # chat(): 헤드리스 경로
        code = asyncio.run(run_headless(prompt, a, cwd=cwd))  # 헤드리스 실행
    except HeadlessExit as e:
        code = e.code                                       # 종료 코드 가져옴
    except KeyboardInterrupt:                                # SIGINT
        return hl.EXIT_SIGINT                               # Ctrl+C 종료 코드
    except Exception as e:                                   # 오류 → stderr JSON, 1
        log.debug("headless error", exc_info=True)          # 오류 기록
        sys.stderr.write(hl.format_error_output(hl.error_message(e)))  # 오류 JSON 을 stderr 에 출력
        return hl.EXIT_ERROR                                # 오류 종료 코드
    return hl.final_exit_code(code, False)                  # 최종 종료 코드 반환
