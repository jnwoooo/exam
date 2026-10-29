# ncn/tokenizer.py — 토큰 세기. cn extensions/cli/src/util/tokenizer.ts + core/llm/getAdjustedTokenCount.ts + compaction.ts(shouldAutoCompact)를 그대로 옮긴 것
# cn은 gpt-tokenizer 3.0.1 의 기본 encode 를 쓴다. 그 기본은 o200k_base(esm/main.js 가 encoding/o200k_base.js 를 다시 내보냄 — 직접 확인).
# 단, gpt-tokenizer 3.0.1 의 o200k_base 는 tiktoken 의 o200k 정규식이 아니라 cl100k 계열 정규식(CL_AND_O_TOKEN_SPLIT_PATTERN, 플래그 giu)으로 나눈다
# → tiktoken.get_encoding("o200k_base") 를 그대로 쓰면 숫자·대소문자 섞인 글 등에서 개수가 다르다. 그래서 같은 순위표 + gpt-tokenizer 정규식으로 Encoding 을 직접 만든다.
# 특수 토큰도 gpt-tokenizer 쪽 8개(tiktoken 은 2개)를 그대로 넣어, 글에 "<|im_start|>" 등이 있으면 cn처럼 오류가 나게 한다.
# 폐쇄망: 순위표 파일은 ncn/data/o200k_base.tiktoken 만 읽는다(인터넷·tiktoken 캐시 폴더를 쓰지 않음).
#   원본 URL: https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
#   sha256  : 446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d (tiktoken_ext/openai_public.py 의 expected_hash 와 같음)
from __future__ import annotations                           # 타입 주석 지연 평가

import base64                                                # 순위표 줄의 토큰은 base64
import hashlib                                               # 파일 무결성 확인
import json                                                  # 오류 시 JSON.stringify 흉내
import logging                                               # cn logger.error/debug 대응
import math                                                  # Math.ceil — 올림 계산용
import threading                                             # 처음 한 번만 읽기(동시 호출 보호)
from pathlib import Path                                     # 파일 경로
from typing import Any, Optional                             # 타입

import tiktoken                                              # BPE 엔진(Rust) — 순위표·정규식은 직접 넣음

from ncn.tools.formatters import to_locale_string           # JS toLocaleString(천 단위 쉼표)
from ncn.tools.truncate import js_len                        # JS string.length(UTF-16 단위)

_log = logging.getLogger("ncn.tokenizer")                   # 로그(표준 출력에 쓰지 않음)

# ───────────────────────── 인코딩 준비 ─────────────────────────
DATA_FILE = Path(__file__).parent / "data" / "o200k_base.tiktoken"   # 순위표(배포 묶음에 포함해야 함)
DATA_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"   # 원본 파일 해시

# JS \s 집합(Rust regex \s 와 다름: JS 는 \ufeff 포함·\x85 제외) — 정규식 글자 묶음 안에 넣을 글
_JS_WS = r"\t\n\x0b\x0c\r \x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}\x{feff}"  # JS 공백 문자 집합(정규식 문자 클래스 안에 넣을 글)
# gpt-tokenizer 3.0.1 esm/encodingParams/constants.js CL_AND_O_TOKEN_SPLIT_PATTERN 원문:
#   /(?:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+/giu
# 아래는 같은 뜻을 tiktoken(fancy-regex) 문법으로 옮긴 것: 플래그 i 는 맨 앞 (?i), \s·\S 는 JS 집합으로 풀어 씀
PAT_STR = (                                                 # gpt-tokenizer 의 글 나누기 정규식(조각을 이어 붙여 만듦)
    r"(?i)(?:'s|'t|'re|'ve|'m|'ll|'d)"                       # 축약형('s 등, 대소문자 무시)
    r"|[^\r\n\p{L}\p{N}]?\p{L}+"                             # (글자 아닌 한 글자 +) 글자들
    r"|\p{N}{1,3}"                                           # 숫자 1~3개
    r"| ?[^" + _JS_WS + r"\p{L}\p{N}]+[\r\n]*"               # 기호 묶음(+ 줄바꿈)
    r"|[" + _JS_WS + r"]*[\r\n]+"                            # 공백 + 줄바꿈
    r"|[" + _JS_WS + r"]+(?![^" + _JS_WS + r"])"             # 뒤에 공백 아닌 글자가 오지 않는 공백(\s+(?!\S))
    r"|[" + _JS_WS + r"]+"                                   # 나머지 공백
)                                                           # 정규식 조립 끝
# gpt-tokenizer 3.0.1 esm/encodingParams/o200k_base.js 의 특수 토큰(번호까지 그대로)
SPECIAL_TOKENS = {                                          # gpt-tokenizer 특수 토큰 8개와 번호
    "<|endoftext|>": 199_999,                               # 문장 끝 토큰
    "<|fim_prefix|>": 200_000,                              # FIM 접두 토큰
    "<|fim_middle|>": 200_001,                              # FIM 중간 토큰
    "<|fim_suffix|>": 200_002,                              # FIM 접미 토큰
    "<|im_start|>": 200_003,                                # 채팅 시작 토큰
    "<|im_end|>": 200_004,                                  # 채팅 끝 토큰
    "<|im_sep|>": 200_005,                                  # 채팅 구분 토큰
    "<|endofprompt|>": 200_006,                             # 프롬프트 끝 토큰
}                                                           # 특수 토큰 사전 끝

_enc: Optional[tiktoken.Encoding] = None                     # 만든 인코딩(한 번만)
_enc_lock = threading.Lock()                                 # 동시에 처음 부를 때 보호


def _load_ranks(path: Path) -> dict[bytes, int]:            # 순위표 파일 읽기 함수
    """o200k_base.tiktoken 읽기 — 해시 확인 후 "base64토큰 순위" 줄들을 사전으로(tiktoken load_tiktoken_bpe 와 같은 해석)."""
    data = path.read_bytes()                                 # 파일 전체(3.6MB)
    digest = hashlib.sha256(data).hexdigest()                # 무결성
    if digest != DATA_SHA256:                                # 깨진 파일이면 바로 알림
        raise ValueError(f"o200k_base.tiktoken 해시 불일치: {digest} (기대 {DATA_SHA256})")  # 파일이 변조·손상되었으면 즉시 예외
    ranks: dict[bytes, int] = {}                             # 토큰 바이트 → 순위
    for line in data.splitlines():                           # 줄마다
        if not line:                                         # 빈 줄 무시
            continue                                        # 빈 줄은 건너뜀
        token, rank = line.split()                           # "base64 숫자"
        b = base64.b64decode(token)                          # 토큰 바이트
        if b.startswith(_BOM):                               # gpt-tokenizer 에서는 찾을 수 없는 토큰(아래 설명) → 빼서 똑같이
            continue                                        # BOM 시작 토큰은 cn 이 못 찾으므로 제외
        ranks[b] = int(rank)                                 # 등록
    return ranks                                            # 순위표 반환


# gpt-tokenizer 3.0.1 특이점(직접 확인): BOM(EF BB BF)으로 시작하는 토큰 10개(5574 "\ufeff", 9251 "\ufeffusing" …)는
# 바이트 배열로 저장되고, 찾을 때 getBpeRankFromBytes 가 TextDecoder 로 글자로 바꾸는데 TextDecoder 가 앞 BOM 을 지워 ""·"using" 등으로 찾게 된다
# → 영영 못 찾음. 그래서 "\ufeff" 한 글자가 cn에서는 [5416, 123] 두 토큰이 된다(tiktoken 원본은 [5574] 하나). 같은 개수를 내려고 이 토큰들을 뺀다.
_BOM = b"\xef\xbb\xbf"                                       # UTF-8 BOM 바이트


def get_encoding() -> tiktoken.Encoding:                    # 인코딩을 만들어 돌려주는 함수(처음 한 번만 만듦)
    """cn encode 와 같은 결과를 내는 인코딩(처음 부를 때 파일을 읽어 만든다)."""
    global _enc                                             # 모듈 전역 캐시 사용 선언
    if _enc is None:                                         # 아직 안 만들었으면
        with _enc_lock:                                      # 한 스레드만
            if _enc is None:                                # 락 안에서 한 번 더 확인(이중 검사)
                _enc = tiktoken.Encoding(                    # 인터넷 없이 직접 구성
                    name="o200k_base_gpt_tokenizer_3_0_1",   # 이름(표시용)
                    pat_str=PAT_STR,                         # gpt-tokenizer 정규식
                    mergeable_ranks=_load_ranks(DATA_FILE),  # o200k 순위표
                    special_tokens=SPECIAL_TOKENS,           # gpt-tokenizer 특수 토큰
                )                                           # Encoding 생성 끝
    return _enc                                             # 캐시된 인코딩 반환


class DisallowedSpecialTokenError(ValueError):              # 특수 토큰이 글에 있을 때의 예외
    """gpt-tokenizer encode 가 던지는 오류와 같은 글: "Disallowed special token found: <|endoftext|>"."""


def encode(text: str) -> list[int]:                         # 토큰 목록으로 바꾸는 함수(gpt-tokenizer encode)
    """gpt-tokenizer encode(text) — 기본 설정(특수 토큰 전부 금지). 글에 특수 토큰 글이 있으면 오류."""
    if not isinstance(text, str):                            # JS: encode(undefined) 등은 TypeError
        raise TypeError(f"Cannot encode {type(text).__name__}")  # 문자열이 아니면 TypeError
    enc = get_encoding()                                     # 인코딩
    try:                                                     # tiktoken 도 특수 토큰이 있으면 ValueError — 오류 글만 cn과 맞춤
        return enc.encode(text, allowed_special=set(), disallowed_special="all")   # 특수 토큰 금지
    except ValueError as e:                                  # tiktoken 오류 → gpt-tokenizer 글로
        found = _first_special(text)                         # 처음 나온 특수 토큰
        if found is not None:                               # 찾았으면
            raise DisallowedSpecialTokenError(f"Disallowed special token found: {found}") from e  # cn 과 같은 문구로 바꿔 올림
        raise                                                # 그 밖 오류는 그대로


def _first_special(text: str) -> Optional[str]:             # 글에서 가장 앞 특수 토큰을 찾는 함수
    """글에서 가장 앞에 나온 특수 토큰 글(JS text.match(정규식)[0] 과 같게 — 같은 위치면 목록 순서가 앞인 것)."""
    best: Optional[tuple[int, int, str]] = None              # (위치, 목록 순서, 글)
    for order, tok in enumerate(SPECIAL_TOKENS):             # 정규식 a|b|c 는 같은 위치에서 앞 선택지가 이김
        pos = text.find(tok)                                 # 처음 위치
        if pos >= 0 and (best is None or (pos, order) < best[:2]):  # 더 앞 위치(같으면 목록 순서 앞)이면 갱신
            best = (pos, order, tok)                        # 후보 저장
    return best[2] if best else None                        # 없으면 None, 있으면 그 글


def count_tokens(text: str, model_name: str = "") -> int:   # 배수 적용한 토큰 수 함수
    """encode(text).length 에 모델별 배수(getAdjustedTokenCountFromModel)를 적용한 값. model_name 이 비면 배수 1."""
    return get_adjusted_token_count_from_model(len(encode(text)), model_name)  # encode 길이에 모델 배수를 적용


# ───────────────────────── core/llm/getAdjustedTokenCount.ts ─────────────────────────
ANTHROPIC_TOKEN_MULTIPLIER = 1.23                            # claude — Anthropic 모델 배수
GEMINI_TOKEN_MULTIPLIER = 1.18                               # gemini — Gemini 모델 배수
MISTRAL_TOKEN_MULTIPLIER = 1.26                              # *stral, mixtral — Mistral 계열 배수


def get_adjusted_token_count_from_model(base_tokens: int, model_name: Optional[str]) -> int:  # 모델별 배수 적용 함수
    """getAdjustedTokenCountFromModel — 모델 이름(소문자)에 따라 배수 후 올림."""
    multiplier = 1.0                                         # 기본 배수
    lower = (model_name or "").lower()                       # modelName?.toLowerCase() ?? ""  # 소문자 모델 이름
    if "claude" in lower:                                    # Anthropic — claude 면
        multiplier = ANTHROPIC_TOKEN_MULTIPLIER             # Anthropic 배수
    elif "gemini" in lower:                                  # Google — gemini 면
        multiplier = GEMINI_TOKEN_MULTIPLIER                # Gemini 배수
    elif "stral" in lower or "mixtral" in lower:             # Mistral 계열(mistral, codestral, devstral …)
        multiplier = MISTRAL_TOKEN_MULTIPLIER               # Mistral 배수
    return math.ceil(base_tokens * multiplier)               # Math.ceil(base * 배수) — 부동소수 계산도 JS와 같음


# ───────────────────────── 모델 설정 접근(ModelConfig 또는 dict) ─────────────────────────
DEFAULT_MAX_TOKENS_RATIO = 0.35                              # 출력 예약 비율
MAX_MAX_TOKENS = 64_000                                      # 출력 예약 상한
DEFAULT_CONTEXT_LENGTH = 200_000                             # 설정에 없을 때 컨텍스트 길이


def _completion_options(model: Any) -> dict:                # 모델의 기본 완료 옵션을 꺼내는 함수
    """model.defaultCompletionOptions — ncn ModelConfig(default_completion_options) 또는 cn 모양 dict 모두 받음."""
    if model is None:                                       # 모델이 없으면
        return {}                                           # 빈 옵션
    if isinstance(model, dict):                              # cn 원형 dict
        return model.get("defaultCompletionOptions") or {}  # dict 면 cn 키로 읽음
    return getattr(model, "default_completion_options", None) or {}   # ncn ModelConfig — ncn ModelConfig 속성으로 읽음


def _model_id(model: Any) -> str:                           # 모델 id 를 꺼내는 함수
    """model.model ?? "" — 배수 판정은 name 이 아니라 model(모델 id) 로 한다(tokenizer.ts:45,58)."""
    if model is None:                                       # 모델이 없으면
        return ""                                           # 빈 글
    if isinstance(model, str):                               # 이름만 넘긴 경우
        return model                                        # 문자열이면 그대로 id
    if isinstance(model, dict):                             # dict 면
        return model.get("model") or ""                     # "model" 키 값
    return getattr(model, "model", None) or ""              # 객체면 model 속성


def get_model_context_limit(model: Any) -> int:             # 모델 컨텍스트 한도 함수
    """getModelContextLimit — defaultCompletionOptions.contextLength ?? 200000(모델 최상위 contextLength 는 보지 않음)."""
    v = _completion_options(model).get("contextLength")      # 값
    return DEFAULT_CONTEXT_LENGTH if v is None else v        # ?? — null/undefined 만 기본값


def get_model_max_tokens(model: Any) -> int:                # 출력 예약 토큰 수 함수
    """getModelMaxTokens — maxTokens 가 없으면 ceil(min(한도×0.35, 64000))."""
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = _completion_options(model).get("maxTokens")  # 설정값
    if max_tokens is None:                                   # === undefined — maxTokens 지정이 없으면
        return math.ceil(min(context_limit * DEFAULT_MAX_TOKENS_RATIO, MAX_MAX_TOKENS))  # 한도×0.35 와 64000 중 작은 값을 올림
    return max_tokens                                       # 지정값 그대로


# ───────────────────────── 메시지·대화 기록 세기(tokenizer.ts) ─────────────────────────
def _count_content_tokens(content: Any, model: Any) -> int:  # 메시지 내용 토큰 세기 함수
    """countContentTokens — 문자열이면 encode, 배열이면 text 부분 + 그림 1024개씩, 그 뒤 배수."""
    if isinstance(content, str):                             # 일반 글
        return get_adjusted_token_count_from_model(len(encode(content)), _model_id(model))  # 글 토큰 수에 모델 배수 적용
    if isinstance(content, list):                            # 멀티모달 배열
        token_count = 0                                      # 합계
        for part in content:                                 # 조각마다
            if part.get("type") == "text" and part.get("text"):   # 글 조각(빈 글은 건너뜀)
                token_count += len(encode(part["text"]))    # 글 조각의 토큰 수 합산
            if part.get("type") == "imageUrl":               # 그림 조각 — 대략값
                token_count += 1024                         # 그림은 대략 1024 토큰
        return get_adjusted_token_count_from_model(token_count, _model_id(model))  # 배수 적용
    return 0                                                 # 그 밖(없음 등)


def _count_tool_call_function_tokens(fn: Optional[dict]) -> int:  # 도구 호출 함수 부분 토큰 세기
    """countToolCallFunctionTokens — 이름 + 10(구조) + 인자."""
    if not fn:                                               # 없으면 0
        return 0                                            # 함수 정보 없으면 0
    token_count = 0                                         # 합계 초기화
    token_count += len(encode(fn.get("name") if fn.get("name") is not None else "")) + 10   # name ?? "" + 구조 비용
    token_count += len(encode(fn.get("arguments") if fn.get("arguments") is not None else ""))   # arguments ?? ""  # 인자 글 토큰 합산
    return token_count                                      # 합계 반환


def _count_tool_output_tokens(output: Optional[list]) -> int:  # 도구 출력 토큰 세기 함수
    """countToolOutputTokens — 항목마다 content(있으면) + 5."""
    if not output:                                           # undefined → 0 (빈 배열도 0)
        return 0                                            # 출력 없으면 0
    token_count = 0                                         # 합계 초기화
    for item in output:                                      # 출력 항목마다
        if item.get("content"):                              # 빈 글이 아니면
            token_count += len(encode(item["content"]))     # 내용 토큰 합산
        token_count += 5                                     # 구조 비용(name 은 모델에 안 보내므로 세지 않음)
    return token_count                                      # 합계 반환


def _as_history_item(message_or_item: dict) -> dict:        # 메시지를 기록 항목 모양으로 맞추는 함수
    """cn ChatHistoryItem({message, contextItems, toolCallStates}) 모양으로. 메시지 dict 만 오면 감싼다."""
    if "message" in message_or_item and isinstance(message_or_item.get("message"), dict):   # 이미 기록 항목
        return message_or_item                              # 이미 항목이면 그대로
    return {"message": message_or_item, "contextItems": []}  # 메시지만 → 항목으로


def _js_json_stringify(v: Any) -> Optional[str]:            # JSON.stringify 흉내 함수
    """JSON.stringify(v) 흉내(간격 없음·유니코드 그대로). undefined 는 None."""
    if v is None:                                            # undefined/null 구분 불가 — content 없음은 undefined 로 본다
        return None                                         # 값이 없으면 None
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # 공백 없는 JSON 글(한글은 그대로)


def count_chat_history_item_tokens(history_item: dict, model: Any) -> int:  # 기록 항목 하나의 토큰 세기 함수
    """countChatHistoryItemTokens — 항목 하나(내용 배수 + 역할 2 + 도구 호출/결과/컨텍스트 고정값). 오류면 4글자=1토큰 추정."""
    item = _as_history_item(history_item)                    # 모양 맞춤
    context_items = item.get("contextItems") or []           # cn은 항상 배열(ncn 기록에 없으면 빈 배열로 봄)
    try:                                                    # 세는 중 오류는 글자 수 추정으로 대체
        token_count = 0                                     # 합계 초기화
        message = item["message"]                            # 메시지
        token_count += _count_content_tokens(message.get("content"), model)   # 내용(배수 적용)
        token_count += 2                                     # 역할(대략 1~2)
        tool_calls = message.get("toolCalls")                # 도구 호출(ncn 이 OpenAI 모양 tool_calls 를 쓰면 그것도 봄)
        if tool_calls is None:                              # toolCalls 가 없으면
            tool_calls = message.get("tool_calls")          # OpenAI 모양 tool_calls 로 대체
        if tool_calls and not item.get("toolCallStates"):    # toolCallStates 가 있으면 거기서 셈(중복 방지)
            for tc in tool_calls:                           # 호출마다
                token_count += _count_tool_call_function_tokens(tc.get("function"))  # 함수 부분 토큰 합산
        if message.get("role") == "tool" and "content" in message:   # 도구 결과 메시지 구조 비용
            token_count += 5                                # 도구 결과 구조 비용
        for ci in context_items:                             # 컨텍스트 항목
            token_count += len(encode(ci.get("content")))   # 컨텍스트 내용 토큰
            token_count += len(encode(ci.get("name")))      # 컨텍스트 이름 토큰
            token_count += 5                                 # 구조 비용
        for ts in item.get("toolCallStates") or []:          # 도구 호출 상태(호출 + 출력)
            token_count += _count_tool_call_function_tokens((ts.get("toolCall") or {}).get("function"))
            token_count += _count_tool_output_tokens(ts.get("output"))
        return token_count                                  # 합계 반환
    except Exception as error:                               # cn: logger.error 후 글자 수 추정
        _log.error("Error counting tokens for history item %s", error)  # 오류를 기록(stdout 금지)
        message = item.get("message") or {}                 # 메시지(없으면 빈 사전)
        c = message.get("content")                          # 내용
        content = c if isinstance(c, str) else _js_json_stringify(c)   # 문자열 아니면 JSON.stringify
        if content is None:                                  # JS: undefined.length → TypeError(밖으로 나감)
            raise TypeError("Cannot read properties of undefined (reading 'length')") from error  # 내용이 없으면 cn 처럼 TypeError
        context_content = "".join(f"{ci.get('content')}{ci.get('name')}" for ci in context_items)   # item.content + item.name — 컨텍스트 내용+이름 이어 붙임
        return math.ceil((js_len(content) + js_len(context_content)) / 4)   # 4글자(UTF-16 단위) = 1토큰


def count_message_tokens(message: dict, model_name: Any) -> int:  # 공개 API 이름 함수(항목 토큰 세기와 같음)
    """공개 API 이름 — count_chat_history_item_tokens 와 같다(메시지 dict 또는 기록 항목, 모델은 ModelConfig/dict/모델 id 글)."""
    return count_chat_history_item_tokens(message, model_name)  # 항목 토큰 세기에 위임


def count_chat_history_tokens(messages: list[dict], model_name: Any) -> int:  # 대화 기록 전체 토큰 세기 함수
    """countChatHistoryTokens — 항목 합 + 항목 수 × 3."""
    total = 0                                               # 합계 초기화
    for item in messages:                                    # 항목마다
        total += count_chat_history_item_tokens(item, model_name)  # 항목별 토큰 합산
    total += len(messages) * 3                               # 메시지 구조 비용
    return total                                            # 합계 반환


def calculate_context_usage_percentage(token_count: int, model: Any) -> int:  # 컨텍스트 사용률(%) 함수
    """calculateContextUsagePercentage — min(100, round(토큰/한도×100))."""
    limit = get_model_context_limit(model)                  # 모델 한도
    return min(100, _js_round(token_count / limit * 100))   # 사용률을 반올림하고 100 으로 제한


def _js_round(x: float) -> int:                             # JS Math.round 흉내 함수
    """Math.round — .5 는 위로(파이썬 round 는 짝수 쪽이라 다름)."""
    return math.floor(x + 0.5)                              # .5 는 위로 올림


# ───────────────────────── 도구 정의 세기 ─────────────────────────
def _count_parameter_field_tokens(fields: Any) -> int:      # 도구 인자 필드 토큰 세기 함수
    """countParameterFieldTokens — type·description 각 2+글, enum 은 (항목마다 3+글) - 3."""
    if not fields or not isinstance(fields, dict):           # 없으면 0(JS 는 객체 아닌 값이면 속성이 undefined)
        return 0                                            # 필드가 없으면 0
    tokens = 0                                              # 합계 초기화
    field_type = fields.get("type")                          # 형식
    field_desc = fields.get("description")                   # 설명
    field_enum = fields.get("enum")                          # 열거
    if field_type and isinstance(field_type, str):           # 빈 글 아닌 문자열
        tokens += 2                                         # type 구조 비용 2
        tokens += len(encode(field_type))                   # type 글 토큰
    if field_desc and isinstance(field_desc, str):          # description 이 있으면
        tokens += 2                                         # 구조 비용 2
        tokens += len(encode(field_desc))                   # description 글 토큰
    if field_enum and isinstance(field_enum, list) and len(field_enum) > 0:  # enum 이 있으면
        tokens -= 3                                         # enum 구조 보정 -3
        for e in field_enum:                                 # 항목마다
            tokens += 3                                     # 항목마다 구조 비용 3
            tokens += len(encode(e)) if isinstance(e, str) else 5   # 글이 아니면 5
    return tokens                                           # 합계 반환


def _count_single_tool_tokens(tool: dict) -> int:           # 도구 하나의 토큰 세기 함수
    """countSingleToolTokens — 이름 + 설명 + 최상위 properties 의 키와 필드(중첩·required 는 세지 않음)."""
    fn = tool["function"]                                    # {"name","description","parameters"}  # 함수 정의
    tokens = len(encode(fn["name"]))                         # 이름
    if fn.get("description"):                                # 설명(빈 글이면 건너뜀)
        tokens += len(encode(fn["description"]))            # 설명 토큰
    params = fn.get("parameters")                            # 인자 스키마
    props = params.get("properties") if isinstance(params, dict) else None  # 최상위 properties
    if props:                                                # 빈 객체도 JS 에선 참이지만 반복할 키가 없음
        for key in props:                                    # for…in — 합계라 순서 무관
            tokens += len(encode(key))                      # 키 이름 토큰
            tokens += _count_parameter_field_tokens(props[key])  # 필드 정의 토큰
    return tokens                                           # 합계 반환


def count_tool_definitions_tokens(tools: Optional[list[dict]]) -> int:  # 도구 정의 전체 토큰 세기 함수
    """countToolDefinitionTokens — 도구 없으면 0, 있으면 12 + Σ도구 + 12."""
    if not tools:                                            # 없음/빈 배열
        return 0                                            # 도구 없으면 0
    num_tokens = 12                                          # 배열 구조 비용
    for tool in tools:                                      # 도구마다
        num_tokens += _count_single_tool_tokens(tool)       # 하나씩 합산
    return num_tokens + 12                                   # 감싸는 구조 비용


count_tool_definition_tokens = count_tool_definitions_tokens   # cn 이름(단수형) 별칭


def count_total_input_tokens(*, chat_history: list[dict], model: Any, system_message: Optional[str] = None,  # 입력 토큰 총합 함수(기록+시스템+도구)
                             tools: Optional[list[dict]] = None) -> int:  # 시스템 메시지·도구 정의는 선택 인자
    """countTotalInputTokens — 기록 + (기록에 system 이 없을 때) 시스템 메시지+4 + 도구 정의. 시스템 메시지는 배수 없음."""
    total = count_chat_history_tokens(chat_history, model)   # 기록
    if system_message:                                       # 빈 글이면 건너뜀
        has_system = any(_as_history_item(i)["message"].get("role") == "system" for i in chat_history)  # 기록에 system 메시지가 이미 있는지
        if not has_system:                                   # 기록에 system 이 없을 때만
            total += len(encode(system_message))             # (오류는 잡지 않음 — cn과 같음)
            total += 4                                       # 구조 비용
    if tools:                                                # 도구가 있으면
        total += count_tool_definitions_tokens(tools)       # 도구 정의 토큰 추가
    return total                                            # 총합 반환


def validate_context_length(*, chat_history: list[dict], model: Any, safety_buffer: int = 0,  # 컨텍스트 길이 검증 함수
                            system_message: Optional[str] = None, tools: Optional[list[dict]] = None) -> dict:  # 여유 버퍼·시스템 메시지·도구는 선택 인자
    """validateContextLength — 입력 + 출력 예약(maxTokens 또는 ceil(한도×0.35), 64000 상한 없음) + 버퍼 > 한도 이면 오류 글.
    반환: {"isValid", "error"?, "inputTokens", "contextLimit", "maxTokens"} (cn 반환 키 그대로)."""
    input_tokens = count_total_input_tokens(chat_history=chat_history, system_message=system_message, tools=tools, model=model)  # 입력 토큰 총합
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = _completion_options(model).get("maxTokens") or 0   # maxTokens || 0 — 설정된 maxTokens(없으면 0)
    reserved_for_output = max_tokens if max_tokens > 0 else math.ceil(context_limit * 0.35)   # 출력 예약
    total_required = input_tokens + reserved_for_output + safety_buffer   # 필요량
    if total_required > context_limit:                       # 넘으면 cn 원문 오류 글
        buf = f" + buffer ({safety_buffer})" if safety_buffer > 0 else ""   # 버퍼 표기(이 값만 쉼표 없음 — 원문 그대로)
        return {                                            # 한도를 넘으면 실패 결과 반환
            "isValid": False,                               # 유효하지 않음
            "error": (f"Context length exceeded: input ({to_locale_string(input_tokens)}) + max_tokens "  # cn 원문 오류 글(천 단위 쉼표 포함)
                      f"({to_locale_string(reserved_for_output)}){buf} = {to_locale_string(total_required)} "
                      f"> context_limit ({to_locale_string(context_limit)})"),
            "inputTokens": input_tokens,                    # 입력 토큰 수
            "contextLimit": context_limit,                  # 컨텍스트 한도
            "maxTokens": reserved_for_output,               # 출력 예약 토큰
        }
    return {"isValid": True, "inputTokens": input_tokens, "contextLimit": context_limit, "maxTokens": reserved_for_output}  # 한도 이내면 성공 결과 반환


# ───────────────────────── 자동 압축 판단(compaction.ts:18-20, 266-315) ─────────────────────────
AUTO_COMPACT_BUFFER_CAP = 15_000                             # 버퍼 상한
AUTO_COMPACT_BUFFER_RATIO = 0.8                              # 비율


def get_auto_compact_message(model: Any) -> str:            # 자동 압축 안내 글 함수
    """getAutoCompactMessage — 압축 안내 글(한도를 K 단위, toFixed(0))."""
    limit = get_model_context_limit(model)                  # 모델 한도
    return f"Approaching context limit ({_js_to_fixed0(limit / 1000)}K tokens). Auto-compacting chat history..."  # K 단위 한도를 넣은 안내 글


def _js_to_fixed0(x: float) -> str:                         # toFixed(0) 흉내 함수
    """Number.prototype.toFixed(0) — 반올림은 .5 에서 0 에서 먼 쪽(양수 기준 위로)."""
    return str(math.floor(x + 0.5)) if x >= 0 else "-" + str(math.floor(-x + 0.5))  # 음수는 부호를 뒤에 붙여 대칭 반올림


def compaction_threshold(model: Any) -> int:                # 자동 압축 임계값 함수
    """압축 임계값 = 한도 - maxTokens - min(max(maxTokens, ceil(0.2×(한도-maxTokens))), 15000). 0 이하이면 cn 원문 오류."""
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = get_model_max_tokens(model)                 # 출력 예약(64000 상한 있음)
    ratio_buffer = math.ceil((1 - AUTO_COMPACT_BUFFER_RATIO) * (context_limit - max_tokens))   # (1-0.8) 도 JS 와 같은 부동소수
    safe_buffer = max(max_tokens, ratio_buffer)              # 큰 쪽
    buffer = min(safe_buffer, AUTO_COMPACT_BUFFER_CAP)       # 상한
    threshold = context_limit - max_tokens - buffer          # 임계
    if threshold <= 0:                                       # 설정 오류
        raise ValueError("max_tokens is larger than context_length, which should not be possible. Please check your configuration.")  # 임계값이 0 이하면 설정 오류
    return threshold                                        # 임계값 반환


def should_auto_compact(*, chat_history: list[dict], model: Any, system_message: Optional[str] = None,  # 자동 압축 필요 여부 함수
                        tools: Optional[list[dict]] = None) -> bool:  # 도구 정의는 선택 인자
    """shouldAutoCompact — 총 입력 토큰 >= 임계값 이면 참(cn은 입력을 먼저 센 뒤 임계값을 검사 — 순서 그대로)."""
    input_tokens = count_total_input_tokens(chat_history=chat_history, system_message=system_message, tools=tools, model=model)  # 입력 토큰 총합(먼저 센다 — cn 순서)
    threshold = compaction_threshold(model)                  # 임계(오류 가능)
    if tools:                                                # cn 은 로그용으로 도구·시스템 토큰을 다시 셈(시스템 메시지 encode 오류가 여기서도 날 수 있음)
        count_tool_definitions_tokens(tools)                # cn 이 로그용으로 다시 세는 부분(오류 발생 순서 맞춤)
    if system_message:                                      # 시스템 메시지가 있으면
        len(encode(system_message))                         # encode 오류가 여기서도 나게 호출만 한다
    return input_tokens >= threshold                        # 입력이 임계값 이상이면 압축 필요


should_compact = should_auto_compact                         # 공개 API 짧은 이름
