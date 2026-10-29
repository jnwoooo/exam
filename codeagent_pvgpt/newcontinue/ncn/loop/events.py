# ncn/loop/events.py — 루프가 화면/ACP로 내보내는 이벤트 종류. cn extensions/cli/src/stream/streamChatResponse.types.ts StreamCallbacks
# 루프는 LoopDeps.on_event(dict) 하나로 모든 알림을 보낸다. 이벤트는 {"type": 종류, ...} 모양의 dict.
# cn 콜백과의 대응(괄호 안이 cn 이름). tool_call_id는 cn 콜백에는 없지만 ACP 도구 카드 연결용으로 ncn이 붙인다.
#
# | type                | cn 콜백                  | 필드                                                        | 언제
# |---------------------|--------------------------|-------------------------------------------------------------|------------------------------------------
# | content             | onContent                | text                                                        | 응답 글 조각을 받을 때마다
# | content_complete    | onContentComplete        | content                                                     | 한 번의 LLM 응답 글이 끝났을 때(글이 있을 때만)
# | tool_start          | onToolStart              | name, arguments, tool_call_id                               | 권한 확인 직전(사전 처리 실패 때도 한 번)
# | permission_request  | onToolPermissionRequest  | name, arguments, preview(list[ToolCallPreview]), tool_call_id | 승인 질문 직전(ask_user 호출 직전)
# | tool_result         | onToolResult             | name, result, status("done"|"canceled"), tool_call_id       | 실행 성공 또는 거부
# | tool_error          | onToolError              | name, error, tool_call_id                                   | 사전 처리 실패·실행 실패·권한 확인 실패
# | system_message      | onSystemMessage          | message                                                     | 압축 안내 등(P3 압축 훅이 보냄)
# | retry               | (없음, ncn)               | attempt, delay_ms, error                                    | LLM 호출 재시도 직전 — 화면은 이번 호출의 글 조각을 지워야 한다
# | usage               | (없음, ncn)               | usage                                                       | 한 번의 LLM 응답에 사용량이 왔을 때
from __future__ import annotations                           # 타입 주석 지연 평가

from typing import Any, Callable, Optional                   # 타입

CONTENT = "content"                                          # 응답 글 조각
CONTENT_COMPLETE = "content_complete"                        # 응답 글 끝
TOOL_START = "tool_start"                                    # 도구 시작
PERMISSION_REQUEST = "permission_request"                    # 승인 요청
TOOL_RESULT = "tool_result"                                  # 도구 결과
TOOL_ERROR = "tool_error"                                    # 도구 오류
SYSTEM_MESSAGE = "system_message"                            # 시스템 안내
RETRY = "retry"                                              # [ncn] 재시도
USAGE = "usage"                                              # [ncn] 사용량

ALL_EVENT_TYPES = (CONTENT, CONTENT_COMPLETE, TOOL_START, PERMISSION_REQUEST, TOOL_RESULT,
                   TOOL_ERROR, SYSTEM_MESSAGE, RETRY, USAGE)   # 문서·시험용 목록

EventSink = Callable[[dict], None]                           # on_event 형식


def noop(_event: dict) -> None:
    """기본 이벤트 받는 곳(아무것도 안 함)."""
    return None


def content(text: str) -> dict:
    """응답 글 조각."""
    return {"type": CONTENT, "text": text}


def content_complete(text: str) -> dict:
    """응답 글 끝."""
    return {"type": CONTENT_COMPLETE, "content": text}


def tool_start(name: str, arguments: Any, tool_call_id: Optional[str]) -> dict:
    """도구 시작."""
    return {"type": TOOL_START, "name": name, "arguments": arguments, "tool_call_id": tool_call_id}


def permission_request(name: str, arguments: Any, preview: Any, tool_call_id: Optional[str]) -> dict:
    """승인 요청(preview는 ncn.tools.base.ToolCallPreview 목록 또는 None)."""
    return {"type": PERMISSION_REQUEST, "name": name, "arguments": arguments, "preview": preview, "tool_call_id": tool_call_id}


def tool_result(result: str, name: str, status: str, tool_call_id: Optional[str]) -> dict:
    """도구 결과(status: done | canceled)."""
    return {"type": TOOL_RESULT, "name": name, "result": result, "status": status, "tool_call_id": tool_call_id}


def tool_error(error: str, name: Optional[str], tool_call_id: Optional[str]) -> dict:
    """도구 오류."""
    return {"type": TOOL_ERROR, "name": name, "error": error, "tool_call_id": tool_call_id}


def system_message(message: str) -> dict:
    """시스템 안내."""
    return {"type": SYSTEM_MESSAGE, "message": message}


def retry(attempt: int, delay_ms: int, error: str) -> dict:
    """[ncn] 재시도 직전."""
    return {"type": RETRY, "attempt": attempt, "delay_ms": delay_ms, "error": error}


def usage(u: Any) -> dict:
    """[ncn] 사용량."""
    return {"type": USAGE, "usage": u}


def safe_emit(sink: Optional[EventSink], event: dict) -> None:
    """이벤트를 보낸다. 받는 쪽 오류가 루프를 멈추지 않게 삼킨다(cn 콜백은 대부분 try 밖이지만 화면 오류로 턴이 깨지면 안 됨)."""
    if sink is None:
        return
    try:
        sink(event)
    except Exception:                                        # 화면 쪽 오류는 무시
        import logging                                       # 지연 import
        logging.getLogger("ncn.loop.events").exception("on_event handler failed")
