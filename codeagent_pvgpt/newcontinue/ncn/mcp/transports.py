# ncn/mcp/transports.py — MCP 전송 만들기. cn extensions/cli/src/services/mcpTransports.ts(constructStdioTransport·
#   constructSseTransport·constructHttpTransport) 를 공식 mcp 파이썬 SDK 2.x 전송(stdio_client·sse_client·streamable_http_client)으로
# 같게 맞춘 것: 헤더 = requestOptions.headers + (apiKey 면 Authorization: Bearer), verifySsl:false 면 인증서 검사 끔,
#   stdio 환경변수 = 설정 env + (설정에 없는 키만) 현재 환경변수 중 빈 값이 아닌 것, stderr 는 경고로 모은다(data.toString().trim()).
# cn 은 프록시 환경변수를 쓰지 않는다(Node fetch) → httpx2 도 trust_env=False.
# HTTP 오류 응답(4xx·5xx)은 본문을 기록해 두었다가 cn 원문 오류 글("Streamable HTTP error: Error POSTing to endpoint: …")을 만드는 데 쓴다.
from __future__ import annotations                           # 타입 주석 지연 평가

import contextlib                                            # 비동기 문맥 관리자
import os                                                    # 파이프·환경변수
import threading                                             # stderr 읽는 스레드
from typing import Any, AsyncIterator, Callable, Mapping, Optional   # 타입

import httpx2                                                # mcp SDK 가 쓰는 HTTP 클라이언트(httpx 의 SDK 판)

MCP_DEFAULT_TIMEOUT = 30.0                                   # SDK 기본: 연결·쓰기 30초
MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0                         # SDK 기본: 읽기 300초(스트림을 오래 열어 둠)


def build_headers(config: Mapping[str, Any], api_key: Optional[str]) -> dict[str, str]:   # 원격 서버 요청 헤더
    """{...requestOptions.headers, ...(apiKey && {Authorization: `Bearer ${apiKey}`})}."""
    headers = dict(((config.get("requestOptions") or {}).get("headers")) or {})   # 설정 헤더(복사)
    if api_key:                                              # apiKey 가 참값이면
        headers["Authorization"] = f"Bearer {api_key}"       # 덮어씀(뒤에 펼친 쪽이 이김)
    return headers                                           # 헤더 반환


def _verify(config: Mapping[str, Any]) -> bool:              # 인증서 검사 여부
    """requestOptions.verifySsl === false 일 때만 끈다(그 밖은 켬)."""
    return (config.get("requestOptions") or {}).get("verifySsl") is not False   # false 가 아니면 검사


def make_http_client(config: Mapping[str, Any], api_key: Optional[str], on_http_error: Optional[Callable[[int, str], None]] = None,   # httpx2 클라이언트
                     headers: Optional[dict] = None, timeout: Any = None) -> httpx2.AsyncClient:   # (인자 이어짐)
    """SDK create_mcp_http_client 와 같은 시간 제한 + cn 헤더·verifySsl + 오류 응답 본문 기록 훅."""
    async def _hook(response: httpx2.Response) -> None:      # 응답 훅(POST 오류만)
        if on_http_error is not None and response.request.method == "POST" and response.status_code >= 400:   # POST 4xx·5xx
            try:                                            # 본문 읽기(다시 읽어도 저장된 내용이 나옴)
                await response.aread()                       # 본문 받기
                text = response.text                         # 글로(Node response.text())
            except Exception:                                # 못 읽으면
                text = "null"                                # cn: .catch(() => null) → 템플릿에서 "null"
            on_http_error(response.status_code, text)        # 기록

    return httpx2.AsyncClient(                               # 클라이언트 생성
        headers=build_headers(config, api_key) if headers is None else headers,   # 헤더
        timeout=timeout or httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),   # SDK 기본 시간
        verify=_verify(config),                              # verifySsl
        trust_env=False,                                     # 프록시 환경변수 무시(Node fetch 와 같음)
        event_hooks={"response": [_hook]},                   # 오류 응답 기록
    )                                                        # 끝


@contextlib.asynccontextmanager                              # 비동기 문맥 관리자로
async def open_streamable_http(config: Mapping[str, Any], api_key: Optional[str],   # streamable-http 전송 열기
                               on_http_error: Optional[Callable[[int, str], None]] = None) -> AsyncIterator[Any]:   # (인자 이어짐)
    """constructHttpTransport — StreamableHTTPClientTransport(new URL(url), {requestInit: {headers}})."""
    from mcp.client.streamable_http import streamable_http_client   # SDK 전송
    async with make_http_client(config, api_key, on_http_error) as client:   # 클라이언트(우리가 닫음)
        async with streamable_http_client(config["url"], http_client=client) as streams:   # 전송
            yield streams                                    # (read, write)


@contextlib.asynccontextmanager                              # 비동기 문맥 관리자로
async def open_sse(config: Mapping[str, Any], api_key: Optional[str]) -> AsyncIterator[Any]:   # SSE 전송 열기
    """constructSseTransport — SSEClientTransport(new URL(url), {eventSourceInit, requestInit: {headers}})."""
    from mcp.client.sse import sse_client                    # SDK 전송

    def factory(headers=None, timeout=None, auth=None):      # SDK 가 부르는 클라이언트 공장
        return make_http_client(config, api_key, None, headers=headers, timeout=timeout)   # 같은 설정

    async with sse_client(config["url"], headers=build_headers(config, api_key), httpx_client_factory=factory) as streams:   # 전송
        yield streams                                        # (read, write)


def stdio_env(config: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, str]:   # stdio 서버 환경변수
    """env = serverConfig.env || {}; process.env 중 env 에 없고 값이 참(빈 글 아님)인 것만 더함."""
    env = dict(config.get("env") or {})                      # 설정 env(복사)
    for k, v in environ.items():                             # 현재 환경변수
        if k not in env and v:                               # 없고 빈 글이 아니면
            env[k] = v                                       # 더함
    return env                                               # 결과


class StderrCollector:                                       # stdio 서버의 stderr 를 경고로 모으는 것
    """os.pipe 의 쓰기 끝을 자식 stderr 로 주고, 읽기 끝은 스레드가 읽어 조각마다 trim 해서 sink 에 넣는다(Node 'data' 이벤트 흉내)."""

    def __init__(self, sink: Callable[[str], None]):         # 넣을 곳(경고 목록 추가 함수)
        r, w = os.pipe()                                     # 파이프
        self._r = os.fdopen(r, "rb", buffering=0)            # 읽기 끝(바이트, 버퍼 없음 — 조각 단위)
        self.w = os.fdopen(w, "w", encoding="utf-8")         # 쓰기 끝(자식에게 줄 파일 객체)
        self._sink = sink                                    # 넣을 곳
        self._thread = threading.Thread(target=self._pump, name="ncn-mcp-stderr", daemon=True)   # 읽기 스레드
        self._thread.start()                                 # 시작

    def _pump(self) -> None:                                 # 스레드 본문
        """EOF(자식·부모 쓰기 끝이 모두 닫힘)까지 읽는다."""
        try:                                                # 읽기 오류는 조용히 끝
            while True:                                     # 끝까지
                data = self._r.read(65536)                   # 조각 하나
                if not data:                                 # EOF
                    break                                    # 끝
                text = data.decode("utf-8", "replace").strip()   # data.toString().trim()
                if text:                                     # 비어 있지 않으면
                    self._sink(text)                         # 경고에 추가
        except Exception:                                    # 읽기 실패
            pass                                             # 무시
        finally:                                             # 읽기 끝 닫기
            try:                                            # 닫기
                self._r.close()                              # 닫음
            except Exception:                                # 이미 닫힘
                pass                                         # 무시

    def close_parent_end(self) -> None:                      # 부모의 쓰기 끝 닫기(자식이 뜬 뒤)
        """자식은 자기 사본을 가진다 — 부모 쪽을 닫아야 자식이 끝날 때 EOF 가 온다."""
        try:                                                # 닫기
            self.w.close()                                   # 닫음
        except Exception:                                    # 이미 닫힘
            pass                                             # 무시

    def drain(self, timeout: float = 0.5) -> None:           # 남은 stderr 를 다 읽을 때까지 잠깐 기다림
        """접속 실패 글에 stderr 를 붙이기 전에 부른다(자식이 끝났으면 곧 EOF)."""
        self.close_parent_end()                              # 부모 쪽 닫기
        self._thread.join(timeout)                           # 스레드 끝 기다림(제한 있음)


@contextlib.asynccontextmanager                              # 비동기 문맥 관리자로
async def open_stdio(config: Mapping[str, Any], environ: Mapping[str, str], collector: StderrCollector) -> AsyncIterator[Any]:   # stdio 전송 열기
    """constructStdioTransport — StdioClientTransport({command, args: args || [], env, cwd, stderr: "pipe"})."""
    from mcp.client.stdio import StdioServerParameters, stdio_client   # SDK 전송
    params = StdioServerParameters(command=config["command"], args=list(config.get("args") or []),   # 명령·인자
                                   env=stdio_env(config, environ), cwd=config.get("cwd") or None)   # 환경변수·작업 폴더
    async with stdio_client(params, errlog=collector.w) as streams:   # 자식 띄움(stderr 는 파이프로)
        collector.close_parent_end()                         # 자식이 떴으니 부모 쪽 쓰기 끝은 닫는다
        yield streams                                        # (read, write)
