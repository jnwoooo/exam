# tools/fake_ollama.py — 가짜 Ollama(/api/chat, /api/tags). Ollama 없이 llm_server를 시험하기 위한 것
# 실행: uv run python -m tools.fake_ollama   (기본 :11434)   FAKE_OLLAMA_NO_TOOLS=1 이면 tools를 거부(도구 미지원 모델 흉내)
import json                                                  # NDJSON
import os                                                    # 설정

from fastapi import FastAPI, Request                         # 웹 프레임워크
from fastapi.responses import JSONResponse, StreamingResponse   # 응답

MODEL = "gemma4-26b-32k:latest"                              # 올라가 있는 척하는 모델


def create_app(no_tools: bool = False) -> FastAPI:
    app = FastAPI(title="fake ollama")

    @app.get("/api/tags")
    def tags():
        return {"models": [{"name": MODEL}]}                 # 모델 목록

    @app.post("/api/chat")
    async def chat(request: Request):
        req = await request.json()
        if req.get("tools") and no_tools:                    # 실제 Ollama의 도구 미지원 오류와 같은 모양
            return JSONResponse({"error": f"registry.ollama.ai/library/{MODEL} does not support tools"}, status_code=400)
        msgs = req.get("messages", [])
        last = next((m["content"] for m in reversed(msgs) if m["role"] in ("user", "tool")), "")   # 마지막 질문·도구 결과
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        content, calls = "", []
        if msgs and msgs[-1]["role"] == "tool":              # 도구 결과를 받은 뒤 → 최종 답
            content = f"도구 결과 반영: {last[:30]}"
        elif "도구" in last and req.get("tools"):            # 네이티브 도구 호출
            calls = [{"function": {"name": req["tools"][0]["function"]["name"], "arguments": {"city": "서울"}}}]
        elif "도구" in last and "<tool_call>" in system:     # 프롬프트 방식: 지시대로 글로 도구 호출
            content = '<tool_call>{"name": "get_weather", "arguments": {"city": "서울"}}</tool_call>'
        else:
            content = "2" if "1+1" in last else "안녕"
        prompt_n, out_n = sum(len(m.get("content") or "") for m in msgs) // 4, max(1, len(content) // 4)   # 가짜 토큰 수
        final = {"model": MODEL, "done": True, "done_reason": "stop", "prompt_eval_count": prompt_n, "eval_count": out_n}
        message = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        if not req.get("stream", True):                      # Ollama는 stream 기본값이 true
            return {**final, "message": message}

        def ndjson():                                        # 한 줄에 JSON 하나
            for i in range(0, len(content), 4):              # 글은 4글자씩
                yield json.dumps({"model": MODEL, "done": False,
                                  "message": {"role": "assistant", "content": content[i:i + 4]}}, ensure_ascii=False) + "\n"
            if calls:                                        # 도구 호출은 한 조각에 통째로(실제 Ollama와 같음)
                yield json.dumps({"model": MODEL, "done": False,
                                  "message": {"role": "assistant", "content": "", "tool_calls": calls}}, ensure_ascii=False) + "\n"
            yield json.dumps({**final, "message": {"role": "assistant", "content": ""}}) + "\n"   # 마지막: 토큰 수

        return StreamingResponse(ndjson(), media_type="application/x-ndjson")

    return app


app = create_app(os.environ.get("FAKE_OLLAMA_NO_TOOLS") == "1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKE_OLLAMA_PORT", "11434")))
