# ncn/tools/read_file.py — Read 도구 + "읽은 파일" 기록. cn extensions/cli/src/tools/readFile.ts
# 기록(read_files_set)은 모듈 전역 집합 — 프로세스가 끝날 때까지 유지(압축·/clear·재개에도 안 지움, P0 4장). Write 는 기록 안 함
# 한도를 넘으면 잘라 주지 않고 오류(cn 그대로). 상대경로는 프로세스 cwd 기준
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 환경변수
from typing import Optional                                  # 타입

from ncn.security.ignore import throw_if_file_is_security_concern   # 민감 파일 차단
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import REASON_FILE_TOO_LARGE, REASON_UNKNOWN, REASON_UNSPECIFIED   # 오류 이유
from ncn.tools.formatters import exists_sync, format_tool_argument, read_file_utf8, realpath_sync, to_locale_string   # Node fs 흉내
from ncn.tools.truncate import js_len, parse_env_number      # 환경변수 숫자·JS 글 길이

DEFAULT_READ_FILE_MAX_CHARS = 100000                         # ~25k tokens
DEFAULT_READ_FILE_MAX_LINES = 5000                           # 최대 줄


def get_read_file_max_chars() -> int:
    """CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS(기본 100000) — 호출할 때마다 읽음(cn 그대로)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS"), DEFAULT_READ_FILE_MAX_CHARS)


def get_read_file_max_lines() -> int:
    """CONTINUE_CLI_READ_FILE_MAX_OUTPUT_LINES(기본 5000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_LINES"), DEFAULT_READ_FILE_MAX_LINES)


# Track files that have been read in the current session
read_files_set: set[str] = set()                             # 읽은 파일의 실경로들(Edit·MultiEdit 의 전제 조건)


def mark_file_as_read(file_path: str) -> None:
    """markFileAsRead — 실경로를 기록."""
    read_files_set.add(file_path)


def _strip_dot_slash(filepath: str) -> str:
    """앞의 './' 만 한 번 뗌(cn은 '.\\' 는 안 뗌)."""
    return filepath[2:] if filepath.startswith("./") else filepath


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — './' 뗀 원문 경로로 민감 파일 검사(실경로 전), 미리보기."""
    filepath = args["filepath"]                              # let { filepath } = args
    if not isinstance(filepath, str):                        # JS: filepath.startsWith 가 함수 아님
        raise ContinueError(REASON_UNKNOWN, "filepath.startsWith is not a function")
    filepath = _strip_dot_slash(filepath)
    throw_if_file_is_security_concern(filepath)              # 민감 파일이면 오류
    return PreprocessResult(args=args, preview=[ToolCallPreview("text", f"Will read {format_tool_argument(filepath)}")])


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:
    """run — 있으면 실경로로 읽고, 한도(병렬 호출 수로 나눔) 넘으면 오류, 성공하면 기록 후 'Content of …'."""
    try:
        filepath = _strip_dot_slash(args["filepath"])        # './' 제거
        if not exists_sync(filepath):                        # 없음
            raise ContinueError(REASON_UNSPECIFIED, f"File does not exist: {filepath}")
        real_path = realpath_sync(filepath)                  # 실경로
        content = read_file_utf8(real_path)                  # utf-8 로 읽기

        parallel_count = context.parallel_tool_call_count if context is not None and context.parallel_tool_call_count is not None else 1   # context?.parallelToolCallCount ?? 1
        base_max_lines = get_read_file_max_lines()           # 한 도구 기준 한도
        base_max_chars = get_read_file_max_chars()
        max_lines = base_max_lines // parallel_count         # Math.floor(나눗셈)
        max_chars = base_max_chars // parallel_count
        line_count = len(content.split("\n"))                # 줄 수
        char_count = js_len(content)                         # 글자 수(JS length — UTF-16 단위)

        if char_count > max_chars or line_count > max_lines:   # 한도 초과
            parallel_note = (                                # 병렬 호출로 한도가 줄었다는 안내
                f" (Note: limit reduced due to {parallel_count} parallel tool calls. Single-tool limit: "
                f"{to_locale_string(base_max_chars)} characters or {to_locale_string(base_max_lines)} lines.)"
                if parallel_count > 1 else ""
            )
            raise ContinueError(
                REASON_FILE_TOO_LARGE,
                f"File is too large to read: {filepath} ({to_locale_string(char_count)} characters, {to_locale_string(line_count)} lines). "
                f"Maximum allowed: {to_locale_string(max_chars)} characters or {to_locale_string(max_lines)} lines.{parallel_note} "
                f"Consider using terminal commands like 'head', 'tail', 'sed', or 'grep' to read targeted parts of the file.",
            )

        mark_file_as_read(real_path)                         # Mark this file as read for the edit tool
        return f"Content of {filepath}:\n{content}"
    except ContinueError:                                    # 알려진 오류는 그대로
        raise
    except Exception as e:                                   # 그 밖(cn: 일반 Error) → "Error reading file: …"
        raise ContinueError(REASON_UNKNOWN, f"Error reading file: {e}") from e


READ_TOOL = Tool(
    name="Read",
    display_name="Read",
    description="Read the contents of a file at the specified path",
    parameters={
        "type": "object",
        "required": ["filepath"],
        "properties": {
            "filepath": {
                "type": "string",
                "description": "The path to the file to read",
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
    readonly=True,
    is_built_in=True,
)
