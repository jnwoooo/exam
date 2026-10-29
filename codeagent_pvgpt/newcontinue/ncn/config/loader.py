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
import urllib.parse                                          # new URL(...).hostname — URL 해석용
import urllib.request                                        # file:// URL → 경로
from dataclasses import dataclass, field                     # 결과 형식
from typing import Any, Mapping, Optional                    # 타입

from ncn.config import env as _env                           # dotenv·환경변수
from ncn.config import paths as _paths                       # 전역 폴더
from ncn.config import secrets as _sec                       # 템플릿·비밀값
from ncn.config.schema import (MISSING, ConfigParseError, YamlError, fm_get, js_truthy, markdown_to_rule,   # 검증·YAML
                               parse_assistant_unrolled, parse_block, parse_config_yaml, parse_markdown_rule,  # (import 이어짐) 검증 함수들
                               strip_missing, yaml_reparse_plain, yaml_stringify_is_plain)  # (import 이어짐)
from ncn.tools.formatters import (JS_WS_CHARS, NodeFsError, path_dirname, path_is_absolute, path_join,   # Node path·fs 흉내
                                  path_resolve, read_file_utf8)  # (import 이어짐) 경로 도구


class ConfigError(Exception):                               # 설정 읽기 실패 예외
    """설정 읽기 실패 — str(e) 는 cn이 내는 오류 글과 같다(헤드리스면 {"status":"error","message":…} 로 나가는 글).
    hub=True 면 cn이 Hub 호출을 하다 실패하는 경우(폐쇄망 안내를 덧붙이고 싶을 때 화면 쪽이 참고)."""

    def __init__(self, message: str, *, hub: bool = False):  # 메시지와 Hub 실패 여부를 받는 생성자
        super().__init__(message)                            # str(e) == message — 메시지를 예외 문구로
        self.message = message                               # 글
        self.hub = hub                                       # Hub 관련 실패인가


# ───────────────────────── 결과 형식 ─────────────────────────
@dataclass                                                  # 결과 형식을 데이터 클래스로
class ModelConfig:                                          # 모델 하나의 설정(cn ModelConfig)
    """모델 하나(cn ModelConfig). raw 는 cn 모양(camelCase) 그대로 — 요청 본문·토큰 세기는 raw 를 봐도 된다."""
    name: str                                                # 표시 이름
    provider: str                                            # 제공자(openai 등)
    model: str                                               # 모델 id(토큰 배수 판정에 쓰임)
    api_base: Optional[str]                                  # API 주소(apiBase)
    api_key: Optional[str]                                   # apiKey(못 찾은 비밀이면 "${{ secrets.//X }}" 글 그대로)
    roles: Optional[list[str]]                               # roles(없으면 None = chat 으로 취급)
    capabilities: Optional[list[str]]                        # 모델 기능 목록(capabilities)
    default_completion_options: dict = field(default_factory=dict)   # defaultCompletionOptions(contextLength·maxTokens 등)
    request_options: dict = field(default_factory=dict)      # requestOptions(headers·timeout 등)
    chat_options: dict = field(default_factory=dict)         # chatOptions(toolOverrides·baseSystemMessage 등)
    raw: dict = field(default_factory=dict)                  # 원본 dict

    @property                                               # 속성처럼 읽는 메서드
    def env(self) -> dict:                                  # model.env 접근자
        """model.env."""
        return self.raw.get("env") or {}                    # env 가 없으면 빈 사전

    @classmethod                                            # 클래스 메서드
    def from_raw(cls, raw: dict) -> "ModelConfig":          # cn 모양 dict 에서 ModelConfig 만들기
        """cn 모양 dict → ModelConfig."""
        return cls(                                         # 필드를 하나씩 옮겨 생성
            name=raw.get("name"), provider=raw.get("provider"), model=raw.get("model"),  # 이름·제공자·모델 id
            api_base=raw.get("apiBase"), api_key=raw.get("apiKey"),  # API 주소·키
            roles=raw.get("roles"), capabilities=raw.get("capabilities"),  # 역할·기능 목록
            default_completion_options=raw.get("defaultCompletionOptions") or {},  # 기본 완료 옵션
            request_options=raw.get("requestOptions") or {},  # 요청 옵션
            chat_options=raw.get("chatOptions") or {},      # 채팅 옵션
            raw=raw,                                        # 원본 dict 보관
        )


@dataclass                                                  # 데이터 클래스
class LoadedConfig:                                         # 읽은 설정 전체
    """읽은 설정(cn ConfigService state.config + 고른 모델)."""
    raw: dict                                                # AssistantUnrolled(cn 모양). models 등에 실패 블록 자리 None 이 남을 수 있음
    models: list[ModelConfig]                                # None 아닌 모델들
    chat_model: Optional[ModelConfig]                        # 대화 모델(ModelService 규칙). 못 고르면 None
    chat_model_error: Optional[str]                          # 못 고른 이유(cn 오류 글) — 헤드리스면 이 글로 종료
    mcp_servers: list                                        # mcpServers(cn 그대로 — 실패 블록은 None)
    rules: list                                              # rules(글 또는 {name, rule, …}) — 주입 규칙·.continue/rules 포함
    prompts: list                                            # 프롬프트 목록(prompts)
    context: list                                            # 컨텍스트 목록(context)
    docs: list                                               # 문서 목록(docs)
    data: list                                               # 데이터 목록(data)
    name: Optional[str]                                      # 설정 이름
    version: Optional[str]                                   # 설정 버전
    env: dict                                                # 최상위 env
    request_options: Optional[dict]                          # 최상위 requestOptions
    warnings: list[str] = field(default_factory=list)        # cn이 console.warn/error 로 찍던 글(헤드리스에선 안 보임)
    errors: list[dict] = field(default_factory=list)         # unroll 비치명 오류({fatal, message})
    source: dict = field(default_factory=dict)               # 원천 {"type": "cli-flag"|"local-config-yaml", "path"?}
    config_path: Optional[str] = None                        # --config 값

    @property                                               # 속성처럼 읽는 메서드
    def rule_texts(self) -> list[str]:                      # 규칙 글만 뽑은 목록
        """systemMessage.ts getConfigYamlRules — 글이면 그대로, 객체면 .rule, 빈 것은 뺌."""
        out = []                                            # 결과 목록
        for r in self.rules or []:                          # 규칙을 하나씩
            t = r if isinstance(r, str) else (r.get("rule") if isinstance(r, dict) else None)  # 글이면 그대로, 사전이면 rule 필드
            if js_truthy(t):                                # 비어 있지 않으면
                out.append(t)                               # 목록에 추가
        return out                                          # 규칙 글 목록 반환


# ───────────────────────── 경로 판정(configLoader.ts:511-525, hubLoader.ts isStringRule) ─────────────────────────
def is_file_path(config_path: str) -> bool:                 # 설정 경로가 파일 경로인지 판정
    """isFilePath — ".", "/", "~" 시작, Windows 드라이브·UNC, 또는 .yaml/.yml/.json 이 들어 있으면 파일."""
    return (config_path.startswith(".") or config_path.startswith("/") or config_path.startswith("~")  # ., /, ~ 로 시작하거나
            or re.match(r"^[A-Za-z]:[/\\]", config_path) is not None  # Windows 드라이브 경로이거나
            or config_path.startswith("\\\\")               # UNC 경로이거나
            or ".yaml" in config_path or ".yml" in config_path or ".json" in config_path)  # .yaml/.yml/.json 이 들어 있으면 파일


HUB_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")   # hubLoader.ts HUB_SLUG_PATTERN — 허브 슬러그(owner/pkg) 정규식


def is_string_rule(rule: str) -> bool:                      # 글 규칙인지 판정
    """isStringRule — 공백·줄바꿈이 있으면 글 규칙, 경로·슬러그 모양이면 아님."""
    if " " in rule or "\n" in rule:                         # 공백·줄바꿈이 있으면 글 규칙
        return True                                         # 글 규칙
    if any(rule.startswith(p) for p in ["file:/", ".", "/", "~"]) or "\\" in rule:  # 경로 접두사가 있으면
        return False                                        # 글 규칙 아님
    if HUB_SLUG_PATTERN.match(rule):                        # 허브 슬러그 모양이면
        return False                                        # 글 규칙 아님
    return True                                             # 그 밖은 글 규칙


# ───────────────────────── RegistryClient(config-yaml/src/registryClient.ts) ─────────────────────────
class RegistryClient:                                       # 파일 블록을 읽는 클라이언트
    """파일 블록 읽기. 상대경로는 cwd 기준이 먼저, 없으면 rootPath(설정 파일 폴더). 슬러그는 지원 안 함."""

    def __init__(self, root_path: Optional[str], cwd: str):  # 설정 파일 폴더와 작업 폴더를 받음
        self.root_path = root_path                           # dirname(설정 파일) — 글 그대로
        self.cwd = cwd                                       # process.cwd() — 작업 폴더

    def _read(self, display: str) -> str:                   # 파일 읽기 함수
        """fs.readFileSync(display, "utf8") — 상대경로는 cwd 기준. 오류 글의 경로는 display 그대로(Node 와 같게)."""
        actual = display if path_is_absolute(display) else path_resolve(self.cwd, display)  # 상대경로는 작업 폴더 기준 절대경로로
        try:                                                # 읽기 오류의 경로 표기를 Node 식으로 바꿈
            return read_file_utf8(actual)                   # 파일 내용 반환
        except NodeFsError as e:                             # 경로 표기를 Node 처럼 넘긴 글로
            raise NodeFsError(str(e).replace(f"'{actual}'", f"'{display}'"), e.code) from e  # 표기를 원래 경로로 바꿔 다시 올림

    def get_content(self, id_: dict) -> str:                # 블록 내용 가져오기
        """getContent — 파일이면 읽기, 슬러그면 오류."""
        if id_["uriType"] == "file" and id_.get("content") is not None:   # 미리 읽은 내용
            return id_["content"]                           # 미리 읽은 내용을 그대로
        if id_["uriType"] == "file":                        # 파일이면
            return self._from_file_path(id_["fileUri"])     # 경로에서 읽음
        if id_["uriType"] == "slug":                         # cn: 허브 슬러그 해석 제거됨
            raise ValueError("Slug-based package resolution is not supported")  # 슬러그는 지원하지 않음
        raise ValueError(f"Unknown package identifier type: {id_['uriType']}")  # 알 수 없는 식별자 종류

    def _from_file_path(self, filepath: str) -> str:        # 파일 경로에서 읽기
        """getContentFromFilePath."""
        if filepath.startswith("file://"):                   # new URL(filepath) — file:// URL 이면
            u = urllib.parse.urlsplit(filepath)             # URL 을 분해
            return self._read(urllib.request.url2pathname(u.path))  # 경로로 바꿔 읽음
        if path_is_absolute(filepath):                       # 절대경로
            return self._read(filepath)                     # 절대경로를 그대로 읽음
        resolved = path_resolve(self.cwd, filepath)          # cwd 기준 먼저
        if os.path.exists(resolved):                        # 있으면
            return self._read(resolved)                     # 그 경로를 읽음
        if self.root_path:                                   # 설정 파일 폴더 기준
            return self._read(path_join(self.root_path, filepath))  # 설정 파일 폴더 기준으로 읽음
        raise ValueError("No rootPath provided for relative file path")  # 기준 폴더도 없으면 오류


# ───────────────────────── 블록 처리 도우미(unroll.ts) ─────────────────────────
BLOCK_TYPES = ["models", "context", "data", "mcpServers", "rules", "prompts", "docs"]   # getBlockType.ts BLOCK_TYPES — 블록 종류 목록


def get_block_type(block: dict) -> Optional[str]:           # 블록의 종류를 알아내는 함수
    """getBlockType — 검사 순서 context, models, docs, mcpServers, data, rules, prompts."""
    for k in ("context", "models", "docs", "mcpServers", "data", "rules", "prompts"):  # 검사 순서대로
        v = block.get(k)                                    # 해당 섹션 값
        if isinstance(v, list) and len(v) > 0:               # 비어 있지 않은 목록인지(?.length)
            return k                                        # 비어 있지 않은 목록이면 그 종류
    return None                                             # 어느 것도 아니면 None


class BlockDuplicationDetector:                             # 중복 블록 검출기
    """blockDuplicationDetector.ts — 섹션별로 이름이 이미 나왔으면 중복(뒤 항목을 버림)."""

    def __init__(self):                                     # 섹션별 기록을 준비
        self.records: dict[str, set] = {t: set() for t in BLOCK_TYPES}   # 섹션 → 본 이름들

    def _check(self, identifier: Any, block_type: str) -> bool:  # 이미 나온 이름인지 검사하고 기록
        key = json.dumps(identifier, ensure_ascii=False) if not isinstance(identifier, str) else identifier   # Set 키(undefined 포함)
        if identifier is MISSING or identifier is None:     # 식별자가 없으면 repr 로 키 구성
            key = repr(identifier)                          # 키 문자열
        if key in self.records[block_type]:                 # 이미 있으면
            return True                                     # 중복
        self.records[block_type].add(key)                   # 처음 보는 이름이면 기록
        return False                                        # 중복 아님

    def is_duplicated(self, block: Any, block_type: str) -> bool:  # 블록이 중복인지 판정
        """isDuplicated — null/undefined 는 검사 안 함(중복 아님)."""
        if block is None or block is MISSING:               # 블록이 없으면
            return False                                    # 중복 아님
        if block_type == "rules":                            # 규칙: 글 자체 또는 name
            return self._check(block if isinstance(block, str) else block.get("name", MISSING), "rules")  # 이름(또는 글 자체)으로 검사
        if block_type == "context":                          # 컨텍스트: name ?? params.title ?? provider
            key = block.get("name")                         # 이름
            if key is None:                                 # 이름이 없으면
                params = block.get("params")                # 컨텍스트 매개변수
                key = params.get("title") if isinstance(params, dict) else None  # params.title 사용
            if key is None:                                 # 그래도 없으면
                key = block.get("provider", MISSING)        # provider 사용
            return self._check(key, "context")              # 키로 검사
        return self._check(block.get("name", MISSING), block_type)  # 그 밖은 이름으로 검사


def merge_overrides(block: dict, overrides: dict) -> dict:  # 오버라이드 병합 함수
    """mergeOverrides — 얕은 키 덮어쓰기(블록 객체를 직접 바꿈)."""
    for k, v in overrides.items():                          # 오버라이드 키를 하나씩
        block[k] = v                                        # 블록에 덮어씀
    return block                                            # 병합된 블록 반환


def _inputs_to_fqsns(inputs: Mapping[str, Any], block_id: dict, warnings: list[str]) -> dict[str, str]:  # with 입력을 FQSN 기준으로 바꾸는 함수
    """inputsToFQSNs — with: 값 안의 ${{ secrets.Y }} 도 블록 기준 FQSN 으로."""
    out: dict[str, str] = {}                                # 결과 표
    for k, v in inputs.items():                             # 입력을 하나씩
        if not isinstance(v, str):                           # 글이 아니면 건너뜀(경고)
            warnings.append(f'Skipping input "{k}" with invalid value type: {_js_typeof(v)}. Expected string.')  # 글이 아닌 값은 경고
            continue                                        # 건너뜀
        out[k] = _sec.render_template_data(v, secrets=_sec.extract_fqsn_map(v, [block_id]))  # 값 안의 secrets 를 블록 기준 FQSN 으로 바꿔 저장
    return out                                              # 표 반환


def _js_typeof(v: Any) -> str:                              # JS typeof 흉내 함수
    """typeof."""
    if v is None:                                           # None 은 object
        return "object"                                     # null 은 object
    if v is MISSING:                                        # MISSING 은 undefined
        return "undefined"                                  # 없는 값
    if isinstance(v, bool):                                 # 불리언
        return "boolean"                                    # 참거짓
    if isinstance(v, (int, float)):                         # 숫자
        return "number"                                     # 숫자
    if isinstance(v, str):                                  # 글
        return "string"                                     # 글
    return "object"                                         # 그 밖은 "object"


def _parse_yaml_or_markdown_rule(content: str, id_: dict, parse_fn) -> dict:  # YAML 실패 시 마크다운 규칙으로 읽는 함수
    """parseYamlOrMarkdownRule — YAML 실패 시 .yaml/.yml 파일이 아니면 마크다운 규칙으로 다시."""
    try:                                                    # 먼저 YAML 로 파싱
        return parse_fn(content)                            # 파싱 결과 반환
    except Exception:                                       # YAML 파싱 실패
        if id_["uriType"] == "file" and any(id_["fileUri"].endswith(ext) for ext in (".yaml", ".yml")):  # yaml/yml 파일이면 그대로 오류
            raise                                           # 오류를 다시 올림
        try:                                                # 마크다운 규칙으로 시도
            rule = markdown_to_rule(content, id_)            # 마크다운 규칙
        except Exception:                                   # 이것도 실패하면
            raise                                           # 오류를 그대로 올림
        return {"name": rule["name"], "version": "1.0.0", "rules": [rule]}  # 마크다운 규칙을 설정 모양으로 감싸 반환


def _js_json(v: Any) -> str:                                # JSON.stringify 흉내 함수
    """JSON.stringify."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # 공백 없는 JSON 글


def _err_message(e: BaseException) -> str:                  # 오류 메시지 뽑는 함수
    """(err as Error).message."""
    return str(e)                                           # 예외 글


def resolve_block(id_: dict, inputs: Optional[Mapping[str, Any]], registry: RegistryClient, warnings: list[str]) -> dict:  # 블록 하나를 읽어 검증하는 함수
    """resolveBlock — 블록 원문에 inputs·secrets(FQSN) 채우기, 남은 inputs 가 있으면 오류, 블록 스키마로 검증."""
    raw = registry.get_content(id_)                          # 원문
    rendered_inputs = _inputs_to_fqsns(inputs or {}, id_, warnings)  # 입력 값을 FQSN 기준으로 변환
    templated = _sec.render_template_data(raw, inputs=rendered_inputs, secrets=_sec.extract_fqsn_map(raw, [id_]))  # 원문에 inputs·secrets 채움
    unresolved = [v for v in _sec.get_template_variables(templated) if v.startswith("inputs.")]  # 채우지 못한 inputs 변수
    if unresolved:                                           # 필수 입력 누락
        names = [v.replace("inputs.", "", 1) for v in unresolved]  # 필요한 입력 이름들
        block_name = _sec.package_identifier_to_shorthand_slug(id_)  # 블록 이름
        raise ValueError(f'Missing required input(s) for block "{block_name}": {", ".join(names)}. '  # 필수 입력 누락 오류
                         f'Please provide these values in the "with" block.')  # (문자열 이어짐)
    return _parse_yaml_or_markdown_rule(templated, id_, parse_block)  # 블록 스키마로 검증해 반환


def _inject_local_source_file(block_type: str, resolved: dict, source: Optional[str]) -> list:  # 주입 블록에 sourceFile 붙이는 함수
    """injectLocalSourceFile — 주입 블록 항목에 sourceFile 붙이기(규칙 글은 {sourceFile, name, rule} 객체로)."""
    blocks = resolved.get(block_type) or []                 # 해당 종류의 블록들
    if source is None:                                      # 출처가 없으면
        return blocks                                       # 그대로 반환
    if block_type == "rules":                               # 규칙이면
        out = []                                            # 결과 목록
        for b in blocks:                                    # 규칙마다
            if isinstance(b, str):                          # 글이면
                out.append({"sourceFile": source, "name": b, "rule": b})  # {sourceFile, name, rule} 객체로
            else:                                          # 글이 아닌 규칙
                if isinstance(b, dict):                     # 사전이면
                    b["sourceFile"] = source                # sourceFile 붙임
                out.append(b)                               # 결과에 추가
        return out                                          # 결과 반환
    return [{**b, "sourceFile": source} for b in blocks]    # 그 밖의 종류는 sourceFile 만 더함


def unroll_blocks(assistant: dict, registry: RegistryClient, inject_blocks: list[dict], environ: Mapping[str, str],  # uses 블록 풀기·주입 블록 병합 함수
                  warnings: list[str]) -> tuple[dict, list[dict]]:  # (인자 이어짐)
    """unrollBlocks — uses 블록 풀기, 주입 블록 붙이기, 중복 제거. (결과, 비치명 오류들)."""
    errors: list[dict] = []                                 # 비치명 오류 모음
    unrolled: dict = {"name": assistant.get("name", MISSING), "version": assistant.get("version", MISSING),  # 결과 뼈대(이름·버전·요청 옵션)
                      "requestOptions": assistant.get("requestOptions", MISSING)}  # (사전 이어짐)
    section_results = []                                     # (섹션, 블록들, 오류들)
    for section in ["models", "context", "data", "mcpServers", "prompts", "docs"]:  # 섹션별로 uses 블록 풀기
        items = assistant.get(section)                      # 섹션 항목들
        if not items:                                        # if (!assistant[section]) — 없으면 건드리지 않음
            section_results.append((section, None, []))     # 항목이 없으면 건드리지 않음
            continue                                        # 다음 섹션으로
        blocks, sec_errors = [], []                         # 블록·오류 목록
        for item in items:                                   # 항목마다(순서 유지)
            if isinstance(item, dict) and "uses" in item:    # uses 블록
                uses = item["uses"]                         # uses 값
                try:                                        # 블록 풀기 시도
                    bid = _sec.decode_package_identifier(uses, environ)  # 식별자 해석
                    block_yaml = resolve_block(bid, item.get("with"), registry, warnings)  # 블록 읽기
                    lst = block_yaml.get(section)           # 해당 섹션 목록
                    block = lst[0] if isinstance(lst, list) and lst else None   # [section]?.[0] — 첫 항목
                    blocks.append(merge_overrides(block, item.get("override") or {}) if block else None)  # override 병합(없으면 None)
                except Exception as err:                     # 비치명: 항목은 null
                    msg = f"{_err_message(err)}.\n> {_js_json(uses)}"  # 오류 메시지 구성
                    warnings.append(f"Failed to unroll block {_js_json(uses)}: {_err_message(err)}")   # console.error — 경고 기록
                    blocks.append(None)                     # 실패 항목은 null
                    sec_errors.append({"fatal": False, "message": msg})  # 비치명 오류 기록
            else:                                            # 직접 정의
                blocks.append(item)                         # 직접 정의 항목은 그대로
        section_results.append((section, blocks, sec_errors))  # 섹션 결과 저장

    rules_result = None                                      # 규칙
    rule_errors: list[dict] = []                            # 규칙 오류 모음
    if assistant.get("rules"):                              # 규칙이 있으면
        rules_result = []                                   # 규칙 결과 목록
        for rule in assistant["rules"]:                     # 규칙마다
            if isinstance(rule, str) or not (isinstance(rule, dict) and "uses" in rule):  # 글이거나 uses 가 없으면
                rules_result.append(rule)                   # 그대로 추가
                continue                                    # 다음 규칙으로
            try:                                            # uses 규칙 풀기 시도
                block_yaml = resolve_block(_sec.decode_package_identifier(rule["uses"], environ), rule.get("with"), registry, warnings)  # 블록 읽기
                lst = block_yaml.get("rules")               # rules 목록
                rules_result.append(lst[0] if isinstance(lst, list) and lst and lst[0] else None)  # 첫 규칙
            except Exception as err:                        # 실패하면
                warnings.append(f"Failed to unroll block {rule['uses']}: {_err_message(err)}")  # 경고 기록
                rules_result.append(None)                   # 실패 항목은 null
                rule_errors.append({"fatal": False, "message": f"{_err_message(err)}:\n{rule['uses']}"})  # 비치명 오류 기록

    injected = []                                            # 주입 블록(--rule 파일, --mcp, --model …)
    inj_errors: list[dict] = []                             # 주입 오류 모음
    for ib in inject_blocks or []:                          # 주입 블록마다
        try:                                                # 풀기 시도
            content = registry.get_content(ib)              # 블록 원문 읽기
            with_secrets = _sec.replace_inputs_with_secrets(content)  # inputs 를 secrets 로 바꿈
            with_fqsns = _sec.render_template_data(with_secrets, secrets=_sec.extract_fqsn_map(with_secrets, [ib]))  # secrets 를 FQSN 기준으로 채움
            resolved = _parse_yaml_or_markdown_rule(with_fqsns, ib, parse_config_yaml)  # YAML(또는 마크다운 규칙)로 해석
            bt = get_block_type(resolved)                   # 블록 종류 판정
            if bt and resolved:                             # 내용이 있으면
                injected.append((bt, resolved, ib["fileUri"] if ib["uriType"] == "file" else None))  # (종류, 내용, 출처 파일) 로 기록
        except Exception as err:                            # 실패하면
            if ib["uriType"] == "file":                     # 파일이면
                msg = f"{_err_message(err)}.\n> {ib['fileUri']}"  # 파일 경로를 포함한 오류 글
            else:                                           # 슬러그면
                msg = f"{_err_message(err)}.\n> [object Object]"   # `${injectBlock.fullSlug}` — 객체라 이렇게 찍힘(cn 그대로)
            warnings.append(f"Failed to unroll block {_js_json(ib)}: {_err_message(err)}")  # 경고 기록
            inj_errors.append({"fatal": False, "message": msg})  # 비치명 오류 기록

    for _, _, se in section_results:                         # 오류 순서: 섹션 → 규칙 → 주입
        errors.extend(se)                                   # 섹션 오류부터 모음
    errors.extend(rule_errors)                              # 규칙 오류 추가
    errors.extend(inj_errors)                               # 주입 오류 추가

    detector = BlockDuplicationDetector()                    # 중복 제거(섹션 순서대로)
    for section, blocks, _ in section_results:              # 섹션별로 중복 제거해 결과에 넣음
        if blocks is not None:                              # 항목이 있는 섹션만
            unrolled[section] = [b for b in blocks if not detector.is_duplicated(b, section)]  # 중복이 아닌 블록만 남김
    if rules_result is not None:                            # 규칙 결과가 있으면
        unrolled["rules"] = [r for r in rules_result if not detector.is_duplicated(r, "rules")]  # 중복이 아닌 규칙만 남김
    for bt, resolved, source in injected:                    # 주입 블록은 뒤에 붙임(같은 이름이 앞에 있으면 버림)
        unrolled.setdefault(bt, [])                         # 해당 섹션이 없으면 빈 목록 준비
        unrolled[bt].extend(b for b in _inject_local_source_file(bt, resolved, source) if not detector.is_duplicated(b, bt))  # 주입 블록을 중복 검사하며 뒤에 붙임
    return unrolled, errors                                 # (풀린 설정, 비치명 오류들) 반환


def _render_secrets_in_value(unrolled: Any, parent_id: dict, client: _sec.LocalPlatformClient) -> Any:  # 비밀값을 값 단위로 채우는 함수
    """unrollAssistantFromContent 의 두 단계: ① ${{ secrets.X }} → FQSN 템플릿 ② FQSN → 값.
    cn은 YAML 글 전체에 정규식을 걸고 다시 parse 하므로, 따옴표 없이 쓰이는 글은 바꾼 뒤 YAML 로 다시 읽어 형식(숫자·참거짓 등)을 맞춘다."""
    vars0 = _sec.collect_template_variables(unrolled)        # 문서 전체 변수
    fqsn_map = _sec.secret_to_fqsn_map([v.replace("secrets.", "", 1) for v in vars0 if v.startswith("secrets.")], [parent_id])  # 비밀 이름 → FQSN 템플릿 표
    flat1 = _sec.flatten_template_data(secrets=fqsn_map)     # ① 표
    seen: dict[str, None] = {}                               # ① 뒤의 변수 목록(② 조회 대상)

    def pass1(s: str, _k: bool) -> str:                     # 1단계: secrets 를 FQSN 템플릿으로 바꾸는 함수
        r = _sec.fill_template_variables(s, flat1) if s else s  # 글이 있으면 템플릿 채움
        for v in _sec.get_template_variables(r):            # 바뀐 뒤 남은 변수를 하나씩
            seen.setdefault(v, None)                        # 2단계 조회 대상으로 기록
        return r                                            # 바뀐 글 반환

    _sec.map_strings(unrolled, pass1)                        # 변수 모으기만(값은 아래에서 원문부터 다시 계산)
    vars1 = list(seen)                                      # 2단계 조회 대상 변수 목록
    values = _sec.extract_rendered_secrets_map(vars1, client)   # ② 값 찾기(Hub 는 실패 경고)
    flat2 = _sec.flatten_template_data(secrets=values)      # 2단계 표(FQSN → 값)

    def render(s: str, is_key: bool) -> Any:                # 2단계: 실제 값으로 바꾸는 함수
        if not s:                                            # 빈 글
            return s                                        # 빈 글은 그대로
        r = _sec.fill_template_variables(_sec.fill_template_variables(s, flat1), flat2)   # ①② 차례로
        if r == s or is_key:                                 # 바뀐 것 없음 / 키
            return r                                        # 바뀐 것이 없거나 키면 그대로 반환
        if yaml_stringify_is_plain(s):                       # 원래 글이 따옴표 없이 쓰였으면 다시 읽기
            try:                                            # YAML 로 다시 읽기 시도
                return yaml_reparse_plain(r)                # 숫자·참거짓 등 형식에 맞춰 반환
            except YamlError as e:                           # cn: parseAssistantUnrolled 에서 YAML 오류
                raise ConfigParseError(f"Failed to parse config: {e}") from e  # YAML 오류를 설정 파싱 오류로 바꿈
        return r                                             # 따옴표·블록 글은 글 그대로(근사)

    return _sec.map_strings(unrolled, render)               # 모든 글에 적용해 반환


def unroll_assistant_from_content(id_: dict, raw_yaml: str, registry: RegistryClient, *, inject_blocks: list[dict],  # 설정 원문을 풀어 검증된 설정으로 만드는 함수
                                  client: _sec.LocalPlatformClient, environ: Mapping[str, str],  # (인자 이어짐)
                                  warnings: list[str]) -> tuple[dict, list[dict]]:  # (인자 이어짐)
    """unrollAssistantFromContent(renderSecrets: true) — (설정, 비치명 오류들). 치명 오류는 예외."""
    parsed = _parse_yaml_or_markdown_rule(raw_yaml, id_, parse_config_yaml)   # 검증된 설정
    unrolled, errors = unroll_blocks(parsed, registry, inject_blocks, environ, warnings)  # uses 블록·주입 블록 풀기
    unrolled = strip_missing(unrolled)                       # YAML.stringify 는 undefined 키를 버림
    rendered = _render_secrets_in_value(unrolled, id_, client)   # 비밀값
    return parse_assistant_unrolled(rendered), errors        # 다시 검증


def merge_request_options(base: Optional[dict], glob: Optional[dict]) -> Optional[dict]:   # 요청 옵션 병합 함수
    """mergeConfigYamlRequestOptions — headers 만 합치고 나머지는 base 가 이김."""
    if not base and not glob:                               # 둘 다 없으면
        return None                                         # 합칠 것 없음
    if not base:                                            # base 가 없으면
        return glob                                         # 전역 옵션
    if not glob:                                            # 전역 옵션이 없으면
        return base                                         # base 그대로
    headers = {**(glob.get("headers") or {}), **(base.get("headers") or {})}  # headers 합침(base 가 이김)
    out = {**glob, **base}                                  # 나머지는 base 가 이김
    if headers:                                             # headers 가 있으면
        out["headers"] = headers                            # 합친 headers 저장
    else:                                                   # 없으면
        out.pop("headers", None)                             # 비우면 키 자체를 없앰(undefined)
    return out                                              # 병합 결과 반환


def merge_unrolled_assistants(current: dict, incoming: dict) -> dict:  # 설정 두 개 병합 함수
    """mergeUnrolledAssistants — incoming 을 앞에 두고 섹션별 중복 제거(→ 주입 쪽이 같은 이름을 이김). 빈 섹션은 undefined."""
    out = dict(current)                                     # 현재 설정 복사
    out["env"] = {**(current.get("env") or {}), **(incoming.get("env") or {})}   # {...undefined} = {} — env 합침
    ro = merge_request_options(current.get("requestOptions"), incoming.get("requestOptions"))  # 요청 옵션 합침
    if ro is None:                                          # 결과가 없으면
        out.pop("requestOptions", None)                     # 키 제거
    else:                                                   # 있으면
        out["requestOptions"] = ro                          # 저장
    detector = BlockDuplicationDetector()                   # 중복 검출기
    for bt in BLOCK_TYPES:                                  # 블록 종류마다
        allof = list(incoming.get(bt) or []) + list(current.get(bt) or [])  # incoming 을 앞에 두고 합침
        dedup = [b for b in allof if b is not None and not detector.is_duplicated(b, bt)]   # if (block && …) — null 은 버림
        if dedup:                                           # 항목이 있으면
            out[bt] = dedup                                 # 저장
        else:                                               # 없으면
            out.pop(bt, None)                                # 비면 키 자체를 없앰(undefined)
    return out                                              # 병합 결과 반환


# ───────────────────────── .continue/rules (systemMessage.ts loadMarkdownRulesWithMetadata) ─────────────────────────
def _readdir_recursive(d: str) -> list[str]:                # 폴더를 재귀로 읽는 함수
    """fs.readdirSync(dir, {recursive: true}) — 너비 우선, 각 폴더는 OS 순서, 결과는 dir 기준 상대경로. 링크 폴더는 들어가지 않음."""
    out: list[str] = []                                     # 결과 목록
    queue = [d]                                              # 읽을 폴더
    sep = os.sep                                             # path.join 구분자
    i = 0                                                   # 큐 위치
    while i < len(queue):                                   # 큐가 빌 때까지
        cur = queue[i]                                      # 현재 폴더
        i += 1                                              # 다음 폴더로
        with os.scandir(cur) as it:                          # 오류는 밖으로(전체 건너뜀)
            for entry in it:                                # 폴더 항목마다
                rel = os.path.relpath(entry.path, d) if cur != d else entry.name   # 상대경로
                out.append(rel.replace("/", sep) if sep != "/" else rel)  # 상대경로 저장
                if entry.is_dir(follow_symlinks=False):      # dirent.isDirectory() — 폴더면(링크 제외)
                    queue.append(entry.path)                # 큐에 추가
    return out                                              # 상대경로 목록 반환


def _rule_name_from_path(file_path: str) -> str:            # 경로에서 규칙 이름 만드는 함수
    """getRuleNameFromPath — 경로 마지막 두 조각, 끝 .md 제거."""
    segments = re.split(r"[/\\]", file_path)                # 경로를 구분자로 나눔
    return re.sub(r"\.md$", "", "/".join(s for s in segments[-2:] if s))  # 마지막 두 조각을 "/" 로 이어 .md 제거


def load_markdown_rules_with_metadata(cwd: str, continue_home: str) -> list[dict]:  # 마크다운 규칙 읽는 함수
    """<cwd>/.continue/rules, <전역>/rules 아래 *.md 중 항상 적용 규칙(alwaysApply: true 또는 globs·regex 없음, invokable 아님, 본문 있음)."""
    rules: list[dict] = []                                  # 규칙 목록
    for d in (path_join(cwd, ".continue", "rules"), path_join(continue_home, "rules")):  # <cwd>/.continue/rules 와 <전역>/rules 순서로
        real_d = path_resolve(cwd, d)                        # fs 는 상대경로를 cwd 기준으로
        if not os.path.exists(real_d):                      # 폴더가 없으면
            continue                                        # 건너뜀
        try:                                                # 폴더 읽기 시도
            files = _readdir_recursive(real_d)              # 하위 파일 목록
        except OSError:                                     # 읽기 오류면
            continue                                        # 건너뜀
        for f in files:                                     # 파일마다
            if not f.endswith(".md"):                       # .md 가 아니면
                continue                                    # 건너뜀
            file_path = path_join(d, f)                      # 폴더와 파일 이름을 이음(path.join)
            real = path_resolve(cwd, file_path)             # 실제 경로
            try:                                            # 파일 여부 확인 시도
                if not os.path.isfile(real):                 # statSync(…).isFile()(링크는 따라감)
                    continue                                # 파일이 아니면 건너뜀
            except OSError:                                 # 확인 오류면
                continue                                    # 건너뜀
            try:                                            # 읽기·해석 시도
                content = read_file_utf8(real)              # 파일 내용
                fm, markdown = parse_markdown_rule(content)  # 머리말과 본문 분리
                if js_truthy(fm_get(fm, "invokable")):       # 호출형 규칙은 제외
                    continue                                # 건너뜀
                aa = fm_get(fm, "alwaysApply")              # alwaysApply 값
                is_always = aa is True or (aa is MISSING and not js_truthy(fm_get(fm, "globs")) and not js_truthy(fm_get(fm, "regex")))  # 항상 적용 규칙인지 판정
                if is_always and markdown.strip(JS_WS_CHARS):   # 본문 있음
                    name = fm_get(fm, "name")               # 이름
                    rule = {"name": name if js_truthy(name) else _rule_name_from_path(f), "rule": markdown}  # 규칙 사전(이름 없으면 경로에서 만듦)
                    for k in ("description", "globs", "regex"):  # 부가 필드를 하나씩
                        v = fm_get(fm, k)                   # 머리말 값
                        if v is not MISSING:                # 있으면
                            rule[k] = v                     # 규칙에 복사
                    rule["alwaysApply"] = True              # 항상 적용으로 표시
                    rule["sourceFile"] = file_path          # 출처 파일 기록
                    rules.append(rule)                      # 규칙 목록에 추가
            except Exception:                                # 읽기·해석 실패 파일은 건너뜀
                continue                                    # 건너뜀
    return rules                                            # 규칙 목록 반환


# ───────────────────────── ConfigService.getAdditionalBlocksFromOptions ─────────────────────────
def _url_hostname(u: str) -> str:                           # URL 호스트 이름 함수
    """new URL(u).hostname — 잘못된 URL 이면 TypeError("Invalid URL")."""
    parts = urllib.parse.urlsplit(u)                        # URL 분해
    if not parts.netloc:                                    # 호스트가 없으면
        raise TypeError("Invalid URL")                      # JS 와 같은 오류
    host = parts.netloc.rsplit("@", 1)[-1]                   # 사용자 정보 제거
    if host.startswith("["):                                 # IPv6 — JS 는 대괄호 유지
        return host[:host.index("]") + 1].lower()           # IPv6 는 대괄호 포함 소문자로
    return host.split(":", 1)[0].lower()                    # 포트를 떼고 소문자로


def additional_blocks_from_options(*, models: list[str], mcp: list[str], rules: list[str], prompts: list[str],  # --model·--mcp·--rule·--prompt 를 블록으로 바꾸는 함수
                                   environ: Mapping[str, str], warnings: list[str]) -> tuple[list[dict], dict]:  # (인자 이어짐)
    """getAdditionalBlocksFromOptions — (주입할 패키지 식별자들, 숨은 추가 설정). --agent 파일은 아직 없음."""
    injected: list[dict] = []                               # 주입할 식별자 목록
    additional = {"name": "hidden", "version": "1.0.0", "rules": [], "mcpServers": [], "prompts": []}  # 숨은 추가 설정
    for m in models:                                         # --model 처리(processModels)
        injected.append(_sec.decode_package_identifier(m, environ))  # 모델 슬러그를 식별자로 변환
    for m in mcp:                                            # --mcp 처리(processMcpServers)
        try:                                                # MCP 처리 시도
            if m.startswith("http://") or m.startswith("https://"):  # URL 이면
                additional["mcpServers"].append({"name": _url_hostname(m), "url": m})  # 호스트 이름으로 MCP 서버 추가
            else:                                           # 아니면
                injected.append(_sec.decode_package_identifier(m, environ))  # 식별자로 변환
        except Exception as e:                              # 실패하면
            warnings.append(f'Failed to add MCP server "{m}": {e}')  # 경고 기록
    for item in list(rules) + list(prompts):                 # processRulesAndPrompts — --prompt 글도 규칙으로 들어감(cn 그대로)
        if is_string_rule(item):                            # 글 규칙이면
            additional["rules"].append(item)                # 숨은 설정의 rules 에 추가
        else:                                               # 아니면
            injected.append(_sec.decode_package_identifier(item, environ))  # 식별자로 변환해 주입
    return injected, additional                             # (주입 식별자, 숨은 설정) 반환


# ───────────────────────── 대화 모델(config.ts getLlmApi, ModelService) ─────────────────────────
def _is_chat(m: Any) -> bool:                               # chat 모델인지 판정
    """model?.roles?.includes("chat") || (model && model.roles === undefined)."""
    if not isinstance(m, dict):                             # 사전이 아니면
        return False                                        # 아님
    roles = m.get("roles", MISSING)                         # roles 값
    return roles is MISSING or (isinstance(roles, list) and "chat" in roles)  # roles 가 없거나 chat 이 있으면 참


def select_chat_model(raw: dict, gdir: pathlib.Path) -> ModelConfig:  # 대화 모델 고르는 함수
    """ModelService.doInitialize — 저장된 선택(globalContext cliSelectedModel)이 chat 모델 중에 있으면 그것, 아니면 첫 chat 모델."""
    models = raw.get("models") or []                        # 모델 목록
    available = [m for m in models if _is_chat(m)]           # chat 역할이거나 역할 없음
    preferred = _paths.read_global_context(gdir).get("cliSelectedModel")   # 마지막 선택
    if preferred:                                           # 저장된 선택이 있으면
        for m in available:                                 # chat 모델 중에서
            if (m.get("name") or m.get("model")) == preferred:  # 이름 또는 id 가 같으면
                return ModelConfig.from_raw(m)              # 그 모델 반환
    if not models:                                           # getLlmApi — 모델이 하나도 없으면
        raise ConfigError("No models found in the configured assistant")  # cn 원문 오류
    for m in models:                                        # 첫 chat 모델 찾기
        if _is_chat(m):                                     # chat 모델이면
            return ModelConfig.from_raw(m)                  # 그 모델 반환
    raise ConfigError("No models with the chat role found in the configured assistant")  # chat 모델이 없으면 오류


# ───────────────────────── loadConfiguration ─────────────────────────
def _load_from_cli_flag(config_path: str, *, cwd: str, inject: list[dict], client: _sec.LocalPlatformClient,  # --config 값을 읽는 함수
                        environ: Mapping[str, str], warnings: list[str]) -> dict:  # (인자 이어짐)
    """loadFromCliFlag — 파일이면 그 파일, 아니면 슬러그(로그인 없음 → 로컬 unroll → 지원 안 함 오류)."""
    if is_file_path(config_path):                           # 파일 경로면
        return _load_config_yaml(config_path, cwd=cwd, inject=inject, client=client, environ=environ, warnings=warnings)  # YAML 파일로 읽음
    parts = config_path.split("/")                           # loadAssistantSlug — 슬러그 분해
    if len(parts) < 2 or not parts[0] or not parts[1]:      # 형식이 틀리면
        raise ConfigError(f'Invalid assistant slug format. Expected "owner/package", got: {config_path}')  # 슬러그 형식 오류
    raise ConfigError("Slug-based package resolution is not supported", hub=True)   # registry.getContent(slug) — 슬러그 해석은 지원하지 않음


def _load_config_yaml(file_path: str, *, cwd: str, inject: list[dict], client: _sec.LocalPlatformClient,  # config.yaml 을 읽는 함수
                      environ: Mapping[str, str], warnings: list[str]) -> dict:  # (인자 이어짐)
    """loadConfigYaml → unrollAssistantWithConfig — 경로 글을 그대로 씀(~ 확장·정규화 없음)."""
    id_ = {"uriType": "file", "fileUri": file_path}         # 파일 식별자
    registry = RegistryClient(path_dirname(file_path), cwd)  # rootPath = dirname(fileUri) — 설정 파일 폴더를 rootPath 로
    try:                                                    # 읽기·풀기 시도
        raw = registry.get_content(id_)                      # 읽기(오류는 Node 글)
        config, errors = unroll_assistant_from_content(id_, raw, registry, inject_blocks=inject, client=client,  # 설정 풀기(비밀·블록 처리)
                                                       environ=environ, warnings=warnings)  # (인자 이어짐)
    except ConfigError:                                     # ConfigError 는
        raise                                               # 그대로 올림
    except Exception as e:                                   # 치명 오류 = 그 글
        raise ConfigError(str(e)) from e                    # 치명 오류는 ConfigError 로 감쌈
    if errors:                                               # 비치명 경고(cn: console.warn dim)
        if len(errors) > 1:                                 # 오류가 여러 개면
            w = ",".join(f"\n- {d['message']}" for d in errors)   # 배열을 템플릿에 넣어 "," 로 이어짐(cn 그대로)
        else:                                               # 하나면
            w = errors[0]["message"]                        # 그 메시지
        warnings.append(f"Warning: {w}")                    # 경고 기록
    config["__errors__"] = errors                            # 비치명 오류(load_config 가 꺼내 LoadedConfig.errors 로)
    return config                                           # 설정 반환


def load_config(config_path: Optional[str], *, cwd: pathlib.Path, global_dir: pathlib.Path,  # 설정을 읽는 진입 함수
                environ: Optional[Mapping[str, str]] = None, headless: bool = True,  # (인자 이어짐)
                rules: Optional[list[str]] = None, mcp: Optional[list[str]] = None,  # (인자 이어짐)
                models: Optional[list[str]] = None, prompts: Optional[list[str]] = None) -> LoadedConfig:  # (인자 이어짐)
    """cn ConfigService.loadConfig + ModelService 모델 고르기.
    config_path = --config, rules/mcp/models/prompts = --rule/--mcp/--model/--prompt 값들, headless = -p 여부.
    environ 은 <cwd>/.env 를 반영한 사본으로 쓴다(dotenv.config 와 같음). 실패하면 ConfigError(cn 오류 글)."""
    cwd_s = os.fspath(cwd)                                   # 글로
    env = _env.effective_environ(cwd_s, environ)             # process.env(dotenv 적용)
    home = os.fspath(global_dir)                             # env.continueHome — 전역 폴더(continueHome)
    warnings: list[str] = []                                 # cn console 출력
    client = _sec.LocalPlatformClient(environ=env, continue_home=home, cwd=cwd_s, warnings=warnings)  # 비밀 찾는 클라이언트

    if not headless and config_path is not None:             # onboarding: --config 를 미리 한 번 읽어 봄
        try:                                                # 미리 읽어 보기 시도
            _load_from_cli_flag(config_path, cwd=cwd_s, inject=[], environ=env, warnings=[],  # --config 를 한 번 읽음
                                client=_sec.LocalPlatformClient(environ=env, continue_home=home, cwd=cwd_s))  # (인자 이어짐)
        except ConfigError as e:                             # `${errorMessage}` 는 Error 객체 → "Error: 글"
            raise ConfigError(f'Failed to load config from "{config_path}": Error: {e}', hub=e.hub) from e  # cn 원문 형식 오류로 바꿈

    injected, additional = additional_blocks_from_options(models=models or [], mcp=mcp or [], rules=rules or [],  # 옵션 값을 주입 블록으로 변환
                                                          prompts=prompts or [], environ=env, warnings=warnings)  # (인자 이어짐)
    if config_path:                                          # 1) --config — --config 가 있으면
        source = {"type": "cli-flag", "path": config_path}  # 원천 기록
        loaded = _load_from_cli_flag(config_path, cwd=cwd_s, inject=injected, client=client, environ=env, warnings=warnings)  # --config 읽기
    elif os.path.exists(_paths.global_config_yaml(pathlib.Path(path_resolve(cwd_s, home)))):   # 2) <전역>/config.yaml
        source = {"type": "local-config-yaml"}              # 원천 기록
        loaded = _load_config_yaml(path_join(home, "config.yaml"), cwd=cwd_s, inject=injected, client=client,  # 전역 config.yaml 읽기
                                   environ=env, warnings=warnings)  # (인자 이어짐)
    else:                                                    # 3) Hub 기본 설정 — 폐쇄망: cn이 받는 오류 그대로
        raise ConfigError(_sec.HUB_FETCH_FAILED, hub=True)  # 둘 다 없으면 Hub 실패 오류

    unroll_errors = loaded.pop("__errors__", [])             # 비치명 오류
    merged = merge_unrolled_assistants(loaded, additional)   # 주입 설정 합치기
    md_rules = load_markdown_rules_with_metadata(cwd_s, home)   # .continue/rules — 마크다운 규칙 읽기
    if md_rules:                                            # 있으면
        existing = {(r if isinstance(r, str) else (r or {}).get("rule")) for r in (merged.get("rules") or [])}  # 이미 들어 있는 규칙 글
        new = [r for r in md_rules if r["rule"] not in existing]  # 새 규칙만 골라
        merged["rules"] = list(merged.get("rules") or []) + new  # rules 뒤에 붙임

    has_chat = any(isinstance(m, dict) and (not m.get("roles") or "chat" in m["roles"]) for m in (merged.get("models") or []))  # chat 모델이 있는지
    if not has_chat:                                         # addDefaultChatModelIfNone — 기본 모델 슬러그는 로컬에서 못 읽음
        warnings.append('Failed to unroll block {"uriType":"slug","fullSlug":{"ownerSlug":"anthropic","packageSlug":"claude-sonnet-4-6",'  # 기본 모델 주입 실패 경고
                        '"versionSlug":"1.0.0"}}: Slug-based package resolution is not supported')  # (문자열 이어짐)
        if headless:                                        # 헤드리스면
            raise ConfigError("No model specified in headless mode (and failed to load default model)", hub=True)  # 모델 없음 오류

    model_list = [ModelConfig.from_raw(m) for m in (merged.get("models") or []) if isinstance(m, dict)]  # ModelConfig 목록 만들기
    chat_model, chat_err = None, None                       # 대화 모델 초기값
    try:                                                    # 대화 모델 고르기 시도
        chat_model = select_chat_model(merged, pathlib.Path(path_resolve(cwd_s, home)))  # 저장된 선택·첫 chat 모델
    except ConfigError as e:                                # 못 고르면
        chat_err = e.message                                # 오류 글 기록
    return LoadedConfig(                                    # 읽은 설정 반환
        raw=merged, models=model_list, chat_model=chat_model, chat_model_error=chat_err,  # 원본·모델 목록·대화 모델·오류
        mcp_servers=list(merged.get("mcpServers") or []), rules=list(merged.get("rules") or []),  # MCP 서버·규칙
        prompts=list(merged.get("prompts") or []), context=list(merged.get("context") or []),  # 프롬프트·컨텍스트 목록
        docs=list(merged.get("docs") or []), data=list(merged.get("data") or []),  # 문서·데이터 목록
        name=merged.get("name"), version=merged.get("version"), env=merged.get("env") or {},  # 이름·버전·env
        request_options=merged.get("requestOptions"), warnings=warnings, errors=unroll_errors, source=source,  # 요청 옵션·경고·오류·원천
        config_path=config_path,                            # --config 값
    )
