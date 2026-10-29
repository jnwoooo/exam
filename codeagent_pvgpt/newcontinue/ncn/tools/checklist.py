# ncn/tools/checklist.py — Checklist 도구(작업 목록). cn extensions/cli/src/tools/writeChecklist.ts 를 그대로 옮긴 것
# 실행은 받은 목록을 "Task list status:" 뒤에 그대로 붙여 돌려줄 뿐(상태 저장 없음 — 이전 목록은 대화 기록에 있음)
from __future__ import annotations                           # 타입 주석 지연 평가

from ncn.tools.base import PreprocessResult, Tool, ToolCallPreview   # 공용 형식


async def _preprocess(args: dict) -> PreprocessResult:      # preprocess 훅: 호출 전 미리보기를 만든다
    """preprocess — 미리보기는 checklist 종류로 목록 그대로."""
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="checklist", content=args.get("checklist"))])  # 인자는 그대로, 미리보기는 checklist 종류로 목록 원문


async def _run(args: dict, context=None) -> str:            # run 훅: 받은 목록을 상태 글로 돌려줌
    """run — `Task list status:\\n${checklist}`(값이 없으면 JS 템플릿처럼 'undefined')."""
    checklist = args.get("checklist", None)                  # 목록 글
    text = "undefined" if "checklist" not in args else ("null" if checklist is None else str(checklist))   # 템플릿 글 변환(드문 경우)
    return f"Task list status:\n{text}"                      # cn 원문 형식


CHECKLIST_TOOL = Tool(                                      # Checklist 도구 정의(cn writeChecklist.ts 대응)
    name="Checklist",                                       # 모델에 보이는 도구 이름
    display_name="Checklist",                               # 화면 표시 이름
    description="Create or update a task checklist. The old checklist can be seen in the chat history if it exists. Use this tool to write a new checklist or edit the existing one.",  # 모델에게 주는 도구 설명 원문(cn 그대로)
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "required": ["checklist"],                          # checklist 인자 필수
        "properties": {                                     # 인자별 정의 묶음
            "checklist": {                                  # checklist 인자 정의
                "type": "string",                           # 마크다운 문자열
                "description": "The complete checklist in markdown format using - [ ] for incomplete tasks and - [x] for completed tasks. Avoid headers and additional content unless specifically being used to group checkboxes. Try to keep the list short, and make each item specific and actionable.",  # 작성 형식 안내 원문(cn 그대로)
            },                                              # checklist 정의 끝
        },                                                  # properties 끝
    },                                                      # parameters 끝
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # preprocess 함수 연결
    readonly=False,                                         # 쓰기 도구라 읽기 전용 아님
    is_built_in=True,                                       # 내장 도구 표시
)
