# ncn/config/loader.py — 설정 읽기. cn extensions/cli/src/configLoader.ts + services/ConfigService.ts(주입 블록·규칙·기본 모델) + config.ts(getLlmApi)
#   + services/ModelService.ts(대화 모델 고르기) + systemMessage.ts(loadMarkdownRulesWithMetadata) + hubLoader.ts(isStringRule)
#   + packages/config-yaml/src/load/{unroll,merge,blockDuplicationDetector,getBlockType}.ts + registryClient.ts 를 옮긴 것
# 원천 순서(determineConfigSource): --config → <전역>/config.yaml → (없으면) Hub 의 continuedev/default-cli-config.
# 폐쇄망: Hub 는 부르지 않는다. cn이 Hub 로 가는 경우는 cn이 그 호출에 실패했을 때와 같은 결과·오류 글을 낸다.
#   - 설정 파일 없음 → loadDefaultConfig 의 apiClient.getAssistant 실패 → 오류 "The request failed and the interceptors did not return an alternative response"
#   - --config owner/pkg(슬러그) → 로그인 없음 → 로컬 unroll → "Slug-based package resolution is not supported"
#   - uses: owner/pkg 블록 → 같은 글로 비치명 오류(해당 항목 null, 경고)
#   - 못 찾은 secrets → syncSecrets 실패 경고 "Error resolving FQSNs through API: …", 값은 "${{ secrets.//X }}" 그대로
#   - chat 모델 없음 → 기본 모델 슬러그 주입 실패 → 헤드리스면 "No model specified in headless mode (and failed to load default model)"
# cn은 YAML.stringify 한 글 전체에 비밀값을 끼우고 다시 YAML.parse 한다. ncn은 값 단위로 같은 결과를 만든다(따옴표 없이 쓰이는 글이면 다시 읽어 형식까지 맞춤).
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # JSON.stringify 흉내
import os                                                    # 파일 확인
import pathlib                                               # 경로 형식
import re                                                    # 정규식
import urllib.parse                                          # new URL(...).hostname
import urllib.request                                        # file:// URL → 경로
from dataclasses import dataclass, field                     # 결과 형식
from typing import Any, Mapping, Optional                    # 타입

from ncn.config import env as _env                           # dotenv·환경변수
from ncn.config import paths as _paths                       # 전역 폴더
from ncn.config import secrets as _sec                       # 템플릿·비밀값
from ncn.config.schema import (MISSING, ConfigParseError, YamlError, fm_get, js_truthy, markdown_to_rule,   # 검증·YAML
                               parse_assistant_unrolled, parse_block, parse_config_yaml, parse_markdown_rule,
                               strip_missing, yaml_reparse_plain, yaml_stringify_is_plain)
from ncn.tools.formatters import (JS_WS_CHARS, NodeFsError, path_dirname, path_is_absolute, path_join,   # Node path·fs 흉내
                                  path_resolve, read_file_utf8)


class ConfigError(Exception):
    """설정 읽기 실패 — str(e) 는 cn이 내는 오류 글과 같다(헤드리스면 {"status":"error","message":…} 로 나가는 글).
    hub=True 면 cn이 Hub 호출을 하다 실패하는 경우(폐쇄망 안내를 덧붙이고 싶을 때 화면 쪽이 참고)."""

    def __init__(self, message: str, *, hub: bool = False):
        super().__init__(message)                            # str(e) == message
        self.message = message                               # 글
        self.hub = hub                                       # Hub 관련 실패인가


# ───────────────────────── 결과 형식 ─────────────────────────
@dataclass
class ModelConfig:
    """모델 하나(cn ModelConfig). raw 는 cn 모양(camelCase) 그대로 — 요청 본문·토큰 세기는 raw 를 봐도 된다."""
    name: str                                                # 표시 이름
    provider: str                                            # 제공자(openai 등)
    model: str                                               # 모델 id(토큰 배수 판정에 쓰임)
    api_base: Optional[str]                                  # apiBase
    api_key: Optional[str]                                   # apiKey(못 찾은 비밀이면 "${{ secrets.//X }}" 글 그대로)
    roles: Optional[list[str]]                               # roles(없으면 None = chat 으로 취급)
    capabilities: Optional[list[str]]                        # capabilities
    default_completion_options: dict = field(default_factory=dict)   # defaultCompletionOptions(contextLength·maxTokens 등)
    request_options: dict = field(default_factory=dict)      # requestOptions(headers·timeout 등)
    chat_options: dict = field(default_factory=dict)         # chatOptions(toolOverrides·baseSystemMessage 등)
    raw: dict = field(default_factory=dict)                  # 원본 dict

    @property
    def env(self) -> dict:
        """model.env."""
        return self.raw.get("env") or {}

    @classmethod
    def from_raw(cls, raw: dict) -> "ModelConfig":
        """cn 모양 dict → ModelConfig."""
        return cls(
            name=raw.get("name"), provider=raw.get("provider"), model=raw.get("model"),
            api_base=raw.get("apiBase"), api_key=raw.get("apiKey"),
            roles=raw.get("roles"), capabilities=raw.get("capabilities"),
            default_completion_options=raw.get("defaultCompletionOptions") or {},
            request_options=raw.get("requestOptions") or {},
            chat_options=raw.get("chatOptions") or {},
            raw=raw,
        )


@dataclass
class LoadedConfig:
    """읽은 설정(cn ConfigService state.config + 고른 모델)."""
    raw: dict                                                # AssistantUnrolled(cn 모양). models 등에 실패 블록 자리 None 이 남을 수 있음
    models: list[ModelConfig]                                # None 아닌 모델들
    chat_model: Optional[ModelConfig]                        # 대화 모델(ModelService 규칙). 못 고르면 None
    chat_model_error: Optional[str]                          # 못 고른 이유(cn 오류 글) — 헤드리스면 이 글로 종료
    mcp_servers: list                                        # mcpServers(cn 그대로 — 실패 블록은 None)
    rules: list                                              # rules(글 또는 {name, rule, …}) — 주입 규칙·.continue/rules 포함
    prompts: list                                            # prompts
    context: list                                            # context
    docs: list                                               # docs
    data: list                                               # data
    name: Optional[str]                                      # 설정 이름
    version: Optional[str]                                   # 설정 버전
    env: dict                                                # 최상위 env
    request_options: Optional[dict]                          # 최상위 requestOptions
    warnings: list[str] = field(default_factory=list)        # cn이 console.warn/error 로 찍던 글(헤드리스에선 안 보임)
    errors: list[dict] = field(default_factory=list)         # unroll 비치명 오류({fatal, message})
    source: dict = field(default_factory=dict)               # 원천 {"type": "cli-flag"|"local-config-yaml", "path"?}
    config_path: Optional[str] = None                        # --config 값

    @property
    def rule_texts(self) -> list[str]:
        """systemMessage.ts getConfigYamlRules — 글이면 그대로, 객체면 .rule, 빈 것은 뺌."""
        out = []
        for r in self.rules or []:
            t = r if isinstance(r, str) else (r.get("rule") if isinstance(r, dict) else None)
            if js_truthy(t):
                out.append(t)
        return out


# ───────────────────────── 경로 판정(configLoader.ts:511-525, hubLoader.ts isStringRule) ─────────────────────────
def is_file_path(config_path: str) -> bool:
    """isFilePath — ".", "/", "~" 시작, Windows 드라이브·UNC, 또는 .yaml/.yml/.json 이 들어 있으면 파일."""
    return (config_path.startswith(".") or config_path.startswith("/") or config_path.startswith("~")
            or re.match(r"^[A-Za-z]:[/\\]", config_path) is not None
            or config_path.startswith("\\\\")
            or ".yaml" in config_path or ".yml" in config_path or ".json" in config_path)


HUB_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")   # hubLoader.ts HUB_SLUG_PATTERN


def is_string_rule(rule: str) -> bool:
    """isStringRule — 공백·줄바꿈이 있으면 글 규칙, 경로·슬러그 모양이면 아님."""
    if " " in rule or "\n" in rule:
        return True
    if any(rule.startswith(p) for p in ["file:/", ".", "/", "~"]) or "\\" in rule:
        return False
    if HUB_SLUG_PATTERN.match(rule):
        return False
    return True


# ───────────────────────── RegistryClient(config-yaml/src/registryClient.ts) ─────────────────────────
class RegistryClient:
    """파일 블록 읽기. 상대경로는 cwd 기준이 먼저, 없으면 rootPath(설정 파일 폴더). 슬러그는 지원 안 함."""

    def __init__(self, root_path: Optional[str], cwd: str):
        self.root_path = root_path                           # dirname(설정 파일) — 글 그대로
        self.cwd = cwd                                       # process.cwd()

    def _read(self, display: str) -> str:
        """fs.readFileSync(display, "utf8") — 상대경로는 cwd 기준. 오류 글의 경로는 display 그대로(Node 와 같게)."""
        actual = display if path_is_absolute(display) else path_resolve(self.cwd, display)
        try:
            return read_file_utf8(actual)
        except NodeFsError as e:                             # 경로 표기를 Node 처럼 넘긴 글로
            raise NodeFsError(str(e).replace(f"'{actual}'", f"'{display}'"), e.code) from e

    def get_content(self, id_: dict) -> str:
        """getContent — 파일이면 읽기, 슬러그면 오류."""
        if id_["uriType"] == "file" and id_.get("content") is not None:   # 미리 읽은 내용
            return id_["content"]
        if id_["uriType"] == "file":
            return self._from_file_path(id_["fileUri"])
        if id_["uriType"] == "slug":                         # cn: 허브 슬러그 해석 제거됨
            raise ValueError("Slug-based package resolution is not supported")
        raise ValueError(f"Unknown package identifier type: {id_['uriType']}")

    def _from_file_path(self, filepath: str) -> str:
        """getContentFromFilePath."""
        if filepath.startswith("file://"):                   # new URL(filepath)
            u = urllib.parse.urlsplit(filepath)
            return self._read(urllib.request.url2pathname(u.path))
        if path_is_absolute(filepath):                       # 절대경로
            return self._read(filepath)
        resolved = path_resolve(self.cwd, filepath)          # cwd 기준 먼저
        if os.path.exists(resolved):
            return self._read(resolved)
        if self.root_path:                                   # 설정 파일 폴더 기준
            return self._read(path_join(self.root_path, filepath))
        raise ValueError("No rootPath provided for relative file path")


# ───────────────────────── 블록 처리 도우미(unroll.ts) ─────────────────────────
BLOCK_TYPES = ["models", "context", "data", "mcpServers", "rules", "prompts", "docs"]   # getBlockType.ts BLOCK_TYPES


def get_block_type(block: dict) -> Optional[str]:
    """getBlockType — 검사 순서 context, models, docs, mcpServers, data, rules, prompts."""
    for k in ("context", "models", "docs", "mcpServers", "data", "rules", "prompts"):
        v = block.get(k)
        if isinstance(v, list) and len(v) > 0:               # ?.length
            return k
    return None


class BlockDuplicationDetector:
    """blockDuplicationDetector.ts — 섹션별로 이름이 이미 나왔으면 중복(뒤 항목을 버림)."""

    def __init__(self):
        self.records: dict[str, set] = {t: set() for t in BLOCK_TYPES}   # 섹션 → 본 이름들

    def _check(self, identifier: Any, block_type: str) -> bool:
        key = json.dumps(identifier, ensure_ascii=False) if not isinstance(identifier, str) else identifier   # Set 키(undefined 포함)
        if identifier is MISSING or identifier is None:
            key = repr(identifier)
        if key in self.records[block_type]:
            return True
        self.records[block_type].add(key)
        return False

    def is_duplicated(self, block: Any, block_type: str) -> bool:
        """isDuplicated — null/undefined 는 검사 안 함(중복 아님)."""
        if block is None or block is MISSING:
            return False
        if block_type == "rules":                            # 규칙: 글 자체 또는 name
            return self._check(block if isinstance(block, str) else block.get("name", MISSING), "rules")
        if block_type == "context":                          # 컨텍스트: name ?? params.title ?? provider
            key = block.get("name")
            if key is None:
                params = block.get("params")
                key = params.get("title") if isinstance(params, dict) else None
            if key is None:
                key = block.get("provider", MISSING)
            return self._check(key, "context")
        return self._check(block.get("name", MISSING), block_type)


def merge_overrides(block: dict, overrides: dict) -> dict:
    """mergeOverrides — 얕은 키 덮어쓰기(블록 객체를 직접 바꿈)."""
    for k, v in overrides.items():
        block[k] = v
    return block


def _inputs_to_fqsns(inputs: Mapping[str, Any], block_id: dict, warnings: list[str]) -> dict[str, str]:
    """inputsToFQSNs — with: 값 안의 ${{ secrets.Y }} 도 블록 기준 FQSN 으로."""
    out: dict[str, str] = {}
    for k, v in inputs.items():
        if not isinstance(v, str):                           # 글이 아니면 건너뜀(경고)
            warnings.append(f'Skipping input "{k}" with invalid value type: {_js_typeof(v)}. Expected string.')
            continue
        out[k] = _sec.render_template_data(v, secrets=_sec.extract_fqsn_map(v, [block_id]))
    return out


def _js_typeof(v: Any) -> str:
    """typeof."""
    if v is None:
        return "object"
    if v is MISSING:
        return "undefined"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return "object"


def _parse_yaml_or_markdown_rule(content: str, id_: dict, parse_fn) -> dict:
    """parseYamlOrMarkdownRule — YAML 실패 시 .yaml/.yml 파일이 아니면 마크다운 규칙으로 다시."""
    try:
        return parse_fn(content)
    except Exception:
        if id_["uriType"] == "file" and any(id_["fileUri"].endswith(ext) for ext in (".yaml", ".yml")):
            raise
        try:
            rule = markdown_to_rule(content, id_)            # 마크다운 규칙
        except Exception:
            raise
        return {"name": rule["name"], "version": "1.0.0", "rules": [rule]}


def _js_json(v: Any) -> str:
    """JSON.stringify."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _err_message(e: BaseException) -> str:
    """(err as Error).message."""
    return str(e)


def resolve_block(id_: dict, inputs: Optional[Mapping[str, Any]], registry: RegistryClient, warnings: list[str]) -> dict:
    """resolveBlock — 블록 원문에 inputs·secrets(FQSN) 채우기, 남은 inputs 가 있으면 오류, 블록 스키마로 검증."""
    raw = registry.get_content(id_)                          # 원문
    rendered_inputs = _inputs_to_fqsns(inputs or {}, id_, warnings)
    templated = _sec.render_template_data(raw, inputs=rendered_inputs, secrets=_sec.extract_fqsn_map(raw, [id_]))
    unresolved = [v for v in _sec.get_template_variables(templated) if v.startswith("inputs.")]
    if unresolved:                                           # 필수 입력 누락
        names = [v.replace("inputs.", "", 1) for v in unresolved]
        block_name = _sec.package_identifier_to_shorthand_slug(id_)
        raise ValueError(f'Missing required input(s) for block "{block_name}": {", ".join(names)}. '
                         f'Please provide these values in the "with" block.')
    return _parse_yaml_or_markdown_rule(templated, id_, parse_block)


def _inject_local_source_file(block_type: str, resolved: dict, source: Optional[str]) -> list:
    """injectLocalSourceFile — 주입 블록 항목에 sourceFile 붙이기(규칙 글은 {sourceFile, name, rule} 객체로)."""
    blocks = resolved.get(block_type) or []
    if source is None:
        return blocks
    if block_type == "rules":
        out = []
        for b in blocks:
            if isinstance(b, str):
                out.append({"sourceFile": source, "name": b, "rule": b})
            else:
                if isinstance(b, dict):
                    b["sourceFile"] = source
                out.append(b)
        return out
    return [{**b, "sourceFile": source} for b in blocks]


def unroll_blocks(assistant: dict, registry: RegistryClient, inject_blocks: list[dict], environ: Mapping[str, str],
                  warnings: list[str]) -> tuple[dict, list[dict]]:
    """unrollBlocks — uses 블록 풀기, 주입 블록 붙이기, 중복 제거. (결과, 비치명 오류들)."""
    errors: list[dict] = []
    unrolled: dict = {"name": assistant.get("name", MISSING), "version": assistant.get("version", MISSING),
                      "requestOptions": assistant.get("requestOptions", MISSING)}
    section_results = []                                     # (섹션, 블록들, 오류들)
    for section in ["models", "context", "data", "mcpServers", "prompts", "docs"]:
        items = assistant.get(section)
        if not items:                                        # if (!assistant[section]) — 없으면 건드리지 않음
            section_results.append((section, None, []))
            continue
        blocks, sec_errors = [], []
        for item in items:                                   # 항목마다(순서 유지)
            if isinstance(item, dict) and "uses" in item:    # uses 블록
                uses = item["uses"]
                try:
                    bid = _sec.decode_package_identifier(uses, environ)
                    block_yaml = resolve_block(bid, item.get("with"), registry, warnings)
                    lst = block_yaml.get(section)
                    block = lst[0] if isinstance(lst, list) and lst else None   # [section]?.[0]
                    blocks.append(merge_overrides(block, item.get("override") or {}) if block else None)
                except Exception as err:                     # 비치명: 항목은 null
                    msg = f"{_err_message(err)}.\n> {_js_json(uses)}"
                    warnings.append(f"Failed to unroll block {_js_json(uses)}: {_err_message(err)}")   # console.error
                    blocks.append(None)
                    sec_errors.append({"fatal": False, "message": msg})
            else:                                            # 직접 정의
                blocks.append(item)
        section_results.append((section, blocks, sec_errors))

    rules_result = None                                      # 규칙
    rule_errors: list[dict] = []
    if assistant.get("rules"):
        rules_result = []
        for rule in assistant["rules"]:
            if isinstance(rule, str) or not (isinstance(rule, dict) and "uses" in rule):
                rules_result.append(rule)
                continue
            try:
                block_yaml = resolve_block(_sec.decode_package_identifier(rule["uses"], environ), rule.get("with"), registry, warnings)
                lst = block_yaml.get("rules")
                rules_result.append(lst[0] if isinstance(lst, list) and lst and lst[0] else None)
            except Exception as err:
                warnings.append(f"Failed to unroll block {rule['uses']}: {_err_message(err)}")
                rules_result.append(None)
                rule_errors.append({"fatal": False, "message": f"{_err_message(err)}:\n{rule['uses']}"})

    injected = []                                            # 주입 블록(--rule 파일, --mcp, --model …)
    inj_errors: list[dict] = []
    for ib in inject_blocks or []:
        try:
            content = registry.get_content(ib)
            with_secrets = _sec.replace_inputs_with_secrets(content)
            with_fqsns = _sec.render_template_data(with_secrets, secrets=_sec.extract_fqsn_map(with_secrets, [ib]))
            resolved = _parse_yaml_or_markdown_rule(with_fqsns, ib, parse_config_yaml)
            bt = get_block_type(resolved)
            if bt and resolved:
                injected.append((bt, resolved, ib["fileUri"] if ib["uriType"] == "file" else None))
        except Exception as err:
            if ib["uriType"] == "file":
                msg = f"{_err_message(err)}.\n> {ib['fileUri']}"
            else:
                msg = f"{_err_message(err)}.\n> [object Object]"   # `${injectBlock.fullSlug}` — 객체라 이렇게 찍힘(cn 그대로)
            warnings.append(f"Failed to unroll block {_js_json(ib)}: {_err_message(err)}")
            inj_errors.append({"fatal": False, "message": msg})

    for _, _, se in section_results:                         # 오류 순서: 섹션 → 규칙 → 주입
        errors.extend(se)
    errors.extend(rule_errors)
    errors.extend(inj_errors)

    detector = BlockDuplicationDetector()                    # 중복 제거(섹션 순서대로)
    for section, blocks, _ in section_results:
        if blocks is not None:
            unrolled[section] = [b for b in blocks if not detector.is_duplicated(b, section)]
    if rules_result is not None:
        unrolled["rules"] = [r for r in rules_result if not detector.is_duplicated(r, "rules")]
    for bt, resolved, source in injected:                    # 주입 블록은 뒤에 붙임(같은 이름이 앞에 있으면 버림)
        unrolled.setdefault(bt, [])
        unrolled[bt].extend(b for b in _inject_local_source_file(bt, resolved, source) if not detector.is_duplicated(b, bt))
    return unrolled, errors


def _render_secrets_in_value(unrolled: Any, parent_id: dict, client: _sec.LocalPlatformClient) -> Any:
    """unrollAssistantFromContent 의 두 단계: ① ${{ secrets.X }} → FQSN 템플릿 ② FQSN → 값.
    cn은 YAML 글 전체에 정규식을 걸고 다시 parse 하므로, 따옴표 없이 쓰이는 글은 바꾼 뒤 YAML 로 다시 읽어 형식(숫자·참거짓 등)을 맞춘다."""
    vars0 = _sec.collect_template_variables(unrolled)        # 문서 전체 변수
    fqsn_map = _sec.secret_to_fqsn_map([v.replace("secrets.", "", 1) for v in vars0 if v.startswith("secrets.")], [parent_id])
    flat1 = _sec.flatten_template_data(secrets=fqsn_map)     # ① 표
    seen: dict[str, None] = {}                               # ① 뒤의 변수 목록(② 조회 대상)

    def pass1(s: str, _k: bool) -> str:
        r = _sec.fill_template_variables(s, flat1) if s else s
        for v in _sec.get_template_variables(r):
            seen.setdefault(v, None)
        return r

    _sec.map_strings(unrolled, pass1)                        # 변수 모으기만(값은 아래에서 원문부터 다시 계산)
    vars1 = list(seen)
    values = _sec.extract_rendered_secrets_map(vars1, client)   # ② 값 찾기(Hub 는 실패 경고)
    flat2 = _sec.flatten_template_data(secrets=values)

    def render(s: str, is_key: bool) -> Any:
        if not s:                                            # 빈 글
            return s
        r = _sec.fill_template_variables(_sec.fill_template_variables(s, flat1), flat2)   # ①② 차례로
        if r == s or is_key:                                 # 바뀐 것 없음 / 키
            return r
        if yaml_stringify_is_plain(s):                       # 원래 글이 따옴표 없이 쓰였으면 다시 읽기
            try:
                return yaml_reparse_plain(r)
            except YamlError as e:                           # cn: parseAssistantUnrolled 에서 YAML 오류
                raise ConfigParseError(f"Failed to parse config: {e}") from e
        return r                                             # 따옴표·블록 글은 글 그대로(근사)

    return _sec.map_strings(unrolled, render)


def unroll_assistant_from_content(id_: dict, raw_yaml: str, registry: RegistryClient, *, inject_blocks: list[dict],
                                  client: _sec.LocalPlatformClient, environ: Mapping[str, str],
                                  warnings: list[str]) -> tuple[dict, list[dict]]:
    """unrollAssistantFromContent(renderSecrets: true) — (설정, 비치명 오류들). 치명 오류는 예외."""
    parsed = _parse_yaml_or_markdown_rule(raw_yaml, id_, parse_config_yaml)   # 검증된 설정
    unrolled, errors = unroll_blocks(parsed, registry, inject_blocks, environ, warnings)
    unrolled = strip_missing(unrolled)                       # YAML.stringify 는 undefined 키를 버림
    rendered = _render_secrets_in_value(unrolled, id_, client)   # 비밀값
    return parse_assistant_unrolled(rendered), errors        # 다시 검증


def merge_request_options(base: Optional[dict], glob: Optional[dict]) -> Optional[dict]:
    """mergeConfigYamlRequestOptions — headers 만 합치고 나머지는 base 가 이김."""
    if not base and not glob:
        return None
    if not base:
        return glob
    if not glob:
        return base
    headers = {**(glob.get("headers") or {}), **(base.get("headers") or {})}
    out = {**glob, **base}
    if headers:
        out["headers"] = headers
    else:
        out.pop("headers", None)                             # undefined
    return out


def merge_unrolled_assistants(current: dict, incoming: dict) -> dict:
    """mergeUnrolledAssistants — incoming 을 앞에 두고 섹션별 중복 제거(→ 주입 쪽이 같은 이름을 이김). 빈 섹션은 undefined."""
    out = dict(current)
    out["env"] = {**(current.get("env") or {}), **(incoming.get("env") or {})}   # {...undefined} = {}
    ro = merge_request_options(current.get("requestOptions"), incoming.get("requestOptions"))
    if ro is None:
        out.pop("requestOptions", None)
    else:
        out["requestOptions"] = ro
    detector = BlockDuplicationDetector()
    for bt in BLOCK_TYPES:
        allof = list(incoming.get(bt) or []) + list(current.get(bt) or [])
        dedup = [b for b in allof if b is not None and not detector.is_duplicated(b, bt)]   # if (block && …) — null 은 버림
        if dedup:
            out[bt] = dedup
        else:
            out.pop(bt, None)                                # undefined
    return out


# ───────────────────────── .continue/rules (systemMessage.ts loadMarkdownRulesWithMetadata) ─────────────────────────
def _readdir_recursive(d: str) -> list[str]:
    """fs.readdirSync(dir, {recursive: true}) — 너비 우선, 각 폴더는 OS 순서, 결과는 dir 기준 상대경로. 링크 폴더는 들어가지 않음."""
    out: list[str] = []
    queue = [d]                                              # 읽을 폴더
    sep = os.sep                                             # path.join 구분자
    i = 0
    while i < len(queue):
        cur = queue[i]
        i += 1
        with os.scandir(cur) as it:                          # 오류는 밖으로(전체 건너뜀)
            for entry in it:
                rel = os.path.relpath(entry.path, d) if cur != d else entry.name   # 상대경로
                out.append(rel.replace("/", sep) if sep != "/" else rel)
                if entry.is_dir(follow_symlinks=False):      # dirent.isDirectory()
                    queue.append(entry.path)
    return out


def _rule_name_from_path(file_path: str) -> str:
    """getRuleNameFromPath — 경로 마지막 두 조각, 끝 .md 제거."""
    segments = re.split(r"[/\\]", file_path)
    return re.sub(r"\.md$", "", "/".join(s for s in segments[-2:] if s))


def load_markdown_rules_with_metadata(cwd: str, continue_home: str) -> list[dict]:
    """<cwd>/.continue/rules, <전역>/rules 아래 *.md 중 항상 적용 규칙(alwaysApply: true 또는 globs·regex 없음, invokable 아님, 본문 있음)."""
    rules: list[dict] = []
    for d in (path_join(cwd, ".continue", "rules"), path_join(continue_home, "rules")):
        real_d = path_resolve(cwd, d)                        # fs 는 상대경로를 cwd 기준으로
        if not os.path.exists(real_d):
            continue
        try:
            files = _readdir_recursive(real_d)
        except OSError:
            continue
        for f in files:
            if not f.endswith(".md"):
                continue
            file_path = path_join(d, f)                      # path.join(dir, file)
            real = path_resolve(cwd, file_path)
            try:
                if not os.path.isfile(real):                 # statSync(…).isFile()(링크는 따라감)
                    continue
            except OSError:
                continue
            try:
                content = read_file_utf8(real)
                fm, markdown = parse_markdown_rule(content)
                if js_truthy(fm_get(fm, "invokable")):       # 호출형 규칙은 제외
                    continue
                aa = fm_get(fm, "alwaysApply")
                is_always = aa is True or (aa is MISSING and not js_truthy(fm_get(fm, "globs")) and not js_truthy(fm_get(fm, "regex")))
                if is_always and markdown.strip(JS_WS_CHARS):   # 본문 있음
                    name = fm_get(fm, "name")
                    rule = {"name": name if js_truthy(name) else _rule_name_from_path(f), "rule": markdown}
                    for k in ("description", "globs", "regex"):
                        v = fm_get(fm, k)
                        if v is not MISSING:
                            rule[k] = v
                    rule["alwaysApply"] = True
                    rule["sourceFile"] = file_path
                    rules.append(rule)
            except Exception:                                # 읽기·해석 실패 파일은 건너뜀
                continue
    return rules


# ───────────────────────── ConfigService.getAdditionalBlocksFromOptions ─────────────────────────
def _url_hostname(u: str) -> str:
    """new URL(u).hostname — 잘못된 URL 이면 TypeError("Invalid URL")."""
    parts = urllib.parse.urlsplit(u)
    if not parts.netloc:
        raise TypeError("Invalid URL")
    host = parts.netloc.rsplit("@", 1)[-1]                   # 사용자 정보 제거
    if host.startswith("["):                                 # IPv6 — JS 는 대괄호 유지
        return host[:host.index("]") + 1].lower()
    return host.split(":", 1)[0].lower()


def additional_blocks_from_options(*, models: list[str], mcp: list[str], rules: list[str], prompts: list[str],
                                   environ: Mapping[str, str], warnings: list[str]) -> tuple[list[dict], dict]:
    """getAdditionalBlocksFromOptions — (주입할 패키지 식별자들, 숨은 추가 설정). --agent 파일은 아직 없음."""
    injected: list[dict] = []
    additional = {"name": "hidden", "version": "1.0.0", "rules": [], "mcpServers": [], "prompts": []}
    for m in models:                                         # processModels
        injected.append(_sec.decode_package_identifier(m, environ))
    for m in mcp:                                            # processMcpServers
        try:
            if m.startswith("http://") or m.startswith("https://"):
                additional["mcpServers"].append({"name": _url_hostname(m), "url": m})
            else:
                injected.append(_sec.decode_package_identifier(m, environ))
        except Exception as e:
            warnings.append(f'Failed to add MCP server "{m}": {e}')
    for item in list(rules) + list(prompts):                 # processRulesAndPrompts — --prompt 글도 규칙으로 들어감(cn 그대로)
        if is_string_rule(item):
            additional["rules"].append(item)
        else:
            injected.append(_sec.decode_package_identifier(item, environ))
    return injected, additional


# ───────────────────────── 대화 모델(config.ts getLlmApi, ModelService) ─────────────────────────
def _is_chat(m: Any) -> bool:
    """model?.roles?.includes("chat") || (model && model.roles === undefined)."""
    if not isinstance(m, dict):
        return False
    roles = m.get("roles", MISSING)
    return roles is MISSING or (isinstance(roles, list) and "chat" in roles)


def select_chat_model(raw: dict, gdir: pathlib.Path) -> ModelConfig:
    """ModelService.doInitialize — 저장된 선택(globalContext cliSelectedModel)이 chat 모델 중에 있으면 그것, 아니면 첫 chat 모델."""
    models = raw.get("models") or []
    available = [m for m in models if _is_chat(m)]           # chat 역할이거나 역할 없음
    preferred = _paths.read_global_context(gdir).get("cliSelectedModel")   # 마지막 선택
    if preferred:
        for m in available:
            if (m.get("name") or m.get("model")) == preferred:
                return ModelConfig.from_raw(m)
    if not models:                                           # getLlmApi
        raise ConfigError("No models found in the configured assistant")
    for m in models:
        if _is_chat(m):
            return ModelConfig.from_raw(m)
    raise ConfigError("No models with the chat role found in the configured assistant")


# ───────────────────────── loadConfiguration ─────────────────────────
def _load_from_cli_flag(config_path: str, *, cwd: str, inject: list[dict], client: _sec.LocalPlatformClient,
                        environ: Mapping[str, str], warnings: list[str]) -> dict:
    """loadFromCliFlag — 파일이면 그 파일, 아니면 슬러그(로그인 없음 → 로컬 unroll → 지원 안 함 오류)."""
    if is_file_path(config_path):
        return _load_config_yaml(config_path, cwd=cwd, inject=inject, client=client, environ=environ, warnings=warnings)
    parts = config_path.split("/")                           # loadAssistantSlug
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ConfigError(f'Invalid assistant slug format. Expected "owner/package", got: {config_path}')
    raise ConfigError("Slug-based package resolution is not supported", hub=True)   # registry.getContent(slug)


def _load_config_yaml(file_path: str, *, cwd: str, inject: list[dict], client: _sec.LocalPlatformClient,
                      environ: Mapping[str, str], warnings: list[str]) -> dict:
    """loadConfigYaml → unrollAssistantWithConfig — 경로 글을 그대로 씀(~ 확장·정규화 없음)."""
    id_ = {"uriType": "file", "fileUri": file_path}
    registry = RegistryClient(path_dirname(file_path), cwd)  # rootPath = dirname(fileUri)
    try:
        raw = registry.get_content(id_)                      # 읽기(오류는 Node 글)
        config, errors = unroll_assistant_from_content(id_, raw, registry, inject_blocks=inject, client=client,
                                                       environ=environ, warnings=warnings)
    except ConfigError:
        raise
    except Exception as e:                                   # 치명 오류 = 그 글
        raise ConfigError(str(e)) from e
    if errors:                                               # 비치명 경고(cn: console.warn dim)
        if len(errors) > 1:
            w = ",".join(f"\n- {d['message']}" for d in errors)   # 배열을 템플릿에 넣어 "," 로 이어짐(cn 그대로)
        else:
            w = errors[0]["message"]
        warnings.append(f"Warning: {w}")
    config["__errors__"] = errors                            # 비치명 오류(load_config 가 꺼내 LoadedConfig.errors 로)
    return config


def load_config(config_path: Optional[str], *, cwd: pathlib.Path, global_dir: pathlib.Path,
                environ: Optional[Mapping[str, str]] = None, headless: bool = True,
                rules: Optional[list[str]] = None, mcp: Optional[list[str]] = None,
                models: Optional[list[str]] = None, prompts: Optional[list[str]] = None) -> LoadedConfig:
    """cn ConfigService.loadConfig + ModelService 모델 고르기.
    config_path = --config, rules/mcp/models/prompts = --rule/--mcp/--model/--prompt 값들, headless = -p 여부.
    environ 은 <cwd>/.env 를 반영한 사본으로 쓴다(dotenv.config 와 같음). 실패하면 ConfigError(cn 오류 글)."""
    cwd_s = os.fspath(cwd)                                   # 글로
    env = _env.effective_environ(cwd_s, environ)             # process.env(dotenv 적용)
    home = os.fspath(global_dir)                             # env.continueHome
    warnings: list[str] = []                                 # cn console 출력
    client = _sec.LocalPlatformClient(environ=env, continue_home=home, cwd=cwd_s, warnings=warnings)

    if not headless and config_path is not None:             # onboarding: --config 를 미리 한 번 읽어 봄
        try:
            _load_from_cli_flag(config_path, cwd=cwd_s, inject=[], environ=env, warnings=[],
                                client=_sec.LocalPlatformClient(environ=env, continue_home=home, cwd=cwd_s))
        except ConfigError as e:                             # `${errorMessage}` 는 Error 객체 → "Error: 글"
            raise ConfigError(f'Failed to load config from "{config_path}": Error: {e}', hub=e.hub) from e

    injected, additional = additional_blocks_from_options(models=models or [], mcp=mcp or [], rules=rules or [],
                                                          prompts=prompts or [], environ=env, warnings=warnings)
    if config_path:                                          # 1) --config
        source = {"type": "cli-flag", "path": config_path}
        loaded = _load_from_cli_flag(config_path, cwd=cwd_s, inject=injected, client=client, environ=env, warnings=warnings)
    elif os.path.exists(_paths.global_config_yaml(pathlib.Path(path_resolve(cwd_s, home)))):   # 2) <전역>/config.yaml
        source = {"type": "local-config-yaml"}
        loaded = _load_config_yaml(path_join(home, "config.yaml"), cwd=cwd_s, inject=injected, client=client,
                                   environ=env, warnings=warnings)
    else:                                                    # 3) Hub 기본 설정 — 폐쇄망: cn이 받는 오류 그대로
        raise ConfigError(_sec.HUB_FETCH_FAILED, hub=True)

    unroll_errors = loaded.pop("__errors__", [])             # 비치명 오류
    merged = merge_unrolled_assistants(loaded, additional)   # 주입 설정 합치기
    md_rules = load_markdown_rules_with_metadata(cwd_s, home)   # .continue/rules
    if md_rules:
        existing = {(r if isinstance(r, str) else (r or {}).get("rule")) for r in (merged.get("rules") or [])}
        new = [r for r in md_rules if r["rule"] not in existing]
        merged["rules"] = list(merged.get("rules") or []) + new

    has_chat = any(isinstance(m, dict) and (not m.get("roles") or "chat" in m["roles"]) for m in (merged.get("models") or []))
    if not has_chat:                                         # addDefaultChatModelIfNone — 기본 모델 슬러그는 로컬에서 못 읽음
        warnings.append('Failed to unroll block {"uriType":"slug","fullSlug":{"ownerSlug":"anthropic","packageSlug":"claude-sonnet-4-6",'
                        '"versionSlug":"1.0.0"}}: Slug-based package resolution is not supported')
        if headless:
            raise ConfigError("No model specified in headless mode (and failed to load default model)", hub=True)

    model_list = [ModelConfig.from_raw(m) for m in (merged.get("models") or []) if isinstance(m, dict)]
    chat_model, chat_err = None, None
    try:
        chat_model = select_chat_model(merged, pathlib.Path(path_resolve(cwd_s, home)))
    except ConfigError as e:
        chat_err = e.message
    return LoadedConfig(
        raw=merged, models=model_list, chat_model=chat_model, chat_model_error=chat_err,
        mcp_servers=list(merged.get("mcpServers") or []), rules=list(merged.get("rules") or []),
        prompts=list(merged.get("prompts") or []), context=list(merged.get("context") or []),
        docs=list(merged.get("docs") or []), data=list(merged.get("data") or []),
        name=merged.get("name"), version=merged.get("version"), env=merged.get("env") or {},
        request_options=merged.get("requestOptions"), warnings=warnings, errors=unroll_errors, source=source,
        config_path=config_path,
    )
