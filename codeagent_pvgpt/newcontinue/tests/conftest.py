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


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def _ncn_backup_dir(monkeypatch, tmp_path_factory):          # [ncn E4] 쓰기 도구 시험이 사용자 ~/.continue/ncn-backup 에 백업을 남기지 않게
    """NCN_BACKUP_DIR 을 시험마다 새 임시 폴더로(작업 폴더 바깥)."""
    monkeypatch.setenv("NCN_BACKUP_DIR", str(tmp_path_factory.mktemp("ncnbackup")))   # 백업 폴더 바꿈


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def _no_system_admin_policy(monkeypatch, tmp_path_factory):  # [ncn E1] 개발 PC 의 /etc/ncn/policy.yaml·NCN_POLICY_FILE 이 시험에 새지 않게
    """시스템 정책 위치를 없는 임시 경로로, 시험용 환경변수는 지움(시험이 직접 정책을 줄 때만 씀)."""
    import ncn.policy as policy_mod                          # 정책 모듈
    missing = tmp_path_factory.mktemp("nopolicy") / "policy.yaml"   # 없는 파일
    monkeypatch.setattr(policy_mod, "system_policy_path", lambda environ=None: missing)   # 시스템 위치 바꿈
    monkeypatch.delenv(policy_mod.POLICY_ENV, raising=False)   # 시험용 경로 지움
