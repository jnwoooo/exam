# ncn/tools/write_file.py — Write 도구 + diff 만들기. cn extensions/cli/src/tools/writeFile.ts
# diff 는 cn이 쓰는 npm "diff" 8.0.4 의 createTwoFilesPatch 와 글자 하나까지 같게: 줄 단위 Myers 알고리즘(diff/base.js)과
# 패치 형식(patch/create.js)을 그대로 옮겼다. difflib 은 같은 변경도 줄 맞춤이 달라질 수 있어 쓰지 않음
# Write 는 민감 파일 검사·Read 선행 검사·Read 기록이 모두 없다(cn 그대로)
from __future__ import annotations                           # 타입 주석 지연 평가

import re                                                    # 줄 나누기
from typing import Optional                                  # 타입

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview, ToolRunContext   # 공용 형식
from ncn.tools.findreplace import REASON_FILE_WRITE_ERROR, REASON_UNKNOWN   # 오류 이유
from ncn.tools.formatters import exists_sync, mkdir_recursive, path_dirname, read_file_utf8, write_file_utf8   # Node fs·path


# ───────────────────────── npm diff 8.0.4 — diffLines (diff/base.js + diff/line.js) ─────────────────────────
def _tokenize_lines(value: str) -> list[str]:
    """line.js tokenize — 줄바꿈('\\n' 또는 '\\r\\n')을 줄 끝에 붙인 토큰들."""
    parts = re.split(r"(\n|\r\n)", value)                    # 줄과 구분자를 번갈아
    if not parts[-1]:                                        # 끝이 줄바꿈이면 마지막 빈 조각 제거
        parts.pop()
    ret: list[str] = []
    for i, part in enumerate(parts):                         # 구분자는 앞 줄에 붙임
        if i % 2:
            ret[-1] += part
        else:
            ret.append(part)
    return ret


class _Comp:
    """변경 조각(연결 목록 노드): 개수·추가·삭제·이전 조각."""
    __slots__ = ("count", "added", "removed", "prev", "value")

    def __init__(self, count: int, added: bool, removed: bool, prev: Optional["_Comp"]):
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


def _add_to_path(path: _Path, added: bool, removed: bool, old_pos_inc: int) -> _Path:
    """addToPath — 같은 종류면 개수만 늘림."""
    last = path.last
    if last is not None and last.added == added and last.removed == removed:   # 이어 붙이기
        return _Path(path.old_pos + old_pos_inc, _Comp(last.count + 1, added, removed, last.prev))
    return _Path(path.old_pos + old_pos_inc, _Comp(1, added, removed, last))


def _extract_common(base: _Path, new: list[str], old: list[str], diagonal: int) -> int:
    """extractCommon — 대각선을 따라 같은 토큰을 최대한 전진. 새 쪽 위치를 돌려줌."""
    new_len, old_len = len(new), len(old)
    old_pos = base.old_pos
    new_pos = old_pos - diagonal
    common = 0
    while new_pos + 1 < new_len and old_pos + 1 < old_len and old[old_pos + 1] == new[new_pos + 1]:   # 같은 동안
        new_pos += 1
        old_pos += 1
        common += 1
    if common:                                               # 공통 조각 추가
        base.last = _Comp(common, False, False, base.last)
    base.old_pos = old_pos
    return new_pos


def _build_values(last: Optional[_Comp], new: list[str], old: list[str]) -> list[_Comp]:
    """buildValues — 연결 목록을 순서대로 펼치고 각 조각의 글을 채움."""
    comps: list[_Comp] = []
    while last is not None:                                  # 뒤에서 앞으로
        comps.append(last)
        last = last.prev
    comps.reverse()                                          # 앞에서 뒤로
    new_pos = old_pos = 0
    for c in comps:
        if not c.removed:                                    # 공통 또는 추가: 새 쪽 글
            c.value = "".join(new[new_pos:new_pos + c.count])
            new_pos += c.count
            if not c.added:                                  # 공통이면 옛 쪽도 전진
                old_pos += c.count
        else:                                                # 삭제: 옛 쪽 글
            c.value = "".join(old[old_pos:old_pos + c.count])
            old_pos += c.count
    return comps


def diff_lines(old_str: str, new_str: str) -> list[_Comp]:
    """diffLines(옵션 없음) — npm diff 의 Myers 변형(가장자리 대각선 가지치기 포함) 그대로."""
    old = [t for t in _tokenize_lines(old_str) if t]         # removeEmpty
    new = [t for t in _tokenize_lines(new_str) if t]
    new_len, old_len = len(new), len(old)
    edit_length = 1                                          # 지금 편집 거리
    max_edit_length = new_len + old_len                      # 최대 편집 거리
    best: dict[int, Optional[_Path]] = {0: _Path(-1, None)}  # 대각선별 최선 경로
    new_pos = _extract_common(best[0], new, old, 0)          # 처음 공통 부분
    if best[0].old_pos + 1 >= old_len and new_pos + 1 >= new_len:   # 완전히 같음
        return _build_values(best[0].last, new, old)
    min_diag = float("-inf")                                 # 볼 대각선 범위(가장자리에 닿으면 좁힘)
    max_diag = float("inf")
    while edit_length <= max_edit_length:                    # 편집 거리를 늘려 가며
        diag = int(max(min_diag, -edit_length))              # 시작 대각선
        top = min(max_diag, edit_length)                     # 끝 대각선
        while diag <= top:
            remove_path = best.get(diag - 1)                 # 삭제로 오는 경로
            add_path = best.get(diag + 1)                    # 추가로 오는 경로
            if remove_path is not None:                      # 다시 안 쓰므로 비움
                best[diag - 1] = None
            can_add = False
            if add_path is not None:                         # 추가 후 새 쪽 위치가 범위 안인가
                add_new_pos = add_path.old_pos - diag
                can_add = 0 <= add_new_pos < new_len
            can_remove = remove_path is not None and remove_path.old_pos + 1 < old_len
            if not can_add and not can_remove:               # 막힌 경로
                best[diag] = None
                diag += 2
                continue
            if not can_remove or (can_add and remove_path.old_pos < add_path.old_pos):   # 옛 쪽으로 더 나간 경로 선택
                base = _add_to_path(add_path, True, False, 0)
            else:
                base = _add_to_path(remove_path, False, True, 1)
            new_pos = _extract_common(base, new, old, diag)
            if base.old_pos + 1 >= old_len and new_pos + 1 >= new_len:   # 끝에 닿음
                return _build_values(base.last, new, old)
            best[diag] = base
            if base.old_pos + 1 >= old_len:                  # 옛 쪽 끝 → 위 대각선은 볼 필요 없음
                max_diag = min(max_diag, diag - 1)
            if new_pos + 1 >= new_len:                       # 새 쪽 끝 → 아래 대각선은 볼 필요 없음
                min_diag = max(min_diag, diag + 1)
            diag += 2
        edit_length += 1
    return []                                                # 도달하지 않음(최대 거리 안에 항상 끝남)


# ───────────────────────── npm diff 8.0.4 — structuredPatch + formatPatch (patch/create.js) ─────────────────────────
def _split_lines(text: str) -> list[str]:
    """splitLines — 줄마다 끝 '\\n' 포함(마지막 줄은 원래대로)."""
    has_trailing_nl = text.endswith("\n")
    result = [line + "\n" for line in text.split("\n")]
    if has_trailing_nl:                                      # 끝 줄바꿈이면 마지막 빈 줄 제거
        result.pop()
    else:                                                    # 아니면 마지막 줄의 '\n' 제거
        result[-1] = result[-1][:-1]
    return result


def create_two_files_patch(old_file_name: str, new_file_name: str, old_str: str, new_str: str,
                           old_header: Optional[str] = None, new_header: Optional[str] = None, context: int = 4) -> str:
    """createTwoFilesPatch — 'Index:' + '===' + '---'/'+++' 머리말 + @@ 덩어리. 끝은 항상 '\\n'."""
    diff = diff_lines(old_str, new_str)                      # 줄 변경 목록
    entries = [{"value": c.value, "added": c.added, "removed": c.removed, "lines": None} for c in diff]
    entries.append({"value": "", "added": False, "removed": False, "lines": []})   # 정리용 빈 항목
    hunks = []                                               # 덩어리들
    old_range_start = new_range_start = 0                    # 지금 덩어리 시작(0 = 없음)
    cur_range: list[str] = []                                # 지금 덩어리 줄들
    old_line = new_line = 1                                  # 지금 줄 번호

    def context_lines(lines: list[str]) -> list[str]:        # 공통 줄은 ' ' 접두
        return [" " + e for e in lines]

    for i, cur in enumerate(entries):
        lines = cur["lines"] if cur["lines"] is not None else _split_lines(cur["value"])
        cur["lines"] = lines
        if cur["added"] or cur["removed"]:                   # 변경 줄
            if not old_range_start:                          # 새 덩어리 시작 — 앞 공통 줄을 문맥으로
                prev = entries[i - 1] if i > 0 else None
                old_range_start = old_line
                new_range_start = new_line
                if prev is not None:
                    cur_range = context_lines(prev["lines"][-context:]) if context > 0 else []
                    old_range_start -= len(cur_range)
                    new_range_start -= len(cur_range)
            for line in lines:                               # 변경 줄 기록
                cur_range.append(("+" if cur["added"] else "-") + line)
            if cur["added"]:
                new_line += len(lines)
            else:
                old_line += len(lines)
        else:                                                # 공통 줄
            if old_range_start:                              # 덩어리 진행 중
                if len(lines) <= context * 2 and i < len(entries) - 2:   # 다음 변경과 겹침 → 이어 감
                    cur_range.extend(context_lines(lines))
                else:                                        # 덩어리 닫기
                    context_size = min(len(lines), context)
                    cur_range.extend(context_lines(lines[:context_size]))
                    hunks.append({"oldStart": old_range_start, "oldLines": old_line - old_range_start + context_size,
                                  "newStart": new_range_start, "newLines": new_line - new_range_start + context_size,
                                  "lines": cur_range})
                    old_range_start = new_range_start = 0
                    cur_range = []
            old_line += len(lines)
            new_line += len(lines)
    for hunk in hunks:                                       # 줄 끝 '\n' 떼고, 없던 곳엔 표시 줄 추가
        hl = hunk["lines"]
        i = 0
        while i < len(hl):
            if hl[i].endswith("\n"):
                hl[i] = hl[i][:-1]
            else:
                hl.insert(i + 1, "\\ No newline at end of file")
                i += 1
            i += 1
    ret = []                                                 # formatPatch(INCLUDE_HEADERS)
    if old_file_name == new_file_name:
        ret.append("Index: " + old_file_name)
    ret.append("===================================================================")
    ret.append("--- " + old_file_name + ("" if old_header is None else "\t" + old_header))
    ret.append("+++ " + new_file_name + ("" if new_header is None else "\t" + new_header))
    for hunk in hunks:
        if hunk["oldLines"] == 0:                            # 크기 0 이면 시작 번호를 하나 낮춤(unified diff 관례)
            hunk["oldStart"] -= 1
        if hunk["newLines"] == 0:
            hunk["newStart"] -= 1
        ret.append(f"@@ -{hunk['oldStart']},{hunk['oldLines']} +{hunk['newStart']},{hunk['newLines']} @@")
        ret.extend(hunk["lines"])
    return "\n".join(ret) + "\n"


def generate_diff(old_content: str, new_content: str, file_path: str) -> str:
    """generateDiff — 결과 글용 diff(문맥 3줄)."""
    return create_two_files_patch(file_path, file_path, old_content, new_content, None, None, context=3)


# ───────────────────────── Write 도구 ─────────────────────────
async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 기존 파일이면 diff 미리보기(문맥 2줄), 새 파일이면 앞 3줄."""
    filepath = args.get("filepath") if isinstance(args, dict) else None   # args?.filepath
    content = args.get("content") if isinstance(args, dict) else None
    if content is None:                                      # args?.content ?? ""
        content = ""
    if not isinstance(filepath, str):                        # cn: 일반 Error
        raise ContinueError(REASON_UNKNOWN, "Filepath must be a string")
    if not isinstance(content, str):
        raise ContinueError(REASON_UNKNOWN, "New file content must be a string")
    try:
        if exists_sync(filepath):                            # 기존 파일
            old_content = read_file_utf8(filepath)
            diff = create_two_files_patch(args["filepath"], args["filepath"], old_content, content, None, None, context=2)
            return PreprocessResult(args=args, preview=[ToolCallPreview("text", "Preview of changes:"),
                                                        ToolCallPreview("diff", diff)])
    except Exception:                                        # cn: 무시하고 새 파일처럼
        pass
    lines = content.split("\n")                              # 새 파일 미리보기
    preview = [ToolCallPreview("text", "New file content:")]
    preview += [ToolCallPreview("text", line or " ", padding_left=2) for line in lines[:3]]   # 빈 줄은 ' '
    if len(lines) > 3:
        preview.append(ToolCallPreview("text", f"... ({len(lines) - 3} more lines)"))
    return PreprocessResult(args={"filepath": filepath, "content": content}, preview=preview)


async def _run(args: dict, context: Optional[ToolRunContext] = None) -> str:
    """run — 상위 폴더 만들고 씀. 기존 내용이 있으면 diff 를 붙여 돌려줌."""
    try:
        filepath = args["filepath"]                          # 경로
        content = args["content"]                            # 내용
        dir_path = path_dirname(filepath)                    # 상위 폴더
        if not exists_sync(dir_path):                        # 없으면 만듦
            mkdir_recursive(dir_path)
        old_content = ""                                     # 기존 내용
        if exists_sync(filepath):
            old_content = read_file_utf8(filepath)
        write_file_utf8(filepath, content)                   # 쓰기(utf-8, 줄바꿈 그대로)
        if old_content:                                      # 기존 내용이 있었음(빈 파일이면 새 파일처럼 — cn 그대로)
            diff = generate_diff(old_content, content, filepath)
            return f"Successfully wrote to file: {filepath}\nDiff:\n{diff}"
        return f"Successfully created file: {filepath}"
    except ContinueError:                                    # 그대로
        raise
    except Exception as e:                                   # 그 밖 → 쓰기 오류
        raise ContinueError(REASON_FILE_WRITE_ERROR, f"Error writing to file: {_err_message(e)}") from e


def _err_message(e: BaseException) -> str:
    """JS error.message 흉내 — KeyError 등은 따옴표 없이."""
    if isinstance(e, KeyError) and e.args:
        return str(e.args[0])
    return str(e)


WRITE_TOOL = Tool(
    name="Write",
    display_name="Write",
    description="Write content to a file at the specified path",
    parameters={
        "type": "object",
        "required": ["filepath", "content"],
        "properties": {
            "filepath": {
                "type": "string",
                "description": "The path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
    readonly=False,
    is_built_in=True,
)
