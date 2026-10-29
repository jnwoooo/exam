# ncn/acp/convert.py — ncn(cn) 자료 ↔ ACP 형식 변환(입출력 없는 순수 함수 모음). 계획 14-3 대응표
# cn 기준: tools/ToolCallTitle.tsx(도구 카드 제목) · ui/components/ToolPermissionSelector.tsx(승인 선택지 이름)
#   · ui/hooks/useChat.helpers.ts formatMessageWithFiles(@파일 첨부 → contextItems) · ui/components/ChecklistDisplay.tsx(체크 줄 해석)
#   · shared-options.ts(--readonly/--auto 설명 → 모드 설명)
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 경로
import re                                                    # 체크 줄·URI 조각 해석
import urllib.parse                                          # file:// URI 해석
import uuid                                                  # contextItems id(uuidv4) 항목
from typing import Any, Callable, Optional                   # 타입

from acp.schema import (EnumOption, FileEditToolCallContent, ContentToolCallContent, PermissionOption,  # ACP 자료형
                        PlanEntry, SessionConfigOptionSelect, SessionConfigSelectOption, SessionMode,  # 계획·설정 선택지·모드
                        SessionModeState, TextContentBlock, ToolCallLocation, Usage)   # 모드 상태·글·위치·사용량

from ncn.tools.formatters import node_platform                # 실행 OS(Node process.platform 값 — 시험에서 바꿀 수 있음)

# ─────────────────────────────────────────────────────────────────────────────
# 도구 카드(tool_call): kind·title·locations
# ─────────────────────────────────────────────────────────────────────────────

def public_args(args: Any) -> Any:                          # 화면에 보일 인자(내부 키 제거)
    """'_ncn' 으로 시작하는 내부용 키(Edit·MultiEdit 의 _ncnRedo 등)를 뺀 사본. 객체가 아니면 그대로."""
    if not isinstance(args, dict):                           # 객체가 아니면
        return args                                         # 그대로
    return {k: v for k, v in args.items() if not str(k).startswith("_ncn")}   # 내부 키 제외


TOOL_KINDS = {"Read": "read", "Edit": "edit", "MultiEdit": "edit", "Write": "edit",   # 도구 이름 → ACP ToolKind
              "Bash": "execute", "Search": "search", "Fetch": "fetch"}   # 그 밖은 other
EDIT_TOOLS = ("Edit", "MultiEdit", "Write")                  # diff 내용을 싣는 도구


def tool_kind(name: Optional[str]) -> str:                  # 도구 이름 → ACP kind
    """Read→read, Edit/MultiEdit/Write→edit, Bash→execute, Search→search, Fetch→fetch, 그 밖→other."""
    return TOOL_KINDS.get(name or "", "other")               # 표에 없으면 other


def _display_name(name: str) -> str:                        # 도구 표시 이름(getToolDisplayName)
    """내장 도구면 표시 이름, 아니면 이름 그대로."""
    try:                                                    # 도구 표가 없을 일은 없지만 안전하게
        from ncn.tools.registry import get_tool_display_name   # 지연 import
        return get_tool_display_name(name)                   # 표시 이름
    except Exception:                                        # 실패하면
        return name                                         # 이름 그대로


def tool_title(name: Optional[str], args: Any) -> str:      # 도구 카드 제목(ToolCallTitle.tsx)
    """ToolCallTitle — "표시이름(첫 인자)". 첫 인자가 경로·숫자·불리언이면 formatToolArgument, 여러 줄 글이면 첫 줄+"...".
    인자가 없으면 표시 이름만. (cn 화면 글과 같은 규칙 — 굵게 표시는 화면 몫)"""
    from ncn.tools.formatters import format_tool_argument    # cn formatToolArgument 항목
    display = _display_name(name or "")                      # 표시 이름
    if not isinstance(args, dict) or not args:               # !args || 키 0개
        return display                                       # 이름만
    key, value = next(iter(args.items()))                    # Object.entries(args)[0] — 첫 인자
    formatted = ""                                           # 보일 값
    if "path" in key.lower() or (isinstance(value, (int, float)) and not isinstance(value, bool)) or isinstance(value, bool):   # 경로 이름·숫자·불리언
        formatted = format_tool_argument(value)              # 그대로 변환
    elif isinstance(value, str):                             # 글이면
        lines = value.split("\n")                            # 줄로 나눔
        if len(lines) == 1:                                  # 한 줄이면
            formatted = format_tool_argument(value)          # 그대로
        else:                                                # 여러 줄이면
            first = lines[0].strip()                         # 첫 줄(앞뒤 공백 제거)
            formatted = f"{format_tool_argument(first)}..." if first else "..."   # 첫 줄 + 줄임표
    return f"{display}({formatted})"                         # 이름(값)


_PATH_KEYS = ("filepath", "file_path", "dirPath", "dirpath", "path")   # 위치로 보일 인자 이름(cn 도구들의 경로 인자)


def tool_locations(args: Any, cwd: str) -> Optional[list[ToolCallLocation]]:   # 도구 카드의 파일 위치
    """경로 인자(filepath·file_path·dirPath·path)가 있으면 절대 경로 위치 하나. 없으면 None."""
    if not isinstance(args, dict):                           # 인자가 객체가 아니면
        return None                                         # 위치 없음
    for k in _PATH_KEYS:                                     # 경로 인자 이름 차례로
        v = args.get(k)                                     # 값
        if isinstance(v, str) and v:                         # 글이면
            p = v if os.path.isabs(v) else os.path.normpath(os.path.join(cwd, v))   # 작업 폴더 기준 절대 경로
            return [ToolCallLocation(path=p)]               # 위치 하나
    return None                                             # 경로 인자 없음


# ─────────────────────────────────────────────────────────────────────────────
# 도구 카드 내용: diff(Write/Edit/MultiEdit) · 글
# ─────────────────────────────────────────────────────────────────────────────


def text_content(text: str) -> ContentToolCallContent:      # 글 내용 블록
    """도구 카드에 넣을 글 한 덩어리."""
    return ContentToolCallContent(type="content", content=TextContentBlock(type="text", text=text))   # content{text} 반환


def edit_diff(name: Optional[str], args: Any, old_text_for_write: Optional[str], cwd: str) -> Optional[FileEditToolCallContent]:  # diff 내용 만들기
    """Write/Edit/MultiEdit 의 diff 내용 {path, oldText, newText}. 만들 수 없으면 None.
    Edit 는 사전 처리 인자(resolvedPath·oldContent·newContent), MultiEdit 는 (file_path·originalContent·newContent),
    Write 는 (filepath·content) + 쓰기 전 디스크 내용(old_text_for_write, 새 파일이면 None)."""
    if not isinstance(args, dict):                           # 인자가 객체가 아니면
        return None                                         # 없음
    if name == "Edit" and "newContent" in args:              # Edit 사전 처리 결과
        path, old, new = args.get("resolvedPath"), args.get("oldContent"), args.get("newContent")   # 경로·이전·새 내용
    elif name == "MultiEdit" and "newContent" in args:       # MultiEdit 사전 처리 결과
        path, old, new = args.get("file_path"), args.get("originalContent"), args.get("newContent")   # 경로·이전·새 내용
    elif name == "Write" and isinstance(args.get("content"), str):   # Write 항목
        path, old, new = args.get("filepath"), old_text_for_write, args.get("content")   # 경로·디스크 내용·새 내용
    else:                                                    # 그 밖(사전 처리 전 인자 등)
        return None                                         # 없음
    if not isinstance(path, str) or not isinstance(new, str):   # 경로·새 내용이 글이 아니면
        return None                                         # 없음
    abs_path = path if os.path.isabs(path) else os.path.normpath(os.path.join(cwd, path))   # 절대 경로
    return FileEditToolCallContent(type="diff", path=abs_path, old_text=old if isinstance(old, str) else None, new_text=new)   # diff 블록


def preview_text(preview: Any) -> Optional[str]:            # 승인 미리보기 → 글
    """ToolCallPreview 목록을 줄로 이은 글(diff 종류도 글 그대로). 비면 None."""
    if not preview:                                          # 미리보기가 없으면
        return None                                         # 없음
    lines = []                                              # 줄 모음
    for p in preview:                                       # 조각마다
        content = getattr(p, "content", None) if not isinstance(p, dict) else p.get("content")   # 내용(자료형·dict 둘 다)
        pad = (getattr(p, "padding_left", None) if not isinstance(p, dict) else p.get("padding_left")) or 0   # 들여쓰기
        if content is not None:                              # 내용이 있으면
            lines.append(" " * pad + str(content))          # 들여쓰기 반영해 추가
    return "\n".join(lines) if lines else None               # 이어 붙임


# ─────────────────────────────────────────────────────────────────────────────
# 승인(session/request_permission) — cn 승인 3가지(ToolPermissionSelector.tsx 원문 이름)
# ─────────────────────────────────────────────────────────────────────────────

OPT_ALLOW_ONCE = "allow_once"                                # 1 계속
OPT_ALLOW_ALWAYS = "allow_always"                            # 2 계속 + 다시 묻지 않기
OPT_REJECT_ONCE = "reject_once"                              # 3 아니오(멈추고 입력 대기)
TOOL_CANCELED_SYSTEM_MESSAGE = "[Tool canceled - please tell Continue what to do differently]"   # useChat.ts:842 원문(3번 선택 시 기록에 넣는 system 글)


def permission_options() -> list[PermissionOption]:          # 승인 선택지 3개
    """optionId = kind(allow_once·allow_always·reject_once), name = cn 화면 글 그대로."""
    return [PermissionOption(option_id=OPT_ALLOW_ONCE, name="Continue", kind="allow_once"),   # 1 계속
            PermissionOption(option_id=OPT_ALLOW_ALWAYS, name="Continue + don't ask again", kind="allow_always"),   # 2 계속+다시 묻지 않기
            PermissionOption(option_id=OPT_REJECT_ONCE, name="No, and tell Continue what to do differently", kind="reject_once")]   # 3 아니오


# ─────────────────────────────────────────────────────────────────────────────
# AskQuestion — elicitation 형식 / 승인 형식 대체
# ─────────────────────────────────────────────────────────────────────────────

ANSWER_PREFIX = "answer_"                                    # 승인 형식 대체 때 선택지 optionId 앞붙이(answer_0, answer_1 …)
OPT_SKIP = "skip"                                            # 승인 형식 대체 때 '답하지 않기'


def question_schema(question: str, options: Any, default_answer: Optional[str]) -> dict:   # elicitation 요청 스키마
    """선택지가 있으면 choice(oneOf) + custom(직접 입력, cn QuizPrompt처럼 선택지가 있어도 타이핑 가능), 없으면 answer 하나."""
    if isinstance(options, list) and options:                # 선택지가 있으면
        choice: dict = {"type": "string", "title": question,  # 선택지 필드
                        "oneOf": [EnumOption(const=str(o), title=str(o)).model_dump(by_alias=True, exclude_none=True) for o in options]}   # 선택지 목록
        if default_answer:                                   # 기본 답이 있으면
            choice["default"] = default_answer              # 기본값으로
        return {"type": "object", "properties": {"choice": choice,   # 선택지 필드
                                                 "custom": {"type": "string", "title": "Custom answer (optional)"}}}   # 직접 입력 필드
    answer: dict = {"type": "string", "title": question}     # 자유 답 필드
    if default_answer:                                       # 기본 답이 있으면
        answer["default"] = default_answer                  # 기본값으로
    return {"type": "object", "properties": {"answer": answer}, "required": ["answer"]}   # 답 필수


def answer_from_elicitation(content: Any, default_answer: Optional[str]) -> tuple[str, bool]:   # elicitation 응답 → (답, 직접 입력?)
    """custom(앞뒤 공백 제거)이 있으면 그것(직접 입력), 아니면 choice, 아니면 answer, 다 없으면 기본 답 또는 ""."""
    c = content if isinstance(content, dict) else {}         # 응답 내용
    custom = str(c.get("custom") or "").strip()              # 직접 입력(cn: customInput.trim())
    if custom:                                               # 직접 입력이 있으면
        return custom, True                                  # 그것이 답
    for k in ("choice", "answer"):                           # 선택·자유 답 차례로
        v = c.get(k)                                        # 값
        if isinstance(v, str) and v:                         # 글이면
            return v, k == "answer"                          # 자유 답은 직접 입력으로 친다
    return default_answer or "", False                       # 기본 답(없으면 빈 글)


def question_permission_options(options: Any, default_answer: Optional[str]) -> list[PermissionOption]:   # 승인 형식 대체 선택지
    """선택지마다 allow_once 한 개(answer_<번호>), 선택지가 없고 기본 답이 있으면 그것, 끝에 '건너뛰기'(reject_once)."""
    out = []                                                # 결과
    items = [str(o) for o in options] if isinstance(options, list) and options else ([default_answer] if default_answer else [])   # 보일 답들
    for i, o in enumerate(items):                            # 답마다
        out.append(PermissionOption(option_id=f"{ANSWER_PREFIX}{i}", name=o, kind="allow_once"))   # 답 선택지
    out.append(PermissionOption(option_id=OPT_SKIP, name="Skip", kind="reject_once"))   # 답하지 않기
    return out                                              # 선택지 목록


def answer_from_option(option_id: str, options: Any, default_answer: Optional[str]) -> str:   # 승인 응답 → 답
    """answer_<번호> 면 그 답, 그 밖(Skip·취소)은 기본 답 또는 ""."""
    items = [str(o) for o in options] if isinstance(options, list) and options else ([default_answer] if default_answer else [])   # 선택지와 같은 목록
    if option_id.startswith(ANSWER_PREFIX):                  # 답 선택지면
        try:                                                # 번호 해석
            return items[int(option_id[len(ANSWER_PREFIX):])]   # 그 답
        except (ValueError, IndexError):                    # 잘못된 번호
            pass                                            # 아래로
    return default_answer or ""                              # 기본 답 또는 빈 글


# ─────────────────────────────────────────────────────────────────────────────
# 계획(plan) — Checklist 도구 결과(ChecklistDisplay.tsx 체크 줄 해석)
# ─────────────────────────────────────────────────────────────────────────────

_CHECK_LINE = re.compile(r"^(\s*)-\s*\[([ x])\]\s*(.*)$")    # ChecklistDisplay.tsx:20 정규식 그대로


def checklist_to_plan(checklist: Any) -> list[PlanEntry]:   # 체크리스트 글 → 계획 항목
    """'- [ ] 할 일' → pending, '- [x] 한 일' → completed. 체크 줄이 아닌 줄은 뺀다. priority 는 medium 고정."""
    if not isinstance(checklist, str):                       # 글이 아니면
        return []                                           # 빈 계획
    out = []                                                # 결과
    for line in checklist.split("\n"):                       # 줄마다
        m = _CHECK_LINE.match(line)                          # 체크 줄인가
        if m:                                               # 맞으면
            out.append(PlanEntry(content=m.group(3), priority="medium",   # 항목 글
                                 status="completed" if m.group(2) == "x" else "pending"))   # x 면 완료
    return out                                              # 계획 항목 목록


# ─────────────────────────────────────────────────────────────────────────────
# 모드(session modes + session config options)
# ─────────────────────────────────────────────────────────────────────────────

MODES = (("normal", "normal", "Ask before running tools that need permission"),   # (id, 이름, 설명) — normal: 정책대로 묻기
         ("plan", "plan", "Start in plan mode (read-only tools)"),   # shared-options.ts --readonly 설명
         ("auto", "auto", "Start in auto mode (all tools allowed)"))   # shared-options.ts --auto 설명
MODE_CONFIG_ID = "mode"                                      # 설정 선택지 id


def mode_state(current: str) -> SessionModeState:           # session/new 응답의 modes
    """currentModeId + availableModes(normal·plan·auto)."""
    return SessionModeState(current_mode_id=current,          # 지금 모드
                            available_modes=[SessionMode(id=i, name=n, description=d) for i, n, d in MODES])   # 모드 셋


def config_options(current: str) -> list[SessionConfigOptionSelect]:   # session config options(모드 하나)
    """모드를 select 설정 하나로(category mode) — 규격이 예고한 대체 방식도 같이 지원."""
    return [SessionConfigOptionSelect(id=MODE_CONFIG_ID, name="Mode", category="mode", type="select",   # 모드 설정
                                      current_value=current,   # 지금 값
                                      options=[SessionConfigSelectOption(value=i, name=n, description=d) for i, n, d in MODES])]   # 선택지


# ─────────────────────────────────────────────────────────────────────────────
# 사용량
# ─────────────────────────────────────────────────────────────────────────────


def add_usage(total: Optional[dict], u: Any) -> Optional[dict]:   # 한 턴의 사용량 누적
    """OpenAI 형식 사용량(prompt_tokens·completion_tokens·prompt_tokens_details)을 더한다."""
    if not isinstance(u, dict):                              # 사용량이 아니면
        return total                                        # 그대로
    t = dict(total or {"input": 0, "output": 0, "cached": 0})   # 누적 시작값
    t["input"] += int(u.get("prompt_tokens") or 0)           # 입력 토큰
    t["output"] += int(u.get("completion_tokens") or 0)      # 출력 토큰
    d = u.get("prompt_tokens_details") or {}                 # 캐시 세부
    t["cached"] += int(d.get("cached_tokens") or d.get("cache_read_tokens") or 0)   # 캐시 읽기 토큰
    return t                                                # 누적값


def usage_to_acp(total: Optional[dict]) -> Optional[Usage]:  # 누적 사용량 → ACP Usage
    """PromptResponse.usage(totalTokens·inputTokens·outputTokens·cachedReadTokens). 사용량이 없었으면 None."""
    if not total:                                            # 없으면
        return None                                         # 빠짐
    return Usage(total_tokens=total["input"] + total["output"], input_tokens=total["input"],   # 합계·입력
                 output_tokens=total["output"], cached_read_tokens=total["cached"] or None)   # 출력·캐시(0이면 생략)


# ─────────────────────────────────────────────────────────────────────────────
# 질문(session/prompt) → cn 사용자 메시지(글 + contextItems)
# ─────────────────────────────────────────────────────────────────────────────

_FRAGMENT = re.compile(r"^L?(\d+)(?:[-:,]L?(\d+))?")         # URI 조각의 줄 범위(#L10-20, #L10:20, #10)


def uri_to_path(uri: str) -> Optional[str]:                 # file:// URI → 경로
    """file:///C:/a/b.cs → C:/a/b.cs, file:///Users/x → /Users/x, file://server/share/x → //server/share/x. file 이 아니면 None.
    Windows(win32)에서는 구분자를 '\\' 로 바꾼 OS 경로(C:\\a\\b.cs, \\\\server\\share\\x) — IDE 버퍼 경로·도구 경로와 같은 모양이어야
    fs/read_text_file 위임·@상대경로 계산·Read 기록이 맞는다."""
    p = urllib.parse.urlparse(uri)                           # URI 해석
    if p.scheme != "file":                                   # file 이 아니면
        return None                                         # 경로 아님
    path = urllib.parse.unquote(p.path)                      # %xx 풀기
    if re.match(r"^/[A-Za-z]:", path):                       # Windows 드라이브(/C:/…)
        path = path[1:]                                      # 앞 / 제거
    if p.netloc and p.netloc != "localhost":                 # UNC(\\server\share) 항목
        path = f"//{p.netloc}{path}"                         # //server/share/… 항목
    if node_platform() == "win32":                           # Windows 면
        path = path.replace("/", "\\")                       # OS 구분자로(드라이브·UNC 모두)
    return path                                             # 경로


def _fragment_suffix(uri: str) -> str:                      # 선택 영역 표시(#L시작-끝)
    """URI 조각이 줄 범위면 '#L10-20'(끝이 없으면 '#L10'), 아니면 ''."""
    frag = urllib.parse.urlparse(uri).fragment               # # 뒤
    m = _FRAGMENT.match(frag) if frag else None              # 줄 범위인가
    if not m:                                               # 아니면
        return ""                                           # 표시 없음
    return f"#L{m.group(1)}-{m.group(2)}" if m.group(2) else f"#L{m.group(1)}"   # 범위 표시


def get_last_n_path_parts(filepath: str, n: int) -> str:    # core/util/uri.ts getLastNPathParts 항목
    """끝에서 n 개 경로 조각을 '/'로(구분자 / 와 \\ 둘 다)."""
    if n <= 0:                                              # n 이 0 이하면
        return ""                                           # 빈 글
    return "/".join(re.split(r"[\\/]", filepath)[-n:])       # 끝 n 개를 / 로 이음


def file_context_item(path: str, content: str, uri: Optional[str] = None, name_suffix: str = "") -> dict:   # contextItem 하나
    """formatMessageWithFiles 의 contextItem 모양 그대로: id{providerTitle:'file', itemId}, content, name(basename), description(끝 2조각), uri{type:'file', value}."""
    base = re.split(r"[\\/]", path)[-1] if path else path    # path.basename(구분자 둘 다)
    return {"id": {"providerTitle": "file", "itemId": str(uuid.uuid4())},   # id(uuidv4) 반환
            "content": content,                              # 파일 내용(선택 영역이면 그 글)
            "name": base + name_suffix,                      # 이름(모델에 보이는 <context name="…">)
            "description": get_last_n_path_parts(path, 2),   # 설명
            "uri": {"type": "file", "value": uri or f"file://{path}"}}   # 원래 URI


def _mention(path: str, cwd: str) -> str:                   # @경로 글(사용자가 @파일을 고른 것과 같게)
    """작업 폴더 안이면 상대 경로(/ 구분), 밖이면 절대 경로."""
    try:                                                    # 상대 경로 계산(드라이브가 다르면 실패)
        rel = os.path.relpath(path, cwd)                     # 상대 경로
    except ValueError:                                       # Windows 다른 드라이브
        return path                                         # 절대 경로 그대로
    return path if rel.startswith("..") else rel.replace("\\", "/")   # 밖이면 절대, 안이면 상대


def prompt_to_user_message(blocks: list, cwd: str, read_file: Callable[[str], Optional[str]]) -> tuple[str, list[dict], list[str]]:   # 질문 블록 → 사용자 메시지
    """ACP 질문 블록들 → (사용자 글, contextItems, 무시한 블록 설명).
    - text: 그대로 이어 붙임(블록 사이 구분자 없음)
    - resource_link(file): 글에 '@경로'를 넣고(cn 입력창 @파일 선택과 같게) 파일 내용을 contextItem 으로(없으면 내용 = 경로 — cn ENOENT 처리)
    - resource(내장 글): IDE가 보낸 현재 파일·선택 영역 — 글에는 넣지 않고 contextItem 만(선택 영역이면 이름 뒤 #L시작-끝)
    - image·audio·blob: 무시(능력에 알리지 않음)"""
    text_parts: list[str] = []                               # 글 조각
    items: list[dict] = []                                   # 첨부 맥락
    ignored: list[str] = []                                  # 무시한 것
    for b in blocks:                                         # 블록마다
        kind = getattr(b, "type", None) if not isinstance(b, dict) else b.get("type")   # 블록 종류
        get = (lambda k, _b=b: getattr(_b, k, None)) if not isinstance(b, dict) else (lambda k, _b=b: _b.get(k))   # 필드 읽기(자료형·dict)
        if kind == "text":                                   # 글
            text_parts.append(get("text") or "")             # 그대로
        elif kind == "resource_link":                        # 파일 링크(사용자 @언급)
            uri = get("uri") or ""                          # URI 항목
            path = uri_to_path(uri)                          # 경로
            if path is None:                                 # file 이 아니면
                text_parts.append(f"@{get('name') or uri}")  # 이름만 글로
                ignored.append(f"resource_link {uri}")       # 내용은 못 읽음
                continue                                    # 다음 블록
            text_parts.append(f"@{_mention(path, cwd)}")     # 글에 @경로
            content = read_file(path)                        # 파일 내용(IDE 위임 또는 디스크)
            items.append(file_context_item(path, content if content is not None else _mention(path, cwd), uri))   # 없으면 경로를 내용으로(cn)
        elif kind == "resource":                             # 내장 리소스(IDE 맥락)
            res = get("resource")                            # 내용물
            rtext = getattr(res, "text", None) if not isinstance(res, dict) else res.get("text")   # 글
            ruri = (getattr(res, "uri", None) if not isinstance(res, dict) else res.get("uri")) or ""   # URI 항목
            if rtext is None:                                # blob(이진)
                ignored.append(f"resource blob {ruri}")      # 무시
                continue                                    # 다음 블록
            path = uri_to_path(ruri) or ruri                 # 경로(아니면 URI 그대로)
            items.append(file_context_item(path, rtext, ruri, _fragment_suffix(ruri)))   # 첨부 맥락
        else:                                                # image·audio 등
            ignored.append(str(kind))                        # 무시
    return "".join(text_parts), items, ignored               # 결과 셋


# ─────────────────────────────────────────────────────────────────────────────
# session/load 되살리기 — 저장된 기록 → session/update 목록(사용자·응답 글, 도구 카드)
# ─────────────────────────────────────────────────────────────────────────────


def _content_text(content: Any) -> str:                     # 메시지 내용 → 글
    """글이면 그대로, 부분 배열이면 text 부분만 이어 붙임."""
    if isinstance(content, str):                             # 글이면
        return content                                      # 그대로
    if isinstance(content, list):                            # 부분 배열이면
        return "".join(p.get("text") or "" for p in content if isinstance(p, dict) and p.get("type") == "text")   # 글 부분만
    return ""                                               # 그 밖은 빈 글


def history_to_updates(items: list[dict], cwd: str) -> list[dict]:   # 기록 → 되살릴 업데이트(dict 모양)
    """[{"kind": "user"|"agent", "text"}, {"kind": "tool", "id", "name", "args", "status", "output"}] — 서버가 ACP 자료형으로 바꿔 보낸다.
    system 항목은 뺀다(cn 세션 저장도 뺀다)."""
    out: list[dict] = []                                     # 결과
    for it in items:                                         # 항목마다
        msg = it.get("message") or {}                        # 메시지
        role = msg.get("role")                               # 역할
        if role == "user":                                   # 사용자
            out.append({"kind": "user", "text": _content_text(msg.get("content"))})   # 사용자 글
        elif role == "assistant":                            # 응답
            text = _content_text(msg.get("content"))         # 응답 글
            if text:                                         # 글이 있으면
                out.append({"kind": "agent", "text": text})  # 응답 글
            states = {s.get("toolCallId"): s for s in it.get("toolCallStates") or []}   # 도구 상태(id별)
            for tc in msg.get("toolCalls") or []:            # 도구 호출마다
                fn = tc.get("function") or {}                # 함수 부분
                st = states.get(tc.get("id")) or {}          # 그 호출의 상태
                output = "\n".join(str(o.get("content")) for o in st.get("output") or [])   # 결과 글(변환 규칙과 같게)
                try:                                        # 인자 해석
                    import json                              # 지연 import
                    args = json.loads(fn.get("arguments") or "{}")   # 인자 객체
                except ValueError:                           # 해석 실패
                    args = {}                               # 빈 인자
                out.append({"kind": "tool", "id": tc.get("id"), "name": fn.get("name"), "args": args,   # 도구 카드
                            "status": "completed" if st.get("status") == "done" else "failed", "output": output})   # 상태·결과
    return out                                              # 업데이트 목록
