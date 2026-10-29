# ncn/tools/multi_edit.py — MultiEdit 도구(한 파일에 여러 찾아 바꾸기를 차례로, 전부 되거나 전부 안 되게). cn extensions/cli/src/tools/multiEdit.ts
# 경로 검증은 Edit 와 같은 validate_and_resolve_file_path(Read 선행·민감 파일). preprocess 에서 전부 계산하고 run 은 쓰기만(cn)
# [ncn 부록B B6] ncn은 run 에서 다시 읽어 바뀌었으면 edits 를 다시 차례로 적용(실패면 cn 형식 오류, 아무것도 안 씀). NCN_CN_COMPAT=1 이면 cn 동작
# [ncn E3] CRLF 파일은 LF 로 비교·치환 후 원래 줄바꿈으로(Edit 와 같음). [ncn E4] 쓰기 직전 원본 백업 — /undo
from __future__ import annotations                           # 타입 주석 지연 평가


from typing import Optional                                  # 타입

from ncn import backup, encoding, fsio                       # [ncn V3] 파일 창구 + [ncn E3] 줄바꿈 맞춤 + [ncn E4] 백업
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.compat import cn_compat                             # [ncn 부록B B6] cn 원래 동작 스위치
from ncn.tools.edit import EDIT_TOOL, REDO_KEY, validate_and_resolve_file_path   # Edit 와 같은 경로 검증·B6 재료 키
from ncn.tools.findreplace import REASON_FILE_WRITE_ERROR, execute_multi_find_and_replace, validate_multi_edit   # 찾아 바꾸기
from ncn.tools.formatters import read_file_utf8, write_file_utf8   # Node fs 흉내
from ncn.tools.read_file import READ_TOOL                    # 설명에 들어가는 이름
from ncn.tools.write_file import generate_diff              # diff 생성 함수


def _plural(n: int) -> str:                                 # 개수에 따른 복수형 어미
    """edit / edits."""
    return "" if n == 1 else "s"                            # 1개면 빈 글, 아니면 s


def multi_find_and_replace_e3(content: str, edits: list) -> str:   # [ncn E3] 줄바꿈을 무시한 여러 찾아 바꾸기
    """CRLF 가 있는 파일(E3 켜짐)이면 내용·각 old/new 를 LF 로 맞춰 차례로 적용하고 원래 다수 줄바꿈으로. 아니면 cn 그대로."""
    if not encoding.lf_matching(content):                    # LF 전용·E3 꺼짐
        return execute_multi_find_and_replace(content, edits)   # cn 그대로
    lf_edits = [{**e, "old_string": encoding.to_lf(e["old_string"]), "new_string": encoding.to_lf(e["new_string"])} for e in edits]   # LF 로 맞춘 사본(검증 끝난 edits)
    out = execute_multi_find_and_replace(encoding.to_lf(content), lf_edits)   # LF 로 차례로 적용
    return encoding.conform_newlines(content, out)           # 원래 줄바꿈으로


async def _preprocess(args: dict) -> PreprocessResult:      # MultiEdit 의 preprocess(전부 계산해 둔다)
    """preprocess — 경로·edits 검증 → 차례로 적용한 새 내용 + diff 미리보기."""
    resolved_path = validate_and_resolve_file_path(args)["resolvedPath"]   # 경로 검증(먼저 — cn 순서)
    edits = validate_multi_edit(args)                        # edits 검증
    current_content = await fsio.read_text_file(resolved_path)          # 지금 내용
    new_content = multi_find_and_replace_e3(current_content, edits)   # 차례로 적용(실패하면 오류 — 아무것도 안 씀) — [ncn E3] CRLF 파일은 LF 로 비교
    diff = generate_diff(current_content, new_content, resolved_path)   # 미리보기 diff
    return PreprocessResult(                                # run 인자와 미리보기를 묶어 반환
        args={"file_path": resolved_path, "newContent": new_content, "originalContent": current_content, "editCount": len(edits),  # run 에 넘길 인자(경로·새 내용·옛 내용·편집 수)
              **({} if cn_compat() else {REDO_KEY: edits})},  # [ncn 부록B B6] 고친 동작에서만 run 에서 다시 적용할 edits 를 붙임(LLM 요청에는 안 들어감)
        preview=[ToolCallPreview("text", f"Will apply {len(edits)} edit{_plural(len(edits))} to {resolved_path}:"),  # 미리보기: 안내 글
                 ToolCallPreview("diff", diff)],            # 미리보기: diff
    )                                                       # PreprocessResult 닫기


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # MultiEdit 의 run(쓰기만 한다)
    """run — 새 내용을 쓰고 diff 를 돌려줌. [ncn 부록B B6] 쓰기 전에 다시 읽어 바뀌었으면 다시 적용."""
    try:                                                    # 쓰기 시도
        original, new_content = args["originalContent"], args["newContent"]   # preprocess 때 계산한 내용
        edits = args.get(REDO_KEY)                           # [ncn 부록B B6] 다시 적용할 edits(없으면 cn처럼 그대로 씀)
        if edits is not None and not cn_compat():            # [ncn 부록B B6] 고친 동작(cn 원래 동작: 미리 계산한 내용을 그대로 씀)
            current = await fsio.read_text_file(args["file_path"])      # 승인 뒤 실행 시점의 내용
            if current != original:                          # 승인 대기 중 바뀌었으면
                new_content = multi_find_and_replace_e3(current, edits)   # 지금 내용에 차례로 다시 적용(하나라도 실패하면 cn 글로 오류)
                original = current                          # diff 기준도 지금 내용
        snap = await backup.capture(args["file_path"], "MultiEdit")   # [ncn E4] 쓰기 직전 원본(꺼져 있으면 None)
        await fsio.write_text_file(args["file_path"], new_content)      # 쓰기([ncn E3] 디스크면 원래 인코딩·BOM·줄바꿈으로)
        backup.commit(snap)                                  # [ncn E4] 쓰기 성공 → 백업으로 남김
        diff = generate_diff(original, new_content, args["file_path"])   # 결과용 diff
        n = args["editCount"]                                # 편집 수
        return f"Successfully edited {args['file_path']} with {n} edit{_plural(n)}\nDiff:\n{diff}"  # 성공 메시지 + diff
    except ContinueError:                                   # ContinueError 는 그대로 올린다
        raise                                               # 재전파
    except Exception as e:                                   # 쓰기 실패
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error: failed to edit {args.get('file_path')}: {e}") from e  # 그 밖 오류는 쓰기 실패로 감싼다


MULTI_EDIT_TOOL = Tool(                                     # MultiEdit 도구 정의(cn multiEdit.ts)
    name="MultiEdit",                                       # LLM 에 보이는 이름
    display_name="MultiEdit",                               # 화면 표시 이름
    readonly=False,                                         # 쓰기 도구라 읽기 전용 아님
    is_built_in=True,                                       # 내장 도구
    # 첫 줄 끝 공백은 cn 원문에 있음 — 편집기가 지우지 않게 \x20 으로 적음
    # 도구 설명(cn 원문, 아래 여러 줄)
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
    parameters={                                            # 인자 스키마(키 순서도 cn 과 같게)
        "type": "object",                                   # 최상위는 객체
        "required": ["file_path", "edits"],                 # 필수 인자
        "properties": {                                     # 인자별 정의
            "file_path": {                                  # 수정할 파일 경로
                "type": "string",                           # 문자열 타입
                "description": "Absolute or relative path to the file to modify. Absolute preferred",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
            "edits": {                                      # 편집 목록
                "type": "array",                            # 배열 타입
                "description": "Array of edit operations to perform sequentially on the file",  # 설명(cn 원문)
                "items": {                                  # 배열 원소 정의
                    "type": "object",                       # 원소는 객체
                    "required": ["old_string", "new_string"],  # 원소의 필수 키
                    "properties": {                         # 원소별 정의
                        "old_string": {                     # 바꿀 기존 글
                            "type": "string",               # 문자열 타입
                            "description": "The text to replace (exact match including whitespace/indentation)",  # 설명(cn 원문)
                        },                                  # 여기까지 닫기
                        "new_string": {                     # 바꿀 새 글
                            "type": "string",               # 문자열 타입
                            "description": "The text to replace it with. MUST be different than old_string.",  # 설명(cn 원문)
                        },                                  # 여기까지 닫기
                        "replace_all": {                    # 전부 바꿀지 여부
                            "type": "boolean",              # 불리언 타입
                            "description": "Replace all occurrences of old_string (default false) in the file",  # 설명(cn 원문)
                        },                                  # 여기까지 닫기
                    },                                      # 여기까지 닫기
                },                                          # 여기까지 닫기
            },                                              # 여기까지 닫기
        },                                                  # 여기까지 닫기
    },                                                      # 여기까지 닫기
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # 실행 전 검사 함수 연결
)                                                           # 여기까지 닫기
