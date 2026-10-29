# ncn/tools/base.py — 도구 공통 형식. cn extensions/cli/src/tools/types.ts 를 그대로 옮긴 것
# 모든 도구 모듈은 이 형식으로 Tool 객체 하나(또는 여러 개)를 만든다. 루프(loop/)는 이 형식만 안다
from __future__ import annotations                           # 타입 주석 지연 평가

from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Awaitable, Callable, Optional        # 타입


class ContinueError(Exception):                             # 도구 실행 오류의 공통 예외(cn ContinueError)
    """cn core/util/errors.ts ContinueError — reason(이유 코드)과 사용자에게 보일 메시지.
    도구 실행 중 이 오류가 나면 루프는 메시지를 그대로 모델에게 돌려준다(Error executing tool ... 형식은 루프가 붙임)."""

    def __init__(self, reason: str, message: str):          # 이유 코드와 메시지로 만든다
        super().__init__(message)                           # 부모에 메시지를 넘겨 str(e)가 메시지가 되게
        self.reason = reason                                 # 예: "Unspecified", "FileTooLarge", "FileNotFound"


@dataclass                                                  # dataclass로 선언(생성자 자동)
class ToolCallPreview:                                      # 승인 화면 미리보기 한 조각
    """승인 화면에 보여 줄 미리보기 한 조각(types.ts ToolCallPreview)."""
    type: str                                               # 미리보기 종류
    content: str                                             # 보여 줄 글
    color: Optional[str] = None                              # 색(대화형 화면용)
    padding_left: Optional[int] = None                       # 들여쓰기(대화형 화면용)


@dataclass                                                  # dataclass로 선언
class PreprocessResult:                                     # preprocess가 돌려주는 결과
    """preprocess 결과(types.ts PreprocessToolCallResult): 고쳐진 인자 + 미리보기."""
    args: dict                                               # run에 넘길 인자
    preview: list[ToolCallPreview] = field(default_factory=list)   # 미리보기
    context: Optional[dict] = None                           # {"toolCallId": ...} 등


@dataclass                                                  # dataclass로 선언
class ToolRunContext:                                       # run에 넘기는 실행 정보
    """run에 넘기는 실행 정보(types.ts ToolRunContext)."""
    tool_call_id: str                                        # 도구 호출 id
    parallel_tool_call_count: int = 1                        # 동시에 실행 중인 도구 수 — 출력 한도를 이 값으로 나눈다


@dataclass                                                  # dataclass로 선언
class Tool:                                                 # 도구 한 개의 정의(이름·설명·스키마·실행 함수)
    """도구 하나(types.ts Tool). 이름·설명·인자 스키마는 cn 원문과 글자 하나까지 같아야 한다(요청 본문 비교 대상)."""
    name: str                                                # LLM에 보이는 이름(예: "Read")
    display_name: str                                        # 화면 표시 이름
    description: str                                         # LLM에 보이는 설명(cn 원문)
    parameters: dict                                         # JSON 스키마({"type":"object","required":[...],"properties":{...}}) — 키 순서도 cn과 같게
    run: Callable[[dict, ToolRunContext], Awaitable[str]]    # 실행 → 모델에게 돌려줄 글
    preprocess: Optional[Callable[[dict], Awaitable[PreprocessResult]]] = None   # 실행 전 검사·미리보기(오류면 ContinueError)
    readonly: bool = False                                   # 읽기 전용 여부(plan 모드 판단에 사용)
    is_built_in: bool = True                                 # 내장 도구(MCP 도구는 False)
    evaluate_tool_call_policy: Optional[Callable[[str, dict], str]] = None   # (기본 정책, 인자) → 정책. Bash의 위험 명령 판정 등


def openai_tool_definition(tool: Tool) -> dict:             # Tool을 LLM 요청의 tools[] 항목으로 바꾼다
    """LLM 요청 본문의 tools[] 항목. cn이 보내는 모양과 같게(이름·설명·parameters)."""
    return {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters}}


Json = Any                                                   # 도구 인자 등 JSON 값 표기용
