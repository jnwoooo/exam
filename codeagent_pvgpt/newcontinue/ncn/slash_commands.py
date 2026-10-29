# ncn/slash_commands.py — 콘솔 대화형 슬래시 명령. cn extensions/cli/src/slashCommands.ts(handleSlashCommands) + commands/commands.ts
#   (SYSTEM_SLASH_COMMANDS·getAllSlashCommands) + commands/init.ts(createInitPrompt) + infoScreen.ts(일부) + util/loadMarkdownSkills.ts
# 결과 모양은 cn SlashCommandResult(useChat.types.ts)와 같은 dict: output·clear·exit·compact·newInput·openSessionSelector·openJobsSelector·openExportSelector.
# 화면(ncn/interactive.py)이 결과를 처리한다(useChat.helpers.ts processSlashCommandResult — output 은 기록에 system 메시지로 남는다).
# 로컬에서 의미 없는 것(제외, 이유):
#   /update  — npm 으로 cn 자체를 갱신(폐쇄망 배포는 ZIP·VSIX 교체 — 계획 8장)
#   /config  — Hub 설정 고르기 화면(로그인·조직 목록 필요). ncn 은 --config 로 지정
#   /model   — 모델 고르기 화면(1차 범위 밖: LLMClient·토큰 한도 다시 조립 필요 — 다음 단계)
#   /mcp     — MCP 서버 관리 화면(Hub 서버 목록·재접속 UI). 상태는 ncn --doctor 로 확인
#   원격 모드(/diff·/apply, REMOTE_MODE_SLASH_COMMANDS) — cn serve·Hub 원격 에이전트 전용
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # /import 세션 파일
import os                                                    # 경로
import re                                                    # 이름 정리
import uuid                                                  # /import 새 id
from typing import Any, Optional                             # 타입

from ncn.console_render import Style, help_message            # 색·도움말 글

SYSTEM_SLASH_COMMANDS = [                                    # commands.ts 원문(이름, 설명) — 순서 그대로
    ("help", "Show help message"), ("clear", "Clear the chat history"), ("update", "Update the Continue CLI"),   # help·clear·update 명령
    ("info", "Show session information"), ("model", "Switch between available chat models"),   # info·model 명령
    ("config", "Switch configuration"), ("mcp", "Manage MCP server connections"), ("init", "Create an AGENTS.md file"),   # config·mcp·init 명령
    ("compact", "Summarize chat history into a compact form"), ("resume", "Resume a previous chat session"),   # compact·resume 명령
    ("fork", "Start a forked chat session from the current history"), ("title", "Set the title for the current session"),   # fork·title 명령
    ("rename", "Rename the current session"), ("exit", "Exit the chat"), ("jobs", "List background jobs"),   # rename·exit·jobs 명령
    ("sessions", "Show all chat sessions"), ("skills", "List all available skills"),   # sessions·skills 명령
    ("import-skill", "Import a skill from a URL or name into ~/.continue/skills"),   # import-skill 명령
    ("export", "Export a session to JSON file"), ("import", "Import a session from JSON file"),   # export·import 명령
]                                                            # 표 끝

UNSUPPORTED = {"update", "config", "model", "mcp"}            # ncn 이 막은 명령(위 머리 주석의 이유)
UNSUPPORTED_MESSAGE = "ncn: /{name} 은(는) 콘솔 대화형에서 지원하지 않습니다(Hub·화면 전용 기능)."   # 안내 글(ncn 전용 — 기록에 넣지 않음)
EXPORTED_SESSION_VERSION = 1                                 # slashCommands.ts 원문 값


def create_init_prompt() -> str:                             # commands/init.ts createInitPrompt — 원문 글
    """/init 이 LLM 에 보내는 글(원문 그대로 — 줄 끝 공백 포함)."""
    rel = os.path.join(".continue", "rules", "review.md")     # createRelativeRuleFilePath("review") 대응
    return ("Please analyze this repository and create a comprehensive AGENTS.md file, along with a custom slash command. "   # 1문단
            "Use your available tools to understand the project structure, read important files like README.md, package.json, "   # (이어짐)
            "requirements.txt, and other configuration files to understand the technology stack and setup.\n\n"   # (이어짐)
            "Create an AGENTS.md file with the following structure:\n\n"   # 구조 안내
            "# Repository Overview\n\n"                      # 제목
            "## Project Description\n"                       # 소제목
            "- What this project does\n"                     # 항목
            "- Main purpose and goals  \n"                   # 항목(원문 끝 공백 2개)
            "- Key technologies used\n\n"                    # 항목
            "## Architecture Overview\n"                     # 소제목
            "- High-level architecture\n"                    # 항목
            "- Main components and their relationships\n"    # 항목
            "- Data flow and system interactions\n\n"        # 항목
            "## Directory Structure\n"                       # 소제목
            "- Important directories and their purposes\n"   # 항목
            "- Key files and configuration\n"                # 항목
            "- Entry points and main modules\n\n"            # 항목
            "## Development Workflow\n"                      # 소제목
            "- How to build/run the project\n"               # 항목
            "- Testing approach\n"                           # 항목
            "- Development environment setup\n"              # 항목
            "- Lint and format commands\n\n"                 # 항목
            f"Additionally, create a slash command file at {rel} with the following structure:\n\n"   # 슬래시 명령 파일
            "```md\n---\ninvokable: true\n---\n\n"           # 머리말
            "Review this code for potential issues, including:\n\n"   # 본문
            "<insert custom things to look for based on the repository details you find>\n\n"   # 자리표시
            "Provide specific, actionable feedback for improvements.\n```\n\n"   # 끝
            "This slash command will be invokable using /review and will provide instructions for code review tasks common to this repository.\n\n"   # 안내
            "Please create both the AGENTS.md file and the .continue/rules/review.md file using the Write tool after analyzing the repository. "   # 마무리
            "Focus on providing actionable information that would help both AI agents and human developers understand and work effectively "   # (이어짐)
            "with this codebase. Keep the files concise but informational.")   # (끝)


def skill_slash_command_name(skill: Any) -> str:             # loadMarkdownSkills.ts getSkillSlashCommandName 항목
    """이름을 소문자·[a-z0-9-] 로 정리해 'skill-<이름>'. 비면 폴더 이름, 그것도 비면 'skill-skill'."""
    safe = re.sub(r"-+$", "", re.sub(r"^-+", "", re.sub(r"[^a-z0-9]+", "-", skill.name.strip().lower())))   # 정리
    if safe:                                                 # 이름이 남으면
        return f"skill-{safe}"                               # skill-이름
    d = os.path.basename(os.path.dirname(skill.path)).lower()   # 폴더 이름
    d = re.sub(r"-+$", "", re.sub(r"^-+", "", re.sub(r"[^a-z0-9]+", "-", d)))   # 같은 정리
    return f"skill-{d or 'skill'}"                           # 대체 이름


class SlashContext:                                          # 명령이 쓰는 부품 묶음
    """core(ChatCore)·색·작업 폴더. 시험에서 바꿔 끼우기 쉽게 따로 둔다."""

    def __init__(self, core: Any, style: Style, environ: Any):   # 부품을 받는 생성자
        self.core = core                                     # 세션 부품(cfg·sessions·history)
        self.style = style                                   # 색
        self.environ = environ                               # 환경변수(세션 폴더)

    def skills(self) -> list:                                # 마크다운 기술 목록
        """loadMarkdownSkills — 실패하면 빈 목록."""
        try:                                                 # 읽기 실패 무시
            from ncn.tools.skills import load_markdown_skills   # 기술 읽기
            return load_markdown_skills(self.core.cwd, str(self.core.gdir))[0]   # (목록, 오류) 중 목록
        except Exception:                                    # 실패
            return []                                        # 없음


def all_slash_commands(ctx: SlashContext) -> list[str]:      # getAllSlashCommands 항목(이름만)
    """시스템 명령 + 설정 prompts + invokable 규칙 + 기술 명령 이름."""
    names = [n for n, _ in SYSTEM_SLASH_COMMANDS]            # 시스템
    names += [(p or {}).get("name") or "" for p in (ctx.core.cfg.prompts or []) if isinstance(p, dict) or p is None]   # 설정 프롬프트
    names += [r.get("name") or "" for r in (ctx.core.cfg.rules or []) if isinstance(r, dict) and r.get("invokable") is True]   # 호출형 규칙
    names += [skill_slash_command_name(s) for s in ctx.skills()]   # 기술
    return names                                             # 이름 목록


def handle_slash_commands(text: str, ctx: SlashContext) -> Optional[dict]:   # handleSlashCommands 항목
    """'/' 로 시작하면 명령 결과(dict), 아니면 None(일반 메시지). 모르는 명령이 어떤 이름에도 안 걸리면 None(일반 글로 보냄)."""
    if not text.startswith("/") or not text.strip().startswith("/"):   # 맨 앞이 '/' 가 아니면
        return None                                          # 일반 메시지
    command, *args = text[1:].split(" ")                     # input.slice(1).split(" ") 대응
    st = ctx.style                                           # 색
    handler = _HANDLERS.get(command)                         # 시스템 명령
    if handler is not None:                                  # 있으면
        return handler(args, ctx)                            # 결과
    if command in UNSUPPORTED:                               # ncn 이 막은 명령
        return {"notice": UNSUPPORTED_MESSAGE.format(name=command)}   # 안내만(기록 안 남김)
    for p in ctx.core.cfg.prompts or []:                     # 설정 프롬프트
        if isinstance(p, dict) and p.get("name") == command:   # 이름이 같으면
            return {"newInput": (p.get("prompt") or "") + " ".join(args)}   # 원문: prompt + args.join(" ")(사이 공백 없음)
    for r in ctx.core.cfg.rules or []:                       # 호출형 규칙
        if isinstance(r, dict) and r.get("invokable") is True and r.get("name") == command:   # 이름이 같으면
            return {"newInput": (r.get("rule") or "") + " " + " ".join(args)}   # 원문: rule + " " + args
    skills = ctx.skills()                                    # 기술
    norm = command.strip().lower()                           # 정규화한 명령
    for s in skills:                                         # 기술마다
        if skill_slash_command_name(s) == norm:              # 이름이 같으면
            return {"newInput": f'Load the skill using the **Skills** tool and then set the **skill_name** parameter to "{s.name}".'}   # 원문
    if not any(command.lower() in n.lower() for n in all_slash_commands(ctx)):   # 어떤 명령 이름에도 안 걸리면
        return None                                          # 일반 글로
    return {"output": f"Unknown command: {command}"}         # 원문


# ─────────────────────────────────────────────────────────────────────────────
# 명령별 처리기(원문 글)
# ─────────────────────────────────────────────────────────────────────────────


def _help(args: list, ctx: SlashContext) -> dict:            # /help 항목
    return {"output": help_message(ctx.style)}               # 도움말 글


def _clear(args: list, ctx: SlashContext) -> dict:           # /clear 항목
    return {"clear": True, "output": "Chat history cleared"}   # 원문


def _exit(args: list, ctx: SlashContext) -> dict:            # /exit 항목
    return {"exit": True, "output": "Goodbye!"}              # 원문


def _compact(args: list, ctx: SlashContext) -> dict:         # /compact 항목
    return {"compact": True}                                 # 화면이 수동 압축


def _fork(args: list, ctx: SlashContext) -> dict:            # /fork 항목(handleFork)
    """'cn --fork <id>' 를 보여 준다(클립보드 복사는 ncn 에 없음 → 원문의 실패 경로 글). 'cn' 글자는 원문 그대로(기록에 남는 글)."""
    try:                                                     # 세션 없음 대비
        sid = ctx.core.sessions.get_current()["sessionId"]   # 현재 세션 id
        return {"exit": False, "output": ctx.style.paint(f"cn --fork {sid}", "gray")}   # 원문(clipboardy 없음 경로)
    except Exception as e:                                   # 실패
        return {"exit": False, "output": ctx.style.paint(f"Failed to create fork command: {e}", "red")}   # 원문


def _title(args: list, ctx: SlashContext) -> dict:           # /title·/rename 항목(handleTitle)
    title = " ".join(args).strip()                           # 제목
    st = ctx.style                                           # 색
    if not title:                                            # 비었으면
        return {"exit": False, "output": st.paint("Please provide a title. Usage: /title <your title>", "yellow")}   # 원문
    try:                                                     # 저장 실패 대비
        sm = ctx.core.sessions                               # 세션 관리자
        sm.get_current()["title"] = title                    # updateTitle 대응
        sm.save()                                            # saveSession 대응
        return {"exit": False, "output": st.paint(f'Session title updated to: "{title}"', "green")}   # 원문
    except Exception as e:                                   # 실패
        return {"exit": False, "output": st.paint(f"Failed to update title: {e}", "red")}   # 원문


def _jobs(args: list, ctx: SlashContext) -> dict:            # /jobs 항목
    return {"openJobsSelector": True}                        # 화면이 목록 표시


def _skills(args: list, ctx: SlashContext) -> dict:          # /skills 항목(handleSkills)
    st = ctx.style                                           # 색
    skills = ctx.skills()                                    # 기술
    if not skills:                                           # 없으면
        return {"exit": False, "output": st.paint("No skills found. Add skills under .continue/skills or .claude/skills.", "yellow")}   # 원문
    lines = [f"{st.paint(s.name, 'cyan')} - {s.description} {st.paint(f'({s.path})', 'gray')}" for s in skills]   # 기술 줄
    return {"exit": False, "output": "\n".join([st.paint("Available skills:", "bold"), ""] + lines)}   # 원문


def _import_skill(args: list, ctx: SlashContext) -> dict:    # /import-skill 항목
    q = " ".join(args).strip()                               # 식별자
    if not q:                                                # 비었으면
        return {"exit": False, "output": ctx.style.paint("Please provide a skill URL or name. Usage: /import-skill <url-or-name>", "yellow")}   # 원문
    from ncn.tools.skills import build_import_skill_prompt   # 원문 프롬프트
    return {"newInput": build_import_skill_prompt(q)}         # LLM 에 보냄


def _sessions(args: list, ctx: SlashContext) -> dict:        # /resume·/sessions 항목
    return {"openSessionSelector": True}                     # 화면이 세션 목록


def _export(args: list, ctx: SlashContext) -> dict:          # /export 항목
    return {"exit": False, "openExportSelector": True}       # 화면이 세션 고르기


def _init(args: list, ctx: SlashContext) -> dict:            # /init 항목(handleInit)
    return {"newInput": create_init_prompt()}                 # AGENTS.md 만들기 요청


def _info(args: list, ctx: SlashContext) -> dict:            # /info 항목(infoScreen.ts — 버전·폴더·설정·세션·사용량 부분)
    """원문 줄 모양(진단 정보 — node 경로·npm 버전 — 는 Node 전용이라 뺌)."""
    from ncn import __version__                              # 버전
    from ncn.session import sessions_folder                  # 세션 폴더
    from ncn.tools.formatters import to_locale_string        # 천 단위 쉼표
    st, core = ctx.style, ctx.core                           # 줄임
    lines = [st.paint("CLI Information:", "white"), f"  Version: {st.paint(__version__, 'green')}",   # CLI 정보
             f"  Working Directory: {st.paint(os.getcwd(), 'blue')}", "", st.paint("Configuration:", "white")]   # 작업 폴더·설정 머리
    lines.append(f"  {st.paint(f'Using {core.cfg.name or ""}', 'gray')}")   # 설정 이름
    if core.cfg.config_path:                                 # 설정 경로
        lines.append(f"  Path: {st.paint(str(core.cfg.config_path), 'blue')}")   # 경로
    lines.append(f"  Model: {st.paint(core.model.name or core.model.model or '', 'cyan')}")   # 모델
    cur = core.sessions.get_current()                        # 현재 세션
    lines += ["", st.paint("Session:", "white"), f"  Title: {st.paint(cur.get('title') or '', 'green')}",   # 세션 머리·제목
              f"  ID: {st.paint(cur['sessionId'], 'gray')}",   # id 대응
              f"  File: {st.paint(os.path.join(sessions_folder(ctx.environ), cur['sessionId'] + '.json'), 'blue')}"]   # 파일
    u = core.sessions.usage                                  # 누적 사용량
    lines += ["", st.paint("Usage:", "white")]               # 사용량 머리
    if (u.get("totalCost") or 0) > 0:                        # 사용량이 있으면
        lines.append(f"  Total Cost: {st.paint('$' + format(u['totalCost'], '.6f'), 'green')}")   # 비용
        lines.append(f"  Input Tokens: {st.paint(to_locale_string(u['promptTokens']), 'cyan')}")   # 입력 토큰
        lines.append(f"  Output Tokens: {st.paint(to_locale_string(u['completionTokens']), 'cyan')}")   # 출력 토큰
        lines.append(f"  Total Tokens: {st.paint(to_locale_string(u['promptTokens'] + u['completionTokens']), 'cyan')}")   # 합계
    else:                                                    # 없으면
        lines.append(f"  {st.paint('No usage data yet', 'gray')}")   # 원문
    return {"output": "\n".join(lines)}                      # 글


def _import(args: list, ctx: SlashContext) -> dict:          # /import 항목(handleImport)
    st = ctx.style                                           # 색
    fp = " ".join(args).strip()                              # 파일 경로
    if not fp:                                               # 비었으면
        return {"exit": False, "output": st.paint("Please provide a file path. Usage: /import <file-path>", "yellow")}   # 원문
    if not os.path.exists(fp):                               # 없는 파일
        return {"exit": False, "output": st.paint(f"File not found: {fp}", "red")}   # 원문
    from ncn.session import history_manager_load, history_manager_save   # 세션 파일
    try:                                                     # 읽기·검사·저장
        with open(fp, encoding="utf-8") as fh:               # 파일 열기
            data = json.load(fh)                             # JSON 대응
        sess = data.get("session") if isinstance(data, dict) else None   # 세션
        ok = (isinstance(data, dict) and data.get("version") == EXPORTED_SESSION_VERSION and isinstance(data.get("exportedAt"), str)   # 형식 검사
              and isinstance(sess, dict) and isinstance(sess.get("sessionId"), str) and isinstance(sess.get("title"), str)   # (이어짐)
              and isinstance(sess.get("history"), list))     # (끝)
        if not ok:                                           # 형식이 틀리면
            return {"exit": False, "output": st.paint("Invalid session file: expected a valid Continue exported session (version 1).", "red")}   # 원문
        existing = history_manager_load(sess["sessionId"], ctx.environ)   # 같은 id 가 있나
        if len(existing.get("history") or []) > 0:           # 있으면 새 id
            original = sess["sessionId"]                     # 원래 id
            sess = {**sess, "sessionId": str(uuid.uuid4())}  # 새 id
            history_manager_save(sess, ctx.environ)          # 저장
            return {"exit": False, "output": st.paint(f"Session imported with new ID: {sess['sessionId']}\n"   # 원문
                                                      + st.paint(f"(original ID: {original} already existed)", "gray"), "green")}   # (이어짐)
        history_manager_save(sess, ctx.environ)              # 저장
        return {"exit": False, "output": st.paint(f"Session imported: {sess['sessionId']} ({sess['title']})", "green")}   # 원문
    except Exception as e:                                   # 실패
        return {"exit": False, "output": st.paint(f"Failed to import session: {e}", "red")}   # 원문


_HANDLERS = {                                                # commandHandlers(원문 표 — ncn 이 막은 것은 뺌)
    "help": _help, "clear": _clear, "exit": _exit, "info": _info, "compact": _compact,   # 기본
    "resume": _sessions, "fork": _fork, "title": _title, "rename": _title, "init": _init,   # 세션·init
    "jobs": _jobs, "skills": _skills, "import-skill": _import_skill, "sessions": _sessions,   # 작업·기술
    "export": _export, "import": _import,                    # 내보내기·가져오기
}                                                            # 표 끝
