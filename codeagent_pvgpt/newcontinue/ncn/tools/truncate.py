# ncn/tools/truncate.py — 도구 출력 자르기. cn extensions/cli/src/util/truncateOutput.ts 를 그대로 옮긴 것
# 글자 수는 JS string.length 와 같게 UTF-16 코드 단위로 센다(이모지 등 BMP 밖 글자는 2로 셈)
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # parseInt 흉내
from dataclasses import dataclass                            # 결과 형식

from ncn.tools.formatters import JS_WS_CLASS                 # JS 공백 글자 묶음

TRUNCATION_LINE_SNAP_THRESHOLD = 1000                        # 자를 때 줄 경계에 맞추는 최대 거리(글자)


def js_len(s: str) -> int:                                  # JS length 와 같은 길이 계산
    """JS string.length — UTF-16 코드 단위 수(BMP 밖 글자는 2)."""
    return len(s) + sum(1 for c in s if ord(c) > 0xFFFF)     # 서로게이트 쌍만큼 더함


def _js_slice_utf16(s: str, start: int, end: int | None = None) -> str:  # JS slice 를 UTF-16 위치로 흉내
    """JS string.slice(start, end) — UTF-16 위치 기준(서로게이트 쌍 가운데서 자르면 외톨이 서로게이트가 남음)."""
    if js_len(s) == len(s):                                  # BMP만 있으면 그냥 자름(빠른 길)
        return s[start:end]                                 # BMP 만 있으면 파이썬 슬라이스 그대로
    u = s.encode("utf-16-le", errors="surrogatepass")        # UTF-16 바이트로
    n = len(u) // 2                                          # 코드 단위 수
    a = max(0, n + start) if start < 0 else min(start, n)    # JS slice 음수 위치 규칙
    b = n if end is None else (max(0, n + end) if end < 0 else min(end, n))  # 끝 위치도 같은 음수 규칙으로 보정
    if b <= a:                                              # 빈 구간이면
        return ""                                           # 빈 글
    return u[a * 2:b * 2].decode("utf-16-le", errors="surrogatepass")   # 다시 글로


def parse_env_number(env_var: str | None, default_value: int) -> int:  # 환경변수를 양의 정수로 읽는다
    """parseEnvNumber — 환경변수를 양의 정수로. 없음·빈 글·숫자 아님·0 이하는 기본값.
    JS parseInt(x, 10) 처럼 앞 공백 무시, 앞쪽 숫자만 읽음("12abc" → 12)."""
    if not env_var:                                          # 없음 또는 빈 글
        return default_value                                # 기본값 사용
    m = re.match("^" + JS_WS_CLASS + "*([+-]?[0-9]+)", env_var)   # parseInt: 앞 JS 공백 + 부호 + ASCII 숫자
    if not m:                                               # 숫자가 없으면 NaN 과 같다
        return default_value                                # 기본값 사용
    parsed = int(m.group(1))                                 # 정수
    if parsed <= 0:                                          # 0 이하
        return default_value                                # 기본값 사용
    return parsed                                           # 읽은 정수 반환


@dataclass                                                  # dataclass 선언
class TruncationResult:                                     # 자른 결과 묶음
    """TruncationResult — 자른 글과 잘렸는지."""
    output: str                                              # 결과 글
    was_truncated: bool                                      # 잘렸나


@dataclass                                                  # dataclass 선언
class TruncationLimits:                                     # 자르기 한도 묶음
    """TruncationLimits — 글자·줄 한도."""
    max_chars: int                                           # 최대 글자
    max_lines: int                                           # 최대 줄


def truncate_output_from_start(output: str, limits: TruncationLimits | dict) -> TruncationResult:  # 앞을 버리고 끝을 남기는 자르기(명령 출력용)
    """truncateOutputFromStart — 앞부분을 버리고 끝부분을 남긴다(명령 출력용). 잘리면 "(previous … truncated)" 머리말."""
    if not output:                                           # 빈 글
        return TruncationResult(output, False)              # 자를 것 없음
    if isinstance(limits, dict):                             # {"maxChars":…, "maxLines":…} 로도 받음(cn 모양)
        limits = TruncationLimits(limits["maxChars"], limits["maxLines"])  # dict 로 받은 한도를 객체로 바꾼다
    max_chars, max_lines = limits.max_chars, limits.max_lines  # 한도를 변수로 꺼냄
    lines = output.split("\n")                               # 줄로 나눔
    if len(lines) > max_lines:                               # 줄 수 먼저
        lines_truncated = len(lines) - max_lines             # 버릴 줄 수
        preserved = lines[-max_lines:] if max_lines > 0 else lines[:]   # JS slice(-0) == 전체
        content_after = "\n".join(preserved)                 # 남은 글
        if js_len(content_after) > max_chars:                # 그래도 글자 수 초과
            return _truncate_characters_from_start(content_after, lines_truncated, max_chars)  # 글자 수까지 넘으면 글자 기준으로 한 번 더 자른다
        return TruncationResult(f"(previous {lines_truncated} lines truncated)\n\n{content_after}", True)  # 줄만 잘랐다는 머리말을 붙여 반환
    if js_len(output) > max_chars:                           # 글자 수만 초과
        return _truncate_characters_from_start(output, 0, max_chars)  # 글자 기준으로만 자른다
    return TruncationResult(output, False)                   # 그대로


def truncate_output_from_end(output: str, max_chars: int, context: str | None = None) -> TruncationResult:  # 앞을 남기고 끝을 버리는 자르기(파일 내용·diff용)
    """truncateOutputFromEnd — 앞부분을 남기고 끝을 버린다(파일 내용·diff용). 꼬리말 "(N characters of X truncated)"."""
    if not output or js_len(output) <= max_chars:            # 한도 안
        return TruncationResult(output, False)              # 자를 것 없음
    truncated = _js_slice_utf16(output, 0, max_chars)        # 앞 max_chars
    last_newline = truncated.rfind("\n")                     # 마지막 줄바꿈(코드 단위 위치로 환산)
    if last_newline != -1:                                  # 줄바꿈이 있으면
        last_newline = js_len(truncated[:last_newline])     # 위치를 UTF-16 단위로 환산
    should_snap = last_newline != -1 and js_len(truncated) - last_newline < TRUNCATION_LINE_SNAP_THRESHOLD   # 줄 경계 가까우면
    clean = _js_slice_utf16(truncated, 0, last_newline) if should_snap else truncated   # 줄 경계에서 끊기
    removed = js_len(output) - js_len(clean)                 # 실제로 버린 글자 수
    context_str = f" of {context}" if context else ""        # 꼬리말 맥락
    return TruncationResult(clean + f"\n\n({removed} characters{context_str} truncated)", True)  # 꼬리말을 붙여 반환


def truncate_lines_by_count(output: str, max_lines: int, context: str | None = None) -> TruncationResult:  # 앞 max_lines 줄만 남기는 자르기
    """truncateLinesByCount — 앞 max_lines 줄만 남김."""
    if not output:
        return TruncationResult(output, False)              # 자를 것 없음
    lines = output.split("\n")                               # 줄로
    if len(lines) <= max_lines:                              # 한도 안
        return TruncationResult(output, False)              # 자를 것 없음
    preserved = lines[:max_lines]                            # 앞 줄들
    lines_truncated = len(lines) - max_lines                 # 버린 줄 수
    context_str = f" of {context}" if context else ""       # 꼬리말 맥락 글
    return TruncationResult("\n".join(preserved) + f"\n\n({lines_truncated} lines{context_str} truncated)", True)  # 남긴 줄 + 꼬리말


def truncate_by_lines_and_chars(output: str, max_lines: int, max_chars: int, context: str | None = None) -> TruncationResult:  # 줄 수로 자른 뒤 글자 수로 한 번 더 자른다
    """truncateByLinesAndChars — 줄 수로 자른 뒤 글자 수로 한 번 더(앞부분 유지)."""
    if not output:
        return TruncationResult(output, False)              # 자를 것 없음
    line_result = truncate_lines_by_count(output, max_lines, context)   # 줄 먼저
    if js_len(line_result.output) > max_chars:               # 글자 수 초과
        char_result = truncate_output_from_end(line_result.output, max_chars, context)  # 글자 기준으로 끝을 한 번 더 자른다
        return TruncationResult(char_result.output, True)   # 잘렸다고 표시해 반환
    return line_result                                      # 글자 수 안이면 줄 자르기 결과 그대로


def _truncate_characters_from_start(text: str, lines_truncated: int, max_characters: int) -> TruncationResult:  # 글자 기준으로 앞을 버리는 도우미
    """truncateCharactersFromStart — 앞 글자를 버리고 끝 max_characters 만 남김(가까운 줄 경계에서 시작)."""
    truncation_point = js_len(text) - max_characters         # 버릴 위치
    to_keep = _js_slice_utf16(text, truncation_point)        # 남길 글
    first_newline = to_keep.find("\n")                       # 첫 줄바꿈
    if first_newline != -1:                                 # 줄바꿈이 있으면
        first_newline = js_len(to_keep[:first_newline])      # 코드 단위 위치로
    should_snap = first_newline != -1 and first_newline < TRUNCATION_LINE_SNAP_THRESHOLD   # 가까우면 줄 경계에서
    clean = _js_slice_utf16(to_keep, first_newline + 1) if should_snap else to_keep  # 줄 경계에 맞추면 그 다음부터, 아니면 그대로
    chars_removed = js_len(text) - js_len(clean)             # 버린 글자 수
    if lines_truncated > 0:                                  # 줄도 버렸으면
        prefix = f"(previous output truncated: {lines_truncated} lines and {chars_removed} characters removed)\n\n"
    else:                                                   # 줄은 안 버렸으면
        prefix = f"(previous {chars_removed} characters truncated)\n\n"  # 글자만 버렸다는 머리말
    return TruncationResult(prefix + clean, True)           # 머리말 + 남은 글 반환
