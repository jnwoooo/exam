# tests/test_app.py — 명령줄·헤드리스 조립(ncn/app.py)과 세션 저장(ncn/session.py) 시험. cn flagValidator.test.ts·cli.ts·session.ts 사례
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 세션 파일 확인

from ncn import app                                          # 대상
from ncn.session import SessionManager, history_manager_load   # 세션


def _args(argv):
    """옵션 해석 결과."""
    a, extra = app.build_parser().parse_known_args(argv)
    assert not extra                                         # 알 수 없는 것 없음
    return a


def test_validate_flags_cn_messages():
    # --format 은 -p 와 함께만, 값은 json 만(flagValidator.ts 원문)
    assert app.validate_flags(_args(["--format", "json"])) == ["Error: --format flag can only be used with -p/--print flag"]
    assert app.validate_flags(_args(["-p", "x", "--format", "xml"])) == ["Error: --format currently only supports 'json'"]
    errs = app.validate_flags(_args(["-p", "x", "--readonly", "--auto", "--allow", " "]))
    assert errs == ["Error: Cannot use both --readonly and --auto flags together",
                    "Error: --allow requires a tool name (e.g., --allow readFile)"]
    assert app.validation_error_text(errs).endswith("\nFound 2 validation errors.\n")   # 2개 이상이면 개수 줄


def test_repeatable_options_keep_order():
    a = _args(["-p", "hi", "--allow", "Write", "--allow", "Bash(npm *)", "--rule", "r1", "--rule", "r2"])
    assert a.allow == ["Write", "Bash(npm *)"] and a.rule == ["r1", "r2"] and a.print is True and a.prompt == "hi"


def test_has_supplied_prompt_cli_ts():
    assert app.has_supplied_prompt(["-p", "hello"])          # -p 뒤 인자
    assert not app.has_supplied_prompt(["-p", "--config", "c.yaml"])   # 값 받는 플래그는 값까지 건너뜀
    assert app.has_supplied_prompt(["--prompt", "x", "-p"])  # --prompt 가 있으면 참
    assert not app.has_supplied_prompt(["hello"])            # -p 없음


def test_prepend_prompt_trims():
    assert app.prepend_prompt(None, "  hi \n") == "hi"       # 앞뒤 공백 제거(cn prependPrompt)
    assert app.prepend_prompt("a", "b") == "a\n\nb"
    assert app.prepend_prompt(None, "   ") is None           # 비면 없음


def test_prune_last_message():
    u = {"message": {"role": "user", "content": "q"}}
    t = {"message": {"role": "assistant", "content": "", "toolCalls": [{"id": "1"}]}}
    a = {"message": {"role": "assistant", "content": "a"}}
    assert app._prune_last_message([u, a]) == []             # 끝에서 두 번째가 user → 둘
    assert app._prune_last_message([u, t, a]) == [u]          # 도구 호출 assistant → 둘
    assert app._prune_last_message([u, a, a]) == [u, a]       # 그 밖 → 하나


def test_session_save_format_and_resume(tmp_path):
    env = {"CONTINUE_GLOBAL_DIR": str(tmp_path / "g"), "CONTINUE_CLI_TEST_SESSION_ID": "sid-1", "HOME": str(tmp_path)}
    m = SessionManager(env, "/work")
    m.update_history([{"message": {"role": "system", "content": "s"}, "contextItems": []},
                      {"message": {"role": "user", "content": "q"}, "contextItems": []}])
    raw = (tmp_path / "g" / "sessions" / "sid-1.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert list(data) == ["sessionId", "title", "workspaceDirectory", "history", "usage"]   # 키 순서(cn)
    assert data["title"] == "Untitled Session" and len(data["history"]) == 1                # system 제외
    assert data["history"][0]["editorState"] == "q"           # user 에 editorState
    assert raw.startswith('{\n  "sessionId": "sid-1"')        # 2칸 들여쓰기
    lst = json.loads((tmp_path / "g" / "sessions" / "sessions.json").read_text(encoding="utf-8"))
    assert lst[0]["sessionId"] == "sid-1" and lst[0]["messageCount"] == 0
    m2 = SessionManager(env, "/work")                         # --resume: 가장 최근 세션
    assert m2.load_latest()["sessionId"] == "sid-1"


def test_history_manager_load_missing_returns_empty(tmp_path):
    s = history_manager_load("nope", {"CONTINUE_GLOBAL_DIR": str(tmp_path)})   # cn: 예외 없이 빈 세션
    assert s == {"history": [], "title": "New Session", "workspaceDirectory": "", "sessionId": "nope"}


def test_main_errors_without_llm(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)                              # .env 없는 폴더
    assert app.main(["-p", "x", "--format", "xml"]) == 1      # 검증 실패
    assert "Error: --format currently only supports 'json'" in capsys.readouterr().err
    assert app.main(["-p", "x", "--bogus"]) == 1              # 알 수 없는 옵션: 헤드리스면 cn처럼 아무것도 안 찍음
    assert capsys.readouterr().err == ""
    assert app.main(["--stdio"]) == 2                         # P2 자리
