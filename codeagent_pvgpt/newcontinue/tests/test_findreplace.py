# tests/test_findreplace.py — 찾아 바꾸기(ncn/tools/findreplace.py) 시험. cn core/edit/searchAndReplace/*.vitest.ts 의 대표 사례 + Node 차등 비교
import json                                                  # Node 결과 해석
import random                                                # 무작위 입력
import shutil                                                # node 찾기
import subprocess                                            # node 실행
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from ncn.tools.base import ContinueError                     # 오류 형식
from ncn.tools import findreplace as fr                      # 시험 대상

REF = Path(__file__).resolve().parent.parent / ".ref" / "continue"   # cn 소스(없으면 차등 시험 생략)
SR = REF / "core" / "edit" / "searchAndReplace"              # 찾아 바꾸기 원본 폴더


def m(start, end, name):
    """기대 일치 결과."""
    return fr.SearchMatchResult(start, end, name)


# ───────────── findSearchMatch.vitest.ts ─────────────
@pytest.mark.parametrize("content,search,expected", [
    ("some file content", "", m(0, 0, "emptySearch")),
    ("some file content", "   \n\t  ", m(0, 0, "emptySearch")),
    ("hello world", "hello", m(0, 5, "exactMatch")),
    ("hello world test", "world", m(6, 11, "exactMatch")),
    ("line1\nline2\nline3", "line2\nline3", m(6, 17, "exactMatch")),
    ('function test() { return "hello"; }', '{ return "hello"; }', m(16, 35, "exactMatch")),
    ("test test test", "test", m(0, 4, "exactMatch")),
    ("Hello World", "hello", m(0, 5, "caseInsensitiveMatch")),
    ("hello world", "  hello  ", m(0, 5, "trimmedMatch")),
    ("function test() {}", "\t\nfunction test()\n\t", m(0, 15, "trimmedMatch")),
    ("const x = 5;", "   \n\t  const x = 5;  \t\n  ", m(0, 12, "trimmedMatch")),
    ("function test() {\n  return true;\n}", "function test(){return true;}", m(0, 34, "whitespaceIgnoredMatch")),
    ("if (condition) {\n    doSomething();\n}", "if(condition){doSomething();}", m(0, 37, "whitespaceIgnoredMatch")),
    ("function test() {\n\treturn 42;\n}", "function test() {\n    return 42;\n}", m(0, 31, "whitespaceIgnoredMatch")),
    ("before\nfunction test() {\n  return true;\n}\nafter", "function test(){return true;}", m(7, 41, "whitespaceIgnoredMatch")),
    ("some content", "   \t\n   ", m(0, 0, "emptySearch")),
    ('<!DOCTYPE html>\r\n<html>\r\n  <body>\r\n    <div id="app">\r\n      <p>Hello & goodbye</p>\r\n    </div>\r\n  </body>\r\n</html>',
     '<divid="app"><p>Hello&goodbye</p></div>', m(39, 95, "whitespaceIgnoredMatch")),
    # JS 공백 전부(\f \v \u0020 \u00A0 \u2000 \u2009)를 공백으로 봄
    ("function\fprocess\vdata(input)\u0020{\u00A0\n\treturn\u2000input.trim();\u2009\n}",
     "functionprocessdata(input){returninput.trim();}", m(0, 56, "whitespaceIgnoredMatch")),
])
def test_find_search_match(content, search, expected):
    assert fr.find_search_match(content, search) == expected


def test_find_search_match_none():
    assert fr.find_search_match("hello world", "xyz") is None   # 못 찾음


def test_python_only_whitespace_is_not_js_whitespace():
    # \x1c 는 Python 에선 공백(isspace)이지만 JS \s 는 아님 → trimmedMatch 로 안 맞아야 함
    assert fr.find_search_match("abc", "\x1cabc") is None
    # \ufeff 는 JS 에선 공백 → trim 대상
    assert fr.find_search_match("abc", "\ufeffabc") == m(0, 3, "trimmedMatch")


def test_find_search_matches_all_and_progress():
    assert [x.start_index for x in fr.find_search_matches("aaaa", "aa")] == [0, 2]   # 겹치지 않게
    assert len(fr.find_search_matches("test testing tested test", "test")) == 4
    assert fr.find_search_matches("abc", " ") == [m(0, 0, "emptySearch")]


# ───────────── executeFindAndReplace.vitest.ts ─────────────
@pytest.mark.parametrize("content,old,new,rep,expected", [
    ("Hello world\nThis is a test file\nGoodbye", "Hello", "Hi", False, "Hi world\nThis is a test file\nGoodbye"),
    ("Hello world\nHello again", "Hello world", "Hi world", False, "Hi world\nHello again"),
    ("Hello world\nHello again\nHello there", "Hello", "Hi", True, "Hi world\nHi again\nHi there"),
    ("Hello world", "Hello ", "", False, "world"),
    ("", "", "Hello world", False, "Hello world"),
    ("function test() {\n    const value = 'old';\n    return value;\n}", "    const value = 'old';", "    const value = 'new';", False,
     "function test() {\n    const value = 'new';\n    return value;\n}"),
    ("line1\nline2\nline3", "line1\nline2", "newline1\nnewline2", False, "newline1\nnewline2\nline3"),
    ("const regex = /[a-z]+/g;", "/[a-z]+/g", "/[A-Z]+/g", False, "const regex = /[A-Z]+/g;"),
    ('const text = "Hello $world"', '"Hello $world"', '"Hi $universe"', False, 'const text = "Hi $universe"'),
    ('const path = "C:\\Users\\test"', "C:\\Users\\test", "D:\\Users\\test", False, 'const path = "D:\\Users\\test"'),
    ("test testing tested test", "test", "exam", True, "exam examing examed exam"),
    ("remove this remove that remove everything", "remove ", "", True, "this that everything"),
    ("aaaa", "aa", "b", True, "bb"),
    # 들여쓰기 보정
    ("def foo():\n    x = 1\n    y = 2\n    return x + y\n", "x = 1\ny = 2", "x = 10\ny = 20", False,
     "def foo():\n    x = 10\n    y = 20\n    return x + y\n"),
    ("class Foo:\n    def bar(self):\n        if True:\n            x = 1\n", "if True:\n    x = 1", "if True:\n    x = 1\n    y = 2", False,
     "class Foo:\n    def bar(self):\n        if True:\n            x = 1\n            y = 2\n"),
    ("function foo() {\n\tconst x = 1;\n}\n", "  const x = 1;", "  const x = 2;\n  const y = 3;", False,
     "function foo() {\n\tconst x = 2;\n\tconst y = 3;\n}\n"),
    ("    x = 1\n", "    x = 1", "    x = 2", False, "    x = 2\n"),
    ("def outer():\n    if  True:\n        return  42\n", "if True:\n    return 42", "if True:\n    return 99", False,
     "def outer():\n    if True:\n        return 99\n"),
    ("start" + "a" * 10000 + "end", "a" * 10000, "short", False, "startshortend"),
])
def test_execute_find_and_replace(content, old, new, rep, expected):
    assert fr.execute_find_and_replace(content, old, new, rep) == expected


def test_execute_errors_have_cn_messages():
    with pytest.raises(ContinueError) as e:                  # 못 찾음
        fr.execute_find_and_replace("Hello world", "xyz", "abc", False, 2)
    assert e.value.reason == "find_and_replace_old_string_not_found"
    assert str(e.value) == 'Edit at index 2: string not found in file: "xyz"'
    with pytest.raises(ContinueError) as e:                  # 여러 곳
        fr.execute_find_and_replace("test test test", "test", "exam", False, 1)
    assert e.value.reason == "find_and_replace_multiple_occurrences"
    assert str(e.value) == ('Edit at index 1: String "test" appears 3 times in the file. Either provide a more specific string '
                            "with surrounding context to make it unique, or use replace_all=true to replace all occurrences.")


def test_emoji_whitespace_ignored_replacement():
    content = 'const message = {\n  greeting: "Hello 👋",\n  emoji: "🚀",\n  unicode: "café",\n  symbol: "∑"\n};'
    out = fr.execute_find_and_replace(content, 'emoji:"🚀",unicode:"café",symbol:"∑"', "X", False)
    assert out == 'const message = {\n  greeting: "Hello 👋",\n  X\n};'   # 위치는 UTF-16 과 달라도 결과 글은 같음


# ───────────── findAndReplaceUtils / multiEditValidation ─────────────
def test_validate_single_edit():
    assert fr.validate_single_edit("a", "b") == ("a", "b", None)   # replace_all 없음
    assert fr.validate_single_edit("a", "b", True) == ("a", "b", True)
    cases = [((None, "b"), "string old_string is required", "find_and_replace_missing_old_string"),
             (("a", 1), "string new_string is required", "find_and_replace_missing_new_string"),
             (("a", "a"), "old_string and new_string must be different", "find_and_replace_identical_old_and_new_strings"),
             (("a", "b", "true"), "replace_all must be a valid boolean", "find_and_replace_invalid_replace_all"),
             (("a", "b", None), "replace_all must be a valid boolean", "find_and_replace_invalid_replace_all")]   # JSON null 도 거절(JS)
    for args, msg, reason in cases:
        with pytest.raises(ContinueError) as e:
            fr.validate_single_edit(*args)
        assert str(e.value) == msg and e.value.reason == reason
    with pytest.raises(ContinueError) as e:                  # 위치 앞머리
        fr.validate_single_edit("a", "a", fr.MISSING, 3)
    assert str(e.value) == "edit at index 3: old_string and new_string must be different"


def test_validate_multi_edit():
    for args, msg in [({}, "invalid multi-edit args"), ({"edits": "x"}, "edits array is required"),
                      ({"edits": []}, "edits array must contain at least one edit"),
                      ({"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "", "new_string": "c"}]},
                       "Edit at index 1: old_string cannot be empty. Only the first edit can have an empty old_string for insertion at the beginning of the file.")]:
        with pytest.raises(ContinueError) as e:
            fr.validate_multi_edit(args)
        assert str(e.value) == msg


def test_execute_multi_find_and_replace_sequential():
    out = fr.execute_multi_find_and_replace("a b c", [{"old_string": "a", "new_string": "x"}, {"old_string": "x b", "new_string": "y"}])
    assert out == "y c"                                      # 두 번째는 첫 결과에 적용
    with pytest.raises(ContinueError) as e:
        fr.execute_multi_find_and_replace("a b c", [{"old_string": "a", "new_string": "x"}, {"old_string": "zz", "new_string": "y"}])
    assert str(e.value) == 'Edit at index 1: string not found in file: "zz"'


# ───────────── Node 차등 비교(cn 원본 TS 를 node 로 직접 실행) ─────────────
NODE_RUNNER = r"""
import { findSearchMatches } from "./findSearchMatch.ts";
import { executeFindAndReplace } from "./performReplace.ts";
import fs from "fs";
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map(([c, o, n, r]) => {
  let res; try { res = { ok: executeFindAndReplace(c, o, n, r, 0) }; } catch (e) { res = { err: e.message, reason: e.reason }; }
  res.count = findSearchMatches(c, o).length;
  return res;
});
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node") or not SR.exists(), reason="node 또는 .ref/continue 없음")
def test_node_differential(tmp_path):
    """무작위 입력 1500개를 cn 원본(node)과 ncn 에 넣어 결과·오류 글이 같은지."""
    (tmp_path / "package.json").write_text('{"type":"module"}')   # ESM
    (tmp_path / "findSearchMatch.ts").write_text((SR / "findSearchMatch.ts").read_text())
    src = (SR / "performReplace.ts").read_text()             # import 경로를 실행 가능하게 고침(enum 은 node 가 못 읽어 대체)
    src = src.replace('import { EditOperation } from "../../tools/definitions/multiEdit";', "")
    src = src.replace('import { ContinueError, ContinueErrorReason } from "../../util/errors";',
                      "class ContinueError extends Error { constructor(r, m) { super(m); this.reason = r; } }\n"
                      "const ContinueErrorReason = { FindAndReplaceOldStringNotFound: 'find_and_replace_old_string_not_found', "
                      "FindAndReplaceMultipleOccurrences: 'find_and_replace_multiple_occurrences' };")
    src = src.replace('import { SearchMatchResult, findSearchMatches } from "./findSearchMatch";',
                      'import { findSearchMatches } from "./findSearchMatch.ts";')   # 타입 이름은 실행 때 없음
    (tmp_path / "performReplace.ts").write_text(src)
    (tmp_path / "run.mjs").write_text(NODE_RUNNER)
    rnd = random.Random(7)                                   # 결정적 무작위
    atoms = ["a", "b", "A", " ", "  ", "\t", "\n", "\r\n", "x = 1", "    ", "\u00a0", "\x1c", "é", "İ"]

    def gen(k):                                              # 조각을 이어 무작위 글
        return "".join(rnd.choice(atoms) for _ in range(rnd.randint(0, k)))
    cases = [[gen(14), gen(4), gen(4), rnd.random() < 0.5] for _ in range(1500)]
    proc = subprocess.run(["node", str(tmp_path / "run.mjs")], input=json.dumps(cases), capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    expected = json.loads(proc.stdout)
    bad = []
    for (c, o, n, r), exp in zip(cases, expected):
        try:
            got = {"ok": fr.execute_find_and_replace(c, o, n, r, 0)}
        except ContinueError as e:
            got = {"err": str(e), "reason": e.reason}
        got["count"] = len(fr.find_search_matches(c, o))
        if got != exp:
            bad.append((c, o, n, r, got, exp))
    assert bad == []
