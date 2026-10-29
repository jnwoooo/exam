# ncn/__main__.py — 명령줄 진입점. P0에서는 뼈대만(옵션 이름은 cn과 같게 P1에서 채움)
from __future__ import annotations             # 타입 주석 지연 평가

import argparse                                # 명령줄 해석
import sys                                     # 종료 코드

from ncn import __version__                    # 버전


def main(argv: list[str] | None = None) -> int:
    """명령줄을 해석해 모드를 고른다. 아직 구현 전인 모드는 안내 후 종료 코드 2."""
    p = argparse.ArgumentParser(prog="ncn", description="Continue 호환 CLI 하네스")
    p.add_argument("prompt", nargs="?", help="첫 메시지")                   # cn과 같은 위치 인자
    p.add_argument("-p", "--print", dest="headless", action="store_true", help="헤드리스(P1)")
    p.add_argument("--stdio", action="store_true", help="ACP stdio 모드(P2) — VS 2022 확장용")
    p.add_argument("--version", action="version", version=f"ncn {__version__}")   # 버전 출력
    args = p.parse_args(argv)                  # 해석
    mode = "stdio" if args.stdio else "headless" if args.headless else "interactive"   # 모드 결정
    print(f"ncn {__version__}: '{mode}' 모드는 아직 구현 전입니다(P0 뼈대).", file=sys.stderr)   # 안내는 stderr로(V5)
    return 2                                   # 미구현


if __name__ == "__main__":
    sys.exit(main())                           # python -m ncn
