# ncn/encoding.py — [ncn E3] 인코딩·줄바꿈 보존(계획 6장 E3, 부록 B "UTF-8 가정 → 인코딩·줄바꿈 보존"). cn 원본 없음(ncn 추가)
# cn은 Node readFileSync/writeFileSync(utf-8)만 쓴다 → CP949·UTF-16 파일은 깨진 글(U+FFFD)로 읽고 UTF-8 로 덮어써 원본을 망가뜨린다.
# ncn 판별 순서: UTF-8(BOM 있음/없음, 엄격 해석) → UTF-16 BOM → fallback(기본 CP949, 엄격 해석 성공 시) → 실패면 cn처럼 UTF-8(대체 문자).
# 읽기: 모델에게 주는 글은 cn과 같은 "디코딩된 글"(줄바꿈 그대로, BOM 은 cn(Node utf-8)처럼 글 앞의 U+FEFF 로 남김 — UTF-16 도 같은 규칙).
# 쓰기: 기존 파일이면 그 파일의 인코딩·BOM·줄바꿈(CRLF/LF, 섞여 있으면 다수 쪽)으로 되돌려 쓴다. 새 파일은 cn과 같게 UTF-8·받은 그대로.
# 순수 UTF-8·LF 파일은 읽기·쓰기 바이트가 cn과 완전히 같다(차등 시험 25/25 유지).
# 끄기: ~/.continue/ncn.yaml `encoding: {preserve: false}` 또는 NCN_CN_COMPAT=1 → cn과 같은 UTF-8 가정.
# IDE 위임(fsio.AcpFiles) 경로는 IDE가 인코딩을 관리하므로 인코딩 변환을 하지 않는다(줄바꿈 맞춤만 도구가 글 수준에서 함).
from __future__ import annotations                           # 타입 주석 지연 평가

import codecs                                                # 인코딩 이름 확인
import logging                                               # 경고(stdout 금지 — stdio 모드)
import os                                                    # 파일 존재 확인
from dataclasses import dataclass                            # 자료 구조
from typing import Any, Optional                             # 타입

from ncn.compat import cn_compat                             # cn 원래 동작 스위치

log = logging.getLogger("ncn.encoding")                      # 로거

BOM_UTF8 = b"\xef\xbb\xbf"                                   # UTF-8 BOM 바이트
BOM_UTF16_LE = b"\xff\xfe"                                   # UTF-16 LE BOM 바이트
BOM_UTF16_BE = b"\xfe\xff"                                   # UTF-16 BE BOM 바이트
DEFAULT_FALLBACK = "cp949"                                   # 기본 레거시 인코딩(Visual Studio 한국어 기본 저장 형식)


@dataclass(frozen=True)                                      # 바꿀 수 없는 값
class TextFormat:                                           # 파일 하나의 저장 형식
    """encoding: 'utf-8' | 'utf-16-le' | 'utf-16-be' | fallback 이름(예 'cp949'), bom: BOM 유무, replaced: UTF-8 대체 문자로 읽었나."""
    encoding: str = "utf-8"                                  # 파이썬 코덱 이름
    bom: bool = False                                        # BOM 이 있었나
    replaced: bool = False                                   # 어느 인코딩으로도 안 풀려 대체 문자로 읽었나(cn 과 같은 경우)


UTF8 = TextFormat()                                          # 새 파일·순수 UTF-8 파일 형식


class EncodingSaveError(Exception):                         # 원래 인코딩으로 표현 못 하는 글자를 쓰려 할 때
    """모델이 보게 되는 오류(Edit: 'Error: failed to edit …: <이 글>', Write: 'Error writing to file: <이 글>')."""


# ───────────────────────── ncn.yaml(encoding·backup 절) ─────────────────────────
def parse_file_settings(data: dict, out: Any) -> None:      # ncn.subagent.parse_ncn_settings 가 부른다
    """encoding: {preserve: bool, fallback: 인코딩 이름}, backup: {enabled: bool, max_per_session·keep_days: 양의 정수} 를 out(NcnSettings)에 넣는다."""
    enc = data.get("encoding")                               # encoding 절
    if isinstance(enc, dict):                                # 사전이면
        pv = enc.get("preserve")                             # 보존 여부
        if isinstance(pv, bool):                             # 참거짓이면
            out.encoding_preserve = pv                       # 반영
        elif pv is not None:                                 # 다른 값이면
            log.warning("ncn.yaml: encoding.preserve must be true/false — ignored")   # 경고
        fb = enc.get("fallback")                             # 레거시 인코딩 이름
        if isinstance(fb, str) and _codec_ok(fb):            # 아는 코덱이면
            out.encoding_fallback = fb                       # 반영
        elif fb is not None:                                 # 모르는 이름
            log.warning("ncn.yaml: encoding.fallback %r is not a known encoding — using cp949", fb)   # 경고
    elif enc is not None:                                    # 사전이 아니면
        log.warning("ncn.yaml: encoding must be a mapping — ignored")   # 경고
    bk = data.get("backup")                                  # backup 절([ncn E4])
    if isinstance(bk, dict):                                 # 사전이면
        en = bk.get("enabled")                               # 켜기 여부
        if isinstance(en, bool):                             # 참거짓이면
            out.backup_enabled = en                          # 반영
        elif en is not None:                                 # 다른 값
            log.warning("ncn.yaml: backup.enabled must be true/false — ignored")   # 경고
        mx = bk.get("max_per_session")                       # 세션당 최대 수
        if isinstance(mx, int) and not isinstance(mx, bool) and mx >= 1:   # 양의 정수면
            out.backup_max_per_session = mx                  # 반영
        elif mx is not None:                                 # 다른 값
            log.warning("ncn.yaml: backup.max_per_session must be a positive integer — ignored")   # 경고
        kd = bk.get("keep_days")                             # 세션 폴더 보관 일수
        if isinstance(kd, int) and not isinstance(kd, bool) and kd >= 1:   # 양의 정수면
            out.backup_keep_days = kd                        # 반영
        elif kd is not None:                                 # 다른 값
            log.warning("ncn.yaml: backup.keep_days must be a positive integer — ignored")   # 경고
    elif bk is not None:                                     # 사전이 아니면
        log.warning("ncn.yaml: backup must be a mapping — ignored")   # 경고


def _codec_ok(name: str) -> bool:                           # 파이썬이 아는 인코딩 이름인지
    """codecs.lookup 이 성공하면 참."""
    try:                                                    # 조회 시도
        codecs.lookup(name)                                  # 코덱 찾기
        return True                                         # 있음
    except LookupError:                                      # 없는 이름
        return False                                        # 없음


_settings_cache: dict = {}                                   # (파일 경로, 수정 시각) → 설정


def ncn_settings() -> Any:                                   # 지금 ncn.yaml 설정(파일이 바뀌면 다시 읽음)
    """~/.continue/ncn.yaml(CONTINUE_GLOBAL_DIR 반영)을 ncn.subagent.load_ncn_settings 로 읽는다 — 수정 시각이 같으면 캐시."""
    from ncn.config.paths import global_dir                  # 전역 폴더(지연 import — 순환 방지)
    from ncn.subagent import NCN_YAML, load_ncn_settings     # 같은 파일·같은 읽기 함수
    g = global_dir()                                         # 전역 폴더
    p = g / NCN_YAML                                         # ncn.yaml 경로
    try:                                                    # 수정 시각
        mt = p.stat().st_mtime_ns                            # 바뀌면 다시 읽기 위해
    except OSError:                                          # 없음
        mt = None                                           # 파일 없음 표시
    key = (str(p), mt)                                       # 캐시 키
    hit = _settings_cache.get(key)                           # 캐시 조회
    if hit is None:                                          # 처음이거나 바뀌었으면
        _settings_cache.clear()                              # 예전 값 버림
        hit = load_ncn_settings(g)                           # 읽기(없으면 기본값)
        _settings_cache[key] = hit                           # 저장
    return hit                                              # 설정


def enabled() -> bool:                                      # E3 를 쓸지
    """NCN_CN_COMPAT=1 이 아니고 ncn.yaml encoding.preserve 가 참(기본)이면 참."""
    if cn_compat():                                          # cn 원래 동작
        return False                                        # UTF-8 가정
    return bool(ncn_settings().encoding_preserve)            # 설정값(기본 참)


def fallback_encoding() -> str:                             # 레거시 인코딩 이름
    """ncn.yaml encoding.fallback(기본 cp949)."""
    return ncn_settings().encoding_fallback or DEFAULT_FALLBACK   # 설정값


# ───────────────────────── 판별·해석 ─────────────────────────
def decode_bytes(data: bytes, fallback: Optional[str] = None) -> tuple[str, TextFormat]:   # 바이트 → (글, 형식)
    """판별 순서: UTF-8(BOM 유무, 엄격) → UTF-16 BOM → fallback(엄격) → UTF-8 대체 문자(cn과 같은 글). BOM 은 글 앞 U+FEFF 로 남긴다(cn과 같음)."""
    fb = fallback or DEFAULT_FALLBACK                        # 레거시 인코딩
    if data.startswith(BOM_UTF8):                            # UTF-8 BOM
        try:                                                # 엄격 해석
            return data.decode("utf-8"), TextFormat("utf-8", bom=True)   # BOM 있는 UTF-8(글 앞 U+FEFF — Node 와 같은 글)
        except UnicodeDecodeError:                           # BOM 은 있는데 중간이 깨짐
            return data.decode("utf-8", errors="replace"), TextFormat("utf-8", bom=True, replaced=True)   # BOM 을 믿고 대체 문자(cn과 같은 글)
    try:                                                    # BOM 없는 UTF-8(ASCII 포함)
        return data.decode("utf-8"), UTF8                    # 순수 UTF-8 — cn과 같은 글
    except UnicodeDecodeError:                               # UTF-8 아님
        pass                                                # 다음 후보
    if data.startswith(BOM_UTF16_LE):                        # UTF-16 LE BOM
        return "\ufeff" + data[2:].decode("utf-16-le", errors="replace"), TextFormat("utf-16-le", bom=True)   # BOM 은 U+FEFF 로
    if data.startswith(BOM_UTF16_BE):                        # UTF-16 BE BOM
        return "\ufeff" + data[2:].decode("utf-16-be", errors="replace"), TextFormat("utf-16-be", bom=True)   # BOM 은 U+FEFF 로
    try:                                                    # 레거시(CP949 등) 엄격 해석
        return data.decode(fb), TextFormat(fb)               # 성공하면 그 인코딩
    except (UnicodeDecodeError, LookupError):                # 실패
        pass                                                # cn 처럼
    return data.decode("utf-8", errors="replace"), TextFormat("utf-8", replaced=True)   # cn 과 같은 대체 문자 글


def encode_text(text: str, fmt: TextFormat, path: str = "") -> bytes:   # 글 → 원래 형식 바이트
    """원래 인코딩·BOM 으로. 표현 못 하는 글자가 있으면 EncodingSaveError(모델이 알게)."""
    if fmt.bom and text.startswith("\ufeff"):                # 읽은 글의 U+FEFF 가 그대로 있으면(보통)
        text = text[1:]                                     # 떼고 아래에서 BOM 바이트로 붙임(모델이 빠뜨려도 BOM 유지)
    if fmt.encoding == "utf-8":                              # UTF-8
        body = text.encode("utf-8", errors="surrogatepass")  # cn(write_file_utf8)과 같은 방식
        return (BOM_UTF8 + body) if fmt.bom else body        # BOM 복원
    try:                                                    # 그 밖(UTF-16·CP949 등)은 엄격
        body = text.encode(fmt.encoding)                     # 원래 인코딩으로
    except UnicodeEncodeError as e:                          # 표현 못 하는 글자
        bad = sorted({ch for ch in text if not _encodable(ch, fmt.encoding)})[:5]   # 문제 글자(최대 5개)
        shown = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in bad)   # 글자와 코드 표시
        label = fmt.encoding.upper()                         # 표시용 이름(CP949 등)
        raise EncodingSaveError(                             # 모델에게 보이는 글(영어 — 다른 도구 오류 글과 같은 말)
            f"{path or 'file'} is encoded in {label} and the new content contains characters that {label} cannot represent: "   # 원인
            f"{shown}. Use only characters representable in {label}, or ask the user to convert the file to UTF-8.") from e   # 해결 방법
    if fmt.encoding == "utf-16-le" and fmt.bom:              # UTF-16 LE
        return BOM_UTF16_LE + body                           # BOM 복원
    if fmt.encoding == "utf-16-be" and fmt.bom:              # UTF-16 BE
        return BOM_UTF16_BE + body                           # BOM 복원
    return body                                             # CP949 등(BOM 없음)


def _encodable(ch: str, enc: str) -> bool:                  # 글자 하나가 그 인코딩으로 표현되나
    """한 글자 인코딩 시도."""
    try:                                                    # 시도
        ch.encode(enc)                                       # 인코딩
        return True                                         # 됨
    except UnicodeEncodeError:                               # 안 됨
        return False                                        # 안 됨


# ───────────────────────── 줄바꿈 ─────────────────────────
def to_lf(s: str) -> str:                                   # CRLF → LF
    """'\\r\\n' 을 '\\n' 으로(홀로 있는 '\\r' 은 그대로)."""
    return s.replace("\r\n", "\n")                           # 바꿈


def dominant_newline(text: str) -> Optional[str]:            # 파일의 줄바꿈(다수 쪽)
    """CRLF 가 하나도 없으면 None(= cn 처럼 손대지 않음). 있으면 CRLF·LF 중 많은 쪽(같으면 CRLF — Windows 파일로 봄)."""
    crlf = text.count("\r\n")                                # CRLF 수
    if crlf == 0:                                            # CRLF 없음
        return None                                         # 손대지 않음
    lf = text.count("\n") - crlf                             # 홀로 있는 LF 수
    return "\r\n" if crlf >= lf else "\n"                    # 다수 쪽


def conform_newlines(original: str, new: str) -> str:       # 새 글을 원래 파일의 줄바꿈으로
    """원래 글에 CRLF 가 없으면 새 글 그대로(cn과 같음). 있으면 새 글 전체를 원래의 다수 줄바꿈으로 맞춘다."""
    nl = dominant_newline(original)                          # 원래 줄바꿈
    if nl is None:                                           # LF 전용(또는 줄바꿈 없음) 파일
        return new                                          # 그대로
    lf = to_lf(new)                                          # 먼저 LF 로 통일
    return lf.replace("\n", "\r\n") if nl == "\r\n" else lf   # 다수 쪽으로


def lf_matching(content: str) -> bool:                      # Edit 가 LF 로 비교할지
    """E3 가 켜져 있고 내용에 CRLF 가 있으면 참 — 모델이 LF 로 준 old_string 도 맞게."""
    return enabled() and "\r\n" in content                   # 조건


# ───────────────────────── 디스크 읽기·쓰기(fsio.DiskFiles 가 부른다) ─────────────────────────
def read_text(path: str) -> str:                            # 판별해 읽기
    """디스크 파일을 판별해 해석한 글(BOM 은 U+FEFF). 오류 글은 Node readFileSync 와 같다."""
    from ncn.tools.formatters import read_file_bytes        # 지연 import(순환 방지)
    return decode_bytes(read_file_bytes(path), fallback_encoding())[0]   # 글만


def read_format(path: str) -> Optional[tuple[str, TextFormat]]:   # 기존 파일의 (글, 형식)
    """파일이 없거나 못 읽으면 None."""
    from ncn.tools.formatters import read_file_bytes        # 지연 import
    if not os.path.isfile(path):                             # 없거나 폴더
        return None                                         # 새 파일
    try:                                                    # 읽기
        return decode_bytes(read_file_bytes(path), fallback_encoding())   # 판별 결과
    except Exception:                                        # 권한 등
        return None                                         # 새 파일처럼(쓰기에서 cn 오류가 남)


def write_text(path: str, content: str) -> None:            # 원래 형식으로 쓰기
    """기존 파일이면 그 인코딩·BOM·다수 줄바꿈으로, 없으면 UTF-8·받은 그대로(cn). 인코딩 실패면 아무것도 안 쓰고 EncodingSaveError."""
    from ncn.tools.formatters import write_file_bytes       # 지연 import
    prev = read_format(path)                                 # 기존 파일 형식
    if prev is None:                                         # 새 파일
        data = encode_text(content, UTF8, path)              # cn 과 같은 UTF-8
    else:                                                    # 기존 파일
        old_text, fmt = prev                                 # 원래 글·형식
        data = encode_text(conform_newlines(old_text, content), fmt, path)   # 줄바꿈·인코딩 복원(먼저 인코딩 → 실패면 파일 그대로)
    write_file_bytes(path, data)                             # 기록
