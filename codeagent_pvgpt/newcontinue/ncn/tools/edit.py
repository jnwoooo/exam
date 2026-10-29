# ncn/tools/edit.py — Edit 도구(한 곳 또는 전부 찾아 바꾸기). cn extensions/cli/src/tools/edit.ts
# 전제: 이번 프로세스에서 Read 한 파일(실경로 기준)만. 민감 파일 판정은 실경로("부모 폴더/파일 이름")로
# preprocess 에서 새 내용까지 계산하고, run 은 그 결과를 쓰기만 한다(cn 그대로 — 승인 사이에 파일이 바뀌어도 다시 안 읽음)
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 현재 폴더
from typing import Any, Optional                             # 타입

from ncn.security.ignore import throw_if_file_is_security_concern   # 민감 파일 차단
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import (MISSING, REASON_EDIT_TOOL_FILE_NOT_READ, REASON_FILE_NOT_FOUND,   # 찾아 바꾸기·오류 이유
                                   REASON_FILE_WRITE_ERROR, REASON_FIND_AND_REPLACE_MISSING_FILEPATH, REASON_UNKNOWN,
                                   execute_find_and_replace, validate_single_edit)
from ncn.tools.formatters import (NodeFsError, exists_sync, path_is_absolute, path_resolve,   # Node fs·path 흉내
                                  read_file_utf8, realpath_sync, write_file_utf8)
from ncn.tools.read_file import READ_TOOL, read_files_set    # 읽은 파일 기록
from ncn.tools.write_file import generate_diff               # diff


def validate_and_resolve_file_path(args: Any) -> dict:
    """validateAndResolveFilePath — 경로 필수 → 절대경로 → 실경로 → 민감 파일 → 존재 → Read 했나. {originalPath, resolvedPath}."""
    file_path = args.get("file_path") if isinstance(args, dict) else None   # const { file_path } = args
    if not file_path:                                        # 없음·빈 글
        raise ContinueError(REASON_FIND_AND_REPLACE_MISSING_FILEPATH, "file_path is required")
    if not isinstance(file_path, str):                       # JS: path.isAbsolute 가 TypeError
        raise ContinueError(REASON_UNKNOWN, f'The "path" argument must be of type string. Received {_js_received(file_path)}')
    absolute_path = file_path if path_is_absolute(file_path) else path_resolve(os.getcwd(), file_path)   # 절대경로로
    try:
        resolved_path = realpath_sync(absolute_path)         # 실경로 — 없는 파일이면 여기서 Node ENOENT(lstat) 글로 오류
    except NodeFsError as e:                                 # cn: 일반 Error 가 그대로 올라감
        raise ContinueError(REASON_UNKNOWN, str(e)) from e
    throw_if_file_is_security_concern(resolved_path)         # 민감 파일
    if not exists_sync(resolved_path):                       # Check if file exists(위 realpath 때문에 실제로는 거의 안 옴)
        raise ContinueError(REASON_FILE_NOT_FOUND, f"File {file_path} does not exist")
    if resolved_path not in read_files_set:                  # Check if file has been read
        raise ContinueError(REASON_EDIT_TOOL_FILE_NOT_READ,
                            f"You must use the {READ_TOOL.name} tool to read {file_path} before editing it.")
    return {"originalPath": file_path, "resolvedPath": resolved_path}


def _js_received(v: Any) -> str:
    """Node ERR_INVALID_ARG_TYPE 의 'Received …' 부분(대표 경우만)."""
    if isinstance(v, bool):
        return f"type boolean ({'true' if v else 'false'})"
    if isinstance(v, (int, float)):
        return f"type number ({v})"
    if isinstance(v, list):
        return "an instance of Array"
    if isinstance(v, dict):
        return "an instance of Object"
    return f"type {type(v).__name__}"


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 검증 후 새 내용 계산 + diff 미리보기. run 인자는 {resolvedPath, newContent, oldContent}."""
    old_string = args.get("old_string")                      # 구조 분해(없으면 undefined)
    new_string = args.get("new_string")
    replace_all = args["replace_all"] if "replace_all" in args else MISSING   # 없음과 null 구분
    resolved_path = validate_and_resolve_file_path(args)["resolvedPath"]   # 경로 검증
    old_s, new_s, rep_all = validate_single_edit(old_string, new_string, replace_all)   # 인자 검증
    old_content = read_file_utf8(resolved_path)              # 지금 내용
    new_content = execute_find_and_replace(old_content, old_s, new_s, rep_all if rep_all is not None else False, 0)   # replaceAll ?? false
    diff = generate_diff(old_content, new_content, resolved_path)   # 미리보기 diff
    return PreprocessResult(
        args={"resolvedPath": resolved_path, "newContent": new_content, "oldContent": old_content},
        preview=[ToolCallPreview("text", "Will make the following changes:"), ToolCallPreview("diff", diff)],
    )


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:
    """run — 새 내용을 쓰고 diff 를 돌려줌."""
    try:
        write_file_utf8(args["resolvedPath"], args["newContent"])   # 쓰기
        diff = generate_diff(args["oldContent"], args["newContent"], args["resolvedPath"])   # 결과용 diff
        return f"Successfully edited {args['resolvedPath']}\nDiff:\n{diff}"
    except ContinueError:
        raise
    except Exception as e:                                   # 쓰기 실패
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error: failed to edit {args.get('resolvedPath')}: {e}") from e


EDIT_TOOL = Tool(
    name="Edit",
    display_name="Edit",
    readonly=False,
    is_built_in=True,
    # "USAGE: " 끝 공백은 cn 원문에 있음 — 편집기가 지우지 않게 \x20 으로 적음
    description=f"""Performs exact string replacements in a file.

USAGE:\x20
- ALWAYS use the `{READ_TOOL.name}` tool just before making edits, to understand the file's up-to-date contents and context.
- When editing text from {READ_TOOL.name} tool output, ensure you preserve exact whitespace/indentation.
- Always prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable, for instance.

WARNINGS:
- When not using `replace_all`, the edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- The edit will FAIL if you have not recently used the `{READ_TOOL.name}` tool to view up-to-date file contents.""",
    parameters={
        "type": "object",
        "required": ["file_path", "old_string", "new_string"],
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file to modify. Absolute preferred",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace - must be exact including whitespace/indentation",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (MUST be different from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false)",
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
)
