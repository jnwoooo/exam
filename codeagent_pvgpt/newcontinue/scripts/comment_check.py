# scripts/comment_check.py — 주석 보강 작업 검사 도구: ① 동작 불변(AST 동일) ② 한국어 주석 비율
# 사용:
#   uv run python scripts/comment_check.py snapshot 파일...   # 고치기 전 AST 저장(.ast_snapshot.json)
#   uv run python scripts/comment_check.py verify   파일...   # 고친 뒤 AST 비교(주석만 바뀌었으면 OK)
#   uv run python scripts/comment_check.py coverage 파일...   # 코드 줄 중 한국어 주석이 붙은 줄 비율
import ast                                                   # 구문 트리(주석은 트리에 없음 → 주석만 바꾸면 트리가 같다)
import io                                                    # 문자열을 토큰화하려고
import json                                                  # 스냅숏 저장
import re                                                    # 한글 판정
import sys                                                   # 명령줄
import tokenize                                              # 주석·코드 줄 찾기
from pathlib import Path                                     # 경로

SNAP = Path(__file__).parent / ".ast_snapshot.json"          # 스냅숏 파일(git 제외)


def tree(path: str) -> str:
    """파일의 구문 트리 문자열(줄 번호 제외 — 주석 줄을 넣어도 같게)."""
    return ast.dump(ast.parse(Path(path).read_text(encoding="utf-8")), include_attributes=False)


def coverage(path: str) -> tuple[int, int]:
    """(코드가 있는 줄 수, 그중 한국어 주석이 붙은 줄 수). 여러 줄 문자열 안쪽 줄은 코드 줄로 치지 않는다."""
    src = Path(path).read_text(encoding="utf-8")
    code, com = set(), set()
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT and re.search("[가-힣]", t.string):
            com.add(t.start[0])                              # 한국어 주석이 있는 줄
        elif t.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            code.add(t.start[0])                             # 코드 토큰이 시작하는 줄
    return len(code), len(code & com)


def main() -> int:
    cmd, files = sys.argv[1], sys.argv[2:]
    if cmd == "snapshot":                                    # 기존 스냅숏에 덧붙여 저장
        data = json.loads(SNAP.read_text()) if SNAP.exists() else {}
        data.update({f: tree(f) for f in files})
        SNAP.write_text(json.dumps(data))
        print(f"snapshot {len(files)}")
        return 0
    if cmd == "verify":                                      # 스냅숏과 비교
        data = json.loads(SNAP.read_text())
        bad = [f for f in files if data.get(f) != tree(f)]
        print("AST 동일" if not bad else "AST 바뀜(주석 외 변경): " + ", ".join(bad))
        return 1 if bad else 0
    if cmd == "coverage":                                    # 파일별 비율
        tc = tn = 0
        for f in files:
            n, c = coverage(f)
            tc, tn = tc + c, tn + n
            print(f"{100 * c // max(1, n):3d}% {n:5d} {f}")
        print(f"합계 {100 * tc // max(1, tn)}% ({tc}/{tn})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
