# ncn/mcp/service.py — MCP 서버 연결 관리. cn extensions/cli/src/services/MCPService.ts(doInitialize·connectServer·getConnectedClient·
#   runTool·shutdownConnections) + mcpUtils.ts(isAuthError). 클라이언트는 공식 mcp 파이썬 SDK 2.x(ClientSession).
# cn 동작 그대로:
#   - 설정 mcpServers 를 병렬로 접속. 연결 표는 서버 name 이 키(이름이 같으면 뒤의 것이 덮음 — Map.set).
#     [ncn 부록B M3] 이름이 같은 서버는 뒤의 것을 건너뛰고 stderr 경고(호환 모드는 cn 처럼 덮음).
#   - 접속 전 설정 JSON 에 남은 ${{ secrets.X }} 가 있으면: 헤드리스는 오류, 대화형은 경고("… MCP Server has unresolved secrets: …").
#   - 접속 실패: status "error", error = 오류 글(경고가 있으면 "\n\nServer stderr:\n" + 경고들 — stdio stderr 포함).
#   - 헤드리스는 모두 끝날 때까지 기다리고 실패가 있으면 "MCP server(s) failed to load:\n<이름>: <오류>…" 로 던진다.
#     대화형(cn TUI)은 기다리지 않는다 — ncn ACP 는 wait=True 로 기다린다(첫 질문부터 도구가 보이게, 실패는 던지지 않음).
#   - 서버 능력에 prompts 가 있으면 prompts/list, tools 가 있으면 tools/list(cn 은 첫 쪽만 — nextCursor 를 따라가지 않음).
#     [ncn 부록B M2] nextCursor 를 끝까지 따라간다(상한 MAX_TOOL_PAGES 쪽). 호환 모드는 cn 처럼 첫 쪽만.
#   - 도구 목록(get_tools): cn 은 서버마다 이어 붙일 뿐이라 같은 이름이 요청에 두 번 실린다.
#     [ncn 부록B M3] 먼저 온 것만(내장 도구 이름과 겹치면 내장 우선) + stderr 경고. 호환 모드는 cn 처럼 전부.
#   - type 없는 원격 서버: streamable-http 먼저, 실패하면(인증 오류 제외) SSE. 인증 오류(401/405) 시 대화형 cn 은 npx mcp-remote 로
#     다시 시도하지만 ncn 은 하지 않는다(폐쇄망 — npx 없음).
#   - runTool: 연결된 서버를 순서대로 보며 그 이름의 도구가 있는 첫 서버에 tools/call. 없으면 "Tool <이름> not found".
# 시간 제한: cn 의 Node SDK 는 요청마다 기본 60초(DEFAULT_REQUEST_TIMEOUT_MSEC) → 같게 60초.
#   [ncn] 원격 서버 설정 requestOptions.timeout(초)이 있으면 그 값(stdio 설정 스키마에는 requestOptions 가 없어 적용 안 됨).
# 오류 글은 cn(Node SDK) 원문으로 바꾼다: "MCP error <코드>: <글>", "spawn <명령> ENOENT", "fetch failed",
#   "SSE error: Non-200 status code (404)", "Streamable HTTP error: Error POSTing to endpoint: <본문>" 등.
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기
import errno                                                 # spawn 오류 코드
import logging                                               # 로그(stderr — stdout 금지)
import os                                                    # 환경변수
import sys                                                   # stderr 경고(부록B M3)
import urllib.parse                                          # 주소 분해(오류 글)
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Mapping, Optional                    # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치(NCN_CN_COMPAT=1)
from ncn.config import secrets as _sec                       # 템플릿 변수·FQSN 해석
from ncn.tools.base import Tool                              # 공용 형식

try:                                                         # 3.11+ 내장 예외 묶음
    BaseExceptionGroup                                       # 있는지 확인
except NameError:                                            # 3.10 은 백포트(anyio 의존성)
    from exceptiongroup import BaseExceptionGroup            # type: ignore[no-redef] — 백포트

log = logging.getLogger("ncn.mcp")                           # 로거

DEFAULT_REQUEST_TIMEOUT_S = 60.0                             # Node SDK DEFAULT_REQUEST_TIMEOUT_MSEC = 60000 (초 단위)
MAX_TOOL_PAGES = 100                                         # [ncn 부록B M2] tools/list 쪽 상한(무한 반복 방지)
# [ncn 부록B M3] 내장 도구 이름(ncn/tools/registry.py 가 넣을 수 있는 전부) — MCP 도구가 이 이름이면 내장이 이긴다
BUILTIN_TOOL_NAMES = frozenset({"Read", "Write", "List", "Bash", "Fetch", "Checklist", "CheckBackgroundJob", "AskQuestion",   # 늘 있는 것
                                "Search", "ReportFailure", "UploadArtifact", "MultiEdit", "Edit", "Exit", "Subagent", "Skills"})   # 조건부로 있는 것
CLIENT_NAME, CLIENT_VERSION = "continue-cli-client", "1.0.0"  # cn: new Client({name, version}) — 클라이언트 정보
UNRESOLVED_SECRETS = ("{name} MCP Server has unresolved secrets: {secrets}\n"   # cn 원문(MCPService.ts connectServer)
                      "For personal use you can set the secret in the hub at https://continue.dev/settings/secrets or pass it to the CLI environment.\n"   # 둘째 줄
                      "Org-level secrets can only be used for MCP by Background Agents (https://docs.continue.dev/hub/agents/overview) "   # 셋째 줄 앞
                      "when \"Include in Env\" is enabled for the secret.")   # 셋째 줄 끝


def _warn(message: str) -> None:                             # [ncn 부록B M3] 사용자에게 보이는 경고(헤드리스·ACP 모두 stderr — stdout 금지)
    """'ncn: <글>' 한 줄을 stderr 에 바로 쓴다(로그 설정과 상관없이 보이게)."""
    try:                                                     # stderr 가 닫혔어도 동작은 계속
        sys.stderr.write(f"ncn: {message}\n")                # 한 줄 경고
        sys.stderr.flush()                                   # 바로 내보냄
    except Exception:                                        # 쓰기 실패
        pass                                                 # 무시
    log.debug("warning: %s", message)                        # 로그 파일에도 남김


class McpClientError(Exception):                             # cn(Node SDK) 원문 글로 바꾼 오류
    """message = cn 이 보이는 글, code = HTTP 상태 또는 MCP 오류 코드(isAuthError 판정용)."""

    def __init__(self, message: str, code: Optional[int] = None):   # 글·코드
        super().__init__(message)                            # str(e) = 글
        self.code = code                                     # 코드


@dataclass                                                   # 자료 클래스
class ServerConnection:                                      # 서버 하나의 연결 상태(cn ServerConnection = MCPConnectionInfo + client)
    """config·status(idle/connecting/connected/error)·tools·prompts·warnings·error + SDK 세션."""
    config: dict                                             # 서버 설정(cn 모양)
    status: str = "connecting"                               # 상태
    tools: list = field(default_factory=list)                # tools/list 결과(SDK Tool 객체)
    prompts: list = field(default_factory=list)              # prompts/list 결과
    warnings: list = field(default_factory=list)             # 경고(stderr·비밀값·목록 실패)
    error: Optional[str] = None                              # 실패 글
    session: Any = None                                      # ClientSession(연결됐을 때)
    task: Optional[asyncio.Task] = None                      # 연결을 붙잡고 있는 작업
    stop: Optional[asyncio.Event] = None                     # 닫기 신호
    http_errors: list = field(default_factory=list)          # 최근 HTTP 오류 응답 (상태, 본문)
    kind: str = ""                                           # 지금 전송 종류(stdio·http·sse)

    @property                                                # 읽기 전용 속성
    def name(self) -> str:                                   # 서버 이름
        """config.name."""
        return self.config.get("name") or ""                 # 이름


def _leaf(e: BaseException) -> BaseException:               # 예외 묶음(anyio 작업 그룹)에서 진짜 원인 하나
    """ExceptionGroup 이면 취소가 아닌 첫 하위 예외를 재귀로 고른다."""
    while isinstance(e, BaseExceptionGroup):                 # 묶음이면
        subs = list(e.exceptions)                            # 하위 예외들
        pick = next((x for x in subs if not isinstance(x, asyncio.CancelledError)), subs[0] if subs else e)   # 취소 아닌 것 우선
        if pick is e:                                        # 더 내려갈 수 없으면
            break                                            # 멈춤
        e = pick                                             # 한 단계 내려감
    return e                                                 # 원인


def _refused_suffix(e: BaseException, url: str) -> str:      # Node fetch 원인 글(연결 거부)
    """연결 거부면 ': connect ECONNREFUSED host:port'(undici 원인 글), 아니면 ''."""
    cur: Optional[BaseException] = e                         # 원인 사슬을 따라감
    while cur is not None:                                   # 끝까지
        if isinstance(cur, ConnectionRefusedError) or "refused" in str(cur).lower():   # 연결 거부
            p = urllib.parse.urlsplit(url)                   # 주소 분해
            port = p.port or (443 if p.scheme == "https" else 80)   # 포트(기본값)
            return f": connect ECONNREFUSED {p.hostname}:{port}"   # undici 글
        cur = cur.__cause__ or cur.__context__               # 다음 원인
    return ""                                                # 모름


def to_node_error(e: BaseException, conn: ServerConnection) -> McpClientError:   # 파이썬 SDK 오류 → cn(Node SDK) 원문 오류
    """getErrorString(error) 이 cn 에서 내놓을 글로 바꾼다. 모르는 오류는 str(e)."""
    import anyio                                             # 스트림 끊김 오류 형식
    import httpx2                                            # HTTP 오류 형식
    from mcp.shared.exceptions import MCPError               # 프로토콜 오류
    e = _leaf(e)                                             # 묶음 풀기
    if isinstance(e, McpClientError):                        # 이미 바꾼 것
        return e                                             # 그대로
    if conn.kind == "http" and conn.http_errors and isinstance(e, (MCPError, httpx2.HTTPError)):   # HTTP 오류 응답이 원인
        status, text = conn.http_errors[-1]                  # 마지막 오류 응답
        return McpClientError(f"Streamable HTTP error: Error POSTing to endpoint: {text}", status)   # StreamableHTTPError 원문
    if isinstance(e, MCPError):                              # JSON-RPC 오류
        return McpClientError(f"MCP error {e.code}: {e.error.message}", e.code)   # McpError: `MCP error ${code}: ${message}` 원문
    if isinstance(e, TimeoutError):                          # 시간 초과(anyio.fail_after)
        return McpClientError("MCP error -32001: Request timed out", -32001)   # RequestTimeout 원문
    if isinstance(e, (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream, BrokenPipeError, ConnectionResetError)):   # 연결 끊김
        return McpClientError("MCP error -32000: Connection closed", -32000)   # ConnectionClosed 원문
    if isinstance(e, OSError) and conn.kind == "stdio" and e.errno is not None:   # 자식 띄우기 실패
        code = errno.errorcode.get(e.errno, str(e.errno))    # ENOENT·EACCES 등
        return McpClientError(f"spawn {conn.config.get('command')} {code}")   # Node: `spawn ${command} ENOENT` 원문
    if conn.kind == "sse":                                   # SSE 전송이면
        if isinstance(e, httpx2.HTTPStatusError):            # 200 이 아님
            return McpClientError(f"SSE error: Non-200 status code ({e.response.status_code})", e.response.status_code)   # eventsource 글
        if isinstance(e, httpx2.TransportError):             # 연결 실패
            return McpClientError("SSE error: TypeError: fetch failed" + _refused_suffix(e, conn.config.get("url", "")))   # undici 글
    if conn.kind == "http" and isinstance(e, httpx2.TransportError):   # streamable-http 연결 실패
        return McpClientError("fetch failed")                # Node fetch: TypeError("fetch failed") 원문
    return McpClientError(str(e) or type(e).__name__)        # 그 밖(글이 없으면 형식 이름)


def is_auth_error(e: BaseException) -> bool:                 # mcpUtils.ts isAuthError 판정
    """SseError·StreamableHTTPError 의 401/405, 또는 글에 '401'·'405'·'Unauthorized'."""
    code = getattr(e, "code", None)                          # 코드
    msg = str(e)                                             # 글
    return code in (401, 405) or "401" in msg or "405" in msg or "Unauthorized" in msg   # 판정


def _transport_kinds(config: Mapping[str, Any]) -> list[str]:   # 시도할 전송 순서
    """command 가 있으면 stdio. 원격: type sse → sse, streamable-http → http, 없으면 http 다음 sse."""
    if "command" in config:                                  # "command" in serverConfig 이면
        return ["stdio"]                                     # stdio 하나
    t = config.get("type")                                   # 원격 종류
    if t == "sse":                                           # SSE 이면
        return ["sse"]                                       # 하나
    if t == "streamable-http":                               # streamable-http 이면
        return ["http"]                                      # 하나
    return ["http", "sse"]                                   # 자동: http → sse


class MCPService:                                            # cn MCPService 대응 클래스
    """연결 표(이름 → ServerConnection)와 도구 실행. initialize → get_tools/run_tool → shutdown."""

    def __init__(self, environ: Optional[Mapping[str, str]] = None):   # 환경변수(stdio 자식에게 넘김)
        self.environ = os.environ if environ is None else environ   # dotenv 적용된 process.env
        self.connections: dict[str, ServerConnection] = {}   # 이름 → 연결(삽입 순서 = 설정 순서)
        self.is_headless: bool = False                       # 헤드리스 여부
        self._api_key_cache: dict[str, str] = {}             # apiKeyCache(이름 → apiKey)
        self._pending: list[asyncio.Task] = []                # 기다리지 않은 접속 작업(wait=False)
        self._warned: set = set()                            # [ncn 부록B M3] 이미 경고한 도구 이름 겹침(같은 경고 반복 방지)

    # ───────── 초기화 ─────────
    async def initialize(self, servers: Optional[list], *, headless: bool, has_agent_file: bool = False, wait: Optional[bool] = None) -> None:   # doInitialize 대응(병렬 접속)
        """설정 mcpServers 에 병렬 접속. wait 기본 = headless or has_agent_file(cn). 헤드리스 실패는 예외."""
        self.is_headless = headless                          # 보관
        await self.shutdown()                                # 기존 연결 정리
        self.connections.clear()                             # 표 비움
        configs = [c for c in (servers or []) if c]          # null 블록은 건너뜀
        if not cn_compat():                                  # [ncn 부록B M3] 이름 겹침 처리(cn: 뒤의 것이 Map.set 으로 앞의 것을 덮음)
            seen: set = set()                                # 이미 본 이름
            kept = []                                        # 남길 설정
            for c in configs:                                # 설정 순서대로
                name = (c.get("name") or "") if isinstance(c, dict) else ""   # 서버 이름
                if name in seen:                             # 앞에 같은 이름이 있으면
                    _warn(f"MCP 서버 이름 '{name}' 이(가) 중복되어 뒤의 설정을 건너뜁니다")   # 경고
                    continue                                 # 뒤의 것은 건너뜀(먼저 온 것이 이김)
                seen.add(name)                               # 본 이름에 추가
                kept.append(c)                               # 남김
            configs = kept                                   # 겹침 뺀 목록
        coros = [self.connect_server(c) for c in configs]    # 서버마다 접속 코루틴
        if wait is None:                                     # 기본값
            wait = headless or has_agent_file                # cn: isHeadless || hasAgentFile 이면 기다림
        if not wait:                                         # 기다리지 않음(cn TUI)
            self._pending = [asyncio.ensure_future(c) for c in coros]   # 뒤에서 접속
            return                                           # 바로 돌아감
        await asyncio.gather(*coros)                         # 모두 끝날 때까지(각자 오류를 상태로 남김)
        if headless:                                         # 헤드리스: 실패가 있으면 던짐
            failed = [c for c in self.connections.values() if c.status == "error"]   # 실패한 연결
            if failed:                                       # 하나라도 있으면
                lines = "\n".join(f"{c.config.get('name')}: {c.error}" for c in failed)   # `${c.config?.name}: ${c.error}` 줄들
                raise Exception(f"MCP server(s) failed to load:\n{lines}")   # cn 원문

    async def wait_pending(self) -> None:                    # 뒤에서 하던 접속을 기다림(시험·종료용)
        """wait=False 로 시작한 접속이 끝날 때까지."""
        if self._pending:                                    # 있으면
            await asyncio.gather(*self._pending, return_exceptions=True)   # 기다림
            self._pending = []                               # 비움

    async def connect_server(self, server_config: dict) -> None:   # connectServer 대응
        """서버 하나 접속 → tools·prompts 목록. 실패는 상태에 남긴다(예외 없음 — FQSN 형식 오류만 cn 처럼 밖으로)."""
        conn = ServerConnection(config=dict(server_config))  # 새 연결 상태
        self.connections[conn.name] = conn                   # this.connections.set(name, connection) 와 같음
        variables = _sec.get_template_variables(_js_json(server_config))   # getTemplateVariables(JSON.stringify(serverConfig)) — 템플릿 변수
        unrendered = [_sec.decode_fqsn(v.replace("secrets.", "", 1))["secretName"] for v in variables if v.startswith("secrets.")]   # 못 찾은 비밀 이름
        try:                                                 # 접속 시도
            if unrendered:                                   # 남은 비밀이 있으면
                message = UNRESOLVED_SECRETS.format(name=conn.config.get("name"), secrets=", ".join(unrendered))   # 원문
                if self.is_headless:                         # 헤드리스는 실패
                    raise McpClientError(message)            # throw new Error(message) 와 같음
                conn.warnings.append(message)                # 대화형은 경고
            await self._open(conn)                           # getConnectedClient — 실패면 예외
            conn.status = "connected"                        # 연결됨
            caps = conn.session.server_capabilities          # client.getServerCapabilities() — 서버 능력
            if caps is not None and caps.prompts is not None:   # prompts 능력
                try:                                         # 목록 실패는 경고
                    conn.prompts = list((await self._request(conn, conn.session.list_prompts())).prompts)   # prompts/list — 목록 받기
                except Exception as e:                       # 실패
                    msg = str(to_node_error(e, conn))        # 오류 글
                    conn.warnings.append(f"Failed to load prompts: {msg}")   # 원문
                    log.warning("Failed to load MCP prompts: %s: %s", conn.name, msg)   # 기록
            if caps is not None and caps.tools is not None:  # tools 능력
                try:                                         # 목록 실패는 경고
                    conn.tools = await self._list_tools(conn)   # tools/list(부록B M2: 모든 쪽)
                except Exception as e:                       # 실패
                    msg = str(to_node_error(e, conn))        # 오류 글
                    conn.warnings.append(f"Failed to load tools: {msg}")   # 원문
                    log.warning("Failed to load MCP tools: %s: %s", conn.name, msg)   # 기록
        except Exception as e:                               # 접속 실패
            message = str(to_node_error(e, conn))            # getErrorString(error) 와 같음
            conn.status = "error"                            # 실패 상태
            if conn.warnings:                                # 경고(stderr 등)가 있으면 오류 글에 붙임
                conn.error = f"{message}\n\nServer stderr:\n" + "\n".join(conn.warnings)   # 원문 형식
                conn.warnings = []                           # 경고는 비움
            else:                                            # 없으면
                conn.error = message                         # 오류 글만
            log.error("Failed to connect to MCP server %s: %s", conn.name, conn.error)   # 기록

    async def _list_tools(self, conn: ServerConnection) -> list:   # tools/list 전부 받기
        """[ncn 부록B M2] nextCursor 가 있으면 다음 쪽을 계속 요청(최대 MAX_TOOL_PAGES 쪽).
        cn 원래 동작(호환 모드): 첫 쪽만 — client.listTools() 한 번, nextCursor 무시."""
        from mcp_types import PaginatedRequestParams         # 쪽 요청 인자(cursor)
        result = await self._request(conn, conn.session.list_tools())   # 첫 쪽
        tools = list(result.tools)                           # 첫 쪽 도구
        if cn_compat():                                      # cn 원래 동작
            return tools                                     # 첫 쪽만
        cursor = result.next_cursor                          # 다음 쪽 표시
        pages = 1                                            # 받은 쪽 수
        while cursor is not None:                            # 다음 쪽이 있는 동안
            if pages >= MAX_TOOL_PAGES:                      # 상한에 닿으면
                _warn(f"MCP 서버 '{conn.name}' 의 tools/list 가 {MAX_TOOL_PAGES}쪽을 넘어 나머지를 받지 않습니다")   # 경고
                break                                        # 멈춤(받은 것까지만)
            result = await self._request(conn, conn.session.list_tools(params=PaginatedRequestParams(cursor=cursor)))   # 다음 쪽
            tools.extend(result.tools)                       # 이어 붙임
            cursor = result.next_cursor                      # 그다음 쪽 표시
            pages += 1                                       # 쪽 수 증가
        return tools                                         # 전부

    # ───────── 전송·세션 ─────────
    def _timeout(self, conn: ServerConnection) -> float:     # 요청 시간 제한(초)
        """[ncn] requestOptions.timeout(초) 이 양수면 그 값, 아니면 Node SDK 기본 60초."""
        t = (conn.config.get("requestOptions") or {}).get("timeout")   # 설정 값
        return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0 else DEFAULT_REQUEST_TIMEOUT_S   # 결정

    async def _request(self, conn: ServerConnection, coro: Any) -> Any:   # 요청 하나(시간 제한)
        """시간 제한 안에 끝나지 않으면 TimeoutError(→ "MCP error -32001: Request timed out")."""
        import anyio                                         # 시간 제한
        conn.http_errors.clear()                             # 이번 요청의 HTTP 오류만 보려고 비움
        with anyio.fail_after(self._timeout(conn)):          # 제한
            return await coro                                # 응답

    async def _open(self, conn: ServerConnection) -> None:   # getConnectedClient — 연결을 붙잡는 작업을 띄우고 준비를 기다림
        """연결은 별도 작업이 문맥(anyio 작업 그룹) 안에서 붙잡고 있다가 stop 신호에 닫는다(같은 작업에서 열고 닫아야 함)."""
        loop = asyncio.get_running_loop()                    # 지금 루프
        ready: asyncio.Future = loop.create_future()         # 준비(성공/실패)
        conn.stop = asyncio.Event()                          # 닫기 신호
        conn.task = asyncio.ensure_future(self._hold(conn, ready))   # 붙잡는 작업
        await ready                                          # 준비 결과(실패면 예외)

    async def _hold(self, conn: ServerConnection, ready: asyncio.Future) -> None:   # 연결을 열고 stop 까지 붙잡음
        """전송 종류를 차례로 시도(자동은 http → sse). 성공하면 ready 완료, 실패하면 ready 에 오류."""
        from mcp_types import Implementation                 # 클라이언트 정보
        from mcp.client.session import ClientSession         # SDK 세션
        from ncn.mcp import transports as tr                 # 전송
        cfg = conn.config                                    # 설정
        kinds = _transport_kinds(cfg)                        # 시도 순서
        if "command" not in cfg and cfg.get("apiKey") and not self._api_key_cache.get(conn.name):   # apiKeyCache 채우기
            self._api_key_cache[conn.name] = cfg["apiKey"]   # 이름별 보관
        api_key = self._api_key_cache.get(conn.name)         # 캐시된 키
        for i, kind in enumerate(kinds):                     # 종류마다
            conn.kind = kind                                 # 오류 글 결정용
            conn.http_errors.clear()                         # 이전 시도 기록 지움
            collector = tr.StderrCollector(conn.warnings.append) if kind == "stdio" else None   # stdio 면 stderr 수집
            try:                                             # 이 종류로 시도
                if kind == "stdio":                          # stdio 전송
                    cm = tr.open_stdio(cfg, self.environ, collector)   # 자식 프로세스
                elif kind == "http":                         # streamable-http 전송
                    cm = tr.open_streamable_http(cfg, api_key, lambda s, t: conn.http_errors.append((s, t)))   # 오류 응답 기록
                else:                                        # sse 전송
                    cm = tr.open_sse(cfg, api_key)           # SSE 전송 열기
                async with cm as (read, write):              # 전송 열기
                    async with ClientSession(read, write, client_info=Implementation(name=CLIENT_NAME, version=CLIENT_VERSION)) as session:   # 세션
                        await self._request(conn, session.initialize())   # initialize(+ initialized 알림)
                        conn.session = session               # 보관
                        ready.set_result(None)               # 준비 완료
                        await conn.stop.wait()               # 닫기 신호까지 붙잡음
                return                                       # 정상 종료(닫힘)
            except BaseException as e:                       # 실패(또는 닫는 중 오류)
                if collector is not None:                    # stdio 면
                    collector.drain()                        # 남은 stderr 를 경고로 다 받음
                if ready.done():                             # 이미 연결된 뒤의 오류(서버가 죽음 등)
                    log.debug("MCP connection %s ended: %r", conn.name, e)   # 기록만
                    return                                   # 끝
                if isinstance(e, asyncio.CancelledError) and not isinstance(_leaf(e), Exception):   # 밖에서 취소
                    ready.set_exception(McpClientError("MCP error -32000: Connection closed", -32000))   # 닫힘으로
                    return                                   # 끝
                err = to_node_error(e, conn)                 # cn 글로
                if i + 1 < len(kinds) and not is_auth_error(err):   # 다음 종류가 있고 인증 오류가 아니면
                    continue                                 # SSE 로 다시
                ready.set_exception(err)                     # 실패 알림
                return                                       # 끝
            finally:                                         # 시도마다
                if collector is not None and ready.done():   # 연결이 끝났으면
                    collector.close_parent_end()             # 쓰기 끝 정리(이미 닫혔으면 무시)

    # ───────── 도구 ─────────
    def get_tools(self, builtin_names: Optional[frozenset] = None) -> list[Tool]:   # 연결된 서버의 도구를 ncn Tool 로(cn mcpState.tools.map(convert…))
        """updateState: 연결된 서버(삽입 순서)의 tools 를 이어 붙여 변환.
        [ncn 부록B M3] 이름이 겹치면 먼저 온 것만 — 내장 도구 이름(builtin_names, 기본 BUILTIN_TOOL_NAMES)과 겹치면 뺀다
        + stderr 경고(같은 겹침은 한 번만). cn 원래 동작(호환 모드): 겹쳐도 전부 넣는다(요청에 같은 이름이 두 번)."""
        from ncn.mcp.tools import convert_mcp_tool_to_continue_tool, mcp_tool_fields   # 변환·이름 꺼내기
        compat = cn_compat()                                 # cn 원래 동작인가
        reserved = BUILTIN_TOOL_NAMES if builtin_names is None else builtin_names   # 내장 이름
        seen: dict[str, str] = {}                            # 도구 이름 → 먼저 넣은 서버 이름
        out: list[Tool] = []                                 # 결과
        for conn in list(self.connections.values()):         # 연결마다(삽입 순서)
            if conn.status != "connected":                   # 연결된 것만
                continue                                     # 나머지는 건너뜀
            for t in conn.tools:                             # 서버가 준 순서
                name = mcp_tool_fields(t)[0]                 # 도구 이름
                if not compat:                               # [ncn 부록B M3] 겹침 검사
                    if name in reserved:                     # 내장 도구와 겹침
                        self._warn_once(("builtin", conn.name, name), f"MCP 서버 '{conn.name}' 의 도구 '{name}' 이(가) 내장 도구와 이름이 같아 내장 도구를 씁니다")   # 경고
                        continue                             # MCP 쪽은 뺌
                    if name in seen:                         # 다른(또는 같은) 서버에 이미 있음
                        self._warn_once(("dup", conn.name, name), f"MCP 도구 '{name}' 이(가) 서버 '{seen[name]}' 와 '{conn.name}' 에 모두 있어 '{seen[name]}' 것만 씁니다")   # 경고
                        continue                             # 뒤의 것은 뺌(run_tool 도 먼저 온 서버를 부름)
                    seen[name] = conn.name                   # 먼저 온 서버 기억
                out.append(convert_mcp_tool_to_continue_tool(t, self.run_tool))   # 변환해 추가
        return out                                           # 결과

    def _warn_once(self, key: tuple, message: str) -> None:  # [ncn 부록B M3] 같은 겹침 경고는 한 번만
        """get_tools 는 세션·턴마다 불릴 수 있어 key 로 중복 경고를 막는다."""
        if key not in self._warned:                          # 처음이면
            self._warned.add(key)                            # 기억
            _warn(message)                                   # 경고

    def get_prompts(self) -> list:                           # 연결된 서버의 프롬프트(cn: 대화형 슬래시 명령에만 씀)
        """connectedServers.flatMap(s => s.prompts)."""
        return [p for c in self.connections.values() if c.status == "connected" for p in c.prompts]   # 모음

    async def run_tool(self, name: str, args: dict) -> Any:  # runTool 대응
        """그 이름의 도구가 있는 첫 연결된 서버에 tools/call. 결과 객체(CallToolResult)를 돌려준다."""
        for conn in list(self.connections.values()):         # 연결마다
            if conn.status == "connected" and conn.session is not None:   # 연결됨
                if any(getattr(t, "name", None) == name for t in conn.tools):   # 도구 찾음
                    try:                                     # 호출
                        return await self._request(conn, conn.session.call_tool(name, args))   # client.callTool({name, arguments}) 호출(시간 제한)
                    except Exception as e:                   # 실패 → cn 글로
                        raise to_node_error(e, conn) from e  # 루프가 "Error executing tool …" 을 붙인다
        raise Exception(f"Tool {name} not found")            # cn 원문

    # ───────── 종료 ─────────
    async def shutdown(self) -> None:                        # shutdownConnections 대응
        """모든 연결 닫기(stop 신호 → 붙잡던 작업이 문맥을 빠져나오며 자식·HTTP 세션 정리)."""
        for t in self._pending:                              # 기다리지 않던 접속 작업
            if not t.done():                                 # 아직이면
                t.cancel()                                   # 취소
        self._pending = []                                   # 비움
        tasks = []                                           # 기다릴 작업
        for conn in list(self.connections.values()):         # 연결마다
            if conn.stop is not None:                        # 붙잡는 작업이 있으면
                conn.stop.set()                              # 닫기 신호
            if conn.task is not None:                        # 작업
                tasks.append(conn.task)                      # 기다릴 목록에
            conn.status, conn.session, conn.tools, conn.prompts, conn.error = "idle", None, [], [], None   # shutdownConnection 상태 초기화
            conn.warnings = []                               # 경고 비움
        if tasks:                                            # 기다릴 것이 있으면
            done, pending = await asyncio.wait(tasks, timeout=10)   # 정리 제한(SDK stdio 정리는 최대 몇 초)
            for t in pending:                                # 못 끝낸 작업
                t.cancel()                                   # 취소


def _js_json(v: Any) -> str:                                 # JSON.stringify(설정) — 템플릿 변수 찾기용
    """공백 없는 JSON(한글 그대로)."""
    import json                                              # JSON 모듈
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))   # 글
