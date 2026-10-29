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


class _Missing:
    """JS undefined 자리표시(YAML null = Python None 과 구분해야 zod 판정이 같아진다)."""

    _inst = None                                             # 하나만

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:                              # JS 에서 undefined 는 거짓
        return False


MISSING = _Missing()                                         # undefined


def js_truthy(v: Any) -> bool:
    """JS 참/거짓 판정 — 빈 글·0·NaN·null·undefined 는 거짓, 빈 배열·빈 객체는 참."""
    if v is None or v is MISSING:                            # null/undefined
        return False
    if isinstance(v, bool):                                  # 불리언
        return v
    if isinstance(v, (int, float)):                          # 숫자(NaN 거짓)
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))
    if isinstance(v, str):                                   # 글
        return v != ""
    return True                                              # 배열·객체 등은 항상 참


# ───────────────────────── YAML 1.2 core 읽기(yaml 라이브러리 YAML.parse 흉내) ─────────────────────────


class YamlError(Exception):
    """YAML 읽기 오류(글은 yaml 라이브러리와 다를 수 있음 — PyYAML 글)."""


class _Yaml12Loader(yaml.SafeLoader):
    """YAML 1.2 core 스키마 Loader(yaml 라이브러리 기본값과 같은 암묵 형식 판정)."""


_Yaml12Loader.yaml_implicit_resolvers = {}                   # 1.1 규칙 전부 제거(병합 키·날짜·yes/no 포함)
# yaml/dist/schema/core/*.js · common/null.js 의 test 정규식 그대로
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:~|[Nn]ull|NULL)?$"), ["~", "n", "N", ""])
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$"), list("tTfF"))
_Yaml12Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789"))
_Yaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+|[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$"),
    list("-+0123456789."),
)


def _js_number(v: float) -> Any:
    """JS 숫자에는 정수/실수 구분이 없다 — 정수값 실수(1.0)는 int 로 바꿔 JSON 출력이 JS(1)와 같게."""
    if isinstance(v, float) and math.isfinite(v) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def _construct_int(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    """1.2 core int: 0o 8진수, 0x 16진수, 나머지 10진수(앞자리 0 도 10진수). JS 처럼 숫자 하나로."""
    value = loader.construct_scalar(node)                    # 원문
    if value.startswith("0o"):                               # 8진수
        return int(value[2:], 8)
    if value.startswith("0x"):                               # 16진수
        return int(value[2:], 16)
    return int(value, 10)                                    # 10진수(012 → 12)


def _construct_float(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    """1.2 core float(.inf/.nan 포함). 정수값이면 int 로(JS 숫자와 같은 출력)."""
    value = loader.construct_scalar(node)                    # 원문
    low = value.lower()
    if low in (".inf", "+.inf"):                             # 무한대
        return math.inf
    if low == "-.inf":
        return -math.inf
    if low == ".nan":                                        # NaN
        return math.nan
    return _js_number(float(value))                          # 일반 실수


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    """매핑 — 키 중복은 오류(yaml 라이브러리 기본 uniqueKeys), 키는 JS 객체 키처럼 글로."""
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(None, None, f"expected a mapping node, but found {node.id}", node.start_mark)
    out: dict = {}                                           # 결과
    for key_node, value_node in node.value:                  # 순서대로
        key = loader.construct_object(key_node, deep=True)   # 키 값
        js_key = _js_key(key)                                # JS 객체 키 글
        if js_key in out:                                    # 중복 키
            raise YamlError(f"Map keys must be unique at line {key_node.start_mark.line + 1}, column {key_node.start_mark.column + 1}")
        out[js_key] = loader.construct_object(value_node, deep=True)   # 값
    return out


def _js_key(k: Any) -> str:
    """JS 객체 키 = String(k). 숫자 1 → "1", 참 → "true", null → "" (yaml 라이브러리는 null 키를 "" 로 만든다)."""
    if isinstance(k, str):
        return k
    if k is None:
        return ""
    if isinstance(k, bool):
        return "true" if k else "false"
    if isinstance(k, (int, float)):
        return js_number_to_string(k)
    return json.dumps(k, ensure_ascii=False)                 # 그 밖(드묾)


def js_number_to_string(n: Any) -> str:
    """JS String(number) — 1.0 → "1", 무한대 → "Infinity"."""
    if isinstance(n, bool):
        return "true" if n else "false"
    if isinstance(n, float):
        if math.isnan(n):
            return "NaN"
        if math.isinf(n):
            return "Infinity" if n > 0 else "-Infinity"
        if n.is_integer() and abs(n) < 1e21:
            return str(int(n))
        return repr(n)
    return str(n)


_Yaml12Loader.add_constructor("tag:yaml.org,2002:int", _construct_int)
_Yaml12Loader.add_constructor("tag:yaml.org,2002:float", _construct_float)
_Yaml12Loader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)


def yaml_parse(text: str) -> Any:
    """YAML.parse(text) — 빈 문서는 None(JS null). 오류는 YamlError(글은 PyYAML 것)."""
    try:
        return yaml.load(text, Loader=_Yaml12Loader)         # 1.2 core 규칙
    except YamlError:
        raise
    except yaml.YAMLError as e:                              # 문법 오류
        raise YamlError(str(e)) from e


# yaml 라이브러리 stringifyString.js plainString 의 "따옴표 없이 쓸 수 없는 글" 판정(같은 정규식)
_NOT_PLAIN = re.compile(r"^[\n\t ,\[\]{}#&*!|>'\"%@`]|^[?-]$|^[?-][ \t]|[\n:][ \t]|[ \t]\n|[\n\t ]#|[\n\t :]$")
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")   # 큰따옴표가 강제되는 제어 글자
_NON_STR = re.compile(r"^(?:~|[Nn]ull|NULL)?$|^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$|^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"
                      r"|^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+|[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)|[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$")


def yaml_stringify_is_plain(value: str) -> bool:
    """YAML.stringify 가 이 글(값 자리)을 따옴표 없는 plain 으로 쓰는가. 한 줄·indicator 없음·숫자/참거짓으로 안 읽힘."""
    if value == "" or "\n" in value:                         # 빈 글은 "", 여러 줄은 블록
        return False
    if _CTRL.search(value):                                  # 제어 글자 → 큰따옴표
        return False
    if _NOT_PLAIN.search(value):                             # indicator 등
        return False
    if _NON_STR.match(value):                                # 다른 형식으로 읽힐 글 → 따옴표
        return False
    return True


def yaml_reparse_plain(text: str) -> Any:
    """plain 자리에 글을 그대로 끼워 넣은 뒤 다시 읽은 값(cn은 YAML 글에 비밀값을 끼우고 다시 parse 하므로 "123" → 숫자가 됨)."""
    doc = yaml_parse("k: " + text)                           # 값 자리에 넣어 읽기
    if isinstance(doc, dict) and list(doc.keys()) == ["k"]:  # 정상
        return doc["k"]
    raise YamlError("Unexpected scalar")                     # 키가 더 생기는 등 — cn 에선 다른 구조가 되거나 오류


# ───────────────────────── zod 흉내(3.25 v3 동작) ─────────────────────────
VALID, DIRTY, ABORTED = "valid", "dirty", "aborted"          # zod ParseStatus


def parsed_type(v: Any) -> str:
    """zod getParsedType — 오류 글의 received 값."""
    if v is MISSING:
        return "undefined"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "nan" if isinstance(v, float) and math.isnan(v) else "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "object"                                          # 그 밖(드묾)


def _js_stringify(v: Any) -> str:
    """JSON.stringify(값) — 오류 글용."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _join_values(values: list) -> str:
    """zod util.joinValues — 글은 '작은따옴표', " | " 로 잇기."""
    return " | ".join(f"'{v}'" if isinstance(v, str) else js_number_to_string(v) for v in values)


def issue_message(issue: dict) -> str:
    """zod 기본 오류 글(locales/en.js) 중 이 스키마들이 낼 수 있는 것."""
    code = issue["code"]                                     # 종류
    if issue.get("message") is not None:                     # 스키마에 직접 준 글
        return issue["message"]
    if code == "invalid_type":
        return "Required" if issue["received"] == "undefined" else f"Expected {issue['expected']}, received {issue['received']}"
    if code == "invalid_literal":
        return f"Invalid literal value, expected {_js_stringify(issue['expected'])}"
    if code == "invalid_union":
        return "Invalid input"
    if code == "invalid_enum_value":
        return f"Invalid enum value. Expected {_join_values(issue['options'])}, received '{issue['received']}'"
    if code == "invalid_intersection_types":
        return "Intersection results could not be merged"
    if code == "too_small":
        if issue["type"] == "array":
            how = "exactly" if issue["exact"] else ("at least" if issue["inclusive"] else "more than")
            return f"Array must contain {how} {issue['minimum']} element(s)"
        how = "exactly equal to " if issue["exact"] else ("greater than or equal to " if issue["inclusive"] else "greater than ")
        return f"Number must be {how}{js_number_to_string(issue['minimum'])}"
    if code == "too_big":
        if issue["type"] == "array":
            how = "exactly" if issue["exact"] else ("at most" if issue["inclusive"] else "less than")
            return f"Array must contain {how} {issue['maximum']} element(s)"
        how = "exactly" if issue["exact"] else ("less than or equal to" if issue["inclusive"] else "less than")
        return f"Number must be {how} {js_number_to_string(issue['maximum'])}"
    if code == "invalid_string":
        return "Invalid"
    return "Invalid input"                                   # 그 밖


class ZodError(Exception):
    """zod ZodError — issues 목록(각 {code, path, message, …})."""

    def __init__(self, issues: list[dict]):
        self.issues = issues                                 # zod error.errors
        super().__init__(json.dumps(issues, ensure_ascii=False))


def format_zod_error(error: Any) -> str:
    """unroll.ts formatZodError — "경로: 글" 을 ", " 로 잇는다. ZodError 가 아니면 message."""
    if isinstance(error, ZodError):                          # error.errors 가 배열
        parts = []
        for e in error.issues:                               # 각 문제
            path = (".".join(str(p) for p in e["path"]) + ": ") if e["path"] else ""   # 경로 접두
            parts.append(f"{path}{e['message']}")
        return ", ".join(parts)
    msg = str(error)                                         # 일반 오류
    return msg or "Validation failed"


class Z:
    """zod 형식 하나. parse(data, path, issues) → (상태, 값)."""

    def parse(self, data: Any, path: list, issues: list) -> tuple[str, Any]:   # 하위 클래스가 구현
        raise NotImplementedError

    # 편의(zod 메서드 이름 그대로)
    def optional(self) -> "Z":
        return ZOptional(self)

    def nullable(self) -> "Z":
        return ZNullable(self)

    def array(self) -> "Z":
        return ZArray(self)

    def safe_parse(self, data: Any) -> tuple[bool, Any]:
        """safeParse — (성공, 값 또는 ZodError)."""
        issues: list = []                                    # 모은 문제
        status, value = self.parse(data, [], issues)         # 검증
        if status == VALID and not issues:                   # 성공
            return True, value
        for i in issues:                                     # 글 채우기
            i["message"] = issue_message(i)
        return False, ZodError(issues)

    def parse_or_raise(self, data: Any) -> Any:
        """parse — 실패면 ZodError."""
        ok, v = self.safe_parse(data)
        if not ok:
            raise v
        return v


def _add(issues: list, path: list, **kw: Any) -> None:
    """addIssueToContext."""
    issues.append({"path": list(path), **kw})


class ZString(Z):
    """z.string() (+ .regex(정규식, {message}))."""

    def __init__(self, regex: Optional[re.Pattern] = None, regex_message: Optional[str] = None):
        self.regex = regex                                   # 선택: 정규식 검사
        self.regex_message = regex_message                   # 그때 오류 글

    def parse(self, data, path, issues):
        if not isinstance(data, str):                        # 형식 오류
            _add(issues, path, code="invalid_type", expected="string", received=parsed_type(data))
            return ABORTED, None
        if self.regex is not None and not self.regex.search(data):   # 정규식 실패 → dirty
            _add(issues, path, code="invalid_string", validation="regex", message=self.regex_message)
            return DIRTY, data
        return VALID, data


class ZNumber(Z):
    """z.number() (+ .gt(n))."""

    def __init__(self, gt: Optional[float] = None):
        self.gt = gt                                         # 선택: 초과 조건

    def parse(self, data, path, issues):
        t = parsed_type(data)                                # 형식
        if t != "number":
            _add(issues, path, code="invalid_type", expected="number", received=t)
            return ABORTED, None
        if self.gt is not None and not data > self.gt:       # 조건 실패 → dirty
            _add(issues, path, code="too_small", minimum=self.gt, type="number", inclusive=False, exact=False)
            return DIRTY, data
        return VALID, data


class ZBoolean(Z):
    """z.boolean()."""

    def parse(self, data, path, issues):
        if not isinstance(data, bool):
            _add(issues, path, code="invalid_type", expected="boolean", received=parsed_type(data))
            return ABORTED, None
        return VALID, data


class ZAny(Z):
    """z.any() — 무엇이든 통과(undefined 포함)."""

    def parse(self, data, path, issues):
        return VALID, data


class ZLiteral(Z):
    """z.literal(값)."""

    def __init__(self, value: Any):
        self.value = value

    def parse(self, data, path, issues):
        if data is MISSING or data != self.value or type(data) is not type(self.value):   # === 비교
            _add(issues, path, code="invalid_literal", expected=self.value, received=None if data is MISSING else data)
            return ABORTED, None
        return VALID, data


class ZEnum(Z):
    """z.enum([...])."""

    def __init__(self, options: list[str]):
        self.options = options

    def parse(self, data, path, issues):
        if not isinstance(data, str):                        # 글이 아니면 invalid_type(expected 에 후보 나열)
            _add(issues, path, code="invalid_type", expected=_join_values(self.options), received=parsed_type(data))
            return ABORTED, None
        if data not in self.options:                         # 후보 밖
            _add(issues, path, code="invalid_enum_value", received=data, options=self.options)
            return ABORTED, None
        return VALID, data


class ZOptional(Z):
    """.optional() — undefined 면 통과."""

    def __init__(self, inner: Z):
        self.inner = inner

    def parse(self, data, path, issues):
        if data is MISSING:
            return VALID, MISSING
        return self.inner.parse(data, path, issues)


class ZNullable(Z):
    """.nullable() — null 이면 통과."""

    def __init__(self, inner: Z):
        self.inner = inner

    def parse(self, data, path, issues):
        if data is None:
            return VALID, None
        return self.inner.parse(data, path, issues)


def _merge_status(statuses: list[str]) -> str:
    """ParseStatus.mergeArray/mergeObject — 하나라도 aborted 면 aborted, dirty 면 dirty."""
    if ABORTED in statuses:
        return ABORTED
    if DIRTY in statuses:
        return DIRTY
    return VALID


class ZArray(Z):
    """z.array(형식) (+ .length(n))."""

    def __init__(self, item: Z, exact_length: Optional[int] = None):
        self.item = item
        self.exact_length = exact_length

    def length(self, n: int) -> "ZArray":
        return ZArray(self.item, n)

    def parse(self, data, path, issues):
        if not isinstance(data, list):                       # 배열 아님
            _add(issues, path, code="invalid_type", expected="array", received=parsed_type(data))
            return ABORTED, None
        status = VALID                                       # 길이 검사가 먼저(zod 순서)
        if self.exact_length is not None and len(data) != self.exact_length:
            big = len(data) > self.exact_length
            if big:
                _add(issues, path, code="too_big", maximum=self.exact_length, type="array", inclusive=True, exact=True)
            else:
                _add(issues, path, code="too_small", minimum=self.exact_length, type="array", inclusive=True, exact=True)
            status = DIRTY
        results = [self.item.parse(v, path + [i], issues) for i, v in enumerate(data)]   # 항목마다
        st = _merge_status([status] + [r[0] for r in results])
        if st == ABORTED:
            return ABORTED, None
        return st, [r[1] for r in results]


class ZTuple(Z):
    """z.tuple([...]) — exampleArgs 에만 쓰임."""

    def __init__(self, items: list[Z]):
        self.items = items

    def parse(self, data, path, issues):
        if not isinstance(data, list):
            _add(issues, path, code="invalid_type", expected="array", received=parsed_type(data))
            return ABORTED, None
        if len(data) < len(self.items):                      # 모자람 → 바로 실패
            _add(issues, path, code="too_small", minimum=len(self.items), type="array", inclusive=True, exact=False)
            return ABORTED, None
        status = VALID
        if len(data) > len(self.items):                      # 넘침 → dirty, 넘친 항목은 버림
            _add(issues, path, code="too_big", maximum=len(self.items), type="array", inclusive=True, exact=False)
            status = DIRTY
        results = [s.parse(v, path + [i], issues) for i, (s, v) in enumerate(zip(self.items, data))]
        st = _merge_status([status] + [r[0] for r in results])
        if st == ABORTED:
            return ABORTED, None
        return st, [r[1] for r in results]


class ZObject(Z):
    """z.object({...}) — 모르는 키는 버림, 결과 키 순서는 스키마 순서."""

    def __init__(self, shape: dict[str, Z]):
        self.shape = dict(shape)                             # 키 → 형식(순서 유지)

    def extend(self, more: dict[str, Z]) -> "ZObject":
        return ZObject({**self.shape, **more})

    def partial(self) -> "ZObject":
        return ZObject({k: (v if isinstance(v, ZOptional) else ZOptional(v)) for k, v in self.shape.items()})

    def and_(self, other: Z) -> "ZIntersection":
        return ZIntersection(self, other)

    def parse(self, data, path, issues):
        if not isinstance(data, dict):                       # 객체 아님(배열·null 포함)
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))
            return ABORTED, None
        statuses = []                                        # 키마다 상태
        out: dict = {}                                       # 결과
        for key, schema in self.shape.items():               # 스키마 순서로
            value = data.get(key, MISSING)                   # 없으면 undefined
            st, v = schema.parse(value, path + [key], issues)
            statuses.append(st)
            if v is not MISSING and st != ABORTED:           # undefined 는 키를 만들지 않음
                out[key] = v
        st = _merge_status(statuses)
        if st == ABORTED:
            return ABORTED, None
        return st, out


class ZRecord(Z):
    """z.record(값형식) / z.record(키형식, 값형식) — 키는 항상 글."""

    def __init__(self, value: Z):
        self.value = value

    def parse(self, data, path, issues):
        if not isinstance(data, dict):
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))
            return ABORTED, None
        statuses = []
        out: dict = {}
        for key, v in data.items():                          # 입력 순서
            st, pv = self.value.parse(v, path + [key], issues)
            statuses.append(st)
            if pv is not MISSING and st != ABORTED:
                out[key] = pv
        st = _merge_status(statuses)
        if st == ABORTED:
            return ABORTED, None
        return st, out


class ZUnion(Z):
    """z.union([...]) — 처음 성공한 것, 없으면 처음 dirty, 둘 다 없으면 "Invalid input" 하나."""

    def __init__(self, options: list[Z]):
        self.options = options

    def parse(self, data, path, issues):
        dirty = None                                         # 처음 dirty 결과
        for option in self.options:                          # 선택지마다(각자 문제 목록)
            child: list = []
            st, v = option.parse(data, path, child)
            if st == VALID and not child:                    # 성공
                return VALID, v
            if st == DIRTY and dirty is None:
                dirty = (st, v, child)
        if dirty is not None:                                # dirty 선택지의 문제를 올림
            issues.extend(dirty[2])
            return dirty[0], dirty[1]
        _add(issues, path, code="invalid_union")             # 전부 실패
        return ABORTED, None


def _merge_values(a: Any, b: Any) -> tuple[bool, Any]:
    """zod mergeValues — 교집합 결과 합치기."""
    if a is b or (type(a) is type(b) and not isinstance(a, (dict, list)) and a == b):
        return True, a
    if isinstance(a, dict) and isinstance(b, dict):          # 객체: 겹치는 키는 재귀
        new = {**a, **b}
        for k in [k for k in b if k in a]:
            ok, v = _merge_values(a[k], b[k])
            if not ok:
                return False, None
            new[k] = v
        return True, new
    if isinstance(a, list) and isinstance(b, list):          # 배열: 길이 같으면 항목별
        if len(a) != len(b):
            return False, None
        out = []
        for x, y in zip(a, b):
            ok, v = _merge_values(x, y)
            if not ok:
                return False, None
            out.append(v)
        return True, out
    return False, None


class ZIntersection(Z):
    """a.and(b) — 둘 다 검사(왼쪽 먼저), 결과를 합침."""

    def __init__(self, left: Z, right: Z):
        self.left = left
        self.right = right

    def parse(self, data, path, issues):
        ls, lv = self.left.parse(data, path, issues)         # 왼쪽
        rs, rv = self.right.parse(data, path, issues)        # 오른쪽
        if ls == ABORTED or rs == ABORTED:
            return ABORTED, None
        ok, merged = _merge_values(lv, rv)
        if not ok:
            _add(issues, path, code="invalid_intersection_types")
            return ABORTED, None
        return (DIRTY if DIRTY in (ls, rs) else VALID), merged


# ───────────────────────── 스키마(config-yaml/src/schemas) ─────────────────────────
S, N, B = ZString, ZNumber, ZBoolean                         # 짧은 이름


def _opt(z: Z) -> Z:
    return ZOptional(z)


# models.ts
client_certificate_options_schema = ZObject({"cert": S(), "key": S(), "passphrase": _opt(S())})
request_options_schema = ZObject({
    "timeout": _opt(N()),
    "verifySsl": _opt(B()),
    "caBundlePath": _opt(ZUnion([S(), ZArray(S())])),
    "proxy": _opt(S()),
    "headers": _opt(ZRecord(S())),
    "extraBodyProperties": _opt(ZRecord(ZAny())),
    "noProxy": _opt(ZArray(S())),
    "clientCertificate": _opt(client_certificate_options_schema),
})
MODEL_ROLES = ["chat", "autocomplete", "embed", "rerank", "edit", "apply", "summarize", "subagent"]   # modelRolesSchema
model_roles_schema = ZEnum(MODEL_ROLES)
model_capability_schema = ZUnion([ZLiteral("tool_use"), ZLiteral("image_input"), ZLiteral("next_edit"), S()])
completion_options_schema = ZObject({
    "contextLength": _opt(N()), "maxTokens": _opt(N()), "temperature": _opt(N()), "topP": _opt(N()), "topK": _opt(N()),
    "minP": _opt(N()), "presencePenalty": _opt(N()), "frequencyPenalty": _opt(N()), "stop": _opt(ZArray(S())), "n": _opt(N()),
    "reasoning": _opt(B()), "reasoningBudgetTokens": _opt(N()), "promptCaching": _opt(B()), "stream": _opt(B()), "keepAlive": _opt(N()),
})
embedding_tasks_schema = ZUnion([ZLiteral("chunk"), ZLiteral("query")])
cache_behavior_schema = ZObject({"cacheSystemMessage": _opt(B()), "cacheConversation": _opt(B())})


class _ZEmbedPrefixes(ZRecord):
    """z.record(embeddingTasksSchema, z.string()) — 키도 검사."""

    def parse(self, data, path, issues):
        if not isinstance(data, dict):
            _add(issues, path, code="invalid_type", expected="object", received=parsed_type(data))
            return ABORTED, None
        statuses, out = [], {}
        for key, v in data.items():                          # 키 → 값 순서로 검사
            ks, _ = embedding_tasks_schema.parse(key, path + [key], issues)
            vs, pv = S().parse(v, path + [key], issues)
            statuses += [ks, vs]
            if vs != ABORTED and ks != ABORTED:
                out[key] = pv
        st = _merge_status(statuses)
        return (ABORTED, None) if st == ABORTED else (st, out)


embed_options_schema = ZObject({"maxChunkSize": _opt(N()), "maxBatchSize": _opt(N()), "embeddingPrefixes": _opt(_ZEmbedPrefixes(S()))})
system_message_description_override_schema = ZObject({"prefix": _opt(S()), "exampleArgs": _opt(ZArray(ZTuple([S(), ZUnion([S(), N()])])))})
tool_override_schema = ZObject({
    "description": _opt(S()), "displayTitle": _opt(S()), "wouldLikeTo": _opt(S()), "isCurrently": _opt(S()), "hasAlready": _opt(S()),
    "systemMessageDescription": _opt(system_message_description_override_schema), "disabled": _opt(B()),
})
chat_options_schema = ZObject({
    "baseSystemMessage": _opt(S()), "baseAgentSystemMessage": _opt(S()), "basePlanSystemMessage": _opt(S()),
    "toolOverrides": _opt(ZRecord(tool_override_schema)),
})
template_schema = ZEnum(["llama2", "alpaca", "zephyr", "phi2", "phind", "anthropic", "chatml", "none", "openchat", "deepseek",
                         "xwin-coder", "neural-chat", "codellama-70b", "llava", "gemma", "granite", "llama3", "codestral"])
autocomplete_options_schema = ZObject({
    "disable": _opt(B()), "maxPromptTokens": _opt(N()), "debounceDelay": _opt(N()), "modelTimeout": _opt(N()),
    "maxSuffixPercentage": _opt(N()), "prefixPercentage": _opt(N()), "transform": _opt(B()), "template": _opt(S()),
    "onlyMyCode": _opt(B()), "useCache": _opt(B()), "useImports": _opt(B()), "useRecentlyEdited": _opt(B()), "useRecentlyOpened": _opt(B()),
    "experimental_includeClipboard": _opt(B()), "experimental_includeRecentlyVisitedRanges": _opt(B()),
    "experimental_includeRecentlyEditedRanges": _opt(B()), "experimental_includeDiff": _opt(B()),
    "experimental_enableStaticContextualization": _opt(B()),
})
prompt_templates_schema = ZObject({"apply": _opt(S()), "chat": _opt(template_schema), "edit": _opt(S()), "autocomplete": _opt(S())})
_base_model_fields = {                                       # baseModelFields(순서 그대로 — 결과 키 순서가 됨)
    "name": S(), "model": S(), "apiKey": _opt(S()), "apiBase": _opt(S()), "contextLength": _opt(N()), "maxStopWords": _opt(N()),
    "roles": _opt(ZArray(model_roles_schema)), "capabilities": _opt(ZArray(model_capability_schema)),
    "defaultCompletionOptions": _opt(completion_options_schema), "cacheBehavior": _opt(cache_behavior_schema),
    "requestOptions": _opt(request_options_schema), "embedOptions": _opt(embed_options_schema), "chatOptions": _opt(chat_options_schema),
    "promptTemplates": _opt(prompt_templates_schema), "useLegacyCompletionsEndpoint": _opt(B()), "useResponsesApi": _opt(B()),
    "env": _opt(ZRecord(ZUnion([S(), B(), N()]))), "autocompleteOptions": _opt(autocomplete_options_schema),
}
model_schema = ZObject({**_base_model_fields, "provider": S(), "sourceFile": _opt(S())})
partial_model_schema = ZObject({**_base_model_fields, "provider": S()}).partial()

# mcp/index.ts
_base_mcp = {"name": S(), "serverName": _opt(S()), "faviconUrl": _opt(S()), "sourceFile": _opt(S()), "sourceSlug": _opt(S()),
             "connectionTimeout": _opt(N(gt=0))}
stdio_mcp_server_schema = ZObject({**_base_mcp, "command": S(), "type": _opt(ZLiteral("stdio")), "args": _opt(ZArray(S())),
                                   "env": _opt(ZRecord(S())), "cwd": _opt(S())})
sse_or_http_mcp_server_schema = ZObject({**_base_mcp, "url": S(), "type": _opt(ZUnion([ZLiteral("sse"), ZLiteral("streamable-http")])),
                                         "apiKey": _opt(S()), "requestOptions": _opt(request_options_schema)})
mcp_server_schema = ZUnion([stdio_mcp_server_schema, sse_or_http_mcp_server_schema])
partial_mcp_server_schema = ZUnion([stdio_mcp_server_schema.partial(), sse_or_http_mcp_server_schema.partial()])

# data/index.ts(dataSchema)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$", re.ASCII)
data_schema = ZObject({
    "name": S(), "destination": S(), "schema": S(regex=_SEMVER, regex_message="Version must follow semver format, e.g. 0.2.0"),
    "level": _opt(ZUnion([ZLiteral("all"), ZLiteral("noCode")])), "events": _opt(ZArray(S())),
    "requestOptions": _opt(request_options_schema), "apiKey": _opt(S()),
})

# index.ts
context_schema = ZObject({"name": _opt(S()), "provider": S(), "params": _opt(ZAny())})
prompt_schema = ZObject({"name": S(), "description": _opt(S()), "prompt": S(), "sourceFile": _opt(S())})
doc_schema = ZObject({"name": S(), "startUrl": S(), "rootUrl": _opt(S()), "faviconUrl": _opt(S()), "useLocalCrawling": _opt(B()),
                      "sourceFile": _opt(S())})
rule_object_schema = ZObject({
    "name": S(), "rule": S(), "description": _opt(S()), "globs": _opt(ZUnion([S(), ZArray(S())])),
    "regex": _opt(ZUnion([S(), ZArray(S())])), "alwaysApply": _opt(B()), "invokable": _opt(B()), "sourceFile": _opt(S()),
})
rule_schema = ZUnion([S(), rule_object_schema])
common_metadata_schema = ZObject({"tags": _opt(S()), "sourceCodeUrl": _opt(S()), "description": _opt(S()), "author": _opt(S()),
                                  "license": _opt(S()), "iconUrl": _opt(S())})
env_record = ZRecord(ZUnion([S(), N(), B()]))
base_config_yaml_schema = ZObject({
    "name": S(), "version": S(), "schema": _opt(S()),
    "metadata": _opt(ZIntersection(ZRecord(S()), common_metadata_schema.partial())),
    "env": _opt(env_record), "requestOptions": _opt(request_options_schema),
})
COMMON_MODEL_SLUGS = ["anthropic/claude-sonnet-4-6", "anthropic/claude-opus-4-6", "anthropic/claude-sonnet-4"]   # 실제 효과 없음(z.string() 이 먼저 받음)


def _block_item_wrapper(schema: ZObject) -> ZObject:
    """blockItemWrapperSchema — {uses, with?, override?(부분)}."""
    return ZObject({"uses": S(), "with": _opt(ZRecord(S())), "override": _opt(schema.partial())})


def _block_or(schema: ZObject) -> ZUnion:
    """blockOrSchema — 직접 정의 또는 uses 블록."""
    return ZUnion([schema, _block_item_wrapper(schema)])


config_yaml_schema = base_config_yaml_schema.extend({
    "models": _opt(ZArray(ZUnion([model_schema, ZObject({"uses": ZUnion([S(), ZEnum(COMMON_MODEL_SLUGS)]), "with": _opt(ZRecord(S())),
                                                          "override": _opt(partial_model_schema)})]))),
    "context": _opt(ZArray(_block_or(context_schema))),
    "data": _opt(ZArray(_block_or(data_schema))),
    "mcpServers": _opt(ZArray(ZUnion([mcp_server_schema, ZObject({"uses": S(), "with": _opt(ZRecord(S())),
                                                                   "override": _opt(partial_mcp_server_schema)})]))),
    "rules": _opt(ZArray(ZUnion([rule_schema, ZObject({"uses": S(), "with": _opt(ZRecord(S()))})]))),
    "prompts": _opt(ZArray(_block_or(prompt_schema))),
    "docs": _opt(ZArray(_block_or(doc_schema))),
})
assistant_unrolled_schema = base_config_yaml_schema.extend({
    "models": _opt(ZArray(ZNullable(model_schema))),
    "context": _opt(ZArray(ZNullable(context_schema))),
    "data": _opt(ZArray(ZNullable(data_schema))),
    "mcpServers": _opt(ZArray(ZNullable(mcp_server_schema))),
    "rules": _opt(ZArray(ZNullable(rule_schema))),
    "prompts": _opt(ZArray(ZNullable(prompt_schema))),
    "docs": _opt(ZArray(ZNullable(doc_schema))),
})
block_schema = ZIntersection(base_config_yaml_schema, ZUnion([
    ZObject({"models": ZArray(model_schema).length(1)}),
    ZObject({"context": ZArray(context_schema).length(1)}),
    ZObject({"data": ZArray(data_schema).length(1)}),
    ZObject({"mcpServers": ZArray(mcp_server_schema).length(1)}),
    ZObject({"rules": ZArray(rule_schema).length(1)}),
    ZObject({"prompts": ZArray(prompt_schema).length(1)}),
    ZObject({"docs": ZArray(doc_schema).length(1)}),
]))


# ───────────────────────── unroll.ts parseConfigYaml / parseAssistantUnrolled / parseBlock ─────────────────────────
class ConfigParseError(Exception):
    """"Failed to parse config: …" 등 cn 오류 글을 그대로 담는 오류."""


def parse_config_yaml(text: str) -> dict:
    """parseConfigYaml — YAML 읽기 + configYamlSchema.safeParse. 실패 글 `Failed to parse config: …`."""
    try:
        parsed = yaml_parse(text)                            # YAML
    except YamlError as e:
        raise ConfigParseError(f"Failed to parse config: {e}") from e
    ok, res = config_yaml_schema.safe_parse(parsed)          # 빈 문서(null)는 "Expected object, received null"
    if ok:
        return res
    raise ConfigParseError(f"Failed to parse config: {format_zod_error(res)}")


def parse_assistant_unrolled(data: Any) -> dict:
    """parseAssistantUnrolled — assistantUnrolledSchema.parse. 실패 글 `Failed to parse config: …`."""
    ok, res = assistant_unrolled_schema.safe_parse(data)
    if ok:
        return res
    raise ConfigParseError(f"Failed to parse config: {format_zod_error(res)}")


def parse_block(text: str) -> dict:
    """parseBlock — 블록 파일(name·version + 섹션 하나에 항목 1개). 실패 글 `Failed to parse block: …`."""
    try:
        parsed = yaml_parse(text)
    except YamlError as e:
        raise ConfigParseError(f"Failed to parse block: {e}") from e
    ok, res = block_schema.safe_parse(parsed)
    if ok:
        return res
    raise ConfigParseError(f"Failed to parse block: {format_zod_error(res)}")


# ───────────────────────── markdown/markdownToRule.ts ─────────────────────────
_JS_WS_CLASS = "[" + JS_WS_CHARS + "]"                       # JS \s
_JS_LT = "\n\r\u2028\u2029"                                  # JS 줄 끝 글자(m 플래그의 ^ $ 기준)
_FM_SPLIT = re.compile(rf"(?:(?<=[{_JS_LT}])|\A)---{_JS_WS_CLASS}*(?=[{_JS_LT}]|\Z)")   # /^---\s*$/m


def parse_markdown_rule(content: str) -> tuple[Any, str]:
    """parseMarkdownRule — (frontmatter, markdown). \\r\\n 만 \\n 으로, '---' 줄로 나눠 3조각 이상이면 앞머리 있음."""
    normalized = content.replace("\r\n", "\n")               # 줄바꿈 정리(\r 단독은 그대로)
    parts = _FM_SPLIT.split(normalized)                      # JS split(/^---\s*$/m)
    if len(parts) >= 3:                                      # 앞머리 있음
        frontmatter_str = parts[1]                           # 앞머리 YAML
        markdown_content = "---".join(parts[2:])             # 나머지(--- 는 다시 붙임)
        try:
            fm = yaml_parse(frontmatter_str)                 # YAML
            if not js_truthy(fm):                            # || {} — 빈 앞머리
                fm = {}
            return fm, markdown_content.strip(JS_WS_CHARS)   # 본문은 trim
        except YamlError:                                    # 앞머리 오류 → 본문만(cn은 console.warn)
            return {}, normalized
    return {}, normalized                                    # 앞머리 없음(본문 trim 안 함)


def fm_get(fm: Any, key: str) -> Any:
    """frontmatter.key — 객체가 아니거나 키가 없으면 undefined."""
    if isinstance(fm, dict):
        return fm.get(key, MISSING)
    return MISSING


def get_rule_name(fm: Any, id_: dict) -> str:
    """getRuleName — name 이 있으면 그것, 파일이면 경로 마지막 두 조각."""
    name = fm_get(fm, "name")
    if js_truthy(name):
        return name
    display = id_["fileUri"] if id_["uriType"] == "file" else id_["fullSlug"]["packageSlug"]   # packageIdentifierToDisplayName
    if id_["uriType"] == "file":
        segments = re.split(r"[/\\]", display)               # / 와 \ 모두
        return "/".join(s for s in segments[-2:] if s)
    return display


def markdown_to_rule(rule: str, id_: dict) -> dict:
    """markdownToRule(relativePathForGlobs 없이 — cn 호출 경로와 같음). undefined 값은 키를 만들지 않는다(YAML 왕복에서 빠짐)."""
    fm, markdown = parse_markdown_rule(rule)
    out = {"name": get_rule_name(fm, id_), "rule": markdown}  # 이름·본문
    for k in ("globs", "regex", "description", "alwaysApply", "invokable"):   # cn 객체 순서: globs, regex, description, alwaysApply, invokable
        v = fm_get(fm, k)
        if v is not MISSING:
            out[k] = v
    if id_["uriType"] == "file":                             # 파일 출처
        out["sourceFile"] = id_["fileUri"]
    return out


def strip_missing(v: Any) -> Any:
    """값 안의 undefined 를 지운다(YAML.stringify 가 undefined 키를 버리는 것과 같게)."""
    if isinstance(v, dict):
        return {k: strip_missing(x) for k, x in v.items() if x is not MISSING}
    if isinstance(v, list):
        return [None if x is MISSING else strip_missing(x) for x in v]   # 배열 안 undefined 는 null 이 됨
    return v


ParseFn = Callable[[str], dict]                              # parseConfigYaml / parseBlock 형식
