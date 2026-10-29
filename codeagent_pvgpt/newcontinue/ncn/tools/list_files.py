# ncn/tools/list_files.py — List 도구(폴더 한 단계 목록). cn extensions/cli/src/tools/listFiles.ts
# 결과: "Files in {절대경로}:\n이름 (directory)\n이름 (file, N bytes)". 순서는 Node readdirSync 와 같게 이름의 UTF-8 바이트 순
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 폴더 읽기·상태
from typing import Optional                                  # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import REASON_UNKNOWN             # cn에서 일반 Error 였던 것
from ncn.tools.formatters import (NodeFsError, exists_sync, format_tool_argument, node_fs_error_message,   # Node 흉내
                                  node_platform, path_join, path_normalize, path_resolve)


def _readdir_sync(path: str) -> list[str]:                  # Node readdirSync 흉내
    """Node fs.readdirSync — 이름 목록. libuv scandir 는 posix 에서 strcmp(바이트) 정렬, Windows 는 파일 시스템 순서."""
    try:                                                    # 목록 읽기 시도
        names = os.listdir(path)                             # '.'·'..' 없음
    except OSError as e:                                     # Node 글: "ENOENT: …, scandir 'x'"
        msg = node_fs_error_message(e, "scandir", path)     # Node 형식의 오류 글을 만든다
        raise NodeFsError(msg, msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림
    if node_platform() != "win32":                           # posix: 바이트 순 정렬(scandir + strcmp)
        names.sort(key=lambda n: os.fsencode(n))            # 이름의 바이트 순으로 정렬
    return names                                             # Windows: FindNextFile 순서 그대로(NTFS 는 대체로 이름순)


async def _preprocess(args: dict) -> PreprocessResult:      # List 의 preprocess(경로 검사)
    """preprocess — 경로를 cwd 기준 절대경로로. 없거나 폴더가 아니면 오류."""
    normalized = path_normalize(args["dirpath"])             # Resolve relative paths
    dir_path = path_resolve(os.getcwd(), normalized)         # 절대경로
    if not exists_sync(dir_path):                            # 없음(cn: 일반 Error)
        raise ContinueError(REASON_UNKNOWN, f"Directory does not exist: {dir_path}")
    if not os.path.isdir(dir_path):                          # 폴더 아님
        raise ContinueError(REASON_UNKNOWN, f"Error: Path is not a directory: {dir_path}")
    return PreprocessResult(                                # run 인자와 미리보기를 묶어 반환
        args={"dirpath": dir_path},                         # 절대경로로 바꾼 인자
        preview=[ToolCallPreview("text", f"Will list files in: {format_tool_argument(dir_path)}" if dir_path  # 미리보기: 어느 폴더를 나열하는지
                                 else "Will list files in current directory")],  # 경로가 비었을 때의 글
    )                                                       # 여기까지 닫기


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # List 의 run(항목마다 종류·크기)
    """run — 항목마다 종류·크기. 하나라도 stat 실패(깨진 링크 등)하면 전체가 오류(cn 그대로)."""
    try:                                                    # 전체를 감싸 오류 형식을 맞춘다
        dirpath = args["dirpath"]                            # 절대경로
        details = []                                         # 줄들
        for name in _readdir_sync(dirpath):                  # 항목마다
            full = path_join(dirpath, name)                  # 전체 경로
            try:                                            # stat 시도
                st = os.stat(full)                           # 링크는 따라감(statSync)
            except OSError as e:                             # Node 글: "ENOENT: …, stat 'x'"
                msg = node_fs_error_message(e, "stat", full)  # Node 형식의 오류 글
                raise NodeFsError(msg, msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림
            is_dir = os.path.isdir(full)                     # 폴더?
            kind = "directory" if is_dir else "file"         # 폴더 아니면 전부 file
            size = f"{st.st_size} bytes" if os.path.isfile(full) else ""   # 일반 파일만 크기
            details.append(f"{name} ({kind}{', ' + size if size else ''})")  # 한 줄 형식: 이름 (종류, 크기)
        return f"Files in {dirpath}:\n" + "\n".join(details)  # 머리말 + 줄들을 이어 반환
    except Exception as e:                                   # 전부 "Error listing files: …"
        raise ContinueError(REASON_UNKNOWN, f"Error listing files: {e}") from e


LIST_TOOL = Tool(                                           # List 도구 정의(cn listFiles.ts)
    name="List",                                            # LLM 에 보이는 이름
    display_name="List",                                    # 화면 표시 이름
    description="List files in a directory",                # 도구 설명(cn 원문)
    parameters={                                            # 인자 스키마
        "type": "object",                                   # 최상위는 객체
        "required": ["dirpath"],                            # 필수 인자
        "properties": {                                     # 인자별 정의
            "dirpath": {                                    # 나열할 폴더 경로
                "type": "string",                           # 문자열 타입
                "description": "The path to the directory to list",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
        },                                                  # 여기까지 닫기
    },                                                      # 여기까지 닫기
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # 실행 전 검사 함수 연결
    readonly=True,                                          # 읽기 전용 도구
    is_built_in=True,                                       # 내장 도구
)                                                           # 여기까지 닫기
