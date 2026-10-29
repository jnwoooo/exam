# parity/fake_llm.py — 차등 테스트용 "녹화형" 가짜 OpenAI 호환 LLM(표준 라이브러리만 사용)
# 하는 일: ① 시나리오에 적힌 응답을 요청 순서대로 돌려준다(재생) ② 받은 요청 본문·헤더를 전부 기록한다(녹화)
# cn과 ncn을 같은 시나리오로 이 서버 앞에서 돌리고, 기록된 요청 본문을 비교한다(계획 10장, R4 — 자료는 전부 JSON)
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 요청·응답·기록
import threading                                             # 서버를 별도 스레드로
import time                                                  # 느린 응답(delay)
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
        calls.append({"id": tool_call_id(turn, i), "type": "function",  # OpenAI 형식 호출 항목 구성
                      "function": {"name": c["name"],        # 함수 이름
                                   "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False)}})   # 인자는 JSON 문자열(OpenAI 규격)
    return step.get("content", ""), calls                    # 글과 호출 목록 반환


def usage(step: dict) -> dict:
    """가짜 사용량 — 시나리오에 usage가 있으면 그대로(압축 시험용), 없으면 0."""
    u = step.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0}  # 시나리오 사용량 또는 0
    return {**u, "total_tokens": u["prompt_tokens"] + u["completion_tokens"]}  # 합계 토큰 계산


def sse_chunks(model: str, text: str, calls: list[dict], step: dict, include_usage: bool) -> list[dict]:
    """스트리밍 응답 조각 목록. 실제 서버처럼 글은 잘게, 도구 인자도 두 조각으로 나눠 보낸다."""
    base = {"id": "chatcmpl-parity", "object": "chat.completion.chunk", "created": 0, "model": model}   # created=0 고정(결정적)
    out = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]   # 첫 조각: 역할
    for i in range(0, len(text), CHUNK):                     # 글 조각
        out.append({**base, "choices": [{"index": 0, "delta": {"content": text[i:i + CHUNK]}, "finish_reason": None}]})  # 글 조각 한 개
    for idx, c in enumerate(calls):                          # 도구 호출 조각
        args = c["function"]["arguments"]                    # 인자 문자열
        half = len(args) // 2                                # 반으로 나눔
        out.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "id": c["id"], "type": "function",  # 인자 앞 절반 조각
                    "function": {"name": c["function"]["name"], "arguments": args[:half]}}]}, "finish_reason": None}]})   # 앞 절반 + id·이름
        out.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx,  # 인자 뒤 절반 조각
                    "function": {"arguments": args[half:]}}]}, "finish_reason": None}]})   # 뒤 절반(id 없음 — index로만 이어 붙이게)
    out.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})   # 끝 조각
    if include_usage:                                        # stream_options.include_usage를 보냈을 때만
        out.append({**base, "choices": [], "usage": usage(step)})  # 사용량 조각(choices 비어 있음)
    return out                                               # 조각 목록 반환


class Recorder:
    """시나리오 응답 목록을 들고, 들어온 요청을 순서대로 기록한다."""

    def __init__(self, steps: list[dict]):                   # 기록기 초기화
        self.steps = steps                                   # 준비된 응답들
        self.requests: list[dict] = []                       # 기록: {path, headers, body}
        self.lock = threading.Lock()                         # 동시 요청(하위 에이전트 등) 대비
        self.used: set[int] = set()                          # 이미 쓴 단계 번호

    def next_step(self, record: dict) -> tuple[int, dict]:
        """요청을 기록하고 이번 차례의 응답 단계를 돌려준다.
        단계 고르기: 아직 안 쓴 단계 중 첫 번째로, "when"(글)이 있으면 요청 messages(JSON 글)에 그 글이 들어 있는 것만.
        "when"이 없는 시나리오는 예전처럼 요청 순서대로(동시에 도는 하위 에이전트는 "when"으로 자기 응답을 고른다)."""
        with self.lock:                                      # 요청 기록·단계 고르기는 락으로 보호
            turn = len(self.requests)                        # 몇 번째 요청인가(0부터)
            self.requests.append(record)                     # 기록
            text = json.dumps((record.get("body") or {}).get("messages"), ensure_ascii=False)   # 고르기 기준 글(메시지 전체)
            idx = next((i for i, st in enumerate(self.steps)  # 첫 번째로 맞는 단계
                        if i not in self.used and (st.get("when") is None or st["when"] in text)), None)   # 안 썼고 조건이 맞으면
            if idx is not None:                              # 찾았으면
                self.used.add(idx)                           # 썼다고 표시
        step = self.steps[idx] if idx is not None else {"content": END_TEXT, "overflow": True}   # 없으면 끝 글
        return turn, step                                    # 차례와 단계 반환


def make_handler(rec: Recorder):
    """Recorder를 쓰는 요청 처리기 클래스를 만든다."""

    class Handler(BaseHTTPRequestHandler):                   # 처리기 클래스
        protocol_version = "HTTP/1.1"                        # 연결 유지(실제 클라이언트와 같게)

        def log_message(self, *a):                           # 기본 접속 로그 끄기(stdout 오염 방지)
            pass                                             # 출력 없이 무시

        def send_json(self, code: int, obj: dict):           # JSON 응답 보내기 도우미
            data = json.dumps(obj, ensure_ascii=False).encode()   # 본문
            self.send_response(code)                         # 상태 줄
            self.send_header("Content-Type", "application/json")  # 형식 헤더
            self.send_header("Content-Length", str(len(data)))  # 길이 헤더
            self.end_headers()                               # 헤더 끝
            self.wfile.write(data)                           # 본문 쓰기

        def do_GET(self):                                    # GET 처리
            if self.path.rstrip("/").endswith("/models"):    # /v1/models
                self.send_json(200, {"object": "list", "data": [{"id": "parity-model", "object": "model"}]})  # 모델 목록 응답
            else:                                            # 그 외는 404
                self.send_json(404, {"error": {"message": "not found"}})  # 찾을 수 없음

        def do_POST(self):                                   # POST 처리
            length = int(self.headers.get("Content-Length") or 0)   # 본문 길이
            raw = self.rfile.read(length)                     # 본문
            try:                                             # JSON 해석 시도
                body = json.loads(raw or b"{}")               # JSON 해석
            except ValueError:
                return self.send_json(400, {"error": {"message": "bad json"}})  # 잘못된 JSON이면 400
            if not self.path.rstrip("/").endswith("/chat/completions"):   # 다른 경로는 기록만 하고 404
                rec.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})  # 지원 안 하는 경로도 기록
                return self.send_json(404, {"error": {"message": f"not supported: {self.path}"}})  # 404 응답
            turn, step = rec.next_step({"path": self.path, "headers": dict(self.headers), "body": body})   # 기록 + 차례
            if step.get("delay"):                             # 느린 응답 재현(동시 실행·취소 시험): {"delay": 초}
                time.sleep(float(step["delay"]))             # 이 요청만 기다린다(서버는 요청마다 스레드)
            if step.get("status"):                            # 오류 재현(재시도 시험): {"status": 503}
                return self.send_json(step["status"], {"error": {"message": step.get("content", "error")}})  # 오류 단계는 해당 상태로 응답
            text, calls = scripted_message(step, turn)        # 응답 내용
            model = body.get("model", "")                    # 요청의 모델명
            if not body.get("stream"):                        # 한 번에 보내는 응답
                msg = {"role": "assistant", "content": text or None}  # 비스트리밍 메시지 구성
                if calls:                                    # 도구 호출이 있으면
                    msg["tool_calls"] = calls                # 메시지에 첨부
                return self.send_json(200, {"id": "chatcmpl-parity", "object": "chat.completion", "created": 0, "model": model,  # 비스트리밍 응답 전송
                                            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                                            "usage": usage(step)})
            include = bool((body.get("stream_options") or {}).get("include_usage"))   # 사용량 조각 요청 여부
            self.send_response(200)                           # SSE 시작
            self.send_header("Content-Type", "text/event-stream")  # SSE 형식
            self.send_header("Cache-Control", "no-cache")    # 캐시 금지
            self.send_header("Connection", "close")           # 스트림 끝 = 연결 종료(Content-Length 없이)
            self.end_headers()                               # 헤더 끝
            for c in sse_chunks(model, text, calls, step, include):   # 조각 전송
                self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())  # SSE 데이터 줄 전송
                self.wfile.flush()                           # 즉시 내보내기
            self.wfile.write(b"data: [DONE]\n\n")             # 끝 표시
            self.wfile.flush()                               # 버퍼 비우기
            self.close_connection = True                      # 연결 닫기

    return Handler                                           # 처리기 클래스 반환


def serve(steps: list[dict], port: int = 0) -> tuple[ThreadingHTTPServer, Recorder]:
    """서버를 백그라운드 스레드로 띄운다. port=0이면 빈 포트 자동. (server.server_port로 확인)"""
    rec = Recorder(steps)                                    # 녹화기
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(rec))   # 로컬 전용
    threading.Thread(target=server.serve_forever, daemon=True).start()     # 백그라운드
    return server, rec


if __name__ == "__main__":                                   # 단독 실행: python -m parity.fake_llm 시나리오.json [포트]
    import sys                                               # 단독 실행용 import
    scen = json.load(open(sys.argv[1], encoding="utf-8"))    # 시나리오
    srv, recorder = serve(scen["llm"], int(sys.argv[2]) if len(sys.argv) > 2 else 8399)  # 서버 기동
    print(f"parity fake llm: http://127.0.0.1:{srv.server_port}/v1  (Ctrl+C로 종료 시 기록 출력)", file=sys.stderr)  # 접속 주소 안내
    try:                                                     # 종료 신호 대기
        threading.Event().wait()                             # 계속 대기
    except KeyboardInterrupt:                                # Ctrl+C로 빠져나옴
        print(json.dumps(recorder.requests, ensure_ascii=False, indent=2))   # 기록 출력
