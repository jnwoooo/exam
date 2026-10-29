# ncn/sysmsg.py — 시스템 메시지. cn extensions/cli/src/systemMessage.ts(constructSystemMessage) + hubLoader.ts(processRule) 를 그대로 옮긴 것
# 원문 글(공백·줄바꿈 포함)은 systemMessage.ts 에서 복사했다 — 글자 하나라도 바꾸면 차등 시험이 깨진다.
# cn 특성 그대로:
#   - 기본 부분(작업 폴더·git 여부·플랫폼·날짜·git status)은 모듈을 불러올 때 한 번 계산(대화 중 바뀌지 않음). 날짜는 UTC(toISOString).
#   - AGENTS.md → AGENT.md → CLAUDE.md → CODEX.md 중 처음 있는 것(작업 폴더만).
#   - --rule 값은 processRule 결과로 한 번, ConfigService 가 config.rules 에 넣은 것으로 또 한 번 들어간다(중복 제거 없음 — cn 그대로).
#   - .continue/rules·<전역>/rules 의 규칙은 부를 때마다 다시 읽고, 이미 들어간 글과 같으면 뺀다.
from __future__ import annotations                           # 타입 주석 지연 평가

import datetime                                              # 오늘 날짜(UTC)
import logging                                               # cn console.warn 대응(표준 출력에 쓰지 않음)
import os                                                    # 환경변수·현재 폴더
import pathlib                                               # 경로 형식
import re                                                    # 정규식
import subprocess                                            # git 실행(execSync)
from dataclasses import dataclass                            # 기본 부분 스냅숏
from typing import Any, Mapping, Optional                    # 타입

from ncn.config.loader import LoadedConfig, load_markdown_rules_with_metadata   # 설정·마크다운 규칙
from ncn.tools.formatters import (JS_WS_CHARS, NodeFsError, node_platform, path_join, path_resolve,   # Node 흉내
                                  read_file_utf8)           # (위 import 이어짐) 파일 읽기

_log = logging.getLogger("ncn.sysmsg")                       # 경고 기록(stdout 금지)


# ───────────────────────── git 확인(systemMessage.ts:16-41) ─────────────────────────
def _exec_sync(cmd: str, cwd: str, capture: bool) -> Optional[str]:  # 셸 명령 실행 함수(execSync)
    """execSync — 셸로 실행, 종료 코드가 0 이 아니면 None. capture 면 표준 출력(utf-8)을 돌려준다(표준 오류는 그대로 부모로)."""
    try:                                                    # 실행 실패는 None 으로 처리
        r = subprocess.run(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,  # 셸로 실행, 입력은 막음
                           stdout=subprocess.PIPE if capture else subprocess.DEVNULL,  # capture 일 때만 표준 출력을 받음
                           stderr=None if capture else subprocess.DEVNULL, timeout=30)   # stdio: "ignore" 이면 전부 버림
    except (OSError, subprocess.SubprocessError):            # 실행 불가·시간 초과
        return None                                         # 실행 불가·시간 초과
    if r.returncode != 0:                                    # execSync 는 0 이 아니면 throw
        return None                                         # 종료 코드가 0 이 아니면 None
    return r.stdout.decode("utf-8", errors="replace") if capture else ""  # capture 면 출력 글, 아니면 빈 글


def is_git_repo(cwd: str) -> bool:                          # git 저장소 여부 함수(isGitRepo)
    """isGitRepo — `git rev-parse --is-inside-work-tree` 가 성공(종료 코드 0)하면 참(출력이 "false" 여도 참 — cn 그대로)."""
    return _exec_sync("git rev-parse --is-inside-work-tree", cwd, capture=False) is not None  # 명령이 성공하면 참


def get_git_status(cwd: str) -> str:                        # git status 글 함수(getGitStatus)
    """getGitStatus — git 저장소가 아니면 "Not a git repository", 깨끗하면 "Working tree clean", 실패면 "Git status not available"."""
    if not is_git_repo(cwd):                                # 저장소가 아니면
        return "Not a git repository"                       # 고정 문구 반환
    out = _exec_sync("git status --porcelain", cwd, capture=True)  # 변경 목록(porcelain) 받기
    if out is None:                                         # 실행 실패면
        return "Git status not available"                   # "사용 불가" 문구
    return out.strip(JS_WS_CHARS) or "Working tree clean"    # result.trim() || … — 공백 제거 후 비면 "깨끗함" 문구


# ───────────────────────── 기본 부분(baseSystemMessage) ─────────────────────────
@dataclass(frozen=True)                                     # 불변 데이터 클래스로 선언
class EnvSnapshot:                                          # 기본 부분에 들어갈 환경 스냅숏
    """기본 부분에 들어가는 값들(cn은 모듈 로드 때 한 번 계산)."""
    cwd: str                                                 # Working directory — 작업 폴더
    is_git_repo: bool                                        # Is directory a git repo — git 저장소 여부
    platform: str                                            # Platform(Node process.platform 값)
    today: str                                               # Today's date(UTC, YYYY-MM-DD) — 오늘 날짜(UTC)
    git_status: str                                          # gitStatus 내용


def snapshot_environment(cwd: str | os.PathLike) -> EnvSnapshot:  # 지금 값으로 스냅숏을 만드는 함수
    """지금 값으로 스냅숏을 새로 만든다(시험·재계산용). cn 은 isGitRepo() 를 두 번 부르므로 git 도 두 번 실행된다."""
    c = os.fspath(cwd)                                      # 경로를 문자열로
    return EnvSnapshot(                                     # 스냅숏 객체 생성
        cwd=c,                                              # 작업 폴더
        is_git_repo=is_git_repo(c),                         # git 저장소 여부
        platform=node_platform(),                           # 플랫폼
        today=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),   # new Date().toISOString().split("T")[0]  # 오늘 날짜(UTC)
        git_status=get_git_status(c),                       # git status 내용
    )


def format_base_system_message(s: EnvSnapshot) -> str:      # 기본 시스템 메시지 원문을 만드는 함수
    """baseSystemMessage 원문(systemMessage.ts:43-61)."""
    # f-string 원문: 아래 줄들은 문자열 안이라 줄 끝 주석을 달 수 없다
    return f"""You are an agent in the Continue CLI. Given the user's prompt, you should use the tools available to you to answer the user's question.

Notes:
1. IMPORTANT: You should be concise, direct, and to the point, since your responses will be displayed on a command line interface.
2. When relevant, share file names and code snippets relevant to the query
Here is useful information about the environment you are running in:
<env>
Working directory: {s.cwd}
Is directory a git repo: {"true" if s.is_git_repo else "false"}
Platform: {s.platform}
Today's date: {s.today}
</env>

As you answer the user's questions, you can use the following context:

<context name="gitStatus">This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.

{s.git_status}
</context>"""


_SNAPSHOTS: dict[str, EnvSnapshot] = {}                      # 작업 폴더별 스냅숏(한 번만 계산)


def get_snapshot(cwd: str | os.PathLike) -> EnvSnapshot:    # 작업 폴더별 스냅숏 얻기 함수
    """이 작업 폴더의 스냅숏(처음 한 번 계산 후 계속 같은 값 — cn 모듈 상수와 같음)."""
    c = os.fspath(cwd)                                      # 경로를 문자열로
    if c not in _SNAPSHOTS:                                 # 아직 계산 안 했으면
        _SNAPSHOTS[c] = snapshot_environment(c)             # 한 번 계산해 저장
    return _SNAPSHOTS[c]                                    # 저장된 값 반환


def refresh_snapshot(cwd: str | os.PathLike) -> EnvSnapshot:  # 스냅숏 다시 계산 함수(시험용)
    """스냅숏 다시 계산(시험용 — cn 에는 없는 동작)."""
    c = os.fspath(cwd)                                      # 경로를 문자열로
    _SNAPSHOTS[c] = snapshot_environment(c)                 # 새로 계산해 덮어씀
    return _SNAPSHOTS[c]                                    # 새 값 반환


try:                                                         # 모듈 로드 때 한 번(cn 과 같음): 지금 작업 폴더 기준
    get_snapshot(os.getcwd())                               # 현재 작업 폴더로 미리 계산
except Exception:                                            # 실패해도 불러오기는 계속(필요할 때 다시 계산)
    pass                                                    # 무시하고 계속


# ───────────────────────── hubLoader.ts processRule ─────────────────────────
class RuleError(Exception):                                 # 규칙 처리 실패 예외
    """processRule 실패(글은 cn 원문)."""


def process_rule(rule_spec: str, cwd: str | os.PathLike) -> str:  # --rule 값을 글로 바꾸는 함수(processRule)
    """processRule — 한 줄이고 경로처럼 보이면 파일을 읽고, 아니면 글 그대로(앞뒤 공백도 그대로)."""
    trimmed = rule_spec.strip(JS_WS_CHARS)                   # ruleSpec.trim() — 앞뒤 공백 제거본
    has_newline = re.search(r"[\r\n]", rule_spec) is not None  # 줄바꿈이 있는지
    looks_like_path = (not has_newline and (trimmed.startswith(".") or trimmed.startswith("/") or "\\" in trimmed  # 한 줄이면서 ., / 로 시작하거나 역슬래시가 있거나
                                            or re.search(r"\.[a-zA-Z]+$", trimmed) is not None))  # 확장자로 끝나면 파일 경로로 본다
    if looks_like_path:                                     # 경로처럼 보이면 파일을 읽는다
        try:                                                # 읽기 오류는 RuleError 로 감쌈
            absolute = path_resolve(os.fspath(cwd), trimmed)  # path.resolve(trimmed) — 작업 폴더 기준 절대경로
            if not os.path.exists(absolute):                # 파일이 없으면
                raise RuleError(f"Rule file not found: {rule_spec}")  # cn 원문 오류 글
            return read_file_utf8(absolute)                 # 파일 내용 반환
        except (RuleError, NodeFsError, OSError) as e:      # 규칙·Node 파일·OS 오류
            raise RuleError(f'Failed to read rule file "{rule_spec}": {e}') from e  # cn 원문 형식의 오류로 바꿔 올림
    return rule_spec                                        # 경로가 아니면 글 그대로 반환


# ───────────────────────── constructSystemMessage ─────────────────────────
PLAN_MODE_CONTEXT = ('\n<context name="planMode">You are operating in _Plan Mode_, which means that your goal is to help the user investigate their ideas and develop a plan before taking action. '  # plan 모드 추가 문구(원문)
                     'You only have access to read-only tools and should not attempt to circumvent them to write / delete / create files. '
                     'Ask the user to switch to agent mode if they want to make changes. For example, it is not acceptable to use the Bash tool to write to files.</context>\n')  # (원문 이어짐)
# 커밋 서명 안내 원문(문자열 안이라 줄 끝 주석 불가)
COMMIT_SIGNATURE_CONTEXT = """
<context name="commitSignature">When creating commits using any CLI or tool, include the following in the commit message:
Generated with [Continue](https://continue.dev)

Co-Authored-By: Continue <noreply@continue.dev>
</context>
"""
# 헤드리스 지시 원문(문자열 안이라 줄 끝 주석 불가)
HEADLESS_INSTRUCTIONS = """

IMPORTANT: You are running in headless mode. Provide ONLY your final answer to the user's question. Do not include explanations, reasoning, or additional commentary unless specifically requested. Be direct and concise."""
# JSON 지시 원문(문자열 안이라 줄 끝 주석 불가)
JSON_INSTRUCTIONS = """

IMPORTANT: You are operating in JSON output mode. Your final response MUST be valid JSON that can be parsed by JSON.parse(). The JSON should contain properties relevant to answer the user's question. You don't need to include any general "response" or "answer" field. Do not include any text before or after the JSON - the entire response must be parseable JSON.

Example response format:
{
  "property": "value"
}"""
AGENT_FILES = ["AGENTS.md", "AGENT.md", "CLAUDE.md", "CODEX.md"]   # 찾는 순서


def build_system_message(*, mode: str, headless: bool, format_json: bool, cwd: pathlib.Path, global_dir: pathlib.Path,  # 시스템 메시지 조립 함수(constructSystemMessage)
                         config: Optional[LoadedConfig], model: Any = None, additional_rules: Optional[list[str]] = None,  # 설정·모델·--rule 목록
                         environ: Optional[Mapping[str, str]] = None, snapshot: Optional[EnvSnapshot] = None,  # 환경변수·스냅숏(시험용)
                         warnings: Optional[list[str]] = None) -> str:  # 경고 글을 모을 목록
    """constructSystemMessage(mode, additionalRules(--rule), format, headless) 와 같은 글.
    mode = "normal" | "plan" | "auto". model 은 cn 이 쓰지 않는다(받기만 함). environ 은 dotenv 적용된 환경(기본 os.environ).
    snapshot 을 주면 그 값으로 기본 부분을 만든다(시험용), 없으면 작업 폴더별 첫 계산값."""
    c = os.fspath(cwd)                                       # process.cwd() — 작업 폴더
    env = os.environ if environ is None else environ         # process.env — 환경변수
    warn = warnings if warnings is not None else []          # console.warn 글
    agent_content = ""                                       # AGENTS.md 등 내용
    try:                                                    # 에이전트 파일 읽기 실패는 경고로 처리
        for name in AGENT_FILES:                             # 처음 있는 것 하나
            fp = path_join(c, name)                         # 작업 폴더 안의 후보 경로
            if os.path.exists(fp):                           # existsSync(폴더여도 참)
                agent_content = read_file_utf8(fp)           # 폴더면 EISDIR → 아래에서 경고 후 내용 없음
                break                                       # 처음 찾은 것에서 멈춤
    except Exception as e:                                   # 읽기 실패
        warn.append(f"Warning: Could not read agent configuration file: {e}")  # 경고 글 저장
        _log.warning("Could not read agent configuration file: %s", e)  # 경고 기록

    processed: list[str] = []                                # --rule 처리 결과
    for spec in additional_rules or []:                     # --rule 값을 하나씩
        try:                                                # 실패는 경고 후 건너뜀
            processed.append(process_rule(spec, c))         # 처리 결과 추가
        except Exception as e:                               # 실패는 경고 후 건너뜀
            warn.append(f'Warning: Failed to process rule "{spec}": {e}')  # 경고 글 저장
    if config is not None:                                   # getConfigYamlRules — 설정의 rules 가 있으면
        processed.extend(config.rule_texts)                 # 뒤에 이어 붙임
    md_rules = load_markdown_rules_with_metadata(c, os.fspath(global_dir))   # 매번 다시 읽음
    existing = set(processed)                                # 이미 들어간 글
    for r in md_rules:                                      # 마크다운 규칙을 하나씩
        if r["rule"] not in existing:                       # 이미 들어간 글이면 뺀다(중복 제거)
            processed.append(r["rule"])                     # 규칙 추가
            existing.add(r["rule"])                         # 들어간 글로 기록

    snap = snapshot if snapshot is not None else get_snapshot(c)  # 스냅숏(주어진 것 또는 첫 계산값)
    msg = format_base_system_message(snap)                   # 기본 부분
    if mode == "plan":                                       # plan 모드
        msg += PLAN_MODE_CONTEXT                            # plan 모드 문구 추가
    elif not env.get("CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE"):   # 빈 글이면 끄지 않음(!process.env.X)
        msg += COMMIT_SIGNATURE_CONTEXT                     # 커밋 서명 문구 추가
    if headless:                                             # 헤드리스
        msg += HEADLESS_INSTRUCTIONS                        # 헤드리스 지시 추가
    if format_json:                                          # --format json — json 형식이면
        msg += JSON_INSTRUCTIONS                            # JSON 지시 추가
    if agent_content or processed:                           # 규칙 부분
        msg += '\n\n<context name="userRules">'             # userRules 컨텍스트 열기
        if agent_content:                                   # 에이전트 파일 내용이 있으면
            msg += f"\n{agent_content}"                     # 그 내용 추가
        if processed:                                       # 규칙이 있으면
            sep = "\n\n" if agent_content else "\n"         # 구분자: 에이전트 내용이 있으면 빈 줄, 없으면 줄바꿈
            msg += sep + "\n\n".join(processed)             # 규칙들을 빈 줄로 이어 추가
        msg += "\n</context>"                               # userRules 컨텍스트 닫기
    return msg                                              # 완성된 시스템 메시지 반환
