# ncn/subagent.py — 하위 에이전트(Subagent) 실행. cn extensions/cli/src/subagent/executor.ts(executeSubAgent·buildAgentSystemMessage)
#   + tools/subagent.ts(run — parentSessionId 확인·onOutputUpdate·결과 글 <task_metadata>) + services/ModelService.ts(getSubagentModels)
# [ncn 부록B B16] cn은 실행 중 전역 서비스(권한·시스템 메시지·기록 isReady)를 바꿔 끼웠다가 되돌린다 → 동시 실행 시 섞이고 굳는다(P0 4장).
#   ncn은 하위 대화마다 독립 객체(자기 ChatHistory·권한·시스템 메시지·LLMClient(그 subagent 모델)·LoopDeps)로 돌린다 → 전역 상태 없음.
# [ncn 부록B B16] 헤드리스 cn은 sessionId 가 없어 "No active session found" 로 실패한다 — ncn 기본은 실제로 실행한다(NCN_CN_COMPAT=1 이면 cn처럼 실패).
# [추가 E] ~/.continue/ncn.yaml(전역 폴더, Continue가 모르는 키라 별도 파일 — 계획 13-6) 선택 설정:
#   subagents: {<이름>: {tools: [허용 도구 이름·패턴], max_result_chars: N}}, subagent_max_parallel: N(기본 2). 없으면 cn과 같게(모든 도구 allow).
# 요청 본문(도구 이름·설명·인자)은 cn과 같다 — 바뀌는 것은 실행 방식뿐(차등 시험 유지).
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 동시 실행 수 제한(세마포어)
import dataclasses                                           # 하위 호스트 복사
import inspect                                               # 동기/비동기 값 구분
import logging                                               # 로그(stdout 금지)
import pathlib                                               # ncn.yaml 경로
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Callable, Optional                   # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치

log = logging.getLogger("ncn.subagent")                      # 로거

NCN_YAML = "ncn.yaml"                                        # ncn 전용 설정 파일 이름(전역 폴더 안)
DEFAULT_MAX_PARALLEL = 2                                     # subagent_max_parallel 기본값
CANCELLED_MESSAGE = "Subagent execution was cancelled by the user."   # cn escapeHandler 가 하위 기록에 넣는 글(executor.ts:126)
NO_SESSION_MESSAGE = "No active session found"               # cn tools/subagent.ts:73 오류 글


# ─────────────────────────────────────────────────────────────────────────────
# ncn.yaml(추가 E)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass                                                  # 자료 클래스
class SubagentLimits:                                       # 하위 에이전트 하나의 ncn 제한
    """ncn.yaml subagents.<이름> — tools(None 이면 제한 없음 = cn 처럼 전부 allow), max_result_chars(None 이면 자르지 않음)."""
    tools: Optional[list[str]] = None                        # 허용 도구 이름(권한 패턴 문법: "Read", "Bash(npm test*)", "search_*")
    max_result_chars: Optional[int] = None                   # 메인에 돌려줄 응답 글 최대 글자 수


@dataclass                                                  # 자료 클래스
class NcnSettings:                                          # ncn.yaml 전체
    """ncn.yaml 의 하위 에이전트 관련 설정(모르는 키는 무시)."""
    subagents: dict[str, SubagentLimits] = field(default_factory=dict)   # 이름 → 제한
    subagent_max_parallel: int = DEFAULT_MAX_PARALLEL       # 동시에 도는 하위 에이전트 수(최상위 호출 기준)


def _is_pos_int(v: Any) -> bool:                            # 양의 정수인지(참거짓 제외)
    """bool 이 아닌 int 이고 1 이상이면 참."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1   # YAML true 는 int 의 하위형이라 따로 뺀다


def parse_ncn_settings(data: Any) -> NcnSettings:           # YAML 값 → 설정(잘못된 값은 경고 후 무시)
    """ncn.yaml 내용(dict)을 해석한다. 형식이 틀린 항목은 그 항목만 버린다(시작은 계속)."""
    out = NcnSettings()                                      # 기본값으로 시작
    if data is None:                                         # 빈 파일
        return out                                          # 기본값
    if not isinstance(data, dict):                           # 최상위가 사전이 아니면
        log.warning("ncn.yaml: top level must be a mapping — ignored")   # 경고
        return out                                          # 기본값
    mp = data.get("subagent_max_parallel")                   # 동시 실행 수
    if mp is not None:                                       # 적혀 있으면
        if _is_pos_int(mp):                                  # 양의 정수면
            out.subagent_max_parallel = mp                   # 반영
        else:                                                # 아니면
            log.warning("ncn.yaml: subagent_max_parallel must be a positive integer — ignored")   # 경고
    subs = data.get("subagents")                             # 하위 에이전트별 설정
    if subs is None:                                         # 없으면
        return out                                          # 끝
    if not isinstance(subs, dict):                           # 사전이 아니면
        log.warning("ncn.yaml: subagents must be a mapping — ignored")   # 경고
        return out                                          # 끝
    for name, v in subs.items():                             # 이름마다
        if not isinstance(v, dict):                          # 항목이 사전이 아니면
            log.warning("ncn.yaml: subagents.%s must be a mapping — ignored", name)   # 경고
            continue                                        # 다음 항목
        tools = v.get("tools")                               # 허용 도구 목록
        if tools is not None and not (isinstance(tools, list) and all(isinstance(t, str) for t in tools)):   # 글 목록이 아니면
            log.warning("ncn.yaml: subagents.%s.tools must be a list of tool names — ignored", name)   # 경고
            tools = None                                    # 제한 없음으로
        mrc = v.get("max_result_chars")                      # 결과 길이 상한
        if mrc is not None and not _is_pos_int(mrc):          # 양의 정수가 아니면
            log.warning("ncn.yaml: subagents.%s.max_result_chars must be a positive integer — ignored", name)   # 경고
            mrc = None                                      # 상한 없음으로
        out.subagents[str(name)] = SubagentLimits(tools=list(tools) if tools is not None else None, max_result_chars=mrc)   # 저장
    return out                                              # 완성된 설정


def load_ncn_settings(global_dir: Any) -> NcnSettings:      # 전역 폴더의 ncn.yaml 읽기
    """<전역 폴더>/ncn.yaml(ncn.config.paths.global_dir()) — 없거나 못 읽으면 기본값(cn과 같은 동작)."""
    if global_dir is None:                                   # 전역 폴더를 모르면
        return NcnSettings()                                # 기본값
    path = pathlib.Path(global_dir) / NCN_YAML               # 파일 경로
    if not path.is_file():                                   # 파일이 없으면
        return NcnSettings()                                # 기본값(cn과 같게)
    try:                                                    # 읽기·해석
        import yaml                                         # PyYAML(지연 import)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))   # YAML 해석
    except Exception as e:                                   # 읽기·문법 오류
        log.warning("ncn.yaml: cannot read %s — %s", path, e)   # 경고(시작은 계속)
        return NcnSettings()                                # 기본값
    return parse_ncn_settings(data)                          # 해석 결과


# ─────────────────────────────────────────────────────────────────────────────
# 하위 대화의 권한(cn: TOOL_PERMISSIONS 를 [{tool:"*", permission:"allow"}] 로 바꿔 끼움 → ncn: 자기 객체)
# ─────────────────────────────────────────────────────────────────────────────


class SubagentPermissions:                                  # 하위 대화 하나의 권한(전역 PermissionService 와 별개)
    """allowed=None 이면 cn처럼 전부 allow. 목록이 있으면 그 패턴만 allow, 나머지는 exclude(요청 목록에서 빠지고 불러도 막힘).
    실행 시점 판정은 PermissionService.check 와 같다(Bash 위험 명령 동적 평가 포함)."""

    def __init__(self, allowed: Optional[list[str]] = None):   # 허용 패턴 목록(None = 전부)
        from ncn.permissions.policies import ALLOW, EXCLUDE, ToolPermissionPolicy   # 정책 형식
        self.allowed = list(allowed) if allowed is not None else None   # 허용 패턴(보관)
        if self.allowed is None:                             # 제한 없음
            self.policies = [ToolPermissionPolicy("*", ALLOW)]   # 전부 허용(executor.ts:85 allow all tools for now)
        else:                                                # [추가 E] 허용 목록
            self.policies = [ToolPermissionPolicy(p, ALLOW) for p in self.allowed] + [ToolPermissionPolicy("*", EXCLUDE)]   # 목록만 allow, 나머지 금지

    def base_permission(self, tool_name: str, args: Optional[dict] = None) -> str:   # 요청 도구 목록용(인자 {})
        """정적 판정. 'Bash(npm test*)' 같은 인자 패턴만 있는 도구도 목록에는 넣는다(실행 때 인자로 다시 판정)."""
        from ncn.permissions.matcher import find_matching_policy   # 첫 일치 정책
        if self.allowed is not None and any(p.startswith(tool_name + "(") for p in self.allowed):   # 인자 패턴 허용이 있으면
            return "allow"                                  # 목록에는 보인다
        permission, _ = find_matching_policy(tool_name, args if args is not None else {}, self.policies)   # 정적 정책 판정
        return permission                                   # 판정 결과(allow | exclude)

    def check(self, tool: Any, args: dict) -> str:          # 실행 직전 판정
        """PermissionService.check 와 같은 규칙(이름이 Bash 면 위험 명령 평가)."""
        from ncn.permissions.matcher import check_tool_permission   # 동적 평가 포함 판정
        from ncn.permissions.service import _builtin_bash_policy   # 내장 Bash 평가
        evaluate = getattr(tool, "evaluate_tool_call_policy", None)   # 도구의 동적 평가
        if evaluate is None and getattr(tool, "name", None) == "Bash":   # 이름만 Bash 인 도구
            evaluate = _builtin_bash_policy                 # 내장 Bash 평가
        permission, _ = check_tool_permission(tool.name, args if args is not None else {}, self.policies, evaluate)   # 판정
        return permission                                   # 판정 결과(allow | ask | exclude)


# ─────────────────────────────────────────────────────────────────────────────
# 부모 쪽 준비물(호스트) — 헤드리스(app.py)·ACP(acp/server.py)가 만들어 Subagent 도구에 넘긴다
# ─────────────────────────────────────────────────────────────────────────────


@dataclass                                                  # 자료 클래스
class SubagentHost:                                         # 하위 대화를 만들 때 필요한 부모 쪽 정보
    """부모 대화의 도구·시스템 메시지·설정을 읽기만 한다(바꾸지 않음 — B16).
    tools(): 부모와 같은 전체 도구 목록(getAllAvailableTools — Subagent 도구는 하위 호스트로 바꿔 끼움).
    system_message(): 부모의 지금 모드 시스템 메시지(buildAgentSystemMessage 의 baseMessage).
    get_signal(): 부모 턴의 중단 신호(ACP session/cancel → 하위 대화도 중단)."""
    models: list                                             # 설정 models(ModelConfig 또는 cn 모양 dict) — getSubagentModels 대상
    headless: bool                                           # 부모가 헤드리스인가(cn: services.toolPermissions.isHeadless())
    tools: Callable[[], Any]                                 # 부모 전체 도구 목록 함수
    system_message: Callable[[], Any]                        # 부모 시스템 메시지 함수
    parent_history: Any = None                               # 부모 ChatHistory(session_id 확인·onOutputUpdate)
    get_signal: Optional[Callable[[], Any]] = None           # 부모 턴 중단 신호 얻기
    on_event: Optional[Callable[[dict], None]] = None        # 부모 이벤트 수신자(하위 사용량 usage 를 넘김 — 세션 사용량)
    on_progress: Optional[Callable[[str, str, str], None]] = None   # (호출 id, 하위 이름, 지금까지 출력) — ACP 카드 갱신
    settings: NcnSettings = field(default_factory=NcnSettings)   # ncn.yaml 설정
    make_llm: Optional[Callable[[Any], Any]] = None          # 모델 설정 → LLMClient(없으면 기본 생성)
    depth: int = 0                                           # 중첩 깊이(0 = 메인이 부른 것)
    _sem: Any = None                                         # 동시 실행 세마포어(처음 쓸 때 만듦)
    _sem_size: int = 0                                       # 세마포어를 만들 때의 크기

    def child(self) -> "SubagentHost":                      # 하위 대화 안의 Subagent 도구용 호스트
        """깊이만 하나 늘린 복사본(중첩 호출은 동시 실행 수에 세지 않는다 — 부모가 자리를 쥔 채 기다리면 교착)."""
        return dataclasses.replace(self, depth=self.depth + 1, _sem=None, _sem_size=0)   # 세마포어는 공유하지 않음

    def semaphore(self) -> asyncio.Semaphore:               # 동시 실행 세마포어
        """subagent_max_parallel 크기(설정이 바뀌면 새로)."""
        size = self.settings.subagent_max_parallel or DEFAULT_MAX_PARALLEL   # 크기
        if self._sem is None or self._sem_size != size:      # 처음이거나 크기가 바뀌었으면
            self._sem = asyncio.Semaphore(size)              # 새 세마포어
            self._sem_size = size                            # 크기 기록
        return self._sem                                    # 세마포어


def _raw(m: Any) -> dict:                                   # ModelConfig 또는 dict → cn 모양 dict
    """getSubagentModels 는 cn 모양(camelCase) dict 를 본다."""
    if isinstance(m, dict):                                  # 이미 dict 면
        return m                                            # 그대로
    return getattr(m, "raw", None) or {}                     # ModelConfig 의 원본 dict


def model_dicts(models: Optional[list]) -> list:            # 설정 모델 목록 → dict 목록(None 자리 유지)
    """registry·misc 가 쓰는 cn 모양 dict 목록."""
    return [(_raw(m) if m is not None else None) for m in (models or [])]   # 빈 자리는 None 그대로(cn !!model 필터가 거름)


def _model_config(m: dict) -> Any:                          # dict 를 ModelConfig 로 바꾸는 함수
    """LLMClient 를 만들 때 쓰는 ModelConfig."""
    from ncn.config.loader import ModelConfig                # 모델 설정 형식
    return ModelConfig.from_raw(m)                           # cn 모양 dict 에서


def _default_make_llm(mc: Any) -> Any:                      # 기본 LLM 만들기(createLlmApi 자리)
    """app.py 의 chat 모델과 같은 방법으로 그 subagent 모델의 LLMClient."""
    from ncn.llm.client import LLMClient                     # 스트리밍 클라이언트
    return LLMClient(api_base=mc.api_base, api_key=mc.api_key, model=mc.model,   # 주소·키·모델
                     default_completion_options=(mc.raw or {}).get("defaultCompletionOptions"),   # 기본 완료 옵션
                     request_options=mc.request_options)    # 요청 옵션


async def _maybe_await(v: Any) -> Any:                      # 동기/비동기 값을 모두 받는 보조 함수
    """awaitable 이면 기다린다."""
    if inspect.isawaitable(v):                              # 기다릴 수 있으면
        return await v                                      # 결과
    return v                                                # 그대로


async def build_agent_system_message(agent: dict, host: SubagentHost) -> str:   # 하위 대화 시스템 메시지 만들기(buildAgentSystemMessage)
    """부모 시스템 메시지(지금 모드) + 빈 줄 + chatOptions.baseSystemMessage(없으면 부모 것만)."""
    base = await _maybe_await(host.system_message()) if host.system_message is not None else ""   # 부모 시스템 메시지(getSystemMessage(currentMode))
    agent_prompt = (agent.get("chatOptions") or {}).get("baseSystemMessage") or ""   # 하위 에이전트 지시
    if agent_prompt:                                         # 있으면
        return f"{base}\n\n{agent_prompt}"                  # 합친 글(cn 원문 형식)
    return base                                             # 부모 것만


def format_subagent_output(response: str, success: bool) -> str:   # tools/subagent.ts 결과 글
    """[response, "<task_metadata>", "status: …", "</task_metadata>"].filter(Boolean).join("\\n") — 빈 응답 줄은 빠진다."""
    parts = [response, "<task_metadata>", f"status: {'completed' if success else 'failed'}", "</task_metadata>"]   # 줄들
    return "\n".join(p for p in parts if p)                  # 빈 글 제외하고 이음


def _truncate(text: str, limit: Optional[int]) -> str:      # [추가 E] 결과 길이 상한
    """limit 글자를 넘으면 앞부분 + 잘림 표시(ncn.yaml 을 쓴 경우만 — cn에는 없음)."""
    if not limit or len(text) <= limit:                      # 상한 없음 또는 짧으면
        return text                                         # 그대로
    return text[:limit] + f"\n[ncn: subagent result truncated to {limit} characters]"   # 잘라서 표시


# ─────────────────────────────────────────────────────────────────────────────
# 실행(executor.ts executeSubAgent + tools/subagent.ts run)
# ─────────────────────────────────────────────────────────────────────────────


async def execute_subagent(host: SubagentHost, agent: dict, prompt: str, *, tool_call_id: Optional[str] = None,  # 하위 대화 하나 실행
                           compat: bool = False) -> dict:   # compat: cn 원래 동작(ncn.yaml 무시·부모 중단과 분리)
    """하위 대화 한 번. 반환 {"success", "response", "error"?}(cn SubAgentResult). 실패는 예외 대신 success False.
    독립 객체: ChatHistory([user prompt]) · 권한(SubagentPermissions) · 시스템 메시지(한 번 계산) · LLMClient(그 모델) · LoopDeps."""
    from ncn.app import make_validate_context                # 컨텍스트 검사(헤드리스와 같은 것)
    from ncn.compaction import make_compaction_hook          # 하위 대화 자기 압축
    from ncn.llm.client import AbortSignal                   # 중단 신호
    from ncn.loop import events as ev                        # 이벤트 종류
    from ncn.loop.agent import LoopDeps, stream_chat_response   # 같은 루프
    from ncn.loop.history import ChatHistory                 # 자기 기록
    from ncn.tools import registry                           # 도구 덮어쓰기

    name = agent.get("name") or ""                           # 하위 에이전트 이름
    limits = (SubagentLimits() if compat else host.settings.subagents.get(name)) or SubagentLimits()   # ncn.yaml 제한(compat 면 무시)
    llm = None                                              # 그 모델의 LLM(만든 뒤 닫는다)
    try:                                                    # cn: 어떤 오류든 success false
        log.debug("Starting subagent execution: %s", name)   # cn 로그
        mc = _model_config(agent)                            # 모델 설정
        if not mc.model:                                     # cn: model·llmApi 가 없으면
            raise Exception("Model or LLM API not available")   # executor.ts:78 원문
        llm = (host.make_llm or _default_make_llm)(mc)       # createLlmApi(model) — 하위 대화 전용
        perms = SubagentPermissions(limits.tools)            # 자기 권한(전역 TOOL_PERMISSIONS 는 그대로)
        system_message = await build_agent_system_message(agent, host)   # 시작 때 한 번(cn: getSystemMessage 를 이 값으로 고정)
        history = ChatHistory([{"message": {"role": "user", "content": prompt}, "contextItems": []}])   # 자기 기록(부모 기록과 별개)
        child_host = host.child()                            # 하위 대화 안의 Subagent 도구용
        overrides = (mc.chat_options or {}).get("toolOverrides")   # 그 모델의 toolOverrides(cn: model.chatOptions)
        accumulated = {"text": ""}                           # 지금까지 출력(accumulatedOutput)

        def tools():                                         # 하위 대화의 도구 목록(부모 것 + Subagent 만 하위 호스트로)
            from ncn.tools.misc import make_subagent_tool    # Subagent 도구 생성
            out = []                                        # 결과
            for t in (host.tools() or []):                   # 부모 목록 순서 그대로
                if t.name == "Subagent" and t.is_built_in:   # 중첩 Subagent(cn: 베타 플래그가 전역이라 다시 보임)
                    out.append(make_subagent_tool(host.models, host=child_host))   # 같은 설명·인자, 하위 호스트
                else:                                        # 그 밖의 도구
                    out.append(t)                           # 그대로
            return out                                      # 목록

        def report() -> None:                               # 진행 알림(onOutputUpdate)
            if tool_call_id is None:                         # 호출 id 가 없으면
                return                                      # 갱신 안 함(cn 그대로)
            try:                                            # 갱신 오류는 무시(cn 그대로)
                if host.parent_history is not None:          # 부모 기록의 도구 결과를 '실행 중' 출력으로
                    host.parent_history.add_tool_result(tool_call_id, accumulated["text"], "calling")   # 부모 기록에 실행 중 출력(addToolResult)
                if host.on_progress is not None:             # [ncn] ACP 카드 갱신
                    host.on_progress(tool_call_id, name, accumulated["text"])   # 진행 표시
            except Exception:                                # 표시 실패
                pass                                        # 무시

        def sink(e: dict) -> None:                          # 하위 대화 이벤트(cn 콜백: onContent·onToolResult 뿐)
            t = e.get("type")                               # 종류
            if t == ev.CONTENT:                             # 글 조각(onContent)
                accumulated["text"] += e.get("text") or ""  # 이어 붙임
                report()                                    # 갱신
            elif t == ev.TOOL_RESULT:                       # onToolResult(성공·거부 결과)
                accumulated["text"] += f"\n\n{e.get('result') or ''}"   # todo(cn): 도구 출력 대신 이름·인자
                report()                                    # 갱신
            elif t in (ev.USAGE, ev.RETRY) and host.on_event is not None:   # [ncn] 사용량·재시도는 부모로(세션 사용량 — cn도 같은 세션에 누적)
                ev.safe_emit(host.on_event, e)              # 부모 수신자

        deps = LoopDeps(                                    # 자기 루프 의존성
            llm=llm, tools=tools,                           # 그 모델 LLM·도구 목록
            check_permission=lambda tool, args: perms.check(tool, args),   # 자기 권한(실행 시점)
            list_permission=lambda tool, args: perms.base_permission(tool.name, {}),   # 자기 권한(목록)
            ask_user=None,                                   # cn: 하위 대화 콜백에 승인 질문 없음 → ask 는 거부
            system_message=lambda: system_message,           # 고정 시스템 메시지
            on_event=sink,                                   # 출력 모으기
            validate_context=make_validate_context(mc.raw),  # 그 모델 기준 컨텍스트 검사
            apply_tool_overrides=(lambda defs: registry.apply_chat_completion_tool_overrides(defs, overrides)),   # 그 모델의 도구 덮어쓰기
        )                                                   # 끝
        deps.compaction_hook = make_compaction_hook(history, mc.raw)   # 자기 기록만 압축(부모 세션 저장 없음)
        parent_signal = host.get_signal() if host.get_signal is not None else None   # 부모 턴 중단 신호
        signal = AbortSignal() if (compat or parent_signal is None) else parent_signal   # cn: new AbortController() / ncn: 부모 취소 = 하위 취소
        await stream_chat_response(history, deps, headless=host.headless, model_name=mc.model or "", signal=signal)   # 같은 루프로 하위 대화 실행(streamChatResponse)
        if signal.aborted:                                   # escapeHandler — 취소 글을 하위 기록 끝에
            history.add_user_message(CANCELLED_MESSAGE)      # 취소 글을 user 로 넣음(cn escapeHandler)
        last = (history.get_history() or [{}])[-1]           # 마지막 메시지(chatHistory.at(-1))
        content = (last.get("message") or {}).get("content")   # 마지막 메시지 내용
        response = content if isinstance(content, str) else ""   # 글이 아니면 빈 글
        log.debug("Subagent execution completed: %s (%d chars)", name, len(response))   # cn 로그
        return {"success": True, "response": _truncate(response, limits.max_result_chars)}   # 성공(ncn.yaml 상한 적용)
    except Exception as e:                                   # cn: logger.error 후 success false
        log.error("Subagent execution failed: %s — %s", name, e)   # 로그
        return {"success": False, "response": "", "error": str(e)}   # 실패
    finally:                                                # 하위 LLM 연결 닫기
        if llm is not None and hasattr(llm, "aclose"):       # 닫을 수 있으면
            try:                                            # 닫기 실패 무시
                await llm.aclose()                          # 연결 정리
            except Exception:                                # 이미 닫힘 등
                pass                                        # 무시


async def run_subagent_tool(host: SubagentHost, agent: dict, args: dict, tool_call_id: Optional[str]) -> str:   # tools/subagent.ts run(찾기 뒤)
    """세션 확인(compat) → 동시 실행 제한(최상위만) → 실행 → 결과 글."""
    compat = cn_compat()                                     # cn 원래 동작인가
    if compat:                                               # cn: parentSessionId 가 없으면 실패
        sid = getattr(host.parent_history, "session_id", "") if host.parent_history is not None else ""   # 부모 세션 id
        if not sid:                                          # 헤드리스(sessionId "")
            raise Exception(NO_SESSION_MESSAGE)             # 세션 없음 오류(cn 원문)
    prompt = args.get("prompt")                              # 하위 대화 첫 사용자 메시지
    if compat or host.depth > 0:                             # cn 은 제한 없음 / 중첩은 세지 않음
        result = await execute_subagent(host, agent, prompt, tool_call_id=tool_call_id, compat=compat)   # 바로 실행
    else:                                                    # [추가 E] 최상위 호출은 동시 실행 수 제한
        async with host.semaphore():                         # 자리 얻기
            result = await execute_subagent(host, agent, prompt, tool_call_id=tool_call_id)   # 실행
    return format_subagent_output(result.get("response") or "", bool(result.get("success")))   # 결과 글
