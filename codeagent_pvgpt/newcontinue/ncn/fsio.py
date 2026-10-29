# ncn/fsio.py — 파일 읽기·쓰기 창구(계획 14-2 V3). cn 원본 없음(ncn 추가: ACP fs/* 위임 준비)
# 기본은 디스크(ncn.tools.formatters 의 Node fs 흉내 함수). ACP 세션이 붙으면 그 세션의 작업(asyncio 컨텍스트)에서만
#   IDE 위임 구현(AcpFiles)으로 바뀐다 — 열린 버퍼 기준 읽기, 텍스트 버퍼 편집(Ctrl+Z 가능), 인코딩은 IDE가 유지.
# IDE가 능력을 알리지 않았거나 요청이 실패(거절)하면 디스크로 되돌아간다(V3: "확장이 거절하거나 터미널 모드면 디스크").
# 도구 쪽 연결(다른 작업자): Read/Edit/MultiEdit/Write 의 read_file_utf8·write_file_utf8 호출을
#   `await fsio.read_text_file(p)`·`await fsio.write_text_file(p, s)` 로 바꾸면 된다(도구 run·preprocess 는 이미 async).
# [ncn E3] 디스크 경로는 인코딩·BOM·줄바꿈 보존(ncn/encoding.py). IDE 위임(AcpFiles) 경로는 IDE가 인코딩을 관리하므로 E3 변환을 하지 않는다
#   (위임이 실패해 디스크로 되돌아가면 그때는 E3 적용). E3 가 꺼져 있으면(ncn.yaml encoding.preserve: false·NCN_CN_COMPAT=1) cn과 같은 UTF-8.
from __future__ import annotations                           # 타입 주석 지연 평가

import contextvars                                           # 세션(작업)별 구현 보관
import logging                                               # 로그(stdout 금지 — stdio 모드)
import os                                                    # 절대 경로 만들기
from typing import Any, Optional, Protocol                   # 타입

log = logging.getLogger("ncn.fsio")                          # 로거


class FileBackend(Protocol):                                # 파일 창구 구현이 갖출 모양
    """파일 창구 구현 — 읽기·쓰기 두 가지(둘 다 async)."""

    async def read_text(self, path: str) -> str: ...        # 글 읽기(없으면 Node 형식 오류)

    async def write_text(self, path: str, content: str) -> None: ...   # 글 쓰기(통째로 바꿈)


class DiskFiles:                                            # 기본 구현: 디스크 직접
    """디스크 직접 읽기·쓰기 — Node readFileSync/writeFileSync(utf-8) 흉내 함수를 그대로 쓴다(오류 글도 같음)."""

    async def read_text(self, path: str) -> str:            # 디스크에서 읽기
        """read_file_utf8 — BOM 유지, 줄바꿈 변환 없음. [ncn E3] 켜져 있으면 인코딩 판별(BOM·줄바꿈 그대로)."""
        from ncn import encoding                             # [ncn E3] 인코딩 보존
        from ncn.tools.formatters import read_file_utf8      # 지연 import(도구 모듈과 순환 방지)
        if encoding.enabled():                               # [ncn E3] 판별해 읽기(cn: 항상 UTF-8)
            return encoding.read_text(path)                  # 판별 결과 글
        return read_file_utf8(path)                          # 디스크 내용 반환

    async def write_text(self, path: str, content: str) -> None:   # 디스크에 쓰기
        """write_file_utf8 — 줄바꿈 변환 없이 그대로. [ncn E3] 켜져 있으면 기존 파일의 인코딩·BOM·다수 줄바꿈으로."""
        from ncn import encoding                             # [ncn E3] 인코딩 보존
        from ncn.tools.formatters import write_file_utf8     # 지연 import
        if encoding.enabled():                               # [ncn E3] 원래 형식으로 쓰기(cn: 항상 UTF-8)
            encoding.write_text(path, content)               # 표현 못 하는 글자면 EncodingSaveError(아무것도 안 씀)
            return                                          # 끝
        write_file_utf8(path, content)                       # 디스크에 씀


class AcpFiles:                                             # ACP 위임 구현: fs/read_text_file·fs/write_text_file
    """IDE(ACP 클라이언트)에 위임. 능력이 없거나 실패하면 디스크(fallback)로 되돌아간다.

    conn: acp AgentSideConnection(read_text_file·write_text_file 을 가진 것), session_id: ACP 세션 id,
    can_read/can_write: initialize 의 clientCapabilities.fs.readTextFile / writeTextFile."""

    def __init__(self, conn: Any, session_id: str, *, can_read: bool, can_write: bool,  # 생성자: 연결·세션·능력
                 fallback: Optional[FileBackend] = None):    # 되돌아갈 구현(기본 디스크)
        self.conn = conn                                     # 클라이언트에 요청을 보낼 연결
        self.session_id = session_id                         # 요청에 실을 세션 id
        self.can_read = can_read                             # IDE가 읽기를 받는가
        self.can_write = can_write                           # IDE가 쓰기를 받는가
        self.fallback = fallback or DiskFiles()              # 실패 시 디스크
        self.delegated: list[tuple[str, str]] = []           # 위임 기록(("read"|"write", 경로)) — 진단·시험용

    @staticmethod
    def _abs(path: str) -> str:                              # ACP 는 절대 경로만 받는다
        """상대 경로면 현재 작업 폴더 기준 절대 경로로(ACP 규격: path 는 절대 경로)."""
        return path if os.path.isabs(path) else os.path.abspath(path)   # 절대면 그대로, 아니면 붙임

    async def read_text(self, path: str) -> str:            # 읽기 위임
        """fs/read_text_file — 열린 버퍼(저장 안 한 변경 포함) 기준. 실패하면 디스크."""
        if not self.can_read:                                # 능력이 없으면
            return await self.fallback.read_text(path)      # 바로 디스크
        try:                                                # IDE 요청 시도
            r = await self.conn.read_text_file(session_id=self.session_id, path=self._abs(path))   # IDE 에 읽기 요청
            self.delegated.append(("read", self._abs(path)))  # 위임 기록
            return r.content                                 # IDE 가 준 내용
        except Exception as e:                               # 거절·오류(없는 파일 포함)
            log.debug("fs/read_text_file 실패 → 디스크: %s (%s)", path, e)   # 디스크로 되돌아감을 기록
            return await self.fallback.read_text(path)      # 디스크(없는 파일이면 Node 형식 오류가 여기서 남)

    async def write_text(self, path: str, content: str) -> None:   # 쓰기 위임
        """fs/write_text_file — IDE 텍스트 버퍼 편집. 실패하면 디스크."""
        if not self.can_write:                               # 능력이 없으면
            await self.fallback.write_text(path, content)   # 바로 디스크
            return                                          # 끝
        try:                                                # IDE 요청 시도
            await self.conn.write_text_file(session_id=self.session_id, path=self._abs(path), content=content)   # IDE 에 쓰기 요청
            self.delegated.append(("write", self._abs(path)))  # 위임 기록
        except Exception as e:                               # 거절·오류
            log.warning("fs/write_text_file 실패 → 디스크에 씀: %s (%s)", path, e)   # 경고(사용자가 알아야 함)
            await self.fallback.write_text(path, content)   # 디스크에 씀


_DISK = DiskFiles()                                          # 공용 기본 구현
_current: contextvars.ContextVar[Optional[FileBackend]] = contextvars.ContextVar("ncn_fsio_backend", default=None)   # 지금 작업의 구현


def get_backend() -> FileBackend:                           # 지금 쓰는 구현
    """현재 asyncio 작업(컨텍스트)의 구현 — 설정이 없으면 디스크."""
    return _current.get() or _DISK                           # 없으면 디스크


def set_backend(backend: Optional[FileBackend]) -> contextvars.Token:   # 구현 바꾸기
    """이 컨텍스트(와 여기서 만든 하위 작업)의 구현을 바꾼다. 돌려준 토큰으로 reset_backend."""
    return _current.set(backend)                             # 토큰 반환


def reset_backend(token: contextvars.Token) -> None:        # 바꾸기 전으로
    """set_backend 이전 상태로 되돌린다."""
    _current.reset(token)                                    # 되돌림


async def read_text_file(path: str) -> str:                 # 공개 읽기 함수(도구가 쓸 것)
    """파일 글 읽기 — ACP 세션 안이면 IDE 위임, 아니면 디스크."""
    return await get_backend().read_text(path)              # 현재 구현으로 읽음


async def write_text_file(path: str, content: str) -> None:  # 공개 쓰기 함수(도구가 쓸 것)
    """파일 글 쓰기 — ACP 세션 안이면 IDE 위임, 아니면 디스크."""
    await get_backend().write_text(path, content)            # 현재 구현으로 씀


def read_text_file_disk(path: str) -> Optional[str]:        # 동기 디스크 읽기(없으면 None)
    """동기 디스크 읽기 — 이벤트 처리처럼 기다릴 수 없는 곳용(diff 의 원래 글 등). 없거나 못 읽으면 None."""
    from ncn import encoding                                 # [ncn E3] 인코딩 보존
    from ncn.tools.formatters import read_file_utf8          # 지연 import
    try:                                                    # 읽기 시도
        if encoding.enabled():                               # [ncn E3] 판별해 읽기
            return encoding.read_text(path)                  # 판별 결과 글
        return read_file_utf8(path)                          # 내용 반환
    except Exception:                                        # 없는 파일·폴더·권한
        return None                                         # 없음으로 처리


def writes_to_ide() -> bool:                                # [ncn E4] 지금 쓰기가 IDE 위임인지
    """현재 구현이 쓰기를 IDE 에 위임하면 참(백업은 IDE 버퍼 글로, 되돌림은 IDE 쓰기 요청으로)."""
    b = get_backend()                                        # 현재 구현
    return isinstance(b, AcpFiles) and b.can_write           # 위임 + 쓰기 능력
