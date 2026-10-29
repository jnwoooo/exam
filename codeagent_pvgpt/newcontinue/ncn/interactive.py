# ncn/interactive.py — 콘솔 대화형 모드(`ncn`, `ncn "첫 메시지"`). 계획 3장(단순 대화형)·9-1(대화형 입력 표)·14-5(모든 OS ANSI)
# 동작 기준은 대화형 cn: ui/hooks/useChat.ts(processMessage·handleUserMessage·handleInterrupt·handleToolPermissionResponse·handleResumeRequest)
#   + useChat.helpers.ts(processSlashCommandResult·formatMessageWithFiles) + useChat.shellMode.ts(! 셸 모드) + stream/messageQueue.ts(바쁠 때 줄 세우기)
#   + ui/UserInput.tsx(Enter·줄 끝 \ 이어쓰기·Esc) + ui/hooks/useUserInput.ts(Ctrl+C·Ctrl+D·Ctrl+L·Ctrl+B·Shift+Tab) + index.ts(enableSigintHandler 두 번 Ctrl+C)
#   + ui/components/ToolPermissionSelector.tsx(승인 3가지·키) + ui/components/QuizPrompt.tsx(AskQuestion) + ui/TUIChat.tsx(/resume·/export)
#   + util/inputHistory.ts(입력 기록 파일 ~/.continue/input_history.json)
# 세션 조립은 ncn/chat_session.py(ACP 와 공용) — 요청 본문(시스템 메시지·도구 목록)이 ACP·대화형 cn 과 같다.
# 화면: prompt_toolkit 입력 줄 하나를 계속 띄워 두고(바쁠 때도 입력 가능 → 줄 세움), 출력은 그 위에 줄 단위로 찍는다(patch_stdout).
#   승인·질문·세션 고르기는 입력 줄의 머리글(message)로 보여 주고 키로 고른다. 맨 아래 줄(bottom_toolbar)에 모드·상태.
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄 결과 형식
import asyncio                                               # 비동기
import contextlib                                            # nullcontext 대응
import json                                                  # 입력 기록·세션 목록 파일
import logging                                               # 로그
import os                                                    # 경로·환경변수
import random                                                # 셸 모드 호출 id
import string                                                # 셸 모드 호출 id 글자
import sys                                                   # 표준 출력
import time                                                  # 두 번 Ctrl+C 간격
from collections import deque                                # 메시지 줄(messageQueue)
from dataclasses import dataclass                            # 고르기 상태
from typing import Any, Optional                             # 타입

from ncn import console_render as cr                         # 화면 글(ANSI)
from ncn.slash_commands import SlashContext, all_slash_commands, handle_slash_commands   # 슬래시 명령

log = logging.getLogger("ncn.interactive")                   # 로거

CANCEL_GRACE_S = 1.0                                         # 중단 뒤 턴이 스스로 끝나기를 기다리는 시간(초) — 넘으면 작업 취소(ACP 와 같음)
DOUBLE_CTRL_C_S = 1.0                                        # index.ts enableSigintHandler: 1초 안에 두 번이면 종료
QUEUE_DELAY_S = 0.1                                          # useChat.ts: 줄 선 메시지 처리 전 100ms(화면 갱신 여유)
MAX_INPUT_HISTORY = 1000                                     # inputHistory.ts MAX_HISTORY_SIZE 대응
TOOL_CANCELED_SYSTEM_MESSAGE = "[Tool canceled - please tell Continue what to do differently]"   # useChat.ts 원문(3번 선택 시 기록에 넣는 system 글)
PERMISSION_OPTIONS = [("Continue", "green", "(tab)"),        # ToolPermissionSelector.tsx 원문 이름·색·단축키 — 1 계속
                      ("Continue + don't ask again", "cyan", "(shift+tab)"),   # 2 계속 + 다시 묻지 않기
                      ("No, and tell Continue what to do differently", "yellow", "(esc)")]   # 3 아니오
TTYLESS_ERROR = ("Cannot start TUI in TTY-less environment. No TTY available for interactive mode.\n"   # ui/index.ts 원문 오류 글
                 "For non-interactive use, run with -p flag:\n"   # (이어짐)
                 '  cn -p "your prompt here"')               # (끝)


# ─────────────────────────────────────────────────────────────────────────────
# 입력 기록(util/inputHistory.ts — cn 과 같은 파일·형식)
# ─────────────────────────────────────────────────────────────────────────────


def _make_history(path: str):                                # prompt_toolkit History 를 cn 입력 기록 파일에 잇는 함수
    """~/.continue/input_history.json([{text, timestamp}], 최근 것이 앞, 같은 글은 하나만, 최대 1000개)."""
    from prompt_toolkit.history import History               # 기록 기본 클래스

    class CnInputHistory(History):                           # cn 형식 기록
        def load_history_strings(self):                      # 최근 것부터 돌려줌(prompt_toolkit 규약과 cn 순서가 같다)
            for e in _read_input_history(path):              # 항목마다
                if isinstance(e, dict) and isinstance(e.get("text"), str):   # 올바른 항목만
                    yield e["text"]                          # 글

        def store_string(self, text: str) -> None:           # addEntry 항목
            if not text.strip():                             # 빈 글은 저장 안 함
                return                                       # 끝
            items = [e for e in _read_input_history(path) if not (isinstance(e, dict) and e.get("text") == text)]   # 같은 글 제거
            items.insert(0, {"text": text, "timestamp": int(time.time() * 1000)})   # 맨 앞에
            try:                                             # 저장 실패는 무시(cn: console.error)
                os.makedirs(os.path.dirname(path), exist_ok=True)   # 폴더
                with open(path, "w", encoding="utf-8", newline="") as fh:   # 파일 쓰기
                    fh.write(json.dumps(items[:MAX_INPUT_HISTORY], ensure_ascii=False, indent=2))   # JSON.stringify(…, null, 2) — 최대 1000개
            except OSError as e:                             # 쓰기 실패
                log.debug("Failed to save input history: %s", e)   # 기록만

    return CnInputHistory()                                  # 기록 객체


def _read_input_history(path: str) -> list:                  # 입력 기록 파일 읽기
    """없거나 깨졌으면 빈 목록."""
    try:                                                     # 읽기 실패 무시
        with open(path, encoding="utf-8") as fh:             # 파일 열기
            data = json.load(fh)                             # JSON 대응
        return data if isinstance(data, list) else []        # 목록만
    except (OSError, ValueError):                            # 없음·깨짐
        return []                                            # 빈 목록


def list_sessions(environ: Any, limit: int = 20) -> list[dict]:   # session.ts listSessions(로컬만)
    """sessions.json 목록 중 파일이 있는 것을 수정 시각 최근 순으로. 첫 사용자 메시지(미리보기)도 붙인다."""
    from ncn.session import sessions_folder                  # 세션 폴더
    d = sessions_folder(environ)                             # 폴더
    try:                                                     # 목록 파일
        with open(os.path.join(d, "sessions.json"), encoding="utf-8") as fh:   # sessions.json 대응
            metas = json.load(fh)                            # 목록
    except (OSError, ValueError):                            # 없음·깨짐
        metas = []                                           # 빈 목록
    out = []                                                 # 결과
    for m in metas if isinstance(metas, list) else []:       # 항목마다
        p = os.path.join(d, f"{m.get('sessionId')}.json")    # 세션 파일
        if not os.path.exists(p):                            # 파일이 없으면
            continue                                         # 건너뜀
        first = ""                                           # 첫 사용자 메시지
        try:                                                 # 미리보기 읽기
            with open(p, encoding="utf-8") as fh:            # 세션 파일
                hist = json.load(fh).get("history") or []    # 기록
            first = next((_content_text(it["message"].get("content")) for it in hist if it.get("message", {}).get("role") == "user"), "")   # 첫 user 글
        except (OSError, ValueError, KeyError, AttributeError):   # 깨진 파일
            pass                                             # 미리보기 없음
        out.append({**m, "mtime": os.path.getmtime(p), "firstUserMessage": first})   # 결과 항목
    out.sort(key=lambda x: x["mtime"], reverse=True)         # 최근 먼저
    return out[:limit]                                       # 앞 limit 개


def _content_text(content: Any) -> str:                      # 메시지 내용 → 글(convertMessageContentToString)
    """글이면 그대로, 조각 목록이면 text 를 잇고 그림은 [Image]."""
    if isinstance(content, str):                             # 글
        return content                                       # 그대로
    if isinstance(content, list):                            # 조각 목록
        return "".join(p.get("text", "") if p.get("type") == "text" else "[Image]" if p.get("type") == "imageUrl" else ""   # 조각마다
                       for p in content if isinstance(p, dict))   # (이어짐)
    return ""                                                # 그 밖


# ─────────────────────────────────────────────────────────────────────────────
# 고르기 상태(승인·질문·세션 목록)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass   # 자료 클래스로 선언
class _Choice:                                               # 지금 입력 줄이 받고 있는 고르기 하나
    """kind: perm(승인 3가지) | quiz(AskQuestion) | select(세션 고르기). fut 에 결과(번호·답·None)를 넣는다."""
    kind: str                                                # 종류
    options: list                                            # 선택지 글
    fut: asyncio.Future                                      # 결과 자리
    index: int = 0                                           # 지금 고른 줄
    custom: bool = False                                     # quiz: 직접 입력 중(입력 줄 버퍼 사용)
    default: Optional[str] = None                            # quiz: 기본 답
    title: str = ""                                          # select: 머리 글


# ─────────────────────────────────────────────────────────────────────────────
# 대화형 앱
# ─────────────────────────────────────────────────────────────────────────────


class InteractiveApp:                                        # 콘솔 대화형 한 번 실행
    """세션 조립(chat_session) + prompt_toolkit 입력 줄 + 턴 실행(run_turn) + 승인·질문·슬래시 명령.
    시험에서는 input=create_pipe_input(), output=DummyOutput(), out=StringIO 로 바꿔 끼운다."""

    def __init__(self, args: argparse.Namespace, environ: Optional[dict] = None, *, input: Any = None, output: Any = None,   # 명령줄·환경·입출력
                 out: Any = None, color: Optional[bool] = None, patch_stdout: Optional[bool] = None):   # 출력 글 스트림·색·patch_stdout 여부
        self.args = args                                     # 명령줄 결과
        self.environ = os.environ if environ is None else environ   # 환경변수(dotenv 적용됨)
        self.pt_input, self.pt_output = input, output        # prompt_toolkit 입출력(None 이면 진짜 단말)
        self._out = out                                      # 출력 글 스트림(None 이면 그때의 sys.stdout — patch_stdout 대리자)
        self.use_patch = (out is None) if patch_stdout is None else patch_stdout   # 진짜 단말이면 입력 줄 위로 찍기
        if color is None:                                    # 색 자동 판단
            color = not self.environ.get("NO_COLOR") and (out is None and getattr(sys.stdout, "isatty", lambda: False)())   # 단말이고 NO_COLOR 아님
        self.style = cr.Style(bool(color))                   # 색
        self.md = cr.MarkdownStream(self.style)              # 응답 글 강조기
        self.core: Any = None                                # ChatCore(조립 뒤)
        self.ps: Any = None                                  # PromptSession 항목
        self.signal: Any = None                              # 지금 턴의 AbortSignal
        self.turn_task: Optional[asyncio.Task] = None        # 지금 턴 작업
        self.busy = False                                    # 응답·압축 중(isWaitingForResponse || isCompacting)
        self.compacting = False                              # /compact 중
        self.queue: deque = deque()                          # 줄 선 메시지(messageQueue)
        self.was_interrupted = False                         # 중단됐나(빈 Enter 로 이어 가기 — handleResumeRequest)
        self.rejected = False                                # 이번 턴에 3번(아니오)을 골랐나
        self.choice: Optional[_Choice] = None                # 지금 고르기
        self.ask_lock = asyncio.Lock()                       # 승인·질문을 하나씩(병렬 도구 대비)
        self.exiting = False                                 # 종료 요청됨
        self.exit_code = 0                                   # 종료 코드
        self._last_ctrl_c = 0.0                              # 마지막 Ctrl+C 시각
        self._exit_hint_until = 0.0                          # "ctrl+c to exit" 보일 때까지
        self._msg_open = False                               # 응답 글이 줄 머리(●)를 이미 찍었나
        self.subagent_line = ""                              # Subagent 진행 표시(맨 아래 줄)
        self.tool_names: dict = {}                           # 호출 id → 도구 이름(결과 요약용)
        self.slash: Optional[SlashContext] = None            # 슬래시 명령 부품
        self._bg: set = set()                                # 셸 모드 등 떠 있는 작업(끝날 때 정리)

    # ───────── 출력 ─────────
    @property   # 읽기 전용 속성
    def out(self) -> Any:                                    # 지금 쓸 출력 스트림
        """주입된 스트림, 없으면 지금의 sys.stdout(patch_stdout 이 바꿔 둔 대리자)."""
        return self._out if self._out is not None else sys.stdout   # 스트림

    def write(self, line: str = "") -> None:                 # 한 줄 쓰기
        """줄 하나(끝에 줄바꿈). 입력 줄이 떠 있으면 patch_stdout 이 그 위에 올린다."""
        try:                                                 # 출력 실패(닫힌 스트림) 무시
            self.out.write(line + "\n")                      # 쓰기
            self.out.flush()                                 # 바로 내보냄
        except (OSError, ValueError):                        # 닫힘
            pass                                             # 무시

    def _flush_md(self) -> None:                             # 남은 응답 글 마무리
        """줄바꿈 없이 끝난 응답 글을 찍는다."""
        for ln in self.md.flush():                           # 남은 줄
            self._write_content_line(ln)                     # 찍기
        self._msg_open = False                               # 다음 글은 새 머리

    def _write_content_line(self, ln: str) -> None:          # 응답 글 한 줄 찍기
        """첫 줄은 '● '(cn MemoizedMessage 머리), 다음 줄은 두 칸 들여쓰기."""
        self.write(("● " if not self._msg_open else "  ") + ln)   # 머리 또는 들여쓰기
        self._msg_open = True                                # 머리 찍음

    def _invalidate(self) -> None:                           # 입력 줄 다시 그리기
        """머리글·맨 아래 줄이 바뀌었을 때."""
        app = getattr(self.ps, "app", None) if self.ps is not None else None   # 앱
        if app is not None and app.is_running:               # 떠 있으면
            app.invalidate()                                 # 다시 그림

    # ───────── 시작 ─────────
    def _select_session(self, sm: Any) -> tuple[str, list]:  # useChat.ts useState(currentSession) — --fork / --resume / 새 세션
        """--fork: 그 기록으로 새 id(못 찾으면 빈 기록 — cn 그대로), --resume: 가장 최근 세션(없으면 새), 아니면 새 세션."""
        from ncn.session import history_manager_load         # 세션 파일 읽기
        a = self.args                                        # 명령줄
        if a.fork:                                           # --fork 항목
            forked = history_manager_load(a.fork, self.environ)   # HistoryManager.load(없으면 빈 세션)
            s = sm.start_new(forked.get("history") or [])    # startNewSession(새 uuid)
            return s["sessionId"], s["history"]              # 새 id·복제 기록
        if a.resume:                                         # --resume 항목
            s = sm.load_latest()                             # 가장 최근 세션
            if s:                                            # 있으면
                return s["sessionId"], s.get("history") or []   # 그 id·기록(이후 저장도 그 파일)
        cur = sm.get_current()                               # createSession([]) — 새 세션
        return cur["sessionId"], []                          # 새 id·빈 기록

    async def _open(self) -> bool:                           # 세션 조립 + MCP 접속
        """실패하면 'Fatal error: …'(cn chat.ts 대화형 오류 글)를 stderr 에 쓰고 False."""
        from ncn.chat_session import SessionOpenError, connect_mcp, open_chat_core   # 공용 조립
        try:                                                 # 조립
            self.core = open_chat_core(self.args, self.environ, os.path.abspath(os.getcwd()), select_session=self._select_session,   # 대화형 조립
                                       on_event=self._on_event, ask_user=self._ask_user,   # 이벤트·승인
                                       get_signal=lambda: self.signal,   # 하위 대화도 같은 중단 신호
                                       on_subagent_progress=self._on_subagent_progress)   # Subagent 진행
        except SessionOpenError as e:                        # 설정 오류·모델 없음
            sys.stderr.write(self.style.paint(f"Fatal error: {e.message}", "red") + "\n")   # cn: logging.error(chalk.red(`Fatal error: …`)) 대응
            self.exit_code = 1                               # gracefulExit(1) 대응
            return False                                     # 실패
        await connect_mcp(self.core, self.environ)           # 설정 mcpServers(실패해도 계속)
        self.slash = SlashContext(self.core, self.style, self.environ)   # 슬래시 명령 부품
        from ncn.tools.ask_question import quiz_service      # AskQuestion 질문 서비스(모듈 공용)
        quiz_service.on_question_requested.append(self._on_question)   # 질문이 걸리면 콘솔에서 묻는다
        return True                                          # 성공

    def _build_prompt(self) -> Any:                          # PromptSession 만들기
        """입력 줄: 여러 줄 버퍼(Enter 는 직접 처리), cn 입력 기록 파일, 슬래시 명령 Tab 완성, 맨 아래 줄."""
        from prompt_toolkit import PromptSession             # 입력 줄
        from prompt_toolkit.completion import Completer, Completion   # Tab 완성
        from prompt_toolkit.formatted_text import ANSI       # ANSI 글 → 화면 글
        from prompt_toolkit.styles import Style as PTStyle   # 화면 스타일
        from ncn.config import paths as cpaths               # 전역 폴더

        app = self                                           # 안쪽 클래스에서 쓰기

        class SlashCompleter(Completer):                     # '/' 명령 완성(cn SlashCommandUI 대신 Tab 완성)
            def get_completions(self, document, complete_event):   # 완성 후보
                t = document.text_before_cursor              # 커서 앞 글
                if not t.startswith("/") or " " in t or app.slash is None:   # 명령 이름을 쓰는 중이 아니면
                    return                                   # 후보 없음
                for name in all_slash_commands(app.slash):   # 명령마다
                    if name.lower().startswith(t[1:].lower()):   # 앞부분이 맞으면
                        yield Completion("/" + name, start_position=-len(t))   # 후보

        gdir = self.core.gdir if self.core is not None else cpaths.global_dir(self.environ, os.getcwd())   # env.continueHome 대응
        kw: dict = dict(message=lambda: ANSI(self._message()), multiline=True, key_bindings=self._key_bindings(),   # 머리글·여러 줄·키
                        history=_make_history(os.path.join(str(gdir), "input_history.json")),   # cn 입력 기록 파일
                        completer=SlashCompleter(), complete_while_typing=False,   # Tab 으로만 완성
                        bottom_toolbar=lambda: ANSI(self._toolbar()), erase_when_done=True,   # 맨 아래 줄·끝나면 지움
                        prompt_continuation=lambda width, line_number, wrap_count: "  ",   # 둘째 줄부터 들여쓰기
                        style=PTStyle.from_dict({"bottom-toolbar": "noreverse"}))   # 맨 아래 줄 반전 끄기
        if self.pt_input is not None:                        # 시험 입력
            kw["input"] = self.pt_input                      # 파이프 입력
        if self.pt_output is not None:                       # 시험 출력
            kw["output"] = self.pt_output                    # DummyOutput 항목
        return PromptSession(**kw)                           # 입력 줄

    # ───────── 화면 머리글·맨 아래 줄 ─────────
    def _message(self) -> str:                               # 입력 줄 머리글(ANSI)
        """보통: '> '. 승인·질문·세션 고르기 중에는 선택지 목록 + 안내."""
        st, c = self.style, self.choice                      # 줄임
        if c is None:                                        # 보통 입력
            return st.paint("> ", "bold")                    # 입력 표시
        lines = []                                           # 머리글 줄
        if c.kind == "perm":                                 # 승인 3가지
            for i, (name, color, short) in enumerate(PERMISSION_OPTIONS):   # 선택지마다
                sel = i == c.index                           # 고른 줄인가
                label = f"{'> ' if sel else '  '}{i + 1}. {name}"   # "> 1. Continue"(번호는 ncn 추가 — 숫자 키)
                lines.append((st.paint(label, color, "bold") if sel else st.paint(label, "white")) + " " + st.paint(short, "gray"))   # 원문 색·단축키
            lines.append(st.paint("1/2/3, ↑/↓ Enter, tab/shift+tab/esc, y/n", "dim"))   # 키 안내(ncn)
            return "\n".join(lines) + "\n"                   # 끝 줄바꿈(버퍼는 비어 있음)
        if c.kind == "quiz":                                 # AskQuestion 항목
            if not c.custom:                                 # 선택지 고르기
                for i, opt in enumerate(c.options):          # 선택지마다
                    sel = i == c.index                       # 고른 줄인가
                    lines.append("  " + (st.paint(f"❯ {opt}", "cyan", "bold") if sel else st.paint(f"  {opt}", "white")))   # 원문 모양
                lines.append(st.paint("    (or start typing for custom answer)", "gray"))   # 원문
                lines.append(st.paint("↑/↓ navigate, Enter select", "gray"))   # 원문
                return "\n".join(lines) + "\n"               # 끝 줄바꿈
            buf = self.ps.default_buffer.text if self.ps is not None else ""   # 지금 친 답
            if c.default and not buf:                        # 기본 답 안내
                lines.append(st.paint(f"  (Press Enter for default: {c.default})", "gray"))   # 원문
            lines.append(st.paint("Type answer, Enter submit", "gray"))   # 원문
            return "\n".join(lines) + "\n" + st.paint("❯ ", "cyan")   # 답 입력 표시
        for i, opt in enumerate(c.options):                  # select: 세션 목록
            sel = i == c.index                               # 고른 줄인가
            lines.append((st.paint(f"> {i + 1}. {opt}", "cyan", "bold") if sel else f"  {i + 1}. {opt}"))   # 번호·글
        lines.append(st.paint("↑/↓ Enter, 1-9, Esc cancel", "dim"))   # 키 안내
        return st.paint(c.title, "bold") + "\n" + "\n".join(lines) + "\n"   # 머리 + 목록

    def _toolbar(self) -> str:                               # 맨 아래 줄(BottomStatusBar 일부)
        """모드 표시 · 상태(응답 중/줄 선 개수) · Subagent 진행 · 'ctrl+c to exit'(1초)."""
        st = self.style                                      # 색
        parts = [cr.mode_indicator(self.core.perms.mode if self.core else "normal", st)]   # 모드(ModeIndicator)
        if time.monotonic() < self._exit_hint_until:         # 첫 Ctrl+C 뒤 1초
            parts.insert(0, st.paint("ctrl+c to exit", "dim"))   # 원문
        if self.busy:                                        # 응답·압축 중
            parts.append(st.paint("Compacting…" if self.compacting else "응답 중… (esc 중단, ctrl+b 백그라운드)", "dim"))   # 상태
        if self.queue:                                       # 줄 선 메시지
            parts.append(st.paint(f"대기 {len(self.queue)}건", "yellow"))   # 개수
        if self.subagent_line:                               # Subagent 진행
            parts.append(st.paint(self.subagent_line, "dim"))   # 마지막 줄
        return " ".join(parts)                               # 한 줄

    # ───────── 키 ─────────
    def _key_bindings(self) -> Any:                          # 키 묶음 만들기
        """보통 입력·고르기·직접 입력 세 상태로 나눠 키를 건다(뒤에 단 키가 우선 — <any> 를 먼저 단다)."""
        from prompt_toolkit.filters import Condition         # 조건
        from prompt_toolkit.key_binding import KeyBindings   # 키 묶음
        from prompt_toolkit.keys import Keys                 # 키 이름
        kb = KeyBindings()                                   # 키 묶음
        picking = Condition(lambda: self.choice is not None and not self.choice.custom)   # 선택지 고르는 중
        typing_answer = Condition(lambda: self.choice is not None and self.choice.custom)   # 질문 답 직접 입력 중
        normal = Condition(lambda: self.choice is None)      # 보통 입력

        @kb.add(Keys.Any, filter=picking)                    # 고르는 중의 다른 글자
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            data = ev.data or ""                             # 친 글자
            if c.kind == "perm" and data in ("y", "Y"):      # y — 계속
                self._resolve(0)                             # 1 계속
            elif c.kind == "perm" and data in ("n", "N"):    # n — 아니오
                self._resolve(2)                             # 3 아니오
            elif c.kind == "perm" and data in ("1", "2", "3"):   # 숫자 키(ncn)
                self._resolve(int(data) - 1)                 # 그 번호
            elif c.kind == "select" and data.isdigit() and 1 <= int(data) <= len(c.options):   # 세션 번호
                self._resolve(int(data) - 1)                 # 그 번호
            elif c.kind == "quiz" and data.isprintable() and data:   # 글자를 치면 직접 입력으로(QuizPrompt)
                c.custom = True                              # 직접 입력
                ev.app.current_buffer.text = data            # 친 글자로 시작
                ev.app.current_buffer.cursor_position = len(data)   # 커서 끝
                self._invalidate()                           # 다시 그림

        @kb.add("up", filter=picking)                        # ↑ — 위 선택지
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            c.index = (len(c.options) - 1 if c.index == 0 else c.index - 1) if c.kind == "quiz" else max(0, c.index - 1)   # quiz 는 맨 위에서 맨 아래로
            self._invalidate()                               # 다시 그림

        @kb.add("down", filter=picking)                      # ↓ — 아래 선택지
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            if c.kind == "quiz" and c.index == len(c.options) - 1:   # quiz 맨 아래에서 ↓ → 직접 입력(QuizPrompt)
                c.custom, c.index = True, 0                  # 직접 입력
            else:                                            # 그 밖
                c.index = min(len(c.options) - 1, c.index + 1)   # 한 줄 아래
            self._invalidate()                               # 다시 그림

        @kb.add("enter", filter=picking)                     # Enter — 고른 것
        def _(ev):                                           # 처리기
            self._resolve(self.choice.index)                 # 지금 줄

        @kb.add("tab", filter=picking)                       # Tab — 승인: 계속
        def _(ev):                                           # 처리기
            if self.choice.kind == "perm":                   # 승인이면
                self._resolve(0)                             # 1 계속

        @kb.add("s-tab", filter=picking)                     # Shift+Tab — 승인: 계속 + 다시 묻지 않기
        def _(ev):                                           # 처리기
            if self.choice.kind == "perm":                   # 승인이면
                self._resolve(1)                             # 2 계속 + 규칙

        @kb.add("escape", filter=picking)                    # Esc — 승인: 아니오 / 질문: 턴 중단 / 세션: 닫기
        @kb.add("c-c", filter=picking)                       # Ctrl+C 도 같음(ToolPermissionSelector)
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            if c.kind == "perm":                             # 승인
                self._resolve(2)                             # 3 아니오(멈춤)
            elif c.kind == "select":                         # 세션 고르기
                self._resolve(None)                          # 닫기
            else:                                            # 질문
                self.interrupt()                             # 턴 중단(질문은 기본 답으로 정리)

        @kb.add("enter", filter=typing_answer)               # 직접 입력 답 보내기
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            ans = ev.app.current_buffer.text.strip() or c.default or ""   # 친 답 또는 기본 답
            if ans:                                          # 답이 있으면(QuizPrompt: 비면 무시)
                ev.app.current_buffer.reset()                # 입력 비움
                self._resolve((ans, True))                   # 직접 입력 답

        @kb.add("up", filter=typing_answer)                  # 빈 입력에서 ↑ → 선택지로(QuizPrompt)
        def _(ev):                                           # 처리기
            c = self.choice                                  # 고르기
            if c.options and not ev.app.current_buffer.text:   # 선택지가 있고 비었으면
                c.custom, c.index = False, len(c.options) - 1   # 맨 아래 선택지로
                self._invalidate()                           # 다시 그림

        @kb.add("escape", filter=typing_answer)              # 직접 입력 중 Esc — 턴 중단
        @kb.add("c-c", filter=typing_answer)                 # Ctrl+C 도 같음
        def _(ev):                                           # 처리기
            self.interrupt()                                 # 중단

        @kb.add("enter", filter=normal)                      # Enter — 보내기 / 줄 끝 \ 이면 이어쓰기(UserInput.tsx handleEnterKey)
        def _(ev):                                           # 처리기
            buf = ev.app.current_buffer                      # 입력 버퍼
            before = buf.document.text_before_cursor.rstrip()   # 커서 앞(끝 공백 제거)
            if before.endswith("\\"):                        # 줄 끝 역슬래시 → 새 줄
                after = buf.document.text_after_cursor       # 커서 뒤
                buf.text = before[:-1] + "\n" + after        # \ 를 줄바꿈으로
                buf.cursor_position = len(before)            # 새 줄 머리
                return                                       # 보내지 않음
            if buf.text.strip() or self.was_interrupted:     # 내용이 있거나 중단 뒤 빈 Enter(이어 가기)
                text = buf.text.strip()                      # 보낼 글
                buf.reset(append_to_history=bool(text))      # 입력 기록에 넣고 비움(inputHistory.addEntry)
                self.submit(text)                            # 보내기(바쁘면 줄 세움)

        @kb.add("escape", "enter", filter=normal)            # Alt+Enter — 새 줄(Shift+Enter 를 못 받는 단말 대신)
        @kb.add("c-j", filter=normal)                        # Ctrl+J — 새 줄(일부 단말의 Shift+Enter)
        def _(ev):                                           # 처리기
            ev.app.current_buffer.insert_text("\n")          # 줄바꿈 넣기

        @kb.add("escape", filter=normal)                     # Esc — 응답·압축 중단(handleEscapeKey)
        def _(ev):                                           # 처리기
            self.interrupt()                                 # 바쁘면 중단

        @kb.add("c-c", filter=normal)                        # Ctrl+C — 입력 비우기 + (바쁘면 중단) + 1초 안에 두 번이면 종료
        def _(ev):                                           # 처리기
            ev.app.current_buffer.reset()                    # clearInput 대응
            self.on_ctrl_c()                                 # 두 단계 종료

        @kb.add("c-d", filter=normal)                        # Ctrl+D — 바로 종료(useUserInput.ts)
        def _(ev):                                           # 처리기
            self.request_exit()                              # 종료

        @kb.add("c-l")                                       # Ctrl+L — 화면 지우기
        def _(ev):                                           # 처리기
            ev.app.renderer.clear()                          # \x1b[2J\x1b[H 와 같은 효과

        @kb.add("c-b")                                       # Ctrl+B — 실행 중 Bash 를 백그라운드로
        def _(ev):                                           # 처리기
            from ncn.tools.background_jobs import background_signal_manager   # Ctrl+B 신호
            background_signal_manager.signal_background()    # 듣는 Bash 도구에 알림

        @kb.add("s-tab", filter=normal)                      # Shift+Tab — 모드 순환 normal → plan → auto
        def _(ev):                                           # 처리기
            self.cycle_mode()                                # 순환

        return kb                                            # 키 묶음

    def _resolve(self, value: Any) -> None:                  # 고르기 끝내기
        """결과를 넣고 고르기를 닫는다."""
        c = self.choice                                      # 고르기
        self.choice = None                                   # 닫기
        if c is not None and not c.fut.done():               # 기다리는 쪽이 있으면
            c.fut.set_result(value)                          # 결과 전달
        self._invalidate()                                   # 다시 그림

    async def _pick(self, choice: _Choice) -> Any:           # 고르기 띄우고 기다리기
        """입력 줄을 고르기 상태로 바꾸고 결과를 기다린다(중단되면 None)."""
        self.choice = choice                                 # 고르기 상태
        if self.ps is not None and self.ps.app.is_running:   # 입력 줄이 떠 있으면
            self.ps.default_buffer.reset()                   # 치던 글은 비움(고르기 중 버퍼는 답 입력에만)
        self._invalidate()                                   # 다시 그림
        try:                                                 # 결과 기다림
            return await choice.fut                          # 결과
        finally:                                             # 취소돼도
            if self.choice is choice:                        # 아직 이 고르기면
                self.choice = None                           # 닫기
                self._invalidate()                           # 다시 그림

    # ───────── 키 동작 ─────────
    def cycle_mode(self) -> str:                             # UserInput.tsx cycleModes 대응
        """normal → plan → auto → normal. 다음 반복부터 도구 목록·시스템 메시지에 반영."""
        mode = self.core.perms.cycle_mode()                  # ToolPermissionService.switchMode 대응
        self._invalidate()                                   # 모드 표시 갱신
        return mode                                          # 새 모드

    def on_ctrl_c(self) -> None:                             # index.ts enableSigintHandler 대응
        """1초 안에 두 번째면 종료, 아니면 'ctrl+c to exit' 를 1초 보인다. [ncn] 응답 중이면 첫 번째에 중단도 한다(계획 9-1)."""
        now = time.monotonic()                               # 지금
        if self._last_ctrl_c and now - self._last_ctrl_c <= DOUBLE_CTRL_C_S:   # 1초 안 두 번째
            self.request_exit()                              # gracefulExit(0) 대응
            return                                           # 끝
        self._last_ctrl_c = now                              # 시각 기록
        self._exit_hint_until = now + DOUBLE_CTRL_C_S        # 안내 1초
        self.interrupt()                                     # 바쁘면 중단(ncn)
        try:                                                 # 1초 뒤 안내 지우기
            asyncio.get_running_loop().call_later(DOUBLE_CTRL_C_S + 0.05, self._invalidate)   # 다시 그림 예약
        except RuntimeError:                                 # 루프 없음(있을 수 없음)
            pass                                             # 무시
        self._invalidate()                                   # 안내 보이기

    def request_exit(self, code: int = 0) -> None:           # 종료 요청
        """입력 줄을 닫는다 → run() 이 정리(세션 저장·MCP 정리) 후 끝난다."""
        self.exiting = True                                  # 표시
        self.exit_code = code                                # 종료 코드
        if self.choice is not None:                          # 고르기 중이면
            self._resolve(None)                              # 풀기
        app = getattr(self.ps, "app", None) if self.ps is not None else None   # 앱
        if app is not None and app.is_running and not app.future.done():   # 떠 있으면
            app.exit(result=None)                            # 입력 줄 닫기

    def interrupt(self) -> bool:                             # useChat.ts handleInterrupt 대응
        """응답·압축 중이면 중단 신호. 도구가 실행 중이라 곧 안 끝나면 턴 작업을 취소. 고르기 중이면 풀어 준다."""
        if not self.busy or self.signal is None:             # 바쁘지 않으면
            return False                                     # 할 일 없음
        if self.compacting:                                  # 압축 중단이면 줄 선 메시지도 버림(cn)
            self.queue.clear()                               # setQueuedMessages([]) 대응
        self.was_interrupted = not self.compacting           # setWasInterrupted(true)(응답 중단만)
        self.signal.abort()                                  # 중단 신호(스트림·재시도·하위 대화)
        if self.choice is not None and self.choice.kind != "select":   # 승인·질문 대기 중이면
            self._resolve(None)                              # 거부·기본 답으로 풀기
        task = self.turn_task                                # 지금 턴

        def force_cancel():                                  # 도구 실행 중이면 작업 취소
            if task is not None and not task.done() and task is self.turn_task:   # 아직 같은 턴이 돌면
                task.cancel()                                # 취소(Bash 등 도구 중단)

        asyncio.get_running_loop().call_later(CANCEL_GRACE_S, force_cancel)   # 1초 뒤 확인
        self.write(self.style.paint("Interrupted", "dim"))   # 중단 표시(ncn)
        return True                                          # 중단함

    # ───────── 입력 처리 ─────────
    def submit(self, text: str) -> None:                     # UserInput.tsx onSubmit / messageQueue.enqueueMessage 대응
        """바쁘면 줄 세우고, 아니면 바로 처리한다."""
        if self.busy:                                        # 응답·압축 중
            self.queue.append(text)                          # 줄 세움
            self.write(self.style.paint(f"⏳ {text}", "gray"))   # 줄 선 메시지 표시
            self._invalidate()                               # 개수 표시
            return                                           # 끝
        self._start(text, queued=False)                      # 처리 시작

    def _start(self, text: str, *, queued: bool) -> None:    # 처리 작업 하나 시작
        """턴 작업(처리 + 끝나면 다음 줄 선 메시지)."""
        self.busy = False                                    # 슬래시 명령 등은 바쁨 아님(스트리밍·압축 때만 켬)
        self.turn_task = asyncio.ensure_future(self._process_and_drain(text, queued))   # 작업

    async def _process_and_drain(self, text: str, queued: bool) -> None:   # 처리 → 줄 선 메시지 하나씩
        """executeStreamingResponse finally — 응답이 끝나면 줄 선 것 하나를 꺼내 다시 processMessage."""
        try:                                                 # 처리
            await self.process_message(text, queued=queued)  # 메시지 하나
        except asyncio.CancelledError:                       # 중단으로 취소(도구 실행 중)
            self._flush_md()                                 # 남은 글
            if self.exiting:                                 # 종료 중이면
                raise                                        # 그대로
        except Exception as e:                               # 예상 못 한 오류
            log.debug("process error", exc_info=True)        # 기록
            self.write(self.style.paint(f"Error: {e}", "red"))   # 표시
        finally:                                             # 상태 정리
            self.busy = False                                # 바쁨 끝
            self.compacting = False                          # 압축 끝
            self._invalidate()                               # 상태 갱신
        if self.queue and not self.exiting:                  # 줄 선 메시지가 있으면
            nxt = self.queue.popleft()                       # messageQueue.getNextMessage(FIFO) 대응
            await asyncio.sleep(QUEUE_DELAY_S)               # 100ms 대응
            if not self.exiting:                             # 그 사이 종료가 아니면
                self._start(nxt, queued=True)                # 다음 처리(processQueuedMessage)

    async def process_message(self, message: str, *, queued: bool = False) -> None:   # handleUserMessage + processMessage 대응
        """빈 글(중단 뒤) → 이어 가기, ! → 셸 모드, / → 슬래시 명령, 나머지 → LLM 턴."""
        if not queued:                                       # handleUserMessage(줄 선 메시지는 바로 processMessage)
            if message == "" and self.was_interrupted:       # handleResumeRequest — 중단 뒤 빈 Enter
                await self._resume_after_interrupt()         # 마지막 user/도구까지 잘라 다시
                return                                       # 끝
            if self.was_interrupted and message != "":       # 새 글이면
                self.was_interrupted = False                 # 중단 표시 해제
        if message.strip() == "":                            # 그 밖의 빈 글
            return                                           # 할 일 없음
        self._echo_user(message)                             # 사용자 메시지 표시
        bash = self._shell_command(message)                  # handleBashModeProcessing 대응
        if bash is not None:                                 # ! 명령이면
            self._run_shell(bash)                            # 비동기 실행(화면을 막지 않음)
            return                                           # 끝
        res = handle_slash_commands(message, self.slash)     # handleSlashCommands 대응
        if res is not None:                                  # 명령이면
            if res.get("compact"):                           # /compact 항목
                await self._compact()                        # 수동 압축
                return                                       # 끝
            new_input = await self._apply_slash_result(res)  # processSlashCommandResult 대응
            if new_input is None:                            # 처리 끝
                return                                       # 끝
            message = new_input                              # newInput 으로 이어서
        await self._stream_turn(message)                     # LLM 턴

    def _echo_user(self, message: str) -> None:              # 사용자 메시지 표시(MemoizedMessage — 파랑 ● + 흐린 글)
        """입력 줄은 보낸 뒤 지워지므로(erase_when_done) 보낸 글을 위에 남긴다."""
        lines = message.split("\n")                          # 줄로
        self.write(self.style.paint("● ", "blue") + self.style.paint(lines[0], "dim"))   # 첫 줄
        for ln in lines[1:]:                                 # 나머지 줄
            self.write("  " + self.style.paint(ln, "dim"))   # 들여쓰기

    # ───────── LLM 턴 ─────────
    def _begin_turn(self) -> None:                           # 턴 시작 공통 준비
        """중단 신호·상태 초기화."""
        from ncn.llm.client import AbortSignal               # 중단 신호
        self.signal = AbortSignal()                          # 새 신호
        self.rejected = False                                # 거부 표시 초기화
        self.busy = True                                     # 바쁨(isWaitingForResponse)
        self._msg_open = False                               # 응답 머리
        self.md.reset()                                      # 강조기 비움
        self._invalidate()                                   # 상태 표시

    async def _stream_turn(self, message: str) -> None:      # executeStreamingResponse(+ run_turn 이 압축·사용자 메시지 추가)
        """대화형 한 턴. 끝나면 세션 저장(updateSessionHistory), 오류는 기록에 system 'Error: …'(cn 그대로)."""
        from ncn.headless import error_message               # 오류 글(formatError)
        from ncn.loop.agent import run_turn                  # 한 턴
        core = self.core                                     # 부품
        self._begin_turn()                                   # 준비
        try:                                                 # 턴
            await run_turn(core.history, message, core.deps, headless=False, model_name=core.model_name, signal=self.signal)   # 대화형 한 턴
            self._flush_md()                                 # 남은 글
            core.sessions.update_history(core.history.get_history())   # 세션 파일 저장
        except asyncio.CancelledError:                       # 도구 실행 중 중단
            self._flush_md()                                 # 남은 글
            raise                                            # 위에서 처리
        except Exception as e:                               # LLM·설정 오류
            self._flush_md()                                 # 남은 글
            msg = f"Error: {error_message(e)}"               # cn: `Error: ${formatError(error)}` 대응
            core.history.add_system_message(msg)             # 기록에 system 메시지(cn 그대로 — 다음 요청에 들어감)
            self.write(self.style.paint(msg, "red"))         # 표시

    async def _resume_after_interrupt(self) -> None:         # handleResumeRequest 항목
        """마지막 user 또는 도구 호출 항목까지 잘라 같은 기록으로 다시 응답을 받는다."""
        from ncn.loop.agent import stream_chat_response      # 루프(사용자 메시지 추가 없이)
        items = self.core.history.get_history()              # 기록
        idx = next((i for i in range(len(items) - 1, -1, -1)   # 끝에서부터
                    if items[i].get("message", {}).get("role") == "user" or items[i].get("toolCallStates")), -1)   # user 또는 도구 상태가 있는 항목
        if idx < 0:                                          # 없으면
            return                                           # 할 일 없음
        self.core.history.set_history(items[:idx + 1])       # 자르기
        self.was_interrupted = False                         # 해제
        self._begin_turn()                                   # 준비
        try:                                                 # 다시 응답
            await stream_chat_response(self.core.history, self.core.deps, headless=False, model_name=self.core.model_name, signal=self.signal)   # 루프
            self._flush_md()                                 # 남은 글
            self.core.sessions.update_history(self.core.history.get_history())   # 저장
        except asyncio.CancelledError:                       # 중단
            self._flush_md()                                 # 남은 글
            raise                                            # 위로
        except Exception as e:                               # 오류
            from ncn.headless import error_message           # 오류 글
            self._flush_md()                                 # 남은 글
            msg = f"Error: {error_message(e)}"               # cn 형식
            self.core.history.add_system_message(msg)        # 기록
            self.write(self.style.paint(msg, "red"))         # 표시

    async def _compact(self) -> None:                        # useChat.compaction.ts handleCompactCommand 대응
        """수동 압축(압축 훅 manual — 성공·실패·취소 안내는 system_message 이벤트로 온다)."""
        self._begin_turn()                                   # 준비(Esc 로 취소 가능)
        self.compacting = True                               # isCompacting 대응
        self._invalidate()                                   # 상태 표시
        core = self.core                                     # 부품
        await core.deps.compaction_hook("manual", core.history.get_history(),   # 수동 압축
                                        {"is_headless": False, "model_name": core.model_name, "deps": core.deps, "signal": self.signal})   # 대화형·취소 신호

    # ───────── 슬래시 명령 결과 ─────────
    async def _apply_slash_result(self, res: dict) -> Optional[str]:   # processSlashCommandResult 항목
        """exit → 종료, 고르기 화면 → 콘솔 목록, clear → 새 세션, output → 기록에 system 메시지 + 표시, newInput → 그 글을 보냄."""
        st, core = self.style, self.core                     # 줄임
        if res.get("notice"):                                # ncn 이 막은 명령 안내(기록 안 남김)
            self.write(st.paint(res["notice"], "yellow"))    # 표시
            return None                                      # 끝
        if res.get("exit"):                                  # /exit 항목
            if res.get("output"):                            # "Goodbye!" 대응
                self.write(res["output"])                    # 표시
            self.request_exit(0)                             # gracefulExit(0) 대응
            return None                                      # 끝
        if res.get("openSessionSelector"):                   # /resume·/sessions 항목
            await self._session_selector()                   # 세션 고르기
            return None                                      # 끝
        if res.get("openJobsSelector"):                      # /jobs 항목
            self._show_jobs()                                # 작업 목록
            return None                                      # 끝
        if res.get("openExportSelector"):                    # /export 항목
            await self._export_selector()                    # 세션 내보내기
            return None                                      # 끝
        if res.get("clear"):                                 # /clear 항목
            sysm = next((i for i in core.history.get_history() if i.get("message", {}).get("role") == "system"), None)   # 기존 system 항목
            new = core.sessions.start_new([sysm] if sysm else [])   # startNewSession(새 sessionId)
            core.id = new["sessionId"]                       # 새 id
            if hasattr(core.history, "session_id"):          # 기록의 세션 id 도
                core.history.session_id = new["sessionId"]   # 갱신
            core.history.set_history([sysm] if sysm else [])   # setChatHistory(newHistory) 대응
            if res.get("output"):                            # "Chat history cleared" 대응
                from ncn.loop.history import create_history_item   # 기록 항목
                core.history.set_history([create_history_item({"role": "system", "content": res["output"]})])   # 기록을 안내 하나로(cn 그대로)
                self.write(st.paint(res["output"], "dim", "italic"))   # 표시
            return None                                      # 끝
        if res.get("output"):                                # 글 결과
            core.history.add_system_message(res["output"])   # 기록에 system 메시지(cn 그대로 — 다음 요청에 들어감)
            self.write(res["output"])                        # 표시
            return None                                      # 끝
        return res.get("newInput") or None                   # 보낼 글

    async def _session_selector(self) -> None:               # SessionSelector + loadAndSetSession(TUIChat.tsx) 대응
        """최근 세션 목록에서 고르면 그 기록을 지금 대화로(세션 id 는 그대로 — cn loadAndSetSession 과 같음)."""
        from ncn.session import history_manager_load         # 세션 읽기
        metas = list_sessions(self.environ)                  # 목록
        if not metas:                                        # 없으면
            self.write(self.style.paint("No previous sessions found.", "yellow"))   # 안내
            return                                           # 끝
        labels = [f"{m.get('title') or ''} — {(m.get('firstUserMessage') or '').splitlines()[0][:60] if m.get('firstUserMessage') else ''}" for m in metas]   # 제목 — 첫 메시지
        idx = await self._pick(_Choice("select", labels, asyncio.get_running_loop().create_future(), title="Resume a previous chat session"))   # 고르기
        if idx is None:                                      # 닫음
            return                                           # 끝
        sid = metas[idx]["sessionId"]                        # 고른 id
        session = history_manager_load(sid, self.environ)    # loadSessionById 대응
        self.environ["CONTINUE_CLI_TEST_SESSION_ID"] = sid.replace("continue-cli-", "")   # cn 그대로(새 세션을 만들 때만 쓰임)
        self.core.history.set_history(session.get("history") or [])   # setChatHistory(session.history) 대응
        self._print_history()                                # 불러온 기록 보이기

    async def _export_selector(self) -> None:                # handleExportSession(TUIChat.tsx) 대응
        """세션을 골라 <작업 폴더>/continue-session-<id>.json 에 내보내고 기록에 system 메시지."""
        from ncn.session import history_manager_load         # 세션 읽기
        metas = list_sessions(self.environ)                  # 목록
        if not metas:                                        # 없으면
            self.write(self.style.paint("No previous sessions found.", "yellow"))   # 안내
            return                                           # 끝
        idx = await self._pick(_Choice("select", [m.get("title") or m["sessionId"] for m in metas],   # 제목 목록
                                       asyncio.get_running_loop().create_future(), title="Export a session to JSON file"))   # 고르기
        if idx is None:                                      # 닫음
            return                                           # 끝
        sid = metas[idx]["sessionId"]                        # 고른 id
        p = os.path.join(os.getcwd(), f"continue-session-{sid}.json")   # 원문 파일 이름
        session = history_manager_load(sid, self.environ)    # 세션
        from datetime import datetime, timezone              # 내보낸 시각
        payload = {"version": 1, "exportedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "session": session}   # 원문 모양
        with open(p, "w", encoding="utf-8") as fh:           # 쓰기
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))   # JSON.stringify(…, null, 2) 대응
        msg = f"Session exported to {p}"                     # 원문
        self.core.history.add_system_message(msg)            # 기록
        self.write(self.style.paint(msg, "dim", "italic"))   # 표시

    def _show_jobs(self) -> None:                            # JobsSelector 대신 목록 출력
        """백그라운드 작업 id·상태·명령."""
        from ncn.tools.background_jobs import background_job_service   # 작업 서비스
        jobs = background_job_service.get_all_jobs()         # 전체
        if not jobs:                                         # 없으면
            self.write(self.style.paint("No background jobs", "gray"))   # 안내
            return                                           # 끝
        for j in jobs:                                       # 작업마다
            self.write(f"  {self.style.paint(j.id, 'cyan')} {j.status} {j.command}")   # 한 줄

    # ───────── 셸 모드(! 명령) ─────────
    @staticmethod   # 정적 메서드
    def _shell_command(message: str) -> Optional[str]:       # handleBashModeProcessing 판정
        """'!' 로 시작하고 뒤에 명령이 있으면 그 명령, 아니면 None."""
        if not message.lstrip().startswith("!"):             # ! 아님
            return None                                      # 일반
        cmd = message.lstrip()[1:].strip()                   # 명령
        return cmd or None                                   # 비면 일반 메시지

    def _run_shell(self, command: str) -> None:              # 셸 모드 실행(기다리지 않음)
        """기록에 assistant(Bash 호출) 추가 → 상태 calling → 실행 → 결과(done/errored). cn 처럼 세션 저장은 안 한다."""
        from ncn.tools.bash import make_bash_tool            # Bash 도구
        from ncn.tools.base import ToolRunContext            # 실행 정보
        h = self.core.history                                # 기록
        tcid = f"shell-{int(time.time() * 1000)}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"   # 원문 id 모양
        h.add_assistant_message("", [{"id": tcid, "type": "function", "function": {"name": "Bash", "arguments": json.dumps({"command": command})}}])   # 호출 기록
        h.update_tool_status(tcid, "calling")                # 실행 중
        self.write("○ " + cr.tool_title("Bash", {"command": command}, self.style))   # 표시

        async def go():                                      # 실행
            try:                                             # 결과
                result = await make_bash_tool(self.core.cwd).run({"command": command}, ToolRunContext(tool_call_id=tcid))   # runTerminalCommandTool.run 대응
                h.add_tool_result(tcid, result, "done")      # 결과 기록
                for ln in cr.result_summary("Bash", result, self.style, self.core.cwd):   # 요약
                    self.write("  " + ln)                    # 표시
            except Exception as e:                           # 실패
                msg = f"Bash command failed: {e}"            # 원문
                h.add_tool_result(tcid, msg, "errored")      # 기록
                self.write("  " + self.style.paint(msg, "red"))   # 표시

        t = asyncio.ensure_future(go())                      # 기다리지 않음
        self._bg.add(t)                                      # 정리용 보관
        t.add_done_callback(self._bg.discard)                # 끝나면 뺌

    # ───────── 루프 이벤트 → 화면 ─────────
    def _on_event(self, e: dict) -> None:                    # 루프 이벤트(동기)
        """content(줄 단위 강조) · tool_start(○ 제목) · tool_result/error(⎿ 요약) · system_message(흐린 글) · retry · usage."""
        t = e.get("type")                                    # 종류
        st = self.style                                      # 색
        if t == "content":                                   # 글 조각
            for ln in self.md.feed(e.get("text") or ""):     # 끝난 줄
                self._write_content_line(ln)                 # 찍기
        elif t == "content_complete":                        # 응답 글 끝
            self._flush_md()                                 # 남은 줄
        elif t == "tool_start":                              # 도구 시작
            self._flush_md()                                 # 글 먼저
            tcid = e.get("tool_call_id") or ""               # 호출 id
            if tcid not in self.tool_names:                  # 처음이면(사전 처리 실패 때 한 번 더 올 수 있음)
                self.tool_names[tcid] = e.get("name")        # 이름 기억
                self.write("○ " + cr.tool_title(e.get("name"), e.get("arguments"), st))   # 도구 줄(cn: 실행 전 ○)
        elif t in ("tool_result", "tool_error"):             # 도구 결과·오류
            self._flush_md()                                 # 글 먼저
            name = e.get("name") or self.tool_names.get(e.get("tool_call_id") or "")   # 이름
            self.subagent_line = "" if name == "Subagent" else self.subagent_line   # Subagent 진행 표시 지움
            if t == "tool_error":                            # 오류
                self.write("  " + st.paint(str(e.get("error") or "Tool execution failed"), "red"))   # cn: 빨간 글
            else:                                            # 결과(done·canceled)
                for ln in cr.result_summary(name, str(e.get("result") or ""), st, self.core.cwd if self.core else None):   # 요약
                    self.write("  " + ln)                    # marginLeft 2 대응
            self._invalidate()                               # 상태 갱신
        elif t == "system_message":                          # 압축 안내 등
            self._flush_md()                                 # 글 먼저
            self.write(st.paint(e.get("message") or "", "dim", "italic"))   # 흐린 기울임(cn system 메시지)
        elif t == "retry":                                   # 재시도 — 이번 호출의 글 조각을 버림
            self.md.reset()                                  # 덜 찍힌 줄 버림
            self.write(st.paint(f"Retrying ({e.get('attempt')})… {e.get('error') or ''}", "dim"))   # 안내(ncn)
        elif t == "usage":                                   # 사용량
            u = e.get("usage")                               # 사용량 dict
            if isinstance(u, dict) and self.core is not None:   # 있으면
                self.core.sessions.track_usage(u)            # 누적·저장(trackSessionUsage)

    def _on_subagent_progress(self, tcid: str, name: str, output: str) -> None:   # Subagent 진행
        """맨 아래 줄에 하위 에이전트 이름과 마지막 출력 줄(간단히)."""
        last = next((ln for ln in reversed(output.splitlines()) if ln.strip()), "")   # 마지막 글 줄
        self.subagent_line = f"⎿ {name}: {last[:80]}" if last else f"⎿ {name}: Subagent executing..."   # 표시 글
        self._invalidate()                                   # 다시 그림

    # ───────── 승인(ask_user) ─────────
    async def _ask_user(self, name: str, args: Any, preview: Any, tool_call_id: str) -> str:   # 루프 승인 콜백(ToolPermissionSelector)
        """승인 3가지. 1 계속 → allow, 2 계속+다시 묻지 않기 → 규칙 추가 후 allow, 3 아니오 → 응답 중단 + 기록에 system 글 → reject."""
        async with self.ask_lock:                            # 하나씩
            if self.signal is not None and self.signal.aborted and not self.rejected:   # 이미 중단(Esc)된 턴
                return "reject"                              # 묻지 않고 거부
            self._flush_md()                                 # 글 먼저
            st = self.style                                  # 색
            self.write(st.paint("╭ ", "magenta") + st.paint(cr.strip_ansi(cr.tool_title(name, args, st)), "magenta", "bold"))   # 제목(자홍 굵게)
            for ln in cr.preview_lines(name, args, preview, st):   # 미리보기(diff 색 포함)
                self.write(st.paint("│ ", "magenta") + ln)   # 테두리 대신 왼쪽 선
            self.write(st.paint("│ ", "magenta") + st.paint("Would you like to continue?", "dim"))   # 원문
            idx = await self._pick(_Choice("perm", [o[0] for o in PERMISSION_OPTIONS], asyncio.get_running_loop().create_future()))   # 고르기
            chosen = PERMISSION_OPTIONS[idx][0] if idx is not None else "(interrupted)"   # 고른 글
            self.write(st.paint("╰ ", "magenta") + st.paint(chosen, "dim"))   # 결과 남김
            if idx in (0, 1):                                # 1·2 계속
                if idx == 1:                                 # 2: 다시 묻지 않기
                    try:                                     # 규칙 저장 실패는 무시(cn: 로그 후 승인 계속)
                        self.core.perms.add_always_allow(name, args)   # permissions.yaml allow 에 규칙 추가 후 다시 읽기
                    except Exception as e:                   # 실패
                        log.error("Failed to create policy or reload permissions: %s", e)   # 기록
                return "allow"                               # 승인
            if idx is None:                                  # 중단(Esc 로 턴을 멈춤)
                return "reject"                              # 거부(기록에 system 글 없음)
            if not self.rejected:                            # 이 턴의 첫 3번만(cn: 중단 뒤 isWaitingForResponse=false 라 두 번째부터는 중단·system 글 없음)
                self.signal.abort()                          # 응답 중단(stopStream)
                self.core.history.add_system_message(TOOL_CANCELED_SYSTEM_MESSAGE)   # 기록에 system 글(다음 요청에 들어감)
            self.rejected = True                             # 거부로 멈춤 표시
            return "reject"                                  # "Permission denied by user" 대응

    # ───────── AskQuestion(QuizService) ─────────
    def _on_question(self, pq: Any) -> None:                 # questionRequested 처리기(동기 — 도구 실행 중 불림)
        """비동기로 콘솔에서 묻고 answer_question 으로 답한다."""
        t = asyncio.ensure_future(self._answer_question(pq))   # 묻기 작업
        self._bg.add(t)                                      # 정리용 보관
        t.add_done_callback(self._bg.discard)                # 끝나면 뺌

    async def _answer_question(self, pq: Any) -> None:       # QuizPrompt 항목
        """선택지가 있으면 고르기(글자를 치면 직접 입력), 없으면 바로 직접 입력. 중단되면 기본 답(없으면 빈 글)."""
        from ncn.tools.ask_question import quiz_service      # 질문 서비스
        async with self.ask_lock:                            # 승인과 겹치지 않게
            self._flush_md()                                 # 글 먼저
            st = self.style                                  # 색
            self.write(st.paint("? ", "magenta", "bold") + st.paint(pq.question, "bold"))   # 질문(원문 모양)
            opts = [str(o) for o in pq.options] if isinstance(pq.options, list) else []   # 선택지
            res = await self._pick(_Choice("quiz", opts, asyncio.get_running_loop().create_future(),   # 고르기
                                           custom=not opts, default=pq.default_answer))   # 선택지 없으면 직접 입력
            if res is None:                                  # 중단
                answer, custom = pq.default_answer or "", False   # 기본 답
            elif isinstance(res, tuple):                     # 직접 입력
                answer, custom = res                         # 답·직접 입력
            else:                                            # 선택지 번호
                answer, custom = opts[res], False            # 그 선택지
            self.write(st.paint("  ❯ ", "cyan") + answer)    # 고른 답 남김
            quiz_service.answer_question(pq.request_id, answer, custom)   # 도구에 답 전달

    # ───────── 기록 보이기(--resume·--fork·/resume) ─────────
    def _print_history(self) -> None:                        # 불러온 기록 간단히 보이기
        """user(파랑 ●) · assistant 글(●) · 도구 호출(● 제목 + 요약) · system(흐린 기울임)."""
        st = self.style                                      # 색
        for it in self.core.history.get_history():           # 항목마다
            m = it.get("message", {})                        # 메시지
            role = m.get("role")                             # 역할
            text = _content_text(m.get("content"))           # 글
            if role == "user":                               # 사용자
                self._echo_user(text)                        # 파랑 ●
            elif role == "system":                           # system 대응
                self.write(st.paint(text, "dim", "italic"))  # 흐린 기울임
            elif role == "assistant":                        # 응답
                if text:                                     # 글이 있으면
                    md = cr.MarkdownStream(st)               # 새 강조기
                    lines = md.feed(text) + md.flush()       # 전부
                    for i, ln in enumerate(lines):           # 줄마다
                        self.write(("● " if i == 0 else "  ") + ln)   # 머리·들여쓰기
                for tcs in it.get("toolCallStates") or []:   # 도구 호출
                    fn = (tcs.get("toolCall") or {}).get("function") or {}   # 함수
                    bad = tcs.get("status") in ("errored", "canceled")   # 실패·취소
                    self.write(st.paint("●", "red" if bad else "green") + " " + cr.tool_title(fn.get("name"), tcs.get("parsedArgs"), st))   # 제목
                    out = "\n".join(o.get("content") or "" for o in tcs.get("output") or [])   # 결과
                    if out:                                  # 있으면
                        for ln in cr.result_summary(fn.get("name"), out, st, self.core.cwd):   # 요약
                            self.write("  " + ln)            # 표시

    # ───────── 실행 ─────────
    async def run(self, initial_prompt: Optional[str] = None) -> int:   # 대화형 전체
        """조립 → (기록 보이기) → 첫 메시지 → 입력 줄(종료까지) → 정리(세션 저장·MCP·백그라운드 작업). 돌려주는 값: 종료 코드."""
        if not await self._open():                           # 조립 실패
            return self.exit_code                            # 1 대응
        ctx = contextlib.nullcontext()                       # 시험: 그대로
        if self.use_patch:                                   # 진짜 단말
            from prompt_toolkit.patch_stdout import patch_stdout   # 입력 줄 위로 찍기
            ctx = patch_stdout(raw=True)                     # raw: ANSI 색을 그대로 통과
        try:                                                 # 끝나면 정리
            with ctx:                                        # 출력 대리자
                self.ps = self._build_prompt()               # 입력 줄
                self._intro()                                # 시작 안내
                if self.core.history.get_history():          # --resume·--fork 로 기록이 있으면
                    self._print_history()                    # 보이기
                from ncn.app import process_and_combine_prompts   # --prompt 합치기
                if initial_prompt or self.args.prompts:      # 첫 메시지(useChat.ts initialPrompt 효과)
                    final = process_and_combine_prompts(self.args.prompts, initial_prompt, self.core.cwd)   # processAndCombinePrompts 대응
                    if final:                                # 있으면
                        self.submit(final)                   # 보내기
                if not self.exiting:                         # 이미 끝나지 않았으면
                    await self.ps.prompt_async()             # 입력 줄(종료 요청까지 돈다)
        finally:                                             # 정리(gracefulExit)
            await self._shutdown()                           # 저장·정리
        return self.exit_code                                # 종료 코드

    def _intro(self) -> None:                                # 시작 안내(IntroMessage 대신 한 줄)
        """버전·모델·작업 폴더·도움말 안내."""
        from ncn import __version__                          # 버전
        st = self.style                                      # 색
        self.write(st.paint(f"ncn {__version__}", "bold") + st.paint(f" · {self.core.model.name or self.core.model.model} · {self.core.cwd}", "dim"))   # 버전·모델·폴더
        self.write(st.paint("/help 도움말 · Enter 보내기 · 줄 끝 \\ 또는 Alt+Enter 새 줄 · Shift+Tab 모드 · Esc 중단 · Ctrl+C 두 번 종료", "dim"))   # 키 안내

    async def _shutdown(self) -> None:                       # gracefulExit 항목
        """돌던 턴 취소 → 세션 저장(cn 형식) → 질문 처리기 해제 → 백그라운드 작업 종료 → LLM·MCP 정리."""
        self.exiting = True                                  # 종료 중
        for t in [self.turn_task, *list(self._bg)]:          # 돌던 작업
            if t is not None and not t.done():               # 아직 돌면
                t.cancel()                                   # 취소
                try:                                         # 끝날 때까지
                    await t                                  # 기다림
                except BaseException:                        # 취소·오류
                    pass                                     # 무시
        if self.core is None:                                # 조립 전 실패
            return                                           # 끝
        try:                                                 # 세션 저장
            self.core.sessions.update_history(self.core.history.get_history())   # updateSessionHistory(내용 없으면 저장 안 함)
        except Exception as e:                               # 실패
            log.error("Error saving session: %s", e)         # 기록
        from ncn.tools.ask_question import quiz_service      # 질문 서비스
        try:                                                 # 처리기 해제
            quiz_service.on_question_requested.remove(self._on_question)   # 해제
        except ValueError:                                   # 없음
            pass                                             # 무시
        try:                                                 # 백그라운드 작업 정리(gracefulExit: killAllJobs)
            from ncn.tools.background_jobs import background_job_service   # 작업 서비스
            if background_job_service.get_running_job_count() > 0:   # 돌고 있으면
                background_job_service.kill_all_jobs()       # 종료
        except Exception:                                    # 정리 실패
            pass                                             # 무시
        await self.core.aclose()                             # LLM·MCP 정리


# ─────────────────────────────────────────────────────────────────────────────
# 콘솔 준비(Windows UTF-8·ANSI)와 진입
# ─────────────────────────────────────────────────────────────────────────────


def prepare_windows_console() -> Optional[tuple]:            # Windows 콘솔 UTF-8·가상 터미널 켜기
    """Windows 10+: ① 콘솔 출력·입력 코드 페이지를 65001(UTF-8)로 — cp949 콘솔에서도 한글·이모지가 깨지지 않게.
    (파이썬 자체 출력은 PEP 528 로 WriteConsoleW 를 쓰지만, 자식 프로세스·콘솔 코드 페이지를 따르는 프로그램 출력과
     prompt_toolkit 의 VT100 출력이 같은 바이트 해석을 쓰게 맞춘다.)
    ② ENABLE_VIRTUAL_TERMINAL_PROCESSING(0x0004) — ANSI 색 제어열을 콘솔이 해석(계획 14-5: 모든 OS ANSI).
    돌려주는 값: 되돌릴 (출력 CP, 입력 CP, 원래 모드) — 끝날 때 restore_windows_console 로 원래대로(사용자 셸 보호)."""
    if sys.platform != "win32":                              # Windows 가 아니면
        return None                                          # 할 일 없음
    try:                                                     # ctypes 실패 무시(콘솔 없음 등)
        import ctypes                                        # Win32 API 대응
        k32 = ctypes.windll.kernel32                         # type: ignore[attr-defined] — kernel32 대응
        old_out, old_in = k32.GetConsoleOutputCP(), k32.GetConsoleCP()   # 원래 코드 페이지
        k32.SetConsoleOutputCP(65001)                        # 출력 UTF-8
        k32.SetConsoleCP(65001)                              # 입력 UTF-8
        h = k32.GetStdHandle(-11)                            # STD_OUTPUT_HANDLE 대응
        mode = ctypes.c_uint32()                             # 콘솔 모드
        old_mode = None                                      # 원래 모드
        if k32.GetConsoleMode(h, ctypes.byref(mode)):        # 콘솔이면
            old_mode = mode.value                            # 보관
            k32.SetConsoleMode(h, mode.value | 0x0004)       # ENABLE_VIRTUAL_TERMINAL_PROCESSING 켜기
        return old_out, old_in, old_mode                     # 되돌릴 값
    except Exception as e:                                   # 실패
        log.debug("Windows console setup failed: %s", e)     # 기록
        return None                                          # 없음


def restore_windows_console(saved: Optional[tuple]) -> None:   # 콘솔 원래대로
    """prepare_windows_console 이 바꾼 코드 페이지·모드를 되돌린다."""
    if not saved or sys.platform != "win32":                 # 바꾼 게 없으면
        return                                               # 끝
    try:                                                     # 실패 무시
        import ctypes                                        # Win32 API 대응
        k32 = ctypes.windll.kernel32                         # type: ignore[attr-defined] — kernel32 대응
        old_out, old_in, old_mode = saved                    # 값
        k32.SetConsoleOutputCP(old_out)                      # 출력 CP
        k32.SetConsoleCP(old_in)                             # 입력 CP
        if old_mode is not None:                             # 모드도 바꿨으면
            k32.SetConsoleMode(k32.GetStdHandle(-11), old_mode)   # 원래 모드
    except Exception:                                        # 실패
        pass                                                 # 무시


def run_interactive(a: argparse.Namespace, prompt: Optional[str]) -> int:   # app.py 가 부르는 진입(동기)
    """cn index.ts action(대화형) — 파이프 stdin 이면 첫 메시지에 합치고 입력은 단말(/dev/tty)에서. 단말이 전혀 없으면 cn 오류 글."""
    from ncn.app import read_stdin_sync                      # 파이프 stdin
    stdin_text = read_stdin_sync(sys.argv[1:])               # readStdinSync(대화형: -p 없음)
    if stdin_text:                                           # 파이프 입력이 있으면
        prompt = f"{stdin_text}\n\n{prompt}" if prompt else stdin_text   # 원문: stdin + 빈 줄 + prompt(<stdin> 감싸기 없음)
    tty_in = None                                            # 단말 입력(파이프일 때)
    ttyless = not any(getattr(s, "isatty", lambda: False)() for s in (sys.stdin, sys.stdout, sys.stderr))   # isTTYless 대응
    if stdin_text and sys.platform != "win32":               # 파이프 + POSIX: /dev/tty 를 입력으로(cn customStdin)
        try:                                                 # 단말 열기
            tty_in = open("/dev/tty", "r")                   # 단말
        except OSError:                                      # 단말 없음
            ttyless = True                                   # 단말 없음으로
    if ttyless or (stdin_text and tty_in is None):           # 단말이 없으면(Windows 파이프 포함)
        sys.stderr.write(f"Fatal error: {TTYLESS_ERROR}\n")  # cn 원문(Fatal error: …)
        return 1                                             # gracefulExit(1) 대응
    saved = prepare_windows_console()                        # Windows UTF-8·ANSI 대응
    try:                                                     # 실행
        pt_input = None                                      # 기본 입력
        if tty_in is not None:                               # 파이프였으면
            from prompt_toolkit.input import create_input    # 입력 만들기
            pt_input = create_input(stdin=tty_in)            # 단말 입력
        return asyncio.run(InteractiveApp(a, input=pt_input).run(prompt))   # 대화형
    except KeyboardInterrupt:                                # 입력 줄 밖에서 Ctrl+C
        return 130                                           # SIGINT 종료 코드
    finally:                                                 # 정리
        restore_windows_console(saved)                       # 콘솔 원래대로
        if tty_in is not None:                               # 단말 파일
            tty_in.close()                                   # 닫기
