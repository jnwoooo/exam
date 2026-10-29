# tests/test_config.py — ncn/config 시험(paths·env·secrets·schema·loader). cn CLIPlatformClient.test.ts·services/ConfigService.test.ts 대표 사례 +
#   cn 실제 코드(packages/config-yaml/src 를 tsx 로 직접 실행, dotenv 16.6.1 실행)에서 얻은 기대값
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 기대값 비교
import pathlib                                               # 경로

import pytest                                                # 시험 도구

from ncn.config import env as E                              # dotenv — dotenv 모듈
from ncn.config import loader as L                           # 설정 읽기
from ncn.config import paths as P                            # 전역 폴더
from ncn.config import secrets as S                          # 비밀값
from ncn.config.schema import yaml_parse                     # YAML 1.2 — YAML 1.2 파서


def _w(p: pathlib.Path, text: str) -> pathlib.Path:         # 파일 쓰기 도우미 함수
    """파일 쓰기(상위 폴더 생성)."""
    p.parent.mkdir(parents=True, exist_ok=True)             # 상위 폴더 생성
    p.write_text(text, encoding="utf-8")                    # 내용 쓰기
    return p                                                # 쓴 경로 반환


def _env(tmp_path, **kw) -> dict:                           # 깨끗한 환경변수 사전 도우미 함수
    """깨끗한 환경(HOME 은 임시 폴더)."""
    return {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), **kw}  # HOME 을 임시 폴더로, 나머지는 인자로


MODEL_YAML = "name: t\nversion: 0.0.1\nmodels:\n  - name: m\n    provider: openai\n    model: fake-model\n"  # 모델 하나만 든 설정 YAML


# ───────────────────────── paths ─────────────────────────
def test_global_dir_rules(tmp_path):                        # 전역 폴더 규칙 시험
    """CONTINUE_GLOBAL_DIR: 절대경로 그대로, 상대경로는 cwd 기준, 빈 글은 무시(~/.continue)."""
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "/abs/x", "HOME": "/h"}, cwd="/w") == pathlib.Path("/abs/x")  # 절대경로는 그대로
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "rel/x", "HOME": "/h"}, cwd="/w") == pathlib.Path("/w/rel/x")  # 상대경로는 작업 폴더 기준
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "", "HOME": "/h"}, cwd="/w") == pathlib.Path("/h/.continue")  # 빈 글은 무시하고 ~/.continue
    assert P.continue_home({"CONTINUE_GLOBAL_DIR": "rel/x"}) == "rel/x"   # env.ts 형태는 그대로


# ───────────────────────── dotenv 16.6.1(node 로 얻은 결과) ─────────────────────────
DOTENV_CASES = [                                            # dotenv 시험 사례 모음(node 결과 기준)
    ("A=1\nB = two \nC='single # not comment'\nD=\"dq\\nnl\"\nE=`bt`\nexport F=exp\nG=val # comment\nH=\nI\nJ: colon\n# c\nK=\"multi\nline\"\nL=a=b\r\nM=last",  # 여러 문법을 섞은 입력
     {"A": "1", "B": "two", "C": "single # not comment", "D": "dq\nnl", "E": "bt", "F": "exp", "G": "val", "H": "", "J": "colon",  # 기대 결과 앞부분
      "K": "multi\nline", "L": "a=b", "M": "last"}),        # 기대 결과 뒷부분
    ("\ufeffBOM=1\nA.B-C=dots", {"BOM": "1", "A.B-C": "dots"}),  # BOM 과 점·하이픈 키
    ("X='it\\'s'\nY=\"a\\\"b\"\nZ=  spaced  ", {"X": "it\\'s", "Y": "a\\\"b", "Z": "spaced"}),  # 따옴표 이스케이프와 공백
    ("DUP=1\nDUP=2", {"DUP": "2"}),                         # 중복 키는 뒤가 이김
    ("Q=\"unterminated\nR=after", {"Q": "\"unterminated", "R": "after"}),  # 닫히지 않은 따옴표
]                                                           # (사례 끝)


@pytest.mark.parametrize("src,expected", DOTENV_CASES)      # dotenv 사례 반복 시험
def test_parse_dotenv(src, expected):                       # dotenv 파싱 시험 함수
    assert E.parse_dotenv(src) == expected                  # 기대 결과와 같은지 확인


def test_apply_dotenv_does_not_override(tmp_path):          # dotenv 가 기존 값을 덮지 않는 시험
    """dotenv.config — 이미 있는 키(빈 값 포함)는 그대로."""
    _w(tmp_path / ".env", "A=file\nB=file\nC=file\n")       # .env 파일 작성
    environ = {"A": "shell", "B": ""}                       # 이미 있는 값(빈 값 포함)
    E.apply_dotenv(environ, str(tmp_path))                  # dotenv 적용
    assert environ == {"A": "shell", "B": "", "C": "file"}  # 기존 키는 그대로, 새 키만 추가


# ───────────────────────── YAML 1.2 ─────────────────────────
def test_yaml_core_schema():                                # YAML 1.2 형식 판정 시험
    """yaml 라이브러리(1.2 core)와 같은 형식 판정 — node 결과와 비교."""
    doc = yaml_parse("a: 012\nb: 0o17\nc: yes\nd: 1e3\ne: 1_000\nf: 2020-01-01\n<<: x\n1: z\nnull: n\ng: .5\nh: -0x1\ni: 1.0")  # 여러 형식의 값을 파싱
    assert doc == {"a": 12, "b": 15, "c": "yes", "d": 1000, "e": "1_000", "f": "2020-01-01", "<<": "x", "1": "z", "": "n",  # 기대 결과 앞부분
                   "g": 0.5, "h": "-0x1", "i": 1}           # 기대 결과 뒷부분
    assert yaml_parse("") is None                           # 빈 문서는 None


# ───────────────────────── secrets(CLIPlatformClient.test.ts) ─────────────────────────
def _client(tmp_path, environ) -> S.LocalPlatformClient:    # 비밀 클라이언트 만드는 도우미 함수
    return S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path / "w"))  # 임시 폴더 기준 클라이언트 생성


def test_resolve_empty_no_hub(tmp_path):                    # 빈 목록은 Hub 를 부르지 않는 시험
    c = _client(tmp_path, {})                               # 클라이언트 생성
    assert c.resolve_fqsns([]) == [] and c.hub_calls == 0 and c.warnings == []  # 결과·호출 횟수·경고 확인


def test_resolve_order(tmp_path):                           # 비밀 찾는 순서 시험
    """환경변수 > <전역>/.env > <cwd>/.continue/.env > <cwd>/.env, 빈 값은 못 찾은 것."""
    _w(tmp_path / "gd" / ".env", "A=home\nB=home\n")        # 전역 .env 작성
    _w(tmp_path / "w" / ".continue" / ".env", "B=wsc\nC=wsc\n")  # <cwd>/.continue/.env 작성
    _w(tmp_path / "w" / ".env", "C=ws\nD=ws\nE=\n")         # <cwd>/.env 작성
    c = _client(tmp_path, {"A": "env", "Z": ""})            # 환경변수 A 와 빈 값 Z
    names = ["A", "B", "C", "D", "Z", "E"]                  # 조회할 비밀 이름들
    res = c.resolve_fqsns([{"packageSlugs": [], "secretName": n} for n in names])  # FQSN 목록으로 조회
    assert [r["value"] if r else None for r in res] == ["env", "home", "wsc", "ws", None, None]  # 우선순위대로 값이 나오고 빈 값은 못 찾음
    assert c.hub_calls == 1                                  # 못 찾은 것이 있으면 Hub 시도 → 실패 경고
    assert c.warnings == ["Error resolving FQSNs through API: The request failed and the interceptors did not return an alternative response"]  # Hub 실패 경고 글 확인


def test_dotenv_trap_cwd_env_beats_home_env(tmp_path):      # dotenv 함정 시험
    """cn 함정: dotenv.config 가 <cwd>/.env 를 먼저 환경변수에 넣으므로 ~/.continue/.env 보다 이긴다."""
    gd = tmp_path / "gd"                                    # 전역 폴더
    _w(gd / ".env", "K=home\n")                             # 전역 .env 작성
    w = tmp_path / "w"                                      # 작업 폴더
    _w(w / ".env", "K=cwd\n")                               # 작업 폴더 .env 작성
    _w(w / "c.yaml", MODEL_YAML + "    apiKey: ${{ secrets.K }}\n")  # 비밀을 쓰는 설정 작성
    cfg = L.load_config("./c.yaml", cwd=w, global_dir=gd, environ=_env(tmp_path))  # 설정 읽기
    assert cfg.chat_model.api_key == "cwd"                  # <cwd>/.env 값이 이김


def test_fqsn_roundtrip():                                  # FQSN 왕복 시험
    assert S.decode_fqsn("//X") == {"packageSlugs": [{"ownerSlug": "", "packageSlug": ""}], "secretName": "X"}  # 빈 패키지 슬러그 해석
    assert S.encode_fqsn(S.decode_fqsn("////X")) == "////X"  # 해석 후 다시 인코딩해도 같음
    with pytest.raises(ValueError, match="Invalid FQSN format"):  # 홀수 조각은 오류
        S.decode_fqsn("a/X")                                # 오류가 나는 입력


# ───────────────────────── 원천 결정·Hub 대체 ─────────────────────────
def test_no_config_is_hub_failure(tmp_path):                # 설정 파일 없음 시험
    """설정 파일이 없으면 cn은 Hub 기본 설정을 받으려다 실패 — 같은 글."""
    with pytest.raises(L.ConfigError) as ei:                # Hub 실패 오류가 나야 함
        L.load_config(None, cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 설정 읽기 시도
    assert str(ei.value) == "The request failed and the interceptors did not return an alternative response"  # cn 과 같은 오류 글
    assert ei.value.hub is True                             # Hub 관련 실패 표시


def test_local_global_config_used(tmp_path):                # 전역 config.yaml 사용 시험
    """--config 가 없으면 <전역>/config.yaml."""
    _w(tmp_path / "gd" / "config.yaml", MODEL_YAML)         # 전역 설정 작성
    cfg = L.load_config(None, cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # --config 없이 읽기
    assert cfg.source == {"type": "local-config-yaml"} and cfg.chat_model.model == "fake-model"  # 원천과 모델 확인


def test_slug_config(tmp_path):                             # 슬러그 --config 시험
    with pytest.raises(L.ConfigError, match="^Slug-based package resolution is not supported$"):  # 슬러그는 지원하지 않는다는 오류
        L.load_config("owner/pkg", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))  # 읽기 시도
    with pytest.raises(L.ConfigError) as ei:                # 형식이 틀린 슬러그
        L.load_config("abc", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))  # 읽기 시도
    assert str(ei.value) == 'Invalid assistant slug format. Expected "owner/package", got: abc'  # 오류 글 확인


def test_missing_file_node_message(tmp_path):               # 없는 파일 오류 글 시험
    with pytest.raises(L.ConfigError) as ei:                # 오류가 나야 함
        L.load_config("./nope.yaml", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))  # 읽기 시도
    assert str(ei.value) == "ENOENT: no such file or directory, open 'nope.yaml'"   # rootPath "." 와 합쳐져 정규화된 경로


def test_tui_precheck_wrapper(tmp_path):                    # 대화형 사전 점검 감싸기 시험
    """대화형: onboarding 이 먼저 읽어 보고 `Failed to load config from "…": Error: …` 로 감싼다."""
    with pytest.raises(L.ConfigError) as ei:                # 오류가 나야 함
        L.load_config("owner/pkg", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path), headless=False)  # 대화형(headless=False)으로 읽기
    assert str(ei.value) == 'Failed to load config from "owner/pkg": Error: Slug-based package resolution is not supported'  # 감싼 오류 글 확인


def test_is_file_path():                                    # 파일 경로 판정 시험
    assert L.is_file_path("./a") and L.is_file_path("~/a") and L.is_file_path("C:\\x") and L.is_file_path("x.yml")  # 파일로 판정되는 경우
    assert not L.is_file_path("owner/pkg")                  # 슬러그는 파일이 아님


# ───────────────────────── 검증 오류 글(cn 실제 실행 결과) ─────────────────────────
@pytest.mark.parametrize("text,expected", [                 # 검증 오류 글 시험(여러 입력)
    ("name: 1\nversion: 0.0.1\nmodels:\n  - name: m\n    model: x\n  - provider: openai\n    name: m\n    model: [1]\n    roles: [chatx]\n"  # 여러 오류가 섞인 설정
     "mcpServers:\n  - name: s\n    connectionTimeout: 0\n    command: x\ndata:\n  - name: d\n    destination: x\n    schema: 1.0\n",  # (설정 이어짐)
     "Failed to parse config: name: Expected string, received number, models.0: Invalid input, models.1: Invalid input, data.0: Invalid input, "  # 기대 오류 글 앞부분
     "mcpServers.0.connectionTimeout: Number must be greater than 0"),  # 기대 오류 글 뒷부분
    ("name: t\nversion: \"1\"\nmodels: notalist\nrules: [1]\nmetadata: {a: 1}\n",  # 배열이 아닌 값 등 오류
     "Failed to parse config: metadata.a: Expected string, received number, models: Expected array, received string, rules.0: Invalid input"),  # 기대 오류 글
    ("", "Failed to parse config: Expected object, received null"),  # 빈 문서
    ("name: t\nversion: \"1\"\ndata:\n  - name: d\n    destination: x\n    schema: \"1.0\"\n",  # semver 가 아닌 스키마 버전
     "Failed to parse config: data.0.schema: Version must follow semver format, e.g. 0.2.0"),  # 기대 오류 글
])
def test_validation_messages(tmp_path, text, expected):     # 검증 오류 글 시험 함수
    _w(tmp_path / "c.yaml", text)                           # 설정 작성
    with pytest.raises(L.ConfigError) as ei:                # 오류가 나야 함
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 설정 읽기 시도
    assert str(ei.value) == expected                        # 기대 오류 글과 같은지 확인


def test_numeric_secret_breaks_like_cn(tmp_path):           # 숫자 비밀값 시험
    """비밀값이 "12345" 면 cn은 YAML 글에 끼운 뒤 다시 읽어 숫자가 되어 검증 실패."""
    _w(tmp_path / "c.yaml", "name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: openai\n    model: x\n    apiKey: ${{ secrets.NUM }}\n")  # 비밀을 쓰는 설정 작성
    with pytest.raises(L.ConfigError) as ei:                # 오류가 나야 함
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path, NUM="12345"))  # 숫자 비밀값으로 읽기 시도
    assert str(ei.value) == "Failed to parse config: models.0.apiKey: Expected string, received number"  # 형식 오류 글 확인


# ───────────────────────── 전체 unroll 결과(cn 실제 실행 결과와 같음) ─────────────────────────
# unroll 시험용 설정 원문(여러 줄 문자열이라 줄 끝 주석 불가)
D01 = """name: t
version: 0.0.1
models:
  - name: m
    provider: openai
    model: fake-model
    apiKey: ${{ secrets.K }}
    apiBase: ${{ secrets.UNSET }}
    requestOptions:
      headers:
        Authorization: Bearer ${{ secrets.K }}
        X-Q: "'${{ secrets.K }}'"
        X-B: ${{ secrets.BOOL }}
        X-S: ${{ secrets.SPACE }}
        ${{ secrets.K }}: keyed
    chatOptions:
      toolOverrides:
        Bash: {description: "hi", disabled: false}
    capabilities: [tool_use, custom]
    env: {A: 1, B: true, C: x}
  - uses: ./block1.yaml
    with: {KEY: "${{ secrets.UNSET2 }}"}
rules:
  - "This is a very long rule that has a secret ${{ secrets.K }} embedded somewhere in the middle of a long line so folding happens maybe"
  - uses: ./rule1.md
  - uses: ./missing_rule.md
  - uses: someone/rule
mcpServers:
  - name: remote
    url: https://example.com/mcp
    type: streamable-http
    apiKey: ${{ secrets.UNSET }}
data:
  - name: d
    destination: x
    schema: 0.2.0
    level: all
docs:
  - name: doc
    startUrl: http://a
context:
  - provider: code
  - provider: code
prompts:
  - name: p
    prompt: do ${{ inputs.X }}
"""
BLOCK1 = "name: b\nversion: 1.0.0\nmodels:\n  - name: bm\n    provider: openai\n    model: m2\n    apiKey: ${{ inputs.KEY }}\n"  # 블록 파일 원문
RULE1 = "---\nname: R\nalwaysApply: true\n---\n\nRule body\n"  # 규칙 파일 원문
D01_EXPECTED = json.loads("""{"name": "t", "version": "0.0.1", "models": [{"name": "m", "model": "fake-model", "apiKey": "sekret", "apiBase": "${{ secrets.//UNSET }}", "capabilities": ["tool_use", "custom"], "requestOptions": {"headers": {"Authorization": "Bearer sekret", "X-Q": "'sekret'", "X-B": "yes", "X-S": "a b", "sekret": "keyed"}}, "chatOptions": {"toolOverrides": {"Bash": {"description": "hi", "disabled": false}}}, "env": {"A": 1, "B": true, "C": "x"}, "provider": "openai"}, {"name": "bm", "model": "m2", "apiKey": "${{ secrets.////UNSET2 }}", "provider": "openai"}], "context": [{"provider": "code"}], "data": [{"name": "d", "destination": "x", "schema": "0.2.0", "level": "all"}], "mcpServers": [{"name": "remote", "url": "https://example.com/mcp", "type": "streamable-http", "apiKey": "${{ secrets.//UNSET }}"}], "rules": ["This is a very long rule that has a secret sekret embedded somewhere in the middle of a long line so folding happens maybe", {"name": "R", "rule": "Rule body", "alwaysApply": true, "sourceFile": "./rule1.md"}, null, null], "prompts": [{"name": "p", "prompt": "do ${{ inputs.X }}"}], "docs": [{"name": "doc", "startUrl": "http://a"}]}""")  # cn 에서 얻은 기대 설정
D01_ERRORS = [{"fatal": False, "message": "ENOENT: no such file or directory, open 'missing_rule.md':\n./missing_rule.md"},  # 기대 비치명 오류들
              {"fatal": False, "message": "Slug-based package resolution is not supported:\nsomeone/rule"}]  # (오류 이어짐)


def test_unroll_matches_cn(tmp_path):                       # unroll 결과 시험
    """uses 블록·inputs·secrets(FQSN)·override·중복 제거·키 순서까지 cn unrollAssistant 결과와 같다."""
    _w(tmp_path / "d01.yaml", D01)                          # 설정 파일 작성
    _w(tmp_path / "block1.yaml", BLOCK1)                    # 블록 파일 작성
    _w(tmp_path / "rule1.md", RULE1)                        # 규칙 파일 작성
    environ = _env(tmp_path, K="sekret", BOOL="yes", SPACE="a b")  # 비밀이 든 환경변수
    warnings: list[str] = []                                # 경고 모음
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path), warnings=warnings)  # 비밀 클라이언트 생성
    reg = L.RegistryClient(".", str(tmp_path))              # 파일 블록 클라이언트
    raw = reg.get_content({"uriType": "file", "fileUri": "./d01.yaml"})  # 설정 원문 읽기
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./d01.yaml"}, raw, reg, inject_blocks=[],  # 설정 풀기
                                                client=client, environ=environ, warnings=warnings)  # (인자 이어짐)
    assert json.dumps(cfg) == json.dumps(D01_EXPECTED)       # 키 순서까지
    assert errs == D01_ERRORS                               # 비치명 오류 확인
    assert "Error resolving FQSNs through API: The request failed and the interceptors did not return an alternative response" in warnings  # Hub 실패 경고 확인


def test_load_config_merged_and_warning(tmp_path):          # 병합·경고 시험
    """load_config: 실패 블록(null)은 합치기에서 빠지고, 비치명 오류는 'Warning: \\n- …,\\n- …' 한 줄로."""
    _w(tmp_path / "d01.yaml", D01)                          # 설정 파일 작성
    _w(tmp_path / "block1.yaml", BLOCK1)                    # 블록 파일 작성
    _w(tmp_path / "rule1.md", RULE1)                        # 규칙 파일 작성
    cfg = L.load_config("./d01.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path, K="sekret", BOOL="yes", SPACE="a b"))  # 설정 읽기
    assert [m.name for m in cfg.models] == ["m", "bm"]      # 모델 목록 확인
    assert cfg.chat_model.name == "m" and cfg.chat_model.api_key == "sekret"  # 대화 모델 확인
    assert cfg.rule_texts == ["This is a very long rule that has a secret sekret embedded somewhere in the middle of a long line so folding happens maybe",  # 규칙 글 목록 확인
                              "Rule body"]                  # (규칙 이어짐)
    assert ("Warning: \n- ENOENT: no such file or directory, open 'missing_rule.md':\n./missing_rule.md,"  # 경고 한 줄 확인
            "\n- Slug-based package resolution is not supported:\nsomeone/rule") in cfg.warnings  # (경고 이어짐)
    assert cfg.env == {}                                     # mergeUnrolledAssistants: {...undefined} = {} — env 는 빈 사전


def test_block_errors_nonfatal(tmp_path):                   # 블록 오류가 치명적이지 않은 시험
    """블록 항목 수 오류·inputs 누락 → 항목 null, 오류 글 cn 그대로."""
    _w(tmp_path / "block1.yaml", BLOCK1)                    # 블록 파일 작성
    _w(tmp_path / "bad.yaml", "name: b\nversion: 1.0.0\nmodels:\n  - name: a\n    provider: o\n    model: x\n  - name: b\n    provider: o\n    model: y\n")  # 항목이 2개인 잘못된 블록 작성
    _w(tmp_path / "c.yaml", "name: t\nversion: \"1\"\nmodels:\n  - uses: ./bad.yaml\n  - uses: ./block1.yaml\n")  # 두 블록을 쓰는 설정 작성
    reg = L.RegistryClient(".", str(tmp_path))              # 파일 블록 클라이언트
    environ = _env(tmp_path)                                # 환경변수
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path))  # 비밀 클라이언트
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./c.yaml"}, reg.get_content({"uriType": "file", "fileUri": "./c.yaml"}),  # 설정 풀기
                                                reg, inject_blocks=[], client=client, environ=environ, warnings=[])  # (인자 이어짐)
    assert cfg == {"name": "t", "version": "1", "models": [None, None]}  # 실패 항목은 null
    assert errs == [{"fatal": False, "message": "Failed to parse block: models: Array must contain exactly 1 element(s).\n> \"./bad.yaml\""},  # 기대 오류 글
                    {"fatal": False, "message": "Missing required input(s) for block \"/\": KEY. Please provide these values in the \"with\" block..\n> \"./block1.yaml\""}]  # (오류 이어짐)


def test_injected_blocks_match_cn(tmp_path):                # 주입 블록 시험
    """주입 블록(--rule 파일·--mcp 파일·슬러그): 같은 이름은 버리고, 슬러그 오류 글은 '[object Object]'(cn 그대로)."""
    _w(tmp_path / "e01.yaml", "name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: o\n    model: x\nrules:\n  - name: R\n    rule: existing\n")  # 기존 규칙이 든 설정 작성
    _w(tmp_path / "rule1.md", RULE1)                        # 규칙 파일 작성
    _w(tmp_path / "d03.md", "---\nname: MDR\nglobs: \"*.ts\"\n---\n\nMarkdown config body\n")  # 마크다운 설정 파일 작성
    _w(tmp_path / "mcp.yaml", "name: mcpb\nversion: 1.0.0\nmcpServers:\n  - name: inj\n    command: run\n    env: {T: \"${{ inputs.TOK }}\"}\n")  # MCP 블록 작성
    inj = [{"uriType": "file", "fileUri": "./rule1.md"}, {"uriType": "file", "fileUri": "./d03.md"}, {"uriType": "file", "fileUri": "./mcp.yaml"},  # 주입할 식별자들
           {"uriType": "slug", "fullSlug": {"ownerSlug": "o", "packageSlug": "x", "versionSlug": "latest"}}, {"uriType": "file", "fileUri": "./nope.md"}]  # (식별자 이어짐)
    environ = _env(tmp_path, TOK="tk")                      # 환경변수
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path))  # 비밀 클라이언트
    reg = L.RegistryClient(".", str(tmp_path))              # 파일 블록 클라이언트
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./e01.yaml"}, reg.get_content({"uriType": "file", "fileUri": "./e01.yaml"}),  # 주입 블록과 함께 풀기
                                                reg, inject_blocks=inj, client=client, environ=environ, warnings=[])  # (인자 이어짐)
    assert cfg == {"name": "t", "version": "1", "models": [{"name": "m", "model": "x", "provider": "o"}],  # 기대 설정 앞부분
                   "mcpServers": [{"name": "inj", "sourceFile": "./mcp.yaml", "command": "run", "env": {"T": "tk"}}],  # MCP 서버가 주입됨
                   "rules": [{"name": "R", "rule": "existing"}, {"name": "MDR", "rule": "Markdown config body", "globs": "*.ts", "sourceFile": "./d03.md"}]}  # 규칙이 주입됨
    assert errs == [{"fatal": False, "message": "Slug-based package resolution is not supported.\n> [object Object]"},  # 기대 오류 글
                    {"fatal": False, "message": "ENOENT: no such file or directory, open 'nope.md'.\n> ./nope.md"}]  # (오류 이어짐)


def test_markdown_config_file(tmp_path):                    # 마크다운 설정 파일 시험
    """.yaml 이 아닌 파일은 YAML 검증 실패 시 마크다운 규칙으로."""
    _w(tmp_path / "d03.md", "---\nname: MDR\nglobs: \"*.ts\"\n---\n\nMarkdown config body\n")  # 마크다운 파일 작성
    reg = L.RegistryClient(".", str(tmp_path))              # 파일 블록 클라이언트
    environ = _env(tmp_path)                                # 환경변수
    client = S.LocalPlatformClient(environ=environ, continue_home="/x", cwd=str(tmp_path))  # 비밀 클라이언트
    cfg, _ = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./d03.md"}, reg.get_content({"uriType": "file", "fileUri": "./d03.md"}),  # 설정 풀기
                                             reg, inject_blocks=[], client=client, environ=environ, warnings=[])  # (인자 이어짐)
    assert cfg == {"name": "MDR", "version": "1.0.0", "rules": [{"name": "MDR", "rule": "Markdown config body", "globs": "*.ts", "sourceFile": "./d03.md"}]}  # 마크다운 규칙으로 읽힌 결과 확인


# ───────────────────────── ConfigService 주입(--rule/--prompt/--mcp) ─────────────────────────
def test_cli_injections(tmp_path):                          # 명령줄 주입 시험
    _w(tmp_path / "c.yaml", MODEL_YAML + "rules:\n  - config rule\n")  # 설정 작성
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path),  # 규칙·프롬프트·MCP 를 주입해 읽기
                        rules=["use tabs please"], prompts=["a prompt text"], mcp=["https://Example.COM:8443/mcp", "not a url"])  # (인자 이어짐)
    assert cfg.rule_texts == ["use tabs please", "a prompt text", "config rule"]   # 주입이 앞(incoming first), --prompt 글도 규칙(cn 그대로)
    assert cfg.mcp_servers == [{"name": "example.com", "url": "https://Example.COM:8443/mcp"}]  # URL MCP 서버가 호스트 이름으로 추가됨


def test_string_rule_detection():                           # 글 규칙 판정 시험
    assert L.is_string_rule("use tabs") and L.is_string_rule("plain")  # 글 규칙인 경우
    assert not L.is_string_rule("./r.md") and not L.is_string_rule("owner/pkg") and not L.is_string_rule("a\\b")  # 글 규칙이 아닌 경우


# ───────────────────────── 대화 모델 고르기 ─────────────────────────
def test_headless_no_chat_model(tmp_path):                  # chat 모델이 없을 때 시험
    _w(tmp_path / "c.yaml", MODEL_YAML + "    roles: [subagent]\n")  # chat 역할이 없는 설정 작성
    with pytest.raises(L.ConfigError, match=r"^No model specified in headless mode \(and failed to load default model\)$"):  # 헤드리스면 오류
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 읽기 시도
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path), headless=False)  # 대화형이면 오류 대신 결과에 오류 글
    assert cfg.chat_model is None and cfg.chat_model_error == "No models with the chat role found in the configured assistant"  # 대화 모델이 없고 오류 글이 있음
    _w(tmp_path / "e.yaml", "name: t\nversion: \"1\"\n")    # 모델이 아예 없는 설정 작성
    cfg2 = L.load_config("./e.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path), headless=False)  # 읽기
    assert cfg2.chat_model_error == "No models found in the configured assistant"  # 오류 글 확인


def test_chat_model_rules_and_persisted(tmp_path):          # 대화 모델 고르기와 저장된 선택 시험
    """roles 없음 = chat. globalContext cliSelectedModel 이 chat 모델 중에 있으면 그것."""
    y = ("name: t\nversion: \"1\"\nmodels:\n  - name: a\n    provider: o\n    model: x\n    roles: [edit]\n"  # 역할이 다른 모델 세 개를 가진 설정
         "  - name: b\n    provider: o\n    model: y\n  - name: c\n    provider: o\n    model: z\n    roles: [chat]\n")  # (설정 이어짐)
    _w(tmp_path / "c.yaml", y)                              # 설정 작성
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 설정 읽기
    assert cfg.chat_model.name == "b"                       # 역할 없는 b 가 첫 chat 모델
    _w(tmp_path / "gd" / "index" / "globalContext.json", json.dumps({"cliSelectedModel": "c"}))  # 저장된 선택(c) 작성
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 다시 읽기
    assert cfg.chat_model.name == "c"                       # 저장된 선택이 우선


def test_numbers_like_js(tmp_path):                         # JS 식 숫자 표기 시험
    """temperature: 1.0 은 JS 처럼 1(요청 본문 JSON 이 같아지게)."""
    _w(tmp_path / "c.yaml", MODEL_YAML + "    defaultCompletionOptions: {temperature: 1.0, maxTokens: 512, topP: 0.9}\n")  # 실수 옵션이 든 설정 작성
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))  # 설정 읽기
    dco = cfg.chat_model.default_completion_options         # 완료 옵션
    assert json.dumps(dco) == '{"maxTokens": 512, "temperature": 1, "topP": 0.9}'   # 키 순서도 스키마 순서


# ───────────────────────── .continue/rules ─────────────────────────
def test_markdown_rules_loading(tmp_path):                  # 마크다운 규칙 읽기 시험
    """항상 적용 규칙만(invokable·globs·regex 제외, alwaysApply: true 는 globs 있어도 포함). 이름은 앞머리 name 또는 경로 끝 두 조각."""
    w, gd = tmp_path / "w", tmp_path / "gd"                 # 작업 폴더와 전역 폴더
    _w(w / ".continue" / "rules" / "a.md", "Rule A body\n")  # 앞머리 없는 규칙
    _w(w / ".continue" / "rules" / "sub" / "b.md", "---\nglobs: '*.py'\n---\nB\n")  # globs 가 있는 규칙(제외)
    _w(w / ".continue" / "rules" / "sub" / "c.md", "---\nglobs: '*.py'\nalwaysApply: true\n---\nC\n")  # alwaysApply 가 true 인 규칙(포함)
    _w(w / ".continue" / "rules" / "d.md", "---\ninvokable: true\n---\nD\n")  # 호출형 규칙(제외)
    _w(w / ".continue" / "rules" / "e.txt", "not md")       # md 가 아닌 파일(제외)
    _w(gd / "rules" / "g.md", "---\nname: Global\n---\n\nG body\n")  # 전역 규칙
    rules = L.load_markdown_rules_with_metadata(str(w), str(gd))  # 규칙 읽기
    by_name = {r["name"]: r for r in rules}                 # 이름별 사전
    assert set(by_name) == {"a", "sub/c", "Global"}         # 포함된 규칙 이름 확인
    assert by_name["a"]["rule"] == "Rule A body\n"           # 앞머리 없으면 trim 하지 않음
    assert by_name["sub/c"]["rule"] == "C" and by_name["sub/c"]["globs"] == "*.py"  # alwaysApply 규칙 내용 확인
    assert by_name["Global"]["rule"] == "G body"            # 전역 규칙 내용 확인
    _w(w / "c.yaml", MODEL_YAML + "rules:\n  - C\n")         # 같은 글이 이미 있으면 중복 추가 안 함
    cfg = L.load_config("./c.yaml", cwd=w, global_dir=gd, environ=_env(tmp_path))  # 설정과 함께 읽기
    assert cfg.rule_texts.count("C") == 1 and "Rule A body\n" in cfg.rule_texts  # 중복 없이 한 번만 들어감
