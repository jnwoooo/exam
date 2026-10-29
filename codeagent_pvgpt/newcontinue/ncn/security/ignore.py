# ncn/security/ignore.py — 민감 파일 판정(Read·Edit·MultiEdit 차단). cn core/indexing/ignore.ts + npm "ignore" 7.0.5 판정기
# cn은 gitignore 문법 판정기(npm ignore, 대소문자 무시)를 쓴다. 같은 결과를 내려고 그 패키지의 패턴→정규식 변환과
# 부모 디렉터리 확인 규칙을 그대로 옮겼다(pathspec 은 대소문자·오류 글이 달라 쓰지 않음)
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # 정규식
from urllib.parse import unquote, urlsplit                   # file:// URI → 경로

from ncn.tools.base import ContinueError                     # cn ContinueError
from ncn.tools.formatters import (JS_WS_CLASS, node_platform, path_basename,   # JS 공백·실행 OS·Node path
                                  path_dirname, path_is_absolute)

# ───────────────────────── core/indexing/ignore.ts:8-91 차단 목록(원문 순서) ─────────────────────────
DEFAULT_SECURITY_IGNORE_FILETYPES = [
    # Environment and configuration files with secrets
    "*.env",
    "*.env.*",
    ".env*",
    "config.json",
    "config.yaml",
    "config.yml",
    "settings.json",
    "appsettings.json",
    "appsettings.*.json",
    # Certificate and key files
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.crt",
    "*.cer",
    "*.jks",
    "*.keystore",
    "*.truststore",
    # Database files that may contain sensitive data
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.mdb",
    "*.accdb",
    # Credential and secret files
    "*.secret",
    "*.secrets",
    "auth.json",
    "*.token",
    # Backup files that might contain sensitive data
    "*.bak",
    "*.backup",
    "*.old",
    "*.orig",
    # Docker secrets
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    # SSH and GPG
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.ppk",
    "*.gpg",
]

DEFAULT_SECURITY_IGNORE_DIRS = [
    # Environment and configuration directories
    ".env/",
    "env/",
    # Cloud provider credential directories
    ".aws/",
    ".gcp/",
    ".azure/",
    ".kube/",
    ".docker/",
    # Secret directories
    "secrets/",
    ".secrets/",
    "private/",
    ".private/",
    "certs/",
    "certificates/",
    "keys/",
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    # Temporary directories that might contain sensitive data
    "tmp/secrets/",
    "temp/secrets/",
    ".tmp/",
]

# ───────────────────────── npm ignore 7.0.5 — 패턴 → 정규식(REPLACERS 그대로) ─────────────────────────
_WS = JS_WS_CLASS                                            # JS \s
_BOM = chr(0xFEFF)                                           # 바이트 순서 표시


def _sanitize_range(rng: str) -> str:
    """sanitizeRange — 거꾸로 된 범위(z-a)는 지움."""
    return re.sub(r"([0-z])-([0-z])", lambda m: m.group(0) if ord(m.group(1)) <= ord(m.group(2)) else "", rng)


def _clean_range_backslash(slashes: str) -> str:
    """cleanRangeBackSlash — 짝수 개만 남김."""
    n = len(slashes)
    return slashes[:n - n % 2]


def _r_trailing_spaces(m: re.Match) -> str:
    """끝 공백 제거(\\ 로 감싼 공백은 하나 남김)."""
    return m.group(1) + (" " if m.group(2).startswith("\\") else "")


def _r_escaped_space(m: re.Match) -> str:
    """(\\ ) → ' '."""
    n = len(m.group(1))
    return m.group(1)[:n - n % 2] + " "


def _r_double_star(m: re.Match) -> str:
    """'/**' 처리 — 마지막이 아니면 0개 이상 폴더, 마지막이면 안의 전부."""
    return r"(?:\/[^\/]+)*" if m.start() + 6 < len(m.string) else r"\/.+"


def _r_intermediate_wildcard(m: re.Match) -> str:
    """중간 '*' → '[^/]*'."""
    return m.group(1) + m.group(2).replace("\\*", r"[^\/]*")


def _r_range(m: re.Match) -> str:
    """[a-z] 범위 표기."""
    lead, rng, end_escape, close = m.group(1), m.group(2), m.group(3), m.group(4)
    if lead == "\\":                                         # '\[bar]' → 글자 그대로
        return "\\[" + rng + _clean_range_backslash(end_escape) + close
    if close == "]":                                         # 정상 범위
        if len(end_escape) % 2 == 0:
            return "[" + _sanitize_range(rng) + end_escape + "]"
        return "[]"                                          # 잘못된 범위
    return "[]"


def _r_ending(m: re.Match) -> str:
    """끝 처리 — 'foo/' 는 폴더만, 'foo' 는 'foo' 와 'foo/'."""
    match = m.group(0)
    return match + "$" if match.endswith("/") else match + r"(?=$|\/$)"


# (정규식, 바꿀 것, 횟수: 0=전부(g), 1=처음만). 순서·내용은 ignore/index.js REPLACERS 와 같음. JS $ → Python \Z
_REPLACERS = [
    ("^" + _BOM, lambda m: "", 1),                           # BOM 제거
    (r"((?:\\\\)*?)(\\?" + _WS + r"+)\Z", _r_trailing_spaces, 1),   # 끝 공백
    (r"(\\+?)" + _WS, _r_escaped_space, 0),                  # '\ ' → ' '
    (r"[\\$.|*+(){^]", lambda m: "\\" + m.group(0), 0),      # 정규식 특수 글자 이스케이프
    (r"(?!\\)\?", lambda m: "[^/]", 0),                      # ? → 한 글자
    (r"^/", lambda m: "^", 1),                               # 앞 슬래시 = 처음부터
    (r"/", lambda m: r"\/", 0),                              # 슬래시 이스케이프
    (r"^\^*\\\*\\\*\\/", lambda m: r"^(?:.*\/)?", 1),        # 앞 '**/'
    None,                                                    # 시작 처리(패턴 원문이 필요해 아래에서 따로)
    (r"\\/\\\*\\\*(?=\\/|\Z)", _r_double_star, 0),           # '/**'
    (r"(^|[^\\]+)(\\\*)+(?=.+)", _r_intermediate_wildcard, 0),   # 중간 '*'
    (r"\\\\\\(?=[$.|*+(){^])", lambda m: "\\", 0),           # 이스케이프 되돌림
    (r"\\\\", lambda m: "\\", 0),                            # '\\' → '\'
    (r"(\\)?\[([^\]/]*?)(\\*)(\Z|\])", _r_range, 0),         # 범위
    (r"(?:[^*])\Z", _r_ending, 1),                           # 끝
]


def _make_regex_prefix(pattern: str) -> str:
    """makeRegexPrefix — REPLACERS 를 차례로 적용."""
    s = pattern
    for item in _REPLACERS:
        if item is None:                                     # 시작 처리: 앞/가운데 슬래시가 없으면 어느 깊이든
            if re.match(r"[^^]", s):                         # /^(?=[^^])/ — 이미 '^' 로 시작하지 않을 때만
                s = ("(?:^|\\/)" if not re.search(r"/(?!\Z)", pattern) else "^") + s
            continue
        rx, repl, count = item
        s = re.sub(rx, repl, s, count=count)                 # JS replace(g 면 전부)
    return s


def _trailing_wildcard(m: re.Match) -> str:
    """TRAILING_WILD_CARD_REPLACERS.regex — 끝 '*'."""
    p1 = m.group(1)
    prefix = f"{p1}[^/]+" if p1 else "[^/]*"                 # 'abc/*' 는 'abc/' 와 안 맞음
    return prefix + r"(?=$|\/$)"


def _js_regex_to_python(src: str) -> str:
    """만든 JS 정규식 글을 Python 용으로 — 묶음 밖 '$' → '\\Z', 빈 묶음 '[]' → 안 맞음."""
    out = []                                                 # 결과 조각
    i = 0
    in_class = False                                         # [...] 안인가
    while i < len(src):
        c = src[i]
        if c == "\\":                                        # 이스케이프는 두 글자 그대로
            out.append(src[i:i + 2])
            i += 2
            continue
        if not in_class:
            if c == "[":                                     # 묶음 시작
                if src[i + 1:i + 2] == "]":                  # JS '[]' = 아무것도 안 맞음
                    out.append("(?!)")
                    i += 2
                    continue
                in_class = True
                out.append(c)
                if src[i + 1:i + 2] == "^":                  # 부정 묶음
                    out.append("^")
                    i += 1
                i += 1
                continue
            out.append(r"\Z" if c == "$" else c)             # JS $(m 없음) = 글 끝
        else:
            if c == "]":                                     # 묶음 끝
                in_class = False
                out.append(c)
            elif c == "[":                                   # Python 중첩 묶음 경고 방지
                out.append("\\[")
            else:
                out.append(c)
        i += 1
    return "".join(out)


class _IgnoreRule:
    """IgnoreRule — 패턴 하나."""

    def __init__(self, pattern: str, ignore_case: bool):
        self.pattern = pattern                               # 원문
        body = pattern
        self.negative = False                                # '!' 부정 패턴
        if body.startswith("!"):
            self.negative = True
            body = body[1:]
        body = re.sub(r"^\\!", "!", body, count=1)           # '\!' → '!'
        body = re.sub(r"^\\#", "#", body, count=1)           # '\#' → '#'
        prefix = _make_regex_prefix(body)                    # 정규식 앞부분
        js_src = re.sub(r"(^|\\/)?\\\*\Z", _trailing_wildcard, prefix, count=1)   # 끝 '*' 처리(ignores 모드)
        self.js_source = js_src                              # 비교 시험용(Node 결과와 같아야 함)
        self.regex = re.compile(_js_regex_to_python(js_src), re.IGNORECASE if ignore_case else 0)   # 'i' 플래그


def _check_pattern(p: object) -> bool:
    """checkPattern — 빈 줄·주석·끝 역슬래시 패턴 제외."""
    return (isinstance(p, str) and bool(p)
            and not re.match("^" + _WS + r"+\Z", p)
            and not re.search(r"(?:[^\\]|^)\\\Z", p)
            and not p.startswith("#"))


class Ignore:
    """npm ignore 의 Ignore(ignores 만) — 규칙 목록 + 부모 디렉터리 우선 판정."""

    def __init__(self, ignore_case: bool = True):
        self._ignore_case = ignore_case                      # 기본 대소문자 무시(ignore 기본값)
        self._rules: list[_IgnoreRule] = []                  # 규칙
        self._cache: dict[str, dict] = {}                    # ignores 결과 저장(cn과 같이 프로세스 동안 유지)

    def add(self, patterns) -> "Ignore":
        """add — 글(줄바꿈 구분)·목록·다른 Ignore."""
        if isinstance(patterns, Ignore):                     # 다른 판정기의 규칙을 이어 붙임
            self._rules.extend(patterns._rules)
            self._cache = {}
            return self
        if isinstance(patterns, str):                        # 여러 줄 글
            patterns = [x for x in re.split(r"\r?\n", patterns) if x]
        added = False
        for p in patterns:
            if isinstance(p, Ignore):
                self._rules.extend(p._rules)
                added = True
            elif _check_pattern(p):
                self._rules.append(_IgnoreRule(p, self._ignore_case))
                added = True
        if added:
            self._cache = {}                                 # 규칙이 바뀌면 저장 결과 버림
        return self

    def _test_one(self, path: str) -> dict:
        """RuleManager.test(checkUnignored=false) — 부모 확인 없이 이 경로만."""
        ignored = False
        unignored = False
        for rule in self._rules:
            neg = rule.negative
            if (unignored == neg and ignored != unignored) or (neg and not ignored and not unignored):   # 건너뛰는 경우(표 그대로)
                continue
            if not rule.regex.search(path):                  # 안 맞음
                continue
            ignored = not neg
            unignored = neg
        return {"ignored": ignored, "unignored": unignored}

    def _t(self, path: str, slices: list[str] | None = None) -> dict:
        """_t — 부모 폴더('a/', 'a/b/')가 먼저 걸리면 그 결과."""
        if path in self._cache:                              # 저장된 결과
            return self._cache[path]
        if slices is None:                                   # 'a/b/c.js' → ['a','b','c.js']
            slices = [x for x in path.split("/") if x]
        slices.pop() if slices else None                     # 마지막 조각 제외
        if not slices:                                       # 부모 없음
            r = self._test_one(path)
            self._cache[path] = r
            return r
        parent = self._t("/".join(slices) + "/", slices)     # 부모 먼저(slices 를 같이 줄여 감 — JS와 같음)
        r = parent if parent["ignored"] else self._test_one(path)
        self._cache[path] = r
        return r

    def ignores(self, path: str) -> bool:
        """ignores — 경로 검사 후 판정. 상대경로가 아니면 RangeError 글로 오류(cn에서 그대로 모델에게 감)."""
        converted = _convert(path) if path else path         # Windows: '\' → '/'
        _check_path(converted, path)                         # 형식 검사
        return self._t(converted)["ignored"]


class IgnorePathError(ContinueError):
    """npm ignore 의 TypeError/RangeError — cn에서는 일반 Error 라 reason 은 unknown, 글만 모델에게 감."""

    def __init__(self, message: str):
        super().__init__("unknown", message)


_INVALID_PATH = re.compile(r"^\.{0,2}/|^\.{1,2}\Z")         # REGEX_TEST_INVALID_PATH: /x, ./x, ../x, ., ..
_WIN_ABS = re.compile(r"^[a-z]:/", re.IGNORECASE)            # Windows 'C:/'


def _convert(p: str) -> str:
    """checkPath.convert — win32 에서만 '\\' → '/'(\\\\?\\ 접두나 금지 글자가 있으면 그대로)."""
    if node_platform() != "win32":                           # posix 는 그대로
        return p
    if re.match(r"^\\\\\?\\", p) or re.search(r'["<>|\x00-\x1f]+', p):   # 변환 안 함
        return p
    return p.replace("\\", "/")


def _check_path(path: object, original: object) -> None:
    """checkPath — 글 아님·빈 글·상대경로 아님이면 오류(글은 ignore 원문)."""
    if not isinstance(path, str):
        raise IgnorePathError(f"path must be a string, but got `{original}`")
    if not path:
        raise IgnorePathError("path must not be empty")
    not_relative = bool(_INVALID_PATH.search(path))          # 기본 규칙
    if node_platform() == "win32" and _WIN_ABS.search(path):   # Windows 절대경로
        not_relative = True
    if not_relative:
        raise IgnorePathError(f'path should be a `path.relative()`d string, but got "{original}"')


# ───────────────────────── core/indexing/ignore.ts:205-264 ─────────────────────────
default_security_ignore_file = Ignore().add(DEFAULT_SECURITY_IGNORE_FILETYPES)   # 파일 패턴
default_security_ignore_dir = Ignore().add(DEFAULT_SECURITY_IGNORE_DIRS)         # 폴더 패턴
default_file_and_folder_security_ignores = Ignore().add(default_security_ignore_file).add(default_security_ignore_dir)   # 합친 판정기


def _file_url_to_path(s: str) -> str:
    """Node url.fileURLToPath — file: URL 이 아니면 오류(호출한 쪽이 무시하고 원래 글을 씀)."""
    parts = urlsplit(s)                                      # URL 나누기
    if parts.scheme.lower() != "file":                       # file: 만
        raise ValueError("ERR_INVALID_URL_SCHEME")
    path = parts.path or "/"                                 # 'file:foo' 도 '/foo' 로(WHATWG)
    if not path.startswith("/"):
        path = "/" + path
    host = parts.hostname or ""                              # 호스트
    if node_platform() == "win32":                           # Windows
        if re.search(r"%2f|%5c", path, re.IGNORECASE):       # 인코딩된 구분자 금지
            raise ValueError("ERR_INVALID_FILE_URL_PATH")
        path = unquote(path).replace("/", "\\")              # 구분자 '\'
        if host and host != "localhost":                     # UNC
            return f"\\\\{host}{path}"
        if not re.match(r"^\\[a-zA-Z]:", path):              # 'C:' 필요
            raise ValueError("ERR_INVALID_FILE_URL_PATH")
        return path[1:]
    if host and host != "localhost":                         # posix 는 호스트 금지
        raise ValueError("ERR_INVALID_FILE_URL_HOST")
    if re.search(r"%2f", path, re.IGNORECASE):               # 인코딩된 '/' 금지
        raise ValueError("ERR_INVALID_FILE_URL_PATH")
    return unquote(path)


def is_security_concern(file_path_or_uri: str) -> bool:
    """isSecurityConcern — 절대경로는 '부모 폴더 이름/파일 이름' 두 조각으로 줄여 판정, 상대경로는 전체로."""
    if not file_path_or_uri:                                 # 빈 값
        return False
    filepath = file_path_or_uri
    try:
        filepath = _file_url_to_path(file_path_or_uri)       # file:// 이면 경로로
    except Exception:                                        # 아니면 그대로
        pass
    if path_is_absolute(filepath):                           # 절대경로
        d = re.split(r"/|\\", path_dirname(filepath))[-1]    # 바로 위 폴더 이름
        base = path_basename(filepath)                       # 파일 이름
        filepath = f"{d + '/' if d else ''}{base}"
    if not filepath:
        return False
    return default_file_and_folder_security_ignores.ignores(filepath)


def throw_if_file_is_security_concern(filepath: str) -> None:
    """throwIfFileIsSecurityConcern — 민감 파일이면 cn 원문 글로 ContinueError."""
    if is_security_concern(filepath):
        raise ContinueError(
            "file_is_security_concern",
            f"Reading or Editing {filepath} is not allowed because it is a security concern. Do not attempt to read or edit this file in any way.",
        )
