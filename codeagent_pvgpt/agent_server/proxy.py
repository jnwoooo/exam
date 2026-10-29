# agent_server/proxy.py — Continue용 OpenAI 호환 LLM 프록시(/v1). b 방식: Continue의 apiBase = 이 서버
# 흐름: 개인 토큰 → 사번 → 한도(Redis) → 외부 LLM(gemma4)에 그대로 전달 → 응답을 그대로 돌려줌 → 사용량 기록(Postgres)
import json                                                  # 요청·SSE 조각 해석
import time                                                  # 지연 시간 측정

import httpx                                                 # 외부 LLM 호출
from fastapi import APIRouter, Request                       # 라우터
from fastapi.responses import JSONResponse, Response, StreamingResponse   # 응답 종류

from agent_server import auth, usage                         # 인증·사용량


def openai_error(status: int, message: str, kind: str) -> JSONResponse:
    """OpenAI 형식 오류 — Continue가 메시지를 사용자에게 그대로 보여 준다."""
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def upstream_headers(st) -> dict:
    """외부 LLM 호출 헤더 — 키는 서버만 가진다(PC에 배포하지 않음)."""
    return {"Authorization": f"Bearer {st.settings.llm_api_key}"} if st.settings.llm_api_key else {}


def prompt_chars(body: dict) -> str:
    """입력 토큰 추정용 글 — 메시지와 도구 목록(매 호출 함께 간다)."""
    return json.dumps(body.get("messages", []), ensure_ascii=False) + json.dumps(body.get("tools", []), ensure_ascii=False)


def make_router(get_state) -> APIRouter:
    """get_state() = 현재 AgentState. 앱 시작 후에 채워지므로 함수로 받는다."""
    router = APIRouter()

    @router.get("/v1/models")
    async def models(request: Request):
        st = get_state()
        user, _ = await auth.lookup(st, auth.bearer(request.headers))   # 모델 목록도 인증 후
        if user is None:
            return openai_error(401, "개인 토큰이 없거나 잘못됐습니다(Continue 설정 apiKey 확인)", "invalid_api_key")
        return {"object": "list", "data": [{"id": st.settings.llm_model, "object": "model", "owned_by": "codeagent"}]}

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        st = get_state()                                     # 연결들
        user, _ = await auth.lookup(st, auth.bearer(request.headers))   # ① 신원
        if user is None:
            return openai_error(401, "개인 토큰이 없거나 잘못됐습니다(Continue 설정 apiKey 확인)", "invalid_api_key")
        reason = await usage.check_and_count(st, user)       # ② 한도
        if reason:
            return openai_error(429, reason, "rate_limit_exceeded")
        body = await request.json()                          # 요청 본문(messages·tools·stream 등)
        body["model"] = st.settings.llm_model                # Continue가 무슨 이름을 보내든 실제 모델로 고정
        stream = bool(body.get("stream"))                    # 스트리밍 여부
        if stream:                                           # 스트리밍이면 마지막 조각에 usage를 달라고 요청
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        url = f"{st.settings.llm_base_url.rstrip('/')}/chat/completions"   # 외부 LLM 주소
        est_prompt = usage.estimate_tokens(prompt_chars(body))   # usage가 안 올 때 대비한 추정치
        t0 = time.monotonic()                                # 시작 시각
        ms = lambda: int((time.monotonic() - t0) * 1000)     # 경과 ms
        rec = dict(user=user, source="chat", model=st.settings.llm_model, stream=stream, est_prompt=est_prompt)  # 기록 공통값

        if not stream:                                       # ── 한 번에 받는 경우
            try:
                r = await st.http.post(url, json=body, headers=upstream_headers(st))   # ③ 전달
            except httpx.HTTPError as e:                     # 연결 실패·시간 초과
                await usage.record_llm(st, **rec, usage=None, est_completion=0, latency_ms=ms(),
                                       status=502, error=f"{type(e).__name__}: {e}")
                return openai_error(502, f"외부 LLM 연결 실패: {type(e).__name__}", "upstream_error")
            try:
                data = r.json()                              # JSON 응답
            except ValueError:                               # JSON이 아니면 그대로 전달
                await usage.record_llm(st, **rec, usage=None, est_completion=0, latency_ms=ms(),
                                       status=r.status_code, error=r.text[:200])
                return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
            text = json.dumps(data.get("choices", []), ensure_ascii=False)   # 출력 추정용
            await usage.record_llm(st, **rec, usage=data.get("usage"), est_completion=usage.estimate_tokens(text),
                                   latency_ms=ms(), status=r.status_code,
                                   error="" if r.status_code < 400 else r.text[:200])   # ④ 기록
            return JSONResponse(data, status_code=r.status_code)   # 그대로 돌려줌

        # ── 스트리밍(Continue 기본): SSE 바이트를 그대로 흘려보내면서 사본을 읽어 usage를 찾는다
        try:
            upstream = await st.http.send(st.http.build_request("POST", url, json=body, headers=upstream_headers(st)),
                                          stream=True)       # 응답 본문은 아직 안 읽음
        except httpx.HTTPError as e:
            await usage.record_llm(st, **rec, usage=None, est_completion=0, latency_ms=ms(),
                                   status=502, error=f"{type(e).__name__}: {e}")
            return openai_error(502, f"외부 LLM 연결 실패: {type(e).__name__}", "upstream_error")
        if upstream.status_code >= 400:                      # 오류면 본문을 다 읽어 그대로 전달
            content = await upstream.aread()
            await upstream.aclose()
            await usage.record_llm(st, **rec, usage=None, est_completion=0, latency_ms=ms(),
                                   status=upstream.status_code, error=content[:200].decode(errors="replace"))
            return Response(content, status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type", "application/json"))

        async def relay():
            found: dict = {"usage": None, "chars": 0}        # 찾은 usage, 출력 글자 수
            buf = b""                                        # 줄 단위로 자르기 위한 버퍼
            status, err = 200, ""
            try:
                async for chunk in upstream.aiter_raw():     # 받은 바이트 그대로
                    yield chunk                              # 즉시 Continue로(지연 없이)
                    buf += chunk                             # 사본은 버퍼에
                    *lines, buf = buf.split(b"\n")           # 완성된 줄만 꺼냄
                    for line in lines:
                        scan_sse_line(line, found)           # usage·출력 길이 찾기
                scan_sse_line(buf, found)                    # 남은 마지막 줄
            except httpx.HTTPError as e:                     # 도중에 끊김
                status, err = 502, f"stream cut: {type(e).__name__}"
            finally:
                await upstream.aclose()                      # 외부 연결 닫기
                await usage.record_llm(st, **rec, usage=found["usage"],
                                       est_completion=usage.estimate_tokens("x" * found["chars"]),
                                       latency_ms=ms(), status=status, error=err)   # ④ 기록

        return StreamingResponse(relay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})   # SSE 응답

    return router


def scan_sse_line(line: bytes, found: dict) -> None:
    """SSE 한 줄('data: {...}')에서 usage와 출력 길이(내용·도구 인자)를 찾는다."""
    line = line.strip()                                      # 앞뒤 공백·\r 제거
    if not line.startswith(b"data:"):                        # 데이터 줄만
        return
    payload = line[5:].strip()                               # 'data:' 다음
    if not payload or payload == b"[DONE]":                  # 끝 표시
        return
    try:
        obj = json.loads(payload)                            # JSON 조각
    except ValueError:
        return                                               # 깨진 조각은 무시(전달은 이미 됨)
    if obj.get("usage"):                                     # 마지막 조각의 usage
        found["usage"] = obj["usage"]
    for ch in obj.get("choices") or []:                      # 출력 길이 누적(추정용)
        delta = ch.get("delta") or {}
        found["chars"] += len(delta.get("content") or "")    # 글 출력
        for tc in delta.get("tool_calls") or []:             # 도구 호출 인자 출력
            found["chars"] += len((tc.get("function") or {}).get("arguments") or "")
