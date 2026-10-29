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

from ncn.llm.client import APIUserAbortError, AbortSignal    # 중단
from ncn.llm.retry import (BackoffOptions, RetryMarker, chat_completion_stream_with_backoff,
                           is_context_length_error, with_exponential_backoff)   # 재시도
from ncn.loop import events as ev                            # 이벤트
from ncn.loop.history import ChatHistory, convert_from_unified_history_with_system_message   # 기록·변환
from ncn.loop.tool_calls import (AskUser, CheckPermission, ToolCall, get_request_tools, handle_tool_calls,
                                 process_tool_call_delta)    # 도구 호출 처리
from ncn.tools.base import Tool                              # 공용 도구 형식

log = logging.getLogger("ncn.loop.agent")                    # 로거

# 압축 훅(P3): await hook(stage, items, ctx) → (새 기록, 압축했나)
#   stage: "pre_user_message"(chat.ts, 사용자 메시지 넣기 전) | "pre_api"(호출 전) | "post_tool"(도구 결과로 한도 초과 시 강제)
#          | "normal"(도구를 쓴 뒤 일반) | "manual"(/compact)
#   ctx: system_message, tools(요청 도구 정의), is_headless, is_compacting, model_name, should_continue, tool_calls, deps
#   post_tool에서 압축 후에도 한도를 넘거나 압축 실패면 훅이 예외를 던진다(cn 원문 글은 훅이 만든다).
CompactionHook = Callable[[str, list, dict], Awaitable[tuple[list, bool]]]

# 컨텍스트 검사(P3/토큰 세기): validate(items, system_message, tools) → 잘라 맞춘 items. 그래도 넘으면
#   Exception("Context length validation failed: …")를 던진다(streamChatResponse.ts:221-251).
ValidateContext = Callable[[list, str, list], Union[list, Awaitable[list]]]


@dataclass
class LoopDeps:
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


@dataclass
class TurnResult:
    """한 턴의 결과."""
    response: str                                            # 헤드리스: 마지막 반복의 글(finalResponse) / 대화형: 전체 글(fullResponse)
    aborted: bool = False                                    # 중단으로 끝났나
    stopped_by_rejection: bool = False                       # 헤드리스 권한 거부로 일찍 끝났나(종료 코드는 0)
    manual_compaction: Any = None                            # /compact 결과(훅이 돌려준 값)


@dataclass
class StreamResult:
    """LLM 응답 한 번의 결과(processStreamingResponse 반환값)."""
    content: str                                             # 받은 글
    tool_calls: list[ToolCall]                               # 이름이 있는 도구 호출
    should_continue: bool                                    # 도구 호출이 있으면 참
    usage: Any = None                                        # 사용량(+model, cost_cents)
    aborted: bool = False                                    # 중단됐나


async def _maybe_await(v: Any) -> Any:
    """awaitable이면 기다린다."""
    if inspect.isawaitable(v):
        return await v
    return v


async def _resolve_tools(deps: LoopDeps) -> list[Tool]:
    """현재 전체 도구 목록(getAllAvailableTools)."""
    t = deps.tools
    if callable(t):                                          # 함수면 매번 부른다
        t = await _maybe_await(t())
    return list(t)


def _js_round(x: float) -> int:
    """JS Math.round(반올림, .5는 위로)."""
    return int(math.floor(x + 0.5))


def _with_usage_extras(usage: dict, model_name: str) -> dict:
    """세션 저장용 사용량에 model·cost_cents를 붙인다. 요금표(calculateRequestCost)는 옮기지 않고 cn 대체 계산($1/$2 per MTok)만."""
    u = dict(usage)                                          # 복사
    cost = ((u.get("prompt_tokens") or 0) * 1 + (u.get("completion_tokens") or 0) * 2) / 1_000_000   # calculateFallbackCost
    u["model"] = model_name
    u["cost_cents"] = _js_round(cost * 100)
    return u


# ─────────────────────────────────────────────────────────────────────────────
# LLM 응답 한 번(streamChatResponse.ts processStreamingResponse)
# ─────────────────────────────────────────────────────────────────────────────


async def process_streaming_response(items: list[dict], deps: LoopDeps, *, system_message: str, tools: list[dict],
                                     headless: bool, signal: AbortSignal, model_name: str) -> StreamResult:
    """컨텍스트 검사 → 메시지 변환 → 스트리밍(재시도) → 글·도구 호출 모으기."""
    if deps.validate_context is not None:                    # 한도 검사·끝부터 잘라 맞춤(실패면 예외)
        items = await _maybe_await(deps.validate_context(items, system_message, tools))
    messages = convert_from_unified_history_with_system_message(items, system_message)   # OpenAI 형식

    def factory(retry_signal: AbortSignal):                  # 시도마다 새 스트림
        return chat_completion_stream_with_backoff(deps.llm, messages, tools, retry_signal)

    ai_response = ""                                         # 받은 글
    calls: dict[str, ToolCall] = {}                          # id → 도구 호출(넣은 순서 유지)
    index_to_id: dict[Any, str] = {}                         # index → id
    full_usage: Any = None                                   # 사용량
    try:
        async for chunk in with_exponential_backoff(factory, signal, deps.backoff):
            if isinstance(chunk, RetryMarker):               # [ncn] 재시도 표지
                ev.safe_emit(deps.on_event, ev.retry(chunk.attempt, chunk.delay_ms, str(chunk.error)))
                if deps.discard_partial_on_retry:            # 부록 B: 이번 호출에서 받은 것 버리기
                    ai_response, calls, index_to_id, full_usage = "", {}, {}, None
                continue
            if isinstance(chunk, dict) and chunk.get("usage"):   # 사용량
                full_usage = chunk["usage"]
            if signal.aborted:                               # 중단 확인
                break
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if not choices or not choices[0]:                # 빈 choices(사용량 조각 등)
                continue
            delta = choices[0].get("delta")
            if not delta:
                continue
            if delta.get("content"):                         # 글 조각
                ai_response += delta["content"]
                ev.safe_emit(deps.on_event, ev.content(delta["content"]))
            for d in delta.get("tool_calls") or []:          # 도구 호출 조각
                process_tool_call_delta(d, calls, index_to_id)
        if full_usage:                                       # 세션 저장용 추가 정보
            full_usage = _with_usage_extras(full_usage, model_name)
            ev.safe_emit(deps.on_event, ev.usage(full_usage))
    except Exception as e:                                   # 실패
        if isinstance(e, APIUserAbortError) or signal.aborted:   # 중단: 받은 데까지
            log.debug("Stream aborted by user")
            return StreamResult(content=ai_response, tool_calls=[], should_continue=False, usage=full_usage, aborted=True)
        if is_context_length_error(e):                      # 컨텍스트 초과는 안내 글로 감싼다(`${error}` = "Error: " + message)
            raise RuntimeError(f"Context length exceeded: Error: {getattr(e, 'message', str(e))}") from e
        raise
    valid = []                                               # 이름이 없는 호출은 버린다
    for tc in calls.values():
        if not tc.name:
            log.error("Incomplete tool call id=%s argumentsStr=%r", tc.id, tc.arguments_str)
            continue
        valid.append(tc)
    return StreamResult(content=ai_response, tool_calls=valid, should_continue=len(valid) > 0,
                        usage=full_usage, aborted=signal.aborted)


# ─────────────────────────────────────────────────────────────────────────────
# 대화 루프(streamChatResponse.ts streamChatResponse)
# ─────────────────────────────────────────────────────────────────────────────


async def _compaction(deps: LoopDeps, history: ChatHistory, stage: str, items: list, ctx: dict) -> tuple[list, bool]:
    """압축 훅 호출. 압축했으면 기록을 바꾸고 새로 읽어 돌려준다."""
    if deps.compaction_hook is None:                         # 훅 없음 = 압축 안 함
        return items, False
    new_items, was = await deps.compaction_hook(stage, items, ctx)
    if was:                                                  # 서비스 기록 갱신(compactionHelpers.ts)
        history.set_history(new_items)
        return history.get_history(), True
    return items, False


async def stream_chat_response(history: ChatHistory, deps: LoopDeps, *, headless: bool, model_name: str,
                               signal: Optional[AbortSignal] = None, is_compacting: bool = False,
                               items: Optional[list[dict]] = None) -> TurnResult:
    """도구 호출이 없을 때까지 LLM ↔ 도구를 반복한다.
    is_compacting=True(P3 압축 요약 호출)이면 items를 그대로 쓰고 기록에서 다시 읽지 않는다(호출 전 압축 검사도 건너뜀)."""
    signal = signal or AbortSignal()                         # 중단 신호
    chat_items = list(items) if items is not None else history.get_history()   # 이번 루프의 기록 보기
    full_response = ""                                       # 전체 글
    final_response = ""                                      # 마지막 반복 글
    compaction_occurred = False                              # 이번 턴에 압축이 있었나

    while True:
        if not is_compacting:                                # 1 기록 새로 읽기(압축 중이면 받은 것 그대로)
            chat_items = history.get_history()
        system_message = await _maybe_await(deps.system_message())   # 2 시스템 메시지(모드 반영)
        all_tools = await _resolve_tools(deps)               # 3 도구 목록
        tools = await get_request_tools(all_tools, deps.list_permission or deps.check_permission, headless)   # 목록용 판정(인자 {})
        if deps.apply_tool_overrides is not None:            # toolOverrides
            tools = deps.apply_tool_overrides(tools)
        ctx = {"system_message": system_message, "tools": tools, "is_headless": headless,
               "is_compacting": is_compacting, "model_name": model_name, "deps": deps, "signal": signal}   # 훅에 줄 정보

        if not is_compacting:                                # 4 호출 전 압축 검사
            chat_items, was = await _compaction(deps, history, "pre_api", chat_items, ctx)
            compaction_occurred = compaction_occurred or was

        result = await process_streaming_response(chat_items, deps, system_message=system_message, tools=tools,
                                                   headless=headless, signal=signal, model_name=model_name)   # 5·6
        if signal.aborted:                                   # 중단: 기록에 넣지 않고 끝
            return TurnResult(response=final_response or result.content or full_response, aborted=True)

        content = result.content                             # 이번 글
        full_response += content
        if not result.should_continue:                       # updateFinalResponse
            final_response = content
        elif headless and content:
            final_response = content
        if content:                                          # onContentComplete
            ev.safe_emit(deps.on_event, ev.content_complete(content))

        should_return = await handle_tool_calls(result.tool_calls, content, history, tools=all_tools,
                                                check_permission=deps.check_permission, ask_user=deps.ask_user,
                                                on_event=deps.on_event, headless=headless, usage=result.usage)   # 7
        if should_return:                                    # 헤드리스 거부 → 턴 끝(종료 코드 0)
            return TurnResult(response=final_response or content or full_response, stopped_by_rejection=True)

        if result.tool_calls:                                # 8-② 도구 결과로 한도 초과면 강제 압축(훅이 판정)
            chat_items = history.get_history()
            chat_items, was = await _compaction(deps, history, "post_tool", chat_items,
                                                {**ctx, "tool_calls": result.tool_calls})
            compaction_occurred = compaction_occurred or was
        if result.should_continue:                           # 8-③ 일반 압축 검사
            chat_items = history.get_history()
            chat_items, was = await _compaction(deps, history, "normal", chat_items,
                                                {**ctx, "should_continue": True})
            compaction_occurred = compaction_occurred or was

        auto_continue = False                                # 9 압축 후 이어 가기
        if compaction_occurred and not result.should_continue:
            log.debug("Auto-compaction occurred during this turn - automatically continuing session")
            history.add_user_message("continue")
            chat_items = history.get_history()
            auto_continue = True
            compaction_occurred = False                      # 무한 반복 방지
        if not result.should_continue and not auto_continue:
            break

    return TurnResult(response=final_response if headless else full_response)


# ─────────────────────────────────────────────────────────────────────────────
# 한 턴(commands/chat.ts processMessage — 헤드리스·ACP 공용)
# ─────────────────────────────────────────────────────────────────────────────


async def run_turn(history: ChatHistory, user_message: str, deps: LoopDeps, *, headless: bool, model_name: str,
                   signal: Optional[AbortSignal] = None) -> TurnResult:
    """사용자 메시지 하나를 처리한다: (/compact면 수동 압축) → 넣기 전 압축 검사 → 사용자 메시지 추가 → 루프."""
    if user_message.strip() == "/compact":                   # 슬래시 명령(헤드리스)
        if deps.compaction_hook is None:
            return TurnResult(response="")
        items, _was = await deps.compaction_hook("manual", history.get_history(),
                                                 {"is_headless": headless, "model_name": model_name, "deps": deps})
        return TurnResult(response="", manual_compaction=items)
    if deps.compaction_hook is not None:                     # 사용자 메시지 넣기 전 자동 압축 검사
        items, was = await deps.compaction_hook("pre_user_message", history.get_history(),
                                                {"is_headless": headless, "model_name": model_name, "deps": deps})
        if was:
            history.set_history(items)
    history.add_user_message(user_message)                   # 사용자 메시지
    # cn은 압축 위치가 있으면 getHistoryForLLM으로 시작하지만 루프 첫 반복에서 getHistory()로 다시 읽으므로 결과가 같다
    return await stream_chat_response(history, deps, headless=headless, model_name=model_name, signal=signal)
