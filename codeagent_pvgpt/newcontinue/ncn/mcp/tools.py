# ncn/mcp/tools.py — MCP 도구 → ncn Tool, tools/call 결과 → 모델에게 돌려줄 글. cn extensions/cli/src/tools/index.tsx
#   convertMcpToolToContinueTool: name·displayName = 서버가 준 이름 그대로, description = description ?? "",
#   parameters = {type:"object", properties: inputSchema.properties ?? {}, required: inputSchema.required}, isBuiltIn=false,
#   run = JSON.stringify(result?.content) ?? "" — cn 은 isError 를 보지 않는다(오류 결과도 내용 글만 그대로 감).
# [ncn 부록B M1] isError=true 면 McpToolError 를 던져 루프가 내장 도구 실패와 같은 "Error executing tool {이름}: {결과 글}" 로
#   돌려주게 한다(상태 errored → ACP 카드 failed). annotations 는 계속 뺀다(cn 모양). 호환 모드(NCN_CN_COMPAT=1)는 cn 그대로.
# LLM 요청에 실릴 때는 루프의 convertToolToChatCompletionTool 이 {type, required, properties} 순서로 다시 만든다.
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # JSON.stringify 흉내용
from typing import Any, Awaitable, Callable                  # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치(NCN_CN_COMPAT=1)
from ncn.mcp.content import shape_content                    # Node SDK 스키마 모양으로 정리
from ncn.tools.base import Tool, ToolRunContext              # 공용 형식


def _js_value(v: Any) -> Any:                                # JSON.stringify 숫자 표기 맞추기
    """정수 값인 실수(1.0)는 JS 처럼 1 로. 안쪽까지 재귀."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e21:   # JS 는 1e21 미만 정수 값을 소수점 없이 씀
        return int(v)                                        # 정수로
    if isinstance(v, list):                                  # 배열
        return [_js_value(x) for x in v]                     # 원소마다
    if isinstance(v, dict):                                  # 객체
        return {k: _js_value(x) for k, x in v.items()}       # 값마다(키 순서 유지)
    return v                                                 # 그 밖은 그대로


def js_json(v: Any) -> str:                                  # JSON.stringify(v)(들여쓰기 없음)
    """공백 없는 JSON, 한글 그대로."""
    return json.dumps(_js_value(v), ensure_ascii=False, separators=(",", ":"))   # JS 와 같은 꼴


def format_call_tool_result(result: Any) -> str:             # tools/call 결과 → 모델에게 돌려줄 글
    """JSON.stringify(result?.content) ?? "" — result 가 없으면 "". content 는 Node SDK 스키마대로 정리(annotations 등 버림)."""
    if result is None:                                       # result 가 없으면
        return ""                                            # JSON.stringify(undefined) → undefined → "" (빈 글)
    content = result.get("content") if isinstance(result, dict) else getattr(result, "content", None)   # dict 또는 SDK 객체
    if content is not None and not isinstance(content, list):   # SDK 객체 목록이 아닌 경우 방어
        content = list(content)                              # 목록으로
    blocks = [c if isinstance(c, dict) else c.model_dump(by_alias=True, exclude_unset=True, mode="json")   # 받은 키만(선 기본값 제외)
              for c in (content or [])]                      # 내용마다
    return js_json(shape_content(blocks))                    # 정리 후 JSON 글


class McpToolError(Exception):                               # [ncn 부록B M1] isError 결과를 루프의 오류 경로로 보내는 예외
    """str(e) = 결과 글(JSON) — 루프가 "Error executing tool {이름}: " 을 앞에 붙인다."""


def is_error_result(result: Any) -> bool:                    # [ncn 부록B M1] tools/call 결과가 isError=true 인가
    """dict 면 isError 키, SDK 객체면 is_error 속성."""
    if result is None:                                       # 결과 없음
        return False                                         # 오류 아님
    if isinstance(result, dict):                             # dict 모양
        return result.get("isError") is True                 # 키
    return getattr(result, "is_error", None) is True         # SDK 객체 속성


def mcp_tool_fields(mcp_tool: Any) -> tuple[str, Any, dict]:   # SDK Tool 객체·dict 에서 (이름, 설명, inputSchema)
    """SDK(pydantic) 객체면 속성, dict 면 키(name·description·inputSchema)."""
    if isinstance(mcp_tool, dict):                           # dict 모양
        return mcp_tool.get("name", ""), mcp_tool.get("description"), mcp_tool.get("inputSchema") or {}   # 키에서
    return mcp_tool.name, getattr(mcp_tool, "description", None), getattr(mcp_tool, "input_schema", None) or {}   # 속성에서


def convert_mcp_tool_to_continue_tool(mcp_tool: Any, run_tool: Callable[[str, dict], Awaitable[Any]]) -> Tool:   # MCP 도구 하나 → Tool
    """convertMcpToolToContinueTool. run_tool(name, args) 는 MCPService.run_tool(결과 객체를 돌려줌)."""
    name, description, schema = mcp_tool_fields(mcp_tool)    # 필드
    properties = schema.get("properties")                    # inputSchema.properties 꺼내기
    parameters: dict = {"type": "object",                    # cn 객체 순서: type → properties → required
                        "properties": properties if properties is not None else {}}   # ?? {} (없으면 빈 객체)
    if schema.get("required") is not None:                   # undefined 면 키 없음(요청 본문에서도 빠짐)
        parameters["required"] = schema["required"]          # 필수 목록

    async def run(args: dict, ctx: ToolRunContext) -> str:   # 실행 → 결과 글
        result = await run_tool(name, args)                  # services.mcp.runTool(name, args) 호출
        text = format_call_tool_result(result)               # JSON.stringify(result?.content) ?? "" 로 바꿈
        if is_error_result(result) and not cn_compat():      # [ncn 부록B M1] 오류 결과(cn: 표시 없이 내용 글만 돌려줌)
            raise McpToolError(text)                         # 루프가 "Error executing tool {name}: {text}" + errored 로 처리
        return text                                          # 정상 결과 글

    return Tool(name=name, display_name=name,                # 이름·표시 이름 = 서버 이름 그대로
                description=description if description is not None else "",   # ?? "" (없으면 빈 글)
                parameters=parameters, run=run,              # 스키마·실행
                readonly=False, is_built_in=False)           # readonly: undefined(거짓), 내장 아님
