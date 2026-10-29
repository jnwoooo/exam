# ncn/tools/misc.py — 헤드리스 기본 목록에 안 들어가는 도구들의 자리: Status·ReportFailure·UploadArtifact·Subagent
# cn extensions/cli/src/tools/{status,reportFailure,uploadArtifact,subagent}.ts + subagent/{index,get-agents}.ts
# 이름·설명·인자·오류 글은 cn 원문 그대로. Hub(api.continue.dev) 보고·하위 에이전트 실행은 P3에서 — 지금은 cn이 폐쇄망·헤드리스에서 내는 결과와 같은 오류를 낸다
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # --id 읽기(process.argv)
from typing import Any, Optional                             # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview   # 공용 형식


def get_agent_id_from_args(argv: Optional[list[str]] = None) -> Optional[str]:
    """getAgentIdFromArgs — 명령줄의 '--id <값>'(cn serve 전용). 없으면 None."""
    args = sys.argv if argv is None else argv                # process.argv
    try:
        idx = args.index("--id")                             # 처음 나온 위치
    except ValueError:                                       # 없음
        return None
    if idx + 1 < len(args):                                  # 값이 있으면
        return args[idx + 1]
    return None


def _post_not_available(what: str) -> Exception:
    """[P3] Hub API post — ncn은 Hub에 보내지 않는다. cn이 폐쇄망에서 fetch 실패 시 내는 'Request failed: fetch failed' 와 같은 글."""
    return Exception(f"{what}: Request failed: fetch failed")   # 바깥 catch 가 붙이는 앞말 포함


# ───────────────────────── Status(cn tools/status.ts) ─────────────────────────
async def _status_run(args: dict, context=None) -> str:
    """run — --id 가 없으면 ContinueError. 있으면 Hub 로 상태 보고(P3: 폐쇄망 실패 글)."""
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:                                         # 없음
        raise ContinueError("Unspecified", "Agent ID is required. Please use the --id flag with cn serve.")
    # P3에서: post(`agents/${agentId}/status`, {status}) → 성공 시 `Status set: ${status}`, DONE/FAILED 면 updateAgentMetadata
    raise _post_not_available("Error setting status")


STATUS_TOOL = Tool(
    name="Status",
    display_name="Status",
    description=(
        "Set the current status of your task for the user to see\n"
        "\n"
        "The default available statuses are:\n"
        "- PLANNING: You are creating a plan before beginning implementation\n"
        "- WORKING: The task is in progress\n"
        "- DONE: The task is complete\n"
        "- BLOCKED: You need further information from the user in order to proceed\n"
        "\n"
        "However, if the user explicitly specifies in their prompt to use one or more different statuses, you can use those as well.\n"
        "\n"
        "You should use this tool to notify the user whenever the state of your work changes. By default, the status is assumed to be \"PLANNING\" prior to you setting a different status."
    ),
    parameters={
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {
                "type": "string",
                "description": "The status value to set",
            },
        },
    },
    run=_status_run,
    readonly=True,
    is_built_in=True,
)


# ───────────────────────── ReportFailure(cn tools/reportFailure.ts) ─────────────────────────
async def _report_failure_run(args: dict, context=None) -> str:
    """run — 빈 메시지·--id 없음은 ContinueError. 있으면 Hub 보고(P3: 폐쇄망 실패 글)."""
    msg = args.get("errorMessage")                           # 메시지
    if not isinstance(msg, str):                             # args.errorMessage.trim() — 글이 아니면 TypeError
        detail = "Cannot read properties of undefined (reading 'trim')" if msg is None else "args.errorMessage.trim is not a function"   # V8 TypeError 글
        raise Exception(f"Error reporting failure: {detail}")    # 바깥 catch 의 앞말
    from ncn.tools.formatters import js_trim                 # JS trim
    trimmed = js_trim(msg)                                   # 앞뒤 공백 제거
    if not trimmed:                                          # 비었음
        raise ContinueError("Unspecified", "errorMessage is required to report a failure.")
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:
        raise ContinueError("Unspecified", "Agent ID is required. Please use the --id flag with cn serve.")
    # P3에서: post(`agents/${agentId}/status`, {status:"FAILED", errorMessage}) → "Failure reported to user."
    raise _post_not_available("Error reporting failure")


REPORT_FAILURE_TOOL = Tool(
    name="ReportFailure",
    display_name="Report Failure",
    description="Report that the task has failed due to an unrecoverable error. Include a succinct explanation for the user.",
    parameters={
        "type": "object",
        "required": ["errorMessage"],
        "properties": {
            "errorMessage": {
                "type": "string",
                "description": "Explain what went wrong and why you cannot continue.",
            },
        },
    },
    run=_report_failure_run,
    readonly=True,
    is_built_in=True,
)


# ───────────────────────── UploadArtifact(cn tools/uploadArtifact.ts) ─────────────────────────
async def _upload_artifact_run(args: dict, context=None) -> str:
    """run — cn은 인증이 제거되어 --id 가 있어도 항상 'requires authentication' 오류(그대로)."""
    fp = args.get("filePath")                                # 경로
    from ncn.tools.formatters import js_trim                 # JS trim
    trimmed = js_trim(fp) if isinstance(fp, str) else None   # args.filePath?.trim()
    if not trimmed:                                          # 비었음
        raise ContinueError("Unspecified", "filePath is required to upload an artifact.")
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:
        raise ContinueError("Unspecified", "Agent ID is required. This tool only works with 'cn serve --id <agentId>'.")
    raise ContinueError("Unspecified", "Artifact upload requires authentication, which has been removed.")   # accessToken = null


UPLOAD_ARTIFACT_TOOL = Tool(
    name="UploadArtifact",
    display_name="Upload Artifact",
    description="Upload a file (screenshot, video, log) to the session artifacts for user review. Supported formats: images (png, jpg, jpeg, gif, webp), videos (mp4, mov, avi, webm), and text files. Maximum file size: 50MB. If an artifact with the same filename already exists, it will be overwritten with the new file.",
    parameters={
        "type": "object",
        "required": ["filePath"],
        "properties": {
            "filePath": {
                "type": "string",
                "description": "Absolute path to the file to upload (e.g., /tmp/screenshot.png)",
            },
        },
    },
    run=_upload_artifact_run,
    readonly=True,
    is_built_in=True,
)


# ───────────────────────── Subagent(cn tools/subagent.ts + subagent/*) — 베타(--beta-subagent-tool) ─────────────────────────
_SUBAGENT_PARAMETERS = {                                     # SUBAGENT_TOOL_META.parameters
    "type": "object",
    "required": ["description", "prompt", "subagent_name"],
    "properties": {
        "description": {
            "type": "string",
            "description": "A short description of the task",
        },
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform",
        },
        "subagent_name": {
            "type": "string",
            "description": "The type of specialized agent to use for this task.",
        },
    },
}


async def _empty_run(args: dict, context=None) -> str:
    """META.run — 빈 글."""
    return ""


SUBAGENT_TOOL_META = Tool(                                   # allBuiltIns 정적 자리
    name="Subagent",
    display_name="Subagent",
    description="Use a subagent to handle a specialized task.",
    parameters=_SUBAGENT_PARAMETERS,
    run=_empty_run,
    readonly=False,
    is_built_in=True,
)


def get_subagent_models(models: Optional[list[dict]]) -> list[dict]:
    """ModelService.getSubagentModels — 이름 있고 roles 에 subagent, chatOptions.baseSystemMessage 가 있는 모델(설정 순서)."""
    if not models:                                           # assistant 없음
        return []
    out = []
    for m in models:
        if not m or not m.get("name"):                       # 빈 것·이름 없음
            continue
        if "subagent" not in (m.get("roles") or []):         # 역할
            continue
        if not ((m.get("chatOptions") or {}).get("baseSystemMessage")):   # 시스템 메시지 필수
            continue
        out.append(m)
    return out


def generate_subagent_tool_description(models: Optional[list[dict]]) -> str:
    """generateSubagentToolDescription — 하위 에이전트 목록을 넣은 설명(cn 원문)."""
    agent_list = "\n".join(f"  - {m['name']}: {(m.get('chatOptions') or {}).get('baseSystemMessage')}"
                           for m in get_subagent_models(models))   # 목록
    return f"Launch a specialized subagent to handle a specific task.\n\nHere are the available subagents:\n{agent_list}\n"


def make_subagent_tool(models: Optional[list[dict]]) -> Tool:
    """subagentTool() — models 는 설정의 models 목록(dict: name·roles·chatOptions …)."""
    names = [m["name"] for m in get_subagent_models(models)]   # getAgentNames
    params: dict[str, Any] = {                               # {...META.parameters, properties: {...META.properties, subagent_name}}
        "type": _SUBAGENT_PARAMETERS["type"],
        "required": list(_SUBAGENT_PARAMETERS["required"]),
        "properties": {
            **_SUBAGENT_PARAMETERS["properties"],
            "subagent_name": {
                "type": "string",
                "description": f"The type of specialized agent to use for this task. Available agents: {', '.join(names)}",
            },
        },
    }

    def find(name: Any) -> Optional[dict]:
        """getSubagent — model.name 이 같은 첫 모델."""
        return next((m for m in get_subagent_models(models) if m["name"] == name), None)

    async def preprocess(args: dict) -> PreprocessResult:
        """preprocess — 모르는 이름이면 오류, 미리보기 'Spawning <모델> to: <설명>'."""
        agent = find(args.get("subagent_name"))              # 찾기
        if not agent:
            raise Exception(f"Unknown agent type: {args.get('subagent_name')}. Available agents: {', '.join(names)}")
        return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Spawning {agent['name']} to: {args.get('description')}")])

    async def run(args: dict, context=None) -> str:
        """run — P3.5에서 하위 대화 실행. 지금은 cn 헤드리스와 같은 'No active session found'(P0 결과 4장)."""
        agent = find(args.get("subagent_name"))              # 찾기
        if not agent:
            raise Exception(f"Unknown agent type: {args.get('subagent_name')}")
        # P3에서: parentSessionId 확인 → executeSubAgent → `${response}\n<task_metadata>\nstatus: completed|failed\n</task_metadata>`
        raise Exception("No active session found")

    return Tool(
        name="Subagent",
        display_name="Subagent",
        description=generate_subagent_tool_description(models),
        parameters=params,
        run=run,
        preprocess=preprocess,
        readonly=False,
        is_built_in=True,
    )


# ───────────────────────── 표시 이름(allBuiltIns.ts ALL_BUILT_IN_TOOLS / builtInToolNames.ts) ─────────────────────────
BUILT_IN_TOOL_NAMES = [                                      # builtInToolNames.ts 순서 그대로
    "AskQuestion", "Edit", "Exit", "Fetch", "List", "MultiEdit", "Read", "ReportFailure", "Bash",
    "Search", "Status", "Subagent", "Skills", "UploadArtifact", "Diff", "Checklist", "Write",
]

_DISPLAY_NAMES = {                                           # ALL_BUILT_IN_TOOLS 의 displayName
    "AskQuestion": "Ask Question", "Edit": "Edit", "Exit": "Exit", "Fetch": "Fetch", "List": "List",
    "MultiEdit": "MultiEdit", "Read": "Read", "ReportFailure": "Report Failure", "Bash": "Bash",
    "Search": "Search", "Status": "Status", "Subagent": "Subagent", "Skills": "Skills",
    "UploadArtifact": "Upload Artifact", "Diff": "Diff", "Checklist": "Checklist", "Write": "Write",
}                                                            # CheckBackgroundJob 은 ALL_BUILT_IN_TOOLS 에 없음(cn 그대로 → 이름 그대로 표시)


def get_tool_display_name(tool_name: str) -> str:
    """getToolDisplayName — 내장 도구면 표시 이름, 아니면 이름 그대로."""
    return _DISPLAY_NAMES.get(tool_name) or tool_name        # 없으면 원래 이름
