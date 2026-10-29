# parity/fake_llm.py — 차등 테스트용 "녹화형" 가짜 OpenAI 호환 LLM(표준 라이브러리만 사용)
# 하는 일: ① 시나리오에 적힌 응답을 요청 순서대로 돌려준다(재생) ② 받은 요청 본문·헤더를 전부 기록한다(녹화)
# cn과 ncn을 같은 시나리오로 이 서버 앞에서 돌리고, 기록된 요청 본문을 비교한다(계획 10장, R4 — 자료는 전부 JSON)
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 요청·응답·기록
import threading                                             # 서버를 별도 스레드로
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # 표준 HTTP 서버

CHUNK = 8                                                    # 스트리밍 때 글을 몇 글자씩 나눠 보낼지(조각 이어 붙이기 시험)
END_TEXT = "(시나리오 끝)"                                    # 준비된 응답보다 요청이 많을 때 돌려줄 글


def tool_call_id(turn: int, idx: int) -> str:
    """도구 호출 id를 결정적으로 만든다 — 비교할 때 id 차이가 생기지 않게."""
    return f"call_{turn}_{idx}"                              # 예: call_0_0


def scripted_message(step: dict, turn: int) -> tuple[str, list[dict]]:
    """시나리오 한 단계 → (글, OpenAI 형식 도구 호출 목록)."""
    calls = []                                               # 도구 호출
    for i, c in enumerate(step.get("tool_calls", [])):       # 시나리오에는 {name, arguments(dict)}로 적음
        calls.append({"id": tool_call_id(turn, i), "type": "function",
                      "function": {"name": c["name"],
                                   "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False)}})   # 인자는 JSON 문자열(OpenAI 규격)
    return step.get("content", ""), calls


def usage(step: dict) -> dict:
    """가짜 사용량 — 시나리오에 usage가 있으면 그대로(압축 시험용), 없으면 0."""
    u = step.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0}
    return {**u, "total_tokens": u["prompt_tokens"] + u["completion_tokens"]}


def sse_chunks(model: str, text: str, calls: list[dict], step: dict, include_usage: bool) -> list[dict]:
    """스트리밍 응답 조각 목록. 실제 서버처럼 글은 잘게, 도구 인자도 두 조각으로 나눠 보낸다."""
    base = {"id": "chatcmpl-parity", "object": "chat.completion.chunk", "created": 0, "model": model}   # created=0 고정(결정적)
    out = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]   # 첫 조각: 역할
    for i in range(0, len(text), CHUNK):                     # 글 조각
        out.append({**base, "choices": [{"index": 0, "delta": {"content": text[i:i + CHUNK]}, "finish_reason": None}]})
    for idx, c in enumerate(calls):                          # 도구 호출 조각
        args = c["function"]["arguments"]                    # 인자 문자열
        half = len(args) // 2                                # 반으로 나눔
        out.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "id": c["id"], "type": "function",
                    "function": {"name": c["function"]["name"], "arguments": args[:half]}}]}, "finish_reason": None}]})   # 앞 절반 + id·이름
        out.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx,
                    "function": {"arguments": args[half:]}}]}, "finish_reason": None}]})   # 뒤 절반(id 없음 — index로만 이어 붙이게)
    out.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})   # 끝 조각
    if include_usage:                                        # stream_options.include_usage를 보냈을 때만
        out.append({**base, "choices": [], "usage": usage(step)})
    return out


class Recorder:
    """시나리오 응답 목록을 들고, 들어온 요청을 순서대로 기록한다."""

    def __init__(self, steps: list[dict]):
        self.steps = steps                                   # 준비된 응답들
        self.requests: list[dict] = []                       # 기록: {path, headers, body}
        self.lock = threading.Lock()                         # 동시 요청(하위 에이전트 등) 대비

    def next_step(self, record: dict) -> tuple[int, dict]:
        """요청을 기록하고 이번 차례의 응답 단계를 돌려준다."""
        with self.lock:
            turn = len(self.requests)                        # 몇 번째 요청인가(0부터)
            self.requests.append(record)                     # 기록
        step = self.steps[turn] if turn < len(self.steps) else {"content": END_TEXT, "overflow": True}   # 넘치면 끝 글
        return turn, step


def make_handler(rec: Recorder):
    """Recorder를 쓰는 요청 처리기 클래스를 만든다."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"                        # 연결 유지(실제 클라이언트와 같게)

        def log_message(self, *a):                           # 기본 접속 로그 끄기(stdout 오염 방지)
            pass

        def send_json(self, code: int, obj: dict):
            data = json.dumps(obj, ensure_ascii=False).encode()   # 본문
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):    # /v1/models
                self.send_json(200, {"object": "list", "data": [{"id": "parity-model", "object": "model"}]})
            else:
                self.send_json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)   # 본문 길이
            raw = self.rfile.read(length)                     # 본문
            try:
                body = json.loads(raw or b"{}")               # JSON 해석
            except ValueError:
                return self.send_json(400, {"error": {"message": "bad json"}})
            if not self.path.rstrip("/").endswith("/chat/completions"):   # 다른 경로는 기록만 하고 404
                rec.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                return self.send_json(404, {"error": {"message": f"not supported: {self.path}"}})
            turn, step = rec.next_step({"path": self.path, "headers": dict(self.headers), "body": body})   # 기록 + 차례
            if step.get("status"):                            # 오류 재현(재시도 시험): {"status": 503}
                return self.send_json(step["status"], {"error": {"message": step.get("content", "error")}})
            text, calls = scripted_message(step, turn)        # 응답 내용
            model = body.get("model", "")
            if not body.get("stream"):                        # 한 번에 보내는 응답
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                return self.send_json(200, {"id": "chatcmpl-parity", "object": "chat.completion", "created": 0, "model": model,
                                            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                                            "usage": usage(step)})
            include = bool((body.get("stream_options") or {}).get("include_usage"))   # 사용량 조각 요청 여부
            self.send_response(200)                           # SSE 시작
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")           # 스트림 끝 = 연결 종료(Content-Length 없이)
            self.end_headers()
            for c in sse_chunks(model, text, calls, step, include):   # 조각 전송
                self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")             # 끝 표시
            self.wfile.flush()
            self.close_connection = True                      # 연결 닫기

    return Handler


def serve(steps: list[dict], port: int = 0) -> tuple[ThreadingHTTPServer, Recorder]:
    """서버를 백그라운드 스레드로 띄운다. port=0이면 빈 포트 자동. (server.server_port로 확인)"""
    rec = Recorder(steps)                                    # 녹화기
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(rec))   # 로컬 전용
    threading.Thread(target=server.serve_forever, daemon=True).start()     # 백그라운드
    return server, rec


if __name__ == "__main__":                                   # 단독 실행: python -m parity.fake_llm 시나리오.json [포트]
    import sys
    scen = json.load(open(sys.argv[1], encoding="utf-8"))    # 시나리오
    srv, recorder = serve(scen["llm"], int(sys.argv[2]) if len(sys.argv) > 2 else 8399)
    print(f"parity fake llm: http://127.0.0.1:{srv.server_port}/v1  (Ctrl+C로 종료 시 기록 출력)", file=sys.stderr)
    try:
        threading.Event().wait()                             # 계속 대기
    except KeyboardInterrupt:
        print(json.dumps(recorder.requests, ensure_ascii=False, indent=2))   # 기록 출력
