# ncn/mcp/content.py — tools/call 결과의 content 를 cn 이 보는 모양으로 정리. cn 이 쓰는 @modelcontextprotocol/sdk 1.24.1
#   dist/esm/types.js 의 zod(v4) 스키마(TextContentSchema·ImageContentSchema·AudioContentSchema·ResourceLinkSchema·EmbeddedResourceSchema)
# zod z.object 는 ① 모르는 키를 버리고(strip) ② 스키마에 적힌 순서로 키를 다시 놓는다. 그래서 cn 결과 글에서는
#   서버가 보낸 annotations 가 빠지고 "type" 이 맨 앞에 온다(직접 실행해 확인 — parity/scenarios/21_mcp_tool_call).
# 파이썬 SDK(pydantic)는 annotations 를 남기고 순서도 다르므로 여기서 Node 스키마대로 다시 만든다.
from __future__ import annotations                           # 타입 주석 지연 평가

from typing import Any, Optional                             # 타입

# 각 스키마의 키 순서(값: 하위 스키마 이름 또는 None=그대로). "_meta" 는 z.record → 안쪽은 그대로
_ICON = [("src", None), ("mimeType", None), ("sizes", None)]   # IconSchema 키 순서
_RESOURCE_CONTENTS = [("uri", None), ("mimeType", None), ("_meta", None)]   # ResourceContentsSchema 키 순서
_TEXT_RESOURCE = _RESOURCE_CONTENTS + [("text", None)]       # TextResourceContentsSchema = ResourceContents.extend({text}) 키 순서
_BLOB_RESOURCE = _RESOURCE_CONTENTS + [("blob", None)]       # BlobResourceContentsSchema = ResourceContents.extend({blob}) 키 순서
_SHAPES: dict[str, list] = {                                 # content type → 키 순서
    "text": [("type", None), ("text", None), ("_meta", None)],   # TextContentSchema(annotations 없음)
    "image": [("type", None), ("data", None), ("mimeType", None), ("_meta", None)],   # ImageContentSchema 키 순서
    "audio": [("type", None), ("data", None), ("mimeType", None), ("_meta", None)],   # AudioContentSchema 키 순서
    "resource_link": [("name", None), ("title", None), ("icons", "icons"), ("uri", None), ("description", None),   # ResourceSchema(Base·Icons 펼침)
                      ("mimeType", None), ("_meta", None), ("type", None)],   # …+ extend({type}) 라 type 이 마지막
    "resource": [("type", None), ("resource", "resource"), ("_meta", None)],   # EmbeddedResourceSchema 키 순서
}                                                            # 표 닫기


def _pick(obj: dict, shape: list) -> dict:                   # 스키마 순서대로 있는 키만 골라 새 dict
    """zod strip — shape 에 있는 키만, shape 순서로. 값이 있는 키만 넣는다(없던 키는 만들지 않음)."""
    out: dict = {}                                           # 결과
    for key, sub in shape:                                   # 스키마 키마다
        if key not in obj:                                   # 입력에 없으면
            continue                                         # 건너뜀(zod 도 만들지 않음)
        val = obj[key]                                       # 값
        if sub == "icons" and isinstance(val, list):         # icons: IconSchema 배열
            val = [_pick(i, _ICON) if isinstance(i, dict) else i for i in val]   # 아이콘마다 정리
        elif sub == "resource" and isinstance(val, dict):    # resource: Text | Blob 합집합(앞의 것이 맞으면 그것)
            val = _pick(val, _TEXT_RESOURCE if "text" in val else _BLOB_RESOURCE)   # text 가 있으면 Text 스키마
        out[key] = val                                       # 넣기
    return out                                               # 결과 반환


def shape_content_block(block: dict) -> dict:                # content 한 개를 Node 스키마 모양으로
    """ContentBlockSchema(union) — type 별 스키마로 정리. 모르는 type 은 그대로(cn 은 검증 오류지만 여기서는 보존)."""
    shape: Optional[list] = _SHAPES.get(block.get("type"))   # type 에 맞는 스키마
    return _pick(block, shape) if shape is not None else dict(block)   # 정리 또는 그대로


def shape_content(content: Any) -> list:                     # content 배열 전체 정리
    """CallToolResultSchema.content(default []) — 없으면 빈 배열."""
    if not isinstance(content, list):                        # 없거나 배열이 아니면
        return []                                            # default([]) — 빈 배열
    return [shape_content_block(b) if isinstance(b, dict) else b for b in content]   # 하나씩 정리
