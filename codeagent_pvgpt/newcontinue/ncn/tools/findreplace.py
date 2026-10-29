# ncn/tools/findreplace.py — 찾아 바꾸기(찾기 4단계 + 들여쓰기 보정 + 인자 검증). cn core/edit/searchAndReplace/*
# 원본: findSearchMatch.ts(찾기) · performReplace.ts(바꾸기·들여쓰기) · findAndReplaceUtils.ts(validateSingleEdit) · multiEditValidation.ts
# 오류 이유 코드는 cn core/util/errors.ts ContinueErrorReason 값 그대로(이 파일이 ncn의 모음 — 도구 모듈들이 가져다 씀)
# 위치(인덱스)는 Python 글자 단위. cn은 UTF-16 단위지만 같은 글 안에서만 쓰므로 결과 글은 같다
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # 공백 패턴
from dataclasses import dataclass                            # 일치 결과
from typing import Any, Optional                             # 타입

from ncn.tools.base import ContinueError                     # cn ContinueError
from ncn.tools.formatters import JS_WS_CLASS, js_is_ws, js_trim   # JS 공백 규칙(\s·trim)

# ───────────────────────── ContinueErrorReason(core/util/errors.ts) ─────────────────────────
REASON_FIND_AND_REPLACE_IDENTICAL_OLD_AND_NEW_STRINGS = "find_and_replace_identical_old_and_new_strings"   # old == new
REASON_FIND_AND_REPLACE_MISSING_OLD_STRING = "find_and_replace_missing_old_string"   # old_string 없음
REASON_FIND_AND_REPLACE_NON_FIRST_EMPTY_OLD_STRING = "find_and_replace_non_first_empty_old_string"   # 두 번째 이후 빈 old
REASON_FIND_AND_REPLACE_MISSING_NEW_STRING = "find_and_replace_missing_new_string"   # new_string 없음
REASON_FIND_AND_REPLACE_INVALID_REPLACE_ALL = "find_and_replace_invalid_replace_all"   # replace_all 이 불리언 아님
REASON_FIND_AND_REPLACE_OLD_STRING_NOT_FOUND = "find_and_replace_old_string_not_found"   # 못 찾음
REASON_FIND_AND_REPLACE_MULTIPLE_OCCURRENCES = "find_and_replace_multiple_occurrences"   # 여러 곳
REASON_FIND_AND_REPLACE_MISSING_FILEPATH = "find_and_replace_missing_filepath"   # 경로 없음
REASON_MULTI_EDIT_EDITS_ARRAY_REQUIRED = "multi_edit_edits_array_required"   # edits 배열 없음
REASON_MULTI_EDIT_EDITS_ARRAY_EMPTY = "multi_edit_edits_array_empty"   # edits 빈 배열
REASON_EDIT_TOOL_FILE_NOT_READ = "edit_tool_file_not_yet_read"   # Read 안 한 파일
REASON_FILE_NOT_FOUND = "file_not_found"                     # 파일 없음
REASON_FILE_WRITE_ERROR = "file_write_error"                 # 쓰기 실패
REASON_FILE_IS_SECURITY_CONCERN = "file_is_security_concern"   # 민감 파일
REASON_FILE_TOO_LARGE = "file_too_large"                     # 너무 큼
REASON_UNSPECIFIED = "unspecified"                           # 알려진 오류지만 코드 없음
REASON_UNKNOWN = "unknown"                                   # cn에서 일반 Error(ContinueError 아님)였던 것

FOUND_MULTIPLE_FIND_STRINGS_ERROR = (                        # findAndReplaceUtils.ts 상수(원문)
    "Either provide a more specific string with surrounding context to make it unique, or use replace_all=true to replace all occurrences."
)

_WS_RE = re.compile(JS_WS_CLASS)                             # JS /\s/g


# ───────────────────────── findSearchMatch.ts ─────────────────────────
@dataclass
class SearchMatchResult:
    """일치 하나: 시작·끝(끝은 포함 안 함 — slice와 같음)과 찾은 방법 이름."""
    start_index: int                                         # 시작 위치
    end_index: int                                           # 끝 위치(제외)
    strategy_name: str                                       # exactMatch / trimmedMatch / caseInsensitiveMatch / whitespaceIgnoredMatch / emptySearch


def _exact_match(file_content: str, search_content: str) -> Optional[tuple[int, int]]:
    """1단계 exactMatch — 글자 그대로."""
    idx = file_content.find(search_content)                  # indexOf
    if idx != -1:
        return idx, idx + len(search_content)
    return None


def _trimmed_match(file_content: str, search_content: str) -> Optional[tuple[int, int]]:
    """2단계 trimmedMatch — 찾을 글의 앞뒤 공백을 떼고(JS trim) 그대로 찾기."""
    trimmed = js_trim(search_content)                        # 앞뒤 공백 제거
    idx = file_content.find(trimmed)
    if idx != -1:
        return idx, idx + len(trimmed)
    return None


def _case_insensitive_match(file_content: str, search_content: str) -> Optional[tuple[int, int]]:
    """3단계 caseInsensitiveMatch — 둘 다 소문자로 찾기. 끝 = 시작 + 원래 찾을 글 길이(cn 그대로)."""
    idx = file_content.lower().find(search_content.lower())  # toLowerCase 후 indexOf
    if idx != -1:
        return idx, idx + len(search_content)                # 소문자화로 길이가 바뀌는 글자(İ 등)는 cn과 같이 어긋날 수 있음
    return None


def _whitespace_ignored_match(file_content: str, search_content: str) -> Optional[tuple[int, int]]:
    """4단계 whitespaceIgnoredMatch — 공백을 전부 지우고 찾은 뒤 원래 위치로 되돌림."""
    stripped_file = _WS_RE.sub("", file_content)             # 파일에서 공백 제거
    stripped_search = _WS_RE.sub("", search_content)         # 찾을 글에서 공백 제거
    if stripped_search == "":                                # 공백뿐
        return None
    stripped_index = stripped_file.find(stripped_search)     # 공백 없는 글에서 위치
    if stripped_index == -1:
        return None
    original_start = -1                                      # 원래 글에서 시작 위치
    count = 0                                                # 지나온 공백 아닌 글자 수
    for i, ch in enumerate(file_content):                    # 공백 아닌 글자를 세어 시작 찾기
        if not js_is_ws(ch):
            if count == stripped_index:
                original_start = i
                break
            count += 1
    if original_start == -1:                                 # 일어나지 않아야 함
        return None
    original_end = original_start                            # 끝 위치
    matched = 0                                              # 맞춘 공백 아닌 글자 수
    for i in range(original_start, len(file_content)):       # 찾을 글의 공백 아닌 글자 수만큼 진행
        if not js_is_ws(file_content[i]):
            matched += 1
            if matched == len(stripped_search):              # 다 맞춤
                original_end = i + 1
                break
        original_end = i + 1                                 # 공백이어도 끝 위치는 늘림
    return original_start, original_end


_MATCHING_STRATEGIES = [                                     # 찾는 순서(cn matchingStrategies — jaroWinkler는 cn에서 주석 처리됨)
    (_exact_match, "exactMatch"),
    (_trimmed_match, "trimmedMatch"),
    (_case_insensitive_match, "caseInsensitiveMatch"),
    (_whitespace_ignored_match, "whitespaceIgnoredMatch"),
]


def find_search_match(file_content: str, search_content: str) -> Optional[SearchMatchResult]:
    """findSearchMatch — 첫 일치. 찾을 글이 공백뿐이면 파일 맨 앞(emptySearch)."""
    if js_trim(search_content) == "":                        # 빈 찾기 → 0 위치
        return SearchMatchResult(0, 0, "emptySearch")
    for strategy, name in _MATCHING_STRATEGIES:              # 순서대로 시도
        r = strategy(file_content, search_content)
        if r is not None:                                    # 처음 성공한 방법
            return SearchMatchResult(r[0], r[1], name)
    return None


def find_search_matches(file_content: str, search_content: str) -> list[SearchMatchResult]:
    """findSearchMatches — 모든 일치. 앞 일치의 끝 뒤에서 다시 찾기를 반복(방법은 매번 1단계부터)."""
    matches: list[SearchMatchResult] = []                    # 결과
    if js_trim(search_content) == "":                        # 빈 찾기는 항상 0 위치 하나
        return [SearchMatchResult(0, 0, "emptySearch")]
    remaining = file_content                                 # 남은 글
    offset = 0                                               # 남은 글의 시작 위치
    while len(remaining) > 0:                                # 남은 글이 있는 동안
        m = find_search_match(remaining, search_content)     # 남은 글에서 찾기
        if m is None:
            break
        adjusted = SearchMatchResult(m.start_index + offset, m.end_index + offset, m.strategy_name)   # 전체 위치로
        if matches and adjusted.start_index <= matches[-1].start_index:   # 앞으로 못 나가면 멈춤(무한 반복 방지)
            break
        matches.append(adjusted)
        offset = adjusted.end_index                          # 일치 끝 뒤부터
        remaining = file_content[offset:]
    return matches


# ───────────────────────── performReplace.ts ─────────────────────────
def _leading_ws(line: str) -> str:
    r"""line.match(/^(\s*)/)[1] — 줄 앞의 JS 공백."""
    i = 0
    while i < len(line) and js_is_ws(line[i]):               # 공백인 동안
        i += 1
    return line[:i]


def _get_leading_indent(text: str) -> str:
    """getLeadingIndent — 첫 번째 비어 있지 않은 줄의 앞 공백."""
    for line in text.split("\n"):                            # 줄마다
        if len(js_trim(line)) > 0:                           # 내용 있는 줄
            return _leading_ws(line)
    return ""


def _get_line_indent_at_position(file_content: str, position: int) -> str:
    """getLineIndentAtPosition — position 이 있는 줄의 들여쓰기."""
    line_start = _js_last_index_of(file_content, "\n", position - 1) + 1   # 줄 시작
    line_end = file_content.find("\n", line_start)           # 줄 끝
    line = file_content[line_start:len(file_content) if line_end == -1 else line_end]
    return _leading_ws(line)


def _js_last_index_of(s: str, sub: str, from_index: int) -> int:
    """JS lastIndexOf(sub, fromIndex) — fromIndex 가 음수면 0으로(그 위치에서 시작하는 일치까지 봄)."""
    from_index = max(0, min(from_index, len(s)))             # JS 규칙: 0~length 로 자름
    return s.rfind(sub, 0, from_index + len(sub))            # from_index 에서 시작하는 일치까지 포함


def _adjust_replacement_indentation(file_content: str, match: SearchMatchResult, old_string: str, new_string: str) -> str:
    """adjustReplacementIndentation — 느슨한 방법으로 찾았을 때 new_string 의 들여쓰기를 파일 쪽에 맞춤."""
    if match.strategy_name in ("exactMatch", "emptySearch"):   # 정확히 찾았으면 그대로
        return new_string
    matched_indent = _get_line_indent_at_position(file_content, match.start_index)   # 파일 쪽 들여쓰기
    old_indent = _get_leading_indent(old_string)             # 모델이 준 들여쓰기
    if matched_indent == old_indent:                         # 같으면 그대로
        return new_string
    out = []                                                 # 고친 줄들
    for index, line in enumerate(new_string.split("\n")):
        if len(js_trim(line)) == 0:                          # 빈 줄은 그대로
            out.append(line)
            continue
        if index == 0:                                       # 첫 줄: 파일의 시작 위치 앞에 이미 들여쓰기가 있으므로 old 들여쓰기만 뗌
            if old_indent and line.startswith(old_indent):
                out.append(line[len(old_indent):])
            else:
                out.append(line)
            continue
        if line.startswith(old_indent):                      # 다음 줄들: old 들여쓰기 → 파일 들여쓰기
            out.append(matched_indent + line[len(old_indent):])
        else:
            out.append(line)
    return "\n".join(out)


def execute_find_and_replace(file_content: str, old_string: str, new_string: str, replace_all: bool, edit_index: int = 0) -> str:
    """executeFindAndReplace — 찾아 바꾼 새 내용. 못 찾음·여러 곳이면 cn과 같은 글로 ContinueError."""
    matches = find_search_matches(file_content, old_string)  # 모든 일치
    if len(matches) == 0:                                    # 못 찾음
        raise ContinueError(REASON_FIND_AND_REPLACE_OLD_STRING_NOT_FOUND,
                            f'Edit at index {edit_index}: string not found in file: "{old_string}"')
    if replace_all:                                          # 전부 바꾸기 — 뒤에서부터(앞 위치가 안 바뀌게)
        result = file_content
        for match in reversed(matches):
            adjusted = _adjust_replacement_indentation(result, match, old_string, new_string)
            result = result[:match.start_index] + adjusted + result[match.end_index:]
        return result
    if len(matches) > 1:                                     # 한 곳만 바꾸는데 여러 곳
        raise ContinueError(REASON_FIND_AND_REPLACE_MULTIPLE_OCCURRENCES,
                            f'Edit at index {edit_index}: String "{old_string}" appears {len(matches)} times in the file. '
                            f"{FOUND_MULTIPLE_FIND_STRINGS_ERROR}")
    match = matches[0]                                       # 하나
    adjusted = _adjust_replacement_indentation(file_content, match, old_string, new_string)
    return file_content[:match.start_index] + adjusted + file_content[match.end_index:]


def execute_multi_find_and_replace(file_content: str, edits: list[dict]) -> str:
    """executeMultiFindAndReplace — 차례로 적용(각 편집은 앞 결과에). 하나라도 실패하면 오류(파일은 안 씀)."""
    result = file_content
    for edit_index, edit in enumerate(edits):                # 순서대로
        result = execute_find_and_replace(result, edit.get("old_string"), edit.get("new_string"),
                                          _nullish(edit.get("replace_all"), False), edit_index)   # replace_all ?? false
    return result


def _nullish(v: Any, default: Any) -> Any:
    """JS v ?? default — None 이면 기본값."""
    return default if v is None else v


# ───────────────────────── findAndReplaceUtils.ts / multiEditValidation.ts ─────────────────────────
MISSING = object()                                           # JS undefined 표시(인자 자체가 없음)


def validate_single_edit(old_string: Any, new_string: Any, replace_all: Any = MISSING, index: Optional[int] = None) -> tuple[str, str, Any]:
    """validateSingleEdit — (old, new, replace_all) 검증. replace_all 이 없거나 None 이면 그대로 None 반환."""
    context = f"edit at index {index}: " if index is not None else ""   # 오류 글 앞머리
    if not isinstance(old_string, str):                      # undefined 또는 글 아님
        raise ContinueError(REASON_FIND_AND_REPLACE_MISSING_OLD_STRING, f"{context}string old_string is required")
    if not isinstance(new_string, str):
        raise ContinueError(REASON_FIND_AND_REPLACE_MISSING_NEW_STRING, f"{context}string new_string is required")
    if old_string == new_string:                             # 같으면 거절
        raise ContinueError(REASON_FIND_AND_REPLACE_IDENTICAL_OLD_AND_NEW_STRINGS, f"{context}old_string and new_string must be different")
    if replace_all is MISSING:                               # 인자 없음(undefined)
        replace_all = None
    elif not isinstance(replace_all, bool):                  # JS: undefined 가 아니면 boolean 이어야 함(null 도 거절)
        raise ContinueError(REASON_FIND_AND_REPLACE_INVALID_REPLACE_ALL, f"{context}replace_all must be a valid boolean")
    return old_string, new_string, replace_all


def validate_multi_edit(args: Any) -> list[dict]:
    """validateMultiEdit — edits 배열과 각 편집 검증. 두 번째부터는 빈 old_string 금지."""
    if not isinstance(args, dict) or "edits" not in args:    # 인자 모양이 틀림
        raise ContinueError(REASON_MULTI_EDIT_EDITS_ARRAY_REQUIRED, "invalid multi-edit args")
    edits = args["edits"]
    if not isinstance(edits, list):                          # 배열 아님
        raise ContinueError(REASON_MULTI_EDIT_EDITS_ARRAY_REQUIRED, "edits array is required")
    if len(edits) == 0:                                      # 빈 배열
        raise ContinueError(REASON_MULTI_EDIT_EDITS_ARRAY_EMPTY, "edits array must contain at least one edit")
    for i, edit in enumerate(edits):                         # 하나씩
        if not isinstance(edit, dict):                       # cn: edit.old_string 접근에서 TypeError(null) 또는 undefined
            if edit is None:                                 # null.old_string → TypeError
                raise ContinueError(REASON_UNKNOWN, "Cannot read properties of null (reading 'old_string')")
            edit = {}                                        # 숫자·글 등은 속성이 전부 undefined
        validate_single_edit(edit.get("old_string"), edit.get("new_string"),
                             edit["replace_all"] if "replace_all" in edit else MISSING, i)   # 같은 검증 + 위치
        if i > 0 and edit.get("old_string") == "":           # 첫 편집만 빈 old 허용(맨 앞 삽입)
            raise ContinueError(REASON_FIND_AND_REPLACE_NON_FIRST_EMPTY_OLD_STRING,
                                f"Edit at index {i}: old_string cannot be empty. Only the first edit can have an empty old_string for insertion at the beginning of the file.")
    return edits


def trim_empty_lines(lines: list[str], from_end: bool) -> list[str]:
    """trimEmptyLines — 앞(또는 뒤)의 빈 줄들 제거(cn findAndReplaceUtils.ts — 지금 cn 도구에선 안 씀, 함께 옮김)."""
    work = list(reversed(lines)) if from_end else list(lines)   # 뒤에서면 뒤집기
    out: list[str] = []
    removing = True                                          # 아직 지우는 중
    for line in work:
        if removing and js_trim(line) == "":
            continue
        removing = False
        out.append(line)
    return list(reversed(out)) if from_end else out
