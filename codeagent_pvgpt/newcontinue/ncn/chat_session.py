# ncn/chat_session.py — 대화형 세션 조립(ACP `--stdio` 와 콘솔 대화형 `ncn` 공용). cn extensions/cli/src/services/index.ts(초기화 순서)
#   + commands/chat.ts(대화형 경로: initializeServices headless=false) + ui/hooks/useChat.ts(세션·기록) + stream/streamChatResponse.ts(isHeadless=false)
# 대화형 cn 의 규칙: ask 정책 도구도 LLM 목록에 들어가고(Exit 없음), 시스템 메시지에 headless 블록이 없고, 기록에 세션 id 가 있다.
# 조립 순서(run_headless 와 같고 headless=False): 권한 → 설정 → 모델 → LLMClient → 세션 저장 관리자 → 기록 → 도구 목록·시스템 메시지
#   → Subagent 호스트 → 루프 의존성(LoopDeps) → 압축 훅. 화면(ACP·콘솔)마다 다른 것(승인 묻기·이벤트·중단 신호)은 콜백으로 받는다.
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄 결과 형식
import logging                                               # 로그
import os                                                    # 경로·환경변수
import pathlib                                               # 경로 객체
from dataclasses import dataclass                            # 자료 구조
from typing import Any, Callable, Optional                   # 타입

log = logging.getLogger("ncn.chat_session")                  # 로거(stdout 금지)


class SessionOpenError(Exception):                          # 세션 조립 실패(설정 오류·모델 없음) 예외
    """설정을 못 읽었거나 chat 모델이 없을 때 — message 는 cn 원문 오류 글."""

    def __init__(self, message: str):                       # 오류 글을 받는 생성자
        super().__init__(message)                           # 예외 글
        self.message = message                              # 화면·ACP 오류 응답에 쓸 글


@dataclass   # 자료 클래스로 선언
class ChatCore:                                             # 대화형 세션 하나의 공용 부품
    """조립된 부품 — 화면(ACP·콘솔)은 이것을 들고 턴을 돌린다. mcp 는 접속 뒤 채운다(도구 목록 함수가 매번 읽음)."""
    id: str                                                  # 세션 id(= cn sessionId, 세션 파일 이름)
    cwd: str                                                 # 작업 폴더(절대 경로)
    gdir: pathlib.Path                                       # 전역 폴더(~/.continue)
    cfg: Any                                                 # 읽은 설정(LoadedConfig)
    model: Any                                               # chat 모델
    llm: Any                                                 # LLMClient 항목
    perms: Any                                               # PermissionService(headless=False)
    history: Any                                             # ChatHistory(세션 id 있음)
    sessions: Any                                            # SessionManager(세션 파일 저장)
    deps: Any = None                                         # LoopDeps(조립 끝에 채움)
    mcp: Any = None                                          # MCPService(connect_mcp 뒤 채움)
    subagent_host: Any = None                                # SubagentHost(베타 Subagent 도구용)
    audit: Any = None                                        # [ncn E6] AuditLog(감사 기록 — 꺼져 있으면 None)

    @property   # 읽기 전용 속성
    def model_name(self) -> str:                             # 루프에 넘길 모델명(model.model — cn 그대로)
        """run_turn 의 model_name(사용량 기록의 model 값)."""
        return self.model.model or ""                        # 없으면 빈 글

    async def aclose(self) -> None:                          # LLM·MCP 정리
        """LLM 연결 닫기 → MCP 연결 닫기(stdio 자식 종료). 실패는 무시."""
        try:                                                # 닫기 실패 무시
            await self.llm.aclose()                          # LLM 클라이언트 닫기
        except Exception:                                    # 이미 닫힘 등
            pass                                            # 무시
        if self.mcp is not None:                             # MCP 연결이 있으면
            try:                                            # 정리 실패 무시
                await self.mcp.shutdown()                    # MCP 연결 정리
            except Exception:                                # 이미 닫힘 등
                pass                                        # 무시
        if self.audit is not None:                           # [ncn E6] 감사 기록이 있으면
            import asyncio                                   # [ncn E6] 스레드에서 닫기
            await asyncio.to_thread(self.audit.close)        # [ncn E6] 마지막 전송(최대 3초, 대화 루프를 막지 않게)


def initial_mode(args: argparse.Namespace) -> str:           # 시작 모드 계산 함수
    """--readonly → plan, --auto → auto, 아니면 normal(processCommandFlags)."""
    return "plan" if args.readonly else "auto" if args.auto else "normal"   # 플래그 → 모드 이름


def open_chat_core(args: argparse.Namespace, environ: Any, cwd: str, *,   # 대화형 세션 하나 조립 함수
                   select_session: Callable[[Any], tuple[str, list]],   # SessionManager → (세션 id, 처음 기록)
                   on_event: Callable[[dict], None],         # 루프 이벤트 받는 곳(화면)
                   ask_user: Any,                             # 승인 질문 콜백(AskUser)
                   get_signal: Callable[[], Any],             # 지금 턴의 중단 신호(하위 대화도 같이 멈춤)
                   check_permission: Optional[Callable[[Any, Any], Any]] = None,   # 실행 시점 판정(없으면 perms.check)
                   on_subagent_progress: Optional[Callable[[str, str, str], None]] = None) -> ChatCore:   # Subagent 진행 표시
    """권한 → 설정 → 모델 → LLMClient → 세션 저장 관리자 → 기록 → 루프 의존성. 실패하면 SessionOpenError(cn 오류 글).
    cwd 는 이미 절대 경로이고 지금 작업 폴더여야 한다(도구의 상대 경로 기준 = process.cwd)."""
    from ncn.app import make_validate_context                # 컨텍스트 검사(헤드리스와 같은 것)
    from ncn.compaction import make_compaction_hook          # 대화 압축(P3a)
    from ncn.config import paths as cpaths                   # 전역 폴더
    from ncn.config.loader import ConfigError, load_config   # 설정
    from ncn.llm.client import LLMClient                     # LLM 클라이언트
    from ncn.loop.agent import LoopDeps                      # 루프 의존성
    from ncn.loop.history import ChatHistory                 # 기록
    from ncn.permissions.service import PermissionService    # 권한
    from ncn.session import SessionManager                   # 세션 저장
    from ncn.subagent import SubagentHost, load_ncn_settings  # 하위 에이전트 호스트·ncn.yaml
    from ncn.sysmsg import build_system_message              # 시스템 메시지
    from ncn.tools import registry                           # 도구 목록

    a = args                                                 # 명령줄 결과
    cwd_p = pathlib.Path(cwd)                                # 경로 객체
    gdir = cpaths.global_dir(environ, cwd_p)                 # 전역 폴더
    if a.beta_subagent_tool:                                 # 베타 Subagent
        registry.set_beta_subagent_tool_enabled(True)       # 켜기
    from ncn.policy import PolicyError                       # [ncn E1] 관리자 정책 오류(깨진 파일·금지 모드)
    try:                                                    # [ncn E1] 정책 오류면 세션을 열지 않음(실행 거부)
        perms = PermissionService(mode=initial_mode(a), allow=a.allow, ask=a.ask, exclude=a.exclude,   # 권한(대화형 기본표)
                                  headless=False, global_dir=gdir)   # 헤드리스 아님
    except PolicyError as e:                                 # [ncn E1] 깨진 정책 파일·금지된 시작 모드
        raise SessionOpenError(e.message) from e             # 화면·ACP 가 오류로 보여 줌
    try:                                                    # 설정 읽기
        cfg = load_config(a.config, cwd=cwd_p, global_dir=gdir, environ=environ, headless=False,   # 대화형 설정 읽기
                          rules=a.rule, mcp=a.mcp, models=a.model, prompts=a.prompts)   # (인자 이어짐)
    except ConfigError as e:                                 # 설정 오류(cn 원문 글)
        raise SessionOpenError(e.message) from e             # 화면이 알아서 보여 줌
    if cfg.chat_model is None:                               # chat 모델 없음
        raise SessionOpenError(cfg.chat_model_error or "No models were found.")   # cn 원문 오류 글
    model = cfg.chat_model                                   # 채팅 모델
    llm = LLMClient(api_base=model.api_base, api_key=model.api_key, model=model.model,   # LLM 클라이언트
                    default_completion_options=model.raw.get("defaultCompletionOptions"),   # 기본 완료 옵션
                    request_options=model.request_options)   # 요청 옵션
    sm = SessionManager(environ, cwd)                        # 세션 저장 관리자
    sid, items = select_session(sm)                          # 새 세션·불러온 세션·--resume·--fork 는 화면이 고른다
    from ncn import backup                                   # [ncn E4] 쓰기 전 백업은 현재 세션 id 폴더로(/undo)
    backup.set_session_resolver(lambda: sm.get_current()["sessionId"])   # [ncn E4] 쓸 때마다 현재 세션 id
    history = ChatHistory(items, session_id=sid)             # 대화 기록(대화형은 세션 id 가 있다)
    core = ChatCore(id=sid, cwd=cwd, gdir=gdir, cfg=cfg, model=model, llm=llm, perms=perms,   # 부품 묶음
                    history=history, sessions=sm)            # (인자 이어짐)
    overrides = (model.chat_options or {}).get("toolOverrides")   # 모델별 도구 덮어쓰기

    def all_tools():                                         # getAllAvailableTools(false) — 매 반복 다시
        kw = dict(model_name=model.name or "", model=model.model, headless=False,   # 대화형 도구 목록(Exit 없음) 인자
                  mode=perms.mode, cwd=cwd_p, global_dir=gdir,   # (인자 이어짐)
                  subagent_models=cfg.models, subagent_host=core.subagent_host)   # Subagent(베타): 설정 models + 하위 대화 호스트
        if core.mcp is None:                                 # MCP 연결이 없으면
            return registry.get_builtin_tools(**kw)          # 내장 도구만
        builtin = frozenset(t.name for t in registry.get_builtin_tools(**kw))   # [ncn 부록B M3] 이 세션에 실제로 있는 내장 도구 이름
        return registry.get_builtin_tools(**kw, extra_tools=core.mcp.get_tools(builtin_names=builtin))   # MCP 도구(Skills 뒤, 내장과 겹치면 내장 우선)

    def system_message():                                    # getSystemMessage(currentMode) — 대화형(headless 블록 없음)
        return build_system_message(mode=perms.mode, headless=False, format_json=False, cwd=cwd_p,   # 현재 모드 기준
                                    global_dir=gdir, config=cfg, additional_rules=a.rule, environ=environ)   # (인자 이어짐)

    from ncn.audit import fan_out, make_audit                # [ncn E6] 감사 기록
    from ncn.loopguard import make_loop_guard, quiz_ask_continue   # [ncn E5] 반복 감시·묻기(AskQuestion 창구)
    ncn_settings = load_ncn_settings(gdir)                   # ~/.continue/ncn.yaml(한 번 읽어 같이 씀)
    core.audit = make_audit(gdir, environ, ncn_settings, ui="acp" if getattr(a, "stdio", False) else "interactive",   # [ncn E6] 기록기(꺼짐이면 None)
                            session_id=lambda: history.session_id or sid, mode=lambda: perms.mode)   # [ncn E6] 세션 id·모드는 매번 읽음
    guard = make_loop_guard(ncn_settings, global_dir=gdir, environ=environ, ask_continue=quiz_ask_continue,   # [ncn E5] 감시기(꺼짐이면 None)
                            on_decision=core.audit.loop_decision if core.audit else None)   # [ncn E5] 결정은 감사 기록에
    on_event = fan_out(on_event, guard.observe if guard else None, core.audit.on_event if core.audit else None)   # [ncn E5·E6] 화면 + 감시 + 감사
    if guard is not None:                                    # [ncn E5] 감시기가 있으면
        guard.on_event = on_event                            # [ncn E5] 멈춤 안내도 화면·감사로
    if core.audit is not None:                               # [ncn E6] 권한 판정·승인 선택 기록
        check_permission = core.audit.wrap_check(check_permission or (lambda tool, targs: perms.check(tool, targs)), perms)   # [ncn E6] 판정은 그대로, 기록만
        ask_user = core.audit.wrap_ask(ask_user)             # [ncn E6] 사용자 선택 기록
    core.subagent_host = SubagentHost(models=cfg.models, headless=False, tools=all_tools, system_message=system_message,   # [ncn 부록B B16] 세션마다 독립
                                      parent_history=history, get_signal=get_signal,   # 부모 기록·지금 턴 중단 신호(중단 → 하위도 중단)
                                      on_event=on_event,     # 하위 사용량을 이 턴 사용량에
                                      on_progress=on_subagent_progress,   # 진행 표시(화면마다)
                                      settings=ncn_settings)   # ~/.continue/ncn.yaml(추가 E)
    core.deps = LoopDeps(                                    # 루프 의존성
        llm=llm, tools=all_tools,                            # LLM·도구 목록 함수
        check_permission=check_permission or (lambda tool, targs: perms.check(tool, targs)),   # 실행 시점 판정(Bash 위험 판정 포함)
        list_permission=lambda tool, targs: perms.base_permission(tool.name, {}),   # 목록용(ask 도 대화형이면 포함)
        ask_user=ask_user,                                   # 승인 질문(화면마다)
        system_message=system_message, on_event=on_event,    # 시스템 메시지·이벤트
        validate_context=make_validate_context(model.raw),   # 컨텍스트 검사
        apply_tool_overrides=(lambda defs: registry.apply_chat_completion_tool_overrides(defs, overrides)),   # 도구 덮어쓰기
        loop_guard=guard,                                    # [ncn E5] 반복 상한·연속 실패 감시
    )                                                        # 끝
    core.deps.compaction_hook = make_compaction_hook(history, model.raw, on_compacted=sm.update_history)   # 압축 훅(압축 뒤 세션 저장)
    return core                                              # 부품 반환


async def connect_mcp(core: ChatCore, environ: Any, extra_servers: Optional[list] = None) -> None:   # 세션의 MCP 접속 함수
    """설정 mcpServers 뒤에 추가 서버(ACP session/new 의 것)를 이어 같은 연결 코드로 붙인다.
    대화형 cn 과 같이 실패해도 던지지 않는다(상태·로그). cn TUI 는 기다리지 않지만 ncn 은 기다린다(첫 질문부터 도구 보이게)."""
    from ncn.mcp.service import MCPService                   # 연결 관리
    servers = list(core.cfg.mcp_servers or []) + list(extra_servers or [])   # 설정 먼저, 그다음 추가
    core.mcp = MCPService(environ)                           # 세션마다 하나
    await core.mcp.initialize(servers, headless=False, wait=True)   # 병렬 접속(실패는 상태로)
    for c in core.mcp.connections.values():                  # 연결마다
        if c.status == "error":                              # 실패면
            log.warning("mcpServers: %s 접속 실패 — %s", c.name, c.error)   # 로그(stdout 금지)
