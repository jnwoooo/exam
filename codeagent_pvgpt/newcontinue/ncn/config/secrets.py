# ncn/config/secrets.py — ${{ secrets.X }}·${{ inputs.X }} 템플릿과 비밀값 찾기. cn packages/config-yaml/src/load/unroll.ts(템플릿 함수),
#   interfaces/slugs.ts(FQSN·패키지 식별자), interfaces/SecretResult.ts, load/clientRender.ts, extensions/cli/src/CLIPlatformClient.ts 를 옮긴 것
# 찾는 순서(CLIPlatformClient.findSecretInLocalEnvFiles): 환경변수(값이 빈 글이면 못 찾은 것) → <continueHome>/.env → <cwd>/.continue/.env → <cwd>/.env
#   → (못 찾은 것만) Hub API syncSecrets. 폐쇄망이므로 Hub 는 부르지 않고, cn이 그 호출에 실패했을 때와 같은 경고만 남긴다.
# 주의(dotenv 함정): cn은 시작할 때 dotenv.config()로 <cwd>/.env 를 환경변수에 먼저 넣으므로 실제로는 <cwd>/.env 가 ~/.continue/.env 보다 먼저 이긴다
#   → 이 모듈은 받은 environ 을 그대로 쓰고, 그 environ 을 dotenv 적용 상태로 만드는 것은 loader(env.effective_environ)가 한다.
# 못 찾은 비밀은 오류 없이 "${{ secrets.//X }}" 글이 설정값에 그대로 남는다(cn과 같음).
from __future__ import annotations                           # 타입 주석 지연 평가

import os                                                    # 파일 존재 확인
import re                                                    # 템플릿 정규식
from dataclasses import dataclass, field                     # 결과 형식
from typing import Any, Callable, Mapping, Optional          # 타입

from ncn.config import env as _env                           # dotenv.parse — dotenv 파서 사용
from ncn.tools.formatters import JS_WS_CHARS, path_join, path_resolve, read_file_utf8   # Node path·fs 흉내

# Hub(api.continue.dev) 호출이 실패하면 @continuedev/sdk 0.0.13 runtime.js 가 던지는 FetchError 글(직접 실행해 확인)
HUB_FETCH_FAILED = "The request failed and the interceptors did not return an alternative response"  # Hub 호출 실패 때 cn 이 남기는 오류 글

# ───────────────────────── 템플릿(unroll.ts:83-141) ─────────────────────────
_WS = "[" + JS_WS_CHARS + "]"                                # JS \s — JS 공백 문자 클래스
TEMPLATE_VAR_REGEX = re.compile(r"\$\{\{" + _WS + r"*([^}" + JS_WS_CHARS + r"]+)" + _WS + r"*\}\}")   # /\${{[\s]*([^}\s]+)[\s]*}}/g — ${{ 변수 }} 템플릿 정규식


def get_template_variables(templated: Any) -> list[str]:    # 템플릿 변수 이름 모으는 함수
    """getTemplateVariables — 글 안의 변수 이름들(중복 없이, 처음 나온 순서)."""
    if not templated or not isinstance(templated, str):      # 방어(빈 값·글 아님)
        return []                                           # 글이 아니면 빈 목록
    seen: dict[str, None] = {}                               # Set(삽입 순서)
    for m in TEMPLATE_VAR_REGEX.finditer(templated):        # 템플릿 변수를 하나씩 찾음
        seen.setdefault(m.group(1), None)                   # 중복 없이 처음 순서 유지
    return list(seen)                                       # 이름 목록 반환


def fill_template_variables(templated: Any, data: Mapping[str, str]) -> str:  # 템플릿 변수 채우는 함수
    """fillTemplateVariables — 데이터에 있는 변수만 바꾸고, 없는 것은 원래 ${{ … }} 그대로 둔다."""
    if not templated or not isinstance(templated, str):      # 방어
        return ""                                           # 글이 아니면 빈 글
    return TEMPLATE_VAR_REGEX.sub(lambda m: data[m.group(1)] if m.group(1) in data else m.group(0), templated)  # 데이터에 있으면 값으로, 없으면 원문 그대로


def flatten_template_data(inputs: Optional[Mapping[str, str]] = None, secrets: Optional[Mapping[str, str]] = None) -> dict[str, str]:  # inputs·secrets 를 한 사전으로 평탄화하는 함수
    """flattenTemplateData — inputs.<키>, secrets.<키> 로 평탄화(continue 네임스페이스는 비어 있음)."""
    flat: dict[str, str] = {}                               # 결과 사전
    for k, v in (inputs or {}).items():                     # inputs 를 하나씩
        flat[f"inputs.{k}"] = v                             # "inputs.<키>" 로 저장
    for k, v in (secrets or {}).items():                    # secrets 를 하나씩
        flat[f"secrets.{k}"] = v                            # "secrets.<키>" 로 저장
    return flat                                             # 평탄화된 사전 반환


def render_template_data(raw: str, *, inputs: Optional[Mapping[str, str]] = None, secrets: Optional[Mapping[str, str]] = None) -> str:  # 글 하나에 inputs/secrets 채우는 함수
    """renderTemplateData — 글 하나에 inputs/secrets 채우기."""
    return fill_template_variables(raw, flatten_template_data(inputs, secrets))  # 평탄화 후 채워 반환


def replace_inputs_with_secrets(yaml_content: str) -> str:  # inputs 변수를 secrets 로 바꾸는 함수
    """replaceInputsWithSecrets — 주입 블록의 ${{ inputs.X }} 를 ${{ secrets.X }} 로."""
    m: dict[str, str] = {}                                  # 바꿀 변수 표
    for v in get_template_variables(yaml_content):          # 글 안의 변수를 하나씩
        if v.startswith("inputs."):                         # inputs. 로 시작하면
            m[v] = "${{ " + v.replace("inputs.", "secrets.", 1) + " }}"  # secrets. 로 바꾼 템플릿 글을 표에 넣음
    return fill_template_variables(yaml_content, m)         # 표대로 채워 반환


# ───────────────────────── 패키지 식별자(interfaces/slugs.ts) ─────────────────────────
def _home_directory(environ: Mapping[str, str]) -> Optional[str]:  # 홈 폴더를 찾는 함수
    """getHomeDirectory — HOME(앞뒤 공백 제거) 다음 USERPROFILE."""
    h = (environ.get("HOME") or "").strip(JS_WS_CHARS)       # env?.HOME?.trim() — HOME 값(공백 제거)
    if h:                                                   # 있으면
        return h                                            # 그 값 반환
    u = (environ.get("USERPROFILE") or "").strip(JS_WS_CHARS)  # USERPROFILE 값(공백 제거)
    if u:                                                   # 있으면
        return u                                            # 그 값 반환
    return None                                             # 둘 다 없으면 None


def decode_full_slug(full: str) -> dict:                    # "owner/pkg@ver" 해석 함수
    """decodeFullSlug — "owner/pkg@ver" (split(/[/@]/), 버전 없으면 latest). 모자란 조각은 None(JS undefined)."""
    parts = re.split(r"[/@]", full)                          # 나누기
    owner = parts[0] if len(parts) > 0 else None            # 소유자 조각
    package = parts[1] if len(parts) > 1 else None          # 패키지 조각
    version = parts[2] if len(parts) > 2 and parts[2] else "latest"   # versionSlug || "latest"  # 버전 조각(없으면 latest)
    return {"ownerSlug": owner, "packageSlug": package, "versionSlug": version}  # 조각들을 사전으로 반환


def decode_package_identifier(identifier: str, environ: Mapping[str, str]) -> dict:  # 패키지 식별자 해석 함수
    """decodePackageIdentifier — "." 또는 "/" 시작 = 파일, "file://" = 파일(접두 제거), "~" = 홈으로 바꾼 파일, 그 밖 = 허브 슬러그."""
    if identifier.startswith(".") or identifier.startswith("/"):  # . 또는 / 로 시작하면 파일
        return {"uriType": "file", "fileUri": identifier}   # 파일 경로 그대로
    if identifier.startswith("file://"):                    # file:// 로 시작하면
        return {"uriType": "file", "fileUri": identifier[7:]}  # 접두 7글자를 뗀 파일 경로
    if identifier.startswith("~"):                           # 앞 ~ 만 바꿈
        home = _home_directory(environ)                     # 홈 폴더
        return {"uriType": "file", "fileUri": identifier if not home else home + identifier[1:]}  # ~ 를 홈으로 바꾼 파일 경로(홈이 없으면 그대로)
    return {"uriType": "slug", "fullSlug": decode_full_slug(identifier)}  # 그 밖은 허브 슬러그


def _js_str(v: Any) -> str:                                 # JS 템플릿 문자열 변환 함수
    """`${v}` — undefined 는 "undefined"."""
    return "undefined" if v is None else str(v)             # None 은 "undefined", 나머지는 str


def encode_package_slug(ps: dict) -> str:                   # "owner/package" 인코딩 함수
    """encodePackageSlug — "owner/package"."""
    return f"{_js_str(ps.get('ownerSlug'))}/{_js_str(ps.get('packageSlug'))}"  # 소유자/패키지 이어 붙임


def encode_full_slug(fs: dict) -> str:                      # "owner/package@version" 인코딩 함수
    """encodeFullSlug — "owner/package@version"."""
    return f"{_js_str(fs.get('ownerSlug'))}/{_js_str(fs.get('packageSlug'))}@{_js_str(fs.get('versionSlug'))}"  # 소유자/패키지@버전 이어 붙임


def encode_package_identifier(pid: dict) -> str:            # 패키지 식별자 인코딩 함수
    """encodePackageIdentifier — 슬러그는 full slug, 파일은 경로 그대로."""
    return encode_full_slug(pid["fullSlug"]) if pid["uriType"] == "slug" else pid["fileUri"]  # 슬러그면 full slug, 파일이면 경로


def package_identifier_to_shorthand_slug(pid: dict) -> str:  # 짧은 슬러그 변환 함수
    """clientRender.ts packageIdentifierToShorthandSlug — 슬러그 "owner/package", 파일은 "/"."""
    if pid["uriType"] == "slug":                            # 슬러그면
        return f"{_js_str(pid['fullSlug'].get('ownerSlug'))}/{_js_str(pid['fullSlug'].get('packageSlug'))}"  # "owner/package"
    return "/"                                              # 파일이면 "/"


# ───────────────────────── FQSN(Fully Qualified Secret Name) ─────────────────────────
def encode_fqsn(fqsn: dict) -> str:                         # FQSN 인코딩 함수
    """encodeFQSN — [owner/package…, 이름].join("/")."""
    return "/".join([encode_package_slug(p) for p in fqsn["packageSlugs"]] + [fqsn["secretName"]])  # 패키지 슬러그들 + 비밀 이름을 "/" 로 이음


def decode_fqsn(s: str) -> dict:                            # FQSN 해석 함수
    """decodeFQSN — 마지막 조각 = 비밀 이름, 앞 조각은 둘씩 owner/package. 홀수면 오류."""
    parts = s.split("/")                                     # 나누기
    secret_name = parts.pop()                                # 마지막 = 이름(실제 조회 키)
    slugs = []                                              # 패키지 슬러그 모음
    for i in range(0, len(parts), 2):                        # 둘씩
        if i + 1 >= len(parts):                             # 짝이 안 맞으면
            raise ValueError("Invalid FQSN format: package slug must have two parts")  # 형식 오류 예외
        slugs.append({"ownerSlug": parts[i], "packageSlug": parts[i + 1]})  # owner/package 한 쌍 추가
    return {"packageSlugs": slugs, "secretName": secret_name}  # 슬러그들과 비밀 이름 반환


def secret_to_fqsn_map(secret_names: list[str], parent_packages: list[dict]) -> dict[str, str]:  # 비밀 이름 → FQSN 템플릿 표 함수
    """secretToFQSNMap — 이름 → "${{ secrets.<부모들>/이름 }}". 최상위 파일은 부모가 "/" 라서 "//이름"."""
    out: dict[str, str] = {}                                # 결과 표
    for secret in secret_names:                             # 비밀 이름마다
        parts = [package_identifier_to_shorthand_slug(p) for p in parent_packages] + [secret]  # 부모 패키지 경로 + 이름
        out[secret] = "${{ secrets." + "/".join(parts) + " }}"  # "${{ secrets.… }}" 템플릿 글 저장
    return out                                              # 표 반환


def extract_fqsn_map(raw: Any, parent_packages: list[dict]) -> dict[str, str]:  # 글 안의 secrets 변수 → FQSN 표 함수
    """extractFQSNMap — 글 안의 secrets.* 변수를 FQSN 템플릿으로 바꾸는 표."""
    secrets = [v.replace("secrets.", "", 1) for v in get_template_variables(raw) if v.startswith("secrets.")]  # secrets. 접두를 뗀 이름들
    return secret_to_fqsn_map(secrets, parent_packages)     # FQSN 표로 변환


# ───────────────────────── SecretResult(interfaces/SecretResult.ts) ─────────────────────────
def encode_secret_location(loc: dict) -> str:               # 비밀 위치 인코딩 함수
    """encodeSecretLocation — 값 없이 위치만 돌아온 비밀의 표기(로컬에서는 쓰일 일이 없지만 형식 유지)."""
    t = loc["secretType"]                                   # 비밀 종류
    if t == "organization":                                 # 조직 비밀
        return f"organization:{loc['orgSlug']}/{loc['secretName']}"  # organization:<조직>/<이름>
    if t == "user":                                         # 사용자 비밀
        return f"user:{loc['userSlug']}/{loc['secretName']}"  # user:<사용자>/<이름>
    if t == "package":                                      # 패키지 비밀
        return f"package:{encode_package_slug(loc['packageSlug'])}/{loc['secretName']}"  # package:<패키지>/<이름>
    if t == "not_found":                                    # 못 찾은 비밀
        return f"not_found:{loc['secretName']}"             # not_found:<이름>
    if t == "models_add_on":                                # 모델 애드온 비밀
        return f"models_add_on:{encode_package_slug(loc['blockSlug'])}/{loc['secretName']}"  # models_add_on:<블록>/<이름>
    if t == "local_env":                                    # 로컬 .env 파일 비밀
        return f"local_env:{loc['secretName']}"             # local_env:<이름>
    if t == "process_env":                                  # 프로세스 환경변수 비밀
        return f"process_env:{loc['secretName']}"           # process_env:<이름>
    raise ValueError(f"Invalid secret type: {t}")           # 알 수 없는 종류는 오류


# ───────────────────────── CLIPlatformClient ─────────────────────────
@dataclass                                                  # 데이터 클래스로 선언
class LocalPlatformClient:                                  # 로컬 환경변수·.env 에서 비밀을 찾는 클라이언트
    """cn CLIPlatformClient(로컬 부분) + 실패하는 Hub. warnings 에 cn이 console.warn 하던 글을 모은다."""
    environ: Mapping[str, str]                               # dotenv 적용된 환경변수(process.env)
    continue_home: str                                       # env.continueHome(글 그대로 — 상대경로면 cwd 기준)
    cwd: str                                                 # process.cwd() — 작업 폴더
    warnings: list[str] = field(default_factory=list)        # 경고 글(cn console.warn)
    hub_calls: int = 0                                       # Hub 를 부르려 한 횟수(시험·진단용)

    def _find_in_env_file(self, file_path: str, secret_name: str) -> Optional[str]:  # .env 파일 하나에서 비밀 찾기
        """findSecretInEnvFile — 파일 없으면 None, 읽기 오류는 경고 후 None."""
        real = path_resolve(self.cwd, file_path)             # fs 는 상대경로를 cwd 기준으로 해석
        try:                                                # 파일 읽기·파싱 중 오류는 경고로 처리
            if not os.path.exists(real):                     # fs.existsSync — 파일이 없으면
                return None                                 # None
            parsed = _env.parse_dotenv(read_file_utf8(real))  # dotenv.parse — .env 를 dotenv 로 파싱
            return parsed.get(secret_name)                   # 없으면 None(undefined)
        except Exception as e:                               # 읽기 오류
            self.warnings.append(f"Error reading .env file at {file_path}: {e}")  # 경고 글 저장
            return None                                     # 못 찾은 것으로 처리

    def _find_local(self, fqsn: dict) -> Optional[dict]:    # 로컬(환경변수·.env)에서 비밀 찾기
        """findSecretInLocalEnvFiles — 환경변수 → 세 .env 파일(값이 빈 글이면 다음으로)."""
        name = fqsn["secretName"]                            # 실제 조회 키(마지막 조각)
        v = self.environ.get(name)                           # process.env[name] — 환경변수 값
        if v:                                                # 값이 truthy 일 때만
            return {"found": True, "fqsn": fqsn, "value": v, "secretLocation": {"secretName": name, "secretType": "local_env"}}  # 환경변수에서 찾음
        for p in (path_join(self.continue_home, ".env"),     # ~/.continue/.env — ~/.continue/.env 부터 순서대로
                  path_join(self.cwd, ".continue", ".env"),  # <cwd>/.continue/.env — <cwd>/.continue/.env
                  path_join(self.cwd, ".env")):              # <cwd>/.env — <cwd>/.env
            sv = self._find_in_env_file(p, name)            # 파일에서 찾기
            if sv:                                           # truthy 만 채택
                return {"found": True, "fqsn": fqsn, "value": sv, "secretLocation": {"secretName": name, "secretType": "local_env"}}  # 파일에서 찾음
        return None                                         # 어디에도 없으면 None

    def resolve_fqsns(self, fqsns: list[dict]) -> list[Optional[dict]]:  # FQSN 목록 해석 함수
        """resolveFQSNs — 로컬에서 먼저 찾고, 못 찾은 것이 있으면 Hub 를 부른다(→ 폐쇄망: 실패 경고만)."""
        if not fqsns:                                        # 비어 있으면 Hub 도 안 부름
            return []                                       # 빈 결과
        results: list[Optional[dict]] = [None] * len(fqsns)  # 찾은 결과 자리(처음엔 None)
        for i, f in enumerate(fqsns):                        # 로컬
            r = self._find_local(f)                         # 로컬에서 찾기
            if r and r.get("found"):                        # 찾았으면
                results[i] = r                              # 결과에 저장
        if any(r is None for r in results):                  # 못 찾은 것 → apiClient.syncSecrets(...) 가 네트워크 오류로 실패
            self.hub_calls += 1                             # Hub 호출 시도 횟수 증가
            self.warnings.append(f"Error resolving FQSNs through API: {HUB_FETCH_FAILED}")  # Hub 실패 경고 저장(폐쇄망이라 실제 호출은 하지 않음)
        return results                                      # 결과 반환


def extract_rendered_secrets_map(raw_vars: list[str], client: LocalPlatformClient, always_use_proxy: bool = False) -> dict[str, str]:  # 렌더용 비밀 표 만드는 함수
    """extractRenderedSecretsMap — FQSN 변수들 → 값(또는 위치 템플릿). raw_vars = 문서 전체의 템플릿 변수 이름 목록."""
    secrets = [v.replace("secrets.", "", 1) for v in raw_vars if v.startswith("secrets.")]   # FQSN 글
    fqsns = [decode_fqsn(s) for s in secrets]                # 해석(형식 오류는 그대로 던짐)
    results = client.resolve_fqsns(fqsns)                    # 찾기
    out: dict[str, str] = {}                                # 결과 표
    for r in results:                                       # 결과마다
        if not r:                                            # 못 찾음 → 표에 없음 → 원문 유지
            continue                                        # 못 찾았으면 건너뜀(원문 유지)
        if "value" in r and not always_use_proxy:            # 사용자 비밀 = 값
            out[encode_fqsn(r["fqsn"])] = r["value"]        # 값을 표에 넣음
        else:                                                # 위치만
            out[encode_fqsn(r["fqsn"])] = "${{ secrets." + encode_secret_location(r["secretLocation"]) + " }}"  # 위치 템플릿을 표에 넣음
    return out                                              # 표 반환


def map_strings(value: Any, fn: Callable[[str, bool], Any]) -> Any:  # 값 속 모든 글에 함수 적용
    """값 안의 모든 글(객체 키 포함)에 fn 적용. fn(글, 키인가) — YAML 글 전체에 정규식을 거는 cn 방식을 값 단위로 흉내."""
    if isinstance(value, str):                              # 글이면
        return fn(value, False)                             # 키가 아님으로 적용
    if isinstance(value, list):                             # 목록이면
        return [map_strings(v, fn) for v in value]          # 원소마다 재귀
    if isinstance(value, dict):                             # 사전이면
        return {fn(k, True): map_strings(v, fn) for k, v in value.items()}  # 키는 키로, 값은 재귀로 적용
    return value                                            # 그 밖은 그대로


def collect_template_variables(value: Any) -> list[str]:    # 값 전체의 템플릿 변수 모으기
    """값 전체(키 포함)의 템플릿 변수 이름(처음 나온 순서) — YAML.stringify 결과에 getTemplateVariables 를 건 것과 같은 집합."""
    seen: dict[str, None] = {}                              # 중복 없는 이름 모음

    def visit(s: str, _is_key: bool) -> str:                # 글마다 부르는 방문 함수
        for v in get_template_variables(s):                 # 변수를 하나씩
            seen.setdefault(v, None)                        # 처음 순서로 기록
        return s                                            # 글은 그대로 반환

    map_strings(value, visit)                               # 모든 글 방문
    return list(seen)                                       # 이름 목록 반환
