# ncn/llm/retry.py — LLM 호출 재시도(지수 백오프). cn extensions/cli/src/util/exponentialBackoff.ts
# 최대 10회 재시도, 1초부터 ×1.6, 최대 30초, 지터 50~100%. 대상: 429/502/503/504·연결 오류·server_error 등
# 스트림 도중 오류가 나도 처음부터 다시 요청한다(withExponentialBackoff).
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 대기
import logging                                               # 재시도 경고(stdout 오염 금지 — 로거로만)
import math                                                  # floor
import random                                                # 지터
from dataclasses import dataclass                            # 옵션·표지
from typing import Any, AsyncIterator, Awaitable, Callable, Optional   # 타입

from ncn.llm.client import AbortSignal, ResponsesApiNotSupportedError, is_responses_model   # 중단 신호·응답 API 판정

log = logging.getLogger("ncn.llm.retry")                     # 로거


@dataclass
class BackoffOptions:                                       # 백오프 옵션 묶음
    """exponentialBackoff.ts ExponentialBackoffOptions + DEFAULT_OPTIONS."""
    max_retries: int = 10                                    # 최대 재시도 횟수(첫 시도 제외)
    initial_delay: int = 1000                                # 첫 대기(ms)
    max_delay: int = 30000                                   # 최대 대기(ms)
    backoff_multiplier: float = 1.6                          # 배수
    jitter: bool = True                                      # 지터 사용
    hidden_retries: int = 2                                  # 처음 몇 번은 경고를 숨김


@dataclass
class RetryMarker:                                          # 재시도 표지 자료형
    """[ncn] 재시도 직전에 흘려보내는 표지. 루프가 받으면 그 호출에서 받은 조각을 버린다(계획 부록 B).
    cn은 이 표지가 없어 앞 시도의 조각이 그대로 남아 중복된다."""
    attempt: int                                             # 몇 번째 시도가 실패했나(0부터)
    delay_ms: int                                            # 기다릴 시간
    error: BaseException                                     # 실패 원인


async def _sleep(seconds: float) -> None:                   # 비동기 대기(시험에서 교체 가능하도록 분리)
    """기다리기(시험에서 바꿔 끼울 수 있게 모듈 함수로)."""
    await asyncio.sleep(seconds)                            # 지정한 초만큼 양보하며 기다린다


def _attr(err: Any, name: str) -> Any:                      # 오류의 속성 읽기(JS의 error.code 흉내)
    """오류 객체의 속성(dict이면 키) — JS의 error.code / error.status 접근 흉내."""
    if err is None:                                         # 오류가 없으면
        return None                                         # 속성도 없다
    if isinstance(err, dict):                                # 시험용 평범한 객체
        return err.get(name)                                # 키로 읽는다
    return getattr(err, name, None)                          # 예외 객체


def _message(err: Any) -> str:                              # 오류의 message 글 뽑기
    """JS error.message(없으면 빈 글)."""
    if err is None or isinstance(err, str):                  # 문자열 오류에는 message가 없다
        return ""                                           # 빈 글
    if isinstance(err, dict):                               # dict이면
        return err.get("message") or ""                     # message 키(없으면 빈 글)
    m = getattr(err, "message", None)                        # LLMError.message
    if isinstance(m, str):                                  # 글이면
        return m                                            # 그대로
    if isinstance(err, BaseException):                       # 일반 예외는 str(e)
        return str(err)                                     # str(예외)
    return ""                                               # 그 밖은 빈 글


def is_network_error(err: Any) -> bool:                     # 네트워크 오류 판정
    """네트워크 오류 코드인가(ECONNRESET·ENOTFOUND·ETIMEDOUT·EPIPE·ECONNREFUSED)."""
    return _attr(err, "code") in ("ECONNRESET", "ENOTFOUND", "ETIMEDOUT", "EPIPE", "ECONNREFUSED")  # 오류 code가 네트워크 코드 중 하나인가


def is_retryable_http_status(status: Any) -> bool:          # 재시도 가능한 HTTP 코드 판정
    """재시도할 HTTP 코드인가(429·502·503·504)."""
    return status in (429, 502, 503, 504)                   # 429·502·503·504면 참


def is_server_error(err: Any) -> bool:                      # 서버 오류 type 판정
    """오류 type이 server_error / rate_limit_exceeded인가."""
    return _attr(err, "type") in ("server_error", "rate_limit_exceeded")  # type이 server_error·rate_limit_exceeded면 참


CONNECTION_ERROR_PATTERNS = ["premature close", "premature end", "connection reset", "socket hang up", "aborted", "overloaded"]   # 소문자 메시지에서 찾음


def is_connection_error(message: str) -> bool:              # 메시지가 연결 문제 패턴을 담는지 판정
    """메시지(소문자)가 연결 문제를 뜻하는가."""
    return any(p in message for p in CONNECTION_ERROR_PATTERNS)  # 패턴 하나라도 들어 있으면 참


CONTEXT_LENGTH_PATTERNS = [                                  # exponentialBackoff.ts:96-107 순서 그대로
    "input length and max_tokens exceed context limit",      # Anthropic
    "decrease input length or max_tokens",
    "maximum context length",                                # OpenAI
    "reduce the length of the messages",
    "tokens in the prompt exceeds",                          # Mistral
    "use a shorter prompt",
    "context_length_exceeded",                               # 일반
]


def is_context_length_error(err: Any) -> bool:              # 컨텍스트 길이 초과 판정
    """컨텍스트 길이 초과 오류인가(재시도하지 않음). JS는 Error 인스턴스만 본다 → 예외 객체만."""
    if not isinstance(err, BaseException):                  # 예외 인스턴스가 아니면(JS의 instanceof Error)
        return False                                        # 해당 없음
    m = _message(err).lower()                                # 소문자
    if "invalid_request_error" in m and "context" in m:      # 요청 오류 + context
        return True                                         # 컨텍스트 초과
    return any(p in m for p in CONTEXT_LENGTH_PATTERNS)     # 패턴 중 하나라도 있으면 컨텍스트 초과


def is_retryable_error(err: Any) -> bool:                   # 재시도할 오류인지 판정(cn과 같은 순서)
    """exponentialBackoff.ts isRetryableError — 판정 순서 그대로."""
    if is_context_length_error(err):                         # 컨텍스트 초과는 절대 재시도 안 함
        return False                                        # 재시도 금지
    if is_network_error(err):                                # 네트워크 코드
        return True                                         # 재시도
    status = _attr(err, "status")                            # HTTP 코드
    if status and is_retryable_http_status(status):         # 재시도 대상 상태면
        return True                                         # 재시도
    if is_server_error(err):                                 # 서버 오류 type
        return True                                         # 재시도
    m = _message(err).lower()                                # 메시지 패턴
    if m and is_connection_error(m):                        # 연결 오류 패턴이 맞으면
        return True                                         # 재시도
    return False                                            # 어디에도 해당 없으면 재시도 안 함


def calculate_delay(attempt: int, opts: BackoffOptions, rand: Callable[[], float] = random.random) -> float:  # 시도 횟수에 따른 대기 시간 계산
    """다음 대기(ms): initial × multiplier^attempt, 최대 max_delay, 지터면 floor(× (0.5~1.0))."""
    base = opts.initial_delay * math.pow(opts.backoff_multiplier, attempt)   # 지수
    capped = min(base, opts.max_delay)                       # 상한
    if opts.jitter:                                          # 지터: 50%~100%
        return math.floor(capped * (0.5 + rand() * 0.5))    # 50~100% 범위로 줄여 내림
    return capped                                           # 지터 없으면 그대로


def chat_completion_stream_with_backoff(llm: Any, messages: list[dict], tools: Optional[list[dict]],  # 재시도 감싼 스트림 생성(cn의 생성기 만들기에 대응)
                                        signal: AbortSignal) -> AsyncIterator[dict]:
    """exponentialBackoff.ts chatCompletionStreamWithBackoff.
    cn은 여기서 생성기만 만들기 때문에 이 층의 재시도는 사실상 일어나지 않는다(오류는 순회 중에 나서 바깥 층이 처리).
    모델 이름이 gpt-5*/o[0-9]*이면 cn은 apiBase와 상관없이 /responses로 간다 → ncn은 미지원 오류."""
    if signal.aborted:                                       # 요청 전 중단 확인
        raise RuntimeError("Request aborted")               # 요청 보내지 않고 오류
    model = getattr(llm, "model", "") or ""                 # LLM 모델 이름
    if is_responses_model(model):                           # /responses API를 쓰는 모델이면
        raise ResponsesApiNotSupportedError(                # ncn 미지원 오류로 알린다
            f'Model "{model}" would use the OpenAI Responses API (/responses), which ncn does not support')
    return llm.stream_chat(messages, tools, signal=signal)   # 생성기(아직 요청 안 보냄)


async def with_exponential_backoff(factory: Callable[[AbortSignal], Awaitable[AsyncIterator[Any]] | AsyncIterator[Any]],  # 생성기 전체를 재시도하는 비동기 생성기
                                   signal: AbortSignal, options: Optional[BackoffOptions] = None,
                                   *, rand: Callable[[], float] = random.random) -> AsyncIterator[Any]:
    """exponentialBackoff.ts withExponentialBackoff — 생성기 전체를 재시도한다.
    재시도 직전에 RetryMarker를 흘려보낸다(받는 쪽이 무시하면 cn과 같은 동작)."""
    opts = options or BackoffOptions()                       # 옵션
    last_error: Optional[BaseException] = None               # 마지막 오류
    for attempt in range(opts.max_retries + 1):             # 첫 시도 + 재시도 횟수만큼 반복
        try:                                                # 한 번의 시도를 감싼다
            if signal.aborted:                               # 만들기 전 중단 확인
                raise RuntimeError("Request aborted")       # 중단 오류
            gen = factory(signal)                            # 생성기 만들기(cn은 시도마다 새 AbortController를 원 신호에 연결 — 같은 효과)
            if asyncio.iscoroutine(gen) or isinstance(gen, asyncio.Future):   # async 함수가 생성기를 돌려주는 경우
                gen = await gen                             # 생성기 얻기
            async for chunk in gen:                          # 조각 순회
                if signal.aborted:                           # 순회 중 중단
                    raise RuntimeError("Request aborted")   # 중단 오류
                yield chunk                                 # 조각을 호출자에게 넘긴다
            return                                           # 성공
        except Exception as err:                             # 실패
            last_error = err                                # 오류 보관
            if signal.aborted:                               # 중단이면 재시도하지 않음
                raise                                       # 그대로 올린다
            if attempt == opts.max_retries:                  # 마지막 시도였으면 끝
                break                                       # 반복 종료
            if not is_retryable_error(err):                  # 재시도 대상이 아니면 그대로
                raise                                       # 그대로 올린다
            delay = calculate_delay(attempt, opts, rand)     # 대기 시간
            if attempt >= opts.hidden_retries:               # 처음 hiddenRetries번은 조용히
                log.warning("Retrying request (%d/%d). Error: %s", attempt + 1 - opts.hidden_retries,  # 재시도 경고 로그
                            opts.max_retries + 1 - opts.hidden_retries, _message(err))
            yield RetryMarker(attempt=attempt, delay_ms=int(delay), error=err)   # [ncn] 표지
            await _sleep(delay / 1000)                       # 대기
    log.error("Failed after %d attempts. Last error: %s", opts.max_retries + 1, _message(last_error))  # 재시도를 다 썼다는 오류 로그
    assert last_error is not None                            # 여기 오면 반드시 오류가 있다
    raise last_error                                        # 마지막 오류를 올린다
