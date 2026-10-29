# tests/test_registry.py — 내장 도구 목록 구성 시험(cn tools/index.tsx getAllAvailableTools·index.test.ts·applyToolOverrides.test.ts)
# 순서·MultiEdit/Edit 선택(모델 이름)·rg 유무·헤드리스 Exit·--id 도구·베타·toolOverrides
import sys                                                   # argv 바꾸기

import pytest                                                # 시험 틀

for _m in ("ncn.tools.read_file", "ncn.tools.write_file", "ncn.tools.edit", "ncn.tools.multi_edit", "ncn.tools.list_files", "ncn.tools.truncate"):  # 파일 도구 모듈 목록을 돌며 확인
    pytest.importorskip(_m)                                  # 파일 도구(다른 작업자)가 아직 없으면 건너뜀

from ncn.tools import registry                               # 시험 대상
from ncn.tools.base import Tool                              # 공용 형식

BASE = ["Read", "Write", "List", "Bash", "Fetch", "Checklist", "CheckBackgroundJob", "AskQuestion"]   # 항상 있는 것(cn 순서)


@pytest.fixture(autouse=True)                               # 모든 시험 앞에 자동 적용되는 초기화 픽스처
def _reset(monkeypatch, tmp_path):                          # _reset: 베타 플래그·argv·환경변수 정리
    """베타 끄기, --id 없애기, 내장 검색 끄기."""
    registry.set_beta_subagent_tool_enabled(False)          # 베타 Subagent 도구 끔
    registry.set_beta_upload_artifact_tool_enabled(False)   # 베타 UploadArtifact 도구 끔
    monkeypatch.setattr(sys, "argv", ["ncn"])               # argv 에서 --id 제거(에이전트 ID 없음)
    monkeypatch.delenv("NCN_BUILTIN_SEARCH", raising=False)  # 내장 검색 환경변수 제거
    yield                                                   # 시험 실행 구간
    registry.set_beta_subagent_tool_enabled(False)          # 시험 뒤에도 Subagent 베타 원복
    registry.set_beta_upload_artifact_tool_enabled(False)   # 시험 뒤에도 UploadArtifact 베타 원복


def names(tools):                                           # names: 도구 목록을 이름 목록으로
    """이름 목록."""
    return [t.name for t in tools]                          # 각 도구의 name 만 모음


def build(tmp_path, **kw):                                  # build: 기본 인자로 도구 목록 생성
    """기본 인자로 목록 만들기."""
    args = dict(model_name="gemma4-26b-32k", headless=False, mode="normal", cwd=tmp_path, global_dir=tmp_path / "g", ripgrep_installed=False)  # 기본 인자(비헤드리스·rg 없음) 구성
    args.update(kw)                                         # 시험별 인자로 덮어씀
    return registry.get_builtin_tools(**args)               # 레지스트리의 내장 도구 목록 반환


def test_order_non_capable_model_without_rg(tmp_path):      # rg 없고 능력 낮은 모델의 순서 시험
    """gemma(능력 없음) + rg 없음 → Edit, Search 없음, Skills 가 끝."""
    assert names(build(tmp_path)) == BASE + ["Edit", "Skills"]  # Edit 와 Skills 만 추가됨을 확인


def test_order_capable_model_with_rg_headless(tmp_path):    # 능력 있는 모델 + rg + 헤드리스 순서 시험
    """claude + rg 있음 + 헤드리스 → Search, MultiEdit, Exit."""
    assert names(build(tmp_path, model_name="claude-sonnet", headless=True, ripgrep_installed=True)) == BASE + ["Search", "MultiEdit", "Exit", "Skills"]  # Search·MultiEdit·Exit 이 포함된 순서 확인


def test_builtin_search_extra(tmp_path, monkeypatch):       # 내장 검색 환경변수 추가 기능 시험
    """[추가] rg 가 없어도 NCN_BUILTIN_SEARCH=1 이면 Search 를 넣음(같은 자리)."""
    monkeypatch.setenv("NCN_BUILTIN_SEARCH", "1")           # 환경변수로 내장 검색 켬
    assert names(build(tmp_path)) == BASE + ["Search", "Edit", "Skills"]  # rg 없이도 Search 가 같은 자리에 들어감


def test_agent_id_and_betas(tmp_path):                      # --id 와 베타 도구 순서 시험
    """--id 가 있으면 ReportFailure(+베타 UploadArtifact), 베타 Subagent 는 Exit 뒤·Skills 앞."""
    registry.set_beta_upload_artifact_tool_enabled(True)    # UploadArtifact 베타 켬
    registry.set_beta_subagent_tool_enabled(True)           # Subagent 베타 켬
    got = names(build(tmp_path, agent_id="A1", headless=True, ripgrep_installed=True,  # 에이전트 ID 와 subagent 모델을 넘겨 목록 생성
                      subagent_models=[{"name": "explorer", "roles": ["subagent"], "chatOptions": {"baseSystemMessage": "x"}}]))
    assert got == BASE + ["Search", "ReportFailure", "UploadArtifact", "Edit", "Exit", "Subagent", "Skills"]  # ReportFailure·UploadArtifact·Subagent 위치까지 확인
    sys.argv = ["ncn", "--id", "B2"]                         # 명령줄 --id 도 읽음(cn getAgentIdFromArgs)
    registry.set_beta_upload_artifact_tool_enabled(False)   # UploadArtifact 베타 다시 끔
    assert "ReportFailure" in names(build(tmp_path)) and "UploadArtifact" not in names(build(tmp_path))  # ReportFailure 는 있고 UploadArtifact 는 없어야 함


def test_model_field_also_checked(tmp_path):                # model 필드로도 능력 판정함을 시험
    """name 이 아니라 model 필드가 맞아도 MultiEdit(cn: name OR model)."""
    assert "MultiEdit" in names(build(tmp_path, model_name="my-local", model="qwen2.5-coder"))  # name 은 모르는 값이어도 model 이 qwen 이면 MultiEdit
    with pytest.raises(Exception, match="Model service is not initialized"):  # 모델 이름이 비면 오류가 나야 함
        build(tmp_path, model_name="")                      # 빈 모델 이름으로 생성 시도


@pytest.mark.parametrize("name,model,expected", [           # isModelCapable 패턴 표(이름, model, 기대값)
    ("gemma4-26b-32k", None, False), ("gemma3", None, False), ("Claude-3", None, True), ("gpt-4o", None, True),  # gemma 는 비능력, claude/gpt 는 능력
    ("o3-mini", None, True), ("foo2", None, True), ("bar2", None, False), ("llama3", None, True), ("Mistral-small", None, True),  # o\d·llama·mistral 패턴과 bar2 는 비능력
    ("local", "Qwen3", True), ("deepseek", "deepseek-coder", False), ("kimi-k2", None, True), ("pro1", None, True),   # 'pro1' 은 /o\d/ — cn 그대로
])                                                          # 표 끝
def test_is_model_capable(name, model, expected):           # is_model_capable 시험 함수
    """isModelCapable 패턴(gemma 는 Edit)."""
    assert registry.is_model_capable("openai", name, model) is expected  # 결과가 기대 불리언과 같아야 함


def test_tool_shapes(tmp_path):                             # 도구 객체 형태와 요청 본문 변환 시험
    """목록 속 도구는 Tool 이고, 요청 본문 변환은 type→required→properties 순서(required 없으면 키 없음)."""
    tools = build(tmp_path, headless=True)                  # 헤드리스 목록 생성
    assert all(isinstance(t, Tool) for t in tools)          # 모두 Tool 인스턴스
    exit_tool = next(t for t in tools if t.name == "Exit")  # Exit 도구 찾기
    assert registry.convert_tool_to_chat_completion_tool(exit_tool) == {  # Exit 변환 결과는 required 키가 없음
        "type": "function", "function": {"name": "Exit", "description": "Exit the current process with status code 1, indicating a failure or error",  # type/function/name/description 그대로
                                         "parameters": {"type": "object", "properties": {}}}}  # parameters 는 빈 properties
    bash = next(t for t in tools if t.name == "Bash")       # Bash 도구 찾기
    assert list(registry.convert_tool_to_chat_completion_tool(bash)["function"]["parameters"]) == ["type", "required", "properties"]  # 변환 후 parameters 키 순서가 type→required→properties
    assert str(tmp_path) in bash.description                 # 작업 폴더


def test_tool_overrides_on_tools(tmp_path):                 # toolOverrides 적용 시험
    """toolOverrides — disabled 는 빼고 description 은 바꿈(원본 객체는 그대로). 목록 모양도 받음."""
    tools = build(tmp_path, tool_overrides={"Fetch": {"disabled": True}, "Bash": {"description": "custom"}})  # Fetch 는 끄고 Bash 설명은 바꾸도록 지정
    assert "Fetch" not in names(tools)                      # disabled 도구는 목록에서 빠짐
    assert next(t for t in tools if t.name == "Bash").description == "custom"  # description 이 덮어써짐
    assert "custom" not in next(t for t in build(tmp_path) if t.name == "Bash").description  # 원본 정의는 바뀌지 않음
    tools2 = build(tmp_path, tool_overrides=[{"name": "Checklist", "disabled": True}])  # 목록 모양 overrides 도 받음
    assert "Checklist" not in names(tools2)                 # Checklist 가 빠짐


_MOCK = [{"type": "function", "function": {"name": "read_file", "description": "Read a file"}},  # 모의 chat completion 도구 목록
         {"type": "function", "function": {"name": "write_file", "description": "Write a file"}}]  # 두 번째 모의 도구


def test_apply_chat_completion_tool_overrides():            # cn applyToolOverrides.test.ts 사례 시험
    """cn applyToolOverrides.test.ts 사례."""
    f = registry.apply_chat_completion_tool_overrides       # 덮어쓰기 함수 별칭
    assert f(_MOCK, None) == _MOCK and f(_MOCK, {}) == _MOCK  # overrides 가 없으면 원본 그대로
    r = f(_MOCK, {"read_file": {"description": "Custom read description"}})  # read_file 설명만 바꿈
    assert r[0]["function"]["description"] == "Custom read description" and r[1]["function"]["description"] == "Write a file"  # 바뀐 것과 안 바뀐 것을 확인
    r = f(_MOCK, {"read_file": {"disabled": True}})         # disabled 이면 목록에서 제거
    assert len(r) == 1 and r[0]["function"]["name"] == "write_file"  # 하나만 남고 write_file
    r = f(_MOCK, {"read_file": {"description": "Custom read"}, "write_file": {"description": "Custom write"}})  # 두 도구 모두 설명 변경
    assert [x["function"]["description"] for x in r] == ["Custom read", "Custom write"]  # 변경된 설명 순서 확인
    assert f(_MOCK, {"non_existent_tool": {"description": "Should be ignored"}}) == _MOCK  # 없는 도구 이름은 무시
    f(_MOCK, {"read_file": {"description": "Modified description"}})  # 덮어쓴 뒤에도
    assert _MOCK[0]["function"]["description"] == "Read a file"   # 원본 안 바뀜


def test_display_name_and_extract_tool_calls():             # 표시 이름과 도구 호출 추출 시험
    """cn index.test.ts — getToolDisplayName·extractToolCalls."""
    assert registry.get_tool_display_name("Read") == "Read"  # 알려진 이름은 그대로
    assert registry.get_tool_display_name("AskQuestion") == "Ask Question"  # AskQuestion 은 공백이 들어간 표시 이름
    assert registry.get_tool_display_name("unknown_tool") == "unknown_tool" and registry.get_tool_display_name("") == ""  # 모르는 이름·빈 문자열은 그대로 돌려줌
    ex = registry.extract_tool_calls                        # 추출 함수 별칭
    assert ex('Here\'s the result:\n<tool>{"name": "Read", "arguments": {"filepath": "/path/to/file.txt"}}</tool>\nDone.') == [  # <tool> 태그 속 JSON 을 호출로 추출
        {"name": "Read", "arguments": {"filepath": "/path/to/file.txt"}}]  # 기대하는 호출 하나
    assert ex("This is just regular text with no tool calls.") == [] and ex("") == []  # 태그 없는 글·빈 글은 빈 목록
    bad = ('<tool>{"name": "Read", "arguments": {"filepath": "/file.txt"}}</tool>\n<tool>{"name": "invalid_json", "arguments": {malformed json}}</tool>\n'  # 깨진 JSON 이 낀 입력
           '<tool>{"name": "Write", "arguments": {"filepath": "/file2.txt", "content": "test"}}</tool>')  # 앞뒤 정상 호출만 남는지 확인
    assert [c["name"] for c in ex(bad)] == ["Read", "Write"]  # 깨진 것은 건너뛰고 Read·Write 만
    assert ex('<tool>{"arguments": {"filepath": "/file.txt"}}</tool><tool>{"name": "Read"}</tool>') == []  # name 이 없거나 arguments 가 없으면 무시
    assert ex('<tool>{"name": "List", "arguments": {}}</tool>') == [{"name": "List", "arguments": {}}]   # {} 는 JS 참값
    assert ex('<tool>{"name": "exit", "arguments": null}</tool>') == []  # arguments 가 null 이면 무시
    assert ex('<tool>{"name": "Write", "arguments": {"filepath": "/file.txt", "content": "Some content with <tool> tags inside"}}</tool>') == [  # 내용 속 <tool> 문자열이 있어도 정상 추출
        {"name": "Write", "arguments": {"filepath": "/file.txt", "content": "Some content with <tool> tags inside"}}]  # 추출 결과 비교
