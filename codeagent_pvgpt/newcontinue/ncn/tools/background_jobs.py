# ncn/tools/background_jobs.py — 백그라운드 작업 관리. cn extensions/cli/src/services/BackgroundJobService.ts + util/backgroundSignalManager.ts
# Bash 실행 중 Ctrl+B(대화형, P2)로 넘긴 명령과 runCommandInBackground로 띄운 명령을 최대 5개까지 추적한다
# 자식 프로세스는 asyncio로 띄우고, stdout/stderr 를 읽는 작업이 "받는 쪽(sink)" 함수를 부른다 — 받는 쪽을 바꿔 끼우면 Bash → 백그라운드로 넘어간다
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 자식 프로세스·읽기 작업
import codecs                                                # 조각 단위 UTF-8 해석
import sys                                                   # stderr 기록
import time                                                  # 현재 시각 밀리초(Date.now) 계산용
from dataclasses import dataclass                            # 작업 자료
from datetime import datetime, timezone                      # 시작·종료 시각 표현과 ISO 문자열 변환용
from typing import Callable, Optional                        # 타입

MAX_CONCURRENT_JOBS = 5                                      # 동시에 돌 수 있는 작업 수(cn 원문 값)
MAX_OUTPUT_LINES = 1000                                      # 작업마다 남기는 출력 줄 수(뒤쪽 유지)


def _now_ms() -> int:                                        # Date.now() 대응 함수 정의
    """Date.now() — 1970년부터 밀리초."""
    return int(time.time() * 1000)                           # 정수 밀리초


def iso_string(dt: datetime) -> str:                         # toISOString 대응 함수 정의
    """Date.prototype.toISOString() — 'YYYY-MM-DDTHH:MM:SS.mmmZ'(UTC, 밀리초 3자리)."""
    u = dt.astimezone(timezone.utc)                          # UTC로
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"   # 밀리초까지


def _now() -> datetime:                                      # new Date() 대응 함수 정의
    """new Date() — 밀리초 단위로 자른 현재 시각(JS Date는 밀리초 정밀도)."""
    d = datetime.now(timezone.utc)                           # 현재 UTC
    return d.replace(microsecond=(d.microsecond // 1000) * 1000)   # 밀리초 아래 버림


class _Logger:                                               # 로그 대용 클래스(stdout은 ACP 전용이라 stderr만 사용)
    """cn logger 대신 — stdout 은 ACP 전용이므로 stderr 에만(그것도 기본은 조용히)."""
    enabled = False                                          # 기본 꺼짐(로그 모듈은 다른 단계)

    def warn(self, msg: str) -> None:                        # 경고 출력 메서드
        if self.enabled:                                     # 켰을 때만
            print(msg, file=sys.stderr)                      # 켜져 있을 때만 stderr로 출력

    error = warn                                             # 같은 처리
    debug = warn                                             # 같은 처리


logger = _Logger()                                           # 모듈 공용


# ───────────────────────── 자식 프로세스 손잡이(Node ChildProcess 대응) ─────────────────────────
class ChildHandle:                                           # Node ChildProcess 대응 손잡이 클래스
    """Node ChildProcess 흉내: data/close/error 이벤트를 함수 속성으로 받는다.
    on_stdout/on_stderr(글), on_close(종료 코드 또는 None), on_error(메시지) — 실행 중에 바꿔 끼울 수 있다."""

    def __init__(self, proc: asyncio.subprocess.Process):    # asyncio 프로세스를 받아 손잡이를 만든다
        self.proc = proc                                     # asyncio 프로세스
        self.on_stdout: Optional[Callable[[str], None]] = None   # stdout 조각 받는 곳
        self.on_stderr: Optional[Callable[[str], None]] = None   # stderr 조각 받는 곳
        self.on_close: Optional[Callable[[Optional[int]], None]] = None   # 'close'(스트림 다 닫히고 종료)
        self.on_error: Optional[Callable[[str], None]] = None  # 오류 이벤트 받는 곳('error')
        self.killed = False                                  # kill() 불렀나
        self._task: Optional[asyncio.Task] = None            # 읽기 작업

    def start(self) -> None:                                 # 읽기 작업을 시작하는 메서드
        """읽기 작업 시작(spawn 직후 한 번)."""
        self._task = asyncio.ensure_future(self._pump())     # 백그라운드로 읽기

    async def _read(self, stream: Optional[asyncio.StreamReader], which: str) -> None:  # 스트림 하나를 끝까지 읽는 코루틴
        """스트림 하나를 끝까지 읽어 받는 곳에 넘김. 조각 경계에서 UTF-8 글자가 잘리지 않게 이어서 해석."""
        if stream is None:                                   # 파이프 없음
            return                                           # 파이프가 없으면 읽을 것이 없다
        dec = codecs.getincrementaldecoder("utf-8")("replace")   # Node setEncoding('utf8')/toString() 대응(깨진 바이트는 U+FFFD)
        while True:                                          # 스트림 끝까지 반복해서 읽는다
            chunk = await stream.read(65536)                 # 조각 읽기
            if not chunk:                                    # 끝
                rest = dec.decode(b"", final=True)           # 남은 바이트 마무리
                if rest:                                     # 남은 글이 있을 때만 전달
                    self._emit(which, rest)                  # 마지막 조각을 받는 곳에 넘김
                return                                       # 스트림이 끝났으니 읽기 종료
            text = dec.decode(chunk)                         # 글로
            if text:                                         # 빈 문자열이면 전달하지 않음
                self._emit(which, text)                      # 받는 곳에 전달

    def _emit(self, which: str, text: str) -> None:          # 받는 곳으로 글을 넘기는 메서드
        """지금 붙어 있는 받는 곳(on_stdout/on_stderr)에 글을 넘김."""
        cb = self.on_stdout if which == "stdout" else self.on_stderr   # 어느 쪽인지
        if cb is not None:                                   # 떼어 냈으면(removeListener) 버림
            cb(text)                                         # 붙어 있는 받는 곳을 호출

    async def _pump(self) -> None:                           # 두 스트림을 읽고 close 이벤트를 내는 코루틴
        """두 스트림을 다 읽고 종료를 기다린 뒤 close 이벤트."""
        try:                                                 # 읽기·종료 대기 중 예외를 잡기 위해
            await asyncio.gather(self._read(self.proc.stdout, "stdout"), self._read(self.proc.stderr, "stderr"))   # 둘 다 끝까지
            rc = await self.proc.wait()                      # 종료 코드
        except Exception as e:                               # 읽기 실패 → error 이벤트
            if self.on_error:                                # 오류 받는 곳이 있으면
                self.on_error(str(e))                        # 오류 메시지를 넘김
            return                                           # 정상 종료 처리는 건너뜀
        code = rc if rc is not None and rc >= 0 else None    # 신호로 끝나면 Node는 code=null
        if self.on_close:                                    # close 이벤트
            self.on_close(code)                              # 종료 코드를 close 받는 곳에 전달

    def kill(self) -> None:                                  # 프로세스를 죽이는 메서드
        """child.kill() — 기본 SIGTERM(Windows는 TerminateProcess). 이미 끝났으면 조용히 무시."""
        self.killed = True                                   # 표시
        try:                                                 # 이미 끝난 프로세스일 수 있어 예외 방어
            self.proc.terminate()                            # SIGTERM 전송
        except ProcessLookupError:                           # 이미 끝남
            pass                                             # 이미 끝났으면 무시
        except Exception:                                    # 그 밖(Windows 권한 등) — Node도 오류를 던지지 않음
            pass                                             # 그 밖의 오류도 무시(Node와 동일)


class _DeadChild:                                            # spawn 실패 때 쓰는 빈 손잡이
    """spawn 이 바로 실패했을 때 돌려주는 빈 손잡이(kill 은 아무것도 안 함)."""
    killed = False                                           # 표시용

    def kill(self) -> None:                                  # 죽일 프로세스가 없는 kill
        """죽일 프로세스가 없음."""
        self.killed = True                                   # 표시만


async def spawn_child(shell: str, args: list[str], *, cwd: Optional[str] = None, env: Optional[dict] = None,  # 프로세스를 띄우는 비동기 함수(spawn 대응)
                      creationflags: int = 0) -> ChildHandle:  # Windows 전용 플래그 인자
    """spawn(shell, args, {stdio: "pipe"}) — 실패하면 OSError 를 그대로 던진다(호출하는 쪽이 'error' 처리)."""
    kwargs = {}                                              # 추가 인자
    if creationflags:                                        # Windows 창 숨김 등
        kwargs["creationflags"] = creationflags              # Windows 창 숨김 등 플래그 전달
    proc = await asyncio.create_subprocess_exec(             # 자식 프로세스 생성(셸 + 인자, 파이프 연결)
        shell, *args,                                        # 셸 + 인자
        stdin=asyncio.subprocess.PIPE,                       # Node 기본 stdio 'pipe' — stdin 은 열어 둔 채(닫지 않음, cn과 같음)
        stdout=asyncio.subprocess.PIPE,                      # 출력 파이프
        stderr=asyncio.subprocess.PIPE,                      # 오류 파이프
        cwd=cwd, env=env, **kwargs)                          # 작업 폴더·환경변수(None이면 물려받음)
    return ChildHandle(proc)                                 # 손잡이(읽기는 start()에서)


def spawn_error_message(shell: str, err: OSError) -> str:    # spawn 실패 메시지 만드는 함수
    """Node spawn 실패 메시지 — 'spawn /bin/zsh ENOENT' 꼴."""
    import errno as _errno                                   # 오류 번호 이름
    code = _errno.errorcode.get(err.errno or 0, "UNKNOWN")   # ENOENT·EACCES 등
    if isinstance(err, FileNotFoundError):                   # Windows 의 FileNotFoundError(WinError 2)도 ENOENT
        code = "ENOENT"                                      # FileNotFoundError는 ENOENT로 통일
    return f"spawn {shell} {code}"                           # Node 형식


# ───────────────────────── 작업 자료(BackgroundJob) ─────────────────────────
@dataclass                                                   # 작업 자료를 담는 데이터클래스
class BackgroundJob:                                         # 백그라운드 작업 한 건의 상태 모음
    """BackgroundJob — 상태는 pending/running/completed/failed/cancelled."""
    id: str                                                  # bg-<번호>-<밀리초>
    status: str                                              # 상태
    command: str                                             # 명령
    output: str                                              # 출력(뒤 1000줄)
    exit_code: Optional[int]                                 # 종료 코드(없으면 None)
    start_time: datetime                                     # 시작 시각
    end_time: Optional[datetime]                             # 끝난 시각
    error: Optional[str] = None                              # 실패 메시지


class BackgroundJobService:                                  # 작업 관리 서비스 클래스
    """BackgroundJobService — 작업 만들기·시작·출력 쌓기·완료·취소."""

    def __init__(self) -> None:                              # 작업·프로세스 표와 번호를 초기화
        self.jobs: dict[str, BackgroundJob] = {}             # id → 작업(넣은 순서 유지 = JS Map)
        self.processes: dict[str, ChildHandle] = {}          # id → 실행 중 프로세스
        self.job_counter = 0                                 # 번호

    def _new_id(self) -> str:                                # 새 작업 id 생성 메서드
        """`bg-${++this.jobCounter}-${Date.now()}`."""
        self.job_counter += 1                                # 먼저 올림
        return f"bg-{self.job_counter}-{_now_ms()}"          # bg-번호-밀리초 형식 id 반환

    def create_job(self, command: str) -> Optional[BackgroundJob]:  # pending 작업 등록 메서드
        """createJob — 한도(5개)면 None. 상태 pending 으로 등록만."""
        running = self.get_running_job_count()               # 지금 도는 수
        if running >= MAX_CONCURRENT_JOBS:                   # 한도
            logger.warn(f"Cannot create background job: limit of {MAX_CONCURRENT_JOBS} reached")  # 한도 초과 경고를 남김
            return None                                      # 등록하지 않고 None 반환
        job = BackgroundJob(id=self._new_id(), status="pending", command=command, output="",  # pending 상태 새 작업 객체 생성
                            exit_code=None, start_time=_now(), end_time=None)   # 새 작업
        self.jobs[job.id] = job                              # 등록
        return job                                           # 등록한 작업 반환

    def _attach(self, job_id: str, child: ChildHandle) -> None:  # 자식 이벤트를 작업에 연결하는 메서드
        """자식의 이벤트를 이 작업에 연결(startJob·createJobWithProcess 공통 부분)."""
        child.on_stdout = lambda data: self.append_output(job_id, data)   # stdout → 출력
        child.on_stderr = lambda data: self.append_output(job_id, data)   # stderr → 같은 출력
        child.on_close = lambda code: self.complete_job(job_id, code if code is not None else 0)  # 종료 시 complete_job 호출(code가 None이면 0)
        child.on_error = lambda msg: self.fail_job(job_id, msg)  # error → 실패

    async def start_job(self, job_id: str, shell: str, args: list[str], *, cwd: Optional[str] = None,  # 작업을 running으로 바꾸고 프로세스를 띄우는 코루틴
                        env: Optional[dict] = None, creationflags: int = 0) -> Optional[ChildHandle]:  # cwd·env·플래그는 spawn_child에 그대로 전달
        """startJob — 작업을 running 으로 바꾸고 프로세스를 띄움. 작업이 없으면 None.
        Node spawn 은 셸이 없어도 객체를 돌려주고 나중에 'error' 를 낸다 → 여기서도 fail_job 후 죽은 손잡이를 돌려준다(성공으로 보임, cn과 같음)"""
        job = self.jobs.get(job_id)                          # 작업
        if not job:                                          # 없음
            logger.error(f"Cannot start job {job_id}: job not found")  # 없는 작업 id 오류 로그
            return None                                      # 시작하지 않고 None 반환
        job.status = "running"                               # 실행 중
        try:                                                 # spawn 실패(OSError)를 잡기 위해
            child = await spawn_child(shell, args, cwd=cwd, env=env, creationflags=creationflags)   # 띄우기
        except OSError as e:                                 # spawn 실패 → Node는 'error' 이벤트
            self.fail_job(job_id, spawn_error_message(shell, e))   # 실패 기록('error' 이벤트 대응)
            return _DeadChild()                              # Node 처럼 객체는 돌려줌
        self.processes[job_id] = child                       # 등록
        self._attach(job_id, child)                          # 이벤트 연결
        child.start()                                        # 읽기 시작
        return child                                         # 띄운 손잡이 반환

    def create_job_with_process(self, command: str, child: ChildHandle, existing_output: str = "") -> Optional[BackgroundJob]:  # 이미 도는 프로세스를 작업으로 등록하는 메서드
        """createJobWithProcess — 이미 돌고 있는 프로세스(Bash에서 넘긴 것)를 작업으로 등록. 한도면 None."""
        running = self.get_running_job_count()               # 지금 도는 수
        if running >= MAX_CONCURRENT_JOBS:                   # 한도
            logger.warn(f"Cannot create background job: limit of {MAX_CONCURRENT_JOBS} reached")  # 한도 초과 경고를 남김
            return None                                      # 등록하지 않고 None 반환
        job = BackgroundJob(id=self._new_id(), status="running", command=command, output=existing_output,  # 이미 running 상태인 작업 객체 생성(기존 출력 유지)
                            exit_code=None, start_time=_now(), end_time=None)   # 바로 running
        self.jobs[job.id] = job                              # 등록
        self.processes[job.id] = child                       # 프로세스
        self._attach(job.id, child)                          # 이벤트를 이 작업으로 옮김
        return job                                           # 등록한 작업 반환

    def append_output(self, job_id: str, data: str) -> None:  # 출력을 덧붙이는 메서드
        """appendOutput — 출력을 붙이고 1000줄을 넘으면 뒤 1000줄만."""
        job = self.jobs.get(job_id)                          # 작업
        if job:                                              # 작업이 있을 때만
            job.output += data                               # 붙이기
            lines = job.output.split("\n")                   # 줄
            if len(lines) > MAX_OUTPUT_LINES:                # 넘침
                job.output = "\n".join(lines[-MAX_OUTPUT_LINES:])   # 뒤쪽만

    def complete_job(self, job_id: str, exit_code: int) -> None:  # 정상/비정상 종료 기록 메서드
        """completeJob — 취소된 작업은 그대로. 0이면 completed, 아니면 failed."""
        job = self.jobs.get(job_id)                          # 작업 조회
        if job:                                              # 작업이 있으면
            if job.status == "cancelled":                    # 이미 취소
                return                                       # 취소된 작업은 상태를 바꾸지 않음
            job.status = "completed" if exit_code == 0 else "failed"   # 결과
            job.exit_code = exit_code                        # 코드
            job.end_time = _now()                            # 끝 시각
            self.processes.pop(job_id, None)                 # 프로세스 목록에서 뺌

    def fail_job(self, job_id: str, error: str) -> None:     # 실패 기록 메서드
        """failJob — 실패 기록."""
        job = self.jobs.get(job_id)                          # 작업 조회
        if job:                                              # 작업이 있으면
            job.status = "failed"                            # 실패
            job.error = error                                # 메시지
            job.end_time = _now()                            # 끝 시각
            self.processes.pop(job_id, None)                 # 뺌

    def cancel_job(self, job_id: str) -> bool:               # 작업 취소 메서드
        """cancelJob — 프로세스를 죽이고 cancelled. 작업이 없으면 False."""
        job = self.jobs.get(job_id)                          # 작업 조회
        process = self.processes.get(job_id)                 # 실행 중 프로세스 조회
        if not job:                                          # 작업이 없으면
            return False                                     # 취소 실패
        if process:                                          # 돌고 있으면
            process.kill()                                   # 죽임
            self.processes.pop(job_id, None)                 # 뺌
        job.status = "cancelled"                             # 취소
        job.end_time = _now()                                # 끝 시각
        return True                                          # 취소 성공

    def get_job(self, job_id: str) -> Optional[BackgroundJob]:  # 작업 하나 조회 메서드
        """getJob."""
        return self.jobs.get(job_id)                         # id로 작업 반환

    def get_running_jobs(self) -> list[BackgroundJob]:       # 실행 중·대기 중 작업 목록 메서드
        """getRunningJobs — running 또는 pending."""
        return [j for j in self.jobs.values() if j.status in ("running", "pending")]  # running 또는 pending 상태만 골라 반환

    def get_all_jobs(self) -> list[BackgroundJob]:           # 전체 작업 목록 메서드
        """getAllJobs — 등록 순서."""
        return list(self.jobs.values())                      # 등록 순서대로 반환

    def get_running_job_count(self) -> int:                  # 실행 중 작업 수 메서드
        """getRunningJobCount."""
        return len(self.get_running_jobs())                  # 목록 길이가 곧 개수

    def kill_all_jobs(self) -> None:                         # 전체 작업 종료 메서드
        """killAllJobs — 프로세스가 있는 작업을 모두 죽이고 cancelled(종료 때 Exit 도구·정상 종료에서 부름)."""
        for job_id, process in list(self.processes.items()):   # 도는 것마다
            process.kill()                                   # 죽임
            job = self.jobs.get(job_id)                      # 작업 조회
            if job:                                          # 작업이 있으면
                job.status = "cancelled"                     # 취소
                job.end_time = _now()                        # 끝 시각
        self.processes.clear()                               # 비움


background_job_service = BackgroundJobService()              # cn과 같이 프로세스 하나에 하나(services.backgroundJobs)


# ───────────────────────── Ctrl+B 신호(backgroundSignalManager) ─────────────────────────
class BackgroundSignalManager:                               # Ctrl+B 신호 관리 클래스
    """backgroundSignalManager — 'backgroundRequested' 이벤트. 대화형 화면(P2)이 signal_background() 를 부른다."""

    def __init__(self) -> None:                              # 듣는 함수 목록 초기화
        self._listeners: list[Callable[[], None]] = []       # 듣는 함수들(등록 순서)

    def on(self, fn: Callable[[], None]) -> None:            # 듣는 함수 등록 메서드
        """on('backgroundRequested', fn)."""
        self._listeners.append(fn)                           # 등록

    def off(self, fn: Callable[[], None]) -> None:           # 듣는 함수 제거 메서드
        """off/removeListener — 하나만 뗌(같은 함수가 없으면 무시)."""
        try:                                                 # 없는 함수 제거 시 예외 방어
            self._listeners.remove(fn)                       # 떼기
        except ValueError:                                   # 없음
            pass                                             # 없으면 무시

    remove_listener = off                                    # 같은 동작(cn이 둘 다 씀)

    def signal_background(self) -> None:                     # 등록된 함수를 모두 부르는 메서드
        """signalBackground — 지금 등록된 함수 모두 부름(부르는 중 떼어도 안전하게 복사본으로)."""
        for fn in list(self._listeners):                     # 복사본
            fn()                                             # 듣는 함수 호출

    def listener_count(self) -> int:                         # 등록 수 조회(시험용)
        """시험용 — 등록된 수."""
        return len(self._listeners)                          # 등록된 개수 반환


background_signal_manager = BackgroundSignalManager()        # 모듈 공용


# ───────────────────────── CheckBackgroundJob 도구(cn tools/checkBackgroundJob.ts) ─────────────────────────
def _js_json(value, indent=None) -> str:                     # JSON.stringify 대응 함수
    """JSON.stringify(value[, null, indent]) — 한글 등은 그대로(ensure_ascii=False), 들여쓰기 없으면 공백 없이."""
    import json                                              # 표준 JSON
    if indent is None:                                       # 한 줄
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))  # 들여쓰기 없으면 공백 없는 한 줄 JSON
    return json.dumps(value, ensure_ascii=False, indent=indent)   # 들여쓰기(구분자 ", " / ": " → 줄 끝 공백 없음, JS와 같음)


def _js_substring50(s: str) -> str:                          # substring(0,50)+"..." 대응 함수
    """j.command.substring(0, 50) + (길이 > 50 ? "..." : "") — UTF-16 길이 기준."""
    from ncn.tools.truncate import js_len                    # JS 길이
    if js_len(s) == len(s):                                  # BMP만
        return s[:50] + ("..." if len(s) > 50 else "")       # BMP 글자만이면 파이썬 길이로 충분
    u = s.encode("utf-16-le", errors="surrogatepass")        # UTF-16으로 바꿔 JS 길이 기준으로 자름
    return u[:100].decode("utf-16-le", errors="surrogatepass") + ("..." if js_len(s) > 50 else "")  # 앞 100바이트(50글자)만 복원하고 길면 말줄임


async def _check_background_job_run(args: dict, context=None) -> str:  # CheckBackgroundJob 실행 코루틴
    """run — 작업이 없으면 목록과 함께 오류 JSON(한 줄), 있으면 상태 JSON(2칸 들여쓰기)."""
    import math                                              # 반올림
    job_id = args.get("job_id")                              # 작업 id
    job = background_job_service.get_job(job_id) if isinstance(job_id, str) else None   # 찾기
    if not job:                                              # 없음
        return _js_json({                                    # 작업이 없으면 오류 JSON 반환
            "error": f"Job {'undefined' if job_id is None else job_id} not found",   # 템플릿 글(undefined 표기)
            "available_jobs": [                              # 가능한 작업 목록 제공
                {"id": j.id, "status": j.status, "command": _js_substring50(j.command)}  # 작업 요약 한 건(명령은 50자까지)
                for j in background_job_service.get_all_jobs()  # 등록된 모든 작업을 순회
            ],                                               # 목록 닫기
        })                                                   # 오류 JSON 끝
    result: dict = {                                         # 키 순서 = cn
        "job_id": job.id,                                    # 작업 id
        "status": job.status,                                # 상태
        "command": job.command,                              # 명령
        "output": job.output,                                # 출력
        "started_at": iso_string(job.start_time),            # 시작 시각(ISO)
    }                                                        # 결과 dict 끝
    if job.end_time:                                         # 끝났으면
        result["ended_at"] = iso_string(job.end_time)        # 종료 시각(ISO)
        ms = (job.end_time - job.start_time).total_seconds() * 1000   # 밀리초 차
        result["duration_seconds"] = int(math.floor(ms / 1000 + 0.5))   # Math.round(반올림 .5 는 올림)
    if job.exit_code is not None:                            # 종료 코드
        result["exit_code"] = job.exit_code                  # 종료 코드
    if job.error:                                            # 실패 메시지
        result["error"] = job.error                          # 실패 메시지
    return _js_json(result, indent=2)                        # JSON.stringify(result, null, 2)


def _make_check_background_job_tool():                       # CheckBackgroundJob 도구 객체 생성 함수
    """CheckBackgroundJob 도구 객체."""
    from ncn.tools.base import Tool                          # 공용 형식(순환 import 피하려고 안에서)
    return Tool(                                             # 도구 정의 반환
        name="CheckBackgroundJob",                           # 도구 이름
        display_name="Check Background Job",                 # 화면 표시 이름
        description=(                                        # 도구 설명 원문(cn 그대로)
            "Check the status and output of a background job.\n"
            "Returns the current status, exit code (if finished), and all available output.\n"
            "If the job is still running, returns partial output with \"running\" status."
        ),                                                   # 설명 끝
        parameters={                                         # 인자 JSON 스키마
            "type": "object",                                # 객체 타입
            "required": ["job_id"],                          # 필수 인자: job_id
            "properties": {                                  # 인자 속성 정의
                "job_id": {                                  # job_id 인자
                    "type": "string",                        # 문자열 타입
                    "description": "The ID of the background job to check (e.g., bg-1-1234567890)",  # 인자 설명 원문
                },
            },
        },
        run=_check_background_job_run,                       # 실행 함수 연결
        readonly=True,                                       # 읽기 전용 도구
        is_built_in=True,                                    # 내장 도구 표시
    )


CHECK_BACKGROUND_JOB_TOOL = _make_check_background_job_tool()   # 내장 도구(cn checkBackgroundJobTool)
