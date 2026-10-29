# vs/gallery/make_feed.py — 사설 확장 갤러리(Atom 피드) 만들기: VSIX 안 extension.vsixmanifest 에서 id·버전·이름을 읽어 feed.xml 생성 (계획 14-2 V10)
"""사용법(파일 서버 폴더에 VSIX 를 올린 뒤, 그 폴더에서 한 번 실행):

    python make_feed.py \\\\fs01\\tools\\ncn-vs                       # 폴더 안 *.vsix → 같은 폴더 feed.xml(링크는 UNC 경로)
    python make_feed.py \\\\fs01\\tools\\ncn-vs --base \\\\fs01\\tools\\ncn-vs --out feed.xml
    python make_feed.py NcnVs.vsix --base \\\\fs01\\tools\\ncn-vs         # 파일 하나만

VS 등록: 도구 → 옵션 → 환경 → 확장 → 추가 확장 갤러리 → 추가 → URL 에 feed.xml 의 UNC 경로.
표준 라이브러리만 쓴다(현장 PC 의 아무 Python 3.8+ 에서 실행 가능).
"""
from __future__ import annotations                           # 타입 주석 지연 평가(3.8 호환)

import argparse                                              # 명령줄
import datetime as _dt                                       # 시각
import ntpath                                                # Windows 경로(맥·리눅스에서 실행해도 UNC 처리)
import pathlib                                               # 경로
import sys                                                   # 종료 코드
import urllib.parse                                          # URI 인코딩
import uuid                                                  # 피드 id
import zipfile                                               # VSIX = zip
from xml.etree import ElementTree as ET                      # XML 읽기·쓰기

VSX_NS = "http://schemas.microsoft.com/developer/vsx-schema/2011"   # vsixmanifest 2.0 이름공간
ATOM_NS = "http://www.w3.org/2005/Atom"                      # Atom 이름공간
SYN_NS = "http://schemas.microsoft.com/developer/vsx-syndication-schema/2010"   # VS 갤러리 확장 이름공간
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"         # xsi:nil
FEED_NAMESPACE_UUID = uuid.UUID("df57f35a-6e02-467f-bc82-4b361c36f3a9")   # 피드 id 를 만들 기준(패키지 GUID)


def read_manifest(vsix_path: pathlib.Path) -> dict:          # VSIX → 매니페스트 정보
    """extension.vsixmanifest 의 Identity(Id·Version·Publisher)·DisplayName·Description."""
    with zipfile.ZipFile(vsix_path) as z:                    # zip 열기
        data = z.read("extension.vsixmanifest")              # 매니페스트(VSIX 빌드가 이 이름으로 넣음)
    root = ET.fromstring(data)                               # XML 해석
    meta = root.find(f"{{{VSX_NS}}}Metadata")                # Metadata
    if meta is None:                                         # 2.0 형식이 아님
        raise ValueError(f"vsixmanifest 2.0 형식이 아닙니다: {vsix_path}")   # 오류
    ident = meta.find(f"{{{VSX_NS}}}Identity")               # Identity
    def text(tag: str) -> str:                               # 자식 글
        el = meta.find(f"{{{VSX_NS}}}{tag}")                 # 요소
        return (el.text or "").strip() if el is not None else ""   # 글(없으면 빈 글)
    return {                                                 # 결과
        "id": ident.get("Id"),                               # 확장 id(업데이트 판정 기준)
        "version": ident.get("Version"),                     # 버전(높으면 업데이트 알림)
        "publisher": ident.get("Publisher") or "",           # 게시자
        "name": text("DisplayName"),                         # 이름
        "description": text("Description"),                  # 설명
        "tags": text("Tags"),                                # 태그
    }


def version_key(v: str) -> tuple:                            # "0.10.2" → (0, 10, 2)
    """숫자 단위로 비교(같은 id 가 여러 개면 가장 높은 버전만 피드에 넣는다)."""
    out = []                                                 # 조각
    for part in (v or "0").split("."):                       # 점으로 나눔
        out.append(int(part) if part.isdigit() else 0)       # 숫자만(아니면 0)
    return tuple(out)                                        # 튜플


def link_for(base: str, file_name: str) -> str:              # 내려받기 링크
    """base 가 UNC(\\\\서버\\공유\\…) 또는 드라이브 경로면 file:// URI, http(s) 면 URL 이어 붙이기."""
    if base.startswith(("http://", "https://")):             # 웹 서버
        return base.rstrip("/") + "/" + file_name            # URL
    full = ntpath.join(base, file_name).replace("/", "\\")   # Windows 경로로 합침(구분자 통일)
    if full.startswith("\\\\"):                                # UNC(\\서버\공유\…)
        return "file://" + urllib.parse.quote(full[2:].replace("\\", "/"), safe="/")   # file://서버/공유/…(한글은 %xx)
    return "file:///" + urllib.parse.quote(full.replace("\\", "/"), safe="/:")   # C:\a\b.vsix → file:///C:/a/b.vsix


def build_feed(entries: list[dict], title: str) -> ET.ElementTree:   # 피드 만들기
    """VS 사설 갤러리 형식(Atom + Vsix 확장 요소)."""
    ET.register_namespace("", ATOM_NS)                       # 기본 이름공간
    ET.register_namespace("xsi", XSI_NS)                     # xsi 접두사
    ET.register_namespace("vsix", SYN_NS)                    # VS 확장 요소 접두사
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # 지금(UTC)
    feed = ET.Element(f"{{{ATOM_NS}}}feed")                  # <feed>
    ET.SubElement(feed, f"{{{ATOM_NS}}}title", {"type": "text"}).text = title   # 갤러리 이름
    ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = "uuid:" + str(uuid.uuid5(FEED_NAMESPACE_UUID, title))   # 고정 id(이름 기준)
    ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = now  # 갱신 시각
    for e in entries:                                        # 확장마다
        entry = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")   # <entry>
        ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = e["id"]   # 확장 id
        ET.SubElement(entry, f"{{{ATOM_NS}}}title", {"type": "text"}).text = e["name"]   # 이름
        ET.SubElement(entry, f"{{{ATOM_NS}}}summary", {"type": "text"}).text = e["description"]   # 설명
        ET.SubElement(entry, f"{{{ATOM_NS}}}published").text = e["updated"]   # 게시 시각(파일 수정 시각)
        ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = e["updated"]     # 갱신 시각
        author = ET.SubElement(entry, f"{{{ATOM_NS}}}author")   # 게시자
        ET.SubElement(author, f"{{{ATOM_NS}}}name").text = e["publisher"]   # 이름
        if e["tags"]:                                        # 태그
            for t in [x.strip() for x in e["tags"].replace(";", ",").split(",") if x.strip()]:   # 하나씩
                ET.SubElement(entry, f"{{{ATOM_NS}}}category", {"term": t})   # 분류
        ET.SubElement(entry, f"{{{ATOM_NS}}}content", {"type": "application/octet-stream", "src": e["link"]})   # VSIX 링크
        vsix = ET.SubElement(entry, f"{{{SYN_NS}}}Vsix")     # VS 확장 정보
        ET.SubElement(vsix, f"{{{SYN_NS}}}Id").text = e["id"]   # id(매니페스트와 같아야 업데이트 판정)
        ET.SubElement(vsix, f"{{{SYN_NS}}}Version").text = e["version"]   # 버전
        ET.SubElement(vsix, f"{{{SYN_NS}}}References")       # 의존 확장 없음
        for tag in ("Rating", "RatingCount", "DownloadCount"):   # 평점 등(없음)
            ET.SubElement(vsix, f"{{{SYN_NS}}}{tag}", {f"{{{XSI_NS}}}nil": "true"})   # xsi:nil
    return ET.ElementTree(feed)                              # 트리


def collect(inputs: list[str]) -> list[pathlib.Path]:        # 입력 → VSIX 파일 목록
    """폴더면 그 안의 *.vsix, 파일이면 그 파일."""
    files: list[pathlib.Path] = []                           # 결과
    for raw in inputs:                                       # 입력마다
        p = pathlib.Path(raw)                                # 경로
        if p.is_dir():                                       # 폴더
            files.extend(sorted(p.glob("*.vsix")))           # 안의 VSIX
        elif p.is_file():                                    # 파일
            files.append(p)                                  # 그대로
        else:                                                # 없음
            raise FileNotFoundError(f"없는 경로: {raw}")     # 오류
    return files                                             # 결과


def main(argv: list[str] | None = None) -> int:              # 진입점
    ap = argparse.ArgumentParser(description="VSIX 폴더 → VS 사설 갤러리 Atom 피드(feed.xml)")   # 인자 해석기
    ap.add_argument("inputs", nargs="+", help="VSIX 파일 또는 VSIX 가 있는 폴더")   # 입력
    ap.add_argument("--base", help="피드에 적을 VSIX 위치(UNC 폴더 또는 http URL). 기본: 첫 입력 폴더")   # 링크 기준
    ap.add_argument("--out", help="출력 파일. 기본: <첫 입력 폴더>\\feed.xml")   # 출력
    ap.add_argument("--title", default="ncn 사설 갤러리", help="갤러리 이름(VS 확장 관리자에 보임)")   # 이름
    args = ap.parse_args(argv)                               # 해석
    files = collect(args.inputs)                             # VSIX 목록
    if not files:                                            # 없음
        print("VSIX 파일이 없습니다.", file=sys.stderr)       # 안내
        return 1                                             # 실패
    first_dir = pathlib.Path(args.inputs[0])                 # 첫 입력
    first_dir = first_dir if first_dir.is_dir() else first_dir.parent   # 폴더로
    base = args.base or str(first_dir.resolve())             # 링크 기준
    best: dict[str, dict] = {}                               # id → 가장 높은 버전 항목
    for f in files:                                          # 파일마다
        m = read_manifest(f)                                 # 매니페스트
        m["link"] = link_for(base, f.name)                   # 링크
        m["updated"] = _dt.datetime.fromtimestamp(f.stat().st_mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # 파일 시각
        cur = best.get(m["id"])                              # 같은 id
        if cur is None or version_key(m["version"]) > version_key(cur["version"]):   # 더 높은 버전
            best[m["id"]] = m                                # 교체
    tree = build_feed(sorted(best.values(), key=lambda e: e["name"]), args.title)   # 피드
    out = pathlib.Path(args.out) if args.out else first_dir / "feed.xml"   # 출력 경로
    if hasattr(ET, "indent"):                                # 3.9+ 면
        ET.indent(tree, space="  ")                          # 보기 좋게 들여쓰기
    tree.write(out, encoding="utf-8", xml_declaration=True)  # 쓰기(UTF-8)
    for e in best.values():                                  # 결과 안내
        print(f"{e['id']} {e['version']} → {e['link']}")     # 한 줄
    print(f"피드: {out}")                                    # 출력 경로
    return 0                                                 # 성공


if __name__ == "__main__":                                   # 직접 실행
    sys.exit(main())                                         # 종료 코드
