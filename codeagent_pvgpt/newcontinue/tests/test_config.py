# tests/test_config.py — ncn/config 시험(paths·env·secrets·schema·loader). cn CLIPlatformClient.test.ts·services/ConfigService.test.ts 대표 사례 +
#   cn 실제 코드(packages/config-yaml/src 를 tsx 로 직접 실행, dotenv 16.6.1 실행)에서 얻은 기대값
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # 기대값 비교
import pathlib                                               # 경로

import pytest                                                # 시험 도구

from ncn.config import env as E                              # dotenv
from ncn.config import loader as L                           # 설정 읽기
from ncn.config import paths as P                            # 전역 폴더
from ncn.config import secrets as S                          # 비밀값
from ncn.config.schema import yaml_parse                     # YAML 1.2


def _w(p: pathlib.Path, text: str) -> pathlib.Path:
    """파일 쓰기(상위 폴더 생성)."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _env(tmp_path, **kw) -> dict:
    """깨끗한 환경(HOME 은 임시 폴더)."""
    return {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home"), **kw}


MODEL_YAML = "name: t\nversion: 0.0.1\nmodels:\n  - name: m\n    provider: openai\n    model: fake-model\n"


# ───────────────────────── paths ─────────────────────────
def test_global_dir_rules(tmp_path):
    """CONTINUE_GLOBAL_DIR: 절대경로 그대로, 상대경로는 cwd 기준, 빈 글은 무시(~/.continue)."""
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "/abs/x", "HOME": "/h"}, cwd="/w") == pathlib.Path("/abs/x")
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "rel/x", "HOME": "/h"}, cwd="/w") == pathlib.Path("/w/rel/x")
    assert P.global_dir({"CONTINUE_GLOBAL_DIR": "", "HOME": "/h"}, cwd="/w") == pathlib.Path("/h/.continue")
    assert P.continue_home({"CONTINUE_GLOBAL_DIR": "rel/x"}) == "rel/x"   # env.ts 형태는 그대로


# ───────────────────────── dotenv 16.6.1(node 로 얻은 결과) ─────────────────────────
DOTENV_CASES = [
    ("A=1\nB = two \nC='single # not comment'\nD=\"dq\\nnl\"\nE=`bt`\nexport F=exp\nG=val # comment\nH=\nI\nJ: colon\n# c\nK=\"multi\nline\"\nL=a=b\r\nM=last",
     {"A": "1", "B": "two", "C": "single # not comment", "D": "dq\nnl", "E": "bt", "F": "exp", "G": "val", "H": "", "J": "colon",
      "K": "multi\nline", "L": "a=b", "M": "last"}),
    ("\ufeffBOM=1\nA.B-C=dots", {"BOM": "1", "A.B-C": "dots"}),
    ("X='it\\'s'\nY=\"a\\\"b\"\nZ=  spaced  ", {"X": "it\\'s", "Y": "a\\\"b", "Z": "spaced"}),
    ("DUP=1\nDUP=2", {"DUP": "2"}),
    ("Q=\"unterminated\nR=after", {"Q": "\"unterminated", "R": "after"}),
]


@pytest.mark.parametrize("src,expected", DOTENV_CASES)
def test_parse_dotenv(src, expected):
    assert E.parse_dotenv(src) == expected


def test_apply_dotenv_does_not_override(tmp_path):
    """dotenv.config — 이미 있는 키(빈 값 포함)는 그대로."""
    _w(tmp_path / ".env", "A=file\nB=file\nC=file\n")
    environ = {"A": "shell", "B": ""}
    E.apply_dotenv(environ, str(tmp_path))
    assert environ == {"A": "shell", "B": "", "C": "file"}


# ───────────────────────── YAML 1.2 ─────────────────────────
def test_yaml_core_schema():
    """yaml 라이브러리(1.2 core)와 같은 형식 판정 — node 결과와 비교."""
    doc = yaml_parse("a: 012\nb: 0o17\nc: yes\nd: 1e3\ne: 1_000\nf: 2020-01-01\n<<: x\n1: z\nnull: n\ng: .5\nh: -0x1\ni: 1.0")
    assert doc == {"a": 12, "b": 15, "c": "yes", "d": 1000, "e": "1_000", "f": "2020-01-01", "<<": "x", "1": "z", "": "n",
                   "g": 0.5, "h": "-0x1", "i": 1}
    assert yaml_parse("") is None


# ───────────────────────── secrets(CLIPlatformClient.test.ts) ─────────────────────────
def _client(tmp_path, environ) -> S.LocalPlatformClient:
    return S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path / "w"))


def test_resolve_empty_no_hub(tmp_path):
    c = _client(tmp_path, {})
    assert c.resolve_fqsns([]) == [] and c.hub_calls == 0 and c.warnings == []


def test_resolve_order(tmp_path):
    """환경변수 > <전역>/.env > <cwd>/.continue/.env > <cwd>/.env, 빈 값은 못 찾은 것."""
    _w(tmp_path / "gd" / ".env", "A=home\nB=home\n")
    _w(tmp_path / "w" / ".continue" / ".env", "B=wsc\nC=wsc\n")
    _w(tmp_path / "w" / ".env", "C=ws\nD=ws\nE=\n")
    c = _client(tmp_path, {"A": "env", "Z": ""})
    names = ["A", "B", "C", "D", "Z", "E"]
    res = c.resolve_fqsns([{"packageSlugs": [], "secretName": n} for n in names])
    assert [r["value"] if r else None for r in res] == ["env", "home", "wsc", "ws", None, None]
    assert c.hub_calls == 1                                  # 못 찾은 것이 있으면 Hub 시도 → 실패 경고
    assert c.warnings == ["Error resolving FQSNs through API: The request failed and the interceptors did not return an alternative response"]


def test_dotenv_trap_cwd_env_beats_home_env(tmp_path):
    """cn 함정: dotenv.config 가 <cwd>/.env 를 먼저 환경변수에 넣으므로 ~/.continue/.env 보다 이긴다."""
    gd = tmp_path / "gd"
    _w(gd / ".env", "K=home\n")
    w = tmp_path / "w"
    _w(w / ".env", "K=cwd\n")
    _w(w / "c.yaml", MODEL_YAML + "    apiKey: ${{ secrets.K }}\n")
    cfg = L.load_config("./c.yaml", cwd=w, global_dir=gd, environ=_env(tmp_path))
    assert cfg.chat_model.api_key == "cwd"


def test_fqsn_roundtrip():
    assert S.decode_fqsn("//X") == {"packageSlugs": [{"ownerSlug": "", "packageSlug": ""}], "secretName": "X"}
    assert S.encode_fqsn(S.decode_fqsn("////X")) == "////X"
    with pytest.raises(ValueError, match="Invalid FQSN format"):
        S.decode_fqsn("a/X")


# ───────────────────────── 원천 결정·Hub 대체 ─────────────────────────
def test_no_config_is_hub_failure(tmp_path):
    """설정 파일이 없으면 cn은 Hub 기본 설정을 받으려다 실패 — 같은 글."""
    with pytest.raises(L.ConfigError) as ei:
        L.load_config(None, cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    assert str(ei.value) == "The request failed and the interceptors did not return an alternative response"
    assert ei.value.hub is True


def test_local_global_config_used(tmp_path):
    """--config 가 없으면 <전역>/config.yaml."""
    _w(tmp_path / "gd" / "config.yaml", MODEL_YAML)
    cfg = L.load_config(None, cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    assert cfg.source == {"type": "local-config-yaml"} and cfg.chat_model.model == "fake-model"


def test_slug_config(tmp_path):
    with pytest.raises(L.ConfigError, match="^Slug-based package resolution is not supported$"):
        L.load_config("owner/pkg", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))
    with pytest.raises(L.ConfigError) as ei:
        L.load_config("abc", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))
    assert str(ei.value) == 'Invalid assistant slug format. Expected "owner/package", got: abc'


def test_missing_file_node_message(tmp_path):
    with pytest.raises(L.ConfigError) as ei:
        L.load_config("./nope.yaml", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path))
    assert str(ei.value) == "ENOENT: no such file or directory, open 'nope.yaml'"   # rootPath "." 와 합쳐져 정규화된 경로


def test_tui_precheck_wrapper(tmp_path):
    """대화형: onboarding 이 먼저 읽어 보고 `Failed to load config from "…": Error: …` 로 감싼다."""
    with pytest.raises(L.ConfigError) as ei:
        L.load_config("owner/pkg", cwd=tmp_path, global_dir=tmp_path, environ=_env(tmp_path), headless=False)
    assert str(ei.value) == 'Failed to load config from "owner/pkg": Error: Slug-based package resolution is not supported'


def test_is_file_path():
    assert L.is_file_path("./a") and L.is_file_path("~/a") and L.is_file_path("C:\\x") and L.is_file_path("x.yml")
    assert not L.is_file_path("owner/pkg")


# ───────────────────────── 검증 오류 글(cn 실제 실행 결과) ─────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("name: 1\nversion: 0.0.1\nmodels:\n  - name: m\n    model: x\n  - provider: openai\n    name: m\n    model: [1]\n    roles: [chatx]\n"
     "mcpServers:\n  - name: s\n    connectionTimeout: 0\n    command: x\ndata:\n  - name: d\n    destination: x\n    schema: 1.0\n",
     "Failed to parse config: name: Expected string, received number, models.0: Invalid input, models.1: Invalid input, data.0: Invalid input, "
     "mcpServers.0.connectionTimeout: Number must be greater than 0"),
    ("name: t\nversion: \"1\"\nmodels: notalist\nrules: [1]\nmetadata: {a: 1}\n",
     "Failed to parse config: metadata.a: Expected string, received number, models: Expected array, received string, rules.0: Invalid input"),
    ("", "Failed to parse config: Expected object, received null"),
    ("name: t\nversion: \"1\"\ndata:\n  - name: d\n    destination: x\n    schema: \"1.0\"\n",
     "Failed to parse config: data.0.schema: Version must follow semver format, e.g. 0.2.0"),
])
def test_validation_messages(tmp_path, text, expected):
    _w(tmp_path / "c.yaml", text)
    with pytest.raises(L.ConfigError) as ei:
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    assert str(ei.value) == expected


def test_numeric_secret_breaks_like_cn(tmp_path):
    """비밀값이 "12345" 면 cn은 YAML 글에 끼운 뒤 다시 읽어 숫자가 되어 검증 실패."""
    _w(tmp_path / "c.yaml", "name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: openai\n    model: x\n    apiKey: ${{ secrets.NUM }}\n")
    with pytest.raises(L.ConfigError) as ei:
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path, NUM="12345"))
    assert str(ei.value) == "Failed to parse config: models.0.apiKey: Expected string, received number"


# ───────────────────────── 전체 unroll 결과(cn 실제 실행 결과와 같음) ─────────────────────────
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
BLOCK1 = "name: b\nversion: 1.0.0\nmodels:\n  - name: bm\n    provider: openai\n    model: m2\n    apiKey: ${{ inputs.KEY }}\n"
RULE1 = "---\nname: R\nalwaysApply: true\n---\n\nRule body\n"
D01_EXPECTED = json.loads("""{"name": "t", "version": "0.0.1", "models": [{"name": "m", "model": "fake-model", "apiKey": "sekret", "apiBase": "${{ secrets.//UNSET }}", "capabilities": ["tool_use", "custom"], "requestOptions": {"headers": {"Authorization": "Bearer sekret", "X-Q": "'sekret'", "X-B": "yes", "X-S": "a b", "sekret": "keyed"}}, "chatOptions": {"toolOverrides": {"Bash": {"description": "hi", "disabled": false}}}, "env": {"A": 1, "B": true, "C": "x"}, "provider": "openai"}, {"name": "bm", "model": "m2", "apiKey": "${{ secrets.////UNSET2 }}", "provider": "openai"}], "context": [{"provider": "code"}], "data": [{"name": "d", "destination": "x", "schema": "0.2.0", "level": "all"}], "mcpServers": [{"name": "remote", "url": "https://example.com/mcp", "type": "streamable-http", "apiKey": "${{ secrets.//UNSET }}"}], "rules": ["This is a very long rule that has a secret sekret embedded somewhere in the middle of a long line so folding happens maybe", {"name": "R", "rule": "Rule body", "alwaysApply": true, "sourceFile": "./rule1.md"}, null, null], "prompts": [{"name": "p", "prompt": "do ${{ inputs.X }}"}], "docs": [{"name": "doc", "startUrl": "http://a"}]}""")
D01_ERRORS = [{"fatal": False, "message": "ENOENT: no such file or directory, open 'missing_rule.md':\n./missing_rule.md"},
              {"fatal": False, "message": "Slug-based package resolution is not supported:\nsomeone/rule"}]


def test_unroll_matches_cn(tmp_path):
    """uses 블록·inputs·secrets(FQSN)·override·중복 제거·키 순서까지 cn unrollAssistant 결과와 같다."""
    _w(tmp_path / "d01.yaml", D01)
    _w(tmp_path / "block1.yaml", BLOCK1)
    _w(tmp_path / "rule1.md", RULE1)
    environ = _env(tmp_path, K="sekret", BOOL="yes", SPACE="a b")
    warnings: list[str] = []
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path), warnings=warnings)
    reg = L.RegistryClient(".", str(tmp_path))
    raw = reg.get_content({"uriType": "file", "fileUri": "./d01.yaml"})
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./d01.yaml"}, raw, reg, inject_blocks=[],
                                                client=client, environ=environ, warnings=warnings)
    assert json.dumps(cfg) == json.dumps(D01_EXPECTED)       # 키 순서까지
    assert errs == D01_ERRORS
    assert "Error resolving FQSNs through API: The request failed and the interceptors did not return an alternative response" in warnings


def test_load_config_merged_and_warning(tmp_path):
    """load_config: 실패 블록(null)은 합치기에서 빠지고, 비치명 오류는 'Warning: \\n- …,\\n- …' 한 줄로."""
    _w(tmp_path / "d01.yaml", D01)
    _w(tmp_path / "block1.yaml", BLOCK1)
    _w(tmp_path / "rule1.md", RULE1)
    cfg = L.load_config("./d01.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path, K="sekret", BOOL="yes", SPACE="a b"))
    assert [m.name for m in cfg.models] == ["m", "bm"]
    assert cfg.chat_model.name == "m" and cfg.chat_model.api_key == "sekret"
    assert cfg.rule_texts == ["This is a very long rule that has a secret sekret embedded somewhere in the middle of a long line so folding happens maybe",
                              "Rule body"]
    assert ("Warning: \n- ENOENT: no such file or directory, open 'missing_rule.md':\n./missing_rule.md,"
            "\n- Slug-based package resolution is not supported:\nsomeone/rule") in cfg.warnings
    assert cfg.env == {}                                     # mergeUnrolledAssistants: {...undefined} = {}


def test_block_errors_nonfatal(tmp_path):
    """블록 항목 수 오류·inputs 누락 → 항목 null, 오류 글 cn 그대로."""
    _w(tmp_path / "block1.yaml", BLOCK1)
    _w(tmp_path / "bad.yaml", "name: b\nversion: 1.0.0\nmodels:\n  - name: a\n    provider: o\n    model: x\n  - name: b\n    provider: o\n    model: y\n")
    _w(tmp_path / "c.yaml", "name: t\nversion: \"1\"\nmodels:\n  - uses: ./bad.yaml\n  - uses: ./block1.yaml\n")
    reg = L.RegistryClient(".", str(tmp_path))
    environ = _env(tmp_path)
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path))
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./c.yaml"}, reg.get_content({"uriType": "file", "fileUri": "./c.yaml"}),
                                                reg, inject_blocks=[], client=client, environ=environ, warnings=[])
    assert cfg == {"name": "t", "version": "1", "models": [None, None]}
    assert errs == [{"fatal": False, "message": "Failed to parse block: models: Array must contain exactly 1 element(s).\n> \"./bad.yaml\""},
                    {"fatal": False, "message": "Missing required input(s) for block \"/\": KEY. Please provide these values in the \"with\" block..\n> \"./block1.yaml\""}]


def test_injected_blocks_match_cn(tmp_path):
    """주입 블록(--rule 파일·--mcp 파일·슬러그): 같은 이름은 버리고, 슬러그 오류 글은 '[object Object]'(cn 그대로)."""
    _w(tmp_path / "e01.yaml", "name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: o\n    model: x\nrules:\n  - name: R\n    rule: existing\n")
    _w(tmp_path / "rule1.md", RULE1)
    _w(tmp_path / "d03.md", "---\nname: MDR\nglobs: \"*.ts\"\n---\n\nMarkdown config body\n")
    _w(tmp_path / "mcp.yaml", "name: mcpb\nversion: 1.0.0\nmcpServers:\n  - name: inj\n    command: run\n    env: {T: \"${{ inputs.TOK }}\"}\n")
    inj = [{"uriType": "file", "fileUri": "./rule1.md"}, {"uriType": "file", "fileUri": "./d03.md"}, {"uriType": "file", "fileUri": "./mcp.yaml"},
           {"uriType": "slug", "fullSlug": {"ownerSlug": "o", "packageSlug": "x", "versionSlug": "latest"}}, {"uriType": "file", "fileUri": "./nope.md"}]
    environ = _env(tmp_path, TOK="tk")
    client = S.LocalPlatformClient(environ=environ, continue_home=str(tmp_path / "gd"), cwd=str(tmp_path))
    reg = L.RegistryClient(".", str(tmp_path))
    cfg, errs = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./e01.yaml"}, reg.get_content({"uriType": "file", "fileUri": "./e01.yaml"}),
                                                reg, inject_blocks=inj, client=client, environ=environ, warnings=[])
    assert cfg == {"name": "t", "version": "1", "models": [{"name": "m", "model": "x", "provider": "o"}],
                   "mcpServers": [{"name": "inj", "sourceFile": "./mcp.yaml", "command": "run", "env": {"T": "tk"}}],
                   "rules": [{"name": "R", "rule": "existing"}, {"name": "MDR", "rule": "Markdown config body", "globs": "*.ts", "sourceFile": "./d03.md"}]}
    assert errs == [{"fatal": False, "message": "Slug-based package resolution is not supported.\n> [object Object]"},
                    {"fatal": False, "message": "ENOENT: no such file or directory, open 'nope.md'.\n> ./nope.md"}]


def test_markdown_config_file(tmp_path):
    """.yaml 이 아닌 파일은 YAML 검증 실패 시 마크다운 규칙으로."""
    _w(tmp_path / "d03.md", "---\nname: MDR\nglobs: \"*.ts\"\n---\n\nMarkdown config body\n")
    reg = L.RegistryClient(".", str(tmp_path))
    environ = _env(tmp_path)
    client = S.LocalPlatformClient(environ=environ, continue_home="/x", cwd=str(tmp_path))
    cfg, _ = L.unroll_assistant_from_content({"uriType": "file", "fileUri": "./d03.md"}, reg.get_content({"uriType": "file", "fileUri": "./d03.md"}),
                                             reg, inject_blocks=[], client=client, environ=environ, warnings=[])
    assert cfg == {"name": "MDR", "version": "1.0.0", "rules": [{"name": "MDR", "rule": "Markdown config body", "globs": "*.ts", "sourceFile": "./d03.md"}]}


# ───────────────────────── ConfigService 주입(--rule/--prompt/--mcp) ─────────────────────────
def test_cli_injections(tmp_path):
    _w(tmp_path / "c.yaml", MODEL_YAML + "rules:\n  - config rule\n")
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path),
                        rules=["use tabs please"], prompts=["a prompt text"], mcp=["https://Example.COM:8443/mcp", "not a url"])
    assert cfg.rule_texts == ["use tabs please", "a prompt text", "config rule"]   # 주입이 앞(incoming first), --prompt 글도 규칙(cn 그대로)
    assert cfg.mcp_servers == [{"name": "example.com", "url": "https://Example.COM:8443/mcp"}]


def test_string_rule_detection():
    assert L.is_string_rule("use tabs") and L.is_string_rule("plain")
    assert not L.is_string_rule("./r.md") and not L.is_string_rule("owner/pkg") and not L.is_string_rule("a\\b")


# ───────────────────────── 대화 모델 고르기 ─────────────────────────
def test_headless_no_chat_model(tmp_path):
    _w(tmp_path / "c.yaml", MODEL_YAML + "    roles: [subagent]\n")
    with pytest.raises(L.ConfigError, match=r"^No model specified in headless mode \(and failed to load default model\)$"):
        L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path), headless=False)
    assert cfg.chat_model is None and cfg.chat_model_error == "No models with the chat role found in the configured assistant"
    _w(tmp_path / "e.yaml", "name: t\nversion: \"1\"\n")
    cfg2 = L.load_config("./e.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path), headless=False)
    assert cfg2.chat_model_error == "No models found in the configured assistant"


def test_chat_model_rules_and_persisted(tmp_path):
    """roles 없음 = chat. globalContext cliSelectedModel 이 chat 모델 중에 있으면 그것."""
    y = ("name: t\nversion: \"1\"\nmodels:\n  - name: a\n    provider: o\n    model: x\n    roles: [edit]\n"
         "  - name: b\n    provider: o\n    model: y\n  - name: c\n    provider: o\n    model: z\n    roles: [chat]\n")
    _w(tmp_path / "c.yaml", y)
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    assert cfg.chat_model.name == "b"
    _w(tmp_path / "gd" / "index" / "globalContext.json", json.dumps({"cliSelectedModel": "c"}))
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    assert cfg.chat_model.name == "c"


def test_numbers_like_js(tmp_path):
    """temperature: 1.0 은 JS 처럼 1(요청 본문 JSON 이 같아지게)."""
    _w(tmp_path / "c.yaml", MODEL_YAML + "    defaultCompletionOptions: {temperature: 1.0, maxTokens: 512, topP: 0.9}\n")
    cfg = L.load_config("./c.yaml", cwd=tmp_path, global_dir=tmp_path / "gd", environ=_env(tmp_path))
    dco = cfg.chat_model.default_completion_options
    assert json.dumps(dco) == '{"maxTokens": 512, "temperature": 1, "topP": 0.9}'   # 키 순서도 스키마 순서


# ───────────────────────── .continue/rules ─────────────────────────
def test_markdown_rules_loading(tmp_path):
    """항상 적용 규칙만(invokable·globs·regex 제외, alwaysApply: true 는 globs 있어도 포함). 이름은 앞머리 name 또는 경로 끝 두 조각."""
    w, gd = tmp_path / "w", tmp_path / "gd"
    _w(w / ".continue" / "rules" / "a.md", "Rule A body\n")
    _w(w / ".continue" / "rules" / "sub" / "b.md", "---\nglobs: '*.py'\n---\nB\n")
    _w(w / ".continue" / "rules" / "sub" / "c.md", "---\nglobs: '*.py'\nalwaysApply: true\n---\nC\n")
    _w(w / ".continue" / "rules" / "d.md", "---\ninvokable: true\n---\nD\n")
    _w(w / ".continue" / "rules" / "e.txt", "not md")
    _w(gd / "rules" / "g.md", "---\nname: Global\n---\n\nG body\n")
    rules = L.load_markdown_rules_with_metadata(str(w), str(gd))
    by_name = {r["name"]: r for r in rules}
    assert set(by_name) == {"a", "sub/c", "Global"}
    assert by_name["a"]["rule"] == "Rule A body\n"           # 앞머리 없으면 trim 하지 않음
    assert by_name["sub/c"]["rule"] == "C" and by_name["sub/c"]["globs"] == "*.py"
    assert by_name["Global"]["rule"] == "G body"
    _w(w / "c.yaml", MODEL_YAML + "rules:\n  - C\n")         # 같은 글이 이미 있으면 중복 추가 안 함
    cfg = L.load_config("./c.yaml", cwd=w, global_dir=gd, environ=_env(tmp_path))
    assert cfg.rule_texts.count("C") == 1 and "Rule A body\n" in cfg.rule_texts
