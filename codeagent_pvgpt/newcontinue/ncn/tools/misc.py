# ncn/tools/misc.py — 헤드리스 기본 목록에 안 들어가는 도구들의 자리: Status·ReportFailure·UploadArtifact·Subagent
# cn extensions/cli/src/tools/{status,reportFailure,uploadArtifact,subagent}.ts + subagent/{index,get-agents}.ts
# 이름·설명·인자·오류 글은 cn 원문 그대로. Hub(api.continue.dev) 보고·하위 에이전트 실행은 P3에서 — 지금은 cn이 폐쇄망·헤드리스에서 내는 결과와 같은 오류를 낸다
from __future__ import annotations                           # 타입 주석 지연 평가

import sys                                                   # --id 읽기(process.argv)
from typing import Any, Optional                             # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview   # 공용 형식


def get_agent_id_from_args(argv: Optional[list[str]] = None) -> Optional[str]:  # 명령줄에서 --id 값을 꺼내는 함수(argv를 주면 시험용)
    """getAgentIdFromArgs — 명령줄의 '--id <값>'(cn serve 전용). 없으면 None."""
    args = sys.argv if argv is None else argv                # process.argv
    try:                                                    # --id 가 없으면 index 가 ValueError
        idx = args.index("--id")                             # 처음 나온 위치
    except ValueError:                                       # 없음
        return None
    if idx + 1 < len(args):                                  # 값이 있으면
        return args[idx + 1]                                # 값 자리가 있으면 그것을 에이전트 ID로
    return None                                             # 값이 없으면 None


def _post_not_available(what: str) -> Exception:            # Hub 전송 불가 상황을 예외로 만드는 도우미
    """[P3] Hub API post — ncn은 Hub에 보내지 않는다. cn이 폐쇄망에서 fetch 실패 시 내는 'Request failed: fetch failed' 와 같은 글."""
    return Exception(f"{what}: Request failed: fetch failed")   # 바깥 catch 가 붙이는 앞말 포함


# ───────────────────────── Status(cn tools/status.ts) ─────────────────────────
async def _status_run(args: dict, context=None) -> str:     # Status run 훅
    """run — --id 가 없으면 ContinueError. 있으면 Hub 로 상태 보고(P3: 폐쇄망 실패 글)."""
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:                                         # 없음
        raise ContinueError("Unspecified", "Agent ID is required. Please use the --id flag with cn serve.")  # --id 없음은 cn 원문 오류 글로 거부
    # P3에서: post(`agents/${agentId}/status`, {status}) → 성공 시 `Status set: ${status}`, DONE/FAILED 면 updateAgentMetadata
    raise _post_not_available("Error setting status")       # Hub 전송은 P3 — 지금은 폐쇄망 실패 글로 끝냄


STATUS_TOOL = Tool(                                         # Status 도구 정의(cn status.ts 대응)
    name="Status",                                          # 모델에 보이는 도구 이름
    display_name="Status",                                  # 화면 표시 이름
    description=(                                           # 모델에게 주는 도구 설명(cn 원문, 아래 여러 줄)
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
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "required": ["status"],                             # status 인자 필수
        "properties": {                                     # 인자별 정의 묶음
            "status": {                                     # status 인자 정의
                "type": "string",                           # 문자열
                "description": "The status value to set",   # 인자 설명 원문
            },                                              # status 정의 끝
        },                                                  # properties 끝
    },                                                      # parameters 끝
    run=_status_run,                                        # 실행 함수 연결
    readonly=True,                                          # 헤드리스 기본 목록 밖의 읽기 전용 도구
    is_built_in=True,                                       # 내장 도구 표시
)


# ───────────────────────── ReportFailure(cn tools/reportFailure.ts) ─────────────────────────
async def _report_failure_run(args: dict, context=None) -> str:  # ReportFailure run 훅
    """run — 빈 메시지·--id 없음은 ContinueError. 있으면 Hub 보고(P3: 폐쇄망 실패 글)."""
    msg = args.get("errorMessage")                           # 메시지
    if not isinstance(msg, str):                             # args.errorMessage.trim() — 글이 아니면 TypeError
        detail = "Cannot read properties of undefined (reading 'trim')" if msg is None else "args.errorMessage.trim is not a function"   # V8 TypeError 글
        raise Exception(f"Error reporting failure: {detail}")    # 바깥 catch 의 앞말
    from ncn.tools.formatters import js_trim                 # JS trim
    trimmed = js_trim(msg)                                   # 앞뒤 공백 제거
    if not trimmed:                                          # 비었음
        raise ContinueError("Unspecified", "errorMessage is required to report a failure.")  # 공백뿐이어도 거부(cn 원문 글)
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:                                        # --id 없으면 거부
        raise ContinueError("Unspecified", "Agent ID is required. Please use the --id flag with cn serve.")  # cn 원문 오류 글
    # P3에서: post(`agents/${agentId}/status`, {status:"FAILED", errorMessage}) → "Failure reported to user."
    raise _post_not_available("Error reporting failure")    # Hub 전송은 P3 — 폐쇄망 실패 글로 끝냄


REPORT_FAILURE_TOOL = Tool(                                 # ReportFailure 도구 정의(cn reportFailure.ts 대응)
    name="ReportFailure",                                   # 모델에 보이는 도구 이름
    display_name="Report Failure",                          # 화면 표시 이름
    description="Report that the task has failed due to an unrecoverable error. Include a succinct explanation for the user.",  # 모델에게 주는 도구 설명 원문
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "required": ["errorMessage"],                       # errorMessage 인자 필수
        "properties": {                                     # 인자별 정의 묶음
            "errorMessage": {                               # errorMessage 인자 정의
                "type": "string",                           # 문자열
                "description": "Explain what went wrong and why you cannot continue.",  # 인자 설명 원문
            },                                              # errorMessage 정의 끝
        },                                                  # properties 끝
    },                                                      # parameters 끝
    run=_report_failure_run,                                # 실행 함수 연결
    readonly=True,                                          # 읽기 전용 도구 표시
    is_built_in=True,                                       # 내장 도구 표시
)


# ───────────────────────── UploadArtifact(cn tools/uploadArtifact.ts) ─────────────────────────
async def _upload_artifact_run(args: dict, context=None) -> str:  # UploadArtifact run 훅
    """run — cn은 인증이 제거되어 --id 가 있어도 항상 'requires authentication' 오류(그대로)."""
    fp = args.get("filePath")                                # 경로
    from ncn.tools.formatters import js_trim                 # JS trim
    trimmed = js_trim(fp) if isinstance(fp, str) else None   # args.filePath?.trim()
    if not trimmed:                                          # 비었음
        raise ContinueError("Unspecified", "filePath is required to upload an artifact.")  # 경로가 비면 cn 원문 오류 글로 거부
    agent_id = get_agent_id_from_args()                      # --id
    if not agent_id:                                        # --id 없으면 거부
        raise ContinueError("Unspecified", "Agent ID is required. This tool only works with 'cn serve --id <agentId>'.")  # cn 원문 오류 글
    raise ContinueError("Unspecified", "Artifact upload requires authentication, which has been removed.")   # accessToken = null


UPLOAD_ARTIFACT_TOOL = Tool(                                # UploadArtifact 도구 정의(cn uploadArtifact.ts 대응)
    name="UploadArtifact",                                  # 모델에 보이는 도구 이름
    display_name="Upload Artifact",                         # 화면 표시 이름
    description="Upload a file (screenshot, video, log) to the session artifacts for user review. Supported formats: images (png, jpg, jpeg, gif, webp), videos (mp4, mov, avi, webm), and text files. Maximum file size: 50MB. If an artifact with the same filename already exists, it will be overwritten with the new file.",  # 모델에게 주는 도구 설명 원문
    parameters={                                            # 인자 스키마(JSON Schema)
        "type": "object",                                   # 인자는 객체
        "required": ["filePath"],                           # filePath 인자 필수
        "properties": {                                     # 인자별 정의 묶음
            "filePath": {                                   # filePath 인자 정의
                "type": "string",                           # 문자열
                "description": "Absolute path to the file to upload (e.g., /tmp/screenshot.png)",  # 인자 설명 원문
            },                                              # filePath 정의 끝
        },                                                  # properties 끝
    },                                                      # parameters 끝
    run=_upload_artifact_run,                               # 실행 함수 연결
    readonly=True,                                          # 읽기 전용 도구 표시
    is_built_in=True,                                       # 내장 도구 표시
)


# ───────────────────────── Subagent(cn tools/subagent.ts + subagent/*) — 베타(--beta-subagent-tool) ─────────────────────────
_SUBAGENT_PARAMETERS = {                                     # SUBAGENT_TOOL_META.parameters
    "type": "object",                                       # 인자 타입은 객체
    "required": ["description", "prompt", "subagent_name"],  # 세 인자 모두 필수
    "properties": {                                         # 인자별 정의 묶음
        "description": {                                    # 작업 설명 인자
            "type": "string",                               # 문자열
            "description": "A short description of the task",  # 인자 설명 원문
        },                                                  # description 정의 끝
        "prompt": {                                         # 프롬프트 인자
            "type": "string",                               # 문자열
            "description": "The task for the agent to perform",  # 인자 설명 원문
        },                                                  # prompt 정의 끝
        "subagent_name": {                                  # 하위 에이전트 이름 인자
            "type": "string",                               # 문자열
            "description": "The type of specialized agent to use for this task.",  # 인자 설명 원문
        },                                                  # subagent_name 정의 끝
    },                                                      # properties 끝
}                                                           # 스키마 끝


async def _empty_run(args: dict, context=None) -> str:      # META 의 run 훅(빈 글)
    """META.run — 빈 글."""
    return ""                                               # 실제 실행은 make_subagent_tool 쪽


SUBAGENT_TOOL_META = Tool(                                   # allBuiltIns 정적 자리
    name="Subagent",                                        # 모델에 보이는 도구 이름
    display_name="Subagent",                                # 화면 표시 이름
    description="Use a subagent to handle a specialized task.",  # 모델에게 주는 도구 설명 원문
    parameters=_SUBAGENT_PARAMETERS,                        # 공용 인자 스키마 연결
    run=_empty_run,                                         # 빈 실행 함수 연결
    readonly=False,                                         # 쓰기 도구 취급(읽기 전용 아님)
    is_built_in=True,                                       # 내장 도구 표시
)


def get_subagent_models(models: Optional[list[dict]]) -> list[dict]:  # subagent 역할을 가진 모델만 골라내는 함수
    """ModelService.getSubagentModels — 이름 있고 roles 에 subagent, chatOptions.baseSystemMessage 가 있는 모델(설정 순서)."""
    if not models:                                           # assistant 없음
        return []                                           # 모델 목록이 없으면 빈 목록
    out = []
    for m in models:                                        # 설정 순서대로 훑음
        if not m or not m.get("name"):                       # 빈 것·이름 없음
            continue                                        # 이 모델은 건너뜀
        if "subagent" not in (m.get("roles") or []):         # 역할
            continue                                        # 이 모델은 건너뜀
        if not ((m.get("chatOptions") or {}).get("baseSystemMessage")):   # 시스템 메시지 필수
            continue                                        # 이 모델은 건너뜀
        out.append(m)                                       # 조건을 통과한 모델 수집
    return out                                              # 걸러낸 모델 목록 반환


def generate_subagent_tool_description(models: Optional[list[dict]]) -> str:  # 모델 목록으로 도구 설명을 만드는 함수
    """generateSubagentToolDescription — 하위 에이전트 목록을 넣은 설명(cn 원문)."""
    agent_list = "\n".join(f"  - {m['name']}: {(m.get('chatOptions') or {}).get('baseSystemMessage')}"  # '  - 이름: 시스템 메시지' 줄들을 이음
                           for m in get_subagent_models(models))   # 목록
    return f"Launch a specialized subagent to handle a specific task.\n\nHere are the available subagents:\n{agent_list}\n"  # cn 원문 틀에 목록을 끼움


def make_subagent_tool(models: Optional[list], host: Any = None) -> Tool:  # 모델 목록을 반영한 Subagent 도구를 만드는 함수
    """subagentTool() — models 는 설정의 models 목록(dict: name·roles·chatOptions … 또는 ModelConfig).
    host(ncn.subagent.SubagentHost)가 있으면 run 이 하위 대화를 실제로 실행한다. 없으면 cn 헤드리스와 같은 'No active session found'."""
    from ncn.subagent import model_dicts                     # ModelConfig → cn 모양 dict
    models = model_dicts(models)                             # getSubagentModels 는 dict 를 본다
    names = [m["name"] for m in get_subagent_models(models)]   # getAgentNames
    params: dict[str, Any] = {                               # {...META.parameters, properties: {...META.properties, subagent_name}}
        "type": _SUBAGENT_PARAMETERS["type"],               # 기본 스키마의 type 재사용
        "required": list(_SUBAGENT_PARAMETERS["required"]),  # required 는 복사본(원본 보호)
        "properties": {                                     # properties 는 기본 정의 + 이름 목록을 넣은 subagent_name
            **_SUBAGENT_PARAMETERS["properties"],           # 기본 properties 펼침
            "subagent_name": {                              # subagent_name 을 사용 가능한 이름 목록으로 덮어씀
                "type": "string",                           # 문자열
                "description": f"The type of specialized agent to use for this task. Available agents: {', '.join(names)}",  # 사용 가능한 에이전트 이름을 설명에 덧붙임(cn 원문)
            },                                              # subagent_name 정의 끝
        },                                                  # properties 끝
    }

    def find(name: Any) -> Optional[dict]:                  # 이름으로 하위 에이전트 모델을 찾는 도우미
        """getSubagent — model.name 이 같은 첫 모델."""
        return next((m for m in get_subagent_models(models) if m["name"] == name), None)  # 첫 번째로 이름이 같은 모델, 없으면 None

    async def preprocess(args: dict) -> PreprocessResult:   # preprocess 훅
        """preprocess — 모르는 이름이면 오류, 미리보기 'Spawning <모델> to: <설명>'."""
        agent = find(args.get("subagent_name"))              # 찾기
        if not agent:                                       # 모르는 이름이면 거부
            raise Exception(f"Unknown agent type: {args.get('subagent_name')}. Available agents: {', '.join(names)}")  # cn 원문 오류 글
        return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Spawning {agent['name']} to: {args.get('description')}")])  # 미리보기 'Spawning <모델> to: <설명>'

    async def run(args: dict, context=None) -> str:         # run 훅
        """run — 하위 대화 실행(ncn/subagent.py). 호스트가 없으면 cn 헤드리스와 같은 'No active session found'(P0 4장)."""
        agent = find(args.get("subagent_name"))              # 찾기
        if not agent:                                       # 모르는 이름이면 거부
            raise Exception(f"Unknown agent type: {args.get('subagent_name')}")  # cn 원문 오류 글
        if host is None:                                    # 부모 정보가 없으면(시험·단독 사용)
            raise Exception("No active session found")      # cn 헤드리스와 같은 오류
        from ncn.subagent import run_subagent_tool          # 하위 대화 실행(지연 import — 순환 방지)
        return await run_subagent_tool(host, agent, args, getattr(context, "tool_call_id", None))   # 결과 글(<task_metadata> 포함)

    return Tool(                                            # 이름 목록을 반영한 도구 객체 반환
        name="Subagent",                                    # 모델에 보이는 도구 이름
        display_name="Subagent",                            # 화면 표시 이름
        description=generate_subagent_tool_description(models),  # 하위 에이전트 목록이 든 설명
        parameters=params,                                  # 이름이 들어간 인자 스키마
        run=run,                                            # run 함수 연결
        preprocess=preprocess,                              # preprocess 함수 연결
        readonly=False,                                     # 쓰기 도구 취급(읽기 전용 아님)
        is_built_in=True,                                   # 내장 도구 표시
    )


# ───────────────────────── 표시 이름(allBuiltIns.ts ALL_BUILT_IN_TOOLS / builtInToolNames.ts) ─────────────────────────
BUILT_IN_TOOL_NAMES = [                                      # builtInToolNames.ts 순서 그대로
    "AskQuestion", "Edit", "Exit", "Fetch", "List", "MultiEdit", "Read", "ReportFailure", "Bash",  # 이름 순서는 cn 목록 그대로(앞 절반)
    "Search", "Status", "Subagent", "Skills", "UploadArtifact", "Diff", "Checklist", "Write",  # 이름 순서는 cn 목록 그대로(뒷 절반)
]                                                           # 목록 끝

_DISPLAY_NAMES = {                                           # ALL_BUILT_IN_TOOLS 의 displayName
    "AskQuestion": "Ask Question", "Edit": "Edit", "Exit": "Exit", "Fetch": "Fetch", "List": "List",  # 도구 이름 → 표시 이름(앞)
    "MultiEdit": "MultiEdit", "Read": "Read", "ReportFailure": "Report Failure", "Bash": "Bash",  # 도구 이름 → 표시 이름
    "Search": "Search", "Status": "Status", "Subagent": "Subagent", "Skills": "Skills",  # 도구 이름 → 표시 이름
    "UploadArtifact": "Upload Artifact", "Diff": "Diff", "Checklist": "Checklist", "Write": "Write",  # 도구 이름 → 표시 이름(끝)
}                                                            # CheckBackgroundJob 은 ALL_BUILT_IN_TOOLS 에 없음(cn 그대로 → 이름 그대로 표시)


def get_tool_display_name(tool_name: str) -> str:           # 표시 이름 조회 함수
    """getToolDisplayName — 내장 도구면 표시 이름, 아니면 이름 그대로."""
    return _DISPLAY_NAMES.get(tool_name) or tool_name        # 없으면 원래 이름
