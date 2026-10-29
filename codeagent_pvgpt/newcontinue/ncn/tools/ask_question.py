# ncn/tools/ask_question.py — AskQuestion 도구(사용자에게 되묻기). cn extensions/cli/src/tools/askQuestion.ts + services/QuizService.ts
# 질문을 "대기 중 질문"으로 걸어 두고, 화면(대화형·ACP _ncn/ask)이 answer_question 으로 답하면 이어진다
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 답 기다리기
import time                                                  # requestId
from dataclasses import dataclass                            # 자료
from typing import Callable, Optional                        # 타입

from ncn.tools.base import Tool                              # 공용 형식


@dataclass
class PendingQuestion:
    """PendingQuestion — 질문 + requestId + 시각."""
    question: str                                            # 질문
    options: Optional[list] = None                           # 선택지
    default_answer: Optional[str] = None                     # 기본 답
    request_id: str = ""                                     # quiz-<밀리초>
    timestamp: int = 0                                       # 밀리초


class QuizService:
    """QuizService — 질문 하나를 걸고 답을 기다린다. 'questionRequested'/'questionAnswered' 를 듣는 함수 목록."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[PendingQuestion, asyncio.Future]] = {}   # requestId → (질문, 답 기다림)
        self.pending_question: Optional[PendingQuestion] = None   # 지금 걸린 질문(currentState.pendingQuestion)
        self.on_question_requested: list[Callable[[PendingQuestion], None]] = []   # 화면이 등록
        self.on_question_answered: list[Callable[[str, str, bool], None]] = []     # (requestId, 답, 직접 입력?)

    async def ask_question(self, question: str, options: Optional[list] = None, default_answer: Optional[str] = None) -> str:
        """askQuestion — 답이 올 때까지 기다림(헤드리스에서는 아무도 답하지 않으면 끝없이 기다림 — cn과 같음)."""
        now = int(time.time() * 1000)                        # Date.now()
        request_id = f"quiz-{now}"                           # cn 형식
        pq = PendingQuestion(question, options, default_answer, request_id, now)   # 대기 질문
        fut = asyncio.get_running_loop().create_future()     # 답 자리
        self._pending[request_id] = (pq, fut)                # 등록
        self.pending_question = pq                           # 현재 상태
        for fn in list(self.on_question_requested):          # emit("questionRequested")
            fn(pq)
        return await fut                                     # 답

    def answer_question(self, request_id: str, answer: str, is_custom_answer: bool = False) -> bool:
        """answerQuestion — 해당 질문이 없으면 False."""
        item = self._pending.pop(request_id, None)           # 꺼냄
        if not item:
            return False
        self.pending_question = None                         # 비움
        for fn in list(self.on_question_answered):           # emit("questionAnswered")
            fn(request_id, answer, is_custom_answer)
        if not item[1].done():                               # 답 전달
            item[1].set_result(answer)
        return True

    def initialize(self) -> None:
        """initialize — 상태 비우기."""
        self.pending_question = None
        self._pending.clear()


quiz_service = QuizService()                                 # 모듈 공용(cn quizService)


def _js_index_of(options: list, answer) -> int:
    """Array.indexOf — 엄격 비교(===). 글과 숫자는 다르게 본다."""
    for i, o in enumerate(options):                          # 차례로
        if type(o) is type(answer) and o == answer:          # 같은 형식·같은 값
            return i
    return -1


async def _run(args: dict, context=None) -> str:
    """run — 선택지 중 하나면 'User selected option N', 아니면 직접 답, 선택지가 없으면 'User answered'."""
    question = args.get("question")                          # 질문
    options = args.get("options")                            # 선택지
    default_answer = args.get("defaultAnswer")               # 기본 답
    answer = await quiz_service.ask_question(question, options, default_answer)   # 답 기다림
    if options and isinstance(options, list) and len(options) > 0:   # options && options.length > 0
        selected_index = _js_index_of(options, answer)       # 몇 번째
        if selected_index != -1:                             # 선택지 중 하나
            return f'User selected option {selected_index + 1}: "{answer}"'
        return f'User provided custom answer: "{answer}"'    # 직접 입력
    return f'User answered: "{answer}"'                      # 자유 답


ASK_QUESTION_TOOL = Tool(
    name="AskQuestion",
    display_name="Ask Question",
    description=(
        "Ask the user a clarifying question to gather requirements, preferences, or implementation details before proceeding.\n"
        "Guidelines:\n"
        "- You should use this tool **whenever you want to clarify your assumption or need answers to build your plan**.\n"
        "- DO NOT supply \"other\" or \"none of the above\" or similar as an option. User can always provide a free-form answer when needed.\n"
    ),
    parameters={
        "type": "object",
        "required": ["question", "options"],
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user",
            },
            "options": {
                "type": "array",
                "description": "The list of choices. Leave as empty array if user should provide a free-form answer.",
                "items": {
                    "type": "string",
                },
            },
            "defaultAnswer": {
                "type": "string",
                "description": "Default answer if user presses Enter without providing input",
            },
        },
    },
    run=_run,
    readonly=True,
    is_built_in=True,
)
