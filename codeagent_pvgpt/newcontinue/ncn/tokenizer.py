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
import math                                                  # Math.ceil
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
_JS_WS = r"\t\n\x0b\x0c\r \x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}\x{feff}"
# gpt-tokenizer 3.0.1 esm/encodingParams/constants.js CL_AND_O_TOKEN_SPLIT_PATTERN 원문:
#   /(?:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+/giu
# 아래는 같은 뜻을 tiktoken(fancy-regex) 문법으로 옮긴 것: 플래그 i 는 맨 앞 (?i), \s·\S 는 JS 집합으로 풀어 씀
PAT_STR = (
    r"(?i)(?:'s|'t|'re|'ve|'m|'ll|'d)"                       # 축약형('s 등, 대소문자 무시)
    r"|[^\r\n\p{L}\p{N}]?\p{L}+"                             # (글자 아닌 한 글자 +) 글자들
    r"|\p{N}{1,3}"                                           # 숫자 1~3개
    r"| ?[^" + _JS_WS + r"\p{L}\p{N}]+[\r\n]*"               # 기호 묶음(+ 줄바꿈)
    r"|[" + _JS_WS + r"]*[\r\n]+"                            # 공백 + 줄바꿈
    r"|[" + _JS_WS + r"]+(?![^" + _JS_WS + r"])"             # 뒤에 공백 아닌 글자가 오지 않는 공백(\s+(?!\S))
    r"|[" + _JS_WS + r"]+"                                   # 나머지 공백
)
# gpt-tokenizer 3.0.1 esm/encodingParams/o200k_base.js 의 특수 토큰(번호까지 그대로)
SPECIAL_TOKENS = {
    "<|endoftext|>": 199_999,
    "<|fim_prefix|>": 200_000,
    "<|fim_middle|>": 200_001,
    "<|fim_suffix|>": 200_002,
    "<|im_start|>": 200_003,
    "<|im_end|>": 200_004,
    "<|im_sep|>": 200_005,
    "<|endofprompt|>": 200_006,
}

_enc: Optional[tiktoken.Encoding] = None                     # 만든 인코딩(한 번만)
_enc_lock = threading.Lock()                                 # 동시에 처음 부를 때 보호


def _load_ranks(path: Path) -> dict[bytes, int]:
    """o200k_base.tiktoken 읽기 — 해시 확인 후 "base64토큰 순위" 줄들을 사전으로(tiktoken load_tiktoken_bpe 와 같은 해석)."""
    data = path.read_bytes()                                 # 파일 전체(3.6MB)
    digest = hashlib.sha256(data).hexdigest()                # 무결성
    if digest != DATA_SHA256:                                # 깨진 파일이면 바로 알림
        raise ValueError(f"o200k_base.tiktoken 해시 불일치: {digest} (기대 {DATA_SHA256})")
    ranks: dict[bytes, int] = {}                             # 토큰 바이트 → 순위
    for line in data.splitlines():                           # 줄마다
        if not line:                                         # 빈 줄 무시
            continue
        token, rank = line.split()                           # "base64 숫자"
        b = base64.b64decode(token)                          # 토큰 바이트
        if b.startswith(_BOM):                               # gpt-tokenizer 에서는 찾을 수 없는 토큰(아래 설명) → 빼서 똑같이
            continue
        ranks[b] = int(rank)                                 # 등록
    return ranks


# gpt-tokenizer 3.0.1 특이점(직접 확인): BOM(EF BB BF)으로 시작하는 토큰 10개(5574 "\ufeff", 9251 "\ufeffusing" …)는
# 바이트 배열로 저장되고, 찾을 때 getBpeRankFromBytes 가 TextDecoder 로 글자로 바꾸는데 TextDecoder 가 앞 BOM 을 지워 ""·"using" 등으로 찾게 된다
# → 영영 못 찾음. 그래서 "\ufeff" 한 글자가 cn에서는 [5416, 123] 두 토큰이 된다(tiktoken 원본은 [5574] 하나). 같은 개수를 내려고 이 토큰들을 뺀다.
_BOM = b"\xef\xbb\xbf"                                       # UTF-8 BOM 바이트


def get_encoding() -> tiktoken.Encoding:
    """cn encode 와 같은 결과를 내는 인코딩(처음 부를 때 파일을 읽어 만든다)."""
    global _enc
    if _enc is None:                                         # 아직 안 만들었으면
        with _enc_lock:                                      # 한 스레드만
            if _enc is None:
                _enc = tiktoken.Encoding(                    # 인터넷 없이 직접 구성
                    name="o200k_base_gpt_tokenizer_3_0_1",   # 이름(표시용)
                    pat_str=PAT_STR,                         # gpt-tokenizer 정규식
                    mergeable_ranks=_load_ranks(DATA_FILE),  # o200k 순위표
                    special_tokens=SPECIAL_TOKENS,           # gpt-tokenizer 특수 토큰
                )
    return _enc


class DisallowedSpecialTokenError(ValueError):
    """gpt-tokenizer encode 가 던지는 오류와 같은 글: "Disallowed special token found: <|endoftext|>"."""


def encode(text: str) -> list[int]:
    """gpt-tokenizer encode(text) — 기본 설정(특수 토큰 전부 금지). 글에 특수 토큰 글이 있으면 오류."""
    if not isinstance(text, str):                            # JS: encode(undefined) 등은 TypeError
        raise TypeError(f"Cannot encode {type(text).__name__}")
    enc = get_encoding()                                     # 인코딩
    try:                                                     # tiktoken 도 특수 토큰이 있으면 ValueError — 오류 글만 cn과 맞춤
        return enc.encode(text, allowed_special=set(), disallowed_special="all")   # 특수 토큰 금지
    except ValueError as e:                                  # tiktoken 오류 → gpt-tokenizer 글로
        found = _first_special(text)                         # 처음 나온 특수 토큰
        if found is not None:
            raise DisallowedSpecialTokenError(f"Disallowed special token found: {found}") from e
        raise                                                # 그 밖 오류는 그대로


def _first_special(text: str) -> Optional[str]:
    """글에서 가장 앞에 나온 특수 토큰 글(JS text.match(정규식)[0] 과 같게 — 같은 위치면 목록 순서가 앞인 것)."""
    best: Optional[tuple[int, int, str]] = None              # (위치, 목록 순서, 글)
    for order, tok in enumerate(SPECIAL_TOKENS):             # 정규식 a|b|c 는 같은 위치에서 앞 선택지가 이김
        pos = text.find(tok)                                 # 처음 위치
        if pos >= 0 and (best is None or (pos, order) < best[:2]):
            best = (pos, order, tok)
    return best[2] if best else None


def count_tokens(text: str, model_name: str = "") -> int:
    """encode(text).length 에 모델별 배수(getAdjustedTokenCountFromModel)를 적용한 값. model_name 이 비면 배수 1."""
    return get_adjusted_token_count_from_model(len(encode(text)), model_name)


# ───────────────────────── core/llm/getAdjustedTokenCount.ts ─────────────────────────
ANTHROPIC_TOKEN_MULTIPLIER = 1.23                            # claude
GEMINI_TOKEN_MULTIPLIER = 1.18                               # gemini
MISTRAL_TOKEN_MULTIPLIER = 1.26                              # *stral, mixtral


def get_adjusted_token_count_from_model(base_tokens: int, model_name: Optional[str]) -> int:
    """getAdjustedTokenCountFromModel — 모델 이름(소문자)에 따라 배수 후 올림."""
    multiplier = 1.0                                         # 기본 배수
    lower = (model_name or "").lower()                       # modelName?.toLowerCase() ?? ""
    if "claude" in lower:                                    # Anthropic
        multiplier = ANTHROPIC_TOKEN_MULTIPLIER
    elif "gemini" in lower:                                  # Google
        multiplier = GEMINI_TOKEN_MULTIPLIER
    elif "stral" in lower or "mixtral" in lower:             # Mistral 계열(mistral, codestral, devstral …)
        multiplier = MISTRAL_TOKEN_MULTIPLIER
    return math.ceil(base_tokens * multiplier)               # Math.ceil(base * 배수) — 부동소수 계산도 JS와 같음


# ───────────────────────── 모델 설정 접근(ModelConfig 또는 dict) ─────────────────────────
DEFAULT_MAX_TOKENS_RATIO = 0.35                              # 출력 예약 비율
MAX_MAX_TOKENS = 64_000                                      # 출력 예약 상한
DEFAULT_CONTEXT_LENGTH = 200_000                             # 설정에 없을 때 컨텍스트 길이


def _completion_options(model: Any) -> dict:
    """model.defaultCompletionOptions — ncn ModelConfig(default_completion_options) 또는 cn 모양 dict 모두 받음."""
    if model is None:
        return {}
    if isinstance(model, dict):                              # cn 원형 dict
        return model.get("defaultCompletionOptions") or {}
    return getattr(model, "default_completion_options", None) or {}   # ncn ModelConfig


def _model_id(model: Any) -> str:
    """model.model ?? "" — 배수 판정은 name 이 아니라 model(모델 id) 로 한다(tokenizer.ts:45,58)."""
    if model is None:
        return ""
    if isinstance(model, str):                               # 이름만 넘긴 경우
        return model
    if isinstance(model, dict):
        return model.get("model") or ""
    return getattr(model, "model", None) or ""


def get_model_context_limit(model: Any) -> int:
    """getModelContextLimit — defaultCompletionOptions.contextLength ?? 200000(모델 최상위 contextLength 는 보지 않음)."""
    v = _completion_options(model).get("contextLength")      # 값
    return DEFAULT_CONTEXT_LENGTH if v is None else v        # ?? — null/undefined 만 기본값


def get_model_max_tokens(model: Any) -> int:
    """getModelMaxTokens — maxTokens 가 없으면 ceil(min(한도×0.35, 64000))."""
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = _completion_options(model).get("maxTokens")  # 설정값
    if max_tokens is None:                                   # === undefined
        return math.ceil(min(context_limit * DEFAULT_MAX_TOKENS_RATIO, MAX_MAX_TOKENS))
    return max_tokens


# ───────────────────────── 메시지·대화 기록 세기(tokenizer.ts) ─────────────────────────
def _count_content_tokens(content: Any, model: Any) -> int:
    """countContentTokens — 문자열이면 encode, 배열이면 text 부분 + 그림 1024개씩, 그 뒤 배수."""
    if isinstance(content, str):                             # 일반 글
        return get_adjusted_token_count_from_model(len(encode(content)), _model_id(model))
    if isinstance(content, list):                            # 멀티모달 배열
        token_count = 0                                      # 합계
        for part in content:                                 # 조각마다
            if part.get("type") == "text" and part.get("text"):   # 글 조각(빈 글은 건너뜀)
                token_count += len(encode(part["text"]))
            if part.get("type") == "imageUrl":               # 그림 조각 — 대략값
                token_count += 1024
        return get_adjusted_token_count_from_model(token_count, _model_id(model))
    return 0                                                 # 그 밖(없음 등)


def _count_tool_call_function_tokens(fn: Optional[dict]) -> int:
    """countToolCallFunctionTokens — 이름 + 10(구조) + 인자."""
    if not fn:                                               # 없으면 0
        return 0
    token_count = 0
    token_count += len(encode(fn.get("name") if fn.get("name") is not None else "")) + 10   # name ?? "" + 구조 비용
    token_count += len(encode(fn.get("arguments") if fn.get("arguments") is not None else ""))   # arguments ?? ""
    return token_count


def _count_tool_output_tokens(output: Optional[list]) -> int:
    """countToolOutputTokens — 항목마다 content(있으면) + 5."""
    if not output:                                           # undefined → 0 (빈 배열도 0)
        return 0
    token_count = 0
    for item in output:                                      # 출력 항목마다
        if item.get("content"):                              # 빈 글이 아니면
            token_count += len(encode(item["content"]))
        token_count += 5                                     # 구조 비용(name 은 모델에 안 보내므로 세지 않음)
    return token_count


def _as_history_item(message_or_item: dict) -> dict:
    """cn ChatHistoryItem({message, contextItems, toolCallStates}) 모양으로. 메시지 dict 만 오면 감싼다."""
    if "message" in message_or_item and isinstance(message_or_item.get("message"), dict):   # 이미 기록 항목
        return message_or_item
    return {"message": message_or_item, "contextItems": []}  # 메시지만 → 항목으로


def _js_json_stringify(v: Any) -> Optional[str]:
    """JSON.stringify(v) 흉내(간격 없음·유니코드 그대로). undefined 는 None."""
    if v is None:                                            # undefined/null 구분 불가 — content 없음은 undefined 로 본다
        return None
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def count_chat_history_item_tokens(history_item: dict, model: Any) -> int:
    """countChatHistoryItemTokens — 항목 하나(내용 배수 + 역할 2 + 도구 호출/결과/컨텍스트 고정값). 오류면 4글자=1토큰 추정."""
    item = _as_history_item(history_item)                    # 모양 맞춤
    context_items = item.get("contextItems") or []           # cn은 항상 배열(ncn 기록에 없으면 빈 배열로 봄)
    try:
        token_count = 0
        message = item["message"]                            # 메시지
        token_count += _count_content_tokens(message.get("content"), model)   # 내용(배수 적용)
        token_count += 2                                     # 역할(대략 1~2)
        tool_calls = message.get("toolCalls")                # 도구 호출(ncn 이 OpenAI 모양 tool_calls 를 쓰면 그것도 봄)
        if tool_calls is None:
            tool_calls = message.get("tool_calls")
        if tool_calls and not item.get("toolCallStates"):    # toolCallStates 가 있으면 거기서 셈(중복 방지)
            for tc in tool_calls:
                token_count += _count_tool_call_function_tokens(tc.get("function"))
        if message.get("role") == "tool" and "content" in message:   # 도구 결과 메시지 구조 비용
            token_count += 5
        for ci in context_items:                             # 컨텍스트 항목
            token_count += len(encode(ci.get("content")))
            token_count += len(encode(ci.get("name")))
            token_count += 5                                 # 구조 비용
        for ts in item.get("toolCallStates") or []:          # 도구 호출 상태(호출 + 출력)
            token_count += _count_tool_call_function_tokens((ts.get("toolCall") or {}).get("function"))
            token_count += _count_tool_output_tokens(ts.get("output"))
        return token_count
    except Exception as error:                               # cn: logger.error 후 글자 수 추정
        _log.error("Error counting tokens for history item %s", error)
        message = item.get("message") or {}
        c = message.get("content")
        content = c if isinstance(c, str) else _js_json_stringify(c)   # 문자열 아니면 JSON.stringify
        if content is None:                                  # JS: undefined.length → TypeError(밖으로 나감)
            raise TypeError("Cannot read properties of undefined (reading 'length')") from error
        context_content = "".join(f"{ci.get('content')}{ci.get('name')}" for ci in context_items)   # item.content + item.name
        return math.ceil((js_len(content) + js_len(context_content)) / 4)   # 4글자(UTF-16 단위) = 1토큰


def count_message_tokens(message: dict, model_name: Any) -> int:
    """공개 API 이름 — count_chat_history_item_tokens 와 같다(메시지 dict 또는 기록 항목, 모델은 ModelConfig/dict/모델 id 글)."""
    return count_chat_history_item_tokens(message, model_name)


def count_chat_history_tokens(messages: list[dict], model_name: Any) -> int:
    """countChatHistoryTokens — 항목 합 + 항목 수 × 3."""
    total = 0
    for item in messages:                                    # 항목마다
        total += count_chat_history_item_tokens(item, model_name)
    total += len(messages) * 3                               # 메시지 구조 비용
    return total


def calculate_context_usage_percentage(token_count: int, model: Any) -> int:
    """calculateContextUsagePercentage — min(100, round(토큰/한도×100))."""
    limit = get_model_context_limit(model)
    return min(100, _js_round(token_count / limit * 100))


def _js_round(x: float) -> int:
    """Math.round — .5 는 위로(파이썬 round 는 짝수 쪽이라 다름)."""
    return math.floor(x + 0.5)


# ───────────────────────── 도구 정의 세기 ─────────────────────────
def _count_parameter_field_tokens(fields: Any) -> int:
    """countParameterFieldTokens — type·description 각 2+글, enum 은 (항목마다 3+글) - 3."""
    if not fields or not isinstance(fields, dict):           # 없으면 0(JS 는 객체 아닌 값이면 속성이 undefined)
        return 0
    tokens = 0
    field_type = fields.get("type")                          # 형식
    field_desc = fields.get("description")                   # 설명
    field_enum = fields.get("enum")                          # 열거
    if field_type and isinstance(field_type, str):           # 빈 글 아닌 문자열
        tokens += 2
        tokens += len(encode(field_type))
    if field_desc and isinstance(field_desc, str):
        tokens += 2
        tokens += len(encode(field_desc))
    if field_enum and isinstance(field_enum, list) and len(field_enum) > 0:
        tokens -= 3
        for e in field_enum:                                 # 항목마다
            tokens += 3
            tokens += len(encode(e)) if isinstance(e, str) else 5   # 글이 아니면 5
    return tokens


def _count_single_tool_tokens(tool: dict) -> int:
    """countSingleToolTokens — 이름 + 설명 + 최상위 properties 의 키와 필드(중첩·required 는 세지 않음)."""
    fn = tool["function"]                                    # {"name","description","parameters"}
    tokens = len(encode(fn["name"]))                         # 이름
    if fn.get("description"):                                # 설명(빈 글이면 건너뜀)
        tokens += len(encode(fn["description"]))
    params = fn.get("parameters")                            # 인자 스키마
    props = params.get("properties") if isinstance(params, dict) else None
    if props:                                                # 빈 객체도 JS 에선 참이지만 반복할 키가 없음
        for key in props:                                    # for…in — 합계라 순서 무관
            tokens += len(encode(key))
            tokens += _count_parameter_field_tokens(props[key])
    return tokens


def count_tool_definitions_tokens(tools: Optional[list[dict]]) -> int:
    """countToolDefinitionTokens — 도구 없으면 0, 있으면 12 + Σ도구 + 12."""
    if not tools:                                            # 없음/빈 배열
        return 0
    num_tokens = 12                                          # 배열 구조 비용
    for tool in tools:
        num_tokens += _count_single_tool_tokens(tool)
    return num_tokens + 12                                   # 감싸는 구조 비용


count_tool_definition_tokens = count_tool_definitions_tokens   # cn 이름(단수형) 별칭


def count_total_input_tokens(*, chat_history: list[dict], model: Any, system_message: Optional[str] = None,
                             tools: Optional[list[dict]] = None) -> int:
    """countTotalInputTokens — 기록 + (기록에 system 이 없을 때) 시스템 메시지+4 + 도구 정의. 시스템 메시지는 배수 없음."""
    total = count_chat_history_tokens(chat_history, model)   # 기록
    if system_message:                                       # 빈 글이면 건너뜀
        has_system = any(_as_history_item(i)["message"].get("role") == "system" for i in chat_history)
        if not has_system:                                   # 기록에 system 이 없을 때만
            total += len(encode(system_message))             # (오류는 잡지 않음 — cn과 같음)
            total += 4                                       # 구조 비용
    if tools:                                                # 도구가 있으면
        total += count_tool_definitions_tokens(tools)
    return total


def validate_context_length(*, chat_history: list[dict], model: Any, safety_buffer: int = 0,
                            system_message: Optional[str] = None, tools: Optional[list[dict]] = None) -> dict:
    """validateContextLength — 입력 + 출력 예약(maxTokens 또는 ceil(한도×0.35), 64000 상한 없음) + 버퍼 > 한도 이면 오류 글.
    반환: {"isValid", "error"?, "inputTokens", "contextLimit", "maxTokens"} (cn 반환 키 그대로)."""
    input_tokens = count_total_input_tokens(chat_history=chat_history, system_message=system_message, tools=tools, model=model)
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = _completion_options(model).get("maxTokens") or 0   # maxTokens || 0
    reserved_for_output = max_tokens if max_tokens > 0 else math.ceil(context_limit * 0.35)   # 출력 예약
    total_required = input_tokens + reserved_for_output + safety_buffer   # 필요량
    if total_required > context_limit:                       # 넘으면 cn 원문 오류 글
        buf = f" + buffer ({safety_buffer})" if safety_buffer > 0 else ""   # 버퍼 표기(이 값만 쉼표 없음 — 원문 그대로)
        return {
            "isValid": False,
            "error": (f"Context length exceeded: input ({to_locale_string(input_tokens)}) + max_tokens "
                      f"({to_locale_string(reserved_for_output)}){buf} = {to_locale_string(total_required)} "
                      f"> context_limit ({to_locale_string(context_limit)})"),
            "inputTokens": input_tokens,
            "contextLimit": context_limit,
            "maxTokens": reserved_for_output,
        }
    return {"isValid": True, "inputTokens": input_tokens, "contextLimit": context_limit, "maxTokens": reserved_for_output}


# ───────────────────────── 자동 압축 판단(compaction.ts:18-20, 266-315) ─────────────────────────
AUTO_COMPACT_BUFFER_CAP = 15_000                             # 버퍼 상한
AUTO_COMPACT_BUFFER_RATIO = 0.8                              # 비율


def get_auto_compact_message(model: Any) -> str:
    """getAutoCompactMessage — 압축 안내 글(한도를 K 단위, toFixed(0))."""
    limit = get_model_context_limit(model)
    return f"Approaching context limit ({_js_to_fixed0(limit / 1000)}K tokens). Auto-compacting chat history..."


def _js_to_fixed0(x: float) -> str:
    """Number.prototype.toFixed(0) — 반올림은 .5 에서 0 에서 먼 쪽(양수 기준 위로)."""
    return str(math.floor(x + 0.5)) if x >= 0 else "-" + str(math.floor(-x + 0.5))


def compaction_threshold(model: Any) -> int:
    """압축 임계값 = 한도 - maxTokens - min(max(maxTokens, ceil(0.2×(한도-maxTokens))), 15000). 0 이하이면 cn 원문 오류."""
    context_limit = get_model_context_limit(model)           # 한도
    max_tokens = get_model_max_tokens(model)                 # 출력 예약(64000 상한 있음)
    ratio_buffer = math.ceil((1 - AUTO_COMPACT_BUFFER_RATIO) * (context_limit - max_tokens))   # (1-0.8) 도 JS 와 같은 부동소수
    safe_buffer = max(max_tokens, ratio_buffer)              # 큰 쪽
    buffer = min(safe_buffer, AUTO_COMPACT_BUFFER_CAP)       # 상한
    threshold = context_limit - max_tokens - buffer          # 임계
    if threshold <= 0:                                       # 설정 오류
        raise ValueError("max_tokens is larger than context_length, which should not be possible. Please check your configuration.")
    return threshold


def should_auto_compact(*, chat_history: list[dict], model: Any, system_message: Optional[str] = None,
                        tools: Optional[list[dict]] = None) -> bool:
    """shouldAutoCompact — 총 입력 토큰 >= 임계값 이면 참(cn은 입력을 먼저 센 뒤 임계값을 검사 — 순서 그대로)."""
    input_tokens = count_total_input_tokens(chat_history=chat_history, system_message=system_message, tools=tools, model=model)
    threshold = compaction_threshold(model)                  # 임계(오류 가능)
    if tools:                                                # cn 은 로그용으로 도구·시스템 토큰을 다시 셈(시스템 메시지 encode 오류가 여기서도 날 수 있음)
        count_tool_definitions_tokens(tools)
    if system_message:
        len(encode(system_message))
    return input_tokens >= threshold


should_compact = should_auto_compact                         # 공개 API 짧은 이름
