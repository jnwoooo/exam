# tests/test_parity_run.py — 차등 시험 실행기(parity.run)를 가짜 하네스로 확인: 격리 실행·정규화·비교
import sys                                                   # 현재 파이썬
from pathlib import Path                                     # 경로

from parity import run                                       # 시험 대상

FAKE = [sys.executable, str(Path(__file__).parent / "fake_harness.py")]   # 가짜 하네스 명령
SCEN = {"name": "t", "workspace": {"files": {"README.md": "# a\n"}}, "prompt": "안녕", "args": [],  # 시나리오 한 개
        "llm": [{"content": "반가워요"}], "expect": {"files": {"README.md": "# a\n"}, "requests": 1}}


def test_run_one_normalizes_cwd_date_and_headers():                  # 정규화 시험
    res = run.run_one(SCEN, FAKE)                            # 실행
    assert res["exit_code"] == 0 and res["stdout"] == "반가워요\n"   # 최종 응답
    req = res["requests"][0]                                 # 첫 요청 꺼내기
    assert req["path"] == "/v1/chat/completions"             # 가짜 LLM의 경로 확인
    assert req["body"]["messages"][0]["content"] == "Working directory: <CWD>\nToday's date: <DATE>"   # 매번 다른 값 치환
    assert req["headers"] == {"authorization": "Bearer sk-parity", "content-type": "application/json"}   # User-Agent 제외
    assert run.check_expect(SCEN, res) == []                 # 기대값 일치


def test_same_harness_twice_has_no_diff_and_file_change_is_detected():  # 비교·변경 감지 시험
    a, b = run.run_one(SCEN, FAKE), run.run_one(SCEN, FAKE)  # 다른 임시 폴더·포트에서 두 번
    keys = ("exit_code", "stdout", "requests", "files")      # 비교할 키
    assert run.diff({k: a[k] for k in keys}, {k: b[k] for k in keys}) == []   # 정규화 후 같음
    c = run.run_one({**SCEN, "args": ["--touch"]}, FAKE)     # 파일을 바꾸는 실행
    assert run.check_expect(SCEN, c) == ["파일 결과가 기대와 다름"]  # 파일 변경이 기대와 어긋남
    assert any(".files.README.md" in d for d in run.diff(a["files"] and {"files": a["files"]}, {"files": c["files"]}))  # 파일 차이가 경로로 보고되는지


def test_config_uses_safe_model_name():  # 설정 시험
    cfg = run.config_yaml(1234)                              # 설정 문자열 생성
    assert "model: fake-model" in cfg and "apiBase: http://127.0.0.1:1234/v1/" in cfg  # 모델명·주소 확인
    assert "secrets" not in cfg and "uses:" not in cfg       # Hub 호출 유발 금지
