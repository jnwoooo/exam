# tests/test_e4_undo.py — [ncn E4] 쓰기 전 백업과 /undo 시험(ncn/backup.py, 쓰기 도구, 대화형 슬래시 명령, ACP "/undo"). cn 원본 없음(ncn 추가)
import asyncio                                               # 도구는 async
import os                                                    # 경로
from pathlib import Path                                     # 경로
from types import SimpleNamespace                            # 가짜 core

import pytest                                                # 시험 도구

from ncn import backup, fsio                                 # 시험 대상
from ncn.console_render import Style                         # 슬래시 명령 색
from ncn.slash_commands import SlashContext, handle_slash_commands   # 슬래시 명령
from ncn.tools.base import ToolRunContext                    # 실행 정보
from ncn.tools.edit import EDIT_TOOL                         # Edit 도구
from ncn.tools.multi_edit import MULTI_EDIT_TOOL             # MultiEdit 도구
from ncn.tools.read_file import READ_TOOL, read_files_set    # Read 도구
from ncn.tools.write_file import WRITE_TOOL                  # Write 도구

SID = "sess-1"                                               # 시험 세션 id


def run(coro):                                              # async 를 동기로
    """이벤트 루프에서 끝까지."""
    return asyncio.run(coro)                                 # 실행


async def call(tool, args):                                 # 루프처럼 preprocess → run
    """preprocess 결과 인자로 run."""
    pre = await tool.preprocess(args)                        # 실행 전 검사
    return await tool.run(pre.args, ToolRunContext("t1"))    # 실행


@pytest.fixture                                             # 시험마다 새 폴더·세션 id
def ws(tmp_path, monkeypatch):                              # 작업 폴더
    """작업·전역·백업 폴더를 임시로, 세션 id 는 SID 로 고정."""
    real = Path(os.path.realpath(tmp_path))                  # 실경로
    for d in ("w", "g", "bk"):                               # 폴더들
        (real / d).mkdir()                                   # 만들기
    monkeypatch.chdir(real / "w")                            # cwd
    monkeypatch.setenv("CONTINUE_GLOBAL_DIR", str(real / "g"))   # 전역 폴더(ncn.yaml)
    monkeypatch.setenv("NCN_BACKUP_DIR", str(real / "bk"))   # 백업 폴더
    backup.set_session_resolver(lambda: SID)                 # 세션 id
    read_files_set.clear()                                   # 읽은 파일 기록 비움
    yield real / "w"                                         # 작업 폴더
    backup.set_session_resolver(None)                        # 정리
    read_files_set.clear()                                   # 정리


def _edit(name, old, new):                                  # Read → Edit
    """Read 선행 후 Edit."""
    run(call(READ_TOOL, {"filepath": name}))                 # Read
    return run(call(EDIT_TOOL, {"file_path": name, "old_string": old, "new_string": new}))   # Edit


def test_edit_backup_and_undo_restores_exact_bytes(ws):     # CP949·CRLF 원본도 바이트 그대로 복원
    p = ws / "a.cpp"                                         # 파일
    raw = "// 원본\r\nint a;\r\n".encode("cp949")             # CP949 + CRLF
    p.write_bytes(raw)                                       # 만들기
    _edit("a.cpp", "int a;", "int b;")                       # 수정
    assert p.read_bytes() != raw                             # 바뀜
    entries = backup.list_entries(SID)                       # 백업 목록
    assert len(entries) == 1 and entries[0]["tool"] == "Edit" and entries[0]["existed"] is True   # 하나
    assert entries[0]["path"] == str(p)                      # 절대 경로
    msg = backup.undo_last(SID)                              # 되돌리기
    assert msg.startswith(f"되돌림: Edit {p}") and "남은 백업 0개" in msg   # 결과 글
    assert p.read_bytes() == raw                             # 원본 바이트 그대로
    assert backup.list_entries(SID) == []                    # 되돌린 백업은 지움
    assert backup.undo_last(SID).startswith("되돌릴 파일 수정이 없습니다")   # 더 없음


def test_undo_is_lifo_and_new_file_is_deleted(ws):          # 마지막 수정부터, 새 파일은 삭제
    (ws / "x.txt").write_bytes(b"one\n")                     # 기존 파일
    _edit("x.txt", "one", "two")                             # 1번 수정
    run(call(MULTI_EDIT_TOOL, {"file_path": "x.txt", "edits": [{"old_string": "two", "new_string": "three"}]}))   # 2번 수정
    run(call(WRITE_TOOL, {"filepath": "sub/new.txt", "content": "n\n"}))   # 3번: 새 파일
    listing = backup.format_list(SID)                        # 목록 글
    lines = listing.splitlines()                             # 줄
    assert lines[0].startswith("백업 3개") and "Write" in lines[1] and "새 파일" in lines[1]   # 최근 것이 맨 위
    assert "MultiEdit" in lines[2] and "Edit" in lines[3]    # 순서
    backup.undo_last(SID)                                    # 3번 되돌림
    assert not (ws / "sub" / "new.txt").exists()             # 새 파일 삭제
    backup.undo_last(SID)                                    # 2번 되돌림
    assert (ws / "x.txt").read_bytes() == b"two\n"           # 2번 전
    backup.undo_last(SID)                                    # 1번 되돌림
    assert (ws / "x.txt").read_bytes() == b"one\n"           # 원래대로


def test_changed_after_edit_note(ws):                       # 수정 뒤 또 바뀐 경우 알림
    (ws / "c.txt").write_bytes(b"a\n")                       # 파일
    _edit("c.txt", "a", "b")                                 # 수정
    (ws / "c.txt").write_bytes(b"user change\n")             # 사용자가 또 고침
    msg = backup.undo_last(SID)                              # 되돌리기
    assert "또 바뀌었습니다" in msg and (ws / "c.txt").read_bytes() == b"a\n"   # 알리고 되돌림


def test_prune_max_per_session(ws):                         # 세션당 최대 개수
    (ws.parent / "g" / "ncn.yaml").write_text("backup:\n  max_per_session: 2\n", encoding="utf-8")   # 2개
    (ws / "p.txt").write_bytes(b"0\n")                       # 파일
    run(call(READ_TOOL, {"filepath": "p.txt"}))              # Read
    for i in range(4):                                       # 4번 수정
        run(call(EDIT_TOOL, {"file_path": "p.txt", "old_string": str(i), "new_string": str(i + 1)}))   # 수정
    entries = backup.list_entries(SID)                       # 목록
    assert [e["seq"] for e in entries] == [4, 3]             # 최근 2개만
    backup.undo_last(SID)                                    # 되돌림
    backup.undo_last(SID)                                    # 되돌림
    assert (ws / "p.txt").read_bytes() == b"2\n"             # 남은 백업까지만 되돌아감


def test_disabled_by_ncn_yaml_and_cn_compat(ws, monkeypatch):   # 끄기
    (ws.parent / "g" / "ncn.yaml").write_text("backup:\n  enabled: false\n", encoding="utf-8")   # 끔
    (ws / "d.txt").write_bytes(b"a\n")                       # 파일
    _edit("d.txt", "a", "b")                                 # 수정
    assert backup.list_entries(SID) == []                    # 백업 없음
    (ws.parent / "g" / "ncn.yaml").unlink()                  # 설정 지움(기본 켜짐)
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 원래 동작
    _edit("d.txt", "b", "c")                                 # 수정
    assert backup.list_entries(SID) == []                    # 백업 없음


def test_session_id_sources(monkeypatch):                   # 세션 id 고르기
    backup.set_session_resolver(None)                        # 등록 없음
    assert backup.current_session_id() == f"proc-{os.getpid()}"   # 대체 id
    backup.set_session_resolver(lambda: "from-resolver")     # 대화형·헤드리스
    assert backup.current_session_id() == "from-resolver"    # 등록 함수

    async def in_task():                                     # ACP 턴 작업
        backup.set_session_id("acp-sess")                    # 이 작업만
        return backup.current_session_id()                   # 결과

    assert run(in_task()) == "acp-sess"                      # 컨텍스트 값 우선
    assert backup.current_session_id() == "from-resolver"    # 바깥은 그대로
    backup.set_session_resolver(None)                        # 정리
    assert backup.session_dir("a/b").endswith("a_b")         # 경로 글자 정리


def _slash_ctx(sid):                                        # 대화형 슬래시 명령 부품
    """sessions.get_current() 만 있는 가짜 core."""
    core = SimpleNamespace(sessions=SimpleNamespace(get_current=lambda: {"sessionId": sid}),   # 세션
                           cfg=SimpleNamespace(prompts=[], rules=[]), cwd=os.getcwd(), gdir=os.getcwd())   # 설정·폴더
    return SlashContext(core, Style(False), os.environ)      # 부품 묶음


def test_interactive_slash_undo(ws, monkeypatch):           # 대화형 /undo·/undo list
    (ws / "s.txt").write_bytes(b"a\n")                       # 파일
    _edit("s.txt", "a", "b")                                 # 수정
    ctx = _slash_ctx(SID)                                    # 부품
    assert handle_slash_commands("/undo list", ctx)["output"].startswith("백업 1개")   # 목록
    assert handle_slash_commands("/undo foo", ctx)["output"] == backup.UNDO_USAGE   # 사용법
    out = handle_slash_commands("/undo", ctx)["output"]      # 되돌리기
    assert out.startswith("되돌림:") and (ws / "s.txt").read_bytes() == b"a\n"   # 되돌림
    monkeypatch.setenv("NCN_CN_COMPAT", "1")                 # cn 원래 동작
    assert handle_slash_commands("/undo", ctx) is None       # cn: 모르는 명령 → 일반 글


def test_undo_async_via_ide(ws):                            # IDE 위임 중 되돌림은 IDE 쓰기 요청
    p = ws / "i.txt"                                         # 파일
    p.write_bytes(b"disk\n")                                 # 디스크(백업 원본)
    _edit("i.txt", "disk", "edited")                         # 디스크에서 수정(백업 = 디스크 바이트)

    class Conn:                                              # 가짜 IDE
        def __init__(self):                                  # 생성
            self.writes = {}                                 # 받은 쓰기

        async def read_text_file(self, session_id, path):    # 읽기(쓰지 않음)
            raise RuntimeError("no")                          # 거절

        async def write_text_file(self, session_id, path, content):   # 쓰기
            self.writes[path] = content                      # 기록

    async def go():                                          # 위임 구간
        conn = Conn()                                        # 연결
        tok = fsio.set_backend(fsio.AcpFiles(conn, "s", can_read=True, can_write=True))   # 위임
        try:                                                # /undo
            msg = await backup.undo_command_async("/undo", SID)   # 되돌리기
        finally:                                            # 되돌림
            fsio.reset_backend(tok)                          # 기본으로
        return msg, conn.writes                              # 결과

    msg, writes = run(go())                                  # 실행
    assert msg.startswith("되돌림:") and writes == {str(p): "disk\n"}   # IDE 로 원본 글
    assert p.read_bytes() == b"edited\n"                     # 디스크는 IDE 가 저장할 때 바뀜


def test_acp_prompt_undo(tmp_path):                         # ACP: Edit 뒤 "/undo" 텍스트 프롬프트
    from acp.helpers import text_block                       # 질문 블록
    from tests.test_acp import FakeIDE, Harness, run as arun   # ACP 시험 부품 재사용
    steps = [{"tool_calls": [{"name": "Read", "arguments": {"filepath": "u.py"}}]},   # Read
             {"tool_calls": [{"name": "Edit", "arguments": {"file_path": "u.py", "old_string": "1", "new_string": "2"}}]},   # Edit
             {"content": "끝"}]                               # 답
    h = Harness(tmp_path, steps, {"u.py": "v = 1\n"})        # 준비(fs 능력 없음 → 디스크)
    ide = FakeIDE(["allow_once"])                            # 승인

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        await h.conn.prompt(session_id=s.session_id, prompt=[text_block("바꿔")])   # 수정 턴
        assert (h.work / "u.py").read_text() == "v = 2\n"    # 바뀜
        ide.updates.clear()                                  # 응답 비움
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("/undo")])   # 되돌리기
        assert r.stop_reason == "end_turn"                   # 정상 끝
        await h.stop()                                       # 종료
        return s.session_id                                  # 세션 id

    sid = arun(go())                                         # 실행
    assert (h.work / "u.py").read_text() == "v = 1\n"        # 되돌아감
    assert ide.text().startswith("되돌림: Edit ")            # 결과 글
    assert len(h.bodies()) == 3                              # /undo 는 LLM 호출 없음
    assert (h.cnhome / "ncn-backup" / sid).is_dir()          # 전역 폴더 아래 세션 id 폴더
    assert not (h.work / ".continue").exists()               # 작업 폴더는 깨끗
