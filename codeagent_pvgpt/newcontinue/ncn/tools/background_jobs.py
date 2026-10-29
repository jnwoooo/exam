# ncn/tools/background_jobs.py — 백그라운드 작업 관리. cn extensions/cli/src/services/BackgroundJobService.ts + util/backgroundSignalManager.ts
# Bash 실행 중 Ctrl+B(대화형, P2)로 넘긴 명령과 runCommandInBackground로 띄운 명령을 최대 5개까지 추적한다
# 자식 프로세스는 asyncio로 띄우고, stdout/stderr 를 읽는 작업이 "받는 쪽(sink)" 함수를 부른다 — 받는 쪽을 바꿔 끼우면 Bash → 백그라운드로 넘어간다
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 자식 프로세스·읽기 작업
import codecs                                                # 조각 단위 UTF-8 해석
import sys                                                   # stderr 기록
import time                                                  # Date.now()
from dataclasses import dataclass                            # 작업 자료
from datetime import datetime, timezone                      # startTime/endTime(toISOString)
from typing import Callable, Optional                        # 타입

MAX_CONCURRENT_JOBS = 5                                      # 동시에 돌 수 있는 작업 수(cn 원문 값)
MAX_OUTPUT_LINES = 1000                                      # 작업마다 남기는 출력 줄 수(뒤쪽 유지)


def _now_ms() -> int:
    """Date.now() — 1970년부터 밀리초."""
    return int(time.time() * 1000)                           # 정수 밀리초


def iso_string(dt: datetime) -> str:
    """Date.prototype.toISOString() — 'YYYY-MM-DDTHH:MM:SS.mmmZ'(UTC, 밀리초 3자리)."""
    u = dt.astimezone(timezone.utc)                          # UTC로
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"   # 밀리초까지


def _now() -> datetime:
    """new Date() — 밀리초 단위로 자른 현재 시각(JS Date는 밀리초 정밀도)."""
    d = datetime.now(timezone.utc)                           # 현재 UTC
    return d.replace(microsecond=(d.microsecond // 1000) * 1000)   # 밀리초 아래 버림


class _Logger:
    """cn logger 대신 — stdout 은 ACP 전용이므로 stderr 에만(그것도 기본은 조용히)."""
    enabled = False                                          # 기본 꺼짐(로그 모듈은 다른 단계)

    def warn(self, msg: str) -> None:
        if self.enabled:                                     # 켰을 때만
            print(msg, file=sys.stderr)                      # stderr

    error = warn                                             # 같은 처리
    debug = warn                                             # 같은 처리


logger = _Logger()                                           # 모듈 공용


# ───────────────────────── 자식 프로세스 손잡이(Node ChildProcess 대응) ─────────────────────────
class ChildHandle:
    """Node ChildProcess 흉내: data/close/error 이벤트를 함수 속성으로 받는다.
    on_stdout/on_stderr(글), on_close(종료 코드 또는 None), on_error(메시지) — 실행 중에 바꿔 끼울 수 있다."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc                                     # asyncio 프로세스
        self.on_stdout: Optional[Callable[[str], None]] = None   # stdout 조각 받는 곳
        self.on_stderr: Optional[Callable[[str], None]] = None   # stderr 조각 받는 곳
        self.on_close: Optional[Callable[[Optional[int]], None]] = None   # 'close'(스트림 다 닫히고 종료)
        self.on_error: Optional[Callable[[str], None]] = None    # 'error'
        self.killed = False                                  # kill() 불렀나
        self._task: Optional[asyncio.Task] = None            # 읽기 작업

    def start(self) -> None:
        """읽기 작업 시작(spawn 직후 한 번)."""
        self._task = asyncio.ensure_future(self._pump())     # 백그라운드로 읽기

    async def _read(self, stream: Optional[asyncio.StreamReader], which: str) -> None:
        """스트림 하나를 끝까지 읽어 받는 곳에 넘김. 조각 경계에서 UTF-8 글자가 잘리지 않게 이어서 해석."""
        if stream is None:                                   # 파이프 없음
            return
        dec = codecs.getincrementaldecoder("utf-8")("replace")   # Node setEncoding('utf8')/toString() 대응(깨진 바이트는 U+FFFD)
        while True:
            chunk = await stream.read(65536)                 # 조각 읽기
            if not chunk:                                    # 끝
                rest = dec.decode(b"", final=True)           # 남은 바이트 마무리
                if rest:
                    self._emit(which, rest)
                return
            text = dec.decode(chunk)                         # 글로
            if text:
                self._emit(which, text)                      # 받는 곳에 전달

    def _emit(self, which: str, text: str) -> None:
        """지금 붙어 있는 받는 곳(on_stdout/on_stderr)에 글을 넘김."""
        cb = self.on_stdout if which == "stdout" else self.on_stderr   # 어느 쪽인지
        if cb is not None:                                   # 떼어 냈으면(removeListener) 버림
            cb(text)

    async def _pump(self) -> None:
        """두 스트림을 다 읽고 종료를 기다린 뒤 close 이벤트."""
        try:
            await asyncio.gather(self._read(self.proc.stdout, "stdout"), self._read(self.proc.stderr, "stderr"))   # 둘 다 끝까지
            rc = await self.proc.wait()                      # 종료 코드
        except Exception as e:                               # 읽기 실패 → error 이벤트
            if self.on_error:
                self.on_error(str(e))
            return
        code = rc if rc is not None and rc >= 0 else None    # 신호로 끝나면 Node는 code=null
        if self.on_close:                                    # close 이벤트
            self.on_close(code)

    def kill(self) -> None:
        """child.kill() — 기본 SIGTERM(Windows는 TerminateProcess). 이미 끝났으면 조용히 무시."""
        self.killed = True                                   # 표시
        try:
            self.proc.terminate()                            # SIGTERM
        except ProcessLookupError:                           # 이미 끝남
            pass
        except Exception:                                    # 그 밖(Windows 권한 등) — Node도 오류를 던지지 않음
            pass


class _DeadChild:
    """spawn 이 바로 실패했을 때 돌려주는 빈 손잡이(kill 은 아무것도 안 함)."""
    killed = False                                           # 표시용

    def kill(self) -> None:
        """죽일 프로세스가 없음."""
        self.killed = True                                   # 표시만


async def spawn_child(shell: str, args: list[str], *, cwd: Optional[str] = None, env: Optional[dict] = None,
                      creationflags: int = 0) -> ChildHandle:
    """spawn(shell, args, {stdio: "pipe"}) — 실패하면 OSError 를 그대로 던진다(호출하는 쪽이 'error' 처리)."""
    kwargs = {}                                              # 추가 인자
    if creationflags:                                        # Windows 창 숨김 등
        kwargs["creationflags"] = creationflags
    proc = await asyncio.create_subprocess_exec(
        shell, *args,                                        # 셸 + 인자
        stdin=asyncio.subprocess.PIPE,                       # Node 기본 stdio 'pipe' — stdin 은 열어 둔 채(닫지 않음, cn과 같음)
        stdout=asyncio.subprocess.PIPE,                      # 출력 파이프
        stderr=asyncio.subprocess.PIPE,                      # 오류 파이프
        cwd=cwd, env=env, **kwargs)                          # 작업 폴더·환경변수(None이면 물려받음)
    return ChildHandle(proc)                                 # 손잡이(읽기는 start()에서)


def spawn_error_message(shell: str, err: OSError) -> str:
    """Node spawn 실패 메시지 — 'spawn /bin/zsh ENOENT' 꼴."""
    import errno as _errno                                   # 오류 번호 이름
    code = _errno.errorcode.get(err.errno or 0, "UNKNOWN")   # ENOENT·EACCES 등
    if isinstance(err, FileNotFoundError):                   # Windows 의 FileNotFoundError(WinError 2)도 ENOENT
        code = "ENOENT"
    return f"spawn {shell} {code}"                           # Node 형식


# ───────────────────────── 작업 자료(BackgroundJob) ─────────────────────────
@dataclass
class BackgroundJob:
    """BackgroundJob — 상태는 pending/running/completed/failed/cancelled."""
    id: str                                                  # bg-<번호>-<밀리초>
    status: str                                              # 상태
    command: str                                             # 명령
    output: str                                              # 출력(뒤 1000줄)
    exit_code: Optional[int]                                 # 종료 코드(없으면 None)
    start_time: datetime                                     # 시작 시각
    end_time: Optional[datetime]                             # 끝난 시각
    error: Optional[str] = None                              # 실패 메시지


class BackgroundJobService:
    """BackgroundJobService — 작업 만들기·시작·출력 쌓기·완료·취소."""

    def __init__(self) -> None:
        self.jobs: dict[str, BackgroundJob] = {}             # id → 작업(넣은 순서 유지 = JS Map)
        self.processes: dict[str, ChildHandle] = {}          # id → 실행 중 프로세스
        self.job_counter = 0                                 # 번호

    def _new_id(self) -> str:
        """`bg-${++this.jobCounter}-${Date.now()}`."""
        self.job_counter += 1                                # 먼저 올림
        return f"bg-{self.job_counter}-{_now_ms()}"          # id

    def create_job(self, command: str) -> Optional[BackgroundJob]:
        """createJob — 한도(5개)면 None. 상태 pending 으로 등록만."""
        running = self.get_running_job_count()               # 지금 도는 수
        if running >= MAX_CONCURRENT_JOBS:                   # 한도
            logger.warn(f"Cannot create background job: limit of {MAX_CONCURRENT_JOBS} reached")
            return None
        job = BackgroundJob(id=self._new_id(), status="pending", command=command, output="",
                            exit_code=None, start_time=_now(), end_time=None)   # 새 작업
        self.jobs[job.id] = job                              # 등록
        return job

    def _attach(self, job_id: str, child: ChildHandle) -> None:
        """자식의 이벤트를 이 작업에 연결(startJob·createJobWithProcess 공통 부분)."""
        child.on_stdout = lambda data: self.append_output(job_id, data)   # stdout → 출력
        child.on_stderr = lambda data: self.append_output(job_id, data)   # stderr → 같은 출력
        child.on_close = lambda code: self.complete_job(job_id, code if code is not None else 0)   # code ?? 0
        child.on_error = lambda msg: self.fail_job(job_id, msg)  # error → 실패

    async def start_job(self, job_id: str, shell: str, args: list[str], *, cwd: Optional[str] = None,
                        env: Optional[dict] = None, creationflags: int = 0) -> Optional[ChildHandle]:
        """startJob — 작업을 running 으로 바꾸고 프로세스를 띄움. 작업이 없으면 None.
        Node spawn 은 셸이 없어도 객체를 돌려주고 나중에 'error' 를 낸다 → 여기서도 fail_job 후 죽은 손잡이를 돌려준다(성공으로 보임, cn과 같음)"""
        job = self.jobs.get(job_id)                          # 작업
        if not job:                                          # 없음
            logger.error(f"Cannot start job {job_id}: job not found")
            return None
        job.status = "running"                               # 실행 중
        try:
            child = await spawn_child(shell, args, cwd=cwd, env=env, creationflags=creationflags)   # 띄우기
        except OSError as e:                                 # spawn 실패 → Node는 'error' 이벤트
            self.fail_job(job_id, spawn_error_message(shell, e))   # 실패 기록('error' 이벤트 대응)
            return _DeadChild()                              # Node 처럼 객체는 돌려줌
        self.processes[job_id] = child                       # 등록
        self._attach(job_id, child)                          # 이벤트 연결
        child.start()                                        # 읽기 시작
        return child

    def create_job_with_process(self, command: str, child: ChildHandle, existing_output: str = "") -> Optional[BackgroundJob]:
        """createJobWithProcess — 이미 돌고 있는 프로세스(Bash에서 넘긴 것)를 작업으로 등록. 한도면 None."""
        running = self.get_running_job_count()               # 지금 도는 수
        if running >= MAX_CONCURRENT_JOBS:                   # 한도
            logger.warn(f"Cannot create background job: limit of {MAX_CONCURRENT_JOBS} reached")
            return None
        job = BackgroundJob(id=self._new_id(), status="running", command=command, output=existing_output,
                            exit_code=None, start_time=_now(), end_time=None)   # 바로 running
        self.jobs[job.id] = job                              # 등록
        self.processes[job.id] = child                       # 프로세스
        self._attach(job.id, child)                          # 이벤트를 이 작업으로 옮김
        return job

    def append_output(self, job_id: str, data: str) -> None:
        """appendOutput — 출력을 붙이고 1000줄을 넘으면 뒤 1000줄만."""
        job = self.jobs.get(job_id)                          # 작업
        if job:
            job.output += data                               # 붙이기
            lines = job.output.split("\n")                   # 줄
            if len(lines) > MAX_OUTPUT_LINES:                # 넘침
                job.output = "\n".join(lines[-MAX_OUTPUT_LINES:])   # 뒤쪽만

    def complete_job(self, job_id: str, exit_code: int) -> None:
        """completeJob — 취소된 작업은 그대로. 0이면 completed, 아니면 failed."""
        job = self.jobs.get(job_id)
        if job:
            if job.status == "cancelled":                    # 이미 취소
                return
            job.status = "completed" if exit_code == 0 else "failed"   # 결과
            job.exit_code = exit_code                        # 코드
            job.end_time = _now()                            # 끝 시각
            self.processes.pop(job_id, None)                 # 프로세스 목록에서 뺌

    def fail_job(self, job_id: str, error: str) -> None:
        """failJob — 실패 기록."""
        job = self.jobs.get(job_id)
        if job:
            job.status = "failed"                            # 실패
            job.error = error                                # 메시지
            job.end_time = _now()                            # 끝 시각
            self.processes.pop(job_id, None)                 # 뺌

    def cancel_job(self, job_id: str) -> bool:
        """cancelJob — 프로세스를 죽이고 cancelled. 작업이 없으면 False."""
        job = self.jobs.get(job_id)
        process = self.processes.get(job_id)
        if not job:
            return False
        if process:                                          # 돌고 있으면
            process.kill()                                   # 죽임
            self.processes.pop(job_id, None)                 # 뺌
        job.status = "cancelled"                             # 취소
        job.end_time = _now()                                # 끝 시각
        return True

    def get_job(self, job_id: str) -> Optional[BackgroundJob]:
        """getJob."""
        return self.jobs.get(job_id)

    def get_running_jobs(self) -> list[BackgroundJob]:
        """getRunningJobs — running 또는 pending."""
        return [j for j in self.jobs.values() if j.status in ("running", "pending")]

    def get_all_jobs(self) -> list[BackgroundJob]:
        """getAllJobs — 등록 순서."""
        return list(self.jobs.values())

    def get_running_job_count(self) -> int:
        """getRunningJobCount."""
        return len(self.get_running_jobs())

    def kill_all_jobs(self) -> None:
        """killAllJobs — 프로세스가 있는 작업을 모두 죽이고 cancelled(종료 때 Exit 도구·정상 종료에서 부름)."""
        for job_id, process in list(self.processes.items()):   # 도는 것마다
            process.kill()                                   # 죽임
            job = self.jobs.get(job_id)
            if job:
                job.status = "cancelled"                     # 취소
                job.end_time = _now()                        # 끝 시각
        self.processes.clear()                               # 비움


background_job_service = BackgroundJobService()              # cn과 같이 프로세스 하나에 하나(services.backgroundJobs)


# ───────────────────────── Ctrl+B 신호(backgroundSignalManager) ─────────────────────────
class BackgroundSignalManager:
    """backgroundSignalManager — 'backgroundRequested' 이벤트. 대화형 화면(P2)이 signal_background() 를 부른다."""

    def __init__(self) -> None:
        self._listeners: list[Callable[[], None]] = []       # 듣는 함수들(등록 순서)

    def on(self, fn: Callable[[], None]) -> None:
        """on('backgroundRequested', fn)."""
        self._listeners.append(fn)                           # 등록

    def off(self, fn: Callable[[], None]) -> None:
        """off/removeListener — 하나만 뗌(같은 함수가 없으면 무시)."""
        try:
            self._listeners.remove(fn)                       # 떼기
        except ValueError:                                   # 없음
            pass

    remove_listener = off                                    # 같은 동작(cn이 둘 다 씀)

    def signal_background(self) -> None:
        """signalBackground — 지금 등록된 함수 모두 부름(부르는 중 떼어도 안전하게 복사본으로)."""
        for fn in list(self._listeners):                     # 복사본
            fn()

    def listener_count(self) -> int:
        """시험용 — 등록된 수."""
        return len(self._listeners)


background_signal_manager = BackgroundSignalManager()        # 모듈 공용


# ───────────────────────── CheckBackgroundJob 도구(cn tools/checkBackgroundJob.ts) ─────────────────────────
def _js_json(value, indent=None) -> str:
    """JSON.stringify(value[, null, indent]) — 한글 등은 그대로(ensure_ascii=False), 들여쓰기 없으면 공백 없이."""
    import json                                              # 표준 JSON
    if indent is None:                                       # 한 줄
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=indent)   # 들여쓰기(구분자 ", " / ": " → 줄 끝 공백 없음, JS와 같음)


def _js_substring50(s: str) -> str:
    """j.command.substring(0, 50) + (길이 > 50 ? "..." : "") — UTF-16 길이 기준."""
    from ncn.tools.truncate import js_len                    # JS 길이
    if js_len(s) == len(s):                                  # BMP만
        return s[:50] + ("..." if len(s) > 50 else "")
    u = s.encode("utf-16-le", errors="surrogatepass")        # UTF-16
    return u[:100].decode("utf-16-le", errors="surrogatepass") + ("..." if js_len(s) > 50 else "")


async def _check_background_job_run(args: dict, context=None) -> str:
    """run — 작업이 없으면 목록과 함께 오류 JSON(한 줄), 있으면 상태 JSON(2칸 들여쓰기)."""
    import math                                              # 반올림
    job_id = args.get("job_id")                              # 작업 id
    job = background_job_service.get_job(job_id) if isinstance(job_id, str) else None   # 찾기
    if not job:                                              # 없음
        return _js_json({
            "error": f"Job {'undefined' if job_id is None else job_id} not found",   # 템플릿 글(undefined 표기)
            "available_jobs": [
                {"id": j.id, "status": j.status, "command": _js_substring50(j.command)}
                for j in background_job_service.get_all_jobs()
            ],
        })
    result: dict = {                                         # 키 순서 = cn
        "job_id": job.id,
        "status": job.status,
        "command": job.command,
        "output": job.output,
        "started_at": iso_string(job.start_time),
    }
    if job.end_time:                                         # 끝났으면
        result["ended_at"] = iso_string(job.end_time)
        ms = (job.end_time - job.start_time).total_seconds() * 1000   # 밀리초 차
        result["duration_seconds"] = int(math.floor(ms / 1000 + 0.5))   # Math.round(반올림 .5 는 올림)
    if job.exit_code is not None:                            # 종료 코드
        result["exit_code"] = job.exit_code
    if job.error:                                            # 실패 메시지
        result["error"] = job.error
    return _js_json(result, indent=2)                        # JSON.stringify(result, null, 2)


def _make_check_background_job_tool():
    """CheckBackgroundJob 도구 객체."""
    from ncn.tools.base import Tool                          # 공용 형식(순환 import 피하려고 안에서)
    return Tool(
        name="CheckBackgroundJob",
        display_name="Check Background Job",
        description=(
            "Check the status and output of a background job.\n"
            "Returns the current status, exit code (if finished), and all available output.\n"
            "If the job is still running, returns partial output with \"running\" status."
        ),
        parameters={
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The ID of the background job to check (e.g., bg-1-1234567890)",
                },
            },
        },
        run=_check_background_job_run,
        readonly=True,
        is_built_in=True,
    )


CHECK_BACKGROUND_JOB_TOOL = _make_check_background_job_tool()   # 내장 도구(cn checkBackgroundJobTool)
