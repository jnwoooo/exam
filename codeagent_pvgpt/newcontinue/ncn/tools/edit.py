# ncn/tools/edit.py — Edit 도구(한 곳 또는 전부 찾아 바꾸기). cn extensions/cli/src/tools/edit.ts
# 전제: 이번 프로세스에서 Read 한 파일(실경로 기준)만. 민감 파일 판정은 실경로("부모 폴더/파일 이름")로
# preprocess 에서 새 내용까지 계산하고, run 은 그 결과를 쓴다. cn은 승인 사이에 파일이 바뀌어도 다시 안 읽음(사용자 수정을 덮어씀)
# [ncn 부록B B6] ncn은 run 에서 파일을 다시 읽어 preprocess 때와 다르면 다시 찾아 바꾼다(실패면 cn 형식 오류). NCN_CN_COMPAT=1 이면 cn 동작
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 현재 폴더
from typing import Any, Optional                             # 타입

from ncn.compat import cn_compat                             # [ncn 부록B B6] cn 원래 동작 스위치
from ncn.security.ignore import throw_if_file_is_security_concern   # 민감 파일 차단
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import (MISSING, REASON_EDIT_TOOL_FILE_NOT_READ, REASON_FILE_NOT_FOUND,   # 찾아 바꾸기·오류 이유
                                   REASON_FILE_WRITE_ERROR, REASON_FIND_AND_REPLACE_MISSING_FILEPATH, REASON_UNKNOWN,  # 이어서 가져오는 이름들(오류 이유 코드)
                                   execute_find_and_replace, validate_single_edit)  # 검증·실행 함수
from ncn.tools.formatters import (NodeFsError, exists_sync, path_is_absolute, path_resolve,   # Node fs·path 흉내
                                  read_file_utf8, realpath_sync, write_file_utf8)  # 읽기·실경로·쓰기 함수
from ncn.tools.read_file import READ_TOOL, read_files_set    # 읽은 파일 기록
from ncn.tools.write_file import generate_diff              # diff 생성 함수


REDO_KEY = "_ncnRedo"                                        # [ncn 부록B B6] run 인자 안의 '다시 찾아 바꾸기 재료' 키(도구 안에서만 씀)


def validate_and_resolve_file_path(args: Any) -> dict:      # Edit·MultiEdit 공용 경로 검증
    """validateAndResolveFilePath — 경로 필수 → 절대경로 → 실경로 → 민감 파일 → 존재 → Read 했나. {originalPath, resolvedPath}."""
    file_path = args.get("file_path") if isinstance(args, dict) else None  # 인자가 dict면 file_path 꺼냄
    if not file_path:                                        # 없음·빈 글
        raise ContinueError(REASON_FIND_AND_REPLACE_MISSING_FILEPATH, "file_path is required")  # 경로가 없으면 오류
    if not isinstance(file_path, str):                       # JS: path.isAbsolute 가 TypeError
        raise ContinueError(REASON_UNKNOWN, f'The "path" argument must be of type string. Received {_js_received(file_path)}')  # 문자열이 아니면 Node 형식의 타입 오류
    absolute_path = file_path if path_is_absolute(file_path) else path_resolve(os.getcwd(), file_path)   # 절대경로로
    try:                                                    # 실경로 변환 시도
        resolved_path = realpath_sync(absolute_path)         # 실경로 — 없는 파일이면 여기서 Node ENOENT(lstat) 글로 오류
    except NodeFsError as e:                                 # cn: 일반 Error 가 그대로 올라감
        raise ContinueError(REASON_UNKNOWN, str(e)) from e  # Node fs 오류를 ContinueError로 감싼다
    throw_if_file_is_security_concern(resolved_path)         # 민감 파일
    if not exists_sync(resolved_path):                       # Check if file exists(위 realpath 때문에 실제로는 거의 안 옴)
        raise ContinueError(REASON_FILE_NOT_FOUND, f"File {file_path} does not exist")  # 파일이 없으면 오류
    if resolved_path not in read_files_set:                 # 이번 프로세스에서 Read 한 파일만 허용
        raise ContinueError(REASON_EDIT_TOOL_FILE_NOT_READ,  # Read 선행 위반 오류
                            f"You must use the {READ_TOOL.name} tool to read {file_path} before editing it.")  # Read 하라는 안내 메시지
    return {"originalPath": file_path, "resolvedPath": resolved_path}  # 원래 경로와 실경로를 함께 반환


def _js_received(v: Any) -> str:                            # JS 타입 이름 흉내(오류 메시지용)
    """Node ERR_INVALID_ARG_TYPE 의 'Received …' 부분(대표 경우만)."""
    if isinstance(v, bool):                                 # 불리언
        return f"type boolean ({'true' if v else 'false'})"  # JS 의 boolean 표기
    if isinstance(v, (int, float)):                         # 숫자
        return f"type number ({v})"                         # JS 의 number 표기
    if isinstance(v, list):                                 # 배열
        return "an instance of Array"                       # JS 의 Array 표기
    if isinstance(v, dict):                                 # 객체
        return "an instance of Object"                      # JS 의 Object 표기
    return f"type {type(v).__name__}"                       # 그 밖은 파이썬 타입 이름


async def _preprocess(args: dict) -> PreprocessResult:      # Edit 의 preprocess(검증 + 새 내용 계산)
    """preprocess — 검증 후 새 내용 계산 + diff 미리보기. run 인자는 {resolvedPath, newContent, oldContent}."""
    old_string = args.get("old_string")                      # 구조 분해(없으면 undefined)
    new_string = args.get("new_string")                     # 바꿀 새 글(없으면 None)
    replace_all = args["replace_all"] if "replace_all" in args else MISSING   # 없음과 null 구분
    resolved_path = validate_and_resolve_file_path(args)["resolvedPath"]   # 경로 검증
    old_s, new_s, rep_all = validate_single_edit(old_string, new_string, replace_all)   # 인자 검증
    old_content = read_file_utf8(resolved_path)              # 지금 내용
    new_content = execute_find_and_replace(old_content, old_s, new_s, rep_all if rep_all is not None else False, 0)  # 바꾼 새 내용 계산(replaceAll ?? false)
    diff = generate_diff(old_content, new_content, resolved_path)   # 미리보기 diff
    redo = {"old_string": old_s, "new_string": new_s, "replace_all": rep_all if rep_all is not None else False}   # [ncn 부록B B6] run 에서 다시 찾아 바꿀 재료
    return PreprocessResult(                                # run 인자와 미리보기를 묶어 반환
        args={"resolvedPath": resolved_path, "newContent": new_content, "oldContent": old_content,  # run 에 넘길 인자(실경로·새 내용·옛 내용)
              **({} if cn_compat() else {REDO_KEY: redo})},  # [ncn 부록B B6] 고친 동작에서만 재료를 붙임(LLM 요청에는 안 들어감)
        preview=[ToolCallPreview("text", "Will make the following changes:"), ToolCallPreview("diff", diff)],  # 미리보기: 안내 글 + diff
    )                                                       # PreprocessResult 닫기


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # Edit 의 run(쓰기만 한다)
    """run — 새 내용을 쓰고 diff 를 돌려줌. [ncn 부록B B6] 쓰기 전에 다시 읽어 바뀌었으면 다시 찾아 바꿈."""
    try:                                                    # 쓰기 시도
        old_content, new_content = args["oldContent"], args["newContent"]   # preprocess 때 계산한 내용
        redo = args.get(REDO_KEY)                            # [ncn 부록B B6] 다시 찾아 바꿀 재료(없으면 cn처럼 그대로 씀)
        if redo is not None and not cn_compat():             # [ncn 부록B B6] 고친 동작(cn 원래 동작: 미리 계산한 내용을 그대로 씀)
            current = read_file_utf8(args["resolvedPath"])   # 승인 뒤 실행 시점의 내용
            if current != old_content:                       # 승인 대기 중 사용자·린터가 고쳤으면
                new_content = execute_find_and_replace(current, redo["old_string"], redo["new_string"], redo["replace_all"], 0)  # 지금 내용에서 다시 찾아 바꿈(못 찾음·여러 곳이면 cn 글로 오류)
                old_content = current                       # diff 기준도 지금 내용
        write_file_utf8(args["resolvedPath"], new_content)   # 쓰기
        diff = generate_diff(old_content, new_content, args["resolvedPath"])   # 결과용 diff
        return f"Successfully edited {args['resolvedPath']}\nDiff:\n{diff}"  # 성공 메시지 + diff
    except ContinueError:                                   # ContinueError 는 그대로 올린다
        raise                                               # 재전파
    except Exception as e:                                   # 쓰기 실패
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error: failed to edit {args.get('resolvedPath')}: {e}") from e  # 그 밖 오류는 쓰기 실패로 감싼다


EDIT_TOOL = Tool(                                           # Edit 도구 정의(cn edit.ts)
    name="Edit",                                            # LLM 에 보이는 이름
    display_name="Edit",                                    # 화면 표시 이름
    readonly=False,                                         # 쓰기 도구라 읽기 전용 아님
    is_built_in=True,                                       # 내장 도구
    # "USAGE: " 끝 공백은 cn 원문에 있음 — 편집기가 지우지 않게 \x20 으로 적음
    # 도구 설명(cn 원문, 아래 여러 줄)
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
    parameters={                                            # 인자 스키마(키 순서도 cn 과 같게)
        "type": "object",                                   # 최상위는 객체
        "required": ["file_path", "old_string", "new_string"],  # 필수 인자
        "properties": {                                     # 인자별 정의
            "file_path": {                                  # 수정할 파일 경로
                "type": "string",                           # 문자열 타입
                "description": "Absolute or relative path to the file to modify. Absolute preferred",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
            "old_string": {                                 # 바꿀 기존 글
                "type": "string",                           # 문자열 타입
                "description": "The text to replace - must be exact including whitespace/indentation",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
            "new_string": {                                 # 바꿀 새 글
                "type": "string",                           # 문자열 타입
                "description": "The text to replace it with (MUST be different from old_string)",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
            "replace_all": {                                # 전부 바꿀지 여부
                "type": "boolean",                          # 불리언 타입
                "description": "Replace all occurrences of old_string (default false)",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
        },                                                  # 여기까지 닫기
    },                                                      # 여기까지 닫기
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # 실행 전 검사 함수 연결
)                                                           # 여기까지 닫기
