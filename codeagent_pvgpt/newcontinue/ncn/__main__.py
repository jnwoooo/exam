# ncn/__main__.py — 명령줄 진입점(uv run ncn / python -m ncn). 실제 해석·조립은 ncn/app.py(cn extensions/cli/src/index.ts 자리)
from __future__ import annotations             # 타입 주석 지연 평가

import sys                                     # 종료 코드

from ncn.app import main                       # 옵션 해석 + 헤드리스 실행


if __name__ == "__main__":
    sys.exit(main())                           # python -m ncn
