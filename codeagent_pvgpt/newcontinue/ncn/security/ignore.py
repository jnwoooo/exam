# ncn/security/ignore.py — 민감 파일 판정(Read·Edit·MultiEdit 차단). cn core/indexing/ignore.ts + npm "ignore" 7.0.5 판정기
# [ncn E7] cn 목록 다음에 키 파일 패턴(ncn/security/secrets.py EXTRA_BLOCK_FILES + 설정 block_files)도 확인한다 — 오류 글은 cn과 같다.
# cn은 gitignore 문법 판정기(npm ignore, 대소문자 무시)를 쓴다. 같은 결과를 내려고 그 패키지의 패턴→정규식 변환과
# 부모 디렉터리 확인 규칙을 그대로 옮겼다(pathspec 은 대소문자·오류 글이 달라 쓰지 않음)
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # [ncn 부록B S5] 작업 폴더
import re                                                    # 정규식
from urllib.parse import unquote, urlsplit                   # file:// URI → 경로

from ncn.compat import cn_compat                             # [ncn 부록B S5] cn 원래 동작 스위치
from ncn.tools.base import ContinueError                     # cn ContinueError
from ncn.tools.formatters import (JS_WS_CLASS, node_platform, path_basename,   # JS 공백·실행 OS·Node path
                                  path_dirname, path_is_absolute, path_relative, path_resolve)  # 상대경로·절대경로 함수([ncn 부록B S5])

# ───────────────────────── core/indexing/ignore.ts:8-91 차단 목록(원문 순서) ─────────────────────────
DEFAULT_SECURITY_IGNORE_FILETYPES = [                       # 민감 파일 이름 패턴 목록(cn 원문 순서)
    # Environment and configuration files with secrets
    "*.env",                                                # 환경설정 파일(.env 로 끝남)
    "*.env.*",                                              # 환경설정 파일(.env.변형)
    ".env*",                                                # 환경설정 파일(.env 로 시작)
    "config.json",                                          # 설정 파일
    "config.yaml",                                          # 설정 파일(yaml)
    "config.yml",                                           # 설정 파일(yml)
    "settings.json",                                        # 설정 파일(settings)
    "appsettings.json",                                     # .NET 설정 파일
    "appsettings.*.json",                                   # .NET 환경별 설정 파일
    # Certificate and key files
    "*.key",                                                # 개인 키
    "*.pem",                                                # PEM 인증서·키
    "*.p12",                                                # PKCS12 키 저장소
    "*.pfx",                                                # PFX 키 저장소
    "*.crt",                                                # 인증서
    "*.cer",                                                # 인증서(cer)
    "*.jks",                                                # Java 키 저장소
    "*.keystore",                                           # Java 키스토어
    "*.truststore",                                         # Java 신뢰 저장소
    # Database files that may contain sensitive data
    "*.db",                                                 # 데이터베이스 파일
    "*.sqlite",                                             # SQLite 파일
    "*.sqlite3",                                            # SQLite3 파일
    "*.mdb",                                                # Access 파일
    "*.accdb",                                              # Access 파일(accdb)
    # Credential and secret files
    "*.secret",                                             # 비밀 파일
    "*.secrets",                                            # 비밀 파일(복수형)
    "auth.json",                                            # 인증 정보 파일
    "*.token",                                              # 토큰 파일
    # Backup files that might contain sensitive data
    "*.bak",                                                # 백업 파일
    "*.backup",                                             # 백업 파일(backup)
    "*.old",                                                # 옛 파일
    "*.orig",                                               # 병합 원본 파일
    # Docker secrets
    "docker-compose.override.yml",                          # 도커 오버라이드 파일(yml)
    "docker-compose.override.yaml",                         # 도커 오버라이드 파일(yaml)
    # SSH and GPG
    "id_rsa",                                               # SSH RSA 키
    "id_dsa",                                               # SSH DSA 키
    "id_ecdsa",                                             # SSH ECDSA 키
    "id_ed25519",                                           # SSH ED25519 키
    "*.ppk",                                                # PuTTY 키
    "*.gpg",                                                # GPG 파일
]                                                           # 목록 닫기

DEFAULT_SECURITY_IGNORE_DIRS = [                            # 민감 폴더 패턴 목록(끝 '/' 는 폴더만)
    # Environment and configuration directories
    ".env/",                                                # 환경 폴더(.env)
    "env/",                                                 # 환경 폴더(env)
    # Cloud provider credential directories
    ".aws/",                                                # AWS 자격 폴더
    ".gcp/",                                                # GCP 자격 폴더
    ".azure/",                                              # Azure 자격 폴더
    ".kube/",                                               # 쿠버네티스 설정 폴더
    ".docker/",                                             # 도커 설정 폴더
    # Secret directories
    "secrets/",                                             # 비밀 폴더
    ".secrets/",                                            # 숨김 비밀 폴더
    "private/",                                             # 비공개 폴더
    ".private/",                                            # 숨김 비공개 폴더
    "certs/",                                               # 인증서 폴더
    "certificates/",                                        # 인증서 폴더(긴 이름)
    "keys/",                                                # 키 폴더
    ".ssh/",                                                # SSH 폴더
    ".gnupg/",                                              # GnuPG 폴더
    ".gpg/",                                                # GPG 폴더
    # Temporary directories that might contain sensitive data
    "tmp/secrets/",                                         # 임시 폴더 안 비밀
    "temp/secrets/",                                        # temp 안 비밀
    ".tmp/",                                                # 숨김 임시 폴더
]                                                           # 목록 닫기

# ───────────────────────── npm ignore 7.0.5 — 패턴 → 정규식(REPLACERS 그대로) ─────────────────────────
_WS = JS_WS_CLASS                                           # JS 공백 클래스
_BOM = chr(0xFEFF)                                           # 바이트 순서 표시


def _sanitize_range(rng: str) -> str:                       # 거꾸로 된 범위 정리
    """sanitizeRange — 거꾸로 된 범위(z-a)는 지움."""
    return re.sub(r"([0-z])-([0-z])", lambda m: m.group(0) if ord(m.group(1)) <= ord(m.group(2)) else "", rng)  # z-a 같은 범위는 지움


def _clean_range_backslash(slashes: str) -> str:            # 역슬래시를 짝수 개만 남김
    """cleanRangeBackSlash — 짝수 개만 남김."""
    n = len(slashes)                                        # 역슬래시 개수
    return slashes[:n - n % 2]                              # 짝수 개만 반환


def _r_trailing_spaces(m: re.Match) -> str:                 # 끝 공백 제거 규칙
    """끝 공백 제거(\\ 로 감싼 공백은 하나 남김)."""
    return m.group(1) + (" " if m.group(2).startswith("\\") else "")  # \\ 로 감싼 공백은 하나 남김


def _r_escaped_space(m: re.Match) -> str:                   # 이스케이프된 공백 규칙
    """(\\ ) → ' '."""
    n = len(m.group(1))                                     # 역슬래시 개수
    return m.group(1)[:n - n % 2] + " "                     # 짝수 개와 공백 하나 반환


def _r_double_star(m: re.Match) -> str:                     # '/**' 규칙
    """'/**' 처리 — 마지막이 아니면 0개 이상 폴더, 마지막이면 안의 전부."""
    return r"(?:\/[^\/]+)*" if m.start() + 6 < len(m.string) else r"\/.+"  # 마지막이 아니면 0개 이상 폴더, 마지막이면 안의 전부


def _r_intermediate_wildcard(m: re.Match) -> str:           # 중간 '*' 규칙
    """중간 '*' → '[^/]*'."""
    return m.group(1) + m.group(2).replace("\\*", r"[^\/]*")  # '*' 를 슬래시 아닌 글자들로


def _r_range(m: re.Match) -> str:                           # 범위 [a-z] 규칙
    """[a-z] 범위 표기."""
    lead, rng, end_escape, close = m.group(1), m.group(2), m.group(3), m.group(4)  # 묶음 조각을 꺼냄
    if lead == "\\":                                         # '\[bar]' → 글자 그대로
        return "\\[" + rng + _clean_range_backslash(end_escape) + close  # 글자 그대로 취급
    if close == "]":                                         # 정상 범위
        if len(end_escape) % 2 == 0:                        # 이스케이프 개수가 짝수면
            return "[" + _sanitize_range(rng) + end_escape + "]"  # 정리한 범위 반환
        return "[]"                                          # 잘못된 범위
    return "[]"                                             # 닫는 괄호가 없으면 아무것도 안 맞음


def _r_ending(m: re.Match) -> str:                          # 패턴 끝 처리 규칙
    """끝 처리 — 'foo/' 는 폴더만, 'foo' 는 'foo' 와 'foo/'."""
    match = m.group(0)                                      # 일치한 끝 글자
    return match + "$" if match.endswith("/") else match + r"(?=$|\/$)"  # 슬래시로 끝나면 폴더만, 아니면 파일·폴더 모두


# (정규식, 바꿀 것, 횟수: 0=전부(g), 1=처음만). 순서·내용은 ignore/index.js REPLACERS 와 같음. JS $ → Python \Z
_REPLACERS = [                                              # 패턴→정규식 변환 규칙표
    ("^" + _BOM, lambda m: "", 1),                           # BOM 제거
    (r"((?:\\\\)*?)(\\?" + _WS + r"+)\Z", _r_trailing_spaces, 1),   # 끝 공백
    (r"(\\+?)" + _WS, _r_escaped_space, 0),                 # 이스케이프된 공백
    (r"[\\$.|*+(){^]", lambda m: "\\" + m.group(0), 0),      # 정규식 특수 글자 이스케이프
    (r"(?!\\)\?", lambda m: "[^/]", 0),                      # ? → 한 글자
    (r"^/", lambda m: "^", 1),                               # 앞 슬래시 = 처음부터
    (r"/", lambda m: r"\/", 0),                              # 슬래시 이스케이프
    (r"^\^*\\\*\\\*\\/", lambda m: r"^(?:.*\/)?", 1),        # 앞 '**/'
    None,                                                    # 시작 처리(패턴 원문이 필요해 아래에서 따로)
    (r"\\/\\\*\\\*(?=\\/|\Z)", _r_double_star, 0),          # '/**'
    (r"(^|[^\\]+)(\\\*)+(?=.+)", _r_intermediate_wildcard, 0),   # 중간 '*'
    (r"\\\\\\(?=[$.|*+(){^])", lambda m: "\\", 0),           # 이스케이프 되돌림
    (r"\\\\", lambda m: "\\", 0),                           # 역슬래시 두 개를 하나로
    (r"(\\)?\[([^\]/]*?)(\\*)(\Z|\])", _r_range, 0),         # 범위
    (r"(?:[^*])\Z", _r_ending, 1),                           # 끝
]                                                           # 규칙표 닫기


def _make_regex_prefix(pattern: str) -> str:                # 패턴을 정규식 앞부분으로 변환
    """makeRegexPrefix — REPLACERS 를 차례로 적용."""
    s = pattern                                             # 패턴 원문에서 시작
    for item in _REPLACERS:                                 # 규칙을 차례로
        if item is None:                                     # 시작 처리: 앞/가운데 슬래시가 없으면 어느 깊이든
            if re.match(r"[^^]", s):                         # /^(?=[^^])/ — 이미 '^' 로 시작하지 않을 때만
                s = ("(?:^|\\/)" if not re.search(r"/(?!\Z)", pattern) else "^") + s  # 슬래시가 없으면 어느 깊이든 맞게 접두 추가
            continue                                        # 다음 규칙으로
        rx, repl, count = item                              # 규칙 풀기
        s = re.sub(rx, repl, s, count=count)                 # JS replace(g 면 전부)
    return s                                                # 변환 결과 반환


def _trailing_wildcard(m: re.Match) -> str:                 # 끝 '*' 규칙
    """TRAILING_WILD_CARD_REPLACERS.regex — 끝 '*'."""
    p1 = m.group(1)                                         # 앞 조각
    prefix = f"{p1}[^/]+" if p1 else "[^/]*"                 # 'abc/*' 는 'abc/' 와 안 맞음
    return prefix + r"(?=$|\/$)"                            # 끝 조건을 붙여 반환


def _js_regex_to_python(src: str) -> str:                   # JS 정규식을 파이썬용으로 변환
    """만든 JS 정규식 글을 Python 용으로 — 묶음 밖 '$' → '\\Z', 빈 묶음 '[]' → 안 맞음."""
    out = []                                                 # 결과 조각
    i = 0                                                   # 읽는 위치
    in_class = False                                         # [...] 안인가
    while i < len(src):                                     # 글자마다
        c = src[i]                                          # 지금 글자
        if c == "\\":                                        # 이스케이프는 두 글자 그대로
            out.append(src[i:i + 2])                        # 이스케이프는 두 글자 그대로 복사
            i += 2                                          # 두 글자 건너뜀
            continue                                        # 다음 반복으로
        if not in_class:                                    # 묶음 밖이면
            if c == "[":                                     # 묶음 시작
                if src[i + 1:i + 2] == "]":                  # JS '[]' = 아무것도 안 맞음
                    out.append("(?!)")                      # 빈 묶음은 아무것도 안 맞음
                    i += 2                                  # 두 글자 건너뜀
                    continue                                # 다음 반복으로
                in_class = True                             # 묶음 안으로 들어감
                out.append(c)                               # 여는 괄호 복사
                if src[i + 1:i + 2] == "^":                  # 부정 묶음
                    out.append("^")                         # 부정 표시 복사
                    i += 1                                  # 한 글자 건너뜀
                i += 1                                      # 위치 이동
                continue                                    # 다음 반복으로
            out.append(r"\Z" if c == "$" else c)             # JS $(m 없음) = 글 끝
        else:                                               # 묶음 안이면
            if c == "]":                                     # 묶음 끝
                in_class = False                            # 묶음 밖으로 나옴
                out.append(c)                               # 닫는 괄호 복사
            elif c == "[":                                   # Python 중첩 묶음 경고 방지
                out.append("\\[")                           # 이스케이프해서 복사
            else:                                           # 그 밖의 글자는
                out.append(c)                               # 그대로 복사
        i += 1                                              # 다음 글자로
    return "".join(out)                                     # 변환 결과 반환


class _IgnoreRule:                                          # 패턴 하나의 규칙 객체
    """IgnoreRule — 패턴 하나."""

    def __init__(self, pattern: str, ignore_case: bool):    # 패턴과 대소문자 무시 여부로 만든다
        self.pattern = pattern                               # 원문
        body = pattern                                      # 본문(부정 표시 제외)
        self.negative = False                                # '!' 부정 패턴
        if body.startswith("!"):                            # '!' 로 시작하면
            self.negative = True                            # 부정 패턴 표시
            body = body[1:]                                 # '!' 를 뗀 본문
        body = re.sub(r"^\\!", "!", body, count=1)           # '\!' → '!'
        body = re.sub(r"^\\#", "#", body, count=1)           # '\#' → '#'
        prefix = _make_regex_prefix(body)                    # 정규식 앞부분
        js_src = re.sub(r"(^|\\/)?\\\*\Z", _trailing_wildcard, prefix, count=1)   # 끝 '*' 처리(ignores 모드)
        self.js_source = js_src                              # 비교 시험용(Node 결과와 같아야 함)
        self.regex = re.compile(_js_regex_to_python(js_src), re.IGNORECASE if ignore_case else 0)   # 'i' 플래그


def _check_pattern(p: object) -> bool:                      # 검사할 만한 패턴인지 판정
    """checkPattern — 빈 줄·주석·끝 역슬래시 패턴 제외."""
    return (isinstance(p, str) and bool(p)                  # 문자열이고 비어 있지 않고
            and not re.match("^" + _WS + r"+\Z", p)         # 공백뿐이 아니고
            and not re.search(r"(?:[^\\]|^)\\\Z", p)        # 끝 역슬래시가 없고
            and not p.startswith("#"))                      # 주석('#')이 아님


class Ignore:                                               # npm ignore 판정기 흉내
    """npm ignore 의 Ignore(ignores 만) — 규칙 목록 + 부모 디렉터리 우선 판정."""

    def __init__(self, ignore_case: bool = True):           # 대소문자 무시 여부로 만든다
        self._ignore_case = ignore_case                      # 기본 대소문자 무시(ignore 기본값)
        self._rules: list[_IgnoreRule] = []                  # 규칙
        self._cache: dict[str, dict] = {}                    # ignores 결과 저장(cn과 같이 프로세스 동안 유지)

    def add(self, patterns) -> "Ignore":                    # 패턴을 추가
        """add — 글(줄바꿈 구분)·목록·다른 Ignore."""
        if isinstance(patterns, Ignore):                     # 다른 판정기의 규칙을 이어 붙임
            self._rules.extend(patterns._rules)             # 규칙을 이어 붙임
            self._cache = {}                                # 저장 결과 버림
            return self                                     # 자기 자신 반환
        if isinstance(patterns, str):                        # 여러 줄 글
            patterns = [x for x in re.split(r"\r?\n", patterns) if x]  # 줄로 나눔(빈 줄 제외)
        added = False                                       # 추가 여부
        for p in patterns:                                  # 패턴마다
            if isinstance(p, Ignore):                       # 판정기가 섞여 있으면
                self._rules.extend(p._rules)                # 규칙을 이어 붙임
                added = True                                # 추가됨 표시
            elif _check_pattern(p):                         # 유효한 패턴이면
                self._rules.append(_IgnoreRule(p, self._ignore_case))  # 규칙으로 만들어 추가
                added = True                                # 추가됨 표시
        if added:                                           # 규칙이 바뀌었으면
            self._cache = {}                                 # 규칙이 바뀌면 저장 결과 버림
        return self                                         # 자기 자신 반환

    def _test_one(self, path: str) -> dict:                 # 부모 확인 없이 이 경로만 판정
        """RuleManager.test(checkUnignored=false) — 부모 확인 없이 이 경로만."""
        ignored = False                                     # 무시됨 여부
        unignored = False                                   # 무시 해제됨 여부
        for rule in self._rules:                            # 규칙마다
            neg = rule.negative                             # 부정 규칙인지
            if (unignored == neg and ignored != unignored) or (neg and not ignored and not unignored):   # 건너뛰는 경우(표 그대로)
                continue                                    # 건너뛰는 경우
            if not rule.regex.search(path):                  # 안 맞음
                continue                                    # 다음 규칙으로
            ignored = not neg                               # 부정이 아니면 무시됨
            unignored = neg                                 # 부정이면 해제됨
        return {"ignored": ignored, "unignored": unignored}  # 판정 결과 반환

    def _t(self, path: str, slices: list[str] | None = None) -> dict:  # 부모 폴더를 먼저 확인
        """_t — 부모 폴더('a/', 'a/b/')가 먼저 걸리면 그 결과."""
        if path in self._cache:                              # 저장된 결과
            return self._cache[path]                        # 그대로 반환
        if slices is None:                                  # 조각이 없으면
            slices = [x for x in path.split("/") if x]      # 경로를 조각으로 나눔
        slices.pop() if slices else None                     # 마지막 조각 제외
        if not slices:                                       # 부모 없음
            r = self._test_one(path)                        # 이 경로만 판정
            self._cache[path] = r                           # 결과 저장
            return r                                        # 결과 반환
        parent = self._t("/".join(slices) + "/", slices)     # 부모 먼저(slices 를 같이 줄여 감 — JS와 같음)
        r = parent if parent["ignored"] else self._test_one(path)  # 부모가 무시됐으면 그 결과, 아니면 이 경로 판정
        self._cache[path] = r                               # 결과 저장
        return r                                            # 결과 반환

    def ignores(self, path: str) -> bool:                   # 경로가 무시 대상인지 판정
        """ignores — 경로 검사 후 판정. 상대경로가 아니면 RangeError 글로 오류(cn에서 그대로 모델에게 감)."""
        converted = _convert(path) if path else path        # Windows 면 구분자 변환
        _check_path(converted, path)                         # 형식 검사
        return self._t(converted)["ignored"]                # 판정 결과 반환


class IgnorePathError(ContinueError):                       # 경로 형식 오류
    """npm ignore 의 TypeError/RangeError — cn에서는 일반 Error 라 reason 은 unknown, 글만 모델에게 감."""

    def __init__(self, message: str):                       # 메시지로 만든다
        super().__init__("unknown", message)                # 이유 코드는 unknown


_INVALID_PATH = re.compile(r"^\.{0,2}/|^\.{1,2}\Z")         # 잘못된 경로 형식 정규식
_WIN_ABS = re.compile(r"^[a-z]:/", re.IGNORECASE)           # Windows 드라이브 경로 정규식


def _convert(p: str) -> str:                                # Windows 구분자 변환
    """checkPath.convert — win32 에서만 '\\' → '/'(\\\\?\\ 접두나 금지 글자가 있으면 그대로)."""
    if node_platform() != "win32":                           # posix 는 그대로
        return p                                            # 그대로 반환
    if re.match(r"^\\\\\?\\", p) or re.search(r'["<>|\x00-\x1f]+', p):   # 변환 안 함
        return p                                            # 그대로 반환
    return p.replace("\\", "/")                             # 구분자를 '/' 로


def _check_path(path: object, original: object) -> None:    # 경로 형식 검사
    """checkPath — 글 아님·빈 글·상대경로 아님이면 오류(글은 ignore 원문)."""
    if not isinstance(path, str):                           # 문자열이 아니면
        raise IgnorePathError(f"path must be a string, but got `{original}`")  # 형식 오류
    if not path:                                            # 비어 있으면
        raise IgnorePathError("path must not be empty")     # 빈 경로 오류
    not_relative = bool(_INVALID_PATH.search(path))          # 기본 규칙
    if node_platform() == "win32" and _WIN_ABS.search(path):   # Windows 절대경로
        not_relative = True                                 # 상대경로 아님 표시
    if not_relative:                                        # 상대경로가 아니면
        raise IgnorePathError(f'path should be a `path.relative()`d string, but got "{original}"')  # 형식 오류


# ───────────────────────── core/indexing/ignore.ts:205-264 ─────────────────────────
default_security_ignore_file = Ignore().add(DEFAULT_SECURITY_IGNORE_FILETYPES)   # 파일 패턴
default_security_ignore_dir = Ignore().add(DEFAULT_SECURITY_IGNORE_DIRS)         # 폴더 패턴
default_file_and_folder_security_ignores = Ignore().add(default_security_ignore_file).add(default_security_ignore_dir)   # 합친 판정기


def _file_url_to_path(s: str) -> str:                       # file: URL 을 경로로 변환
    """Node url.fileURLToPath — file: URL 이 아니면 오류(호출한 쪽이 무시하고 원래 글을 씀)."""
    parts = urlsplit(s)                                      # URL 나누기
    if parts.scheme.lower() != "file":                       # file: 만
        raise ValueError("ERR_INVALID_URL_SCHEME")          # 오류
    path = parts.path or "/"                                 # 'file:foo' 도 '/foo' 로(WHATWG)
    if not path.startswith("/"):                            # '/' 로 시작하지 않으면
        path = "/" + path                                   # '/' 를 붙임
    host = parts.hostname or ""                              # 호스트
    if node_platform() == "win32":                          # Windows 면
        if re.search(r"%2f|%5c", path, re.IGNORECASE):       # 인코딩된 구분자 금지
            raise ValueError("ERR_INVALID_FILE_URL_PATH")   # 오류
        path = unquote(path).replace("/", "\\")              # 구분자 '\'
        if host and host != "localhost":                    # 호스트가 있으면 UNC
            return f"\\\\{host}{path}"                      # UNC 경로 반환
        if not re.match(r"^\\[a-zA-Z]:", path):              # 'C:' 필요
            raise ValueError("ERR_INVALID_FILE_URL_PATH")   # 오류
        return path[1:]                                     # 앞 '\\' 를 떼고 반환
    if host and host != "localhost":                         # posix 는 호스트 금지
        raise ValueError("ERR_INVALID_FILE_URL_HOST")       # 오류
    if re.search(r"%2f", path, re.IGNORECASE):               # 인코딩된 '/' 금지
        raise ValueError("ERR_INVALID_FILE_URL_PATH")       # 오류
    return unquote(path)                                    # 인코딩을 풀어 반환


_DARWIN_FIRMLINK = re.compile(r"^private/(?:tmp|var|etc)(?:/|$)")   # [ncn 부록B S5] macOS /tmp·/var·/etc 의 실제 위치(/private/...) 앞부분


def _full_relative_pieces(abs_path: str) -> str:            # [ncn 부록B S5] 절대경로를 "조각 전체" 상대 글로 바꾸는 함수
    """절대경로 → 판정용 상대 글(조각 전체). 작업 폴더(cwd) 안이면 cwd 기준 상대경로, 밖이면 루트·드라이브를 뗀 전체 조각.
    macOS 의 '/private/tmp|var|etc' 는 '/tmp' 등의 실제 위치라 앞의 'private/' 를 뗀다('private/' 폴더 패턴에 다 걸리지 않게)."""
    resolved = path_resolve(abs_path)                        # '..'·중복 구분자 정리(Node path.resolve)
    for base in {os.getcwd(), os.path.realpath(os.getcwd())}:   # 작업 폴더(링크 푼 경로도 — S4 실경로와 맞추려고)
        rel = path_relative(base, resolved)                  # 작업 폴더 기준 상대경로
        outside = rel == ".." or rel.startswith("../") or rel.startswith("..\\")   # 위 폴더로 나가면 작업 폴더 밖
        if rel and not outside and not path_is_absolute(rel):   # 작업 폴더 안(다른 드라이브면 절대경로가 나옴)
            return rel.replace("\\", "/")                   # 구분자를 '/' 로(판정기 형식)
    pieces = re.sub(r"^(?:[A-Za-z]:)?[/\\]+", "", resolved).replace("\\", "/")   # 밖이면 루트('/', 'C:\\', UNC '\\\\')를 뗀 전체
    if node_platform() == "darwin" and _DARWIN_FIRMLINK.match(pieces):   # macOS 의 /private/tmp 등
        pieces = pieces[len("private/"):]                    # 앞 'private/' 를 뗌
    return pieces                                           # 판정용 상대 글 반환


def is_security_concern(file_path_or_uri: str) -> bool:     # 민감 파일 여부 판정
    """isSecurityConcern — 절대경로는 '부모 폴더 이름/파일 이름' 두 조각으로 줄여 판정, 상대경로는 전체로.
    [ncn 부록B S5] 고친 동작: 절대경로는 cn 두 조각 판정 + 조각 전체(_full_relative_pieces) 판정 중 하나라도 걸리면 민감.
    cn 원래 동작('/p/secrets/sub/a.txt' → 'sub/a.txt' 만 봐서 통과)은 NCN_CN_COMPAT=1."""
    if not file_path_or_uri:                                 # 빈 값
        return False                                        # 민감하지 않음
    filepath = file_path_or_uri                             # 검사할 경로
    try:                                                    # file:// 변환 시도
        filepath = _file_url_to_path(file_path_or_uri)       # file:// 이면 경로로
    except Exception:                                        # 아니면 그대로
        pass                                                # 원래 글 사용
    full_pieces = ""                                        # [ncn 부록B S5] 조각 전체 판정용 글(상대경로면 쓰지 않음)
    if path_is_absolute(filepath):                           # 절대경로
        if not cn_compat():                                 # [ncn 부록B S5] 고친 동작에서만
            full_pieces = _full_relative_pieces(filepath)   # 조각 전체(작업 폴더 기준 등)
        d = re.split(r"/|\\", path_dirname(filepath))[-1]    # 바로 위 폴더 이름
        base = path_basename(filepath)                       # 파일 이름
        filepath = f"{d + '/' if d else ''}{base}"          # '폴더/파일' 두 조각으로 줄임
    if full_pieces and default_file_and_folder_security_ignores.ignores(full_pieces):   # [ncn 부록B S5] 조각 전체 중 민감 폴더·파일이 있으면
        return True                                         # 민감(cn 두 조각 판정보다 넓게)
    if not filepath:                                        # 비면
        return False                                        # 민감하지 않음
    if default_file_and_folder_security_ignores.ignores(filepath):   # cn 목록에 걸리면
        return True                                         # 민감
    extra = _ncn_extra_block_ignore()                       # [ncn E7] 추가 키 파일 패턴(id_rsa*·.netrc 등 + ncn.yaml·정책의 block_files)
    if extra is None:                                       # 꺼짐(NCN_CN_COMPAT=1·secrets.enabled:false)
        return False                                        # cn과 같은 결과
    return bool(extra.ignores(filepath) or (full_pieces and extra.ignores(full_pieces)))   # 두 조각·조각 전체 중 하나라도 걸리면 민감


def _ncn_extra_block_ignore():                              # [ncn E7] 추가 판정기 얻기
    """ncn/security/secrets.py extra_block_ignore — 읽기 실패는 기본 패턴 판정기로(안전 쪽)."""
    from ncn.security import secrets                        # 지연 import(순환 방지)
    try:                                                    # 설정 읽기
        return secrets.extra_block_ignore()                 # 판정기 또는 None
    except Exception:                                       # 실패
        return Ignore().add(secrets.EXTRA_BLOCK_FILES)      # 기본 패턴


def security_concern_error(filepath: str) -> ContinueError:  # 민감 파일 오류 객체를 만드는 함수
    """cn 원문 글의 민감 파일 오류(throwIfFileIsSecurityConcern 과 같은 글) — [ncn 부록B S4·S6] 실경로·Write 판정에서도 같은 글을 쓰려고 나눔."""
    return ContinueError(                                   # cn 원문 메시지로 오류
        "file_is_security_concern",                         # 이유 코드
        f"Reading or Editing {filepath} is not allowed because it is a security concern. Do not attempt to read or edit this file in any way.",  # 안내 메시지(cn 원문)
    )                                                       # 오류 닫기


def throw_if_file_is_security_concern(filepath: str) -> None:  # 민감 파일이면 오류를 낸다
    """throwIfFileIsSecurityConcern — 민감 파일이면 cn 원문 글로 ContinueError."""
    if is_security_concern(filepath):                       # 민감 파일이면
        raise security_concern_error(filepath)              # cn 원문 메시지로 오류
