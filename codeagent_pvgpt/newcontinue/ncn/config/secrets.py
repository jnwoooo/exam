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

from ncn.config import env as _env                           # dotenv.parse
from ncn.tools.formatters import JS_WS_CHARS, path_join, path_resolve, read_file_utf8   # Node path·fs 흉내

# Hub(api.continue.dev) 호출이 실패하면 @continuedev/sdk 0.0.13 runtime.js 가 던지는 FetchError 글(직접 실행해 확인)
HUB_FETCH_FAILED = "The request failed and the interceptors did not return an alternative response"

# ───────────────────────── 템플릿(unroll.ts:83-141) ─────────────────────────
_WS = "[" + JS_WS_CHARS + "]"                                # JS \s
TEMPLATE_VAR_REGEX = re.compile(r"\$\{\{" + _WS + r"*([^}" + JS_WS_CHARS + r"]+)" + _WS + r"*\}\}")   # /\${{[\s]*([^}\s]+)[\s]*}}/g


def get_template_variables(templated: Any) -> list[str]:
    """getTemplateVariables — 글 안의 변수 이름들(중복 없이, 처음 나온 순서)."""
    if not templated or not isinstance(templated, str):      # 방어(빈 값·글 아님)
        return []
    seen: dict[str, None] = {}                               # Set(삽입 순서)
    for m in TEMPLATE_VAR_REGEX.finditer(templated):
        seen.setdefault(m.group(1), None)
    return list(seen)


def fill_template_variables(templated: Any, data: Mapping[str, str]) -> str:
    """fillTemplateVariables — 데이터에 있는 변수만 바꾸고, 없는 것은 원래 ${{ … }} 그대로 둔다."""
    if not templated or not isinstance(templated, str):      # 방어
        return ""
    return TEMPLATE_VAR_REGEX.sub(lambda m: data[m.group(1)] if m.group(1) in data else m.group(0), templated)


def flatten_template_data(inputs: Optional[Mapping[str, str]] = None, secrets: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """flattenTemplateData — inputs.<키>, secrets.<키> 로 평탄화(continue 네임스페이스는 비어 있음)."""
    flat: dict[str, str] = {}
    for k, v in (inputs or {}).items():
        flat[f"inputs.{k}"] = v
    for k, v in (secrets or {}).items():
        flat[f"secrets.{k}"] = v
    return flat


def render_template_data(raw: str, *, inputs: Optional[Mapping[str, str]] = None, secrets: Optional[Mapping[str, str]] = None) -> str:
    """renderTemplateData — 글 하나에 inputs/secrets 채우기."""
    return fill_template_variables(raw, flatten_template_data(inputs, secrets))


def replace_inputs_with_secrets(yaml_content: str) -> str:
    """replaceInputsWithSecrets — 주입 블록의 ${{ inputs.X }} 를 ${{ secrets.X }} 로."""
    m: dict[str, str] = {}
    for v in get_template_variables(yaml_content):
        if v.startswith("inputs."):
            m[v] = "${{ " + v.replace("inputs.", "secrets.", 1) + " }}"
    return fill_template_variables(yaml_content, m)


# ───────────────────────── 패키지 식별자(interfaces/slugs.ts) ─────────────────────────
def _home_directory(environ: Mapping[str, str]) -> Optional[str]:
    """getHomeDirectory — HOME(앞뒤 공백 제거) 다음 USERPROFILE."""
    h = (environ.get("HOME") or "").strip(JS_WS_CHARS)       # env?.HOME?.trim()
    if h:
        return h
    u = (environ.get("USERPROFILE") or "").strip(JS_WS_CHARS)
    if u:
        return u
    return None


def decode_full_slug(full: str) -> dict:
    """decodeFullSlug — "owner/pkg@ver" (split(/[/@]/), 버전 없으면 latest). 모자란 조각은 None(JS undefined)."""
    parts = re.split(r"[/@]", full)                          # 나누기
    owner = parts[0] if len(parts) > 0 else None
    package = parts[1] if len(parts) > 1 else None
    version = parts[2] if len(parts) > 2 and parts[2] else "latest"   # versionSlug || "latest"
    return {"ownerSlug": owner, "packageSlug": package, "versionSlug": version}


def decode_package_identifier(identifier: str, environ: Mapping[str, str]) -> dict:
    """decodePackageIdentifier — "." 또는 "/" 시작 = 파일, "file://" = 파일(접두 제거), "~" = 홈으로 바꾼 파일, 그 밖 = 허브 슬러그."""
    if identifier.startswith(".") or identifier.startswith("/"):
        return {"uriType": "file", "fileUri": identifier}
    if identifier.startswith("file://"):
        return {"uriType": "file", "fileUri": identifier[7:]}
    if identifier.startswith("~"):                           # 앞 ~ 만 바꿈
        home = _home_directory(environ)
        return {"uriType": "file", "fileUri": identifier if not home else home + identifier[1:]}
    return {"uriType": "slug", "fullSlug": decode_full_slug(identifier)}


def _js_str(v: Any) -> str:
    """`${v}` — undefined 는 "undefined"."""
    return "undefined" if v is None else str(v)


def encode_package_slug(ps: dict) -> str:
    """encodePackageSlug — "owner/package"."""
    return f"{_js_str(ps.get('ownerSlug'))}/{_js_str(ps.get('packageSlug'))}"


def encode_full_slug(fs: dict) -> str:
    """encodeFullSlug — "owner/package@version"."""
    return f"{_js_str(fs.get('ownerSlug'))}/{_js_str(fs.get('packageSlug'))}@{_js_str(fs.get('versionSlug'))}"


def encode_package_identifier(pid: dict) -> str:
    """encodePackageIdentifier — 슬러그는 full slug, 파일은 경로 그대로."""
    return encode_full_slug(pid["fullSlug"]) if pid["uriType"] == "slug" else pid["fileUri"]


def package_identifier_to_shorthand_slug(pid: dict) -> str:
    """clientRender.ts packageIdentifierToShorthandSlug — 슬러그 "owner/package", 파일은 "/"."""
    if pid["uriType"] == "slug":
        return f"{_js_str(pid['fullSlug'].get('ownerSlug'))}/{_js_str(pid['fullSlug'].get('packageSlug'))}"
    return "/"


# ───────────────────────── FQSN(Fully Qualified Secret Name) ─────────────────────────
def encode_fqsn(fqsn: dict) -> str:
    """encodeFQSN — [owner/package…, 이름].join("/")."""
    return "/".join([encode_package_slug(p) for p in fqsn["packageSlugs"]] + [fqsn["secretName"]])


def decode_fqsn(s: str) -> dict:
    """decodeFQSN — 마지막 조각 = 비밀 이름, 앞 조각은 둘씩 owner/package. 홀수면 오류."""
    parts = s.split("/")                                     # 나누기
    secret_name = parts.pop()                                # 마지막 = 이름(실제 조회 키)
    slugs = []
    for i in range(0, len(parts), 2):                        # 둘씩
        if i + 1 >= len(parts):
            raise ValueError("Invalid FQSN format: package slug must have two parts")
        slugs.append({"ownerSlug": parts[i], "packageSlug": parts[i + 1]})
    return {"packageSlugs": slugs, "secretName": secret_name}


def secret_to_fqsn_map(secret_names: list[str], parent_packages: list[dict]) -> dict[str, str]:
    """secretToFQSNMap — 이름 → "${{ secrets.<부모들>/이름 }}". 최상위 파일은 부모가 "/" 라서 "//이름"."""
    out: dict[str, str] = {}
    for secret in secret_names:
        parts = [package_identifier_to_shorthand_slug(p) for p in parent_packages] + [secret]
        out[secret] = "${{ secrets." + "/".join(parts) + " }}"
    return out


def extract_fqsn_map(raw: Any, parent_packages: list[dict]) -> dict[str, str]:
    """extractFQSNMap — 글 안의 secrets.* 변수를 FQSN 템플릿으로 바꾸는 표."""
    secrets = [v.replace("secrets.", "", 1) for v in get_template_variables(raw) if v.startswith("secrets.")]
    return secret_to_fqsn_map(secrets, parent_packages)


# ───────────────────────── SecretResult(interfaces/SecretResult.ts) ─────────────────────────
def encode_secret_location(loc: dict) -> str:
    """encodeSecretLocation — 값 없이 위치만 돌아온 비밀의 표기(로컬에서는 쓰일 일이 없지만 형식 유지)."""
    t = loc["secretType"]
    if t == "organization":
        return f"organization:{loc['orgSlug']}/{loc['secretName']}"
    if t == "user":
        return f"user:{loc['userSlug']}/{loc['secretName']}"
    if t == "package":
        return f"package:{encode_package_slug(loc['packageSlug'])}/{loc['secretName']}"
    if t == "not_found":
        return f"not_found:{loc['secretName']}"
    if t == "models_add_on":
        return f"models_add_on:{encode_package_slug(loc['blockSlug'])}/{loc['secretName']}"
    if t == "local_env":
        return f"local_env:{loc['secretName']}"
    if t == "process_env":
        return f"process_env:{loc['secretName']}"
    raise ValueError(f"Invalid secret type: {t}")


# ───────────────────────── CLIPlatformClient ─────────────────────────
@dataclass
class LocalPlatformClient:
    """cn CLIPlatformClient(로컬 부분) + 실패하는 Hub. warnings 에 cn이 console.warn 하던 글을 모은다."""
    environ: Mapping[str, str]                               # dotenv 적용된 환경변수(process.env)
    continue_home: str                                       # env.continueHome(글 그대로 — 상대경로면 cwd 기준)
    cwd: str                                                 # process.cwd()
    warnings: list[str] = field(default_factory=list)        # 경고 글(cn console.warn)
    hub_calls: int = 0                                       # Hub 를 부르려 한 횟수(시험·진단용)

    def _find_in_env_file(self, file_path: str, secret_name: str) -> Optional[str]:
        """findSecretInEnvFile — 파일 없으면 None, 읽기 오류는 경고 후 None."""
        real = path_resolve(self.cwd, file_path)             # fs 는 상대경로를 cwd 기준으로 해석
        try:
            if not os.path.exists(real):                     # fs.existsSync
                return None
            parsed = _env.parse_dotenv(read_file_utf8(real))  # dotenv.parse
            return parsed.get(secret_name)                   # 없으면 None(undefined)
        except Exception as e:                               # 읽기 오류
            self.warnings.append(f"Error reading .env file at {file_path}: {e}")
            return None

    def _find_local(self, fqsn: dict) -> Optional[dict]:
        """findSecretInLocalEnvFiles — 환경변수 → 세 .env 파일(값이 빈 글이면 다음으로)."""
        name = fqsn["secretName"]                            # 실제 조회 키(마지막 조각)
        v = self.environ.get(name)                           # process.env[name]
        if v:                                                # 값이 truthy 일 때만
            return {"found": True, "fqsn": fqsn, "value": v, "secretLocation": {"secretName": name, "secretType": "local_env"}}
        for p in (path_join(self.continue_home, ".env"),     # ~/.continue/.env
                  path_join(self.cwd, ".continue", ".env"),  # <cwd>/.continue/.env
                  path_join(self.cwd, ".env")):              # <cwd>/.env
            sv = self._find_in_env_file(p, name)
            if sv:                                           # truthy 만 채택
                return {"found": True, "fqsn": fqsn, "value": sv, "secretLocation": {"secretName": name, "secretType": "local_env"}}
        return None

    def resolve_fqsns(self, fqsns: list[dict]) -> list[Optional[dict]]:
        """resolveFQSNs — 로컬에서 먼저 찾고, 못 찾은 것이 있으면 Hub 를 부른다(→ 폐쇄망: 실패 경고만)."""
        if not fqsns:                                        # 비어 있으면 Hub 도 안 부름
            return []
        results: list[Optional[dict]] = [None] * len(fqsns)
        for i, f in enumerate(fqsns):                        # 로컬
            r = self._find_local(f)
            if r and r.get("found"):
                results[i] = r
        if any(r is None for r in results):                  # 못 찾은 것 → apiClient.syncSecrets(...) 가 네트워크 오류로 실패
            self.hub_calls += 1
            self.warnings.append(f"Error resolving FQSNs through API: {HUB_FETCH_FAILED}")
        return results


def extract_rendered_secrets_map(raw_vars: list[str], client: LocalPlatformClient, always_use_proxy: bool = False) -> dict[str, str]:
    """extractRenderedSecretsMap — FQSN 변수들 → 값(또는 위치 템플릿). raw_vars = 문서 전체의 템플릿 변수 이름 목록."""
    secrets = [v.replace("secrets.", "", 1) for v in raw_vars if v.startswith("secrets.")]   # FQSN 글
    fqsns = [decode_fqsn(s) for s in secrets]                # 해석(형식 오류는 그대로 던짐)
    results = client.resolve_fqsns(fqsns)                    # 찾기
    out: dict[str, str] = {}
    for r in results:
        if not r:                                            # 못 찾음 → 표에 없음 → 원문 유지
            continue
        if "value" in r and not always_use_proxy:            # 사용자 비밀 = 값
            out[encode_fqsn(r["fqsn"])] = r["value"]
        else:                                                # 위치만
            out[encode_fqsn(r["fqsn"])] = "${{ secrets." + encode_secret_location(r["secretLocation"]) + " }}"
    return out


def map_strings(value: Any, fn: Callable[[str, bool], Any]) -> Any:
    """값 안의 모든 글(객체 키 포함)에 fn 적용. fn(글, 키인가) — YAML 글 전체에 정규식을 거는 cn 방식을 값 단위로 흉내."""
    if isinstance(value, str):
        return fn(value, False)
    if isinstance(value, list):
        return [map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {fn(k, True): map_strings(v, fn) for k, v in value.items()}
    return value


def collect_template_variables(value: Any) -> list[str]:
    """값 전체(키 포함)의 템플릿 변수 이름(처음 나온 순서) — YAML.stringify 결과에 getTemplateVariables 를 건 것과 같은 집합."""
    seen: dict[str, None] = {}

    def visit(s: str, _is_key: bool) -> str:
        for v in get_template_variables(s):
            seen.setdefault(v, None)
        return s

    map_strings(value, visit)
    return list(seen)
