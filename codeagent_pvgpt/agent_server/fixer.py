# agent_server/fixer.py — request_fix "가치 버전"(진행기록 4장): Continue의 LLM이 혼자 못 하는 일만 더한다
#   ① 서버 RAG로 관련 코드·사내 규칙을 찾아 붙여 생성(대화에는 남기지 않음)
#   ② 결과를 '찾을 글 → 바꿀 글' 블록으로 받아 원본에 적용해 보고 문법 검사 → 실패하면 오류를 붙여 재생성
#   ③ 통과한 블록만 돌려줌 → Continue의 LLM이 편집 도구(single_find_and_replace)에 바로 옮김
import ast                                                   # 파이썬 문법 검사
import json                                                  # JSON 문법 검사
import re                                                    # 블록 찾기

BLOCK_RE = re.compile(r"<{7} SEARCH\n(.*?)\n={7}\n(.*?)>{7} REPLACE", re.DOTALL)   # SEARCH/REPLACE 블록 하나
REF_CHARS = 1500                                             # 참고 자료 하나당 최대 글자(32k 컨텍스트 보호)
REF_TOTAL = 6000                                             # 참고 자료 전체 최대 글자

SYSTEM = """당신은 코드 수정기다. 요청을 반영하기 위해 바꿀 부분만 아래 형식의 블록으로 출력한다.
<<<<<<< SEARCH
(원본에 있는 그대로의 줄들 — 공백·들여쓰기까지 똑같이, 원본에서 한 곳만 찾히도록 충분히)
=======
(바꿀 줄들)
>>>>>>> REPLACE
규칙: 블록 밖에는 아무것도 쓰지 않는다(설명·마크다운 금지). 바꿀 곳이 여러 곳이면 블록을 여러 개 쓴다.
요청 밖의 부분은 바꾸지 않는다. 참고 자료의 사내 규칙과 기존 코드 스타일을 따른다."""


def parse_blocks(text: str) -> list[tuple[str, str]]:
    """LLM 출력 → [(찾을 글, 바꿀 글)]. 바꿀 글 끝의 줄바꿈 하나는 형식상 붙은 것이라 뗀다."""
    return [(s, r[:-1] if r.endswith("\n") else r) for s, r in BLOCK_RE.findall(text)]


def apply_blocks(code: str, blocks: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """블록을 원본에 차례로 적용해 본다 → (결과 코드, 오류 목록). 오류가 있으면 적용 실패."""
    errors, out = [], code                                   # 오류, 적용 중인 코드
    if not blocks:                                           # 형식을 안 지켰으면
        return code, ["SEARCH/REPLACE 블록이 하나도 없습니다 — 형식대로만 출력하세요."]
    for i, (search, replace) in enumerate(blocks, 1):        # 블록마다
        n = out.count(search)                                # 원본에서 몇 번 나오나
        if n == 0:
            errors.append(f"블록 {i}: SEARCH 내용이 원본에 없습니다(공백·들여쓰기까지 똑같이).")
        elif n > 1:
            errors.append(f"블록 {i}: SEARCH 내용이 원본에 {n}번 나옵니다 — 앞뒤 줄을 더 넣어 한 곳만 찾히게.")
        else:
            out = out.replace(search, replace, 1)            # 정확히 한 곳 → 바꾼다
    return out, errors


def _balance(text: str) -> tuple[int, int, int]:
    """괄호 균형 (), [], {} — 문자열 안 괄호도 세는 단순 검사라 '원본과의 차이'로만 쓴다."""
    return text.count("(") - text.count(")"), text.count("[") - text.count("]"), text.count("{") - text.count("}")


def validate(path: str, original: str, new_code: str) -> str | None:
    """문법 검사. 문제가 있으면 오류 문자열, 없으면 None."""
    if path.endswith(".py"):                                 # 파이썬: 실제 문법 해석
        try:
            ast.parse(original)                              # 원본이 문법상 완결된 조각일 때만 검사 의미가 있다
        except SyntaxError:
            return None if _balance(original) == _balance(new_code) else "괄호 짝이 원본과 달라졌습니다."
        try:
            ast.parse(new_code)                              # 수정 결과 해석
            return None
        except SyntaxError as e:
            return f"파이썬 문법 오류: {e.msg} ({e.lineno}번째 줄)"
    if path.endswith(".json"):                               # JSON
        try:
            json.loads(original)                             # 원본이 온전한 JSON일 때만
        except ValueError:
            return None
        try:
            json.loads(new_code)
            return None
        except ValueError as e:
            return f"JSON 문법 오류: {e}"
    if _balance(original) != _balance(new_code):             # 그 밖의 언어: 괄호 균형이 바뀌었으면 의심
        return "괄호 (), [], {} 의 짝이 원본과 달라졌습니다."
    return None


def checker_name(path: str) -> str:
    """사용자에게 보여 줄 검사 종류."""
    return "파이썬 문법" if path.endswith(".py") else "JSON 문법" if path.endswith(".json") else "괄호 균형"


def build_references(code_hits: list[dict], doc_hits: list[dict]) -> tuple[str, list[str]]:
    """RAG 결과 → 프롬프트에 붙일 참고 자료 글, 참고한 위치 목록(사용자에게 보여 줄 것)."""
    parts, names, total = [], [], 0                          # 글 조각들, 위치 이름들, 누적 글자
    for kind, hits in (("사내 규칙·문서", doc_hits), ("관련 코드", code_hits)):   # 규칙을 먼저
        for h in hits:
            text = h.get("text", "")[:REF_CHARS]             # 하나당 상한
            if not text or total + len(text) > REF_TOTAL:    # 전체 상한을 넘으면 그만
                continue
            name = f"{h['repo']}/{h['path']}" + (f" · {h['symbol']}" if h.get("symbol") else "")   # 위치 이름
            parts.append(f"[{kind}] {name}\n{text}")         # 프롬프트 조각
            names.append(name)
            total += len(text)
    return ("\n\n".join(parts), names)


def format_result(path: str, blocks: list[tuple[str, str]], attempts: int, refs: list[str]) -> str:
    """Continue의 LLM이 읽을 최종 결과 — 편집 도구에 그대로 옮길 수 있는 형태."""
    lines = [f"수정안({path}) — 아직 적용되지 않음. 아래 블록을 순서대로 편집 도구(single_find_and_replace 등)로 적용하세요."]
    for i, (s, r) in enumerate(blocks, 1):                   # 블록마다
        lines += [f"\n[{i}/{len(blocks)}] 찾을 글:", "```", s, "```", "바꿀 글:", "```", r, "```"]
    lines.append(f"\n검증: {checker_name(path)} 통과(생성 {attempts}회)")   # 검증 결과
    if refs:
        lines.append("참고한 사내 자료: " + ", ".join(refs))  # 무엇을 참고했나(서버 색인 기준 힌트)
    return "\n".join(lines)


def first_user_prompt(path: str, instruction: str, code: str, references: str) -> str:
    """첫 생성 요청 글."""
    ref = f"<references>\n{references}\n</references>\n\n" if references else ""   # 참고 자료(있을 때만)
    return f"{ref}파일: {path}\n요청: {instruction}\n\n<code>\n{code}\n</code>"
