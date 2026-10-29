# ncn/mcp/acp_servers.py — ACP session/new·session/load 의 mcpServers(McpServerStdio·HttpMcpServer·SseMcpServer)를
#   cn 설정 mcpServers 항목 모양으로 바꾼다 → 설정에서 온 서버와 같은 연결 코드(ncn/mcp/service.py)를 쓴다.
# 대응: stdio {name, command, args, env:[{name,value}]} → {name, command, args, env:{이름: 값}}
#       http  {type:"http", name, url, headers:[{name,value}]} → {name, type:"streamable-http", url, requestOptions:{headers}}
#       sse   {type:"sse", …} → {name, type:"sse", url, requestOptions:{headers}}
#       acp   (규격 불안정, ACP 연결 위의 MCP) → 지원하지 않음(로그 후 건너뜀)
from __future__ import annotations                           # 타입 주석 지연 평가

import logging                                               # 로그(stderr)
from typing import Any, Optional                             # 타입

log = logging.getLogger("ncn.mcp")                           # 로거


def _get(obj: Any, key: str, default: Any = None) -> Any:    # pydantic 객체·dict 둘 다에서 값 꺼내기
    """dict 면 키, 객체면 속성."""
    if isinstance(obj, dict):                                # dict
        return obj.get(key, default)                         # 키
    return getattr(obj, key, default)                        # 속성


def _pairs(items: Any) -> dict[str, str]:                    # [{name, value}] → {name: value}
    """EnvVariable·HttpHeader 목록을 사전으로(같은 이름은 뒤의 것)."""
    return {str(_get(i, "name")): str(_get(i, "value", "")) for i in (items or [])}   # 변환


def acp_server_to_config(server: Any) -> Optional[dict]:     # ACP McpServer 하나 → cn 설정 항목
    """모르는 종류(acp 등)는 None."""
    kind = _get(server, "type")                              # 종류(stdio 는 type 이 없음)
    name = _get(server, "name") or ""                        # 이름
    if kind is None and _get(server, "command"):             # stdio
        cfg: dict = {"name": name, "command": _get(server, "command"), "args": list(_get(server, "args") or [])}   # 명령·인자
        env = _pairs(_get(server, "env"))                    # 환경변수
        if env:                                              # 있으면
            cfg["env"] = env                                 # 넣음
        return cfg                                           # stdio 설정
    if kind in ("http", "sse"):                              # 원격
        cfg = {"name": name, "type": "streamable-http" if kind == "http" else "sse", "url": _get(server, "url")}   # 종류·주소
        headers = _pairs(_get(server, "headers"))            # 헤더
        if headers:                                          # 있으면
            cfg["requestOptions"] = {"headers": headers}     # cn requestOptions.headers 로
        return cfg                                           # 원격 설정
    log.warning("지원하지 않는 ACP MCP 서버 종류 무시: %s (%s)", kind, name)   # 기록
    return None                                              # 건너뜀


def acp_servers_to_configs(servers: Any) -> list[dict]:      # 목록 변환
    """None 은 빼고 차례대로."""
    out = []                                                 # 결과
    for s in servers or []:                                  # 서버마다
        c = acp_server_to_config(s)                          # 변환
        if c is not None:                                    # 지원하는 것만
            out.append(c)                                    # 추가
    return out                                               # 결과
