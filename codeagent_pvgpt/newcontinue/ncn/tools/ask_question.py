# ncn/tools/ask_question.py — AskQuestion 도구(사용자에게 되묻기). cn extensions/cli/src/tools/askQuestion.ts + services/QuizService.ts
# 질문을 "대기 중 질문"으로 걸어 두고, 화면(대화형·ACP _ncn/ask)이 answer_question 으로 답하면 이어진다
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 답 기다리기
import time                                                  # requestId
from dataclasses import dataclass                            # 자료
from typing import Callable, Optional                        # 타입

from ncn.tools.base import Tool                              # 공용 형식


@dataclass                                                  # 자료 클래스로 선언
class PendingQuestion:                                      # 대기 중 질문 한 건
    """PendingQuestion — 질문 + requestId + 시각."""
    question: str                                            # 질문
    options: Optional[list] = None                           # 선택지
    default_answer: Optional[str] = None                     # 기본 답
    request_id: str = ""                                     # quiz-<밀리초>
    timestamp: int = 0                                       # 밀리초


class QuizService:
    """QuizService — 질문 하나를 걸고 답을 기다린다. 'questionRequested'/'questionAnswered' 를 듣는 함수 목록."""

    def __init__(self) -> None:                             # 초기화: 대기 목록·현재 질문·듣는 함수 목록 준비
        self._pending: dict[str, tuple[PendingQuestion, asyncio.Future]] = {}   # requestId → (질문, 답 기다림)
        self.pending_question: Optional[PendingQuestion] = None   # 지금 걸린 질문(currentState.pendingQuestion)
        self.on_question_requested: list[Callable[[PendingQuestion], None]] = []   # 화면이 등록
        self.on_question_answered: list[Callable[[str, str, bool], None]] = []     # (requestId, 답, 직접 입력?)

    async def ask_question(self, question: str, options: Optional[list] = None, default_answer: Optional[str] = None) -> str:  # 질문을 걸고 답 올 때까지 기다리는 비동기 함수
        """askQuestion — 답이 올 때까지 기다림(헤드리스에서는 아무도 답하지 않으면 끝없이 기다림 — cn과 같음)."""
        now = int(time.time() * 1000)                        # Date.now()
        request_id = f"quiz-{now}"                           # cn 형식
        pq = PendingQuestion(question, options, default_answer, request_id, now)   # 대기 질문
        fut = asyncio.get_running_loop().create_future()     # 답 자리
        self._pending[request_id] = (pq, fut)                # 등록
        self.pending_question = pq                           # 현재 상태
        for fn in list(self.on_question_requested):          # emit("questionRequested")
            fn(pq)                                          # 듣는 쪽(화면)에 질문 전달
        return await fut                                     # 답

    def answer_question(self, request_id: str, answer: str, is_custom_answer: bool = False) -> bool:  # 화면이 사용자 답을 넘겨주는 함수
        """answerQuestion — 해당 질문이 없으면 False."""
        item = self._pending.pop(request_id, None)           # 꺼냄
        if not item:                                        # 없는 requestId면 처리 불가
            return False                                    # 답할 질문이 없음을 알림
        self.pending_question = None                         # 비움
        for fn in list(self.on_question_answered):           # emit("questionAnswered")
            fn(request_id, answer, is_custom_answer)
        if not item[1].done():                               # 답 전달
            item[1].set_result(answer)
        return True                                         # 정상 처리됨

    def initialize(self) -> None:                           # 상태 초기화 함수
        """initialize — 상태 비우기."""
        self.pending_question = None                        # 현재 질문 비움
        self._pending.clear()                               # 대기 중이던 질문 모두 버림


quiz_service = QuizService()                                 # 모듈 공용(cn quizService)


def _js_index_of(options: list, answer) -> int:             # JS indexOf와 같은 엄격 비교 도우미
    """Array.indexOf — 엄격 비교(===). 글과 숫자는 다르게 본다."""
    for i, o in enumerate(options):                          # 차례로
        if type(o) is type(answer) and o == answer:          # 같은 형식·같은 값
            return i                                        # 찾으면 그 위치 반환
    return -1                                               # 못 찾으면 -1(JS indexOf와 동일)


async def _run(args: dict, context=None) -> str:            # run 훅: 질문을 던지고 답을 글로 정리
    """run — 선택지 중 하나면 'User selected option N', 아니면 직접 답, 선택지가 없으면 'User answered'."""
    question = args.get("question")                          # 질문
    options = args.get("options")                            # 선택지
    default_answer = args.get("defaultAnswer")               # 기본 답
    answer = await quiz_service.ask_question(question, options, default_answer)   # 답 기다림
    if options and isinstance(options, list) and len(options) > 0:   # options && options.length > 0
        selected_index = _js_index_of(options, answer)       # 몇 번째
        if selected_index != -1:                             # 선택지 중 하나
            return f'User selected option {selected_index + 1}: "{answer}"'  # 선택지 번호는 1부터(cn 원문 형식)
        return f'User provided custom answer: "{answer}"'    # 직접 입력
    return f'User answered: "{answer}"'                      # 자유 답


ASK_QUESTION_TOOL = Tool(                                   # AskQuestion 도구 정의(cn askQuestion.ts 대응)
    name="AskQuestion",                                     # 모델에 보이는 도구 이름
    display_name="Ask Question",                            # 화면 표시 이름
    description=(                                           # 모델에게 주는 도구 설명 원문(cn 그대로)
        "Ask the user a clarifying question to gather requirements, preferences, or implementation details before proceeding.\n"
        "Guidelines:\n"
        "- You should use this tool **whenever you want to clarify your assumption or need answers to build your plan**.\n"
        "- DO NOT supply \"other\" or \"none of the above\" or similar as an option. User can always provide a free-form answer when needed.\n"
    ),
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "required": ["question", "options"],                # question·options 필수
        "properties": {                                     # 인자별 정의 묶음
            "question": {                                   # 질문 인자
                "type": "string",                           # 문자열
                "description": "The question to ask the user",  # 인자 설명 원문
            },                                              # question 정의 끝
            "options": {                                    # 선택지 인자
                "type": "array",                            # 문자열 배열
                "description": "The list of choices. Leave as empty array if user should provide a free-form answer.",  # 인자 설명 원문
                "items": {                                  # 배열 원소 정의
                    "type": "string",                       # 원소는 문자열
                },                                          # items 끝
            },                                              # options 정의 끝
            "defaultAnswer": {                              # 기본 답 인자
                "type": "string",                           # 문자열
                "description": "Default answer if user presses Enter without providing input",  # 인자 설명 원문
            },                                              # defaultAnswer 정의 끝
        },                                                  # properties 끝
    },                                                      # parameters 끝
    run=_run,                                               # 실행 함수 연결
    readonly=True,                                          # 사용자 입력만 받는 읽기 전용 도구
    is_built_in=True,                                       # 내장 도구 표시
)
