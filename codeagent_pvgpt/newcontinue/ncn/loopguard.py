# ncn/loopguard.py — [ncn E5] 한 턴의 반복 상한·같은 도구 반복 실패 감지. (cn 원본 없음 — cn에는 반복 상한이 없다, 계획 5-1·부록 A U3)
# 설정: <전역 폴더>/ncn.yaml `loop: {max_iterations: 50, repeat_failure_limit: 3}` (0 이면 그 검사를 끈다)
#   관리자 정책(policy.yaml, ncn/policy.py)에 max_iterations 가 있으면 그 값이 넘을 수 없는 상한(사용자가 "계속"을 골라도 멈춤)
# 걸리면: 대화형·ACP = 사용자에게 계속할지 묻기(AskQuestion 과 같은 질문 창구 quiz_service — 콘솔 고르기 / ACP elicitation·_ncn/ask·승인 형식)
#         헤드리스 = 턴을 끝내고 app.py 가 stderr 안내 + 종료 코드 EXIT_LOOP_GUARD(3)
# 요청 본문(도구 목록·설명·시스템 메시지)은 바꾸지 않는다 — 멈출지 말지만 정한다(차등 시험 유지). NCN_CN_COMPAT=1 이면 꺼진다.
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 인자 정규화(JSON)
import logging                                               # 로그(stdout 금지)
from typing import Any, Awaitable, Callable, Optional        # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치
from ncn.loop import events as ev                            # 이벤트(system_message 안내)

log = logging.getLogger("ncn.loopguard")                     # 로거

DEFAULT_MAX_ITERATIONS = 50                                  # loop.max_iterations 기본값(한 턴의 LLM 호출 수)
DEFAULT_REPEAT_FAILURE_LIMIT = 3                             # loop.repeat_failure_limit 기본값(같은 도구·같은 인자 연속 실패 수)
EXIT_LOOP_GUARD = 3                                          # 헤드리스에서 반복 상한으로 멈췄을 때의 종료 코드(0·1·2·130 과 겹치지 않게)
CHOICE_CONTINUE = "계속"                                      # 묻기 선택지 1(계속 진행)
CHOICE_STOP = "멈춤"                                          # 묻기 선택지 2(이번 턴 멈춤) — 기본 답(취소·Esc 도 멈춤)

REASON_MAX_ITERATIONS = "max_iterations"                     # 멈춘 이유: 사용자 설정 반복 상한
REASON_REPEAT_FAILURE = "repeat_failure"                     # 멈춘 이유: 같은 호출 연속 실패
REASON_POLICY = "policy_max_iterations"                      # 멈춘 이유: 관리자 정책 상한(묻지 않음)

QUESTION_SOURCE = "loop_guard"                               # quiz_service 질문 출처 표시(ACP 가 AskQuestion 카드와 구분)
LOOP_GUARD_CARD_TITLE = "ncn: 반복 상한 안내"                  # ACP 질문 카드 제목

AskContinue = Callable[[str], Awaitable[bool]]               # (질문 글) → 계속하면 참


def _non_neg_int(v: Any) -> bool:                            # 0 이상의 정수인지(참거짓 제외)
    """bool 이 아닌 int 이고 0 이상이면 참(0 = 끔)."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0   # YAML true 는 int 의 하위형이라 따로 뺀다


def parse_loop_settings(data: Any, out: Any) -> None:        # ncn.yaml 의 loop 절 → NcnSettings 필드(ncn/subagent.py 가 부름)
    """loop: {max_iterations, repeat_failure_limit} 를 out.loop_max_iterations·out.loop_repeat_failure_limit 에 넣는다.
    형식이 틀리면 그 값만 경고 후 기본값(시작은 계속)."""
    sec = data.get("loop") if isinstance(data, dict) else None   # loop 절
    if sec is None:                                          # 없으면
        return                                              # 기본값 그대로
    if not isinstance(sec, dict):                            # 사전이 아니면
        log.warning("ncn.yaml: loop must be a mapping — ignored")   # 경고
        return                                              # 기본값
    for key, attr in (("max_iterations", "loop_max_iterations"),   # 키 → 필드 이름 짝
                      ("repeat_failure_limit", "loop_repeat_failure_limit")):   # (이어짐)
        v = sec.get(key)                                     # 적힌 값
        if v is None:                                        # 안 적혔으면
            continue                                        # 기본값
        if _non_neg_int(v):                                  # 0 이상 정수면
            setattr(out, attr, v)                            # 반영
        else:                                                # 아니면
            log.warning("ncn.yaml: loop.%s must be a non-negative integer — ignored", key)   # 경고


def read_policy_value(key: str, global_dir: Any = None, environ: Any = None) -> Any:   # 관리자 정책 예약 키 값 하나(없으면 None)
    """ncn/policy.py(E1) 의 current_admin_policy_or_none(environ).reserved(key). 정책 파일이 없거나 깨졌으면 None
    (깨진 정책의 실행 거부는 권한 서비스가 이미 한다). 지연 import — 정책 모듈을 못 읽어도 E5·E6 은 돈다. global_dir 은 쓰지 않음(정책은 시스템 위치)."""
    try:                                                    # 모듈·읽기 실패는 정책 없음으로
        from ncn.policy import current_admin_policy_or_none  # 관리자 정책 읽기(E1)
        pol = current_admin_policy_or_none(environ)          # 정책(없거나 깨졌으면 None)
        return pol.reserved(key) if pol is not None else None   # 예약 키 원래 값
    except Exception as e:                                   # 모듈 없음 등
        log.debug("policy: cannot read %s — %s", key, e)     # 로그
        return None                                         # 정책 없음


class LoopGuard:                                            # 한 세션(대화)의 반복 감시기 — 턴마다 begin_turn 으로 초기화
    """루프가 LLM 을 부르기 직전마다 before_iteration 을 부른다(ncn/loop/agent.py). 이벤트는 observe 로 받는다.
    상한에 닿으면 ask_continue(대화형·ACP) 로 묻고, 없으면(헤드리스) 멈춘다. 멈추면 triggered 에 이유가 남는다."""

    def __init__(self, *, max_iterations: int = DEFAULT_MAX_ITERATIONS,   # 사용자 반복 상한(0 = 끔)
                 repeat_failure_limit: int = DEFAULT_REPEAT_FAILURE_LIMIT,   # 연속 실패 상한(0 = 끔)
                 policy_max_iterations: Optional[int] = None,   # 관리자 상한(None·0 = 없음)
                 ask_continue: Optional[AskContinue] = None,   # 묻기(없으면 묻지 않고 멈춤)
                 on_event: Optional[ev.EventSink] = None,   # 멈춤 안내를 보낼 화면 이벤트 수신자
                 on_decision: Optional[Callable[[str, str, str], None]] = None):   # (이유, 결정 continue|stop, 글) — 감사 기록용
        self.max_iterations = max_iterations                 # 사용자 상한
        self.repeat_failure_limit = repeat_failure_limit     # 연속 실패 상한
        self.policy_max_iterations = policy_max_iterations or None   # 관리자 상한(0 은 없음으로)
        self.ask_continue = ask_continue                     # 묻기 함수
        self.on_event = on_event                             # 화면 알림
        self.on_decision = on_decision                       # 감사 기록 알림
        self.begin_turn()                                    # 상태 초기화

    @property   # 읽기 전용 속성
    def enabled(self) -> bool:                               # 검사가 하나라도 켜져 있나
        """세 상한 중 하나라도 켜져 있으면 참."""
        return bool(self.max_iterations or self.repeat_failure_limit or self.policy_max_iterations)   # 하나라도 0 이 아니면

    def begin_turn(self) -> None:                            # 턴 시작 — 세는 값 초기화
        """턴마다 새로 센다(이전 턴의 반복·실패는 잇지 않음)."""
        self.total = 0                                       # 이번 턴의 LLM 호출 수(관리자 상한 기준)
        self.since_ask = 0                                   # 마지막 "계속" 이후 호출 수(사용자 상한 기준)
        self.calls: dict[Any, tuple[str, str]] = {}          # 호출 id → (도구 이름, 정규화 인자)
        self.fail_key: Optional[tuple[str, str]] = None      # 지금 이어지는 실패 호출
        self.fail_count = 0                                  # 그 호출의 연속 실패 수
        self.triggered: Optional[str] = None                 # 멈춘 이유(멈추지 않았으면 None)

    # ── 이벤트 관찰(ncn/loop/events.py) ──
    def observe(self, e: dict) -> None:                      # 루프 이벤트 하나 반영
        """tool_start 로 호출 인자를 기억, tool_error 는 실패로 세고, 실행 성공(tool_result done)은 연속을 끊는다.
        권한 거부(tool_result canceled)는 실패도 성공도 아니다(연속 유지)."""
        t = e.get("type")                                    # 이벤트 종류
        if t == ev.TOOL_START:                               # 도구 시작(사전 처리 실패 때 한 번 더 올 수 있음 — 덮어써도 같음)
            self.calls[e.get("tool_call_id")] = (str(e.get("name") or ""), normalize_args(e.get("arguments")))   # 이름·정규화 인자
        elif t == ev.TOOL_ERROR:                             # 사전 처리·실행·권한 확인 실패
            key = self.calls.get(e.get("tool_call_id")) or (str(e.get("name") or ""), "")   # 기억한 호출(없으면 이름만)
            if key == self.fail_key:                         # 같은 호출이 또 실패
                self.fail_count += 1                         # 연속 실패 +1
            else:                                            # 다른 호출의 실패
                self.fail_key, self.fail_count = key, 1      # 새로 센다
        elif t == ev.TOOL_RESULT and e.get("status") == "done":   # 실행 성공
            self.fail_key, self.fail_count = None, 0          # 연속이 끊김

    # ── 루프 훅 ──
    def _pending_reason(self) -> Optional[tuple[str, str, bool]]:   # 지금 멈춰야 하나(이유, 글, 묻지 않고 멈춤?)
        """관리자 상한 → 사용자 상한 → 연속 실패 순서로 본다."""
        if self.policy_max_iterations and self.total >= self.policy_max_iterations:   # 관리자 상한(넘을 수 없음)
            return (REASON_POLICY, f"ncn: 관리자 정책의 반복 상한({self.policy_max_iterations}회)에 닿아 이번 요청을 멈춥니다.", True)   # 묻지 않음
        if self.max_iterations and self.since_ask >= self.max_iterations:   # 사용자 상한
            return (REASON_MAX_ITERATIONS, f"ncn: 이번 요청에서 LLM 을 {self.since_ask}번 호출했습니다"   # 질문 글
                    f"(반복 상한 {self.max_iterations}회). 계속할까요?", False)   # 물을 수 있음
        if self.repeat_failure_limit and self.fail_key is not None and self.fail_count >= self.repeat_failure_limit:   # 연속 실패
            return (REASON_REPEAT_FAILURE, f"ncn: {self.fail_key[0]} 도구가 같은 인자로 {self.fail_count}번 연속 실패했습니다. 계속할까요?", False)   # 물을 수 있음
        return None                                         # 문제 없음

    async def before_iteration(self, *, headless: bool) -> bool:   # LLM 호출 직전 — 참이면 진행, 거짓이면 턴 멈춤
        """상한에 닿았으면 묻거나(대화형·ACP) 멈춘다(헤드리스·관리자 상한). 진행하면 호출 수를 센다."""
        while True:                                          # 묻고 "계속"이면 다른 이유가 남았는지 다시 본다
            pending = self._pending_reason()                 # 걸린 이유
            if pending is None:                              # 없으면
                break                                       # 진행
            reason, text, hard = pending                     # 이유·글·강제 여부
            go = False                                       # 기본은 멈춤
            if not hard and not headless and self.ask_continue is not None:   # 물을 수 있으면
                try:                                        # 묻기 실패는 멈춤으로
                    go = bool(await self.ask_continue(text))   # 사용자 선택
                except Exception as e:                       # 화면 오류
                    log.warning("loop guard: ask failed — %s", e)   # 기록
                    go = False                               # 멈춤
            self._decide(reason, "continue" if go else "stop", text)   # 감사 기록 알림
            if not go:                                       # 멈춤
                self.triggered = reason                      # 이유 남김(TurnResult.stopped_by_loop_guard)
                ev.safe_emit(self.on_event, ev.system_message(stop_message(reason)))   # 화면 안내(ACP 생각 조각·콘솔 흐린 글)
                return False                                # 턴 멈춤
            if reason == REASON_MAX_ITERATIONS:              # 반복 상한에서 계속
                self.since_ask = 0                           # 다시 max_iterations 만큼
            else:                                            # 연속 실패에서 계속
                self.fail_key, self.fail_count = None, 0      # 연속 초기화
        self.total += 1                                      # 이번 호출 센다(관리자 상한)
        self.since_ask += 1                                  # 이번 호출 센다(사용자 상한)
        return True                                         # 진행

    def _decide(self, reason: str, decision: str, text: str) -> None:   # 결정 알림(감사 기록)
        """on_decision 오류가 루프를 멈추지 않게 삼킨다."""
        if self.on_decision is None:                         # 받는 곳이 없으면
            return                                          # 끝
        try:                                                # 받는 쪽 오류 무시
            self.on_decision(reason, decision, text)         # 이유·결정·글
        except Exception:                                    # 감사 기록 오류
            log.exception("loop guard: on_decision failed")  # 로그만


def stop_message(reason: Optional[str]) -> str:              # 멈춤 안내 글(화면·stderr)
    """이유별 한 줄 안내(사람이 읽는 글 — LLM 요청에는 들어가지 않음)."""
    what = {REASON_MAX_ITERATIONS: "반복 상한(loop.max_iterations)",   # 사용자 상한
            REASON_REPEAT_FAILURE: "같은 도구·같은 인자 연속 실패(loop.repeat_failure_limit)",   # 연속 실패
            REASON_POLICY: "관리자 정책 반복 상한(policy.yaml max_iterations)"}.get(reason or "", "반복 감시")   # 관리자 상한
    return f"ncn: {what}에 걸려 이번 요청을 멈췄습니다. 이어서 하려면 다시 지시하세요(끄기: ~/.continue/ncn.yaml loop: 값 0)."   # 안내 글


def normalize_args(args: Any) -> str:                        # 인자 → 비교용 글(키 순서·공백 무관)
    """키 정렬·공백 없는 JSON. 직렬화할 수 없는 값은 글로 바꾼다."""
    try:                                                    # 이상한 값 대비
        return json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)   # 정규화 JSON
    except Exception:                                        # 정렬 불가(키 형식 섞임 등)
        return repr(args)                                   # 그래도 비교는 되게


async def quiz_ask_continue(text: str) -> bool:              # 대화형·ACP 공용 묻기(AskQuestion 과 같은 창구)
    """quiz_service 로 묻는다 — 콘솔은 고르기, ACP 는 elicitation·_ncn/ask·승인 형식(ncn/acp/server.py _answer_question).
    답할 화면이 없으면(듣는 함수 없음) 묻지 않고 멈춤. 취소·Esc 는 기본 답(멈춤)."""
    from ncn.tools.ask_question import quiz_service          # 질문 서비스(모듈 공용)
    if not quiz_service.on_question_requested:               # 답할 화면이 없으면
        return False                                        # 멈춤
    answer = await quiz_service.ask_question(text, [CHOICE_CONTINUE, CHOICE_STOP], CHOICE_STOP, source=QUESTION_SOURCE)   # 묻고 기다림(ACP 는 자기 카드로)
    return answer == CHOICE_CONTINUE                         # "계속"일 때만 진행


def make_loop_guard(settings: Any, *, global_dir: Any = None, environ: Any = None,   # 설정 → 감시기(꺼져 있으면 None)
                    ask_continue: Optional[AskContinue] = None, on_event: Optional[ev.EventSink] = None,   # 묻기·화면
                    on_decision: Optional[Callable[[str, str, str], None]] = None) -> Optional[LoopGuard]:   # 감사 기록
    """NcnSettings(loop_*) + 관리자 정책 max_iterations → LoopGuard. NCN_CN_COMPAT=1 이거나 전부 0 이면 None(= cn 과 같음)."""
    if cn_compat():                                          # cn 원래 동작
        return None                                         # 상한 없음
    pmax = read_policy_value("max_iterations", global_dir, environ)   # 관리자 상한
    if not _non_neg_int(pmax):                               # 정수가 아니면
        pmax = None                                         # 없음으로
    guard = LoopGuard(max_iterations=getattr(settings, "loop_max_iterations", DEFAULT_MAX_ITERATIONS),   # 사용자 상한
                      repeat_failure_limit=getattr(settings, "loop_repeat_failure_limit", DEFAULT_REPEAT_FAILURE_LIMIT),   # 연속 실패 상한
                      policy_max_iterations=pmax, ask_continue=ask_continue,   # 관리자 상한·묻기
                      on_event=on_event, on_decision=on_decision)   # 화면·감사
    return guard if guard.enabled else None                  # 전부 꺼졌으면 None
