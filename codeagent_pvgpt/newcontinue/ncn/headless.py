# ncn/headless.py — 헤드리스(-p) 출력 형식과 종료 코드. cn extensions/cli/src/commands/chat.ts(processJsonOutput,
#   stripThinkTags, processAndOutputResponse, handleAutoCompaction 콜백, handleManualCompaction, chat() 오류 처리)
#   + util/exit.ts(gracefulExit) + index.ts(신호)
# 실제 조립(설정 읽기·서비스 만들기·run_turn 호출)은 통합 단계에서 한다. 여기는 글 만들기·코드 정하기만.
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # 정규식
from typing import Optional                                  # 타입

from ncn.llm.client import js_parse, js_stringify            # JSON.parse / JSON.stringify와 같게

# ── 종료 코드(P0 2.8) ──
EXIT_OK = 0                                                  # 정상(헤드리스 권한 거부로 턴이 일찍 끝나도 0)
EXIT_ERROR = 1                                               # 실행 중 오류·플래그 오류·프롬프트 없음·Exit 도구
EXIT_SIGINT = 130                                            # Ctrl+C — Ctrl+C 로 끝날 때의 종료 코드
EXIT_SIGTERM = 0                                             # SIGTERM은 0(index.ts:388-390)

HEADLESS_REQUIRES_PROMPT = ('Headless mode requires a prompt. Use: cn -p "your prompt"\n'  # 헤드리스인데 프롬프트가 없을 때의 오류 문구
                            'Or pipe input: echo "prompt" | cn -p\n'
                            "Or use agent files: cn -p --agent my-org/my-agent\n"
                            "Note: Agent files must contain a prompt field.")   # chat.ts:500-505 원문(오류 메시지)

# JS 공백 문자(\s·trim 기준): 공백·탭·줄바꿈류 + 유니코드 공백 + BOM. Python의 \s·strip()과 범위가 달라 직접 정의
_JS_WS = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"  # JS 공백 문자 모음(trim·\s 기준)
_JS_WS_CLASS = "[" + re.escape(_JS_WS) + "]"                 # 정규식 문자 집합


def js_trim(s: str) -> str:                                 # JS trim 대응 함수
    """String.prototype.trim()."""
    return s.strip(_JS_WS)                                  # JS 공백 집합으로 양끝 제거


def process_json_output(response: str) -> str:              # --format json 처리 함수
    """--format json: 다듬은 응답이 JSON이면 그대로, 아니면 {"response","status","note"}로 감싼다."""
    trimmed = js_trim(response)                              # 앞뒤 공백 제거
    try:                                                    # 응답이 JSON 인지 시험
        js_parse(trimmed)                                    # JSON인가
        return trimmed                                      # JSON 이면 다듬은 글 그대로
    except ValueError:                                      # JSON 이 아니면
        return js_stringify({"response": trimmed, "status": "success",  # {"response","status","note"} 객체로 감싸 문자열화
                             "note": "Response was not valid JSON, so it was wrapped in a JSON object"})


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>")           # /<think>[\s\S]*?<\/think>/g ([\s\S]는 아무 글자)
_BLANKS_RE = re.compile(r"\n" + _JS_WS_CLASS + r"*\n" + _JS_WS_CLASS + r"*\n")   # /\n\s*\n\s*\n/g — 빈 줄 3개 이상 연속을 찾는 정규식


def strip_think_tags(response: str) -> str:                 # --silent 처리: think 태그 제거 함수
    """--silent: <think>…</think> 제거, 빈 줄 3개 이상 연속 → 2개, 앞뒤 공백 제거."""
    cleaned = _THINK_RE.sub("", response)                    # 생각 블록 제거
    cleaned = _BLANKS_RE.sub("\n\n", cleaned)                # 빈 줄 줄이기(한 번 훑기 — JS replace /g와 같음)
    return js_trim(cleaned)                                 # 양끝 공백 제거 후 반환


def format_headless_output(final_response: str, *, silent: bool = False, format: Optional[str] = None) -> Optional[str]:  # stdout 에 쓸 최종 글을 만드는 함수
    """processAndOutputResponse: stdout에 쓸 글(끝에 \\n 포함). 응답이 비었거나 공백뿐이면 None(아무것도 안 씀)."""
    if not final_response or not js_trim(final_response):    # 빈 응답
        return None                                         # 쓸 내용이 없으면 None
    out = final_response                                    # 기본은 응답 그대로
    if silent:                                               # --silent — 조용히 모드면
        out = strip_think_tags(out)                         # think 블록 제거
    if format == "json":                                     # --format json — json 형식이면
        out = process_json_output(out)                      # JSON 으로 감싸거나 그대로
    return out + "\n"                                       # 끝에 줄바꿈 하나 붙여 반환


def format_error_output(message: str) -> str:               # 헤드리스 오류 출력 함수
    """헤드리스 오류: stderr에 {"status":"error","message":…} 한 줄(chat.ts:618-626)."""
    return js_stringify({"status": "error", "message": message}) + "\n"  # {"status":"error","message":…} 한 줄


def error_message(err: BaseException) -> str:               # 예외에서 메시지 뽑는 함수
    """err.message || String(err)."""
    m = getattr(err, "message", None)                        # LLMError 등
    if isinstance(m, str) and m:                            # message 가 비어 있지 않은 문자열이면
        return m                                            # 그 메시지 사용
    s = str(err)                                            # 아니면 str(err)
    return s if s else f"{type(err).__name__}"              # 그것도 비면 예외 클래스 이름


def format_auto_compaction_line(message: str, *, headless: bool, format: Optional[str],  # 자동 압축 안내 한 줄 만드는 함수(chat.ts)
                                context_usage_percent: Optional[int] = None) -> Optional[str]:  # context_usage_percent 는 json 안내에 들어가는 사용률
    """헤드리스 사용자 메시지 전 자동 압축 안내(chat.ts:221-275). --format json일 때만 stdout에 JSON 한 줄. 그 밖은 None."""
    if not headless or format != "json":                     # 헤드리스+json이 아니면 stdout에 안 씀
        return None                                         # stdout 에 쓰지 않음
    if "Auto-compacting" in message or "Approaching" in message:  # 자동 압축 시작/임박 안내
        return js_stringify({"status": "info", "message": "Auto-compacting triggered",  # info 상태 JSON 한 줄
                             "contextUsage": f"{context_usage_percent}%"}) + "\n"
    if "✓" in message:                                      # 압축 성공 표시(✓)
        return js_stringify({"status": "success", "message": "Auto-compacted successfully"}) + "\n"  # success 상태 JSON 한 줄
    if "Warning:" in message:                               # 압축 실패 경고
        return js_stringify({"status": "warning", "message": "Auto-compaction failed, continuing without compaction"}) + "\n"  # warning 상태 JSON 한 줄
    return None                                             # 그 밖의 메시지는 출력하지 않음


def format_manual_compaction_line(history_length: Optional[int] = None, error: Optional[str] = None) -> str:  # 헤드리스 /compact 결과 한 줄 만드는 함수
    """헤드리스 /compact 결과(chat.ts:139-160): 성공 {status,message,historyLength} / 실패 {status:"error", message:"Compaction error: …"}."""
    if error is not None:                                   # 오류가 있으면
        return js_stringify({"status": "error", "message": f"Compaction error: {error}"}) + "\n"  # error 상태 JSON 한 줄
    return js_stringify({"status": "success", "message": "Chat history compacted", "historyLength": history_length}) + "\n"  # 성공 JSON 한 줄(기록 길이 포함)


def final_exit_code(code: int, had_unhandled_error: bool) -> int:  # 종료 코드 최종 결정 함수(gracefulExit)
    """gracefulExit: 0으로 끝나려는데 처리 안 된 오류가 있었으면 1."""
    return 1 if code == 0 and had_unhandled_error else code  # 정상 종료인데 미처리 오류가 있었으면 1, 아니면 그대로
