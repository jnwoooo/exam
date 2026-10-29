# ncn/acp/server.py — `ncn --stdio`: ACP(Agent Client Protocol) 에이전트. 계획 14장(V2~V8, 14-3 대응표)·P0_결과.md 2장
# 세션 조립은 ncn/chat_session.py(콘솔 대화형 ncn 과 공용). 동작 기준은 대화형 cn(헤드리스 아님): cn extensions/cli/src/ui/hooks/useChat.ts(processMessage·handleToolPermissionResponse·
#   handleInterrupt) + useChat.helpers.ts(formatMessageWithFiles) + stream/streamChatResponse.ts(같은 루프, isHeadless=false)
#   + services/ToolPermissionService.ts(모드 전환) + services/QuizService.ts(AskQuestion 답 받기)
# 대화형이므로: ask 정책 도구도 LLM 목록에 들어가고(Exit 없음), 시스템 메시지에 headless 블록이 없고, 승인은 IDE 에 묻는다.
#   승인 3가지(ToolPermissionSelector.tsx) = allow_once(계속) / allow_always(계속+다시 묻지 않기: permissions.yaml 규칙 추가)
#   / reject_once(아니오: 거부 글 "Permission denied by user" + 응답 중단 + 기록에 system "[Tool canceled - …]").
# 메시지 흐름: initialize → session/new|load → session/prompt(→ session/update 들, session/request_permission, elicitation/create)
#   → PromptResponse(stopReason). session/cancel 은 진행 중 LLM 스트림·승인·질문·도구를 멈추고 stopReason "cancelled".
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄 결과 형식
import asyncio                                               # 비동기
import contextvars                                           # 지금 턴의 세션(질문 처리기가 찾음)
import logging                                               # 로그(stderr)
import os                                                    # 작업 폴더·환경변수
import pathlib                                               # 경로
import sys                                                   # 표준 입출력
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Optional                             # 타입

from acp import PROTOCOL_VERSION, RequestError               # 규격 버전·JSON-RPC 오류
from acp.helpers import (start_tool_call, update_agent_message_text, update_agent_thought_text, update_current_mode, update_plan,  # 업데이트 생성 도우미
                         update_tool_call, update_user_message_text)   # 도구 갱신·사용자 글
from acp.schema import (AgentCapabilities, ConfigOptionUpdate, ElicitationFormSessionMode, ElicitationSchema,  # 능력·설정 갱신·질문 형식
                        Implementation, InitializeResponse, LoadSessionResponse, NewSessionResponse,  # 초기화·세션 응답
                        McpCapabilities, PromptCapabilities, PromptResponse, SetSessionConfigOptionResponse, SetSessionModeResponse,  # MCP·질문 능력·응답들
                        ToolCallUpdate)                      # 승인 요청의 도구 내용

from ncn import __version__                                  # 버전
from ncn import fsio                                         # 파일 창구(IDE 위임)
from ncn.acp import convert                                  # 형식 변환

log = logging.getLogger("ncn.acp")                           # 로거(stderr)

_CURRENT: contextvars.ContextVar[Optional["AcpSession"]] = contextvars.ContextVar("ncn_acp_session", default=None)   # 지금 턴의 세션
CANCEL_GRACE_S = 1.0                                         # 취소 뒤 턴이 스스로 끝나기를 기다리는 시간(초) — 넘으면 작업 취소
SUBAGENT_PROGRESS_INTERVAL_S = 0.1                           # Subagent 진행 표시 최소 간격(초)


@dataclass   # 자료 클래스로 선언
class _Card:                                                # 도구 카드 하나(ACP tool_call)의 상태
    """화면에 보낸 도구 카드 — 이름·인자·제목·종류 + diff 계산용 자료."""
    name: str                                                # 도구 이름
    args: Any                                                # LLM 이 준 인자
    title: str                                               # 카드 제목(ToolCallTitle)
    kind: str                                                # ACP kind 항목
    pre_args: Any = None                                     # 사전 처리로 고쳐진 인자(Edit oldContent·newContent 등)
    old_text: Optional[str] = None                           # Write: 쓰기 전 내용(fs 위임이면 IDE 버퍼, 아니면 디스크. 새 파일이면 None)
    write_path: Optional[str] = None                         # Write: 대상 절대 경로
    old_read: bool = False                                   # Write: 원래 내용을 읽었나


@dataclass   # 자료 클래스로 선언
class AcpSession:                                           # ACP 세션 하나 = cn 대화 하나
    """세션 상태: 작업 폴더·설정·모델·권한·기록·세션 저장·진행 중 턴."""
    id: str                                                  # 세션 id(= cn sessionId, 세션 파일 이름)
    cwd: str                                                 # 작업 폴더(절대 경로)
    gdir: pathlib.Path                                       # 전역 폴더(~/.continue)
    cfg: Any                                                 # 읽은 설정(LoadedConfig)
    model: Any                                               # chat 모델
    llm: Any                                                 # LLMClient 항목
    perms: Any                                               # PermissionService(headless=False) 항목
    history: Any                                             # ChatHistory 항목
    sessions: Any                                            # SessionManager(세션 파일 저장)
    deps: Any = None                                         # LoopDeps(만든 뒤 채움)
    files: Any = None                                        # fsio 구현(IDE 위임 또는 디스크)
    signal: Any = None                                       # 지금 턴의 AbortSignal
    task: Optional[asyncio.Task] = None                      # 지금 턴의 작업
    cancel_requested: bool = False                           # session/cancel 을 받았나
    cancel_ev: Any = None                                    # session/cancel 이벤트(승인·질문 대기를 푼다 — 3번 거부의 중단 신호와 구분)
    rejected: bool = False                                   # 승인 3번(아니오)으로 멈췄나
    first_prompt: bool = True                                # 첫 질문인가(--prompt 를 앞에 붙임)
    turn_usage: Optional[dict] = None                        # 이번 턴 사용량 누적
    cards: dict = field(default_factory=dict)                # 도구 카드(호출 id → _Card)
    last_ask_id: Optional[str] = None                        # 마지막 AskQuestion 카드 id(질문을 카드에 잇기)
    mcp: Any = None                                          # MCPService(설정 mcpServers + session/new 의 mcpServers)
    progress_sent: dict = field(default_factory=dict)        # Subagent 진행 표시를 마지막으로 보낸 시각(호출 id → 초)
    progress_latest: dict = field(default_factory=dict)      # 아직 안 보낸 최근 진행 출력(호출 id → (이름, 글))
    progress_timers: dict = field(default_factory=dict)      # 예약된 진행 표시(호출 id → TimerHandle)
    core: Any = None                                         # 공용 부품(ncn/chat_session.py ChatCore — 도구 목록이 core.mcp 를 읽음)


class NcnAgent:                                             # acp Agent 구현
    """ACP 에이전트 — 요청 처리기(initialize·session/*)와 루프 콜백(on_event·ask_user·질문 처리기)."""

    def __init__(self, args: argparse.Namespace, environ: Optional[dict] = None):   # 생성자: 명령줄 결과·환경변수
        self.args = args                                     # --config·--allow 등(대화형 cn 과 같은 옵션)
        self.environ = os.environ if environ is None else environ   # 환경변수(dotenv 적용됨)
        self.conn: Any = None                                # AgentSideConnection(on_connect 에서 받음)
        self.client_caps: Any = None                         # 클라이언트 능력(initialize)
        self.sessions: dict[str, AcpSession] = {}            # 세션 id → 세션
        self._outbox: Optional[asyncio.Queue] = None          # 보낼 알림(순서 보장)
        self._sender: Optional[asyncio.Task] = None           # 알림 보내기 작업
        from ncn.tools.ask_question import quiz_service      # AskQuestion 질문 서비스(모듈 공용)
        self._quiz = quiz_service                            # 보관
        self._quiz.on_question_requested.append(self._on_question)   # 질문이 걸리면 IDE 에 묻는다

    # ───────── 연결·알림 보내기 ─────────
    def on_connect(self, conn: Any) -> None:                 # SDK 가 연결 객체를 넘겨줌
        """AgentSideConnection(클라이언트에 요청·알림을 보내는 쪽)."""
        self.conn = conn                                     # 보관

    def _enqueue(self, factory) -> None:                     # 알림 하나를 순서대로 보내도록 줄 세움
        """루프 이벤트는 동기 콜백이라 바로 await 할 수 없다 → 큐에 넣고 보내기 작업이 차례로 보낸다."""
        if self._outbox is None:                             # 처음이면
            self._outbox = asyncio.Queue()                   # 큐 생성
            self._sender = asyncio.ensure_future(self._send_loop())   # 보내기 작업 시작
        self._outbox.put_nowait(factory)                     # 줄 세움

    async def _send_loop(self) -> None:                      # 큐를 비우며 보냄
        """큐의 알림을 넣은 순서대로 보낸다. 하나가 실패해도 다음 것은 보낸다."""
        assert self._outbox is not None                      # 큐가 있어야 함
        while True:                                         # 끝없이(프로세스 끝에 취소됨)
            factory = await self._outbox.get()               # 다음 알림
            try:                                            # 보내기 실패는 기록만
                await factory()                              # 보냄
            except Exception as e:                           # 연결 끊김 등
                log.debug("알림 보내기 실패: %s", e)          # 기록
            finally:                                        # 끝 표시
                self._outbox.task_done()                     # join 을 깨우기 위해

    async def flush(self) -> None:                           # 줄 선 알림이 다 나갈 때까지 기다림
        """승인 요청·응답 전에 부른다 — 도구 카드가 승인 요청보다 먼저 도착하게."""
        if self._outbox is not None:                         # 큐가 있으면
            await self._outbox.join()                        # 다 보낼 때까지

    def _update(self, s: AcpSession, update: Any) -> None:  # session/update 하나 줄 세움
        """session/update 알림."""
        self._enqueue(lambda: self.conn.session_update(session_id=s.id, update=update))   # 보낼 함수

    def _ext(self, method: str, params: dict) -> None:       # ncn 확장 알림 줄 세움
        """_ncn/… 확장 알림(SDK 가 앞에 '_' 를 붙인다)."""
        self._enqueue(lambda: self.conn.ext_notification(method, params))   # 보낼 함수

    async def aclose(self) -> None:                          # 끝 정리
        """보내기 작업 멈추고 LLM 연결 닫기."""
        if self._sender is not None:                         # 보내기 작업이 있으면
            self._sender.cancel()                            # 멈춤
        for s in self.sessions.values():                     # 세션마다
            try:                                            # 닫기 실패 무시
                await s.llm.aclose()                         # LLM 클라이언트 닫기
                if s.mcp is not None:                        # MCP 연결이 있으면
                    await s.mcp.shutdown()                   # 닫기(stdio 자식 종료)
            except Exception:                                # 이미 닫힘 등
                pass                                        # 무시
        try:                                                # 질문 처리기 해제
            self._quiz.on_question_requested.remove(self._on_question)   # 등록 해제
        except ValueError:                                   # 이미 없음
            pass                                            # 무시

    # ───────── initialize ─────────
    async def initialize(self, protocol_version: int, client_capabilities: Any = None, client_info: Any = None,  # initialize 요청
                         **kwargs: Any) -> InitializeResponse:   # (인자 이어짐)
        """규격 버전 1, 세션 불러오기 지원, 질문 능력: 내장 리소스(embeddedContext)만(그림·소리 없음)."""
        self.client_caps = client_capabilities               # 클라이언트 능력 보관(fs·elicitation·_meta)
        log.debug("initialize: client=%s version=%s caps=%s", client_info, protocol_version, client_capabilities)   # 기록
        return InitializeResponse(protocol_version=PROTOCOL_VERSION,   # 규격 버전(정수 1)
                                  agent_capabilities=AgentCapabilities(load_session=True,   # session/load 지원
                                                                       prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=True),   # 내장 리소스 지원
                                                                       mcp_capabilities=McpCapabilities(http=True, sse=True)),   # session/new 의 MCP: stdio·http·sse
                                  agent_info=Implementation(name="ncn", title="ncn (Continue CLI compatible)", version=__version__),   # 에이전트 정보
                                  auth_methods=[])                    # 인증 없음

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:   # authenticate(인증 없음)
        """인증 방법이 없으므로 아무 것도 하지 않는다."""
        return None                                          # 빈 응답

    # ───────── 능력 판단 ─────────
    def _fs_caps(self) -> tuple[bool, bool]:                 # fs 능력(읽기·쓰기)
        """clientCapabilities.fs.readTextFile / writeTextFile."""
        fs = getattr(self.client_caps, "fs", None)           # fs 능력
        return bool(getattr(fs, "read_text_file", False)), bool(getattr(fs, "write_text_file", False))   # 둘 다 참/거짓

    def _can_elicit(self) -> bool:                           # elicitation(form) 능력
        """clientCapabilities.elicitation.form 이 있으면 참."""
        el = getattr(self.client_caps, "elicitation", None)  # elicitation 능력
        return el is not None and getattr(el, "form", None) is not None   # form 이 있으면

    def _can_ncn_ask(self) -> bool:                          # ncn 확장 질문 능력(_meta.ncn.ask)
        """clientCapabilities._meta = {"ncn": {"ask": true}} 이면 _ncn/ask 확장 요청으로 묻는다(VS 확장용)."""
        meta = getattr(self.client_caps, "field_meta", None) or {}   # _meta 항목
        return bool((meta.get("ncn") or {}).get("ask")) if isinstance(meta, dict) else False   # ncn.ask 참이면

    # ───────── 세션 만들기 ─────────
    def _open(self, cwd: str, mcp_servers: Any, load: Optional[dict] = None) -> AcpSession:   # 세션 하나 조립(ncn/chat_session.py 공용 조립, headless=False)
        """작업 폴더 이동 → 공용 조립(권한·설정·모델·LLMClient·세션 저장·기록·루프 의존성). 실패하면 RequestError(cn 오류 글)."""
        from ncn.chat_session import SessionOpenError, open_chat_core   # 대화형 세션 공용 조립(콘솔 대화형과 같은 코드)
        cwd_s = os.path.abspath(cwd or os.getcwd())          # 절대 경로(규격상 절대 경로가 온다)
        try:                                                # 작업 폴더로 이동(도구의 상대 경로 기준 = process.cwd)
            os.chdir(cwd_s)                                  # cn 은 실행 폴더가 작업 폴더 — 같게 맞춤
        except OSError as e:                                 # 없는 폴더
            raise RequestError.invalid_params({"message": f"cwd not found: {cwd_s} ({e})"}) from e   # 잘못된 인자

        def select(sm):                                      # 세션 고르기(SessionManager → id·기록)
            if load is not None:                             # session/load 항목
                sm.set_session(load)                         # 이후 저장도 이 세션 파일로
                return load["sessionId"], load.get("history") or []   # 불러온 id·기록
            cur = sm.get_current()                           # 새 세션(시험용 id 환경변수가 있으면 그 id)
            if cur["sessionId"] in self.sessions:            # 같은 id 가 이미 열려 있으면(시험용 id 고정 때)
                cur = sm.start_new([])                       # 새 uuid 로
            return cur["sessionId"], []                      # 새 id·빈 기록

        s: Optional[AcpSession] = None                       # 아래 콜백들이 늦게 읽는다(조립 뒤 채움)
        try:                                                # 공용 조립
            core = open_chat_core(self.args, self.environ, cwd_s, select_session=select,   # 대화형 조립
                                  on_event=lambda e: self._on_event(s, e),   # 루프 이벤트 → session/update
                                  ask_user=self._ask_user,   # 승인 질문 → session/request_permission
                                  get_signal=lambda: s.signal,   # 지금 턴 중단 신호(session/cancel → 하위도 중단)
                                  check_permission=lambda tool, args: self._check(s, tool, args),   # 실행 시점 판정 — 카드 먼저 보낸 뒤
                                  on_subagent_progress=lambda tcid, name, out: self._on_subagent_progress(s, tcid, name, out))   # 카드 안 진행 표시
        except SessionOpenError as e:                        # 설정 오류·모델 없음
            raise RequestError.internal_error({"message": e.message}) from e   # 오류 응답(cn 원문 글)
        s = AcpSession(id=core.id, cwd=cwd_s, gdir=core.gdir, cfg=core.cfg, model=core.model, llm=core.llm, perms=core.perms,   # 세션 상태
                       history=core.history, sessions=core.sessions, deps=core.deps,   # 기록·저장·루프 의존성
                       first_prompt=load is None, core=core)   # 불러온 세션은 --prompt 를 붙이지 않음
        can_read, can_write = self._fs_caps()                # fs 능력
        s.files = fsio.AcpFiles(self.conn, s.id, can_read=can_read, can_write=can_write) if (can_read or can_write) else None   # IDE 위임(능력 있을 때만)
        self.sessions[s.id] = s                              # 등록
        return s                                            # 세션 반환

    async def _connect_mcp(self, s: AcpSession, mcp_servers: Any) -> None:   # 세션의 MCP 접속
        """설정 mcpServers 뒤에 ACP mcpServers(cn 설정 모양으로 바꿈)를 이어 같은 연결 코드로 붙인다(ncn/chat_session.py connect_mcp)."""
        from ncn.chat_session import connect_mcp             # 공용 접속(실패해도 던지지 않음)
        from ncn.mcp.acp_servers import acp_servers_to_configs   # ACP → cn 설정 모양
        await connect_mcp(s.core, self.environ, acp_servers_to_configs(mcp_servers))   # 설정 먼저, 그다음 IDE 가 준 것
        s.mcp = s.core.mcp                                   # 정리(aclose)용으로도 보관

    def _session(self, session_id: str) -> AcpSession:      # id 로 세션 찾기
        """없으면 resource_not_found 오류."""
        s = self.sessions.get(session_id)                    # 찾기
        if s is None:                                        # 없으면
            raise RequestError.resource_not_found(session_id)   # 오류 응답
        return s                                            # 세션

    # ───────── session/new · session/load ─────────
    async def new_session(self, cwd: str, additional_directories: Any = None, mcp_servers: Any = None,   # session/new 항목
                          **kwargs: Any) -> NewSessionResponse:   # (인자 이어짐)
        """새 대화. 응답에 모드(normal·plan·auto)와 같은 내용의 config option 을 싣는다."""
        s = self._open(cwd, mcp_servers)                     # 세션 조립
        await self._connect_mcp(s, mcp_servers)              # MCP 접속(설정 + session/new)
        return NewSessionResponse(session_id=s.id, modes=convert.mode_state(s.perms.mode),   # 세션 id·모드
                                  config_options=convert.config_options(s.perms.mode))   # 설정 선택지(모드)

    async def load_session(self, cwd: str, session_id: str, mcp_servers: Any = None,   # session/load 항목
                           additional_directories: Any = None, **kwargs: Any) -> LoadSessionResponse:   # (인자 이어짐)
        """cn 형식 세션 파일(<전역>/sessions/<id>.json)을 불러와 기록을 session/update 로 되살린 뒤 응답한다."""
        from ncn.session import history_manager_load, sessions_folder   # 세션 파일
        if not os.path.exists(os.path.join(sessions_folder(self.environ), f"{session_id}.json")):   # 파일이 없으면
            raise RequestError.resource_not_found(session_id)   # cn HistoryManager 는 빈 세션을 주지만 IDE 에는 오류로 알림
        data = history_manager_load(session_id, self.environ)   # 세션 읽기
        s = self._open(cwd, mcp_servers, load=data)          # 세션 조립(기록 채움)
        await self._connect_mcp(s, mcp_servers)              # MCP 접속(설정 + session/load)
        for u in convert.history_to_updates(s.history.get_history(), s.cwd):   # 기록 되살리기
            if u["kind"] == "user":                          # 사용자 글
                self._update(s, update_user_message_text(u["text"]))   # user_message_chunk 알림 보냄
            elif u["kind"] == "agent":                       # 응답 글
                self._update(s, update_agent_message_text(u["text"]))   # agent_message_chunk 알림 보냄
            else:                                            # 도구 카드
                self._update(s, start_tool_call(u["id"], convert.tool_title(u["name"], u["args"]), kind=convert.tool_kind(u["name"]),   # tool_call(끝난 상태)
                                                status=u["status"], content=[convert.text_content(u["output"])] if u["output"] else None,   # 결과 글
                                                locations=convert.tool_locations(u["args"], s.cwd), raw_input=convert.public_args(u["args"])))   # 위치·인자
        await self.flush()                                   # 응답 전에 다 보냄(규격)
        return LoadSessionResponse(modes=convert.mode_state(s.perms.mode), config_options=convert.config_options(s.perms.mode))   # 모드

    # ───────── 모드 ─────────
    def _switch_mode(self, s: AcpSession, mode_id: str) -> None:   # 모드 전환(ToolPermissionService.switchMode)
        """normal·plan·auto 중 하나로. 바뀐 모드를 current_mode_update·config_option_update 로 알린다."""
        from ncn.permissions.policies import PERMISSION_MODES   # 모드 목록
        if mode_id not in PERMISSION_MODES:                  # 모르는 모드
            raise RequestError.invalid_params({"message": f"Unknown mode: {mode_id}"})   # 오류 응답
        layer = getattr(s.perms, "layer", None)              # [ncn E1] 판정 층(관리자 정책)
        if layer is not None and not layer.mode_allowed(mode_id):   # [ncn E1] 관리자 정책이 금지한 모드(deny_modes)
            raise RequestError.invalid_params({"message": f"Mode '{mode_id}' is disabled by the administrator policy"})   # 오류 응답(모드 그대로)
        s.perms.set_mode(mode_id)                            # 정책 교체(다음 반복부터 도구 목록·시스템 메시지 반영)
        self._update(s, update_current_mode(mode_id))        # current_mode_update 알림 보냄
        self._update(s, ConfigOptionUpdate(session_update="config_option_update", config_options=convert.config_options(mode_id)))   # config_option_update 알림 보냄

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> SetSessionModeResponse:   # session/set_mode 항목
        """모드 전환(Shift+Tab 과 같은 효과)."""
        s = self._session(session_id)                        # 세션
        self._switch_mode(s, mode_id)                        # 전환
        await self.flush()                                   # 알림 먼저
        return SetSessionModeResponse()                      # 빈 응답

    async def set_config_option(self, config_id: str, session_id: str, value: Any, **kwargs: Any) -> SetSessionConfigOptionResponse:   # session/set_config_option 항목
        """설정 선택지 'mode' 만 지원 — set_mode 와 같은 일."""
        s = self._session(session_id)                        # 세션
        if config_id != convert.MODE_CONFIG_ID:              # mode 가 아니면
            raise RequestError.invalid_params({"message": f"Unknown config option: {config_id}"})   # 오류 응답
        self._switch_mode(s, str(value))                     # 전환
        await self.flush()                                   # 알림 먼저
        return SetSessionConfigOptionResponse(config_options=convert.config_options(s.perms.mode))   # 바뀐 설정

    # ───────── session/prompt ─────────
    async def prompt(self, session_id: str, prompt: list, **kwargs: Any) -> PromptResponse:   # session/prompt 항목
        """질문 한 번 = cn 대화형 processMessage 한 번. 리소스는 @파일 첨부(contextItems)로."""
        from ncn.app import process_and_combine_prompts      # --prompt 합치기
        from ncn.headless import error_message               # 오류 글
        from ncn.llm.client import AbortSignal               # 중단 신호
        from ncn.compaction import is_interactive_compact_command   # /compact 판정
        from ncn.loop.agent import run_turn                  # 한 턴
        s = self._session(session_id)                        # 세션
        if s.task is not None and not s.task.done():         # 이미 턴이 돌고 있으면
            raise RequestError.invalid_request({"message": "A prompt is already running in this session"})   # 동시 질문 금지
        if os.getcwd() != s.cwd:                             # 다른 세션이 폴더를 바꿨으면
            os.chdir(s.cwd)                                  # 이 세션 폴더로
        files: dict[str, Optional[str]] = {}                 # resource_link 파일 내용(미리 읽음 — IDE 위임은 async)
        for b in prompt:                                     # 블록마다
            if getattr(b, "type", None) == "resource_link":  # 파일 링크면
                p = convert.uri_to_path(getattr(b, "uri", "") or "")   # 경로
                if p is not None:                            # file 이면
                    try:                                    # 읽기(위임 또는 디스크)
                        files[p] = await (s.files or fsio.get_backend()).read_text(p)   # 내용
                    except Exception:                        # 없는 파일(cn: ENOENT 면 경로를 내용으로)
                        files[p] = None                     # 없음
        text, items, ignored = convert.prompt_to_user_message(prompt, s.cwd, lambda p: files.get(p))   # cn 사용자 메시지로
        if ignored:                                          # 무시한 블록
            log.warning("지원하지 않는 질문 블록 무시: %s", ", ".join(ignored))   # 기록
        if s.first_prompt and self.args.prompts:             # 첫 질문이면 --prompt 를 앞에
            text = process_and_combine_prompts(self.args.prompts, text or None, s.cwd) or ""   # processAndCombinePrompts 항목
        s.first_prompt = False                               # 다음부터는 아님
        s.signal = AbortSignal()                             # 이번 턴 중단 신호
        s.cancel_requested = False                           # 취소 표시 초기화
        s.cancel_ev = asyncio.Event()                        # 이번 턴 취소 이벤트
        s.rejected = False                                   # 거부 표시 초기화
        s.turn_usage = None                                  # 사용량 초기화

        async def body():                                    # 턴 본문(이 작업의 컨텍스트에만 세션·파일 창구를 건다)
            _CURRENT.set(s)                                  # 질문 처리기·승인이 세션을 찾게
            fsio.set_backend(s.files)                        # 이 턴의 파일 창구(IDE 위임 또는 디스크)
            from ncn import backup                           # [ncn E4] 백업·되돌리기
            from ncn.compat import cn_compat                 # [ncn E4] cn 원래 동작이면 /undo 도 일반 글
            backup.set_session_id(s.id)                      # [ncn E4] 이 턴의 백업은 이 세션 id 폴더로
            if not cn_compat() and backup.is_undo_command(text):   # [ncn E4] "/undo"·"/undo list"(cn 에 없음) → LLM 턴 없이 처리
                self._update(s, update_agent_message_text(await backup.undo_command_async(text, s.id)))   # 결과 글(기록에는 안 남김)
                return None                                  # 끝
            if is_interactive_compact_command(text):         # 대화형 /compact(slashCommands.ts) → 수동 압축, LLM 턴 없음
                return await s.deps.compaction_hook("manual", s.history.get_history(),   # useChat.compaction.ts handleCompactCommand
                                                    {"is_headless": False, "model_name": s.model.model or "",   # 대화형·모델명
                                                     "deps": s.deps, "signal": s.signal})   # session/cancel 로 취소 가능
            return await run_turn(s.history, text, s.deps, headless=False, model_name=s.model.model or "",   # 대화형 한 턴
                                  signal=s.signal, context_items=items)   # 중단 신호·첨부 맥락

        s.task = asyncio.ensure_future(body())               # 턴 시작
        await asyncio.wait({s.task})                         # 끝날 때까지(밖에서 취소돼도 턴 작업은 건드리지 않게 wait)
        await self.flush()                                   # 남은 알림을 응답보다 먼저
        s.sessions.update_history(s.history.get_history())   # 세션 파일 저장(updateSessionHistory)
        meta = {"ncn": {"stoppedByRejection": True}} if s.rejected else None   # 승인 3번으로 멈췄으면 표시
        usage = convert.usage_to_acp(s.turn_usage)           # 사용량
        exc = None if s.task.cancelled() else s.task.exception()   # 턴 오류(꺼내 두어야 '꺼내지 않은 예외' 경고가 없음)
        if s.task.cancelled() or s.cancel_requested:         # 취소
            return PromptResponse(stop_reason="cancelled", usage=usage, field_meta=meta)   # stopReason cancelled 반환
        if exc is not None:                                  # LLM·설정 오류 등
            log.debug("turn error", exc_info=exc)            # 기록
            raise RequestError(-32603, error_message(exc), {"message": error_message(exc)})   # 오류 응답(cn 오류 글)
        return PromptResponse(stop_reason="end_turn", usage=usage, field_meta=meta)   # 정상 끝

    # ───────── session/cancel ─────────
    async def cancel(self, session_id: str, **kwargs: Any) -> None:   # session/cancel(알림)
        """handleInterrupt — 중단 신호(스트림·재시도 멈춤, 승인·질문 대기 풀림). 도구 실행 중이라 곧 안 끝나면 턴 작업을 취소한다."""
        s = self.sessions.get(session_id)                    # 세션
        if s is None or s.task is None or s.task.done():     # 돌고 있는 턴이 없으면
            return                                          # 할 일 없음
        s.cancel_requested = True                            # 취소 표시
        s.cancel_ev.set()                                    # 승인·질문 대기 풀기
        s.signal.abort()                                     # 중단 신호
        try:                                                # 스스로 끝나기를 잠깐 기다림
            await asyncio.wait_for(asyncio.shield(s.task), CANCEL_GRACE_S)   # shield: 기다림이 끝나도 턴 작업은 그대로
        except asyncio.TimeoutError:                         # 도구가 아직 실행 중
            s.task.cancel()                                  # 턴 작업 취소(Bash 등 도구 중단)
        except BaseException:                                # 턴 오류는 prompt 쪽에서 처리
            pass                                            # 무시
        q = self._quiz.pending_question                      # 남은 질문
        if q is not None:                                    # 답 없이 남았으면
            self._quiz.answer_question(q.request_id, "")     # 대기 정리(작업은 이미 취소됨)

    # ───────── 루프 이벤트 → session/update ─────────
    def _on_event(self, s: AcpSession, e: dict) -> None:     # 루프 이벤트 처리(동기)
        """ncn/loop/events.py 이벤트를 ACP 알림으로 바꿔 줄 세운다."""
        t = e.get("type")                                    # 이벤트 종류
        if t == "content":                                   # 글 조각
            self._update(s, update_agent_message_text(e.get("text") or ""))   # agent_message_chunk 알림 보냄
        elif t == "tool_start":                              # 도구 시작(권한 확인 직전)
            self._on_tool_start(s, e)                        # 카드 보냄
        elif t in ("tool_result", "tool_error"):             # 도구 결과·오류
            self._on_tool_end(s, e)                          # 카드 갱신
        elif t == "usage":                                   # 사용량
            u = e.get("usage")                               # 사용량 dict
            s.turn_usage = convert.add_usage(s.turn_usage, u)   # 턴 누적
            if isinstance(u, dict):                          # 세션 저장(trackSessionUsage)
                s.sessions.track_usage(u)                   # 누적·저장
        elif t == "retry":                                   # 재시도 — 이번 호출의 글 조각을 화면이 지워야 함
            self._ext("ncn/retry", {"sessionId": s.id, "attempt": e.get("attempt"),   # _ncn/retry 확장 알림
                                    "delayMs": e.get("delay_ms"), "error": e.get("error")})   # 지연·오류
        elif t == "system_message":                          # 압축 안내 등
            self._ext("ncn/system_message", {"sessionId": s.id, "message": e.get("message")})   # _ncn/system_message 확장 알림
            self._update(s, update_agent_thought_text((e.get("message") or "") + "\n"))   # session/update agent_thought_chunk 로도(규격 안 알림)

    def _on_tool_start(self, s: AcpSession, e: dict) -> None:   # 도구 카드 만들기
        """tool_call(status pending, title, kind, locations, rawInput). Edit/Write 는 바뀔 내용(diff)도 싣는다(V4)."""
        tcid = e.get("tool_call_id") or ""                   # 호출 id
        name = e.get("name") or ""                           # 도구 이름
        args = e.get("arguments")                            # 인자
        if name == "AskQuestion":                            # 질문 도구면
            s.last_ask_id = tcid                             # 질문을 이 카드에 잇는다
        card = s.cards.get(tcid)                             # 이미 보낸 카드인가
        if card is None:                                     # 처음이면
            card = _Card(name=name, args=args, title=convert.tool_title(name, args), kind=convert.tool_kind(name))   # 카드 상태
            s.cards[tcid] = card                             # 보관
            if name == "Write" and isinstance(args, dict) and isinstance(args.get("filepath"), str):   # Write: 쓰기 전 내용
                p = args["filepath"]                         # 경로
                card.write_path = p if os.path.isabs(p) else os.path.join(s.cwd, p)   # 절대 경로(보낼 때 읽음)
        card.pre_args = e.get("preprocessed_args", card.pre_args)   # 사전 처리 인자(있으면)

        async def send():                                    # 보낼 때 만든다(Write 원래 내용을 fs 위임으로 읽기 위해 async)
            if card.write_path is not None and not card.old_read:   # Write 원래 내용을 아직 안 읽었으면
                card.old_read = True                         # 한 번만
                if s.files is not None and s.files.can_read:   # fs 위임이 켜졌으면 IDE 버퍼 기준
                    try:                                    # 위임 읽기(실패하면 디스크, 없으면 오류)
                        card.old_text = await s.files.read_text(card.write_path)   # 버퍼 내용
                    except Exception:                        # 새 파일
                        card.old_text = None                # 없음
                else:                                        # 위임 없음
                    card.old_text = fsio.read_text_file_disk(card.write_path)   # 디스크 내용(없으면 None)
            diff = convert.edit_diff(name, card.pre_args if card.pre_args is not None else args, card.old_text, s.cwd)   # diff 내용
            await self.conn.session_update(session_id=s.id, update=start_tool_call(tcid, card.title, kind=card.kind, status="pending",   # tool_call 알림 보냄
                                           content=[diff] if diff is not None else None,   # 바뀔 내용
                                           locations=convert.tool_locations(args, s.cwd), raw_input=convert.public_args(args)))   # 위치·인자(내부 키 제외)

        self._enqueue(send)                                  # 순서대로 보냄(실행 전에 _check 가 flush)

    def _on_tool_end(self, s: AcpSession, e: dict) -> None:  # 도구 카드 끝내기
        """tool_call_update: 성공 → completed(+diff, 결과 글), 거부(canceled)·오류 → failed(글). Checklist 성공이면 plan 도."""
        tcid = e.get("tool_call_id") or ""                   # 호출 id
        self._drop_subagent_progress(s, tcid)                # 남은 Subagent 진행 표시 버림(끝 결과가 마지막이 되게)
        card = s.cards.get(tcid)                             # 카드
        name = e.get("name") or (card.name if card else "")  # 도구 이름
        ok = e.get("type") == "tool_result" and e.get("status") == "done"   # 성공인가
        text = e.get("result") if e.get("type") == "tool_result" else e.get("error")   # 결과 또는 오류 글

        async def send():                                    # 보낼 때 만든다(카드 시작 알림이 원래 내용을 채운 뒤)
            content = []                                     # 카드 내용
            if ok and card is not None:                      # 성공이면 diff 도
                diff = convert.edit_diff(name, card.pre_args if card.pre_args is not None else card.args, card.old_text, s.cwd)   # diff 항목
                if diff is not None:                         # 편집 도구면
                    content.append(diff)                    # diff 먼저
            if text:                                         # 글이 있으면
                content.append(convert.text_content(str(text)))   # 결과 글
            await self.conn.session_update(session_id=s.id, update=update_tool_call(tcid, status="completed" if ok else "failed",   # tool_call_update 알림 보냄
                                                                                    content=content or None, raw_output=text))   # 내용·원문 결과

        self._enqueue(send)                                  # 순서대로 보냄
        if ok and name == "Checklist" and card is not None and isinstance(card.args, dict):   # Checklist 성공
            self._update(s, update_plan(convert.checklist_to_plan(card.args.get("checklist"))))   # plan 알림 보냄

    def _on_subagent_progress(self, s: AcpSession, tcid: str, name: str, output: str) -> None:   # 하위 에이전트 진행 → 카드 갱신
        """[ncn] Subagent 카드(tool_call)의 content 를 하위 대화 출력(지금까지)으로 바꾸고 _meta.ncn.subagent 에 이름·글자 수.
        조각마다 보내면 너무 많으므로 같은 카드는 0.1초에 한 번만 — 그 사이에 온 것은 남은 시간 뒤 마지막 것 하나만 보낸다.
        도구가 끝나면(_on_tool_end) 남은 예약은 버린다(끝난 카드가 다시 in_progress 로 돌아가지 않게)."""
        import time                                          # 보낸 시각
        s.progress_latest[tcid] = (name, output)             # 가장 최근 출력
        if tcid in s.progress_timers:                        # 이미 예약돼 있으면
            return                                          # 그때 최근 것을 보낸다
        wait = SUBAGENT_PROGRESS_INTERVAL_S - (time.monotonic() - s.progress_sent.get(tcid, 0.0))   # 남은 간격
        if wait <= 0:                                        # 간격이 지났으면
            self._send_subagent_progress(s, tcid)            # 바로 보냄
            return                                          # 끝
        s.progress_timers[tcid] = asyncio.get_event_loop().call_later(wait, self._send_subagent_progress, s, tcid)   # 남은 시간 뒤 예약

    def _send_subagent_progress(self, s: AcpSession, tcid: str) -> None:   # 진행 표시 하나 보내기
        """가장 최근 출력으로 tool_call_update(in_progress) 하나를 줄 세운다."""
        import time                                          # 보낸 시각
        s.progress_timers.pop(tcid, None)                    # 예약 해제
        latest = s.progress_latest.pop(tcid, None)           # 보낼 출력
        if latest is None:                                   # 없으면(이미 끝남)
            return                                          # 할 일 없음
        name, output = latest                                # 이름·출력
        s.progress_sent[tcid] = time.monotonic()             # 시각 기록
        u = update_tool_call(tcid, status="in_progress", content=[convert.text_content(output)])   # 카드 내용 = 지금까지 출력
        u = u.model_copy(update={"field_meta": {"ncn": {"subagent": {"name": name, "outputChars": len(output)}}}})   # _meta 에 하위 이름·길이
        self._update(s, u)                                   # session/update 줄 세움

    def _drop_subagent_progress(self, s: AcpSession, tcid: str) -> None:   # 남은 진행 표시 버리기
        """도구가 끝났으면 예약된 진행 표시를 취소한다."""
        timer = s.progress_timers.pop(tcid, None)            # 예약
        if timer is not None:                                # 있으면
            timer.cancel()                                   # 취소
        s.progress_latest.pop(tcid, None)                    # 남은 출력 버림

    # ───────── 승인(ask_user) → session/request_permission ─────────
    async def _check(self, s: AcpSession, tool: Any, args: Any) -> str:   # 실행 시점 권한 판정(루프가 await)
        """도구 카드(tool_start 직후 줄 선 알림 — Write 원래 내용 읽기 포함)를 먼저 다 보낸 뒤 판정 → 도구가 실행되기 전에 원래 내용이 확정된다."""
        await self.flush()                                   # 카드 먼저
        return s.perms.check(tool, args)                     # allow|ask|exclude

    async def _race(self, s: AcpSession, coro) -> Any:       # 요청과 취소 중 먼저 끝나는 것
        """IDE 응답을 기다리다 session/cancel 이 오면 요청을 버리고 None(3번 거부의 중단 신호로는 풀지 않음 — cn은 다음 승인도 띄움)."""
        task = asyncio.ensure_future(coro)                   # 요청 작업
        waiter = asyncio.ensure_future(s.cancel_ev.wait())   # 취소 기다림
        try:                                                # 둘 중 하나
            done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)   # 먼저 끝나는 것
        finally:                                            # 정리
            waiter.cancel()                                  # 중단 기다림 정리
        if task in done:                                     # 응답이 먼저
            return task.result()                             # 응답(오류면 예외)
        task.cancel()                                        # 중단이 먼저 → 요청 버림
        return None                                         # 없음

    async def _ask_user(self, name: str, args: Any, preview: Any, tool_call_id: str) -> str:   # 루프 승인 콜백
        """승인 3가지. 돌려주는 값 "allow" | "reject". (ask_user 형식: tool_calls.py AskUser)"""
        s = _CURRENT.get()                                   # 지금 턴의 세션
        if s is None:                                        # 세션 밖(있을 수 없음)
            return "reject"                                  # 거부
        await self.flush()                                   # 도구 카드가 먼저 도착하게
        if s.cancel_requested:                               # session/cancel 을 받은 턴
            return "reject"                                  # 묻지 않고 거부
        # 앞 도구에서 3번(아니오)을 골랐어도 cn 대화형은 같은 묶음의 다음 도구 승인을 또 띄운다(executeStreamedToolCalls 권한 단계는 중단 신호를 보지 않음)
        card = s.cards.get(tool_call_id)                     # 카드
        old = card.old_text if card is not None else None    # Write 원래 내용
        diff = convert.edit_diff(name, args, old, s.cwd)      # 바뀔 내용(Edit·MultiEdit 는 사전 처리 인자)
        pv = convert.preview_text(preview)                   # 미리보기 글
        content = [diff] if diff is not None else ([convert.text_content(pv)] if pv else None)   # diff 또는 미리보기 글
        tc = ToolCallUpdate(tool_call_id=tool_call_id, title=card.title if card else convert.tool_title(name, args),   # 승인 대상 도구
                            kind=convert.tool_kind(name), status="pending", content=content,   # 종류·상태·내용
                            raw_input=convert.public_args(args), locations=convert.tool_locations(args, s.cwd))   # 인자(내부 _ncn 키 제외)·위치
        resp = await self._race(s, self.conn.request_permission(session_id=s.id, tool_call=tc, options=convert.permission_options()))   # IDE 에 묻기
        if resp is None:                                     # 중단으로 끝남
            return "reject"                                  # 거부
        outcome = resp.outcome                               # 결과
        if getattr(outcome, "outcome", None) != "selected":   # cancelled(IDE 가 취소)
            return "reject"                                  # 거부(기록에 system 글 없음)
        opt = outcome.option_id                              # 고른 선택지
        if opt in (convert.OPT_ALLOW_ONCE, convert.OPT_ALLOW_ALWAYS):   # 1·2 계속
            if opt == convert.OPT_ALLOW_ALWAYS:              # 2: 다시 묻지 않기
                s.perms.add_always_allow(name, args)         # permissions.yaml allow 에 규칙 추가 후 다시 읽기
            self._update(s, update_tool_call(tool_call_id, status="in_progress"))   # 실행 중
            return "allow"                                   # 승인
        if not s.rejected:                                   # 이 턴의 첫 3번 선택만(cn: 중단 뒤 isWaitingForResponse=false 라 두 번째부터는 중단·system 글 없음)
            s.signal.abort()                                 # 3: 응답 중단(useChat.ts stopStream)
            s.history.add_system_message(convert.TOOL_CANCELED_SYSTEM_MESSAGE)   # 기록에 system 글(다음 요청에 들어감)
        s.rejected = True                                    # 거부로 멈춤 표시
        return "reject"                                      # 거부 → "Permission denied by user"

    # ───────── AskQuestion → elicitation / _ncn/ask / 승인 형식 ─────────
    def _on_question(self, pq: Any) -> None:                 # QuizService questionRequested 처리기(동기)
        """도구 실행 중(턴 작업 안)에 불린다 → 비동기로 IDE 에 묻고 answer_question 으로 답한다."""
        s = _CURRENT.get()                                   # 지금 턴의 세션
        if s is None:                                        # ACP 턴 밖(헤드리스 등)
            return                                          # 답하지 않음(cn 과 같음)
        asyncio.ensure_future(self._answer_question(s, pq))  # 비동기로 묻기

    async def _answer_question(self, s: AcpSession, pq: Any) -> None:   # 질문 하나 처리
        """elicitation(form) 능력 → elicitation/create, _meta.ncn.ask → _ncn/ask, 아니면 승인 요청 형식(선택지마다 allow_once + Skip)."""
        await self.flush()                                   # 카드 먼저
        q, opts, default = pq.question, pq.options, pq.default_answer   # 질문·선택지·기본 답
        answer, custom = default or "", False                # 기본값(거절·취소 시)
        try:                                                # IDE 오류는 기본 답으로
            if self._can_elicit():                           # 1) elicitation 항목
                mode = ElicitationFormSessionMode(session_id=s.id, tool_call_id=s.last_ask_id,   # 세션·카드
                                                  requested_schema=ElicitationSchema.model_validate(convert.question_schema(q, opts, default)))   # 폼 스키마
                resp = await self._race(s, self.conn.create_elicitation(message=q, mode=mode))   # 묻기
                if resp is None:                             # 중단
                    return                                  # 턴 취소가 정리
                if getattr(resp, "action", None) == "accept":   # 수락
                    answer, custom = convert.answer_from_elicitation(resp.content, default)   # 답
            elif self._can_ncn_ask():                        # 2) ncn 확장
                r = await self._race(s, self.conn.ext_method("ncn/ask", {"sessionId": s.id, "toolCallId": s.last_ask_id,   # _ncn/ask 요청
                                                                          "question": q, "options": opts, "defaultAnswer": default}))   # 질문 내용
                if r is None:                                # 중단
                    return                                  # 끝
                answer = str((r or {}).get("answer") or default or "")   # 답
                custom = bool((r or {}).get("isCustom"))     # 직접 입력 여부
            else:                                            # 3) 승인 요청 형식
                tc = ToolCallUpdate(tool_call_id=s.last_ask_id or f"ask-{pq.request_id}", title=q, kind="other",   # 질문 카드
                                    status="pending", content=[convert.text_content(q)],   # 질문 글
                                    raw_input={"question": q, "options": opts, "defaultAnswer": default})   # 원래 인자
                resp = await self._race(s, self.conn.request_permission(session_id=s.id, tool_call=tc,   # 묻기
                                                                        options=convert.question_permission_options(opts, default)))   # 답 선택지
                if resp is None:                             # 중단
                    return                                  # 끝
                oc = resp.outcome                            # 결과
                answer = convert.answer_from_option(oc.option_id, opts, default) if getattr(oc, "outcome", None) == "selected" else (default or "")   # 답
        except Exception as e:                               # IDE 오류
            log.warning("AskQuestion 묻기 실패 — 기본 답 사용: %s", e)   # 기록
        self._quiz.answer_question(pq.request_id, answer, custom)   # 도구에 답 전달(User selected option … 글은 도구가 만듦)


# ─────────────────────────────────────────────────────────────────────────────
# 실행(app.py 가 부름)
# ─────────────────────────────────────────────────────────────────────────────


async def serve(args: argparse.Namespace, out: Any, inp: Any = None) -> int:   # ACP 연결 하나를 끝까지 처리
    """stdin 이 끝날 때까지 요청을 처리한다. out = 프로토콜 전용 바이너리 출력."""
    from acp.agent.connection import AgentSideConnection     # 에이전트 쪽 연결
    from ncn.acp.stdio import StdioTransport                 # 줄 단위 JSON 전송
    transport = StdioTransport(out, inp)                     # 전송
    agent = NcnAgent(args)                                   # 에이전트
    conn = AgentSideConnection(agent, transport, listening=False)   # 연결(on_connect 로 agent.conn 이 채워짐)
    try:                                                    # 끝날 때까지
        await conn.listen()                                  # 받기 반복(EOF 면 끝)
    finally:                                                # 정리
        await agent.aclose()                                 # LLM 닫기 등
        await conn.close()                                   # 연결 닫기
    return 0                                                # 정상 종료


def run_stdio(args: argparse.Namespace) -> int:             # --stdio 진입(동기)
    """stdout 확보(V5) → 로그를 stderr 로 → 이벤트 루프에서 serve."""
    from ncn.acp.stdio import reserve_stdout                 # stdout 확보
    out = reserve_stdout()                                   # 진짜 stdout 은 프로토콜 전용, 나머지는 stderr
    root = logging.getLogger()                               # 루트 로거
    for h in list(root.handlers):                            # 기존 처리기 제거(stdout 으로 가는 것 방지)
        root.removeHandler(h)                                # 제거
    h = logging.StreamHandler(sys.stderr)                    # stderr 처리기
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))   # 형식
    root.addHandler(h)                                       # 등록
    root.setLevel(logging.DEBUG if args.verbose else logging.WARNING)   # --verbose 면 DEBUG
    logging.captureWarnings(True)                            # warnings 도 로그(stderr)로
    try:                                                    # 실행
        return asyncio.run(serve(args, out))                 # 연결 처리
    except KeyboardInterrupt:                                # Ctrl+C 항목
        return 130                                          # SIGINT 종료 코드
