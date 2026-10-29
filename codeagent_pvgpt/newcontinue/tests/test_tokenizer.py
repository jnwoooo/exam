# tests/test_tokenizer.py — ncn/tokenizer.py 시험. cn util/tokenizer.test.ts·util/tokenizer.contextValidation.test.ts·core/llm/getAdjustedTokenCount.test.ts 대표 사례
# cn 시험처럼 encode 를 "4글자=1토큰" 가짜로 바꾼 시험 + 실제 인코딩 값 시험(값은 gpt-tokenizer 3.0.1 을 node 로 직접 돌려 얻은 것)
from __future__ import annotations                           # 타입 주석 지연 평가

import math                                                  # 올림

import pytest                                                # 시험 도구

from ncn import tokenizer as T                               # 대상


def _model(context_length=None, max_tokens=None, model="test") -> dict:
    """cn 시험의 createModel — defaultCompletionOptions 만 채운 dict."""
    dco = {}                                                 # 완성 옵션
    if context_length is not None:
        dco["contextLength"] = context_length
    if max_tokens is not None:
        dco["maxTokens"] = max_tokens
    return {"name": "test-model", "model": model, "provider": "openai", "defaultCompletionOptions": dco}


@pytest.fixture
def fake_encode(monkeypatch):
    """vi.mock("gpt-tokenizer") 와 같음: encode = 길이 ceil(len/4) 배열."""
    monkeypatch.setattr(T, "encode", lambda text: [0] * math.ceil(len(text) / 4))


# ───────────────────────── 실제 인코딩(gpt-tokenizer 3.0.1 결과와 비교) ─────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("", []),
    ("hello world", [24912, 2375]),
    ("\ufeffusing", [5416, 123, 1846]),                       # BOM 토큰을 못 찾는 gpt-tokenizer 특이점
    (" \ufeff", [71280]),
    ("\ufeff\ufeff", [5416, 123, 5416, 123]),
    ("#", [2]),
])
def test_encode_matches_gpt_tokenizer(text, expected):
    """o200k 순위표 + gpt-tokenizer 정규식 + BOM 특이점까지 같은 토큰."""
    assert T.encode(text) == expected


def test_encode_special_token_error():
    """특수 토큰 글은 gpt-tokenizer 처럼 오류(글도 같게) — tiktoken 기본보다 많은 8개."""
    with pytest.raises(ValueError, match=r"^Disallowed special token found: <\|endoftext\|>$"):
        T.encode("a <|endoftext|> b")
    with pytest.raises(ValueError, match=r"^Disallowed special token found: <\|im_start\|>$"):
        T.encode("x<|im_start|>y<|endoftext|>")              # 앞에 나온 것으로


def test_count_tokens_multiplier():
    """count_tokens = encode 길이 × 모델 배수(올림)."""
    assert T.count_tokens("hello world") == 2
    assert T.count_tokens("hello world", "claude-3") == math.ceil(2 * 1.23)


def test_offline_data_file():
    """폐쇄망: 순위표가 패키지 안에 있고 해시가 원본과 같다."""
    import hashlib
    assert T.DATA_FILE.exists()
    assert hashlib.sha256(T.DATA_FILE.read_bytes()).hexdigest() == T.DATA_SHA256


# ───────────────────────── getAdjustedTokenCount.test.ts ─────────────────────────
@pytest.mark.parametrize("name,base,expected", [
    ("gpt-4", 100, 100), ("claude-3-5-sonnet", 100, 123), ("CLAUDE", 100, 123), ("gemini-pro", 100, 118),
    ("mistral-large", 100, 126), ("codestral", 100, 126), ("mixtral-8x7b", 100, 126), ("devstral", 10, 13),
    ("", 7, 7), (None, 7, 7),
])
def test_adjusted_token_count(name, base, expected):
    assert T.get_adjusted_token_count_from_model(base, name) == expected


# ───────────────────────── 한도 ─────────────────────────
def test_context_limit_and_max_tokens():
    """getModelContextLimit / getModelMaxTokens."""
    assert T.get_model_context_limit(_model(8192)) == 8192
    assert T.get_model_context_limit(_model()) == T.DEFAULT_CONTEXT_LENGTH
    assert T.get_model_max_tokens(_model()) == 64000          # min(200000*0.35, 64000)
    assert T.get_model_max_tokens(_model(32768)) == 11469     # ceil(11468.8)
    assert T.get_model_max_tokens(_model(1000, 0)) == 0       # 0 은 그대로(undefined 만 기본값)
    top_level_only = {"name": "m", "model": "x", "provider": "o", "contextLength": 1000}   # 최상위 contextLength 는 무시
    assert T.get_model_context_limit(top_level_only) == 200000


def test_model_config_object_accepted():
    """ncn ModelConfig 객체도 받는다."""
    from ncn.config.loader import ModelConfig
    m = ModelConfig.from_raw(_model(4096, 100, model="claude-x"))
    assert T.get_model_context_limit(m) == 4096 and T.get_model_max_tokens(m) == 100


# ───────────────────────── tokenizer.test.ts(가짜 encode) ─────────────────────────
def test_history_item_string(fake_encode):
    assert T.count_chat_history_item_tokens({"message": {"role": "user", "content": "Hello world"}, "contextItems": []}, _model()) == 5


def test_history_item_array(fake_encode):
    item = {"message": {"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "imageUrl", "imageUrl": {"url": "t.jpg"}}]},
            "contextItems": []}
    assert T.count_chat_history_item_tokens(item, _model()) == 1028


def test_history_item_context_items(fake_encode):
    item = {"message": {"role": "user", "content": "Hello"},
            "contextItems": [{"content": "Context content", "name": "Test context", "description": "d"}]}
    assert T.count_chat_history_item_tokens(item, _model()) == 16


def test_history_item_tool_calls_and_states(fake_encode):
    """toolCalls 는 toolCallStates 가 없을 때만, 도구 메시지 +5, 출력 항목마다 +5."""
    fn = {"name": "test_function", "arguments": '{"param": "value"}'}   # 4+10 + 5
    msg = {"role": "assistant", "content": "I'll help you", "toolCalls": [{"id": "c1", "type": "function", "function": fn}]}
    assert T.count_chat_history_item_tokens({"message": msg, "contextItems": []}, _model()) == 4 + 2 + (4 + 10 + 5)
    with_states = {"message": msg, "contextItems": [], "toolCallStates": [
        {"toolCall": {"function": fn}, "output": [{"content": "12345678", "name": "x"}, {"name": "y"}]}]}
    assert T.count_chat_history_item_tokens(with_states, _model()) == 4 + 2 + (4 + 10 + 5) + (2 + 5) + 5
    tool_msg = {"message": {"role": "tool", "content": "ok", "toolCallId": "c1"}, "contextItems": []}
    assert T.count_chat_history_item_tokens(tool_msg, _model()) == 1 + 2 + 5


def test_history_item_error_fallback():
    """특수 토큰으로 encode 가 실패하면 (글자 수 + 컨텍스트 글자 수)/4 올림 — 글자 수는 UTF-16 단위."""
    item = {"message": {"role": "user", "content": "<|endoftext|>😀"}, "contextItems": [{"content": "ab", "name": "c"}]}
    assert T.count_chat_history_item_tokens(item, _model()) == math.ceil((13 + 2 + 3) / 4)


def test_multiplier_applies_only_to_content(fake_encode):
    """배수는 content 에만 — 역할 2 는 그대로."""
    item = {"message": {"role": "user", "content": "x" * 400}, "contextItems": []}
    assert T.count_chat_history_item_tokens(item, _model(model="claude-3")) == 123 + 2


def test_chat_history_tokens(fake_encode):
    hist = [{"message": {"role": "system", "content": "You are helpful"}, "contextItems": []},
            {"message": {"role": "user", "content": "Hello"}, "contextItems": []},
            {"message": {"role": "assistant", "content": "Hi there"}, "contextItems": []}]
    assert T.count_chat_history_tokens(hist, _model()) == (4 + 2) + (2 + 2) + (2 + 2) + 9
    assert T.count_chat_history_tokens([], _model()) == 0


def test_plain_message_dict_accepted(fake_encode):
    """count_message_tokens 는 메시지 dict 만 줘도 된다."""
    assert T.count_message_tokens({"role": "user", "content": "Hello world"}, "test") == 5


def test_context_usage_percentage():
    m = _model(1000)
    assert T.calculate_context_usage_percentage(800, m) == 80
    assert T.calculate_context_usage_percentage(1200, m) == 100
    assert T.calculate_context_usage_percentage(5, m) == 1    # Math.round(0.5) = 1


def test_tool_definition_tokens(fake_encode):
    """12 + 이름 + 설명 + (키 + type 2+ + description 2+ + enum) + 12."""
    tools = [{"type": "function", "function": {"name": "Read", "description": "Read a file", "parameters": {
        "type": "object", "required": ["filepath"],
        "properties": {"filepath": {"type": "string", "description": "path"}, "mode": {"type": "string", "enum": ["a", "bb", 3]}}}}}]
    expected = 12 + 1 + 3 + (2 + 2 + 2 + 2 + 1) + (1 + 2 + 2 + (-3 + 3 + 1 + 3 + 1 + 3 + 5)) + 12
    assert T.count_tool_definitions_tokens(tools) == expected
    assert T.count_tool_definitions_tokens([]) == 0


def test_total_input_tokens(fake_encode):
    """시스템 메시지는 기록에 system 이 없을 때만 +encode+4."""
    hist = [{"message": {"role": "user", "content": "Hello"}, "contextItems": []}]
    base = T.count_chat_history_tokens(hist, _model())
    assert T.count_total_input_tokens(chat_history=hist, model=_model(), system_message="abcdefgh") == base + 2 + 4
    hist2 = [{"message": {"role": "system", "content": "s"}, "contextItems": []}] + hist
    assert T.count_total_input_tokens(chat_history=hist2, model=_model(), system_message="abcdefgh") == T.count_chat_history_tokens(hist2, _model())


# ───────────────────────── validateContextLength(tokenizer.contextValidation.test.ts) ─────────────────────────
def test_validate_context_length(fake_encode):
    hist = [{"message": {"role": "user", "content": "x" * 4000}, "contextItems": []}]   # 1000+2+3
    ok = T.validate_context_length(chat_history=hist, model=_model(4000, 1000))
    assert ok == {"isValid": True, "inputTokens": 1005, "contextLimit": 4000, "maxTokens": 1000}
    bad = T.validate_context_length(chat_history=hist, model=_model(2000, 1000), safety_buffer=100)
    assert bad["isValid"] is False
    assert bad["error"] == "Context length exceeded: input (1,005) + max_tokens (1,000) + buffer (100) = 2,105 > context_limit (2,000)"
    no_max = T.validate_context_length(chat_history=hist, model=_model(1500))   # 35% 예약 = 525, 버퍼 0 이면 표기 없음
    assert no_max["error"] == "Context length exceeded: input (1,005) + max_tokens (525) = 1,530 > context_limit (1,500)"
    big = T.validate_context_length(chat_history=[], model=_model())
    assert big["maxTokens"] == 70000                         # 200000 × 0.35 (getModelMaxTokens 와 다름)


# ───────────────────────── shouldAutoCompact(tokenizer.test.ts) ─────────────────────────
def _hist(tokens: int) -> list[dict]:
    return [{"message": {"role": "user", "content": "x" * (tokens * 4)}, "contextItems": []}]


@pytest.mark.parametrize("ctx,mx,tokens,expected", [
    (1000, 200, 650, True), (1000, 200, 500, False), (1000, None, 350, True), (1000, None, 250, False),
    (200, 50, 110, True), (1000, 0, 850, True), (200_000, 4_000, 182_000, True),
])
def test_should_auto_compact(fake_encode, ctx, mx, tokens, expected):
    assert T.should_auto_compact(chat_history=_hist(tokens), model=_model(ctx, mx)) is expected


def test_compaction_threshold_values():
    assert T.compaction_threshold(_model(1000, 200)) == 600
    assert T.compaction_threshold(_model(1000)) == 300
    assert T.compaction_threshold(_model(200_000, 4_000)) == 181_000
    assert T.compaction_threshold(_model()) == 121_000
    assert T.compaction_threshold(_model(32768)) == 9830


@pytest.mark.parametrize("ctx,mx", [(1000, 800), (1000, 1000), (1000, 1200)])
def test_should_auto_compact_errors(fake_encode, ctx, mx):
    with pytest.raises(ValueError, match="^max_tokens is larger than context_length, which should not be possible. Please check your configuration.$"):
        T.should_auto_compact(chat_history=_hist(1), model=_model(ctx, mx))


def test_auto_compact_message():
    assert T.get_auto_compact_message(_model(32768)) == "Approaching context limit (33K tokens). Auto-compacting chat history..."
