# tests/test_app.py — 명령줄·헤드리스 조립(ncn/app.py)과 세션 저장(ncn/session.py) 시험. cn flagValidator.test.ts·cli.ts·session.ts 사례
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 세션 파일 확인

from ncn import app                                          # 대상
from ncn.session import SessionManager, history_manager_load   # 세션


def _args(argv):                                            # 옵션 해석 도우미 함수
    """옵션 해석 결과."""
    a, extra = app.build_parser().parse_known_args(argv)    # 명령줄을 파서에 통과시킴
    assert not extra                                         # 알 수 없는 것 없음
    return a                                                # 해석 결과 반환


def test_validate_flags_cn_messages():                      # 플래그 검증 오류 글 시험
    # --format 은 -p 와 함께만, 값은 json 만(flagValidator.ts 원문)
    assert app.validate_flags(_args(["--format", "json"])) == ["Error: --format flag can only be used with -p/--print flag"]  # -p 없이 --format 을 쓰면 오류
    assert app.validate_flags(_args(["-p", "x", "--format", "xml"])) == ["Error: --format currently only supports 'json'"]  # json 이 아닌 형식 값은 오류
    errs = app.validate_flags(_args(["-p", "x", "--readonly", "--auto", "--allow", " "]))  # 상충 옵션과 빈 --allow 값을 함께 검사
    assert errs == ["Error: Cannot use both --readonly and --auto flags together",  # readonly·auto 동시 사용 오류 글
                    "Error: --allow requires a tool name (e.g., --allow readFile)"]  # 빈 --allow 값 오류 글
    assert app.validation_error_text(errs).endswith("\nFound 2 validation errors.\n")   # 2개 이상이면 개수 줄


def test_repeatable_options_keep_order():                   # 반복 가능한 옵션이 순서를 유지하는지 시험
    a = _args(["-p", "hi", "--allow", "Write", "--allow", "Bash(npm *)", "--rule", "r1", "--rule", "r2"])  # 여러 옵션을 한꺼번에 해석
    assert a.allow == ["Write", "Bash(npm *)"] and a.rule == ["r1", "r2"] and a.print is True and a.prompt == "hi"  # allow·rule 순서, print, 프롬프트 확인


def test_has_supplied_prompt_cli_ts():                      # 프롬프트 존재 판정 시험
    assert app.has_supplied_prompt(["-p", "hello"])          # -p 뒤 인자
    assert not app.has_supplied_prompt(["-p", "--config", "c.yaml"])   # 값 받는 플래그는 값까지 건너뜀
    assert app.has_supplied_prompt(["--prompt", "x", "-p"])  # --prompt 가 있으면 참
    assert not app.has_supplied_prompt(["hello"])            # -p 없음


def test_prepend_prompt_trims():                            # prependPrompt 공백 처리 시험
    assert app.prepend_prompt(None, "  hi \n") == "hi"       # 앞뒤 공백 제거(cn prependPrompt)
    assert app.prepend_prompt("a", "b") == "a\n\nb"         # 둘 다 있으면 빈 줄로 연결
    assert app.prepend_prompt(None, "   ") is None           # 비면 없음


def test_prune_last_message():                              # 마지막 메시지 잘라내기 시험
    u = {"message": {"role": "user", "content": "q"}}       # user 메시지
    t = {"message": {"role": "assistant", "content": "", "toolCalls": [{"id": "1"}]}}  # 도구 호출이 있는 assistant 메시지
    a = {"message": {"role": "assistant", "content": "a"}}  # 일반 assistant 메시지
    assert app._prune_last_message([u, a]) == []             # 끝에서 두 번째가 user → 둘
    assert app._prune_last_message([u, t, a]) == [u]          # 도구 호출 assistant → 둘
    assert app._prune_last_message([u, a, a]) == [u, a]       # 그 밖 → 하나


def test_session_save_format_and_resume(tmp_path):          # 세션 저장 형식과 이어받기 시험
    env = {"CONTINUE_GLOBAL_DIR": str(tmp_path / "g"), "CONTINUE_CLI_TEST_SESSION_ID": "sid-1", "HOME": str(tmp_path)}  # 전역 폴더·시험 세션 id·HOME 을 임시 폴더로 지정
    m = SessionManager(env, "/work")                        # 세션 관리자 생성
    m.update_history([{"message": {"role": "system", "content": "s"}, "contextItems": []},  # system 과 user 항목을 넣어 저장
                      {"message": {"role": "user", "content": "q"}, "contextItems": []}])  # (기록 이어짐)
    raw = (tmp_path / "g" / "sessions" / "sid-1.json").read_text(encoding="utf-8")  # 저장된 세션 파일 내용
    data = json.loads(raw)                                  # JSON 으로 해석
    assert list(data) == ["sessionId", "title", "workspaceDirectory", "history", "usage"]   # 키 순서(cn)
    assert data["title"] == "Untitled Session" and len(data["history"]) == 1                # system 제외
    assert data["history"][0]["editorState"] == "q"           # user 에 editorState
    assert raw.startswith('{\n  "sessionId": "sid-1"')        # 2칸 들여쓰기
    lst = json.loads((tmp_path / "g" / "sessions" / "sessions.json").read_text(encoding="utf-8"))  # 세션 목록 파일
    assert lst[0]["sessionId"] == "sid-1" and lst[0]["messageCount"] == 0  # 목록에 id 와 메시지 수 기록
    m2 = SessionManager(env, "/work")                         # --resume: 가장 최근 세션
    assert m2.load_latest()["sessionId"] == "sid-1"         # 가장 최근 세션을 불러와 id 확인


def test_history_manager_load_missing_returns_empty(tmp_path):  # 없는 세션 불러오기 시험
    s = history_manager_load("nope", {"CONTINUE_GLOBAL_DIR": str(tmp_path)})   # cn: 예외 없이 빈 세션
    assert s == {"history": [], "title": "New Session", "workspaceDirectory": "", "sessionId": "nope"}  # 빈 세션이 돌아오는지 확인


def test_main_errors_without_llm(tmp_path, capsys, monkeypatch):  # LLM 없이 main 이 내는 오류 시험
    monkeypatch.chdir(tmp_path)                              # .env 없는 폴더
    assert app.main(["-p", "x", "--format", "xml"]) == 1      # 검증 실패
    assert "Error: --format currently only supports 'json'" in capsys.readouterr().err  # 형식 오류 글이 stderr 에 나옴
    assert app.main(["-p", "x", "--bogus"]) == 1              # 알 수 없는 옵션: 헤드리스면 cn처럼 아무것도 안 찍음
    assert capsys.readouterr().err == ""                    # stderr 에 아무것도 안 나옴
    assert app.main(["--stdio"]) == 2                         # P2 자리
