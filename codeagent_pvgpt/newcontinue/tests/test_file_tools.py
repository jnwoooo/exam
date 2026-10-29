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
                                 default_file_and_folder_security_ignores, is_security_concern,
                                 throw_if_file_is_security_concern)
from ncn.tools import formatters                             # 실행 OS 바꾸기
from ncn.tools.base import ContinueError, ToolRunContext, openai_tool_definition   # 공용 형식
from ncn.tools.edit import EDIT_TOOL                         # Edit
from ncn.tools.list_files import LIST_TOOL                   # List
from ncn.tools.multi_edit import MULTI_EDIT_TOOL             # MultiEdit
from ncn.tools.read_file import READ_TOOL, mark_file_as_read, read_files_set   # Read
from ncn.tools.truncate import (parse_env_number, truncate_by_lines_and_chars, truncate_lines_by_count,   # 자르기
                                truncate_output_from_end, truncate_output_from_start)
from ncn.tools.write_file import WRITE_TOOL, create_two_files_patch, generate_diff   # Write·diff

REF = Path(__file__).resolve().parent.parent / ".ref" / "continue"   # cn 소스
CLI = REF / "extensions" / "cli" / "src"                     # cn CLI 소스
HAS_NODE = bool(shutil.which("node")) and (REF / "core" / "node_modules").exists()   # 차등 비교 가능?


def run(coro):
    """async 호출을 동기로."""
    return asyncio.run(coro)


async def call(tool, args, ctx=None):
    """루프처럼: preprocess → run(고쳐진 인자로)."""
    pre = await tool.preprocess(args)
    return await tool.run(pre.args, ctx or ToolRunContext("t1"))


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """임시 작업 폴더를 cwd 로, 읽은 파일 기록은 시험마다 비움(cn 시험의 readFilesSet.clear() 와 같음)."""
    real = Path(os.path.realpath(tmp_path))                  # macOS /var → /private/var
    monkeypatch.chdir(real)
    read_files_set.clear()
    yield real
    read_files_set.clear()


# ───────────── 도구 정의(LLM 에 보이는 글)가 cn 원문과 같은가 ─────────────
def _ts_template(src: str, start_marker: str) -> str:
    """TS 의 description: `…` 템플릿 글 뽑기(${…Tool.name} 치환)."""
    i = src.index(start_marker) + len(start_marker)
    j = i
    while True:                                              # 이스케이프 안 된 ` 찾기
        j = src.index("`", j)
        if src[j - 1] != "\\":
            break
        j += 1
    body = src[i:j].replace("\\`", "`")
    return body.replace("${readFileTool.name}", "Read").replace("${editTool.name}", "Edit")


@pytest.mark.skipif(not CLI.exists(), reason=".ref/continue 없음")
def test_descriptions_match_cn_source():
    assert EDIT_TOOL.description == _ts_template((CLI / "tools" / "edit.ts").read_text(), "description: `")
    assert MULTI_EDIT_TOOL.description == _ts_template((CLI / "tools" / "multiEdit.ts").read_text(), "description: `")
    for tool, f in [(READ_TOOL, "readFile.ts"), (WRITE_TOOL, "writeFile.ts"), (LIST_TOOL, "listFiles.ts")]:
        m = re.search(r'description: "([^"]+)"', (CLI / "tools" / f).read_text())
        assert tool.description == m.group(1)


def test_tool_names_and_schema_key_order():
    assert [t.name for t in (READ_TOOL, WRITE_TOOL, EDIT_TOOL, MULTI_EDIT_TOOL, LIST_TOOL)] == ["Read", "Write", "Edit", "MultiEdit", "List"]
    assert list(EDIT_TOOL.parameters["properties"]) == ["file_path", "old_string", "new_string", "replace_all"]
    assert list(EDIT_TOOL.parameters) == ["type", "required", "properties"]
    d = openai_tool_definition(MULTI_EDIT_TOOL)
    assert list(d["function"]["parameters"]["properties"]["edits"]["items"]["properties"]) == ["old_string", "new_string", "replace_all"]
    assert EDIT_TOOL.description.split("\n")[2] == "USAGE: "   # 끝 공백까지 원문 그대로
    assert READ_TOOL.readonly and LIST_TOOL.readonly and not WRITE_TOOL.readonly


# ───────────── Read ─────────────
def test_read_success_marks_realpath_and_strips_dot_slash(ws):
    (ws / "a.txt").write_text("hi\nthere")
    pre = run(READ_TOOL.preprocess({"filepath": "./a.txt"}))
    assert pre.preview[0].content == "Will read a.txt"
    out = run(READ_TOOL.run({"filepath": "./a.txt"}, ToolRunContext("t")))
    assert out == "Content of a.txt:\nhi\nthere"             # 줄 번호 없음
    assert str(ws / "a.txt") in read_files_set               # 실경로로 기록


def test_read_missing_and_security(ws):
    with pytest.raises(ContinueError) as e:
        run(call(READ_TOOL, {"filepath": "nope.txt"}))
    assert str(e.value) == "File does not exist: nope.txt"
    with pytest.raises(ContinueError) as e:
        run(READ_TOOL.preprocess({"filepath": "./.env"}))
    assert e.value.reason == "file_is_security_concern"
    assert str(e.value) == ("Reading or Editing .env is not allowed because it is a security concern. "
                            "Do not attempt to read or edit this file in any way.")
    with pytest.raises(ContinueError) as e:                  # npm ignore 의 RangeError 글이 그대로
        run(READ_TOOL.preprocess({"filepath": "../x.txt"}))
    assert str(e.value) == 'path should be a `path.relative()`d string, but got "../x.txt"'


def test_read_too_large_message_and_parallel(ws, monkeypatch):
    (ws / "big.txt").write_text("x" * 1500 + "\n" + "y")
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "1000")
    with pytest.raises(ContinueError) as e:
        run(call(READ_TOOL, {"filepath": "big.txt"}))
    assert e.value.reason == "file_too_large"
    assert str(e.value) == ("File is too large to read: big.txt (1,502 characters, 2 lines). Maximum allowed: 1,000 characters or 5,000 lines. "
                            "Consider using terminal commands like 'head', 'tail', 'sed', or 'grep' to read targeted parts of the file.")
    assert str(ws / "big.txt") not in read_files_set         # 실패하면 기록 안 함
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "3000")
    with pytest.raises(ContinueError) as e:                  # 병렬 3개 → 1000자
        run(call(READ_TOOL, {"filepath": "big.txt"}, ToolRunContext("t", parallel_tool_call_count=3)))
    assert ("Maximum allowed: 1,000 characters or 1,666 lines. (Note: limit reduced due to 3 parallel tool calls. "
            "Single-tool limit: 3,000 characters or 5,000 lines.) Consider") in str(e.value)


def test_read_counts_utf16_and_keeps_crlf_bom(ws, monkeypatch):
    (ws / "e.txt").write_bytes("\ufeff😀\r\n".encode("utf-8"))
    assert run(call(READ_TOOL, {"filepath": "e.txt"})) == "Content of e.txt:\n\ufeff😀\r\n"   # BOM·CRLF 그대로
    monkeypatch.setenv("CONTINUE_CLI_READ_FILE_MAX_OUTPUT_CHARS", "4")
    with pytest.raises(ContinueError) as e:                  # JS length: BOM1 + 😀2 + \r\n2 = 5
        run(call(READ_TOOL, {"filepath": "e.txt"}))
    assert "(5 characters, 2 lines)" in str(e.value)


def test_read_directory_error(ws):
    (ws / "d").mkdir()
    with pytest.raises(ContinueError) as e:
        run(call(READ_TOOL, {"filepath": "d"}))
    assert str(e.value) == "Error reading file: EISDIR: illegal operation on a directory, read"


# ───────────── Write ─────────────
def test_write_new_and_existing(ws):
    pre = run(WRITE_TOOL.preprocess({"filepath": "sub/dir/n.txt", "content": "l1\n\nl3\nl4\nl5"}))
    assert [p.content for p in pre.preview] == ["New file content:", "l1", " ", "l3", "... (2 more lines)"]
    assert pre.preview[1].padding_left == 2
    assert run(WRITE_TOOL.run(pre.args, ToolRunContext("t"))) == "Successfully created file: sub/dir/n.txt"
    assert (ws / "sub/dir/n.txt").read_bytes() == b"l1\n\nl3\nl4\nl5"
    assert str(ws / "sub/dir/n.txt") not in read_files_set   # Write 는 기록 안 함
    pre = run(WRITE_TOOL.preprocess({"filepath": "sub/dir/n.txt", "content": "l1\nX\nl3\nl4\nl5"}))
    assert pre.preview[0].content == "Preview of changes:" and pre.preview[1].type == "diff"
    assert pre.preview[1].content == ("Index: sub/dir/n.txt\n===================================================================\n"
                                      "--- sub/dir/n.txt\n+++ sub/dir/n.txt\n@@ -1,4 +1,4 @@\n l1\n-\n+X\n l3\n l4\n")   # 문맥 2줄
    out = run(WRITE_TOOL.run(pre.args, ToolRunContext("t")))
    assert out == ("Successfully wrote to file: sub/dir/n.txt\nDiff:\nIndex: sub/dir/n.txt\n"
                   "===================================================================\n--- sub/dir/n.txt\n+++ sub/dir/n.txt\n"
                   "@@ -1,5 +1,5 @@\n l1\n-\n+X\n l3\n l4\n l5\n\\ No newline at end of file\n")   # 문맥 3줄


def test_write_errors(ws):
    with pytest.raises(ContinueError) as e:
        run(WRITE_TOOL.preprocess({"filepath": 3, "content": "x"}))
    assert str(e.value) == "Filepath must be a string"
    (ws / "d").mkdir()
    with pytest.raises(ContinueError) as e:
        run(WRITE_TOOL.run({"filepath": "d", "content": "x"}, ToolRunContext("t")))
    assert e.value.reason == "file_write_error"
    assert str(e.value) == "Error writing to file: EISDIR: illegal operation on a directory, read"   # 기존 내용 읽기에서 먼저 실패(cn 순서)


# ───────────── Edit(cn edit.test.ts) ─────────────
ORIGINAL = "Hello world\nThis is a test file\nGoodbye world"


@pytest.fixture
def edit_file(ws):
    p = ws / "test-edit-file.txt"
    p.write_text(ORIGINAL)
    return p


def test_edit_requires_read(edit_file):
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "Hello world", "new_string": "Hi there"}))
    assert e.value.reason == "edit_tool_file_not_yet_read"
    assert str(e.value) == f"You must use the Read tool to read {edit_file} before editing it."


def test_edit_missing_file_gives_node_lstat_error(ws):
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.preprocess({"file_path": "nope.txt", "old_string": "a", "new_string": "b"}))
    assert str(e.value) == f"ENOENT: no such file or directory, lstat '{ws / 'nope.txt'}'"   # realpathSync 가 먼저 던짐
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.preprocess({"old_string": "a", "new_string": "b"}))
    assert str(e.value) == "file_path is required"


@pytest.mark.parametrize("args,reason", [
    ({"old_string": "Not found", "new_string": "Hi there"}, "find_and_replace_old_string_not_found"),
    ({"old_string": "world", "new_string": "universe", "replace_all": False}, "find_and_replace_multiple_occurrences"),
    ({"old_string": "Hello world", "new_string": "Hello world"}, "find_and_replace_identical_old_and_new_strings"),
])
def test_edit_preprocess_errors(edit_file, args, reason):
    run(call(READ_TOOL, {"filepath": "test-edit-file.txt"}))
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.preprocess({"file_path": str(edit_file), **args}))
    assert e.value.reason == reason


def test_edit_success_relative_path_and_replace_all(edit_file, ws):
    run(call(READ_TOOL, {"filepath": "test-edit-file.txt"}))
    pre = run(EDIT_TOOL.preprocess({"file_path": "test-edit-file.txt", "old_string": "Hello world", "new_string": "Hi there"}))
    assert pre.args == {"resolvedPath": str(edit_file), "newContent": "Hi there\nThis is a test file\nGoodbye world", "oldContent": ORIGINAL}
    assert pre.preview[0].content == "Will make the following changes:"
    out = run(EDIT_TOOL.run(pre.args, ToolRunContext("t")))
    assert out == f"Successfully edited {edit_file}\nDiff:\n" + generate_diff(ORIGINAL, pre.args["newContent"], str(edit_file))
    assert edit_file.read_text() == "Hi there\nThis is a test file\nGoodbye world"
    pre = run(EDIT_TOOL.preprocess({"file_path": str(edit_file), "old_string": "world", "new_string": "universe", "replace_all": True}))
    assert pre.args["newContent"] == "Hi there\nThis is a test file\nGoodbye universe"


def test_edit_security_uses_realpath_parent_and_name(ws):
    (ws / "secrets").mkdir()
    f = ws / "secrets" / "a.txt"
    f.write_text("x")
    mark_file_as_read(str(f))
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.preprocess({"file_path": "secrets/a.txt", "old_string": "x", "new_string": "y"}))
    assert str(e.value).startswith(f"Reading or Editing {f} is not allowed")   # 실경로로 판정·표시


def test_edit_write_failure(ws):
    with pytest.raises(ContinueError) as e:
        run(EDIT_TOOL.run({"resolvedPath": str(ws), "newContent": "n", "oldContent": "o"}, ToolRunContext("t")))
    assert e.value.reason == "file_write_error"
    assert str(e.value) == f"Error: failed to edit {ws}: EISDIR: illegal operation on a directory, open '{ws}'"


# ───────────── MultiEdit(cn multiEdit.test.ts) ─────────────
def test_multi_edit_sequential_atomic(ws):
    f = ws / "m.py"
    f.write_text("a = 1\nb = 2\n")
    run(call(READ_TOOL, {"filepath": "m.py"}))
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "a = 1", "new_string": "a = 10"},
                                                                         {"old_string": "a = 10\nb", "new_string": "c"}]}))
    assert pre.args["newContent"] == "c = 2\n" and pre.args["editCount"] == 2
    assert pre.preview[0].content == f"Will apply 2 edits to {f}:"
    assert run(MULTI_EDIT_TOOL.run(pre.args, ToolRunContext("t"))).startswith(f"Successfully edited {f} with 2 edits\nDiff:\nIndex: {f}\n")
    with pytest.raises(ContinueError):                       # 두 번째 실패 → 파일 그대로
        run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "c", "new_string": "d"},
                                                                       {"old_string": "zzz", "new_string": "q"}]}))
    assert f.read_text() == "c = 2\n"
    pre = run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.py", "edits": [{"old_string": "", "new_string": "# head\n"}]}))
    assert pre.args["newContent"] == "# head\nc = 2\n"       # 빈 old_string = 맨 앞 삽입
    assert pre.preview[0].content == f"Will apply 1 edit to {f}:"


def test_multi_edit_validation_order(ws):
    with pytest.raises(ContinueError) as e:                  # 경로 검증이 먼저
        run(MULTI_EDIT_TOOL.preprocess({"edits": []}))
    assert str(e.value) == "file_path is required"
    (ws / "m.txt").write_text("x")
    run(call(READ_TOOL, {"filepath": "m.txt"}))
    with pytest.raises(ContinueError) as e:
        run(MULTI_EDIT_TOOL.preprocess({"file_path": "m.txt", "edits": []}))
    assert e.value.reason == "multi_edit_edits_array_empty"


# ───────────── List ─────────────
def test_list(ws):
    (ws / "b.txt").write_text("12345")
    (ws / "a_dir").mkdir()
    (ws / "C.txt").write_text("")                         # 대문자가 소문자보다 앞(바이트 순)
    pre = run(LIST_TOOL.preprocess({"dirpath": "."}))
    assert pre.args == {"dirpath": str(ws)}
    assert pre.preview[0].content == f"Will list files in: {ws}"   # 같은 폴더면 상대경로가 빈 글 → 원래 값
    out = run(LIST_TOOL.run(pre.args, ToolRunContext("t")))
    assert out == f"Files in {ws}:\nC.txt (file, 0 bytes)\na_dir (directory)\nb.txt (file, 5 bytes)"   # 바이트 순
    with pytest.raises(ContinueError) as e:
        run(LIST_TOOL.preprocess({"dirpath": "nope"}))
    assert str(e.value) == f"Directory does not exist: {ws / 'nope'}"
    with pytest.raises(ContinueError) as e:
        run(LIST_TOOL.preprocess({"dirpath": "b.txt"}))
    assert str(e.value) == f"Error: Path is not a directory: {ws / 'b.txt'}"
    sub = ws / "a_dir"
    pre = run(LIST_TOOL.preprocess({"dirpath": "a_dir/"}))
    assert pre.args["dirpath"] == str(sub) and pre.preview[0].content == "Will list files in: a_dir"
    assert run(LIST_TOOL.run(pre.args, ToolRunContext("t"))) == f"Files in {sub}:\n"


def test_list_broken_symlink_fails_whole(ws):
    os.symlink(ws / "missing", ws / "link")
    with pytest.raises(ContinueError) as e:
        run(LIST_TOOL.run({"dirpath": str(ws)}, ToolRunContext("t")))
    assert str(e.value) == f"Error listing files: ENOENT: no such file or directory, stat '{ws / 'link'}'"


# ───────────── 민감 파일 판정 ─────────────
@pytest.mark.parametrize("path,expected", [
    (".env", True), ("a.env", True), ("src/config.yaml", True), ("CONFIG.JSON", True), ("appsettings.Dev.json", True),
    ("secrets/sub/a.txt", True), ("foo.env/bar.txt", True), ("env", False), ("env/a", True), ("tmp/secrets/x", True),
    ("README.md", False), ("src/main.py", False),
    ("/home/u/.ssh/id_rsa", True), ("/proj/secrets/sub/a.txt", False),   # 절대경로는 "부모/이름" 두 조각
    ("/home/u/project/config.json", True), ("/", False), ("file:///home/u/.ssh/known_hosts", True),
])
def test_is_security_concern(path, expected):
    assert is_security_concern(path) is expected


def test_security_win32_branch(monkeypatch):
    monkeypatch.setattr(formatters, "PLATFORM_OVERRIDE", "win32")
    assert is_security_concern("C:\\Users\\u\\.ssh\\id_rsa") is True   # win32 dirname/basename
    assert is_security_concern("src\\secrets\\a.txt") is True          # '\' → '/' 변환 후 판정
    assert is_security_concern("C:\\proj\\src\\main.py") is False
    with pytest.raises(ContinueError) as e:
        throw_if_file_is_security_concern("..\\x")
    assert str(e.value) == 'path should be a `path.relative()`d string, but got "..\\x"'


# ───────────── truncateOutput.test.ts ─────────────
def test_truncate_from_start():
    assert truncate_output_from_start("", {"maxChars": 10, "maxLines": 2}).output == ""
    lines = "\n".join(f"line{i}" for i in range(10))
    r = truncate_output_from_start(lines, {"maxChars": 1000, "maxLines": 3})
    assert r.was_truncated and r.output == "(previous 7 lines truncated)\n\nline7\nline8\nline9"
    r = truncate_output_from_start("a" * 50, {"maxChars": 10, "maxLines": 100})
    assert r.output == "(previous 40 characters truncated)\n\n" + "a" * 10
    r = truncate_output_from_start("aaaa\nbbbb\ncccc", {"maxChars": 7, "maxLines": 100})
    assert r.output == "(previous 10 characters truncated)\n\ncccc"   # 줄 경계로 맞춤
    r = truncate_output_from_start("x" * 20 + "\n" + "y" * 20 + "\n" + "z" * 20, {"maxChars": 25, "maxLines": 2})
    assert r.output == "(previous output truncated: 1 lines and 21 characters removed)\n\n" + "z" * 20


def test_truncate_from_end_and_lines():
    assert truncate_output_from_end("short", 10).was_truncated is False
    r = truncate_output_from_end("a" * 20, 5, "diff output")
    assert r.output == "aaaaa\n\n(15 characters of diff output truncated)"
    assert truncate_output_from_end("line1\nline2\nline3", 14).output == "line1\nline2\n\n(6 characters truncated)"
    assert truncate_lines_by_count("a\nb\nc\nd", 2, "file").output == "a\nb\n\n(2 lines of file truncated)"
    assert truncate_by_lines_and_chars("aaaa\nbbbb\ncccc", 2, 6).output.endswith("characters truncated)")


@pytest.mark.parametrize("val,expected", [(None, 7), ("", 7), ("abc", 7), ("0", 7), ("-5", 7), ("42", 42), ("3.9", 3), (" 12x", 12)])
def test_parse_env_number(val, expected):
    assert parse_env_number(val, 7) == expected


# ───────────── Node 차등 비교: npm diff · npm ignore · truncateOutput.ts 원본 ─────────────
@pytest.mark.skipif(not HAS_NODE, reason="node 또는 .ref/continue/core/node_modules 없음")
def test_node_diff_library_parity(tmp_path):
    lib = REF / "core" / "node_modules" / "diff" / "libesm" / "index.js"
    (tmp_path / "d.mjs").write_text(f'import {{ createTwoFilesPatch }} from {json.dumps(lib.as_uri())};\nimport fs from "fs";\n'
                                    'const cs = JSON.parse(fs.readFileSync(0, "utf8"));\n'
                                    "console.log(JSON.stringify(cs.map(c => createTwoFilesPatch(c[0], c[0], c[1], c[2], undefined, undefined, { context: c[3] }))));\n")
    rnd = random.Random(3)

    def gen():
        s = "\n".join(rnd.choice(["a", "b", "c", "", "x y", "\r", "한글"]) for _ in range(rnd.randint(0, 12)))
        return s + ("\n" if rnd.random() < 0.3 else "")
    cases = [["f.txt", gen(), gen(), rnd.choice([0, 2, 3, 4])] for _ in range(1500)]
    proc = subprocess.run(["node", str(tmp_path / "d.mjs")], input=json.dumps(cases), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    exp = json.loads(proc.stdout)
    assert [create_two_files_patch(c[0], c[0], c[1], c[2], None, None, context=c[3]) for c in cases] == exp


@pytest.mark.skipif(not HAS_NODE, reason="node 또는 .ref/continue/core/node_modules 없음")
def test_node_ignore_library_parity(tmp_path):
    (tmp_path / "i.cjs").write_text(f"const ignore = require({json.dumps(str(REF / 'core' / 'node_modules' / 'ignore'))});\n"
                                    'const inp = JSON.parse(require("fs").readFileSync(0, "utf8"));\n'
                                    "const ig = ignore().add(inp.pats); const out = {};\n"
                                    'for (const p of inp.paths) { try { out[p] = ig.ignores(p); } catch (e) { out[p] = "ERR:" + e.message; } }\n'
                                    "console.log(JSON.stringify(out));\n")
    names = ["a.env", ".envrc", "x.env.local", "Config.Yml", "a/b/settings.json", "foo.env/bar", "env", "env/", "a/env/b", "myenv/a",
             "tmp/secrets/x", "a/tmp/secrets/x", "temp/secrets", "id_rsa", "id_rsa.pub", ".ssh/x", "x.DB", "a..b", ".tmp/x", "keys/",
             "../x", "./x", "/abs", "..", ".", "...", "dir/", "a\\b.env", "docker-compose.override.yaml", "x.orig", "src/app.py"]
    pats = DEFAULT_SECURITY_IGNORE_FILETYPES + DEFAULT_SECURITY_IGNORE_DIRS
    proc = subprocess.run(["node", str(tmp_path / "i.cjs")], input=json.dumps({"pats": pats, "paths": names}), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    exp = json.loads(proc.stdout)
    got = {}
    for p in names:
        try:
            got[p] = default_file_and_folder_security_ignores.ignores(p)
        except ContinueError as e:
            got[p] = "ERR:" + str(e)
    assert got == exp


@pytest.mark.skipif(not (shutil.which("node") and (CLI / "util" / "truncateOutput.ts").exists()), reason="node 또는 원본 없음")
def test_node_truncate_parity(tmp_path):
    (tmp_path / "package.json").write_text('{"type":"module"}')
    (tmp_path / "t.ts").write_text((CLI / "util" / "truncateOutput.ts").read_text())
    (tmp_path / "r.mjs").write_text('import * as t from "./t.ts"; import fs from "fs";\n'
                                    'const cs = JSON.parse(fs.readFileSync(0, "utf8"));\n'
                                    "console.log(JSON.stringify(cs.map(([s, c, l]) => [t.truncateOutputFromStart(s, {maxChars: c, maxLines: l}).output,"
                                    " t.truncateOutputFromEnd(s, c, 'x').output, t.truncateByLinesAndChars(s, l, c).output])));\n")
    rnd = random.Random(5)
    cases = [["".join(rnd.choice(["a", "bb", "\n", "\n\n", "x" * 300]) for _ in range(rnd.randint(0, 30))),
              rnd.randint(1, 2500), rnd.randint(1, 8)] for _ in range(800)]
    proc = subprocess.run(["node", str(tmp_path / "r.mjs")], input=json.dumps(cases), capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    exp = json.loads(proc.stdout)
    got = [[truncate_output_from_start(s, {"maxChars": c, "maxLines": l}).output, truncate_output_from_end(s, c, "x").output,
            truncate_by_lines_and_chars(s, l, c).output] for s, c, l in cases]
    assert got == exp


@pytest.mark.skipif(not shutil.which("node"), reason="node 없음")
@pytest.mark.parametrize("plat", ["darwin", "win32"])
def test_node_path_parity(plat, monkeypatch):
    """Node path(posix·win32) 이식이 node 와 같은가 — 무작위 경로 1500개."""
    monkeypatch.setattr(formatters, "PLATFORM_OVERRIDE", plat)
    rnd = random.Random(11)
    atoms = ["a", "B", "..", ".", "/", "\\", "x.txt", "C:", "c:\\", ":", "CON:", "\\\\srv\\sh", ""]
    ps = ["".join(rnd.choice(atoms) for _ in range(rnd.randint(0, 6))) for _ in range(1500)]
    mod = "win32" if plat == "win32" else "posix"
    js = (f'const p=require("path").{mod};const a=JSON.parse(require("fs").readFileSync(0,"utf8"));'
          'console.log(JSON.stringify(a.map((x,i)=>[p.normalize(x),p.dirname(x),p.basename(x),p.join("r",x),p.isAbsolute(x),'
          'p.resolve(x),p.relative(x,a[(i+1)%a.length])])))')
    proc = subprocess.run(["node", "-e", js], input=json.dumps(ps), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    exp = json.loads(proc.stdout)
    f = formatters
    got = [[f.path_normalize(x), f.path_dirname(x), f.path_basename(x), f.path_join("r", x), f.path_is_absolute(x),
            f.path_resolve(x), f.path_relative(x, ps[(i + 1) % len(ps)])] for i, x in enumerate(ps)]
    assert got == exp
