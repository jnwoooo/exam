# ncn/tools/fetch.py — Fetch 도구(웹 페이지 → 마크다운). cn extensions/cli/src/tools/fetch.ts
#   + core/tools/implementations/fetchUrlContent.ts + core/context/providers/URLContextProvider.ts(getUrlContextItems)
# cn은 JSDOM(HTML 파싱) → @mozilla/readability 0.6.0(본문 고르기) → node-html-markdown 1.3.0(마크다운 변환)을 쓴다.
# ncn은 표준 라이브러리만으로: html.parser 로 DOM 을 만들고, Readability 는 핵심 절차만 줄여 옮기고(차이는 보고서), node-html-markdown 은 규칙을 그대로 옮겼다
from __future__ import annotations                           # 타입 주석 지연 평가

import html as _html                                         # 엔티티 풀기
import math                                                 # Math.floor 대용(정수 내림)
import os                                                    # 환경변수
import re                                                    # 정규식
from html.parser import HTMLParser                           # HTML 토큰
from typing import Any, Callable, Optional                   # 타입
from urllib.parse import quote, urlsplit, urlunsplit         # URL 정리

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview   # 공용 형식
from ncn.tools.formatters import JS_WS_CHARS, JS_WS_CLASS, js_trim   # JS 공백 규칙
from ncn.tools.truncate import js_len, parse_env_number, truncate_output_from_end   # 자르기

DEFAULT_FETCH_MAX_CHARS = 20000                              # CLI 자르기 한도(CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH)
DEFAULT_FETCH_URL_CHAR_LIMIT = 20000                         # core 쪽 항목별 한도

_LT = "\n\r\u2028\u2029"                                     # JS 정규식의 줄 끝 글자
_BOL = r"(?:(?<![\s\S])|(?<=[" + _LT + r"]))"                # JS /m 의 ^ (문자열 시작 또는 줄 끝 글자 뒤)
_EOL = r"(?=[" + _LT + r"]|(?![\s\S]))"                      # JS /m 의 $
_NS = "[^" + JS_WS_CHARS + "]"                              # JS \S 와 같은 "공백이 아닌 글자" 클래스
_WS_NO_NL = "[" + "".join(c for c in JS_WS_CHARS if c not in "\r\n") + "]"  # JS [^\S\r\n] — 줄바꿈 제외 공백 클래스


# ═════════════════════════ 1. 작은 DOM ═════════════════════════
class Text:                                                 # 글 마디 클래스
    """글 마디(엔티티는 이미 풀린 글)."""
    node_type = 3                                           # DOM nodeType 상수(TEXT_NODE)

    def __init__(self, data: str):                          # 글 마디 생성
        self.data = data                                     # 글
        self.parent: Optional[Element] = None                # 부모

    @property                                               # 속성처럼 읽는 textContent
    def text_content(self) -> str:                          # textContent 접근자
        """textContent."""
        return self.data                                    # 엔티티가 풀린 글 그대로 돌려준다


class Element:                                              # 요소 마디 클래스
    """요소 마디 — tag 는 소문자, attrs 는 이름(소문자) → 값(풀린 글)."""
    node_type = 1                                           # DOM nodeType 상수(ELEMENT_NODE)

    def __init__(self, tag: str, attrs: Optional[dict] = None):  # 요소 생성(태그·속성)
        self.tag = tag                                       # 태그 이름(소문자)
        self.attrs: dict[str, str] = dict(attrs or {})       # 속성
        self.child_nodes: list = []                          # 자식(글·요소)
        self.parent: Optional[Element] = None                # 부모
        self.readability: Optional[dict] = None              # Readability 점수 자리

    @property                                               # 속성처럼 읽는 tagName
    def tag_name(self) -> str:                              # tagName 접근자
        """tagName(대문자)."""
        return self.tag.upper()                             # DOM 의 tagName 은 대문자

    @property                                               # 속성처럼 읽는 children
    def children(self) -> list["Element"]:                  # children 접근자(요소만)
        """children(요소만)."""
        return [c for c in self.child_nodes if isinstance(c, Element)]  # 글 마디를 빼고 요소만 추림

    @property                                               # 속성처럼 읽는 textContent
    def text_content(self) -> str:                          # textContent 접근자
        """textContent — 자손 글을 이어 붙임."""
        out: list[str] = []                                 # 모은 글 조각
        stack = [self]                                       # 반복으로(깊은 문서 대비)
        while stack:                                        # 스택이 빌 때까지 깊이 우선으로 훑음
            n = stack.pop()                                 # 다음 마디를 꺼냄
            if isinstance(n, Text):                         # 글 마디면
                out.append(n.data)                          # 글을 모음
            else:                                           # 요소면
                stack.extend(reversed(n.child_nodes))       # 자식을 뒤집어 넣어 문서 순서대로 꺼내게 함
        return "".join(out)                                 # 조각을 이어 붙여 반환

    def get_attribute(self, name: str) -> Optional[str]:    # 속성 읽기 함수
        """getAttribute(없으면 None)."""
        return self.attrs.get(name.lower())                 # HTML 속성 이름은 대소문자 무시

    def append_child(self, node) -> None:                   # 자식 붙이기
        """appendChild(다른 곳에 있으면 떼어 옮김)."""
        if node.parent is not None:                         # 이미 다른 부모가 있으면
            node.parent.remove_child(node)                  # 그쪽에서 먼저 뗀다
        node.parent = self                                  # 새 부모 지정
        self.child_nodes.append(node)                       # 자식 목록 끝에 추가

    def remove_child(self, node) -> None:                   # 자식 떼기
        """removeChild."""
        self.child_nodes.remove(node)                       # 목록에서 제거
        node.parent = None                                  # 부모 연결 끊기

    def replace_child(self, new, old) -> None:              # 자식 바꿔치기
        """replaceChild(new, old)."""
        if new.parent is not None:                          # 새 마디가 다른 곳에 붙어 있으면
            new.parent.remove_child(new)                    # 먼저 뗀다
        i = self.child_nodes.index(old)                     # 바꿀 자리 위치
        self.child_nodes[i] = new                           # 그 자리에 새 마디를 넣음
        new.parent = self                                   # 새 마디의 부모 지정
        old.parent = None                                   # 옛 마디의 부모 연결 끊기

    def iter(self):                                         # 자기 포함 자손 요소 순회
        """자신 포함 자손 요소(문서 순서)."""
        stack = [self]                                      # 스택에 자신부터 넣음
        while stack:                                        # 스택이 빌 때까지
            n = stack.pop()                                 # 하나 꺼냄
            yield n                                         # 문서 순서대로 내보냄
            stack.extend(reversed(n.children))              # 자식 요소를 뒤집어 넣어 순서 유지

    def get_elements_by_tag(self, *tags: str) -> list["Element"]:  # 태그 이름으로 자손 찾기
        """getElementsByTagName / querySelectorAll(여러 태그) — 자신 제외, 문서 순서."""
        want = set(tags)                                    # 찾을 태그 집합
        return [e for e in self.iter() if e is not self and e.tag in want]  # 자신을 뺀 자손 중 태그가 맞는 것


def _remove(node) -> None:                                  # 마디를 부모에서 떼는 도우미
    """node.remove()."""
    if node.parent is not None:                             # 부모가 있을 때만
        node.parent.remove_child(node)                      # 부모에서 제거


def _next_sibling(node):                                    # 다음 형제 마디(글 포함)
    """nextSibling."""
    p = node.parent                                         # 부모 확인
    if p is None:                                           # 부모가 없으면
        return None                                         # 형제도 없음
    i = p.child_nodes.index(node)                           # 부모 자식 목록에서의 내 위치
    return p.child_nodes[i + 1] if i + 1 < len(p.child_nodes) else None  # 다음 칸이 있으면 그것, 없으면 None


def _next_element_sibling(node):                            # 다음 형제 요소(글 마디 건너뜀)
    """nextElementSibling."""
    p = node.parent                                         # 부모 확인
    if p is None:                                           # 부모가 없으면
        return None                                         # 형제도 없음
    sib = p.child_nodes[p.child_nodes.index(node) + 1:]     # 내 뒤의 형제들
    return next((s for s in sib if isinstance(s, Element)), None)  # 그중 첫 번째 요소(없으면 None)


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr", "keygen"}   # 닫는 태그 없는 요소
_CLOSES_P = {"address", "article", "aside", "blockquote", "details", "dialog", "div", "dl", "fieldset", "figcaption", "figure",  # 열린 <p> 를 닫는 시작 태그 집합(블록 요소 앞부분)
             "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p",  # 위 집합의 뒷부분
             "pre", "section", "table", "ul", "li", "dd", "dt"}   # 열린 <p> 를 닫는 시작 태그(HTML 규칙)
_HEAD_TAGS = {"title", "meta", "link", "style", "script", "base", "noscript", "template"}   # 본문 전이면 head 로


class _TreeBuilder(HTMLParser):                             # HTML 을 Element 나무로 만드는 파서
    """HTML → Element 나무. HTML5 나무 만들기 규칙 중 자주 쓰는 것만(암묵적 html/head/body, <p>·<li>·표 닫기, tbody 넣기)."""

    def __init__(self) -> None:                             # 파서 초기화
        super().__init__(convert_charrefs=True)              # 엔티티 자동 풀기
        self.html = Element("html")                          # 문서 요소
        self.head = Element("head")                         # head 요소
        self.body = Element("body")                         # body 요소
        self.html.append_child(self.head)                   # 문서 요소에 head 를 붙임
        self.html.append_child(self.body)                   # 문서 요소에 body 를 붙임
        self.in_body = False                                 # 본문 시작했나
        self.stack: list[Element] = [self.html, self.head]   # 열린 요소들
        self.skip_lf = False                                 # <pre> 바로 뒤 줄바꿈 하나 버리기

    def _cur(self) -> Element:                              # 현재 요소 도우미
        """지금 넣을 곳."""
        return self.stack[-1]                               # 열린 스택의 맨 위

    def _enter_body(self) -> None:                          # 본문 진입 도우미
        """본문 시작 — head 를 닫고 body 로."""
        if not self.in_body:                                # 아직 본문 전이면
            self.in_body = True                             # 본문 시작으로 표시
            self.stack = [self.html, self.body]             # 스택을 html→body 로 교체(head 닫음)

    def _close_until(self, names: set, stop: set = frozenset()) -> None:  # 이름이 names 인 요소까지 닫는 도우미
        """열린 요소 중 names 를 찾아 거기까지 닫음(stop 을 먼저 만나면 그만)."""
        for i in range(len(self.stack) - 1, 1, -1):         # 스택을 위에서부터 훑음(html·head 는 남김)
            t = self.stack[i].tag                           # 검사 중인 요소의 태그
            if t in stop:                                   # 막는 태그를 먼저 만나면
                return                                      # 닫지 않고 그만둠
            if t in names:                                  # 찾던 태그면
                del self.stack[i:]                          # 거기서부터 위를 모두 닫음
                return                                      # 끝냄

    def handle_starttag(self, tag, attrs):                  # 시작 태그 처리
        tag = tag.lower()                                    # 소문자
        if tag == "html":                                    # 속성만 합침
            for k, v in attrs:                              # html 태그: 속성만 문서 요소에 합침
                self.html.attrs.setdefault(k.lower(), v or "")  # 이미 있는 속성은 유지
            return                                          # 나무에는 안 넣음
        if tag == "head":                                   # head 태그는 이미 만들어 둠
            return                                          # 무시
        if tag == "body":                                    # 속성 합치고 본문으로
            self._enter_body()                              # body 로 진입
            for k, v in attrs:                              # body 속성 합치기
                self.body.attrs.setdefault(k.lower(), v or "")  # 이미 있는 속성은 유지
            return                                          # body 마디는 이미 있음
        if not self.in_body and tag not in _HEAD_TAGS:       # head 에 못 들어가는 것 → 본문 시작
            self._enter_body()                              # head 에 못 들어가는 태그면 본문 시작
        if self.in_body:                                     # 암묵적 닫기
            if tag in _CLOSES_P:                            # 열린 <p> 를 닫아야 하는 태그면
                self._close_until({"p"}, stop={"button", "table", "td", "th", "li"} if tag != "li" else {"ul", "ol", "table", "td", "th"})  # button·표 셀 등은 경계로 보고 <p> 를 닫음
            if tag == "li":                                 # <li> 는
                self._close_until({"li"}, stop={"ul", "ol", "table"})  # 열린 <li> 를 닫음
            if tag in ("dd", "dt"):                         # dd/dt 는
                self._close_until({"dd", "dt"}, stop={"dl", "table"})  # 열린 dd/dt 를 닫음
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._cur().tag in ("h1", "h2", "h3", "h4", "h5", "h6"):  # 제목 안에 제목이 열려 있으면
                self.stack.pop()                            # 바깥 제목을 닫음
            if tag in ("td", "th"):                         # 표 칸이면
                self._close_until({"td", "th"}, stop={"tr", "table"})  # 열린 칸을 닫음
            if tag == "tr":                                 # 행이면
                self._close_until({"tr", "td", "th"}, stop={"table", "tbody", "thead", "tfoot"})  # 열린 행·칸을 닫음
                if self._cur().tag == "table":               # 표 바로 아래 tr → tbody 넣기
                    tb = Element("tbody")                   # 표에 바로 tr 이 오면 tbody 를 암묵적으로 만든다
                    self._cur().append_child(tb)            # tbody 를 현재 요소에 붙임
                    self.stack.append(tb)                   # tbody 를 스택에 올림
            if tag in ("tbody", "thead", "tfoot"):          # tbody 류는
                self._close_until({"tbody", "thead", "tfoot", "tr", "td", "th"}, stop={"table"})  # 열린 행·구역을 닫음
            if tag in ("td", "th") and self._cur().tag in ("table", "tbody", "thead", "tfoot"):   # tr 없이 칸 → tr 넣기
                if self._cur().tag == "table":              # 표 바로 아래에 tr 이면
                    tb = Element("tbody")                   # tbody 를 암묵적으로 만들고
                    self._cur().append_child(tb)            # 표에 붙인 뒤
                    self.stack.append(tb)                   # 스택에 올림
                tr = Element("tr")                          # 행 요소 생성
                self._cur().append_child(tr)                # 현재 요소에 붙임
                self.stack.append(tr)                       # 스택에 올림
            if tag == "option":                             # option 은
                self._close_until({"option"}, stop={"select"})  # 열린 option 을 닫음
            if tag == "a":                                   # a 안의 a 는 앞 a 를 닫음
                self._close_until({"a"})                    # a 가 중첩되면 앞의 a 를 닫음
        el = Element(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})   # 새 요소(같은 이름 속성은 처음 것)
        self._cur().append_child(el)                        # 새 요소를 현재 위치에 붙임
        if tag not in _VOID:                                 # 닫는 태그가 있는 것만 열어 둠
            self.stack.append(el)                           # 빈 요소가 아니면 열린 스택에 올림
            if tag in ("pre", "textarea", "listing"):        # 시작 직후 줄바꿈 하나 무시(HTML 규칙)
                self.skip_lf = True                         # <pre> 직후 첫 줄바꿈은 버리도록 표시

    def handle_startendtag(self, tag, attrs):               # <tag/> 형태 처리
        self.handle_starttag(tag, attrs)                     # <x/> — HTML 에서는 그냥 시작 태그
        if tag.lower() not in _VOID and self._cur().tag == tag.lower():  # 빈 요소가 아닌데 방금 연 같은 태그면(자기닫힘 무시)
            self.stack.pop()                                 # (외부 SVG 등) 바로 닫음

    def handle_endtag(self, tag):                           # 끝 태그 처리
        tag = tag.lower()                                   # 소문자로
        if tag in ("html", "body", "head"):                  # 무시(끝까지 받음)
            if tag == "head" and not self.in_body:          # head 끝 태그는
                pass                                        # 본문 전이면 할 일 없음
            return                                          # 그냥 반환
        if tag == "br":                                      # </br> 은 <br> 로(HTML 규칙)
            self.handle_starttag("br", [])                  # 짝 없는 </br> 은 <br> 로 취급
            return                                          # 끝
        for i in range(len(self.stack) - 1, 1, -1):          # 가장 가까운 같은 요소까지 닫기
            if self.stack[i].tag == tag:                    # 스택을 위에서부터 찾아 같은 태그면
                del self.stack[i:]                          # 그 위까지 모두 닫음
                return                                      # 끝냄

    def handle_data(self, data):                            # 글 처리
        if self.skip_lf:                                     # <pre> 뒤 첫 줄바꿈
            self.skip_lf = False                            # <pre> 직후 플래그를 내림
            if data.startswith("\n"):                       # 앞머리 줄바꿈 하나는
                data = data[1:]                             # 버린다
            if not data:                                    # 남은 글이 없으면
                return                                      # 무시
        if not self.in_body:                                 # head 안
            if self._cur().tag in ("title", "style", "script", "noscript", "template"):  # head 안의 title·style 등은 글을 그대로 붙임
                self._cur().append_child(Text(data))        # 글 마디로 붙임
                return                                      # 끝냄
            if not data.strip(JS_WS_CHARS):                  # 공백은 버림
                return                                      # 본문 전의 공백뿐인 글은 버림
            self._enter_body()                               # 글이 나오면 본문 시작
        cur = self._cur()                                   # 현재 요소
        if cur.child_nodes and isinstance(cur.child_nodes[-1], Text):   # 이웃 글은 합침(DOM 과 같게)
            cur.child_nodes[-1].data += data                # 직전 자식이 글이면 이어 붙임
        else:                                               # 아니면
            cur.append_child(Text(data))                    # 새 글 마디로 붙임

    def handle_comment(self, data):                         # 주석은 DOM 에 넣지 않고 버림
        pass                                                 # 주석 버림(Readability·NHM 모두 영향 없음)


def parse_html(html: str) -> _TreeBuilder:                  # HTML 문자열을 파싱해 나무 반환
    """HTML 글 → 문서(html/head/body)."""
    b = _TreeBuilder()                                      # 파서 생성
    try:                                                    # 파싱 시도
        b.feed(html)                                        # 데이터 투입
        b.close()                                           # 끝까지 처리
    except Exception:                                        # 깨진 문서도 거기까지
        pass                                                # 깨진 HTML 은 가능한 데까지만 쓴다
    return b                                                # 만들어진 나무 반환


# ═════════════════════════ 2. Readability(축약 이식) ═════════════════════════
_RX = {                                                      # REGEXPS(cn이 쓰는 0.6.0 원문)
    "unlikely": re.compile(r"-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|yom-remote", re.I),  # 광고·잡동사니로 보이는 클래스/id 패턴(Readability REGEXPS.unlikelyCandidates)
    "maybe": re.compile(r"and|article|body|column|content|main|shadow", re.I),  # 후보로 되살리는 패턴(okMaybeItsACandidate)
    "positive": re.compile(r"article|body|content|entry|hentry|h-entry|main|page|pagination|post|text|blog|story", re.I),  # 본문일 법한 이름 패턴(positive)
    "negative": re.compile(r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|gdpr|masthead|media|meta|outbrain|promo|related|scroll|share|shoutbox|sidebar|skyscraper|sponsor|shopping|tags|widget", re.I),  # 본문이 아닐 법한 이름 패턴(negative)
    "byline": re.compile(r"byline|author|dateline|writtenby|p-author", re.I),  # 글쓴이 표시 패턴(byline)
    "normalize": re.compile(JS_WS_CLASS + r"{2,}"),         # 연속 공백 정리용(normalize)
    "share": re.compile(r"(\b|_)(share|sharedaddy)(\b|_)", re.I),  # 공유 버튼 패턴(shareElements)
    "hash": re.compile(r"^#[\s\S]+"),                       # 해시 링크 패턴(hashUrl)
    "commas": re.compile(",|،|﹐|︐|︑|⹁|⸴|⸲|，"),              # 여러 언어의 쉼표 모음(commas)
    "tokenize": re.compile(r"[^A-Za-z0-9_]+"),              # 단어 나누기용(tokenize)
}
_UNLIKELY_ROLES = ["menu", "menubar", "complementary", "navigation", "alert", "alertdialog", "dialog"]  # 의미상 본문이 아닌 ARIA role 목록
_DIV_TO_P = {"BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"}  # <div> 를 <p> 로 바꿀지 판단할 때 자식으로 치는 블록 태그
_ALTER_TO_DIV_EXC = ["DIV", "ARTICLE", "SECTION", "P", "OL", "UL"]  # div 로 바꾸지 않는 예외 태그
_PHRASING = {"ABBR", "AUDIO", "B", "BDO", "BR", "BUTTON", "CITE", "CODE", "DATA", "DATALIST", "DFN", "EM", "EMBED", "I", "IMG",  # 구문(phrasing) 요소 집합 앞부분
             "INPUT", "KBD", "LABEL", "MARK", "MATH", "METER", "NOSCRIPT", "OBJECT", "OUTPUT", "PROGRESS", "Q", "RUBY", "SAMP",  # 구문 요소 집합 중간
             "SCRIPT", "SELECT", "SMALL", "SPAN", "STRONG", "SUB", "SUP", "TEXTAREA", "TIME", "VAR", "WBR"}  # 구문 요소 집합 뒷부분
_TAGS_TO_SCORE = {"SECTION", "H2", "H3", "H4", "H5", "H6", "P", "TD", "PRE"}  # 점수를 매길 후보 태그
_VIDEO_RX = re.compile(r"\/\/(www\.)?((dailymotion|youtube|youtube-nocookie|player\.vimeo|v\.qq)\.com|(archive|upload\.wikimedia)\.org|player\.twitch\.tv)", re.I)  # 동영상 사이트 URL 패턴(videos)


def _cls(e: Element) -> str:                                # 클래스 속성 읽기 도우미
    """className."""
    return e.get_attribute("class") or ""                   # 없으면 빈 문자열


def _id(e: Element) -> str:                                 # id 속성 읽기 도우미
    """id."""
    return e.get_attribute("id") or ""                      # 없으면 빈 문자열


class _Readability:                                         # Readability 핵심 절차 클래스
    """Readability 0.6.0 의 parse() 핵심: 스크립트·숨김·광고성 요소 제거 → 점수로 본문 고르기 → 정리.
    빠진 것: JSON-LD 메타데이터, _cleanConditionally(표·목록·div 조건부 제거), _markDataTables, _fixLazyImages, _unwrapNoscriptImages."""

    F_STRIP, F_WEIGHT, F_CLEAN = 1, 2, 4                     # 플래그

    def __init__(self, doc: _TreeBuilder):                  # Readability 초기화
        self.doc = doc                                       # 문서
        self.flags = 7                                       # 모두 켜고 시작
        self.title = ""                                      # 제목
        self.byline: Optional[str] = None                    # 작성자
        self.meta_byline: Optional[str] = None               # 메타 작성자
        self.attempts: list = []                             # 시도들

    # ── 도우미 ──
    def _active(self, f: int) -> bool:                      # 플래그 켜짐 여부 확인 도우미
        return (self.flags & f) > 0                         # 비트 AND 로 해당 플래그 검사

    def _inner_text(self, e, normalize: bool = True) -> str:  # 이 요소의 글(기본은 공백 정리)
        """_getInnerText — trim 후 공백 2개 이상을 하나로."""
        t = js_trim(e.text_content)                         # 양끝 공백을 JS trim 규칙으로 제거
        return _RX["normalize"].sub(" ", t) if normalize else t  # normalize 면 연속 공백을 한 칸으로

    def _link_density(self, e: Element) -> float:           # 링크 글자 비율(링크 밀도) 계산
        """_getLinkDensity — 링크 글 길이 / 전체 글 길이(#링크는 0.3배)."""
        tl = js_len(self._inner_text(e))                    # 전체 글자 수
        if tl == 0:                                         # 글이 없으면
            return 0                                        # 밀도 0 으로 본다
        ll = 0.0                                            # 링크 글자 수 합계
        for a in e.get_elements_by_tag("a"):                # 모든 a 요소에 대해
            href = a.get_attribute("href")                  # href 값
            coef = 0.3 if href and _RX["hash"].search(href) else 1  # # 해시 링크는 0.3 배로 쳐줌
            ll += js_len(self._inner_text(a)) * coef        # 링크 글자 수를 계수 곱해 누적
        return ll / tl                                      # 링크 글자/전체 글자

    def _class_weight(self, e: Element) -> int:             # 클래스·id 이름으로 가중치 계산
        """_getClassWeight — class·id 에 positive/negative 가 있으면 ±25."""
        if not self._active(self.F_WEIGHT):                 # 가중치 플래그가 꺼져 있으면
            return 0                                        # 0 점
        w = 0                                               # 누적 점수
        for s in (_cls(e), _id(e)):                         # class 와 id 각각에 대해
            if s:                                           # 비어 있지 않으면
                if _RX["negative"].search(s):               # 본문 아닐 법한 이름이면
                    w -= 25                                 # 25 점 깎음
                if _RX["positive"].search(s):               # 본문일 법한 이름이면
                    w += 25                                 # 25 점 더함
        return w                                            # 가중치 반환

    def _init_node(self, e: Element) -> None:               # 후보 요소 점수판 초기화
        """_initializeNode — 태그별 기본 점수 + class 가중치."""
        e.readability = {"contentScore": 0}                 # Readability 점수판 생성(기본 0)
        t = e.tag_name                                      # 태그 이름(대문자)
        if t == "DIV":                                      # DIV 는
            e.readability["contentScore"] += 5              # +5 점
        elif t in ("PRE", "TD", "BLOCKQUOTE"):              # PRE·TD·BLOCKQUOTE 는
            e.readability["contentScore"] += 3              # +3 점
        elif t in ("ADDRESS", "OL", "UL", "DL", "DD", "DT", "LI", "FORM"):  # 목록·폼류는
            e.readability["contentScore"] -= 3              # -3 점
        elif t in ("H1", "H2", "H3", "H4", "H5", "H6", "TH"):  # 제목류·TH 는
            e.readability["contentScore"] -= 5              # -5 점
        e.readability["contentScore"] += self._class_weight(e)  # 클래스·id 가중치를 더함

    @staticmethod                                           # 정적 메서드
    def _next_node(node: Element, ignore_self_and_kids: bool = False):  # 문서 순서로 다음 요소 찾기
        """_getNextNode — 깊이 우선 다음 요소."""
        if not ignore_self_and_kids and node.children:      # 자식이 있고 건너뛰지 않으면 첫 자식으로
            return node.children[0]                         # 첫 자식 반환
        n = _next_element_sibling(node)                     # 다음 형제 요소
        if n:                                               # 있으면
            return n                                        # 그것 반환
        while True:                                         # 형제가 없으면 위로 올라가며 찾음
            node = node.parent                              # 부모로 이동
            if node is None:                                # 루트를 넘으면
                return None                                 # 더는 없음
            n = _next_element_sibling(node)                 # 부모의 다음 형제
            if n:                                           # 있으면
                return n                                    # 그것 반환

    def _remove_and_next(self, node: Element):              # 노드 제거 후 다음 노드 반환
        """_removeAndGetNext."""
        nxt = self._next_node(node, True)                   # 자식은 건너뛰고 다음 노드를 미리 구함
        _remove(node)                                       # 지금 노드 제거
        return nxt                                          # 미리 구한 다음 노드 반환

    @staticmethod                                           # 정적 메서드
    def _set_tag(node: Element, tag: str) -> Element:       # 태그 이름 바꾸기(setNodeTag)
        """_setNodeTag — 같은 자리에 새 태그 요소(자식·속성·점수 옮김)."""
        rep = Element(tag.lower(), node.attrs)              # 새 태그로 요소 생성(속성 복사)
        rep.readability = node.readability                  # 점수판 이어받음
        for c in list(node.child_nodes):                    # 자식 목록을 복사해 순회
            rep.append_child(c)                             # 자식을 새 요소로 옮김
        if node.parent is not None:                         # 원래 요소가 부모에 붙어 있으면
            node.parent.replace_child(rep, node)            # 새 요소로 자리 바꿈
        return rep                                          # 새 요소 반환

    def _is_phrasing(self, n) -> bool:                      # 구문(phrasing) 요소인지 판단
        """_isPhrasingContent."""
        if isinstance(n, Text):                             # 글 마디면
            return True                                     # 구문이다
        if n.tag_name in _PHRASING:                         # 구문 태그 목록에 있으면
            return True                                     # 구문이다
        return n.tag_name in ("A", "DEL", "INS") and all(self._is_phrasing(c) for c in n.child_nodes)  # A·DEL·INS 는 자식이 모두 구문일 때만 구문

    @staticmethod                                           # 정적 메서드
    def _is_ws(n) -> bool:                                  # 공백뿐인 마디인지 판단
        """_isWhitespace — 빈 글 또는 BR."""
        if isinstance(n, Text):                             # 글 마디면
            return len(js_trim(n.data)) == 0                # 트림해 비었는지로 판단
        return n.tag_name == "BR"                           # 아니면 BR 만 공백 취급

    @staticmethod                                           # 정적 메서드
    def _has_single_tag(e: Element, tag: str) -> bool:      # 요소가 해당 태그 하나만 가졌는지 판단
        """_hasSingleTagInsideElement."""
        ch = e.children                                     # 자식 요소들
        if len(ch) != 1 or ch[0].tag_name != tag:           # 하나가 아니거나 태그가 다르면
            return False                                    # 아니다
        return not any(isinstance(n, Text) and re.search(_NS + r"$", n.data) for n in e.child_nodes)  # 자식 사이에 의미 있는 글(공백 아닌 끝 글자)이 없어야 함(/\S$/)

    def _has_child_block(self, e) -> bool:                  # 블록 자식을 가졌는지 재귀 판단
        """_hasChildBlockElement."""
        return any(isinstance(n, Element) and (n.tag_name in _DIV_TO_P or self._has_child_block(n)) for n in e.child_nodes)  # 자손 중 DIV_TO_P 태그가 하나라도 있으면 참

    @staticmethod                                           # 정적 메서드
    def _without_content(n: Element) -> bool:               # 내용 없는 요소인지 판단
        """_isElementWithoutContent."""
        if len(js_trim(n.text_content)):                    # 글이 있으면
            return False                                    # 내용 있음
        ch = n.children                                     # 자식 요소들
        return not ch or len(ch) == len(n.get_elements_by_tag("br")) + len(n.get_elements_by_tag("hr"))  # 자식이 없거나 전부 br·hr 뿐이면 내용 없음

    @staticmethod                                           # 정적 메서드
    def _has_ancestor(node, tag: str, max_depth: int = 3) -> bool:  # 조상 중 특정 태그가 있는지 확인
        """_hasAncestorTag(node, tag) — 기본 깊이 3."""
        tag = tag.upper()                                   # 비교용 대문자
        depth = 0                                           # 올라간 깊이
        while node.parent is not None:                      # 부모가 있는 동안
            if max_depth > 0 and depth > max_depth:         # 깊이 제한을 넘으면
                return False                                # 없다고 본다
            if node.parent.tag_name == tag:                 # 부모가 찾는 태그면
                return True                                 # 있다
            node = node.parent                              # 한 칸 위로
            depth += 1                                      # 깊이 증가
        return False                                        # 끝까지 없었음

    @staticmethod                                           # 정적 메서드
    def _visible(n: Element) -> bool:                       # 눈에 보이는 요소인지 판단
        """_isProbablyVisible — style display:none / visibility:hidden, hidden, aria-hidden=true(fallback-image 제외)."""
        style = (n.get_attribute("style") or "").lower()    # style 속성(소문자)
        decl = {}                                           # 파싱한 css 선언
        for part in style.split(";"):                       # 세미콜론으로 선언 분리
            if ":" in part:                                 # 콜론이 있는 것만
                k, v = part.split(":", 1)                   # 속성:값 분리
                decl[k.strip()] = v.strip()                 # 공백 제거해 저장
        if decl.get("display") == "none" or decl.get("visibility") == "hidden":  # display:none 이나 visibility:hidden 이면
            return False                                    # 숨김
        if "hidden" in n.attrs:                             # hidden 속성이 있으면
            return False                                    # 숨김
        if n.get_attribute("aria-hidden") == "true" and "fallback-image" not in _cls(n):  # aria-hidden 이면(단 fallback-image 는 예외)
            return False                                    # 숨김
        return True                                         # 그 외엔 보임

    def _text_similarity(self, a: str, b: str) -> float:    # 두 글의 단어 유사도(0~1)
        """_textSimilarity."""
        ta = [t for t in _RX["tokenize"].split(a.lower()) if t]  # a 의 단어 목록
        tb = [t for t in _RX["tokenize"].split(b.lower()) if t]  # b 의 단어 목록
        if not ta or not tb:                                # 한쪽이 비면
            return 0                                        # 유사도 0
        uniq = [t for t in tb if t not in ta]               # b 에만 있는 단어
        return 1 - len(" ".join(uniq)) / len(" ".join(tb))  # b 중 겹치지 않는 글자 비율을 빼서 유사도로

    def _header_dup_title(self, n: Element) -> bool:        # 제목과 중복되는 머리글인지 판단
        """_headerDuplicatesTitle."""
        if n.tag_name not in ("H1", "H2"):                  # H1·H2 가 아니면
            return False                                    # 해당 없음
        return self._text_similarity(self.title, self._inner_text(n, False)) > 0.75  # 문서 제목과 75% 넘게 비슷하면 중복

    def _valid_byline(self, n: Element, match: str) -> bool:  # 글쓴이 표시로 쓸 만한지 판단
        """_isValidByline."""
        rel = n.get_attribute("rel")                        # rel 속성
        itemprop = n.get_attribute("itemprop")              # itemprop 속성
        ln = js_len(js_trim(n.text_content))                # 글자 수
        return (rel == "author" or bool(itemprop and "author" in itemprop) or bool(_RX["byline"].search(match))) and bool(ln) and ln < 100  # author 표시이면서 길이가 100 미만(1 이상)일 때 유효

    # ── 제목(_getArticleTitle + 메타 일부) ──
    def _article_title(self) -> str:                        # 문서 제목 고르기(getArticleTitle)
        """메타(og:title·twitter:title·dc:title 등) → 없으면 <title> 규칙."""
        metas = {}                                          # 메타 태그 모음
        for m in self.doc.html.get_elements_by_tag("meta"):  # 모든 meta 요소에 대해
            content = m.get_attribute("content")            # content 값
            if not content:                                 # 없으면
                continue                                    # 건너뜀
            for key in ((m.get_attribute("property") or "") + " " + (m.get_attribute("name") or "")).split():  # property·name 속성의 각 키에 대해
                k = key.lower().replace(".", ":")           # 키를 소문자로 정규화
                metas[k] = js_trim(content)                 # 메타 값 저장
        for k in ("dc:title", "dcterm:title", "og:title", "weibo:article:title", "weibo:webpage:title", "title", "twitter:title", "parsely-title"):  # 제목으로 쓸 만한 메타 키를 우선순위대로
            if metas.get(k):                                # 값이 있으면
                self._set_meta_byline(metas)                # 글쓴이 메타도 기록
                return metas[k]                             # 그 값을 제목으로
        self._set_meta_byline(metas)                        # 메타 제목이 없으면 글쓴이만 기록
        titles = self.doc.html.get_elements_by_tag("title")  # title 요소들
        orig = js_trim(titles[0].text_content) if titles else ""  # 원래 제목
        cur = orig                                          # 손질할 제목
        had_hier = False                                    # 계층 구분자 사용 여부

        def wc(s: str) -> int:                              # 단어 수 세는 내부 함수
            return len(re.split(JS_WS_CLASS + "+", s))      # 공백으로 쪼개 개수
        if re.search(r" [\|\-\\\/>»] ", cur):               # 구분자(| - \ / > »)가 있으면
            had_hier = bool(re.search(r" [\\\/>»] ", cur))  # 계층 구분자(\ / > »)가 있었는지 기록
            seps = list(re.finditer(r" [\|\-\\\/>»] ", orig))  # 구분자 위치들
            cur = orig[: seps[-1].start()]                  # 마지막 구분자 앞까지만 남김
            if wc(cur) < 3:                                 # 단어가 3 개 미만이면
                cur = re.sub(r"^[^\|\-\\\/>»]*[\|\-\\\/>»]", "", orig)  # 첫 구분자 뒤를 쓰는 식으로 다시 자름
        elif ": " in cur:                                   # 콜론이 있으면
            hs = self.doc.html.get_elements_by_tag("h1", "h2")  # h1·h2 요소들
            if not any(js_trim(h.text_content) == js_trim(cur) for h in hs):  # 콜론 포함 제목이 머리글에 그대로 있지 않으면
                cur = orig[orig.rfind(":") + 1:]            # 마지막 콜론 뒤를 취함
                if wc(cur) < 3:                             # 단어가 3 개 미만이면
                    cur = orig[orig.find(":") + 1:]         # 첫 콜론 뒤를 취함
                elif wc(orig[: orig.find(":")]) > 5:        # 콜론 앞이 5 단어 넘으면
                    cur = orig                              # 원래 제목 유지
        elif js_len(cur) > 150 or js_len(cur) < 15:         # 제목이 너무 길거나 짧으면
            h1s = self.doc.html.get_elements_by_tag("h1")   # h1 요소들
            if len(h1s) == 1:                               # h1 이 하나뿐이면
                cur = self._inner_text(h1s[0])              # 그 글을 제목으로
        cur = _RX["normalize"].sub(" ", js_trim(cur))       # 연속 공백 정리
        n = wc(cur)                                         # 단어 수
        if n <= 4 and (not had_hier or n != wc(re.sub(r"[\|\-\\\/>»]+", "", orig)) - 1):  # 너무 짧으면(4 이하) 원래 제목으로 되돌림
            cur = orig                                      # 원래 제목 사용
        return cur                                          # 최종 제목 반환

    def _set_meta_byline(self, metas: dict) -> None:        # 메타에서 글쓴이 기록
        """메타 작성자(dc:creator·author 등) — 있으면 본문의 작성자 요소를 지우지 않음."""
        for k in ("dc:creator", "dcterm:creator", "author", "parsely-author"):  # 글쓴이 메타 키를 순서대로
            if metas.get(k):                                # 값이 있으면
                self.meta_byline = metas[k]                 # 글쓴이로 저장
                return                                      # 하나 찾으면 끝

    # ── 문서 준비 ──
    def _prep_document(self) -> None:                       # 문서 전처리(prepDocument)
        """_removeScripts + _prepDocument(style 제거, <br><br> → <p>, font → span)."""
        for e in self.doc.html.get_elements_by_tag("script", "noscript", "style"):  # script·noscript·style 제거
            _remove(e)                                      # 요소 제거
        self._replace_brs(self.doc.body)                    # 연속 br 을 문단으로 바꿈
        for f in self.doc.html.get_elements_by_tag("font"):  # font 요소를
            self._set_tag(f, "span")                        # span 으로 바꿈

    def _skip_ws(self, n):                                  # 공백 글 마디 건너뛰기
        """_nextNode — 공백 글은 건너뛴 다음 마디."""
        while n is not None and not isinstance(n, Element) and not js_trim(n.text_content):  # 글 마디 중 비어 있으면 계속 다음으로
            n = _next_sibling(n)                            # 다음 형제로 이동
        return n                                            # 첫 유의미한 마디 반환

    def _replace_brs(self, elem: Element) -> None:          # 연속된 <br> 을 <p> 로 바꿈
        """_replaceBrs — 연속 <br> 두 개 이상을 <p> 로."""
        for br in elem.get_elements_by_tag("br"):           # 모든 br 에 대해
            if br.parent is None:                           # 이미 떼어졌으면
                continue                                    # 건너뜀
            nxt = _next_sibling(br)                         # 바로 다음 형제
            replaced = False                                # 바꿨는지 여부
            while True:                                     # 뒤따르는 br 들을 찾음
                nxt = self._skip_ws(nxt)                    # 공백 글 건너뜀
                if not (nxt is not None and isinstance(nxt, Element) and nxt.tag_name == "BR"):  # 다음이 br 이 아니면
                    break                                   # 그만 찾음
                replaced = True                             # 연속 br 이 있다고 표시
                sib = _next_sibling(nxt)                    # 다음 형제 기억
                _remove(nxt)                                # 중복 br 제거
                nxt = sib                                   # 다음으로 이동
            if replaced:                                    # 연속 br 이 있었다면
                p = Element("p")                            # 새 문단 생성
                br.parent.replace_child(p, br)              # 첫 br 을 문단으로 교체
                nxt = _next_sibling(p)                      # 문단 다음 형제부터 문단에 담음
                while nxt is not None:                      # 형제가 남아 있는 동안
                    if isinstance(nxt, Element) and nxt.tag_name == "BR":  # br 이면
                        ne = self._skip_ws(_next_sibling(nxt))  # 그 뒤 공백을 건너뛴 다음 마디
                        if ne is not None and isinstance(ne, Element) and ne.tag_name == "BR":  # 또 br 이면(문단 끝)
                            break                           # 담기를 멈춤
                    if not self._is_phrasing(nxt):          # 구문 요소가 아니면
                        break                               # 담기를 멈춤
                    sib = _next_sibling(nxt)                # 다음 형제 기억
                    p.append_child(nxt)                     # 문단 안으로 옮김
                    nxt = sib                               # 다음으로 이동
                while p.child_nodes and self._is_ws(p.child_nodes[-1]):  # 문단 끝의 공백 마디는
                    _remove(p.child_nodes[-1])              # 제거
                if p.parent is not None and p.parent.tag_name == "P":  # 부모가 P 이면(p 안의 p 방지)
                    self._set_tag(p.parent, "div")          # 부모를 div 로 바꿈

    # ── 본문 고르기(_grabArticle) ──
    def _grab(self) -> Optional[Element]:                   # 본문 후보 고르기 핵심(grabArticle) — 실패하면 플래그를 줄여 재시도
        import copy                                          # 실패 시 원래 본문으로 되돌리기
        body = self.doc.body                                # body 요소
        page_cache = copy.deepcopy(body.child_nodes)         # page.innerHTML 저장 대응
        while True:                                         # 플래그를 줄여가며 재시도하는 바깥 루프
            strip = self._active(self.F_STRIP)              # 이번 시도에서 의심 후보 제거를 쓸지
            to_score: list[Element] = []                    # 점수를 매길 요소들
            node = self.doc.html                            # html 부터 문서 순서로 훑음
            remove_title_header = True                      # 첫 머리글 중복 제거 여부
            while node is not None:                         # 노드가 있는 동안
                match = _cls(node) + " " + _id(node)        # class+id 문자열
                if not self._visible(node):                  # 숨김
                    node = self._remove_and_next(node)      # 숨겨진 요소면 지우고 다음으로
                    continue                                # 다음 노드로
                if node.get_attribute("aria-modal") == "true" and node.get_attribute("role") == "dialog":  # aria-modal 대화상자면
                    node = self._remove_and_next(node)      # 지우고 다음으로
                    continue                                # 다음 노드로
                if not self.byline and not self.meta_byline and self._valid_byline(node, match):   # 작성자 요소
                    self.byline = js_trim(node.text_content)  # 글쓴이 표시면 글쓴이로 저장
                    node = self._remove_and_next(node)      # 지우고 다음으로
                    continue                                # 다음 노드로
                if remove_title_header and self._header_dup_title(node):   # 제목과 같은 머리글
                    remove_title_header = False             # 제목과 중복된 머리글이면 지우고 한 번만 처리
                    node = self._remove_and_next(node)      # 지우고 다음으로
                    continue                                # 다음 노드로
                if strip:                                    # 광고·메뉴 등
                    if (_RX["unlikely"].search(match) and not _RX["maybe"].search(match) and not self._has_ancestor(node, "table")  # 광고 등 의심 후보면(표·코드 안, BODY·A 는 제외)
                            and not self._has_ancestor(node, "code") and node.tag_name not in ("BODY", "A")):  # 위 조건의 이어지는 부분
                        node = self._remove_and_next(node)  # 지우고 다음으로
                        continue                            # 다음 노드로
                    if node.get_attribute("role") in _UNLIKELY_ROLES:  # 본문 아닌 ARIA role 이면
                        node = self._remove_and_next(node)  # 지우고 다음으로
                        continue                            # 다음 노드로
                if node.tag_name in ("DIV", "SECTION", "HEADER", "H1", "H2", "H3", "H4", "H5", "H6") and self._without_content(node):  # 내용 없는 컨테이너·머리글이면
                    node = self._remove_and_next(node)      # 지우고 다음으로
                    continue                                # 다음 노드로
                if node.tag_name in _TAGS_TO_SCORE:         # 점수를 매길 태그면
                    to_score.append(node)                   # 후보 목록에 추가
                if node.tag_name == "DIV":                   # div 안의 글 덩어리를 p 로 묶기
                    p = None                                # DIV 는 안의 내용을 문단으로 감쌈
                    child = node.child_nodes[0] if node.child_nodes else None  # 첫 자식
                    while child is not None:                # 자식을 차례로 훑음
                        ns = _next_sibling(child)           # 다음 형제 기억
                        if self._is_phrasing(child):        # 구문 요소면
                            if p is not None:               # 열린 문단이 있으면
                                p.append_child(child)       # 문단에 담음
                            elif not self._is_ws(child):    # 공백이 아니면
                                p = Element("p")            # 새 문단을 열고
                                node.replace_child(p, child)  # 그 자리에 문단 삽입
                                p.append_child(child)       # 자식을 문단에 담음
                        elif p is not None:                 # 구문이 아니고 열린 문단이 있으면
                            while p.child_nodes and self._is_ws(p.child_nodes[-1]):  # 문단 끝 공백 마디를
                                _remove(p.child_nodes[-1])  # 제거
                            p = None                        # 문단을 닫음
                        child = ns                          # 다음 자식으로
                    if self._has_single_tag(node, "P") and self._link_density(node) < 0.25:  # P 하나뿐이고 링크가 적으면 div 를 그 P 로 대체
                        nn = node.children[0]               # 그 P
                        node.parent.replace_child(nn, node)  # div 자리에 P 를 넣음
                        node = nn                           # P 를 현재 노드로
                        to_score.append(node)               # 점수 대상에 추가
                    elif not self._has_child_block(node):   # 블록 자식이 없으면
                        node = self._set_tag(node, "p")     # div 를 p 로 바꿈
                        to_score.append(node)               # 점수 대상에 추가
                node = self._next_node(node)                # 문서 순서로 다음 노드

            candidates: list[Element] = []                   # 점수 매기기
            for el in to_score:                             # 점수 대상마다 조상에게 점수 전파
                if el.parent is None:                       # 이미 떼어졌으면
                    continue                                # 건너뜀
                inner = self._inner_text(el)                # 글
                if js_len(inner) < 25:                      # 25자 미만이면
                    continue                                # 무시
                ancestors = []                              # 점수를 나눠줄 조상(최대 5 단계)
                a = el                                      # 올라가기 시작점
                while a.parent is not None and len(ancestors) < 5:  # 부모가 있고 5 단계 미만인 동안
                    ancestors.append(a.parent)              # 부모 추가
                    a = a.parent                            # 한 칸 위로
                if not ancestors:                           # 조상이 없으면
                    continue                                # 건너뜀
                score = 1 + len(_RX["commas"].split(inner)) + min(math.floor(js_len(inner) / 100), 3)  # 기본 1 + 쉼표 수 + 길이 보너스(최대 3)
                for level, anc in enumerate(ancestors):     # 조상마다 레벨에 따라
                    if anc.parent is None:                  # 루트 위면
                        continue                            # 건너뜀
                    if anc.readability is None:             # 점수판이 없으면
                        self._init_node(anc)                # 초기화하고
                        candidates.append(anc)              # 후보에 추가
                    div = 1 if level == 0 else (2 if level == 1 else level * 3)  # 레벨별 나눔수: 0→1, 1→2, 그 이상은 레벨×3
                    anc.readability["contentScore"] += score / div  # 점수를 나눠 더함

            top: list[Element] = []                          # 상위 5개
            for c in candidates:                            # 후보마다 링크 밀도로 점수 감쇠
                s = c.readability["contentScore"] * (1 - self._link_density(c))  # 점수 × (1 - 링크 밀도)
                c.readability["contentScore"] = s           # 감쇠한 점수 저장
                for t in range(5):                          # 상위 5 개 후보 목록 유지
                    if t >= len(top) or s > top[t].readability["contentScore"]:  # 자리가 있거나 기존보다 높으면
                        top.insert(t, c)                    # 그 위치에 삽입
                        if len(top) > 5:                    # 5 개 넘으면
                            top.pop()                       # 꼴찌 제거
                        break                               # 삽입했으면 끝
            tc = top[0] if top else None                    # 최고 점수 후보
            created = False                                 # 새로 만든 후보인지
            if tc is None or tc.tag_name == "BODY":          # 후보 없음 → 본문 전체
                tc = Element("div")                         # 후보가 없으면 body 내용을 새 div 로 감쌈
                created = True                              # 새로 만들었다고 표시
                for ch in list(body.child_nodes):           # body 의 모든 자식을
                    tc.append_child(ch)                     # 새 div 로 옮기고
                body.append_child(tc)                       # div 를 body 에 붙임
                self._init_node(tc)                         # 점수판 초기화
            else:                                           # 후보가 있으면
                alts = [self._ancestors(t) for t in top[1:] if t.readability["contentScore"] / tc.readability["contentScore"] >= 0.75] if tc.readability["contentScore"] else []  # 최고 점수의 75% 이상인 다른 후보들의 조상 목록
                if len(alts) >= 3:                          # 비슷한 후보가 3 개 이상이면
                    pt = tc.parent                          # 최고 후보의 부모부터
                    while pt is not None and pt.tag_name != "BODY":  # BODY 에 닿을 때까지
                        if sum(1 for al in alts if pt in al) >= 3:  # 3 개 이상 후보를 품은 조상이면
                            tc = pt                         # 그 조상을 선택
                            break                           # 끝냄
                        pt = pt.parent                      # 위로 올라감
                if tc.readability is None:                  # 점수판이 없으면
                    self._init_node(tc)                     # 초기화
                pt = tc.parent                              # 부모부터 점수 상승 여부를 확인
                last = tc.readability["contentScore"]       # 마지막 점수
                thr = last / 3                              # 임계값 = 마지막 점수의 1/3
                while pt is not None and pt.tag_name != "BODY":  # BODY 에 닿을 때까지
                    if pt.readability is None:              # 점수판이 없는 부모는
                        pt = pt.parent                      # 건너뛰고 위로
                        continue                            # 다음으로
                    ps = pt.readability["contentScore"]     # 부모 점수
                    if ps < thr:                            # 임계값보다 낮으면
                        break                               # 그만 올라감
                    if ps > last:                           # 부모 점수가 더 높으면
                        tc = pt                             # 부모를 선택
                        break                               # 끝냄
                    last = ps                               # 마지막 점수 갱신
                    pt = pt.parent                          # 위로 올라감
                pt = tc.parent                              # 부모 확인
                while pt is not None and pt.tag_name != "BODY" and len(pt.children) == 1:  # 부모가 BODY 가 아니고 자식이 하나뿐이면
                    tc = pt                                 # 부모를 선택
                    pt = tc.parent                          # 한 칸 더 위로
                if tc.readability is None:                  # 점수판이 없으면
                    self._init_node(tc)                     # 초기화

            article = Element("div")                         # 형제 모으기
            sib_thr = max(10, tc.readability["contentScore"] * 0.2)  # 형제 포함 임계값 = max(10, 점수의 20%)
            parent = tc.parent                              # 선택한 후보의 부모
            for sib in list(parent.children):               # 부모의 모든 자식 형제에 대해
                append = False                              # 포함 여부
                if sib is tc:                               # 후보 자신이면
                    append = True                           # 포함
                else:                                       # 아니면
                    bonus = tc.readability["contentScore"] * 0.2 if _cls(sib) == _cls(tc) and _cls(tc) != "" else 0  # 같은 클래스면 보너스(점수의 20%)
                    if sib.readability and sib.readability["contentScore"] + bonus >= sib_thr:  # 점수+보너스가 임계값 이상이면
                        append = True                       # 포함
                    elif sib.tag_name == "P":               # P 형제는 글 길이·링크 밀도로 판단
                        ld = self._link_density(sib)        # 링크 밀도
                        content = self._inner_text(sib)     # 글
                        ln = js_len(content)                # 글자 수
                        if ln > 80 and ld < 0.25:           # 80자 넘고 링크가 적으면
                            append = True                   # 포함
                        elif 0 < ln < 80 and ld == 0 and re.search(r"\.( |$)", content):  # 짧아도 링크가 없고 문장 끝(마침표)이 있으면
                            append = True                   # 포함
                if append:                                  # 포함하기로 한 형제는
                    if sib.tag_name not in _ALTER_TO_DIV_EXC:  # div/p/ol/ul/article/section 이 아니면
                        sib = self._set_tag(sib, "div")     # div 로 바꿔서
                    article.append_child(sib)               # 결과 article 에 붙임
            self._prep_article(article)                     # 결과 정리(prepArticle)
            if created:                                     # 후보를 새로 만들었으면
                tc.attrs["id"] = "readability-page-1"       # 그대로 page-1 로 표시
                tc.attrs["class"] = "page"                  # class 를 page 로
            else:                                           # 아니면
                div = Element("div", {"id": "readability-page-1", "class": "page"})  # page-1 div 를 만들어
                for ch in list(article.child_nodes):        # article 의 자식을
                    div.append_child(ch)                    # 그 div 로 옮기고
                article.append_child(div)                   # div 를 article 에 붙임
            tl = js_len(self._inner_text(article, True))    # 결과 글자 수
            if tl < 500:                                     # charThreshold — 플래그를 하나씩 끄고 다시
                body.child_nodes = []                       # 너무 짧으면 body 를 원상 복구하고 플래그를 줄여 재시도
                for ch in list(copy.deepcopy(page_cache)):   # 복사본 목록 = 복사된 부모의 자식 목록이라 미리 list 로 떼어 둠
                    body.append_child(ch)                   # 원래 자식을 body 로 되돌림
                self.attempts.append((article, tl))         # 이번 시도 기록
                if self._active(self.F_STRIP):              # 후보 거르기를 끄고
                    self.flags &= ~self.F_STRIP             # STRIP 플래그 해제
                elif self._active(self.F_WEIGHT):           # 다음엔 가중치를 끄고
                    self.flags &= ~self.F_WEIGHT            # WEIGHT 플래그 해제
                elif self._active(self.F_CLEAN):            # 다음엔 조건부 청소를 끄고
                    self.flags &= ~self.F_CLEAN             # CLEAN 플래그 해제
                else:                                       # 더 줄일 게 없으면
                    self.attempts.sort(key=lambda x: -x[1])  # 가장 긴 것(안정 정렬)
                    if not self.attempts[0][1]:             # 시도들 중 가장 긴 것을 고르는데, 글이 전혀 없으면
                        return None                         # 실패(None)
                    return self.attempts[0][0]              # 첫 시도 결과 반환
                continue                                    # 재시도
            return article                                  # 성공: 결과 반환

    @staticmethod                                           # 정적 메서드
    def _ancestors(n: Element) -> list:                     # 조상 목록 구하기
        """_getNodeAncestors(제한 없음)."""
        out = []                                            # 조상 모음
        while n.parent is not None:                         # 부모가 있는 동안
            out.append(n.parent)                            # 부모 추가
            n = n.parent                                    # 한 칸 위로
        return out                                          # 조상 목록 반환

    def _clean(self, e: Element, tag: str) -> None:         # 특정 태그를 지움(영상 임베드는 예외)
        """_clean — 그 태그 제거(object·embed·iframe 은 동영상 주소면 남김)."""
        is_embed = tag in ("object", "embed", "iframe")     # 임베드 계열 태그인지
        for el in reversed(e.get_elements_by_tag(tag)):     # 뒤에서부터 지움(문서 순서 영향 방지)
            if el.parent is None:                           # 이미 떼어졌으면
                continue                                    # 건너뜀
            if is_embed and any(_VIDEO_RX.search(v or "") for v in el.attrs.values()):  # 영상 사이트 주소가 속성에 있으면
                continue                                    # 지우지 않음
            _remove(el)                                     # 요소 제거

    # ── 표·조건부 정리(_markDataTables / _getRowAndColumnCount / _getTextDensity / _cleanConditionally) ──
    @staticmethod                                           # 정적 메서드
    def _parse_int(v: Optional[str]) -> int:                # rowspan 같은 정수 속성 읽기
        """parseInt(v, 10) — 앞 숫자만, 없으면 0(NaN 은 || 1 로 1 이 되게 0 반환)."""
        m = re.match(r"^[" + JS_WS_CHARS + r"]*([+-]?\d+)", v or "")  # 앞 공백 뒤의 정수 부분(parseInt 흉내)
        return int(m.group(1)) if m else 0                  # 없으면 0

    def _row_col_count(self, table: Element) -> tuple[int, int]:  # 표의 행·열 수 세기
        """_getRowAndColumnCount — rowspan·colspan 반영."""
        rows = cols = 0                                     # 행·열 초기값
        for tr in table.get_elements_by_tag("tr"):          # 모든 tr 에 대해
            rs = self._parse_int(tr.get_attribute("rowspan")) if tr.get_attribute("rowspan") else 0  # rowspan 이 있으면 그 값
            rows += rs or 1                                 # 행 수 누적(없으면 1)
            in_row = 0                                      # 이 행의 열 수
            for td in tr.get_elements_by_tag("td"):         # 모든 td 에 대해
                cs = self._parse_int(td.get_attribute("colspan")) if td.get_attribute("colspan") else 0  # colspan 값
                in_row += cs or 1                           # 열 수 누적(없으면 1)
            cols = max(cols, in_row)                        # 가장 많은 열 수 기록
        return rows, cols                                   # (행, 열) 반환

    def _mark_data_tables(self, root: Element) -> None:     # 표가 데이터표인지 표시(_markDataTables)
        """_markDataTables — 자료 표인지 표시(자료 표는 조건부 정리에서 보호)."""
        for table in root.get_elements_by_tag("table"):     # 모든 table 에 대해
            if table.get_attribute("role") == "presentation":  # role=presentation 이면
                table.data_table = False                    # 데이터표 아님
                continue                                    # 다음 표로
            if table.get_attribute("datatable") == "0":     # datatable=0 이면
                table.data_table = False                    # 데이터표 아님
                continue                                    # 다음 표로
            if table.get_attribute("summary"):              # summary 속성이 있으면
                table.data_table = True                     # 데이터표
                continue                                    # 다음 표로
            caps = table.get_elements_by_tag("caption")     # caption 요소
            if caps and caps[0].child_nodes:                # 내용이 있으면
                table.data_table = True                     # 데이터표
                continue                                    # 다음 표로
            if any(table.get_elements_by_tag(t) for t in ("col", "colgroup", "tfoot", "thead", "th")):  # col·colgroup·tfoot·thead·th 중 하나라도 있으면
                table.data_table = True                     # 데이터표
                continue                                    # 다음 표로
            if table.get_elements_by_tag("table"):          # 안에 표가 또 있으면
                table.data_table = False                    # 레이아웃용이라 데이터표 아님
                continue                                    # 다음 표로
            rows, cols = self._row_col_count(table)         # 행·열 수
            if cols == 1 or rows == 1:                      # 한 줄짜리면
                table.data_table = False                    # 데이터표 아님
                continue                                    # 다음 표로
            if rows >= 10 or cols > 4:                      # 행이 많거나 열이 5 이상이면
                table.data_table = True                     # 데이터표
                continue                                    # 다음 표로
            table.data_table = rows * cols > 10             # 그 외엔 칸 수가 10 넘으면 데이터표

    def _fix_lazy_images(self, root: Element) -> None:      # 지연 로딩 이미지의 진짜 주소 복원(_fixLazyImages)
        """_fixLazyImages — data-src 같은 속성의 그림 주소를 src/srcset 으로."""
        b64 = re.compile(r"^data:\s*([^\s;,]+)\s*;\s*base64\s*,", re.I)  # base64 data URI 패턴
        for elem in root.get_elements_by_tag("img", "picture", "figure"):  # img·picture·figure 에 대해
            src = elem.get_attribute("src") or ""           # src 값
            m = b64.match(src)                              # data URI 인지 검사
            if src and m:                                   # data URI 면
                if m.group(1) == "image/svg+xml":           # SVG 는 그대로 둠
                    continue                                # 다음으로
                could = any(k != "src" and re.search(r"\.(jpg|jpeg|png|webp)", v or "", re.I) for k, v in elem.attrs.items())  # 다른 속성에 이미지 파일 주소가 있는지
                if could and js_len(src) - len(m.group(0)) < 133:  # data URI 본문이 짧아(133 미만) 자리표시자로 보이면
                    elem.attrs.pop("src", None)             # src 를 지움
            src = elem.get_attribute("src") or ""           # src 다시 읽기
            srcset = elem.get_attribute("srcset")           # srcset 값
            if (src or (srcset and srcset != "null")) and "lazy" not in _cls(elem).lower():  # 이미 src 가 있고 lazy 표시가 없으면
                continue                                    # 건드리지 않음
            for k, v in list(elem.attrs.items()):           # 속성 전체를 훑어서
                if k in ("src", "srcset", "alt"):           # src·srcset·alt 는
                    continue                                # 건너뜀
                copy_to = None                              # 옮길 속성 이름
                if re.search(r"\.(jpg|jpeg|png|webp)\s+\d", v or ""):  # "파일 크기" 꼴이면
                    copy_to = "srcset"                      # srcset 으로 복사
                elif re.search(r"^\s*\S+\.(jpg|jpeg|png|webp)\S*\s*$", v or ""):  # 단일 이미지 주소 꼴이면
                    copy_to = "src"                         # src 로 복사
                if copy_to:                                 # 복사할 대상이 있으면
                    if elem.tag_name in ("IMG", "PICTURE"):  # IMG·PICTURE 면
                        elem.attrs[copy_to] = v             # 속성으로 값을 복사
                    elif elem.tag_name == "FIGURE" and not elem.get_elements_by_tag("img", "picture"):  # 내부에 이미지가 없는 FIGURE 면
                        img = Element("img", {copy_to: v})  # 새 img 를 만들어
                        elem.append_child(img)              # figure 에 붙임

    def _text_density(self, e: Element, tags: list[str]) -> float:  # 지정 태그 글자가 차지하는 비율 계산
        """_getTextDensity — 그 태그들 글 길이 / 전체 글 길이."""
        tl = js_len(self._inner_text(e, True))              # 전체 글자 수
        if tl == 0:                                         # 글이 없으면
            return 0                                        # 0
        return sum(js_len(self._inner_text(c, True)) for c in e.get_elements_by_tag(*[t.lower() for t in tags])) / tl  # 지정 태그들의 글자 합 / 전체

    def _has_ancestor_fn(self, node, tag: str, fn) -> bool:  # 함수 조건을 만족하는 조상이 있는지 확인
        """_hasAncestorTag(node, tag, -1, fn) — 깊이 제한 없음."""
        while node.parent is not None:                      # 부모가 있는 동안
            if node.parent.tag_name == tag.upper() and fn(node.parent):  # 부모가 해당 태그이고 조건을 만족하면
                return True                                 # 있다
            node = node.parent                              # 한 칸 위로
        return False                                        # 없다

    _AD_WORDS = re.compile(r"^(ad(vertising|vertisement)?|pub(licité)?|werb(ung)?|广告|Реклама|Anuncio)$", re.I)  # 광고 단어 패턴(여러 언어)
    _LOADING_WORDS = re.compile(r"^((loading|正在加载|Загрузка|chargement|cargando)(…|\.\.\.)?)$", re.I)  # 로딩 단어 패턴(여러 언어)

    def _clean_conditionally(self, e: Element, tag: str) -> None:  # 조건부 청소(_cleanConditionally)
        """_cleanConditionally — 글보다 링크·그림·입력이 많은 등 '내용 같지 않은' 요소 제거."""
        if not self._active(self.F_CLEAN):                  # CLEAN 플래그가 꺼졌으면
            return                                          # 아무것도 안 함
        is_dt = lambda t: bool(getattr(t, "data_table", False))   # 자료 표?
        for node in reversed(e.get_elements_by_tag(tag)):   # 뒤에서부터(_removeNodes)
            if node.parent is None:                         # 이미 떼어진 노드는
                continue                                    # 건너뜀
            if self._should_remove(node, tag, is_dt):       # 지워야 하는 노드면
                _remove(node)                               # 제거

    def _should_remove(self, node: Element, tag: str, is_dt) -> bool:  # 지울지 판단
        """_cleanConditionally 의 판정 함수."""
        is_list = tag in ("ul", "ol")                       # 목록 태그인지
        if not is_list:                                     # 목록이 아니면
            ll = sum(js_len(self._inner_text(l)) for l in node.get_elements_by_tag("ul", "ol"))  # 안에 든 목록의 글자 수 합
            tl = js_len(self._inner_text(node))             # 전체 글자 수
            is_list = (ll / tl > 0.9) if tl else (ll > 0)    # 0/0=NaN(거짓), x/0=Infinity(참)
        if tag == "table" and is_dt(node):                  # 데이터표면
            return False                                    # 보존
        if self._has_ancestor_fn(node, "table", is_dt):     # 데이터표 안에 있으면
            return False                                    # 보존
        if self._has_ancestor(node, "code"):                # code 안에 있으면
            return False                                    # 보존
        if any(is_dt(t) for t in node.get_elements_by_tag("table")):  # 안에 데이터표가 있으면
            return False                                    # 보존
        weight = self._class_weight(node)                   # 클래스·id 가중치
        if weight < 0:                                      # 음수면
            return True                                     # 지움
        if len(self._inner_text(node).split(",")) - 1 >= 10:   # _getCharCount(node, ",") < 10 이 아니면 남김
            return False                                    # (위 조건에 해당하면) 보존
        p = len(node.get_elements_by_tag("p"))              # 문단 수
        img = len(node.get_elements_by_tag("img"))          # 이미지 수
        li = len(node.get_elements_by_tag("li")) - 100      # li 수(100 은 목록 안 li 보정)
        inp = len(node.get_elements_by_tag("input"))        # 입력 요소 수
        heading_density = self._text_density(node, ["h1", "h2", "h3", "h4", "h5", "h6"])  # 제목 글자 비율
        embed_count = 0                                     # 임베드 수
        for emb in node.get_elements_by_tag("object", "embed", "iframe"):  # 임베드류 요소마다
            if any(_VIDEO_RX.search(v or "") for v in emb.attrs.values()):  # 영상 사이트 것이면
                return False                                # 보존
            embed_count += 1                                # 임베드 수 증가
        inner = self._inner_text(node)                      # 글
        if self._AD_WORDS.search(inner) or self._LOADING_WORDS.search(inner):  # 광고·로딩 문구면
            return True                                     # 지움
        content_length = js_len(inner)                      # 글자 수
        link_density = self._link_density(node)             # 링크 밀도
        text_density = self._text_density(node, ["SPAN", "LI", "TD"] + sorted(_DIV_TO_P, key=["BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"].index))  # SPAN·LI·TD·블록 태그의 글자 밀도
        is_fig = self._has_ancestor(node, "figure")         # figure 안인지
        errs = False                                        # 지울 이유 표시
        if not is_fig and img > 1 and p / img < 0.5:        # 이미지가 많고 문단이 적으면
            errs = True                                     # 지울 이유 있음
        if not is_list and li > p:                          # li 가 문단보다 많으면
            errs = True                                     # 지울 이유 있음
        if inp > math.floor(p / 3):                         # 입력 요소가 많으면
            errs = True                                     # 지울 이유 있음
        if not is_list and not is_fig and heading_density < 0.9 and content_length < 25 and (img == 0 or img > 2) and link_density > 0:  # 제목 밀도·글 길이·이미지·링크 조건에 맞으면
            errs = True                                     # 지울 이유 있음
        if not is_list and weight < 25 and link_density > 0.2:  # 가중치가 낮고 링크가 많으면
            errs = True                                     # 지울 이유 있음
        if weight >= 25 and link_density > 0.5:             # 가중치가 높아도 링크가 너무 많으면
            errs = True                                     # 지울 이유 있음
        if (embed_count == 1 and content_length < 75) or embed_count > 1:  # 임베드 조건에 맞으면
            errs = True                                     # 지울 이유 있음
        if img == 0 and text_density == 0:                  # 이미지도 글도 없으면
            errs = True                                     # 지울 이유 있음
        if is_list and errs:                                # 목록이면 예외 검사
            for child in node.children:                     # 자식마다
                if len(child.children) > 1:                 # 자식이 2 개 넘으면
                    return errs                             # 판정 그대로 반환
            if img == len(node.get_elements_by_tag("li")):  # 이미지 수가 li 수와 같으면(이미지 목록)
                return False                                # 보존
        return errs                                         # 판정 반환

    def _prep_article(self, a: Element) -> None:            # 결과 글 정리(_prepArticle)
        """_prepArticle — 쓸데없는 요소 제거, 조건부 정리, h1 → h2, 빈 p 제거, 칸 하나 표 풀기(_cleanStyles 는 마크다운에 영향 없어 생략)."""
        self._mark_data_tables(a)                            # 자료 표 표시
        self._fix_lazy_images(a)                             # 늦게 불러오는 그림
        self._clean_conditionally(a, "form")                # form 조건부 청소
        self._clean_conditionally(a, "fieldset")            # fieldset 조건부 청소
        for t in ("object", "embed", "footer", "link", "aside"):  # 불필요 태그 제거
            self._clean(a, t)                               # 해당 태그를 지움
        for top in list(a.children):                         # share 가 들어간 작은 요소
            end = self._next_node(top, True)                # 공유 버튼 영역 끝 위치
            nxt = self._next_node(top)                      # 다음 노드
            while nxt is not None and nxt is not end:       # 영역 끝까지
                if _RX["share"].search(_cls(nxt) + " " + _id(nxt)) and js_len(nxt.text_content) < 500:  # 공유 패턴이고 글이 500자 미만이면
                    nxt = self._remove_and_next(nxt)        # 제거
                else:                                       # 아니면
                    nxt = self._next_node(nxt)              # 다음으로
        for t in ("iframe", "input", "textarea", "select", "button"):  # 입력 계열 태그 제거
            self._clean(a, t)                               # 해당 태그를 지움
        for h in reversed(a.get_elements_by_tag("h1", "h2")):  # 가중치가 음수인 h1·h2 제거(_cleanHeaders)
            if h.parent is not None and self._class_weight(h) < 0:  # 부모가 있고 가중치가 음수면
                _remove(h)                                  # 제거
        for t in ("table", "ul", "div"):                     # 마지막에 조건부 정리
            self._clean_conditionally(a, t)                 # 조건부 청소 적용
        for h in a.get_elements_by_tag("h1"):               # h1 은 h2 로 낮춤
            self._set_tag(h, "h2")                          # 태그 이름 변경
        for p in reversed(a.get_elements_by_tag("p")):       # 빈 p
            if p.parent is None:                            # 이미 떼어졌으면
                continue                                    # 건너뜀
            if not p.get_elements_by_tag("img", "embed", "object", "iframe") and not self._inner_text(p, False):  # 미디어도 글도 없는 빈 문단이면
                _remove(p)                                  # 제거
        for br in a.get_elements_by_tag("br"):               # p 앞의 br
            n = self._skip_ws(_next_sibling(br))            # br 뒤 공백을 건너뛴 다음 마디
            if n is not None and isinstance(n, Element) and n.tag_name == "P":  # 그것이 문단이면
                _remove(br)                                 # br 제거
        for table in a.get_elements_by_tag("table"):         # 칸 하나짜리 표
            if table.parent is None:                        # 이미 떼어졌으면
                continue                                    # 건너뜀
            tbody = table.children[0] if self._has_single_tag(table, "TBODY") else table  # tbody 가 하나뿐이면 그 안을 본다
            if self._has_single_tag(tbody, "TR"):           # tr 하나뿐이면
                row = tbody.children[0]                     # 그 행
                if self._has_single_tag(row, "TD"):         # td 하나뿐이면
                    cell = row.children[0]                  # 그 칸
                    cell = self._set_tag(cell, "p" if all(self._is_phrasing(c) for c in cell.child_nodes) else "div")  # 칸 내용이 구문뿐이면 p, 아니면 div 로 바꿈
                    table.parent.replace_child(cell, table)  # 표를 그 칸으로 대체

    def parse(self) -> Optional[dict]:                      # 전체 절차 실행
        """parse() — {'title', 'content'(본문 Element)} 또는 None."""
        self.title = self._article_title()                   # 메타는 스크립트 지우기 전에(JSON-LD 는 빠짐)
        self._prep_document()                                # 스크립트·스타일 제거 등
        article = self._grab()                               # 본문
        if article is None:                                 # 본문이 없으면
            return None                                     # 실패(None)
        _fix_relative_uris(article)                          # 주소 정리
        for e in article.iter():                             # _cleanClasses(page 만 남김)
            c = " ".join(x for x in re.split(JS_WS_CLASS + "+", _cls(e)) if x == "page")  # class 에서 page 만 남김
            if c:                                           # 남은 게 있으면
                e.attrs["class"] = c                        # class 로 지정
            else:                                           # 없으면
                e.attrs.pop("class", None)                  # class 속성 제거
        return {"title": self.title, "content": article}    # 제목과 본문을 돌려줌


_SPECIAL = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "file": None}   # WHATWG 특수 스킴(기본 포트)


def _whatwg_href(uri: str) -> str:                          # URL 을 WHATWG 규칙에 맞춰 문자열로
    """new URL(uri, 'about:blank').href 근사 — 절대 주소만 정리(상대 주소는 about:blank 기준이라 실패 → 원래 글).
    특수 스킴: 스킴·호스트 소문자, 기본 포트 제거, 빈 경로는 '/', 공백 등 퍼센트 인코딩."""
    m = re.match(r"^[" + JS_WS_CHARS + r"\x00-\x20]*([A-Za-z][A-Za-z0-9+.\-]*):", uri)  # 스킴으로 시작하는지 검사
    if not m:                                                # 스킴 없음 = 상대 → 실패
        raise ValueError("Invalid URL")                     # 스킴이 없으면 오류
    u = uri.strip("\x00 \t\n\r\x0b\x0c").replace("\t", "").replace("\n", "").replace("\r", "")   # 앞뒤 C0·공백, 탭·줄바꿈 제거
    scheme = m.group(1).lower()                             # 스킴(소문자)
    if scheme not in _SPECIAL:                               # 특수 스킴 아님(mailto 등) — 스킴만 소문자
        return scheme + u[len(m.group(1)):]                 # 스킴 뒤를 그대로 이어 붙임(비계층 URL)
    parts = urlsplit(u.replace("\\", "/"))                   # 특수 스킴은 \ 도 / 로
    host = parts.hostname or ""                             # 호스트
    if scheme != "file" and not host:                        # 호스트 없음 → 실패
        raise ValueError("Invalid URL")                     # 호스트가 없으면 오류
    try:                                                    # 호스트 정규화 시도
        host = host.encode("idna").decode("ascii") if any(ord(c) > 127 for c in host) else host.lower()  # 비ASCII 면 IDNA 로, 아니면 소문자로
    except Exception:                                       # 실패하면
        raise ValueError("Invalid URL")                     # 오류로 통일
    netloc = host                                           # netloc 조립 시작
    if parts.username is not None:                           # 사용자 정보
        netloc = parts.username + (":" + parts.password if parts.password else "") + "@" + netloc  # 사용자 정보 붙임
    try:                                                    # 포트 읽기 시도
        port = parts.port                                   # 포트 값
    except ValueError:                                      # 잘못된 포트면
        raise ValueError("Invalid URL")                     # 오류로 통일
    if port is not None and port != _SPECIAL[scheme]:        # 기본 포트는 뺌
        netloc += f":{port}"                                # 포트 붙임
    path = parts.path or "/"                                # 경로(없으면 /)
    path = quote(path, safe="/%:@!$&'()*+,;=-._~[]|^")      # 경로 인코딩
    query = quote(parts.query, safe="/%:@!$&'()*+,;=-._~?[]\\^`{|}") if parts.query else ""  # 쿼리 퍼센트 인코딩
    frag = quote(parts.fragment, safe="/%:@!$&'()*+,;=-._~?#[]\\^{|}") if parts.fragment else ""  # 프래그먼트 퍼센트 인코딩
    out = urlunsplit((scheme, netloc, path, "", ""))        # 조립
    if "?" in u:                                            # 원문에 ? 가 있었으면
        out += "?" + query                                  # 쿼리 붙임(빈 쿼리도 유지)
    if "#" in u:                                            # 원문에 # 가 있었으면
        out += "#" + frag                                   # 프래그먼트 붙임
    return out                                              # 결과 반환


def _fix_relative_uris(article: Element) -> None:           # 상대 주소를 절대 주소로(_fixRelativeUris)
    """_fixRelativeUris — baseURI=documentURI='about:blank'(cn은 JSDOM 에 url 을 안 줌): #링크·상대 주소는 그대로, 절대 주소만 정리. javascript: 링크는 글로."""
    def to_abs(uri: str) -> str:                            # 주소 하나를 절대화하는 내부 함수
        if uri[:1] == "#":                                   # 해시 링크
            return uri                                      # 빈 값은 그대로
        try:                                                # 변환 시도
            return _whatwg_href(uri)                        # WHATWG 규칙으로 정리
        except Exception:                                   # 실패하면
            return uri                                      # 원문 유지
    for link in article.get_elements_by_tag("a"):           # 모든 링크에 대해
        href = link.get_attribute("href")                   # href 값
        if href:                                            # href 가 있으면
            if href.startswith("javascript:"):               # 스크립트 링크 → 글/ span
                if len(link.child_nodes) == 1 and isinstance(link.child_nodes[0], Text):  # javascript: 링크면
                    if link.parent is not None:             # 자식이 글 하나뿐이면
                        link.parent.replace_child(Text(link.text_content), link)  # 부모에서 링크를 글로 대체
                else:                                       # 아니면
                    span = Element("span")                  # span 을 만들어
                    for c in list(link.child_nodes):        # 링크의 자식을
                        span.append_child(c)                # span 으로 옮기고
                    if link.parent is not None:             # 부모가 있으면
                        link.parent.replace_child(span, link)  # 링크를 span 으로 대체
            else:                                           # 일반 링크면
                link.attrs["href"] = to_abs(href)           # 절대 주소로 바꿈
    for media in article.get_elements_by_tag("img", "picture", "figure", "video", "audio", "source"):  # 미디어 요소에 대해
        for attr in ("src", "poster"):                      # src·poster 속성마다
            v = media.get_attribute(attr)                   # 값
            if v:                                           # 있으면
                media.attrs[attr] = to_abs(v)               # 절대 주소로 바꿈
        srcset = media.get_attribute("srcset")              # srcset 값
        if srcset:                                          # 있으면
            media.attrs["srcset"] = re.sub(r"(\S+)(\s+[\d.]+[xw])?(\s*(?:,|$))",  # srcset 의 각 주소를 절대화
                                           lambda m: to_abs(m.group(1)) + (m.group(2) or "") + m.group(3), srcset)  # 람다로 후보별 주소 변환


# ═════════════════════════ 3. node-html-markdown 1.3.0 이식 ═════════════════════════
REMOVE_NODE = object()                                      # 후처리 결과: 노드 제거 신호(PostProcessResult.RemoveNode)

_BLOCK = ["ADDRESS", "ARTICLE", "ASIDE", "AUDIO", "BLOCKQUOTE", "BODY", "CANVAS", "CENTER", "DD", "DIR", "DIV", "DL",  # 블록 요소 목록(defaultBlockElements) 앞부분
          "DT", "FIELDSET", "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "FRAMESET", "H1", "H2", "H3", "H4", "H5", "H6",  # 블록 요소 목록 중간
          "HEADER", "HGROUP", "HR", "HTML", "ISINDEX", "LI", "MAIN", "MENU", "NAV", "NOFRAMES", "NOSCRIPT", "OL",  # 블록 요소 목록 중간
          "OUTPUT", "P", "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT", "TH", "THEAD", "TR", "UL"]  # 블록 요소 목록 뒷부분(defaultBlockElements)
_IGNORE = ["AREA", "BASE", "COL", "COMMAND", "EMBED", "HEAD", "INPUT", "KEYGEN", "LINK", "META", "PARAM", "SCRIPT",  # 무시할 요소 목록(defaultIgnoreElements) 앞부분
           "SOURCE", "STYLE", "TRACK", "WBR"]               # 무시할 요소 목록 뒷부분
_CONTENTLESS = ["BR", "HR", "IMG"]                          # 내용 없는 요소 목록(contentlessElements)


def _is_ws_only(s: str) -> bool:                            # 공백뿐인 글인지
    """isWhiteSpaceOnly — /\\S/ 가 없음."""
    return all(c in JS_WS_CHARS for c in s)                 # 모든 글자가 JS 공백이면 참


def _trim_newlines(s: str) -> str:                          # 앞뒤 줄바꿈 제거
    """trimNewLines — 앞뒤 \\n 만 제거."""
    return s.strip("\n")                                    # \n 만 양끝에서 벗김


def _split_special(s: str) -> list[tuple[str, str]]:        # 글을 (줄 내용, 줄바꿈) 쌍으로 나눔
    """splitSpecial — (줄 글, 그 줄의 줄바꿈 글자) 목록."""
    lines = []                                              # 결과 목록
    n = len(s)                                              # 글 길이
    i = 0                                                   # 현재 위치
    start = 0                                               # 줄 시작 위치
    while i < n:                                            # 끝까지 훑음
        ch = s[i]                                           # 현재 글자
        nl = ""                                             # 이 글자의 줄바꿈 종류
        if ch == "\r":                                      # \r 이면
            nl = "\r\n" if i + 1 < n and s[i + 1] == "\n" else "\r"  # \r\n 인지 \r 인지 판별
        elif ch == "\n":                                    # \n 이면
            nl = "\n"                                       # 줄바꿈 \n
        end = i if nl else (i + 1 if i == n - 1 else None)  # 줄 끝 위치(마지막 줄은 글 끝)
        if end is None:                                     # 줄 끝이 아니면
            i += 1                                          # 다음 글자로
            continue                                        # 계속
        lines.append((s[start:end], nl))                    # 줄 내용과 줄바꿈 기록
        start = end + len(nl)                               # 다음 줄 시작
        if len(nl) > 1:                                     # \r\n 은 두 글자
            i += 1                                          # 한 글자 더 건너뜀
        i += 1                                              # 다음 글자로
    return lines                                            # 나눈 목록 반환


def _tag_surround(content: str, sur: str) -> str:           # 줄마다 강조 기호를 감쌈(안쪽 공백은 밖으로)
    """tagSurround — 각 줄의 앞뒤 공백을 밖으로 빼고 구분자로 감쌈."""
    if sur in content:                                       # 안에 같은 구분자가 있으면 지움
        content = re.sub(r"([^\\])" + re.escape(sur), r"\1", content)  # 안에 이미 있는 같은 기호는 제거
    res = ""                                                # 결과 글
    for text, nl in _split_special(content):                # 줄마다 처리
        start = next((i for i, c in enumerate(text) if c not in JS_WS_CHARS), None)   # 첫 비공백
        if start is None:                                    # 전부 공백 → 감싸지 않음
            res += text + nl                                # 내용이 공백뿐이면 감싸지 않고 그대로
            continue                                        # 다음 줄로
        end = next(i for i in range(len(text) - 1, start - 1, -1) if text[i] not in JS_WS_CHARS)   # 끝 비공백(start 이상)
        lead = text[start - 1] if start > 0 else ""         # 앞쪽 공백 글자(없으면 빈 글)
        trail = text[end + 1] if end < len(text) - 1 else ""  # 뒤쪽 공백 글자(없으면 빈 글)
        res += lead + sur + text[start:end + 1] + sur + trail + nl  # 공백, 기호, 내용, 기호, 공백 순으로 조립
    return res                                              # 결과 반환


def _trailing_ws_info(s: str) -> tuple[int, int]:           # 끝 공백 정보 계산
    """getTrailingWhitespaceInfo — 끝 10글자 안의 (공백 수, 줄바꿈 수)."""
    ws = nl = 0                                             # 공백 글자 수·줄바꿈 수
    for i in range(len(s) - 1, max(len(s) - 10, 0) - 1, -1):  # 끝에서부터 최대 10 글자까지 검사
        c = s[i]                                            # 검사 중인 글자
        if c not in JS_WS_CHARS:                            # 공백이 아니면
            break                                           # 거기서 멈춤
        ws += 1                                             # 공백 수 증가
        if c in "\r\n":                                     # 줄바꿈 글자면
            nl += 1                                         # 줄바꿈 수도 증가
    return ws, nl                                           # (공백 수, 줄바꿈 수) 반환


def _trim_text(text: str) -> str:                           # 앞뒤 공백을 한 칸으로 줄이는 글 다듬기(줄바꿈은 제외)
    """node-html-parser trimText — 양끝 공백을 없애되 줄바꿈 아닌 공백이 있었으면 공백 하나 남김."""
    start = next((i for i, c in enumerate(text) if c not in JS_WS_CHARS), None)  # 첫 비공백 위치
    end = next((i for i in range(len(text) - 1, -1, -1) if text[i] not in JS_WS_CHARS), None)  # 마지막 비공백 위치
    if start is None:                                       # 비공백이 없으면
        start = 0                                           # 처음부터
    if end is None or (start is not None and end < start):  # 끝도 없거나 순서가 꼬이면
        end = len(text) - 1                                 # 글 끝까지
    lead = start > 0 and text[start - 1] in JS_WS_CHARS and text[start - 1] not in "\r\n"  # 앞쪽에 줄바꿈 아닌 공백이 있었는지
    trail = end < len(text) - 1 and text[end + 1] in JS_WS_CHARS and text[end + 1] not in "\r\n"  # 뒤쪽에 줄바꿈 아닌 공백이 있었는지
    return (" " if lead else "") + text[start:end + 1] + (" " if trail else "")  # 있던 쪽은 공백 한 칸으로 남김


_GLOBAL_ESC = re.compile(r"[\\`*_~\[\]]")                   # 마크다운 전역 이스케이프 대상 글자(globalEscape)
_LINE_START_ESC = re.compile(_BOL + r"(" + JS_WS_CLASS + r"*?)((?:\+" + JS_WS_CLASS + r")|(?:[=>-])|(?:#{1,6}" + JS_WS_CLASS + r"))|(?:([0-9]+)(\." + JS_WS_CLASS + r"))")  # 줄 시작 이스케이프 패턴(lineStartEscape): 목록·인용·제목·번호 목록
_DOCTYPE = re.compile(_BOL + r"<!DOCTYPE[^" + _LT + r"]*>", re.I)  # <!DOCTYPE …> 줄 제거용(textReplace)


def _line_start_repl(m: re.Match) -> str:                   # 줄 시작 이스케이프 치환 함수
    """'$1$3\\\\$2$4' — 안 맞은 그룹은 빈 글."""
    return (m.group(1) or "") + (m.group(3) or "") + "\\" + (m.group(2) or "") + (m.group(4) or "")  # 해당 기호 앞에 역슬래시를 넣어 마크다운 문법이 되지 않게 함


def _process_text(text: str, md: dict) -> str:              # 글 마디의 글을 마크다운용으로 처리(processText)
    """processText — 공백 접기 + 이스케이프."""
    res = text                                              # 작업 글
    if not md.get("preserveWhitespace"):                    # 공백 보존 모드가 아니면
        res = re.sub(JS_WS_CLASS + "+", " ", res)           # 연속 공백을 한 칸으로
    if md.get("noEscape"):                                  # 이스케이프 금지면
        return res                                          # 그대로 반환
    res = _GLOBAL_ESC.sub(lambda m: "\\" + m.group(0), res)  # 전역 이스케이프 문자 앞에 \\ 를 붙임
    res = _LINE_START_ESC.sub(_line_start_repl, res)        # 줄 시작 문법 문자를 이스케이프
    res = _DOCTYPE.sub("", res)                             # DOCTYPE 줄 제거
    return res                                              # 결과 반환


class _Fn:                                                  # 속성을 ctx 에서 동적으로 계산하는 번역 설정 래퍼
    """함수형 번역기 + base(합쳐질 기본 설정)."""

    def __init__(self, fn: Callable[[dict], dict], base: Optional[dict] = None):  # 래퍼 생성
        self.fn = fn                                        # ctx → 설정 함수
        self.base = base                                    # 합칠 기본 설정


class _Collection(dict):                                    # 번역 설정 모음(dict) — 태그 이름(대문자) → 설정
    """TranslatorCollection — 대문자 태그 → 설정(dict) 또는 _Fn."""

    def set(self, keys: str, cfg, preserve_base: bool = False) -> None:  # 여러 태그에 같은 설정 등록
        for el in keys.split(","):                          # 쉼표로 나눈 태그마다
            el = el.upper()                                 # 대문자로
            res = cfg                                       # 기본은 주어진 설정 그대로
            if preserve_base:                               # 기존 설정을 이어받는 모드면
                base = self.get(el)                         # 이미 있던 설정
                if isinstance(base, dict):                   # 기본 설정(객체)이 있으면
                    res = _Fn(cfg.fn, base) if isinstance(cfg, _Fn) else {**base, **cfg}   # 함수는 base 를 달고, 객체는 합침
            self[el] = res                                  # 등록


def _li_fn(ctx: dict) -> dict:                              # li 번역 설정 만들기
    """'li' 번역기."""
    indent_level = int(ctx.get("indentLevel") or 0)         # 들여쓰기 단계
    lk = ctx.get("listKind")                                # 부모 목록 종류(OL/UL)
    num = ctx.get("listItemNumber")                         # 번호 목록의 현재 번호
    prefix = "   " * indent_level + (f"{num}. " if lk == "OL" and num is not None else f"{ctx['options']['bulletMarker']} ")  # 접두사: 들여쓰기 + (번호. 또는 글머리 기호)

    def post(c: dict):                                      # 후처리 함수
        content = c["content"]                              # 내용
        if _is_ws_only(content):                            # 공백뿐이면
            return REMOVE_NODE                              # 항목 제거
        t = js_trim(content)                                # 양끝 공백 제거
        t = re.sub(r"([^\r\n])(?:\r?\n)+", lambda m: m.group(1) + "  \n" + "   " * indent_level, t)  # 줄바꿈마다 두 칸 공백과 들여쓰기를 넣어 항목 안에서 이어지게 함
        t = re.sub(r"(" + _NS + r"+?)" + _WS_NO_NL + r"+" + _EOL, lambda m: m.group(1) + "  ", t)  # 줄 끝 공백을 두 칸으로(마크다운 강제 줄바꿈)
        return t                                            # 결과 반환
    return {"prefix": prefix, "surroundingNewlines": 1, "postprocess": post}  # 접두사·앞뒤 줄바꿈 1·후처리를 담아 반환


def _code_fn(ctx: dict) -> dict:                            # code 번역 설정 만들기
    """'code' 번역기 — pre 안의 유일한 자식이면 코드 블록, 아니면 인라인."""
    node, parent = ctx["node"], ctx["parent"]               # 현재 노드와 부모
    is_block = parent is not None and isinstance(parent, Element) and parent.tag_name in ("PRE", "WRAPPED-PRE") and len(parent.child_nodes) < 2  # 부모가 pre 하나뿐의 자식이면 코드 블록
    if not is_block:                                        # 인라인 코드면
        def post(c: dict):                                  # 인라인 코드 후처리
            content = c["content"]                          # 내용
            runs = sorted(re.findall(r"`+", content), key=lambda r: -len(r))   # 긴 것부터(안정 정렬)
            delim = "`" + (runs[0] if runs else "")         # 내용 속 백틱 연속에 맞춰 구분자 길이 결정
            pad = " " if len(delim) > 1 else ""             # 구분자가 길면 안쪽에 공백 한 칸
            return delim + pad + content + pad + delim      # 구분자로 감싸 반환
        return {"spaceIfRepeatingChar": True, "noEscape": True, "postprocess": post}  # 인라인 설정 반환
    m = re.search(r"language-(" + _NS + r"+)", node.get_attribute("class") or "")  # 코드 블록: language-xxx 클래스에서 언어 추출
    lang = m.group(1) if m else ""                          # 언어(없으면 빈 글)
    return {"noEscape": True, "prefix": "```" + lang + "\n", "postfix": "\n```", "childTranslators": ctx["visitor"].code_block_translators}  # 펜스 코드 블록 설정 반환


def _table_fn(ctx: dict) -> dict:                           # table 번역 설정 만들기
    """'table' 번역기 — 칸 너비를 맞춘 마크다운 표."""
    def post(c: dict):                                      # 후처리 함수
        raw_rows = [re.sub(r"^(?:\|" + JS_WS_CLASS + r"+)?([^" + _LT + r"]+)" + JS_WS_CLASS + r"*\|" + JS_WS_CLASS + r"*\Z", r"\1", t, count=1)  # 각 줄에서 앞의 | 와 뒤의 | 를 벗겨 칸 내용만 남김
                    for t, _ in _split_special(c["content"])]  # 줄 목록 위에서 이어짐
        rows = []                                           # 칸으로 나눈 행들
        widths: list[int] = []                              # 열별 최대 너비
        for row in raw_rows:                                # 줄마다
            if not row:                                     # 빈 줄은
                continue                                    # 건너뜀
            cols = []                                       # 이 행의 칸들
            for i, col in enumerate(row.split(" |")):       # " |" 로 칸을 나눔
                col = js_trim(col)                          # 칸 내용 다듬기
                if len(widths) < i + 1:                     # 새 열이면
                    widths.append(js_len(col))              # 너비 기록
                elif widths[i] < js_len(col):               # 더 넓으면
                    widths[i] = js_len(col)                 # 너비 갱신
                cols.append(col)                            # 칸 추가
            rows.append(cols)                               # 행 추가
        if len(rows) < 1:                                   # 행이 하나도 없으면
            return REMOVE_NODE                              # 표 제거
        res = ""                                            # 결과 글
        caption = c["nodeMetadata"].get(id(c["node"]), {}).get("tableMeta", {}).get("caption")  # 저장해 둔 표 캡션
        if caption:                                         # 캡션이 있으면
            res += caption + "\n"                           # 맨 위에 붙임
        for rn, cols in enumerate(rows):                    # 행마다
            res += "| "                                     # 행 시작
            for i in range(len(widths)):                    # 열마다
                cc = cols[i] if i < len(cols) else ""       # 칸 내용(없으면 빈 글)
                cc += " " * max(0, widths[i] - js_len(cc))  # 너비에 맞춰 오른쪽 공백 채움
                res += cc + " |" + (" " if i < len(widths) - 1 else "")  # 칸 닫기
            res += "\n"                                     # 행 끝 줄바꿈
            if rn == 0:                                     # 첫 행이면
                res += "|" + "".join(" " + "-" * w + " |" for w in widths) + "\n"  # 헤더 구분선(---) 추가
        return res                                          # 표 글 반환
    return {"surroundingNewlines": 2, "childTranslators": ctx["visitor"].table_translators, "postprocess": post}  # 앞뒤 줄바꿈 2 와 하위 번역기·후처리 반환


def _a_fn(ctx: dict) -> dict:                               # a 번역 설정 만들기
    """'a' 번역기."""
    node = ctx["node"]                                      # 현재 노드
    href = node.get_attribute("href")                       # href 값
    if not href:                                            # 링크 주소가 없으면
        return {}                                           # 빈 설정(기본 처리)
    enc = "".join({"(": "%28", ")": "%29", "_": "%5F", "*": "%2A"}.get(ch, ch) for ch in href)  # 마크다운을 깨뜨리는 ( ) _ * 를 퍼센트 인코딩
    title = node.get_attribute("title")                     # title 속성
    if node.text_content == href and ctx["options"]["useInlineLinks"]:  # 글이 주소와 같고 인라인 링크 옵션이면
        return {"content": f"<{enc}>"}                      # <주소> 꼴로
    return {                                                # 일반 링크 설정 반환
        "postprocess": lambda c: re.sub(r"(?:\r?\n)+", " ", c["content"]),  # 링크 글 속 줄바꿈은 공백으로
        "childTranslators": ctx["visitor"].a_tag_translators,  # 링크 안에서 쓸 하위 번역기
        "prefix": "[",                                      # 접두사 [
        "postfix": "](" + enc + (f' "{title}"' if title else "") + ")",  # 후위: ](주소 "제목")
    }                                                       # 설정 닫기


def _img_fn(ctx: dict) -> dict:                             # img 번역 설정 만들기
    """'img' 번역기 — data: 이미지는 무시."""
    node = ctx["node"]                                      # 현재 노드
    src = node.get_attribute("src") or ""                   # src 값
    if not src or re.match(r"^data:", src, re.I):           # src 가 없거나 data: 이면
        return {"ignore": True}                             # 무시
    alt = node.get_attribute("alt") or ""                   # alt 값
    title = node.get_attribute("title") or ""               # title 값
    title_part = (' "' + title + '"') if title else ""      # 제목이 있으면 ` "제목"` 꼴(${title && ` "${title}"`})
    return {"content": "![" + alt + "](" + src + title_part + ")", "recurse": False}  # ![alt](src "title") 반환, 자식 순회 안 함


def _surround_post(delim_key: str):                         # 강조류 후처리 만들기
    """strong/del/em 공통 postprocess."""
    def post(c: dict):                                      # 후처리 함수
        if _is_ws_only(c["content"]):                       # 공백뿐이면
            return REMOVE_NODE                              # 항목 제거
        return _tag_surround(c["content"], c["options"][delim_key])  # 구분자로 감싸 반환(공백은 밖으로)
    return post                                             # 후처리 함수 반환


_DEFAULT_TRANSLATORS = [                                     # defaultTranslators(순서 = 원문)
    ("pre", {"noEscape": True, "preserveWhitespace": True}),  # pre: 이스케이프·공백 변환 없음
    ("br", {"content": "  \n", "recurse": False}),          # br: 두 칸 공백+줄바꿈
    ("hr", {"content": "---", "recurse": False}),           # hr: ---
    ("h1,h2,h3,h4,h5,h6", _Fn(lambda ctx: {"prefix": "#" * int(ctx["node"].tag_name[1]) + " "})),  # h1~h6: # 개수로 제목 접두사
    ("strong,b", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("strongDelimiter")}),  # strong,b: ** 로 감쌈
    ("del,s,strike", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("strikeDelimiter")}),  # del,s,strike: ~~ 로 감쌈
    ("em,i", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("emDelimiter")}),  # em,i: _ 로 감쌈
    ("ol,ul", _Fn(lambda ctx: {"surroundingNewlines": 1 if ctx.get("listKind") else 2})),  # 목록: 중첩이면 줄바꿈 1, 최상위면 2
    ("li", _Fn(_li_fn)),                                    # li: 항목 번역
    ("blockquote", {"postprocess": lambda c: re.sub(_BOL + r"(>*)" + _WS_NO_NL + r"?", lambda m: ">" + m.group(1) + " ", _trim_newlines(c["content"]))}),  # blockquote: 줄마다 > 접두
    ("code", _Fn(_code_fn)),                                # code: 인라인·블록 번역
    ("table", _Fn(_table_fn)),                              # table: 마크다운 표
    ("a", _Fn(_a_fn)),                                      # a: 링크
    ("img", _Fn(_img_fn)),                                  # img: 이미지
]                                                           # 기본 번역기 목록 끝


def _caption_post(c: dict):                                 # caption 후처리: 캡션을 표 메타에 저장하고 본문에선 제거
    """table caption — 표 메타에 저장하고 자신은 지움."""
    cap = js_trim(re.sub(r"(?:\r?\n)+", " ", c["content"]))  # 줄바꿈을 공백으로 바꾼 캡션
    if cap:                                                 # 캡션이 있으면
        c["nodeMetadata"][id(c["node"])]["tableMeta"]["caption"] = "__" + cap + "__"  # __캡션__ 꼴로 표 메타에 저장
    return REMOVE_NODE                                      # 캡션 자체는 출력하지 않음


def _cell_post(c: dict):                                    # 표 칸 후처리
    """th/td — 줄바꿈을 공백으로, 첫 | 만 이스케이프(원문 string.replace)."""
    return js_trim(re.sub(r"(?:\r?\n)+", " ", _trim_newlines(c["content"]).replace("|", "\\|", 1)))  # 줄바꿈은 공백으로, 첫 | 는 이스케이프


_DEFAULT_OPTIONS = {"codeFence": "```", "bulletMarker": "*", "indent": "  ", "codeBlockStyle": "fenced", "emDelimiter": "_",  # 기본 옵션 앞부분(코드 펜스·글머리·들여쓰기·강조)
                    "strongDelimiter": "**", "strikeDelimiter": "~~", "maxConsecutiveNewlines": 3, "useInlineLinks": True}  # 기본 옵션 뒷부분(굵게·취소선·연속 줄바꿈 한도·인라인 링크)


class NodeHtmlMarkdown:                                     # node-html-markdown 을 옮긴 변환기 클래스
    """NodeHtmlMarkdown(기본 옵션) — translate(Element) → 마크다운."""

    def __init__(self) -> None:                             # 번역기 모음을 만든다
        self.options = dict(_DEFAULT_OPTIONS)               # 옵션 복사
        self.translators = _Collection()                    # 기본 번역기 모음
        self.code_block_translators = _Collection()         # 코드 블록 안 번역기
        self.a_tag_translators = _Collection()              # 링크 안 번역기
        self.table_translators = _Collection()              # 표 번역기
        self.table_row_translators = _Collection()          # 표 행 번역기
        self.table_cell_translators = _Collection()         # 표 칸 번역기
        for el in _IGNORE:                                   # 기본: 무시 요소
            self.translators.set(el, {"ignore": True, "recurse": False})  # 무시할 요소는 변환·재귀 모두 하지 않음
            self.code_block_translators.set(el, {"ignore": True, "recurse": False})  # 코드 블록 안에서도 무시
        for el in _BLOCK:                                    # 기본: 블록 요소
            self.translators.set(el, {"surroundingNewlines": 2})  # 블록 요소는 앞뒤 줄바꿈 2
            self.code_block_translators.set(el, {"surroundingNewlines": 2})  # 코드 블록 안에서도 동일
        dt = dict(_DEFAULT_TRANSLATORS)                     # 기본 번역기 사전(이름으로 찾기용)
        for k, v in _DEFAULT_TRANSLATORS:                   # 기본 번역기를 모두 등록
            self.translators.set(k, v, True)                # 기존 설정 위에 얹어 등록
        for k, v in [("br", {"content": "\n", "recurse": False}), ("hr", {"content": "---", "recurse": False}),  # 코드 블록 안 번역기(br·hr·제목·목록 등은 단순화)
                     ("h1,h2,h3,h4,h5,h6", {"prefix": "[", "postfix": "]"}), ("ol,ul", dt["ol,ul"]), ("li", dt["li"]),  # 코드 블록 번역기 계속
                     ("tr", {"surroundingNewlines": True}), ("img", {"recurse": False})]:  # 코드 블록 번역기 끝(defaultCodeBlockTranslators)
            self.code_block_translators.set(k, v, True)     # 코드 블록 번역기로 등록
        for k, v in [("br", {"content": "\n", "recurse": False}), ("hr", {"content": "\n", "recurse": False}),  # 링크 안 번역기(br·hr 은 줄바꿈 하나)
                     ("pre", dt["pre"]), ("strong,b", dt["strong,b"]), ("del,s,strike", dt["del,s,strike"]),  # 링크 안 번역기 계속
                     ("em,i", dt["em,i"]), ("img", dt["img"])]:  # 링크 안 번역기 끝(aTagTranslatorConfig)
            self.a_tag_translators.set(k, v, True)          # 링크 번역기로 등록
        cell = {"surroundingNewlines": False, "childTranslators": None, "prefix": " ", "postfix": " |", "postprocess": _cell_post}  # 표 칸 공통 설정
        table_cfg = [                                       # 표 번역기 설정 목록
            ("caption", _Fn(lambda ctx: {"surroundingNewlines": False, "childTranslators": ctx["visitor"].table_cell_translators, "postprocess": _caption_post})),  # caption 번역 설정
            ("tr", _Fn(lambda ctx: {"surroundingNewlines": False, "childTranslators": ctx["visitor"].table_row_translators, "postfix": "\n", "prefix": "| ",  # tr 번역 설정(행 앞뒤 구분자)
                                    "postprocess": lambda c: c["content"] if re.search(r" \|" + JS_WS_CLASS + r"*\Z", c["content"]) else REMOVE_NODE})),  # 행 끝이 | 로 끝나지 않으면 행 제거
            ("th,td", _Fn(lambda ctx: {**cell, "childTranslators": ctx["visitor"].table_cell_translators})),  # th,td 번역 설정(표 칸)
        ]                                                   # 표 번역기 설정 목록 끝
        for k, v in table_cfg:                              # 표 번역기에 등록
            self.table_translators.set(k, v, True)          # 하나씩 등록
        self.table_row_translators.set("th,td", table_cfg[2][1], True)  # 표 행 번역기에도 th,td 등록
        for k in ("a", "strong,b", "del,s,strike", "em,i", "img"):  # 표 칸 번역기에는 링크·강조·이미지만(tableCellTranslatorConfig)
            self.table_cell_translators.set(k, dt[k], True)  # 해당 번역기를 칸 번역기로 등록

    def translate(self, root: Element) -> str:              # DOM 을 마크다운 글로 번역
        """getMarkdownForHtmlNodes."""
        v = _Visitor(self, root)                            # 방문자가 DOM 을 훑으며 글을 만듦
        result = v.text                                     # 변환된 글
        mx = self.options["maxConsecutiveNewlines"]          # 줄바꿈 4개 이상 → 3개
        ws = JS_WS_CLASS                                    # 공백 클래스
        result = re.sub(r"(?:\r?\n" + ws + r"*)+((?:\r?\n" + ws + r"*){" + str(mx) + r"})", r"\1", result)  # 연속 빈 줄을 maxConsecutiveNewlines 이하로 줄임
        return _trim_newlines(result)                       # 양끝 줄바꿈 제거 후 반환


class _Visitor:                                             # DOM 을 훑으며 마크다운을 쌓는 방문자
    """Visitor — 나무를 돌며 글을 쌓는다."""

    def __init__(self, inst: NodeHtmlMarkdown, root: Element):  # 방문자 초기화
        self.inst = inst                                    # 변환기 참조
        self.options = inst.options                         # 옵션 참조
        self.code_block_translators = inst.code_block_translators  # 코드 블록 번역기 참조
        self.a_tag_translators = inst.a_tag_translators     # 링크 번역기 참조
        self.table_translators = inst.table_translators     # 표 번역기 참조
        self.table_row_translators = inst.table_row_translators  # 표 행 번역기 참조
        self.table_cell_translators = inst.table_cell_translators  # 표 칸 번역기 참조
        self.node_metadata: dict[int, dict] = {}            # 노드별 메타데이터(id(node) → 메타)
        self.text = ""                                       # 결과
        self.trail = (0, 0)                                  # (공백, 줄바꿈)
        self.preserve: dict[int, bool] = {}                  # optimizeTree 결과
        self._optimize(root)                                # 내용 있는 노드 표시(최적화)
        self.visit(root, False, None)                       # 루트부터 방문해 글 생성

    def _optimize(self, root) -> bool:                      # 내용이 남는 노드만 표시(_optimize)
        """optimizeTree — 쓸 내용이 있는 마디 표시."""
        def visit(node) -> bool:                            # 재귀 방문 함수
            res = False                                     # 이 노드를 남길지
            if isinstance(node, Text) or (isinstance(node, Element) and node.tag_name in _CONTENTLESS):  # 글 마디이거나 내용 없어도 남는 요소면
                res = True                                  # 남김
            else:                                           # 그 외엔
                if not node.child_nodes:                    # 자식이 없으면
                    t = self.inst.translators.get(node.tag_name) if node.tag else None   # 이름 없는 뿌리는 번역기 없음
                    if (isinstance(t, dict) and t.get("preserveIfEmpty")) or isinstance(t, _Fn):  # 번역기가 빈 요소도 보존하라고 했거나 동적 설정이면
                        res = True                          # 남김
                else:                                       # 자식이 있으면
                    for ch in node.child_nodes:             # 자식마다
                        r = visit(ch)                       # 재귀로 확인
                        res = res or r                      # 하나라도 남기면 남김
            self.preserve[id(node)] = res                   # 결과 저장
            return res                                      # 결과 반환
        return visit(root)                                  # 루트 방문 결과 반환

    def append(self, s: str, start_pos: Optional[int] = None, space_if_repeat: bool = False) -> None:  # 글 덧붙이기(필요하면 시작 위치부터 잘라내고)
        """appendResult."""
        if not s and start_pos is None:                     # 덧붙일 게 없고 자르기도 아니면
            return                                          # 아무것도 안 함
        if start_pos is not None:                           # 자를 위치가 있으면
            self.text = self.text[:start_pos]               # 그 위치까지만 남김
        self.text += (" " if space_if_repeat and self.text[-1:] == s[:1] and s else "") + s  # 같은 글자 반복이면 공백 하나를 끼워 덧붙임
        self.trail = _trailing_ws_info(self.text)           # 끝 공백 정보 갱신

    def append_newlines(self, count: int) -> None:          # 줄바꿈을 모자란 만큼만 덧붙임
        """appendNewlines."""
        self.append("\n" * max(0, int(count) - self.trail[1]))  # 이미 끝에 있는 줄바꿈은 빼고 추가

    def visit(self, node, text_only: bool, md: Optional[dict]) -> None:  # 노드를 방문해 글을 만든다
        """visitNode."""
        if not self.preserve.get(id(node)):                 # 남기지 않기로 한 노드는
            return                                          # 건너뜀
        if isinstance(node, Text):                           # 글 마디
            mdx = md or {}                                  # 메타(없으면 빈 dict)
            if _is_ws_only(node.data) and not mdx.get("preserveWhitespace"):  # 공백뿐인 글이고 공백 보존 모드가 아니면
                if not self.text or self.trail[0] > 0:      # 맨 앞이거나 이미 끝에 공백이 있으면
                    return                                  # 추가 안 함
                self.append(" ")                            # 공백 한 칸 추가
                return                                      # 끝
            self.append(_process_text(node.data if mdx.get("preserveWhitespace") else _trim_text(node.data), mdx))  # 글 처리 결과를 덧붙임(공백 보존이면 그대로, 아니면 다듬어서)
            return                                          # 끝
        if text_only or not isinstance(node, Element):      # 글만 보는 모드이거나 요소가 아니면
            return                                          # 건너뜀
        tag = node.tag_name if node.tag else None            # 뿌리(이름 없음)
        coll = md["translators"] if md and md.get("translators") else self.inst.translators  # 번역기 모음(자식용이 지정됐으면 그것, 아니면 기본)
        tcf = coll.get(tag) if tag else None                # 이 태그의 번역 설정
        if tag in ("UL", "OL"):                              # 목록 정보
            prev = (md or {}).get("indentLevel")            # 이전 들여쓰기 단계(metadata?.indentLevel ?? -1)
            md = {**(md or {}), "listItemNumber": 0, "listKind": tag, "indentLevel": (-1 if prev is None else prev) + 1}  # 목록 메타: 번호 0, 종류, 들여쓰기 +1
        elif tag == "LI":                                   # li 면
            if md and md.get("listKind") == "OL":           # 번호 목록이면
                md["listItemNumber"] = (md.get("listItemNumber") or 0) + 1  # 번호를 하나 올림
        elif tag == "PRE":                                  # pre 면
            md = {**(md or {}), "preserveWhitespace": True}  # 공백 보존 표시
        elif tag == "TABLE":                                # table 이면
            md = {**(md or {}), "tableMeta": {"node": node}}  # 표 메타에 노드 기록
        if md:                                              # 메타가 있으면
            self.node_metadata[id(node)] = md               # 노드별로 저장
        if not tcf:                                          # 번역기 없음 → 자식만
            for ch in list(node.child_nodes):               # 번역 설정이 없으면 자식만 그대로 방문
                self.visit(ch, text_only, md)               # 자식 방문
            return                                          # 끝
        ctx = None                                          # ctx 초기값
        if isinstance(tcf, _Fn):                             # 함수형
            ctx = self._ctx(node, md, tcf.base)             # 동적 설정이면 ctx 를 만들어 함수 호출
            cfg = {**(tcf.base or {}), **tcf.fn(ctx)}       # 기본 설정 위에 계산 결과를 덮음
        else:                                               # 정적 설정이면
            cfg = tcf                                       # 그대로 사용
        if cfg.get("ignore"):                               # 무시 설정이면
            return                                          # 건너뜀
        if cfg.get("noEscape") and not (md or {}).get("noEscape"):  # noEscape 설정을 자손에게 전달
            md = {**(md or {}), "noEscape": cfg["noEscape"]}  # 메타에 noEscape 기록
            self.node_metadata[id(node)] = md               # 저장
        if cfg.get("childTranslators") is not None and cfg.get("childTranslators") is not (md or {}).get("translators"):  # 하위 번역기 모음을 자손에게 전달
            md = {**(md or {}), "translators": cfg["childTranslators"]}  # 메타에 번역기 기록
            self.node_metadata[id(node)] = md               # 저장
        start_outer = len(self.text)                        # 바깥쪽 시작 위치(제거 시 되돌릴 곳)
        if cfg.get("surroundingNewlines"):                  # 앞쪽 줄바꿈이 필요하면
            self.append_newlines(int(cfg["surroundingNewlines"]))  # 부족한 만큼 추가
        if cfg.get("prefix"):                               # 접두사가 있으면
            self.append(cfg["prefix"])                      # 접두사 추가
        if isinstance(cfg.get("content"), str):             # 고정 내용이 있으면
            self.append(cfg["content"], None, bool(cfg.get("spaceIfRepeatingChar")))  # 그 내용 추가
        else:                                               # 없으면
            start = len(self.text)                          # 자식 글 시작 위치
            for ch in list(node.child_nodes):               # 자식을 차례로
                self.visit(ch, cfg.get("recurse") is False, md)  # 방문(recurse 가 False 면 글만)
            if cfg.get("postprocess"):                      # 후처리 함수가 있으면
                c = {**(ctx or self._ctx(node, md, None)), "content": self.text[start:]}  # ctx 에 자식이 만든 글을 content 로 실어
                res = cfg["postprocess"](c)                 # 후처리 실행
                if res is REMOVE_NODE:                      # 제거 신호면
                    if tag == "LI" and md and md.get("listItemNumber"):  # 번호 목록 항목이면 번호를 되돌림
                        md["listItemNumber"] -= 1           # 번호 하나 감소
                    self.append("", start_outer)            # 쌓은 글을 바깥 시작 위치로 되돌림
                    return                                  # 끝
                if isinstance(res, str):                    # 문자열 결과면
                    self.append(res, start, bool(cfg.get("spaceIfRepeatingChar")))  # 자식 글을 그 결과로 교체
        if cfg.get("postfix"):                              # 접미사가 있으면
            self.append(cfg["postfix"])                     # 접미사 추가
        if cfg.get("surroundingNewlines"):                  # 뒤쪽 줄바꿈이 필요하면
            self.append_newlines(int(cfg["surroundingNewlines"]))  # 부족한 만큼 추가

    def _ctx(self, node, md, base) -> dict:                 # 번역 설정 함수에 넘길 맥락
        """createTranslatorContext."""
        return {"node": node, "options": self.options, "parent": node.parent, "nodeMetadata": self.node_metadata,  # 노드·옵션·부모·메타 모음을 담음
                "visitor": self, "base": base, **(md or {})}  # 방문자·기본 설정·현재 메타 포함


def html_to_markdown(root: Element) -> str:                 # Element 나무를 마크다운 글로
    """NodeHtmlMarkdown.translate(article.content) — content 는 articleContent.innerHTML 이므로 root 의 '자식들'을 변환."""
    wrapper = Element("")                                    # node-html-parser 의 이름 없는 뿌리
    for c in list(root.child_nodes):                         # innerHTML = 자식들
        wrapper.append_child(c)                             # 루트의 자식을 임시 wrapper 로 옮김
    return NodeHtmlMarkdown().translate(wrapper)            # wrapper 를 변환해 반환


def html_string_to_markdown(html_text: str) -> str:         # HTML 문자열을 마크다운 글로
    """HTML 조각 글 → 마크다운(시험용 — NHM 단독 비교)."""
    b = _TreeBuilder()                                      # 파서 생성
    b._enter_body()                                          # 조각은 바로 본문으로
    b.feed(html_text)                                       # HTML 투입
    b.close()                                               # 파싱 마무리
    wrapper = Element("")                                   # 이름 없는 wrapper 요소
    wrapper.child_nodes = list(b.body.child_nodes)          # body 자식을 담음
    for c in wrapper.child_nodes:                           # 각 자식의
        c.parent = wrapper                                  # 부모를 wrapper 로 지정
    return NodeHtmlMarkdown().translate(wrapper)            # 변환해 반환


# ═════════════════════════ 4. 가져오기 + 도구 ═════════════════════════
FETCH_IMPL: Optional[Callable[[str], Any]] = None            # 시험용: async (url) → (status, statusText, 본문 bytes)


async def _http_get(url: str) -> tuple[int, str, bytes]:    # HTTP GET(테스트에서는 FETCH_IMPL 로 바꿔치기 가능)
    """fetch(url) — undici 기본처럼 리디렉션 따라가기. 연결 실패는 'fetch failed'."""
    if FETCH_IMPL is not None:                               # 시험에서 바꿔 끼움
        return await FETCH_IMPL(url)                        # 주입된 구현이 있으면 그것을 사용
    import httpx                                             # 의존성(이미 있음)
    try:                                                    # 실제 요청 시도
        async with httpx.AsyncClient(follow_redirects=True, timeout=None,  # 리디렉션 따라가는 비동기 클라이언트 생성(cn 의 fetch 와 같은 헤더)
                                     headers={"user-agent": "node", "accept": "*/*", "accept-language": "*", "sec-fetch-mode": "cors"}) as c:  # 헤더 값
            r = await c.get(url)                            # GET 요청
            return r.status_code, r.reason_phrase, r.content  # (상태 코드, 상태 문구, 본문 바이트) 반환
    except Exception:                                        # 네트워크 오류 등
        raise Exception("fetch failed")                     # 네트워크 오류는 fetch 와 같은 메시지로


def _check_url(query: str) -> str:                          # URL 문자열 검사
    """new URL(query) — 잘못되면 'Invalid URL'."""
    m = re.match(r"^[\x00-\x20]*([A-Za-z][A-Za-z0-9+.\-]*):", query)  # 스킴 패턴 검사
    if not m:                                               # 스킴이 없으면
        raise Exception("Invalid URL")                      # 잘못된 URL 오류
    scheme = m.group(1).lower()                             # 스킴(소문자)
    if scheme in _SPECIAL and scheme != "file":             # 지원하지 않는 특수 스킴이면
        return _whatwg_href(query)                           # 호스트 없으면 Invalid URL
    return query                                            # 검사 통과한 문자열 반환


async def get_url_context_items(query: str) -> list[dict]:  # URL 의 본문을 마크다운 항목으로 가져옴
    """getUrlContextItems — [{name, description, content(마크다운), uri}]. 파비콘 요청은 결과 글에 영향이 없어 생략."""
    url = _check_url(query)                                 # URL 검사(new URL)
    status, status_text, body = await _http_get(url)         # 가져오기
    if not (200 <= status <= 299):                          # HTTP 상태가 2xx 가 아니면 오류(!resp.ok)
        raise Exception(f"HTTP {status} {status_text}")     # 상태 코드와 문구를 담은 오류
    text = body.decode("utf-8", errors="replace")            # resp.text() — 늘 UTF-8
    if text.startswith("\ufeff"):                            # TextDecoder 는 BOM 제거
        text = text[1:]                                     # BOM 이 있으면 제거
    doc = parse_html(text)                                  # HTML 파싱(new JSDOM)
    article = _Readability(doc).parse()                     # Readability 로 본문 추출
    markdown = html_to_markdown(article["content"]) if article else ""   # 마크다운(본문 없으면 "")
    title = (article or {}).get("title") or urlsplit(url).path or "/"  # 제목: 글 제목, 없으면 경로, 그것도 없으면 /
    return [{"description": url, "content": markdown, "name": title, "uri": {"type": "url", "value": url}}]  # 출처·내용·이름·URI 를 담은 항목 하나 반환


async def fetch_url_content_impl(args: dict) -> list[dict]:  # Fetch 도구의 실제 구현(fetchUrlContent.ts)
    """core fetchUrlContentImpl — 항목마다 2만 자 넘으면 자르고 경고 항목 추가."""
    if not args or "url" not in args:                       # url 인자가 없으면(getStringArg)
        raise Exception("`url` argument is required and must not be empty or whitespace-only. (type string)")  # 필수 인자 오류
    url = args["url"]                                       # url 값
    if isinstance(url, (dict, list)):                        # 객체로 파싱된 경우 → JSON 글
        import json                                         # dict 류 값이면 JSON 문자열로 바꿔서 씀
        url = json.dumps(url, ensure_ascii=False, separators=(",", ":"))  # JSON 직렬화(JS JSON.stringify 와 같은 꼴)
    elif not isinstance(url, str):                          # 문자열이 아니면
        raise Exception("`url` argument is required and must not be empty or whitespace-only. (type string)")  # 필수 인자 오류
    elif not js_trim(url):                                  # 공백뿐이면
        raise Exception("Argument url must not be empty or whitespace-only")  # 비어 있으면 안 된다는 오류
    items = await get_url_context_items(url)                # URL 에서 항목 가져오기
    truncated: list[str] = []                               # 잘린 URL 목록
    out = []                                                # 결과 목록
    for it in items:                                        # 항목마다
        if js_len(it["content"]) > DEFAULT_FETCH_URL_CHAR_LIMIT:   # 항목 한도
            truncated.append(url)                           # 한도를 넘은 항목이면 URL 을 기록
            it = {**it, "content": _js_substring(it["content"], DEFAULT_FETCH_URL_CHAR_LIMIT)}  # 내용을 한도 길이로 자름
        out.append(it)                                      # 결과에 추가
    if truncated:                                           # 잘린 게 있으면
        out.append({"name": "Truncation warning", "description": "",  # 잘림 경고 항목 추가
                    "content": f"The content from {', '.join(truncated)} was truncated because it exceeded the {DEFAULT_FETCH_URL_CHAR_LIMIT} character limit. If you need more content, consider fetching specific sections or using a more targeted approach."})  # 경고 문구(한도 초과 안내)
    return out                                              # 결과 반환


def _js_substring(s: str, end: int) -> str:                 # JS substring 처럼 UTF-16 단위로 자르기
    """substring(0, end) — UTF-16 위치 기준."""
    if js_len(s) == len(s):                                 # BMP 만 있으면 그냥 자름
        return s[:end]                                      # 파이썬 슬라이스 사용
    u = s.encode("utf-16-le", errors="surrogatepass")       # UTF-16 바이트로 변환
    return u[: end * 2].decode("utf-16-le", errors="surrogatepass")  # 코드 단위 2 바이트씩 잘라 되돌림


def _get_fetch_max_chars() -> int:                          # 환경변수에서 Fetch 출력 한도 읽기
    """CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH(기본 20000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH"), DEFAULT_FETCH_MAX_CHARS)  # 없거나 숫자가 아니면 기본값 사용


CONTEXT_ITEMS_IMPL: Optional[Callable[[dict], Any]] = None   # 시험용: fetchUrlContentImpl 바꿔 끼우기(cn 시험의 vi.mock 대응)


async def _preprocess(args: dict) -> PreprocessResult:      # 도구 호출 미리보기(preprocess)
    """preprocess — 'Will fetch: <url>'."""
    url = args.get("url")                                   # url 인자
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Will fetch: {'undefined' if url is None and 'url' not in args else url}")])  # "Will fetch: URL" 미리보기를 담아 반환


async def _run(args: dict, context=None) -> str:            # 도구 실행 본체
    """run — 경고 항목을 뺀 내용을 빈 줄로 잇고 2만 자로 자름. 실패는 'Error: …'."""
    url = args.get("url")                                   # url 인자
    try:                                                    # 오류를 감싸 처리
        impl = CONTEXT_ITEMS_IMPL or fetch_url_content_impl  # 구현
        items = await impl({"url": url})                    # 구현 호출
        if len(items) == 0:                                 # 결과가 없으면
            raise ContinueError("Unspecified", f"Could not fetch content from {url}")  # 가져오지 못했다는 오류
        combined = "\n\n".join(it["content"] for it in items if it.get("name") != "Truncation warning")  # 잘림 경고를 뺀 내용을 이어 붙임
        r = truncate_output_from_end(combined, _get_fetch_max_chars(), "fetched content")  # 출력 한도에 맞춰 뒤를 자름
        return r.output                                     # 잘린 출력 반환
    except ContinueError:                                   # ContinueError 는
        raise                                               # 그대로 전달
    except Exception as e:                                  # 그 외 오류는
        raise Exception(f"Error: {e}")                      # "Error: …" 로 감싸서 전달


FETCH_TOOL = Tool(                                          # Fetch 도구 정의(extensions/cli/src/tools/fetch.ts)
    name="Fetch",                                           # 도구 이름
    display_name="Fetch",                                   # 표시 이름
    description="Fetches content from a URL, converts to markdown, and handles long content with truncation",  # 모델에게 보이는 도구 설명
    parameters={                                            # 인자 스키마
        "type": "object",                                   # 객체 타입
        "required": ["url"],                                # 필수 인자
        "properties": {                                     # 속성 목록
            "url": {                                        # url 인자
                "type": "string",                           # 문자열 타입
                "description": "The URL to fetch content from",  # 인자 설명
            },
        },
    },
    run=_run,                                               # 실행 함수
    preprocess=_preprocess,                                 # 미리보기 함수
    readonly=True,                                          # 읽기 전용 도구
    is_built_in=True,                                       # 내장 도구 표시
)
