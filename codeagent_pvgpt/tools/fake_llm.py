# tools/fake_llm.py — 가짜 OpenAI 호환 LLM 서버. 실제 gemma4 연결 전 테스트·연결 확인용
# 실행: uv run python -m tools.fake_llm   (기본 :8300) → .env에서 LLM_BASE_URL=http://127.0.0.1:8300/v1
# 동작: 수정기 요청이면 SEARCH/REPLACE 블록, 'TOOL'이 들어간 질문이면 첫 도구 호출, 그 밖에는 짧은 답
import json                                                  # 응답 만들기
import os                                                    # 포트
import re                                                    # <code> 찾기
import time                                                  # created 시각

from fastapi import FastAPI, Request                         # 웹 프레임워크
from fastapi.responses import StreamingResponse              # SSE

app = FastAPI(title="fake llm")


def reply_for(body: dict) -> tuple[str, list | None]:
    """요청 → (답 글, 도구 호출 목록 또는 None)."""
    msgs = body.get("messages", [])                          # 대화
    system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")   # 시스템 글
    user_texts = [m.get("content") or "" for m in msgs if m.get("role") == "user"]           # 사용자 글들
    if "코드 수정기" in system:                               # request_fix 안쪽 호출
        first = user_texts[0] if user_texts else ""          # 첫 요청(코드가 들어 있음)
        if "FAIL_ONCE" in first and len(user_texts) == 1:    # 재시도 시험: 첫 번째는 형식 위반
            return "형식을 안 지킨 답", None
        m = re.search(r"<code>\n(.*?)\n</code>", first, re.DOTALL)   # 원본 코드
        code = m.group(1) if m else ""
        line = next((l for l in code.splitlines() if l.strip()), "")   # 첫 번째 내용 있는 줄
        return f"<<<<<<< SEARCH\n{line}\n=======\n{line}  # 수정됨\n>>>>>>> REPLACE", None
    last = user_texts[-1] if user_texts else ""              # 마지막 사용자 글
    tools = body.get("tools") or []                          # 도구 목록
    if tools and "TOOL" in last:                             # 도구 호출 시험
        fn = tools[0]["function"]["name"]
        return "", [{"id": "call_1", "type": "function", "function": {"name": fn, "arguments": "{}"}}]
    return f"가짜 응답: {last[:40]}", None


def usage_of(body: dict, text: str) -> dict:
    """가짜 토큰 수 — 글자 수 기반."""
    p = len(json.dumps(body.get("messages", []), ensure_ascii=False)) // 4
    c = max(1, len(text) // 4)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "gemma4-26b-32k:latest", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()                              # 요청
    text, calls = reply_for(body)                            # 답 만들기
    base = {"id": "chatcmpl-fake", "created": int(time.time()), "model": body.get("model", "")}   # 공통 필드
    if not body.get("stream"):                               # 한 번에
        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = calls
        return {**base, "object": "chat.completion",
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": usage_of(body, text)}

    def sse():                                               # 스트리밍: 조각들 → (usage) → [DONE]
        def chunk(delta, finish=None):
            return "data: " + json.dumps({**base, "object": "chat.completion.chunk",
                                          "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                                         ensure_ascii=False) + "\n\n"
        yield chunk({"role": "assistant"})
        if calls:
            yield chunk({"tool_calls": [{**calls[0], "index": 0}]})
        for i in range(0, len(text), 8):                     # 8글자씩
            yield chunk({"content": text[i:i + 8]})
        yield chunk({}, "tool_calls" if calls else "stop")
        if (body.get("stream_options") or {}).get("include_usage"):   # 요청했을 때만 usage
            yield "data: " + json.dumps({**base, "object": "chat.completion.chunk", "choices": [],
                                         "usage": usage_of(body, text)}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKE_LLM_PORT", "8300")))
