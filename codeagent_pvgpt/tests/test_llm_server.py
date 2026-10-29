# tests/test_llm_server.py — llm_server(OpenAI 호환 → Ollama)를 가짜 Ollama 앞에 띄우고 check_compat 3가지를 돌린다
# 네이티브 도구 방식과 프롬프트 방식(도구 미지원 모델) 둘 다 확인. docker·DB 필요 없음
import threading, time                                       # 서버 스레드

import pytest                                                # 테스트
import uvicorn                                               # 서버 실행

from llm_server import convert as cv                         # 변환 함수
from llm_server.check_compat import run_checks               # 호환성 확인
from tools.fake_ollama import create_app as fake_ollama      # 가짜 Ollama


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError(port)


@pytest.fixture(scope="module")
def servers(monkeypatch_module):
    from llm_server.app import create_app as llm_app
    started = [serve(fake_ollama(no_tools=False), 18434), serve(fake_ollama(no_tools=True), 18435)]   # 도구 지원 / 미지원
    monkeypatch_module.setenv("LLM_SERVER_BACKEND_URL", "http://127.0.0.1:18434")
    monkeypatch_module.setenv("LLM_SERVER_TOOL_MODE", "native")
    started.append(serve(llm_app(), 18001))                  # 네이티브 방식 llm_server
    monkeypatch_module.setenv("LLM_SERVER_BACKEND_URL", "http://127.0.0.1:18435")
    started.append(serve(llm_app(), 18002))                  # 도구 미지원 모델 + native → 오류 확인용
    monkeypatch_module.setenv("LLM_SERVER_TOOL_MODE", "prompt")
    started.append(serve(llm_app(), 18003))                  # 도구 미지원 모델 + prompt 방식
    yield
    for s in started:
        s.should_exit = True


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()                                # 모듈 단위 환경변수 설정
    yield mp
    mp.undo()


def test_convert_tool_history_roundtrip():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "날씨"}]},           # content가 목록인 경우
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"서울"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "맑음"}]
    native = cv.to_ollama_messages(msgs, "native", [])
    assert native[1]["tool_calls"][0]["function"]["arguments"] == {"city": "서울"}   # 문자열 → dict
    assert native[2] == {"role": "tool", "content": "맑음", "tool_name": "get_weather"}   # id → 이름
    prompt = cv.to_ollama_messages(msgs, "prompt", [{"type": "function", "function": {"name": "get_weather"}}])
    assert prompt[0]["role"] == "system" and "<tool_call>" in prompt[0]["content"]   # 도구 설명 주입
    assert '<tool_result name="get_weather">' in prompt[3]["content"]
    text, calls = cv.extract_tool_calls('앞<tool_call>{"name":"a","arguments":{"x":1}}</tool_call>')
    assert text == "앞" and calls == [{"name": "a", "arguments": {"x": 1}}]


def test_native_mode_passes_all_checks(servers):
    results = run_checks("http://127.0.0.1:18001/v1", "gemma4-26b-32k:latest")
    assert all(ok for _, ok, _ in results), results          # ①②③ 전부 통과


def test_model_without_tools_native_fails_with_hint(servers):
    r = run_checks("http://127.0.0.1:18002/v1", "gemma4-26b-32k:latest")   # 순서: ①, ②stream, ②tools, ②tools+stream, ③
    assert r[0][1] and r[1][1] and r[4][1]                   # 도구 없는 체크는 통과
    assert not r[2][1] and "LLM_SERVER_TOOL_MODE=prompt" in r[2][2]   # 도구 체크는 실패 + 해결 방법 안내


def test_prompt_mode_makes_toolless_model_pass(servers):
    results = run_checks("http://127.0.0.1:18003/v1", "gemma4-26b-32k:latest")
    assert all(ok for _, ok, _ in results), results          # 프롬프트 방식으로 ①②③ 통과
