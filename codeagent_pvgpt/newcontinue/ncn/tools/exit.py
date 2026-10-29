# ncn/tools/exit.py — Exit 도구(헤드리스에서만 목록에 들어감). cn extensions/cli/src/tools/exit.ts 를 그대로 옮긴 것
# 실행하면 백그라운드 작업을 모두 죽이고 종료 코드 1로 끝낸다(cn gracefulExit(1)). updateAgentMetadata 는 --id(serve) 전용이라 P3에서
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # 종료
from typing import Callable, Optional                        # 타입

from ncn.tools.base import Tool                              # 공용 형식

EXIT_HANDLER: Optional[Callable[[int], None]] = None         # 루프가 끼워 넣는 종료 함수(없으면 sys.exit). 시험에서도 바꿔 끼움


def graceful_exit(code: int = 0) -> None:
    """gracefulExit — 백그라운드 작업 정리 후 종료(텔레메트리·사용량 표시는 ncn에 없음)."""
    try:
        from ncn.tools.background_jobs import background_job_service   # 작업 관리
        if background_job_service.get_running_job_count() > 0:   # 도는 작업이 있으면
            background_job_service.kill_all_jobs()           # 모두 죽임
    except Exception:                                        # 정리 오류는 무시(cn과 같음)
        pass
    if EXIT_HANDLER is not None:                             # 루프가 정한 종료 방법
        EXIT_HANDLER(code)
        return
    sys.exit(code)                                           # process.exit(code)


async def _run(args: dict, context=None) -> str:
    """run — 종료 코드 1로 끝냄. (종료 처리기가 돌아오면) 빈 글."""
    # updateAgentMetadata({isComplete: true}) — --id 가 있을 때만 하는 Hub 보고. ncn은 P3에서(실패해도 무시하는 단계라 결과 글에는 영향 없음)
    graceful_exit(1)                                         # gracefulExit(1)
    return ""                                                # cn: return ""


EXIT_TOOL = Tool(
    name="Exit",
    display_name="Exit",
    description="Exit the current process with status code 1, indicating a failure or error",
    parameters={
        "type": "object",
        "properties": {},
    },                                                       # required 없음(cn 원문 — 요청 본문에도 required 키가 빠짐)
    run=_run,
    readonly=False,
    is_built_in=True,
)
