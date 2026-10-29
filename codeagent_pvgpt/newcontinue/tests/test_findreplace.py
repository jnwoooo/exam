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


def m(start, end, name):                                    # 기대 일치 결과를 만드는 도우미
    """기대 일치 결과."""
    return fr.SearchMatchResult(start, end, name)           # 일치 객체 생성


# ───────────── findSearchMatch.vitest.ts ─────────────
@pytest.mark.parametrize("content,search,expected", [       # 찾기 사례 표(내용, 찾을 글, 기대 결과)
    ("some file content", "", m(0, 0, "emptySearch")),      # 빈 찾기
    ("some file content", "   \n\t  ", m(0, 0, "emptySearch")),  # 공백뿐인 찾기
    ("hello world", "hello", m(0, 5, "exactMatch")),        # 정확히 일치
    ("hello world test", "world", m(6, 11, "exactMatch")),  # 정확히 일치(가운데)
    ("line1\nline2\nline3", "line2\nline3", m(6, 17, "exactMatch")),  # 여러 줄 정확히 일치
    ('function test() { return "hello"; }', '{ return "hello"; }', m(16, 35, "exactMatch")),  # 특수 글자 포함 정확히 일치
    ("test test test", "test", m(0, 4, "exactMatch")),      # 처음 일치만
    ("Hello World", "hello", m(0, 5, "caseInsensitiveMatch")),  # 대소문자 무시 일치
    ("hello world", "  hello  ", m(0, 5, "trimmedMatch")),  # 앞뒤 공백 제거 일치
    ("function test() {}", "\t\nfunction test()\n\t", m(0, 15, "trimmedMatch")),  # 탭·줄바꿈 제거 일치
    ("const x = 5;", "   \n\t  const x = 5;  \t\n  ", m(0, 12, "trimmedMatch")),  # 많은 공백 제거 일치
    ("function test() {\n  return true;\n}", "function test(){return true;}", m(0, 34, "whitespaceIgnoredMatch")),  # 공백 무시 일치
    ("if (condition) {\n    doSomething();\n}", "if(condition){doSomething();}", m(0, 37, "whitespaceIgnoredMatch")),  # 공백 무시 일치(조건문)
    ("function test() {\n\treturn 42;\n}", "function test() {\n    return 42;\n}", m(0, 31, "whitespaceIgnoredMatch")),  # 탭과 공백 차이 무시
    ("before\nfunction test() {\n  return true;\n}\nafter", "function test(){return true;}", m(7, 41, "whitespaceIgnoredMatch")),  # 앞뒤 글이 있는 공백 무시 일치
    ("some content", "   \t\n   ", m(0, 0, "emptySearch")),  # 공백뿐인 찾기
    ('<!DOCTYPE html>\r\n<html>\r\n  <body>\r\n    <div id="app">\r\n      <p>Hello & goodbye</p>\r\n    </div>\r\n  </body>\r\n</html>',  # CRLF 가 든 HTML 사례
     '<divid="app"><p>Hello&goodbye</p></div>', m(39, 95, "whitespaceIgnoredMatch")),  # 공백 무시 일치 기대 위치
    # JS 공백 전부(\f \v \u0020 \u00A0 \u2000 \u2009)를 공백으로 봄
    ("function\fprocess\vdata(input)\u0020{\u00A0\n\treturn\u2000input.trim();\u2009\n}",  # JS 공백 전부가 든 사례
     "functionprocessdata(input){returninput.trim();}", m(0, 56, "whitespaceIgnoredMatch")),  # 공백 무시 일치 기대 위치
])                                                          # 사례 표 닫기
def test_find_search_match(content, search, expected):      # 첫 일치 검사
    assert fr.find_search_match(content, search) == expected  # 기대 결과와 같아야 함


def test_find_search_match_none():                          # 못 찾는 경우 시험
    assert fr.find_search_match("hello world", "xyz") is None   # 못 찾음


def test_python_only_whitespace_is_not_js_whitespace():     # 파이썬만의 공백은 JS 공백이 아님 시험
    # \x1c 는 Python 에선 공백(isspace)이지만 JS \s 는 아님 → trimmedMatch 로 안 맞아야 함
    assert fr.find_search_match("abc", "\x1cabc") is None   # \\x1c 가 붙은 찾기는 못 찾아야 함
    # \ufeff 는 JS 에선 공백 → trim 대상
    assert fr.find_search_match("abc", "\ufeffabc") == m(0, 3, "trimmedMatch")  # \\ufeff 는 trim 대상이라 trimmedMatch


def test_find_search_matches_all_and_progress():            # 모든 일치와 진행 시험
    assert [x.start_index for x in fr.find_search_matches("aaaa", "aa")] == [0, 2]   # 겹치지 않게
    assert len(fr.find_search_matches("test testing tested test", "test")) == 4  # 겹치지 않는 일치 개수
    assert fr.find_search_matches("abc", " ") == [m(0, 0, "emptySearch")]  # 공백 찾기는 빈 찾기 하나


# ───────────── executeFindAndReplace.vitest.ts ─────────────
@pytest.mark.parametrize("content,old,new,rep,expected", [  # 찾아 바꾸기 사례 표
    ("Hello world\nThis is a test file\nGoodbye", "Hello", "Hi", False, "Hi world\nThis is a test file\nGoodbye"),  # 첫 단어 하나만 바꿈
    ("Hello world\nHello again", "Hello world", "Hi world", False, "Hi world\nHello again"),  # 긴 글 하나만 바꿈
    ("Hello world\nHello again\nHello there", "Hello", "Hi", True, "Hi world\nHi again\nHi there"),  # 전부 바꿈
    ("Hello world", "Hello ", "", False, "world"),          # 공백 포함 삭제
    ("", "", "Hello world", False, "Hello world"),          # 빈 파일에 삽입
    ("function test() {\n    const value = 'old';\n    return value;\n}", "    const value = 'old';", "    const value = 'new';", False,  # 들여쓰기가 든 한 줄 바꿈
     "function test() {\n    const value = 'new';\n    return value;\n}"),  # 기대 결과
    ("line1\nline2\nline3", "line1\nline2", "newline1\nnewline2", False, "newline1\nnewline2\nline3"),  # 여러 줄 바꿈
    ("const regex = /[a-z]+/g;", "/[a-z]+/g", "/[A-Z]+/g", False, "const regex = /[A-Z]+/g;"),  # 정규식 글자가 든 사례
    ('const text = "Hello $world"', '"Hello $world"', '"Hi $universe"', False, 'const text = "Hi $universe"'),  # 달러 기호가 든 사례
    ('const path = "C:\\Users\\test"', "C:\\Users\\test", "D:\\Users\\test", False, 'const path = "D:\\Users\\test"'),  # 역슬래시가 든 사례
    ("test testing tested test", "test", "exam", True, "exam examing examed exam"),  # 전부 바꿈(겹침 없음)
    ("remove this remove that remove everything", "remove ", "", True, "this that everything"),  # 전부 삭제
    ("aaaa", "aa", "b", True, "bb"),                        # 겹치지 않게 바꿈
    # 들여쓰기 보정
    ("def foo():\n    x = 1\n    y = 2\n    return x + y\n", "x = 1\ny = 2", "x = 10\ny = 20", False,  # 파이썬 들여쓰기 보정
     "def foo():\n    x = 10\n    y = 20\n    return x + y\n"),  # 기대 결과
    ("class Foo:\n    def bar(self):\n        if True:\n            x = 1\n", "if True:\n    x = 1", "if True:\n    x = 1\n    y = 2", False,  # 중첩 들여쓰기 보정
     "class Foo:\n    def bar(self):\n        if True:\n            x = 1\n            y = 2\n"),  # 기대 결과
    ("function foo() {\n\tconst x = 1;\n}\n", "  const x = 1;", "  const x = 2;\n  const y = 3;", False,  # 탭과 공백 혼용 보정
     "function foo() {\n\tconst x = 2;\n\tconst y = 3;\n}\n"),  # 기대 결과
    ("    x = 1\n", "    x = 1", "    x = 2", False, "    x = 2\n"),  # 들여쓰기 같은 경우
    ("def outer():\n    if  True:\n        return  42\n", "if True:\n    return 42", "if True:\n    return 99", False,  # 공백 무시로 찾은 경우
     "def outer():\n    if True:\n        return 99\n"),    # 기대 결과
    ("start" + "a" * 10000 + "end", "a" * 10000, "short", False, "startshortend"),  # 아주 긴 글 바꾸기
])                                                          # 사례 표 닫기
def test_execute_find_and_replace(content, old, new, rep, expected):  # 찾아 바꾸기 시험
    assert fr.execute_find_and_replace(content, old, new, rep) == expected  # 기대 결과와 같아야 함


def test_execute_errors_have_cn_messages():                 # 오류 메시지가 cn 과 같은지 시험
    with pytest.raises(ContinueError) as e:                  # 못 찾음
        fr.execute_find_and_replace("Hello world", "xyz", "abc", False, 2)  # 못 찾는 경우 실행
    assert e.value.reason == "find_and_replace_old_string_not_found"  # 이유 코드 확인
    assert str(e.value) == 'Edit at index 2: string not found in file: "xyz"'  # 오류 글 확인
    with pytest.raises(ContinueError) as e:                  # 여러 곳
        fr.execute_find_and_replace("test test test", "test", "exam", False, 1)  # 여러 곳인 경우 실행
    assert e.value.reason == "find_and_replace_multiple_occurrences"  # 이유 코드 확인
    assert str(e.value) == ('Edit at index 1: String "test" appears 3 times in the file. Either provide a more specific string '  # 오류 글 앞부분
                            "with surrounding context to make it unique, or use replace_all=true to replace all occurrences.")  # 오류 글 뒷부분


def test_emoji_whitespace_ignored_replacement():            # 이모지 포함 공백 무시 바꾸기 시험
    content = 'const message = {\n  greeting: "Hello 👋",\n  emoji: "🚀",\n  unicode: "café",\n  symbol: "∑"\n};'  # 이모지·유니코드가 든 내용
    out = fr.execute_find_and_replace(content, 'emoji:"🚀",unicode:"café",symbol:"∑"', "X", False)  # 공백 무시로 바꿈
    assert out == 'const message = {\n  greeting: "Hello 👋",\n  X\n};'   # 위치는 UTF-16 과 달라도 결과 글은 같음


# ───────────── findAndReplaceUtils / multiEditValidation ─────────────
def test_validate_single_edit():                            # 단일 편집 검증 시험
    assert fr.validate_single_edit("a", "b") == ("a", "b", None)   # replace_all 없음
    assert fr.validate_single_edit("a", "b", True) == ("a", "b", True)  # replace_all 이 있는 경우
    cases = [((None, "b"), "string old_string is required", "find_and_replace_missing_old_string"),  # 오류 사례 표(인자, 메시지, 이유)
             (("a", 1), "string new_string is required", "find_and_replace_missing_new_string"),  # new_string 이 없음
             (("a", "a"), "old_string and new_string must be different", "find_and_replace_identical_old_and_new_strings"),  # old 와 new 동일
             (("a", "b", "true"), "replace_all must be a valid boolean", "find_and_replace_invalid_replace_all"),  # replace_all 이 불리언 아님
             (("a", "b", None), "replace_all must be a valid boolean", "find_and_replace_invalid_replace_all")]   # JSON null 도 거절(JS)
    for args, msg, reason in cases:                         # 사례마다
        with pytest.raises(ContinueError) as e:             # 오류 기대
            fr.validate_single_edit(*args)                  # 검증 실행
        assert str(e.value) == msg and e.value.reason == reason  # 메시지와 이유 확인
    with pytest.raises(ContinueError) as e:                  # 위치 앞머리
        fr.validate_single_edit("a", "a", fr.MISSING, 3)    # 위치 앞머리가 붙은 오류 시험
    assert str(e.value) == "edit at index 3: old_string and new_string must be different"  # 오류 글 확인


def test_validate_multi_edit():                             # MultiEdit 검증 시험
    for args, msg in [({}, "invalid multi-edit args"), ({"edits": "x"}, "edits array is required"),  # 오류 사례(인자, 메시지)
                      ({"edits": []}, "edits array must contain at least one edit"),  # 빈 edits
                      ({"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "", "new_string": "c"}]},  # 두 번째 편집이 빈 old
                       "Edit at index 1: old_string cannot be empty. Only the first edit can have an empty old_string for insertion at the beginning of the file.")]:  # 오류 메시지(cn 원문)
        with pytest.raises(ContinueError) as e:             # 오류 기대
            fr.validate_multi_edit(args)                    # 검증 실행
        assert str(e.value) == msg                          # 메시지 확인


def test_execute_multi_find_and_replace_sequential():       # 여러 편집 순차 적용 시험
    out = fr.execute_multi_find_and_replace("a b c", [{"old_string": "a", "new_string": "x"}, {"old_string": "x b", "new_string": "y"}])  # 두 번째는 첫 결과에 적용되는 편집들
    assert out == "y c"                                      # 두 번째는 첫 결과에 적용
    with pytest.raises(ContinueError) as e:                 # 두 번째가 실패하면 오류
        fr.execute_multi_find_and_replace("a b c", [{"old_string": "a", "new_string": "x"}, {"old_string": "zz", "new_string": "y"}])  # 못 찾는 편집
    assert str(e.value) == 'Edit at index 1: string not found in file: "zz"'  # 오류 글 확인


# ───────────── Node 차등 비교(cn 원본 TS 를 node 로 직접 실행) ─────────────
# node 로 cn 원본을 돌리는 스크립트
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


@pytest.mark.skipif(not shutil.which("node") or not SR.exists(), reason="node 또는 .ref/continue 없음")  # node 또는 원본이 없으면 건너뜀
def test_node_differential(tmp_path):                       # Node 차등 비교 시험
    """무작위 입력 1500개를 cn 원본(node)과 ncn 에 넣어 결과·오류 글이 같은지."""
    (tmp_path / "package.json").write_text('{"type":"module"}')  # ESM 설정 파일
    (tmp_path / "findSearchMatch.ts").write_text((SR / "findSearchMatch.ts").read_text())  # 원본 찾기 코드 복사
    src = (SR / "performReplace.ts").read_text()             # import 경로를 실행 가능하게 고침(enum 은 node 가 못 읽어 대체)
    src = src.replace('import { EditOperation } from "../../tools/definitions/multiEdit";', "")  # 타입 import 제거
    src = src.replace('import { ContinueError, ContinueErrorReason } from "../../util/errors";',  # 오류 클래스 import 를 대체
                      "class ContinueError extends Error { constructor(r, m) { super(m); this.reason = r; } }\n"
                      "const ContinueErrorReason = { FindAndReplaceOldStringNotFound: 'find_and_replace_old_string_not_found', "
                      "FindAndReplaceMultipleOccurrences: 'find_and_replace_multiple_occurrences' };")  # 이유 값도 대체
    src = src.replace('import { SearchMatchResult, findSearchMatches } from "./findSearchMatch";',  # 찾기 import 경로 수정
                      'import { findSearchMatches } from "./findSearchMatch.ts";')   # 타입 이름은 실행 때 없음
    (tmp_path / "performReplace.ts").write_text(src)        # 수정한 원본 저장
    (tmp_path / "run.mjs").write_text(NODE_RUNNER)          # 실행 스크립트 저장
    rnd = random.Random(7)                                   # 결정적 무작위
    atoms = ["a", "b", "A", " ", "  ", "\t", "\n", "\r\n", "x = 1", "    ", "\u00a0", "\x1c", "é", "İ"]  # 무작위 조각 후보

    def gen(k):                                              # 조각을 이어 무작위 글
        return "".join(rnd.choice(atoms) for _ in range(rnd.randint(0, k)))  # 조각을 이어 무작위 글 생성
    cases = [[gen(14), gen(4), gen(4), rnd.random() < 0.5] for _ in range(1500)]  # 1500개 사례 만들기
    proc = subprocess.run(["node", str(tmp_path / "run.mjs")], input=json.dumps(cases), capture_output=True, text=True, cwd=tmp_path)  # node 실행
    assert proc.returncode == 0, proc.stderr                # 성공해야 함
    expected = json.loads(proc.stdout)                      # node 결과
    bad = []                                                # 다른 사례 모음
    for (c, o, n, r), exp in zip(cases, expected):          # 사례마다 비교
        try:                                                # 파이썬 결과 계산
            got = {"ok": fr.execute_find_and_replace(c, o, n, r, 0)}  # 성공 결과
        except ContinueError as e:                          # 오류가 나면
            got = {"err": str(e), "reason": e.reason}       # 오류 글과 이유
        got["count"] = len(fr.find_search_matches(c, o))    # 일치 개수도 비교
        if got != exp:                                      # 다르면
            bad.append((c, o, n, r, got, exp))              # 불일치 기록
    assert bad == []                                        # 불일치가 없어야 함
