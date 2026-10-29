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
class BackoffOptions:
    """exponentialBackoff.ts ExponentialBackoffOptions + DEFAULT_OPTIONS."""
    max_retries: int = 10                                    # 최대 재시도 횟수(첫 시도 제외)
    initial_delay: int = 1000                                # 첫 대기(ms)
    max_delay: int = 30000                                   # 최대 대기(ms)
    backoff_multiplier: float = 1.6                          # 배수
    jitter: bool = True                                      # 지터 사용
    hidden_retries: int = 2                                  # 처음 몇 번은 경고를 숨김


@dataclass
class RetryMarker:
    """[ncn] 재시도 직전에 흘려보내는 표지. 루프가 받으면 그 호출에서 받은 조각을 버린다(계획 부록 B).
    cn은 이 표지가 없어 앞 시도의 조각이 그대로 남아 중복된다."""
    attempt: int                                             # 몇 번째 시도가 실패했나(0부터)
    delay_ms: int                                            # 기다릴 시간
    error: BaseException                                     # 실패 원인


async def _sleep(seconds: float) -> None:
    """기다리기(시험에서 바꿔 끼울 수 있게 모듈 함수로)."""
    await asyncio.sleep(seconds)


def _attr(err: Any, name: str) -> Any:
    """오류 객체의 속성(dict이면 키) — JS의 error.code / error.status 접근 흉내."""
    if err is None:                                          # null/undefined
        return None
    if isinstance(err, dict):                                # 시험용 평범한 객체
        return err.get(name)
    return getattr(err, name, None)                          # 예외 객체


def _message(err: Any) -> str:
    """JS error.message(없으면 빈 글)."""
    if err is None or isinstance(err, str):                  # 문자열 오류에는 message가 없다
        return ""
    if isinstance(err, dict):
        return err.get("message") or ""
    m = getattr(err, "message", None)                        # LLMError.message
    if isinstance(m, str):
        return m
    if isinstance(err, BaseException):                       # 일반 예외는 str(e)
        return str(err)
    return ""


def is_network_error(err: Any) -> bool:
    """네트워크 오류 코드인가(ECONNRESET·ENOTFOUND·ETIMEDOUT·EPIPE·ECONNREFUSED)."""
    return _attr(err, "code") in ("ECONNRESET", "ENOTFOUND", "ETIMEDOUT", "EPIPE", "ECONNREFUSED")


def is_retryable_http_status(status: Any) -> bool:
    """재시도할 HTTP 코드인가(429·502·503·504)."""
    return status in (429, 502, 503, 504)


def is_server_error(err: Any) -> bool:
    """오류 type이 server_error / rate_limit_exceeded인가."""
    return _attr(err, "type") in ("server_error", "rate_limit_exceeded")


CONNECTION_ERROR_PATTERNS = ["premature close", "premature end", "connection reset", "socket hang up", "aborted", "overloaded"]   # 소문자 메시지에서 찾음


def is_connection_error(message: str) -> bool:
    """메시지(소문자)가 연결 문제를 뜻하는가."""
    return any(p in message for p in CONNECTION_ERROR_PATTERNS)


CONTEXT_LENGTH_PATTERNS = [                                  # exponentialBackoff.ts:96-107 순서 그대로
    "input length and max_tokens exceed context limit",      # Anthropic
    "decrease input length or max_tokens",
    "maximum context length",                                # OpenAI
    "reduce the length of the messages",
    "tokens in the prompt exceeds",                          # Mistral
    "use a shorter prompt",
    "context_length_exceeded",                               # 일반
]


def is_context_length_error(err: Any) -> bool:
    """컨텍스트 길이 초과 오류인가(재시도하지 않음). JS는 Error 인스턴스만 본다 → 예외 객체만."""
    if not isinstance(err, BaseException):                   # error instanceof Error
        return False
    m = _message(err).lower()                                # 소문자
    if "invalid_request_error" in m and "context" in m:      # 요청 오류 + context
        return True
    return any(p in m for p in CONTEXT_LENGTH_PATTERNS)


def is_retryable_error(err: Any) -> bool:
    """exponentialBackoff.ts isRetryableError — 판정 순서 그대로."""
    if is_context_length_error(err):                         # 컨텍스트 초과는 절대 재시도 안 함
        return False
    if is_network_error(err):                                # 네트워크 코드
        return True
    status = _attr(err, "status")                            # HTTP 코드
    if status and is_retryable_http_status(status):
        return True
    if is_server_error(err):                                 # 서버 오류 type
        return True
    m = _message(err).lower()                                # 메시지 패턴
    if m and is_connection_error(m):
        return True
    return False


def calculate_delay(attempt: int, opts: BackoffOptions, rand: Callable[[], float] = random.random) -> float:
    """다음 대기(ms): initial × multiplier^attempt, 최대 max_delay, 지터면 floor(× (0.5~1.0))."""
    base = opts.initial_delay * math.pow(opts.backoff_multiplier, attempt)   # 지수
    capped = min(base, opts.max_delay)                       # 상한
    if opts.jitter:                                          # 지터: 50%~100%
        return math.floor(capped * (0.5 + rand() * 0.5))
    return capped


def chat_completion_stream_with_backoff(llm: Any, messages: list[dict], tools: Optional[list[dict]],
                                        signal: AbortSignal) -> AsyncIterator[dict]:
    """exponentialBackoff.ts chatCompletionStreamWithBackoff.
    cn은 여기서 생성기만 만들기 때문에 이 층의 재시도는 사실상 일어나지 않는다(오류는 순회 중에 나서 바깥 층이 처리).
    모델 이름이 gpt-5*/o[0-9]*이면 cn은 apiBase와 상관없이 /responses로 간다 → ncn은 미지원 오류."""
    if signal.aborted:                                       # 요청 전 중단 확인
        raise RuntimeError("Request aborted")
    model = getattr(llm, "model", "") or ""                  # params.model
    if is_responses_model(model):                            # isResponsesModel(params.model)
        raise ResponsesApiNotSupportedError(
            f'Model "{model}" would use the OpenAI Responses API (/responses), which ncn does not support')
    return llm.stream_chat(messages, tools, signal=signal)   # 생성기(아직 요청 안 보냄)


async def with_exponential_backoff(factory: Callable[[AbortSignal], Awaitable[AsyncIterator[Any]] | AsyncIterator[Any]],
                                   signal: AbortSignal, options: Optional[BackoffOptions] = None,
                                   *, rand: Callable[[], float] = random.random) -> AsyncIterator[Any]:
    """exponentialBackoff.ts withExponentialBackoff — 생성기 전체를 재시도한다.
    재시도 직전에 RetryMarker를 흘려보낸다(받는 쪽이 무시하면 cn과 같은 동작)."""
    opts = options or BackoffOptions()                       # 옵션
    last_error: Optional[BaseException] = None               # 마지막 오류
    for attempt in range(opts.max_retries + 1):              # 0..maxRetries
        try:
            if signal.aborted:                               # 만들기 전 중단 확인
                raise RuntimeError("Request aborted")
            gen = factory(signal)                            # 생성기 만들기(cn은 시도마다 새 AbortController를 원 신호에 연결 — 같은 효과)
            if asyncio.iscoroutine(gen) or isinstance(gen, asyncio.Future):   # async 함수가 생성기를 돌려주는 경우
                gen = await gen
            async for chunk in gen:                          # 조각 순회
                if signal.aborted:                           # 순회 중 중단
                    raise RuntimeError("Request aborted")
                yield chunk
            return                                           # 성공
        except Exception as err:                             # 실패
            last_error = err
            if signal.aborted:                               # 중단이면 재시도하지 않음
                raise
            if attempt == opts.max_retries:                  # 마지막 시도였으면 끝
                break
            if not is_retryable_error(err):                  # 재시도 대상이 아니면 그대로
                raise
            delay = calculate_delay(attempt, opts, rand)     # 대기 시간
            if attempt >= opts.hidden_retries:               # 처음 hiddenRetries번은 조용히
                log.warning("Retrying request (%d/%d). Error: %s", attempt + 1 - opts.hidden_retries,
                            opts.max_retries + 1 - opts.hidden_retries, _message(err))
            yield RetryMarker(attempt=attempt, delay_ms=int(delay), error=err)   # [ncn] 표지
            await _sleep(delay / 1000)                       # 대기
    log.error("Failed after %d attempts. Last error: %s", opts.max_retries + 1, _message(last_error))
    assert last_error is not None                            # 여기 오면 반드시 오류가 있다
    raise last_error
