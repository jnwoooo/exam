# ncn/loop/events.py — 루프가 화면/ACP로 내보내는 이벤트 종류. cn extensions/cli/src/stream/streamChatResponse.types.ts StreamCallbacks
# 루프는 LoopDeps.on_event(dict) 하나로 모든 알림을 보낸다. 이벤트는 {"type": 종류, ...} 모양의 dict.
# cn 콜백과의 대응(괄호 안이 cn 이름). tool_call_id는 cn 콜백에는 없지만 ACP 도구 카드 연결용으로 ncn이 붙인다.
#
# | type                | cn 콜백                  | 필드                                                        | 언제
# |---------------------|--------------------------|-------------------------------------------------------------|------------------------------------------
# | content             | onContent                | text                                                        | 응답 글 조각을 받을 때마다
# | content_complete    | onContentComplete        | content                                                     | 한 번의 LLM 응답 글이 끝났을 때(글이 있을 때만)
# | tool_start          | onToolStart              | name, arguments, tool_call_id, [preprocessed_args]          | 권한 확인 직전(사전 처리 실패 때도 한 번)
#   preprocessed_args: [ncn] 사전 처리로 고쳐진 인자(Edit의 oldContent·newContent 등) — 사전 처리가 끝난 호출에만 붙는다(ACP diff용)
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

ALL_EVENT_TYPES = (CONTENT, CONTENT_COMPLETE, TOOL_START, PERMISSION_REQUEST, TOOL_RESULT,  # 모든 이벤트 종류를 한 곳에 모은 튜플
                   TOOL_ERROR, SYSTEM_MESSAGE, RETRY, USAGE)   # 문서·시험용 목록

EventSink = Callable[[dict], None]                           # on_event 형식


def noop(_event: dict) -> None:                             # 아무것도 안 하는 기본 수신자
    """기본 이벤트 받는 곳(아무것도 안 함)."""
    return None                                             # on_event를 안 줬을 때 쓰는 빈 동작


def content(text: str) -> dict:                             # content 이벤트 생성기(onContent)
    """응답 글 조각."""
    return {"type": CONTENT, "text": text}                  # 글 조각 dict


def content_complete(text: str) -> dict:                    # content_complete 이벤트 생성기(onContentComplete)
    """응답 글 끝."""
    return {"type": CONTENT_COMPLETE, "content": text}      # 한 응답의 전체 글 dict


def tool_start(name: str, arguments: Any, tool_call_id: Optional[str], preprocessed_args: Any = None) -> dict:  # tool_start 이벤트 생성기(onToolStart)
    """도구 시작. preprocessed_args(사전 처리 결과 인자)는 있을 때만 키를 넣는다."""
    e = {"type": TOOL_START, "name": name, "arguments": arguments, "tool_call_id": tool_call_id}  # 도구 이름·인자·호출 id를 담은 dict
    if preprocessed_args is not None:                        # [ncn] 사전 처리 결과가 있으면
        e["preprocessed_args"] = preprocessed_args          # ACP diff 표시용으로 함께 싣는다
    return e                                                # 이벤트 반환


def permission_request(name: str, arguments: Any, preview: Any, tool_call_id: Optional[str]) -> dict:  # permission_request 이벤트 생성기(onToolPermissionRequest)
    """승인 요청(preview는 ncn.tools.base.ToolCallPreview 목록 또는 None)."""
    return {"type": PERMISSION_REQUEST, "name": name, "arguments": arguments, "preview": preview, "tool_call_id": tool_call_id}  # 승인 질문에 필요한 미리보기까지 담은 dict


def tool_result(result: str, name: str, status: str, tool_call_id: Optional[str]) -> dict:  # tool_result 이벤트 생성기(onToolResult)
    """도구 결과(status: done | canceled)."""
    return {"type": TOOL_RESULT, "name": name, "result": result, "status": status, "tool_call_id": tool_call_id}  # 결과 글과 상태(done|canceled)를 담은 dict


def tool_error(error: str, name: Optional[str], tool_call_id: Optional[str]) -> dict:  # tool_error 이벤트 생성기(onToolError)
    """도구 오류."""
    return {"type": TOOL_ERROR, "name": name, "error": error, "tool_call_id": tool_call_id}  # 실패 사유를 담은 dict


def system_message(message: str) -> dict:                   # system_message 이벤트 생성기(onSystemMessage)
    """시스템 안내."""
    return {"type": SYSTEM_MESSAGE, "message": message}     # 안내 문구 dict


def retry(attempt: int, delay_ms: int, error: str) -> dict:  # retry 이벤트 생성기(ncn 추가)
    """[ncn] 재시도 직전."""
    return {"type": RETRY, "attempt": attempt, "delay_ms": delay_ms, "error": error}  # 몇 번째 시도·대기 시간·오류를 담은 dict


def usage(u: Any) -> dict:                                  # usage 이벤트 생성기(ncn 추가)
    """[ncn] 사용량."""
    return {"type": USAGE, "usage": u}                      # 사용량 dict


def safe_emit(sink: Optional[EventSink], event: dict) -> None:  # 이벤트를 안전하게 내보내는 함수
    """이벤트를 보낸다. 받는 쪽 오류가 루프를 멈추지 않게 삼킨다(cn 콜백은 대부분 try 밖이지만 화면 오류로 턴이 깨지면 안 됨)."""
    if sink is None:                                        # 받는 곳이 없으면
        return                                              # 아무 일도 하지 않는다
    try:                                                    # 수신자 오류가 루프로 번지지 않게 감싼다
        sink(event)                                         # 이벤트 전달
    except Exception:                                        # 화면 쪽 오류는 무시
        import logging                                       # 지연 import
        logging.getLogger("ncn.loop.events").exception("on_event handler failed")  # 실패 내용은 로그로만 남긴다
