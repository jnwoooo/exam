# packaging/ncn_entry.py — PyInstaller 실행 파일(ncn.exe)의 진입 스크립트. cn 원본 없음(배포 전용)
# `uv run ncn` 은 pyproject [project.scripts] → ncn.__main__:main 이지만, PyInstaller 는 패키지 밖의 스크립트 하나를 진입점으로 받는다.
# --doctor 는 ncn/doctor.py 로 바로 보낸다(ncn/app.py 에 연결되기 전에도 설치 확인이 되게 — 연결 뒤에도 같은 함수라 결과가 같다).
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # 명령줄·종료 코드


def run() -> int:                                            # 실행 파일 본체
    """argv 에 --doctor 가 있으면 점검, 아니면 ncn.app.main(헤드리스·--stdio·--version 등)."""
    argv = sys.argv[1:]                                      # 프로그램 이름 뺀 인자
    if "--doctor" in argv:                                   # 설치·연결 점검
        from ncn.doctor import main as doctor_main           # 지연 import(시작 시간 보호)
        return doctor_main(argv)                             # 점검 실행
    from ncn.app import main                                 # 일반 실행(ncn/__main__.py 와 같은 함수)
    return main(argv)                                        # 종료 코드


if __name__ == "__main__":                                   # 실행 파일로 시작될 때
    import multiprocessing                                   # PyInstaller 묶음에서 자식 프로세스 재진입 방지
    multiprocessing.freeze_support()                         # Windows: 누군가 multiprocessing 을 써도 exe 가 무한 재실행되지 않게
    sys.exit(run())                                          # 종료 코드로 끝냄
