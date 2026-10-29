# ncn/loop/agent.py — 에이전트 루프(한 턴). cn extensions/cli/src/stream/streamChatResponse.ts
#   + commands/chat.ts processMessage(헤드리스 한 턴) + stream/streamChatResponse.compactionHelpers.ts(호출 위치만)
# 다른 모듈(도구·권한·설정·시스템 메시지·압축)은 LoopDeps로 주입받는다 — 루프는 그 내부를 모른다.
#
# 반복 한 번(cn 순서 그대로):
#   1 기록 새로 읽기 → 2 시스템 메시지 → 3 요청 도구 목록(헤드리스는 ask 제외) + toolOverrides
#   → 4 압축 검사 ①(pre_api, 압축 중이 아니면) → 5 컨텍스트 검사·끝부터 잘라 맞춤(validate_context)
#   → 6 LLM 스트리밍(재시도) → 7 도구 처리(헤드리스 거부면 턴 끝) → 8 압축 검사 ②(post_tool) ③(normal)
#   → 9 이번 턴에 압축이 있었고 멈추려 하면 "continue"를 붙여 한 번 더
from __future__ import annotations                           # 타입 주석 지연 평가

import inspect                                               # 동기/비동기 콜백 둘 다 받기
import logging                                               # 로그
import math                                                  # Math.round 흉내
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Awaitable, Callable, Optional, Union  # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치(부록B C1)
from ncn.llm.client import APIUserAbortError, AbortSignal    # 중단
from ncn.llm.retry import (BackoffOptions, RetryMarker, chat_completion_stream_with_backoff,  # 재시도·백오프 도구와 컨텍스트 초과 판정
                           is_context_length_error, with_exponential_backoff)   # 재시도
from ncn.loop import events as ev                            # 이벤트
from ncn.loop.history import ChatHistory, convert_from_unified_history_with_system_message   # 기록·변환
from ncn.loop.tool_calls import (AskUser, CheckPermission, ToolCall, get_request_tools, handle_tool_calls,  # 도구 호출 처리 함수와 콜백 형식
                                 process_tool_call_delta)    # 도구 호출 처리
from ncn.tools.base import Tool                              # 공용 도구 형식

log = logging.getLogger("ncn.loop.agent")                    # 로거

TURN_START = "turn_start"                                    # [ncn E6] run_turn 시작 이벤트(감사 기록용 — ncn/audit.py)
TURN_END = "turn_end"                                        # [ncn E6] run_turn 끝 이벤트(aborted·stopped_by_*·error)

# 압축 훅(P3): await hook(stage, items, ctx) → (새 기록, 압축했나)
#   stage: "pre_user_message"(chat.ts, 사용자 메시지 넣기 전) | "pre_api"(호출 전) | "post_tool"(도구 결과로 한도 초과 시 강제)
#          | "normal"(도구를 쓴 뒤 일반) | "manual"(/compact)
#   ctx: system_message, tools(요청 도구 정의), is_headless, is_compacting, model_name, should_continue, tool_calls, deps
#   post_tool에서 압축 후에도 한도를 넘거나 압축 실패면 훅이 예외를 던진다(cn 원문 글은 훅이 만든다).
CompactionHook = Callable[[str, list, dict], Awaitable[tuple[list, bool]]]  # 압축 훅의 함수 형식

# 컨텍스트 검사(P3/토큰 세기): validate(items, system_message, tools) → 잘라 맞춘 items. 그래도 넘으면
#   Exception("Context length validation failed: …")를 던진다(streamChatResponse.ts:221-251).
ValidateContext = Callable[[list, str, list], Union[list, Awaitable[list]]]  # 컨텍스트 검사 함수의 형식


@dataclass                                                  # LoopDeps를 자료 구조로 정의
class LoopDeps:                                             # 루프 의존성 묶음 클래스
    """루프가 쓰는 바깥 모듈들(의존성 주입)."""
    llm: Any                                                 # ncn.llm.client.LLMClient(또는 stream_chat·model을 가진 같은 모양)
    tools: Union[list[Tool], Callable[[], Any]]              # 이미 구성된 전체 도구 목록(모드가 바뀌면 함수로 주면 매 반복 다시 읽음)
    check_permission: CheckPermission                        # (도구, 인자) → "allow"|"ask"|"exclude"
    ask_user: Optional[AskUser]                              # 승인 질문(name, args, preview, tool_call_id) → "allow"|"allow_always"|"reject". 헤드리스면 None
    system_message: Callable[[], Any]                        # 매 반복 시스템 메시지(모드 반영) — str 또는 awaitable
    on_event: ev.EventSink = ev.noop                         # 화면/ACP 이벤트(종류는 ncn/loop/events.py)
    compaction_hook: Optional[CompactionHook] = None         # P3 압축(없으면 압축 안 함)
    validate_context: Optional[ValidateContext] = None       # P3 컨텍스트 검사(없으면 검사 안 함)
    apply_tool_overrides: Optional[Callable[[list[dict]], list[dict]]] = None   # model.chatOptions.toolOverrides 적용
    backoff: BackoffOptions = field(default_factory=BackoffOptions)   # 재시도 옵션(cn 기본값)
    discard_partial_on_retry: bool = True                    # [ncn 부록 B] 재시도 시 그 호출의 조각을 버림(False면 cn처럼 중복)
    list_permission: Optional[CheckPermission] = None        # 도구 목록용 판정(cn getRequestTools: 동적 평가 없이 정적 정책만). 없으면 check_permission
    loop_guard: Any = None                                   # [ncn E5] ncn.loopguard.LoopGuard — 반복 상한·연속 실패 감시(None 이면 cn 처럼 상한 없음)


@dataclass                                                  # TurnResult를 자료 구조로 정의
class TurnResult:                                           # 한 턴 결과 클래스
    """한 턴의 결과."""
    response: str                                            # 헤드리스: 마지막 반복의 글(finalResponse) / 대화형: 전체 글(fullResponse)
    aborted: bool = False                                    # 중단으로 끝났나
    stopped_by_rejection: bool = False                       # 헤드리스 권한 거부로 일찍 끝났나(종료 코드는 0)
    manual_compaction: Any = None                            # /compact 결과(훅이 돌려준 값)
    stopped_by_loop_guard: Optional[str] = None              # [ncn E5] 반복 감시로 멈춘 이유(ncn.loopguard.REASON_*), 아니면 None


@dataclass                                                  # StreamResult를 자료 구조로 정의
class StreamResult:                                         # LLM 응답 한 번의 결과 클래스
    """LLM 응답 한 번의 결과(processStreamingResponse 반환값)."""
    content: str                                             # 받은 글
    tool_calls: list[ToolCall]                               # 이름이 있는 도구 호출
    should_continue: bool                                    # 도구 호출이 있으면 참
    usage: Any = None                                        # 사용량(+model, cost_cents)
    aborted: bool = False                                    # 중단됐나


async def _maybe_await(v: Any) -> Any:                      # 동기/비동기 값을 모두 받는 보조 함수
    """awaitable이면 기다린다."""
    if inspect.isawaitable(v):                              # 기다릴 수 있는 값이면
        return await v                                      # 완료를 기다려 결과를 돌려준다
    return v                                                # 아니면 값을 그대로 돌려준다


async def _resolve_tools(deps: LoopDeps) -> list[Tool]:     # 매 반복 현재 도구 목록을 얻는 함수
    """현재 전체 도구 목록(getAllAvailableTools)."""
    t = deps.tools                                          # 도구 목록 또는 목록을 만드는 함수
    if callable(t):                                          # 함수면 매번 부른다
        t = await _maybe_await(t())                         # 함수면 호출하고 결과가 awaitable이면 기다린다
    return list(t)                                          # 목록 복사본을 돌려준다(원본 보호)


def _js_round(x: float) -> int:                             # JS Math.round 흉내 함수
    """JS Math.round(반올림, .5는 위로)."""
    return int(math.floor(x + 0.5))                         # 음수 쪽도 JS와 같게 floor(x+0.5)


def _with_usage_extras(usage: dict, model_name: str) -> dict:  # 세션 저장용 사용량 보강 함수
    """세션 저장용 사용량에 model·cost_cents를 붙인다. 요금표(calculateRequestCost)는 옮기지 않고 cn 대체 계산($1/$2 per MTok)만."""
    u = dict(usage)                                          # 복사
    cost = ((u.get("prompt_tokens") or 0) * 1 + (u.get("completion_tokens") or 0) * 2) / 1_000_000  # 대체 요금 계산(입력 $1, 출력 $2 per MTok)
    u["model"] = model_name                                 # 어떤 모델의 사용량인지 기록
    u["cost_cents"] = _js_round(cost * 100)                 # 달러를 센트로 바꿔 반올림
    return u                                                # 보강된 사용량 dict 반환


# ─────────────────────────────────────────────────────────────────────────────
# LLM 응답 한 번(streamChatResponse.ts processStreamingResponse)
# ─────────────────────────────────────────────────────────────────────────────


async def process_streaming_response(items: list[dict], deps: LoopDeps, *, system_message: str, tools: list[dict],  # LLM 응답 한 번을 처리하는 함수
                                     headless: bool, signal: AbortSignal, model_name: str) -> StreamResult:  # 헤드리스 여부·중단 신호·모델명은 키워드 전용
    """컨텍스트 검사 → 메시지 변환 → 스트리밍(재시도) → 글·도구 호출 모으기."""
    if deps.validate_context is not None:                    # 한도 검사·끝부터 잘라 맞춤(실패면 예외)
        items = await _maybe_await(deps.validate_context(items, system_message, tools))  # 검사 함수가 기록을 잘라 맞춘 결과로 교체
    messages = convert_from_unified_history_with_system_message(items, system_message)   # OpenAI 형식

    def factory(retry_signal: AbortSignal):                  # 시도마다 새 스트림
        return chat_completion_stream_with_backoff(deps.llm, messages, tools, retry_signal)  # 재시도 래퍼를 거치는 스트림 생성

    ai_response = ""                                         # 받은 글
    calls: dict[str, ToolCall] = {}                          # id → 도구 호출(넣은 순서 유지)
    index_to_id: dict[Any, str] = {}                        # 도구 호출 index를 id로 잇는 표(조각이 index로만 오는 경우)
    full_usage: Any = None                                   # 사용량
    try:                                                    # 스트리밍 전체를 예외 처리로 감싼다
        async for chunk in with_exponential_backoff(factory, signal, deps.backoff):  # 백오프 재시도 아래에서 조각을 하나씩 받는다
            if isinstance(chunk, RetryMarker):               # [ncn] 재시도 표지
                ev.safe_emit(deps.on_event, ev.retry(chunk.attempt, chunk.delay_ms, str(chunk.error)))  # 화면에 재시도를 알려 이번 호출의 글을 지우게 한다
                if deps.discard_partial_on_retry:            # 부록 B: 이번 호출에서 받은 것 버리기
                    ai_response, calls, index_to_id, full_usage = "", {}, {}, None  # 부록 B: 받은 글·도구 호출·사용량을 모두 초기화
                continue                                    # 표지는 조각이 아니므로 다음 조각으로
            if isinstance(chunk, dict) and chunk.get("usage"):   # 사용량
                full_usage = chunk["usage"]                 # 마지막으로 온 사용량을 보관
            if signal.aborted:                               # 중단 확인
                break                                       # 중단됐으면 받기를 멈춘다
            choices = chunk.get("choices") if isinstance(chunk, dict) else None  # choices를 안전하게 꺼낸다(dict가 아니면 None)
            if not choices or not choices[0]:                # 빈 choices(사용량 조각 등)
                continue                                    # 내용 없는 조각은 건너뛴다
            delta = choices[0].get("delta")                 # 이번 조각의 delta
            if not delta:                                   # delta가 비었으면
                continue                                    # 건너뛴다
            if delta.get("content"):                         # 글 조각
                ai_response += delta["content"]             # 글 조각을 이어 붙인다
                ev.safe_emit(deps.on_event, ev.content(delta["content"]))  # 화면에 글 조각 이벤트를 보낸다
            for d in delta.get("tool_calls") or []:          # 도구 호출 조각
                process_tool_call_delta(d, calls, index_to_id)  # 도구 호출 조각을 id별로 합친다
        if full_usage:                                       # 세션 저장용 추가 정보
            full_usage = _with_usage_extras(full_usage, model_name)  # model·cost_cents를 붙인다
            ev.safe_emit(deps.on_event, ev.usage(full_usage))  # 사용량 이벤트 전달
    except Exception as e:                                   # 실패
        if isinstance(e, APIUserAbortError) or signal.aborted:   # 중단: 받은 데까지
            log.debug("Stream aborted by user")             # 사용자 중단은 오류가 아니라 조용히 끝낸다
            return StreamResult(content=ai_response, tool_calls=[], should_continue=False, usage=full_usage, aborted=True)  # 받은 데까지 담아 중단 결과를 돌려준다
        if is_context_length_error(e):                      # 컨텍스트 초과는 안내 글로 감싼다(`${error}` = "Error: " + message)
            raise RuntimeError(f"Context length exceeded: Error: {getattr(e, 'message', str(e))}") from e  # cn 원문처럼 안내 글로 감싸 다시 던진다
        raise                                               # 그 밖의 오류는 그대로 올린다
    valid = []                                               # 이름이 없는 호출은 버린다
    for tc in calls.values():                               # 모은 도구 호출을 훑는다
        if not tc.name:                                     # 이름이 비었으면(조각이 덜 온 호출)
            log.error("Incomplete tool call id=%s argumentsStr=%r", tc.id, tc.arguments_str)  # 불완전 호출임을 로그로 남기고
            continue                                        # 버린다
        valid.append(tc)                                    # 온전한 호출만 모은다
    return StreamResult(content=ai_response, tool_calls=valid, should_continue=len(valid) > 0,  # 글·도구 호출·사용량을 묶어 결과 반환
                        usage=full_usage, aborted=signal.aborted)  # 도구 호출이 있으면 루프를 계속(should_continue)


# ─────────────────────────────────────────────────────────────────────────────
# 대화 루프(streamChatResponse.ts streamChatResponse)
# ─────────────────────────────────────────────────────────────────────────────


async def _compaction(deps: LoopDeps, history: ChatHistory, stage: str, items: list, ctx: dict) -> tuple[list, bool]:  # 압축 훅을 감싸는 보조 함수
    """압축 훅 호출. 압축했으면 기록을 바꾸고 새로 읽어 돌려준다."""
    if deps.compaction_hook is None:                         # 훅 없음 = 압축 안 함
        return items, False                                 # 압축하지 않았음을 돌려준다
    new_items, was = await deps.compaction_hook(stage, items, ctx)  # 훅에 단계·기록·정보를 넘겨 새 기록과 압축 여부를 받는다
    if was:                                                  # 서비스 기록 갱신(compactionHelpers.ts)
        history.set_history(new_items)                      # 압축됐으면 서비스 기록을 새 것으로 교체
        return history.get_history(), True                  # 기록을 다시 읽어 압축했다고 알린다
    return items, False                                     # 압축이 없으면 기존 기록 그대로


async def stream_chat_response(history: ChatHistory, deps: LoopDeps, *, headless: bool, model_name: str,  # 도구 호출이 없을 때까지 도는 대화 루프
                               signal: Optional[AbortSignal] = None, is_compacting: bool = False,  # 중단 신호와 압축 여부는 선택 인자
                               items: Optional[list[dict]] = None) -> TurnResult:  # 압축 요약 호출은 items를 직접 받는다
    """도구 호출이 없을 때까지 LLM ↔ 도구를 반복한다.
    is_compacting=True(P3 압축 요약 호출)이면 items를 그대로 쓰고 기록에서 다시 읽지 않는다(호출 전 압축 검사도 건너뜀)."""
    signal = signal or AbortSignal()                         # 중단 신호
    chat_items = list(items) if items is not None else history.get_history()   # 이번 루프의 기록 보기
    full_response = ""                                       # 전체 글
    final_response = ""                                      # 마지막 반복 글
    compaction_occurred = False                              # 이번 턴에 압축이 있었나
    guard = deps.loop_guard if not is_compacting else None   # [ncn E5] 압축 요약 호출은 감시하지 않음
    if guard is not None:                                    # [ncn E5] 감시기가 있으면
        guard.begin_turn()                                   # [ncn E5] 턴마다 새로 센다

    while True:                                             # 한 반복이 한 번의 LLM 호출 + 도구 처리
        if guard is not None and not await guard.before_iteration(headless=headless):   # [ncn E5] 상한·연속 실패 → 묻고 멈춤이면
            return TurnResult(response=final_response or full_response, stopped_by_loop_guard=guard.triggered)   # [ncn E5] 턴 끝(기록은 그대로)
        if not is_compacting:                                # 1 기록 새로 읽기(압축 중이면 받은 것 그대로)
            chat_items = history.get_history()              # 압축 중이 아니면 최신 기록을 다시 읽는다
        system_message = await _maybe_await(deps.system_message())   # 2 시스템 메시지(모드 반영)
        all_tools = await _resolve_tools(deps)               # 3 도구 목록
        tools = await get_request_tools(all_tools, deps.list_permission or deps.check_permission, headless)   # 목록용 판정(인자 {})
        if deps.apply_tool_overrides is not None:           # 모델별 도구 덮어쓰기가 있으면
            tools = deps.apply_tool_overrides(tools)        # 도구 정의에 적용한다
        ctx = {"system_message": system_message, "tools": tools, "is_headless": headless,  # 압축 훅에 넘길 정보 묶음
               "is_compacting": is_compacting, "model_name": model_name, "deps": deps, "signal": signal}   # 훅에 줄 정보

        if not is_compacting:                                # 4 호출 전 압축 검사
            chat_items, was = await _compaction(deps, history, "pre_api", chat_items, ctx)  # pre_api 단계 압축 검사
            compaction_occurred = compaction_occurred or was  # 한 번이라도 압축했으면 기억

        result = await process_streaming_response(chat_items, deps, system_message=system_message, tools=tools,  # LLM 응답 한 번 처리(5 컨텍스트 검사, 6 스트리밍)
                                                   headless=headless, signal=signal, model_name=model_name)  # 요청 도구 정의와 모델명을 함께 넘긴다
        if signal.aborted:                                   # 중단: 기록에 넣지 않고 끝
            return TurnResult(response=final_response or result.content or full_response, aborted=True)  # 중단되면 지금까지의 글로 결과를 만들고 끝

        content = result.content                             # 이번 글
        full_response += content                            # 전체 글에 이번 글을 누적
        if not result.should_continue:                      # 도구 호출이 없으면 이번 글이 최종 응답
            final_response = content                        # 마지막 반복의 글을 최종 응답으로 갱신
        elif headless and content:                          # 헤드리스는 도구를 쓰는 반복에서도 글이 있으면 갱신
            final_response = content                        # 최종 응답을 이번 글로
        if content:                                         # 글이 있을 때만
            ev.safe_emit(deps.on_event, ev.content_complete(content))  # 글 끝 이벤트를 보낸다(onContentComplete)

        if is_compacting and result.tool_calls and not cn_compat():   # [ncn 부록B C1] 요약 호출 중 도구 호출 — cn은 실제로 실행한다
            log.warning("Ignoring %d tool call(s) requested during compaction", len(result.tool_calls))   # 실행·기록 없이 무시
            return TurnResult(response=content or full_response)   # 받은 글만 요약으로(요청 본문의 tools 는 cn과 같게 보냈다)

        should_return = await handle_tool_calls(result.tool_calls, content, history, tools=all_tools,  # 7 도구 호출 처리(권한 확인·실행·기록 추가)
                                                check_permission=deps.check_permission, ask_user=deps.ask_user,  # 권한 판정과 승인 질문 콜백을 넘긴다
                                                on_event=deps.on_event, headless=headless, usage=result.usage)  # 이벤트·헤드리스 여부·사용량도 함께
        if should_return:                                    # 헤드리스 거부 → 턴 끝(종료 코드 0)
            return TurnResult(response=final_response or content or full_response, stopped_by_rejection=True)  # 거부로 끝난 턴의 결과(종료 코드 0)

        if result.tool_calls:                                # 8-② 도구 결과로 한도 초과면 강제 압축(훅이 판정)
            chat_items = history.get_history()              # 도구 결과가 들어간 최신 기록
            chat_items, was = await _compaction(deps, history, "post_tool", chat_items,  # post_tool 단계 압축 검사
                                                {**ctx, "tool_calls": result.tool_calls})  # 도구 호출 목록을 훅 정보에 추가
            compaction_occurred = compaction_occurred or was  # 압축 여부 누적
        if result.should_continue:                           # 8-③ 일반 압축 검사
            chat_items = history.get_history()              # 도구를 쓴 뒤의 최신 기록
            chat_items, was = await _compaction(deps, history, "normal", chat_items,  # normal 단계 압축 검사
                                                {**ctx, "should_continue": True})  # 계속 진행 중임을 훅에 알린다
            compaction_occurred = compaction_occurred or was  # 압축 여부 누적

        auto_continue = False                                # 9 압축 후 이어 가기
        if compaction_occurred and not result.should_continue:  # 압축이 있었는데 멈추려 하면
            log.debug("Auto-compaction occurred during this turn - automatically continuing session")  # 자동 이어 가기를 로그로 남긴다
            history.add_user_message("continue")            # "continue"를 사용자 메시지로 넣어 한 번 더 돈다
            chat_items = history.get_history()              # 새 기록 읽기
            auto_continue = True                            # 이어 가기 표시
            compaction_occurred = False                      # 무한 반복 방지
        if not result.should_continue and not auto_continue:  # 도구도 없고 이어 가기도 아니면
            break                                           # 루프를 끝낸다

    return TurnResult(response=final_response if headless else full_response)  # 헤드리스는 마지막 반복 글, 대화형은 전체 글


# ─────────────────────────────────────────────────────────────────────────────
# 한 턴(commands/chat.ts processMessage — 헤드리스·ACP 공용)
# ─────────────────────────────────────────────────────────────────────────────


async def run_turn(history: ChatHistory, user_message: str, deps: LoopDeps, *, headless: bool, model_name: str,  # 사용자 메시지 한 건을 처리하는 한 턴 함수
                   signal: Optional[AbortSignal] = None, context_items: Optional[list] = None) -> TurnResult:  # 중단 신호·첨부 맥락은 선택
    """사용자 메시지 하나를 처리한다: (/compact면 수동 압축) → 넣기 전 압축 검사 → 사용자 메시지 추가 → 루프.
    context_items: 대화형 @파일 첨부(useChat.helpers.ts formatMessageWithFiles의 contextItems) — ACP 리소스가 여기로 온다."""
    if user_message.strip() == "/compact":                   # 슬래시 명령(헤드리스)
        if deps.compaction_hook is None:                    # 압축 훅이 없으면
            return TurnResult(response="")                  # 아무 것도 하지 않고 빈 응답
        items, _was = await deps.compaction_hook("manual", history.get_history(),  # 수동 압축을 훅에 맡긴다
                                                 {"is_headless": headless, "model_name": model_name, "deps": deps})  # 헤드리스 여부·모델명·의존성을 정보로 넘긴다
        return TurnResult(response="", manual_compaction=items)  # 압축 결과를 담아 돌려준다
    if deps.compaction_hook is not None:                     # 사용자 메시지 넣기 전 자동 압축 검사
        items, was = await deps.compaction_hook("pre_user_message", history.get_history(),  # 훅에 pre_user_message 단계 요청
                                                {"is_headless": headless, "model_name": model_name, "deps": deps})  # 같은 정보 묶음을 넘긴다
        if was:                                             # 압축됐으면
            history.set_history(items)                      # 서비스 기록을 교체
    history.add_user_message(user_message, context_items)    # 사용자 메시지(+첨부 맥락 — 없으면 [])
    # cn은 압축 위치가 있으면 getHistoryForLLM으로 시작하지만 루프 첫 반복에서 getHistory()로 다시 읽으므로 결과가 같다
    ev.safe_emit(deps.on_event, {"type": TURN_START})        # [ncn E6] 감사 기록용 턴 시작(화면은 모르는 종류라 무시)
    try:                                                    # [ncn E6] 오류도 턴 끝으로 남긴다
        result = await stream_chat_response(history, deps, headless=headless, model_name=model_name, signal=signal)  # 루프를 돌려 턴 결과를 얻는다
    except BaseException as e:                               # [ncn E6] 턴 오류·취소
        ev.safe_emit(deps.on_event, {"type": TURN_END, "error": f"{type(e).__name__}: {e}"})   # [ncn E6] 오류 요약(감사 기록이 첫 줄만 남김)
        raise                                               # 그대로 올린다
    ev.safe_emit(deps.on_event, {"type": TURN_END, "aborted": result.aborted,   # [ncn E6] 턴 끝(멈춘 이유)
                                 "stopped_by_rejection": result.stopped_by_rejection, "stopped_by_loop_guard": result.stopped_by_loop_guard})   # (이어짐)
    return result                                           # 턴 결과
