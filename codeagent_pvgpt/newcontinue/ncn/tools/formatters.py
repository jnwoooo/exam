# ncn/tools/formatters.py — 도구 인자 표시 형식 + Node 호환 경로·오류 도우미. cn extensions/cli/src/tools/formatters.ts
# formatToolArgument 는 cn 원본 그대로. 아래 "Node 호환" 부분은 cn이 쓰는 Node path·fs 동작을 같은 결과가 나오게 옮긴 것
# (Python os.path 와 Node path 는 끝 슬래시·빈 경로·"//"·Windows 루트 처리 등이 달라서 직접 옮김)
from __future__ import annotations                           # 타입 주석 지연 평가

import errno as _errno                                       # 오류 번호 → Node 오류 코드 이름
import ntpath                                                # Windows 경로 규칙(win32 분기)
import os                                                    # 현재 폴더·실경로
import re                                                    # 경로 나누기
import stat as _stat                                         # 링크 판별
import sys                                                   # 실행 OS 판별
from typing import Any                                       # 타입

# ───────────────────────── 실행 OS(Node process.platform 값) ─────────────────────────
PLATFORM_OVERRIDE: str | None = None                         # 시험용: "win32"/"darwin"/"linux"로 바꿔 분기 확인


def node_platform() -> str:                                 # Node process.platform 값을 흉내
    """Node process.platform 과 같은 값 — win32 / darwin / linux."""
    if PLATFORM_OVERRIDE:                                    # 시험에서 강제로 바꾼 값
        return PLATFORM_OVERRIDE                            # 강제 지정값 반환
    if sys.platform.startswith("win"):                       # Windows(32·64비트 모두 Node는 win32)
        return "win32"                                      # Windows 는 win32
    if sys.platform == "darwin":                            # macOS 인지
        return "darwin"                                     # darwin 반환
    return "linux"                                           # 그 밖(리눅스)


def _is_win() -> bool:                                      # win32 경로 규칙을 쓸지 판정
    """Node path 가 win32 규칙을 쓰는가(path = path.win32)."""
    return node_platform() == "win32"                       # 플랫폼이 win32 인가


# ───────────────────────── Node path.posix 이식(node lib/path.js) ─────────────────────────
def _normalize_string(path: str, allow_above_root: bool, separator: str, is_sep) -> str:  # Node normalizeString 이식
    """Node normalizeString — '.'·'..'·중복 구분자 정리(루트 없는 본문만)."""
    res = ""                                                 # 결과
    last_segment_length = 0                                  # 마지막 조각 길이
    last_slash = -1                                          # 마지막 구분자 위치
    dots = 0                                                 # 지금 조각의 점 개수(-1 = 점 아닌 글자 있음)
    code = ""                                                # 지금 글자
    i = 0                                                    # 위치
    while i <= len(path):                                    # 끝 다음 칸까지(가상의 구분자 처리)
        if i < len(path):                                    # 실제 글자
            code = path[i]                                  # 지금 글자
        elif is_sep(code):                                   # 마지막 글자가 구분자였으면 끝
            break                                           # 끝 처리 종료
        else:                                                # 끝에 가상의 '/' 를 둔다
            code = "/"                                      # 가상 구분자
        if is_sep(code):                                     # 조각 하나가 끝남
            if last_slash == i - 1 or dots == 1:             # 빈 조각 또는 '.' → 무시
                pass                                        # 무시
            elif dots == 2:                                 # 상위 폴더 '..' 처리
                if (len(res) < 2 or last_segment_length != 2  # 앞 조각이 '..' 이 아니면 한 단계 올라가는 조건
                        or res[-1] != "." or res[-2] != "."):   # 앞 조각이 '..' 가 아니면 한 단계 올라감
                    if len(res) > 2:                         # 앞 조각이 있으면 지움
                        last_slash_index = res.rfind(separator)   # 마지막 구분자
                        if last_slash_index == -1:           # 조각 하나뿐
                            res = ""                        # 조각 하나뿐이라 결과를 비움
                            last_segment_length = 0         # 마지막 조각 길이 초기화
                        else:                                # 마지막 조각만 지움
                            res = res[:last_slash_index]    # 마지막 조각을 잘라 냄
                            last_segment_length = len(res) - 1 - res.rfind(separator)  # 새 마지막 조각 길이 계산
                        last_slash = i                      # 구분자 위치 갱신
                        dots = 0                            # 점 세기 초기화
                        i += 1                              # 다음 글자로
                        continue                            # 다음 반복으로
                    elif len(res) != 0:                      # 짧은 조각 하나 → 비움
                        res = ""                            # 결과를 비움
                        last_segment_length = 0             # 마지막 조각 길이 초기화
                        last_slash = i                      # 구분자 위치 갱신
                        dots = 0                            # 점 세기 초기화
                        i += 1                              # 다음 글자로
                        continue                            # 다음 반복으로
                if allow_above_root:                         # 루트 위로 올라갈 수 있으면 '..' 남김
                    res += f"{separator}.." if len(res) > 0 else ".."  # 루트 위로 가는 '..' 을 결과에 덧붙임
                    last_segment_length = 2                 # 마지막 조각 길이는 '..' 의 2
            else:                                            # 보통 조각
                if len(res) > 0:                            # 결과가 있으면
                    res += f"{separator}{path[last_slash + 1:i]}"  # 구분자와 함께 조각 덧붙임
                else:                                       # 결과가 비었으면
                    res = path[last_slash + 1:i]            # 조각만 결과로
                last_segment_length = i - last_slash - 1    # 마지막 조각 길이 갱신
            last_slash = i                                  # 구분자 위치 갱신
            dots = 0                                        # 점 세기 초기화
        elif code == "." and dots != -1:                     # 점 세기
            dots += 1                                       # 점 개수 증가
        else:                                                # 점 아닌 글자
            dots = -1                                       # 점 아닌 글자 표시
        i += 1                                              # 다음 글자로
    return res                                              # 정리된 본문 반환


def _posix_is_sep(c: str) -> bool:                          # posix 구분자 판정
    """posix 구분자는 '/' 하나."""
    return c == "/"                                         # '/' 인지


def _posix_normalize(path: str) -> str:                     # Node path.posix.normalize 이식
    """Node path.posix.normalize — 끝 슬래시 유지, 빈 글은 '.'."""
    if len(path) == 0:                                       # 빈 경로
        return "."                                          # 빈 경로는 '.'
    is_absolute = path[0] == "/"                             # 절대경로?
    trailing = path[-1] == "/"                               # 끝 슬래시?
    path = _normalize_string(path, not is_absolute, "/", _posix_is_sep)   # 본문 정리
    if len(path) == 0:                                       # 다 지워짐
        if is_absolute:                                     # 절대경로면
            return "/"                                      # 루트만 반환
        return "./" if trailing else "."                    # 상대경로면 './' 또는 '.'
    if trailing:                                             # 끝 슬래시 되살림
        path += "/"                                         # 끝 슬래시 되살림
    return f"/{path}" if is_absolute else path              # 절대경로면 앞에 '/'


def _posix_resolve(*args: str) -> str:                      # Node path.posix.resolve 이식
    """Node path.posix.resolve — 오른쪽부터 절대경로가 나올 때까지 이어 붙이고 정리. 끝 슬래시 없음."""
    resolved_path = ""                                       # 결과 본문
    resolved_absolute = False                                # 절대경로가 됐나
    i = len(args) - 1                                        # 오른쪽부터
    while i >= -1 and not resolved_absolute:                 # -1 = 현재 폴더
        path = args[i] if i >= 0 else os.getcwd()            # 인자 또는 cwd
        i -= 1                                              # 다음 인자로
        if len(path) == 0:                                   # 빈 인자는 건너뜀
            continue                                        # 건너뜀
        resolved_path = f"{path}/{resolved_path}"            # 앞에 붙임
        resolved_absolute = path[0] == "/"                   # 절대경로면 멈춤
    resolved_path = _normalize_string(resolved_path, not resolved_absolute, "/", _posix_is_sep)   # 정리
    if resolved_absolute:                                   # 절대경로면
        return f"/{resolved_path}"                          # 절대경로 반환
    return resolved_path if len(resolved_path) > 0 else "."  # 상대경로 결과(비었으면 '.')


def _posix_relative(frm: str, to: str) -> str:              # Node path.posix.relative 이식
    """Node path.posix.relative — 같으면 ''."""
    if frm == to:                                            # 글자 그대로 같음
        return ""                                           # 같으면 빈 글
    frm = _posix_resolve(frm)                                # 둘 다 절대경로로
    to = _posix_resolve(to)                                 # 둘 다 절대경로로
    if frm == to:                                            # 정리 후 같음
        return ""                                           # 같으면 빈 글
    f_parts = [p for p in frm.split("/") if p]               # 조각들
    t_parts = [p for p in to.split("/") if p]               # 조각들
    n = 0                                                    # 공통 앞부분 길이
    while n < len(f_parts) and n < len(t_parts) and f_parts[n] == t_parts[n]:  # 공통 앞부분을 센다
        n += 1                                              # 공통 조각 증가
    ups = [".."] * (len(f_parts) - n)                        # 올라갈 단계
    return "/".join(ups + t_parts[n:])                       # 올라간 뒤 내려갈 경로


def _posix_dirname(path: str) -> str:                       # Node path.posix.dirname 이식
    """Node path.posix.dirname — 끝 슬래시 무시, 루트만 남으면 '/'."""
    if len(path) == 0:                                      # 빈 경로는 '.'
        return "."                                          # 빈 경로는 '.'
    has_root = path[0] == "/"                                # 루트로 시작?
    end = -1                                                 # 잘라 낼 위치
    matched_slash = True                                     # 끝의 슬래시들을 지나는 중
    for i in range(len(path) - 1, 0, -1):                    # 뒤에서 앞으로(0번 제외)
        if path[i] == "/":                                  # 슬래시를 만나면
            if not matched_slash:                            # 글자를 지난 뒤 만난 첫 슬래시
                end = i                                     # 잘라 낼 위치 확정
                break                                       # 탐색 종료
        else:                                               # 글자를 만나면
            matched_slash = False                           # 끝 슬래시 구간 끝
    if end == -1:                                            # 슬래시 없음
        return "/" if has_root else "."                     # 루트면 '/', 아니면 '.'
    if has_root and end == 1:                                # "//x" 형태
        return "//"                                         # '//' 로 시작하는 경우
    return path[:end]                                       # 마지막 조각 앞까지 반환


def _posix_basename(path: str) -> str:                      # Node path.posix.basename 이식
    """Node path.posix.basename(확장자 인자 없음) — 끝 슬래시 무시."""
    start = 0                                                # 시작
    end = -1                                                 # 끝(없으면 -1)
    matched_slash = True                                     # 끝 슬래시 지나는 중
    for i in range(len(path) - 1, -1, -1):                   # 뒤에서 앞으로
        if path[i] == "/":                                  # 슬래시를 만나면
            if not matched_slash:                            # 이름 앞의 슬래시
                start = i + 1                               # 이름의 시작 확정
                break                                       # 탐색 종료
        elif end == -1:                                      # 이름의 마지막 글자
            matched_slash = False                           # 끝 슬래시 구간 끝
            end = i + 1                                     # 이름의 끝 위치
    if end == -1:                                           # 이름이 없으면
        return ""                                           # 이름이 없으면 빈 글
    return path[start:end]                                  # 이름 반환


# ───────────────────────── Node path.win32 이식(node v24 lib/path.js) ─────────────────────────
_WINDOWS_RESERVED_NAMES = [                                  # Windows 예약 장치 이름(Node 목록 그대로)
    "CON", "PRN", "AUX", "NUL",                             # 기본 장치 이름
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",  # COM 포트 이름
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",  # LPT 포트 이름
    "COM\xb9", "COM\xb2", "COM\xb3",                        # 위첨자 숫자 COM 이름
    "LPT\xb9", "LPT\xb2", "LPT\xb3",                        # 위첨자 숫자 LPT 이름
]                                                           # 목록 닫기


def _win_is_sep(c: str) -> bool:                            # win32 구분자 판정
    """win32 구분자는 '/' 와 '\\'."""
    return c in ("/", "\\")                                 # '/' 또는 '\\' 인지


def _win_is_device_root(c: str) -> bool:                    # 드라이브 글자 판정
    """드라이브 글자(A-Z, a-z)."""
    return ("A" <= c <= "Z") or ("a" <= c <= "z")           # 영문자인지


def _win_is_reserved_name(path: str, colon_index: int) -> bool:  # 예약 장치 이름인지 판정
    """isWindowsReservedName — 콜론 앞이 예약 장치 이름인가(대문자 비교). colon_index=-1 이면 JS slice(0,-1) 와 같게."""
    device_part = (path[:colon_index] if colon_index >= 0 else path[:-1]).upper()   # JS slice(0, -1) == 끝 한 글자 뺌
    return device_part in _WINDOWS_RESERVED_NAMES           # 목록에 있는지


def _char(s: str, i: int) -> str:                           # JS 문자 접근 흉내
    """JS charCodeAt 비교용 — 범위 밖이면 빈 글(NaN 처럼 아무것과도 같지 않음)."""
    return s[i] if 0 <= i < len(s) else ""                  # 범위 밖이면 빈 글


def _win_is_absolute(path: str) -> bool:                    # Node path.win32.isAbsolute 이식
    """Node path.win32.isAbsolute — 구분자로 시작하거나 'X:\\'."""
    if len(path) == 0:                                      # 빈 경로는 상대
        return False                                        # 빈 경로는 상대
    c = path[0]                                             # 첫 글자
    return _win_is_sep(c) or (len(path) > 2 and _win_is_device_root(c) and path[1] == ":" and _win_is_sep(path[2]))  # 구분자로 시작하거나 'X:\\' 형태면 절대


def _win_match_unc(path: str) -> tuple[int, int, int, str] | None:  # UNC 경로의 서버·공유 위치 찾기
    """'\\\\서버\\공유' 앞부분 찾기 — (서버 끝 j1, 공유 시작 last, 공유 끝 j, 서버 이름) 또는 None."""
    n = len(path)                                           # 글자 수
    j = 2                                                   # 서버 이름 시작 위치
    last = j                                                # 공유 시작 후보
    while j < n and not _win_is_sep(path[j]):                # 서버 이름
        j += 1                                              # 서버 이름 끝까지
    if not (j < n and j != last):                           # 서버 이름이 없으면 UNC 아님
        return None                                         # 서버 이름이 없으면 UNC 아님
    first_part = path[last:j]                               # 서버 이름
    last = j                                                # 구분자 시작 위치
    while j < n and _win_is_sep(path[j]):                    # 구분자들
        j += 1                                              # 구분자들을 건너뜀
    if not (j < n and j != last):                           # 공유 이름이 없으면 UNC 아님
        return None                                         # 공유 이름이 없으면 UNC 아님
    last = j                                                # 공유 시작 위치
    while j < n and not _win_is_sep(path[j]):                # 공유 이름
        j += 1                                              # 공유 이름 끝까지
    return (0, last, j, first_part)                         # (0, 공유 시작, 공유 끝, 서버 이름) 반환


def _win_normalize(path: str) -> str:                       # Node path.win32.normalize 이식
    """Node path.win32.normalize — 루트(드라이브·UNC·장치) 처리 + 본문 정리 + CVE-2024-36139 보호."""
    n = len(path)                                           # 글자 수
    if n == 0:                                              # 빈 경로는 '.'
        return "."                                          # '.' 반환
    root_end = 0                                             # 루트 끝 위치
    device: str | None = None                                # 장치(드라이브·UNC) 부분
    is_absolute = False                                      # 절대경로?
    code = path[0]                                          # 첫 글자
    if n == 1:                                               # 한 글자
        return "\\" if code == "/" else path                # 한 글자면 '/' 만 '\\' 로
    if _win_is_sep(code):                                    # 구분자로 시작 — UNC 가능
        is_absolute = True                                  # 절대경로 표시
        if _win_is_sep(path[1]):                             # '\\' 로 시작
            m = _win_match_unc(path)                        # UNC 위치 찾기
            if m is not None:                               # UNC 로 인식됐으면
                _, last, j, first_part = m                  # UNC 위치 풀기
                if j == n or j != last:                     # 서버·공유가 온전할 때
                    if first_part in (".", "?"):             # 장치 루트(\\.\PHYSICALDRIVE0)
                        device = f"\\\\{first_part}"        # 장치 이름 구성
                        root_end = 4                        # 루트 끝 위치
                        colon_index = path.find(":")        # 콜론 위치
                        possible_device = path[4:colon_index + 1]  # 장치 이름 후보 추출
                        if _win_is_reserved_name(possible_device, len(possible_device) - 1):  # 예약 장치 이름이면
                            device = f"\\\\?\\{possible_device}"  # 장치 이름 재구성
                            root_end = 4 + len(possible_device)  # 루트 끝 위치 갱신
                    elif j == n:                             # UNC 루트뿐
                        return f"\\\\{first_part}\\{path[last:]}\\"  # UNC 루트뿐이면 바로 반환
                    else:                                    # UNC 루트 + 나머지
                        device = f"\\\\{first_part}\\{path[last:j]}"  # UNC 루트 장치 이름
                        root_end = j                        # 루트 끝 위치
        else:                                               # 구분자 하나로 시작하는 경우
            root_end = 1                                    # 구분자 하나뿐인 루트
    else:                                                   # 구분자로 시작하지 않는 경우
        colon_index = path.find(":")                        # 콜론 위치
        if colon_index > 0:                                 # 콜론이 있으면
            if _win_is_device_root(code) and colon_index == 1:  # 'C:' 형태의 드라이브
                device = path[0:2]                          # 드라이브 이름
                root_end = 2                                # 루트 끝 위치
                if n > 2 and _win_is_sep(path[2]):          # 뒤에 구분자가 있으면
                    is_absolute = True                      # 절대경로 표시
                    root_end = 3                            # 루트 끝 위치 갱신
            elif _win_is_reserved_name(path, colon_index):   # 'CON:' 등
                device = path[0:colon_index + 1]            # 예약 장치 이름
                root_end = colon_index + 1                  # 루트 끝 위치
    tail = _normalize_string(path[root_end:], not is_absolute, "\\", _win_is_sep) if root_end < n else ""   # 본문 정리
    if len(tail) == 0 and not is_absolute:                  # 본문이 비고 상대경로면
        tail = "."                                          # 본문이 비면 '.'
    if len(tail) > 0 and _win_is_sep(path[n - 1]):           # 끝 구분자 유지
        tail += "\\"                                        # 끝 구분자 유지
    if not is_absolute and device is None and ":" in path:   # 상대경로가 드라이브처럼 보이지 않게(CVE-2024-36139)
        if len(tail) >= 2 and _win_is_device_root(tail[0]) and tail[1] == ":":  # 드라이브처럼 보이면
            return f".\\{tail}"                             # '.\\' 를 붙여 드라이브처럼 안 보이게
        index = path.find(":")                              # 첫 콜론 위치
        while index != -1:                                  # 콜론이 있는 동안
            if index == n - 1 or _win_is_sep(_char(path, index + 1)):  # 콜론 뒤가 끝이거나 구분자면
                return f".\\{tail}"                         # '.\\' 를 붙여 반환
            index = path.find(":", index + 1)               # 다음 콜론 탐색
    colon_index = path.find(":")                            # 콜론 위치
    if _win_is_reserved_name(path, colon_index):             # 예약 이름
        return f".\\{device or ''}{tail}"                   # 예약 이름이면 '.\\' 를 붙여 반환
    if device is None:                                      # 장치가 없으면
        return f"\\{tail}" if is_absolute else tail         # 장치 없으면 본문만
    return f"{device}\\{tail}" if is_absolute else f"{device}{tail}"  # 장치와 본문을 이어 반환


def _win_resolve(*args: str) -> str:                        # Node path.win32.resolve 이식
    """Node path.win32.resolve — 드라이브별 현재 폴더(환경변수 '=C:')까지 Node 그대로."""
    resolved_device = ""                                     # 결정된 장치
    resolved_tail = ""                                       # 결정된 본문
    resolved_absolute = False                                # 절대경로가 됐나
    i = len(args) - 1                                       # 오른쪽 인자부터
    while i >= -1:                                           # 오른쪽부터(-1 = cwd)
        if i >= 0:                                          # 인자가 남아 있으면
            path = args[i]                                  # 인자 하나
            i -= 1                                          # 다음 인자로
            if len(path) == 0:                               # 빈 인자 건너뜀
                continue                                    # 건너뜀
        elif len(resolved_device) == 0:                      # 장치가 아직 없으면 cwd
            i -= 1                                          # 다음 단계로
            path = os.getcwd()                              # 현재 폴더 사용
            if len(args) == 0 or (len(args) == 1 and args[0] in ("", ".") and _win_is_sep(_char(path, 0))):   # 빠른 길
                if not sys.platform.startswith("win"):           # Node: 실제 Windows 가 아니면 '/' → '\'
                    path = path.replace("/", "\\")          # '\\' 로 바꿔 둠
                return path                                 # 빠른 길 결과 반환
        else:                                                # 드라이브별 현재 폴더
            i -= 1                                          # 다음 단계로
            path = os.environ.get(f"={resolved_device}") or os.getcwd()  # 그 드라이브의 현재 폴더(환경변수 '=C:')
            if path is None or (path[0:2].lower() != resolved_device.lower() and _char(path, 2) == "\\"):  # 다른 드라이브면
                path = f"{resolved_device}\\"               # 드라이브 루트로 대체
        n = len(path)                                       # 글자 수
        root_end = 0                                        # 루트 끝 위치
        device = ""                                         # 장치 이름
        is_absolute = False                                 # 절대경로 여부
        code = _char(path, 0)                               # 첫 글자
        if n == 1:                                           # 한 글자
            if _win_is_sep(code):                           # 구분자 한 글자면
                root_end = 1                                # 루트 끝 위치
                is_absolute = True                          # 절대경로
        elif _win_is_sep(code):                              # UNC 가능
            is_absolute = True                              # 절대경로 표시
            if _win_is_sep(path[1]):                        # 두 번째도 구분자면 UNC 후보
                m = _win_match_unc(path)                    # UNC 위치 찾기
                if m is not None:                           # UNC 로 인식됐으면
                    _, last, j, first_part = m              # UNC 위치 풀기
                    if j == n or j != last:                 # 서버·공유가 온전할 때
                        if first_part not in (".", "?"):     # UNC 루트
                            device = f"\\\\{first_part}\\{path[last:j]}"  # UNC 장치 이름
                            root_end = j                    # 루트 끝 위치
                        else:                                # 장치 루트
                            device = f"\\\\{first_part}"    # 장치 루트 이름
                            root_end = 4                    # 루트 끝 위치
            else:                                           # 구분자 하나로 시작하는 경우
                root_end = 1                                # 구분자 하나뿐인 루트
        elif _win_is_device_root(code) and _char(path, 1) == ":":   # 드라이브
            device = path[0:2]                              # 드라이브 이름
            root_end = 2                                    # 루트 끝 위치
            if n > 2 and _win_is_sep(path[2]):              # 뒤에 구분자가 있으면
                is_absolute = True                          # 절대경로 표시
                root_end = 3                                # 루트 끝 위치 갱신
        if len(device) > 0:                                 # 장치가 있으면
            if len(resolved_device) > 0:                    # 이미 정한 장치가 있으면
                if device.lower() != resolved_device.lower():   # 다른 드라이브는 무시
                    continue                                # 다음 인자로
            else:                                           # 아직 장치가 없으면
                resolved_device = device                    # 처음 만난 장치를 채택
        if resolved_absolute:                               # 이미 절대경로면
            if len(resolved_device) > 0:                    # 장치가 이미 정해졌으면
                break                                       # 장치까지 정해졌으니 끝
        else:                                               # 아직 절대경로가 아니면
            resolved_tail = f"{path[root_end:]}\\{resolved_tail}"  # 본문을 앞에 이어 붙임
            resolved_absolute = is_absolute                 # 절대경로 여부 갱신
            if is_absolute and len(resolved_device) > 0:    # 절대경로이고 장치도 있으면
                break                                       # 끝
    resolved_tail = _normalize_string(resolved_tail, not resolved_absolute, "\\", _win_is_sep)   # 정리
    if resolved_absolute:                                   # 절대경로면
        return f"{resolved_device}\\{resolved_tail}"        # 절대경로 결과 반환
    return f"{resolved_device}{resolved_tail}" or "."       # 상대경로 결과(비었으면 '.')


def _win_relative(frm: str, to: str) -> str:                # Node path.win32.relative 이식
    """Node path.win32.relative — 대소문자 무시 비교, 드라이브가 다르면 to 그대로."""
    if frm == to:                                           # 같으면 빈 글
        return ""                                           # 같으면 빈 글
    from_orig = _win_resolve(frm)                           # 둘 다 절대경로로
    to_orig = _win_resolve(to)                              # to 도 절대경로로
    if from_orig == to_orig:                                # 같으면 빈 글
        return ""                                           # 정리 후 같으면 빈 글
    f = from_orig.lower()                                   # 소문자 비교용
    t = to_orig.lower()                                     # to 소문자 비교용
    if f == t:                                              # 대소문자만 다르면
        return ""                                           # 빈 글
    if len(from_orig) != len(f) or len(to_orig) != len(t):   # 소문자화로 길이가 바뀌는 글자가 있을 때
        fs = from_orig.split("\\")                          # 조각으로 나눔
        ts = to_orig.split("\\")                            # to 조각
        if fs[-1] == "":                                    # 끝 빈 조각이면
            fs.pop()                                        # 제거
        if ts[-1] == "":                                    # 끝 빈 조각이면
            ts.pop()                                        # 제거
        length = min(len(fs), len(ts))                      # 공통 비교 길이
        k = 0                                               # 공통 조각 수
        while k < length and fs[k].lower() == ts[k].lower():  # 공통 조각을 센다
            k += 1                                          # 공통 조각 증가
        if k == 0:                                          # 공통이 없으면
            return to_orig                                  # 드라이브가 다르면 to 그대로
        if k == length:                                     # 한쪽이 다른 쪽의 앞부분이면
            if len(ts) > length:                            # to 가 더 길면
                return "\\".join(ts[k:])                    # to 의 나머지 조각 반환
            if len(fs) > length:                            # from 이 더 길면
                return "..\\" * (len(fs) - 1 - k) + ".."    # 올라갈 '..' 반환
            return ""                                       # 같으면 빈 글
        return "..\\" * (len(fs) - k) + "\\".join(ts[k:])   # 올라간 뒤 내려갈 경로
    from_start = 0                                           # 앞 '\' 건너뜀
    while from_start < len(f) and f[from_start] == "\\":    # 앞 '\\' 건너뜀
        from_start += 1                                     # 시작 위치 증가
    from_end = len(f)                                        # 끝 '\' 건너뜀(UNC)
    while from_end - 1 > from_start and f[from_end - 1] == "\\":  # 끝 '\\' 건너뜀
        from_end -= 1                                       # 끝 위치 감소
    from_len = from_end - from_start                        # from 길이
    to_start = 0                                            # to 시작 위치
    while to_start < len(t) and t[to_start] == "\\":        # to 앞 '\\' 건너뜀
        to_start += 1                                       # 시작 위치 증가
    to_end = len(t)                                         # to 끝 위치
    while to_end - 1 > to_start and t[to_end - 1] == "\\":  # to 끝 '\\' 건너뜀
        to_end -= 1                                         # 끝 위치 감소
    to_len = to_end - to_start                              # to 길이
    length = min(from_len, to_len)                           # 공통 앞부분
    last_common_sep = -1                                    # 마지막 공통 구분자 위치
    k = 0                                                   # 공통 글자 수
    while k < length:                                       # 공통 글자를 센다
        fc = f[from_start + k]                              # from 의 글자
        if fc != t[to_start + k]:                           # 다르면 중단
            break                                           # 다르면 중단
        if fc == "\\":                                      # 구분자면
            last_common_sep = k                             # 위치 기록
        k += 1                                              # 공통 글자 증가
    if k != length:                                         # 중간에서 갈라졌으면
        if last_common_sep == -1:                            # 첫 구분자 전에 다름
            return to_orig                                  # to 그대로 반환
    else:                                                   # 한쪽이 다른 쪽의 앞부분이면
        if to_len > length:                                 # to 가 더 길면
            if _char(t, to_start + k) == "\\":               # from 이 to 의 바로 위
                return to_orig[to_start + k + 1:]           # 그 뒤 경로 반환
            if k == 2:                                       # from 이 드라이브 루트
                return to_orig[to_start + k:]               # 드라이브 루트 뒤 경로 반환
        if from_len > length:                               # from 이 더 길면
            if _char(f, from_start + k) == "\\":             # to 가 from 의 위
                last_common_sep = k                         # 공통 구분자 위치 갱신
            elif k == 2:                                     # to 가 드라이브 루트
                last_common_sep = 3                         # 드라이브 루트 처리
        if last_common_sep == -1:                           # 공통 구분자가 없으면
            last_common_sep = 0                             # 0 으로 둠
    out = ""                                                # 올라갈 '..' 모음
    k = from_start + last_common_sep + 1                    # from 에서 공통 구분자 다음 위치
    while k <= from_end:                                     # 올라갈 단계
        if k == from_end or f[k] == "\\":                   # 조각이 끝나면
            out += ".." if len(out) == 0 else "\\.."        # '..' 추가
        k += 1                                              # 다음 위치로
    to_start += last_common_sep                             # to 시작 위치 보정
    if len(out) > 0:                                        # 올라갈 단계가 있으면
        return f"{out}{to_orig[to_start:to_end]}"           # '..' 들과 to 나머지를 이어 반환
    if _char(to_orig, to_start) == "\\":                    # to 가 구분자로 시작하면
        to_start += 1                                       # 앞 구분자를 건너뜀
    return to_orig[to_start:to_end]                         # to 의 나머지 반환


def _win_dirname(path: str) -> str:                         # Node path.win32.dirname 이식
    """Node path.win32.dirname."""
    n = len(path)                                           # 글자 수
    if n == 0:                                              # 빈 경로는 '.'
        return "."                                          # 빈 경로는 '.'
    root_end = -1                                           # 루트 끝 위치
    offset = 0                                              # 탐색 시작 위치
    code = path[0]                                          # 첫 글자
    if n == 1:                                               # 한 글자
        return path if _win_is_sep(code) else "."           # 구분자면 그대로, 아니면 '.'
    if _win_is_sep(code):                                    # UNC 가능
        root_end = offset = 1                               # 루트 끝 위치와 탐색 시작 위치
        if _win_is_sep(path[1]):                            # 두 번째도 구분자면 UNC 후보
            m = _win_match_unc(path)                        # UNC 위치 찾기
            if m is not None:                               # UNC 로 인식됐으면
                _, last, j, _first = m                      # UNC 위치 풀기
                if j == n:                                   # UNC 루트뿐
                    return path                             # UNC 루트뿐이면 그대로 반환
                if j != last:                                # UNC 루트 + 나머지
                    root_end = offset = j + 1               # 루트 끝 위치 갱신
    elif _win_is_device_root(code) and path[1] == ":":       # 드라이브
        root_end = 3 if n > 2 and _win_is_sep(path[2]) else 2  # 드라이브 루트 끝 위치
        offset = root_end                                   # 탐색 시작 위치
    end = -1                                                # 잘라 낼 위치
    matched_slash = True                                    # 끝 구분자 구간을 지나는 중
    for i in range(n - 1, offset - 1, -1):                   # 뒤에서 앞으로
        if _win_is_sep(path[i]):                            # 구분자를 만나면
            if not matched_slash:                           # 글자를 지난 뒤면
                end = i                                     # 잘라 낼 위치 확정
                break                                       # 탐색 종료
        else:                                               # 글자를 만나면
            matched_slash = False                           # 끝 구분자 구간 끝
    if end == -1:                                           # 구분자가 없으면
        if root_end == -1:                                  # 루트도 없으면
            return "."                                      # '.' 반환
        end = root_end                                      # 루트 끝에서 자름
    return path[:end]                                       # 앞부분 반환


def _win_basename(path: str) -> str:                        # Node path.win32.basename 이식
    """Node path.win32.basename(확장자 인자 없음) — 'X:' 드라이브 접두 제외."""
    start = 0                                               # 시작 위치
    end = -1                                                # 끝 위치
    matched_slash = True                                    # 끝 구분자 구간을 지나는 중
    if len(path) >= 2 and _win_is_device_root(path[0]) and path[1] == ":":   # 드라이브 접두
        start = 2                                           # 드라이브 접두 다음부터
    for i in range(len(path) - 1, start - 1, -1):           # 뒤에서 앞으로
        if _win_is_sep(path[i]):                            # 구분자를 만나면
            if not matched_slash:                           # 글자를 지난 뒤면
                start = i + 1                               # 이름의 시작 확정
                break                                       # 탐색 종료
        elif end == -1:                                     # 이름의 마지막 글자
            matched_slash = False                           # 끝 구분자 구간 끝
            end = i + 1                                     # 이름의 끝 위치
    if end == -1:                                           # 이름이 없으면
        return ""                                           # 이름이 없으면 빈 글
    return path[start:end]                                  # 이름 반환


def _win_join(*args: str) -> str:                           # Node path.win32.join 이식
    """Node path.win32.join — '\\'로 이어 붙이고, 앞 '//' 정리, 예약 이름이 있으면 정리하지 않음."""
    joined = None                                           # 이어 붙인 결과
    first_part = ""                                         # 첫 조각
    for arg in args:                                         # 빈 조각 제외하고 이어 붙임
        if len(arg) > 0:                                    # 빈 조각은 건너뜀
            if joined is None:                              # 첫 조각이면
                joined = first_part = arg                   # 결과와 첫 조각으로 저장
            else:                                           # 이미 이어 붙인 것이 있으면
                joined += f"\\{arg}"                        # '\\' 를 붙여 이음
    if joined is None:                                      # 이을 조각이 없으면
        return "."                                          # '.' 반환
    needs_replace = True                                     # UNC 로 오해하지 않게 앞 구분자 정리
    slash_count = 0                                         # 앞 구분자 개수
    if _win_is_sep(first_part[0]):                          # 첫 조각이 구분자로 시작하면
        slash_count += 1                                    # 개수 증가
        if len(first_part) > 1 and _win_is_sep(first_part[1]):  # 두 번째도 구분자면
            slash_count += 1                                # 개수 증가
            if len(first_part) > 2:                         # 세 번째 글자가 있으면
                if _win_is_sep(first_part[2]):              # 구분자면
                    slash_count += 1                        # 개수 증가
                else:                                        # UNC 로 의도함
                    needs_replace = False                   # UNC 의도라 정리하지 않음
    if needs_replace:                                       # 정리가 필요하면
        while slash_count < len(joined) and _win_is_sep(joined[slash_count]):  # 앞 구분자를 더 센다
            slash_count += 1                                # 개수 증가
        if slash_count >= 2:                                # 둘 이상이면
            joined = f"\\{joined[slash_count:]}"            # 구분자 하나로 줄임
    parts = [p for p in re.split(r"\\+", joined) if p]       # '\' 로만 나눈 조각들
    if any((p.find(":") != -1 and _win_is_reserved_name(p, p.find(":"))) for p in parts):   # 예약 이름 → '/'만 '\'로
        return joined.replace("/", "\\")                    # 예약 이름이면 '/' 만 '\\' 로 바꿔 반환
    return _win_normalize(joined)                           # 정리해서 반환


# ───────────────────────── 공개: 실행 OS에 따라 posix/win32 선택(Node 'path' 기본 동작) ─────────────────────────
def path_is_absolute(path: str) -> bool:                    # 절대경로인지 판정(Node path.isAbsolute)
    """Node path.isAbsolute."""
    return _win_is_absolute(path) if _is_win() else path.startswith("/")  # 플랫폼에 맞는 규칙으로 판정


def path_normalize(path: str) -> str:                       # 경로 정리(Node path.normalize)
    """Node path.normalize."""
    return _win_normalize(path) if _is_win() else _posix_normalize(path)  # 플랫폼에 맞는 규칙으로 정리


def path_resolve(*args: str) -> str:                        # 절대경로로 만들기(Node path.resolve)
    """Node path.resolve(…) — 빈 인자면 cwd."""
    return _win_resolve(*args) if _is_win() else _posix_resolve(*args)  # 플랫폼에 맞는 규칙으로 절대경로화


def path_relative(frm: str, to: str) -> str:                # 상대경로 구하기(Node path.relative)
    """Node path.relative."""
    return _win_relative(frm, to) if _is_win() else _posix_relative(frm, to)  # 플랫폼에 맞는 규칙으로 상대경로화


def path_join(*args: str) -> str:                           # 경로 잇기(Node path.join)
    """Node path.join — 빈 조각 빼고 구분자로 이어 붙인 뒤 normalize(절대 조각이어도 갈아 끼우지 않음)."""
    if _is_win():                                            # win32 는 따로(앞 '//'·예약 이름 처리)
        return _win_join(*args)                             # win32 이음
    parts = [a for a in args if a]                           # 빈 조각 제외
    if not parts:                                           # 조각이 없으면
        return "."                                          # '.' 반환
    return _posix_normalize("/".join(parts))                # '/' 로 이어 정리해 반환


def path_dirname(path: str) -> str:                         # 상위 폴더(Node path.dirname)
    """Node path.dirname."""
    return _win_dirname(path) if _is_win() else _posix_dirname(path)  # 플랫폼에 맞는 규칙으로 상위 폴더


def path_basename(path: str) -> str:                        # 파일 이름(Node path.basename)
    """Node path.basename."""
    return _win_basename(path) if _is_win() else _posix_basename(path)  # 플랫폼에 맞는 규칙으로 파일 이름


# ───────────────────────── Node fs 오류 글 ─────────────────────────
_UV_DESC = {                                                 # libuv 오류 설명(Node 오류 글에 그대로 나옴)
    "ENOENT": "no such file or directory",                  # 파일·폴더 없음
    "ENOTDIR": "not a directory",                           # 폴더가 아님
    "EISDIR": "illegal operation on a directory",           # 폴더에 대한 잘못된 연산
    "EACCES": "permission denied",                          # 권한 거부
    "EPERM": "operation not permitted",                     # 허용 안 된 연산
    "EEXIST": "file already exists",                        # 이미 있음
    "ELOOP": "too many symbolic links encountered",         # 심볼릭 링크 너무 많음
    "ENAMETOOLONG": "name too long",                        # 이름이 너무 김
    "EBUSY": "resource busy or locked",                     # 자원 사용 중
    "EMFILE": "too many open files",                        # 열린 파일 너무 많음
    "ENOSPC": "no space left on device",                    # 저장 공간 부족
    "EROFS": "read-only file system",                       # 읽기 전용 파일 시스템
    "EINVAL": "invalid argument",                           # 잘못된 인자
}                                                           # 여기까지 닫기


def node_fs_error_message(err: OSError, syscall: str, path: str | None = None) -> str:  # OSError 를 Node fs 오류 글로 변환
    """Python OSError → Node fs 오류 글. 예: "ENOENT: no such file or directory, realpath '/a/b'"."""
    code = _errno.errorcode.get(err.errno or 0, "EIO")      # 오류 코드 이름
    if isinstance(err, IsADirectoryError):                   # Windows에서 디렉터리 열기는 EACCES로 오기도 함 → 종류로 보정
        code = "EISDIR"                                     # 폴더면 EISDIR
    elif isinstance(err, FileNotFoundError):                # 파일 없음 종류면
        code = "ENOENT"                                     # 파일 없음 코드로 보정
    elif isinstance(err, NotADirectoryError):               # 폴더가 아님 종류면
        code = "ENOTDIR"                                    # 폴더 아님 코드로 보정
    elif isinstance(err, PermissionError) and code not in ("EACCES", "EPERM"):  # 권한 오류 종류면
        code = "EACCES"                                     # 권한 거부 코드로 보정
    desc = _UV_DESC.get(code, (err.strerror or "unknown error").lower())   # 설명
    if path is None:                                         # 경로 없는 글(예: 디렉터리 read)
        return f"{code}: {desc}, {syscall}"                 # 경로 없는 오류 글
    return f"{code}: {desc}, {syscall} '{path}'"            # 경로가 있는 오류 글


class NodeFsError(Exception):                               # Node fs 오류를 흉내 내는 예외
    """Node fs 함수가 던지는 오류 흉내 — str(e)가 Node 오류 글과 같다."""

    def __init__(self, message: str, code: str):            # 메시지와 코드로 만든다
        super().__init__(message)                            # str(e) == Node 오류 글
        self.code = code                                     # "ENOENT" 등


def realpath_sync(path: str) -> str:                        # Node realpathSync 이식(조각마다 lstat)
    """Node fs.realpathSync(JS 구현) — 루트부터 조각마다 lstat 하며 링크를 따라간다.
    실패하면 Node와 같은 글: 처음 실패한 조각의 lstat 오류(예: "ENOENT: no such file or directory, lstat '/a/b'")."""
    win = _is_win()                                          # 구분자 결정
    sep = "\\" if win else "/"                               # 구분자
    p = path_resolve(path)                                   # 먼저 절대경로로
    links = 0                                                # 따라간 링크 수(무한 반복 방지)
    while True:                                              # 링크를 만나면 처음부터 다시
        if win:                                              # Windows 루트: "C:\" 또는 "\\server\share\"
            drive, rest = ntpath.splitdrive(p)              # 드라이브와 나머지 분리
            root = drive + "\\"                             # 루트 문자열
        else:                                                # posix 루트 "/"
            root, rest = "/", p                             # posix 루트는 '/'
        parts = [x for x in rest.replace("\\", "/").split("/") if x]   # 조각들
        current = root                                       # 지금까지 확인한 실경로
        restarted = False                                    # 링크로 다시 시작하나
        for idx, part in enumerate(parts):                   # 조각마다
            nxt = current + part if current.endswith(sep) else current + sep + part   # 다음 경로
            try:                                            # lstat 시도
                st = os.lstat(nxt)                           # 링크 자체 정보
            except OSError as e:                             # Node: lstat 오류
                msg = node_fs_error_message(e, "lstat", nxt)  # Node 형식 오류 글
                raise NodeFsError(msg, msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림
            if _stat.S_ISLNK(st.st_mode):                    # 심볼릭 링크
                links += 1                                  # 링크 수 증가
                if links > 40:                               # 링크 고리
                    msg = f"ELOOP: {_UV_DESC['ELOOP']}, lstat '{nxt}'"  # ELOOP 오류 글
                    raise NodeFsError(msg, "ELOOP")         # ELOOP 오류 올림
                target = os.readlink(nxt)                    # 가리키는 곳
                p = path_resolve(current, target, *parts[idx + 1:])   # 남은 조각을 붙여 다시
                restarted = True                            # 처음부터 다시 시작 표시
                break                                       # 조각 순회 중단
            current = nxt                                    # 확인 완료
        if not restarted:                                    # 끝까지 확인
            return current                                  # 실경로 반환


def exists_sync(path: str) -> bool:                         # Node existsSync 이식
    """Node fs.existsSync — 오류 없이 참/거짓(깨진 링크는 거짓)."""
    try:                                                    # stat 시도
        os.stat(path)                                        # 따라가서 확인
        return True                                         # 존재함
    except (OSError, ValueError):                            # 없음·잘못된 경로(널 글자 등)
        return False                                        # 존재하지 않음


def read_file_utf8(path: str) -> str:                       # Node readFileSync(utf-8) 이식
    """Node fs.readFileSync(path, "utf-8") — BOM 유지, 잘못된 바이트는 U+FFFD, 줄바꿈 변환 없음."""
    try:                                                    # 읽기 시도
        with open(path, "rb") as f:                          # 바이트로 읽기(줄바꿈 변환 방지)
            data = f.read()                                 # 전부 읽음
    except IsADirectoryError as e:                          # 폴더를 읽으려 한 경우
        raise NodeFsError(node_fs_error_message(e, "read"), "EISDIR") from e  # EISDIR 오류로 변환
    except PermissionError as e:                             # Windows는 디렉터리 열기도 여기로 옴
        if os.path.isdir(path):                             # 폴더였다면
            raise NodeFsError(node_fs_error_message(IsADirectoryError(_errno.EISDIR, "dir"), "read"), "EISDIR") from e  # EISDIR 오류로 변환
        raise NodeFsError(node_fs_error_message(e, "open", path), "EACCES") from e  # 진짜 권한 오류
    except OSError as e:                                     # 그 밖 → open 오류 글
        msg = node_fs_error_message(e, "open", path)        # Node 형식 오류 글
        raise NodeFsError(msg, msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림
    return data.decode("utf-8", errors="replace")            # Node utf-8 해석(BOM 그대로)


def write_file_utf8(path: str, content: str) -> None:       # Node writeFileSync(utf-8) 이식
    """Node fs.writeFileSync(path, content, "utf-8") — 줄바꿈 변환 없이 그대로 씀."""
    try:                                                    # 쓰기 시도
        with open(path, "wb") as f:                          # 바이트로 쓰기(Windows \n→\r\n 변환 방지)
            f.write(content.encode("utf-8", errors="surrogatepass"))   # 외톨이 서로게이트도 Node처럼 기록 시도
    except OSError as e:                                     # Node 글로
        code_msg = node_fs_error_message(IsADirectoryError(_errno.EISDIR, "dir") if os.path.isdir(path) else e, "open", path)  # 폴더였다면 EISDIR 로 보정한 오류 글
        raise NodeFsError(code_msg, code_msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림


def mkdir_recursive(path: str) -> None:                     # Node mkdirSync(recursive) 이식
    """Node fs.mkdirSync(path, {recursive: true})."""
    try:                                                    # 생성 시도
        os.makedirs(path, exist_ok=True)                     # 상위 폴더까지 생성
    except OSError as e:                                     # Node 글로
        msg = node_fs_error_message(e, "mkdir", path)       # Node 형식 오류 글
        raise NodeFsError(msg, msg.split(":", 1)[0]) from e  # Node 오류로 바꿔 올림


# ───────────────────────── JS 글 도우미 ─────────────────────────
JS_WS_CHARS = ("\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"  # JS 공백 문자 모음
               "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")   # JS 공백(\s·trim 대상) 전부
JS_WS_CLASS = "[" + JS_WS_CHARS + "]"                        # 정규식 글자 묶음(Python \s 는 \x1c~\x1f·\x85 도 포함해 JS와 다름)


def js_trim(s: str) -> str:                                 # JS trim 이식
    """JS String.prototype.trim — JS 공백만 양끝에서 제거(Python strip 과 대상 글자가 다름)."""
    return s.strip(JS_WS_CHARS)                             # JS 공백만 양끝에서 제거


def js_is_ws(c: str) -> bool:                               # 한 글자가 JS 공백인지
    """JS /\\s/.test(c) — 한 글자가 JS 공백인가."""
    return c in JS_WS_CHARS                                 # 공백 목록에 있는지


def to_locale_string(n: int) -> str:                        # JS toLocaleString 흉내
    """JS Number.toLocaleString()(en-US·ko-KR 공통) — 천 단위 쉼표."""
    return f"{n:,}"                                         # 천 단위 쉼표를 넣은 글


# ───────────────────────── cn formatters.ts ─────────────────────────
def format_tool_argument(value: Any) -> str:                # 도구 인자를 화면용 글로 변환(cn formatToolArgument)
    """formatToolArgument — 절대경로는 cwd 기준 상대경로로, 구분자는 '/' 로. None 은 빈 글."""
    if value is None:                                       # None 이면
        return ""                                           # 빈 글
    if isinstance(value, str) and path_is_absolute(value):   # 절대경로
        workspace_root = os.getcwd()                         # 작업 폴더
        relative_path = path_relative(workspace_root, value)   # 상대경로
        return (relative_path or value).replace("\\", "/")   # 같으면 원래 값, 구분자 통일
    if isinstance(value, bool):                             # 불리언이면
        return "true" if value else "false"                 # JS 처럼 소문자 true/false
    return str(value)                                        # 그 밖은 글로
