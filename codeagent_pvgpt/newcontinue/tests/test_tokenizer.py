# tests/test_tokenizer.py — ncn/tokenizer.py 시험. cn util/tokenizer.test.ts·util/tokenizer.contextValidation.test.ts·core/llm/getAdjustedTokenCount.test.ts 대표 사례
# cn 시험처럼 encode 를 "4글자=1토큰" 가짜로 바꾼 시험 + 실제 인코딩 값 시험(값은 gpt-tokenizer 3.0.1 을 node 로 직접 돌려 얻은 것)
from __future__ import annotations                           # 타입 주석 지연 평가

import math                                                  # 올림

import pytest                                                # 시험 도구

from ncn import tokenizer as T                               # 대상


def _model(context_length=None, max_tokens=None, model="test") -> dict:  # 시험용 모델 dict 를 만드는 함수
    """cn 시험의 createModel — defaultCompletionOptions 만 채운 dict."""
    dco = {}                                                 # 완성 옵션
    if context_length is not None:                          # 컨텍스트 길이가 주어지면
        dco["contextLength"] = context_length               # 완성 옵션에 넣음
    if max_tokens is not None:                              # 최대 토큰이 주어지면
        dco["maxTokens"] = max_tokens                       # 완성 옵션에 넣음
    return {"name": "test-model", "model": model, "provider": "openai", "defaultCompletionOptions": dco}  # cn 모양 모델 dict 반환


@pytest.fixture                                             # pytest 픽스처
def fake_encode(monkeypatch):                               # encode 를 가짜로 바꾸는 픽스처
    """vi.mock("gpt-tokenizer") 와 같음: encode = 길이 ceil(len/4) 배열."""
    monkeypatch.setattr(T, "encode", lambda text: [0] * math.ceil(len(text) / 4))  # encode 를 "4글자=1토큰" 가짜로 교체


# ───────────────────────── 실제 인코딩(gpt-tokenizer 3.0.1 결과와 비교) ─────────────────────────
@pytest.mark.parametrize("text,expected", [                 # 실제 인코딩 결과 비교 시험(여러 입력)
    ("", []),                                               # 빈 글은 토큰 없음
    ("hello world", [24912, 2375]),                         # 일반 영어 문장
    ("\ufeffusing", [5416, 123, 1846]),                       # BOM 토큰을 못 찾는 gpt-tokenizer 특이점
    (" \ufeff", [71280]),                                   # 공백 + BOM
    ("\ufeff\ufeff", [5416, 123, 5416, 123]),               # BOM 두 개
    ("#", [2]),                                             # 기호 하나
])
def test_encode_matches_gpt_tokenizer(text, expected):      # 인코딩 결과 시험 함수
    """o200k 순위표 + gpt-tokenizer 정규식 + BOM 특이점까지 같은 토큰."""
    assert T.encode(text) == expected                       # gpt-tokenizer 값과 같은지 확인


def test_encode_special_token_error():                      # 특수 토큰 오류 시험
    """특수 토큰 글은 gpt-tokenizer 처럼 오류(글도 같게) — tiktoken 기본보다 많은 8개."""
    with pytest.raises(ValueError, match=r"^Disallowed special token found: <\|endoftext\|>$"):  # 오류 문구가 cn 과 같은지 확인
        T.encode("a <|endoftext|> b")                       # 특수 토큰이 든 글 인코딩 시도
    with pytest.raises(ValueError, match=r"^Disallowed special token found: <\|im_start\|>$"):  # 처음 나온 특수 토큰이 오류 글에 나옴
        T.encode("x<|im_start|>y<|endoftext|>")              # 앞에 나온 것으로


def test_count_tokens_multiplier():                         # 모델 배수 적용 시험
    """count_tokens = encode 길이 × 모델 배수(올림)."""
    assert T.count_tokens("hello world") == 2               # 배수 없는 토큰 수
    assert T.count_tokens("hello world", "claude-3") == math.ceil(2 * 1.23)  # claude 배수 적용 후 올림


def test_offline_data_file():                               # 오프라인 데이터 파일 시험
    """폐쇄망: 순위표가 패키지 안에 있고 해시가 원본과 같다."""
    import hashlib                                          # 해시 계산용 가져오기
    assert T.DATA_FILE.exists()                             # 순위표 파일이 있는지
    assert hashlib.sha256(T.DATA_FILE.read_bytes()).hexdigest() == T.DATA_SHA256  # 해시가 원본과 같은지


# ───────────────────────── getAdjustedTokenCount.test.ts ─────────────────────────
@pytest.mark.parametrize("name,base,expected", [            # 모델 이름별 배수 시험(여러 입력)
    ("gpt-4", 100, 100), ("claude-3-5-sonnet", 100, 123), ("CLAUDE", 100, 123), ("gemini-pro", 100, 118),  # gpt 는 1배, claude 는 1.23배(대소문자 무시), gemini 는 1.18배
    ("mistral-large", 100, 126), ("codestral", 100, 126), ("mixtral-8x7b", 100, 126), ("devstral", 10, 13),  # mistral 계열은 1.26배, 올림 확인
    ("", 7, 7), (None, 7, 7),                               # 빈 이름·None 은 배수 없음
])
def test_adjusted_token_count(name, base, expected):        # 배수 적용 시험 함수
    assert T.get_adjusted_token_count_from_model(base, name) == expected  # 기대값과 같은지 확인


# ───────────────────────── 한도 ─────────────────────────
def test_context_limit_and_max_tokens():                    # 컨텍스트 한도·출력 예약 시험
    """getModelContextLimit / getModelMaxTokens."""
    assert T.get_model_context_limit(_model(8192)) == 8192  # 지정한 한도
    assert T.get_model_context_limit(_model()) == T.DEFAULT_CONTEXT_LENGTH  # 지정이 없으면 기본 한도
    assert T.get_model_max_tokens(_model()) == 64000          # min(200000*0.35, 64000) — 기본 출력 예약은 64000 상한
    assert T.get_model_max_tokens(_model(32768)) == 11469     # ceil(11468.8) — 한도 32768 의 35% 를 올림
    assert T.get_model_max_tokens(_model(1000, 0)) == 0       # 0 은 그대로(undefined 만 기본값)
    top_level_only = {"name": "m", "model": "x", "provider": "o", "contextLength": 1000}   # 최상위 contextLength 는 무시
    assert T.get_model_context_limit(top_level_only) == 200000  # 기본 한도가 적용됨


def test_model_config_object_accepted():                    # ModelConfig 객체 입력 시험
    """ncn ModelConfig 객체도 받는다."""
    from ncn.config.loader import ModelConfig               # ModelConfig 가져오기
    m = ModelConfig.from_raw(_model(4096, 100, model="claude-x"))  # dict 에서 ModelConfig 생성
    assert T.get_model_context_limit(m) == 4096 and T.get_model_max_tokens(m) == 100  # 한도·출력 예약을 객체에서도 읽음


# ───────────────────────── tokenizer.test.ts(가짜 encode) ─────────────────────────
def test_history_item_string(fake_encode):                  # 문자열 내용 토큰 시험
    assert T.count_chat_history_item_tokens({"message": {"role": "user", "content": "Hello world"}, "contextItems": []}, _model()) == 5  # 내용 토큰 + 역할 2 = 5


def test_history_item_array(fake_encode):                   # 배열 내용 토큰 시험
    item = {"message": {"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "imageUrl", "imageUrl": {"url": "t.jpg"}}]},  # 글 조각과 그림 조각이 섞인 항목
            "contextItems": []}                             # (항목 이어짐)
    assert T.count_chat_history_item_tokens(item, _model()) == 1028  # 글 + 그림 1024 + 역할 = 1028


def test_history_item_context_items(fake_encode):           # 컨텍스트 항목 토큰 시험
    item = {"message": {"role": "user", "content": "Hello"},  # 컨텍스트 항목이 붙은 항목
            "contextItems": [{"content": "Context content", "name": "Test context", "description": "d"}]}  # (항목 이어짐)
    assert T.count_chat_history_item_tokens(item, _model()) == 16  # 내용·역할·컨텍스트 합계 확인


def test_history_item_tool_calls_and_states(fake_encode):   # 도구 호출·상태 토큰 시험
    """toolCalls 는 toolCallStates 가 없을 때만, 도구 메시지 +5, 출력 항목마다 +5."""
    fn = {"name": "test_function", "arguments": '{"param": "value"}'}   # 4+10 + 5 — 함수 정의(이름 4 + 구조 10 + 인자 5)
    msg = {"role": "assistant", "content": "I'll help you", "toolCalls": [{"id": "c1", "type": "function", "function": fn}]}  # 도구 호출이 있는 assistant 메시지
    assert T.count_chat_history_item_tokens({"message": msg, "contextItems": []}, _model()) == 4 + 2 + (4 + 10 + 5)  # toolCallStates 가 없으면 toolCalls 로 센다
    with_states = {"message": msg, "contextItems": [], "toolCallStates": [  # toolCallStates 가 있는 항목
        {"toolCall": {"function": fn}, "output": [{"content": "12345678", "name": "x"}, {"name": "y"}]}]}  # (출력 항목 이어짐)
    assert T.count_chat_history_item_tokens(with_states, _model()) == 4 + 2 + (4 + 10 + 5) + (2 + 5) + 5  # 상태가 있으면 거기서 센다
    tool_msg = {"message": {"role": "tool", "content": "ok", "toolCallId": "c1"}, "contextItems": []}  # 도구 결과 메시지
    assert T.count_chat_history_item_tokens(tool_msg, _model()) == 1 + 2 + 5  # 도구 메시지 구조 비용 +5


def test_history_item_error_fallback():                     # 오류 시 글자 수 추정 시험
    """특수 토큰으로 encode 가 실패하면 (글자 수 + 컨텍스트 글자 수)/4 올림 — 글자 수는 UTF-16 단위."""
    item = {"message": {"role": "user", "content": "<|endoftext|>😀"}, "contextItems": [{"content": "ab", "name": "c"}]}  # 특수 토큰이 든 내용(이모지는 UTF-16 두 글자)
    assert T.count_chat_history_item_tokens(item, _model()) == math.ceil((13 + 2 + 3) / 4)  # 글자 수 / 4 올림 확인


def test_multiplier_applies_only_to_content(fake_encode):   # 배수는 내용에만 적용되는지 시험
    """배수는 content 에만 — 역할 2 는 그대로."""
    item = {"message": {"role": "user", "content": "x" * 400}, "contextItems": []}  # 긴 내용 항목
    assert T.count_chat_history_item_tokens(item, _model(model="claude-3")) == 123 + 2  # 배수 적용 내용 + 역할 2


def test_chat_history_tokens(fake_encode):                  # 대화 기록 전체 토큰 시험
    hist = [{"message": {"role": "system", "content": "You are helpful"}, "contextItems": []},  # system 항목
            {"message": {"role": "user", "content": "Hello"}, "contextItems": []},  # user 항목
            {"message": {"role": "assistant", "content": "Hi there"}, "contextItems": []}]  # assistant 항목
    assert T.count_chat_history_tokens(hist, _model()) == (4 + 2) + (2 + 2) + (2 + 2) + 9  # 항목 합 + 항목 수 × 3
    assert T.count_chat_history_tokens([], _model()) == 0   # 빈 기록은 0


def test_plain_message_dict_accepted(fake_encode):          # 메시지 dict 만 줘도 되는지 시험
    """count_message_tokens 는 메시지 dict 만 줘도 된다."""
    assert T.count_message_tokens({"role": "user", "content": "Hello world"}, "test") == 5  # 토큰 수 확인


def test_context_usage_percentage():                        # 컨텍스트 사용률 시험
    m = _model(1000)                                        # 한도 1000 인 모델
    assert T.calculate_context_usage_percentage(800, m) == 80  # 800/1000 = 80%
    assert T.calculate_context_usage_percentage(1200, m) == 100  # 100% 를 넘으면 100 으로 제한
    assert T.calculate_context_usage_percentage(5, m) == 1    # Math.round(0.5) = 1 — 0.5 는 위로 올림


def test_tool_definition_tokens(fake_encode):               # 도구 정의 토큰 시험
    """12 + 이름 + 설명 + (키 + type 2+ + description 2+ + enum) + 12."""
    tools = [{"type": "function", "function": {"name": "Read", "description": "Read a file", "parameters": {  # 도구 정의 하나
        "type": "object", "required": ["filepath"],         # 인자 스키마 형식·필수 목록
        "properties": {"filepath": {"type": "string", "description": "path"}, "mode": {"type": "string", "enum": ["a", "bb", 3]}}}}}]  # properties(필드 두 개, enum 포함)
    expected = 12 + 1 + 3 + (2 + 2 + 2 + 2 + 1) + (1 + 2 + 2 + (-3 + 3 + 1 + 3 + 1 + 3 + 5)) + 12  # 기대 합계 계산
    assert T.count_tool_definitions_tokens(tools) == expected  # 도구 정의 토큰 수 확인
    assert T.count_tool_definitions_tokens([]) == 0         # 도구가 없으면 0


def test_total_input_tokens(fake_encode):                   # 입력 토큰 총합 시험
    """시스템 메시지는 기록에 system 이 없을 때만 +encode+4."""
    hist = [{"message": {"role": "user", "content": "Hello"}, "contextItems": []}]  # user 항목만 있는 기록
    base = T.count_chat_history_tokens(hist, _model())      # 기록 토큰 수
    assert T.count_total_input_tokens(chat_history=hist, model=_model(), system_message="abcdefgh") == base + 2 + 4  # system 이 없으면 시스템 메시지 + 4 가 더해짐
    hist2 = [{"message": {"role": "system", "content": "s"}, "contextItems": []}] + hist  # system 이 이미 있는 기록
    assert T.count_total_input_tokens(chat_history=hist2, model=_model(), system_message="abcdefgh") == T.count_chat_history_tokens(hist2, _model())  # 이 경우 시스템 메시지를 따로 더하지 않음


# ───────────────────────── validateContextLength(tokenizer.contextValidation.test.ts) ─────────────────────────
def test_validate_context_length(fake_encode):              # 컨텍스트 길이 검증 시험
    hist = [{"message": {"role": "user", "content": "x" * 4000}, "contextItems": []}]   # 1000+2+3 — 입력이 큰 기록
    ok = T.validate_context_length(chat_history=hist, model=_model(4000, 1000))  # 한도 안이면
    assert ok == {"isValid": True, "inputTokens": 1005, "contextLimit": 4000, "maxTokens": 1000}  # 성공 결과 확인
    bad = T.validate_context_length(chat_history=hist, model=_model(2000, 1000), safety_buffer=100)  # 버퍼를 두고 한도를 넘기면
    assert bad["isValid"] is False                          # 유효하지 않음
    assert bad["error"] == "Context length exceeded: input (1,005) + max_tokens (1,000) + buffer (100) = 2,105 > context_limit (2,000)"  # 오류 글이 cn 원문과 같은지
    no_max = T.validate_context_length(chat_history=hist, model=_model(1500))   # 35% 예약 = 525, 버퍼 0 이면 표기 없음
    assert no_max["error"] == "Context length exceeded: input (1,005) + max_tokens (525) = 1,530 > context_limit (1,500)"  # maxTokens 가 없을 때 오류 글
    big = T.validate_context_length(chat_history=[], model=_model())  # 기록이 비었을 때
    assert big["maxTokens"] == 70000                         # 200000 × 0.35 (getModelMaxTokens 와 다름)


# ───────────────────────── shouldAutoCompact(tokenizer.test.ts) ─────────────────────────
def _hist(tokens: int) -> list[dict]:                       # 토큰 수만큼 긴 기록을 만드는 도우미 함수
    return [{"message": {"role": "user", "content": "x" * (tokens * 4)}, "contextItems": []}]  # user 항목 하나 반환


@pytest.mark.parametrize("ctx,mx,tokens,expected", [        # 자동 압축 판단 시험(여러 입력)
    (1000, 200, 650, True), (1000, 200, 500, False), (1000, None, 350, True), (1000, None, 250, False),  # 한도·출력 예약·토큰 수별 기대 결과
    (200, 50, 110, True), (1000, 0, 850, True), (200_000, 4_000, 182_000, True),  # (기대 결과 이어짐)
])
def test_should_auto_compact(fake_encode, ctx, mx, tokens, expected):  # 자동 압축 시험 함수
    assert T.should_auto_compact(chat_history=_hist(tokens), model=_model(ctx, mx)) is expected  # 기대 결과와 같은지 확인


def test_compaction_threshold_values():                     # 압축 임계값 시험
    assert T.compaction_threshold(_model(1000, 200)) == 600  # 한도 1000, 출력 200
    assert T.compaction_threshold(_model(1000)) == 300      # 출력 예약 미지정
    assert T.compaction_threshold(_model(200_000, 4_000)) == 181_000  # 한도 200000, 출력 4000
    assert T.compaction_threshold(_model()) == 121_000      # 모델 기본값
    assert T.compaction_threshold(_model(32768)) == 9830    # 한도 32768


@pytest.mark.parametrize("ctx,mx", [(1000, 800), (1000, 1000), (1000, 1200)])  # 임계값 오류 시험(출력 예약이 한도 이상)
def test_should_auto_compact_errors(fake_encode, ctx, mx):  # 오류 시험 함수
    with pytest.raises(ValueError, match="^max_tokens is larger than context_length, which should not be possible. Please check your configuration.$"):  # 오류 문구 확인
        T.should_auto_compact(chat_history=_hist(1), model=_model(ctx, mx))  # 압축 판단 호출


def test_auto_compact_message():                            # 자동 압축 안내 글 시험
    assert T.get_auto_compact_message(_model(32768)) == "Approaching context limit (33K tokens). Auto-compacting chat history..."  # K 단위 안내 글 확인
