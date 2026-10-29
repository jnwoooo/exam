# ncn/tools/read_file.py — Read 도구 + "읽은 파일" 기록. cn extensions/cli/src/tools/readFile.ts
# 기록(read_files_set)은 모듈 전역 집합 — 프로세스가 끝날 때까지 유지(압축·/clear·재개에도 안 지움, P0 4장). Write 는 기록 안 함
# 한도를 넘으면 잘라 주지 않고 오류(cn 그대로). 상대경로는 프로세스 cwd 기준
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 환경변수
from typing import Optional                                  # 타입

from ncn.compat import cn_compat                             # [ncn 부록B S4] cn 원래 동작 스위치
from ncn.security.ignore import is_security_concern, security_concern_error, throw_if_file_is_security_concern   # 민감 파일 차단
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import REASON_FILE_TOO_LARGE, REASON_UNKNOWN, REASON_UNSPECIFIED   # 오류 이유
from ncn.tools.formatters import exists_sync, format_tool_argument, read_file_utf8, realpath_sync, to_locale_string   # Node fs 흉내
from ncn.tools.truncate import js_len, parse_env_number      # 환경변수 숫자·JS 글 길이

DEFAULT_READ_FILE_MAX_CHARS = 100000                         # ~25k tokens
DEFAULT_READ_FILE_MAX_LINES = 5000                           # 최대 줄


def get_read_file_max_chars() -> int:                       # 환경변수로 정한 글자 한도를 구한다
    """CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS(기본 100000) — 호출할 때마다 읽음(cn 그대로)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS"), DEFAULT_READ_FILE_MAX_CHARS)  # 매번 환경변수를 다시 읽어 숫자로(없으면 기본값)


def get_read_file_max_lines() -> int:                       # 환경변수로 정한 줄 한도를 구한다
    """CONTINUE_CLI_READ_FILE_MAX_OUTPUT_LINES(기본 5000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_LINES"), DEFAULT_READ_FILE_MAX_LINES)  # 매번 환경변수를 다시 읽어 숫자로(없으면 기본값)


# Track files that have been read in the current session
read_files_set: set[str] = set()                             # 읽은 파일의 실경로들(Edit·MultiEdit 의 전제 조건)


def mark_file_as_read(file_path: str) -> None:              # 읽은 파일을 기록하는 함수
    """markFileAsRead — 실경로를 기록."""
    read_files_set.add(file_path)                           # 집합에 실경로 추가


def _strip_dot_slash(filepath: str) -> str:                 # 경로 앞의 './' 제거 도우미
    """앞의 './' 만 한 번 뗌(cn은 '.\\' 는 안 뗌)."""
    return filepath[2:] if filepath.startswith("./") else filepath  # './' 로 시작할 때만 두 글자 뗌


async def _preprocess(args: dict) -> PreprocessResult:      # Read 의 preprocess(민감 파일 검사 + 미리보기)
    """preprocess — './' 뗀 원문 경로로 민감 파일 검사(실경로 전), 미리보기. [ncn 부록B S4] 실경로로도 검사."""
    filepath = args["filepath"]                              # let { filepath } = args
    if not isinstance(filepath, str):                        # JS: filepath.startsWith 가 함수 아님
        raise ContinueError(REASON_UNKNOWN, "filepath.startsWith is not a function")  # 문자열이 아니면 JS 와 같은 오류
    filepath = _strip_dot_slash(filepath)                   # './' 제거
    throw_if_file_is_security_concern(filepath)              # 민감 파일이면 오류
    if not cn_compat() and exists_sync(filepath):            # [ncn 부록B S4] 고친 동작: 링크를 따라간 실제 경로로도 판정
        # cn 원래 동작: 링크 따라가기 전 경로로만 판정 → 'notes.txt -> .env' 같은 심볼릭 링크로 우회. NCN_CN_COMPAT=1 이면 cn 동작
        try:                                                # 실경로 구하기 실패(권한 등)는 run 에서 cn 글로 처리되게 둠
            real_path = realpath_sync(filepath)              # 링크를 푼 실제 경로(run 이 읽는 경로와 같음)
        except Exception:                                    # 실경로를 못 구하면
            real_path = None                                # 실경로 판정 생략
        if real_path is not None and is_security_concern(real_path):   # 실제 경로가 민감 파일이면(둘 중 하나라도 걸리면 거부)
            raise security_concern_error(filepath)          # cn과 같은 글(모델이 준 경로로 표시)
    return PreprocessResult(args=args, preview=[ToolCallPreview("text", f"Will read {format_tool_argument(filepath)}")])  # "Will read ..." 미리보기와 함께 반환


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # Read 의 run(실제로 읽는다)
    """run — 있으면 실경로로 읽고, 한도(병렬 호출 수로 나눔) 넘으면 오류, 성공하면 기록 후 'Content of …'."""
    try:                                                    # 전체를 감싸 오류 형식을 맞춘다
        filepath = _strip_dot_slash(args["filepath"])        # './' 제거
        if not exists_sync(filepath):                        # 없음
            raise ContinueError(REASON_UNSPECIFIED, f"File does not exist: {filepath}")  # 파일이 없으면 오류
        real_path = realpath_sync(filepath)                  # 실경로
        content = read_file_utf8(real_path)                  # utf-8 로 읽기

        parallel_count = context.parallel_tool_call_count if context is not None and context.parallel_tool_call_count is not None else 1  # 동시에 실행 중인 도구 수(없으면 1) — 출력 한도를 나누는 데 쓴다
        base_max_lines = get_read_file_max_lines()           # 한 도구 기준 한도
        base_max_chars = get_read_file_max_chars()          # 글자 한도의 기준값
        max_lines = base_max_lines // parallel_count         # Math.floor(나눗셈)
        max_chars = base_max_chars // parallel_count
        line_count = len(content.split("\n"))                # 줄 수
        char_count = js_len(content)                         # 글자 수(JS length — UTF-16 단위)

        if char_count > max_chars or line_count > max_lines:   # 한도 초과
            parallel_note = (                                # 병렬 호출로 한도가 줄었다는 안내
                f" (Note: limit reduced due to {parallel_count} parallel tool calls. Single-tool limit: "  # 병렬 호출 안내 앞부분
                f"{to_locale_string(base_max_chars)} characters or {to_locale_string(base_max_lines)} lines.)"  # 병렬 호출 안내 뒷부분(기준 한도)
                if parallel_count > 1 else ""               # 병렬이 아니면 안내 없음
            )                                               # 여기까지 닫기
            raise ContinueError(                            # 한도 초과 오류를 만든다
                REASON_FILE_TOO_LARGE,                      # 이유 코드
                f"File is too large to read: {filepath} ({to_locale_string(char_count)} characters, {to_locale_string(line_count)} lines). "  # 실제 크기 안내
                f"Maximum allowed: {to_locale_string(max_chars)} characters or {to_locale_string(max_lines)} lines.{parallel_note} "  # 허용 한도 안내
                f"Consider using terminal commands like 'head', 'tail', 'sed', or 'grep' to read targeted parts of the file.",  # 대안 안내(cn 원문)
            )                                               # 여기까지 닫기

        mark_file_as_read(real_path)                        # 읽기에 성공한 파일로 기록(Edit 의 전제)
        return f"Content of {filepath}:\n{content}"         # 파일 내용을 모델에게 돌려줌
    except ContinueError:                                    # 알려진 오류는 그대로
        raise
    except Exception as e:                                   # 그 밖(cn: 일반 Error) → "Error reading file: …"
        raise ContinueError(REASON_UNKNOWN, f"Error reading file: {e}") from e


READ_TOOL = Tool(                                           # Read 도구 정의(cn readFile.ts)
    name="Read",                                            # LLM 에 보이는 이름
    display_name="Read",                                    # 화면 표시 이름
    description="Read the contents of a file at the specified path",  # 도구 설명(cn 원문)
    parameters={                                            # 인자 스키마
        "type": "object",                                   # 최상위는 객체
        "required": ["filepath"],                           # 필수 인자
        "properties": {                                     # 인자별 정의
            "filepath": {                                   # 읽을 파일 경로
                "type": "string",                           # 문자열 타입
                "description": "The path to the file to read",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
        },                                                  # 여기까지 닫기
    },                                                      # 여기까지 닫기
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # 실행 전 검사 함수 연결
    readonly=True,                                          # 읽기 전용 도구
    is_built_in=True,                                       # 내장 도구
)                                                           # 여기까지 닫기
