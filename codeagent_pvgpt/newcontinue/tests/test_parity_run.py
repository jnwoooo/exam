# tests/test_parity_run.py — 차등 시험 실행기(parity.run)를 가짜 하네스로 확인: 격리 실행·정규화·비교
import sys                                                   # 현재 파이썬
from pathlib import Path                                     # 경로

from parity import run                                       # 시험 대상

FAKE = [sys.executable, str(Path(__file__).parent / "fake_harness.py")]   # 가짜 하네스 명령
SCEN = {"name": "t", "workspace": {"files": {"README.md": "# a\n"}}, "prompt": "안녕", "args": [],
        "llm": [{"content": "반가워요"}], "expect": {"files": {"README.md": "# a\n"}, "requests": 1}}


def test_run_one_normalizes_cwd_date_and_headers():
    res = run.run_one(SCEN, FAKE)                            # 실행
    assert res["exit_code"] == 0 and res["stdout"] == "반가워요\n"   # 최종 응답
    req = res["requests"][0]
    assert req["path"] == "/v1/chat/completions"
    assert req["body"]["messages"][0]["content"] == "Working directory: <CWD>\nToday's date: <DATE>"   # 매번 다른 값 치환
    assert req["headers"] == {"authorization": "Bearer sk-parity", "content-type": "application/json"}   # User-Agent 제외
    assert run.check_expect(SCEN, res) == []                 # 기대값 일치


def test_same_harness_twice_has_no_diff_and_file_change_is_detected():
    a, b = run.run_one(SCEN, FAKE), run.run_one(SCEN, FAKE)  # 다른 임시 폴더·포트에서 두 번
    keys = ("exit_code", "stdout", "requests", "files")
    assert run.diff({k: a[k] for k in keys}, {k: b[k] for k in keys}) == []   # 정규화 후 같음
    c = run.run_one({**SCEN, "args": ["--touch"]}, FAKE)     # 파일을 바꾸는 실행
    assert run.check_expect(SCEN, c) == ["파일 결과가 기대와 다름"]
    assert any(".files.README.md" in d for d in run.diff(a["files"] and {"files": a["files"]}, {"files": c["files"]}))


def test_config_uses_safe_model_name():
    cfg = run.config_yaml(1234)
    assert "model: fake-model" in cfg and "apiBase: http://127.0.0.1:1234/v1/" in cfg
    assert "secrets" not in cfg and "uses:" not in cfg       # Hub 호출 유발 금지
