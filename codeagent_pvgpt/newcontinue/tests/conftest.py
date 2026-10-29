# tests/conftest.py — 시험 공용 준비물. cn 원본 없음(ncn 전용)
# cn_compat_env: NCN_CN_COMPAT=1 로 cn 원래 동작을 켠다(부록B 로 고친 곳의 cn 기대값 시험용)
import pytest                                                # 시험 도구

from ncn.compat import COMPAT_ENV                            # 스위치 환경변수 이름


@pytest.fixture                                              # 시험마다 새로
def cn_compat_env(monkeypatch):                              # cn 원래 동작으로 돌리는 준비물
    """NCN_CN_COMPAT=1 — 시험이 끝나면 monkeypatch 가 원래대로 돌린다."""
    monkeypatch.setenv(COMPAT_ENV, "1")                      # cn 원래 동작 켜기


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def _no_inherited_compat(monkeypatch):                       # 바깥 환경의 스위치가 시험에 새지 않게
    """셸에서 NCN_CN_COMPAT 를 켜 둔 채 pytest 를 돌려도 기본(고친 동작)에서 시작."""
    monkeypatch.delenv(COMPAT_ENV, raising=False)            # 스위치 끄기(없으면 그대로)
