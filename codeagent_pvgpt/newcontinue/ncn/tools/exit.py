# ncn/tools/exit.py — Exit 도구(헤드리스에서만 목록에 들어감). cn extensions/cli/src/tools/exit.ts 를 그대로 옮긴 것
# 실행하면 백그라운드 작업을 모두 죽이고 종료 코드 1로 끝낸다(cn gracefulExit(1)). updateAgentMetadata 는 --id(serve) 전용이라 P3에서
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # 종료
from typing import Callable, Optional                        # 타입

from ncn.tools.base import Tool                              # 공용 형식

EXIT_HANDLER: Optional[Callable[[int], None]] = None         # 루프가 끼워 넣는 종료 함수(없으면 sys.exit). 시험에서도 바꿔 끼움


def graceful_exit(code: int = 0) -> None:                   # 백그라운드 작업 정리 후 종료하는 함수
    """gracefulExit — 백그라운드 작업 정리 후 종료(텔레메트리·사용량 표시는 ncn에 없음)."""
    try:                                                    # 정리 중 오류는 아래에서 삼킨다
        from ncn.tools.background_jobs import background_job_service   # 작업 관리
        if background_job_service.get_running_job_count() > 0:   # 도는 작업이 있으면
            background_job_service.kill_all_jobs()           # 모두 죽임
    except Exception:                                        # 정리 오류는 무시(cn과 같음)
        pass                                                # 무시하고 계속 진행
    if EXIT_HANDLER is not None:                             # 루프가 정한 종료 방법
        EXIT_HANDLER(code)                                  # 핸들러로 종료를 위임
        return                                              # 핸들러가 돌아오면 sys.exit 하지 않고 끝냄
    sys.exit(code)                                           # process.exit(code)


async def _run(args: dict, context=None) -> str:            # run 훅: 종료 코드 1로 끝내는 도구 본문
    """run — 종료 코드 1로 끝냄. (종료 처리기가 돌아오면) 빈 글."""
    # updateAgentMetadata({isComplete: true}) — --id 가 있을 때만 하는 Hub 보고. ncn은 P3에서(실패해도 무시하는 단계라 결과 글에는 영향 없음)
    graceful_exit(1)                                         # gracefulExit(1)
    return ""                                                # cn: return ""


EXIT_TOOL = Tool(                                           # Exit 도구 정의(cn exit.ts 대응)
    name="Exit",                                            # 모델에 보이는 도구 이름
    display_name="Exit",                                    # 화면 표시 이름
    description="Exit the current process with status code 1, indicating a failure or error",  # 모델에게 주는 도구 설명 원문(cn 그대로)
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "properties": {},                                   # 받는 인자 없음
    },                                                       # required 없음(cn 원문 — 요청 본문에도 required 키가 빠짐)
    run=_run,                                               # 실행 함수 연결
    readonly=False,                                         # 쓰기 도구라 읽기 전용 아님
    is_built_in=True,                                       # 내장 도구 표시
)
