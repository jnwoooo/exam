# rag_service/chunking.py — 추출한 파일 내용을 검색 단위(청크)로 자른다(2판 md 5-3)
import ast                                                   # 파이썬 문법 구조 해석(함수·클래스 경계)

WINDOW, OVERLAP = 60, 10                                     # 줄 묶음 청킹: 60줄씩, 10줄 겹침
MAX_FUNC_LINES = 150                                         # 이보다 긴 함수·클래스는 줄 묶음으로 다시 자른다


def chunk_lines(lines: list[str], offset: int = 0) -> list[dict]:
    """줄 목록 → [{start, end, symbol, body}] (줄 번호는 1부터, offset만큼 밀어서)."""
    out, step = [], WINDOW - OVERLAP                         # 결과, 다음 조각 시작 간격(50줄)
    for i in range(0, max(len(lines), 1), step):             # 50줄씩 이동
        seg = lines[i:i + WINDOW]                            # 60줄 조각
        if any(line.strip() for line in seg):                # 빈 줄뿐인 조각은 버림
            out.append({"start": offset + i + 1,             # 시작 줄(1부터)
                        "end": offset + i + len(seg),        # 끝 줄
                        "symbol": "",                        # 줄 묶음은 함수명 없음
                        "body": "\n".join(seg)})             # 본문
        if i + WINDOW >= len(lines):                         # 마지막 조각까지 왔으면 끝
            break
    return out


def chunk_python(text: str) -> list[dict]:
    """파이썬은 최상위 함수·클래스 경계로 자르고 이름(symbol)을 남긴다 — 결과에 함수명을 돌려주기 위해."""
    lines = text.splitlines()                                # 줄 목록
    try:
        tree = ast.parse(text)                               # 문법 구조로 해석
    except SyntaxError:
        return chunk_lines(lines)                            # 문법 오류 파일은 줄 묶음으로
    out, covered = [], set()                                 # 결과, 함수·클래스가 차지한 줄 번호들
    for node in tree.body:                                   # 파일 최상위 문장들
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):   # 함수·클래스만
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])      # 데코레이터 줄 포함 시작
            end = node.end_lineno                            # 끝 줄
            if end - start + 1 > MAX_FUNC_LINES:             # 너무 길면
                parts = chunk_lines(lines[start - 1:end], offset=start - 1)  # 줄 묶음으로 쪼개고
                for p in parts:
                    p["symbol"] = node.name                  # 어느 함수의 일부인지는 남긴다
                out += parts
            else:
                out.append({"start": start, "end": end, "symbol": node.name,
                            "body": "\n".join(lines[start - 1:end])})   # 함수·클래스 하나 = 청크 하나
            covered.update(range(start, end + 1))            # 차지한 줄 기록
    run: list[int] = []                                      # 함수 밖 코드(임포트·상수)의 연속 구간
    for n in range(1, len(lines) + 2):                       # 끝 다음 줄까지 돌아 마지막 구간도 마무리
        if n <= len(lines) and n not in covered:             # 함수 밖 줄이면
            run.append(n)                                    # 구간에 추가
        elif run:                                            # 구간이 끊기면
            out += chunk_lines(lines[run[0] - 1:run[-1]], offset=run[0] - 1)  # 그 구간을 줄 묶음으로
            run = []
    return sorted(out, key=lambda c: c["start"])             # 파일 순서대로


def chunk_markdown(text: str) -> list[dict]:
    """문서는 '## 섹션' 단위로 자르고 섹션 제목을 symbol에 남긴다."""
    out, title, start, buf = [], "", 1, []                   # 결과, 현재 제목, 시작 줄, 본문 버퍼
    lines = text.splitlines()                                # 줄 목록
    for i, line in enumerate(lines + ["## "], 1):            # 끝에 가짜 제목 → 마지막 섹션도 마무리
        if line.startswith("## "):                           # 새 섹션 시작
            body_lines = [b for b in buf if b.strip() and not b.startswith("# ")]   # 문서 제목(# …)만 있는 머리말은 버림
            if body_lines:                                   # 내용이 있으면
                out.append({"start": start, "end": i - 1, "symbol": title, "body": "\n".join(buf)})  # 섹션 하나 = 청크
            title, start, buf = line[3:].strip(), i, []      # 다음 섹션 준비
        else:
            buf.append(line)                                 # 본문 쌓기
    return out


def chunk_file(path: str, text: str) -> list[dict]:
    """확장자에 맞는 청킹을 고른다."""
    if path.endswith(".py"):                                 # 파이썬
        return chunk_python(text)
    if path.endswith(".md"):                                 # 마크다운 문서
        return chunk_markdown(text)
    return chunk_lines(text.splitlines())                    # 그 밖의 언어는 줄 묶음(언어별 파서는 범위 밖)


def embed_text(repo: str, path: str, chunk: dict) -> str:
    """임베딩할 글 = 위치 헤더 + 본문. 청크만 떼어 봐도 어디 코드인지 알게(자기완결성)."""
    sym = f" · {chunk['symbol']}" if chunk["symbol"] else ""     # 함수명이 있으면 붙인다
    return f"# {repo}/{path}{sym} (lines {chunk['start']}-{chunk['end']})\n{chunk['body']}"


def fit_to_tokens(chunks: list[dict], count_tokens, max_tokens: int) -> list[dict]:
    """모델 입력 한도를 넘는 청크는 반으로 나눈다 — 넘는 부분은 임베딩에서 조용히 잘려 버리기 때문."""
    out = []
    for c in chunks:
        lines = c["body"].splitlines()                       # 본문 줄들
        if count_tokens(c["body"]) <= max_tokens or len(lines) < 2:   # 한도 안이거나 더 못 나누면
            out.append(c)                                    # 그대로
            continue
        mid = len(lines) // 2                                # 가운데 줄
        first = {**c, "end": c["start"] + mid - 1, "body": "\n".join(lines[:mid])}   # 앞 절반
        second = {**c, "start": c["start"] + mid, "body": "\n".join(lines[mid:])}    # 뒤 절반
        out += fit_to_tokens([first, second], count_tokens, max_tokens)              # 아직 길면 다시 나눔
    return out
