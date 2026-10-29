# ncn/acp/stdio.py — stdio 모드의 stdout 확보와 줄 단위 JSON 전송(계획 14-2 V5). cn 원본 없음(ncn 추가)
# 규칙: stdout(파일 기술자 1)에는 프로토콜 JSON 줄만. 시작하자마자
#   ① 진짜 stdout 을 복제해 프로토콜 전용으로 보관 ② 기술자 1 을 stderr 로 돌림(C 확장·자식 프로세스가 1에 써도 stderr 로)
#   ③ sys.stdout 도 stderr 로(print 가 프로토콜을 깨지 않게) ④ 로그는 stderr.
# 쓰기는 UTF-8 바이트를 직접(Windows 텍스트 모드 \r\n 변환·cp949 없음), 한 메시지 = JSON 한 줄 + "\n".
# 읽기는 별도 스레드가 stdin 바이너리 버퍼를 줄 단위로 읽어 큐에 넣는다(Windows 파이프도 같은 방법 — SDK 와 같음).
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 큐·스레드 넘기기
import json                                                  # 메시지 직렬화
import logging                                               # 로그
import os                                                    # 파일 기술자 복제
import sys                                                   # 표준 입출력
import threading                                             # stdin 읽기 스레드
from typing import Any, BinaryIO, Optional                   # 타입

log = logging.getLogger("ncn.acp.stdio")                     # 로거


def reserve_stdout() -> BinaryIO:                           # stdout 을 프로토콜 전용으로 확보
    """진짜 stdout 을 복제한 바이너리 파일을 돌려주고, 기술자 1·sys.stdout 은 stderr 로 돌린다."""
    try:                                                    # 남은 출력이 있으면 먼저 비움
        sys.stdout.flush()                                  # (아직 아무것도 안 썼어야 정상)
    except Exception:                                        # 닫힌 stdout 등
        pass                                                # 무시
    proto_fd = os.dup(1)                                     # 진짜 stdout 복제(프로토콜 전용)
    if sys.platform == "win32":                              # Windows: 텍스트 모드면 \n → \r\n
        import msvcrt                                        # Windows 전용 모듈
        msvcrt.setmode(proto_fd, os.O_BINARY)                # 바이너리 모드로(줄바꿈 변환 끔)
    os.dup2(2, 1)                                            # 기술자 1 → stderr(자식·C 코드의 쓰기도 stderr 로)
    sys.stdout = sys.stderr                                  # print → stderr
    return os.fdopen(proto_fd, "wb", buffering=0)            # 버퍼 없는 바이너리 파일


def encode_message(message: dict) -> bytes:                 # 메시지 → 한 줄 바이트
    """JSON 한 줄(공백 없는 구분자, 한글 그대로) + "\\n" 을 UTF-8 로. JSON 안의 줄바꿈은 \\n 으로 이스케이프되므로 한 줄이 보장된다."""
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")   # UTF-8 바이트


class StdioTransport:                                       # acp Transport(메시지 단위) 구현
    """acp Connection 에 넘기는 메시지 단위 전송(send/receive/close). 읽기 = 스레드 + 큐, 쓰기 = 바이너리 직접."""

    def __init__(self, out: BinaryIO, inp: Optional[BinaryIO] = None):   # 생성자: 출력·입력 파일
        self._out = out                                      # 프로토콜 전용 출력
        self._inp = inp if inp is not None else sys.stdin.buffer   # 입력(기본 stdin 바이너리)
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()   # 읽은 줄(None = 끝)
        self._lock = asyncio.Lock()                          # 쓰기 순서 보장
        self._closed = False                                 # 닫혔나
        loop = asyncio.get_running_loop()                    # 지금 이벤트 루프

        def reader() -> None:                                # stdin 읽기 스레드 본문
            """한 줄씩 읽어 큐로 넘긴다. 끝(EOF)이면 None."""
            try:                                            # 읽기 오류도 끝으로 처리
                while True:                                 # 끝날 때까지
                    line = self._inp.readline()             # 한 줄(바이트)
                    if not line:                            # EOF
                        break                               # 끝
                    loop.call_soon_threadsafe(self._queue.put_nowait, line)   # 이벤트 루프 쪽 큐에 넣음
            except Exception as e:                           # 읽기 실패
                log.debug("stdin 읽기 끝: %s", e)            # 기록
            finally:                                        # 마지막에
                try:                                        # 루프가 이미 닫혔을 수 있음
                    loop.call_soon_threadsafe(self._queue.put_nowait, None)   # 끝 표시
                except RuntimeError:                         # 루프 닫힘
                    pass                                    # 무시

        threading.Thread(target=reader, name="ncn-acp-stdin", daemon=True).start()   # 데몬 스레드로 시작

    async def send(self, message: dict[str, Any]) -> None:   # 메시지 한 개 보내기
        """한 줄로 직렬화해 바로 쓰고 비운다(여러 작업이 동시에 보내도 줄이 섞이지 않게 잠금)."""
        if self._closed:                                     # 닫혔으면
            raise ConnectionError("Transport closed")        # 보낼 수 없음
        data = encode_message(message)                       # 한 줄 바이트
        async with self._lock:                               # 순서·원자성 보장
            await asyncio.to_thread(self._write_all, data)   # 파이프가 차도 이벤트 루프를 막지 않게 스레드에서

    def _write_all(self, data: bytes) -> None:              # 끝까지 쓰기(버퍼 없음이라 부분 쓰기 대비)
        """write 가 일부만 쓸 수 있으므로 남은 것까지 반복."""
        view = memoryview(data)                              # 복사 없이 자르기
        while view:                                         # 남은 바이트가 있는 동안
            n = self._out.write(view)                        # 쓴 바이트 수
            view = view[n or 0:]                             # 남은 부분

    async def receive(self) -> Optional[dict[str, Any]]:    # 메시지 한 개 받기
        """빈 줄은 건너뛰고, JSON 이 아닌 줄은 stderr 로그만 남기고 버린다. 끝이면 None."""
        while True:                                         # 메시지가 나올 때까지
            line = await self._queue.get()                   # 한 줄
            if line is None:                                 # 끝
                return None                                 # 연결 끝
            line = line.strip()                              # 앞뒤 공백·\r\n 제거
            if not line:                                    # 빈 줄
                continue                                    # 건너뜀
            try:                                            # JSON 해석
                msg = json.loads(line.decode("utf-8"))       # UTF-8 로 풀어 해석
            except Exception:                                # 깨진 줄
                log.error("JSON 이 아닌 입력 줄을 버림: %r", line[:200])   # stderr 로그
                continue                                    # 다음 줄
            if isinstance(msg, dict):                        # 객체면
                return msg                                  # 메시지
            log.error("객체가 아닌 JSON 입력을 버림: %r", line[:200])   # 배열(일괄 요청)은 미지원

    async def close(self) -> None:                           # 닫기
        """더 쓰지 않는다(파일은 프로세스 끝에 닫힘)."""
        self._closed = True                                  # 닫힘 표시
