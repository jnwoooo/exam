# ncn/compaction.py — 대화 압축(요약으로 기록 갈아 끼우기). cn extensions/cli/src/compaction.ts(compactChatHistory·pruneLastMessage)
#   + stream/streamChatResponse.autoCompaction.ts(handleAutoCompaction) + stream/streamChatResponse.compactionHelpers.ts(단계별 검사)
#   + commands/chat.ts(handleAutoCompaction 헤드리스 래퍼·handleManualCompaction) + ui/hooks/useChat.compaction.ts(대화형 /compact)
#   + slashCommands.ts(/compact 판정)
# 루프(ncn/loop/agent.py)는 압축 내부를 모르고 훅 하나(make_compaction_hook 이 만든 함수)만 부른다:
#   await hook(stage, items, ctx) → (새 기록, 압축했나). stage 는 pre_user_message·pre_api·post_tool·normal·manual.
# 화면 알림 규칙(cn 그대로):
#   - 헤드리스: 자동 압축은 아무 것도 출력하지 않는다(notifyCompactionStart·handleCompactionSuccess 가 isHeadless 면 바로 return).
#     수동 /compact 만 stdout 에 JSON 한 줄(성공 줄은 ncn/app.py, 실패 줄은 이 모듈).
#   - 대화형(ACP): 루프 안 자동 압축(pre_api·post_tool·normal)은 onSystemMessage → 기록에 system 메시지 추가 + system_message 이벤트.
#     사용자 메시지 전 자동 압축(pre_user_message)은 알림 없음(TUI 래퍼가 onSystemMessage 를 넘기지 않음).
#     수동 /compact 는 성공·취소·실패 system 메시지를 기록 끝에 붙인다.
from __future__ import annotations                           # 타입 주석 지연 평가

import dataclasses                                           # LoopDeps 복사(이벤트 수신자만 바꿈)
import inspect                                               # 동기/비동기 값 구분
import logging                                               # 로그(stdout 금지)
from typing import Any, Callable, Optional                   # 타입

from ncn import headless as hl                               # 헤드리스 출력 줄·오류 글
from ncn.llm.client import AbortSignal                       # 중단 신호(cn AbortController 자리)
from ncn.loop import events as ev                            # 루프 이벤트 종류·생성기
from ncn.tokenizer import (count_chat_history_item_tokens, count_chat_history_tokens,  # 토큰 세기(cn util/tokenizer.ts 규칙)
                           get_auto_compact_message, get_model_context_limit, get_model_max_tokens,  # 한도·안내 글
                           should_auto_compact, validate_context_length)  # 압축 필요 판정·한도 검사

log = logging.getLogger("ncn.compaction")                    # 로거

# 요약 요청 글(compaction.ts:40-41 원문 그대로)
COMPACTION_PROMPT = (                                        # 요약을 시키는 사용자 메시지 글
    "Please provide a concise summary of our conversation so far, capturing the key context, decisions made, "  # 원문 1/4
    "and current state. Format this as a single comprehensive message that preserves all important information "  # 원문 2/4
    "needed to continue our work. You do not need to recap the system message, as this will remain. Make sure it "  # 원문 3/4
    "is clear what the current stream of work was at the very end prior to compaction so that you can continue "  # 원문 4/4 앞부분
    "exactly where you left off without missing any information.")  # 원문 끝
COMPACTION_PROMPT_TOKENS = 150                               # 요약 요청 글의 넉넉한 토큰 추정치(compaction.ts:43)
SAFETY_BUFFER = 100                                          # 도구 실행 뒤 한도 검사의 안전 여유(compactionHelpers.ts:94)

AUTO_SUCCESS_MESSAGE = "Chat history auto-compacted successfully."   # 자동 압축 성공 안내(autoCompaction.ts:64)
MANUAL_SUCCESS_MESSAGE = "Chat history compacted successfully."      # 대화형 /compact 성공(useChat.compaction.ts:75)
MANUAL_CANCELLED_MESSAGE = "Compaction cancelled."                   # 대화형 /compact 취소(useChat.compaction.ts:89)
POST_TOOL_FAILED = "Context limit exceeded and compaction failed. Unable to continue."   # 강제 압축 실패(compactionHelpers.ts:160)


def _role(item: dict) -> Any:                               # 기록 항목의 역할을 꺼내는 보조 함수
    """item.message.role(없으면 None)."""
    return (item.get("message") or {}).get("role")          # message 가 없을 때도 안전하게


async def _maybe_await(v: Any) -> Any:                      # 동기/비동기 값을 모두 받는 보조 함수
    """awaitable 이면 기다린다."""
    if inspect.isawaitable(v):                              # 기다릴 수 있는 값이면
        return await v                                      # 결과를 기다려 돌려준다
    return v                                                # 아니면 그대로


# ─────────────────────────────────────────────────────────────────────────────
# 잘라내기·요약(compaction.ts)
# ─────────────────────────────────────────────────────────────────────────────


def prune_last_message(items: list[dict]) -> list[dict]:    # 끝에서부터 잘라내는 함수(pruneLastMessage)
    """끝에서 두 번째가 도구 호출 assistant 이거나 user 면 둘, 아니면 하나를 뺀다. 하나뿐이면 빈 목록."""
    if not items:                                           # 비었으면
        return items                                        # 그대로
    if len(items) == 1:                                     # 하나뿐이면
        return []                                           # 항상 빈 목록
    second = items[-2].get("message") or {}                 # 끝에서 두 번째 메시지
    if second.get("role") == "assistant" and len(second.get("toolCalls") or []) > 0:   # 도구 호출 assistant 면
        return items[:-2]                                   # 마지막 둘 제거
    if second.get("role") == "user":                        # user 면
        return items[:-2]                                   # 마지막 둘 제거
    return items[:-1]                                       # 그 밖엔 마지막 하나만


def _compaction_sink(deps: Any, on_stream_content: Optional[Callable[[str], None]]):   # 요약 호출용 이벤트 수신자를 만드는 함수
    """요약 호출의 이벤트 수신자와 '모은 글 읽기' 함수를 만든다.
    cn 요약 호출의 콜백은 onContent·onContentComplete 뿐(도구 카드 등은 없음) → 글만 모으고, ncn 추가 이벤트(사용량·재시도)는 원래 수신자로."""
    state = {"done": "", "partial": ""}                     # 끝난 호출들의 글 / 지금 호출의 글
    original = deps.on_event                                # 원래 수신자(세션 사용량 저장·ACP 재시도 알림)

    def sink(e: dict) -> None:                              # 요약 호출의 이벤트 수신자
        t = e.get("type")                                   # 이벤트 종류
        if t == ev.CONTENT:                                 # 글 조각 → compactionContent += content
            text = e.get("text") or ""                      # 조각 글
            state["partial"] += text                        # 지금 호출의 글에 이어 붙임
            if on_stream_content is not None:               # callbacks.onStreamContent — 콜백이 있으면
                on_stream_content(text)                     # 넘겨받은 콜백에 조각 전달
        elif t == ev.CONTENT_COMPLETE:                      # 한 호출의 글 끝(onContentComplete → onStreamComplete)
            state["done"] += state["partial"]               # 지금 호출 글을 확정
            state["partial"] = ""                           # 다음 호출을 위해 비움
        elif t == ev.RETRY:                                 # [ncn] 재시도 — 루프가 그 호출의 조각을 버리면 여기서도 버린다(부록 B)
            if getattr(deps, "discard_partial_on_retry", True):   # 버리기 설정이면
                state["partial"] = ""                       # 지금 호출 글 버림
            ev.safe_emit(original, e)                       # 화면에도 재시도 알림
        elif t == ev.USAGE:                                 # 사용량(cn 도 요약 호출 사용량을 세션에 누적 — trackSessionUsage)
            ev.safe_emit(original, e)                       # 원래 수신자로

    return sink, (lambda: state["done"] + state["partial"])  # 수신자와 모은 글 읽기 함수


async def compact_chat_history(chat_history: list[dict], model: Any, history: Any, deps: Any, *,  # 기록을 요약 하나로 압축하는 함수(compactChatHistory)
                               headless: bool, model_name: str, system_message_tokens: int = 0,  # 헤드리스 여부·모델명·시스템 메시지 토큰
                               signal: Optional[AbortSignal] = None,  # 중단 신호(없으면 새로 — cn new AbortController())
                               on_stream_content: Optional[Callable[[str], None]] = None) -> dict:  # 요약 글 조각 콜백
    """요약 요청 글을 붙이고(넘치면 끝부터 잘라) 같은 루프로 LLM 에 보내 요약을 받는다.
    model: 토큰 세기용 모델 설정(cn ModelConfig 모양 dict). history: 서비스 기록(요약 호출 안 도구 처리가 쓰는 곳 — cn 그대로).
    반환: {"compactedHistory", "compactionContent", "compactionIndex"}(cn 키 그대로). 실패는 예외."""
    from ncn.loop.agent import stream_chat_response         # 순환 import 피하려고 안에서
    compaction_prompt = {"message": {"role": "user", "content": COMPACTION_PROMPT}, "contextItems": []}   # 요약 요청 항목
    history_to_use = list(chat_history)                     # 잘라 갈 기록
    history_for_compaction = [*history_to_use, compaction_prompt]   # 요약 호출에 보낼 기록
    context_limit = get_model_context_limit(model)          # 컨텍스트 한도
    max_tokens = get_model_max_tokens(model)                # 출력 예약(64000 상한 있음)
    has_system = any(_role(i) == "system" for i in chat_history)   # 기록에 system 이 이미 있으면 이중 계산 안 함
    reservation = 0 if has_system else system_message_tokens   # 시스템 메시지 예약
    available = context_limit - max_tokens - reservation - COMPACTION_PROMPT_TOKENS   # 요약 호출 입력에 쓸 수 있는 토큰
    while count_chat_history_tokens(history_for_compaction, model) > available and len(history_to_use) > 0:   # 넘치면 끝부터 자름
        log.debug("Compaction history too long, pruning last message")   # cn 과 같은 로그
        pruned = prune_last_message(history_to_use)         # 하나 또는 한 쌍 제거
        if len(pruned) == len(history_to_use):              # 더 줄지 않으면(무한 반복 방지)
            log.warning("Cannot prune history further while maintaining valid conversation structure")   # 경고
            break                                           # 중단
        history_to_use = pruned                             # 잘린 기록
        history_for_compaction = [*history_to_use, compaction_prompt]   # 요약 요청 다시 붙임
    sink, collected = _compaction_sink(deps, on_stream_content)   # 요약 글을 모을 수신자
    sub = dataclasses.replace(deps, on_event=sink)          # 같은 LLM·도구·시스템 메시지, 이벤트만 바꾼 의존성
    try:                                                    # 요약 호출(실패는 그대로 올림)
        await stream_chat_response(history, sub, headless=headless, model_name=model_name,   # 평소와 같은 루프(isCompacting=true)
                                   signal=signal or AbortSignal(), is_compacting=True, items=history_for_compaction)   # 받은 기록 그대로 사용
    except Exception:                                       # cn: logger.error 후 다시 던짐
        log.error("Compaction failed", exc_info=True)       # 기록
        raise                                               # 호출자에게
    content = collected()                                   # 요약 글 전체
    system_item = next((i for i in chat_history if _role(i) == "system"), None)   # 기록의 첫 system 항목(있으면 유지)
    summary = {"message": {"role": "assistant", "content": content}, "contextItems": [],   # 요약 항목(키 순서 cn 그대로)
               "conversationSummary": content}             # 압축 표지
    compacted = [system_item, summary] if system_item is not None else [summary]   # [system?, 요약]
    return {"compactedHistory": compacted, "compactionContent": content,   # cn CompactionResult — 압축 결과 반환
            "compactionIndex": 1 if system_item is not None else 0}   # 요약 위치


# ─────────────────────────────────────────────────────────────────────────────
# 슬래시 명령(대화형 slashCommands.ts handleSlashCommands)
# ─────────────────────────────────────────────────────────────────────────────


def is_interactive_compact_command(text: str) -> bool:      # 대화형 입력이 /compact 명령인지 판정하는 함수
    """첫 글자가 '/' 이고 '/' 뒤를 공백 하나로 나눈 첫 조각이 'compact' 면 참("/compact foo" 도 참, " /compact" 는 거짓)."""
    if not text.startswith("/") or not hl.js_trim(text).startswith("/"):   # 맨 앞이 '/' 가 아니면 명령 아님
        return False                                        # 일반 메시지
    return text[1:].split(" ")[0] == "compact"             # input.slice(1).split(" ")[0] — 명령 이름 비교


# ─────────────────────────────────────────────────────────────────────────────
# 훅(autoCompaction.ts + compactionHelpers.ts + chat.ts + useChat.compaction.ts)
# ─────────────────────────────────────────────────────────────────────────────


def make_compaction_hook(history: Any, model: Any, *,                       # 루프에 넣을 압축 훅을 만드는 함수
                         stdout_write: Optional[Callable[[str], Any]] = None,   # 헤드리스 /compact 실패 줄을 쓸 곳(없으면 sys.stdout)
                         on_compacted: Optional[Callable[[list], Any]] = None):   # 압축 뒤 세션 저장(updateSessionHistory)
    """LoopDeps.compaction_hook 으로 쓸 async 함수를 돌려준다.
    history: ncn.loop.history.ChatHistory(서비스 기록). model: 토큰 세기용 모델 설정(cn ModelConfig 모양 dict, 보통 model.raw)."""

    def write(s: str) -> None:                              # stdout 한 줄 쓰기(safeStdout)
        if stdout_write is not None:                        # 주입된 곳이 있으면
            stdout_write(s)                                 # 거기로
            return                                          # 끝
        import sys                                          # 기본은 표준 출력
        sys.stdout.write(s)                                 # 쓰고
        sys.stdout.flush()                                  # 바로 내보냄

    def save(items: list) -> None:                          # 세션 저장(updateSessionHistory)
        if on_compacted is not None:                        # 저장 함수가 있으면
            on_compacted(items)                             # 압축된 기록 저장

    def system_notice(ctx: dict, message: str) -> None:    # 대화형 onSystemMessage(useChat.stream.helpers.ts)
        history.add_system_message(message)                 # 기록에 system 메시지 추가(svc.addSystemMessage)
        ev.safe_emit(ctx["deps"].on_event, ev.system_message(message))   # 화면(ACP)에도 알림

    async def auto(items: list, ctx: dict, *, notify: bool, system_message: Optional[str],   # handleAutoCompaction(공통)
                   tools: Optional[list]) -> tuple[list, bool]:   # 도구 정의(없으면 세지 않음)
        """판정 → (대화형이면 시작 알림) → 시스템 메시지 토큰 → 요약 → 저장 → (성공 알림). 실패는 경고 후 압축 없이 계속."""
        headless = bool(ctx.get("is_headless"))             # 헤드리스 여부
        if not should_auto_compact(chat_history=items, model=model, system_message=system_message, tools=tools):   # 임계 미만(설정 오류 예외는 그대로)
            return items, False                             # 압축 안 함
        log.info("Auto-compaction triggered%s", " in headless mode" if headless else "")   # cn 로그
        show = notify and not headless                      # 헤드리스는 알림을 전혀 안 함
        if show:                                            # notifyCompactionStart — 시작 알림
            system_notice(ctx, get_auto_compact_message(model))   # "Approaching context limit (NK tokens). Auto-compacting chat history..." — 원문 안내 글
        try:                                                # 실패하면 압축 없이 계속
            sm = system_message if system_message is not None else await _maybe_await(ctx["deps"].system_message())   # 없으면 지금 모드로 다시 만든다
            sm_tokens = count_chat_history_item_tokens({"message": {"role": "system", "content": sm}, "contextItems": []}, model)   # 시스템 메시지를 항목처럼 센 값
            result = await compact_chat_history(items, model, history, ctx["deps"], headless=headless,   # 요약(헤드리스는 콜백 없음)
                                                model_name=ctx.get("model_name") or "", system_message_tokens=sm_tokens)   # 새 중단 신호(cn 그대로)
            save(result["compactedHistory"])                # updateSessionHistory — 압축 뒤 세션 저장
            if show:                                        # handleCompactionSuccess — 성공 알림
                system_notice(ctx, AUTO_SUCCESS_MESSAGE)    # 성공 안내(곧 기록 교체로 기록에서는 사라짐 — cn 그대로)
            return result["compactedHistory"], True          # 압축된 기록
        except Exception as e:                              # handleCompactionError — 실패 처리
            msg = f"Auto-compaction error: {hl.error_message(e)}"   # formatError — 오류 글
            log.error(msg)                                  # 로그
            if show:                                        # 대화형만 경고
                system_notice(ctx, f"Warning: {msg}. Continuing without compaction...")   # 경고 글(기록에 남음)
            return items, False                             # 압축 없이 계속

    async def post_tool(items: list, ctx: dict) -> tuple[list, bool]:   # handlePostToolValidation — 도구 실행 뒤 한도 검사
        """도구 결과로 한도(안전 여유 100)를 넘었으면 임계와 무관하게 강제 압축. 실패·여전히 초과면 예외."""
        if not ctx.get("tool_calls"):                       # 도구 호출이 없으면
            return items, False                             # 할 일 없음
        sm, tools = ctx.get("system_message"), ctx.get("tools")   # 이번 반복의 시스템 메시지·도구 정의
        v = validate_context_length(chat_history=items, model=model, safety_buffer=SAFETY_BUFFER, system_message=sm, tools=tools)   # 한도 검사
        if v["isValid"]:                                    # 넘지 않았으면
            return items, False                             # 그대로
        log.warning("Tool execution caused context overflow, forcing compaction")   # cn 로그
        new_items, was = await auto(items, ctx, notify=True, system_message=sm, tools=tools)   # 강제 압축(판정은 auto 안에서 다시 — cn 그대로)
        if not was:                                         # 압축 못 했으면
            log.error("Failed to compact history after tool execution overflow")   # 로그
            raise Exception(POST_TOOL_FAILED)               # 더 진행 불가
        history.set_history(new_items)                      # 서비스 기록 갱신(예외로 끝나도 반영 — cn 그대로)
        items = history.get_history()                       # 다시 읽기
        v2 = validate_context_length(chat_history=items, model=model, safety_buffer=SAFETY_BUFFER, system_message=sm, tools=tools)   # 다시 검사
        if not v2["isValid"]:                               # 압축해도 넘으면
            log.error("Compaction failed to bring context under limit, stopping execution")   # 로그
            raise Exception(f"Context limit exceeded even after compaction: {v2['error']}")   # cn 원문 오류
        log.info("Successfully compacted after tool overflow")   # 로그
        return items, True                                  # 압축됨

    async def manual(items: list, ctx: dict) -> tuple[Any, bool]:   # /compact — 수동 압축
        """헤드리스(chat.ts handleManualCompaction) / 대화형(useChat.compaction.ts handleCompactCommand)."""
        deps = ctx["deps"]                                  # 루프 의존성
        headless = bool(ctx.get("is_headless"))             # 헤드리스 여부
        signal = ctx.get("signal") or AbortSignal()         # 대화형은 취소 가능(Esc → session/cancel)
        try:                                                # 요약(시스템 메시지 토큰 0 — cn 은 넘기지 않음)
            result = await compact_chat_history(items, model, history, deps, headless=headless,   # 기록 전체 요약
                                                model_name=ctx.get("model_name") or "", signal=signal)   # 같은 LLM
        except Exception as e:                              # 실패
            if headless:                                    # 헤드리스: stdout 에 오류 JSON 한 줄, 그리고 그냥 끝
                write(hl.format_manual_compaction_line(error=hl.error_message(e)))   # {"status":"error","message":"Compaction error: …"} — 오류 줄 출력
                return None, False                          # app.py 는 성공 줄을 쓰지 않는다
            if signal.aborted:                              # 사용자가 취소
                log.info("Manual compaction was cancelled by user")   # 로그
                system_notice(ctx, MANUAL_CANCELLED_MESSAGE)   # "Compaction cancelled." — 취소 안내
            else:                                           # 그 밖의 실패
                log.error("Compaction failed: %s", e)       # 로그
                system_notice(ctx, f"Compaction failed: {hl.error_message(e)}")   # 실패 안내
            return None, False                              # 압축 안 됨
        if headless:                                        # services.chatHistory.compact + 성공 줄(app.py)
            history.compact(result["compactedHistory"], result["compactionIndex"])   # 기록 교체 + 압축 위치
            return history.get_history(), True              # 성공(app.py 가 historyLength 줄을 쓴다)
        if signal.aborted:                                  # 요약은 끝났지만 취소됨 → 아무 것도 안 함(cn 그대로)
            return None, False                              # 압축 안 됨
        history.compact(result["compactedHistory"], result["compactionIndex"])   # setChatHistory + setCompactionIndex — 기록 교체
        save(result["compactedHistory"])                    # updateSessionHistory — 세션 저장
        system_notice(ctx, MANUAL_SUCCESS_MESSAGE)          # 성공 안내(기록 끝에 system 메시지로 남음)
        return history.get_history(), True                  # 압축됨

    async def hook(stage: str, items: list, ctx: dict) -> tuple[Any, bool]:   # 루프가 부르는 훅
        """stage 별로 cn 의 해당 함수를 부른다."""
        if stage == "manual":                               # /compact — 수동 압축 단계
            return await manual(items, ctx)                 # 수동 압축
        if stage == "pre_user_message":                     # 사용자 메시지 넣기 전(chat.ts·useChat.ts — 시스템 메시지·도구 없이 판정, 알림 없음)
            return await auto(items, ctx, notify=False, system_message=None, tools=None)   # 시스템 메시지는 토큰 계산용으로만 다시 만든다
        if ctx.get("is_compacting") and stage == "pre_api":   # 요약 호출 중에는 호출 전 검사 안 함(handlePreApiCompaction)
            return items, False                             # 그대로
        if stage == "pre_api":                              # 호출 전
            return await auto(items, ctx, notify=True, system_message=ctx.get("system_message"), tools=ctx.get("tools"))   # 일반 검사
        if stage == "post_tool":                            # 도구 결과로 한도 초과 시 강제
            return await post_tool(items, ctx)              # 강제 압축
        if stage == "normal":                               # 도구를 쓴 뒤 일반 검사
            if not ctx.get("should_continue"):              # 계속할 게 아니면
                return items, False                         # 검사 안 함
            return await auto(items, ctx, notify=True, system_message=ctx.get("system_message"), tools=ctx.get("tools"))   # 일반 검사
        return items, False                                 # 모르는 단계는 무시

    return hook                                             # 훅 반환
