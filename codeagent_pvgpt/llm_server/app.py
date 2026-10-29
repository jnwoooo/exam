# llm_server/app.py — "LLM 앞의 FastAPI" 예측 구현: Ollama(gemma4)를 OpenAI 호환 /v1로 감싼다
# 외부 호스트에 이미 떠 있는 FastAPI가 이것과 같은 모양(OpenAI 호환)이면 코드에이전트 서버의 LLM_BASE_URL에 바로 붙는다.
# 다르면 이 파일이 "어떻게 맞춰야 하는지"의 기준이 된다. 확인 도구: python -m llm_server.check_compat
#
# 흐름: 코드에이전트 서버(:8200) ──OpenAI 형식──▶ 이 서버(:8000) ──Ollama 형식──▶ Ollama(:11434, gemma4)
# 실행: uv run python -m llm_server.app        (설정은 LLM_SERVER_* 환경변수 / .env)
import json                                                  # NDJSON 해석
import os                                                    # 환경변수
import uuid                                                  # 응답 id

import httpx                                                 # Ollama 호출
from fastapi import FastAPI, Request                         # 웹 프레임워크
from fastapi.responses import JSONResponse, StreamingResponse   # 응답 종류

from common.settings import load_env_file                    # .env 읽기(공용)
from llm_server import convert as cv                         # 형식 변환


def env(name: str, default: str) -> str:
    return os.environ.get(name) or default                   # 빈 값이면 기본값


def create_app() -> FastAPI:
    load_env_file()                                          # .env → 환경변수
    backend = env("LLM_SERVER_BACKEND_URL", "http://localhost:11434").rstrip("/")   # Ollama 주소
    backend_model = env("LLM_SERVER_BACKEND_MODEL", "gemma4-26b-32k:latest")      # Ollama에 올라간 모델 이름
    tool_mode = env("LLM_SERVER_TOOL_MODE", "native")        # native: Ollama tools 기능 / prompt: 프롬프트로 흉내
    num_ctx = int(env("LLM_SERVER_NUM_CTX", "32768"))        # 컨텍스트 길이
    keys = {k.strip() for k in env("LLM_SERVER_KEYS", "").split(",") if k.strip()}   # 받을 API 키(비면 인증 없음)
    timeout = float(env("LLM_SERVER_TIMEOUT", "300"))        # Ollama 응답 시간 제한(초)
    app = FastAPI(title="llm server (OpenAI 호환 → Ollama)")

    def error(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
        return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)   # OpenAI 오류 모양

    def authorized(request: Request) -> bool:
        if not keys:                                         # 키를 안 정했으면 누구나(사내망 전제)
            return True
        auth = request.headers.get("authorization", "")
        return auth.lower().startswith("bearer ") and auth[7:].strip() in keys   # Bearer 키 확인

    @app.get("/healthz")
    async def healthz():
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{backend}/api/tags")       # Ollama에 올라간 모델 목록
            names = [m.get("name") for m in r.json().get("models", [])]
            return {"ok": backend_model in names, "backend": backend, "model": backend_model, "tool_mode": tool_mode}
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"Ollama 연결 실패: {type(e).__name__}"}, status_code=503)

    @app.get("/v1/models")
    async def models(request: Request):
        if not authorized(request):
            return error(401, "invalid api key", "invalid_api_key")
        return {"object": "list", "data": [{"id": backend_model, "object": "model", "owned_by": "ollama"}]}

    # ── ① 이 경로가 있어야 OpenAI 호환이다 ─────────────────────────────
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if not authorized(request):
            return error(401, "invalid api key", "invalid_api_key")
        body = await request.json()                          # OpenAI 요청
        if not body.get("messages"):
            return error(400, "messages가 필요합니다")
        tools = [] if body.get("tool_choice") == "none" else (body.get("tools") or [])   # ② 도구 목록(Continue Agent 모드가 보냄)
        stream = bool(body.get("stream"))                    # ② 스트리밍 여부(Continue 기본 true)
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))   # ③ 마지막에 usage를 달라는지
        model = body.get("model") or backend_model           # 응답에 적을 모델 이름(요청 그대로)
        req = {"model": backend_model,                       # Ollama 요청
               "messages": cv.to_ollama_messages(body["messages"], tool_mode, tools),
               "options": cv.ollama_options(body, num_ctx)}
        if tools and tool_mode == "native":
            req["tools"] = tools                             # Ollama tools는 OpenAI 도구 정의 모양을 그대로 받는다

        # 프롬프트 방식 + 도구: 출력 전체를 받아 <tool_call>을 찾아야 하므로 내부는 한 번에 받고, 밖으로는 원하는 모양으로
        if tools and tool_mode == "prompt":
            final = await ollama_once(req)
            if "error" in final:
                return error(final.get("_status", 502), final["error"], "upstream_error")
            content, calls = cv.extract_tool_calls(final.get("message", {}).get("content", ""))   # 도구 호출 찾기
            if not stream:
                return cv.completion_response(model, content, calls, final)
            return StreamingResponse(replay_as_stream(model, content, calls, final, include_usage),
                                     media_type="text/event-stream")

        if not stream:                                       # 네이티브, 한 번에
            final = await ollama_once(req)
            if "error" in final:
                return error(final.get("_status", 502), hint(final["error"]), "upstream_error")
            msg = final.get("message", {})
            return cv.completion_response(model, msg.get("content", ""), cv.native_calls(msg), final)

        # 네이티브 스트리밍: Ollama NDJSON 줄 → OpenAI SSE 조각으로 바로바로 변환
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10))
        try:
            resp = await client.send(client.build_request("POST", f"{backend}/api/chat", json={**req, "stream": True}),
                                     stream=True)
        except httpx.HTTPError as e:
            await client.aclose()
            return error(502, f"Ollama 연결 실패: {type(e).__name__}", "upstream_error")
        if resp.status_code >= 400:                          # 모델이 tools 미지원 등
            text = (await resp.aread()).decode(errors="replace")
            await resp.aclose(); await client.aclose()
            return error(resp.status_code, hint(ollama_error_text(text)), "upstream_error")

        async def relay():
            cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"        # 조각들이 같은 응답임을 알리는 id
            yield cv.chunk(cid, model, {"role": "assistant", "content": ""})   # 첫 조각: 역할
            n_calls = 0                                      # 지금까지 보낸 도구 호출 수(index용)
            try:
                async for line in resp.aiter_lines():        # Ollama는 한 줄에 JSON 하나(NDJSON)
                    if not line.strip():
                        continue
                    part = json.loads(line)
                    if part.get("error"):                    # 도중 오류
                        yield cv.chunk(cid, model, {"content": f"\n[오류] {part['error']}"}, "stop")
                        break
                    msg = part.get("message") or {}
                    if msg.get("content"):                   # 글 조각 → content 델타
                        yield cv.chunk(cid, model, {"content": msg["content"]})
                    calls = cv.native_calls(msg)
                    if calls:                                # ② 도구 호출 → tool_calls 델타(index·id·arguments 문자열)
                        yield cv.chunk(cid, model, {"tool_calls": cv.openai_tool_calls(calls, with_index=True, start=n_calls)})
                        n_calls += len(calls)
                    if part.get("done"):                     # 마지막 줄: 끝난 이유 + 토큰 수
                        yield cv.chunk(cid, model, {}, cv.finish_reason(part.get("done_reason"), n_calls > 0))
                        if include_usage:                    # ③ 요청했으면 usage 조각(choices는 빈 목록)
                            yield cv.chunk(cid, model, None, usage=cv.usage_from(part))
                        break
            finally:
                await resp.aclose(); await client.aclose()
            yield "data: [DONE]\n\n"                         # OpenAI 스트림 끝 표시

        return StreamingResponse(relay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def ollama_once(req: dict) -> dict:
        """Ollama를 스트리밍 없이 한 번 호출 → 마지막 응답(dict). 실패면 {"error", "_status"}."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as c:
                r = await c.post(f"{backend}/api/chat", json={**req, "stream": False})
        except httpx.HTTPError as e:
            return {"error": f"Ollama 연결 실패: {type(e).__name__}", "_status": 502}
        if r.status_code >= 400:
            return {"error": ollama_error_text(r.text), "_status": r.status_code}
        return r.json()

    async def replay_as_stream(model, content, calls, final, include_usage):
        """프롬프트 방식 결과를 스트리밍 모양으로 다시 내보낸다(내용은 이미 다 받은 상태)."""
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        yield cv.chunk(cid, model, {"role": "assistant", "content": ""})
        if content:
            yield cv.chunk(cid, model, {"content": content})
        if calls:
            yield cv.chunk(cid, model, {"tool_calls": cv.openai_tool_calls(calls, with_index=True)})
        yield cv.chunk(cid, model, {}, cv.finish_reason(final.get("done_reason"), bool(calls)))
        if include_usage:
            yield cv.chunk(cid, model, None, usage=cv.usage_from(final))
        yield "data: [DONE]\n\n"

    def hint(message: str) -> str:
        """흔한 오류에 해결 방법을 붙인다."""
        if "does not support tools" in message:              # Ollama: 이 모델 템플릿에 도구 기능이 없음
            return message + " — LLM_SERVER_TOOL_MODE=prompt 로 바꾸면 프롬프트 방식으로 도구를 흉내 낸다"
        return message

    return app


def ollama_error_text(text: str) -> str:
    """Ollama 오류 본문({"error": "..."}) → 메시지."""
    try:
        return json.loads(text).get("error") or text[:300]
    except ValueError:
        return text[:300]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host=env("LLM_SERVER_HOST", "0.0.0.0"), port=int(env("LLM_SERVER_PORT", "8000")))
