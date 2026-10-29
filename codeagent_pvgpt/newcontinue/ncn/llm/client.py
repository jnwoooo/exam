# ncn/llm/client.py — OpenAI 호환 스트리밍 LLM 클라이언트. cn packages/openai-adapters/src/apis/OpenAI.ts
#   + openai-node 5.23.2 SDK(URL 조립·헤더·SDK 재시도·SSE 해석·오류 글) + packages/fetch/src/fetch.ts(requestOptions)
# 루프(ncn/loop)는 stream_chat()이 돌려주는 조각(dict, OpenAI chat.completion.chunk 모양)만 안다
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기·시간 제한·중단
import email.utils                                           # retry-after 날짜 형식 해석
import json                                                  # JSON 해석·문자열 이스케이프
import math                                                  # NaN·무한대 판정
import platform                                              # X-Stainless-Arch 값
import random                                                # SDK 재시도 지터
import re                                                    # 응답 API 모델 이름 정규식
import sys                                                   # X-Stainless-OS 값
import time                                                  # retry-after 날짜 → 남은 시간
from decimal import Decimal                                  # 실수의 최단 자릿수 분해(JS 숫자 표기용)
from typing import Any, AsyncIterator, Optional              # 타입
from urllib.parse import urlsplit, urlunsplit                # localhost → 127.0.0.1 치환

import httpx                                                 # HTTP 클라이언트

from ncn.compat import cn_compat                             # [ncn 부록B B1] cn 원래 동작 스위치

# ─────────────────────────────────────────────────────────────────────────────
# JS 호환 JSON — cn은 JSON.parse / JSON.stringify를 쓴다. 숫자 표기·키 순서·undefined 생략을 같게 만든다
# ─────────────────────────────────────────────────────────────────────────────


class _Undefined:                                           # JS undefined를 나타내는 자리표시 클래스
    """JS undefined 자리표시. 객체 안에서는 키째 빠지고, 배열 안에서는 null이 된다(JSON.stringify 규칙)."""

    def __repr__(self) -> str:                               # 디버깅 표시
        return "undefined"                                  # undefined 글자로 보여 준다


UNDEFINED = _Undefined()                                     # 하나뿐인 undefined 값

_MAX_SAFE = 2 ** 53                                          # JS가 정수를 정확히 표현하는 한계


def js_number(x: float | int) -> str:                       # JS 숫자 표기와 같은 글을 만드는 함수
    """JS Number.prototype.toString(ECMAScript Number::toString)과 같은 숫자 글을 만든다."""
    if isinstance(x, bool):                                  # bool은 int의 하위형 — 숫자로 쓰지 않음
        return "true" if x else "false"                     # JS식 소문자 true/false
    if isinstance(x, int) and abs(x) <= _MAX_SAFE:           # 안전 범위 정수는 그대로
        return str(x)                                       # 그대로 글로
    x = float(x)                                             # 큰 정수는 JS처럼 배정밀도로 바뀐다
    if math.isnan(x) or math.isinf(x):                      # NaN·무한대면
        return "null"                                       # JSON처럼 null
    if x == 0:                                               # -0도 "0"
        return "0"                                          # 0은 항상 0 글자
    sign = "-" if x < 0 else ""                              # 부호
    t = Decimal(repr(abs(x))).as_tuple()                     # repr = 최단 왕복 자릿수(JS와 같은 기준)
    digits = "".join(map(str, t.digits))                     # 유효 숫자
    n = len(digits) + t.exponent                             # 소수점 위치(ECMAScript의 n)
    digits = digits.rstrip("0") or "0"                       # 끝 0 제거(위치 n은 그대로)
    digits = digits.lstrip("0") or "0"                       # 앞 0 제거(Decimal은 앞 0이 없지만 안전하게)
    k = len(digits)                                          # 자릿수(ECMAScript의 k)
    if k <= n <= 21:                                         # 정수 모양: 숫자 + 0들
        return sign + digits + "0" * (n - k)                # 숫자 뒤에 0을 채운다
    if 0 < n <= 21:                                          # 소수점이 숫자 사이
        return sign + digits[:n] + "." + digits[n:]         # 소수점 삽입
    if -6 < n <= 0:                                          # 0.000ddd 모양
        return sign + "0." + "0" * (-n) + digits            # 앞에 0들을 붙인다
    e = n - 1                                                # 지수 표기
    exp = ("+" if e >= 0 else "-") + str(abs(e))             # JS는 e+21, e-7처럼 부호를 항상 붙이고 0을 채우지 않음
    if k == 1:                                               # 한 자리
        return sign + digits + "e" + exp                    # "de+N" 모양
    return sign + digits[0] + "." + digits[1:] + "e" + exp   # 여러 자리


def _is_array_index(key: str) -> bool:                      # 배열 인덱스처럼 취급되는 키인지 판정
    """JS 객체에서 '정수 인덱스'로 취급돼 앞으로 정렬되는 키인가(0 ~ 2^32-2, 앞자리 0 없음)."""
    if not key or not key.isascii() or not key.isdigit():    # 숫자만으로 된 글자가 아니면 아님
        return False                                        # 해당 없음
    if len(key) > 1 and key[0] == "0":                       # "01" 같은 것은 일반 키
        return False                                        # 일반 키
    return int(key) < 2 ** 32 - 1                            # 배열 인덱스 범위


def _js_key_order(d: dict) -> list:                         # JS 객체의 키 순서를 계산
    """JS 객체 키 순서: 정수 인덱스 키(오름차순) → 나머지(넣은 순서)."""
    keys = [k for k in d.keys()]                             # 넣은 순서
    idx = sorted((k for k in keys if isinstance(k, str) and _is_array_index(k)), key=int)   # 정수 키
    rest = [k for k in keys if not (isinstance(k, str) and _is_array_index(k))]             # 나머지
    return idx + rest                                       # 정수 키가 앞, 나머지가 뒤


def js_stringify(value: Any) -> str:                        # JSON.stringify와 같은 글을 만든다
    """JSON.stringify(value)와 같은 글(공백 없음, 한글 등은 이스케이프하지 않음)."""
    if value is None:                                       # None이면
        return "null"                                       # JSON null
    if value is True:                                       # True면
        return "true"                                       # JSON true
    if value is False:                                      # False면
        return "false"                                      # JSON false
    if isinstance(value, (int, float)):                      # 숫자
        return js_number(value)                             # JS 숫자 표기
    if isinstance(value, str):                               # 문자열 — 이스케이프 규칙은 json.dumps(ensure_ascii=False)와 JS가 같다
        return json.dumps(value, ensure_ascii=False)        # 한글 등은 그대로 둔다
    if isinstance(value, (list, tuple)):                     # 배열: undefined는 null
        return "[" + ",".join("null" if v is UNDEFINED else js_stringify(v) for v in value) + "]"  # undefined 원소는 null로
    if isinstance(value, dict):                              # 객체: undefined 값은 키째 생략
        parts = []                                           # "키":값 조각
        for k in _js_key_order(value):                      # JS 키 순서대로
            v = value[k]                                    # 값 꺼내기
            if v is UNDEFINED:                               # 생략
                continue                                    # 키째 생략
            parts.append(json.dumps(str(k), ensure_ascii=False) + ":" + js_stringify(v))  # 키와 값을 직렬화해 모은다
        return "{" + ",".join(parts) + "}"                  # 중괄호로 감싼다
    raise TypeError(f"js_stringify: 지원하지 않는 형식 {type(value).__name__}")  # 지원하지 않는 형식은 오류


def _parse_float(s: str) -> float | int:                    # JSON 실수 글을 JS 숫자로
    """JSON 실수 글 → JS Number와 같은 값. 정수값이면 int로(JS는 1.0과 1을 구분하지 않음)."""
    f = float(s)                                             # 가장 가까운 배정밀도
    if f.is_integer() and abs(f) <= _MAX_SAFE:              # 정수값이고 안전 범위면
        return int(f)                                       # int로 돌려준다
    return f                                                # 아니면 실수 그대로


def _parse_int(s: str) -> float | int:                      # JSON 정수 글을 JS 숫자로
    """JSON 정수 글 → JS Number와 같은 값. 2^53을 넘으면 JS처럼 배정밀도로 바뀐다."""
    i = int(s)                                               # 정확한 값
    return i if abs(i) <= _MAX_SAFE else float(s)            # 큰 수는 반올림


def _reject_constant(s: str):                               # NaN·Infinity 거부용 콜백
    """JSON.parse는 NaN·Infinity를 받지 않는다."""
    raise ValueError(f"Unexpected token {s[0]} in JSON")    # JSON.parse와 같은 오류 글


def js_parse(text: str) -> Any:                             # JSON.parse와 같은 규칙의 해석 함수
    """JSON.parse(text)와 같은 규칙으로 해석한다. 실패하면 ValueError."""
    return json.loads(text, parse_float=_parse_float, parse_int=_parse_int, parse_constant=_reject_constant)  # JS 숫자 규칙·상수 거부를 적용해 해석


# ─────────────────────────────────────────────────────────────────────────────
# 중단 신호 — JS AbortSignal 대응
# ─────────────────────────────────────────────────────────────────────────────


class AbortSignal:                                          # JS AbortSignal 대응 클래스
    """JS AbortController + AbortSignal을 하나로. abort()를 부르면 aborted가 참이 되고 wait()가 깨어난다."""

    def __init__(self) -> None:                             # 초기화
        self.aborted = False                                 # 중단됐는가
        self._event: Optional[asyncio.Event] = None          # 대기용(이벤트 루프가 생긴 뒤 만든다)

    def _ev(self) -> asyncio.Event:                         # 대기 이벤트를 얻는 도우미
        """대기 이벤트(지연 생성 — 루프 밖에서 만들어도 되게)."""
        if self._event is None:                             # 아직 없으면
            self._event = asyncio.Event()                   # 새로 만든다
            if self.aborted:                                 # 이미 중단됐으면 바로 세팅
                self._event.set()                           # 바로 세팅
        return self._event                                  # 이벤트를 돌려준다

    def abort(self) -> None:                                # 중단을 알리는 메서드
        """중단(Esc, ACP session/cancel 등)."""
        self.aborted = True                                 # 중단 표시
        if self._event is not None:                         # 대기자가 있으면
            self._event.set()                               # 깨운다

    async def wait(self) -> None:                           # 중단될 때까지 기다리는 메서드
        """중단될 때까지 기다린다."""
        await self._ev().wait()                             # 이벤트가 세팅될 때까지 대기


# ─────────────────────────────────────────────────────────────────────────────
# 오류 — openai-node APIError 계열의 메시지·속성을 흉내 낸다(재시도 판정과 헤드리스 오류 출력에 쓰임)
# ─────────────────────────────────────────────────────────────────────────────


class LLMError(Exception):                                  # LLM 호출 오류 클래스
    """LLM 호출 오류. status(HTTP 코드)·error(본문의 error 필드)·code·type을 cn 오류 객체처럼 갖는다."""

    def __init__(self, message: str, *, status: Optional[int] = None, error: Any = None,  # 초기화(상태 코드·본문 오류·헤더 보관)
                 code: Optional[str] = None, headers: Optional[dict] = None):
        super().__init__(message)                           # 예외 메시지 설정
        self.message = message                              # JS error.message와 같은 속성
        self.status = status                                # HTTP 상태 코드
        self.error = error                                   # 본문의 error 필드
        self.code = code if code is not None else (error.get("code") if isinstance(error, dict) else None)  # code가 없으면 본문 error의 code를 쓴다
        self.type = error.get("type") if isinstance(error, dict) else None   # SDK: error.type(server_error 등)
        self.headers = headers or {}                         # 응답 헤더


class APIConnectionError(LLMError):                         # 요청 자체 실패 오류 클래스
    """openai-node APIConnectionError — 요청 자체가 실패(연결 거부·DNS 등)."""

    def __init__(self, message: str = "Connection error."):  # 초기화(기본 메시지)
        super().__init__(message)                            # SDK 기본 메시지


class APIConnectionTimeoutError(APIConnectionError):        # SDK 시간 초과 오류 클래스
    """openai-node APIConnectionTimeoutError — SDK 시간 제한 초과."""

    def __init__(self, message: str = "Request timed out."):  # 초기화(기본 메시지)
        super().__init__(message)                            # SDK 기본 메시지


class APIUserAbortError(LLMError):                          # 사용자 중단 오류 클래스
    """openai-node APIUserAbortError — 사용자가 중단."""

    def __init__(self, message: str = "Request was aborted."):  # 초기화(기본 메시지)
        super().__init__(message)                           # SDK 기본 메시지로 부모 초기화


class ResponsesApiNotSupportedError(LLMError):              # /responses 미지원 오류 클래스
    """cn은 gpt-5*/o[0-9]* 모델이면 /responses 엔드포인트로 간다. ncn은 이 경로를 옮기지 않았다(미지원)."""


def make_api_error_message(status: Optional[int], error: Any, message: Optional[str]) -> str:  # SDK 오류 메시지를 만드는 함수
    """openai-node APIError.makeMessage 그대로: '{status} {error.message}' 등."""
    if isinstance(error, dict) and error.get("message"):    # error.message가 있으면
        m = error["message"]                                # message 꺼내기
        msg = m if isinstance(m, str) else js_stringify(m)   # 문자열이 아니면 JSON
    elif error is not None and error is not UNDEFINED:       # error는 있는데 message가 없으면 error 전체 JSON
        msg = js_stringify(error)                           # error 전체를 JSON 글로
    else:                                                   # error가 아예 없으면
        msg = message                                        # 본문이 JSON이 아니었을 때의 원문
    if status and msg:                                       # 둘 다
        return f"{status} {msg}"                            # "상태 메시지"
    if status:                                               # 코드만
        return f"{status} status code (no body)"            # 본문 없음 표시
    if msg:                                                  # 글만
        return msg                                          # 그대로 반환
    return "(no status code or body)"                       # 둘 다 없을 때의 기본 글


# ─────────────────────────────────────────────────────────────────────────────
# 요청 본문 — streamChatResponse.ts:265-275 + streamChatResponse.types.ts getDefaultCompletionOptions + OpenAI.ts modifyChatBody
# ─────────────────────────────────────────────────────────────────────────────

OFFICIAL_API_BASE = "https://api.openai.com/v1/"             # OpenAI.ts:37 기본 apiBase(공식 API 판정에도 씀)
RESPONSES_MODEL_RE = re.compile(r"^(?:gpt-5|gpt-5-codex|o[0-9])", re.IGNORECASE)  # /responses를 쓰는 모델 이름 정규식


def is_responses_model(model: str) -> bool:                 # /responses 모델인지 판정
    """openaiResponses.ts isResponsesModel — 이 이름이면 cn은 /responses로 보낸다."""
    return bool(model) and bool(RESPONSES_MODEL_RE.match(model))  # 이름이 있고 정규식에 맞으면 참


def default_completion_options(opts: Optional[dict]) -> dict:  # 설정 옵션을 본문 키로 바꾼다
    """streamChatResponse.types.ts getDefaultCompletionOptions: 설정(camelCase) → 본문 키. 값이 없으면 undefined(생략)."""
    if opts is None:                                         # if (!opts) return {} — 빈 dict {}는 JS에서 참이라 아래로 간다
        return {}                                           # 빈 dict
    return {                                                 # 없는 키는 undefined(JSON에서 빠짐), null은 null 그대로
        "max_tokens": opts.get("maxTokens", UNDEFINED),      # 순서 그대로
        "temperature": opts.get("temperature", UNDEFINED),  # 온도
        "frequency_penalty": opts.get("frequencyPenalty", UNDEFINED),  # 빈도 페널티
        "presence_penalty": opts.get("presencePenalty", UNDEFINED),  # 존재 페널티
        "top_p": opts.get("topP", UNDEFINED),               # 누적 확률 상한
    }


def _truthy(v: Any) -> bool:                                # JS 참/거짓 판정
    """JS 참/거짓 판정(undefined·None·0·''·False는 거짓)."""
    return v is not UNDEFINED and bool(v)                   # undefined가 아니고 참인 값이면 참


def modify_chat_body(body: dict, api_base: str) -> dict:    # 요청 본문 보정(OpenAI.ts modifyChatBody)
    """OpenAI.ts modifyChatBody 그대로(본문을 직접 고치고 돌려준다)."""
    if body.get("stream"):                                   # 스트리밍이면 사용량 조각 요청
        body["stream_options"] = {"include_usage": True}    # 사용량 조각을 요청
    model = body.get("model") or ""                          # 모델 이름
    if _truthy(body.get("max_tokens", UNDEFINED)) and ("api.deepseek.com" in (api_base or "") or "deepseek-reasoner" in model):  # DeepSeek reasoner면
        body["max_completion_tokens"] = body["max_tokens"]  # max_tokens를 max_completion_tokens로 옮김
        body["max_tokens"] = UNDEFINED                       # 키 자리는 남고 JSON에서는 빠짐(JS와 같음)
    if api_base == OFFICIAL_API_BASE:                        # 공식 API일 때만
        if model.startswith("o") or "gpt-5" in model:        # o 계열·gpt-5
            body["max_completion_tokens"] = body.get("max_tokens", UNDEFINED)  # max_completion_tokens로 옮김
            body["max_tokens"] = UNDEFINED                  # 원래 키는 직렬화에서 빠지게 한다
            body["messages"] = [({**m, "role": "developer"} if m.get("role") == "system" else m)  # system 메시지는 developer 역할로 바꾼다
                                for m in body.get("messages", [])]   # system → developer
        tools = body.get("tools")                            # 도구가 있고 o3가 아니면
        if tools and tools is not UNDEFINED and not model.startswith("o3"):  # 도구가 있고 o3가 아니면
            body["parallel_tool_calls"] = False             # 병렬 도구 호출을 끈다
    return body                                             # 보정한 본문 반환


def build_chat_url(api_base: str, path: str = "/chat/completions") -> str:  # 요청 주소를 조립
    """openai-node buildURL: baseURL이 /로 끝나고 path가 /로 시작하면 한 번만. 그 뒤 fetch.ts가 localhost → 127.0.0.1."""
    url = api_base + (path[1:] if api_base.endswith("/") and path.startswith("/") else path)   # 단순 이어 붙이기(SDK와 같음)
    parts = urlsplit(url)                                    # 호스트 치환
    if parts.hostname == "localhost":                       # 호스트가 localhost면
        netloc = parts.netloc                                # 사용자정보·포트 보존
        userinfo, _, hostport = netloc.rpartition("@")      # 사용자정보와 호스트:포트 분리
        host, sep, port = hostport.partition(":")           # 호스트와 포트 분리
        hostport = "127.0.0.1" + (sep + port if sep else "")  # 호스트를 127.0.0.1로 치환
        netloc = (userinfo + "@" if userinfo else "") + hostport  # 사용자정보 다시 붙이기
        url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))  # 주소 재조립
    return url                                              # 완성된 주소 반환


# ─────────────────────────────────────────────────────────────────────────────
# 헤더 — openai-node 5.23.2 기본 헤더 + requestOptions.headers 덮어쓰기(util.ts customFetch, fetch.ts)
# ─────────────────────────────────────────────────────────────────────────────

SDK_VERSION = "5.23.2"                                       # packages/openai-adapters/package-lock.json의 openai 버전
NODE_VERSION_HINT = "v20.0.0"                                # X-Stainless-Runtime-Version 자리(차등 비교 대상 아님 — P0 3.6)
SDK_DEFAULT_TIMEOUT_MS = 600_000                             # openai-node 기본 시간 제한 10분
SDK_MAX_RETRIES = 2                                          # openai-node 기본 maxRetries(OpenAI.ts가 지정하지 않음)
AGENT_DEFAULT_TIMEOUT_S = 7200                               # getAgentOptions.ts: 소켓 시간 제한 기본 2시간


def _stainless_os() -> str:                                 # X-Stainless-OS 값 계산
    """X-Stainless-OS 값(openai-node 표기)."""
    if sys.platform == "win32":                             # Windows면
        return "Windows"                                    # 윈도우 표기
    if sys.platform == "darwin":                            # macOS면
        return "MacOS"                                      # 맥 표기
    if sys.platform.startswith("linux"):                    # 리눅스면
        return "Linux"                                      # 리눅스 표기
    return f"Other:{sys.platform}"                          # 그 밖의 OS


def _stainless_arch() -> str:                               # X-Stainless-Arch 값 계산
    """X-Stainless-Arch 값(Node process.arch 표기)."""
    m = platform.machine().lower()                          # CPU 구조 이름(소문자)
    return {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64", "i386": "x32", "x86": "x32"}.get(m, f"other:{m}")  # Node 표기로 바꾸고 모르면 other:


def build_headers(api_key: Optional[str], request_options: dict, *, retry_count: int, timeout_ms: Optional[float]) -> dict:  # 요청 헤더를 만드는 함수
    """요청 헤더. SDK 기본 → 커스텀 인증이 있으면 기본 인증 삭제 → requestOptions.headers 덮어쓰기."""
    h = {                                                    # Headers 객체를 forEach로 옮기면 키가 소문자(fetch.ts:111-118)
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": f"OpenAI/JS {SDK_VERSION}",
        "x-stainless-lang": "js",
        "x-stainless-package-version": SDK_VERSION,
        "x-stainless-os": _stainless_os(),
        "x-stainless-arch": _stainless_arch(),
        "x-stainless-runtime": "node",
        "x-stainless-runtime-version": NODE_VERSION_HINT,
        "x-stainless-retry-count": str(retry_count),        # 몇 번째 SDK 재시도인가
        "authorization": f"Bearer {api_key if api_key is not None else ''}",  # 키가 없으면 빈 글로 Bearer 헤더(OpenAI.ts:45)
    }
    if timeout_ms:                                           # SDK: 시간 제한이 있으면 초 단위 정수
        h["x-stainless-timeout"] = str(int(timeout_ms // 1000))  # 초 단위 정수로 헤더 추가
    custom = (request_options or {}).get("headers") or {}   # 설정의 커스텀 헤더
    has_auth = custom.get("Authorization") or custom.get("authorization")  # 커스텀 인증 헤더 여부
    has_xkey = custom.get("x-api-key") or custom.get("X-Api-Key")  # 커스텀 x-api-key 여부
    if has_auth:                                             # 커스텀 인증이면 기본 Authorization 삭제
        h.pop("authorization", None)                        # 기본 Authorization 제거
    if has_xkey:                                             # chat 경로엔 x-api-key가 원래 없음(그래도 같은 규칙)
        h.pop("x-api-key", None)                            # 기본 x-api-key 제거
    out = httpx.Headers(h)                                   # 대소문자 무시 덮어쓰기
    for k, v in custom.items():                             # 커스텀 헤더로 덮어쓴다
        out[k] = str(v)                                     # 값을 글로 저장
    return dict(out.multi_items())                           # 보낼 dict


# ─────────────────────────────────────────────────────────────────────────────
# SSE — openai-node streaming.ts _iterSSEMessages + Stream.fromSSEResponse
# ─────────────────────────────────────────────────────────────────────────────


class _SSEDecoder:                                          # SSE 줄 해석기 클래스
    """줄 단위 SSE 해석기(SDK SSEDecoder와 같은 규칙: 빈 줄에서 한 이벤트 완성)."""

    def __init__(self) -> None:                             # 초기화
        self.event: Optional[str] = None                     # event: 필드
        self.data: list[str] = []                            # data: 필드들

    def decode(self, line: str) -> Optional[dict]:          # 한 줄을 넣어 이벤트를 완성하는 메서드
        """한 줄을 넣는다. 이벤트가 완성되면 {event, data}를 돌려준다."""
        if line.endswith("\r"):                              # 남은 CR 제거
            line = line[:-1]                                # 제거
        if not line:                                         # 빈 줄 = 이벤트 끝
            if self.event is None and not self.data:         # 아무것도 없으면 무시
                return None                                 # 무시
            sse = {"event": self.event, "data": "\n".join(self.data)}  # 이벤트 하나 완성
            self.event, self.data = None, []                 # 초기화
            return sse                                      # 완성된 이벤트 반환
        if line.startswith(":"):                             # 주석 줄
            return None                                     # 무시
        field, _, value = line.partition(":")                # 필드:값
        if value.startswith(" "):                            # 값 앞 공백 하나 제거
            value = value[1:]                               # 제거
        if field == "event":                                # event 필드면
            self.event = value                              # 이름 보관
        elif field == "data":                               # data 필드면
            self.data.append(value)                         # 값 모으기
        return None                                          # id·retry 등은 무시


async def _iter_sse_chunks(resp: httpx.Response, signal: Optional[AbortSignal]) -> AsyncIterator[dict]:  # SSE 응답을 JSON 조각으로 바꾸는 비동기 생성기
    """SSE 응답 → 조각(dict). [DONE] 뒤는 버리고, data.error가 있으면 APIError로 던진다."""
    decoder = _SSEDecoder()                                  # 해석기
    done = False                                             # [DONE] 받았는가
    try:                                                    # 스트림 읽기 중 오류를 감싼다
        async for line in resp.aiter_lines():                # \n, \r\n, \r 모두 줄 끝
            if signal is not None and signal.aborted:        # 중단
                raise APIUserAbortError()                   # 사용자 중단 오류
            sse = decoder.decode(line)                       # 한 줄 넣기
            if sse is None:                                  # 아직 이벤트가 안 끝남
                continue                                    # 다음 줄로
            if done:                                         # [DONE] 뒤는 읽기만 하고 버림
                continue                                    # 버린다
            if sse["data"].startswith("[DONE]"):             # 끝 표시
                done = True                                 # 종료 표시
                continue                                    # 다음 줄로
            event = sse["event"]                             # 이벤트 이름
            data = js_parse(sse["data"])                     # JSON 해석(실패하면 그대로 오류 — SDK도 throw)
            if event is None or not event.startswith("thread."):   # 일반 조각
                if isinstance(data, dict) and data.get("error"):   # 스트림 안의 오류
                    raise LLMError(make_api_error_message(None, data["error"], None), error=data["error"])  # API 오류로 던진다
                yield data                                  # 조각을 호출자에게 넘긴다
            elif event == "error":                           # (Assistants 전용 형식 — chat에는 오지 않음)
                raise LLMError(make_api_error_message(None, data.get("error"), data.get("message")), error=data.get("error"))  # API 오류로 던진다
            # thread.* 이벤트는 chat 조각이 아니므로 루프에 넘기지 않는다(cn processChunk도 choices가 없으면 무시)
    except (httpx.RemoteProtocolError, httpx.ReadError) as e:   # 스트림 도중 끊김 → node-fetch "Premature close"와 같은 글
        raise LLMError(f"Invalid response body while trying to fetch {resp.request.url}: Premature close",  # node-fetch와 같은 Premature close 오류
                       code="ERR_STREAM_PREMATURE_CLOSE") from e  # 오류 코드 지정
    except httpx.ReadTimeout as e:                           # 소켓 시간 제한(getAgentOptions timeout)
        raise LLMError(f"network timeout at: {resp.request.url}", code="ETIMEDOUT") from e  # 네트워크 시간 초과 오류


# ─────────────────────────────────────────────────────────────────────────────
# SDK 재시도 — openai-node core makeRequest/shouldRetry/retryRequest
# ─────────────────────────────────────────────────────────────────────────────


async def _sleep(seconds: float) -> None:                   # 비동기 대기(시험에서 교체 가능하도록 분리)
    """기다리기(시험에서 바꿔 끼울 수 있게 모듈 함수로 둔다)."""
    await asyncio.sleep(seconds)                            # 지정한 초만큼 기다린다


def _should_retry_status(resp: httpx.Response) -> bool:     # 상태 코드로 재시도 여부 판정
    """SDK shouldRetry: x-should-retry 헤더 우선, 그다음 408·409·429·5xx."""
    hint = resp.headers.get("x-should-retry")                # 서버 지시
    if hint == "true":                                      # true면
        return True                                         # 재시도
    if hint == "false":                                     # false면
        return False                                        # 재시도 안 함
    return resp.status_code in (408, 409, 429) or resp.status_code >= 500  # 408·409·429 또는 5xx면 재시도


def _sdk_retry_delay(retries_remaining: int, max_retries: int, headers: Optional[httpx.Headers]) -> float:  # SDK 재시도 대기 시간 계산
    """SDK retryRequest의 대기 시간(초). retry-after-ms / retry-after(초·날짜)가 0~60초면 그 값, 아니면 0.5×2^n(최대 8) × (1 - 0~25%)."""
    timeout_ms: Optional[float] = None                       # 서버가 준 대기 시간
    if headers is not None:                                 # 헤더가 있으면
        ms = headers.get("retry-after-ms")                   # 밀리초
        if ms:                                              # 있으면
            try:                                            # 숫자로 해석
                timeout_ms = float(ms)                      # 밀리초 값
            except ValueError:                              # 숫자가 아니면
                timeout_ms = None                           # 무효 처리
        ra = headers.get("retry-after")                      # 초 또는 HTTP 날짜
        if ra and timeout_ms is None:                       # 아직 값이 없을 때만
            try:                                            # 초 단위 숫자로 시도
                timeout_ms = float(ra) * 1000               # 밀리초로 환산
            except ValueError:                              # 숫자가 아니면 날짜 형식으로 시도
                try:                                        # 날짜 해석 시도
                    dt = email.utils.parsedate_to_datetime(ra)  # HTTP 날짜를 시각으로 변환
                    timeout_ms = dt.timestamp() * 1000 - time.time() * 1000  # 지금까지 남은 시간(ms)
                except (TypeError, ValueError):             # 날짜도 아니면
                    timeout_ms = None                       # 무효 처리
    if timeout_ms is None or not (0 <= timeout_ms < 60_000):  # 없거나 범위 밖이면 기본 계산
        num_retries = max_retries - retries_remaining        # 지금까지 재시도 횟수
        sleep_s = min(0.5 * (2 ** num_retries), 8.0)        # 0.5초부터 2배씩, 최대 8초
        jitter = 1 - random.random() * 0.25                  # 최대 25% 줄임
        timeout_ms = sleep_s * jitter * 1000                # 밀리초로 환산
    return timeout_ms / 1000                                # 초 단위로 반환


# ─────────────────────────────────────────────────────────────────────────────
# 클라이언트
# ─────────────────────────────────────────────────────────────────────────────


class LLMClient:                                            # 스트리밍 클라이언트 클래스
    """OpenAI 호환 chat/completions 스트리밍 클라이언트(cn OpenAIApi.chatCompletionStream 경로).

    - api_base: config의 apiBase(없으면 None → https://api.openai.com/v1/)
    - default_completion_options: config의 defaultCompletionOptions(camelCase 그대로) 또는 None
    - request_options: config의 requestOptions(headers·extraBodyProperties·timeout·verifySsl·caBundlePath·proxy·noProxy)
    """

    def __init__(self, *, api_base: Optional[str], api_key: Optional[str], model: str,  # 초기화(주소·키·모델·옵션)
                 default_completion_options: Optional[dict] = None, request_options: Optional[dict] = None,
                 http: Optional[httpx.AsyncClient] = None, sdk_max_retries: int = SDK_MAX_RETRIES):
        self.api_base = api_base if api_base is not None else OFFICIAL_API_BASE   # config.apiBase ?? 기본값
        self.api_key = api_key                               # 인증 키(None이면 "Bearer ")
        self.model = model                                   # 본문 model
        self.default_completion_options = default_completion_options   # 본문 옵션 원본
        self.request_options = request_options or {}        # 요청 옵션(없으면 빈 dict)
        self.sdk_max_retries = sdk_max_retries               # SDK 층 재시도 횟수
        ro_timeout = self.request_options.get("timeout")     # 설정의 timeout(문서상 초)
        # OpenAI.ts:47 — `timeout: requestOptions.timeout || undefined`를 SDK에 그대로 넘긴다. SDK는 이 값을 **밀리초**로 쓴다(cn 버그: 60 → 60ms).
        # [ncn 부록B B1] 고친 동작: 설정값(초)을 ×1000 해 밀리초로. NCN_CN_COMPAT=1 이면 cn처럼 그대로(밀리초로 해석)
        sdk_scale = 1 if cn_compat() else 1000               # [ncn 부록B B1] 초 → 밀리초 배율(cn 동작이면 1)
        self.sdk_timeout_ms: float = float(ro_timeout) * sdk_scale if ro_timeout else SDK_DEFAULT_TIMEOUT_MS  # SDK 시간 제한(ms) — 설정값 또는 기본 10분
        self.socket_timeout_s: float = float(ro_timeout) if ro_timeout is not None else AGENT_DEFAULT_TIMEOUT_S   # 소켓 시간 제한(초, getAgentOptions)
        self._own_http = http is None                        # 직접 만든 클라이언트면 닫을 책임
        self.http = http or self._make_http()                # HTTP 클라이언트

    def _make_http(self) -> httpx.AsyncClient:              # 요청 옵션으로 httpx 클라이언트를 만든다
        """requestOptions(verifySsl·caBundlePath·proxy·noProxy·clientCertificate)로 httpx 클라이언트를 만든다."""
        ro = self.request_options                            # 줄임
        verify: Any = True                                   # 기본: 인증서 검사
        if ro.get("verifySsl") is False:                    # verifySsl이 false면
            verify = False                                  # 검사 끄기
        elif ro.get("caBundlePath"):                         # 사내 CA 추가(시스템 CA + 사용자 CA)
            import ssl                                       # 필요할 때만
            ctx = ssl.create_default_context()               # 기본 신뢰 저장소
            paths = ro["caBundlePath"] if isinstance(ro["caBundlePath"], list) else [ro["caBundlePath"]]  # 문자열이든 목록이든 목록으로
            for p in paths:                                  # 파일마다 추가(실패는 cn처럼 무시)
                try:                                        # 추가 시도
                    ctx.load_verify_locations(cafile=p)     # CA 파일 적재
                except (OSError, ssl.SSLError):             # 실패는
                    pass                                    # 무시(cn과 같음)
            verify = ctx                                    # 만든 문맥을 검증 방식으로 쓴다
        cert = None                                          # 클라이언트 인증서
        cc = ro.get("clientCertificate")                    # 설정의 clientCertificate
        if isinstance(cc, dict) and cc.get("cert"):         # cert가 있으면
            cert = (cc["cert"], cc.get("key"), cc.get("passphrase")) if cc.get("key") else cc["cert"]  # key가 있으면 (cert, key, passphrase), 없으면 cert만
        proxy = self._proxy_for(self.api_base)               # 프록시(설정 > 환경변수, noProxy 제외)
        return httpx.AsyncClient(verify=verify, cert=cert, proxy=proxy, trust_env=False,  # httpx 클라이언트 생성(환경변수는 직접 처리하므로 끔)
                                 timeout=httpx.Timeout(self.socket_timeout_s))  # 소켓 시간 제한 적용

    def _proxy_for(self, url: str) -> Optional[str]:        # URL에 쓸 프록시를 고른다
        """packages/fetch/src/util.ts getProxy + shouldBypassProxy."""
        import os                                            # 환경변수
        parts = urlsplit(url)                                # 대상 주소
        proxy = self.request_options.get("proxy")            # 설정 우선
        if not proxy:                                       # 설정에 없으면
            if parts.scheme == "https":                     # https 대상이면
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")  # HTTPS 프록시 환경변수 우선, 없으면 HTTP 것
            else:                                           # http 대상이면
                proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")  # HTTP 프록시 환경변수
        if not proxy:                                       # 프록시가 없으면
            return None                                     # 프록시 안 씀
        env_no = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""   # 우회 목록
        ignores = [i.strip().lower() for i in env_no.split(",") if i.strip()]  # 우회 패턴 목록
        ignores += [i.strip().lower() for i in (self.request_options.get("noProxy") or []) if i.strip()]  # 설정의 noProxy도 합친다
        host = (parts.hostname or "").lower()                # 주의: cn은 hostname(포트 없음)으로 비교
        for pat in ignores:                                 # 패턴마다
            if _pattern_matches_hostname(host, pat):        # 호스트가 맞으면
                return None                                 # 프록시 우회
        return proxy                                        # 프록시 주소 반환

    async def aclose(self) -> None:                         # HTTP 클라이언트 닫기
        """직접 만든 HTTP 클라이언트를 닫는다."""
        if self._own_http:                                  # 직접 만든 경우만
            await self.http.aclose()                        # 연결 종료

    def build_body(self, messages: list[dict], tools: Optional[list[dict]]) -> dict:  # 요청 본문 dict를 만든다
        """요청 본문(키 순서: model, messages, stream, tools, 옵션들, stream_options, [max_completion_tokens…])."""
        body: dict = {                                      # 키 순서가 중요한 본문
            "model": self.model,                            # 본문 model 필드
            "messages": messages,                            # 시스템 메시지 포함
            "stream": True,                                  # 항상 스트리밍
            "tools": tools if tools is not None else UNDEFINED,   # 빈 배열이어도 들어감
            **default_completion_options(self.default_completion_options),   # max_tokens 등
        }
        return modify_chat_body(body, self.api_base)        # stream_options 등

    def serialize_body(self, body: dict) -> str:            # 본문을 JSON 글로 직렬화
        """본문 JSON 글 — fetch.ts의 extraBodyProperties 병합까지 적용({...body, ...extra})."""
        extra = self.request_options.get("extraBodyProperties")  # 추가 본문 속성
        if extra:                                            # 같은 키는 값만 바뀌고 새 키는 뒤에 붙는다
            body = {**body, **extra}                        # 뒤의 값이 앞을 덮어쓴다
        return js_stringify(body)                           # JS 방식으로 직렬화

    async def stream_chat(self, messages: list[dict], tools: Optional[list[dict]], *,  # chat/completions를 스트리밍하는 비동기 생성기
                          signal: Optional[AbortSignal] = None) -> AsyncIterator[dict]:
        """chat/completions 스트리밍. 조각(dict)을 차례로 돌려준다. 사용량 조각은 맨 끝으로 미룬다(OpenAI.ts:160-174)."""
        body = self.build_body(messages, tools)              # 본문
        data = self.serialize_body(body).encode("utf-8")     # 보낼 바이트
        url = build_chat_url(self.api_base)                  # 주소
        resp = await self._send_with_sdk_retries(url, data, signal)   # 응답(헤더까지)
        last_usage_chunk: Optional[dict] = None              # 사용량 조각 보관
        try:                                                # 스트림 읽는 동안
            async for chunk in _iter_sse_chunks(resp, signal):   # 조각들
                if isinstance(chunk, dict) and chunk.get("usage"):   # 사용량이 든 조각은 보관(마지막 것만 남음)
                    last_usage_chunk = chunk                # 보관만 한다
                else:                                       # 그 밖의 조각은
                    yield chunk                             # 바로 넘긴다
            if last_usage_chunk is not None:                 # 끝에 한 번
                yield last_usage_chunk                      # 맨 끝에 넘긴다
        finally:                                            # 항상 실행
            await resp.aclose()                              # 연결 정리

    async def _send_with_sdk_retries(self, url: str, data: bytes, signal: Optional[AbortSignal]) -> httpx.Response:  # SDK 방식으로 요청을 보내고 재시도
        """SDK makeRequest: 시간 제한(헤더 받을 때까지) + 408/409/429/5xx·연결 오류면 최대 maxRetries번 재시도."""
        retries_remaining = self.sdk_max_retries             # 남은 재시도
        while True:                                         # 성공하거나 오류를 던질 때까지 반복
            if signal is not None and signal.aborted:        # 보내기 전 중단 확인
                raise APIUserAbortError()                   # 사용자 중단 오류
            headers = build_headers(self.api_key, self.request_options,  # 시도마다 헤더 새로 만들기
                                    retry_count=self.sdk_max_retries - retries_remaining, timeout_ms=self.sdk_timeout_ms)  # 몇 번째 재시도인지 헤더에 넣는다
            req = self.http.build_request("POST", url, content=data, headers=headers)   # 요청
            try:                                            # 보내기 시도
                resp = await self._send_once(req, signal)    # 보내기(헤더까지)
            except APIUserAbortError:                        # 중단은 재시도하지 않음
                raise                                       # 그대로 올린다
            except APIConnectionError as e:                  # 시간 초과·연결 실패
                if retries_remaining > 0:                    # SDK: 연결 오류도 재시도
                    await _sleep(_sdk_retry_delay(retries_remaining, self.sdk_max_retries, None))  # 대기
                    retries_remaining -= 1                  # 재시도 횟수 차감
                    continue                                # 다시 시도
                raise e                                     # 없으면 오류를 올린다
            if resp.is_success:                              # 2xx → 스트림 시작
                return resp                                 # 응답 반환
            if retries_remaining > 0 and _should_retry_status(resp):   # 재시도 대상 상태 코드
                await resp.aclose()                          # 본문 버리고
                await _sleep(_sdk_retry_delay(retries_remaining, self.sdk_max_retries, resp.headers))  # 서버 지시에 따라 대기
                retries_remaining -= 1                      # 재시도 횟수 차감
                continue                                    # 다시 시도
            raise await self._status_error(resp)             # 오류 객체로

    async def _send_once(self, req: httpx.Request, signal: Optional[AbortSignal]) -> httpx.Response:  # 한 번 보내기(시간 제한·중단 포함)
        """한 번 보내기. SDK 시간 제한(밀리초)과 중단 신호를 함께 건다."""
        send = asyncio.ensure_future(self.http.send(req, stream=True))   # 보내기 작업
        waiters = [send]                                     # 기다릴 것들
        abort_task = None                                    # 중단 대기 작업
        if signal is not None:                              # 중단 신호가 있으면
            abort_task = asyncio.ensure_future(signal.wait())  # 중단 대기 작업 만들기
            waiters.append(abort_task)                      # 목록에 추가
        try:                                                # 먼저 끝나는 것을 기다린다
            done, _ = await asyncio.wait(waiters, timeout=self.sdk_timeout_ms / 1000, return_when=asyncio.FIRST_COMPLETED)  # 시간 제한 안에 하나라도 끝나면 진행
        finally:                                            # 항상 정리
            if abort_task is not None and not abort_task.done():  # 중단 대기가 남아 있으면
                abort_task.cancel()                          # 중단 대기 정리
        if send not in done:                                 # 시간 초과 또는 중단
            send.cancel()                                    # 보내기 취소
            try:                                            # 취소 완료를 기다린다
                await send                                  # 취소된 작업 기다리기
            except BaseException:                            # 취소 예외는 삼킨다
                pass                                        # 삼킨다
            if signal is not None and signal.aborted:       # 중단 때문이면
                raise APIUserAbortError()                   # 사용자 중단 오류
            raise APIConnectionTimeoutError()               # 아니면 시간 초과 오류
        try:                                                # 보내기가 끝났으면
            return send.result()                             # 응답
        except httpx.TimeoutException as e:                  # 소켓 시간 제한
            raise APIConnectionTimeoutError() from e        # 시간 초과 오류
        except httpx.HTTPError as e:                         # 연결 거부·DNS 등 → "Connection error."
            raise APIConnectionError() from e               # 연결 오류
        except OSError as e:                                 # 그 밖 소켓 오류
            raise APIConnectionError() from e               # 연결 오류

    async def _status_error(self, resp: httpx.Response) -> LLMError:  # 오류 응답을 예외 객체로 바꾼다
        """오류 응답 → SDK APIError와 같은 메시지('{status} {error.message}')."""
        try:                                                # 본문 읽기 시도
            raw = (await resp.aread()).decode("utf-8", errors="replace")   # 본문 글
        except httpx.HTTPError as e:                         # 읽기 실패면 그 메시지
            raw = str(e)                                    # 오류 글을 본문 대신 쓴다
        finally:                                            # 항상
            await resp.aclose()                             # 연결 정리
        try:                                                # JSON 해석 시도
            err_json = js_parse(raw)                         # JSON이면
        except ValueError:                                  # JSON이 아니면
            err_json = None                                 # 해석 결과 없음
        err_message = None if err_json is not None else raw  # JSON이 아니면 원문이 메시지
        error = err_json.get("error") if isinstance(err_json, dict) else None  # 본문의 error 필드
        msg = make_api_error_message(resp.status_code, error, err_message)  # SDK와 같은 메시지 조립
        return LLMError(msg, status=resp.status_code, error=error, headers=dict(resp.headers))  # 상태·본문·헤더를 담은 오류 객체


def _pattern_matches_hostname(hostname: str, pattern: str) -> bool:  # NO_PROXY 패턴이 호스트와 맞는지 판정
    """packages/fetch/src/util.ts patternMatchesHostname(정확히·*.도메인·.도메인, 포트 지정)."""
    host, _, host_port = hostname.lower().partition(":")    # 호스트:포트
    pat, _, pat_port = pattern.lower().partition(":")       # 패턴과 포트 분리
    if pat_port and (not host_port or host_port != pat_port):   # 포트가 지정됐는데 다르면
        return False                                        # 불일치
    if pat == host:                                          # 정확히
        return True                                         # 일치
    if pat.startswith("*.") and host.endswith(pat[1:]):     # *.도메인 패턴이 맞으면
        return True                                         # 일치
    if pat.startswith(".") and host.endswith(pat[1:]):       # .example.com(cn: slice(1)이라 'example.com'으로 끝나면 일치)
        return True                                         # 일치
    return False                                            # 아무것도 안 맞으면 불일치
