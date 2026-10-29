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


def create_history_item(message: dict, context_items: Optional[list] = None,  # 기록 항목 하나를 만드는 함수(createHistoryItem)
                        tool_call_states: Optional[list] = None) -> dict:  # 도구 호출 상태는 선택
    """messageConversion.ts createHistoryItem: {message, contextItems, [toolCallStates]}."""
    item = {"message": message, "contextItems": list(context_items or [])}   # 기본 두 키
    if tool_call_states is not None:                         # ...(toolCallStates && {toolCallStates}) — 빈 배열도 JS에서는 참
        item["toolCallStates"] = tool_call_states           # 상태가 있을 때만 키를 넣는다
    return item                                             # 만든 항목 반환


# ─────────────────────────────────────────────────────────────────────────────
# ChatHistory → OpenAI 메시지(messageConversion.ts convertFromUnified*)
# ─────────────────────────────────────────────────────────────────────────────


def convert_from_message_content(content: Any) -> Any:      # 메시지 내용을 OpenAI 형식으로 바꾸는 함수
    """내용(문자열 또는 [{type:text}|{type:imageUrl}]) → OpenAI 형식."""
    if isinstance(content, str):                             # 문자열은 그대로
        return content                                      # 문자열이면 변환 없이 반환
    if isinstance(content, list):                            # 부분 배열
        out = []                                            # 부분 배열 결과를 담을 목록
        for part in content:                                # 부분마다 종류를 확인
            if part.get("type") == "text":                   # 글
                out.append({"type": "text", "text": part.get("text")})  # 글 부분은 text 형태로 복사
            elif part.get("type") == "imageUrl":             # 그림
                out.append({"type": "image_url", "image_url": {"url": (part.get("imageUrl") or {}).get("url")}})  # imageUrl을 OpenAI의 image_url 모양으로 바꾼다
            else:                                           # 모르는 부분 종류는
                raise ValueError(f"Unsupported content part type: {part.get('type')}")  # cn처럼 오류를 낸다
        return out                                          # 변환한 배열 반환
    raise ValueError(f"Unsupported content type: {type(content).__name__}")  # 문자열도 배열도 아니면 오류


def convert_from_unified_message(message: dict) -> dict:    # 통합 메시지 하나를 OpenAI 메시지로 바꾸는 함수
    """ChatMessage → ChatCompletionMessageParam(키 순서 cn과 같게)."""
    role = message.get("role")                               # 역할
    if role == "system":                                    # system 역할이면
        return {"role": "system", "content": message.get("content")}  # 내용을 그대로 담는다
    if role == "user":                                      # user 역할이면
        return {"role": "user", "content": convert_from_message_content(message.get("content"))}  # 내용만 형식 변환해서 담는다
    if role == "assistant":                                 # assistant 역할이면
        out = {"role": "assistant", "content": convert_from_message_content(message.get("content"))}  # 기본 응답 메시지
        tcs = message.get("toolCalls")                       # 도구 호출
        if tcs:                                              # 있고 비어 있지 않을 때만
            conv = []                                       # 도구 호출 변환 결과 목록
            for tc in tcs:                                  # 호출마다 변환
                fn = tc.get("function") or {}               # function이 없을 때를 대비한 기본값
                d = {"id": tc.get("id"), "type": "function",  # OpenAI tool_call 모양으로 만든다
                     "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}}  # 이름·인자가 없으면 빈 문자열
                if tc.get("extra_content"):                 # 추가 정보가 있으면 ...(tc.extra_content && {extra_content})
                    d["extra_content"] = tc["extra_content"]  # 추가 정보가 있으면 보존
                conv.append(d)                              # 변환된 호출을 모은다
            out["tool_calls"] = conv                        # 메시지에 tool_calls로 붙인다
        return out                                          # assistant 메시지 반환
    if role == "tool":                                      # tool 역할이면
        return {"role": "tool", "content": message.get("content"), "tool_call_id": message.get("toolCallId")}  # 도구 결과 메시지로 toolCallId를 tool_call_id로 바꾼다
    if role == "thinking":                                   # 생각 메시지는 assistant로
        return {"role": "assistant", "content": convert_from_message_content(message.get("content"))}  # 생각 메시지는 assistant 형태로 보낸다
    raise ValueError(f"Unsupported message role: {role}")   # 모르는 역할은 오류


def convert_from_unified_history(items: list[dict]) -> list[dict]:  # 기록 전체를 OpenAI 메시지 목록으로 바꾸는 함수
    """ChatHistoryItem[] → 메시지[]. 도구 호출 상태마다 tool 메시지를 이어 붙인다(결과 없으면 'Tool cancelled')."""
    messages: list[dict] = []                                # 결과
    for item in items:                                      # 항목을 차례로 변환
        msg = item.get("message") or {}                     # message가 없으면 빈 dict
        base = convert_from_unified_message(msg)             # 기본 변환
        ctx = item.get("contextItems") or []                 # 첨부 맥락
        if msg.get("role") == "user" and ctx:                # 사용자 메시지면 <context> 블록을 앞에
            prefix = "".join(f'<context name="{c.get("name")}">\n{c.get("content")}\n</context>\n\n' for c in ctx)  # 맥락 항목을 <context> 블록 글로 만든다
            if isinstance(base["content"], str):             # 배열 내용이면 그대로 둔다
                base["content"] = prefix + base["content"]  # 글 내용일 때만 앞에 붙인다
        messages.append(base)                               # 변환한 메시지를 결과에 추가
        for st in item.get("toolCallStates") or []:          # 도구 호출 상태들
            output = st.get("output")                       # 도구 호출 상태의 출력
            if output:                                       # 결과가 있으면 줄바꿈으로 이어서
                content = "\n".join(str(o.get("content")) for o in output)  # 여러 출력은 줄바꿈으로 합친다
            else:                                           # 출력이 없으면
                content = TOOL_CANCELLED                     # 결과 없음
            messages.append({"role": "tool", "content": content, "tool_call_id": st.get("toolCallId")})  # 도구 결과 메시지를 바로 뒤에 붙인다
    return messages                                         # 완성된 메시지 목록 반환


def convert_from_unified_history_with_system_message(items: list[dict], system_message: str) -> list[dict]:  # 시스템 메시지를 맨 앞에 두고 변환하는 함수
    """맨 앞에 시스템 메시지를 넣고 나머지를 변환(streamChatResponse.ts:256-259)."""
    return [{"role": "system", "content": system_message}, *convert_from_unified_history(items)]  # 시스템 메시지 + 변환된 기록


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI 메시지 → ChatHistory(messageConversion.ts convertToUnifiedHistory) — 세션 가져오기 등
# ─────────────────────────────────────────────────────────────────────────────


def _try_parse_json(s: str) -> Any:                         # JSON 글을 읽고 실패하면 빈 dict를 주는 함수
    """tryParseJson: 실패하면 {}."""
    try:                                                    # 읽기를 시도한다
        return js_parse(s)                                  # JS JSON.parse와 같은 해석
    except (ValueError, TypeError):                         # 해석 오류는
        return {}                                           # 빈 객체로 대신한다


def _convert_message_content(content: Any) -> Any:          # OpenAI 내용을 통합 형식으로 바꾸는 함수
    """OpenAI 내용 → 통합 형식(null → '')."""
    if content is None:                                     # None이면
        return ""                                           # 빈 문자열로
    if isinstance(content, str):                            # 문자열이면
        return content                                      # 그대로 반환
    if isinstance(content, list):                           # 배열이면
        out = []                                            # 변환 결과 목록
        for part in content:                                # 부분마다 종류를 확인
            if part.get("type") == "text":                  # 글 부분은
                out.append({"type": "text", "text": part.get("text")})  # text 형태로 복사
            elif part.get("type") == "image_url":           # image_url 부분은
                out.append({"type": "imageUrl", "imageUrl": {"url": part["image_url"]["url"]}})  # imageUrl 모양으로 바꾼다
            else:                                           # 모르는 부분 종류는
                raise ValueError(f"Unsupported content part type: {part.get('type')}")  # 오류를 낸다
        return out                                          # 변환한 배열 반환
    raise ValueError(f"Unsupported content type: {type(content).__name__}")  # 그 밖의 타입은 오류


def convert_to_unified_history(messages: list[dict]) -> list[dict]:  # OpenAI 메시지 목록을 기록 항목으로 바꾸는 함수
    """메시지[] → ChatHistoryItem[]. tool 메시지는 직전 assistant의 toolCallStates.output으로 들어간다."""
    items: list[dict] = []                                   # 결과
    pending: dict[str, dict] = {}                            # id → 도구 호출
    for m in messages:                                      # 메시지를 차례로 처리
        role = m.get("role")                                # 역할 확인
        if role == "assistant" and m.get("tool_calls"):      # 도구 호출이 있는 assistant
            um = {"role": "assistant", "content": _convert_message_content(m.get("content") or "")}  # 도구 호출이 있는 assistant 메시지 틀
            tcs, states = [], []                            # 도구 호출 목록과 상태 목록
            for tc in m["tool_calls"]:                      # 호출마다 변환
                fn = tc.get("function") or {}               # function이 없을 때의 기본값
                call = {"id": tc.get("id") or "", "type": "function",  # 통합 형식의 도구 호출
                        "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}}  # 이름·인자 없으면 빈 문자열
                if tc.get("extra_content"):                 # 추가 정보가 있으면
                    call["extra_content"] = tc["extra_content"]  # 보존한다
                tcs.append(dict(call))                      # 복사본을 메시지용으로 담는다
                pending[call["id"]] = call                  # tool 메시지가 찾을 수 있게 id로 보관
                states.append({"toolCallId": call["id"], "toolCall": call, "status": "done",  # 호출마다 상태(done)를 만든다
                               "parsedArgs": _try_parse_json(call["function"]["arguments"])})  # 인자 글을 읽어 parsedArgs로
            um["toolCalls"] = tcs                           # 메시지에 도구 호출을 붙인다
            items.append(create_history_item(um, [], states))  # 항목으로 추가
        elif role == "tool":                                 # 도구 결과
            call = pending.get(m.get("tool_call_id"))       # 이 결과가 속한 호출 찾기
            if call is None:                                # 짝이 없는 결과는
                continue                                    # 버린다
            last = next((it for it in reversed(items) if it["message"]["role"] == "assistant"), None)  # 가장 가까운 assistant 항목을 거꾸로 찾는다
            if last is None or not last.get("toolCallStates"):  # 상태가 없으면
                continue                                    # 버린다
            for st in last["toolCallStates"]:               # 해당 도구 호출 상태를 찾아
                if st["toolCallId"] == m.get("tool_call_id"):  # id가 같으면
                    st["output"] = [{"content": m.get("content") if isinstance(m.get("content"), str) else "",  # 출력 하나를 붙인다(문자열이 아니면 빈 글)
                                     "name": f"Tool Result: {call['function']['name']}",  # 이름은 'Tool Result: 도구명'
                                     "description": "Tool execution result"}]  # 설명은 고정 문구
        elif role == "system":                              # system 메시지면
            items.append(create_history_item({"role": "system", "content": m.get("content") if isinstance(m.get("content"), str) else ""}))  # system 항목으로 추가
        elif role == "user":                                # user 메시지면
            items.append(create_history_item({"role": "user", "content": _convert_message_content(m.get("content"))}))  # user 항목으로 추가
        elif role == "assistant":                           # 도구 호출 없는 assistant면
            items.append(create_history_item({"role": "assistant", "content": _convert_message_content(m.get("content") or "")}))  # assistant 항목으로 추가
        else:                                               # 그 밖의 역할은
            raise ValueError(f"Unsupported message role: {role}")  # 오류를 낸다
    return items                                            # 변환된 기록 반환


# ─────────────────────────────────────────────────────────────────────────────
# ChatHistoryService
# ─────────────────────────────────────────────────────────────────────────────


def find_compaction_index(items: list[dict]) -> Optional[int]:  # 압축 요약이 있는 위치를 찾는 함수
    """conversationSummary가 있는 첫 항목의 위치(없으면 None)."""
    for i, it in enumerate(items):                          # 항목을 순서대로 검사
        if "conversationSummary" in it:                     # 요약 키가 있으면
            return i                                        # 그 위치를 돌려준다
    return None                                             # 없으면 None


class ChatHistory:                                          # 대화 기록 서비스 클래스
    """cn ChatHistoryService — 대화 기록의 단일 원천. 갱신은 항목을 새로 만들어 바꾼다(앞서 받아 간 목록은 안 바뀜).

    on_change(items): 기록이 바뀔 때마다 불린다(세션 저장용 — cn updateSessionHistory 자리)."""

    def __init__(self, items: Optional[list[dict]] = None, *, session_id: str = "",  # 기록 서비스 생성자
                 on_change: Optional[Callable[[list[dict]], None]] = None):  # 변경 알림 콜백은 선택
        self._items: list[dict] = list(items or [])          # 기록
        self.compaction_index: Optional[int] = find_compaction_index(self._items)   # 압축 위치
        self.session_id = session_id                         # 세션 id(헤드리스는 "" — P0 0장 3번)
        self.on_change = on_change                           # 바뀜 알림
        self._past: list[list[dict]] = []                    # 되돌리기
        self._future: list[list[dict]] = []                  # 다시 하기

    # ── 내부 ──
    def _set(self, items: list[dict], *, record_undo: bool = True, compaction_index: Any = "auto",  # 내부 교체 함수(setHistoryInternal)
             persist: bool = True) -> None:                 # persist=False면 저장 콜백을 부르지 않는다
        """setHistoryInternal: 되돌리기 기록 → 교체 → 압축 위치 → 저장."""
        if record_undo:                                      # 되돌리기 지점
            self._past.append(list(self._items))            # 현재 기록을 되돌리기 목록에 쌓는다
            self._future = []                               # 새 변경이 있으면 다시 하기 목록은 비운다
        self._items = list(items)                            # 교체
        self.compaction_index = find_compaction_index(self._items) if compaction_index == "auto" else compaction_index  # 압축 위치는 자동 계산하거나 지정값을 쓴다
        if persist and self.on_change is not None:           # 저장
            self.on_change(self.get_history())              # 바뀐 기록을 콜백으로 알린다

    # ── 읽기 ──
    def get_history(self) -> list[dict]:                    # 현재 기록의 얕은 복사본을 돌려주는 함수
        """얕은 복사본(getHistory)."""
        return list(self._items)                            # 복사본 반환(원본 보호)

    def get_history_for_llm(self, compaction_index: Optional[int] = None) -> list[dict]:  # LLM에 보낼 기록을 만드는 함수
        """getHistoryForLLM: 압축 위치부터(맨 앞이 system이면 그것도)."""
        index = compaction_index if compaction_index is not None else self.compaction_index  # 지정값이 없으면 저장된 압축 위치
        full = self._items                                  # 전체 항목
        if index is None or index >= len(full):             # 압축 위치가 없거나 범위 밖이면
            return self.get_history()                       # 전체 기록 반환
        system = full[0] if full and full[0].get("message", {}).get("role") == "system" else None  # 맨 앞이 system이면 따로 챙긴다
        tail = full[index:]                                 # 압축 위치부터의 뒷부분
        return [system, *tail] if system is not None and index > 0 else list(tail)  # system을 앞에 다시 붙여 반환

    def is_ready(self) -> bool:                             # 준비 여부를 알려주는 함수
        """서비스 준비 여부(ncn은 항상 준비됨 → cn의 '서비스 경로'만 옮겼다)."""
        return True                                         # ncn은 항상 준비됨

    # ── 추가 ──
    def add_user_message(self, content: Any, context_items: Optional[list] = None) -> dict:  # 사용자 메시지를 추가하는 함수
        """사용자 메시지 추가."""
        item = create_history_item({"role": "user", "content": content}, context_items or [])  # 사용자 항목 만들기
        self._set([*self._items, item])                     # 새 목록으로 교체해 기록 추가
        return item                                         # 만든 항목 반환

    def add_assistant_message(self, content: Any, tool_calls: Optional[list] = None, usage: Any = None) -> dict:  # assistant 메시지를 추가하는 함수
        """assistant 메시지 추가. tool_calls(ChatCompletion 형식)가 있으면 toolCallStates(status generated)를 만든다."""
        message: dict = {"role": "assistant", "content": content}  # 기본 assistant 메시지
        if tool_calls:                                       # 도구 호출
            message["toolCalls"] = tool_calls               # 도구 호출을 메시지에 담는다
        if usage:                                            # 사용량(요청에는 안 들어감)
            message["usage"] = usage                        # 사용량을 메시지에 담는다
        states = None                                       # 도구 상태 목록(없으면 None)
        if tool_calls is not None:                          # 도구 호출이 주어졌으면
            states = []                                     # 상태 목록 시작
            for tc in tool_calls:                           # 호출마다 상태를 만든다
                fn = tc.get("function") or {}               # function이 없을 때의 기본값
                name = fn.get("name") if fn.get("name") is not None else tc.get("name")  # 이름은 function.name 우선, 없으면 tc.name
                if isinstance(tc.get("arguments"), str):     # 인자 글 고르기(ChatHistoryService.ts:146-158)
                    raw = tc["arguments"]                   # 최상위 arguments가 문자열이면 그대로
                elif isinstance(fn.get("arguments"), str):  # function.arguments가 문자열이면
                    raw = fn["arguments"]                   # 그것을 사용
                elif tc.get("arguments") is None:           # 인자가 아예 없으면
                    raw = "{}" if fn.get("arguments") is None else str(fn.get("arguments"))  # 빈 객체 글 또는 문자열화한 값
                else:                                       # 그 밖의 경우는
                    raw = js_stringify(tc["arguments"])     # JS JSON.stringify로 직렬화
                if isinstance(tc.get("arguments"), (dict, list)):   # parsedArgs: 객체면 그대로
                    parsed = tc["arguments"]                # 객체/배열이면 그대로 parsedArgs
                else:                                       # 아니면
                    try:                                    # 글을 해석해 본다
                        parsed = js_parse(raw)              # JS JSON.parse와 같은 해석
                    except (ValueError, TypeError):         # 해석 실패는
                        parsed = {}                         # 빈 객체로 대신
                states.append({"toolCallId": tc.get("id"),  # 상태 항목(generated)을 쌓는다
                               "toolCall": {"id": tc.get("id"), "type": "function", "function": {"name": name, "arguments": raw}},  # 호출 원본도 함께 보관
                               "status": "generated", "parsedArgs": parsed})  # 아직 실행 전이므로 generated
        item = create_history_item(message, [], states)     # 상태가 붙은 assistant 항목 만들기
        self._set([*self._items, item])                     # 기록에 추가
        return item                                         # 만든 항목 반환

    def add_system_message(self, content: str) -> dict:     # 시스템 메시지를 추가하는 함수
        """시스템 메시지 항목 추가."""
        item = create_history_item({"role": "system", "content": content})  # system 항목 만들기
        self._set([*self._items, item])                     # 기록에 추가
        return item                                         # 만든 항목 반환

    def add_history_item(self, item: dict) -> dict:         # 항목 그대로 추가하는 함수
        """항목 그대로 추가."""
        self._set([*self._items, item])                     # 기록에 추가
        return item                                         # 받은 항목 반환

    # ── 도구 상태 ──
    def update_tool_call_state(self, message_index: int, tool_call_id: str, updates: dict) -> None:  # 도구 호출 상태를 갱신하는 함수
        """해당 항목의 도구 상태를 새 객체로 바꾼다."""
        items = list(self._items)                           # 복사본에서 작업
        if not (0 <= message_index < len(items)) or not items[message_index].get("toolCallStates"):  # 인덱스가 범위 밖이거나 상태가 없으면
            log.warning("No tool call states found at message index %s (%s)", message_index, tool_call_id)  # 경고 로그를 남기고
            return                                          # 그만둔다
        item = items[message_index]                         # 대상 항목
        if not any(st.get("toolCallId") == tool_call_id for st in item["toolCallStates"]):  # id에 맞는 상태가 없으면
            log.warning("Tool call state not found: %s", tool_call_id)  # 경고 로그를 남기고
            return                                          # 그만둔다
        items[message_index] = {**item, "toolCallStates": [({**st, **updates} if st.get("toolCallId") == tool_call_id else st)  # id가 맞는 상태만 updates를 덮어쓴 새 객체로 교체
                                                           for st in item["toolCallStates"]]}  # 나머지 상태는 그대로 둔다
        self._set(items)                                    # 새 목록으로 기록 교체

    def _find_tool_item(self, tool_call_id: str) -> int:    # 도구 호출을 가진 항목 위치를 찾는 함수
        """이 도구 호출을 가진 마지막 assistant 항목 위치(-1이면 없음)."""
        for i in range(len(self._items) - 1, -1, -1):       # 뒤에서부터 거꾸로 훑는다
            it = self._items[i]                             # 현재 항목
            if it.get("message", {}).get("role") == "assistant" and it.get("toolCallStates"):  # 도구 상태가 있는 assistant 항목이면
                if any(st.get("toolCallId") == tool_call_id for st in it["toolCallStates"]):  # id가 일치하는 상태가 있으면
                    return i                                # 그 위치 반환
        return -1                                           # 못 찾으면 -1

    def add_tool_result(self, tool_call_id: str, result: str, status: str = "done") -> None:  # 도구 결과를 붙이는 함수
        """도구 결과를 assistant 항목의 상태에 붙인다(output 하나, name 'Tool Result')."""
        idx = self._find_tool_item(tool_call_id)            # 결과를 붙일 항목 위치
        if idx < 0:                                         # 없으면
            log.warning("Could not find message for tool result: %s", tool_call_id)  # 경고 로그를 남기고
            return                                          # 그만둔다
        self.update_tool_call_state(idx, tool_call_id, {    # 해당 상태에 결과를 반영
            "status": status,                               # 상태(기본 done)
            "output": [{"content": result, "name": "Tool Result", "description": "Tool execution result"}],  # 출력 하나를 붙인다
        })                                                  # 상태 갱신 끝

    def update_tool_status(self, tool_call_id: str, status: str) -> None:  # 도구 상태만 바꾸는 함수
        """도구 상태만 바꾼다(승인 직후 'calling')."""
        idx = self._find_tool_item(tool_call_id)            # 대상 항목 위치
        if idx < 0:                                         # 없으면
            log.warning("Could not find message for tool status update: %s", tool_call_id)  # 경고 로그를 남기고
            return                                          # 그만둔다
        self.update_tool_call_state(idx, tool_call_id, {"status": status})  # 상태 값만 갱신

    # ── 통째로 ──
    def set_history(self, items: list[dict]) -> None:       # 기록 전체 교체 함수
        """기록 전체 교체(압축 결과 반영 등)."""
        self._set(items)                                    # 되돌리기 기록과 함께 교체

    def compact(self, items: list[dict], compaction_index: int) -> None:  # 압축 결과로 교체하는 함수
        """압축 결과로 교체 + 압축 위치 지정."""
        self._set(items, compaction_index=compaction_index)  # 압축 위치를 지정해 교체

    def clear(self) -> None:                                # 기록 비우기 함수
        """비우기."""
        self._set([], compaction_index=None)                # 빈 목록으로 교체, 압축 위치 없음

    def can_undo(self) -> bool:                             # 되돌릴 수 있는지 묻는 함수
        return bool(self._past)                              # 되돌릴 것이 있나

    def can_redo(self) -> bool:                             # 다시 할 수 있는지 묻는 함수
        return bool(self._future)                            # 다시 할 것이 있나

    def undo(self) -> None:                                 # 되돌리기 함수
        """되돌리기."""
        if not self._past:                                  # 되돌릴 것이 없으면
            return                                          # 아무것도 안 한다
        prev = self._past.pop()                             # 가장 최근 지점 꺼내기
        self._future.append(list(self._items))              # 현재 기록은 다시 하기로 보낸다
        self._set(prev, record_undo=False)                  # 되돌리기 기록은 새로 쌓지 않고 교체

    def redo(self) -> None:                                 # 다시 하기 함수
        """다시 하기."""
        if not self._future:                                # 다시 할 것이 없으면
            return                                          # 아무것도 안 한다
        nxt = self._future.pop()                            # 가장 최근 다시 하기 지점 꺼내기
        self._past.append(list(self._items))                # 현재 기록은 되돌리기로 보낸다
        self._set(nxt, record_undo=False)                   # 되돌리기 기록은 새로 쌓지 않고 교체
