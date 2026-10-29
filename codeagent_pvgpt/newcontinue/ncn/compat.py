# ncn/compat.py — cn 원래 동작으로 되돌리는 스위치 하나. (cn 원본 없음 — ncn 전용, 계획 부록 B)
# ncn은 cn 버그 중 ★ 항목(P1/cn_버그후보.md: S1~S7, B1, B3, B4, B6, B16, C1, M1~M3)을 고쳤다. 고친 곳마다 주석에 [ncn 부록B Sx] 표시가 있다
# 환경변수 NCN_CN_COMPAT=1 이면 고친 곳 전부가 cn 원래 동작으로 돌아간다(차등 시험·비교 조사용)
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 환경변수 읽기

COMPAT_ENV = "NCN_CN_COMPAT"                                  # 스위치 환경변수 이름


def cn_compat() -> bool:                                     # cn 원래 동작(버그 포함)을 쓸지 판정
    """NCN_CN_COMPAT=1 이면 참 — 고친 곳이 cn 원래 동작을 쓴다. 호출할 때마다 읽는다(시험에서 바꿔 끼울 수 있게)."""
    return os.environ.get(COMPAT_ENV) == "1"                 # 정확히 "1" 일 때만 참
