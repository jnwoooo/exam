# tests/test_llm_client.py — LLM 클라이언트·재시도 시험(cn util/exponentialBackoff.test.ts 사례 + 녹화형 가짜 LLM으로 실제 HTTP 확인)
import asyncio                                               # 비동기 실행
import json                                                  # 본문 비교

import pytest                                                # 시험 도구

import ncn.llm.client as client_mod                          # 대기 함수 바꿔 끼우기용
import ncn.llm.retry as retry_mod                            # 대기 함수 바꿔 끼우기용
from ncn.llm.client import (OFFICIAL_API_BASE, AbortSignal, LLMClient, LLMError, build_chat_url, js_number,
                            js_parse, js_stringify, modify_chat_body)   # 시험 대상
from ncn.llm.retry import (BackoffOptions, RetryMarker, calculate_delay, is_retryable_error,
                           with_exponential_backoff)         # 시험 대상
from parity.fake_llm import serve                            # 녹화형 가짜 LLM


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """재시도 대기 없애기."""
    async def fast(_s):                                      # 즉시 돌아옴
        return None
    monkeypatch.setattr(client_mod, "_sleep", fast)
    monkeypatch.setattr(retry_mod, "_sleep", fast)


def run(coro):
    """코루틴 실행."""
    return asyncio.run(coro)


async def collect(client: LLMClient, messages, tools):
    """스트림 조각 전부 모으기."""
    out = []
    async for c in client.stream_chat(messages, tools):
        out.append(c)
    await client.aclose()
    return out


# ── JS 호환 JSON ──

def test_js_number_matches_javascript_tostring():
    assert js_number(1.0) == "1"                             # 정수값 실수
    assert js_number(0.1) == "0.1"
    assert js_number(1.5e-7) == "1.5e-7"                     # JS는 e-7(파이썬 repr은 e-07)
    assert js_number(0.00001) == "0.00001"                   # JS는 -6까지 소수 표기
    assert js_number(1e21) == "1e+21"                        # 21자리부터 지수
    assert js_number(123456789012345680000.0) == "123456789012345680000"
    assert js_number(12345678901234567890) == "12345678901234567000"   # 2^53 초과 정수는 배정밀도로
    assert js_number(-0.0) == "0"
    assert js_number(float("nan")) == "null"


def test_js_stringify_reorders_integer_keys_and_drops_undefined():
    assert js_stringify(js_parse('{"b":1,"2":2,"1":3,"a":1.0}')) == '{"1":3,"2":2,"b":1,"a":1}'   # JS 객체 키 순서
    assert js_stringify({"a": client_mod.UNDEFINED, "b": [client_mod.UNDEFINED]}) == '{"b":[null]}'   # undefined 규칙
    assert js_stringify({"k": "한글\n\"q\""}) == '{"k":"한글\\n\\"q\\""}'   # 한글은 그대로, 제어문자는 이스케이프
    with pytest.raises(ValueError):                          # JSON.parse는 NaN을 받지 않는다
        js_parse("NaN")


# ── 본문·URL ──

def test_build_chat_url_joins_like_openai_sdk():
    assert build_chat_url("http://h/v1/") == "http://h/v1/chat/completions"   # 끝 / 한 번만
    assert build_chat_url("http://h/v1") == "http://h/v1/chat/completions"
    assert build_chat_url("http://localhost:8000/v1/") == "http://127.0.0.1:8000/v1/chat/completions"   # localhost 치환


def test_modify_chat_body_official_and_deepseek():
    b = modify_chat_body({"model": "o1", "messages": [{"role": "system", "content": "s"}], "stream": True,
                          "tools": [{"x": 1}], "max_tokens": 10}, OFFICIAL_API_BASE)
    assert json.loads(js_stringify(b)) == {"model": "o1", "messages": [{"role": "developer", "content": "s"}], "stream": True,
                                           "tools": [{"x": 1}], "stream_options": {"include_usage": True},
                                           "max_completion_tokens": 10, "parallel_tool_calls": False}
    d = modify_chat_body({"model": "deepseek-reasoner", "messages": [], "stream": True, "max_tokens": 5}, "http://x/v1/")
    assert list(json.loads(js_stringify(d))) == ["model", "messages", "stream", "stream_options", "max_completion_tokens"]
    o = modify_chat_body({"model": "o1", "messages": [], "stream": True, "tools": []}, "http://local/v1/")
    assert "parallel_tool_calls" not in o and "max_completion_tokens" not in o   # 공식 API가 아니면 그대로


def test_request_body_headers_and_stream_over_http():
    server, rec = serve([{"content": "안녕하세요 반갑습니다", "usage": {"prompt_tokens": 3, "completion_tokens": 4}}])
    try:
        c = LLMClient(api_base=f"http://localhost:{server.server_port}/v1/", api_key="sk-1", model="fake-model",
                      default_completion_options={"maxTokens": 100, "temperature": 0.7},
                      request_options={"headers": {"X-Org": "a"}, "extraBodyProperties": {"top_k": 5}})
        chunks = run(collect(c, [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], []))
    finally:
        server.shutdown()
    req = rec.requests[0]                                    # 녹화된 요청
    assert req["path"] == "/v1/chat/completions"
    assert list(req["body"]) == ["model", "messages", "stream", "tools", "max_tokens", "temperature", "stream_options", "top_k"]
    assert req["body"]["tools"] == [] and req["body"]["stream_options"] == {"include_usage": True}
    h = {k.lower(): v for k, v in req["headers"].items()}
    assert h["authorization"] == "Bearer sk-1" and h["x-org"] == "a" and h["user-agent"] == "OpenAI/JS 5.23.2"
    text = "".join((ch["choices"][0]["delta"].get("content") or "") for ch in chunks if ch.get("choices"))
    assert text == "안녕하세요 반갑습니다"
    assert chunks[-1]["usage"]["prompt_tokens"] == 3         # 사용량 조각은 맨 끝


def test_custom_authorization_header_replaces_default():
    server, rec = serve([{"content": "ok"}])
    try:
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1", api_key="sk-1", model="m",
                      request_options={"headers": {"Authorization": "Token abc"}})
        run(collect(c, [], None))
    finally:
        server.shutdown()
    h = {k.lower(): v for k, v in rec.requests[0]["headers"].items()}
    assert h["authorization"] == "Token abc"
    assert "tools" not in rec.requests[0]["body"]            # tools=None(undefined)이면 키가 없다
    assert "max_tokens" not in rec.requests[0]["body"]       # defaultCompletionOptions 없음 → 옵션 키 없음


def test_status_error_message_and_sdk_retries():
    server, rec = serve([{"status": 400, "content": "bad thing"}])
    try:
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="m")
        with pytest.raises(LLMError) as ei:
            run(collect(c, [], []))
    finally:
        server.shutdown()
    assert str(ei.value) == "400 bad thing" and ei.value.status == 400   # SDK APIError 글
    assert len(rec.requests) == 1                            # 400은 SDK도 재시도 안 함

    server, rec = serve([{"status": 503}, {"status": 503}, {"content": "ok"}])   # SDK가 2번 재시도
    try:
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="m")
        chunks = run(collect(c, [], []))
    finally:
        server.shutdown()
    assert len(rec.requests) == 3 and chunks
    retry_counts = [{k.lower(): v for k, v in r["headers"].items()}["x-stainless-retry-count"] for r in rec.requests]
    assert retry_counts == ["0", "1", "2"]


def test_connection_error_message():
    c = LLMClient(api_base="http://127.0.0.1:1/v1/", api_key="k", model="m", sdk_max_retries=0)   # 닫힌 포트
    with pytest.raises(LLMError) as ei:
        run(collect(c, [], []))
    assert str(ei.value) == "Connection error."
    assert is_retryable_error(ei.value) is False             # cn: SDK가 감싼 연결 오류는 CLI 층에서 재시도 안 함


# ── 재시도(exponentialBackoff.test.ts) ──

@pytest.mark.parametrize("err,expected", [
    ({"code": "ECONNRESET"}, True), ({"code": "ENOTFOUND"}, True), ({"code": "ETIMEDOUT"}, True),
    ({"status": 429}, True), ({"status": 502}, True), ({"status": 503}, True), ({"status": 504}, True),
    ({"type": "server_error"}, True), ({"type": "rate_limit_exceeded"}, True), ({"code": "EPIPE"}, True),
    ({"code": "ECONNREFUSED"}, True), ({"message": "The service is overloaded"}, True),
    ({"message": "premature close"}, True), ({"message": "Request aborted"}, True), ({"message": "PREMATURE CLOSE"}, True),
    ({"status": 400}, False), ({"status": 401}, False), ({"status": 403}, False), ({"status": 404}, False),
    ({"type": "client_error"}, False), ({"message": "Unknown error"}, False), ({}, False), (None, False),
    ("Error message", False), ({"code": "INVALID_REQUEST", "status": 429}, True), ({"status": 429, "type": "client_error"}, True),
])
def test_is_retryable_error(err, expected):
    assert is_retryable_error(err) is expected


def test_context_length_error_is_not_retryable():
    assert is_retryable_error(LLMError("400 This model's maximum context length is 8192 tokens", status=503)) is False


def test_calculate_delay():
    o = BackoffOptions(jitter=False)                         # 지터 없이
    assert [calculate_delay(i, o) for i in range(3)] == pytest.approx([1000, 1600, 2560])   # JS도 2560.0000000000005
    assert calculate_delay(20, o) == 30000                   # 상한
    j = BackoffOptions()
    assert calculate_delay(0, j, rand=lambda: 0.0) == 500    # 최소 50%
    assert calculate_delay(0, j, rand=lambda: 0.999999) == 999   # floor
    assert calculate_delay(10, j, rand=lambda: 0.0) == 15000  # 상한 뒤 지터


def test_with_exponential_backoff_retries_whole_stream():
    attempts = []                                            # 시도 기록

    async def factory(_sig):
        attempts.append(1)
        async def gen():
            yield "a"
            if len(attempts) < 3:
                raise LLMError("x", status=503)              # 재시도 대상
            yield "b"
        return gen()

    async def main():
        out = []
        async for c in with_exponential_backoff(factory, AbortSignal()):
            out.append("R" if isinstance(c, RetryMarker) else c)
        return out
    assert run(main()) == ["a", "R", "a", "R", "a", "b"]     # 처음부터 다시(표지를 무시하면 cn처럼 중복)

    async def bad(_sig):
        async def gen():
            raise LLMError("400 nope", status=400)
            yield                                            # 생성기로 만들기
        return gen()

    async def main2():
        async for _ in with_exponential_backoff(bad, AbortSignal()):
            pass
    with pytest.raises(LLMError):                            # 재시도 대상 아님 → 바로
        run(main2())


def test_responses_model_is_unsupported():
    from ncn.llm.client import ResponsesApiNotSupportedError
    from ncn.llm.retry import chat_completion_stream_with_backoff
    c = LLMClient(api_base="http://127.0.0.1:1/v1/", api_key="k", model="gpt-5-mini")
    with pytest.raises(ResponsesApiNotSupportedError):
        chat_completion_stream_with_backoff(c, [], [], AbortSignal())
