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


def node_platform() -> str:
    """Node process.platform 과 같은 값 — win32 / darwin / linux."""
    if PLATFORM_OVERRIDE:                                    # 시험에서 강제로 바꾼 값
        return PLATFORM_OVERRIDE
    if sys.platform.startswith("win"):                       # Windows(32·64비트 모두 Node는 win32)
        return "win32"
    if sys.platform == "darwin":                             # macOS
        return "darwin"
    return "linux"                                           # 그 밖(리눅스)


def _is_win() -> bool:
    """Node path 가 win32 규칙을 쓰는가(path = path.win32)."""
    return node_platform() == "win32"


# ───────────────────────── Node path.posix 이식(node lib/path.js) ─────────────────────────
def _normalize_string(path: str, allow_above_root: bool, separator: str, is_sep) -> str:
    """Node normalizeString — '.'·'..'·중복 구분자 정리(루트 없는 본문만)."""
    res = ""                                                 # 결과
    last_segment_length = 0                                  # 마지막 조각 길이
    last_slash = -1                                          # 마지막 구분자 위치
    dots = 0                                                 # 지금 조각의 점 개수(-1 = 점 아닌 글자 있음)
    code = ""                                                # 지금 글자
    i = 0                                                    # 위치
    while i <= len(path):                                    # 끝 다음 칸까지(가상의 구분자 처리)
        if i < len(path):                                    # 실제 글자
            code = path[i]
        elif is_sep(code):                                   # 마지막 글자가 구분자였으면 끝
            break
        else:                                                # 끝에 가상의 '/' 를 둔다
            code = "/"
        if is_sep(code):                                     # 조각 하나가 끝남
            if last_slash == i - 1 or dots == 1:             # 빈 조각 또는 '.' → 무시
                pass
            elif dots == 2:                                  # '..'
                if (len(res) < 2 or last_segment_length != 2
                        or res[-1] != "." or res[-2] != "."):   # 앞 조각이 '..' 가 아니면 한 단계 올라감
                    if len(res) > 2:                         # 앞 조각이 있으면 지움
                        last_slash_index = res.rfind(separator)   # 마지막 구분자
                        if last_slash_index == -1:           # 조각 하나뿐
                            res = ""
                            last_segment_length = 0
                        else:                                # 마지막 조각만 지움
                            res = res[:last_slash_index]
                            last_segment_length = len(res) - 1 - res.rfind(separator)
                        last_slash = i
                        dots = 0
                        i += 1
                        continue
                    elif len(res) != 0:                      # 짧은 조각 하나 → 비움
                        res = ""
                        last_segment_length = 0
                        last_slash = i
                        dots = 0
                        i += 1
                        continue
                if allow_above_root:                         # 루트 위로 올라갈 수 있으면 '..' 남김
                    res += f"{separator}.." if len(res) > 0 else ".."
                    last_segment_length = 2
            else:                                            # 보통 조각
                if len(res) > 0:
                    res += f"{separator}{path[last_slash + 1:i]}"
                else:
                    res = path[last_slash + 1:i]
                last_segment_length = i - last_slash - 1
            last_slash = i
            dots = 0
        elif code == "." and dots != -1:                     # 점 세기
            dots += 1
        else:                                                # 점 아닌 글자
            dots = -1
        i += 1
    return res


def _posix_is_sep(c: str) -> bool:
    """posix 구분자는 '/' 하나."""
    return c == "/"


def _posix_normalize(path: str) -> str:
    """Node path.posix.normalize — 끝 슬래시 유지, 빈 글은 '.'."""
    if len(path) == 0:                                       # 빈 경로
        return "."
    is_absolute = path[0] == "/"                             # 절대경로?
    trailing = path[-1] == "/"                               # 끝 슬래시?
    path = _normalize_string(path, not is_absolute, "/", _posix_is_sep)   # 본문 정리
    if len(path) == 0:                                       # 다 지워짐
        if is_absolute:
            return "/"
        return "./" if trailing else "."
    if trailing:                                             # 끝 슬래시 되살림
        path += "/"
    return f"/{path}" if is_absolute else path


def _posix_resolve(*args: str) -> str:
    """Node path.posix.resolve — 오른쪽부터 절대경로가 나올 때까지 이어 붙이고 정리. 끝 슬래시 없음."""
    resolved_path = ""                                       # 결과 본문
    resolved_absolute = False                                # 절대경로가 됐나
    i = len(args) - 1                                        # 오른쪽부터
    while i >= -1 and not resolved_absolute:                 # -1 = 현재 폴더
        path = args[i] if i >= 0 else os.getcwd()            # 인자 또는 cwd
        i -= 1
        if len(path) == 0:                                   # 빈 인자는 건너뜀
            continue
        resolved_path = f"{path}/{resolved_path}"            # 앞에 붙임
        resolved_absolute = path[0] == "/"                   # 절대경로면 멈춤
    resolved_path = _normalize_string(resolved_path, not resolved_absolute, "/", _posix_is_sep)   # 정리
    if resolved_absolute:
        return f"/{resolved_path}"
    return resolved_path if len(resolved_path) > 0 else "."


def _posix_relative(frm: str, to: str) -> str:
    """Node path.posix.relative — 같으면 ''."""
    if frm == to:                                            # 글자 그대로 같음
        return ""
    frm = _posix_resolve(frm)                                # 둘 다 절대경로로
    to = _posix_resolve(to)
    if frm == to:                                            # 정리 후 같음
        return ""
    f_parts = [p for p in frm.split("/") if p]               # 조각들
    t_parts = [p for p in to.split("/") if p]
    n = 0                                                    # 공통 앞부분 길이
    while n < len(f_parts) and n < len(t_parts) and f_parts[n] == t_parts[n]:
        n += 1
    ups = [".."] * (len(f_parts) - n)                        # 올라갈 단계
    return "/".join(ups + t_parts[n:])                       # 올라간 뒤 내려갈 경로


def _posix_dirname(path: str) -> str:
    """Node path.posix.dirname — 끝 슬래시 무시, 루트만 남으면 '/'."""
    if len(path) == 0:
        return "."
    has_root = path[0] == "/"                                # 루트로 시작?
    end = -1                                                 # 잘라 낼 위치
    matched_slash = True                                     # 끝의 슬래시들을 지나는 중
    for i in range(len(path) - 1, 0, -1):                    # 뒤에서 앞으로(0번 제외)
        if path[i] == "/":
            if not matched_slash:                            # 글자를 지난 뒤 만난 첫 슬래시
                end = i
                break
        else:
            matched_slash = False
    if end == -1:                                            # 슬래시 없음
        return "/" if has_root else "."
    if has_root and end == 1:                                # "//x" 형태
        return "//"
    return path[:end]


def _posix_basename(path: str) -> str:
    """Node path.posix.basename(확장자 인자 없음) — 끝 슬래시 무시."""
    start = 0                                                # 시작
    end = -1                                                 # 끝(없으면 -1)
    matched_slash = True                                     # 끝 슬래시 지나는 중
    for i in range(len(path) - 1, -1, -1):                   # 뒤에서 앞으로
        if path[i] == "/":
            if not matched_slash:                            # 이름 앞의 슬래시
                start = i + 1
                break
        elif end == -1:                                      # 이름의 마지막 글자
            matched_slash = False
            end = i + 1
    if end == -1:
        return ""
    return path[start:end]


# ───────────────────────── Node path.win32 이식(node v24 lib/path.js) ─────────────────────────
_WINDOWS_RESERVED_NAMES = [                                  # Windows 예약 장치 이름(Node 목록 그대로)
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    "COM\xb9", "COM\xb2", "COM\xb3",
    "LPT\xb9", "LPT\xb2", "LPT\xb3",
]


def _win_is_sep(c: str) -> bool:
    """win32 구분자는 '/' 와 '\\'."""
    return c in ("/", "\\")


def _win_is_device_root(c: str) -> bool:
    """드라이브 글자(A-Z, a-z)."""
    return ("A" <= c <= "Z") or ("a" <= c <= "z")


def _win_is_reserved_name(path: str, colon_index: int) -> bool:
    """isWindowsReservedName — 콜론 앞이 예약 장치 이름인가(대문자 비교). colon_index=-1 이면 JS slice(0,-1) 와 같게."""
    device_part = (path[:colon_index] if colon_index >= 0 else path[:-1]).upper()   # JS slice(0, -1) == 끝 한 글자 뺌
    return device_part in _WINDOWS_RESERVED_NAMES


def _char(s: str, i: int) -> str:
    """JS charCodeAt 비교용 — 범위 밖이면 빈 글(NaN 처럼 아무것과도 같지 않음)."""
    return s[i] if 0 <= i < len(s) else ""


def _win_is_absolute(path: str) -> bool:
    """Node path.win32.isAbsolute — 구분자로 시작하거나 'X:\\'."""
    if len(path) == 0:
        return False
    c = path[0]
    return _win_is_sep(c) or (len(path) > 2 and _win_is_device_root(c) and path[1] == ":" and _win_is_sep(path[2]))


def _win_match_unc(path: str) -> tuple[int, int, int, str] | None:
    """'\\\\서버\\공유' 앞부분 찾기 — (서버 끝 j1, 공유 시작 last, 공유 끝 j, 서버 이름) 또는 None."""
    n = len(path)
    j = 2
    last = j
    while j < n and not _win_is_sep(path[j]):                # 서버 이름
        j += 1
    if not (j < n and j != last):
        return None
    first_part = path[last:j]
    last = j
    while j < n and _win_is_sep(path[j]):                    # 구분자들
        j += 1
    if not (j < n and j != last):
        return None
    last = j
    while j < n and not _win_is_sep(path[j]):                # 공유 이름
        j += 1
    return (0, last, j, first_part)


def _win_normalize(path: str) -> str:
    """Node path.win32.normalize — 루트(드라이브·UNC·장치) 처리 + 본문 정리 + CVE-2024-36139 보호."""
    n = len(path)
    if n == 0:
        return "."
    root_end = 0                                             # 루트 끝 위치
    device: str | None = None                                # 장치(드라이브·UNC) 부분
    is_absolute = False                                      # 절대경로?
    code = path[0]
    if n == 1:                                               # 한 글자
        return "\\" if code == "/" else path
    if _win_is_sep(code):                                    # 구분자로 시작 — UNC 가능
        is_absolute = True
        if _win_is_sep(path[1]):                             # '\\' 로 시작
            m = _win_match_unc(path)
            if m is not None:
                _, last, j, first_part = m
                if j == n or j != last:
                    if first_part in (".", "?"):             # 장치 루트(\\.\PHYSICALDRIVE0)
                        device = f"\\\\{first_part}"
                        root_end = 4
                        colon_index = path.find(":")
                        possible_device = path[4:colon_index + 1]   # JS slice(4, colonIndex+1)
                        if _win_is_reserved_name(possible_device, len(possible_device) - 1):
                            device = f"\\\\?\\{possible_device}"
                            root_end = 4 + len(possible_device)
                    elif j == n:                             # UNC 루트뿐
                        return f"\\\\{first_part}\\{path[last:]}\\"
                    else:                                    # UNC 루트 + 나머지
                        device = f"\\\\{first_part}\\{path[last:j]}"
                        root_end = j
        else:
            root_end = 1
    else:
        colon_index = path.find(":")
        if colon_index > 0:
            if _win_is_device_root(code) and colon_index == 1:   # 'C:'
                device = path[0:2]
                root_end = 2
                if n > 2 and _win_is_sep(path[2]):
                    is_absolute = True
                    root_end = 3
            elif _win_is_reserved_name(path, colon_index):   # 'CON:' 등
                device = path[0:colon_index + 1]
                root_end = colon_index + 1
    tail = _normalize_string(path[root_end:], not is_absolute, "\\", _win_is_sep) if root_end < n else ""   # 본문 정리
    if len(tail) == 0 and not is_absolute:
        tail = "."
    if len(tail) > 0 and _win_is_sep(path[n - 1]):           # 끝 구분자 유지
        tail += "\\"
    if not is_absolute and device is None and ":" in path:   # 상대경로가 드라이브처럼 보이지 않게(CVE-2024-36139)
        if len(tail) >= 2 and _win_is_device_root(tail[0]) and tail[1] == ":":
            return f".\\{tail}"
        index = path.find(":")
        while index != -1:
            if index == n - 1 or _win_is_sep(_char(path, index + 1)):
                return f".\\{tail}"
            index = path.find(":", index + 1)
    colon_index = path.find(":")
    if _win_is_reserved_name(path, colon_index):             # 예약 이름
        return f".\\{device or ''}{tail}"
    if device is None:
        return f"\\{tail}" if is_absolute else tail
    return f"{device}\\{tail}" if is_absolute else f"{device}{tail}"


def _win_resolve(*args: str) -> str:
    """Node path.win32.resolve — 드라이브별 현재 폴더(환경변수 '=C:')까지 Node 그대로."""
    resolved_device = ""                                     # 결정된 장치
    resolved_tail = ""                                       # 결정된 본문
    resolved_absolute = False                                # 절대경로가 됐나
    i = len(args) - 1
    while i >= -1:                                           # 오른쪽부터(-1 = cwd)
        if i >= 0:
            path = args[i]
            i -= 1
            if len(path) == 0:                               # 빈 인자 건너뜀
                continue
        elif len(resolved_device) == 0:                      # 장치가 아직 없으면 cwd
            i -= 1
            path = os.getcwd()
            if len(args) == 0 or (len(args) == 1 and args[0] in ("", ".") and _win_is_sep(_char(path, 0))):   # 빠른 길
                if not sys.platform.startswith("win"):           # Node: 실제 Windows 가 아니면 '/' → '\'
                    path = path.replace("/", "\\")
                return path
        else:                                                # 드라이브별 현재 폴더
            i -= 1
            path = os.environ.get(f"={resolved_device}") or os.getcwd()
            if path is None or (path[0:2].lower() != resolved_device.lower() and _char(path, 2) == "\\"):
                path = f"{resolved_device}\\"
        n = len(path)
        root_end = 0
        device = ""
        is_absolute = False
        code = _char(path, 0)
        if n == 1:                                           # 한 글자
            if _win_is_sep(code):
                root_end = 1
                is_absolute = True
        elif _win_is_sep(code):                              # UNC 가능
            is_absolute = True
            if _win_is_sep(path[1]):
                m = _win_match_unc(path)
                if m is not None:
                    _, last, j, first_part = m
                    if j == n or j != last:
                        if first_part not in (".", "?"):     # UNC 루트
                            device = f"\\\\{first_part}\\{path[last:j]}"
                            root_end = j
                        else:                                # 장치 루트
                            device = f"\\\\{first_part}"
                            root_end = 4
            else:
                root_end = 1
        elif _win_is_device_root(code) and _char(path, 1) == ":":   # 드라이브
            device = path[0:2]
            root_end = 2
            if n > 2 and _win_is_sep(path[2]):
                is_absolute = True
                root_end = 3
        if len(device) > 0:
            if len(resolved_device) > 0:
                if device.lower() != resolved_device.lower():   # 다른 드라이브는 무시
                    continue
            else:
                resolved_device = device
        if resolved_absolute:
            if len(resolved_device) > 0:
                break
        else:
            resolved_tail = f"{path[root_end:]}\\{resolved_tail}"
            resolved_absolute = is_absolute
            if is_absolute and len(resolved_device) > 0:
                break
    resolved_tail = _normalize_string(resolved_tail, not resolved_absolute, "\\", _win_is_sep)   # 정리
    if resolved_absolute:
        return f"{resolved_device}\\{resolved_tail}"
    return f"{resolved_device}{resolved_tail}" or "."


def _win_relative(frm: str, to: str) -> str:
    """Node path.win32.relative — 대소문자 무시 비교, 드라이브가 다르면 to 그대로."""
    if frm == to:
        return ""
    from_orig = _win_resolve(frm)
    to_orig = _win_resolve(to)
    if from_orig == to_orig:
        return ""
    f = from_orig.lower()
    t = to_orig.lower()
    if f == t:
        return ""
    if len(from_orig) != len(f) or len(to_orig) != len(t):   # 소문자화로 길이가 바뀌는 글자가 있을 때
        fs = from_orig.split("\\")
        ts = to_orig.split("\\")
        if fs[-1] == "":
            fs.pop()
        if ts[-1] == "":
            ts.pop()
        length = min(len(fs), len(ts))
        k = 0
        while k < length and fs[k].lower() == ts[k].lower():
            k += 1
        if k == 0:
            return to_orig
        if k == length:
            if len(ts) > length:
                return "\\".join(ts[k:])
            if len(fs) > length:
                return "..\\" * (len(fs) - 1 - k) + ".."
            return ""
        return "..\\" * (len(fs) - k) + "\\".join(ts[k:])
    from_start = 0                                           # 앞 '\' 건너뜀
    while from_start < len(f) and f[from_start] == "\\":
        from_start += 1
    from_end = len(f)                                        # 끝 '\' 건너뜀(UNC)
    while from_end - 1 > from_start and f[from_end - 1] == "\\":
        from_end -= 1
    from_len = from_end - from_start
    to_start = 0
    while to_start < len(t) and t[to_start] == "\\":
        to_start += 1
    to_end = len(t)
    while to_end - 1 > to_start and t[to_end - 1] == "\\":
        to_end -= 1
    to_len = to_end - to_start
    length = min(from_len, to_len)                           # 공통 앞부분
    last_common_sep = -1
    k = 0
    while k < length:
        fc = f[from_start + k]
        if fc != t[to_start + k]:
            break
        if fc == "\\":
            last_common_sep = k
        k += 1
    if k != length:
        if last_common_sep == -1:                            # 첫 구분자 전에 다름
            return to_orig
    else:
        if to_len > length:
            if _char(t, to_start + k) == "\\":               # from 이 to 의 바로 위
                return to_orig[to_start + k + 1:]
            if k == 2:                                       # from 이 드라이브 루트
                return to_orig[to_start + k:]
        if from_len > length:
            if _char(f, from_start + k) == "\\":             # to 가 from 의 위
                last_common_sep = k
            elif k == 2:                                     # to 가 드라이브 루트
                last_common_sep = 3
        if last_common_sep == -1:
            last_common_sep = 0
    out = ""
    k = from_start + last_common_sep + 1
    while k <= from_end:                                     # 올라갈 단계
        if k == from_end or f[k] == "\\":
            out += ".." if len(out) == 0 else "\\.."
        k += 1
    to_start += last_common_sep
    if len(out) > 0:
        return f"{out}{to_orig[to_start:to_end]}"
    if _char(to_orig, to_start) == "\\":
        to_start += 1
    return to_orig[to_start:to_end]


def _win_dirname(path: str) -> str:
    """Node path.win32.dirname."""
    n = len(path)
    if n == 0:
        return "."
    root_end = -1
    offset = 0
    code = path[0]
    if n == 1:                                               # 한 글자
        return path if _win_is_sep(code) else "."
    if _win_is_sep(code):                                    # UNC 가능
        root_end = offset = 1
        if _win_is_sep(path[1]):
            m = _win_match_unc(path)
            if m is not None:
                _, last, j, _first = m
                if j == n:                                   # UNC 루트뿐
                    return path
                if j != last:                                # UNC 루트 + 나머지
                    root_end = offset = j + 1
    elif _win_is_device_root(code) and path[1] == ":":       # 드라이브
        root_end = 3 if n > 2 and _win_is_sep(path[2]) else 2
        offset = root_end
    end = -1
    matched_slash = True
    for i in range(n - 1, offset - 1, -1):                   # 뒤에서 앞으로
        if _win_is_sep(path[i]):
            if not matched_slash:
                end = i
                break
        else:
            matched_slash = False
    if end == -1:
        if root_end == -1:
            return "."
        end = root_end
    return path[:end]


def _win_basename(path: str) -> str:
    """Node path.win32.basename(확장자 인자 없음) — 'X:' 드라이브 접두 제외."""
    start = 0
    end = -1
    matched_slash = True
    if len(path) >= 2 and _win_is_device_root(path[0]) and path[1] == ":":   # 드라이브 접두
        start = 2
    for i in range(len(path) - 1, start - 1, -1):
        if _win_is_sep(path[i]):
            if not matched_slash:
                start = i + 1
                break
        elif end == -1:
            matched_slash = False
            end = i + 1
    if end == -1:
        return ""
    return path[start:end]


def _win_join(*args: str) -> str:
    """Node path.win32.join — '\\'로 이어 붙이고, 앞 '//' 정리, 예약 이름이 있으면 정리하지 않음."""
    joined = None
    first_part = ""
    for arg in args:                                         # 빈 조각 제외하고 이어 붙임
        if len(arg) > 0:
            if joined is None:
                joined = first_part = arg
            else:
                joined += f"\\{arg}"
    if joined is None:
        return "."
    needs_replace = True                                     # UNC 로 오해하지 않게 앞 구분자 정리
    slash_count = 0
    if _win_is_sep(first_part[0]):
        slash_count += 1
        if len(first_part) > 1 and _win_is_sep(first_part[1]):
            slash_count += 1
            if len(first_part) > 2:
                if _win_is_sep(first_part[2]):
                    slash_count += 1
                else:                                        # UNC 로 의도함
                    needs_replace = False
    if needs_replace:
        while slash_count < len(joined) and _win_is_sep(joined[slash_count]):
            slash_count += 1
        if slash_count >= 2:
            joined = f"\\{joined[slash_count:]}"
    parts = [p for p in re.split(r"\\+", joined) if p]       # '\' 로만 나눈 조각들
    if any((p.find(":") != -1 and _win_is_reserved_name(p, p.find(":"))) for p in parts):   # 예약 이름 → '/'만 '\'로
        return joined.replace("/", "\\")
    return _win_normalize(joined)


# ───────────────────────── 공개: 실행 OS에 따라 posix/win32 선택(Node 'path' 기본 동작) ─────────────────────────
def path_is_absolute(path: str) -> bool:
    """Node path.isAbsolute."""
    return _win_is_absolute(path) if _is_win() else path.startswith("/")


def path_normalize(path: str) -> str:
    """Node path.normalize."""
    return _win_normalize(path) if _is_win() else _posix_normalize(path)


def path_resolve(*args: str) -> str:
    """Node path.resolve(…) — 빈 인자면 cwd."""
    return _win_resolve(*args) if _is_win() else _posix_resolve(*args)


def path_relative(frm: str, to: str) -> str:
    """Node path.relative."""
    return _win_relative(frm, to) if _is_win() else _posix_relative(frm, to)


def path_join(*args: str) -> str:
    """Node path.join — 빈 조각 빼고 구분자로 이어 붙인 뒤 normalize(절대 조각이어도 갈아 끼우지 않음)."""
    if _is_win():                                            # win32 는 따로(앞 '//'·예약 이름 처리)
        return _win_join(*args)
    parts = [a for a in args if a]                           # 빈 조각 제외
    if not parts:
        return "."
    return _posix_normalize("/".join(parts))


def path_dirname(path: str) -> str:
    """Node path.dirname."""
    return _win_dirname(path) if _is_win() else _posix_dirname(path)


def path_basename(path: str) -> str:
    """Node path.basename."""
    return _win_basename(path) if _is_win() else _posix_basename(path)


# ───────────────────────── Node fs 오류 글 ─────────────────────────
_UV_DESC = {                                                 # libuv 오류 설명(Node 오류 글에 그대로 나옴)
    "ENOENT": "no such file or directory",
    "ENOTDIR": "not a directory",
    "EISDIR": "illegal operation on a directory",
    "EACCES": "permission denied",
    "EPERM": "operation not permitted",
    "EEXIST": "file already exists",
    "ELOOP": "too many symbolic links encountered",
    "ENAMETOOLONG": "name too long",
    "EBUSY": "resource busy or locked",
    "EMFILE": "too many open files",
    "ENOSPC": "no space left on device",
    "EROFS": "read-only file system",
    "EINVAL": "invalid argument",
}


def node_fs_error_message(err: OSError, syscall: str, path: str | None = None) -> str:
    """Python OSError → Node fs 오류 글. 예: "ENOENT: no such file or directory, realpath '/a/b'"."""
    code = _errno.errorcode.get(err.errno or 0, "EIO")      # 오류 코드 이름
    if isinstance(err, IsADirectoryError):                   # Windows에서 디렉터리 열기는 EACCES로 오기도 함 → 종류로 보정
        code = "EISDIR"
    elif isinstance(err, FileNotFoundError):
        code = "ENOENT"
    elif isinstance(err, NotADirectoryError):
        code = "ENOTDIR"
    elif isinstance(err, PermissionError) and code not in ("EACCES", "EPERM"):
        code = "EACCES"
    desc = _UV_DESC.get(code, (err.strerror or "unknown error").lower())   # 설명
    if path is None:                                         # 경로 없는 글(예: 디렉터리 read)
        return f"{code}: {desc}, {syscall}"
    return f"{code}: {desc}, {syscall} '{path}'"


class NodeFsError(Exception):
    """Node fs 함수가 던지는 오류 흉내 — str(e)가 Node 오류 글과 같다."""

    def __init__(self, message: str, code: str):
        super().__init__(message)                            # str(e) == Node 오류 글
        self.code = code                                     # "ENOENT" 등


def realpath_sync(path: str) -> str:
    """Node fs.realpathSync(JS 구현) — 루트부터 조각마다 lstat 하며 링크를 따라간다.
    실패하면 Node와 같은 글: 처음 실패한 조각의 lstat 오류(예: "ENOENT: no such file or directory, lstat '/a/b'")."""
    win = _is_win()                                          # 구분자 결정
    sep = "\\" if win else "/"                               # 구분자
    p = path_resolve(path)                                   # 먼저 절대경로로
    links = 0                                                # 따라간 링크 수(무한 반복 방지)
    while True:                                              # 링크를 만나면 처음부터 다시
        if win:                                              # Windows 루트: "C:\" 또는 "\\server\share\"
            drive, rest = ntpath.splitdrive(p)
            root = drive + "\\"
        else:                                                # posix 루트 "/"
            root, rest = "/", p
        parts = [x for x in rest.replace("\\", "/").split("/") if x]   # 조각들
        current = root                                       # 지금까지 확인한 실경로
        restarted = False                                    # 링크로 다시 시작하나
        for idx, part in enumerate(parts):                   # 조각마다
            nxt = current + part if current.endswith(sep) else current + sep + part   # 다음 경로
            try:
                st = os.lstat(nxt)                           # 링크 자체 정보
            except OSError as e:                             # Node: lstat 오류
                msg = node_fs_error_message(e, "lstat", nxt)
                raise NodeFsError(msg, msg.split(":", 1)[0]) from e
            if _stat.S_ISLNK(st.st_mode):                    # 심볼릭 링크
                links += 1
                if links > 40:                               # 링크 고리
                    msg = f"ELOOP: {_UV_DESC['ELOOP']}, lstat '{nxt}'"
                    raise NodeFsError(msg, "ELOOP")
                target = os.readlink(nxt)                    # 가리키는 곳
                p = path_resolve(current, target, *parts[idx + 1:])   # 남은 조각을 붙여 다시
                restarted = True
                break
            current = nxt                                    # 확인 완료
        if not restarted:                                    # 끝까지 확인
            return current


def exists_sync(path: str) -> bool:
    """Node fs.existsSync — 오류 없이 참/거짓(깨진 링크는 거짓)."""
    try:
        os.stat(path)                                        # 따라가서 확인
        return True
    except (OSError, ValueError):                            # 없음·잘못된 경로(널 글자 등)
        return False


def read_file_utf8(path: str) -> str:
    """Node fs.readFileSync(path, "utf-8") — BOM 유지, 잘못된 바이트는 U+FFFD, 줄바꿈 변환 없음."""
    try:
        with open(path, "rb") as f:                          # 바이트로 읽기(줄바꿈 변환 방지)
            data = f.read()
    except IsADirectoryError as e:                           # Node: "EISDIR: illegal operation on a directory, read"
        raise NodeFsError(node_fs_error_message(e, "read"), "EISDIR") from e
    except PermissionError as e:                             # Windows는 디렉터리 열기도 여기로 옴
        if os.path.isdir(path):
            raise NodeFsError(node_fs_error_message(IsADirectoryError(_errno.EISDIR, "dir"), "read"), "EISDIR") from e
        raise NodeFsError(node_fs_error_message(e, "open", path), "EACCES") from e
    except OSError as e:                                     # 그 밖 → open 오류 글
        msg = node_fs_error_message(e, "open", path)
        raise NodeFsError(msg, msg.split(":", 1)[0]) from e
    return data.decode("utf-8", errors="replace")            # Node utf-8 해석(BOM 그대로)


def write_file_utf8(path: str, content: str) -> None:
    """Node fs.writeFileSync(path, content, "utf-8") — 줄바꿈 변환 없이 그대로 씀."""
    try:
        with open(path, "wb") as f:                          # 바이트로 쓰기(Windows \n→\r\n 변환 방지)
            f.write(content.encode("utf-8", errors="surrogatepass"))   # 외톨이 서로게이트도 Node처럼 기록 시도
    except OSError as e:                                     # Node 글로
        code_msg = node_fs_error_message(IsADirectoryError(_errno.EISDIR, "dir") if os.path.isdir(path) else e, "open", path)
        raise NodeFsError(code_msg, code_msg.split(":", 1)[0]) from e


def mkdir_recursive(path: str) -> None:
    """Node fs.mkdirSync(path, {recursive: true})."""
    try:
        os.makedirs(path, exist_ok=True)                     # 상위 폴더까지 생성
    except OSError as e:                                     # Node 글로
        msg = node_fs_error_message(e, "mkdir", path)
        raise NodeFsError(msg, msg.split(":", 1)[0]) from e


# ───────────────────────── JS 글 도우미 ─────────────────────────
JS_WS_CHARS = ("\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
               "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")   # JS 공백(\s·trim 대상) 전부
JS_WS_CLASS = "[" + JS_WS_CHARS + "]"                        # 정규식 글자 묶음(Python \s 는 \x1c~\x1f·\x85 도 포함해 JS와 다름)


def js_trim(s: str) -> str:
    """JS String.prototype.trim — JS 공백만 양끝에서 제거(Python strip 과 대상 글자가 다름)."""
    return s.strip(JS_WS_CHARS)


def js_is_ws(c: str) -> bool:
    """JS /\\s/.test(c) — 한 글자가 JS 공백인가."""
    return c in JS_WS_CHARS


def to_locale_string(n: int) -> str:
    """JS Number.toLocaleString()(en-US·ko-KR 공통) — 천 단위 쉼표."""
    return f"{n:,}"


# ───────────────────────── cn formatters.ts ─────────────────────────
def format_tool_argument(value: Any) -> str:
    """formatToolArgument — 절대경로는 cwd 기준 상대경로로, 구분자는 '/' 로. None 은 빈 글."""
    if value is None:                                        # null/undefined
        return ""
    if isinstance(value, str) and path_is_absolute(value):   # 절대경로
        workspace_root = os.getcwd()                         # 작업 폴더
        relative_path = path_relative(workspace_root, value)   # 상대경로
        return (relative_path or value).replace("\\", "/")   # 같으면 원래 값, 구분자 통일
    if isinstance(value, bool):                              # JS String(true) == "true"
        return "true" if value else "false"
    return str(value)                                        # 그 밖은 글로
