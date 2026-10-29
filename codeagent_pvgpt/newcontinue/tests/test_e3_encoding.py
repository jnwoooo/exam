# tests/test_e3_encoding.py — [ncn E3] 인코딩·줄바꿈 보존 시험(ncn/encoding.py, ncn/fsio.py, Read·Edit·MultiEdit·Write). cn 원본 없음(ncn 추가)
# 시험 자료(CP949·CRLF·BOM·UTF-16)는 시험 안에서 바이트로 만든다(저장소에 바이너리 파일을 두지 않음 — Windows CI 줄바꿈 변환도 피함)
import asyncio                                               # 도구는 async
import os                                                    # 경로
from pathlib import Path                                     # 경로

import pytest                                                # 시험 도구

from ncn import encoding, fsio                               # 시험 대상
from ncn.tools.base import ContinueError, ToolRunContext     # 공용 형식
from ncn.tools.edit import EDIT_TOOL                         # Edit 도구
from ncn.tools.multi_edit import MULTI_EDIT_TOOL             # MultiEdit 도구
from ncn.tools.read_file import READ_TOOL, read_files_set    # Read 도구·읽은 파일 기록
from ncn.tools.write_file import WRITE_TOOL                  # Write 도구

KO = "한글 주석"                                              # CP949 로 표현되는 글
KO_CP949 = KO.encode("cp949")                                # 그 CP949 바이트


def run(coro):                                              # async 를 동기로
    """이벤트 루프에서 끝까지."""
    return asyncio.run(coro)                                 # 실행


async def call(tool, args):                                 # 루프처럼 preprocess → run
    """preprocess 결과 인자로 run."""
    pre = await tool.preprocess(args)                        # 실행 전 검사
    return await tool.run(pre.args, ToolRunContext("t1"))    # 실행


@pytest.fixture                                             # 시험마다 새 작업 폴더·전역 폴더
def ws(tmp_path, monkeypatch):                              # 작업 폴더
    """작업 폴더를 cwd 로, 전역 폴더(ncn.yaml 위치)도 임시로 — 사용자 설정이 섞이지 않게."""
    real = Path(os.path.realpath(tmp_path))                  # 실경로
    (real / "w").mkdir()                                     # 작업 폴더
    (real / "g").mkdir()                                     # 전역 폴더
    monkeypatch.chdir(real / "w")                            # cwd
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(real / "g"))   # ~/.continue 대신
    read_files_set.clear()                                   # 읽은 파일 기록 비움
    yield real / "w"                                         # 작업 폴더 넘김
    read_files_set.clear()                                   # 정리


def _read(name):                                            # Read 도구 실행
    """Read 결과 글."""
    return run(call(READ_TOOL, {"filepath": name}))          # 실행


# ───────────────────────── 판별 ─────────────────────────
def test_decode_order():                                    # 판별 순서
    t, f = encoding.decode_bytes(b"abc\n")                   # ASCII
    assert (t, f) == ("abc\n", encoding.UTF8)                # UTF-8 BOM 없음
    t, f = encoding.decode_bytes(b"\xef\xbb\xbf" + KO.encode())   # UTF-8 BOM
    assert t == "\ufeff" + KO and f.encoding == "utf-8" and f.bom   # BOM 은 U+FEFF 로(cn 과 같은 글)
    t, f = encoding.decode_bytes(b"\xff\xfe" + KO.encode("utf-16-le"))   # UTF-16 LE BOM
    assert t == "\ufeff" + KO and f == encoding.TextFormat("utf-16-le", bom=True)   # 판별
    t, f = encoding.decode_bytes(b"\xfe\xff" + KO.encode("utf-16-be"))   # UTF-16 BE BOM
    assert t == "\ufeff" + KO and f.encoding == "utf-16-be"  # 판별
    t, f = encoding.decode_bytes(KO_CP949)                   # CP949
    assert t == KO and f.encoding == "cp949" and not f.bom   # 판별
    t, f = encoding.decode_bytes(b"\xff\x80\xff")            # 어디에도 안 맞음
    assert f.replaced and f.encoding == "utf-8" and t == b"\xff\x80\xff".decode("utf-8", errors="replace")   # cn 처럼 대체 문자


def test_conform_newlines():                                # 다수 줄바꿈
    assert encoding.conform_newlines("a\nb\n", "x\r\ny") == "x\r\ny"   # LF 전용 원본 → 손대지 않음(cn)
    assert encoding.conform_newlines("a\r\nb\r\nc\n", "x\ny\r\n") == "x\r\ny\r\n"   # CRLF 다수 → CRLF
    assert encoding.conform_newlines("a\r\nb\nc\n", "x\r\ny\n") == "x\ny\n"   # LF 다수 → LF
    assert encoding.conform_newlines("a\r\nb\n", "x\ny") == "x\r\ny"   # 같으면 CRLF


# ───────────────────────── 도구 ─────────────────────────
def test_pure_utf8_lf_is_byte_identical(ws):                # 차등 시험과 같은 경우: 결과 바이트가 cn 과 같다
    p = ws / "a.py"                                          # 파일
    p.write_bytes("x = 1\n# 주석\n".encode())                # UTF-8 LF
    assert _read("a.py") == "Content of a.py:\nx = 1\n# 주석\n"   # cn 과 같은 글
    run(call(EDIT_TOOL, {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}))   # 수정
    assert p.read_bytes() == "x = 2\n# 주석\n".encode()      # cn 과 같은 바이트


def test_cp949_read_edit_keeps_encoding(ws):                # CP949 파일
    p = ws / "legacy.cpp"                                    # 레거시 소스
    p.write_bytes(b"int a; // " + KO_CP949 + b"\r\nint b;\r\n")   # CP949 + CRLF
    assert _read("legacy.cpp") == f"Content of legacy.cpp:\nint a; // {KO}\r\nint b;\r\n"   # 제대로 해석(cn: 깨진 글)
    out = run(call(EDIT_TOOL, {"file_path": "legacy.cpp", "old_string": "int a; // 한글 주석\nint b;", "new_string": "int a; // 한글 수정\nint c;"}))   # LF 로 준 old_string
    assert out.startswith(f"Successfully edited {p}")        # 성공(cn: string not found)
    assert p.read_bytes() == b"int a; // " + "한글 수정".encode("cp949") + b"\r\nint c;\r\n"   # CP949·CRLF 그대로


def test_cp949_unencodable_is_error_and_file_untouched(ws):  # CP949 로 못 쓰는 글자
    p = ws / "l.txt"                                         # 파일
    raw = KO_CP949 + b"\n"                                   # 원본 바이트
    p.write_bytes(raw)                                       # 만들기
    _read("l.txt")                                           # Read 선행
    with pytest.raises(ContinueError) as e:                  # 오류 기대
        run(call(EDIT_TOOL, {"file_path": "l.txt", "old_string": "주석", "new_string": "주석 😀"}))   # 이모지
    msg = str(e.value)                                       # 오류 글
    assert msg.startswith(f"Error: failed to edit {p}: ") and "CP949" in msg and "U+1F600" in msg   # 모델이 알게
    assert p.read_bytes() == raw                             # 파일은 그대로


def test_utf8_bom_crlf_write_keeps_bom_and_crlf(ws):        # BOM + CRLF 파일 Write
    p = ws / "b.cs"                                          # 파일
    p.write_bytes(b"\xef\xbb\xbfclass A {}\r\n")             # BOM + CRLF
    out = run(call(WRITE_TOOL, {"filepath": "b.cs", "content": "class B {}\nclass C {}\n"}))   # BOM 없이 LF 로 줌
    assert out.startswith("Successfully wrote to file: b.cs")   # 성공
    assert p.read_bytes() == b"\xef\xbb\xbfclass B {}\r\nclass C {}\r\n"   # BOM·CRLF 복원


def test_utf16_edit_roundtrip(ws):                          # UTF-16 LE BOM 파일 Edit
    p = ws / "u.rc"                                          # 리소스 파일(VS 는 UTF-16 으로 저장하기도 함)
    p.write_bytes(b"\xff\xfe" + "A=1\r\nB=2\r\n".encode("utf-16-le"))   # UTF-16 LE + CRLF
    assert _read("u.rc") == "Content of u.rc:\n\ufeffA=1\r\nB=2\r\n"   # 해석된 글
    run(call(MULTI_EDIT_TOOL, {"file_path": "u.rc", "edits": [{"old_string": "A=1\nB=2", "new_string": "A=3\nB=4"}]}))   # LF 로 준 old_string
    assert p.read_bytes() == b"\xff\xfe" + "A=3\r\nB=4\r\n".encode("utf-16-le")   # UTF-16·BOM·CRLF 그대로


def test_mixed_newlines_follow_majority(ws):                # 섞인 줄바꿈
    p = ws / "m.txt"                                         # 파일
    p.write_bytes(b"a\r\nb\nc\nd\n")                         # LF 다수
    _read("m.txt")                                           # Read 선행
    run(call(EDIT_TOOL, {"file_path": "m.txt", "old_string": "a\nb", "new_string": "A\nB"}))   # LF 로 비교
    assert p.read_bytes() == b"A\nB\nc\nd\n"                 # 다수 쪽(LF)으로


def test_new_file_is_utf8_as_given(ws):                     # 새 파일
    run(call(WRITE_TOOL, {"filepath": "n.txt", "content": "가\r\n나\n"}))   # 섞인 줄바꿈 그대로
    assert (ws / "n.txt").read_bytes() == "가\r\n나\n".encode()   # cn 과 같게 UTF-8·받은 그대로


def test_preserve_false_in_ncn_yaml_is_cn(ws):              # ncn.yaml 로 끄기
    (ws.parent / "g" / "ncn.yaml").write_text("encoding:\n  preserve: false\n", encoding="utf-8")   # 끔
    (ws / "l.txt").write_bytes(KO_CP949)                     # CP949 파일
    assert _read("l.txt") == "Content of l.txt:\n" + KO_CP949.decode("utf-8", errors="replace")   # cn 처럼 대체 문자


def test_cn_compat_is_cn(ws, cn_compat_env):                # NCN_CN_COMPAT=1
    p = ws / "c.txt"                                         # 파일
    p.write_bytes(b"a\r\nb\r\n")                             # CRLF
    _read("c.txt")                                           # Read 선행
    run(call(EDIT_TOOL, {"file_path": "c.txt", "old_string": "a\nb", "new_string": "x\ny"}))   # cn: 공백 무시 찾기로 맞지만
    assert p.read_bytes() == b"x\ny\r\n"                    # 넣은 글은 LF 그대로 → 줄바꿈이 섞임(E3 는 CRLF 로 맞춤)


def test_fallback_setting(ws):                              # fallback 인코딩 바꾸기
    (ws.parent / "g" / "ncn.yaml").write_text("encoding:\n  fallback: shift_jis\n", encoding="utf-8")   # 일본어 레거시
    (ws / "j.txt").write_bytes("日本".encode("shift_jis"))    # Shift_JIS 파일
    assert _read("j.txt") == "Content of j.txt:\n日本"        # 그 인코딩으로 해석
    s = encoding.ncn_settings()                              # 설정
    assert s.encoding_fallback == "shift_jis" and s.encoding_preserve is True   # 반영


def test_bad_settings_are_ignored(ws):                      # 잘못된 값
    (ws.parent / "g" / "ncn.yaml").write_text("encoding:\n  preserve: 3\n  fallback: nope-enc\n", encoding="utf-8")   # 틀린 값
    s = encoding.ncn_settings()                              # 설정
    assert s.encoding_preserve is True and s.encoding_fallback == "cp949"   # 기본값


def test_acp_delegation_skips_encoding(ws):                 # IDE 위임은 인코딩 변환 안 함
    p = ws / "d.txt"                                         # 파일(디스크엔 CP949)
    p.write_bytes(KO_CP949)                                  # 디스크 내용

    class Conn:                                              # 가짜 IDE
        def __init__(self):                                  # 생성
            self.writes = {}                                 # 받은 쓰기

        async def read_text_file(self, session_id, path):    # 버퍼 읽기
            class R:                                         # 응답
                content = "버퍼"                              # IDE 버퍼 글
            return R()                                       # 응답

        async def write_text_file(self, session_id, path, content):   # 버퍼 쓰기
            self.writes[path] = content                      # 기록

    async def go():                                          # 위임 구간
        conn = Conn()                                        # 연결
        tok = fsio.set_backend(fsio.AcpFiles(conn, "s", can_read=True, can_write=True))   # 위임
        try:                                                # 쓰기
            await fsio.write_text_file(str(p), "새 글")       # IDE 로
        finally:                                            # 되돌림
            fsio.reset_backend(tok)                          # 기본으로
        return conn.writes                                   # 받은 것

    assert run(go()) == {str(p): "새 글"}                     # 글 그대로 IDE 에(인코딩은 IDE 몫)
    assert p.read_bytes() == KO_CP949                        # 디스크는 그대로
