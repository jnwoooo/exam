# ncn/config/env.py — 환경변수와 .env 읽기. cn extensions/cli/src/env.ts + dotenv 16.6.1 lib/main.js(parse·config·populate)를 옮긴 것
# cn은 모듈을 불러오는 순간 dotenv.config()로 <cwd>/.env 를 process.env 에 넣는다(이미 있는 키는 덮지 않음 — 값이 빈 글이어도 "있음").
#   그래서 <cwd>/.env 의 CONTINUE_GLOBAL_DIR·HOME·비밀값이 cn 전체(설정 위치·secrets 찾기·Bash 자식 프로세스 환경)에 영향을 준다.
# ncn: effective_environ(cwd, environ) 은 사본에 적용(시험용), apply_dotenv(os.environ, cwd) 는 프로그램 시작 때 한 번(cn과 같게) 부른다.
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 환경변수
import re                                                    # dotenv LINE 정규식
from typing import Mapping, MutableMapping, Optional         # 타입

from ncn.tools.formatters import JS_WS_CHARS, path_resolve   # JS 공백 집합, Node path.resolve

DEFAULT_API_BASE = "https://api.continue.dev/"               # env.ts apiBase 기본값(ncn 은 부르지 않음)

# dotenv 16.6.1 lib/main.js:9 LINE 원문:
#   /(?:^|^)\s*(?:export\s+)?([\w.-]+)(?:\s*=\s*?|:\s+?)(\s*'(?:\\'|[^'])*'|\s*"(?:\\"|[^"])*"|\s*`(?:\\`|[^`])*`|[^#\r\n]+)?\s*(?:#.*)?(?:$|$)/mg
# JS 와 Python 정규식 차이를 맞춤: \s = JS 공백 집합, \w = ASCII, m 플래그의 ^ $ 는 \n \r \u2028 \u2029 기준, . 은 줄 끝 글자 제외
_S = "[" + JS_WS_CHARS + "]"                                 # JS \s
_LT = "\n\r\u2028\u2029"                                     # JS 줄 끝 글자
_BOL = rf"(?:(?<=[{_LT}])|\A)"                               # m 플래그 ^
_EOL = rf"(?=[{_LT}]|\Z)"                                    # m 플래그 $
_LINE = re.compile(
    _BOL + _S + r"*(?:export" + _S + r"+)?([A-Za-z0-9_.-]+)(?:" + _S + r"*=" + _S + r"*?|:" + _S + r"+?)"
    r"(" + _S + r"*'(?:\\'|[^'])*'|" + _S + r'*"(?:\\"|[^"])*"|' + _S + r"*`(?:\\`|[^`])*`|[^#\r\n]+)?"
    + _S + r"*(?:#[^" + _LT + r"]*)?" + _EOL
)
_QUOTES = re.compile(_BOL + r"(['\"`])([\s\S]*)\1" + _EOL)   # value.replace(/^(['"`])([\s\S]*)\1$/mg, '$2')


def parse_dotenv(src: str) -> dict[str, str]:
    """dotenv.parse — KEY=값 줄들. 따옴표 벗기기, 큰따옴표 안의 \\n \\r 은 실제 줄바꿈으로. 같은 키는 뒤가 이김."""
    obj: dict[str, str] = {}                                 # 결과
    lines = re.sub(r"\r\n?", "\n", src)                      # 줄바꿈 통일
    for m in _LINE.finditer(lines):                          # 줄마다(JS exec 반복과 같음 — 빈 일치 없음)
        key = m.group(1)                                     # 키
        value = (m.group(2) or "").strip(JS_WS_CHARS)        # 값(없으면 "") + JS trim
        maybe_quote = value[0] if value else ""              # 첫 글자
        value = _QUOTES.sub(r"\2", value)                    # 둘러싼 따옴표 제거
        if maybe_quote == '"':                               # 큰따옴표면 \n \r 해석
            value = value.replace("\\n", "\n").replace("\\r", "\r")
        obj[key] = value
    return obj


def read_dotenv_file(path: str) -> Optional[dict[str, str]]:
    """파일 하나를 dotenv.parse — 없거나 못 읽으면 None(dotenv.config 는 조용히 넘어감)."""
    try:
        with open(path, "rb") as f:                          # Node readFileSync(utf8)
            data = f.read()
    except OSError:
        return None
    return parse_dotenv(data.decode("utf-8", errors="replace"))   # 잘못된 바이트는 U+FFFD(BOM 은 그대로 — JS \s 에 포함되어 무시됨)


def apply_dotenv(environ: MutableMapping[str, str], cwd: str) -> dict[str, str]:
    """dotenv.config() — <cwd>/.env 를 environ 에 넣는다(이미 있는 키는 덮지 않음, override 없음). 읽은 표를 돌려준다."""
    parsed = read_dotenv_file(path_resolve(cwd, ".env")) or {}   # path.resolve(process.cwd(), '.env')
    for k, v in parsed.items():                              # populate
        if k not in environ:                                 # hasOwnProperty — 빈 값이어도 있으면 그대로
            environ[k] = v
    return parsed


def effective_environ(cwd: str, environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """cn 안에서 보이는 process.env — 사본에 dotenv.config 를 적용(여러 번 해도 결과 같음)."""
    out = dict(os.environ if environ is None else environ)   # 사본
    apply_dotenv(out, cwd)                                   # <cwd>/.env 반영
    return out


def api_base(environ: Mapping[str, str]) -> str:
    """env.apiBase — CONTINUE_API_BASE ?? 기본(빈 글도 그대로 씀). ncn 은 이 주소로 아무것도 보내지 않는다(폐쇄망)."""
    v = environ.get("CONTINUE_API_BASE")
    return DEFAULT_API_BASE if v is None else v
