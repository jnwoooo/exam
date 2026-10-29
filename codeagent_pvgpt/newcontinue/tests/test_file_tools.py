# tests/test_file_tools.py — 파일 도구(Read·Write·Edit·MultiEdit·List)·민감 파일 판정·출력 자르기·diff 시험
# cn edit.test.ts·multiEdit.test.ts·truncateOutput.test.ts 대표 사례 + node 가 있으면 cn 원본 라이브러리와 차등 비교
import asyncio                                               # 도구는 async
import json                                                  # Node 결과
import os                                                    # 작업 폴더·환경변수
import random                                                # 무작위 입력
import re                                                    # 설명 원문 뽑기
import shutil                                                # node 찾기
import subprocess                                            # node 실행
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from ncn.security.ignore import (DEFAULT_SECURITY_IGNORE_DIRS, DEFAULT_SECURITY_IGNORE_FILETYPES,   # 시험 대상
                                 default_file_and_folder_security_ignores, is_security_concern,  # 판정기 객체와 함수
                                 throw_if_file_is_security_concern)  # 민감 파일 오류 함수
from ncn.tools import formatters                             # 실행 OS 바꾸기
from ncn.tools.base import ContinueError, ToolRunContext, openai_tool_definition   # 공용 형식
from ncn.tools.edit import EDIT_TOOL                        # Edit 도구
from ncn.tools.list_files import LIST_TOOL                  # List 도구
from ncn.tools.multi_edit import MULTI_EDIT_TOOL            # MultiEdit 도구
from ncn.tools.read_file import READ_TOOL, mark_file_as_read, read_files_set  # Read 도구와 읽은 파일 기록
from ncn.tools.truncate import (parse_env_number, truncate_by_lines_and_chars, truncate_lines_by_count,   # 자르기
                                truncate_output_from_end, truncate_output_from_start)  # 자르기 함수들
from ncn.tools.write_file import WRITE_TOOL, create_two_files_patch, generate_diff  # Write 도구와 diff 함수

REF = Path(__file__).resolve().parent.parent / ".ref" / "continue"   # cn 소스
CLI = REF / "extensions" / "cli" / "src"                     # cn CLI 소스
HAS_NODE = bool(shutil.which("node")) and (REF / "core" / "node_modules").exists()   # 차등 비교 가능?


def run(coro):                                              # async 호출을 동기로 실행하는 도우미
    """async 호출을 동기로."""
    return asyncio.run(coro)                                # 이벤트 루프에서 끝까지 실행


async def call(tool, args, ctx=None):                       # preprocess 다음 run 을 부르는 도우미
    """루프처럼: preprocess → run(고쳐진 인자로)."""
    pre = await tool.preprocess(args)                       # 실행 전 검사를 먼저 한다
    return await tool.run(pre.args, ctx or ToolRunContext("t1"))  # 고쳐진 인자로 실행


@pytest.fixture                                             # 임시 작업 폴더 픽스처
def ws(tmp_path, monkeypatch):                              # 임시 폴더를 cwd 로 만든다
    """임시 작업 폴더를 cwd 로, 읽은 파일 기록은 시험마다 비움(cn 시험의 readFilesSet.clear() 와 같음)."""
    real = Path(os.path.realpath(tmp_path))                 # 심볼릭 링크를 풀어 실경로로
    monkeypatch.chdir(real)                                 # cwd 를 임시 폴더로
    read_files_set.clear()                                  # 읽은 파일 기록 비움
    yield real                                              # 시험에 폴더를 넘김
    read_files_set.clear()                                  # 끝나고 기록 비움


# ───────────── 도구 정의(LLM 에 보이는 글)가 cn 원문과 같은가 ─────────────
def _ts_template(src: str, start_marker: str) -> str:       # TS 설명 템플릿을 뽑는 도우미
    """TS 의 description: `…` 템플릿 글 뽑기(${…Tool.name} 치환)."""
    i = src.index(start_marker) + len(start_marker)         # 시작 위치
    j = i                                                   # 끝 위치 탐색 시작
    while True:                                              # 이스케이프 안 된 ` 찾기
        j = src.index("`", j)                               # 다음 백틱 위치
        if src[j - 1] != "\\":                              # 이스케이프된 백틱이 아니면
            break                                           # 찾음
        j += 1                                              # 이스케이프된 것이면 계속
    body = src[i:j].replace("\\`", "`")                     # 이스케이프를 풀어 본문 추출
    return body.replace("${readFileTool.name}", "Read").replace("${editTool.name}", "Edit")  # 템플릿 변수를 도구 이름으로 치환


@pytest.mark.skipif(not CLI.exists(), reason=".ref/continue 없음")  # 원본 소스가 없으면 건너뜀
def test_descriptions_match_cn_source():                    # 도구 설명이 cn 원문과 같은지 시험
    assert EDIT_TOOL.description == _ts_template((CLI / "tools" / "edit.ts").read_text(), "description: `")  # Edit 설명 비교
    assert MULTI_EDIT_TOOL.description == _ts_template((CLI / "tools" / "multiEdit.ts").read_text(), "description: `")  # MultiEdit 설명 비교
    for tool, f in [(READ_TOOL, "readFile.ts"), (WRITE_TOOL, "writeFile.ts"), (LIST_TOOL, "listFiles.ts")]:  # 나머지 세 도구와 원본 파일 이름
        m = re.search(r'description: "([^"]+)"', (CLI / "tools" / f).read_text())  # 원본에서 description 글을 뽑는다
        assert tool.description == m.group(1)               # 설명이 원문과 같아야 한다


def test_tool_names_and_schema_key_order():                 # 도구 이름과 스키마 키 순서 시험
    assert [t.name for t in (READ_TOOL, WRITE_TOOL, EDIT_TOOL, MULTI_EDIT_TOOL, LIST_TOOL)] == ["Read", "Write", "Edit", "MultiEdit", "List"]  # 이름 순서
    assert list(EDIT_TOOL.parameters["properties"]) == ["file_path", "old_string", "new_string", "replace_all"]  # Edit 인자 키 순서
    assert list(EDIT_TOOL.parameters) == ["type", "required", "properties"]  # Edit 스키마 최상위 키 순서
    d = openai_tool_definition(MULTI_EDIT_TOOL)             # MultiEdit 정의를 LLM 형식으로
    assert list(d["function"]["parameters"]["properties"]["edits"]["items"]["properties"]) == ["old_string", "new_string", "replace_all"]  # 편집 항목 키 순서
    assert EDIT_TOOL.description.split("\n")[2] == "USAGE: "   # 끝 공백까지 원문 그대로
    assert READ_TOOL.readonly and LIST_TOOL.readonly and not WRITE_TOOL.readonly  # 읽기 전용 여부


# ───────────── Read ─────────────
def test_read_success_marks_realpath_and_strips_dot_slash(ws):  # Read 성공 시 실경로 기록과 './' 제거 시험
    (ws / "a.txt").write_text("hi\nthere")                  # 파일 만들기
    pre = run(READ_TOOL.preprocess({"filepath": "./a.txt"}))  # preprocess 실행
    assert pre.preview[0].content == "Will read a.txt"      # 미리보기 글 확인
    out = run(READ_TOOL.run({"filepath": "./a.txt"}, ToolRunContext("t")))  # run 실행
    assert out == "Content of a.txt:\nhi\nthere"             # 줄 번호 없음
    assert str(ws / "a.txt") in read_files_set               # 실경로로 기록


def test_read_missing_and_security(ws):                     # Read 의 없는 파일·민감 파일 시험
    with pytest.raises(ContinueError) as e:                 # 없는 파일은 오류
        run(call(READ_TOOL, {"filepath": "nope.txt"}))      # 실행
    assert str(e.value) == "File does not exist: nope.txt"  # 오류 메시지 확인
    with pytest.raises(ContinueError) as e:                 # 민감 파일은 preprocess 에서 차단
        run(READ_TOOL.preprocess({"filepath": "./.env"}))   # 실행
    assert e.value.reason == "file_is_security_concern"     # 이유 코드 확인
    assert str(e.value) == ("Reading or Editing .env is not allowed because it is a security concern. "  # cn 원문 메시지 확인
                            "Do not attempt to read or edit this file in any way.")  # 메시지 뒷부분
    with pytest.raises(ContinueError) as e:                  # npm ignore 의 RangeError 글이 그대로
        run(READ_TOOL.preprocess({"filepath": "../x.txt"}))  # 상대경로가 아닌 경로
    assert str(e.value) == 'path should be a `path.relative()`d string, but got "../x.txt"'  # 오류 글 확인


def test_read_too_large_message_and_parallel(ws, monkeypatch):  # 한도 초과 메시지와 병렬 한도 시험
    (ws / "big.txt").write_text("x" * 1500 + "\n" + "y")    # 한도를 넘는 파일 만들기
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "1000")  # 글자 한도를 1000 으로
    with pytest.raises(ContinueError) as e:                 # 한도 초과 오류 기대
        run(call(READ_TOOL, {"filepath": "big.txt"}))       # 실행
    assert e.value.reason == "file_too_large"               # 이유 코드 확인
    assert str(e.value) == ("File is too large to read: big.txt (1,502 characters, 2 lines). Maximum allowed: 1,000 characters or 5,000 lines. "  # 오류 메시지 앞부분
                            "Consider using terminal commands like 'head', 'tail', 'sed', or 'grep' to read targeted parts of the file.")  # 오류 메시지 뒷부분
    assert str(ws / "big.txt") not in read_files_set         # 실패하면 기록 안 함
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "3000")  # 글자 한도를 3000 으로
    with pytest.raises(ContinueError) as e:                  # 병렬 3개 → 1000자
        run(call(READ_TOOL, {"filepath": "big.txt"}, ToolRunContext("t", parallel_tool_call_count=3)))  # 병렬 수를 3 으로 실행
    assert ("Maximum allowed: 1,000 characters or 1,666 lines. (Note: limit reduced due to 3 parallel tool calls. "  # 병렬 안내가 들어간 메시지
            "Single-tool limit: 3,000 characters or 5,000 lines.) Consider") in str(e.value)  # 단일 도구 한도 안내


def test_read_counts_utf16_and_keeps_crlf_bom(ws, monkeypatch):  # UTF-16 길이와 CRLF·BOM 유지 시험
    (ws / "e.txt").write_bytes("\ufeff😀\r\n".encode("utf-8"))  # BOM·이모지·CRLF 가 든 파일
    assert run(call(READ_TOOL, {"filepath": "e.txt"})) == "Content of e.txt:\n\ufeff😀\r\n"   # BOM·CRLF 그대로
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "4")  # 글자 한도를 4 로
    with pytest.raises(ContinueError) as e:                 # JS length 로 5 라서 초과
        run(call(READ_TOOL, {"filepath": "e.txt"}))         # 실행
    assert "(5 characters, 2 lines)" in str(e.value)        # 글자·줄 수 확인


def test_read_directory_error(ws):                          # 폴더를 읽을 때의 오류 시험
    (ws / "d").mkdir()                                      # 폴더 만들기
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(call(READ_TOOL, {"filepath": "d"}))             # 폴더를 Read
    assert str(e.value) == "Error reading file: EISDIR: illegal operation on a directory, read"  # Node 와 같은 EISDIR 글


# ───────────── Write ─────────────
def test_write_new_and_existing(ws):                        # Write 새 파일·기존 파일 시험
    pre = run(WRITE_TOOL.preprocess({"filepath": "sub/dir/n.txt", "content": "l1\n\nl3\nl4\nl5"}))  # 새 파일 preprocess
    assert [p.content for p in pre.preview] == ["New file content:", "l1", " ", "l3", "... (2 more lines)"]  # 미리보기 글 목록
    assert pre.preview[1].padding_left == 2                 # 들여쓰기 확인
    assert run(WRITE_TOOL.run(pre.args, ToolRunContext("t"))) == "Successfully created file: sub/dir/n.txt"  # 새 파일 성공 메시지
    assert (ws / "sub/dir/n.txt").read_bytes() == b"l1\n\nl3\nl4\nl5"  # 파일 내용 확인
    assert str(ws / "sub/dir/n.txt") not in read_files_set   # Write 는 기록 안 함
    pre = run(WRITE_TOOL.preprocess({"filepath": "sub/dir/n.txt", "content": "l1\nX\nl3\nl4\nl5"}))  # 기존 파일 preprocess
    assert pre.preview[0].content == "Preview of changes:" and pre.preview[1].type == "diff"  # diff 미리보기 확인
    assert pre.preview[1].content == ("Index: sub/dir/n.txt\n===================================================================\n"  # 문맥 2줄 diff
                                      "--- sub/dir/n.txt\n+++ sub/dir/n.txt\n@@ -1,4 +1,4 @@\n l1\n-\n+X\n l3\n l4\n")   # 문맥 2줄
    out = run(WRITE_TOOL.run(pre.args, ToolRunContext("t")))  # 기존 파일 run
    assert out == ("Successfully wrote to file: sub/dir/n.txt\nDiff:\nIndex: sub/dir/n.txt\n"  # 성공 메시지와 diff
                   "===================================================================\n--- sub/dir/n.txt\n+++ sub/dir/n.txt\n"
                   "@@ -1,5 +1,5 @@\n l1\n-\n+X\n l3\n l4\n l5\n\\ No newline at end of file\n")   # 문맥 3줄


def test_write_errors(ws):                                  # Write 오류 시험
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(WRITE_TOOL.preprocess({"filepath": 3, "content": "x"}))  # 경로가 숫자
    assert str(e.value) == "Filepath must be a string"      # 오류 글 확인
    (ws / "d").mkdir()                                      # 폴더 만들기
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(WRITE_TOOL.run({"filepath": "d", "content": "x"}, ToolRunContext("t")))  # 폴더에 쓰기 시도
    assert e.value.reason == "file_write_error"             # 이유 코드 확인
    assert str(e.value) == "Error writing to file: EISDIR: illegal operation on a directory, read"   # 기존 내용 읽기에서 먼저 실패(cn 순서)


# ───────────── Edit(cn edit.test.ts) ─────────────
ORIGINAL = "Hello world\nThis is a test file\nGoodbye world"  # Edit 시험용 원본 내용


@pytest.fixture                                             # 파일 픽스처
def edit_file(ws):                                          # Edit 시험 파일 만들기
    p = ws / "test-edit-file.txt"                           # 파일 경로
    p.write_text(ORIGINAL)                                  # 원본 내용 쓰기
    return p                                                # 경로 반환


def test_edit_requires_read(edit_file):                     # Read 없이 Edit 하면 오류
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "Hello world", "new_string": "Hi there"}))  # Read 없이 preprocess
    assert e.value.reason == "edit_tool_file_not_yet_read"  # 이유 코드 확인
    assert str(e.value) == f"You must use the Read tool to read {edit_file} before editing it."  # 안내 메시지 확인


def test_edit_missing_file_gives_node_lstat_error(ws):      # 없는 파일은 Node lstat 오류
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(EDIT_TOOL.preprocess({"file_path": "nope.txt", "old_string": "a", "new_string": "b"}))  # 없는 파일 preprocess
    assert str(e.value) == f"ENOENT: no such file or directory, lstat '{ws / 'nope.txt'}'"   # realpathSync 가 먼저 던짐
    with pytest.raises(ContinueError) as e:                 # 경로가 없을 때
        run(EDIT_TOOL.preprocess({"old_string": "a", "new_string": "b"}))  # file_path 없이 실행
    assert str(e.value) == "file_path is required"          # 필수 인자 오류 글


@pytest.mark.parametrize("args,reason", [                   # preprocess 오류 사례 표
    ({"old_string": "Not found", "new_string": "Hi there"}, "find_and_replace_old_string_not_found"),  # 못 찾음
    ({"old_string": "world", "new_string": "universe", "replace_all": False}, "find_and_replace_multiple_occurrences"),  # 여러 곳
    ({"old_string": "Hello world", "new_string": "Hello world"}, "find_and_replace_identical_old_and_new_strings"),  # old 와 new 동일
])                                                          # 표 닫기
def test_edit_preprocess_errors(edit_file, args, reason):   # preprocess 오류 이유 시험
    run(call(READ_TOOL, {"filepath": "test-edit-file.txt"}))  # 먼저 Read 해 기록
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(EDIT_TOOL.preprocess({"file_path": str(edit_file), **args}))  # preprocess 실행
    assert e.value.reason == reason                         # 이유 코드 확인


def test_edit_success_relative_path_and_replace_all(edit_file, ws, cn_compat_env):  # 상대경로·전부 바꾸기 성공 시험(cn run 인자 모양 — [ncn 부록B B6] 재료 키가 없는 cn 동작으로)
    run(call(READ_TOOL, {"filepath": "test-edit-file.txt"}))  # 먼저 Read 해 기록
    pre = run(EDIT_TOOL.preprocess({"file_path": "test-edit-file.txt", "old_string": "Hello world", "new_string": "Hi there"}))  # 상대경로로 preprocess
    assert pre.args == {"resolvedPath": str(edit_file), "newContent": "Hi there\nThis is a test file\nGoodbye world", "oldContent": ORIGINAL}  # run 에 넘길 인자 확인
    assert pre.preview[0].content == "Will make the following changes:"  # 미리보기 글 확인
    out = run(EDIT_TOOL.run(pre.args, ToolRunContext("t")))  # run 실행
    assert out == f"Successfully edited {edit_file}\nDiff:\n" + generate_diff(ORIGINAL, pre.args["newContent"], str(edit_file))  # 결과 메시지와 diff 확인
    assert edit_file.read_text() == "Hi there\nThis is a test file\nGoodbye world"  # 파일이 실제로 바뀜
    pre = run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "world", "new_string": "universe", "replace_all": True}))  # 전부 바꾸기 preprocess
    assert pre.args["newContent"] == "Hi there\nThis is a test file\nGoodbye universe"  # 새 내용 확인


def test_edit_security_uses_realpath_parent_and_name(ws):   # 민감 판정은 실경로의 부모·이름 기준
    (ws / "secrets").mkdir()                                # secrets 폴더 만들기
    f = ws / "secrets" / "a.txt"                            # 그 안의 파일 경로
    f.write_text("x")                                       # 파일 쓰기
    mark_file_as_read(str(f))                               # 읽은 것으로 기록
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(EDIT_TOOL.preprocess({"file_path": "secrets/a.txt", "old_string": "x", "new_string": "y"}))  # 민감 폴더 안 파일 preprocess
    assert str(e.value).startswith(f"Reading or Editing {f} is not allowed")   # 실경로로 판정·표시


def test_edit_write_failure(ws):                            # Edit 쓰기 실패 시험
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(EDIT_TOOL.run({"resolvedPath": str(ws), "newContent": "n", "oldContent": "o"}, ToolRunContext("t")))  # 폴더에 쓰기 시도
    assert e.value.reason == "file_write_error"             # 이유 코드 확인
    assert str(e.value) == f"Error: failed to edit {ws}: EISDIR: illegal operation on a directory, open '{ws}'"  # 오류 메시지 확인


# ───────────── MultiEdit(cn multiEdit.test.ts) ─────────────
def test_multi_edit_sequential_atomic(ws):                  # MultiEdit 순차 적용·전부 또는 전무 시험
    f = ws / "m.py"                                         # 파일 경로
    f.write_text("a = 1\nb = 2\n")                          # 파일 쓰기
    run(call(READ_TOOL, {"filepath": "m.py"}))              # 먼저 Read 해 기록
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "a = 1", "new_string": "a = 10"},  # 편집 두 개 preprocess
                                                                         {"old_string": "a = 10\nb", "new_string": "c"}]}))  # 두 번째 편집은 첫 결과에 적용
    assert pre.args["newContent"] == "c = 2\n" and pre.args["editCount"] == 2  # 새 내용과 편집 수 확인
    assert pre.preview[0].content == f"Will apply 2 edits to {f}:"  # 미리보기 글 확인
    assert run(MULTI_EDIT_TOOL.run(pre.args, ToolRunContext("t"))).startswith(f"Successfully edited {f} with 2 edits\nDiff:\nIndex: {f}\n")  # run 결과 메시지 확인
    with pytest.raises(ContinueError):                       # 두 번째 실패 → 파일 그대로
        run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "c", "new_string": "d"},  # 오류 기대
                                                                       {"old_string": "zzz", "new_string": "q"}]}))  # 두 번째 편집 정의
    assert f.read_text() == "c = 2\n"                       # 파일이 그대로여야 함
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "", "new_string": "# head\n"}]}))  # 빈 old_string preprocess
    assert pre.args["newContent"] == "# head\nc = 2\n"       # 빈 old_string = 맨 앞 삽입
    assert pre.preview[0].content == f"Will apply 1 edit to {f}:"  # 단수형 미리보기 확인


def test_multi_edit_validation_order(ws):                   # MultiEdit 검증 순서 시험
    with pytest.raises(ContinueError) as e:                  # 경로 검증이 먼저
        run(MULTI_EDIT_TOOL.preprocess({"edits": []}))      # 경로 없이 실행
    assert str(e.value) == "file_path is required"          # 오류 글 확인
    (ws / "m.txt").write_text("x")                          # 파일 만들기
    run(call(READ_TOOL, {"filepath": "m.txt"}))             # 먼저 Read 해 기록
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.txt", "edits": []}))  # 빈 edits preprocess
    assert e.value.reason == "multi_edit_edits_array_empty"  # 이유 코드 확인


# ───────────── List ─────────────
def test_list(ws):                                          # List 시험
    (ws / "b.txt").write_text("12345")                      # 파일 만들기
    (ws / "a_dir").mkdir()                                  # 폴더 만들기
    (ws / "C.txt").write_text("")                         # 대문자가 소문자보다 앞(바이트 순)
    pre = run(LIST_TOOL.preprocess({"dirpath": "."}))       # 현재 폴더 preprocess
    assert pre.args == {"dirpath": str(ws)}                 # 절대경로로 바뀐 인자 확인
    assert pre.preview[0].content == f"Will list files in: {ws}"   # 같은 폴더면 상대경로가 빈 글 → 원래 값
    out = run(LIST_TOOL.run(pre.args, ToolRunContext("t")))  # run 실행
    assert out == f"Files in {ws}:\nC.txt (file, 0 bytes)\na_dir (directory)\nb.txt (file, 5 bytes)"   # 바이트 순
    with pytest.raises(ContinueError) as e:                 # 없는 폴더는 오류
        run(LIST_TOOL.preprocess({"dirpath": "nope"}))      # 없는 폴더 preprocess
    assert str(e.value) == f"Directory does not exist: {ws / 'nope'}"  # 오류 글 확인
    with pytest.raises(ContinueError) as e:                 # 파일을 폴더로 쓰면 오류
        run(LIST_TOOL.preprocess({"dirpath": "b.txt"}))     # 파일 경로 preprocess
    assert str(e.value) == f"Error: Path is not a directory: {ws / 'b.txt'}"  # 오류 글 확인
    sub = ws / "a_dir"                                      # 하위 폴더
    pre = run(LIST_TOOL.preprocess({"dirpath": "a_dir/"}))  # 끝 슬래시가 있는 경로 preprocess
    assert pre.args["dirpath"] == str(sub) and pre.preview[0].content == "Will list files in: a_dir"  # 인자와 미리보기 확인
    assert run(LIST_TOOL.run(pre.args, ToolRunContext("t"))) == f"Files in {sub}:\n"  # 빈 폴더 결과 확인


def test_list_broken_symlink_fails_whole(ws):               # 깨진 링크가 있으면 전체 실패
    os.symlink(ws / "missing", ws / "link")                 # 깨진 심볼릭 링크 만들기
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        run(LIST_TOOL.run({"dirpath": str(ws)}, ToolRunContext("t")))  # List 실행
    assert str(e.value) == f"Error listing files: ENOENT: no such file or directory, stat '{ws / 'link'}'"  # stat 오류 글 확인


# ───────────── 민감 파일 판정 ─────────────
@pytest.mark.parametrize("path,expected", [                 # 민감 파일 판정 사례 표
    (".env", True), ("a.env", True), ("src/config.yaml", True), ("CONFIG.JSON", True), ("appsettings.Dev.json", True),  # 환경설정·설정 파일
    ("secrets/sub/a.txt", True), ("foo.env/bar.txt", True), ("env", False), ("env/a", True), ("tmp/secrets/x", True),  # 폴더 패턴 사례
    ("README.md", False), ("src/main.py", False),           # 민감하지 않은 파일
    ("/home/u/.ssh/id_rsa", True), ("/proj/secrets/sub/a.txt", False),   # 절대경로는 "부모/이름" 두 조각
    ("/home/u/project/config.json", True), ("/", False), ("file:///home/u/.ssh/known_hosts", True),  # file:// 주소와 루트
])                                                          # 표 닫기
def test_is_security_concern(path, expected, cn_compat_env):  # 민감 파일 판정 시험(cn 기대값 — [ncn 부록B S5] 고친 동작은 test_ncn_fixes)
    assert is_security_concern(path) is expected            # 기대값과 같아야 함


def test_security_win32_branch(monkeypatch):                # Windows 분기 시험
    monkeypatch.setattr(formatters, "PLATFORM_OVERRIDE", "win32")  # 플랫폼을 win32 로 강제
    assert is_security_concern("C:\\Users\\u\\.ssh\\id_rsa") is True  # win32 dirname/basename
    assert is_security_concern("src\\secrets\\a.txt") is True          # '\' → '/' 변환 후 판정
    assert is_security_concern("C:\\proj\\src\\main.py") is False  # 민감하지 않은 Windows 경로
    with pytest.raises(ContinueError) as e:                 # 오류 기대
        throw_if_file_is_security_concern("..\\x")          # 상위 경로 검사
    assert str(e.value) == 'path should be a `path.relative()`d string, but got "..\\x"'  # 오류 글 확인


# ───────────── truncateOutput.test.ts ─────────────
def test_truncate_from_start():                             # 앞을 버리는 자르기 시험
    assert truncate_output_from_start("", {"maxChars": 10, "maxLines": 2}).output == ""  # 빈 글은 그대로
    lines = "\n".join(f"line{i}" for i in range(10))        # 열 줄짜리 글
    r = truncate_output_from_start(lines, {"maxChars": 1000, "maxLines": 3})  # 줄 한도 3
    assert r.was_truncated and r.output == "(previous 7 lines truncated)\n\nline7\nline8\nline9"  # 앞 7줄이 잘림
    r = truncate_output_from_start("a" * 50, {"maxChars": 10, "maxLines": 100})  # 글자 한도만 초과
    assert r.output == "(previous 40 characters truncated)\n\n" + "a" * 10  # 앞 40자가 잘림
    r = truncate_output_from_start("aaaa\nbbbb\ncccc", {"maxChars": 7, "maxLines": 100})  # 줄 경계로 맞춤 사례
    assert r.output == "(previous 10 characters truncated)\n\ncccc"   # 줄 경계로 맞춤
    r = truncate_output_from_start("x" * 20 + "\n" + "y" * 20 + "\n" + "z" * 20, {"maxChars": 25, "maxLines": 2})  # 줄·글자 모두 초과 사례
    assert r.output == "(previous output truncated: 1 lines and 21 characters removed)\n\n" + "z" * 20  # 둘 다 알리는 머리말


def test_truncate_from_end_and_lines():                     # 끝을 버리는 자르기와 줄 자르기 시험
    assert truncate_output_from_end("short", 10).was_truncated is False  # 한도 안이면 그대로
    r = truncate_output_from_end("a" * 20, 5, "diff output")  # 글자 한도 5
    assert r.output == "aaaaa\n\n(15 characters of diff output truncated)"  # 꼬리말 확인
    assert truncate_output_from_end("line1\nline2\nline3", 14).output == "line1\nline2\n\n(6 characters truncated)"  # 줄 경계에 맞춘 꼬리말
    assert truncate_lines_by_count("a\nb\nc\nd", 2, "file").output == "a\nb\n\n(2 lines of file truncated)"  # 줄 수로 자르기
    assert truncate_by_lines_and_chars("aaaa\nbbbb\ncccc", 2, 6).output.endswith("characters truncated)")  # 줄 후 글자 자르기


@pytest.mark.parametrize("val,expected", [(None, 7), ("", 7), ("abc", 7), ("0", 7), ("-5", 7), ("42", 42), ("3.9", 3), (" 12x", 12)])  # 환경변수 숫자 사례 표
def test_parse_env_number(val, expected):                   # 환경변수 숫자 읽기 시험
    assert parse_env_number(val, 7) == expected             # 기대값과 같아야 함


# ───────────── Node 차등 비교: npm diff · npm ignore · truncateOutput.ts 원본 ─────────────
@pytest.mark.skipif(not HAS_NODE, reason="node 또는 .ref/continue/core/node_modules 없음")  # node 가 없으면 건너뜀
def test_node_diff_library_parity(tmp_path):                # npm diff 라이브러리와 차등 비교
    lib = REF / "core" / "node_modules" / "diff" / "libesm" / "index.js"  # diff 라이브러리 경로
    (tmp_path / "d.mjs").write_text(f'import {{ createTwoFilesPatch }} from {json.dumps(lib.as_uri())};\nimport fs from "fs";\n'  # node 스크립트 작성
                                    'const cs = JSON.parse(fs.readFileSync(0, "utf8"));\n'  # 입력 읽기
                                    "console.log(JSON.stringify(cs.map(c => createTwoFilesPatch(c[0], c[0], c[1], c[2], undefined, undefined, { context: c[3] }))));\n")  # 패치 출력
    rnd = random.Random(3)                                  # 무작위 생성기

    def gen():                                              # 무작위 글 만들기
        s = "\n".join(rnd.choice(["a", "b", "c", "", "x y", "\r", "한글"]) for _ in range(rnd.randint(0, 12)))  # 줄들을 이어 붙임
        return s + ("\n" if rnd.random() < 0.3 else "")     # 가끔 끝 줄바꿈 추가
    cases = [["f.txt", gen(), gen(), rnd.choice([0, 2, 3, 4])] for _ in range(1500)]  # 1500개 사례
    proc = subprocess.run(["node", str(tmp_path / "d.mjs")], input=json.dumps(cases), capture_output=True, text=True)  # node 실행
    assert proc.returncode == 0, proc.stderr                # 성공해야 함
    exp = json.loads(proc.stdout)                           # node 결과
    assert [create_two_files_patch(c[0], c[0], c[1], c[2], None, None, context=c[3]) for c in cases] == exp  # 파이썬 결과와 비교


@pytest.mark.skipif(not HAS_NODE, reason="node 또는 .ref/continue/core/node_modules 없음")  # node 가 없으면 건너뜀
def test_node_ignore_library_parity(tmp_path):              # npm ignore 라이브러리와 차등 비교
    (tmp_path / "i.cjs").write_text(f"const ignore = require({json.dumps(str(REF / 'core' / 'node_modules' / 'ignore'))});\n"  # node 스크립트 작성
                                    'const inp = JSON.parse(require("fs").readFileSync(0, "utf8"));\n'  # 입력 읽기
                                    "const ig = ignore().add(inp.pats); const out = {};\n"  # 판정기 만들기
                                    'for (const p of inp.paths) { try { out[p] = ig.ignores(p); } catch (e) { out[p] = "ERR:" + e.message; } }\n'  # 경로마다 판정
                                    "console.log(JSON.stringify(out));\n")  # 결과 출력
    names = ["a.env", ".envrc", "x.env.local", "Config.Yml", "a/b/settings.json", "foo.env/bar", "env", "env/", "a/env/b", "myenv/a",  # 시험할 경로 이름들
             "tmp/secrets/x", "a/tmp/secrets/x", "temp/secrets", "id_rsa", "id_rsa.pub", ".ssh/x", "x.DB", "a..b", ".tmp/x", "keys/",  # 경로 이름 계속
             "../x", "./x", "/abs", "..", ".", "...", "dir/", "a\\b.env", "docker-compose.override.yaml", "x.orig", "src/app.py"]  # 경로 이름 마지막
    pats = DEFAULT_SECURITY_IGNORE_FILETYPES + DEFAULT_SECURITY_IGNORE_DIRS  # 전체 패턴 목록
    proc = subprocess.run(["node", str(tmp_path / "i.cjs")], input=json.dumps({"pats": pats, "paths": names}), capture_output=True, text=True)  # node 실행
    assert proc.returncode == 0, proc.stderr                # 성공해야 함
    exp = json.loads(proc.stdout)                           # node 결과
    got = {}                                                # 파이썬 결과 모음
    for p in names:                                         # 경로마다
        try:                                                # 판정 시도
            got[p] = default_file_and_folder_security_ignores.ignores(p)  # 판정 결과 저장
        except ContinueError as e:                          # 오류면
            got[p] = "ERR:" + str(e)                        # 오류 글을 결과로
    assert got == exp                                       # node 와 같아야 함


@pytest.mark.skipif(not (shutil.which("node") and (CLI / "util" / "truncateOutput.ts").exists()), reason="node 또는 원본 없음")  # 원본이 없으면 건너뜀
def test_node_truncate_parity(tmp_path):                    # truncateOutput.ts 원본과 차등 비교
    (tmp_path / "package.json").write_text('{"type":"module"}')  # ESM 설정 파일
    (tmp_path / "t.ts").write_text((CLI / "util" / "truncateOutput.ts").read_text())  # 원본 TS 복사
    (tmp_path / "r.mjs").write_text('import * as t from "./t.ts"; import fs from "fs";\n'  # node 스크립트 작성
                                    'const cs = JSON.parse(fs.readFileSync(0, "utf8"));\n'  # 입력 읽기
                                    "console.log(JSON.stringify(cs.map(([s, c, l]) => [t.truncateOutputFromStart(s, {maxChars: c, maxLines: l}).output,"  # 세 함수 결과 출력
                                    " t.truncateOutputFromEnd(s, c, 'x').output, t.truncateByLinesAndChars(s, l, c).output])));\n")  # 출력 마무리
    rnd = random.Random(5)                                  # 무작위 생성기
    cases = [["".join(rnd.choice(["a", "bb", "\n", "\n\n", "x" * 300]) for _ in range(rnd.randint(0, 30))),  # 800개 사례
              rnd.randint(1, 2500), rnd.randint(1, 8)] for _ in range(800)]  # 한도 값도 무작위
    proc = subprocess.run(["node", str(tmp_path / "r.mjs")], input=json.dumps(cases), capture_output=True, text=True, cwd=tmp_path)  # node 실행
    assert proc.returncode == 0, proc.stderr                # 성공해야 함
    exp = json.loads(proc.stdout)                           # node 결과
    got = [[truncate_output_from_start(s, {"maxChars": c, "maxLines": l}).output, truncate_output_from_end(s, c, "x").output,  # 파이썬 결과
            truncate_by_lines_and_chars(s, l, c).output] for s, c, l in cases]  # 세 번째 함수 결과
    assert got == exp                                       # node 와 같아야 함


@pytest.mark.skipif(not shutil.which("node"), reason="node 없음")  # node 가 없으면 건너뜀
@pytest.mark.parametrize("plat", ["darwin", "win32"])       # 플랫폼별로 시험
def test_node_path_parity(plat, monkeypatch):               # Node path 이식 차등 비교
    """Node path(posix·win32) 이식이 node 와 같은가 — 무작위 경로 1500개."""
    monkeypatch.setattr(formatters, "PLATFORM_OVERRIDE", plat)  # 플랫폼 강제
    rnd = random.Random(11)                                 # 무작위 생성기
    atoms = ["a", "B", "..", ".", "/", "\\", "x.txt", "C:", "c:\\", ":", "CON:", "\\\\srv\\sh", ""]  # 경로 조각 후보
    ps = ["".join(rnd.choice(atoms) for _ in range(rnd.randint(0, 6))) for _ in range(1500)]  # 무작위 경로 1500개
    mod = "win32" if plat == "win32" else "posix"           # 비교할 path 모듈
    js = (f'const p=require("path").{mod};const a=JSON.parse(require("fs").readFileSync(0,"utf8"));'  # node 스크립트 앞부분
          'console.log(JSON.stringify(a.map((x,i)=>[p.normalize(x),p.dirname(x),p.basename(x),p.join("r",x),p.isAbsolute(x),'  # 결과 계산 일부
          'p.resolve(x),p.relative(x,a[(i+1)%a.length])])))')  # 결과 계산 나머지
    proc = subprocess.run(["node", "-e", js], input=json.dumps(ps), capture_output=True, text=True)  # node 실행
    assert proc.returncode == 0, proc.stderr                # 성공해야 함
    exp = json.loads(proc.stdout)                           # node 결과
    f = formatters                                          # 짧은 이름
    got = [[f.path_normalize(x), f.path_dirname(x), f.path_basename(x), f.path_join("r", x), f.path_is_absolute(x),  # 파이썬 결과 앞부분
            f.path_resolve(x), f.path_relative(x, ps[(i + 1) % len(ps)])] for i, x in enumerate(ps)]  # 파이썬 결과 뒷부분
    assert got == exp                                       # node 와 같아야 함
