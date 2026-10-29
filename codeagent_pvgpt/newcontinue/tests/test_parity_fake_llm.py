# tests/test_parity_fake_llm.py — 녹화형 가짜 LLM이 OpenAI 스트리밍 규격대로 재생·녹화하는지 확인
import json                                                  # 응답 해석
import urllib.request                                        # 표준 HTTP 클라이언트(추가 의존성 없음)
from pathlib import Path                                     # 시나리오 파일

from parity.fake_llm import END_TEXT, serve                  # 시험 대상

SCEN = Path(__file__).parent.parent / "parity" / "scenarios"  # 시나리오 폴더


def post(port: int, body: dict) -> bytes:
    """채팅 요청을 보내고 원본 응답 바이트를 돌려준다."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",  # 요청 객체 만들기
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})  # 본문은 JSON, 헤더로 형식 알림
    with urllib.request.urlopen(req) as r:                   # 응답이 올 때까지 대기
        return r.read()                                      # 원본 바이트 반환


def collect_stream(raw: bytes) -> tuple[str, dict, str, dict | None]:
    """SSE를 실제 클라이언트처럼 이어 붙인다 → (글, {index: 도구 호출}, 종료 사유, 사용량)."""
    text, calls, finish, use = "", {}, None, None            # 글·호출·종료 사유·사용량 누적 변수
    for line in raw.decode().splitlines():                   # SSE 줄 단위로 훑기
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue                                         # 데이터 줄이 아니거나 종료 표식이면 건너뜀
        c = json.loads(line[6:])                             # 조각
        use = c.get("usage") or use                          # 사용량 조각
        for ch in c["choices"]:                              # 선택지마다
            d = ch["delta"]                                  # 델타(증분) 조각
            text += d.get("content") or ""                   # 글 이어 붙이기
            for tc in d.get("tool_calls") or []:             # 도구 호출: index 기준으로 모음
                cur = calls.setdefault(tc["index"], {"id": None, "name": "", "arguments": ""})  # index별 호출 슬롯 확보
                cur["id"] = tc.get("id") or cur["id"]        # id는 첫 조각에만 있음
                cur["name"] += (tc.get("function") or {}).get("name") or ""  # 함수 이름 이어 붙이기
                cur["arguments"] += (tc.get("function") or {}).get("arguments") or ""  # 인자 문자열 이어 붙이기
            finish = ch.get("finish_reason") or finish       # 종료 사유 갱신
    return text, calls, finish, use                          # 이어 붙인 결과 반환


def test_all_scenarios_are_valid_json_with_required_keys():
    for f in sorted(SCEN.glob("*.json")):                    # 모든 시나리오
        s = json.loads(f.read_text(encoding="utf-8"))        # JSON 읽기
        assert {"name", "workspace", "prompt", "llm", "expect"} <= s.keys(), f.name   # 필수 키
        assert s["name"] == f.stem                           # 파일 이름 = 시나리오 이름
        assert s["expect"]["requests"] == len(s["llm"])      # 기대 요청 수 = 준비한 응답 수


def test_replay_stream_tool_call_then_text_and_record():
    s = json.loads((SCEN / "03_read_edit_auto.json").read_text(encoding="utf-8"))
    server, rec = serve(s["llm"])                            # 빈 포트로 띄움
    port = server.server_port                                # 서버가 잡은 포트
    try:                                                     # 서버 종료를 보장하려 try
        body = {"model": "m", "stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "x"}]}  # 스트리밍 + 사용량 요청 본문
        text, calls, finish, use = collect_stream(post(port, body))   # 1번째: Read 호출
        assert finish == "tool_calls" and text == ""         # 도구 호출이므로 글은 비어 있음
        assert calls[0]["id"] == "call_0_0" and calls[0]["name"] == "Read"  # 결정적 id와 도구 이름 확인
        assert json.loads(calls[0]["arguments"]) == {"filepath": "coupon.py"}   # 두 조각으로 나뉜 인자가 다시 합쳐짐
        assert use["total_tokens"] == 0                      # include_usage → 사용량 조각 있음
        collect_stream(post(port, {**body, "stream_options": None}))   # 2번째: Edit
        text, calls, finish, use = collect_stream(post(port, {**body, "stream_options": None}))   # 3번째: 글
        assert text == "20%로 바꿨습니다." and finish == "stop" and use is None   # 사용량 요청 안 하면 없음
        text, _, _, _ = collect_stream(post(port, body))     # 4번째: 준비된 응답 없음
        assert text == END_TEXT                              # 시나리오 끝 글 확인
        assert len(rec.requests) == 4 and rec.requests[0]["body"]["messages"][0]["content"] == "x"   # 전부 녹화됨
    finally:                                                 # 항상 서버 종료
        server.shutdown()                                    # 서버 종료


def test_non_stream_and_error_status():
    server, rec = serve([{"status": 503, "content": "busy"}, {"content": "ok"}])   # 첫 요청은 503(재시도 시험용)
    port = server.server_port                                # 서버 포트
    try:                                                     # 서버 종료 보장
        try:                                                 # 503 예외 기대
            post(port, {"model": "m", "messages": []})       # 비스트리밍 요청
            assert False, "503이어야 함"                         # 여기 오면 실패
        except urllib.error.HTTPError as e:                  # HTTP 오류 잡기
            assert e.code == 503                             # 상태 코드 503
        r = json.loads(post(port, {"model": "m", "messages": []}))   # 비스트리밍
        assert r["choices"][0]["message"]["content"] == "ok" and r["choices"][0]["finish_reason"] == "stop"  # 본문과 종료 사유 확인
    finally:                                                 # 항상 정리
        server.shutdown()                                    # 서버 종료
