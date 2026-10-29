# tests/test_registry.py — 내장 도구 목록 구성 시험(cn tools/index.tsx getAllAvailableTools·index.test.ts·applyToolOverrides.test.ts)
# 순서·MultiEdit/Edit 선택(모델 이름)·rg 유무·헤드리스 Exit·--id 도구·베타·toolOverrides
import sys                                                   # argv 바꾸기

import pytest                                                # 시험 틀

for _m in ("ncn.tools.read_file", "ncn.tools.write_file", "ncn.tools.edit", "ncn.tools.multi_edit", "ncn.tools.list_files", "ncn.tools.truncate"):
    pytest.importorskip(_m)                                  # 파일 도구(다른 작업자)가 아직 없으면 건너뜀

from ncn.tools import registry                               # 시험 대상
from ncn.tools.base import Tool                              # 공용 형식

BASE = ["Read", "Write", "List", "Bash", "Fetch", "Checklist", "CheckBackgroundJob", "AskQuestion"]   # 항상 있는 것(cn 순서)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    """베타 끄기, --id 없애기, 내장 검색 끄기."""
    registry.set_beta_subagent_tool_enabled(False)
    registry.set_beta_upload_artifact_tool_enabled(False)
    monkeypatch.setattr(sys, "argv", ["ncn"])
    monkeypatch.delenv("NCN_BUILTIN_SEARCH", raising=False)
    yield
    registry.set_beta_subagent_tool_enabled(False)
    registry.set_beta_upload_artifact_tool_enabled(False)


def names(tools):
    """이름 목록."""
    return [t.name for t in tools]


def build(tmp_path, **kw):
    """기본 인자로 목록 만들기."""
    args = dict(model_name="gemma4-26b-32k", headless=False, mode="normal", cwd=tmp_path, global_dir=tmp_path / "g", ripgrep_installed=False)
    args.update(kw)
    return registry.get_builtin_tools(**args)


def test_order_non_capable_model_without_rg(tmp_path):
    """gemma(능력 없음) + rg 없음 → Edit, Search 없음, Skills 가 끝."""
    assert names(build(tmp_path)) == BASE + ["Edit", "Skills"]


def test_order_capable_model_with_rg_headless(tmp_path):
    """claude + rg 있음 + 헤드리스 → Search, MultiEdit, Exit."""
    assert names(build(tmp_path, model_name="claude-sonnet", headless=True, ripgrep_installed=True)) == BASE + ["Search", "MultiEdit", "Exit", "Skills"]


def test_builtin_search_extra(tmp_path, monkeypatch):
    """[추가] rg 가 없어도 NCN_BUILTIN_SEARCH=1 이면 Search 를 넣음(같은 자리)."""
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")
    assert names(build(tmp_path)) == BASE + ["Search", "Edit", "Skills"]


def test_agent_id_and_betas(tmp_path):
    """--id 가 있으면 ReportFailure(+베타 UploadArtifact), 베타 Subagent 는 Exit 뒤·Skills 앞."""
    registry.set_beta_upload_artifact_tool_enabled(True)
    registry.set_beta_subagent_tool_enabled(True)
    got = names(build(tmp_path, agent_id="A1", headless=True, ripgrep_installed=True,
                      subagent_models=[{"name": "explorer", "roles": ["subagent"], "chatOptions": {"baseSystemMessage": "x"}}]))
    assert got == BASE + ["Search", "ReportFailure", "UploadArtifact", "Edit", "Exit", "Subagent", "Skills"]
    sys.argv = ["ncn", "--id", "B2"]                         # 명령줄 --id 도 읽음(cn getAgentIdFromArgs)
    registry.set_beta_upload_artifact_tool_enabled(False)
    assert "ReportFailure" in names(build(tmp_path)) and "UploadArtifact" not in names(build(tmp_path))


def test_model_field_also_checked(tmp_path):
    """name 이 아니라 model 필드가 맞아도 MultiEdit(cn: name OR model)."""
    assert "MultiEdit" in names(build(tmp_path, model_name="my-local", model="qwen2.5-coder"))
    with pytest.raises(Exception, match="Model service is not initialized"):
        build(tmp_path, model_name="")


@pytest.mark.parametrize("name,model,expected", [
    ("gemma4-26b-32k", None, False), ("gemma3", None, False), ("Claude-3", None, True), ("gpt-4o", None, True),
    ("o3-mini", None, True), ("foo2", None, True), ("bar2", None, False), ("llama3", None, True), ("Mistral-small", None, True),
    ("local", "Qwen3", True), ("deepseek", "deepseek-coder", False), ("kimi-k2", None, True), ("pro1", None, True),   # 'pro1' 은 /o\d/ — cn 그대로
])
def test_is_model_capable(name, model, expected):
    """isModelCapable 패턴(gemma 는 Edit)."""
    assert registry.is_model_capable("openai", name, model) is expected


def test_tool_shapes(tmp_path):
    """목록 속 도구는 Tool 이고, 요청 본문 변환은 type→required→properties 순서(required 없으면 키 없음)."""
    tools = build(tmp_path, headless=True)
    assert all(isinstance(t, Tool) for t in tools)
    exit_tool = next(t for t in tools if t.name == "Exit")
    assert registry.convert_tool_to_chat_completion_tool(exit_tool) == {
        "type": "function", "function": {"name": "Exit", "description": "Exit the current process with status code 1, indicating a failure or error",
                                         "parameters": {"type": "object", "properties": {}}}}
    bash = next(t for t in tools if t.name == "Bash")
    assert list(registry.convert_tool_to_chat_completion_tool(bash)["function"]["parameters"]) == ["type", "required", "properties"]
    assert str(tmp_path) in bash.description                 # 작업 폴더


def test_tool_overrides_on_tools(tmp_path):
    """toolOverrides — disabled 는 빼고 description 은 바꿈(원본 객체는 그대로). 목록 모양도 받음."""
    tools = build(tmp_path, tool_overrides={"Fetch": {"disabled": True}, "Bash": {"description": "custom"}})
    assert "Fetch" not in names(tools)
    assert next(t for t in tools if t.name == "Bash").description == "custom"
    assert "custom" not in next(t for t in build(tmp_path) if t.name == "Bash").description
    tools2 = build(tmp_path, tool_overrides=[{"name": "Checklist", "disabled": True}])
    assert "Checklist" not in names(tools2)


_MOCK = [{"type": "function", "function": {"name": "read_file", "description": "Read a file"}},
         {"type": "function", "function": {"name": "write_file", "description": "Write a file"}}]


def test_apply_chat_completion_tool_overrides():
    """cn applyToolOverrides.test.ts 사례."""
    f = registry.apply_chat_completion_tool_overrides
    assert f(_MOCK, None) == _MOCK and f(_MOCK, {}) == _MOCK
    r = f(_MOCK, {"read_file": {"description": "Custom read description"}})
    assert r[0]["function"]["description"] == "Custom read description" and r[1]["function"]["description"] == "Write a file"
    r = f(_MOCK, {"read_file": {"disabled": True}})
    assert len(r) == 1 and r[0]["function"]["name"] == "write_file"
    r = f(_MOCK, {"read_file": {"description": "Custom read"}, "write_file": {"description": "Custom write"}})
    assert [x["function"]["description"] for x in r] == ["Custom read", "Custom write"]
    assert f(_MOCK, {"non_existent_tool": {"description": "Should be ignored"}}) == _MOCK
    f(_MOCK, {"read_file": {"description": "Modified description"}})
    assert _MOCK[0]["function"]["description"] == "Read a file"   # 원본 안 바뀜


def test_display_name_and_extract_tool_calls():
    """cn index.test.ts — getToolDisplayName·extractToolCalls."""
    assert registry.get_tool_display_name("Read") == "Read"
    assert registry.get_tool_display_name("AskQuestion") == "Ask Question"
    assert registry.get_tool_display_name("unknown_tool") == "unknown_tool" and registry.get_tool_display_name("") == ""
    ex = registry.extract_tool_calls
    assert ex('Here\'s the result:\n<tool>{"name": "Read", "arguments": {"filepath": "/path/to/file.txt"}}</tool>\nDone.') == [
        {"name": "Read", "arguments": {"filepath": "/path/to/file.txt"}}]
    assert ex("This is just regular text with no tool calls.") == [] and ex("") == []
    bad = ('<tool>{"name": "Read", "arguments": {"filepath": "/file.txt"}}</tool>\n<tool>{"name": "invalid_json", "arguments": {malformed json}}</tool>\n'
           '<tool>{"name": "Write", "arguments": {"filepath": "/file2.txt", "content": "test"}}</tool>')
    assert [c["name"] for c in ex(bad)] == ["Read", "Write"]
    assert ex('<tool>{"arguments": {"filepath": "/file.txt"}}</tool><tool>{"name": "Read"}</tool>') == []
    assert ex('<tool>{"name": "List", "arguments": {}}</tool>') == [{"name": "List", "arguments": {}}]   # {} 는 JS 참값
    assert ex('<tool>{"name": "exit", "arguments": null}</tool>') == []
    assert ex('<tool>{"name": "Write", "arguments": {"filepath": "/file.txt", "content": "Some content with <tool> tags inside"}}</tool>') == [
        {"name": "Write", "arguments": {"filepath": "/file.txt", "content": "Some content with <tool> tags inside"}}]
