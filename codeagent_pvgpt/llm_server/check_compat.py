# llm_server/check_compat.py — 외부 LLM 서버가 "OpenAI 호환"인지 3가지를 실제로 호출해 확인한다(진행기록 9장)
# 실행: uv run python -m llm_server.check_compat                       (.env의 LLM_BASE_URL·LLM_API_KEY·LLM_MODEL 사용)
#       uv run python -m llm_server.check_compat --base-url http://호스트:8000/v1 --model gemma4-26b-32k:latest
# 결과가 모두 OK면 코드에이전트 서버(LLM_BASE_URL)에 그대로 붙일 수 있다
import argparse                                              # 명령줄
import json                                                  # 응답 해석
import sys                                                   # 종료 코드

import httpx                                                 # HTTP 호출

WEATHER_TOOL = [{"type": "function", "function": {           # 시험용 도구 하나(Continue가 보내는 도구 정의와 같은 모양)
    "name": "get_weather", "description": "도시의 현재 날씨를 조회한다",
    "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "도시 이름"}},
                   "required": ["city"]}}}]
TOOL_PROMPT = [{"role": "user", "content": "서울 날씨 알려줘. 반드시 get_weather 도구를 사용해."}]   # 도구를 부르게 만드는 질문


def sse_events(text: str) -> list:
    """SSE 본문 → 'data:' 줄들의 JSON 목록(마지막 [DONE]은 문자열로)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


def run_checks(base_url: str, model: str, key: str = "", timeout: float = 180) -> list[tuple[str, bool, str]]:
    """체크 목록 실행 → [(이름, 통과?, 설명)]."""
    url = base_url.rstrip("/") + "/chat/completions"         # {주소}/v1/chat/completions
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    results: list[tuple[str, bool, str]] = []

    def post(body: dict) -> httpx.Response:
        return httpx.post(url, json={"model": model, **body}, headers=headers, timeout=timeout)

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as e:                               # 연결 실패·형식 오류도 결과로
            ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
        results.append((name, ok, detail))

    # ── ① 경로와 응답 모양 ───────────────────────────────────────
    def c1():
        r = post({"messages": [{"role": "user", "content": "1+1은? 숫자만."}], "max_tokens": 20})
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        msg = r.json()["choices"][0]["message"]              # OpenAI 모양이면 여기 접근이 된다
        return True, f"content={msg.get('content')!r}"
    check("① POST /v1/chat/completions (OpenAI 응답 모양)", c1)

    def c2a():
        r = post({"messages": [{"role": "user", "content": "안녕이라고만 답해."}], "stream": True, "max_tokens": 20})
        ev = sse_events(r.text)
        text = "".join((e["choices"][0]["delta"].get("content") or "") for e in ev
                       if isinstance(e, dict) and e.get("choices"))
        return (r.status_code == 200 and ev and ev[-1] == "[DONE]"), f"조각 {len(ev)}개, 글={text!r}"
    check("② stream=true → SSE 조각 + [DONE]", c2a)

    # ── ② 도구: 이게 안 되면 Continue Agent 모드 불가 ─────────────────
    def c2b():
        r = post({"messages": TOOL_PROMPT, "tools": WEATHER_TOOL})
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        choice = r.json()["choices"][0]
        calls = choice["message"].get("tool_calls") or []
        if not calls:
            return False, f"tool_calls 없음(글로 답함: {str(choice['message'].get('content'))[:80]!r})"
        fn = calls[0]["function"]
        args = json.loads(fn["arguments"])                   # ★ arguments는 JSON 문자열이어야 한다
        return (fn["name"] == "get_weather" and bool(calls[0].get("id"))
                and choice.get("finish_reason") == "tool_calls"), \
            f"{fn['name']}({args}) id={calls[0].get('id')} finish={choice.get('finish_reason')}"
    check("② tools → message.tool_calls (stream=false)", c2b)

    def c2c():
        r = post({"messages": TOOL_PROMPT, "tools": WEATHER_TOOL, "stream": True})
        ev = [e for e in sse_events(r.text) if isinstance(e, dict) and e.get("choices")]
        deltas = [tc for e in ev for tc in (e["choices"][0]["delta"].get("tool_calls") or [])]
        if not deltas:
            return False, "스트림에 tool_calls 조각 없음"
        name = next((d["function"].get("name") for d in deltas if d.get("function", {}).get("name")), None)
        args = "".join(d.get("function", {}).get("arguments") or "" for d in deltas)   # 인자는 여러 조각으로 나뉠 수 있다
        finish = next((e["choices"][0].get("finish_reason") for e in ev if e["choices"][0].get("finish_reason")), None)
        return (name == "get_weather" and "index" in deltas[0] and finish == "tool_calls"), \
            f"{name}({args}) index={deltas[0].get('index')} finish={finish}"
    check("② tools + stream → delta.tool_calls 조각", c2c)

    # ── ③ 토큰 사용량 ─────────────────────────────────────────
    def c3():
        r = post({"messages": [{"role": "user", "content": "안녕"}], "stream": True, "max_tokens": 20,
                  "stream_options": {"include_usage": True}})
        usage = next((e["usage"] for e in sse_events(r.text) if isinstance(e, dict) and e.get("usage")), None)
        return usage is not None and usage.get("total_tokens", 0) > 0, f"usage={usage}"
    check("③ stream_options.include_usage → 마지막 조각 usage", c3)
    return results


HINTS = {                                                    # 실패했을 때 할 일
    "①": "OpenAI 호환이 아님 → llm_server/app.py처럼 감싸는 FastAPI를 두거나, 코드에이전트 proxy에 변환 계층 추가",
    "②": "도구 미지원 → 모델/서버가 tools를 지원하게 하거나 llm_server의 LLM_SERVER_TOOL_MODE=prompt 방식으로 감싸기",
    "③": "치명적이지 않음 → 코드에이전트 서버가 글자 수로 추정해 기록(감시 화면 estimated)",
}


def main(argv: list[str]) -> int:
    from common.settings import get_settings                 # .env 기본값
    s = get_settings()
    p = argparse.ArgumentParser(prog="check_compat")
    p.add_argument("--base-url", default=s.llm_base_url)     # 예: http://호스트:8000/v1
    p.add_argument("--model", default=s.llm_model)
    p.add_argument("--key", default=s.llm_api_key)
    a = p.parse_args(argv)
    print(f"대상: {a.base_url}  모델: {a.model}\n")
    results = run_checks(a.base_url, a.model, a.key)
    for name, ok, detail in results:
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}\n        {detail}")
        if not ok:
            print(f"        → {HINTS[name[0]]}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
