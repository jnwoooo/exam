# ncn/config/schema.py — config.yaml 검증 스키마 + YAML 1.2 읽기 + 마크다운 규칙 읽기. cn packages/config-yaml/src/schemas/{index,models}.ts, schemas/mcp/index.ts, schemas/data/index.ts,
#   load/unroll.ts(formatZodError), markdown/markdownToRule.ts 를 옮긴 것
# cn은 yaml 2.8 라이브러리(YAML 1.2 core 스키마)로 읽고 zod 3.25 로 검증한다. 오류 글(예: "models.0.provider: Required")이 사용자·로그에 보이므로
#   zod 의 판정 순서·합집합(union) 처리·기본 오류 글을 그대로 흉내 낸 작은 검증기를 만들었다(아래 "zod 흉내").
# zod z.object 는 모르는 키를 오류 없이 버리고, 결과 키 순서는 스키마 정의 순서다 — 이것도 똑같이 한다.
# PyYAML 은 YAML 1.1(yes/no→참거짓, 012→8진수, 날짜→datetime, << 병합)이라 1.2 core 규칙으로 바꾼 Loader 를 쓴다.
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # JSON.stringify 흉내(오류 글)
import math                                                  # NaN·무한대 판정
import re                                                    # 정규식
from typing import Any, Callable, Optional                   # 타입

import yaml                                                  # PyYAML(파서만 사용)

from ncn.tools.formatters import JS_WS_CHARS                # JS 공백 글자 집합

# ───────────────────────── 값 없음(JS undefined) 표시 ─────────────────────────


class _Missing:                                             # JS undefined 를 나타내는 자리표시 클래스
    """JS undefined 자리표시(YAML null = Python None 과 구분해야 zod 판정이 같아진다)."""

    _inst = None                                             # 하나만

    def __new__(cls):                                       # 인스턴스를 하나만 만들도록 __new__ 재정의
        if cls._inst is None:                               # 아직 없으면
            cls._inst = super().__new__(cls)                # 처음 한 번만 생성
        return cls._inst                                    # 항상 같은 인스턴스 반환

    def __repr__(self) -> str:                              # 출력 글
        return "undefined"                                  # JS 처럼 "undefined"

    def __bool__(self) -> bool:                              # JS 에서 undefined 는 거짓
        return False                                        # 거짓으로 취급


MISSING = _Missing()                                         # undefined — undefined 자리표시 인스턴스


def js_truthy(v: Any) -> bool:                              # JS 참/거짓 판정 함수
    """JS 참/거짓 판정 — 빈 글·0·NaN·null·undefined 는 거짓, 빈 배열·빈 객체는 참."""
    if v is None or v is MISSING:                            # null/undefined — null/undefined 는 거짓
        return False                                        # 거짓
    if isinstance(v, bool):                                  # 불리언
        return v                                            # 불리언은 그 값
    if isinstance(v, (int, float)):                          # 숫자(NaN 거짓)
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))  # 0 이나 NaN 이면 거짓
    if isinstance(v, str):                                   # 글
        return v != ""                                      # 비어 있지 않으면 참
    return True                                              # 배열·객체 등은 항상 참


# ───────────────────────── YAML 1.2 core 읽기(yaml 라이브러리 YAML.parse 흉내) ─────────────────────────


class YamlError(Exception):                                 # YAML 읽기 오류 예외
    """YAML 읽기 오류(글은 yaml 라이브러리와 다를 수 있음 — PyYAML 글)."""


class _Yaml12Loader(yaml.SafeLoader):                       # YAML 1.2 core 규칙 Loader
    """YAML 1.2 core 스키마 Loader(yaml 라이브러리 기본값과 같은 암묵 형식 판정)."""


_Yaml12Loader.yaml_implicit_resolvers = {}                   # 1.1 규칙 전부 제거(병합 키·날짜·yes/no 포함)
# yaml/dist/schema/core/*.js · common/null.js 의 test 정규식 그대로
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:~|[Nn]ull|NULL)?$"), ["~", "n", "N", ""])  # null 판정 정규식 등록
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$"), list("tTfF"))  # bool 판정 정규식 등록
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789"))  # int 판정 정규식 등록(0o 8진수·0x 16진수 포함)
_Yaml12Loader.add_implicit_resolver(                        # float 판정 정규식 등록
    "tag:yaml.org,2002:float",                              # (태그)
    re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+|[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$"),  # (정규식: 지수·소수·inf·nan)
    list("-+0123456789."),                                  # (시작 글자)
)                                                           # (등록 끝)


def _js_number(v: float) -> Any:                            # 정수값 실수를 int 로 바꾸는 함수
    """JS 숫자에는 정수/실수 구분이 없다 — 정수값 실수(1.0)는 int 로 바꿔 JSON 출력이 JS(1)와 같게."""
    if isinstance(v, float) and math.isfinite(v) and v.is_integer() and abs(v) < 2 ** 53:  # 유한하고 정수값이며 안전 범위면
        return int(v)                                       # int 로 변환
    return v                                                # 그 밖은 그대로


def _construct_int(loader: yaml.SafeLoader, node: yaml.Node) -> Any:  # int 값 만드는 함수
    """1.2 core int: 0o 8진수, 0x 16진수, 나머지 10진수(앞자리 0 도 10진수). JS 처럼 숫자 하나로."""
    value = loader.construct_scalar(node)                    # 원문
    if value.startswith("0o"):                               # 8진수
        return int(value[2:], 8)                            # 8진수 변환
    if value.startswith("0x"):                               # 16진수
        return int(value[2:], 16)                           # 16진수 변환
    return int(value, 10)                                    # 10진수(012 → 12)


def _construct_float(loader: yaml.SafeLoader, node: yaml.Node) -> Any:  # float 값 만드는 함수
    """1.2 core float(.inf/.nan 포함). 정수값이면 int 로(JS 숫자와 같은 출력)."""
    value = loader.construct_scalar(node)                    # 원문
    low = value.lower()                                     # 소문자 비교용
    if low in (".inf", "+.inf"):                             # 무한대
        return math.inf                                     # 양의 무한대
    if low == "-.inf":                                      # 음의 무한대면
        return -math.inf                                    # 음의 무한대
    if low == ".nan":                                        # NaN
        return math.nan                                     # 숫자 아님
    return _js_number(float(value))                          # 일반 실수


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:  # 매핑 만드는 함수(중복 키는 오류)
    """매핑 — 키 중복은 오류(yaml 라이브러리 기본 uniqueKeys), 키는 JS 객체 키처럼 글로."""
    if not isinstance(node, yaml.MappingNode):              # 매핑 노드가 아니면
        raise yaml.constructor.ConstructorError(None, None, f"expected a mapping node, but found {node.id}", node.start_mark)  # PyYAML 생성 오류 올림
    out: dict = {}                                           # 결과
    for key_node, value_node in node.value:                  # 순서대로
        key = loader.construct_object(key_node, deep=True)   # 키 값
        js_key = _js_key(key)                                # JS 객체 키 글
        if js_key in out:                                    # 중복 키
            raise YamlError(f"Map keys must be unique at line {key_node.start_mark.line + 1}, column {key_node.start_mark.column + 1}")  # 중복 키 오류(줄·열 번호 포함)
        out[js_key] = loader.construct_object(value_node, deep=True)   # 값
    return out                                              # 만든 사전 반환


def _js_key(k: Any) -> str:                                 # JS 객체 키 글로 바꾸는 함수
    """JS 객체 키 = String(k). 숫자 1 → "1", 참 → "true", null → "" (yaml 라이브러리는 null 키를 "" 로 만든다)."""
    if isinstance(k, str):                                  # 글이면
        return k                                            # 그대로
    if k is None:                                           # null 이면
        return ""                                           # 빈 글
    if isinstance(k, bool):                                 # 불리언이면
        return "true" if k else "false"                     # 불리언 글
    if isinstance(k, (int, float)):                         # 숫자면
        return js_number_to_string(k)                       # JS 숫자 표기로
    return json.dumps(k, ensure_ascii=False)                 # 그 밖(드묾)


def js_number_to_string(n: Any) -> str:                     # JS String(number) 흉내 함수
    """JS String(number) — 1.0 → "1", 무한대 → "Infinity"."""
    if isinstance(n, bool):                                 # 불리언이면
        return "true" if n else "false"                     # 불리언 글
    if isinstance(n, float):                                # 실수면
        if math.isnan(n):                                   # NaN 이면
            return "NaN"                                    # NaN 표기
        if math.isinf(n):                                   # 무한대면
            return "Infinity" if n > 0 else "-Infinity"     # 무한대 표기
        if n.is_integer() and abs(n) < 1e21:                # 정수값이면
            return str(int(n))                              # 정수 글로
        return repr(n)                                      # 그 밖은 repr
    return str(n)                                           # 그 밖은 str


_Yaml12Loader.add_constructor("tag:yaml.org,2002:int", _construct_int)  # int 생성자 등록
_Yaml12Loader.add_constructor("tag:yaml.org,2002:float", _construct_float)  # float 생성자 등록
_Yaml12Loader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)  # map 생성자 등록(중복 키 검사)


def yaml_parse(text: str) -> Any:                           # YAML 글을 읽는 함수
    """YAML.parse(text) — 빈 문서는 None(JS null). 오류는 YamlError(글은 PyYAML 것)."""
    try:                                                    # 읽기 시도
        return yaml.load(text, Loader=_Yaml12Loader)         # 1.2 core 규칙
    except YamlError:                                       # 우리 예외는 그대로 올림
        raise                                               # 그대로 올림
    except yaml.YAMLError as e:                              # 문법 오류
        raise YamlError(str(e)) from e                      # PyYAML 오류를 YamlError 로 바꿈


# yaml 라이브러리 stringifyString.js plainString 의 "따옴표 없이 쓸 수 없는 글" 판정(같은 정규식)
_NOT_PLAIN = re.compile(r"^[\n\t ,\[\]{}#&*!|>'\"%@`]|^[?-]$|^[?-][ \t]|[\n:][ \t]|[ \t]\n|[\n\t ]#|[\n\t :]$")  # 따옴표 없이 쓸 수 없는 글 정규식
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")   # 큰따옴표가 강제되는 제어 글자
_NON_STR = re.compile(r"^(?:~|[Nn]ull|NULL)?$|^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$|^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"  # 글이 아닌 값으로 읽히는 글 정규식(null·bool·int)
                      r"|^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+|[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$")  # (float·inf·nan 이어짐)


def yaml_stringify_is_plain(value: str) -> bool:            # plain 글로 쓰이는지 판정하는 함수
    """YAML.stringify 가 이 글(값 자리)을 따옴표 없는 plain 으로 쓰는가. 한 줄·indicator 없음·숫자/참거짓으로 안 읽힘."""
    if value == "" or "\n" in value:                         # 빈 글은 "", 여러 줄은 블록
        return False                                        # 빈 글·여러 줄은 plain 아님
    if _CTRL.search(value):                                  # 제어 글자 → 큰따옴표
        return False                                        # 제어 글자가 있으면 아님
    if _NOT_PLAIN.search(value):                             # indicator 등
        return False                                        # indicator 가 있으면 아님
    if _NON_STR.match(value):                                # 다른 형식으로 읽힐 글 → 따옴표
        return False                                        # 다른 형식으로 읽히면 아님
    return True                                             # plain 임


def yaml_reparse_plain(text: str) -> Any:                   # plain 자리에 끼워 다시 읽는 함수
    """plain 자리에 글을 그대로 끼워 넣은 뒤 다시 읽은 값(cn은 YAML 글에 비밀값을 끼우고 다시 parse 하므로 "123" → 숫자가 됨)."""
    doc = yaml_parse("k: " + text)                           # 값 자리에 넣어 읽기
    if isinstance(doc, dict) and list(doc.keys()) == ["k"]:  # 정상
        return doc["k"]                                     # 값 반환
    raise YamlError("Unexpected scalar")                     # 키가 더 생기는 등 — cn 에선 다른 구조가 되거나 오류


# ───────────────────────── zod 흉내(3.25 v3 동작) ─────────────────────────
VALID, DIRTY, ABORTED = "valid", "dirty", "aborted"          # zod ParseStatus — zod 파싱 상태 이름


def parsed_type(v: Any) -> str:                             # zod 의 값 형식 이름 함수
    """zod getParsedType — 오류 글의 received 값."""
    if v is MISSING:                                        # undefined 면
        return "undefined"                                  # 값 없음
    if v is None:                                           # null 이면
        return "null"                                       # null 값
    if isinstance(v, bool):                                 # 불리언이면
        return "boolean"                                    # 불리언
    if isinstance(v, (int, float)):                         # 숫자면
        return "nan" if isinstance(v, float) and math.isnan(v) else "number"  # NaN 구분
    if isinstance(v, str):                                  # 글이면
        return "string"                                     # 글
    if isinstance(v, list):                                 # 배열이면
        return "array"                                      # 배열
    if isinstance(v, dict):                                 # 사전이면
        return "object"                                     # 객체
    return "object"                                          # 그 밖(드묾)


def _js_stringify(v: Any) -> str:                           # 오류 글용 JSON 직렬화 함수
    """JSON.stringify(값) — 오류 글용."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # 공백 없는 JSON 글


def _join_values(values: list) -> str:                      # 값들을 " | " 로 잇는 함수
    """zod util.joinValues — 글은 '작은따옴표', " | " 로 잇기."""
    return " | ".join(f"'{v}'" if isinstance(v, str) else js_number_to_string(v) for v in values)  # 글은 작은따옴표로 감싸 이음


def issue_message(issue: dict) -> str:                      # zod 기본 오류 글 만드는 함수
    """zod 기본 오류 글(locales/en.js) 중 이 스키마들이 낼 수 있는 것."""
    code = issue["code"]                                     # 종류
    if issue.get("message") is not None:                     # 스키마에 직접 준 글
        return issue["message"]                             # 직접 준 글을 사용
    if code == "invalid_type":                              # 타입이 틀린 경우
        return "Required" if issue["received"] == "undefined" else f"Expected {issue['expected']}, received {issue['received']}"  # 없으면 "Required", 아니면 기대/받은 형식
    if code == "invalid_literal":                           # 리터럴 값이 틀린 경우
        return f"Invalid literal value, expected {_js_stringify(issue['expected'])}"  # 기대한 리터럴 글
    if code == "invalid_union":                             # 합집합에 맞는 것이 없는 경우
        return "Invalid input"                              # 기본 문구
    if code == "invalid_enum_value":                        # enum 값이 틀린 경우
        return f"Invalid enum value. Expected {_join_values(issue['options'])}, received '{issue['received']}'"  # 가능한 값 목록 글
    if code == "invalid_intersection_types":                # 교집합을 합칠 수 없는 경우
        return "Intersection results could not be merged"   # 해당 글
    if code == "too_small":                                 # 너무 작은 경우
        if issue["type"] == "array":                        # 배열이면
            how = "exactly" if issue["exact"] else ("at least" if issue["inclusive"] else "more than")  # 정확히/이상/초과 표현 선택
            return f"Array must contain {how} {issue['minimum']} element(s)"  # 배열 최소 개수 글
        how = "exactly equal to " if issue["exact"] else ("greater than or equal to " if issue["inclusive"] else "greater than ")  # 숫자 비교 표현 선택
        return f"Number must be {how}{js_number_to_string(issue['minimum'])}"  # 숫자 최소값 글
    if code == "too_big":                                   # 너무 큰 경우
        if issue["type"] == "array":                        # 배열이면
            how = "exactly" if issue["exact"] else ("at most" if issue["inclusive"] else "less than")  # 정확히/이하/미만 표현 선택
            return f"Array must contain {how} {issue['maximum']} element(s)"  # 배열 최대 개수 글
        how = "exactly" if issue["exact"] else ("less than or equal to" if issue["inclusive"] else "less than")  # 숫자 비교 표현 선택
        return f"Number must be {how} {js_number_to_string(issue['maximum'])}"  # 숫자 최대값 글
    if code == "invalid_string":                            # 문자열 형식 오류
        return "Invalid"                                    # 기본 문구
    return "Invalid input"                                   # 그 밖


class ZodError(Exception):                                  # zod 오류 예외
    """zod ZodError — issues 목록(각 {code, path, message, …})."""

    def __init__(self, issues: list[dict]):                 # 문제 목록을 받는 생성자
        self.issues = issues                                 # 문제 목록(zod error.errors)
        super().__init__(json.dumps(issues, ensure_ascii=False))  # 예외 문구는 JSON 글


def format_zod_error(error: Any) -> str:                    # zod 오류를 "경로: 글" 로 만드는 함수
    """unroll.ts formatZodError — "경로: 글" 을 ", " 로 잇는다. ZodError 가 아니면 message."""
    if isinstance(error, ZodError):                          # error.errors 가 배열
        parts = []                                          # 글 조각 모음
        for e in error.issues:                               # 각 문제
            path = (".".join(str(p) for p in e["path"]) + ": ") if e["path"] else ""   # 경로 접두
            parts.append(f"{path}{e['message']}")           # "경로: 글" 추가
        return ", ".join(parts)                             # ", " 로 이어 반환
    msg = str(error)                                         # 일반 오류
    return msg or "Validation failed"                       # 글이 없으면 기본 문구


class Z:                                                    # zod 형식 기본 클래스
    """zod 형식 하나. parse(data, path, issues) → (상태, 값)."""

    def parse(self, data: Any, path: list, issues: list) -> tuple[str, Any]:   # 하위 클래스가 구현
        raise NotImplementedError                           # 하위 클래스가 구현해야 함

    # 편의(zod 메서드 이름 그대로)
    def optional(self) -> "Z":                              # optional() 편의 메서드
        return ZOptional(self)                              # 선택 형식으로 감쌈

    def nullable(self) -> "Z":                              # nullable() 편의 메서드
        return ZNullable(self)                              # null 허용 형식으로 감쌈

    def array(self) -> "Z":                                 # array() 편의 메서드
        return ZArray(self)                                 # 배열 형식으로 감쌈

    def safe_parse(self, data: Any) -> tuple[bool, Any]:    # safeParse 흉내 메서드
        """safeParse — (성공, 값 또는 ZodError)."""
        issues: list = []                                    # 모은 문제
        status, value = self.parse(data, [], issues)         # 검증
        if status == VALID and not issues:                   # 성공
            return True, value                              # 성공과 값 반환
        for i in issues:                                     # 글 채우기
            i["message"] = issue_message(i)                 # 문제마다 오류 글 채움
        return False, ZodError(issues)                      # 실패와 ZodError 반환

    def parse_or_raise(self, data: Any) -> Any:             # parse 흉내 메서드(실패는 예외)
        """parse — 실패면 ZodError."""
        ok, v = self.safe_parse(data)                       # safeParse 실행
        if not ok:                                          # 실패면
            raise v                                         # ZodError 를 올림
        return v                                            # 값 반환


def _add(issues: list, path: list, **kw: Any) -> None:      # 문제 목록에 항목을 더하는 함수
    """addIssueToContext."""
    issues.append({"path": list(path), **kw})               # 경로와 내용을 가진 문제 추가


class ZString(Z):                                           # 문자열 형식(+ 정규식 검사)
    """z.string() (+ .regex(정규식, {message}))."""

    def __init__(self, regex: Optional[re.Pattern] = None, regex_message: Optional[str] = None):  # 정규식과 오류 글을 받는 생성자
        self.regex = regex                                   # 선택: 정규식 검사
        self.regex_message = regex_message                   # 그때 오류 글

    def parse(self, data, path, issues):                    # 문자열 검증
        if not isinstance(data, str):                        # 형식 오류
            _add(issues, path, code="invalid_type", expected="string", received=parsed_type(data))  # 문자열이 아니면 형식 오류
            return ABORTED, None                            # 중단
        if self.regex is not None and not self.regex.search(data):   # 정규식 실패 → dirty
            _add(issues, path, code="invalid_string", validation="regex", message=self.regex_message)  # 정규식 실패 문제 추가
            return DIRTY, data                              # dirty 로 값은 유지
        return VALID, data                                  # 통과


class ZNumber(Z):                                           # 숫자 형식(+ 초과 조건)
    """z.number() (+ .gt(n))."""

    def __init__(self, gt: Optional[float] = None):         # 초과 조건을 받는 생성자
        self.gt = gt                                         # 선택: 초과 조건

    def parse(self, data, path, issues):                    # 숫자 검증
        t = parsed_type(data)                                # 형식
        if t != "number":                                   # 숫자가 아니면
            _add(issues, path, code="invalid_type", expected="number", received=t)  # 형식 오류
            return ABORTED, None                            # 중단
        if self.gt is not None and not data > self.gt:       # 조건 실패 → dirty
            _add(issues, path, code="too_small", minimum=self.gt, type="number", inclusive=False, exact=False)  # 너무 작음 문제 추가
            return DIRTY, data                              # dirty 로 값은 유지
        return VALID, data                                  # 통과


class ZBoolean(Z):                                          # 불리언 형식
    """z.boolean()."""

    def parse(self, data, path, issues):                    # 불리언 검증
        if not isinstance(data, bool):                      # 불리언이 아니면
            _add(issues, path, code="invalid_type", expected="boolean", received=parsed_type(data))  # 형식 오류
            return ABORTED, None                            # 중단
        return VALID, data                                  # 통과


class ZAny(Z):                                              # any 형식
    """z.any() — 무엇이든 통과(undefined 포함)."""

    def parse(self, data, path, issues):                    # 무엇이든 통과
        return VALID, data                                  # 값은 보지 않고 통과


class ZLiteral(Z):                                          # 리터럴 형식
    """z.literal(값)."""

    def __init__(self, value: Any):                         # 기대 값을 받는 생성자
        self.value = value                                  # 기대 값 저장

    def parse(self, data, path, issues):                    # 리터럴 검증
        if data is MISSING or data != self.value or type(data) is not type(self.value):   # === 비교
            _add(issues, path, code="invalid_literal", expected=self.value, received=None if data is MISSING else data)  # 리터럴 불일치 문제 추가
            return ABORTED, None                            # 중단
        return VALID, data                                  # 통과


class ZEnum(Z):                                             # enum 형식
    """z.enum([...])."""

    def __init__(self, options: list[str]):                 # 후보 목록을 받는 생성자
        self.options = options                              # 후보 저장

    def parse(self, data, path, issues):                    # enum 검증
        if not isinstance(data, str):                        # 글이 아니면 invalid_type(expected 에 후보 나열)
            _add(issues, path, code="invalid_type", expected=_join_values(self.options), received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        if data not in self.options:                         # 후보 밖
            _add(issues, path, code="invalid_enum_value", received=data, options=self.options)  # enum 값 오류 추가
            return ABORTED, None                            # 중단
        return VALID, data                                  # 통과


class ZOptional(Z):                                         # optional 형식
    """.optional() — undefined 면 통과."""

    def __init__(self, inner: Z):                           # 안쪽 형식을 받는 생성자
        self.inner = inner                                  # 안쪽 형식 저장

    def parse(self, data, path, issues):                    # optional 검증
        if data is MISSING:                                 # undefined 면
            return VALID, MISSING                           # 통과
        return self.inner.parse(data, path, issues)         # 아니면 안쪽 형식으로 검증


class ZNullable(Z):                                         # nullable 형식
    """.nullable() — null 이면 통과."""

    def __init__(self, inner: Z):                           # 안쪽 형식을 받는 생성자
        self.inner = inner                                  # 안쪽 형식 저장

    def parse(self, data, path, issues):                    # nullable 검증
        if data is None:                                    # null 이면
            return VALID, None                              # 통과
        return self.inner.parse(data, path, issues)         # 아니면 안쪽 형식으로 검증


def _merge_status(statuses: list[str]) -> str:              # 상태 합치기 함수
    """ParseStatus.mergeArray/mergeObject — 하나라도 aborted 면 aborted, dirty 면 dirty."""
    if ABORTED in statuses:                                 # 하나라도 aborted 면
        return ABORTED                                      # 중단 상태
    if DIRTY in statuses:                                   # dirty 가 있으면
        return DIRTY                                        # 부분 오류 상태
    return VALID                                            # 모두 valid


class ZArray(Z):                                            # 배열 형식
    """z.array(형식) (+ .length(n))."""

    def __init__(self, item: Z, exact_length: Optional[int] = None):  # 항목 형식과 정확한 길이를 받는 생성자
        self.item = item                                    # 항목 형식 저장
        self.exact_length = exact_length                    # 정확한 길이 저장

    def length(self, n: int) -> "ZArray":                   # 길이 지정 메서드
        return ZArray(self.item, n)                         # 길이를 지정한 새 배열 형식

    def parse(self, data, path, issues):                    # 배열 검증
        if not isinstance(data, list):                       # 배열 아님
            _add(issues, path, code="invalid_type", expected="array", received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        status = VALID                                       # 길이 검사가 먼저(zod 순서)
        if self.exact_length is not None and len(data) != self.exact_length:  # 길이가 다르면
            big = len(data) > self.exact_length             # 넘치는지
            if big:                                         # 넘치면
                _add(issues, path, code="too_big", maximum=self.exact_length, type="array", inclusive=True, exact=True)  # too_big 문제 추가
            else:                                           # 모자라면
                _add(issues, path, code="too_small", minimum=self.exact_length, type="array", inclusive=True, exact=True)  # too_small 문제 추가
            status = DIRTY                                  # dirty 로 표시
        results = [self.item.parse(v, path + [i], issues) for i, v in enumerate(data)]   # 항목마다
        st = _merge_status([status] + [r[0] for r in results])  # 항목 상태를 합침
        if st == ABORTED:                                   # aborted 면
            return ABORTED, None                            # 중단
        return st, [r[1] for r in results]                  # 상태와 항목 값 반환


class ZTuple(Z):                                            # 튜플 형식
    """z.tuple([...]) — exampleArgs 에만 쓰임."""

    def __init__(self, items: list[Z]):                     # 항목 형식 목록을 받는 생성자
        self.items = items                                  # 항목 형식 저장

    def parse(self, data, path, issues):                    # 튜플 검증
        if not isinstance(data, list):                      # 배열이 아니면
            _add(issues, path, code="invalid_type", expected="array", received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        if len(data) < len(self.items):                      # 모자람 → 바로 실패
            _add(issues, path, code="too_small", minimum=len(self.items), type="array", inclusive=True, exact=False)  # too_small 문제 추가
            return ABORTED, None                            # 중단
        status = VALID                                      # 상태 초기화
        if len(data) > len(self.items):                      # 넘침 → dirty, 넘친 항목은 버림
            _add(issues, path, code="too_big", maximum=len(self.items), type="array", inclusive=True, exact=False)  # too_big 문제 추가
            status = DIRTY                                  # dirty 로 표시
        results = [s.parse(v, path + [i], issues) for i, (s, v) in enumerate(zip(self.items, data))]  # 항목별 검증
        st = _merge_status([status] + [r[0] for r in results])  # 상태를 합침
        if st == ABORTED:                                   # aborted 면
            return ABORTED, None                            # 중단
        return st, [r[1] for r in results]                  # 상태와 값 반환


class ZObject(Z):                                           # 객체 형식
    """z.object({...}) — 모르는 키는 버림, 결과 키 순서는 스키마 순서."""

    def __init__(self, shape: dict[str, Z]):                # 키→형식 표를 받는 생성자
        self.shape = dict(shape)                             # 키 → 형식(순서 유지)

    def extend(self, more: dict[str, Z]) -> "ZObject":      # 키를 더한 새 객체 형식
        return ZObject({**self.shape, **more})              # 합친 표로 생성

    def partial(self) -> "ZObject":                         # 모든 키를 optional 로 바꾼 새 객체 형식
        return ZObject({k: (v if isinstance(v, ZOptional) else ZOptional(v)) for k, v in self.shape.items()})  # 이미 optional 이면 그대로

    def and_(self, other: Z) -> "ZIntersection":            # 교집합 형식 만들기
        return ZIntersection(self, other)                   # 두 형식의 교집합

    def parse(self, data, path, issues):                    # 객체 검증
        if not isinstance(data, dict):                       # 객체 아님(배열·null 포함)
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        statuses = []                                        # 키마다 상태
        out: dict = {}                                       # 결과
        for key, schema in self.shape.items():               # 스키마 순서로
            value = data.get(key, MISSING)                   # 없으면 undefined
            st, v = schema.parse(value, path + [key], issues)  # 키의 값을 검증
            statuses.append(st)                             # 상태 기록
            if v is not MISSING and st != ABORTED:           # undefined 는 키를 만들지 않음
                out[key] = v                                # 결과에 저장
        st = _merge_status(statuses)                        # 상태를 합침
        if st == ABORTED:                                   # aborted 면
            return ABORTED, None                            # 중단
        return st, out                                      # 상태와 결과 반환


class ZRecord(Z):                                           # record 형식
    """z.record(값형식) / z.record(키형식, 값형식) — 키는 항상 글."""

    def __init__(self, value: Z):                           # 값 형식을 받는 생성자
        self.value = value                                  # 값 형식 저장

    def parse(self, data, path, issues):                    # record 검증
        if not isinstance(data, dict):                      # 사전이 아니면
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        statuses = []                                       # 키별 상태
        out: dict = {}                                      # 결과 사전
        for key, v in data.items():                          # 입력 순서
            st, pv = self.value.parse(v, path + [key], issues)  # 값을 검증
            statuses.append(st)                             # 상태 기록
            if pv is not MISSING and st != ABORTED:         # undefined 가 아니면
                out[key] = pv                               # 결과에 저장
        st = _merge_status(statuses)                        # 상태를 합침
        if st == ABORTED:                                   # aborted 면
            return ABORTED, None                            # 중단
        return st, out                                      # 상태와 결과 반환


class ZUnion(Z):                                            # 합집합 형식
    """z.union([...]) — 처음 성공한 것, 없으면 처음 dirty, 둘 다 없으면 "Invalid input" 하나."""

    def __init__(self, options: list[Z]):                   # 선택지 목록을 받는 생성자
        self.options = options                              # 선택지 저장

    def parse(self, data, path, issues):                    # 합집합 검증
        dirty = None                                         # 처음 dirty 결과
        for option in self.options:                          # 선택지마다(각자 문제 목록)
            child: list = []                                # 선택지별 문제 목록
            st, v = option.parse(data, path, child)         # 선택지로 검증
            if st == VALID and not child:                    # 성공
                return VALID, v                             # 성공한 것을 반환
            if st == DIRTY and dirty is None:               # 처음 dirty 만 기억
                dirty = (st, v, child)                      # dirty 결과 저장
        if dirty is not None:                                # dirty 선택지의 문제를 올림
            issues.extend(dirty[2])                         # dirty 선택지의 문제를 올림
            return dirty[0], dirty[1]                       # dirty 결과 반환
        _add(issues, path, code="invalid_union")             # 전부 실패
        return ABORTED, None                                # 전부 실패


def _merge_values(a: Any, b: Any) -> tuple[bool, Any]:      # 교집합 값 합치기 함수
    """zod mergeValues — 교집합 결과 합치기."""
    if a is b or (type(a) is type(b) and not isinstance(a, (dict, list)) and a == b):  # 같은 값이면
        return True, a                                      # 합치기 성공
    if isinstance(a, dict) and isinstance(b, dict):          # 객체: 겹치는 키는 재귀
        new = {**a, **b}                                    # 두 사전 합침
        for k in [k for k in b if k in a]:                  # 겹치는 키마다
            ok, v = _merge_values(a[k], b[k])               # 재귀로 합침
            if not ok:                                      # 합칠 수 없으면
                return False, None                          # 실패
            new[k] = v                                      # 합친 값 저장
        return True, new                                    # 성공
    if isinstance(a, list) and isinstance(b, list):          # 배열: 길이 같으면 항목별
        if len(a) != len(b):                                # 길이가 다르면
            return False, None                              # 실패
        out = []                                            # 결과 목록
        for x, y in zip(a, b):                              # 항목별로
            ok, v = _merge_values(x, y)                     # 재귀로 합침
            if not ok:                                      # 합칠 수 없으면
                return False, None                          # 실패
            out.append(v)                                   # 합친 값 추가
        return True, out                                    # 성공
    return False, None                                      # 그 밖은 합칠 수 없음


class ZIntersection(Z):                                     # 교집합 형식
    """a.and(b) — 둘 다 검사(왼쪽 먼저), 결과를 합침."""

    def __init__(self, left: Z, right: Z):                  # 왼쪽·오른쪽 형식을 받는 생성자
        self.left = left                                    # 왼쪽 형식 저장
        self.right = right                                  # 오른쪽 형식 저장

    def parse(self, data, path, issues):                    # 교집합 검증
        ls, lv = self.left.parse(data, path, issues)         # 왼쪽
        rs, rv = self.right.parse(data, path, issues)        # 오른쪽
        if ls == ABORTED or rs == ABORTED:                  # 하나라도 aborted 면
            return ABORTED, None                            # 중단
        ok, merged = _merge_values(lv, rv)                  # 두 결과를 합침
        if not ok:                                          # 합칠 수 없으면
            _add(issues, path, code="invalid_intersection_types")  # 합칠 수 없음 문제 추가
            return ABORTED, None                            # 중단
        return (DIRTY if DIRTY in (ls, rs) else VALID), merged  # 한쪽이라도 dirty 면 dirty, 합친 값 반환


# ───────────────────────── 스키마(config-yaml/src/schemas) ─────────────────────────
S, N, B = ZString, ZNumber, ZBoolean                         # 짧은 이름


def _opt(z: Z) -> Z:                                        # optional 감싸기 도우미
    return ZOptional(z)                                     # 선택 형식으로 감쌈


# models.ts
client_certificate_options_schema = ZObject({"cert": S(), "key": S(), "passphrase": _opt(S())})  # 클라이언트 인증서 옵션 스키마
request_options_schema = ZObject({                          # 요청 옵션 스키마
    "timeout": _opt(N()),                                   # 제한 시간
    "verifySsl": _opt(B()),                                 # SSL 검증 여부
    "caBundlePath": _opt(ZUnion([S(), ZArray(S())])),       # CA 번들 경로(글 또는 글 목록)
    "proxy": _opt(S()),                                     # 프록시
    "headers": _opt(ZRecord(S())),                          # 추가 헤더
    "extraBodyProperties": _opt(ZRecord(ZAny())),           # 요청 본문 추가 속성
    "noProxy": _opt(ZArray(S())),                           # 프록시 제외 목록
    "clientCertificate": _opt(client_certificate_options_schema),  # 클라이언트 인증서
})                                                          # (요청 옵션 스키마 끝)
MODEL_ROLES = ["chat", "autocomplete", "embed", "rerank", "edit", "apply", "summarize", "subagent"]   # modelRolesSchema — 모델 역할 목록
model_roles_schema = ZEnum(MODEL_ROLES)                     # 모델 역할 enum
model_capability_schema = ZUnion([ZLiteral("tool_use"), ZLiteral("image_input"), ZLiteral("next_edit"), S()])  # 모델 기능 스키마(알려진 값 또는 아무 글)
completion_options_schema = ZObject({                       # 완료 옵션 스키마
    "contextLength": _opt(N()), "maxTokens": _opt(N()), "temperature": _opt(N()), "topP": _opt(N()), "topK": _opt(N()),  # 컨텍스트 길이·최대 토큰·온도·top 값
    "minP": _opt(N()), "presencePenalty": _opt(N()), "frequencyPenalty": _opt(N()), "stop": _opt(ZArray(S())), "n": _opt(N()),  # minP·페널티·stop·n
    "reasoning": _opt(B()), "reasoningBudgetTokens": _opt(N()), "promptCaching": _opt(B()), "stream": _opt(B()), "keepAlive": _opt(N()),  # 추론·프롬프트 캐시·스트림·유지 시간
})                                                          # (완료 옵션 끝)
embedding_tasks_schema = ZUnion([ZLiteral("chunk"), ZLiteral("query")])  # 임베딩 작업 종류
cache_behavior_schema = ZObject({"cacheSystemMessage": _opt(B()), "cacheConversation": _opt(B())})  # 캐시 동작 스키마


class _ZEmbedPrefixes(ZRecord):                             # 키도 검사하는 임베딩 접두어 record
    """z.record(embeddingTasksSchema, z.string()) — 키도 검사."""

    def parse(self, data, path, issues):                    # 임베딩 접두어 검증
        if not isinstance(data, dict):                      # 사전이 아니면
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))  # 형식 오류 추가
            return ABORTED, None                            # 중단
        statuses, out = [], {}                              # 상태와 결과 준비
        for key, v in data.items():                          # 키 → 값 순서로 검사
            ks, _ = embedding_tasks_schema.parse(key, path + [key], issues)  # 키 검사
            vs, pv = S().parse(v, path + [key], issues)     # 값 검사
            statuses += [ks, vs]                            # 상태 기록
            if vs != ABORTED and ks != ABORTED:             # 둘 다 중단이 아니면
                out[key] = pv                               # 결과에 저장
        st = _merge_status(statuses)                        # 상태를 합침
        return (ABORTED, None) if st == ABORTED else (st, out)  # aborted 면 중단, 아니면 상태와 결과


embed_options_schema = ZObject({"maxChunkSize": _opt(N()), "maxBatchSize": _opt(N()), "embeddingPrefixes": _opt(_ZEmbedPrefixes(S()))})  # 임베딩 옵션 스키마
system_message_description_override_schema = ZObject({"prefix": _opt(S()), "exampleArgs": _opt(ZArray(ZTuple([S(), ZUnion([S(), N()])])))})  # 시스템 메시지 설명 덮어쓰기 스키마
tool_override_schema = ZObject({                            # 도구 덮어쓰기 스키마
    "description": _opt(S()), "displayTitle": _opt(S()), "wouldLikeTo": _opt(S()), "isCurrently": _opt(S()), "hasAlready": _opt(S()),  # 설명·표시 문구 필드들
    "systemMessageDescription": _opt(system_message_description_override_schema), "disabled": _opt(B()),  # 시스템 메시지 설명·비활성화
})                                                          # (도구 덮어쓰기 끝)
chat_options_schema = ZObject({                             # 채팅 옵션 스키마
    "baseSystemMessage": _opt(S()), "baseAgentSystemMessage": _opt(S()), "basePlanSystemMessage": _opt(S()),  # 기본 시스템 메시지들
    "toolOverrides": _opt(ZRecord(tool_override_schema)),   # 도구별 덮어쓰기
})                                                          # (채팅 옵션 끝)
template_schema = ZEnum(["llama2", "alpaca", "zephyr", "phi2", "phind", "anthropic", "chatml", "none", "openchat", "deepseek",  # 프롬프트 템플릿 이름 enum
                         "xwin-coder", "neural-chat", "codellama-70b", "llava", "gemma", "granite", "llama3", "codestral"])  # (템플릿 이름 이어짐)
autocomplete_options_schema = ZObject({                     # 자동완성 옵션 스키마
    "disable": _opt(B()), "maxPromptTokens": _opt(N()), "debounceDelay": _opt(N()), "modelTimeout": _opt(N()),  # 비활성화·토큰·지연·시간 제한
    "maxSuffixPercentage": _opt(N()), "prefixPercentage": _opt(N()), "transform": _opt(B()), "template": _opt(S()),  # 접미 비율·접두 비율·변환·템플릿
    "onlyMyCode": _opt(B()), "useCache": _opt(B()), "useImports": _opt(B()), "useRecentlyEdited": _opt(B()), "useRecentlyOpened": _opt(B()),  # 내 코드만·캐시·import·최근 편집/열람 사용
    "experimental_includeClipboard": _opt(B()), "experimental_includeRecentlyVisitedRanges": _opt(B()),  # 실험: 클립보드·최근 방문 범위
    "experimental_includeRecentlyEditedRanges": _opt(B()), "experimental_includeDiff": _opt(B()),  # 실험: 최근 편집 범위·diff
    "experimental_enableStaticContextualization": _opt(B()),  # 실험: 정적 문맥화
})                                                          # (자동완성 옵션 끝)
prompt_templates_schema = ZObject({"apply": _opt(S()), "chat": _opt(template_schema), "edit": _opt(S()), "autocomplete": _opt(S())})  # 프롬프트 템플릿 스키마
_base_model_fields = {                                       # baseModelFields(순서 그대로 — 결과 키 순서가 됨)
    "name": S(), "model": S(), "apiKey": _opt(S()), "apiBase": _opt(S()), "contextLength": _opt(N()), "maxStopWords": _opt(N()),  # 이름·모델·키·주소·컨텍스트·정지어
    "roles": _opt(ZArray(model_roles_schema)), "capabilities": _opt(ZArray(model_capability_schema)),  # 역할·기능
    "defaultCompletionOptions": _opt(completion_options_schema), "cacheBehavior": _opt(cache_behavior_schema),  # 완료 옵션·캐시 동작
    "requestOptions": _opt(request_options_schema), "embedOptions": _opt(embed_options_schema), "chatOptions": _opt(chat_options_schema),  # 요청·임베딩·채팅 옵션
    "promptTemplates": _opt(prompt_templates_schema), "useLegacyCompletionsEndpoint": _opt(B()), "useResponsesApi": _opt(B()),  # 프롬프트 템플릿·레거시 엔드포인트·Responses API
    "env": _opt(ZRecord(ZUnion([S(), B(), N()]))), "autocompleteOptions": _opt(autocomplete_options_schema),  # 환경변수·자동완성 옵션
}                                                           # (모델 공통 필드 끝)
model_schema = ZObject({**_base_model_fields, "provider": S(), "sourceFile": _opt(S())})  # 모델 스키마(제공자·출처 파일 추가)
partial_model_schema = ZObject({**_base_model_fields, "provider": S()}).partial()  # 모든 필드가 선택인 부분 모델 스키마

# mcp/index.ts
_base_mcp = {"name": S(), "serverName": _opt(S()), "faviconUrl": _opt(S()), "sourceFile": _opt(S()), "sourceSlug": _opt(S()),  # MCP 공통 필드
             "connectionTimeout": _opt(N(gt=0))}            # 연결 제한 시간(0 초과)
stdio_mcp_server_schema = ZObject({**_base_mcp, "command": S(), "type": _opt(ZLiteral("stdio")), "args": _opt(ZArray(S())),  # stdio MCP 서버 스키마
                                   "env": _opt(ZRecord(S())), "cwd": _opt(S())})  # 환경변수·작업 폴더
sse_or_http_mcp_server_schema = ZObject({**_base_mcp, "url": S(), "type": _opt(ZUnion([ZLiteral("sse"), ZLiteral("streamable-http")])),  # sse/http MCP 서버 스키마
                                         "apiKey": _opt(S()), "requestOptions": _opt(request_options_schema)})  # API 키·요청 옵션
mcp_server_schema = ZUnion([stdio_mcp_server_schema, sse_or_http_mcp_server_schema])  # MCP 서버 스키마(둘 중 하나)
partial_mcp_server_schema = ZUnion([stdio_mcp_server_schema.partial(), sse_or_http_mcp_server_schema.partial()])  # 부분 MCP 서버 스키마

# data/index.ts(dataSchema)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$", re.ASCII)  # semver 정규식
data_schema = ZObject({                                     # 데이터 스키마
    "name": S(), "destination": S(), "schema": S(regex=_SEMVER, regex_message="Version must follow semver format, e.g. 0.2.0"),  # 이름·목적지·스키마 버전(semver 검사)
    "level": _opt(ZUnion([ZLiteral("all"), ZLiteral("noCode")])), "events": _opt(ZArray(S())),  # 수준·이벤트
    "requestOptions": _opt(request_options_schema), "apiKey": _opt(S()),  # 요청 옵션·API 키
})                                                          # (데이터 스키마 끝)

# index.ts
context_schema = ZObject({"name": _opt(S()), "provider": S(), "params": _opt(ZAny())})  # 컨텍스트 스키마
prompt_schema = ZObject({"name": S(), "description": _opt(S()), "prompt": S(), "sourceFile": _opt(S())})  # 프롬프트 스키마
doc_schema = ZObject({"name": S(), "startUrl": S(), "rootUrl": _opt(S()), "faviconUrl": _opt(S()), "useLocalCrawling": _opt(B()),  # 문서 스키마
                      "sourceFile": _opt(S())})             # (문서 스키마 이어짐)
rule_object_schema = ZObject({                              # 규칙 객체 스키마
    "name": S(), "rule": S(), "description": _opt(S()), "globs": _opt(ZUnion([S(), ZArray(S())])),  # 이름·본문·설명·globs
    "regex": _opt(ZUnion([S(), ZArray(S())])), "alwaysApply": _opt(B()), "invokable": _opt(B()), "sourceFile": _opt(S()),  # regex·alwaysApply·invokable·출처 파일
})                                                          # (규칙 객체 끝)
rule_schema = ZUnion([S(), rule_object_schema])             # 규칙 스키마(글 또는 객체)
common_metadata_schema = ZObject({"tags": _opt(S()), "sourceCodeUrl": _opt(S()), "description": _opt(S()), "author": _opt(S()),  # 공통 메타데이터 스키마
                                  "license": _opt(S()), "iconUrl": _opt(S())})  # (공통 메타데이터 이어짐)
env_record = ZRecord(ZUnion([S(), N(), B()]))               # 환경변수 record 스키마
base_config_yaml_schema = ZObject({                         # 기본 설정 스키마
    "name": S(), "version": S(), "schema": _opt(S()),       # 이름·버전·스키마
    "metadata": _opt(ZIntersection(ZRecord(S()), common_metadata_schema.partial())),  # 메타데이터(record 와 공통 필드의 교집합)
    "env": _opt(env_record), "requestOptions": _opt(request_options_schema),  # 환경변수·요청 옵션
})                                                          # (기본 설정 스키마 끝)
COMMON_MODEL_SLUGS = ["anthropic/claude-sonnet-4-6", "anthropic/claude-opus-4-6", "anthropic/claude-sonnet-4"]   # 실제 효과 없음(z.string() 이 먼저 받음)


def _block_item_wrapper(schema: ZObject) -> ZObject:        # 블록 항목 래퍼 스키마 함수
    """blockItemWrapperSchema — {uses, with?, override?(부분)}."""
    return ZObject({"uses": S(), "with": _opt(ZRecord(S())), "override": _opt(schema.partial())})  # {uses, with, override} 객체


def _block_or(schema: ZObject) -> ZUnion:                   # 직접 정의 또는 uses 블록 함수
    """blockOrSchema — 직접 정의 또는 uses 블록."""
    return ZUnion([schema, _block_item_wrapper(schema)])    # 두 형태의 합집합


config_yaml_schema = base_config_yaml_schema.extend({       # config.yaml 스키마(기본 + 섹션)
    "models": _opt(ZArray(ZUnion([model_schema, ZObject({"uses": ZUnion([S(), ZEnum(COMMON_MODEL_SLUGS)]), "with": _opt(ZRecord(S())),  # models 섹션: 직접 정의 또는 uses
                                                          "override": _opt(partial_model_schema)})]))),  # (models 이어짐)
    "context": _opt(ZArray(_block_or(context_schema))),     # context 섹션
    "data": _opt(ZArray(_block_or(data_schema))),           # data 섹션
    "mcpServers": _opt(ZArray(ZUnion([mcp_server_schema, ZObject({"uses": S(), "with": _opt(ZRecord(S())),  # mcpServers 섹션
                                                                   "override": _opt(partial_mcp_server_schema)})]))),  # (mcpServers 이어짐)
    "rules": _opt(ZArray(ZUnion([rule_schema, ZObject({"uses": S(), "with": _opt(ZRecord(S()))})]))),  # rules 섹션
    "prompts": _opt(ZArray(_block_or(prompt_schema))),      # prompts 섹션
    "docs": _opt(ZArray(_block_or(doc_schema))),            # docs 섹션
})                                                          # (config.yaml 스키마 끝)
assistant_unrolled_schema = base_config_yaml_schema.extend({  # 풀린 설정 스키마(실패 항목은 null 허용)
    "models": _opt(ZArray(ZNullable(model_schema))),        # 모델 목록
    "context": _opt(ZArray(ZNullable(context_schema))),     # 컨텍스트 목록
    "data": _opt(ZArray(ZNullable(data_schema))),           # 데이터 목록
    "mcpServers": _opt(ZArray(ZNullable(mcp_server_schema))),  # MCP 서버 목록
    "rules": _opt(ZArray(ZNullable(rule_schema))),          # 규칙 목록
    "prompts": _opt(ZArray(ZNullable(prompt_schema))),      # 프롬프트 목록
    "docs": _opt(ZArray(ZNullable(doc_schema))),            # 문서 목록
})                                                          # (풀린 설정 스키마 끝)
block_schema = ZIntersection(base_config_yaml_schema, ZUnion([  # 블록 스키마: 섹션 하나에 항목 정확히 1개
    ZObject({"models": ZArray(model_schema).length(1)}),    # models 블록
    ZObject({"context": ZArray(context_schema).length(1)}),  # context 블록
    ZObject({"data": ZArray(data_schema).length(1)}),       # data 블록
    ZObject({"mcpServers": ZArray(mcp_server_schema).length(1)}),  # mcpServers 블록
    ZObject({"rules": ZArray(rule_schema).length(1)}),      # rules 블록
    ZObject({"prompts": ZArray(prompt_schema).length(1)}),  # prompts 블록
    ZObject({"docs": ZArray(doc_schema).length(1)}),        # docs 블록
]))                                                         # (블록 스키마 끝)


# ───────────────────────── unroll.ts parseConfigYaml / parseAssistantUnrolled / parseBlock ─────────────────────────
class ConfigParseError(Exception):                          # 설정 해석 실패 예외
    """"Failed to parse config: …" 등 cn 오류 글을 그대로 담는 오류."""


def parse_config_yaml(text: str) -> dict:                   # config.yaml 글을 읽고 검증하는 함수
    """parseConfigYaml — YAML 읽기 + configYamlSchema.safeParse. 실패 글 `Failed to parse config: …`."""
    try:                                                    # YAML 읽기 오류는 해석 오류로 바꿈
        parsed = yaml_parse(text)                            # YAML — YAML 해석
    except YamlError as e:                         # YAML 오류 처리
        raise ConfigParseError(f"Failed to parse config: {e}") from e  # cn 원문 형식의 오류 글
    ok, res = config_yaml_schema.safe_parse(parsed)          # 빈 문서(null)는 "Expected object, received null"
    if ok:                                                  # 검증 성공이면
        return res                                          # 검증된 설정 반환
    raise ConfigParseError(f"Failed to parse config: {format_zod_error(res)}")  # 검증 실패 글로 예외


def parse_assistant_unrolled(data: Any) -> dict:            # 풀린 설정을 검증하는 함수
    """parseAssistantUnrolled — assistantUnrolledSchema.parse. 실패 글 `Failed to parse config: …`."""
    ok, res = assistant_unrolled_schema.safe_parse(data)    # 스키마로 검증
    if ok:                                                  # 성공이면
        return res                                          # 검증된 값 반환
    raise ConfigParseError(f"Failed to parse config: {format_zod_error(res)}")  # 검증 실패 글로 예외


def parse_block(text: str) -> dict:                         # 블록 파일을 읽고 검증하는 함수
    """parseBlock — 블록 파일(name·version + 섹션 하나에 항목 1개). 실패 글 `Failed to parse block: …`."""
    try:                                                    # YAML 읽기 시도
        parsed = yaml_parse(text)                           # YAML 해석
    except YamlError as e:                                  # 실패하면
        raise ConfigParseError(f"Failed to parse block: {e}") from e  # 블록 해석 오류로 바꿈
    ok, res = block_schema.safe_parse(parsed)               # 블록 스키마로 검증
    if ok:                                                  # 성공이면
        return res                                          # 검증된 값 반환
    raise ConfigParseError(f"Failed to parse block: {format_zod_error(res)}")  # 검증 실패 글로 예외


# ───────────────────────── markdown/markdownToRule.ts ─────────────────────────
_JS_WS_CLASS = "[" + JS_WS_CHARS + "]"                       # JS \s — JS 공백 문자 클래스
_JS_LT = "\n\r\u2028\u2029"                                  # JS 줄 끝 글자(m 플래그의 ^ $ 기준)
_FM_SPLIT = re.compile(rf"(?:(?<=[{_JS_LT}])|\A)---{_JS_WS_CLASS}*(?=[{_JS_LT}]|\Z)")   # /^---\s*$/m — --- 줄로 나누는 정규식


def parse_markdown_rule(content: str) -> tuple[Any, str]:   # 마크다운 규칙 읽는 함수
    """parseMarkdownRule — (frontmatter, markdown). \\r\\n 만 \\n 으로, '---' 줄로 나눠 3조각 이상이면 앞머리 있음."""
    normalized = content.replace("\r\n", "\n")               # 줄바꿈 정리(\r 단독은 그대로)
    parts = _FM_SPLIT.split(normalized)                      # JS split(/^---\s*$/m) — --- 줄 기준으로 나눔
    if len(parts) >= 3:                                      # 앞머리 있음
        frontmatter_str = parts[1]                           # 앞머리 YAML
        markdown_content = "---".join(parts[2:])             # 나머지(--- 는 다시 붙임)
        try:                                                # 앞머리 YAML 읽기 시도
            fm = yaml_parse(frontmatter_str)                 # YAML — 앞머리 해석
            if not js_truthy(fm):                            # || {} — 빈 앞머리
                fm = {}                                     # 빈 앞머리는 빈 사전
            return fm, markdown_content.strip(JS_WS_CHARS)   # 본문은 trim
        except YamlError:                                    # 앞머리 오류 → 본문만(cn은 console.warn)
            return {}, normalized                           # 앞머리가 깨졌으면 본문 전체만 사용
    return {}, normalized                                    # 앞머리 없음(본문 trim 안 함)


def fm_get(fm: Any, key: str) -> Any:                       # 앞머리 값을 꺼내는 함수
    """frontmatter.key — 객체가 아니거나 키가 없으면 undefined."""
    if isinstance(fm, dict):                                # 사전이면
        return fm.get(key, MISSING)                         # 키 값(없으면 undefined)
    return MISSING                                          # 아니면 undefined


def get_rule_name(fm: Any, id_: dict) -> str:               # 규칙 이름 정하는 함수
    """getRuleName — name 이 있으면 그것, 파일이면 경로 마지막 두 조각."""
    name = fm_get(fm, "name")                               # 앞머리의 name
    if js_truthy(name):                                     # 있으면
        return name                                         # 그 이름
    display = id_["fileUri"] if id_["uriType"] == "file" else id_["fullSlug"]["packageSlug"]   # packageIdentifierToDisplayName — 표시 이름(파일 경로 또는 패키지 슬러그)
    if id_["uriType"] == "file":                            # 파일이면
        segments = re.split(r"[/\\]", display)               # / 와 \ 모두
        return "/".join(s for s in segments[-2:] if s)      # 경로 마지막 두 조각을 "/" 로 이음
    return display                                          # 슬러그면 패키지 이름


def markdown_to_rule(rule: str, id_: dict) -> dict:         # 마크다운을 규칙 객체로 바꾸는 함수
    """markdownToRule(relativePathForGlobs 없이 — cn 호출 경로와 같음). undefined 값은 키를 만들지 않는다(YAML 왕복에서 빠짐)."""
    fm, markdown = parse_markdown_rule(rule)                # 앞머리와 본문 분리
    out = {"name": get_rule_name(fm, id_), "rule": markdown}  # 이름·본문
    for k in ("globs", "regex", "description", "alwaysApply", "invokable"):   # cn 객체 순서: globs, regex, description, alwaysApply, invokable
        v = fm_get(fm, k)                                   # 앞머리 값
        if v is not MISSING:                                # 있으면
            out[k] = v                                      # 규칙에 복사
    if id_["uriType"] == "file":                             # 파일 출처
        out["sourceFile"] = id_["fileUri"]                  # 출처 파일 기록
    return out                                              # 규칙 반환


def strip_missing(v: Any) -> Any:                           # undefined 제거 함수
    """값 안의 undefined 를 지운다(YAML.stringify 가 undefined 키를 버리는 것과 같게)."""
    if isinstance(v, dict):                                 # 사전이면
        return {k: strip_missing(x) for k, x in v.items() if x is not MISSING}  # undefined 값 키를 버리고 재귀
    if isinstance(v, list):                                 # 배열이면
        return [None if x is MISSING else strip_missing(x) for x in v]   # 배열 안 undefined 는 null 이 됨
    return v                                                # 그 밖은 그대로


ParseFn = Callable[[str], dict]                              # parseConfigYaml / parseBlock 형식
