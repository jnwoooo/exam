# Python 3.8 ~ 3.14 차이 — ncn을 3.14로 만들고 3.8로도 빌드할 수 있는지 판단 자료

> 목적: ncn 1단계는 Python 3.14 + macOS로 개발하고, Windows 7 대응이 필요하면 **같은 코드를 3.8로 빌드**한다(계획 12장). 이 문서는 "3.8 문법 안에서 쓴다(R1)"가 얼마나 불편한지, 무엇을 잃는지 판단하기 위한 정리다.
> 작성 2026-10-10. 라이브러리 버전은 같은 날 PyPI에서 확인한 값.

---

## 1. 결론
- **평소 쓰는 Python 문법은 3.8에서 대부분 그대로 된다.** 못 쓰는 것은 몇 가지 편의 문법이고, 모두 3.8식 대안이 있다(3장).
- **막히는 곳은 MCP 공식 SDK 하나**(3.10+) → 어댑터 뒤에 두고 3.8용 최소 클라이언트를 따로 둔다(R3).
- **잃는 것**: 3.11부터의 속도 향상(CLI는 LLM 대기 시간이 대부분이라 체감 없음), 3.8의 보안 지원(2024-10 종료 — Win7용 빌드에만 쓰므로 범위 한정).
- **검증**: 같은 테스트를 3.14와 3.8에서 모두 돌리고 `vermin --target=3.8`로 문법 검사 → 3.8에서 안 되는 코드는 들어오는 즉시 실패(6장).

---

## 2. 버전별 변화(이 프로젝트 관점)

| 버전(출시 / 지원 종료) | 주요 변화 | ncn 영향 |
|---|---|---|
| **3.8** (2019-10 / **2024-10 종료**) | `:=`(대입 표현식), 위치 전용 인자 `/`, f-string `{x=}`, `typing.Literal`·`Protocol`·`TypedDict`·`Final`, asyncio REPL, Windows 기본 이벤트 루프 Proactor(서브프로세스 지원) | **기준선.** Windows 7 SP1을 지원하는 마지막 버전 |
| **3.9** (2020-10 / 2025-10) | `list[int]`·`dict[str,int]`을 실행 중에도 사용(PEP 585), dict 합치기 `a \| b`(PEP 584), `str.removeprefix/removesuffix`(PEP 616), `zoneinfo`, `graphlib`, `functools.cache`, 새 파서(PEG) | 자주 쓸 법함 → 대안 있음. **Windows 7 지원 종료** |
| **3.10** (2021-10 / 2026-10) | `match` 문(PEP 634), `X \| Y` 타입을 실행 중에도(PEP 604), 괄호로 묶은 `with`, `zip(strict=True)`, 더 친절한 오류 메시지, `itertools.pairwise`, dataclass `kw_only`·`slots`, `aiter`/`anext`, `ParamSpec`·`TypeAlias`·`TypeGuard` | 보통 → 대안 있음 |
| **3.11** (2022-10 / 2027-10) | **CPython 평균 약 25% 빠름**(작업에 따라 10~60%), `ExceptionGroup`·`except*`(PEP 654), `asyncio.TaskGroup`·`asyncio.timeout`, `tomllib`, `typing.Self`·`LiteralString`·`Required/NotRequired`, 오류 위치 정밀 표시, `enum.StrEnum`, `datetime.UTC` | 병렬 도구 실행에 TaskGroup을 쓰고 싶어질 수 있음 → `gather`로 |
| **3.12** (2023-10 / 2028-10) | 타입 매개변수 문법 `def f[T]()`·`type X = …`(PEP 695), f-string 문법 확장 — 안에 같은 따옴표·여러 줄(PEP 701), `itertools.batched`, `Path.walk`, `typing.override`, `distutils` 제거 | 적음 |
| **3.13** (2024-10 / 2029-10) | 새 대화형 셸, GIL 없는 빌드(실험, PEP 703), JIT(실험, PEP 744), 옛 모듈 제거(PEP 594: `cgi`·`telnetlib`·`crypt` 등), `copy.replace`, `warnings.deprecated`, `locals()` 의미 정리(PEP 667) | 없음(쓰지 않는 모듈) |
| **3.14** (2025-10 / 2030-10) | 타입 주석 지연 평가가 기본(PEP 649/749, `annotationlib`), t-string 템플릿(PEP 750), 괄호 없는 `except A, B:`(PEP 758), `finally` 안의 `return`·`break` 경고(PEP 765), `compression.zstd`(PEP 784), GIL 없는 빌드 정식 지원(PEP 779), `concurrent.interpreters`(PEP 734), 원격 디버깅(PEP 768), `Path.copy`·`Path.move` | 없음(쓸 일 거의 없음) |

---

## 3. 3.8에서 못 쓰는 것 → 대신 쓰는 법

| 3.9+ 문법 | 3.8에서 |
|---|---|
| `def f(x: list[str]) -> dict[str, int]:` | 파일 맨 위 `from __future__ import annotations`를 두면 **주석 안에서는 그대로** 사용 가능 |
| `Paths = list[str]`(실행 중 별칭), `cast(list[str], v)`, `isinstance(x, int \| str)` | `from typing import List, Union` → `List[str]`, `isinstance(x, (int, str))` |
| `x: str \| None = None`(pydantic 모델·dataclass 필드) | **`Optional[str]`** — ★ pydantic처럼 주석을 실행 중에 읽는 곳은 `__future__`로도 안 됨 |
| `a \| b`(dict 합치기) | `{**a, **b}` |
| `s.removeprefix("./")` | `s[2:] if s.startswith("./") else s` (작은 함수 하나로) |
| `match kind:` / `case "read": …` | `if` / `elif` |
| `zip(a, b, strict=True)` | 길이를 먼저 비교 |
| `@functools.cache` | `@functools.lru_cache(maxsize=None)` |
| `async with asyncio.TaskGroup() as tg:` | `await asyncio.gather(*tasks)` — cn의 병렬 도구 실행(`Promise.all`)과 같은 의미 |
| `async with asyncio.timeout(10):` | `await asyncio.wait_for(coro, 10)` |
| `except* ValueError` / `ExceptionGroup` | 일반 `except` |
| `tomllib` | 쓰지 않음(설정은 YAML) |
| `from typing import Self` | 클래스 이름을 문자열로 |
| `type Pair = tuple[int, int]` / `def f[T](x: T)` | `Pair = Tuple[int, int]` / `T = TypeVar("T")` |
| f-string 안에 같은 따옴표 `f"{d["k"]}"` | 다른 따옴표 `f"{d['k']}"` |
| `itertools.batched(xs, 50)` | `[xs[i:i+50] for i in range(0, len(xs), 50)]` |

**3.8에서도 되는 것(안심하고 사용)**: dataclass, `typing.Literal/Protocol/TypedDict/Final`, `:=`, f-string `{x=}`, `asyncio` 서브프로세스(Windows 포함), dict 순서 보장, `pathlib`, `subprocess`, `difflib`(Write·Edit의 diff), `json`, `re`, `fnmatch`.

---

## 4. 라이브러리 — 3.8을 지원하는 마지막 버전(PyPI, 2026-10-10)

| 라이브러리 | ncn 용도 | 최신(요구 Python) | **3.8 마지막** | 영향 |
|---|---|---|---|---|
| httpx | LLM·MCP HTTP | 0.28.1 (≥3.8) | 0.28.1 | 없음 |
| PyYAML | config.yaml·permissions.yaml | 6.0.3 (≥3.8) | 6.0.3 | 없음 |
| pydantic | 설정 검증(선택) | 2.14.0 (≥3.10) | 2.10.6 | 쓸 기능 차이 거의 없음. 안 쓰고 dataclass로도 가능 |
| prompt_toolkit | 대화형 입력 | 3.0.53 (≥3.10) | 3.0.52 | 없음 |
| rich | 출력 꾸미기 | 15.0.0 (≥3.9) | 14.3.4 | 없음 |
| tiktoken | 토큰 세기(o200k) | 0.14.0 (≥3.9) | 0.7.0 | o200k는 0.7부터 지원 → cn과 같은 토큰 세기 가능 |
| anyio | httpx 내부 | 4.15.1 (≥3.10) | 4.6.2 | 설치 도구가 자동으로 맞춤 |
| PyInstaller | 실행 파일 묶음 | 6.22.3 (3.8~3.15) | 6.22.3 | 같은 도구로 3.8·3.14 둘 다 빌드 |
| **mcp**(공식 SDK) | MCP 클라이언트 | 2.3.0 (≥3.10) | **없음** | **어댑터 뒤에 두고 3.8용 최소 클라이언트(약 400줄)** |
| vermin | 최소 버전 검사(개발용) | 1.8.0 | — | 개발 도구 |

→ 실제 운영: `pyproject.toml`에 3.14용은 최신, 3.8용은 위 "3.8 마지막" 버전으로 고정한 **잠금 파일 두 개**를 둔다.

---

## 5. 그 밖의 차이
| 항목 | 내용 |
|---|---|
| 보안 지원 | 3.8은 2024-10 종료(이후 보안 수정 없음), 3.14는 2030-10까지. **Win7용 빌드에만** 3.8 사용 — Win7 자체가 2020년에 지원 종료된 OS |
| 속도 | 3.11+가 빠르지만 ncn은 대부분 LLM 응답·명령 실행을 기다리는 시간 → 체감 없음 |
| Windows 지원 | 3.8: Windows 7 SP1+(일부 업데이트 필요할 수 있음 — VM 확인) / 3.9+: Windows 8.1+ / 최신: Windows 10+ |
| 기본 파일 인코딩 | 3.8·3.14 모두 Windows에서 로캘 인코딩(한국어 Windows는 cp949). UTF-8 기본은 3.15 예정(PEP 686) → **모든 파일 입출력에 인코딩 명시**(어차피 인코딩 보존 기능 E3에서 다룸) |
| Windows 콘솔 한글 | 3.6+부터 유니코드 콘솔 API 사용 → 3.8도 Win7 콘솔에서 한글 정상 |
| 타입 주석 평가 | 3.14는 지연 평가가 기본, 3.8은 즉시 평가 → `from __future__ import annotations`를 모든 파일에 두면 두 버전이 비슷하게 동작 |

---

## 6. 검증 방법(믿지 않고 검사)
```bash
uv run --python 3.14 pytest -q          # 개발 기준
uv run --python 3.8  pytest -q          # 같은 테스트를 3.8에서(uv가 3.8.20을 받아 줌 — 이 Mac에서 확인)
uv run --with vermin vermin --target=3.8- --no-tips newcontinue/   # 3.8에서 안 되는 문법·라이브러리 호출 검출
```
- 핵심 모듈(loop·tools·permissions·compaction·config·sysmsg)은 두 버전 모두 통과해야 한다.
- MCP 어댑터는 3.14에서 공식 SDK판, 3.8에서 최소 클라이언트판을 각각 같은 서버(`/mcp`)로 시험한다.
- 3.8에서 안 되는 코드가 들어오면 그 자리에서 실패 → 나중에 몰아서 고칠 일이 없다.
