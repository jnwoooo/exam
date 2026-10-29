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


@pytest.fixture(autouse=True)                               # 모든 시험에 자동 적용되는 픽스처
def no_sleep(monkeypatch):                                  # 재시도 대기를 없애는 픽스처
    """재시도 대기 없애기."""
    async def fast(_s):                                      # 즉시 돌아옴
        return None                                         # 아무것도 안 한다
    monkeypatch.setattr(client_mod, "_sleep", fast)         # SDK 층 대기를 대체
    monkeypatch.setattr(retry_mod, "_sleep", fast)          # CLI 층 대기를 대체


def run(coro):                                              # 코루틴을 동기로 실행하는 도우미
    """코루틴 실행."""
    return asyncio.run(coro)                                # 새 이벤트 루프에서 실행


async def collect(client: LLMClient, messages, tools):      # 스트림 조각을 모두 모으는 도우미
    """스트림 조각 전부 모으기."""
    out = []                                                # 모은 조각 목록
    async for c in client.stream_chat(messages, tools):     # 조각을 하나씩 받아
        out.append(c)                                       # 목록에 추가
    await client.aclose()                                   # 클라이언트 연결 정리
    return out                                              # 모은 조각 반환


# ── JS 호환 JSON ──

def test_js_number_matches_javascript_tostring():           # JS 숫자 표기 시험
    assert js_number(1.0) == "1"                             # 정수값 실수
    assert js_number(0.1) == "0.1"                          # 단순 소수
    assert js_number(1.5e-7) == "1.5e-7"                     # JS는 e-7(파이썬 repr은 e-07)
    assert js_number(0.00001) == "0.00001"                   # JS는 -6까지 소수 표기
    assert js_number(1e21) == "1e+21"                        # 21자리부터 지수
    assert js_number(123456789012345680000.0) == "123456789012345680000"  # 큰 실수의 정수 표기
    assert js_number(12345678901234567890) == "12345678901234567000"   # 2^53 초과 정수는 배정밀도로
    assert js_number(-0.0) == "0"                           # -0은 "0"
    assert js_number(float("nan")) == "null"                # NaN은 null


def test_js_stringify_reorders_integer_keys_and_drops_undefined():  # JS 키 순서·undefined 규칙 시험
    assert js_stringify(js_parse('{"b":1,"2":2,"1":3,"a":1.0}')) == '{"1":3,"2":2,"b":1,"a":1}'   # JS 객체 키 순서
    assert js_stringify({"a": client_mod.UNDEFINED, "b": [client_mod.UNDEFINED]}) == '{"b":[null]}'   # undefined 규칙
    assert js_stringify({"k": "한글\n\"q\""}) == '{"k":"한글\\n\\"q\\""}'   # 한글은 그대로, 제어문자는 이스케이프
    with pytest.raises(ValueError):                          # JSON.parse는 NaN을 받지 않는다
        js_parse("NaN")                                     # js_parse가 ValueError를 던진다


# ── 본문·URL ──

def test_build_chat_url_joins_like_openai_sdk():            # 주소 조립 시험
    assert build_chat_url("http://h/v1/") == "http://h/v1/chat/completions"   # 끝 / 한 번만
    assert build_chat_url("http://h/v1") == "http://h/v1/chat/completions"  # 슬래시가 없어도 같은 결과
    assert build_chat_url("http://localhost:8000/v1/") == "http://127.0.0.1:8000/v1/chat/completions"   # localhost 치환


def test_modify_chat_body_official_and_deepseek():          # 본문 보정 시험(공식 API·DeepSeek)
    b = modify_chat_body({"model": "o1", "messages": [{"role": "system", "content": "s"}], "stream": True,  # o1 모델 본문을 공식 API 기준으로 보정
                          "tools": [{"x": 1}], "max_tokens": 10}, OFFICIAL_API_BASE)  # 도구·max_tokens 포함
    assert json.loads(js_stringify(b)) == {"model": "o1", "messages": [{"role": "developer", "content": "s"}], "stream": True,  # developer 역할·max_completion_tokens 변환 확인
                                           "tools": [{"x": 1}], "stream_options": {"include_usage": True},  # 도구와 스트림 옵션 유지
                                           "max_completion_tokens": 10, "parallel_tool_calls": False}  # 병렬 도구 호출 꺼짐
    d = modify_chat_body({"model": "deepseek-reasoner", "messages": [], "stream": True, "max_tokens": 5}, "http://x/v1/")  # DeepSeek reasoner는 max_tokens가 옮겨진다
    assert list(json.loads(js_stringify(d))) == ["model", "messages", "stream", "stream_options", "max_completion_tokens"]  # 키 순서 확인
    o = modify_chat_body({"model": "o1", "messages": [], "stream": True, "tools": []}, "http://local/v1/")  # 공식 API가 아닌 주소로 보정
    assert "parallel_tool_calls" not in o and "max_completion_tokens" not in o   # 공식 API가 아니면 그대로


def test_request_body_headers_and_stream_over_http():       # 가짜 서버로 요청 본문·헤더·스트림 확인
    server, rec = serve([{"content": "안녕하세요 반갑습니다", "usage": {"prompt_tokens": 3, "completion_tokens": 4}}])  # 가짜 LLM 서버 시작
    try:                                                    # 서버를 반드시 종료하려 try로 감싼다
        c = LLMClient(api_base=f"http://localhost:{server.server_port}/v1/", api_key="sk-1", model="fake-model",  # 클라이언트 생성(localhost 주소)
                      default_completion_options={"maxTokens": 100, "temperature": 0.7},  # 기본 완성 옵션
                      request_options={"headers": {"X-Org": "a"}, "extraBodyProperties": {"top_k": 5}})  # 커스텀 헤더·추가 본문 속성
        chunks = run(collect(c, [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], []))  # 한 번 호출해 조각 모으기
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    req = rec.requests[0]                                    # 녹화된 요청
    assert req["path"] == "/v1/chat/completions"            # 경로 확인
    assert list(req["body"]) == ["model", "messages", "stream", "tools", "max_tokens", "temperature", "stream_options", "top_k"]  # 본문 키 순서 확인
    assert req["body"]["tools"] == [] and req["body"]["stream_options"] == {"include_usage": True}  # 빈 도구 배열·stream_options 확인
    h = {k.lower(): v for k, v in req["headers"].items()}   # 헤더 키 소문자로 정리
    assert h["authorization"] == "Bearer sk-1" and h["x-org"] == "a" and h["user-agent"] == "OpenAI/JS 5.23.2"  # 인증·커스텀·UA 헤더 확인
    text = "".join((ch["choices"][0]["delta"].get("content") or "") for ch in chunks if ch.get("choices"))  # 조각의 content를 이어 붙인다
    assert text == "안녕하세요 반갑습니다"                            # 글 전체 확인
    assert chunks[-1]["usage"]["prompt_tokens"] == 3         # 사용량 조각은 맨 끝


def test_custom_authorization_header_replaces_default():    # 커스텀 Authorization 시험
    server, rec = serve([{"content": "ok"}])                # 가짜 서버 시작
    try:                                                    # 서버 종료 보장
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1", api_key="sk-1", model="m",  # 커스텀 Authorization 헤더 설정
                      request_options={"headers": {"Authorization": "Token abc"}})  # 커스텀 헤더 전달
        run(collect(c, [], None))                           # 호출 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    h = {k.lower(): v for k, v in rec.requests[0]["headers"].items()}  # 헤더 키 소문자로 정리
    assert h["authorization"] == "Token abc"                # 기본 Bearer 대신 커스텀 값이 쓰인다
    assert "tools" not in rec.requests[0]["body"]            # tools=None(undefined)이면 키가 없다
    assert "max_tokens" not in rec.requests[0]["body"]       # defaultCompletionOptions 없음 → 옵션 키 없음


def test_status_error_message_and_sdk_retries():            # 상태 오류 글과 SDK 재시도 시험
    server, rec = serve([{"status": 400, "content": "bad thing"}])  # 400 응답을 내는 서버
    try:                                                    # 서버 종료 보장
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="m")  # 클라이언트 생성
        with pytest.raises(LLMError) as ei:                 # 오류가 올라와야 한다
            run(collect(c, [], []))                         # 호출 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert str(ei.value) == "400 bad thing" and ei.value.status == 400   # SDK APIError 글
    assert len(rec.requests) == 1                            # 400은 SDK도 재시도 안 함

    server, rec = serve([{"status": 503}, {"status": 503}, {"content": "ok"}])   # SDK가 2번 재시도
    try:                                                    # 서버 종료 보장
        c = LLMClient(api_base=f"http://127.0.0.1:{server.server_port}/v1/", api_key="k", model="m")  # 클라이언트 생성
        chunks = run(collect(c, [], []))                    # 호출 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert len(rec.requests) == 3 and chunks                # 요청 3번, 조각 있음
    retry_counts = [{k.lower(): v for k, v in r["headers"].items()}["x-stainless-retry-count"] for r in rec.requests]  # 요청마다 재시도 횟수 헤더 수집
    assert retry_counts == ["0", "1", "2"]                  # 0,1,2 순서 확인


def test_connection_error_message():                        # 연결 오류 글 시험
    c = LLMClient(api_base="http://127.0.0.1:1/v1/", api_key="k", model="m", sdk_max_retries=0)   # 닫힌 포트
    with pytest.raises(LLMError) as ei:                     # 오류가 올라와야 한다
        run(collect(c, [], []))                             # 호출 실행
    assert str(ei.value) == "Connection error."             # SDK 기본 연결 오류 글
    assert is_retryable_error(ei.value) is False             # cn: SDK가 감싼 연결 오류는 CLI 층에서 재시도 안 함


# ── 재시도(exponentialBackoff.test.ts) ──

@pytest.mark.parametrize("err,expected", [                  # 재시도 판정 표 시험
    ({"code": "ECONNRESET"}, True), ({"code": "ENOTFOUND"}, True), ({"code": "ETIMEDOUT"}, True),  # 네트워크 오류 코드
    ({"status": 429}, True), ({"status": 502}, True), ({"status": 503}, True), ({"status": 504}, True),  # 재시도 가능한 상태 코드
    ({"type": "server_error"}, True), ({"type": "rate_limit_exceeded"}, True), ({"code": "EPIPE"}, True),  # 서버 오류 type·EPIPE
    ({"code": "ECONNREFUSED"}, True), ({"message": "The service is overloaded"}, True),  # ECONNREFUSED·과부하 메시지
    ({"message": "premature close"}, True), ({"message": "Request aborted"}, True), ({"message": "PREMATURE CLOSE"}, True),  # 연결 끊김 메시지(대소문자 무시)
    ({"status": 400}, False), ({"status": 401}, False), ({"status": 403}, False), ({"status": 404}, False),  # 재시도 안 하는 상태 코드
    ({"type": "client_error"}, False), ({"message": "Unknown error"}, False), ({}, False), (None, False),  # 그 밖의 오류·빈 값
    ("Error message", False), ({"code": "INVALID_REQUEST", "status": 429}, True), ({"status": 429, "type": "client_error"}, True),  # 문자열 오류와 우선순위 사례
])                                                          # 표 끝
def test_is_retryable_error(err, expected):                 # 표의 각 사례를 검사
    assert is_retryable_error(err) is expected              # 판정 결과가 기대와 같아야 한다


def test_context_length_error_is_not_retryable():           # 컨텍스트 초과는 재시도 금지 시험
    assert is_retryable_error(LLMError("400 This model's maximum context length is 8192 tokens", status=503)) is False  # 상태가 503이어도 컨텍스트 초과면 재시도 안 함


def test_calculate_delay():                                 # 대기 시간 계산 시험
    o = BackoffOptions(jitter=False)                         # 지터 없이
    assert [calculate_delay(i, o) for i in range(3)] == pytest.approx([1000, 1600, 2560])   # JS도 2560.0000000000005
    assert calculate_delay(20, o) == 30000                   # 상한
    j = BackoffOptions()                                    # 지터 있는 옵션
    assert calculate_delay(0, j, rand=lambda: 0.0) == 500    # 최소 50%
    assert calculate_delay(0, j, rand=lambda: 0.999999) == 999  # 난수가 1에 가까우면 내림 후 999
    assert calculate_delay(10, j, rand=lambda: 0.0) == 15000  # 상한 뒤 지터


def test_with_exponential_backoff_retries_whole_stream():   # 스트림 전체 재시도 시험
    attempts = []                                            # 시도 기록

    async def factory(_sig):                                # 테스트용 생성기 공장
        attempts.append(1)                                  # 시도 기록
        async def gen():                                    # 조각을 내는 생성기
            yield "a"                                       # 첫 조각
            if len(attempts) < 3:                           # 세 번째 시도 전에는
                raise LLMError("x", status=503)              # 재시도 대상
            yield "b"                                       # 세 번째엔 둘째 조각까지
        return gen()                                        # 생성기 반환

    async def main():                                       # 재시도 바깥 루프를 돌려 결과 모으기
        out = []                                            # 결과 목록
        async for c in with_exponential_backoff(factory, AbortSignal()):  # 재시도 포함 스트림을 순회
            out.append("R" if isinstance(c, RetryMarker) else c)  # 표지는 R로, 나머지는 그대로
        return out                                          # 결과 반환
    assert run(main()) == ["a", "R", "a", "R", "a", "b"]     # 처음부터 다시(표지를 무시하면 cn처럼 중복)

    async def bad(_sig):                                    # 재시도 대상 아닌 오류를 내는 공장
        async def gen():                                    # 생성기 정의
            raise LLMError("400 nope", status=400)          # 400 오류
            yield                                            # 생성기로 만들기
        return gen()                                        # 생성기 반환

    async def main2():                                      # 오류가 나는 스트림을 순회
        async for _ in with_exponential_backoff(bad, AbortSignal()):  # 재시도 감싼 스트림 순회
            pass                                            # 조각은 쓰지 않는다
    with pytest.raises(LLMError):                            # 재시도 대상 아님 → 바로
        run(main2())                                        # 실행


def test_responses_model_is_unsupported():                  # gpt-5 모델은 미지원 시험
    from ncn.llm.client import ResponsesApiNotSupportedError  # 미지원 오류 클래스 가져오기
    from ncn.llm.retry import chat_completion_stream_with_backoff  # 재시도 층 함수 가져오기
    c = LLMClient(api_base="http://127.0.0.1:1/v1/", api_key="k", model="gpt-5-mini")  # gpt-5 모델을 쓰는 클라이언트
    with pytest.raises(ResponsesApiNotSupportedError):      # 미지원 오류가 나야 한다
        chat_completion_stream_with_backoff(c, [], [], AbortSignal())  # 스트림 생성 요청
