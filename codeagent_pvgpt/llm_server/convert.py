# llm_server/convert.py — OpenAI 형식 ↔ Ollama 형식 변환(순수 함수, 테스트 가능)
# 왜 필요한가: Continue·코드에이전트 서버는 OpenAI 형식(/v1/chat/completions)으로 말하고,
#             gemma4 같은 Ollama 모델은 자기 형식(/api/chat)으로 말한다. 이 서버가 둘 사이 통역을 한다.
#
# 진행기록 9장의 확인 3가지가 코드로는 여기 있다:
#   ① 경로·응답 모양     → app.py의 @app.post("/v1/chat/completions"), completion_response()
#   ② tools·stream·tool_calls → to_ollama_messages()(도구 대화 기록 변환), openai_tool_calls()(도구 호출 응답 변환),
#                              app.py의 스트리밍 처리, 도구를 못 하는 모델은 프롬프트 방식(build_tool_prompt·extract_tool_calls)
#   ③ usage             → usage_from()(Ollama의 prompt_eval_count·eval_count → OpenAI usage)
import json                                                  # 도구 인자(JSON 문자열 ↔ dict)
import re                                                    # 프롬프트 방식의 <tool_call> 찾기
import time                                                  # created 시각
import uuid                                                  # 도구 호출 id

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)   # 프롬프트 방식 도구 호출 표시


def text_of(content) -> str:
    """OpenAI의 content는 문자열이거나 [{type:"text", text:...}, {type:"image_url"...}] 목록이다 → 글만 이어 붙임."""
    if content is None:                                      # 도구 호출만 있는 assistant 메시지는 content가 없다
        return ""
    if isinstance(content, str):                             # 보통의 경우
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")   # 글 조각만


def parse_args(arguments) -> dict:
    """OpenAI는 도구 인자를 'JSON 문자열'로, Ollama는 'dict'로 주고받는다 → dict로 맞춘다."""
    if isinstance(arguments, dict):                          # 이미 dict
        return arguments
    if not arguments:                                        # 비었으면
        return {}
    try:
        value = json.loads(arguments)                        # 문자열 → dict
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}                                            # 깨진 JSON은 빈 인자로


def build_tool_prompt(tools: list[dict]) -> str:
    """프롬프트 방식: 모델이 tools 기능을 지원하지 않을 때 도구 목록을 시스템 글로 알려 준다."""
    lines = ["사용할 수 있는 도구가 있다. 도구가 필요하면 아래 형식으로만 출력하고 그 뒤에는 아무것도 쓰지 않는다.",
             '<tool_call>{"name": "도구이름", "arguments": {인자 JSON}}</tool_call>',
             "여러 도구가 필요하면 <tool_call> 블록을 여러 개 쓴다. 도구가 필요 없으면 평소처럼 답한다.",
             "도구 결과는 <tool_result name=\"도구이름\"> 블록으로 전달된다.", "", "도구 목록:"]
    for t in tools:                                          # 도구마다
        fn = t.get("function", {})                           # OpenAI 도구 정의는 {"type":"function","function":{...}}
        lines.append(f"- {fn.get('name')}: {fn.get('description', '')}")                        # 이름·설명
        lines.append(f"  인자 스키마: {json.dumps(fn.get('parameters', {}), ensure_ascii=False)}")   # 인자 형식
    return "\n".join(lines)


def to_ollama_messages(messages: list[dict], tool_mode: str, tools: list[dict]) -> list[dict]:
    """OpenAI 대화 기록 → Ollama 대화 기록. 도구 대화(assistant의 tool_calls, role=tool 결과)가 핵심."""
    id_to_name: dict[str, str] = {}                          # tool_call_id → 도구 이름(Ollama는 id 대신 이름을 쓴다)
    out: list[dict] = []
    for m in messages:                                       # 메시지마다
        role = m.get("role", "user")                         # 역할
        text = text_of(m.get("content"))                     # 글
        if role == "assistant" and m.get("tool_calls"):      # 이전 턴에 모델이 도구를 불렀던 기록
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                id_to_name[tc.get("id", "")] = fn.get("name", "")   # 결과 메시지와 짝지으려고 기억
                calls.append({"name": fn.get("name", ""), "arguments": parse_args(fn.get("arguments"))})
            if tool_mode == "native":                        # 네이티브: Ollama 형식의 tool_calls로
                out.append({"role": "assistant", "content": text,
                            "tool_calls": [{"function": c} for c in calls]})
            else:                                            # 프롬프트 방식: 모델이 썼던 형식 그대로 글로
                out.append({"role": "assistant", "content": text + "".join(
                    f"<tool_call>{json.dumps(c, ensure_ascii=False)}</tool_call>" for c in calls)})
        elif role == "tool":                                 # 도구 실행 결과(Continue가 실행하고 돌려준 것)
            name = id_to_name.get(m.get("tool_call_id", ""), "")
            if tool_mode == "native":
                out.append({"role": "tool", "content": text, "tool_name": name})   # Ollama의 도구 결과 형식
            else:
                out.append({"role": "user", "content": f'<tool_result name="{name}">\n{text}\n</tool_result>'})  # 글로 전달
        else:                                                # system·user·assistant 일반 글
            out.append({"role": "system" if role == "developer" else role, "content": text})   # developer는 system으로
    if tool_mode == "prompt" and tools:                      # 프롬프트 방식이면 도구 설명을 시스템 글에 붙인다
        prompt = build_tool_prompt(tools)
        if out and out[0]["role"] == "system":
            out[0] = {**out[0], "content": out[0]["content"] + "\n\n" + prompt}   # 기존 시스템 글 뒤에
        else:
            out.insert(0, {"role": "system", "content": prompt})                  # 없으면 맨 앞에 새로
    return out


def ollama_options(body: dict, num_ctx: int) -> dict:
    """OpenAI 생성 옵션 → Ollama options."""
    opts: dict = {"num_ctx": num_ctx}                        # 컨텍스트 길이(gemma4-26b-32k → 32768)
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("seed", "seed"),
                     ("max_tokens", "num_predict"), ("max_completion_tokens", "num_predict")):   # 이름 대응
        if body.get(src) is not None:
            opts[dst] = body[src]
    if body.get("stop"):                                     # 멈춤 문자열(문자열 하나 또는 목록)
        opts["stop"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    return opts


def extract_tool_calls(text: str) -> tuple[str, list[dict]]:
    """프롬프트 방식 출력 → (도구 표시를 뺀 글, [{"name","arguments"}])."""
    calls = []
    for raw in TOOL_CALL_RE.findall(text):                   # <tool_call>{...}</tool_call>마다
        try:
            obj = json.loads(raw)
        except ValueError:
            continue                                         # 깨진 JSON은 무시
        if isinstance(obj, dict) and obj.get("name"):
            calls.append({"name": obj["name"], "arguments": parse_args(obj.get("arguments"))})
    return TOOL_CALL_RE.sub("", text).strip(), calls         # 표시를 지운 나머지 글


def native_calls(message: dict) -> list[dict]:
    """Ollama 응답의 tool_calls([{"function":{"name","arguments":dict}}]) → [{"name","arguments"}]."""
    return [{"name": tc.get("function", {}).get("name", ""), "arguments": parse_args(tc.get("function", {}).get("arguments"))}
            for tc in (message.get("tool_calls") or [])]


def openai_tool_calls(calls: list[dict], with_index: bool = False, start: int = 0) -> list[dict]:
    """[{"name","arguments"}] → OpenAI tool_calls. ★ arguments는 JSON '문자열', id는 결과와 짝지을 고유값."""
    out = []
    for i, c in enumerate(calls):
        item = {"id": f"call_{uuid.uuid4().hex[:16]}", "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
        if with_index:                                       # 스트리밍 조각에는 몇 번째 도구 호출인지(index)가 필요
            item["index"] = start + i
        out.append(item)
    return out


def usage_from(final: dict) -> dict:
    """③ Ollama 마지막 응답의 토큰 수 → OpenAI usage."""
    p, c = int(final.get("prompt_eval_count") or 0), int(final.get("eval_count") or 0)   # 입력·출력 토큰
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def finish_reason(done_reason: str | None, has_tool_calls: bool) -> str:
    """끝난 이유: 도구 호출이면 'tool_calls'(Continue가 도구를 실행하라는 신호), 길이 초과면 'length'."""
    if has_tool_calls:
        return "tool_calls"
    return "length" if done_reason == "length" else "stop"


def completion_response(model: str, content: str, calls: list[dict], final: dict) -> dict:
    """① 한 번에 받는(stream=false) OpenAI 응답 모양."""
    message: dict = {"role": "assistant", "content": content or None}   # 도구만 부르면 content는 null
    if calls:
        message["tool_calls"] = openai_tool_calls(calls)
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason(final.get("done_reason"), bool(calls))}],
            "usage": usage_from(final)}


def chunk(cid: str, model: str, delta: dict | None, finish: str | None = None, usage: dict | None = None) -> str:
    """② 스트리밍 SSE 한 조각: 'data: {...}\\n\\n'. usage 조각은 choices가 빈 목록이다(OpenAI 규칙)."""
    obj = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
           "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        obj["usage"] = usage
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
