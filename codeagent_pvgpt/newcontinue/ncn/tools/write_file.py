# ncn/tools/write_file.py — Write 도구 + diff 만들기. cn extensions/cli/src/tools/writeFile.ts
# diff 는 cn이 쓰는 npm "diff" 8.0.4 의 createTwoFilesPatch 와 글자 하나까지 같게: 줄 단위 Myers 알고리즘(diff/base.js)과
# 패치 형식(patch/create.js)을 그대로 옮겼다. difflib 은 같은 변경도 줄 맞춤이 달라질 수 있어 쓰지 않음
# Write 는 Read 선행 검사·Read 기록이 없다(cn 그대로). 민감 파일 검사는 cn에 없지만 ncn은 한다([ncn 부록B S6], NCN_CN_COMPAT=1 이면 cn처럼 없음)
from __future__ import annotations                           # 타입 주석 지연 평가


import re                                                    # 줄 나누기
from typing import Optional                                  # 타입

from ncn.compat import cn_compat                             # [ncn 부록B S6] cn 원래 동작 스위치
from ncn.security.ignore import is_security_concern, security_concern_error, throw_if_file_is_security_concern   # 민감 파일 판정([ncn 부록B S6])
from ncn import fsio                                         # [ncn V3] 파일 창구(ACP 세션이면 IDE 위임, 아니면 디스크)
from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import REASON_FILE_WRITE_ERROR, REASON_UNKNOWN   # 오류 이유
from ncn.tools.formatters import exists_sync, mkdir_recursive, path_dirname, read_file_utf8, realpath_sync, write_file_utf8   # Node fs·path


# ───────────────────────── npm diff 8.0.4 — diffLines (diff/base.js + diff/line.js) ─────────────────────────
def _tokenize_lines(value: str) -> list[str]:               # 줄 단위 토큰화(줄바꿈을 줄 끝에 붙임)
    """line.js tokenize — 줄바꿈('\\n' 또는 '\\r\\n')을 줄 끝에 붙인 토큰들."""
    parts = re.split(r"(\n|\r\n)", value)                    # 줄과 구분자를 번갈아
    if not parts[-1]:                                        # 끝이 줄바꿈이면 마지막 빈 조각 제거
        parts.pop()                                         # 제거
    ret: list[str] = []                                     # 결과 토큰 목록
    for i, part in enumerate(parts):                         # 구분자는 앞 줄에 붙임
        if i % 2:                                           # 홀수 번째는 구분자
            ret[-1] += part                                 # 앞 줄 끝에 붙임
        else:                                               # 짝수 번째는 줄 내용
            ret.append(part)                                # 새 토큰으로 추가
    return ret                                              # 토큰 목록 반환


class _Comp:                                                # diff 변경 조각(연결 목록의 한 노드)
    """변경 조각(연결 목록 노드): 개수·추가·삭제·이전 조각."""
    __slots__ = ("count", "added", "removed", "prev", "value")  # 메모리 절약용 슬롯 고정

    def __init__(self, count: int, added: bool, removed: bool, prev: Optional["_Comp"]):  # 조각 생성자
        self.count = count                                   # 토큰 수
        self.added = added                                   # 새 쪽에만 있음
        self.removed = removed                               # 옛 쪽에만 있음
        self.prev = prev                                     # 앞 조각
        self.value = ""                                      # 합친 글(buildValues 에서)


class _Path:
    """bestPath 원소: 옛 쪽 위치 + 마지막 조각."""
    __slots__ = ("old_pos", "last")

    def __init__(self, old_pos: int, last: Optional[_Comp]):
        self.old_pos = old_pos                               # 옛 토큰 위치
        self.last = last                                     # 마지막 조각


def _add_to_path(path: _Path, added: bool, removed: bool, old_pos_inc: int) -> _Path:  # 조각 경로를 확장하는 함수
    """addToPath — 같은 종류면 개수만 늘림."""
    last = path.last                                        # 마지막 조각
    if last is not None and last.added == added and last.removed == removed:   # 이어 붙이기
        return _Path(path.old_pos + old_pos_inc, _Comp(last.count + 1, added, removed, last.prev))  # 개수만 늘린 새 조각으로 교체
    return _Path(path.old_pos + old_pos_inc, _Comp(1, added, removed, last))  # 다른 종류면 새 조각 추가


def _extract_common(base: _Path, new: list[str], old: list[str], diagonal: int) -> int:  # 대각선을 따라 같은 토큰을 최대한 전진
    """extractCommon — 대각선을 따라 같은 토큰을 최대한 전진. 새 쪽 위치를 돌려줌."""
    new_len, old_len = len(new), len(old)                   # 두 토큰 목록의 길이
    old_pos = base.old_pos                                  # 옛 쪽 현재 위치
    new_pos = old_pos - diagonal                            # 새 쪽 현재 위치
    common = 0                                              # 이번에 전진한 개수
    while new_pos + 1 < new_len and old_pos + 1 < old_len and old[old_pos + 1] == new[new_pos + 1]:   # 같은 동안
        new_pos += 1                                        # 새 쪽 한 칸
        old_pos += 1                                        # 옛 쪽 한 칸
        common += 1                                         # 공통 개수 증가
    if common:                                               # 공통 조각 추가
        base.last = _Comp(common, False, False, base.last)  # 공통 조각을 경로 끝에 추가
    base.old_pos = old_pos                                  # 옛 쪽 위치 갱신
    return new_pos                                          # 새 쪽 위치 반환


def _build_values(last: Optional[_Comp], new: list[str], old: list[str]) -> list[_Comp]:  # 연결 목록을 펼쳐 각 조각의 글을 채운다
    """buildValues — 연결 목록을 순서대로 펼치고 각 조각의 글을 채움."""
    comps: list[_Comp] = []                                 # 조각 목록
    while last is not None:                                  # 뒤에서 앞으로
        comps.append(last)                                  # 목록에 담음
        last = last.prev                                    # 앞 조각으로 이동
    comps.reverse()                                          # 앞에서 뒤로
    new_pos = old_pos = 0                                   # 새 쪽·옛 쪽 위치
    for c in comps:                                         # 조각마다
        if not c.removed:                                    # 공통 또는 추가: 새 쪽 글
            c.value = "".join(new[new_pos:new_pos + c.count])  # 새 쪽 글로 채움
            new_pos += c.count                              # 새 쪽 위치 전진
            if not c.added:                                  # 공통이면 옛 쪽도 전진
                old_pos += c.count                          # 옛 쪽도 전진
        else:                                                # 삭제: 옛 쪽 글
            c.value = "".join(old[old_pos:old_pos + c.count])  # 옛 쪽 글로 채움
            old_pos += c.count                              # 옛 쪽 위치 전진
    return comps                                            # 채워진 조각 목록 반환


def diff_lines(old_str: str, new_str: str) -> list[_Comp]:  # 줄 단위 diff(Myers 알고리즘)
    """diffLines(옵션 없음) — npm diff 의 Myers 변형(가장자리 대각선 가지치기 포함) 그대로."""
    old = [t for t in _tokenize_lines(old_str) if t]        # 옛 글의 줄 토큰(빈 토큰 제외)
    new = [t for t in _tokenize_lines(new_str) if t]        # 새 글의 줄 토큰(빈 토큰 제외)
    new_len, old_len = len(new), len(old)                   # 두 목록의 길이
    edit_length = 1                                          # 지금 편집 거리
    max_edit_length = new_len + old_len                      # 최대 편집 거리
    best: dict[int, Optional[_Path]] = {0: _Path(-1, None)}  # 대각선별 최선 경로
    new_pos = _extract_common(best[0], new, old, 0)          # 처음 공통 부분
    if best[0].old_pos + 1 >= old_len and new_pos + 1 >= new_len:   # 완전히 같음
        return _build_values(best[0].last, new, old)        # 완전히 같으면 바로 결과 반환
    min_diag = float("-inf")                                 # 볼 대각선 범위(가장자리에 닿으면 좁힘)
    max_diag = float("inf")
    while edit_length <= max_edit_length:                    # 편집 거리를 늘려 가며
        diag = int(max(min_diag, -edit_length))              # 시작 대각선
        top = min(max_diag, edit_length)                     # 끝 대각선
        while diag <= top:                                  # 대각선을 하나씩(2칸 간격)
            remove_path = best.get(diag - 1)                 # 삭제로 오는 경로
            add_path = best.get(diag + 1)                    # 추가로 오는 경로
            if remove_path is not None:                      # 다시 안 쓰므로 비움
                best[diag - 1] = None                       # 비움
            can_add = False                                 # 추가 가능 여부 초기값
            if add_path is not None:                         # 추가 후 새 쪽 위치가 범위 안인가
                add_new_pos = add_path.old_pos - diag       # 추가 후 새 쪽 위치
                can_add = 0 <= add_new_pos < new_len        # 범위 안이면 추가 가능
            can_remove = remove_path is not None and remove_path.old_pos + 1 < old_len  # 삭제 가능 여부
            if not can_add and not can_remove:               # 막힌 경로
                best[diag] = None                           # 이 대각선은 막힘으로 기록
                diag += 2                                   # 다음 대각선으로
                continue                                    # 다음 반복으로
            if not can_remove or (can_add and remove_path.old_pos < add_path.old_pos):   # 옛 쪽으로 더 나간 경로 선택
                base = _add_to_path(add_path, True, False, 0)  # 추가 경로를 이어 감
            else:                                           # 그렇지 않으면
                base = _add_to_path(remove_path, False, True, 1)  # 삭제 경로를 이어 감
            new_pos = _extract_common(base, new, old, diag)  # 공통 부분까지 전진
            if base.old_pos + 1 >= old_len and new_pos + 1 >= new_len:   # 끝에 닿음
                return _build_values(base.last, new, old)   # 조각을 펼쳐 결과 반환
            best[diag] = base                               # 이 대각선의 최선 경로로 기록
            if base.old_pos + 1 >= old_len:                  # 옛 쪽 끝 → 위 대각선은 볼 필요 없음
                max_diag = min(max_diag, diag - 1)          # 위 대각선 범위를 좁힘
            if new_pos + 1 >= new_len:                       # 새 쪽 끝 → 아래 대각선은 볼 필요 없음
                min_diag = max(min_diag, diag + 1)          # 아래 대각선 범위를 좁힘
            diag += 2                                       # 다음 대각선으로
        edit_length += 1                                    # 편집 거리 증가
    return []                                                # 도달하지 않음(최대 거리 안에 항상 끝남)


# ───────────────────────── npm diff 8.0.4 — structuredPatch + formatPatch (patch/create.js) ─────────────────────────
def _split_lines(text: str) -> list[str]:                   # 줄마다 끝 줄바꿈을 포함해 나눈다
    """splitLines — 줄마다 끝 '\\n' 포함(마지막 줄은 원래대로)."""
    has_trailing_nl = text.endswith("\n")                   # 원문이 줄바꿈으로 끝나는지
    result = [line + "\n" for line in text.split("\n")]     # 줄마다 '\n' 을 붙임
    if has_trailing_nl:                                      # 끝 줄바꿈이면 마지막 빈 줄 제거
        result.pop()                                        # 마지막 빈 줄 제거
    else:                                                    # 아니면 마지막 줄의 '\n' 제거
        result[-1] = result[-1][:-1]                        # 마지막 줄의 붙인 '\n' 제거
    return result                                           # 줄 목록 반환


def create_two_files_patch(old_file_name: str, new_file_name: str, old_str: str, new_str: str,  # 두 파일의 통합 diff 문자열을 만든다
                           old_header: Optional[str] = None, new_header: Optional[str] = None, context: int = 4) -> str:
    """createTwoFilesPatch — 'Index:' + '===' + '---'/'+++' 머리말 + @@ 덩어리. 끝은 항상 '\\n'."""
    diff = diff_lines(old_str, new_str)                      # 줄 변경 목록
    entries = [{"value": c.value, "added": c.added, "removed": c.removed, "lines": None} for c in diff]  # diff 조각을 dict 항목으로 변환
    entries.append({"value": "", "added": False, "removed": False, "lines": []})   # 정리용 빈 항목
    hunks = []                                               # 덩어리들
    old_range_start = new_range_start = 0                    # 지금 덩어리 시작(0 = 없음)
    cur_range: list[str] = []                                # 지금 덩어리 줄들
    old_line = new_line = 1                                  # 지금 줄 번호

    def context_lines(lines: list[str]) -> list[str]:        # 공통 줄은 ' ' 접두
        return [" " + e for e in lines]                     # 접두를 붙인 줄 목록

    for i, cur in enumerate(entries):                       # 항목마다
        lines = cur["lines"] if cur["lines"] is not None else _split_lines(cur["value"])  # 줄 목록(없으면 값에서 나눔)
        cur["lines"] = lines                                # 항목에 줄 목록 저장
        if cur["added"] or cur["removed"]:                   # 변경 줄
            if not old_range_start:                          # 새 덩어리 시작 — 앞 공통 줄을 문맥으로
                prev = entries[i - 1] if i > 0 else None    # 앞 항목(문맥으로 쓸 공통 줄)
                old_range_start = old_line                  # 옛 쪽 시작 번호
                new_range_start = new_line                  # 새 쪽 시작 번호
                if prev is not None:                        # 앞 항목이 있으면
                    cur_range = context_lines(prev["lines"][-context:]) if context > 0 else []  # 앞 공통 줄의 끝 context 줄을 문맥으로
                    old_range_start -= len(cur_range)       # 옛 쪽 시작을 문맥만큼 앞당김
                    new_range_start -= len(cur_range)       # 새 쪽 시작을 문맥만큼 앞당김
            for line in lines:                               # 변경 줄 기록
                cur_range.append(("+" if cur["added"] else "-") + line)  # 추가면 '+', 삭제면 '-'
            if cur["added"]:                                # 추가 줄이면
                new_line += len(lines)                      # 새 쪽 줄 번호 증가
            else:                                           # 삭제 줄이면
                old_line += len(lines)                      # 옛 쪽 줄 번호 증가
        else:                                                # 공통 줄
            if old_range_start:                              # 덩어리 진행 중
                if len(lines) <= context * 2 and i < len(entries) - 2:   # 다음 변경과 겹침 → 이어 감
                    cur_range.extend(context_lines(lines))  # 문맥으로 이어 붙임
                else:                                        # 덩어리 닫기
                    context_size = min(len(lines), context)  # 뒤 문맥 줄 수
                    cur_range.extend(context_lines(lines[:context_size]))  # 뒤 문맥을 붙임
                    hunks.append({"oldStart": old_range_start, "oldLines": old_line - old_range_start + context_size,  # 덩어리 하나를 완성해 목록에 추가
                                  "newStart": new_range_start, "newLines": new_line - new_range_start + context_size,  # 새 쪽 시작·줄 수
                                  "lines": cur_range})      # 덩어리 줄들
                    old_range_start = new_range_start = 0   # 덩어리 상태 초기화
                    cur_range = []                          # 줄 목록 초기화
            old_line += len(lines)                          # 옛 쪽 줄 번호 증가
            new_line += len(lines)                          # 새 쪽 줄 번호 증가
    for hunk in hunks:                                       # 줄 끝 '\n' 떼고, 없던 곳엔 표시 줄 추가
        hl = hunk["lines"]                                  # 덩어리의 줄들
        i = 0                                               # 줄 위치
        while i < len(hl):                                  # 줄마다
            if hl[i].endswith("\n"):                        # 줄바꿈이 있으면
                hl[i] = hl[i][:-1]                          # 줄바꿈 제거
            else:                                           # 줄바꿈이 없으면
                hl.insert(i + 1, "\\ No newline at end of file")  # "끝 줄바꿈 없음" 표시 줄 삽입
                i += 1                                      # 표시 줄을 건너뜀
            i += 1                                          # 다음 줄로
    ret = []                                                # 머리말과 덩어리를 이어 붙일 목록
    if old_file_name == new_file_name:                      # 두 이름이 같으면
        ret.append("Index: " + old_file_name)               # 'Index:' 줄 추가
    ret.append("===================================================================")  # 구분선 추가
    ret.append("--- " + old_file_name + ("" if old_header is None else "\t" + old_header))  # 옛 파일 머리말 줄
    ret.append("+++ " + new_file_name + ("" if new_header is None else "\t" + new_header))  # 새 파일 머리말 줄
    for hunk in hunks:                                      # 덩어리마다
        if hunk["oldLines"] == 0:                            # 크기 0 이면 시작 번호를 하나 낮춤(unified diff 관례)
            hunk["oldStart"] -= 1                           # 옛 쪽이 0 줄이면 시작 번호를 하나 낮춤
        if hunk["newLines"] == 0:
            hunk["newStart"] -= 1                           # 새 쪽이 0 줄이면 시작 번호를 하나 낮춤
        ret.append(f"@@ -{hunk['oldStart']},{hunk['oldLines']} +{hunk['newStart']},{hunk['newLines']} @@")  # @@ 덩어리 머리말 추가
        ret.extend(hunk["lines"])                           # 덩어리 줄들 추가
    return "\n".join(ret) + "\n"                            # 줄바꿈으로 이어 붙이고 끝에 '\n'


def generate_diff(old_content: str, new_content: str, file_path: str) -> str:  # 결과 글에 붙일 diff 생성(문맥 3줄)
    """generateDiff — 결과 글용 diff(문맥 3줄)."""
    return create_two_files_patch(file_path, file_path, old_content, new_content, None, None, context=3)  # 문맥 3줄로 통합 diff 생성


# ───────────────────────── Write 도구 ─────────────────────────
def _throw_if_write_target_is_security_concern(filepath: str) -> None:   # [ncn 부록B S6] Write 대상 민감 파일 판정
    """'./' 뗀 경로(Read 와 같은 규칙)로 판정하고, 있는 파일(링크 포함)이면 실경로로도 판정(S4와 같게). 걸리면 cn 원문 글."""
    shown = filepath[2:] if filepath.startswith("./") else filepath   # Read 처럼 앞 './' 만 뗌
    throw_if_file_is_security_concern(shown)                 # 경로 판정(형식 오류도 Read 와 같은 글)
    if exists_sync(shown):                                   # 이미 있는 파일(링크)이면
        try:                                                # 실경로 구하기 실패는 무시
            real_path = realpath_sync(shown)                 # 링크를 푼 실제 경로
        except Exception:                                    # 실패하면
            return                                          # 실경로 판정 생략
        if is_security_concern(real_path):                   # 실제 대상이 민감 파일이면
            raise security_concern_error(shown)             # 같은 글로 거부


async def _preprocess(args: dict) -> PreprocessResult:      # Write 의 preprocess(미리보기 만들기)
    """preprocess — 기존 파일이면 diff 미리보기(문맥 2줄), 새 파일이면 앞 3줄."""
    filepath = args.get("filepath") if isinstance(args, dict) else None   # args?.filepath
    content = args.get("content") if isinstance(args, dict) else None  # 내용도 같은 방식으로 꺼냄
    if content is None:                                      # args?.content ?? ""
        content = ""                                        # 없으면 빈 글
    if not isinstance(filepath, str):                        # cn: 일반 Error
        raise ContinueError(REASON_UNKNOWN, "Filepath must be a string")  # 오류(cn 일반 Error)
    if not isinstance(content, str):                        # 내용이 문자열이 아니면
        raise ContinueError(REASON_UNKNOWN, "New file content must be a string")  # 오류(cn 일반 Error)
    if not cn_compat():                                     # [ncn 부록B S6] 고친 동작: Write 도 민감 파일 판정(cn은 판정 없이 .env 등도 덮어씀)
        _throw_if_write_target_is_security_concern(filepath)   # 경로·실경로 중 하나라도 민감하면 Read·Edit 와 같은 글로 거부
    try:                                                    # 기존 파일 diff 를 시도
        if exists_sync(filepath):                            # 기존 파일
            old_content = await fsio.read_text_file(filepath)          # 옛 내용을 읽는다
            diff = create_two_files_patch(args["filepath"], args["filepath"], old_content, content, None, None, context=2)  # 문맥 2줄 diff 를 만든다
            return PreprocessResult(args=args, preview=[ToolCallPreview("text", "Preview of changes:"),  # diff 미리보기와 함께 반환
                                                        ToolCallPreview("diff", diff)])  # diff 항목
    except Exception:                                        # cn: 무시하고 새 파일처럼
        pass                                                # 실패해도 무시하고 새 파일처럼 처리
    lines = content.split("\n")                              # 새 파일 미리보기
    preview = [ToolCallPreview("text", "New file content:")]  # 안내 글
    preview += [ToolCallPreview("text", line or " ", padding_left=2) for line in lines[:3]]   # 빈 줄은 ' '
    if len(lines) > 3:                                      # 3줄을 넘으면
        preview.append(ToolCallPreview("text", f"... ({len(lines) - 3} more lines)"))  # 나머지 줄 수 안내
    return PreprocessResult(args={"filepath": filepath, "content": content}, preview=preview)  # run 인자와 미리보기를 묶어 반환


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:  # Write 의 run(실제로 쓴다)
    """run — 상위 폴더 만들고 씀. 기존 내용이 있으면 diff 를 붙여 돌려줌."""
    try:                                                    # 전체를 감싸 오류 형식을 맞춘다
        filepath = args["filepath"]                          # 경로
        content = args["content"]                            # 내용
        dir_path = path_dirname(filepath)                    # 상위 폴더
        if not exists_sync(dir_path):                        # 없으면 만듦
            mkdir_recursive(dir_path)                       # 상위 폴더를 만든다
        old_content = ""                                     # 기존 내용
        if exists_sync(filepath):                           # 기존 파일이면
            old_content = await fsio.read_text_file(filepath)          # 옛 내용을 읽는다
        await fsio.write_text_file(filepath, content)                   # 쓰기(utf-8, 줄바꿈 그대로)
        if old_content:                                      # 기존 내용이 있었음(빈 파일이면 새 파일처럼 — cn 그대로)
            diff = generate_diff(old_content, content, filepath)  # 옛 내용과의 diff
            return f"Successfully wrote to file: {filepath}\nDiff:\n{diff}"  # diff 를 붙여 성공 메시지 반환
        return f"Successfully created file: {filepath}"     # 새 파일 성공 메시지 반환
    except ContinueError:                                    # 그대로
        raise
    except Exception as e:                                   # 그 밖 → 쓰기 오류
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error writing to file: {_err_message(e)}") from e  # 쓰기 오류로 감싼다


def _err_message(e: BaseException) -> str:                  # JS error.message 흉내
    """JS error.message 흉내 — KeyError 등은 따옴표 없이."""
    if isinstance(e, KeyError) and e.args:                  # KeyError 는
        return str(e.args[0])                               # 따옴표 없이 키만
    return str(e)                                           # 그 밖은 str(e)


WRITE_TOOL = Tool(                                          # Write 도구 정의(cn writeFile.ts)
    name="Write",                                           # LLM 에 보이는 이름
    display_name="Write",                                   # 화면 표시 이름
    description="Write content to a file at the specified path",  # 도구 설명(cn 원문)
    parameters={                                            # 인자 스키마
        "type": "object",                                   # 최상위는 객체
        "required": ["filepath", "content"],                # 필수 인자
        "properties": {                                     # 인자별 정의
            "filepath": {                                   # 쓸 파일 경로
                "type": "string",                           # 문자열 타입
                "description": "The path to the file to write",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
            "content": {                                    # 쓸 내용
                "type": "string",                           # 문자열 타입
                "description": "The content to write to the file",  # 설명(cn 원문)
            },                                              # 여기까지 닫기
        },                                                  # 여기까지 닫기
    },                                                      # 여기까지 닫기
    run=_run,                                               # 실행 함수 연결
    preprocess=_preprocess,                                 # 실행 전 검사 함수 연결
    readonly=False,                                         # 쓰기 도구라 읽기 전용 아님
    is_built_in=True,                                       # 내장 도구
)                                                           # 여기까지 닫기
