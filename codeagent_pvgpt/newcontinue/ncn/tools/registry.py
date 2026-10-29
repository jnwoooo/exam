# ncn/tools/registry.py — 내장 도구 목록 만들기. cn extensions/cli/src/tools/index.tsx(getAllAvailableTools 등)
#   + utils/modelCapability.ts(isModelCapable) + tools/applyToolOverrides.ts + tools/toolsConfig.ts(베타 플래그)
# 순서(cn 그대로): Read, Write, List, Bash, Fetch, Checklist, CheckBackgroundJob, AskQuestion
#   → Search(rg 있을 때 / [추가] NCN_BUILTIN_SEARCH=1) → ReportFailure·UploadArtifact(--id 있을 때, 업로드는 베타)
#   → MultiEdit(모델이 '능력 있음') 또는 Edit → Exit(헤드리스) → Subagent(베타) → Skills → (MCP 도구는 MCP 모듈이 뒤에 붙임)
# 권한 필터(allow/ask/exclude, 헤드리스 ask 제외)는 이 목록 다음 단계(permissions)의 일이다 — cn getRequestTools 와 같은 나눔
from __future__ import annotations                           # 타입 주석 지연 평가

import dataclasses                                           # 도구 복사(설명 바꾸기)
import json                                                  # extractToolCalls
import pathlib                                               # 경로 타입
import re                                                    # 모델 이름 판정
from typing import Any, Optional                             # 타입

from ncn.tools.base import Tool                              # 공용 형식

# ── toolsConfig.ts — 명령줄 베타 플래그(cn: --beta-upload-artifact-tool / --beta-subagent-tool) ──
_beta_upload_artifact_tool_enabled = False                   # UploadArtifact 베타
_beta_subagent_tool_enabled = False                          # Subagent 베타


def set_beta_upload_artifact_tool_enabled(enabled: bool) -> None:
    """setBetaUploadArtifactToolEnabled."""
    global _beta_upload_artifact_tool_enabled
    _beta_upload_artifact_tool_enabled = bool(enabled)


def is_beta_upload_artifact_tool_enabled() -> bool:
    """isBetaUploadArtifactToolEnabled."""
    return _beta_upload_artifact_tool_enabled


def set_beta_subagent_tool_enabled(enabled: bool) -> None:
    """setBetaSubagentToolEnabled."""
    global _beta_subagent_tool_enabled
    _beta_subagent_tool_enabled = bool(enabled)


def is_beta_subagent_tool_enabled() -> bool:
    """isBetaSubagentToolEnabled."""
    return _beta_subagent_tool_enabled


# ── modelCapability.ts ──
_CAPABLE_PATTERNS = [re.compile(p) for p in (r"gemini", r"claude", r"gpt", r"o\d", r"kimi", r"qwen", r"llama", r"nemotron", r"grok", r"mistral")]   # cn 원문 순서


def is_model_capable(provider: Optional[str], name: str, model: Optional[str] = None) -> bool:
    """isModelCapable — name 또는 model(소문자)에 패턴이 있으면 참. provider 는 cn도 쓰지 않음.
    주의: /o\\d/ 때문에 이름 어디든 'o'+숫자가 있으면 참(예: 'gemma4-26b-32k' 는 거짓, 'foo2'·'pro1' 은 참) — cn 그대로."""
    normalized_name = (name or "").lower()                   # name.toLowerCase()
    normalized_model = model.lower() if model else ""        # model ? model.toLowerCase() : ""
    if any(p.search(normalized_name) for p in _CAPABLE_PATTERNS):   # 이름
        return True
    if model and any(p.search(normalized_model) for p in _CAPABLE_PATTERNS):   # 모델
        return True
    return False


def _check_ripgrep_sync(cwd: str) -> bool:
    """checkIfRipgrepIsInstalled 의 동기판(목록 만들기는 동기 API) — 'rg --version' 을 셸로."""
    from ncn.tools.search import _exec_blocking              # Node exec 흉내
    try:
        _exec_blocking("rg --version", cwd)                  # 실행
        return True
    except Exception:                                        # 없음
        return False


def get_builtin_tools(*, model_name: str, headless: bool, mode: str, cwd: pathlib.Path, global_dir: pathlib.Path,
                      tool_overrides: Any = None, model: Optional[str] = None, agent_id: Optional[str] = None,
                      subagent_models: Optional[list[dict]] = None, ripgrep_installed: Optional[bool] = None) -> list[Tool]:
    """getAllAvailableTools(isHeadless) 의 내장 도구 부분 — cn 순서, 권한 필터 전.
    model_name = 설정의 name, model = 설정의 model 필드(둘 중 하나라도 맞으면 MultiEdit).
    mode 는 받기만 한다 — cn은 목록 단계에서 모드를 보지 않고 권한 단계(plan = 쓰기 도구 exclude)에서 거른다.
    tool_overrides(model.chatOptions.toolOverrides — {이름: {description?, disabled?}} 또는 [{name, …}]) 를 주면
    disabled 는 빼고 description 은 바꾼다. cn은 이것을 LLM 요청용 목록에만 적용하므로(실행할 때 찾는 목록엔 미적용)
    루프는 실행용 조회에서는 넘기지 말 것.
    agent_id 가 None 이면 cn처럼 명령줄의 --id 를 읽는다. ripgrep_installed 는 시험용(None 이면 실제 확인)."""
    from ncn.tools.read_file import READ_TOOL                # 파일 도구(다른 작업자) — 지연 import
    from ncn.tools.write_file import WRITE_TOOL
    from ncn.tools.list_files import LIST_TOOL
    from ncn.tools.edit import EDIT_TOOL
    from ncn.tools.multi_edit import MULTI_EDIT_TOOL
    from ncn.tools.background_jobs import CHECK_BACKGROUND_JOB_TOOL   # 이 작업 담당 도구들
    from ncn.tools.ask_question import ASK_QUESTION_TOOL
    from ncn.tools.bash import make_bash_tool
    from ncn.tools.checklist import CHECKLIST_TOOL
    from ncn.tools.exit import EXIT_TOOL
    from ncn.tools.fetch import FETCH_TOOL
    from ncn.tools.misc import REPORT_FAILURE_TOOL, UPLOAD_ARTIFACT_TOOL, get_agent_id_from_args, make_subagent_tool
    from ncn.tools.search import builtin_search_enabled, make_search_tool
    from ncn.tools.skills import make_skills_tool

    cwd_s = str(cwd)                                         # 글로
    tools: list[Tool] = [                                    # BASE_BUILTIN_TOOLS(항상)
        READ_TOOL,
        WRITE_TOOL,
        LIST_TOOL,
        make_bash_tool(cwd_s),                               # 설명에 작업 폴더가 들어감
        FETCH_TOOL,
        CHECKLIST_TOOL,
        CHECK_BACKGROUND_JOB_TOOL,
        ASK_QUESTION_TOOL,
    ]

    rg = _check_ripgrep_sync(cwd_s) if ripgrep_installed is None else ripgrep_installed   # rg 있나(매번 확인 — cn과 같음)
    if rg or builtin_search_enabled():                       # rg 있으면 Search. [추가] 없어도 NCN_BUILTIN_SEARCH=1 이면 내장 검색으로 넣음
        tools.append(make_search_tool(cwd_s))

    aid = agent_id if agent_id is not None else get_agent_id_from_args()   # --id(cn serve)
    if aid:                                                  # 에이전트 전용 도구(--id 없으면 헷갈리므로 뺌)
        tools.append(REPORT_FAILURE_TOOL)
        if is_beta_upload_artifact_tool_enabled():           # 베타
            tools.append(UPLOAD_ARTIFACT_TOOL)

    if not model_name and not model:                         # cn: 모델이 없으면 오류
        raise Exception("Model service is not initialized")
    if is_model_capable(None, model_name or "", model):      # 능력 있는 모델 → MultiEdit
        tools.append(MULTI_EDIT_TOOL)
    else:                                                    # 그 밖(gemma 등) → Edit
        tools.append(EDIT_TOOL)

    if headless:                                             # 헤드리스만 Exit
        tools.append(EXIT_TOOL)

    if is_beta_subagent_tool_enabled():                      # 베타
        tools.append(make_subagent_tool(subagent_models))

    tools.append(make_skills_tool(cwd_s, str(global_dir)))   # 기술(매번 디스크에서 다시 읽음 — cn과 같음)

    _ = mode                                                 # (목록 단계에서는 쓰지 않음 — 위 설명)
    if tool_overrides:                                       # 덮어쓰기(요청용 목록에서만)
        tools = apply_tool_overrides(tools, tool_overrides)
    return tools


# ── applyToolOverrides.ts ──
def _normalize_overrides(overrides: Any) -> dict:
    """{이름: 설정} 으로 맞춤 — 목록([{name, …}])도 받음."""
    if overrides is None:
        return {}
    if isinstance(overrides, dict):                          # cn 모양
        return overrides
    out = {}                                                 # 목록 모양
    for item in overrides:
        if isinstance(item, dict) and item.get("name"):
            out[item["name"]] = {k: v for k, v in item.items() if k != "name"}
    return out


def apply_tool_overrides(tools: list[Tool], overrides: Any) -> list[Tool]:
    """Tool 목록에 덮어쓰기 — disabled 는 빼고, description 은 새 Tool 로(원본은 그대로)."""
    ov = _normalize_overrides(overrides)
    if not ov:
        return tools
    out = []
    for t in tools:
        o = ov.get(t.name) or {}                             # overrides[name]
        if o.get("disabled"):                                # 끔
            continue
        if not o.get("description"):                         # 설명 바꿀 것 없음
            out.append(t)
            continue
        out.append(dataclasses.replace(t, description=o["description"]))   # 복사본
    return out


def apply_chat_completion_tool_overrides(tools: list[dict], overrides: Any) -> list[dict]:
    """applyChatCompletionToolOverrides — 요청 본문 tools[] 항목({type, function:{name, description, parameters}})에 적용."""
    if overrides is None:                                    # !overrides → 그대로
        return tools
    ov = _normalize_overrides(overrides)
    out = []
    for tool in tools:
        name = tool["function"]["name"]
        o = ov.get(name)
        if o and o.get("disabled"):                          # 끔
            continue
        if not (o and o.get("description")):                 # 그대로
            out.append(tool)
            continue
        out.append({**tool, "function": {**tool["function"], "description": o["description"]}})   # 복사본
    return out


# ── index.tsx 의 작은 도우미 ──
def convert_tool_to_chat_completion_tool(tool: Tool) -> dict:
    """convertToolToChatCompletionTool — parameters 를 {type:"object", required, properties} 순서로 다시 만든다(required 가 없으면 키도 없음)."""
    params: dict = {"type": "object"}                        # 순서: type → required → properties
    if tool.parameters.get("required") is not None:          # undefined 면 JSON 에서 빠짐
        params["required"] = tool.parameters["required"]
    params["properties"] = tool.parameters.get("properties")
    if params["properties"] is None:                         # undefined → 빠짐
        del params["properties"]
    return {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": params}}


def get_tool_display_name(tool_name: str) -> str:
    """getToolDisplayName — 내장 도구면 표시 이름."""
    from ncn.tools.misc import get_tool_display_name as _g   # 표 보관 위치
    return _g(tool_name)


def extract_tool_calls(response: str) -> list[dict]:
    """extractToolCalls — 글 속 <tool>{json}</tool> 에서 name·arguments 가 있는 것만(깨진 JSON 은 건너뜀)."""
    out = []
    for m in re.finditer(r"<tool>([\s\S]*?)</tool>", response):   # 게으른 일치
        try:
            obj = json.loads(m.group(1))                     # JSON.parse
        except Exception:                                    # 깨짐
            continue
        if isinstance(obj, dict) and _js_truthy(obj.get("name")) and _js_truthy(obj.get("arguments")):   # JS 참값 판정({}·[] 도 참)
            out.append({"name": obj["name"], "arguments": obj["arguments"]})
    return out


def _js_truthy(v: Any) -> bool:
    """JS 참값 — null·false·0·NaN·"" 만 거짓({}·[] 는 참)."""
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v == v and v != 0                             # NaN·0 거짓
    if isinstance(v, str):
        return v != ""
    return True
