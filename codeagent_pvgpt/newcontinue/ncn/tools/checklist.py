# ncn/tools/checklist.py — Checklist 도구(작업 목록). cn extensions/cli/src/tools/writeChecklist.ts 를 그대로 옮긴 것
# 실행은 받은 목록을 "Task list status:" 뒤에 그대로 붙여 돌려줄 뿐(상태 저장 없음 — 이전 목록은 대화 기록에 있음)
from __future__ import annotations                           # 타입 주석 지연 평가

from ncn.tools.base import PreprocessResult, Tool, ToolCallPreview   # 공용 형식


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 미리보기는 checklist 종류로 목록 그대로."""
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="checklist", content=args.get("checklist"))])


async def _run(args: dict, context=None) -> str:
    """run — `Task list status:\\n${checklist}`(값이 없으면 JS 템플릿처럼 'undefined')."""
    checklist = args.get("checklist", None)                  # 목록 글
    text = "undefined" if "checklist" not in args else ("null" if checklist is None else str(checklist))   # 템플릿 글 변환(드문 경우)
    return f"Task list status:\n{text}"                      # cn 원문 형식


CHECKLIST_TOOL = Tool(
    name="Checklist",
    display_name="Checklist",
    description="Create or update a task checklist. The old checklist can be seen in the chat history if it exists. Use this tool to write a new checklist or edit the existing one.",
    parameters={
        "type": "object",
        "required": ["checklist"],
        "properties": {
            "checklist": {
                "type": "string",
                "description": "The complete checklist in markdown format using - [ ] for incomplete tasks and - [x] for completed tasks. Avoid headers and additional content unless specifically being used to group checkboxes. Try to keep the list short, and make each item specific and actionable.",
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
    readonly=False,
    is_built_in=True,
)
