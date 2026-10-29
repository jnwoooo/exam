# ncn/console_render.py — 콘솔 대화형 화면 글 만들기(ANSI). 계획 3장(화면 수준: 단순 대화형, Ink 모양은 맞추지 않음)·14-5(모든 OS ANSI)
# cn 원본: ui/MarkdownRenderer.tsx(최소 강조만) · tools/ToolCallTitle.tsx · ui/ToolResultSummary.tsx · ui/ColoredDiff.tsx
#   · ui/components/ToolPreview.tsx · ui/components/ChecklistDisplay.tsx · ui/components/ModeIndicator.tsx · slashCommands.ts(handleHelp 글)
# 글(안내 문구·요약 문구)은 cn 원문 그대로, 모양(테두리·정렬)은 단순하게. 색은 chalk/Ink 이름을 ANSI SGR 로 옮긴다.
from __future__ import annotations                           # 타입 주석 지연 평가

import difflib                                               # 단어 단위 diff(diffWordsWithSpace 대체)
import os                                                    # 경로(상대 경로 표시)
import re                                                    # 마크다운·diff 해석
from typing import Any, Optional                             # 타입

# ─────────────────────────────────────────────────────────────────────────────
# ANSI 색(chalk 이름 → SGR)
# ─────────────────────────────────────────────────────────────────────────────

_SGR = {"bold": "1", "dim": "2", "italic": "3", "strike": "9",   # 글꼴 효과
        "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",   # 기본 색
        "white": "37", "gray": "90"}                         # 흰색·회색(chalk.gray = 밝은 검정)


class Style:                                                # 색 켜고 끄기를 한 곳에서
    """paint(글, "bold", "cyan") → ANSI 로 감싼 글. color=False 면 글 그대로(파이프·NO_COLOR)."""

    def __init__(self, color: bool = True):                 # 색 사용 여부를 받는 생성자
        self.color = color                                   # 색을 쓸지

    def paint(self, text: str, *names: str) -> str:          # 글에 효과 입히기
        """효과 이름(_SGR 키) 또는 'bg:#rrggbb'(Ink backgroundColor 16진) 를 차례로 적용."""
        if not self.color or not names or text == "":        # 색 끄기·효과 없음·빈 글
            return text                                      # 그대로
        codes = []                                           # SGR 번호 모음
        for n in names:                                      # 효과마다
            if n.startswith("bg:#") and len(n) == 10:        # 배경 16진 색
                r, g, b = int(n[4:6], 16), int(n[6:8], 16), int(n[8:10], 16)   # RGB 값
                codes.append(f"48;2;{r};{g};{b}")            # 24비트 배경색
            elif n in _SGR:                                  # 이름 있는 효과
                codes.append(_SGR[n])                        # 번호 추가
        if not codes:                                        # 모르는 이름뿐이면
            return text                                      # 그대로
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"      # 켜고 글 쓰고 끄기


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")                      # SGR 제어열(글 길이·비교용으로 지울 때)


def strip_ansi(text: str) -> str:                            # ANSI 제어열 지우기
    """시험·로그용 — 색 없는 글."""
    return _ANSI_RE.sub("", text)                            # 제어열 제거


# ─────────────────────────────────────────────────────────────────────────────
# 마크다운 최소 강조(줄 단위 스트리밍) — cn MarkdownRenderer 의 일부만: 코드 블록·제목·굵게·인라인 코드
# ─────────────────────────────────────────────────────────────────────────────

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")             # **굵게** / __굵게__
_CODE_RE = re.compile(r"`([^`]+)`")                           # `인라인 코드`
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")                   # # 제목


class MarkdownStream:                                       # 조각을 받아 완성된 줄만 내보내는 마크다운 강조기
    """LLM 글 조각을 모았다가 줄이 끝나면 강조해서 돌려준다. 코드 블록(```) 안은 강조하지 않고 색만 바꾼다.
    줄 단위인 이유: 입력 줄(prompt_toolkit) 위에 찍는 글은 줄이 끝나야 깔끔하게 올라간다(patch_stdout)."""

    def __init__(self, style: Style):                       # 색 설정을 받는 생성자
        self.style = style                                   # 색
        self.buf = ""                                        # 아직 줄이 안 끝난 글
        self.in_code = False                                 # 코드 블록 안인가

    def feed(self, text: str) -> list[str]:                  # 조각 넣기 → 완성된 줄들
        """줄바꿈까지 온 줄을 강조해 돌려준다(줄바꿈 글자는 빼고)."""
        self.buf += text                                     # 모음
        out = []                                             # 완성 줄
        while "\n" in self.buf:                              # 줄이 끝났으면
            line, self.buf = self.buf.split("\n", 1)         # 한 줄 떼기
            out.append(self.render_line(line))               # 강조해서 추가
        return out                                           # 완성 줄들

    def flush(self) -> list[str]:                            # 남은 조각 내보내기(응답 끝)
        """줄바꿈 없이 끝난 마지막 줄. 코드 블록 상태도 초기화."""
        out = [self.render_line(self.buf)] if self.buf else []   # 남은 줄
        self.buf = ""                                        # 비움
        self.in_code = False                                 # 다음 응답은 코드 밖에서 시작
        return out                                           # 남은 줄(없으면 빈 목록)

    def reset(self) -> None:                                 # 버리기(재시도·중단)
        """모은 조각을 버린다."""
        self.buf = ""                                        # 비움
        self.in_code = False                                 # 코드 블록 밖으로

    def render_line(self, line: str) -> str:                 # 한 줄 강조
        """``` 줄은 흐리게(코드 블록 경계), 코드 안은 청록, 제목은 굵게, 그 밖은 **굵게**·`코드`만."""
        st = self.style                                      # 줄임
        if line.lstrip().startswith("```"):                  # 코드 블록 경계
            self.in_code = not self.in_code                  # 안/밖 바꿈
            return st.paint(line, "dim")                     # 경계 줄은 흐리게
        if self.in_code:                                     # 코드 블록 안
            return st.paint(line, "cyan")                    # 코드 색
        m = _HEAD_RE.match(line)                             # 제목인가
        if m:                                                # 제목이면
            return st.paint(m.group(2), "bold")              # 제목 글만 굵게(# 표시는 뺌)
        line = _CODE_RE.sub(lambda mm: st.paint(mm.group(1), "cyan"), line)   # 인라인 코드
        return _BOLD_RE.sub(lambda mm: st.paint(mm.group(1) or mm.group(2), "bold"), line)   # 굵게


# ─────────────────────────────────────────────────────────────────────────────
# 도구 제목·미리보기·결과 요약
# ─────────────────────────────────────────────────────────────────────────────


def tool_title(name: Optional[str], args: Any, style: Style) -> str:   # ToolCallTitle.tsx — "<b>표시이름</b>(첫 인자)"
    """표시 이름은 굵게, 괄호 안은 보통 글(ncn/acp/convert.tool_title 과 같은 규칙)."""
    from ncn.acp.convert import tool_title as plain_title     # 같은 규칙의 글(ACP 카드 제목)
    plain = plain_title(name, args)                          # "Read(a.py)" 또는 "Read"
    i = plain.find("(")                                      # 이름과 인자 경계
    if i < 0:                                                # 인자 없음
        return style.paint(plain, "bold")                    # 이름만 굵게
    return style.paint(plain[:i], "bold") + plain[i:]        # 이름 굵게 + (인자)


def checklist_lines(content: str, style: Style) -> list[str]:   # ChecklistDisplay.tsx 항목
    """- [x] 완료는 ✓(초록)·회색 취소선, - [ ] 는 ○(노랑), 첫 미완료는 굵은 청록. 'Task list' 로 시작하는 줄은 숨김."""
    lines = content.split("\n")                              # 줄로
    rx = re.compile(r"^(\s*)-\s*\[([ x])\]\s*(.*)$")          # 원문 정규식
    first = next((i for i, ln in enumerate(lines) if (m := rx.match(ln)) and m.group(2) == " "), -1)   # 첫 미완료 줄
    out = []                                                 # 결과 줄
    for i, ln in enumerate(lines):                           # 줄마다
        if ln.startswith("Task list"):                       # 머리 줄
            continue                                         # 숨김
        m = rx.match(ln)                                     # 체크 항목인가
        if m:                                                # 체크 항목
            indent, status, text = m.groups()                # 들여쓰기·상태·글
            done = status == "x"                             # 완료?
            mark = style.paint("✓" if done else "○", "green" if done else "yellow")   # 표시
            body = style.paint(text, "gray", "strike") if done else style.paint(text, "bold", "cyan") if i == first else text   # 글
            out.append(f"  {indent}{mark} {body}")           # paddingLeft 2 대응
        elif ln.strip():                                     # 그 밖의 글
            out.append("  " + ln)                            # 그대로
        else:                                                # 빈 줄
            out.append("")                                   # 빈 줄
    return out                                               # 결과


def preview_lines(name: str, args: Any, preview: Any, style: Style) -> list[str]:   # ToolPreview.tsx 항목
    """미리보기 조각: text(첫 줄 앞 '⎿ '), checklist, diff(색 diff). 없으면 'Will call X with N arguments'."""
    from ncn.acp.convert import _display_name                 # 표시 이름(getToolDisplayName)
    if preview:                                              # 미리보기가 있으면
        out = []                                             # 결과 줄
        for idx, p in enumerate(preview):                    # 조각마다
            ptype = p.get("type") if isinstance(p, dict) else getattr(p, "type", "text")   # 종류
            content = (p.get("content") if isinstance(p, dict) else getattr(p, "content", "")) or ""   # 내용
            pad = (p.get("padding_left") if isinstance(p, dict) else getattr(p, "padding_left", None)) or 0   # 들여쓰기
            color = (p.get("color") if isinstance(p, dict) else getattr(p, "color", None)) or "dim"   # 색(없으면 dim)
            if ptype == "text":                              # 글 조각
                lead = style.paint("⎿ ", "dim") if idx == 0 else ""   # 첫 조각만 ⎿
                out.append(" " * pad + lead + style.paint(str(content), color if color in _SGR else "dim"))   # 줄 추가
            elif ptype == "checklist":                       # 체크리스트
                out.extend(checklist_lines(f"Task list status:\n{content}", style))   # 체크리스트 줄
            else:                                            # diff 대응
                out.extend(colored_diff(str(content), style))   # 색 diff
        return out                                           # 결과
    message = f"Will call {_display_name(name)}"              # 기본 안내(원문)
    n = len(args) if isinstance(args, dict) else 0           # 인자 수
    if n > 0:                                                # 인자가 있으면
        message += f" with {n} argument{'' if n == 1 else 's'}"   # 원문 복수형 규칙
    return [style.paint("⎿ ", "dim") + style.paint(message, "dim")]   # 한 줄


MAX_BASH_OUTPUT_LINES = 4                                    # ToolResultSummary.tsx 원문 값
MAX_TASK_OUTPUT_LINES = 20                                   # Subagent 출력 줄 수(원문 값)


def result_summary(name: Optional[str], content: str, style: Style, cwd: Optional[str] = None) -> list[str]:   # ToolResultSummary.tsx 항목
    """도구 결과 요약 줄들(앞에 '⎿ '). 글은 cn 원문 그대로."""
    from ncn.acp.convert import _display_name                 # 표시 이름
    lead = style.paint("⎿ ", "dim")                          # 앞 표시
    if not content:                                          # 결과 없음
        return [lead + style.paint(" No output", "dim")]     # 원문
    lines = len(content.split("\n"))                         # 줄 수
    chars = len(content)                                     # 글자 수
    display = _display_name(name) if name else "Tool"        # 표시 이름
    if name == "Checklist":                                  # 체크리스트
        return [lead + style.paint("Task List Updated", "blue"), ""] + checklist_lines(f"Task list status:\n{content}", style)   # 원문 + 목록
    if name in ("Write", "Edit", "MultiEdit") and "Diff:\n" in content:   # 편집 도구 + diff
        section = content.split("Diff:\n")[1]                # diff 부분
        if section:                                          # 있으면
            msg = " File written successfully" if name == "Write" else " File edited successfully"   # 원문
            return [lead + style.paint(msg, "green")] + colored_diff(section, style)   # 안내 + 색 diff
    if name == "Bash":                                       # 터미널 출력
        is_err = content.startswith("Stderr:")               # stderr 인가
        actual = content[7:].strip() if is_err else content  # 실제 출력
        out_lines = actual.split("\n")                       # 줄로
        head = [lead + style.paint(" Terminal output:", "dim")]   # 머리(원문)
        color = "red" if is_err else "white"                 # 글 색
        if len(out_lines) <= MAX_BASH_OUTPUT_LINES:          # 짧으면 전부
            return head + ["  " + style.paint(x, color) for x in actual.rstrip().split("\n")]   # paddingLeft 2 대응
        first = "\n".join(out_lines[:MAX_BASH_OUTPUT_LINES]).rstrip()   # 앞 4줄
        more = ["  " + style.paint(f"... +{len(out_lines) - MAX_BASH_OUTPUT_LINES} lines", "dim")]   # 남은 줄 수(원문)
        return head + ["  " + style.paint(x, color) for x in first.split("\n")] + more   # 앞 4줄 + 생략 표시
    if name == "Subagent":                                   # 하위 에이전트 출력
        mi = content.find("<task_metadata>")                 # 메타 위치
        actual = content[:mi].strip() if mi >= 0 else content   # 메타 앞까지
        if not actual:                                       # 아직 출력 없음
            return [lead + style.paint(" Subagent executing...", "dim")]   # 원문
        out_lines = actual.split("\n")                       # 줄로
        head = [lead + style.paint(" Subagent output:", "dim")]   # 머리(원문)
        if len(out_lines) <= MAX_TASK_OUTPUT_LINES:          # 짧으면 전부
            return head + ["  " + x for x in actual.rstrip().split("\n")]   # 전부
        last = "\n".join(out_lines[-MAX_TASK_OUTPUT_LINES:]).rstrip()   # 마지막 20줄
        return head + ["  " + style.paint(f"... +{len(out_lines) - MAX_TASK_OUTPUT_LINES} lines", "dim")] + ["  " + x for x in last.split("\n")]   # 앞 생략 표시 + 끝 줄
    return [lead + style.paint(" " + _summary(name, content, display, lines, chars, cwd), "dim")]   # 그 밖은 한 줄 요약


def _summary(name: Optional[str], content: str, display: str, lines: int, chars: int, cwd: Optional[str]) -> str:   # getSummary 항목
    """ToolResultSummary.tsx getSummary — 원문 문구 그대로."""
    if content == "Permission denied by user":               # 사용자 거부
        return "Cancelled by user"                           # 원문
    if content.startswith("Error"):                          # 오류
        ls = content.split("\n")                             # 줄로
        return f"Error: {ls[0]}{'...' if len(ls) > 1 else ''}"   # 첫 줄 + 줄임
    root = cwd or os.getcwd()                                # process.cwd() 대응

    def fmt_path(p: str) -> str:                             # 절대 경로 → 작업 폴더 기준 상대 경로
        if os.path.isabs(p):                                 # 절대 경로면
            rel = os.path.relpath(p, root) if p else ""      # 상대 경로
            return rel or p                                  # 비면 원래 것
        return p                                             # 그대로

    if name == "Read":                                       # 파일 읽기
        if "→" in content:                                   # 줄 번호 형식이면
            m = re.match(r"^(.+?):", content)                 # 앞의 경로
            return f"{display} {fmt_path(m.group(1) if m else 'file')} ({lines} lines)"   # 원문
        return f"{display} tool output ({lines} lines)"      # 원문
    if name == "Write":                                      # 쓰기
        return "File created successfully" if "Successfully created file" in content else "File updated successfully"   # 원문
    if name in ("Edit", "MultiEdit"):                        # 편집
        return "File edited successfully"                    # 원문
    if name == "List":                                       # 목록
        return f"Listed {lines} {'item' if lines == 1 else 'items'}"   # 원문
    if name == "Search":                                     # 검색
        return f"Found {lines} {'match' if lines == 1 else 'matches'}"   # 원문
    if name == "Diff":                                       # diff 대응
        return f"Diff output ({lines} lines)"                # 원문
    if chars > 1000:                                         # 긴 결과
        return f"{display} output: {lines} lines, {chars} characters"   # 원문
    if lines > 10:                                           # 줄 많음
        return f"{display} output: {lines} lines"            # 원문
    return content[:100] + ("..." if len(content) > 100 else "")   # 앞 100자


# ─────────────────────────────────────────────────────────────────────────────
# 색 diff(ColoredDiff.tsx)
# ─────────────────────────────────────────────────────────────────────────────

ADDITION_BG = "bg:#325b30"                                   # 원문 색: 추가 줄 배경
DELETION_BG = "bg:#712f37"                                   # 원문 색: 삭제 줄 배경
ADDITION_HIGHLIGHT = "bg:#59a467"                            # 원문 색: 추가 단어 강조
DELETION_HIGHLIGHT = "bg:#a75e6d"                            # 원문 색: 삭제 단어 강조
_HUNK_RE = re.compile(r"^@@ -(\d+),?\d* \+(\d+),?\d* @@")     # 원문 정규식
MAX_DIFF_LINES = 16                                          # 원문: 앞 16줄만


def parse_diff(diff: str) -> list[dict]:                     # parseDiffWithLineNumbers 항목
    """통합 diff → [{type: add|del|context|hunk|other, old, new, content}]. 첫 hunk 전 줄은 버린다."""
    out: list[dict] = []                                     # 결과
    old = new = 0                                            # 줄 번호
    in_hunk = False                                          # hunk 안인가
    for line in diff.split("\n"):                            # 줄마다
        m = _HUNK_RE.match(line)                             # hunk 머리
        if m:                                                # 머리면
            old, new = int(m.group(1)) - 1, int(m.group(2)) - 1   # 시작 번호 - 1
            in_hunk = True                                   # hunk 시작
            out.append({"type": "hunk", "content": line})    # 머리 기록
            continue                                         # 다음 줄
        if not in_hunk:                                      # 머리 전 줄(---, +++ 등)
            continue                                         # 버림
        if line.startswith("+"):                             # 추가 줄
            new += 1                                         # 새 번호
            out.append({"type": "add", "new": new, "content": line[1:]})   # 기록
        elif line.startswith("-"):                           # 삭제 줄
            old += 1                                         # 옛 번호
            out.append({"type": "del", "old": old, "content": line[1:]})   # 기록
        elif line.startswith(" "):                           # 문맥 줄
            old += 1                                         # 옛 번호
            new += 1                                         # 새 번호
            out.append({"type": "context", "old": old, "new": new, "content": line[1:]})   # 기록
        elif line.startswith("\\"):                          # "\ No newline at end of file" 대응
            out.append({"type": "other", "content": line})   # 기록
    return out                                               # 결과


def _words(s: str) -> list[str]:                             # diffWordsWithSpace 토큰(단어·공백·기호)
    """단어·공백 덩어리·기호 하나씩으로 나눈다."""
    return re.findall(r"\s+|\w+|[^\w\s]", s)                  # 토큰 목록


def _word_diff(old: str, new: str, style: Style, which: str) -> str:   # renderWordLevelContent 항목
    """which=removed 면 옛 글에서 빠진 단어 강조, added 면 새 글에서 들어온 단어 강조."""
    a, b = _words(old), _words(new)                          # 토큰
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)   # 차이 계산
    out = []                                                 # 조각
    for tag, i1, i2, j1, j2 in sm.get_opcodes():             # 구간마다
        if tag == "equal":                                   # 같은 부분
            out.append("".join(a[i1:i2] if which == "removed" else b[j1:j2]))   # 그대로
        elif which == "removed" and i2 > i1:                 # 빠진 부분
            out.append(style.paint("".join(a[i1:i2]), DELETION_HIGHLIGHT))   # 진한 빨강
        elif which == "added" and j2 > j1:                   # 들어온 부분
            out.append(style.paint("".join(b[j1:j2]), ADDITION_HIGHLIGHT))   # 진한 초록
    return "".join(out)                                      # 이어 붙임


def colored_diff(diff: str, style: Style) -> list[str]:      # ColoredDiff.tsx 항목
    """줄 번호(4칸) + '+ '/'- '/'  ' + 내용. 붙어 있는 삭제·추가 묶음은 단어 단위 강조(원문처럼 묶음 첫 줄 번호 하나)."""
    parsed = parse_diff(diff)                                # 해석
    if not parsed:                                           # 바뀐 것 없음
        return [style.paint("No changes detected.", "dim")]  # 원문
    shown = [x for x in parsed if x["type"] not in ("hunk", "other")]   # 보일 줄
    trunc = shown[:MAX_DIFF_LINES]                           # 앞 16줄
    out: list[str] = []                                      # 결과
    i = 0                                                    # 위치
    while i < len(trunc):                                    # 줄마다
        ln = trunc[i]                                        # 지금 줄
        if ln["type"] in ("add", "del"):                     # 바뀐 줄이면 묶음 모으기
            j = i                                            # 묶음 끝
            while j < len(trunc) and trunc[j]["type"] in ("add", "del"):   # 바뀐 줄이 이어지는 동안
                j += 1                                       # 늘림
            group = trunc[i:j]                               # 묶음
            dels = [x for x in group if x["type"] == "del"]  # 삭제 줄
            adds = [x for x in group if x["type"] == "add"]  # 추가 줄
            if len(group) > 1 and dels and adds:             # 둘 다 있으면 단어 단위
                old_c = "\n".join(x["content"] for x in dels)   # 옛 글
                new_c = "\n".join(x["content"] for x in adds)   # 새 글
                out.append(style.paint(str(dels[0].get("old", "")).ljust(4) + " ", "dim") + style.paint("- ", DELETION_BG)   # 삭제 머리
                           + style.paint(_word_diff(old_c, new_c, style, "removed"), DELETION_BG))   # 삭제 글
                out.append(style.paint(str(adds[0].get("new", "")).ljust(4) + " ", "dim") + style.paint("+ ", ADDITION_BG)   # 추가 머리
                           + style.paint(_word_diff(old_c, new_c, style, "added"), ADDITION_BG))   # 추가 글
            else:                                            # 한 종류면 한 줄씩
                for x in group:                              # 줄마다
                    out.append(_single_diff_line(x, style))   # 한 줄
            i = j                                            # 묶음 다음으로
            continue                                         # 계속
        out.append(_single_diff_line(ln, style))             # 문맥 줄
        i += 1                                               # 다음
    if len(shown) > MAX_DIFF_LINES:                          # 잘렸으면
        out.append(style.paint(f"... ({len(shown) - MAX_DIFF_LINES} more lines)", "dim"))   # 원문
    return out                                               # 결과


def _single_diff_line(ln: dict, style: Style) -> str:        # 한 줄 표시
    """add: 초록 배경 '+', del: 빨강 배경 '-', context: 흐리게."""
    if ln["type"] == "add":                                  # 추가
        num, sym, eff = ln.get("new", ""), "+", (ADDITION_BG,)   # 새 번호·기호·배경
    elif ln["type"] == "del":                                # 삭제
        num, sym, eff = ln.get("old", ""), "-", (DELETION_BG,)   # 옛 번호·기호·배경
    else:                                                    # 문맥
        num, sym, eff = ln.get("new", ""), " ", ("dim",)     # 새 번호·공백·흐리게
    return style.paint(str(num).ljust(4) + " ", "dim") + style.paint(sym + " ", *eff) + style.paint(ln["content"], *eff)   # 번호 + 기호 + 내용


# ─────────────────────────────────────────────────────────────────────────────
# 모드 표시·도움말
# ─────────────────────────────────────────────────────────────────────────────


def mode_indicator(mode: str, style: Style) -> str:          # ModeIndicator.tsx 항목
    """plan → [⏸ plan](파랑), auto → [⏵⏵ auto](초록). normal 은 cn 은 숨기지만 ncn 은 [normal](흐리게)로 보인다(계획 3장: 현재 모드 표시)."""
    if mode == "plan":                                       # 계획 모드
        return style.paint("[⏸ plan]", "blue")               # 원문 표시
    if mode == "auto":                                       # 자동 모드
        return style.paint("[⏵⏵ auto]", "green")             # 원문 표시
    return style.paint(f"[{mode}]", "dim")                   # normal(ncn 추가 표시)


def help_message(style: Style) -> str:                       # slashCommands.ts handleHelp — 원문 글
    """/help 출력(chalk 색 그대로). Shift+Enter 줄은 cn 원문 그대로 둔다(콘솔에서는 Alt+Enter·Ctrl+J 도 새 줄 — ncn)."""
    c = lambda s: style.paint(s, "cyan")                     # chalk.cyan 줄임
    return "\n".join([                                       # 줄 모음(원문 순서)
        style.paint("Keyboard Shortcuts:", "bold"),          # 제목
        "",                                                  # 빈 줄
        style.paint("Navigation:", "white"),                 # 이동
        f"  {c('↑/↓')}        Navigate command/file suggestions or history",   # ↑↓ — 도움말 줄
        f"  {c('Tab')}        Complete command or file selection",   # Tab — 도움말 줄
        f"  {c('Enter')}      Submit message",               # Enter — 도움말 줄
        f"  {c('Shift+Enter')} New line",                    # Shift+Enter — 도움말 줄
        f"  {c(chr(92))}          Line continuation (at end of line)",   # 줄 끝 역슬래시
        f"  {c('!')}          Shell mode - run shell commands",   # 셸 모드
        "",                                                  # 빈 줄
        style.paint("Controls:", "white"),                   # 조작
        f"  {c('Ctrl+C')}     Clear input",                  # Ctrl+C — 도움말 줄
        f"  {c('Ctrl+D')}     Exit application",             # Ctrl+D — 도움말 줄
        f"  {c('Ctrl+L')}     Clear screen",                 # Ctrl+L — 도움말 줄
        f"  {c('Shift+Tab')}  Cycle permission modes (normal/plan/auto)",   # Shift+Tab — 도움말 줄
        f"  {c('Esc')}        Cancel streaming or close suggestions",   # Esc — 도움말 줄
        "",                                                  # 빈 줄
        style.paint("Special Characters:", "white"),         # 특수 글자
        f"  {c('@')}          Search and attach files for context",   # @ — 도움말 줄
        f"  {c('/')}          Access slash commands",        # / — 도움말 줄
        f"  {c('!')}          Execute bash commands directly",   # ! — 도움말 줄
        "",                                                  # 빈 줄
        style.paint("Available Commands:", "white"),         # 명령
        f"  Type {c('/')} to see available slash commands",  # / 안내
        f"  Type {c('!')} followed by a command to execute bash directly",   # ! 안내
    ])                                                       # 줄바꿈으로 이음
