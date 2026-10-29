# ncn/tools/search.py — Search 도구(코드 검색). cn extensions/cli/src/tools/searchCode.ts 를 그대로 옮긴 것
# rg 가 있으면 cn과 같은 rg 명령을 셸로 실행(Node child_process.exec 와 같은 셸·출력 한도 1MiB·오류 글)
# [추가] rg 가 없으면 cn은 도구 자체를 뺀다 — ncn은 NCN_BUILTIN_SEARCH=1 일 때만 표준 라이브러리 검색으로 rg 와 같은 출력 형식("경로:줄:내용")을 낸다(계획 부록 B)
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 스레드로 실행
import os                                                    # 경로·환경변수
import re                                                    # 정규식(내장 검색)
import subprocess                                            # 셸 명령
import threading                                             # 출력 읽기 스레드
from typing import Optional                                  # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview   # 공용 형식
from ncn.tools.formatters import js_trim, node_platform      # JS trim, Node process.platform
from ncn.tools.truncate import js_len, parse_env_number      # JS 길이, 환경변수 숫자

DEFAULT_SEARCH_MAX_RESULTS = 100                             # 최대 결과 줄 수
DEFAULT_SEARCH_MAX_RESULT_CHARS = 1000                       # 결과 한 줄 최대 글자(넘는 줄은 버림)
EXEC_MAX_BUFFER = 1024 * 1024                                # Node exec 기본 maxBuffer(바이트)


def builtin_search_enabled() -> bool:
    """[추가] 내장 검색 사용 여부 — 환경변수 NCN_BUILTIN_SEARCH=1 일 때만(기본은 cn과 같이 rg 없으면 도구 제외)."""
    return os.environ.get("NCN_BUILTIN_SEARCH") == "1"       # 정확히 "1"


# ───────────────────────── Node child_process.exec 흉내 ─────────────────────────
class ExecError(Exception):
    """exec 실패 — code(종료 코드, 신호면 None, 출력 한도면 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER'), stdout, stderr."""

    def __init__(self, message: str, code, stdout: str = "", stderr: str = ""):
        super().__init__(message)                            # error.message
        self.code = code                                     # error.code
        self.stdout = stdout                                 # 받은 출력
        self.stderr = stderr                                 # 받은 오류 출력


def _exec_blocking(command: str, cwd: Optional[str]) -> tuple[str, str]:
    """exec(command, {cwd}) — 셸로 실행, (stdout, stderr). 실패하면 ExecError.
    셸: Unix '/bin/sh -c', Windows '%ComSpec% /d /s /c "명령"'(Node 와 같은 명령줄)."""
    if node_platform() == "win32":                           # Windows: cmd.exe
        comspec = os.environ.get("ComSpec") or os.environ.get("COMSPEC") or "cmd.exe"   # process.env.ComSpec || 'cmd.exe'
        argv = f'{comspec} /d /s /c "{command}"'             # windowsVerbatimArguments — 문자열 그대로 CreateProcess 에
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # [추가 V6] 창 숨김(결과 같음)
    else:                                                    # Unix: /bin/sh
        argv = ["/bin/sh", "-c", command]                    # Node 기본 셸
        flags = 0
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,   # Node exec 도 stdin 파이프(닫지 않음)
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    except OSError as e:                                     # 셸 자체가 없음(거의 없음)
        raise ExecError(f"spawn {argv[0] if isinstance(argv, list) else comspec} ENOENT", "ENOENT")
    bufs = {"stdout": bytearray(), "stderr": bytearray()}    # 모은 바이트
    over = {"which": None}                                   # 한도를 넘은 쪽
    lock = threading.Lock()                                  # 공유 상태 보호

    def reader(stream, which: str) -> None:
        """한 스트림 읽기 — 한도를 넘으면 넘친 만큼 자르고 프로세스를 죽인다(Node 와 같게)."""
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)   # 조각
            if not chunk:                                    # 끝
                return
            with lock:
                if over["which"]:                            # 이미 넘침 → 버림
                    continue
                room = EXEC_MAX_BUFFER - len(bufs[which])    # 남은 자리
                if len(chunk) > room:                        # 넘침
                    bufs[which] += chunk[:max(room, 0)]      # 남은 만큼만
                    over["which"] = which                    # 표시
                    try:
                        proc.kill()                          # 죽임
                    except Exception:
                        pass
                    continue
                bufs[which] += chunk                         # 붙이기

    ts = [threading.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True),
          threading.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True)]   # 읽기 스레드 둘
    for t in ts:
        t.start()
    for t in ts:
        t.join()                                             # 다 읽을 때까지
    rc = proc.wait()                                         # 종료
    try:
        proc.stdin.close()                                   # 정리
    except Exception:
        pass
    out = bufs["stdout"].decode("utf-8", errors="replace")   # utf8 해석(깨진 바이트는 U+FFFD)
    err = bufs["stderr"].decode("utf-8", errors="replace")
    if over["which"]:                                        # 출력 한도
        raise ExecError(f"{over['which']} maxBuffer length exceeded", "ERR_CHILD_PROCESS_STDIO_MAXBUFFER", out, err)
    if rc != 0:                                              # 실패 종료
        code = rc if rc >= 0 else None                       # 신호면 null
        raise ExecError(f"Command failed: {command}\n{err}", code, out, err)   # Node 글
    return out, err


async def exec_promise(command: str, cwd: Optional[str] = None) -> tuple[str, str]:
    """util.promisify(exec) — 이벤트 루프를 막지 않게 스레드에서."""
    return await asyncio.to_thread(_exec_blocking, command, cwd)


# ───────────────────────── .gitignore 패턴(find-up) ─────────────────────────
def _find_up(name: str, start: str) -> Optional[str]:
    """findUp(name) — start 부터 위로 올라가며 그 이름의 '파일'을 찾음(find-up 기본 type: file)."""
    d = os.path.abspath(start)                               # 시작
    while True:
        p = os.path.join(d, name)                            # 후보
        if os.path.isfile(p):                                # 파일이면
            return p
        parent = os.path.dirname(d)                          # 위로
        if parent == d:                                      # 루트
            return None
        d = parent


def get_gitignore_patterns(cwd: str) -> list[str]:
    """getGitignorePatterns — 가장 가까운 .gitignore 의 줄(주석·빈 줄·! 로 시작하는 줄 제외)."""
    git_ignore_path = _find_up(".gitignore", cwd)            # 찾기(process.cwd() 기준)
    if not git_ignore_path:                                  # 없음
        return []
    with open(git_ignore_path, encoding="utf-8", errors="replace") as f:   # utf-8
        content = f.read()
    patterns = []                                            # 결과
    for line in js_trim(content).split("\n"):                # content.trim().split("\n")
        line = js_trim(line)                                 # line.trim()
        if line.startswith("#") or line == "":               # 주석·빈 줄
            continue
        if line.startswith("!"):                             # 부정 패턴은 버림
            continue
        patterns.append(line)
    return patterns


# ───────────────────────── 절차 1: ripgrep ─────────────────────────
async def check_if_ripgrep_is_installed(cwd: Optional[str] = None) -> bool:
    """checkIfRipgrepIsInstalled — 'rg --version' 이 성공하나(셸로, 매번 확인 — cn과 같음)."""
    try:
        await exec_promise("rg --version", cwd)              # 실행
        return True
    except Exception:                                        # 없음·실패
        return False


async def search_with_ripgrep(pattern: str, search_path: str, file_pattern: Optional[str], cwd: str) -> tuple[str, str]:
    """searchWithRipgrep — cn과 글자 하나까지 같은 rg 명령(인자를 큰따옴표로 감쌈 — 셸이 해석함, cn 그대로)."""
    command = f'rg --line-number --with-filename --color never "{pattern}"'   # 기본
    if file_pattern:                                         # 파일 패턴
        command += f' -g "{file_pattern}"'
    for ignore_pattern in get_gitignore_patterns(cwd):       # .gitignore 제외
        command += f' -g "!{ignore_pattern}"'
    command += f' "{search_path}"'                           # 찾을 곳
    return await exec_promise(command, cwd)                  # exec(cwd 옵션 없음 = process.cwd() → ncn은 cwd)


# ───────────────────────── 절차 2: grep / findstr(cn에서는 rg 없으면 도구가 빠져 실제로는 안 쓰임) ─────────────────────────
async def search_with_grep_or_findstr(pattern: str, search_path: str, file_pattern: Optional[str], cwd: str) -> tuple[str, str]:
    """searchWithGrepOrFindstr — Windows findstr, 그 밖 grep(cn 원문 명령)."""
    is_windows = node_platform() == "win32"                  # Windows?
    ignore_patterns = get_gitignore_patterns(cwd)            # .gitignore
    if is_windows:                                           # findstr 은 제외 패턴을 못 씀
        file_spec = file_pattern if file_pattern else "*"
        command = f'findstr /S /N /P /R "{pattern}" "{file_spec}"'
    else:
        exclude_args = ""                                    # 제외 인자
        for ip in ignore_patterns:                           # 파일·폴더 둘 다
            exclude_args += f' --exclude="{ip}" --exclude-dir="{ip}"'
        if file_pattern:
            command = f'find . -type f -path "{file_pattern}" -print0 | xargs -0 grep -nH -I{exclude_args} "{pattern}"'
        else:
            command = f'grep -R -n -H -I{exclude_args} "{pattern}" .'
    target = search_path if os.path.isabs(search_path) else os.path.join(cwd, search_path)   # exec cwd: searchPath
    return await exec_promise(command, target)


# ───────────────────────── [추가] 내장 검색 — rg 기본 동작을 흉내 ─────────────────────────
def _sh_double_quote_unescape(s: str) -> str:
    """POSIX 셸 큰따옴표 안 해석 흉내 — '\\' 뒤의 $ ` \" \\ 와 줄바꿈만 풀어 줌(cn 은 패턴을 "…" 로 셸에 넘기므로). 변수 확장은 하지 않음."""
    out = []                                                 # 결과
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in '$`"\\\n':   # 특수 글자 앞 역슬래시
            if s[i + 1] != "\n":                             # 줄 이음은 둘 다 없어짐
                out.append(s[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


class _Rules:
    """한 무시 파일(.gitignore/.ignore/.rgignore)의 규칙 — 그 파일이 있는 폴더 기준."""

    def __init__(self, base: str, lines: list[str]):
        import pathspec                                      # gitignore 규칙 판정(의존성 목록에 있음)
        self.base = base                                     # 기준 폴더(절대)
        self.spec = pathspec.GitIgnoreSpec.from_lines(lines)   # 규칙

    def check(self, abs_path: str, is_dir: bool) -> Optional[bool]:
        """True=무시, False=다시 포함(!), None=해당 없음."""
        rel = os.path.relpath(abs_path, self.base).replace(os.sep, "/")   # 기준 상대
        if rel.startswith(".."):                             # 범위 밖
            return None
        if is_dir:                                           # 폴더 전용 규칙(/)도 맞게
            rel += "/"
        return self.spec.check_file(rel).include             # 결과


def _read_rules(d: str, name: str) -> Optional[_Rules]:
    """폴더 d 의 무시 파일 하나 읽기."""
    p = os.path.join(d, name)                                # 파일
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return _Rules(d, f.read().splitlines())          # 규칙
    except Exception:                                        # 깨진 규칙 파일은 건너뜀
        return None


def _in_git_repo(d: str) -> bool:
    """rg 는 git 저장소 안에서만 .gitignore 를 따른다(.git 폴더/파일이 위쪽에 있나)."""
    d = os.path.abspath(d)
    while True:
        if os.path.exists(os.path.join(d, ".git")):          # 저장소 표시
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _decode_file(data: bytes) -> str:
    """rg 의 BOM 처리 흉내 — UTF-8 BOM 은 떼고, UTF-16 BOM 은 풀어서. 그 밖은 UTF-8(깨진 바이트 U+FFFD)."""
    if data.startswith(b"\xef\xbb\xbf"):                     # UTF-8 BOM
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):   # UTF-16 BOM
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def builtin_search(pattern: str, search_path: str, file_pattern: Optional[str], cwd: str) -> tuple[str, str]:
    """[추가] rg --line-number --with-filename 과 같은 출력('경로:줄:내용\\n'…). 일치가 없으면 ExecError(code=1)(rg 종료 코드 1과 같게).
    따르는 규칙: 숨김 파일·폴더 제외, 기호 링크 제외, NUL 이 있는 파일(이진) 제외, .rgignore/.ignore(항상)·.gitignore(git 저장소 안에서만),
    -g 파일 패턴(맞지 않는 파일 제외)과 cn이 붙이는 -g "!<.gitignore 줄>"(우선), 파일·폴더는 이름순."""
    import pathspec                                          # glob 판정
    if node_platform() != "win32":                           # Unix 셸은 "…" 안을 해석함
        pattern = _sh_double_quote_unescape(pattern)
        file_pattern = _sh_double_quote_unescape(file_pattern) if file_pattern else file_pattern
    try:
        rx = re.compile(pattern)                             # rg(Rust regex)와 대부분 같은 문법
    except re.error as e:                                    # rg: regex parse error → 종료 코드 2
        raise ExecError(f"Command failed: rg\nregex parse error:\n    {pattern}\nerror: {e}\n", 2)
    root = search_path if os.path.isabs(search_path) else os.path.join(cwd, search_path)   # 실제 경로
    overrides: list[tuple[object, bool]] = []                # (규칙, 포함?) — 뒤에 맞은 것이 이김
    if file_pattern:                                         # -g "패턴" = 포함 목록
        overrides.append((pathspec.GitIgnoreSpec.from_lines([file_pattern]), True))
    for ip in get_gitignore_patterns(cwd):                   # -g "!패턴" = 제외
        overrides.append((pathspec.GitIgnoreSpec.from_lines([ip]), False))
    has_whitelist = any(inc for _, inc in overrides)         # 포함 목록이 있나
    ov_base = cwd if os.path.abspath(root).startswith(os.path.abspath(cwd)) else root   # 규칙 기준(cwd 안이면 cwd)
    use_git = _in_git_repo(root)                             # .gitignore 사용 여부

    def override_match(abs_p: str, is_dir: bool) -> Optional[bool]:
        """-g 판정 — True=포함(무시 규칙보다 우선), False=제외, None=해당 없음."""
        rel = os.path.relpath(abs_p, ov_base).replace(os.sep, "/")   # 기준 상대
        if is_dir:
            rel += "/"
        result = None                                        # 마지막으로 맞은 것
        for spec, inc in overrides:
            if spec.match_file(rel):                         # 맞음
                result = inc
        if result is None and has_whitelist and not is_dir:  # 포함 목록이 있는데 어느 것과도 안 맞는 파일
            return False
        return result

    # 위쪽 폴더의 무시 파일(rg 는 부모 폴더의 무시 파일도 읽음)
    parents: list[str] = []                                  # 루트→검색 폴더 바로 위
    d = os.path.dirname(os.path.abspath(root))
    while True:
        parents.insert(0, d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd

    def rules_for(dirs: list[str]) -> dict[str, list[_Rules]]:
        """종류별 규칙 목록(깊은 폴더가 앞) — rg 우선순위: .rgignore > .ignore > .gitignore, 같은 종류 안에서는 깊은 폴더."""
        out = {".rgignore": [], ".ignore": [], ".gitignore": []}
        for dd in reversed(dirs):                            # 깊은 폴더부터
            for name in out:
                if name == ".gitignore" and not use_git:     # git 밖이면 .gitignore 무시
                    continue
                r = _read_rules(dd, name)
                if r:
                    out[name].append(r)
        return out

    def ignored(abs_p: str, is_dir: bool, dirs: list[str]) -> bool:
        """무시 파일 판정."""
        by_kind = rules_for(dirs)                            # 규칙
        for name in (".rgignore", ".ignore", ".gitignore"):  # 종류 우선순위
            for r in by_kind[name]:                          # 깊은 폴더부터
                m = r.check(abs_p, is_dir)
                if m is not None:                            # 처음 맞은 것이 결정
                    return m
        return False

    lines_out: list[str] = []                                # 출력 줄

    def search_file(abs_p: str, shown: str) -> None:
        """파일 하나 검색 — 일치하는 줄마다 '보이는 경로:줄 번호:내용'."""
        try:
            with open(abs_p, "rb") as f:
                data = f.read()                              # 전부 읽기
        except OSError:                                      # 못 읽음(rg 는 경고 후 계속 — 종료 코드 2)
            return
        if b"\x00" in data:                                  # 이진 파일(rg 암묵 검색 기본: 건너뜀)
            return
        text = _decode_file(data)                            # 글로
        parts = text.split("\n")                             # 줄(\r 은 남김 — rg 와 같음)
        if parts and parts[-1] == "":                        # 끝 줄바꿈 뒤 빈 조각
            parts.pop()
        for i, line in enumerate(parts, 1):                  # 줄 번호 1부터
            if rx.search(line):                              # 일치
                lines_out.append(f"{shown}:{i}:{line}")

    def walk(abs_dir: str, shown_dir: str, dirs: list[str]) -> None:
        """폴더 훑기(이름순)."""
        try:
            names = sorted(os.listdir(abs_dir))              # 이름순(rg --sort path 와 같은 순서)
        except OSError:
            return
        here = dirs + [abs_dir]                              # 이 폴더까지의 무시 파일 위치
        for name in names:
            ab = os.path.join(abs_dir, name)                 # 실제 경로
            shown = os.path.join(shown_dir, name)            # 보이는 경로(검색 경로 글자 + 이름)
            if os.path.islink(ab):                           # 기호 링크는 따라가지 않음(rg 기본)
                continue
            is_dir = os.path.isdir(ab)                       # 폴더?
            ov = override_match(ab, is_dir) if overrides else None   # -g 판정
            if ov is False:                                  # 제외
                continue
            if ov is None:                                   # -g 해당 없음 → 숨김·무시 파일 규칙
                if name.startswith("."):                     # 숨김
                    continue
                if ignored(ab, is_dir, here):                # 무시 파일
                    continue
            if is_dir:
                walk(ab, shown, here)                        # 내려감
            elif os.path.isfile(ab):
                search_file(ab, shown)                       # 검색

    if os.path.isfile(root):                                 # 파일 하나(명시한 파일은 무시 규칙 적용 안 함 — rg 와 같음)
        search_file(root, search_path)
    else:
        walk(root, search_path, parents)                     # 폴더
    if not lines_out:                                        # 일치 없음 → rg 종료 코드 1
        raise ExecError(f"Command failed: rg\n", 1)
    return "\n".join(lines_out) + "\n", ""                   # rg 출력처럼 줄마다 \n


# ───────────────────────── 도구 ─────────────────────────
def _get_search_max_results() -> int:
    """CONTINUE_CLI_SEARCH_CODE_MAX_RESULTS(기본 100)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_SEARCH_CODE_MAX_RESULTS"), DEFAULT_SEARCH_MAX_RESULTS)


def _get_search_max_result_chars() -> int:
    """CONTINUE_CLI_SEARCH_CODE_MAX_RESULT_CHARS(기본 1000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_SEARCH_CODE_MAX_RESULT_CHARS"), DEFAULT_SEARCH_MAX_RESULT_CHARS)


def _js_substring(s: str, end: int) -> str:
    """substring(0, end) — UTF-16 위치 기준."""
    if js_len(s) == len(s):
        return s[:end]
    u = s.encode("utf-16-le", errors="surrogatepass")
    return u[: end * 2].decode("utf-16-le", errors="surrogatepass")


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 'Will search for: "패턴"'(50자 넘으면 자르고 ...)."""
    pattern = args.get("pattern")                            # 패턴(글이 아니면 cn은 TypeError)
    if not isinstance(pattern, str):                         # args.pattern.length — 숫자면 undefined > 50 은 false
        truncated = "undefined" if pattern is None else str(pattern)
    else:
        truncated = _js_substring(pattern, 50) + "..." if js_len(pattern) > 50 else pattern
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f'Will search for: "{truncated}"')])


def _no_match(pattern, file_pattern) -> str:
    """일치 없음 글."""
    return f'No matches found for pattern "{pattern}"' + (f' in files matching "{file_pattern}"' if file_pattern else "") + "."


def make_search_tool(cwd: str | os.PathLike | None = None) -> Tool:
    """Search 도구를 만든다(cwd = cn의 process.cwd())."""
    work_dir = str(cwd) if cwd is not None else os.getcwd()  # 작업 폴더

    async def run(args: dict, context=None) -> str:
        """run — 검색 후 긴 줄(1000자 초과) 버리고 100개까지."""
        pattern = args.get("pattern")                        # 패턴
        file_pattern = args.get("file_pattern")              # 파일 패턴
        search_path = args.get("path") or work_dir           # args.path || process.cwd()
        abs_check = search_path if os.path.isabs(search_path) else os.path.join(work_dir, search_path)   # existsSync(상대는 cwd 기준)
        if not os.path.exists(abs_check):                    # 없음
            raise ContinueError("Unspecified", f"Path does not exist: {search_path}")
        try:
            if await check_if_ripgrep_is_installed(work_dir):   # rg 있음
                stdout, stderr = await search_with_ripgrep(pattern, search_path, file_pattern, work_dir)
            elif builtin_search_enabled():                   # [추가] 내장 검색
                stdout, stderr = await asyncio.to_thread(builtin_search, pattern, search_path, file_pattern, work_dir)
            else:                                            # cn: grep/findstr
                stdout, stderr = await search_with_grep_or_findstr(pattern, search_path, file_pattern, work_dir)

            if stderr:                                       # 경고(종료 코드 0인데 stderr 있음)
                return f"Warning during search: {stderr}\n\n{stdout}"
            if not js_trim(stdout):                          # 빈 결과
                return _no_match(pattern, file_pattern)

            max_results = _get_search_max_results()          # 최대 개수
            max_result_chars = _get_search_max_result_chars()   # 줄 최대 글자
            split_lines = stdout.split("\n")                 # 줄
            lines = [ln for ln in split_lines if js_len(ln) <= max_result_chars]   # 긴 줄 버림(끝의 빈 줄도 남음 — cn 그대로)
            if len(lines) == 0:                              # 다 버려짐
                return _no_match(pattern, file_pattern)
            truncated = len(lines) > max_results             # 넘침
            limited = lines[:max_results]                    # 앞쪽만
            result_text = "\n".join(limited)                 # 이어 붙이기
            truncation_message = f"\n\n[Results truncated: showing {max_results} of {len(lines)} matches]" if truncated else ""
            return (f'Search results for pattern "{pattern}"' + (f' in files matching "{file_pattern}"' if file_pattern else "")
                    + f":\n\n{result_text}{truncation_message}")
        except ContinueError:                                # 그대로
            raise
        except ExecError as error:                           # exec 실패
            if error.code == 1:                              # 일치 없음(rg/grep 종료 코드 1)
                return _no_match(pattern, file_pattern)
            raise Exception(f"Error executing search: {error}")
        except Exception as error:                           # 그 밖
            raise Exception(f"Error executing search: {error}")

    return Tool(
        name="Search",
        display_name="Search",
        description="Search the codebase using ripgrep (rg) for a specific pattern",
        parameters={
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The search pattern to look for",
                },
                "path": {
                    "type": "string",
                    "description": "The path to search in (defaults to current directory)",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Optional file pattern to filter results (e.g., '*.ts')",
                },
            },
        },
        run=run,
        preprocess=_preprocess,
        readonly=True,
        is_built_in=True,
    )
