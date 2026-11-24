# -*- coding: utf-8 -*-
"""vupdate 중계 서버 참고 구현 (relay_reference.py)

목적
  08_MCP역할목록.md 의 "서버가 지켜야 할 약속"을 그대로 코드로 옮긴 참고 구현이다.
  MCP 서버에 같은 기능을 넣을 때의 본보기로 쓰거나, MCP 서버가 준비되기 전에 sLLM 앞에 임시로 세워 쓴다.
  표준 라이브러리만 쓴다. vupdate.py 와는 독립된 파일이며 로컬 PC 에는 배포하지 않는다(서버에서만 돈다).

여는 주소(둘 다 같은 처리를 거친다)
  POST /v1/chat/completions   OpenAI 호환 중계. vupdate 의 llm.transport = "openai" (기본)
  GET  /v1/models             한도, 허용 동시 수, 예약량을 알려 준다
  POST /mcp                   MCP(Streamable HTTP, JSON 응답). vupdate 의 llm.transport = "mcp"
                              도구: llm_complete, llm_info, job_open, job_progress, job_close

약속과 구현 위치(번호는 08 문서 3절)
  1 주소와 모델을 서버가 갖는다        CFG 의 upstream_endpoint, upstream_model. 호출자가 보낸 model 은 쓰지 않는다
  2 호출자를 확인하고 기록한다          caller_of(), write_log()
  3 본문을 건드리지 않는다              build_messages() 는 system 앞에 공통 프롬프트만 붙인다. 자르지 않는다
  4 공통 프롬프트와 예약량              common_prompt_file, reserve_tokens (llm_info, /models 로 알려 준다)
  5 한도 초과는 거절한다                sLLM 이 낸 한도 초과 오류를 그대로 넘긴다(over_limit). 중계가 본문을 줄이지 않는다
  6 동시 수를 넘으면 기다리게 하거나 알린다   Gate (busy_mode: reject 면 429/busy, wait 면 줄을 세운다)
  7 모델 설정을 고정한다                temperature 0, extra_body(추론 모드 끄기 등), max_tokens 상한
  8 응답을 그대로 돌려준다              usage, finish_reason 을 그대로 넘긴다
  9 전송 오류는 서버가 먼저 다시 건다    call_upstream() 의 upstream_retries
 10 호출을 기록한다                    write_log() (JSON 한 줄씩). log_bodies 가 참일 때만 본문을 남긴다

실행
  python relay_reference.py --config relay.json
  설정 파일이 없으면 기본값으로 뜬다. 기본값은 아래 DEFAULTS 를 본다.

확인
  PC 에서  vupdate llmcheck --llm-endpoint http://<서버>:8765/v1   (전부 "정상"이면 약속을 지키는 것이다)

한계
  - 토큰 수를 직접 세지 않는다. 한도 검사는 sLLM(vLLM 등)이 하고, 중계는 그 거절을 넘긴다.
    sLLM 이 넘는 요청을 잘라서 받아들이는 설정이면 약속 3, 5 가 깨진다. llmcheck 의 "한도 초과 거절"로 확인한다.
  - 인증은 api_keys(키 -> 호출자 이름) 대조뿐이다. 운영에서는 조직의 인증 방식으로 바꾼다.
  - MCP 는 도구 호출에 필요한 최소한만 구현했다(initialize, tools/list, tools/call, ping). 응답은 항상 JSON 한 건이다.
"""
import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.5.0"
DEFAULTS = {
    "listen_host": "0.0.0.0", "listen_port": 8765,
    "upstream_endpoint": "http://127.0.0.1:8000/v1",   # sLLM 의 OpenAI 호환 주소
    "upstream_model": "gemma4-31b", "upstream_api_key": "",
    "max_total_tokens": 24000,      # 요청 1건의 입력+출력 한도(알려 주는 값. 실제 검사는 sLLM 이 한다)
    "max_output_tokens": 3000,      # 출력 상한. 호출자가 더 크게 요청해도 이 값으로 줄인다
    "max_parallel": 4,              # sLLM 에 동시에 보내는 전체 요청 수
    "per_caller_parallel": 0,       # 호출자 하나의 동시 요청 수. 0 이면 전체 상한만 본다
    "busy_mode": "wait",            # wait: 자리가 날 때까지 wait_seconds 만큼 기다린다. reject: 바로 "잠시 후"로 답한다
    "wait_seconds": 60, "retry_after": 2,
    "common_prompt_file": "",       # 모든 요청의 system 앞에 붙일 공통 프롬프트 파일(UTF-8). 빈 값이면 붙이지 않는다
    "reserve_tokens": 0,            # 공통 프롬프트가 차지할 수 있는 최대 토큰 수(호출자에게 알려 주는 값)
    "prompt_version": "",
    "upstream_retries": 2, "upstream_timeout": 300,
    "extra_body": {},               # sLLM 요청에 더할 값. 예: {"chat_template_kwargs": {"enable_thinking": false}}
    "api_keys": {},                 # {"키": "호출자 이름"}. 비어 있으면 인증하지 않는다
    "log_file": "relay_calls.jsonl", "log_bodies": False,
}
_LIMIT_MSG_RE = re.compile(r"context length|context size|context window|maximum model length|too many tokens|token limit", re.I)
_LIMIT_NUM_RES = [re.compile(p, re.I) for p in (r"maximum context length is (\d+)", r"maximum (?:model|context) length of (\d+)")]
_ASKED_IN_RES = [re.compile(p, re.I) for p in (r"\((\d+) in the messages", r"request has (\d+) input tokens", r"prompt \(length (\d+)\)")]
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOG_LOCK = threading.Lock()


class RelayError(Exception):
    """호출자에게 돌려줄 오류. code: over_limit, busy, denied, bad_request, upstream"""

    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code, self.message, self.extra = code, message, extra

    def as_dict(self):
        d = {"code": self.code, "message": self.message}
        d.update(self.extra)
        return d


class Gate:
    """동시 요청 수를 지킨다(약속 6). 전체 상한과 호출자별 상한을 함께 본다."""

    def __init__(self, cfg):
        self.cfg, self.cv, self.total, self.by_caller = cfg, threading.Condition(), 0, {}

    def _free(self, caller):
        per = int(self.cfg["per_caller_parallel"] or 0)
        return self.total < int(self.cfg["max_parallel"]) and (not per or self.by_caller.get(caller, 0) < per)

    def enter(self, caller):
        deadline = time.time() + (float(self.cfg["wait_seconds"]) if self.cfg["busy_mode"] == "wait" else 0)
        with self.cv:
            while not self._free(caller):
                left = deadline - time.time()
                if left <= 0:
                    raise RelayError("busy", "동시 요청 수를 넘었습니다. 잠시 후 다시 보내세요", retry_after=self.cfg["retry_after"])
                self.cv.wait(left)
            self.total += 1
            self.by_caller[caller] = self.by_caller.get(caller, 0) + 1

    def leave(self, caller):
        with self.cv:
            self.total -= 1
            self.by_caller[caller] = self.by_caller.get(caller, 1) - 1
            self.cv.notify_all()


def load_config(path):
    cfg = dict(DEFAULTS)
    if path:
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    cfg["_common"] = ""
    if cfg["common_prompt_file"]:
        with open(cfg["common_prompt_file"], "r", encoding="utf-8-sig") as f:
            cfg["_common"] = f.read().strip()
    return cfg


def write_log(cfg, rec):
    """호출 기록 한 줄(약속 10)."""
    if not cfg["log_file"]:
        return
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOG_LOCK:
        with open(cfg["log_file"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def caller_of(cfg, headers, args=None):
    """호출자와 실행 ID 를 정한다(약속 2). api_keys 가 있으면 키로 확인하고, 없으면 머리말(또는 도구 인자)의 이름을 믿는다."""
    args = args or {}
    name = urllib.parse.unquote(headers.get("X-Vupdate-Caller") or "") or str(args.get("caller") or "")
    run = urllib.parse.unquote(headers.get("X-Vupdate-Run") or "") or str(args.get("run_id") or "")
    if cfg["api_keys"]:
        key = (headers.get("Authorization") or "").replace("Bearer ", "").strip()
        if key not in cfg["api_keys"]:
            raise RelayError("denied", "허용되지 않은 호출자입니다")
        name = cfg["api_keys"][key] or name
    return name or "unknown", run


def build_messages(cfg, system, user):
    """sLLM 에 보낼 메시지(약속 3, 4). 공통 프롬프트는 system 앞에만 붙인다. 호출자가 보낸 글은 한 글자도 바꾸지 않는다."""
    sys_text = (cfg["_common"] + "\n\n" + system) if cfg["_common"] else system
    return [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]


def call_upstream(cfg, messages, max_tokens):
    """sLLM 을 부른다(약속 1, 7, 9). 돌려주는 값: sLLM 의 응답 JSON 그대로. 4xx 는 다시 걸지 않는다."""
    body = {"model": cfg["upstream_model"], "messages": messages, "temperature": 0,
            "max_tokens": min(int(max_tokens or cfg["max_output_tokens"]), int(cfg["max_output_tokens"]))}
    body.update(cfg["extra_body"] or {})
    headers = {"Content-Type": "application/json"}
    if cfg["upstream_api_key"]:
        headers["Authorization"] = "Bearer " + cfg["upstream_api_key"]
    url, last = cfg["upstream_endpoint"].rstrip("/") + "/chat/completions", ""
    for attempt in range(int(cfg["upstream_retries"]) + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
            with _NO_PROXY.open(req, timeout=cfg["upstream_timeout"]) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:2000]
            if e.code in (400, 413, 422):
                if _LIMIT_MSG_RE.search(text):          # 한도 초과: 수치를 읽어 그대로 알린다(약속 5)
                    lim = next((int(m.group(1)) for m in (r.search(text) for r in _LIMIT_NUM_RES) if m), None)
                    a_in = next((int(m.group(1)) for m in (r.search(text) for r in _ASKED_IN_RES) if m), None)
                    raise RelayError("over_limit", text, limit=lim, input_tokens=a_in, http_status=e.code)
                raise RelayError("bad_request", text, http_status=e.code)
            if e.code in (401, 403):
                raise RelayError("upstream", "sLLM 인증 실패: %s" % text[:200])
            last = "HTTP %s %s" % (e.code, text[:200])
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
        time.sleep(min(5.0, 0.5 * (attempt + 1)))
    raise RelayError("upstream", "sLLM 호출 실패: %s" % last)


def complete(server, headers, system, user, max_tokens, args=None, via="http"):
    """모델 호출 한 건의 전체 처리. 돌려주는 값: (sLLM 응답 JSON, 요약 dict). 오류는 RelayError."""
    cfg, t0 = server.cfg, time.time()
    rec = {"via": via, "caller": "", "run": "", "status": "ok"}
    caller = None
    try:
        caller, run = caller_of(cfg, headers, args)
        rec.update(caller=caller, run=run)
        server.gate.enter(caller)
        try:
            data = call_upstream(cfg, build_messages(cfg, system, user), max_tokens)
        finally:
            server.gate.leave(caller)
        choice, usage = (data.get("choices") or [{}])[0], data.get("usage") or {}
        out = {"content": (choice.get("message") or {}).get("content") or "", "finish_reason": choice.get("finish_reason") or "",
               "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
               "model": cfg["upstream_model"], "prompt_version": cfg["prompt_version"]}
        rec.update(prompt_tokens=out["prompt_tokens"], completion_tokens=out["completion_tokens"], finish=out["finish_reason"])
        if cfg["log_bodies"]:
            rec.update(system=system, user=user, content=out["content"])
        return data, out
    except RelayError as e:
        rec.update(status=e.code, error=e.message[:300])
        raise
    finally:
        rec["ms"] = int((time.time() - t0) * 1000)
        write_log(cfg, rec)


def info_of(cfg):
    """호출자에게 알려 주는 값(llm_info, /models)."""
    return {"model": cfg["upstream_model"], "max_total_tokens": cfg["max_total_tokens"], "max_output_tokens": cfg["max_output_tokens"],
            "max_parallel": int(cfg["per_caller_parallel"] or cfg["max_parallel"]), "reserve_tokens": cfg["reserve_tokens"],
            "prompt_version": cfg["prompt_version"]}


TOOLS = [
    {"name": "llm_complete", "description": "작업 프롬프트를 sLLM 에 보내고 응답을 그대로 돌려준다",
     "inputSchema": {"type": "object", "required": ["system", "user"], "properties": {
         "system": {"type": "string"}, "user": {"type": "string"}, "max_tokens": {"type": "integer"},
         "caller": {"type": "string"}, "run_id": {"type": "string"}, "job_id": {"type": "string"}}}},
    {"name": "llm_info", "description": "토큰 한도, 출력 상한, 허용 동시 수, 공통 프롬프트의 예약량을 알려 준다",
     "inputSchema": {"type": "object", "properties": {"caller": {"type": "string"}, "run_id": {"type": "string"}}}},
    {"name": "job_open", "description": "작업 시작을 등록하고 작업 ID 를 준다", "inputSchema": {"type": "object"}},
    {"name": "job_progress", "description": "단계가 끝났음을 기록한다", "inputSchema": {"type": "object"}},
    {"name": "job_close", "description": "작업 종료를 기록한다", "inputSchema": {"type": "object"}},
]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, obj, headers=()):
        data = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)).decode("utf-8") or "{}")

    def log_message(self, *a):
        pass

    def do_GET(self):
        cfg = self.server.cfg
        if self.path.rstrip("/").endswith("/models"):
            item = info_of(cfg)
            item.update(id=cfg["upstream_model"], object="model", max_model_len=cfg["max_total_tokens"])
            return self._send(200, {"object": "list", "data": [item]})
        self._send(405 if self.path.rstrip("/").endswith("/mcp") else 404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": {"message": "JSON 이 아닙니다"}})
        path = self.path.rstrip("/")
        if path.endswith("/chat/completions"):
            return self._openai(body)
        if path.endswith("/mcp"):
            return self._mcp(body)
        self._send(404, {"error": "not found"})

    # ---- OpenAI 호환 중계 ----
    def _openai(self, body):
        msgs = body.get("messages") or []
        system = "\n\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
        user = "\n\n".join(str(m.get("content") or "") for m in msgs if m.get("role") != "system")
        try:
            data, _out = complete(self.server, self.headers, system, user, body.get("max_tokens"))
            return self._send(200, data)                     # sLLM 의 응답을 그대로(약속 8)
        except RelayError as e:
            if e.code == "over_limit":                       # sLLM 의 문구를 그대로 넘긴다. vupdate 가 수치를 읽는다
                return self._send(400, {"error": {"code": "over_limit", "message": e.message, "limit": e.extra.get("limit"),
                                                  "input_tokens": e.extra.get("input_tokens")}})
            if e.code == "busy":
                return self._send(429, {"error": e.as_dict()}, [("Retry-After", e.extra.get("retry_after", 2))])
            status = {"denied": 401, "bad_request": 400}.get(e.code, 502)
            return self._send(status, {"error": e.as_dict()})

    # ---- MCP(Streamable HTTP, JSON 응답) ----
    def _mcp(self, msg):
        rid, method, params = msg.get("id"), msg.get("method") or "", msg.get("params") or {}
        if "id" not in msg:                                  # 알림(notifications/initialized 등)
            return self._send(202, None)
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "vupdate-relay-reference", "version": VERSION}}
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": res}, [("Mcp-Session-Id", uuid.uuid4().hex)])
        if method == "ping":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {}})
        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method != "tools/call":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "지원하지 않는 메서드: %s" % method}})
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name == "llm_complete":
                _data, out = complete(self.server, self.headers, str(args.get("system") or ""), str(args.get("user") or ""),
                                      args.get("max_tokens"), args, via="mcp")
            elif name == "llm_info":
                out = info_of(self.server.cfg)
            elif name in ("job_open", "job_progress", "job_close"):
                caller, run = caller_of(self.server.cfg, self.headers, args)
                out = {"job_id": args.get("job_id") or uuid.uuid4().hex[:12], "allowed": True}
                write_log(self.server.cfg, {"via": "mcp", "event": name, "caller": caller, "run": run,
                                            "detail": {k: v for k, v in args.items() if k not in ("caller", "run_id")}})
            else:
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "모르는 도구: %s" % name}})
            res = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "structuredContent": out, "isError": False}
        except RelayError as e:
            err = e.as_dict()
            res = {"content": [{"type": "text", "text": json.dumps(err, ensure_ascii=False)}], "structuredContent": err, "isError": True}
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": res})


def make_server(cfg):
    """설정으로 서버를 만든다. serve_forever() 는 부르는 쪽이 한다(시험에서 쓴다)."""
    srv = ThreadingHTTPServer((cfg["listen_host"], int(cfg["listen_port"])), Handler)
    srv.daemon_threads = True
    srv.cfg, srv.gate = cfg, Gate(cfg)
    return srv


def main():
    ap = argparse.ArgumentParser(description="vupdate 중계 서버 참고 구현")
    ap.add_argument("--config", help="설정 파일(JSON). 없으면 기본값")
    args = ap.parse_args()
    cfg = load_config(args.config)
    srv = make_server(cfg)
    print("vupdate 중계(참고 구현) %s: http://%s:%s  ->  %s (모델 %s)"
          % (VERSION, cfg["listen_host"], cfg["listen_port"], cfg["upstream_endpoint"], cfg["upstream_model"]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
