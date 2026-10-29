# ncn/security/terminal_security.py — 터미널 명령 위험 판정. cn packages/terminal-security/src/evaluateTerminalCommandSecurity.ts 를 그대로 옮긴 것
# 명령 파싱은 cn이 쓰는 shell-quote 1.8.3 parse.js(env 없이 호출)를 그대로 옮겼다(shlex와 결과가 다름 — P0/소스확인_terminal_security.md 3장)
# 반환값은 cn ToolPolicy 문자열 그대로: "disabled" | "allowedWithPermission" | "allowedWithoutPermission"
# 주의: JS 정규식과 Python 정규식의 차이(\s 집합, \w의 ASCII 여부, $의 줄 끝 처리)를 모두 명시적으로 맞췄다
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # 정규식
import sys                                                   # stderr 출력
from typing import Any, List, Union                          # 타입

DISABLED = "disabled"                                        # 실행 금지(가장 엄격)
WITH_PERMISSION = "allowedWithPermission"                    # 사용자 승인 필요
WITHOUT_PERMISSION = "allowedWithoutPermission"              # 자동 승인 가능

# JS 정규식 \s 와 String.prototype.trim 이 쓰는 공백 집합(Python \s와 다름: \x1c-\x1f·\x85 없음, \ufeff 있음)
JS_WS = "\t\n\x0b\x0c\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"   # 문자 클래스 안에 넣을 글
_JS_WS_CHARS = "\t\n\x0b\x0c\r \u00a0\u1680" + "".join(chr(c) for c in range(0x2000, 0x200B)) + "\u2028\u2029\u202f\u205f\u3000\ufeff"   # strip용 문자 목록

ParsedToken = Union[str, dict]                               # 토큰: 글 또는 {"op":..} / {"op":"glob","pattern":..} / {"comment":..}


def js_trim(s: str) -> str:                                 # JS trim 대응 함수
    """JS String.prototype.trim 과 같은 공백 집합으로 양끝을 자른다."""
    return s.strip(_JS_WS_CHARS)                             # Python 기본 strip은 집합이 달라 명시


# ───────────────────────── shell-quote 1.8.3 parse.js 이식 ─────────────────────────
# parse.js:5-16 CONTROL — 연산자 목록(긴 것부터). '>(' 는 하나의 연산자가 아님(>, ( 로 나뉨)
_CONTROL = r"(?:\|\||\&\&|;;|\|\&|\<\(|\<\<\<|>>|>\&|<\&|[&;()|<>])"  # shell-quote CONTROL 연산자 패턴
_CONTROL_RE = re.compile(_CONTROL)                           # controlRE = ^CONTROL$ — fullmatch로 사용(Python $는 끝 개행 앞에서도 맞으므로)
_META = "|&;()<> \\t"                                        # parse.js:18 META(정규식 글 안에서 \t)
_SINGLE_QUOTE = r'"((\\"|[^"])*?)"'                          # parse.js:19 — 이름과 달리 큰따옴표 패턴
_DOUBLE_QUOTE = r"'((\\'|[^'])*?)'"                          # parse.js:20 — 이름과 달리 작은따옴표 패턴
_BAREWORD = "(\\\\['\"" + _META + "]|[^" + JS_WS + "'\"" + _META + "])+"   # parse.js:71 BAREWORD(BS = '\\', \s는 JS 집합)
_CHUNKER = re.compile("(" + _CONTROL + ")" + "|" + "(" + _BAREWORD + "|" + _SINGLE_QUOTE + "|" + _DOUBLE_QUOTE + ")+")  # 연산자 또는 단어 덩어리를 찾는 조각 분리 정규식 — parse.js:73-76 chunker(g)
_VAR_SPECIAL = re.compile(r"[*@#?$!_-]")                     # parse.js:131 특수 변수 글자
_VAR_END = re.compile(r"[^A-Za-z0-9_]")                     # 변수 이름이 끝나는 글자 — parse.js:136 /[^\w\d_]/ (JS \w = ASCII)

SQ = "'"                                                     # 작은따옴표
DQ = '"'                                                     # 큰따옴표
DS = "$"                                                     # 달러
BS = "\\"                                                    # 백슬래시(opts.escape 기본값)


def _char_at(s: str, i: int) -> str:                        # JS charAt 대응 함수
    """JS s.charAt(i) — 범위 밖이면 빈 글."""
    return s[i] if 0 <= i < len(s) else ""                   # JS는 예외 대신 ""


def _is_control(s: str) -> bool:                            # 조각 전체가 연산자인지 판정
    """controlRE.test(s) — 문자열 전체가 연산자 하나인지."""
    return _CONTROL_RE.fullmatch(s) is not None              # ^...$ (JS $는 진짜 끝만)


def shell_quote_parse(string: str) -> List[ParsedToken]:    # 명령 문자열을 토큰 목록으로 분해하는 함수
    """shell-quote parse(s) — env 없이 호출한 경우와 같다($VAR → "", 짝 없는 ${ → 예외)."""
    matches = list(_CHUNKER.finditer(string))                # matchAll(빈 일치는 생기지 않는 정규식)
    if len(matches) == 0:                                    # 일치 없음
        return []                                            # 빈 목록
    env: dict = {}                                           # env 없음 → {}
    commented = False                                        # 주석이 시작됐는지(이후 조각은 모두 버림)
    result: List[Any] = []                                   # reduce(concat) 결과

    for match in matches:                                    # 조각마다
        s = match.group(0)                                   # 조각 글
        if not s or commented:                               # 비었거나 주석 이후
            continue                                         # undefined → 버림
        if _is_control(s):                                   # 연산자 조각
            result.append({"op": s})                        # 연산자 토큰으로 추가 — {op}
            continue                                        # 이 조각은 처리 끝
        # 손으로 짠 Bash 따옴표 규칙 스캐너(parse.js:104-194)
        quote: Union[str, bool] = False                      # 현재 따옴표(False 또는 ' / ")
        esc = False                                          # 바로 앞이 백슬래시였는지
        out = ""                                             # 결과 글
        is_glob = False                                      # 따옴표 밖에 * ? 가 있었는지
        i = 0                                                # 글자 위치(parseEnvVar가 옮김)
        early: Any = None                                    # 중간에 끝난 경우의 반환값
        done_early = False                                   # 중간 종료 여부

        def parse_env_var() -> str:                         # $변수 이름을 읽는 안쪽 함수
            """parse.js:115-147 parseEnvVar — 변수 이름을 읽고 값(env 없음 → "" 또는 "$")을 돌려준다."""
            nonlocal i                                       # 바깥 위치를 옮긴다
            i += 1                                           # $ 다음 글자로
            char = _char_at(s, i)                            # 다음 글자
            if char == "{":                                 # 중괄호 형식 — ${NAME}
                i += 1                                       # { 다음
                if _char_at(s, i) == "}":                    # ${} — 이름 없음
                    raise ValueError("Bad substitution: " + s[max(i - 2, 0):i + 1])   # 원문 오류 글
                varend = s.find("}", i)                      # 닫는 }
                if varend < 0:                               # 닫히지 않음
                    raise ValueError("Bad substitution: " + s[i:])   # 원문 오류 글
                varname = s[i:varend]                        # 이름
                i = varend                                   # } 위치(루프의 i++가 다음으로)
            elif char != "" and _VAR_SPECIAL.search(char):  # 특수 변수 한 글자 — $* $@ $# $? $$ $! $_ $-
                varname = char                               # 한 글자 이름
                i += 1                                       # 원본 특이 동작: 루프 i++와 합쳐 다음 글자 하나를 건너뜀
            else:                                           # 일반 이름 — $NAME
                sliced = s[i:]                               # 나머지
                m = _VAR_END.search(sliced)                  # 이름이 끝나는 곳
                if not m:                                    # 끝까지 이름
                    varname = sliced                         # 전부
                    i = len(s)                               # 끝으로
                else:                                       # 이름이 중간에서 끝남
                    varname = sliced[:m.start()]             # 이름
                    i += m.start() - 1                       # 이름 마지막 글자(루프 i++가 다음으로)
            return _get_var(env, "", varname)                # 값

        while i < len(s):                                   # 조각의 글자를 하나씩 스캔 — for (i = 0; i < s.length; i++)
            c = s[i]                                         # 현재 글자
            is_glob = is_glob or (not quote and (c == "*" or c == "?"))   # esc 여부와 무관하게 먼저 계산(원본 순서)
            if esc:                                          # 앞 글자가 백슬래시
                out += c                                     # 그대로
                esc = False                                 # 이스케이프 해제
            elif quote:                                      # 따옴표 안
                if c == quote:                               # 닫는 따옴표
                    quote = False                           # 따옴표 닫힘
                elif quote == SQ:                            # 작은따옴표 안은 그대로
                    out += c                                # 작은따옴표 안은 글자 그대로
                else:                                        # 큰따옴표 안
                    if c == BS:                              # 백슬래시
                        i += 1                               # 다음 글자
                        c = _char_at(s, i)                   # (끝이면 "")
                        if c == DQ or c == BS or c == DS:    # \" \\ \$ 만 풀림
                            out += c                        # 풀리는 이스케이프는 글자만 남김
                        else:                               # 그 밖의 이스케이프
                            out += BS + c                    # 나머지는 백슬래시 유지
                    elif c == DS:                            # 변수
                        out += parse_env_var()              # 큰따옴표 안 변수 치환
                    else:                                   # 일반 글자
                        out += c                            # 글자 그대로 추가
            elif c == DQ or c == SQ:                         # 따옴표 시작
                quote = c                                   # 따옴표 열림
            elif _is_control(c):                             # 따옴표 밖 연산자 글자 → 조각 전체를 연산자로
                early = {"op": s}                           # 조각 전체를 연산자로 확정
                done_early = True                           # 중간 종료 표시
                break                                       # 스캔 중단
            elif c == "#":                                   # 단어 중간이어도 주석(bash와 다름)
                commented = True                             # 이후 조각 모두 버림
                comment_obj = {"comment": string[match.start() + i + 1:]}   # 원본 전체 문자열 기준
                early = [out, comment_obj] if len(out) else [comment_obj]  # 주석 앞 글자가 있으면 함께 반환
                done_early = True                           # 중간 종료 표시
                break                                       # 스캔 중단
            elif c == BS:                                    # 따옴표 밖 백슬래시: 버리고 다음 글자 그대로
                esc = True                                  # 다음 글자를 이스케이프
            elif c == DS:                                    # 변수
                out += parse_env_var()                      # 따옴표 밖 변수 치환
            else:                                           # 일반 글자
                out += c                                    # 글자 그대로 추가
            i += 1                                           # 루프 i++

        if done_early:                                       # 중간 종료
            item = early                                    # 중간 종료 값 사용
        elif is_glob:                                        # 따옴표 밖 * ?
            item = {"op": "glob", "pattern": out}           # glob 토큰으로 만듦
        else:                                               # 일반 단어
            item = out                                      # 그냥 글
        if isinstance(item, list):                           # concat은 배열을 한 겹 펼친다
            result.extend(item)                             # 리스트는 펼쳐서 추가
        else:                                               # 단일 항목
            result.append(item)                             # 그대로 추가
    return result                                           # 완성된 토큰 목록 반환


def _get_var(env: dict, pre: str, key: str) -> str:         # env 변수 값 조회(env는 비어 있음)
    """parse.js:52-64 getVar — env에 없으면 이름이 있을 때 "", 이름이 비면 "$"."""
    r = env.get(key)                                         # env는 항상 {}
    if r is None and key != "":                              # 없는 변수
        r = ""                                              # 이름이 있으면 빈 글
    elif r is None:                                          # $ 단독
        r = "$"                                             # 이름이 없으면 $ 자체
    return pre + r                                          # 접두어와 값을 합쳐 반환


# ───────────────────────── 토큰 종류(evaluateTerminalCommandSecurity.ts:104-123) ─────────────────────────
def _is_operator(token: ParsedToken) -> bool:               # 연산자 토큰 판정
    """연산자 토큰(glob 제외)."""
    return isinstance(token, dict) and "op" in token and token["op"] != "glob"  # op 키가 있고 glob이 아님


def _is_glob(token: ParsedToken) -> bool:                   # glob 토큰 판정
    """glob 토큰."""
    return isinstance(token, dict) and "op" in token and token["op"] == "glob"  # op가 glob임


def _is_comment(token: ParsedToken) -> bool:                # 주석 토큰 판정
    """주석 토큰."""
    return isinstance(token, dict) and "comment" in token   # comment 키 존재


# ───────────────────────── 최상위 함수(:32-102) ─────────────────────────
_LINE_SPLIT = re.compile(r"\r?\n|\r")                        # 줄 분리(따옴표 무시, 원문 전체)


def evaluate_terminal_command_security(base_policy: str, command: Any) -> str:  # 명령 위험도를 판정하는 최상위 함수
    """cn evaluateTerminalCommandSecurity(basePolicy, command) → ToolPolicy.
    결과는 base_policy보다 덜 엄격해지지 않는다(누적값을 base_policy로 시작)."""
    if base_policy == DISABLED:                              # 이미 금지면 그대로
        return DISABLED                                     # 금지는 변경 불가
    if not command or not isinstance(command, str):          # null/undefined/""/문자열 아님
        return base_policy                                  # 명령이 없으면 기본 정책 유지
    normalized = js_trim(command)                            # 앞뒤 공백 제거(JS trim)
    if normalized == "":                                     # 공백만
        return base_policy                                  # 공백뿐이면 기본 정책 유지
    try:                                                    # 파싱 예외를 한꺼번에 처리
        lines = _LINE_SPLIT.split(normalized)                # 줄마다 나눔(개행 = 명령 구분)
        if len(lines) > 1:                                   # 여러 줄
            most = base_policy                               # 가장 엄격한 값
            for line in lines:                               # 줄마다
                t = js_trim(line)                            # 줄 trim
                if t == "":                                  # 빈 줄 건너뜀
                    continue                                # 빈 줄은 평가하지 않음
                tokens = shell_quote_parse(t)                # 파싱(예외 나면 뒤 줄은 평가 안 함)
                p = _evaluate_tokens_security(tokens, base_policy, t)   # 그 줄이 originalCommand
                most = _get_most_restrictive(most, p)        # 누적
                if most == DISABLED:                         # 금지면 즉시
                    return DISABLED                         # 금지가 나오면 즉시 중단
            return most                                     # 가장 엄격한 정책 반환
        tokens = shell_quote_parse(normalized)               # 한 줄
        return _evaluate_tokens_security(tokens, base_policy, normalized)  # 한 줄 명령의 평가 결과 반환
    except Exception as error:                               # 파싱 실패(Bad substitution 등)
        print("Failed to parse command:", error, file=sys.stderr)   # console.error 대응(stdout 금지 — ACP)
        return WITH_PERMISSION                               # 보수적으로 승인 필요


# ───────────────────────── 토큰 평가(:128-274) ─────────────────────────
_HAS_VAR_PATTERN = re.compile(r"\$[A-Za-z0-9_{]")           # 변수 확장 흔적 패턴 — /\$[\w{]/ (JS \w = ASCII)


def _evaluate_tokens_security(tokens: List[ParsedToken], base_policy: str, original: str) -> str:  # 빈 토큰 처리를 포함한 평가 함수
    """evaluateTokensSecurity — 빈 문자열 토큰(변수 확장 흔적)이 있으면 두 가지로 평가."""
    has_empty = any(isinstance(t, str) and t == "" for t in tokens)   # "" 토큰이 있는지
    if has_empty:                                           # 빈 토큰이 있을 때만 이중 평가
        has_empty_quotes = '""' in original or "''" in original   # 정당한 빈 따옴표
        has_var = _HAS_VAR_PATTERN.search(original) is not None    # 변수 패턴
        if not has_empty_quotes or has_var:                  # 변수 확장으로 판단
            with_empty = _evaluate_tokens(tokens, base_policy, original)   # 1) 빈 글 그대로
            without = [t for t in tokens if t != ""]         # 2) 빈 글 뺀 것
            without_policy = _evaluate_tokens(without, base_policy, original) if len(without) > 0 else base_policy  # 빈 글 제거본이 비면 기본 정책
            return _get_most_restrictive(with_empty, without_policy, WITH_PERMISSION)   # 변수 확장은 최소 승인 필요
    return _evaluate_tokens(tokens, base_policy, original)   # 일반 평가


def _evaluate_tokens(tokens: List[ParsedToken], base_policy: str, original: str) -> str:  # 토큰을 명령 단위로 나눠 평가하는 함수
    """evaluateTokens — 연산자로 명령을 나눠 각각 평가하고, 원문으로 치환·난독화를 다시 확인."""
    most = base_policy                                       # 누적값
    current: List[str] = []                                  # 현재 명령 토큰
    for i, token in enumerate(tokens):                       # 토큰마다
        if _is_comment(token):                               # 주석은 무시
            continue                                        # 주석은 건너뜀
        if _is_operator(token):                              # 연산자 = 명령 경계(리다이렉션 포함)
            if len(current) > 0:                             # 앞 명령 평가
                most = _get_most_restrictive(most, _evaluate_single_command(current, original))  # 앞 명령의 위험도를 누적
                if most == DISABLED:                        # 금지면
                    return DISABLED                         # 즉시 반환
            current = []                                     # 다음 명령
            if token["op"] == "|":                           # 파이프만 특별 처리(|& 제외)
                most = _get_most_restrictive(most, _evaluate_pipe_chain(tokens, i))  # 파이프 뒤 명령 위험도 누적
                if most == DISABLED:                        # 금지면
                    return DISABLED                         # 즉시 반환
        elif _is_glob(token):                                # glob은 패턴 글로
            current.append(token["pattern"])                # glob은 패턴 글을 명령 토큰으로
        elif isinstance(token, str):                         # 일반 글
            current.append(token)                           # 일반 글을 명령 토큰으로
    if len(current) > 0:                                     # 남은 명령(조기 반환 없음)
        most = _get_most_restrictive(most, _evaluate_single_command(current, original))  # 마지막 명령의 위험도 누적
    if _has_command_substitution(original):                 # 명령 치환이 있으면 — `...` / $() / <() >()
        for sub in _extract_substituted_commands(original):  # 추출한 명령을 최상위 함수로 재귀 평가
            most = _get_most_restrictive(most, evaluate_terminal_command_security(base_policy, sub))  # 치환된 명령을 재귀 평가해 누적
            if most == DISABLED:                            # 금지면
                return DISABLED                             # 즉시 반환
        if most == WITHOUT_PERMISSION:                       # 치환 자체를 위험으로 봄
            most = WITH_PERMISSION                          # 승인 필요로 올림
    if _has_obfuscation_patterns(original):                  # 난독화
        most = _get_most_restrictive(most, WITH_PERMISSION)  # 난독화는 최소 승인 필요
    return most                                             # 누적 결과 반환


def _get_most_restrictive(*policies: str) -> str:           # 가장 엄격한 정책을 고르는 함수
    """getMostRestrictive(:279-287) — disabled > allowedWithPermission > 나머지."""
    if any(p == DISABLED for p in policies):                # 금지가 하나라도 있으면
        return DISABLED                                     # 금지
    if any(p == WITH_PERMISSION for p in policies):         # 승인 필요가 있으면
        return WITH_PERMISSION                              # 승인 필요
    return WITHOUT_PERMISSION                               # 모두 자동 승인


_DANGEROUS_PIPE = ["sh", "bash", "zsh", "python", "perl", "ruby", "node"]   # 파이프 뒤 위험 명령


def _evaluate_pipe_chain(tokens: List[ParsedToken], start_idx: int) -> str:  # 파이프 뒤 명령을 검사하는 함수
    """evaluatePipeChain(:292-357) — 파이프 뒤 명령이 셸·인터프리터·네트워크 도구인지."""
    nxt = start_idx + 1                                      # 파이프 다음
    while nxt < len(tokens) and _is_operator(tokens[nxt]):   # 이어진 연산자 건너뜀
        nxt += 1                                            # 연속 연산자 건너뜀
    if nxt >= len(tokens):                                   # 뒤에 없음
        return WITHOUT_PERMISSION                           # 뒤 명령이 없으면 안전
    next_command: List[str] = []                             # 파이프 뒤 명령
    i = nxt                                                 # 뒤 명령 시작 위치
    while i < len(tokens) and not _is_operator(tokens[i]):   # 다음 연산자 전까지
        token = tokens[i]                                   # 현재 토큰
        if _is_comment(token):                              # 주석이면
            pass                                             # 주석 무시
        elif _is_glob(token):                               # glob이면
            next_command.append(token["pattern"])           # 패턴 글 추가
        elif isinstance(token, str):                        # 일반 글이면
            next_command.append(token)                      # 토큰 추가
        i += 1                                              # 다음 토큰으로
    if len(next_command) > 0:                               # 뒤 명령이 있으면
        command = next_command[0].lower()                    # 첫 토큰만 소문자
        if command in _DANGEROUS_PIPE:                       # 셸·인터프리터로 넘김
            return WITH_PERMISSION                          # 셸·인터프리터로 넘기면 승인 필요
        if command == "curl" or command == "wget" or command == "nc" or command == "netcat":   # 유출
            return WITH_PERMISSION                          # 네트워크 유출 도구면 승인 필요
    return WITHOUT_PERMISSION                               # 위에 해당 없으면 안전


def _evaluate_single_command(command_tokens: List[str], original: str) -> str:  # 명령 하나의 위험도 판정 함수
    """evaluateSingleCommand(:362-397) — critical → $변수 → high-risk → safe → 기본(승인 필요)."""
    if len(command_tokens) == 0:                            # 빈 명령은 안전
        return WITHOUT_PERMISSION                           # 안전으로 반환
    base = command_tokens[0].lower()                         # 첫 토큰만 소문자화
    args = command_tokens[1:]                                # 인자는 원문 그대로
    if _is_critical_command(base, args):                     # 치명적
        return DISABLED                                     # 치명적이면 금지
    if base.startswith("$"):                                 # 명령이 변수
        return WITH_PERMISSION                              # 변수 명령은 승인 필요
    if _is_high_risk_command(base, args, original):          # 고위험
        return WITH_PERMISSION                              # 고위험은 승인 필요
    if _is_safe_command(base, args):                         # 안전
        return WITHOUT_PERMISSION                           # 안전 명령은 자동 승인
    return WITH_PERMISSION                                   # 알 수 없는 명령


# ───────────────────────── 치명적 명령(:402-560) ─────────────────────────
_RM_CRITICAL_PATHS = ["/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/hosts", "/boot/", "/sys/", "/proc/",  # rm이 건드리면 안 되는 시스템 경로
                      "/dev/", "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/lib/", "/lib64/"]   # rm 대상 시스템 경로
_PRIV_ESC = ["sudo", "su", "doas", "runas", "gsudo", "psexec"]   # 권한 상승
_KERNEL = ["insmod", "modprobe", "rmmod", "iptables", "ip6tables", "nftables"]   # 커널·방화벽


def _is_critical_command(base: str, args: List[str]) -> bool:  # 치명적 명령 판정 함수
    """isCriticalCommand — True면 "disabled"."""
    if base.startswith("mkfs"):                              # 파일시스템 생성
        return True                                         # mkfs는 금지 대상
    all_tokens = [base] + args                               # 변수에 숨긴 rm도 잡으려고 전부 검사(명령 종류 무관)
    has_rf = (any(a == "-rf" or a == "-fr" or (a.startswith("-") and "r" in a and "f" in a) for a in all_tokens)  # 재귀·강제 옵션 조합 여부
              or ("-r" in all_tokens and "-f" in all_tokens)  # -r과 -f가 따로 있는 경우
              or ("--recursive" in all_tokens and "--force" in all_tokens))   # 재귀+강제(인자는 소문자화 안 함)
    has_dangerous_path = any(a == "/" or a == "/*" or a == "~" or a == "~/*" or a == "/usr" or a == "/etc"  # 루트·홈·시스템 경로 인자 여부
                             or a == "/bin" or a == "/sbin" or a.startswith("/usr/") or a.startswith("/etc/")  # 시스템 경로 접두어
                             or a.startswith("/bin/") or a.startswith("/sbin/") for a in all_tokens)   # 위험 경로
    if has_rf and has_dangerous_path:                        # 명령 종류와 무관하게 치명적
        return True                                         # 치명적이므로 금지
    if base == "rm":                                         # rm + 시스템 파일(부분 문자열)
        if any(any(p in a for p in _RM_CRITICAL_PATHS) for a in args):  # rm이 시스템 파일을 건드리는지
            return True                                     # 치명적
    if base == "del":                                        # Windows 삭제
        has_recursive = "/s" in args and "/q" in args        # 정확히 일치, 대소문자 구분
        has_system_drive = any("c:\\" in a.lower() or "c:/" in a for a in args)   # c:/ 는 대소문자 구분(원본대로)
        if has_recursive or has_system_drive:               # 재귀·시스템 드라이브면
            return True                                     # 치명적
    if base == "format" or base == "cipher":                 # 디스크 포맷·덮어쓰기
        return True                                         # 치명적
    if base == "dd":                                         # 디스크 직접 쓰기
        if any("of=/dev/" in a or "of=/dev/sda" in a or "of=/dev/disk" in a for a in args):  # 디스크 장치에 직접 쓰는지
            return True                                     # 치명적
    if base in _PRIV_ESC:                                    # 권한 상승
        return True                                         # 치명적
    if base == "chmod":                                      # 위험한 권한 변경
        if any(a == "777" or a == "776" or a == "775" or a == "+s" or a == "u+s" or a == "g+s" for a in args):  # 위험한 권한 값인지
            return True                                     # 치명적
    if base == "chown":                                      # root 소유로
        if any("root" in a for a in args):                  # root 소유 변경인지
            return True                                     # 치명적
    if base == "icacls":                                     # Windows 권한(Everyone 전체)
        if any("everyone:f" in a.lower() or "*s-1-1-0:(oi)(ci)f" in a for a in args):  # Everyone 전체 권한인지
            return True                                     # 치명적
    if base == "takeown":                                    # 소유권 가져오기
        return True                                         # 치명적
    if base in _KERNEL:                                      # 커널 모듈·방화벽
        return True                                         # 치명적
    if base == "eval" or base == "exec":                    # eval/exec는 금지 — eval / exec
        return True                                         # 치명적
    return False                                            # 해당 없음


# ───────────────────────── 고위험 명령(:565-974) ─────────────────────────
_PACKAGE_MANAGERS = ["npm", "yarn", "pnpm", "pip", "pip3", "gem", "cargo", "go", "apt", "apt-get", "yum", "dnf",  # 패키지 관리자 목록
                     "zypper", "pacman", "brew", "choco", "scoop", "winget"]   # 패키지 관리자
_INSTALL_CMDS = ["install", "add", "i"]                      # 설치 하위 명령
_NETWORK_TOOLS = ["curl", "wget", "nc", "netcat", "ncat", "telnet", "ssh", "scp", "rsync", "ftp", "sftp", "tftp", "socat"]   # 네트워크
_SCRIPT_INTERPRETERS = ["sh", "bash", "zsh", "fish", "ksh", "csh", "tcsh", "dash", "python", "python3", "python2",  # 스크립트 인터프리터 목록
                        "ruby", "perl", "php", "node", "nodejs", "deno", "bun", "lua", "tcl", "powershell", "pwsh"]   # 인터프리터
_DANGEROUS_VARS = ["PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "NODE_PATH", "PERL5LIB", "RUBYLIB"]   # 위험 환경 변수
_PROCESS_CMDS = ["kill", "killall", "pkill", "taskkill", "pskill"]   # 프로세스 종료
_SENSITIVE_LOCATIONS = ["/etc/", "/usr/", "/bin/", "/sbin/", "c:\\windows", "c:\\program"]   # 민감 위치
_CONTAINER_CMDS = ["docker", "podman", "kubectl", "helm", "terraform", "vagrant"]   # 컨테이너
_CLOUD_CMDS = ["aws", "gcloud", "az", "oci", "ibmcloud"]     # 클라우드
_USER_CMDS = ["useradd", "usermod", "userdel", "groupadd", "passwd", "chpasswd"]   # 사용자 관리
# 함수 정의 패턴(:1176-1180) — JS \s 집합, \w = ASCII, ^는 문자열 처음
_FUNCTION_PATTERNS = [                                      # 함수 정의 모양 정규식 목록
    re.compile("^[" + JS_WS + "]*[A-Za-z0-9_]+[" + JS_WS + r"]*\(\)[" + JS_WS + r"]*\{"),  # name() { 형태 — name() {
    re.compile("^[" + JS_WS + "]*function[" + JS_WS + "]+[A-Za-z0-9_]+[" + JS_WS + r"]*\{"),  # function name { 형태 — function name {
    re.compile("^[" + JS_WS + "]*function[" + JS_WS + "]+[A-Za-z0-9_]+[" + JS_WS + r"]*\(\)"),  # function name() 형태 — function name()
]                                                           # 목록 끝


def _is_high_risk_package_manager(base: str, args: List[str]) -> bool:  # 패키지 설치 판정
    """패키지 설치(첫 인자가 install/add/i)."""
    if base in _PACKAGE_MANAGERS:                           # 패키지 관리자이고
        if len(args) > 0 and args[0] in _INSTALL_CMDS:      # 첫 인자가 설치 명령이면
            return True                                     # 고위험
    return False                                            # 해당 없음


def _is_high_risk_script_interpreter(base: str, args: List[str]) -> bool:  # 인터프리터 실행 판정
    """인터프리터 실행(--help/--version 하나만이면 제외)."""
    if base in _SCRIPT_INTERPRETERS:                        # 인터프리터이면
        if len(args) == 1 and (args[0] == "--help" or args[0] == "--version"):   # 도움말·버전
            return False                                    # 도움말·버전은 안전
        if "-c" in args:                                     # 인라인 명령
            return True                                     # 인라인 명령은 고위험
        if len(args) > 0:                                    # 파일 실행
            return True                                     # 파일 실행은 고위험
    return False                                            # 해당 없음


def _is_high_risk_direct_script(base: str) -> bool:         # 스크립트 직접 실행 판정
    """./ 로 시작하거나 스크립트 확장자."""
    return (base.startswith("./") or base.endswith(".sh") or base.endswith(".py") or base.endswith(".rb")  # ./ 시작 또는 스크립트 확장자
            or base.endswith(".pl") or base.endswith(".ps1") or base.endswith(".bat") or base.endswith(".cmd"))  # 그 밖의 스크립트 확장자


def _is_high_risk_environment_modifier(base: str, args: List[str]) -> bool:  # 환경 변수 변경 판정
    """export/setx/set 으로 위험 변수 변경."""
    if base == "export" or base == "setx" or base == "set":  # 변수 설정 명령이면
        if any(any(v in a for v in _DANGEROUS_VARS) for a in args):  # 위험 변수를 건드리면
            return True                                     # 고위험
    return False                                            # 해당 없음


def _is_function_definition(tokens: List[str], original: str) -> bool:  # 함수 정의 판정
    """isFunctionDefinition(:1165-1187) — 첫 토큰 function 또는 원문이 함수 정의 모양."""
    if len(tokens) > 0 and tokens[0] == "function":         # function 키워드로 시작하면
        return True                                         # 함수 정의
    if any(p.search(original) for p in _FUNCTION_PATTERNS):  # 원문이 함수 정의 모양이면
        return True                                         # 함수 정의
    return False                                            # 해당 없음


def _is_high_risk_system_service(base: str) -> bool:        # 서비스 관리 명령 판정
    """서비스 관리."""
    return base == "systemctl" or base == "service" or base == "launchctl" or base == "sc"  # 서비스 관리 도구 이름이면 참


def _is_high_risk_file_operation(base: str, args: List[str]) -> bool:  # 민감 위치로의 파일 이동·복사 판정
    """민감 위치로 mv/cp/copy."""
    if base == "mv" or base == "cp" or base == "copy":      # mv/cp/copy이면
        if any(any(loc in a.lower() for loc in _SENSITIVE_LOCATIONS) for a in args):  # 민감 위치 인자가 있으면
            return True                                     # 고위험
    return False                                            # 해당 없음


def _is_high_risk_rm_command(base: str, args: List[str]) -> bool:  # 치명적이지 않은 rm 판정
    """critical이 아닌 rm(-rf도 시스템 경로도 없을 때)."""
    if base == "rm":                                        # rm이면
        has_rf = any(a == "-rf" or a == "-fr" for a in args)  # -rf 여부
        has_critical_path = any(any(p in a for p in ["/etc/", "/usr/", "/bin/", "/sbin/"]) for a in args)  # 시스템 경로 포함 여부
        if not has_rf and not has_critical_path:            # 둘 다 아니면
            return True                                     # 고위험
    return False                                            # 해당 없음


def _is_high_risk_archive_extraction(base: str, args: List[str]) -> bool:  # 시스템 경로로의 압축 풀기 판정
    """시스템 경로로 압축 풀기(-C/-d/-o 다음 인자 또는 -C/경로)."""
    if base == "tar" or base == "unzip" or base == "7z":    # 압축 도구이면
        def system_extraction(idx: int, a: str) -> bool:    # 인자 하나가 시스템 경로 추출인지 보는 도우미
            if (a == "-C" or a == "-d" or a == "-o") and idx < len(args) - 1:  # 출력 위치 옵션이면 — -C /path
                nxt = args[idx + 1]                         # 옵션 다음 인자
                return nxt.startswith("/") or nxt.startswith("C:\\")  # 절대 경로면 참
            return a.startswith("-C/") or a.startswith("-d/") or a.startswith("-o/")  # 붙여 쓴 옵션 형태도 확인 — -C/path
        if any(system_extraction(idx, a) for idx, a in enumerate(args)):  # 하나라도 해당하면
            return True                                     # 고위험
    return False                                            # 해당 없음


def _is_high_risk_history_manipulation(base: str, args: List[str]) -> bool:  # 기록 지우기 판정
    """기록 지우기."""
    if base == "history" and "-c" in args:                  # 기록 지우기(history -c)
        return True                                         # 고위험
    if base == "unset" and any("HIST" in a for a in args):  # HIST 변수 해제
        return True                                         # 고위험
    return False                                            # 해당 없음


def _is_high_risk_macos_command(base: str, args: List[str]) -> bool:  # macOS 시스템 설정 판정
    """macOS 시스템 설정."""
    if base == "defaults" and any("/Library/" in a for a in args):  # /Library 설정 변경
        return True                                         # 고위험
    if base == "pmset" or base == "csrutil":                # 전원·SIP 설정
        return True                                         # 고위험
    return False                                            # 해당 없음


def _is_high_risk_command(base: str, args: List[str], original: str) -> bool:  # 고위험 명령 종합 판정(원본 순서)
    """isHighRiskCommand(:942-974) — 원본 순서 그대로."""
    if _is_high_risk_package_manager(base, args): return True                       # noqa: E701 패키지 설치
    if base in _NETWORK_TOOLS: return True                                          # noqa: E701 네트워크 도구
    if _is_high_risk_script_interpreter(base, args): return True                    # noqa: E701 인터프리터
    if _is_high_risk_direct_script(base): return True                               # noqa: E701 스크립트 직접 실행
    if _is_high_risk_environment_modifier(base, args): return True                  # noqa: E701 환경 변수
    if base == "alias": return True                         # alias 정의 — noqa: E701 alias
    if _is_function_definition([base] + args, original): return True                # noqa: E701 함수 정의
    if base in _PROCESS_CMDS: return True                                           # noqa: E701 프로세스 종료
    if _is_high_risk_system_service(base): return True                              # noqa: E701 서비스
    if _is_high_risk_file_operation(base, args): return True                        # noqa: E701 민감 위치 이동·복사
    if _is_high_risk_rm_command(base, args): return True    # 치명적이지 않은 rm — noqa: E701 rm
    if _is_high_risk_archive_extraction(base, args): return True                    # noqa: E701 압축 풀기
    if base in _CONTAINER_CMDS: return True                                         # noqa: E701 컨테이너
    if base in _CLOUD_CMDS: return True                                             # noqa: E701 클라우드
    if base in _USER_CMDS: return True                                              # noqa: E701 사용자 관리
    if base == "crontab" or base == "at" or base == "schtasks": return True         # noqa: E701 예약 작업
    if base == "reg" or base == "regedit" or base == "regsvr32": return True        # noqa: E701 Windows 레지스트리
    if base == "wmic" or base == "net" or base == "netsh": return True              # noqa: E701 Windows 관리
    if base == "certutil" or base == "bitsadmin": return True                       # noqa: E701 보안 도구
    if base == "source" or base == ".": return True         # source 실행 — noqa: E701 source
    if _is_high_risk_history_manipulation(base, args): return True                  # noqa: E701 기록 조작
    if base == "dig" or base == "nslookup" or base == "host": return True  # DNS 조회 도구 — noqa: E701 DNS
    if _is_high_risk_macos_command(base, args): return True  # macOS 설정 명령 — noqa: E701 macOS
    return False                                            # 해당 없음


# ───────────────────────── 안전한 명령(:979-1095) ─────────────────────────
_INFO_CMDS = ["ls", "dir", "pwd", "whoami", "id", "hostname", "uname", "date", "uptime", "df", "du", "free", "top",  # 정보 조회 명령 목록
              "htop", "ps", "jobs", "which", "whereis", "type", "file", "stat", "wc", "head", "tail"]   # 정보 조회
_READ_CMDS = ["cat", "less", "more", "nl", "od", "strings"]   # 읽기 전용
_FIND_DANGEROUS = ["-exec", "-execdir", "-ok", "-okdir", "-delete"]   # find 위험 옵션
_SAFE_GIT = ["status", "log", "diff", "show", "branch", "remote"]   # 안전한 git(첫 인자만 확인)
_SAFE_NPM = ["test", "build", "start", "run"]                # 안전한 npm 계열
_SUSPICIOUS_SCRIPTS = ["preinstall", "postinstall", "prepare", "prepublish"]   # 의심 스크립트
_BUILD_CMDS = ["make", "gradle", "mvn", "cargo"]             # 빌드 도구
_SAFE_BUILD_OPS = ["build", "compile", "test", "check", "clean"]   # 안전한 빌드 작업(인자 어디든)


def _is_safe_command(base: str, args: List[str]) -> bool:   # 안전 명령 판정
    """isSafeCommand — 원본 특이 동작(npm은 인자 2개 이상, tar는 인자에 글자 c) 그대로."""
    if base in _INFO_CMDS:                                   # 정보 조회
        return True                                         # 정보 조회 명령
    if base in _READ_CMDS:                                   # 읽기
        return True                                         # 읽기 전용 명령
    if base == "echo":                                      # echo는 안전 — echo
        return True                                         # 안전
    if base == "grep" and "--exec" not in args:             # grep은 안전(--exec 제외) — grep
        return True                                         # 안전
    if base == "find":                                       # find(위험 옵션 없을 때)
        if not any(a in _FIND_DANGEROUS for a in args):     # 위험 옵션이 없으면
            return True                                     # 안전
    if base == "git":                                        # git 읽기 작업
        if len(args) > 0 and args[0] in _SAFE_GIT:          # 읽기 작업 하위 명령이면
            return True                                     # 안전
    if base == "npm" or base == "yarn" or base == "pnpm":    # 패키지 관리자의 안전한 스크립트: npm test/build/start/run
        if len(args) > 0 and args[0] in _SAFE_NPM:          # 안전한 스크립트 이름이면
            if len(args) > 1 and args[1] not in _SUSPICIOUS_SCRIPTS:   # 인자 2개 이상이어야 함(원본대로)
                return True                                 # 안전
    if base in _BUILD_CMDS:                                  # 빌드
        if any(a in _SAFE_BUILD_OPS for a in args):         # 안전한 빌드 작업이면
            return True                                     # 안전
    if base == "tar":                                        # 인자 어딘가에 글자 c → "생성"으로 봄
        if any("c" in a for a in args) and not any(         # tar에 c 글자가 있고
                (a == "-C" and idx < len(args) - 1 and args[idx + 1].startswith("/")) or a.startswith("-C/")  # 시스템 경로 -C 지정이 없으면
                for idx, a in enumerate(args)):             # 인자마다 확인
            return True                                     # 안전
    if base == "zip" and not any(a.startswith("-d/") for a in args):  # zip(-d/ 지정 없음) — zip
        return True                                         # 안전
    return False                                            # 해당 없음


# ───────────────────────── 명령 치환(:1100-1160) ─────────────────────────
_PROC_SUBST = re.compile(r"<\(|>\(")                         # 프로세스 치환
_BACKTICK = re.compile(r"`([^`]+)`")                         # 백틱 쌍


def _has_command_substitution(command: str) -> bool:        # 명령 치환 존재 판정
    """원문에 `, $(, <(, >( 가 있는지(따옴표 무시)."""
    if "`" in command:                                      # 백틱
        return True                                         # 치환 있음
    if "$(" in command:                                     # $( 형태
        return True                                         # 치환 있음
    if _PROC_SUBST.search(command):                         # 프로세스 치환
        return True                                         # 치환 있음
    return False                                            # 치환 없음


def _extract_substituted_commands(command: str) -> List[str]:  # 치환된 명령을 꺼내는 함수
    """백틱 안과 $( ) 안의 명령을 꺼낸다($( 는 괄호 깊이로, 짝이 안 맞으면 버림)."""
    commands: List[str] = []                                # 추출 결과 목록
    commands.extend(_BACKTICK.findall(command))              # 백틱(중첩 없음)
    idx = 0                                                 # 탐색 위치
    while idx < len(command):                               # $( 를 모두 찾을 때까지 반복 — $( ... )
        dollar = command.find("$(", idx)                    # 다음 $( 위치
        if dollar == -1:                                    # 더 없으면
            break                                           # 반복 종료
        depth = 1                                            # 괄호 깊이
        end = dollar + 2                                    # 괄호 안쪽 시작 위치
        while end < len(command) and depth > 0:             # 괄호 깊이가 0이 될 때까지
            if command[end] == "(":                         # 여는 괄호
                depth += 1                                  # 깊이 증가
            elif command[end] == ")":                       # 닫는 괄호
                depth -= 1                                  # 깊이 감소
            end += 1                                        # 다음 글자로
        if depth == 0:                                       # 짝이 맞음
            extracted = command[dollar + 2:end - 1]          # 안쪽 명령
            commands.append(extracted)                      # 안쪽 명령 추가
            if "$(" in extracted or "`" in extracted:        # 중첩 치환
                commands.extend(_extract_substituted_commands(extracted))  # 중첩 치환 재귀 추출
        idx = end                                           # 탐색 위치 이동
    return commands                                         # 추출한 명령 목록 반환


# ───────────────────────── 난독화(:1192-1241) ─────────────────────────
_OBF_BASE64 = re.compile("base64[" + JS_WS + "]+-d|base64[" + JS_WS + "]+--decode")   # base64 디코드
_OBF_HEX = re.compile(r"\\x[0-9a-f]{2}", re.I | re.A)       # 16진 이스케이프 패턴 — \xHH
_OBF_UNICODE = re.compile(r"\\u[0-9a-f]{4}", re.I | re.A)   # 유니코드 이스케이프 패턴 — \uHHHH
_OBF_OCTAL = re.compile(r"\\[0-7]{3}")                      # 8진 이스케이프 패턴 — \OOO
_OBF_ESCAPE = re.compile(r"\\[xnu0-7]")                      # echo -e / printf 이스케이프


def _has_obfuscation_patterns(command: str) -> bool:        # 난독화 패턴 판정
    """원문 부분 문자열 기준(od 검사가 넓게 걸리는 것도 원본대로)."""
    if _OBF_BASE64.search(command):                         # base64 디코드
        return True                                         # 난독화
    if _OBF_HEX.search(command):                            # 16진
        return True                                         # 난독화
    if _OBF_UNICODE.search(command):                        # 유니코드
        return True                                         # 난독화
    if _OBF_OCTAL.search(command):                          # 8진
        return True                                         # 난독화
    if "echo" in command and "-e" in command and _OBF_ESCAPE.search(command):  # echo -e 이스케이프 — echo -e
        return True                                         # 난독화
    if "printf" in command and _OBF_ESCAPE.search(command):  # printf 이스케이프 — printf
        return True                                         # 난독화
    if "xxd" in command and "-r" in command:                # xxd 역변환 — xxd -r
        return True                                         # 난독화
    if "od" in command and ("-x" in command or "-o" in command):  # od 덤프 옵션 — od -x / -o
        return True                                         # 난독화
    return False                                            # 해당 없음
