# ncn/loop/tool_calls.py — 도구 호출 조각 모으기·사전 처리·권한·병렬 실행·기록.
# cn extensions/cli/src/stream/handleToolCalls.ts + streamChatResponse.helpers.ts(processToolCallDelta,
#    preprocessStreamedToolCalls, executeStreamedToolCalls, checkToolPermissionApproval) + tools/index.tsx(일부)
# 모델에게 돌아가는 글은 cn 원문 그대로: "Tool {name} not found", 'Required parameter "{p}" missing for tool "{name}"',
#   "Error executing tool {name}: {msg}", "Command blocked by security policy", "Permission denied by user"
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 병렬 실행
import inspect                                               # 동기/비동기 콜백 둘 다 받기
import logging                                               # 로그
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Awaitable, Callable, Optional        # 타입

from ncn.llm.client import js_parse, js_stringify            # JSON.parse / JSON.stringify와 같게
from ncn.loop import events as ev                            # 이벤트
from ncn.loop.history import ChatHistory                     # 대화 기록
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolRunContext   # 공용 도구 형식

log = logging.getLogger("ncn.loop.tool_calls")               # 로거

POLICY_DENIED = "Command blocked by security policy"         # 정책 거부 글(helpers.ts:44)
USER_DENIED = "Permission denied by user"                    # 사용자 거부 글(helpers.ts:45)

CheckPermission = Callable[[Tool, dict], Any]                # (도구, 인자) → "allow"|"ask"|"exclude" (awaitable도 허용)
AskUser = Callable[[str, Any, Any, str], Awaitable[Any]]     # (도구 이름, 인자, 미리보기, 호출 id) → "allow"|"allow_always"|"reject"


@dataclass
class ToolCall:
    """스트림에서 모은 도구 호출 하나(tools/types.ts ToolCall)."""
    id: str                                                  # 호출 id(LLM이 준 그대로)
    name: str = ""                                           # 도구 이름
    arguments: Any = field(default_factory=dict)             # 마지막으로 해석에 성공한 인자(기본 {})
    arguments_str: str = ""                                  # 이어 붙인 인자 글
    start_notified: bool = False                             # 인자 해석에 한 번이라도 성공했나
    extra_content: Optional[dict] = None                     # 제공자별 추가 내용(그대로 되돌려 보냄)


@dataclass
class PreprocessedToolCall:
    """사전 처리까지 끝난 호출(tools/types.ts PreprocessedToolCall)."""
    call: ToolCall                                           # 원래 호출
    tool: Tool                                               # 찾은 도구
    preprocess_result: Optional[PreprocessResult] = None     # 사전 처리 결과

    @property
    def id(self) -> str:                                     # 줄임
        return self.call.id

    @property
    def name(self) -> str:                                   # 줄임
        return self.call.name

    @property
    def arguments(self) -> Any:                              # 원래 인자(권한 판정에 쓰임)
        return self.call.arguments


async def _maybe_await(v: Any) -> Any:
    """콜백 결과가 awaitable이면 기다린다."""
    if inspect.isawaitable(v):
        return await v
    return v


def _js_string(v: Any) -> str:
    """JS String(v) — 도구 결과를 글로."""
    if v is None:                                            # 결과 없음(JS undefined)
        return "undefined"
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _error_message(e: BaseException) -> str:
    """error instanceof Error ? error.message : String(error)."""
    m = getattr(e, "message", None)                          # LLMError 등
    return m if isinstance(m, str) else str(e)


# ─────────────────────────────────────────────────────────────────────────────
# 조각 모으기(streamChatResponse.helpers.ts processToolCallDelta)
# ─────────────────────────────────────────────────────────────────────────────


def process_tool_call_delta(delta: dict, calls: dict[str, ToolCall], index_to_id: dict[Any, str]) -> None:
    """도구 호출 조각 하나를 반영. id가 오면 index→id를 기억하고, id 없이 index만 오면 기억된 id로 찾는다. 둘 다 없으면 버린다."""
    tool_call_id: Optional[str] = None                       # 이 조각의 호출 id
    if delta.get("id"):                                      # id가 있으면(빈 글은 없는 것)
        tool_call_id = delta["id"]
        if "index" in delta:                                 # index !== undefined(null도 키로 기억 — JS와 같음)
            index_to_id[delta["index"]] = tool_call_id
    elif "index" in delta:                                   # id 없이 index만
        tool_call_id = index_to_id.get(delta["index"])
    if not tool_call_id:                                     # 대응을 못 찾음 → 버림
        log.warning("Tool call delta without ID or valid index mapping: %r", delta)
        return
    tc = calls.get(tool_call_id)                             # 없으면 새로
    if tc is None:
        tc = ToolCall(id=tool_call_id)
        calls[tool_call_id] = tc
    fn = delta.get("function") or {}                         # 함수 부분
    if fn.get("name"):                                       # 이름은 덮어쓴다(이어 붙이지 않음)
        tc.name = fn["name"]
    if fn.get("arguments"):                                  # 인자는 이어 붙이고 매번 해석 시도
        tc.arguments_str += fn["arguments"]
        try:
            tc.arguments = js_parse(tc.arguments_str)        # 성공하면 갱신(실패하면 이전 값 유지)
            tc.start_notified = True
        except ValueError:
            pass                                             # 아직 덜 옴
    if delta.get("extra_content"):                           # 추가 내용
        tc.extra_content = delta["extra_content"]


# ─────────────────────────────────────────────────────────────────────────────
# 도구 정의·요청 도구 목록(tools/index.tsx convertToolToChatCompletionTool, handleToolCalls.ts getRequestTools)
# ─────────────────────────────────────────────────────────────────────────────


def convert_tool_to_chat_completion_tool(tool: Tool) -> dict:
    """요청 본문 tools[] 항목. parameters에는 type·required·properties 세 키만(required가 없으면 빠짐)."""
    params: dict = {"type": "object"}                        # 순서: type, required, properties
    req = (tool.parameters or {}).get("required")            # 필수 목록
    if req is not None:                                      # undefined면 키째 생략
        params["required"] = req
    params["properties"] = (tool.parameters or {}).get("properties")
    return {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": params}}


async def get_request_tools(tools: list[Tool], check_permission: CheckPermission, headless: bool) -> list[dict]:
    """LLM에 보낼 도구 목록: allow는 넣고, ask는 헤드리스가 아닐 때만, exclude는 뺀다(인자 {}로 판정)."""
    allowed: list[Tool] = []                                 # 통과한 도구
    for tool in tools:                                       # 원래 순서 유지
        perm = await _maybe_await(check_permission(tool, {}))
        if perm == "allow" or (perm == "ask" and not headless):
            allowed.append(tool)
    return [convert_tool_to_chat_completion_tool(t) for t in allowed]


def validate_tool_call_args_present(call: ToolCall, tool: Tool) -> None:
    """최상위 required만 확인. 없거나 null이면 오류(tools/index.tsx:333-346)."""
    args = call.arguments if isinstance(call.arguments, dict) else {}   # 객체가 아니면 모든 키가 undefined
    for p in (tool.parameters or {}).get("required") or []:
        if args.get(p) is None:                              # undefined 또는 null
            raise ValueError(f'Required parameter "{p}" missing for tool "{call.name}"')


# ─────────────────────────────────────────────────────────────────────────────
# 사전 처리(helpers.ts preprocessStreamedToolCalls)
# ─────────────────────────────────────────────────────────────────────────────


async def preprocess_streamed_tool_calls(tool_calls: list[ToolCall], tools: list[Tool],
                                         on_event: Optional[ev.EventSink]) -> tuple[list[PreprocessedToolCall], list[dict]]:
    """도구 찾기 → 필수 인자 → preprocess. 실패는 tool 메시지 모양 {role, tool_call_id, content}로 모은다."""
    pre: list[PreprocessedToolCall] = []                     # 성공
    errors: list[dict] = []                                  # 실패 글
    for call in tool_calls:                                  # 순서대로
        try:
            tool = next((t for t in tools if t.name == call.name), None)   # 쓸 수 있는 전체 도구에서 찾음(ask 제외 목록이 아님)
            if tool is None:
                raise ValueError(f"Tool {call.name} not found")
            validate_tool_call_args_present(call, tool)      # 필수 인자
            p = PreprocessedToolCall(call=call, tool=tool)
            if tool.preprocess is not None:                  # 사전 처리(Edit의 '먼저 읽었나' 등)
                p.preprocess_result = await tool.preprocess(call.arguments)
            pre.append(p)
        except Exception as e:                               # 실패해도 화면에는 시작을 알린다
            ev.safe_emit(on_event, ev.tool_start(call.name, call.arguments, call.id))
            msg = _error_message(e)                          # 글 그대로(접두어 없음)
            reason = e.reason if isinstance(e, ContinueError) else "Unknown"   # 텔레메트리용 이유(로그만)
            log.error("Invalid tool call %s: %s (%s)", call.name, msg, reason)
            errors.append({"role": "tool", "tool_call_id": call.id, "content": msg})
            ev.safe_emit(on_event, ev.tool_error(msg, call.name, call.id))
    return pre, errors


# ─────────────────────────────────────────────────────────────────────────────
# 권한(helpers.ts checkToolPermissionApproval / requestUserPermission)
# ─────────────────────────────────────────────────────────────────────────────

_APPROVED = {"allow", "allow_once", "allow_always", "approve", "approved", "yes", "y"}   # ask_user가 승인으로 돌려줄 수 있는 값


async def request_user_permission(call: PreprocessedToolCall, ask_user: Optional[AskUser],
                                  on_event: Optional[ev.EventSink]) -> bool:
    """사용자에게 묻는다. ask_user가 없으면 거부(cn: onToolPermissionRequest 콜백이 없으면 false)."""
    if ask_user is None:
        return False
    args = call.preprocess_result.args if call.preprocess_result is not None else call.arguments   # 고쳐진 인자 우선
    preview = call.preprocess_result.preview if call.preprocess_result is not None else None       # 미리보기
    ev.safe_emit(on_event, ev.permission_request(call.name, args, preview, call.id))   # 화면 알림
    answer = await _maybe_await(ask_user(call.name, args, preview, call.id))   # 대답
    if isinstance(answer, bool):                             # 참/거짓도 받는다
        return answer
    return isinstance(answer, str) and answer.lower() in _APPROVED


async def check_tool_permission_approval(call: PreprocessedToolCall, check_permission: CheckPermission,
                                         ask_user: Optional[AskUser], on_event: Optional[ev.EventSink],
                                         headless: bool) -> tuple[bool, Optional[str]]:
    """(승인 여부, 거부 이유 'user'|'policy'). 헤드리스의 ask는 정책 거부(목록에서 빠졌는데 불렸다는 뜻)."""
    perm = await _maybe_await(check_permission(call.tool, call.arguments))   # 원래 인자로 판정(cn과 같음)
    if perm == "allow":
        return True, None
    if perm == "ask":
        if headless:                                         # 헤드리스는 묻지 않는다
            return False, "policy"
        ok = await request_user_permission(call, ask_user, on_event)
        return (True, None) if ok else (False, "user")
    if perm == "exclude":                                    # 정책으로 막힘
        return False, "policy"
    return False, "policy"                                   # 알 수 없는 값도 정책 거부


# ─────────────────────────────────────────────────────────────────────────────
# 실행(helpers.ts executeStreamedToolCalls)
# ─────────────────────────────────────────────────────────────────────────────


async def execute_streamed_tool_calls(calls: list[PreprocessedToolCall], history: ChatHistory,
                                      check_permission: CheckPermission, ask_user: Optional[AskUser],
                                      on_event: Optional[ev.EventSink], headless: bool) -> tuple[bool, list[dict]]:
    """권한은 순서대로 확인하고, 허락된 것은 바로 시작해 동시에 돌린다. 결과는 원래 순서로 모은다.
    돌려주는 값: (거부가 있었나, [{role, tool_call_id, content, status}])."""
    parallel = len(calls)                                    # parallelToolCallCount — Read·Bash가 출력 한도를 이 값으로 나눔
    by_index: dict[int, dict] = {}                           # 위치 → 결과
    tasks: list[asyncio.Task] = []                           # 실행 중인 작업
    has_rejection = False                                    # 거부 여부

    async def run_one(index: int, call: PreprocessedToolCall) -> None:
        """승인된 도구 하나 실행."""
        try:
            args = call.preprocess_result.args if call.preprocess_result is not None else call.arguments   # 고쳐진 인자 우선
            result = await call.tool.run(args, ToolRunContext(tool_call_id=call.id, parallel_tool_call_count=parallel))
            by_index[index] = {"role": "tool", "tool_call_id": call.id, "content": result, "status": "done"}
            ev.safe_emit(on_event, ev.tool_result(_js_string(result), call.name, "done", call.id))
            history.add_tool_result(call.id, _js_string(result), "done")   # 바로 기록(화면 반영)
        except Exception as e:                               # 실패 글
            msg = f"Error executing tool {call.name}: {_error_message(e)}"
            log.error("Tool execution failed: %s", msg)
            by_index[index] = {"role": "tool", "tool_call_id": call.id, "content": msg, "status": "errored"}
            ev.safe_emit(on_event, ev.tool_error(msg, call.name, call.id))
            history.add_tool_result(call.id, msg, "errored")

    for index, call in enumerate(calls):                     # 권한 단계(차례대로)
        try:
            ev.safe_emit(on_event, ev.tool_start(call.name, call.arguments, call.id))   # 확인 전에 시작 알림
            approved, reason = await check_tool_permission_approval(call, check_permission, ask_user, on_event, headless)
            if not approved:                                 # 거부
                denied = POLICY_DENIED if (reason or "user") == "policy" else USER_DENIED
                by_index[index] = {"role": "tool", "tool_call_id": call.id, "content": denied, "status": "canceled"}
                ev.safe_emit(on_event, ev.tool_result(denied, call.name, "canceled", call.id))
                history.add_tool_result(call.id, denied, "canceled")
                has_rejection = True                         # 뒤의 도구는 각자 따로 판정(취소하지 않음)
                continue
            history.update_tool_status(call.id, "calling")   # 승인 즉시 '실행 중'
            tasks.append(asyncio.ensure_future(run_one(index, call)))   # 바로 시작(다음 권한 확인과 겹침)
        except Exception as e:                               # 권한 확인 자체가 실패
            msg = f"Error checking permissions for {call.name}: {_error_message(e)}"
            log.error("Permission check failed: %s", msg)
            by_index[index] = {"role": "tool", "tool_call_id": call.id, "content": msg, "status": "errored"}
            ev.safe_emit(on_event, ev.tool_error(msg, call.name, call.id))
            history.add_tool_result(call.id, msg, "errored")  # 묶음은 계속

    if tasks:                                                # Promise.all
        await asyncio.gather(*tasks)
    entries = [by_index[i] for i in range(len(calls)) if i in by_index]   # 원래 순서
    return has_rejection, entries


# ─────────────────────────────────────────────────────────────────────────────
# 한 번의 응답 처리(handleToolCalls.ts handleToolCalls)
# ─────────────────────────────────────────────────────────────────────────────


async def handle_tool_calls(tool_calls: list[ToolCall], content: str, history: ChatHistory, *,
                            tools: list[Tool], check_permission: CheckPermission, ask_user: Optional[AskUser],
                            on_event: Optional[ev.EventSink], headless: bool, usage: Any = None) -> bool:
    """assistant 메시지를 기록하고 도구를 처리한다. 돌려주는 값: 턴을 바로 끝내야 하나(헤드리스에서 거부가 있었을 때)."""
    if not tool_calls:                                       # 도구 호출이 없으면
        if content:                                          # 글이 있을 때만 기록
            history.add_assistant_message(content, None, usage)
        return False
    chat_tool_calls = []                                     # ChatCompletion 형식 도구 호출
    for tc in tool_calls:
        d = {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": js_stringify(tc.arguments)}}   # 인자는 해석 결과를 다시 직렬화(원문 아님)
        if tc.extra_content:                                 # ...(tc.extra_content && {extra_content})
            d["extra_content"] = tc.extra_content
        chat_tool_calls.append(d)
    history.add_assistant_message(content or "", chat_tool_calls, usage)   # 상태 generated로 기록

    pre, errors = await preprocess_streamed_tool_calls(tool_calls, tools, on_event)   # 사전 처리
    for err in errors:                                       # 실패는 별도 항목이 아니라 상태에 붙인다(중복 tool 메시지 방지)
        history.add_tool_result(err["tool_call_id"], err["content"] or "", "errored")

    has_rejection, _ = await execute_streamed_tool_calls(pre, history, check_permission, ask_user, on_event, headless)
    if headless and has_rejection:                           # 헤드리스 거부 → 턴 종료
        log.debug("Tool call rejected in headless mode - returning current content")
        return True
    return False
