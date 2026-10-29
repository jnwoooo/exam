# ncn/loop/history.py — 대화 기록(ChatHistoryItem 목록)과 LLM 메시지 변환.
# cn extensions/cli/src/services/ChatHistoryService.ts + core/util/messageConversion.ts
# 항목 모양(cn과 같은 키, 세션 파일에도 그대로 저장 가능):
#   {"message": {"role", "content", ["toolCalls"], ["usage"]}, "contextItems": [...], ["toolCallStates"], ["conversationSummary"]}
# 도구 결과는 assistant 항목의 toolCallStates[i].output에 붙여 두고, LLM에 보낼 때 role:"tool" 메시지로 펼친다.
from __future__ import annotations                           # 타입 주석 지연 평가

import logging                                               # 경고 로그
from typing import Any, Callable, Optional                   # 타입

from ncn.llm.client import js_parse, js_stringify            # JSON.parse / JSON.stringify와 같은 해석·직렬화

log = logging.getLogger("ncn.loop.history")                  # 로거

TOOL_CANCELLED = "Tool cancelled"                            # 결과 없는 도구 호출의 tool 메시지 글(messageConversion.ts:345)


def create_history_item(message: dict, context_items: Optional[list] = None,
                        tool_call_states: Optional[list] = None) -> dict:
    """messageConversion.ts createHistoryItem: {message, contextItems, [toolCallStates]}."""
    item = {"message": message, "contextItems": list(context_items or [])}   # 기본 두 키
    if tool_call_states is not None:                         # ...(toolCallStates && {toolCallStates}) — 빈 배열도 JS에서는 참
        item["toolCallStates"] = tool_call_states
    return item


# ─────────────────────────────────────────────────────────────────────────────
# ChatHistory → OpenAI 메시지(messageConversion.ts convertFromUnified*)
# ─────────────────────────────────────────────────────────────────────────────


def convert_from_message_content(content: Any) -> Any:
    """내용(문자열 또는 [{type:text}|{type:imageUrl}]) → OpenAI 형식."""
    if isinstance(content, str):                             # 문자열은 그대로
        return content
    if isinstance(content, list):                            # 부분 배열
        out = []
        for part in content:
            if part.get("type") == "text":                   # 글
                out.append({"type": "text", "text": part.get("text")})
            elif part.get("type") == "imageUrl":             # 그림
                out.append({"type": "image_url", "image_url": {"url": (part.get("imageUrl") or {}).get("url")}})
            else:
                raise ValueError(f"Unsupported content part type: {part.get('type')}")
        return out
    raise ValueError(f"Unsupported content type: {type(content).__name__}")


def convert_from_unified_message(message: dict) -> dict:
    """ChatMessage → ChatCompletionMessageParam(키 순서 cn과 같게)."""
    role = message.get("role")                               # 역할
    if role == "system":
        return {"role": "system", "content": message.get("content")}
    if role == "user":
        return {"role": "user", "content": convert_from_message_content(message.get("content"))}
    if role == "assistant":
        out = {"role": "assistant", "content": convert_from_message_content(message.get("content"))}
        tcs = message.get("toolCalls")                       # 도구 호출
        if tcs:                                              # 있고 비어 있지 않을 때만
            conv = []
            for tc in tcs:
                fn = tc.get("function") or {}
                d = {"id": tc.get("id"), "type": "function",
                     "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}}
                if tc.get("extra_content"):                  # ...(tc.extra_content && {extra_content})
                    d["extra_content"] = tc["extra_content"]
                conv.append(d)
            out["tool_calls"] = conv
        return out
    if role == "tool":
        return {"role": "tool", "content": message.get("content"), "tool_call_id": message.get("toolCallId")}
    if role == "thinking":                                   # 생각 메시지는 assistant로
        return {"role": "assistant", "content": convert_from_message_content(message.get("content"))}
    raise ValueError(f"Unsupported message role: {role}")


def convert_from_unified_history(items: list[dict]) -> list[dict]:
    """ChatHistoryItem[] → 메시지[]. 도구 호출 상태마다 tool 메시지를 이어 붙인다(결과 없으면 'Tool cancelled')."""
    messages: list[dict] = []                                # 결과
    for item in items:
        msg = item.get("message") or {}
        base = convert_from_unified_message(msg)             # 기본 변환
        ctx = item.get("contextItems") or []                 # 첨부 맥락
        if msg.get("role") == "user" and ctx:                # 사용자 메시지면 <context> 블록을 앞에
            prefix = "".join(f'<context name="{c.get("name")}">\n{c.get("content")}\n</context>\n\n' for c in ctx)
            if isinstance(base["content"], str):             # 배열 내용이면 그대로 둔다
                base["content"] = prefix + base["content"]
        messages.append(base)
        for st in item.get("toolCallStates") or []:          # 도구 호출 상태들
            output = st.get("output")
            if output:                                       # 결과가 있으면 줄바꿈으로 이어서
                content = "\n".join(str(o.get("content")) for o in output)
            else:
                content = TOOL_CANCELLED                     # 결과 없음
            messages.append({"role": "tool", "content": content, "tool_call_id": st.get("toolCallId")})
    return messages


def convert_from_unified_history_with_system_message(items: list[dict], system_message: str) -> list[dict]:
    """맨 앞에 시스템 메시지를 넣고 나머지를 변환(streamChatResponse.ts:256-259)."""
    return [{"role": "system", "content": system_message}, *convert_from_unified_history(items)]


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI 메시지 → ChatHistory(messageConversion.ts convertToUnifiedHistory) — 세션 가져오기 등
# ─────────────────────────────────────────────────────────────────────────────


def _try_parse_json(s: str) -> Any:
    """tryParseJson: 실패하면 {}."""
    try:
        return js_parse(s)
    except (ValueError, TypeError):
        return {}


def _convert_message_content(content: Any) -> Any:
    """OpenAI 내용 → 통합 형식(null → '')."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if part.get("type") == "text":
                out.append({"type": "text", "text": part.get("text")})
            elif part.get("type") == "image_url":
                out.append({"type": "imageUrl", "imageUrl": {"url": part["image_url"]["url"]}})
            else:
                raise ValueError(f"Unsupported content part type: {part.get('type')}")
        return out
    raise ValueError(f"Unsupported content type: {type(content).__name__}")


def convert_to_unified_history(messages: list[dict]) -> list[dict]:
    """메시지[] → ChatHistoryItem[]. tool 메시지는 직전 assistant의 toolCallStates.output으로 들어간다."""
    items: list[dict] = []                                   # 결과
    pending: dict[str, dict] = {}                            # id → 도구 호출
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):      # 도구 호출이 있는 assistant
            um = {"role": "assistant", "content": _convert_message_content(m.get("content") or "")}
            tcs, states = [], []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                call = {"id": tc.get("id") or "", "type": "function",
                        "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}}
                if tc.get("extra_content"):
                    call["extra_content"] = tc["extra_content"]
                tcs.append(dict(call))
                pending[call["id"]] = call
                states.append({"toolCallId": call["id"], "toolCall": call, "status": "done",
                               "parsedArgs": _try_parse_json(call["function"]["arguments"])})
            um["toolCalls"] = tcs
            items.append(create_history_item(um, [], states))
        elif role == "tool":                                 # 도구 결과
            call = pending.get(m.get("tool_call_id"))
            if call is None:
                continue
            last = next((it for it in reversed(items) if it["message"]["role"] == "assistant"), None)
            if last is None or not last.get("toolCallStates"):
                continue
            for st in last["toolCallStates"]:
                if st["toolCallId"] == m.get("tool_call_id"):
                    st["output"] = [{"content": m.get("content") if isinstance(m.get("content"), str) else "",
                                     "name": f"Tool Result: {call['function']['name']}",
                                     "description": "Tool execution result"}]
        elif role == "system":
            items.append(create_history_item({"role": "system", "content": m.get("content") if isinstance(m.get("content"), str) else ""}))
        elif role == "user":
            items.append(create_history_item({"role": "user", "content": _convert_message_content(m.get("content"))}))
        elif role == "assistant":
            items.append(create_history_item({"role": "assistant", "content": _convert_message_content(m.get("content") or "")}))
        else:
            raise ValueError(f"Unsupported message role: {role}")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# ChatHistoryService
# ─────────────────────────────────────────────────────────────────────────────


def find_compaction_index(items: list[dict]) -> Optional[int]:
    """conversationSummary가 있는 첫 항목의 위치(없으면 None)."""
    for i, it in enumerate(items):
        if "conversationSummary" in it:
            return i
    return None


class ChatHistory:
    """cn ChatHistoryService — 대화 기록의 단일 원천. 갱신은 항목을 새로 만들어 바꾼다(앞서 받아 간 목록은 안 바뀜).

    on_change(items): 기록이 바뀔 때마다 불린다(세션 저장용 — cn updateSessionHistory 자리)."""

    def __init__(self, items: Optional[list[dict]] = None, *, session_id: str = "",
                 on_change: Optional[Callable[[list[dict]], None]] = None):
        self._items: list[dict] = list(items or [])          # 기록
        self.compaction_index: Optional[int] = find_compaction_index(self._items)   # 압축 위치
        self.session_id = session_id                         # 세션 id(헤드리스는 "" — P0 0장 3번)
        self.on_change = on_change                           # 바뀜 알림
        self._past: list[list[dict]] = []                    # 되돌리기
        self._future: list[list[dict]] = []                  # 다시 하기

    # ── 내부 ──
    def _set(self, items: list[dict], *, record_undo: bool = True, compaction_index: Any = "auto",
             persist: bool = True) -> None:
        """setHistoryInternal: 되돌리기 기록 → 교체 → 압축 위치 → 저장."""
        if record_undo:                                      # 되돌리기 지점
            self._past.append(list(self._items))
            self._future = []
        self._items = list(items)                            # 교체
        self.compaction_index = find_compaction_index(self._items) if compaction_index == "auto" else compaction_index
        if persist and self.on_change is not None:           # 저장
            self.on_change(self.get_history())

    # ── 읽기 ──
    def get_history(self) -> list[dict]:
        """얕은 복사본(getHistory)."""
        return list(self._items)

    def get_history_for_llm(self, compaction_index: Optional[int] = None) -> list[dict]:
        """getHistoryForLLM: 압축 위치부터(맨 앞이 system이면 그것도)."""
        index = compaction_index if compaction_index is not None else self.compaction_index
        full = self._items
        if index is None or index >= len(full):
            return self.get_history()
        system = full[0] if full and full[0].get("message", {}).get("role") == "system" else None
        tail = full[index:]
        return [system, *tail] if system is not None and index > 0 else list(tail)

    def is_ready(self) -> bool:
        """서비스 준비 여부(ncn은 항상 준비됨 → cn의 '서비스 경로'만 옮겼다)."""
        return True

    # ── 추가 ──
    def add_user_message(self, content: Any, context_items: Optional[list] = None) -> dict:
        """사용자 메시지 추가."""
        item = create_history_item({"role": "user", "content": content}, context_items or [])
        self._set([*self._items, item])
        return item

    def add_assistant_message(self, content: Any, tool_calls: Optional[list] = None, usage: Any = None) -> dict:
        """assistant 메시지 추가. tool_calls(ChatCompletion 형식)가 있으면 toolCallStates(status generated)를 만든다."""
        message: dict = {"role": "assistant", "content": content}
        if tool_calls:                                       # 도구 호출
            message["toolCalls"] = tool_calls
        if usage:                                            # 사용량(요청에는 안 들어감)
            message["usage"] = usage
        states = None
        if tool_calls is not None:                           # toolCalls?.map(...)
            states = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") if fn.get("name") is not None else tc.get("name")
                if isinstance(tc.get("arguments"), str):     # 인자 글 고르기(ChatHistoryService.ts:146-158)
                    raw = tc["arguments"]
                elif isinstance(fn.get("arguments"), str):
                    raw = fn["arguments"]
                elif tc.get("arguments") is None:
                    raw = "{}" if fn.get("arguments") is None else str(fn.get("arguments"))
                else:
                    raw = js_stringify(tc["arguments"])
                if isinstance(tc.get("arguments"), (dict, list)):   # parsedArgs: 객체면 그대로
                    parsed = tc["arguments"]
                else:
                    try:
                        parsed = js_parse(raw)
                    except (ValueError, TypeError):
                        parsed = {}
                states.append({"toolCallId": tc.get("id"),
                               "toolCall": {"id": tc.get("id"), "type": "function", "function": {"name": name, "arguments": raw}},
                               "status": "generated", "parsedArgs": parsed})
        item = create_history_item(message, [], states)
        self._set([*self._items, item])
        return item

    def add_system_message(self, content: str) -> dict:
        """시스템 메시지 항목 추가."""
        item = create_history_item({"role": "system", "content": content})
        self._set([*self._items, item])
        return item

    def add_history_item(self, item: dict) -> dict:
        """항목 그대로 추가."""
        self._set([*self._items, item])
        return item

    # ── 도구 상태 ──
    def update_tool_call_state(self, message_index: int, tool_call_id: str, updates: dict) -> None:
        """해당 항목의 도구 상태를 새 객체로 바꾼다."""
        items = list(self._items)
        if not (0 <= message_index < len(items)) or not items[message_index].get("toolCallStates"):
            log.warning("No tool call states found at message index %s (%s)", message_index, tool_call_id)
            return
        item = items[message_index]
        if not any(st.get("toolCallId") == tool_call_id for st in item["toolCallStates"]):
            log.warning("Tool call state not found: %s", tool_call_id)
            return
        items[message_index] = {**item, "toolCallStates": [({**st, **updates} if st.get("toolCallId") == tool_call_id else st)
                                                           for st in item["toolCallStates"]]}
        self._set(items)

    def _find_tool_item(self, tool_call_id: str) -> int:
        """이 도구 호출을 가진 마지막 assistant 항목 위치(-1이면 없음)."""
        for i in range(len(self._items) - 1, -1, -1):
            it = self._items[i]
            if it.get("message", {}).get("role") == "assistant" and it.get("toolCallStates"):
                if any(st.get("toolCallId") == tool_call_id for st in it["toolCallStates"]):
                    return i
        return -1

    def add_tool_result(self, tool_call_id: str, result: str, status: str = "done") -> None:
        """도구 결과를 assistant 항목의 상태에 붙인다(output 하나, name 'Tool Result')."""
        idx = self._find_tool_item(tool_call_id)
        if idx < 0:
            log.warning("Could not find message for tool result: %s", tool_call_id)
            return
        self.update_tool_call_state(idx, tool_call_id, {
            "status": status,
            "output": [{"content": result, "name": "Tool Result", "description": "Tool execution result"}],
        })

    def update_tool_status(self, tool_call_id: str, status: str) -> None:
        """도구 상태만 바꾼다(승인 직후 'calling')."""
        idx = self._find_tool_item(tool_call_id)
        if idx < 0:
            log.warning("Could not find message for tool status update: %s", tool_call_id)
            return
        self.update_tool_call_state(idx, tool_call_id, {"status": status})

    # ── 통째로 ──
    def set_history(self, items: list[dict]) -> None:
        """기록 전체 교체(압축 결과 반영 등)."""
        self._set(items)

    def compact(self, items: list[dict], compaction_index: int) -> None:
        """압축 결과로 교체 + 압축 위치 지정."""
        self._set(items, compaction_index=compaction_index)

    def clear(self) -> None:
        """비우기."""
        self._set([], compaction_index=None)

    def can_undo(self) -> bool:
        return bool(self._past)                              # 되돌릴 것이 있나

    def can_redo(self) -> bool:
        return bool(self._future)                            # 다시 할 것이 있나

    def undo(self) -> None:
        """되돌리기."""
        if not self._past:
            return
        prev = self._past.pop()
        self._future.append(list(self._items))
        self._set(prev, record_undo=False)

    def redo(self) -> None:
        """다시 하기."""
        if not self._future:
            return
        nxt = self._future.pop()
        self._past.append(list(self._items))
        self._set(nxt, record_undo=False)
