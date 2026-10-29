# ncn/tools/multi_edit.py — MultiEdit 도구(한 파일에 여러 찾아 바꾸기를 차례로, 전부 되거나 전부 안 되게). cn extensions/cli/src/tools/multiEdit.ts
# 경로 검증은 Edit 와 같은 validate_and_resolve_file_path(Read 선행·민감 파일). preprocess 에서 전부 계산하고 run 은 쓰기만
from __future__ import annotations                           # 타입 주석 지연 평가

from typing import Optional                                  # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.edit import EDIT_TOOL, validate_and_resolve_file_path   # Edit 와 같은 경로 검증
from ncn.tools.findreplace import REASON_FILE_WRITE_ERROR, execute_multi_find_and_replace, validate_multi_edit   # 찾아 바꾸기
from ncn.tools.formatters import read_file_utf8, write_file_utf8   # Node fs 흉내
from ncn.tools.read_file import READ_TOOL                    # 설명에 들어가는 이름
from ncn.tools.write_file import generate_diff               # diff


def _plural(n: int) -> str:
    """edit / edits."""
    return "" if n == 1 else "s"


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 경로·edits 검증 → 차례로 적용한 새 내용 + diff 미리보기."""
    resolved_path = validate_and_resolve_file_path(args)["resolvedPath"]   # 경로 검증(먼저 — cn 순서)
    edits = validate_multi_edit(args)                        # edits 검증
    current_content = read_file_utf8(resolved_path)          # 지금 내용
    new_content = execute_multi_find_and_replace(current_content, edits)   # 차례로 적용(실패하면 오류 — 아무것도 안 씀)
    diff = generate_diff(current_content, new_content, resolved_path)   # 미리보기 diff
    return PreprocessResult(
        args={"file_path": resolved_path, "newContent": new_content, "originalContent": current_content, "editCount": len(edits)},
        preview=[ToolCallPreview("text", f"Will apply {len(edits)} edit{_plural(len(edits))} to {resolved_path}:"),
                 ToolCallPreview("diff", diff)],
    )


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:
    """run — 새 내용을 쓰고 diff 를 돌려줌."""
    try:
        write_file_utf8(args["file_path"], args["newContent"])   # 쓰기
        diff = generate_diff(args["originalContent"], args["newContent"], args["file_path"])   # 결과용 diff
        n = args["editCount"]                                # 편집 수
        return f"Successfully edited {args['file_path']} with {n} edit{_plural(n)}\nDiff:\n{diff}"
    except ContinueError:
        raise
    except Exception as e:                                   # 쓰기 실패
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error: failed to edit {args.get('file_path')}: {e}") from e


MULTI_EDIT_TOOL = Tool(
    name="MultiEdit",
    display_name="MultiEdit",
    readonly=False,
    is_built_in=True,
    # 첫 줄 끝 공백은 cn 원문에 있음 — 편집기가 지우지 않게 \x20 으로 적음
    description=f"""Use this tool to make multiple edits to a single file in one operation. It allows you to perform multiple find-and-replace operations efficiently.\x20
Prefer this tool over the {EDIT_TOOL.name} tool when you need to make multiple edits to the same file.

To make multiple edits to a file, provide the following:
1. file_path: The absolute path to the file to modify. Relative paths can also be used (resolved against cwd) but absolute is preferred
2. edits: An array of edit operations to perform, where each edit contains:
   - old_string: The text to replace (must match the file contents exactly, including all whitespace/indentation)
   - new_string: The edited text to replace the old_string
   - replace_all: Replace all occurrences of old_string. This parameter is optional and defaults to false.

IMPORTANT:
- Files may be modified between tool calls by users, linters, etc, so always make all edits in one tool call where possible. For example, do not only edit imports if there are other changes in the file, as unused imports may be removed by a linter between tool calls.
- All edits are applied in sequence, in the order they are provided
- Each edit operates on the result of the previous edit, so plan your edits carefully to avoid conflicts between sequential operations
- Edits are atomic - all edits must be valid for the operation to succeed - if any edit fails, none will be applied
- This tool is ideal when you need to make several changes to different parts of the same file

CRITICAL REQUIREMENTS:
1. ALWAYS use the {READ_TOOL.name} tool just before making edits, to understand the file's up-to-date contents and context
2. When making edits:
- Ensure all edits result in idiomatic, correct code
- Do not leave the code in a broken state
- Always use absolute file paths (starting with /)
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- Use replace_all for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
- Empty old_string can be used to insert content at the beginning of a file

WARNINGS:
- If earlier edits affect the text that later edits are trying to find, files can become mangled
- The tool will fail if edits.old_string doesn't match the file contents exactly (including whitespace)
- The tool will fail if edits.old_string and edits.new_string are the same - they MUST be different
- The tool will fail if you have not used the {READ_TOOL.name} tool to read the file in this session
- The tool will fail if the file does not exist - it cannot create new files
- This tool cannot create new files - the file must already exist""",
    parameters={
        "type": "object",
        "required": ["file_path", "edits"],
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file to modify. Absolute preferred",
            },
            "edits": {
                "type": "array",
                "description": "Array of edit operations to perform sequentially on the file",
                "items": {
                    "type": "object",
                    "required": ["old_string", "new_string"],
                    "properties": {
                        "old_string": {
                            "type": "string",
                            "description": "The text to replace (exact match including whitespace/indentation)",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "The text to replace it with. MUST be different than old_string.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences of old_string (default false) in the file",
                        },
                    },
                },
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
)
