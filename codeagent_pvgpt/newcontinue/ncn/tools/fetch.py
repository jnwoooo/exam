# ncn/tools/fetch.py — Fetch 도구(웹 페이지 → 마크다운). cn extensions/cli/src/tools/fetch.ts
#   + core/tools/implementations/fetchUrlContent.ts + core/context/providers/URLContextProvider.ts(getUrlContextItems)
# cn은 JSDOM(HTML 파싱) → @mozilla/readability 0.6.0(본문 고르기) → node-html-markdown 1.3.0(마크다운 변환)을 쓴다.
# ncn은 표준 라이브러리만으로: html.parser 로 DOM 을 만들고, Readability 는 핵심 절차만 줄여 옮기고(차이는 보고서), node-html-markdown 은 규칙을 그대로 옮겼다
from __future__ import annotations                           # 타입 주석 지연 평가

import html as _html                                         # 엔티티 풀기
import math                                                  # floor
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
_NS = "[^" + JS_WS_CHARS + "]"                               # JS \S
_WS_NO_NL = "[" + "".join(c for c in JS_WS_CHARS if c not in "\r\n") + "]"   # JS [^\S\r\n]


# ═════════════════════════ 1. 작은 DOM ═════════════════════════
class Text:
    """글 마디(엔티티는 이미 풀린 글)."""
    node_type = 3                                            # TEXT_NODE

    def __init__(self, data: str):
        self.data = data                                     # 글
        self.parent: Optional[Element] = None                # 부모

    @property
    def text_content(self) -> str:
        """textContent."""
        return self.data


class Element:
    """요소 마디 — tag 는 소문자, attrs 는 이름(소문자) → 값(풀린 글)."""
    node_type = 1                                            # ELEMENT_NODE

    def __init__(self, tag: str, attrs: Optional[dict] = None):
        self.tag = tag                                       # 태그 이름(소문자)
        self.attrs: dict[str, str] = dict(attrs or {})       # 속성
        self.child_nodes: list = []                          # 자식(글·요소)
        self.parent: Optional[Element] = None                # 부모
        self.readability: Optional[dict] = None              # Readability 점수 자리

    @property
    def tag_name(self) -> str:
        """tagName(대문자)."""
        return self.tag.upper()

    @property
    def children(self) -> list["Element"]:
        """children(요소만)."""
        return [c for c in self.child_nodes if isinstance(c, Element)]

    @property
    def text_content(self) -> str:
        """textContent — 자손 글을 이어 붙임."""
        out: list[str] = []
        stack = [self]                                       # 반복으로(깊은 문서 대비)
        while stack:
            n = stack.pop()
            if isinstance(n, Text):
                out.append(n.data)
            else:
                stack.extend(reversed(n.child_nodes))
        return "".join(out)

    def get_attribute(self, name: str) -> Optional[str]:
        """getAttribute(없으면 None)."""
        return self.attrs.get(name.lower())

    def append_child(self, node) -> None:
        """appendChild(다른 곳에 있으면 떼어 옮김)."""
        if node.parent is not None:
            node.parent.remove_child(node)
        node.parent = self
        self.child_nodes.append(node)

    def remove_child(self, node) -> None:
        """removeChild."""
        self.child_nodes.remove(node)
        node.parent = None

    def replace_child(self, new, old) -> None:
        """replaceChild(new, old)."""
        if new.parent is not None:
            new.parent.remove_child(new)
        i = self.child_nodes.index(old)
        self.child_nodes[i] = new
        new.parent = self
        old.parent = None

    def iter(self):
        """자신 포함 자손 요소(문서 순서)."""
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def get_elements_by_tag(self, *tags: str) -> list["Element"]:
        """getElementsByTagName / querySelectorAll(여러 태그) — 자신 제외, 문서 순서."""
        want = set(tags)
        return [e for e in self.iter() if e is not self and e.tag in want]


def _remove(node) -> None:
    """node.remove()."""
    if node.parent is not None:
        node.parent.remove_child(node)


def _next_sibling(node):
    """nextSibling."""
    p = node.parent
    if p is None:
        return None
    i = p.child_nodes.index(node)
    return p.child_nodes[i + 1] if i + 1 < len(p.child_nodes) else None


def _next_element_sibling(node):
    """nextElementSibling."""
    p = node.parent
    if p is None:
        return None
    sib = p.child_nodes[p.child_nodes.index(node) + 1:]
    return next((s for s in sib if isinstance(s, Element)), None)


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr", "keygen"}   # 닫는 태그 없는 요소
_CLOSES_P = {"address", "article", "aside", "blockquote", "details", "dialog", "div", "dl", "fieldset", "figcaption", "figure",
             "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p",
             "pre", "section", "table", "ul", "li", "dd", "dt"}   # 열린 <p> 를 닫는 시작 태그(HTML 규칙)
_HEAD_TAGS = {"title", "meta", "link", "style", "script", "base", "noscript", "template"}   # 본문 전이면 head 로


class _TreeBuilder(HTMLParser):
    """HTML → Element 나무. HTML5 나무 만들기 규칙 중 자주 쓰는 것만(암묵적 html/head/body, <p>·<li>·표 닫기, tbody 넣기)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)              # 엔티티 자동 풀기
        self.html = Element("html")                          # 문서 요소
        self.head = Element("head")                          # head
        self.body = Element("body")                          # body
        self.html.append_child(self.head)
        self.html.append_child(self.body)
        self.in_body = False                                 # 본문 시작했나
        self.stack: list[Element] = [self.html, self.head]   # 열린 요소들
        self.skip_lf = False                                 # <pre> 바로 뒤 줄바꿈 하나 버리기

    def _cur(self) -> Element:
        """지금 넣을 곳."""
        return self.stack[-1]

    def _enter_body(self) -> None:
        """본문 시작 — head 를 닫고 body 로."""
        if not self.in_body:
            self.in_body = True
            self.stack = [self.html, self.body]

    def _close_until(self, names: set, stop: set = frozenset()) -> None:
        """열린 요소 중 names 를 찾아 거기까지 닫음(stop 을 먼저 만나면 그만)."""
        for i in range(len(self.stack) - 1, 1, -1):
            t = self.stack[i].tag
            if t in stop:
                return
            if t in names:
                del self.stack[i:]
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()                                    # 소문자
        if tag == "html":                                    # 속성만 합침
            for k, v in attrs:
                self.html.attrs.setdefault(k.lower(), v or "")
            return
        if tag == "head":
            return
        if tag == "body":                                    # 속성 합치고 본문으로
            self._enter_body()
            for k, v in attrs:
                self.body.attrs.setdefault(k.lower(), v or "")
            return
        if not self.in_body and tag not in _HEAD_TAGS:       # head 에 못 들어가는 것 → 본문 시작
            self._enter_body()
        if self.in_body:                                     # 암묵적 닫기
            if tag in _CLOSES_P:
                self._close_until({"p"}, stop={"button", "table", "td", "th", "li"} if tag != "li" else {"ul", "ol", "table", "td", "th"})
            if tag == "li":
                self._close_until({"li"}, stop={"ul", "ol", "table"})
            if tag in ("dd", "dt"):
                self._close_until({"dd", "dt"}, stop={"dl", "table"})
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._cur().tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.stack.pop()
            if tag in ("td", "th"):
                self._close_until({"td", "th"}, stop={"tr", "table"})
            if tag == "tr":
                self._close_until({"tr", "td", "th"}, stop={"table", "tbody", "thead", "tfoot"})
                if self._cur().tag == "table":               # 표 바로 아래 tr → tbody 넣기
                    tb = Element("tbody")
                    self._cur().append_child(tb)
                    self.stack.append(tb)
            if tag in ("tbody", "thead", "tfoot"):
                self._close_until({"tbody", "thead", "tfoot", "tr", "td", "th"}, stop={"table"})
            if tag in ("td", "th") and self._cur().tag in ("table", "tbody", "thead", "tfoot"):   # tr 없이 칸 → tr 넣기
                if self._cur().tag == "table":
                    tb = Element("tbody")
                    self._cur().append_child(tb)
                    self.stack.append(tb)
                tr = Element("tr")
                self._cur().append_child(tr)
                self.stack.append(tr)
            if tag == "option":
                self._close_until({"option"}, stop={"select"})
            if tag == "a":                                   # a 안의 a 는 앞 a 를 닫음
                self._close_until({"a"})
        el = Element(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})   # 새 요소(같은 이름 속성은 처음 것)
        self._cur().append_child(el)
        if tag not in _VOID:                                 # 닫는 태그가 있는 것만 열어 둠
            self.stack.append(el)
            if tag in ("pre", "textarea", "listing"):        # 시작 직후 줄바꿈 하나 무시(HTML 규칙)
                self.skip_lf = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)                     # <x/> — HTML 에서는 그냥 시작 태그
        if tag.lower() not in _VOID and self._cur().tag == tag.lower():
            self.stack.pop()                                 # (외부 SVG 등) 바로 닫음

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("html", "body", "head"):                  # 무시(끝까지 받음)
            if tag == "head" and not self.in_body:
                pass
            return
        if tag == "br":                                      # </br> 은 <br> 로(HTML 규칙)
            self.handle_starttag("br", [])
            return
        for i in range(len(self.stack) - 1, 1, -1):          # 가장 가까운 같은 요소까지 닫기
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.skip_lf:                                     # <pre> 뒤 첫 줄바꿈
            self.skip_lf = False
            if data.startswith("\n"):
                data = data[1:]
            if not data:
                return
        if not self.in_body:                                 # head 안
            if self._cur().tag in ("title", "style", "script", "noscript", "template"):
                self._cur().append_child(Text(data))
                return
            if not data.strip(JS_WS_CHARS):                  # 공백은 버림
                return
            self._enter_body()                               # 글이 나오면 본문 시작
        cur = self._cur()
        if cur.child_nodes and isinstance(cur.child_nodes[-1], Text):   # 이웃 글은 합침(DOM 과 같게)
            cur.child_nodes[-1].data += data
        else:
            cur.append_child(Text(data))

    def handle_comment(self, data):
        pass                                                 # 주석 버림(Readability·NHM 모두 영향 없음)


def parse_html(html: str) -> _TreeBuilder:
    """HTML 글 → 문서(html/head/body)."""
    b = _TreeBuilder()
    try:
        b.feed(html)
        b.close()
    except Exception:                                        # 깨진 문서도 거기까지
        pass
    return b


# ═════════════════════════ 2. Readability(축약 이식) ═════════════════════════
_RX = {                                                      # REGEXPS(cn이 쓰는 0.6.0 원문)
    "unlikely": re.compile(r"-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|yom-remote", re.I),
    "maybe": re.compile(r"and|article|body|column|content|main|shadow", re.I),
    "positive": re.compile(r"article|body|content|entry|hentry|h-entry|main|page|pagination|post|text|blog|story", re.I),
    "negative": re.compile(r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|gdpr|masthead|media|meta|outbrain|promo|related|scroll|share|shoutbox|sidebar|skyscraper|sponsor|shopping|tags|widget", re.I),
    "byline": re.compile(r"byline|author|dateline|writtenby|p-author", re.I),
    "normalize": re.compile(JS_WS_CLASS + r"{2,}"),
    "share": re.compile(r"(\b|_)(share|sharedaddy)(\b|_)", re.I),
    "hash": re.compile(r"^#[\s\S]+"),
    "commas": re.compile(",|،|﹐|︐|︑|⹁|⸴|⸲|，"),
    "tokenize": re.compile(r"[^A-Za-z0-9_]+"),
}
_UNLIKELY_ROLES = ["menu", "menubar", "complementary", "navigation", "alert", "alertdialog", "dialog"]
_DIV_TO_P = {"BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"}
_ALTER_TO_DIV_EXC = ["DIV", "ARTICLE", "SECTION", "P", "OL", "UL"]
_PHRASING = {"ABBR", "AUDIO", "B", "BDO", "BR", "BUTTON", "CITE", "CODE", "DATA", "DATALIST", "DFN", "EM", "EMBED", "I", "IMG",
             "INPUT", "KBD", "LABEL", "MARK", "MATH", "METER", "NOSCRIPT", "OBJECT", "OUTPUT", "PROGRESS", "Q", "RUBY", "SAMP",
             "SCRIPT", "SELECT", "SMALL", "SPAN", "STRONG", "SUB", "SUP", "TEXTAREA", "TIME", "VAR", "WBR"}
_TAGS_TO_SCORE = {"SECTION", "H2", "H3", "H4", "H5", "H6", "P", "TD", "PRE"}
_VIDEO_RX = re.compile(r"\/\/(www\.)?((dailymotion|youtube|youtube-nocookie|player\.vimeo|v\.qq)\.com|(archive|upload\.wikimedia)\.org|player\.twitch\.tv)", re.I)


def _cls(e: Element) -> str:
    """className."""
    return e.get_attribute("class") or ""


def _id(e: Element) -> str:
    """id."""
    return e.get_attribute("id") or ""


class _Readability:
    """Readability 0.6.0 의 parse() 핵심: 스크립트·숨김·광고성 요소 제거 → 점수로 본문 고르기 → 정리.
    빠진 것: JSON-LD 메타데이터, _cleanConditionally(표·목록·div 조건부 제거), _markDataTables, _fixLazyImages, _unwrapNoscriptImages."""

    F_STRIP, F_WEIGHT, F_CLEAN = 1, 2, 4                     # 플래그

    def __init__(self, doc: _TreeBuilder):
        self.doc = doc                                       # 문서
        self.flags = 7                                       # 모두 켜고 시작
        self.title = ""                                      # 제목
        self.byline: Optional[str] = None                    # 작성자
        self.meta_byline: Optional[str] = None               # 메타 작성자
        self.attempts: list = []                             # 시도들

    # ── 도우미 ──
    def _active(self, f: int) -> bool:
        return (self.flags & f) > 0

    def _inner_text(self, e, normalize: bool = True) -> str:
        """_getInnerText — trim 후 공백 2개 이상을 하나로."""
        t = js_trim(e.text_content)
        return _RX["normalize"].sub(" ", t) if normalize else t

    def _link_density(self, e: Element) -> float:
        """_getLinkDensity — 링크 글 길이 / 전체 글 길이(#링크는 0.3배)."""
        tl = js_len(self._inner_text(e))
        if tl == 0:
            return 0
        ll = 0.0
        for a in e.get_elements_by_tag("a"):
            href = a.get_attribute("href")
            coef = 0.3 if href and _RX["hash"].search(href) else 1
            ll += js_len(self._inner_text(a)) * coef
        return ll / tl

    def _class_weight(self, e: Element) -> int:
        """_getClassWeight — class·id 에 positive/negative 가 있으면 ±25."""
        if not self._active(self.F_WEIGHT):
            return 0
        w = 0
        for s in (_cls(e), _id(e)):
            if s:
                if _RX["negative"].search(s):
                    w -= 25
                if _RX["positive"].search(s):
                    w += 25
        return w

    def _init_node(self, e: Element) -> None:
        """_initializeNode — 태그별 기본 점수 + class 가중치."""
        e.readability = {"contentScore": 0}
        t = e.tag_name
        if t == "DIV":
            e.readability["contentScore"] += 5
        elif t in ("PRE", "TD", "BLOCKQUOTE"):
            e.readability["contentScore"] += 3
        elif t in ("ADDRESS", "OL", "UL", "DL", "DD", "DT", "LI", "FORM"):
            e.readability["contentScore"] -= 3
        elif t in ("H1", "H2", "H3", "H4", "H5", "H6", "TH"):
            e.readability["contentScore"] -= 5
        e.readability["contentScore"] += self._class_weight(e)

    @staticmethod
    def _next_node(node: Element, ignore_self_and_kids: bool = False):
        """_getNextNode — 깊이 우선 다음 요소."""
        if not ignore_self_and_kids and node.children:
            return node.children[0]
        n = _next_element_sibling(node)
        if n:
            return n
        while True:
            node = node.parent
            if node is None:
                return None
            n = _next_element_sibling(node)
            if n:
                return n

    def _remove_and_next(self, node: Element):
        """_removeAndGetNext."""
        nxt = self._next_node(node, True)
        _remove(node)
        return nxt

    @staticmethod
    def _set_tag(node: Element, tag: str) -> Element:
        """_setNodeTag — 같은 자리에 새 태그 요소(자식·속성·점수 옮김)."""
        rep = Element(tag.lower(), node.attrs)
        rep.readability = node.readability
        for c in list(node.child_nodes):
            rep.append_child(c)
        if node.parent is not None:
            node.parent.replace_child(rep, node)
        return rep

    def _is_phrasing(self, n) -> bool:
        """_isPhrasingContent."""
        if isinstance(n, Text):
            return True
        if n.tag_name in _PHRASING:
            return True
        return n.tag_name in ("A", "DEL", "INS") and all(self._is_phrasing(c) for c in n.child_nodes)

    @staticmethod
    def _is_ws(n) -> bool:
        """_isWhitespace — 빈 글 또는 BR."""
        if isinstance(n, Text):
            return len(js_trim(n.data)) == 0
        return n.tag_name == "BR"

    @staticmethod
    def _has_single_tag(e: Element, tag: str) -> bool:
        """_hasSingleTagInsideElement."""
        ch = e.children
        if len(ch) != 1 or ch[0].tag_name != tag:
            return False
        return not any(isinstance(n, Text) and re.search(_NS + r"$", n.data) for n in e.child_nodes)   # /\S$/

    def _has_child_block(self, e) -> bool:
        """_hasChildBlockElement."""
        return any(isinstance(n, Element) and (n.tag_name in _DIV_TO_P or self._has_child_block(n)) for n in e.child_nodes)

    @staticmethod
    def _without_content(n: Element) -> bool:
        """_isElementWithoutContent."""
        if len(js_trim(n.text_content)):
            return False
        ch = n.children
        return not ch or len(ch) == len(n.get_elements_by_tag("br")) + len(n.get_elements_by_tag("hr"))

    @staticmethod
    def _has_ancestor(node, tag: str, max_depth: int = 3) -> bool:
        """_hasAncestorTag(node, tag) — 기본 깊이 3."""
        tag = tag.upper()
        depth = 0
        while node.parent is not None:
            if max_depth > 0 and depth > max_depth:
                return False
            if node.parent.tag_name == tag:
                return True
            node = node.parent
            depth += 1
        return False

    @staticmethod
    def _visible(n: Element) -> bool:
        """_isProbablyVisible — style display:none / visibility:hidden, hidden, aria-hidden=true(fallback-image 제외)."""
        style = (n.get_attribute("style") or "").lower()
        decl = {}
        for part in style.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                decl[k.strip()] = v.strip()
        if decl.get("display") == "none" or decl.get("visibility") == "hidden":
            return False
        if "hidden" in n.attrs:
            return False
        if n.get_attribute("aria-hidden") == "true" and "fallback-image" not in _cls(n):
            return False
        return True

    def _text_similarity(self, a: str, b: str) -> float:
        """_textSimilarity."""
        ta = [t for t in _RX["tokenize"].split(a.lower()) if t]
        tb = [t for t in _RX["tokenize"].split(b.lower()) if t]
        if not ta or not tb:
            return 0
        uniq = [t for t in tb if t not in ta]
        return 1 - len(" ".join(uniq)) / len(" ".join(tb))

    def _header_dup_title(self, n: Element) -> bool:
        """_headerDuplicatesTitle."""
        if n.tag_name not in ("H1", "H2"):
            return False
        return self._text_similarity(self.title, self._inner_text(n, False)) > 0.75

    def _valid_byline(self, n: Element, match: str) -> bool:
        """_isValidByline."""
        rel = n.get_attribute("rel")
        itemprop = n.get_attribute("itemprop")
        ln = js_len(js_trim(n.text_content))
        return (rel == "author" or bool(itemprop and "author" in itemprop) or bool(_RX["byline"].search(match))) and bool(ln) and ln < 100

    # ── 제목(_getArticleTitle + 메타 일부) ──
    def _article_title(self) -> str:
        """메타(og:title·twitter:title·dc:title 등) → 없으면 <title> 규칙."""
        metas = {}
        for m in self.doc.html.get_elements_by_tag("meta"):
            content = m.get_attribute("content")
            if not content:
                continue
            for key in ((m.get_attribute("property") or "") + " " + (m.get_attribute("name") or "")).split():
                k = key.lower().replace(".", ":")
                metas[k] = js_trim(content)
        for k in ("dc:title", "dcterm:title", "og:title", "weibo:article:title", "weibo:webpage:title", "title", "twitter:title", "parsely-title"):
            if metas.get(k):
                self._set_meta_byline(metas)
                return metas[k]
        self._set_meta_byline(metas)
        titles = self.doc.html.get_elements_by_tag("title")
        orig = js_trim(titles[0].text_content) if titles else ""
        cur = orig
        had_hier = False

        def wc(s: str) -> int:
            return len(re.split(JS_WS_CLASS + "+", s))
        if re.search(r" [\|\-\\\/>»] ", cur):
            had_hier = bool(re.search(r" [\\\/>»] ", cur))
            seps = list(re.finditer(r" [\|\-\\\/>»] ", orig))
            cur = orig[: seps[-1].start()]
            if wc(cur) < 3:
                cur = re.sub(r"^[^\|\-\\\/>»]*[\|\-\\\/>»]", "", orig)
        elif ": " in cur:
            hs = self.doc.html.get_elements_by_tag("h1", "h2")
            if not any(js_trim(h.text_content) == js_trim(cur) for h in hs):
                cur = orig[orig.rfind(":") + 1:]
                if wc(cur) < 3:
                    cur = orig[orig.find(":") + 1:]
                elif wc(orig[: orig.find(":")]) > 5:
                    cur = orig
        elif js_len(cur) > 150 or js_len(cur) < 15:
            h1s = self.doc.html.get_elements_by_tag("h1")
            if len(h1s) == 1:
                cur = self._inner_text(h1s[0])
        cur = _RX["normalize"].sub(" ", js_trim(cur))
        n = wc(cur)
        if n <= 4 and (not had_hier or n != wc(re.sub(r"[\|\-\\\/>»]+", "", orig)) - 1):
            cur = orig
        return cur

    def _set_meta_byline(self, metas: dict) -> None:
        """메타 작성자(dc:creator·author 등) — 있으면 본문의 작성자 요소를 지우지 않음."""
        for k in ("dc:creator", "dcterm:creator", "author", "parsely-author"):
            if metas.get(k):
                self.meta_byline = metas[k]
                return

    # ── 문서 준비 ──
    def _prep_document(self) -> None:
        """_removeScripts + _prepDocument(style 제거, <br><br> → <p>, font → span)."""
        for e in self.doc.html.get_elements_by_tag("script", "noscript", "style"):
            _remove(e)
        self._replace_brs(self.doc.body)
        for f in self.doc.html.get_elements_by_tag("font"):
            self._set_tag(f, "span")

    def _skip_ws(self, n):
        """_nextNode — 공백 글은 건너뛴 다음 마디."""
        while n is not None and not isinstance(n, Element) and not js_trim(n.text_content):
            n = _next_sibling(n)
        return n

    def _replace_brs(self, elem: Element) -> None:
        """_replaceBrs — 연속 <br> 두 개 이상을 <p> 로."""
        for br in elem.get_elements_by_tag("br"):
            if br.parent is None:
                continue
            nxt = _next_sibling(br)
            replaced = False
            while True:
                nxt = self._skip_ws(nxt)
                if not (nxt is not None and isinstance(nxt, Element) and nxt.tag_name == "BR"):
                    break
                replaced = True
                sib = _next_sibling(nxt)
                _remove(nxt)
                nxt = sib
            if replaced:
                p = Element("p")
                br.parent.replace_child(p, br)
                nxt = _next_sibling(p)
                while nxt is not None:
                    if isinstance(nxt, Element) and nxt.tag_name == "BR":
                        ne = self._skip_ws(_next_sibling(nxt))
                        if ne is not None and isinstance(ne, Element) and ne.tag_name == "BR":
                            break
                    if not self._is_phrasing(nxt):
                        break
                    sib = _next_sibling(nxt)
                    p.append_child(nxt)
                    nxt = sib
                while p.child_nodes and self._is_ws(p.child_nodes[-1]):
                    _remove(p.child_nodes[-1])
                if p.parent is not None and p.parent.tag_name == "P":
                    self._set_tag(p.parent, "div")

    # ── 본문 고르기(_grabArticle) ──
    def _grab(self) -> Optional[Element]:
        import copy                                          # 실패 시 원래 본문으로 되돌리기
        body = self.doc.body
        page_cache = copy.deepcopy(body.child_nodes)         # page.innerHTML 저장 대응
        while True:
            strip = self._active(self.F_STRIP)
            to_score: list[Element] = []
            node = self.doc.html
            remove_title_header = True
            while node is not None:
                match = _cls(node) + " " + _id(node)
                if not self._visible(node):                  # 숨김
                    node = self._remove_and_next(node)
                    continue
                if node.get_attribute("aria-modal") == "true" and node.get_attribute("role") == "dialog":
                    node = self._remove_and_next(node)
                    continue
                if not self.byline and not self.meta_byline and self._valid_byline(node, match):   # 작성자 요소
                    self.byline = js_trim(node.text_content)
                    node = self._remove_and_next(node)
                    continue
                if remove_title_header and self._header_dup_title(node):   # 제목과 같은 머리글
                    remove_title_header = False
                    node = self._remove_and_next(node)
                    continue
                if strip:                                    # 광고·메뉴 등
                    if (_RX["unlikely"].search(match) and not _RX["maybe"].search(match) and not self._has_ancestor(node, "table")
                            and not self._has_ancestor(node, "code") and node.tag_name not in ("BODY", "A")):
                        node = self._remove_and_next(node)
                        continue
                    if node.get_attribute("role") in _UNLIKELY_ROLES:
                        node = self._remove_and_next(node)
                        continue
                if node.tag_name in ("DIV", "SECTION", "HEADER", "H1", "H2", "H3", "H4", "H5", "H6") and self._without_content(node):
                    node = self._remove_and_next(node)
                    continue
                if node.tag_name in _TAGS_TO_SCORE:
                    to_score.append(node)
                if node.tag_name == "DIV":                   # div 안의 글 덩어리를 p 로 묶기
                    p = None
                    child = node.child_nodes[0] if node.child_nodes else None
                    while child is not None:
                        ns = _next_sibling(child)
                        if self._is_phrasing(child):
                            if p is not None:
                                p.append_child(child)
                            elif not self._is_ws(child):
                                p = Element("p")
                                node.replace_child(p, child)
                                p.append_child(child)
                        elif p is not None:
                            while p.child_nodes and self._is_ws(p.child_nodes[-1]):
                                _remove(p.child_nodes[-1])
                            p = None
                        child = ns
                    if self._has_single_tag(node, "P") and self._link_density(node) < 0.25:
                        nn = node.children[0]
                        node.parent.replace_child(nn, node)
                        node = nn
                        to_score.append(node)
                    elif not self._has_child_block(node):
                        node = self._set_tag(node, "p")
                        to_score.append(node)
                node = self._next_node(node)

            candidates: list[Element] = []                   # 점수 매기기
            for el in to_score:
                if el.parent is None:
                    continue
                inner = self._inner_text(el)
                if js_len(inner) < 25:
                    continue
                ancestors = []
                a = el
                while a.parent is not None and len(ancestors) < 5:
                    ancestors.append(a.parent)
                    a = a.parent
                if not ancestors:
                    continue
                score = 1 + len(_RX["commas"].split(inner)) + min(math.floor(js_len(inner) / 100), 3)
                for level, anc in enumerate(ancestors):
                    if anc.parent is None:
                        continue
                    if anc.readability is None:
                        self._init_node(anc)
                        candidates.append(anc)
                    div = 1 if level == 0 else (2 if level == 1 else level * 3)
                    anc.readability["contentScore"] += score / div

            top: list[Element] = []                          # 상위 5개
            for c in candidates:
                s = c.readability["contentScore"] * (1 - self._link_density(c))
                c.readability["contentScore"] = s
                for t in range(5):
                    if t >= len(top) or s > top[t].readability["contentScore"]:
                        top.insert(t, c)
                        if len(top) > 5:
                            top.pop()
                        break
            tc = top[0] if top else None
            created = False
            if tc is None or tc.tag_name == "BODY":          # 후보 없음 → 본문 전체
                tc = Element("div")
                created = True
                for ch in list(body.child_nodes):
                    tc.append_child(ch)
                body.append_child(tc)
                self._init_node(tc)
            else:
                alts = [self._ancestors(t) for t in top[1:] if t.readability["contentScore"] / tc.readability["contentScore"] >= 0.75] if tc.readability["contentScore"] else []
                if len(alts) >= 3:
                    pt = tc.parent
                    while pt is not None and pt.tag_name != "BODY":
                        if sum(1 for al in alts if pt in al) >= 3:
                            tc = pt
                            break
                        pt = pt.parent
                if tc.readability is None:
                    self._init_node(tc)
                pt = tc.parent
                last = tc.readability["contentScore"]
                thr = last / 3
                while pt is not None and pt.tag_name != "BODY":
                    if pt.readability is None:
                        pt = pt.parent
                        continue
                    ps = pt.readability["contentScore"]
                    if ps < thr:
                        break
                    if ps > last:
                        tc = pt
                        break
                    last = ps
                    pt = pt.parent
                pt = tc.parent
                while pt is not None and pt.tag_name != "BODY" and len(pt.children) == 1:
                    tc = pt
                    pt = tc.parent
                if tc.readability is None:
                    self._init_node(tc)

            article = Element("div")                         # 형제 모으기
            sib_thr = max(10, tc.readability["contentScore"] * 0.2)
            parent = tc.parent
            for sib in list(parent.children):
                append = False
                if sib is tc:
                    append = True
                else:
                    bonus = tc.readability["contentScore"] * 0.2 if _cls(sib) == _cls(tc) and _cls(tc) != "" else 0
                    if sib.readability and sib.readability["contentScore"] + bonus >= sib_thr:
                        append = True
                    elif sib.tag_name == "P":
                        ld = self._link_density(sib)
                        content = self._inner_text(sib)
                        ln = js_len(content)
                        if ln > 80 and ld < 0.25:
                            append = True
                        elif 0 < ln < 80 and ld == 0 and re.search(r"\.( |$)", content):
                            append = True
                if append:
                    if sib.tag_name not in _ALTER_TO_DIV_EXC:
                        sib = self._set_tag(sib, "div")
                    article.append_child(sib)
            self._prep_article(article)
            if created:
                tc.attrs["id"] = "readability-page-1"
                tc.attrs["class"] = "page"
            else:
                div = Element("div", {"id": "readability-page-1", "class": "page"})
                for ch in list(article.child_nodes):
                    div.append_child(ch)
                article.append_child(div)
            tl = js_len(self._inner_text(article, True))
            if tl < 500:                                     # charThreshold — 플래그를 하나씩 끄고 다시
                body.child_nodes = []
                for ch in list(copy.deepcopy(page_cache)):   # 복사본 목록 = 복사된 부모의 자식 목록이라 미리 list 로 떼어 둠
                    body.append_child(ch)
                self.attempts.append((article, tl))
                if self._active(self.F_STRIP):
                    self.flags &= ~self.F_STRIP
                elif self._active(self.F_WEIGHT):
                    self.flags &= ~self.F_WEIGHT
                elif self._active(self.F_CLEAN):
                    self.flags &= ~self.F_CLEAN
                else:
                    self.attempts.sort(key=lambda x: -x[1])  # 가장 긴 것(안정 정렬)
                    if not self.attempts[0][1]:
                        return None
                    return self.attempts[0][0]
                continue
            return article

    @staticmethod
    def _ancestors(n: Element) -> list:
        """_getNodeAncestors(제한 없음)."""
        out = []
        while n.parent is not None:
            out.append(n.parent)
            n = n.parent
        return out

    def _clean(self, e: Element, tag: str) -> None:
        """_clean — 그 태그 제거(object·embed·iframe 은 동영상 주소면 남김)."""
        is_embed = tag in ("object", "embed", "iframe")
        for el in reversed(e.get_elements_by_tag(tag)):
            if el.parent is None:
                continue
            if is_embed and any(_VIDEO_RX.search(v or "") for v in el.attrs.values()):
                continue
            _remove(el)

    # ── 표·조건부 정리(_markDataTables / _getRowAndColumnCount / _getTextDensity / _cleanConditionally) ──
    @staticmethod
    def _parse_int(v: Optional[str]) -> int:
        """parseInt(v, 10) — 앞 숫자만, 없으면 0(NaN 은 || 1 로 1 이 되게 0 반환)."""
        m = re.match(r"^[" + JS_WS_CHARS + r"]*([+-]?\d+)", v or "")
        return int(m.group(1)) if m else 0

    def _row_col_count(self, table: Element) -> tuple[int, int]:
        """_getRowAndColumnCount — rowspan·colspan 반영."""
        rows = cols = 0
        for tr in table.get_elements_by_tag("tr"):
            rs = self._parse_int(tr.get_attribute("rowspan")) if tr.get_attribute("rowspan") else 0
            rows += rs or 1
            in_row = 0
            for td in tr.get_elements_by_tag("td"):
                cs = self._parse_int(td.get_attribute("colspan")) if td.get_attribute("colspan") else 0
                in_row += cs or 1
            cols = max(cols, in_row)
        return rows, cols

    def _mark_data_tables(self, root: Element) -> None:
        """_markDataTables — 자료 표인지 표시(자료 표는 조건부 정리에서 보호)."""
        for table in root.get_elements_by_tag("table"):
            if table.get_attribute("role") == "presentation":
                table.data_table = False
                continue
            if table.get_attribute("datatable") == "0":
                table.data_table = False
                continue
            if table.get_attribute("summary"):
                table.data_table = True
                continue
            caps = table.get_elements_by_tag("caption")
            if caps and caps[0].child_nodes:
                table.data_table = True
                continue
            if any(table.get_elements_by_tag(t) for t in ("col", "colgroup", "tfoot", "thead", "th")):
                table.data_table = True
                continue
            if table.get_elements_by_tag("table"):
                table.data_table = False
                continue
            rows, cols = self._row_col_count(table)
            if cols == 1 or rows == 1:
                table.data_table = False
                continue
            if rows >= 10 or cols > 4:
                table.data_table = True
                continue
            table.data_table = rows * cols > 10

    def _fix_lazy_images(self, root: Element) -> None:
        """_fixLazyImages — data-src 같은 속성의 그림 주소를 src/srcset 으로."""
        b64 = re.compile(r"^data:\s*([^\s;,]+)\s*;\s*base64\s*,", re.I)
        for elem in root.get_elements_by_tag("img", "picture", "figure"):
            src = elem.get_attribute("src") or ""
            m = b64.match(src)
            if src and m:
                if m.group(1) == "image/svg+xml":
                    continue
                could = any(k != "src" and re.search(r"\.(jpg|jpeg|png|webp)", v or "", re.I) for k, v in elem.attrs.items())
                if could and js_len(src) - len(m.group(0)) < 133:
                    elem.attrs.pop("src", None)
            src = elem.get_attribute("src") or ""
            srcset = elem.get_attribute("srcset")
            if (src or (srcset and srcset != "null")) and "lazy" not in _cls(elem).lower():
                continue
            for k, v in list(elem.attrs.items()):
                if k in ("src", "srcset", "alt"):
                    continue
                copy_to = None
                if re.search(r"\.(jpg|jpeg|png|webp)\s+\d", v or ""):
                    copy_to = "srcset"
                elif re.search(r"^\s*\S+\.(jpg|jpeg|png|webp)\S*\s*$", v or ""):
                    copy_to = "src"
                if copy_to:
                    if elem.tag_name in ("IMG", "PICTURE"):
                        elem.attrs[copy_to] = v
                    elif elem.tag_name == "FIGURE" and not elem.get_elements_by_tag("img", "picture"):
                        img = Element("img", {copy_to: v})
                        elem.append_child(img)

    def _text_density(self, e: Element, tags: list[str]) -> float:
        """_getTextDensity — 그 태그들 글 길이 / 전체 글 길이."""
        tl = js_len(self._inner_text(e, True))
        if tl == 0:
            return 0
        return sum(js_len(self._inner_text(c, True)) for c in e.get_elements_by_tag(*[t.lower() for t in tags])) / tl

    def _has_ancestor_fn(self, node, tag: str, fn) -> bool:
        """_hasAncestorTag(node, tag, -1, fn) — 깊이 제한 없음."""
        while node.parent is not None:
            if node.parent.tag_name == tag.upper() and fn(node.parent):
                return True
            node = node.parent
        return False

    _AD_WORDS = re.compile(r"^(ad(vertising|vertisement)?|pub(licité)?|werb(ung)?|广告|Реклама|Anuncio)$", re.I)
    _LOADING_WORDS = re.compile(r"^((loading|正在加载|Загрузка|chargement|cargando)(…|\.\.\.)?)$", re.I)

    def _clean_conditionally(self, e: Element, tag: str) -> None:
        """_cleanConditionally — 글보다 링크·그림·입력이 많은 등 '내용 같지 않은' 요소 제거."""
        if not self._active(self.F_CLEAN):
            return
        is_dt = lambda t: bool(getattr(t, "data_table", False))   # 자료 표?
        for node in reversed(e.get_elements_by_tag(tag)):   # 뒤에서부터(_removeNodes)
            if node.parent is None:
                continue
            if self._should_remove(node, tag, is_dt):
                _remove(node)

    def _should_remove(self, node: Element, tag: str, is_dt) -> bool:
        """_cleanConditionally 의 판정 함수."""
        is_list = tag in ("ul", "ol")
        if not is_list:
            ll = sum(js_len(self._inner_text(l)) for l in node.get_elements_by_tag("ul", "ol"))
            tl = js_len(self._inner_text(node))
            is_list = (ll / tl > 0.9) if tl else (ll > 0)    # 0/0=NaN(거짓), x/0=Infinity(참)
        if tag == "table" and is_dt(node):
            return False
        if self._has_ancestor_fn(node, "table", is_dt):
            return False
        if self._has_ancestor(node, "code"):
            return False
        if any(is_dt(t) for t in node.get_elements_by_tag("table")):
            return False
        weight = self._class_weight(node)
        if weight < 0:
            return True
        if len(self._inner_text(node).split(",")) - 1 >= 10:   # _getCharCount(node, ",") < 10 이 아니면 남김
            return False
        p = len(node.get_elements_by_tag("p"))
        img = len(node.get_elements_by_tag("img"))
        li = len(node.get_elements_by_tag("li")) - 100
        inp = len(node.get_elements_by_tag("input"))
        heading_density = self._text_density(node, ["h1", "h2", "h3", "h4", "h5", "h6"])
        embed_count = 0
        for emb in node.get_elements_by_tag("object", "embed", "iframe"):
            if any(_VIDEO_RX.search(v or "") for v in emb.attrs.values()):
                return False
            embed_count += 1
        inner = self._inner_text(node)
        if self._AD_WORDS.search(inner) or self._LOADING_WORDS.search(inner):
            return True
        content_length = js_len(inner)
        link_density = self._link_density(node)
        text_density = self._text_density(node, ["SPAN", "LI", "TD"] + sorted(_DIV_TO_P, key=["BLOCKQUOTE", "DL", "DIV", "IMG", "OL", "P", "PRE", "TABLE", "UL"].index))
        is_fig = self._has_ancestor(node, "figure")
        errs = False
        if not is_fig and img > 1 and p / img < 0.5:
            errs = True
        if not is_list and li > p:
            errs = True
        if inp > math.floor(p / 3):
            errs = True
        if not is_list and not is_fig and heading_density < 0.9 and content_length < 25 and (img == 0 or img > 2) and link_density > 0:
            errs = True
        if not is_list and weight < 25 and link_density > 0.2:
            errs = True
        if weight >= 25 and link_density > 0.5:
            errs = True
        if (embed_count == 1 and content_length < 75) or embed_count > 1:
            errs = True
        if img == 0 and text_density == 0:
            errs = True
        if is_list and errs:
            for child in node.children:
                if len(child.children) > 1:
                    return errs
            if img == len(node.get_elements_by_tag("li")):
                return False
        return errs

    def _prep_article(self, a: Element) -> None:
        """_prepArticle — 쓸데없는 요소 제거, 조건부 정리, h1 → h2, 빈 p 제거, 칸 하나 표 풀기(_cleanStyles 는 마크다운에 영향 없어 생략)."""
        self._mark_data_tables(a)                            # 자료 표 표시
        self._fix_lazy_images(a)                             # 늦게 불러오는 그림
        self._clean_conditionally(a, "form")
        self._clean_conditionally(a, "fieldset")
        for t in ("object", "embed", "footer", "link", "aside"):
            self._clean(a, t)
        for top in list(a.children):                         # share 가 들어간 작은 요소
            end = self._next_node(top, True)
            nxt = self._next_node(top)
            while nxt is not None and nxt is not end:
                if _RX["share"].search(_cls(nxt) + " " + _id(nxt)) and js_len(nxt.text_content) < 500:
                    nxt = self._remove_and_next(nxt)
                else:
                    nxt = self._next_node(nxt)
        for t in ("iframe", "input", "textarea", "select", "button"):
            self._clean(a, t)
        for h in reversed(a.get_elements_by_tag("h1", "h2")):   # _cleanHeaders
            if h.parent is not None and self._class_weight(h) < 0:
                _remove(h)
        for t in ("table", "ul", "div"):                     # 마지막에 조건부 정리
            self._clean_conditionally(a, t)
        for h in a.get_elements_by_tag("h1"):                # h1 → h2
            self._set_tag(h, "h2")
        for p in reversed(a.get_elements_by_tag("p")):       # 빈 p
            if p.parent is None:
                continue
            if not p.get_elements_by_tag("img", "embed", "object", "iframe") and not self._inner_text(p, False):
                _remove(p)
        for br in a.get_elements_by_tag("br"):               # p 앞의 br
            n = self._skip_ws(_next_sibling(br))
            if n is not None and isinstance(n, Element) and n.tag_name == "P":
                _remove(br)
        for table in a.get_elements_by_tag("table"):         # 칸 하나짜리 표
            if table.parent is None:
                continue
            tbody = table.children[0] if self._has_single_tag(table, "TBODY") else table
            if self._has_single_tag(tbody, "TR"):
                row = tbody.children[0]
                if self._has_single_tag(row, "TD"):
                    cell = row.children[0]
                    cell = self._set_tag(cell, "p" if all(self._is_phrasing(c) for c in cell.child_nodes) else "div")
                    table.parent.replace_child(cell, table)

    def parse(self) -> Optional[dict]:
        """parse() — {'title', 'content'(본문 Element)} 또는 None."""
        self.title = self._article_title()                   # 메타는 스크립트 지우기 전에(JSON-LD 는 빠짐)
        self._prep_document()                                # 스크립트·스타일 제거 등
        article = self._grab()                               # 본문
        if article is None:
            return None
        _fix_relative_uris(article)                          # 주소 정리
        for e in article.iter():                             # _cleanClasses(page 만 남김)
            c = " ".join(x for x in re.split(JS_WS_CLASS + "+", _cls(e)) if x == "page")
            if c:
                e.attrs["class"] = c
            else:
                e.attrs.pop("class", None)
        return {"title": self.title, "content": article}


_SPECIAL = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21, "file": None}   # WHATWG 특수 스킴(기본 포트)


def _whatwg_href(uri: str) -> str:
    """new URL(uri, 'about:blank').href 근사 — 절대 주소만 정리(상대 주소는 about:blank 기준이라 실패 → 원래 글).
    특수 스킴: 스킴·호스트 소문자, 기본 포트 제거, 빈 경로는 '/', 공백 등 퍼센트 인코딩."""
    m = re.match(r"^[" + JS_WS_CHARS + r"\x00-\x20]*([A-Za-z][A-Za-z0-9+.\-]*):", uri)
    if not m:                                                # 스킴 없음 = 상대 → 실패
        raise ValueError("Invalid URL")
    u = uri.strip("\x00 \t\n\r\x0b\x0c").replace("\t", "").replace("\n", "").replace("\r", "")   # 앞뒤 C0·공백, 탭·줄바꿈 제거
    scheme = m.group(1).lower()
    if scheme not in _SPECIAL:                               # 특수 스킴 아님(mailto 등) — 스킴만 소문자
        return scheme + u[len(m.group(1)):]
    parts = urlsplit(u.replace("\\", "/"))                   # 특수 스킴은 \ 도 / 로
    host = parts.hostname or ""
    if scheme != "file" and not host:                        # 호스트 없음 → 실패
        raise ValueError("Invalid URL")
    try:
        host = host.encode("idna").decode("ascii") if any(ord(c) > 127 for c in host) else host.lower()
    except Exception:
        raise ValueError("Invalid URL")
    netloc = host
    if parts.username is not None:                           # 사용자 정보
        netloc = parts.username + (":" + parts.password if parts.password else "") + "@" + netloc
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Invalid URL")
    if port is not None and port != _SPECIAL[scheme]:        # 기본 포트는 뺌
        netloc += f":{port}"
    path = parts.path or "/"
    path = quote(path, safe="/%:@!$&'()*+,;=-._~[]|^")      # 경로 인코딩
    query = quote(parts.query, safe="/%:@!$&'()*+,;=-._~?[]\\^`{|}") if parts.query else ""
    frag = quote(parts.fragment, safe="/%:@!$&'()*+,;=-._~?#[]\\^{|}") if parts.fragment else ""
    out = urlunsplit((scheme, netloc, path, "", ""))
    if "?" in u:
        out += "?" + query
    if "#" in u:
        out += "#" + frag
    return out


def _fix_relative_uris(article: Element) -> None:
    """_fixRelativeUris — baseURI=documentURI='about:blank'(cn은 JSDOM 에 url 을 안 줌): #링크·상대 주소는 그대로, 절대 주소만 정리. javascript: 링크는 글로."""
    def to_abs(uri: str) -> str:
        if uri[:1] == "#":                                   # 해시 링크
            return uri
        try:
            return _whatwg_href(uri)
        except Exception:
            return uri
    for link in article.get_elements_by_tag("a"):
        href = link.get_attribute("href")
        if href:
            if href.startswith("javascript:"):               # 스크립트 링크 → 글/ span
                if len(link.child_nodes) == 1 and isinstance(link.child_nodes[0], Text):
                    if link.parent is not None:
                        link.parent.replace_child(Text(link.text_content), link)
                else:
                    span = Element("span")
                    for c in list(link.child_nodes):
                        span.append_child(c)
                    if link.parent is not None:
                        link.parent.replace_child(span, link)
            else:
                link.attrs["href"] = to_abs(href)
    for media in article.get_elements_by_tag("img", "picture", "figure", "video", "audio", "source"):
        for attr in ("src", "poster"):
            v = media.get_attribute(attr)
            if v:
                media.attrs[attr] = to_abs(v)
        srcset = media.get_attribute("srcset")
        if srcset:
            media.attrs["srcset"] = re.sub(r"(\S+)(\s+[\d.]+[xw])?(\s*(?:,|$))",
                                           lambda m: to_abs(m.group(1)) + (m.group(2) or "") + m.group(3), srcset)


# ═════════════════════════ 3. node-html-markdown 1.3.0 이식 ═════════════════════════
REMOVE_NODE = object()                                       # PostProcessResult.RemoveNode

_BLOCK = ["ADDRESS", "ARTICLE", "ASIDE", "AUDIO", "BLOCKQUOTE", "BODY", "CANVAS", "CENTER", "DD", "DIR", "DIV", "DL",
          "DT", "FIELDSET", "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "FRAMESET", "H1", "H2", "H3", "H4", "H5", "H6",
          "HEADER", "HGROUP", "HR", "HTML", "ISINDEX", "LI", "MAIN", "MENU", "NAV", "NOFRAMES", "NOSCRIPT", "OL",
          "OUTPUT", "P", "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT", "TH", "THEAD", "TR", "UL"]   # defaultBlockElements
_IGNORE = ["AREA", "BASE", "COL", "COMMAND", "EMBED", "HEAD", "INPUT", "KEYGEN", "LINK", "META", "PARAM", "SCRIPT",
           "SOURCE", "STYLE", "TRACK", "WBR"]                # defaultIgnoreElements
_CONTENTLESS = ["BR", "HR", "IMG"]                           # contentlessElements


def _is_ws_only(s: str) -> bool:
    """isWhiteSpaceOnly — /\\S/ 가 없음."""
    return all(c in JS_WS_CHARS for c in s)


def _trim_newlines(s: str) -> str:
    """trimNewLines — 앞뒤 \\n 만 제거."""
    return s.strip("\n")


def _split_special(s: str) -> list[tuple[str, str]]:
    """splitSpecial — (줄 글, 그 줄의 줄바꿈 글자) 목록."""
    lines = []
    n = len(s)
    i = 0
    start = 0
    while i < n:
        ch = s[i]
        nl = ""
        if ch == "\r":
            nl = "\r\n" if i + 1 < n and s[i + 1] == "\n" else "\r"
        elif ch == "\n":
            nl = "\n"
        end = i if nl else (i + 1 if i == n - 1 else None)
        if end is None:
            i += 1
            continue
        lines.append((s[start:end], nl))
        start = end + len(nl)
        if len(nl) > 1:
            i += 1
        i += 1
    return lines


def _tag_surround(content: str, sur: str) -> str:
    """tagSurround — 각 줄의 앞뒤 공백을 밖으로 빼고 구분자로 감쌈."""
    if sur in content:                                       # 안에 같은 구분자가 있으면 지움
        content = re.sub(r"([^\\])" + re.escape(sur), r"\1", content)
    res = ""
    for text, nl in _split_special(content):
        start = next((i for i, c in enumerate(text) if c not in JS_WS_CHARS), None)   # 첫 비공백
        if start is None:                                    # 전부 공백 → 감싸지 않음
            res += text + nl
            continue
        end = next(i for i in range(len(text) - 1, start - 1, -1) if text[i] not in JS_WS_CHARS)   # 끝 비공백(start 이상)
        lead = text[start - 1] if start > 0 else ""
        trail = text[end + 1] if end < len(text) - 1 else ""
        res += lead + sur + text[start:end + 1] + sur + trail + nl
    return res


def _trailing_ws_info(s: str) -> tuple[int, int]:
    """getTrailingWhitespaceInfo — 끝 10글자 안의 (공백 수, 줄바꿈 수)."""
    ws = nl = 0
    for i in range(len(s) - 1, max(len(s) - 10, 0) - 1, -1):
        c = s[i]
        if c not in JS_WS_CHARS:
            break
        ws += 1
        if c in "\r\n":
            nl += 1
    return ws, nl


def _trim_text(text: str) -> str:
    """node-html-parser trimText — 양끝 공백을 없애되 줄바꿈 아닌 공백이 있었으면 공백 하나 남김."""
    start = next((i for i, c in enumerate(text) if c not in JS_WS_CHARS), None)
    end = next((i for i in range(len(text) - 1, -1, -1) if text[i] not in JS_WS_CHARS), None)
    if start is None:
        start = 0
    if end is None or (start is not None and end < start):
        end = len(text) - 1
    lead = start > 0 and text[start - 1] in JS_WS_CHARS and text[start - 1] not in "\r\n"
    trail = end < len(text) - 1 and text[end + 1] in JS_WS_CHARS and text[end + 1] not in "\r\n"
    return (" " if lead else "") + text[start:end + 1] + (" " if trail else "")


_GLOBAL_ESC = re.compile(r"[\\`*_~\[\]]")                    # globalEscape
_LINE_START_ESC = re.compile(_BOL + r"(" + JS_WS_CLASS + r"*?)((?:\+" + JS_WS_CLASS + r")|(?:[=>-])|(?:#{1,6}" + JS_WS_CLASS + r"))|(?:([0-9]+)(\." + JS_WS_CLASS + r"))")   # lineStartEscape
_DOCTYPE = re.compile(_BOL + r"<!DOCTYPE[^" + _LT + r"]*>", re.I)   # textReplace(/^<!DOCTYPE.*>/gmi)


def _line_start_repl(m: re.Match) -> str:
    """'$1$3\\\\$2$4' — 안 맞은 그룹은 빈 글."""
    return (m.group(1) or "") + (m.group(3) or "") + "\\" + (m.group(2) or "") + (m.group(4) or "")


def _process_text(text: str, md: dict) -> str:
    """processText — 공백 접기 + 이스케이프."""
    res = text
    if not md.get("preserveWhitespace"):
        res = re.sub(JS_WS_CLASS + "+", " ", res)
    if md.get("noEscape"):
        return res
    res = _GLOBAL_ESC.sub(lambda m: "\\" + m.group(0), res)
    res = _LINE_START_ESC.sub(_line_start_repl, res)
    res = _DOCTYPE.sub("", res)
    return res


class _Fn:
    """함수형 번역기 + base(합쳐질 기본 설정)."""

    def __init__(self, fn: Callable[[dict], dict], base: Optional[dict] = None):
        self.fn = fn
        self.base = base


class _Collection(dict):
    """TranslatorCollection — 대문자 태그 → 설정(dict) 또는 _Fn."""

    def set(self, keys: str, cfg, preserve_base: bool = False) -> None:
        for el in keys.split(","):
            el = el.upper()
            res = cfg
            if preserve_base:
                base = self.get(el)
                if isinstance(base, dict):                   # 기본 설정(객체)이 있으면
                    res = _Fn(cfg.fn, base) if isinstance(cfg, _Fn) else {**base, **cfg}   # 함수는 base 를 달고, 객체는 합침
            self[el] = res


def _li_fn(ctx: dict) -> dict:
    """'li' 번역기."""
    indent_level = int(ctx.get("indentLevel") or 0)
    lk = ctx.get("listKind")
    num = ctx.get("listItemNumber")
    prefix = "   " * indent_level + (f"{num}. " if lk == "OL" and num is not None else f"{ctx['options']['bulletMarker']} ")

    def post(c: dict):
        content = c["content"]
        if _is_ws_only(content):
            return REMOVE_NODE
        t = js_trim(content)
        t = re.sub(r"([^\r\n])(?:\r?\n)+", lambda m: m.group(1) + "  \n" + "   " * indent_level, t)
        t = re.sub(r"(" + _NS + r"+?)" + _WS_NO_NL + r"+" + _EOL, lambda m: m.group(1) + "  ", t)
        return t
    return {"prefix": prefix, "surroundingNewlines": 1, "postprocess": post}


def _code_fn(ctx: dict) -> dict:
    """'code' 번역기 — pre 안의 유일한 자식이면 코드 블록, 아니면 인라인."""
    node, parent = ctx["node"], ctx["parent"]
    is_block = parent is not None and isinstance(parent, Element) and parent.tag_name in ("PRE", "WRAPPED-PRE") and len(parent.child_nodes) < 2
    if not is_block:
        def post(c: dict):
            content = c["content"]
            runs = sorted(re.findall(r"`+", content), key=lambda r: -len(r))   # 긴 것부터(안정 정렬)
            delim = "`" + (runs[0] if runs else "")
            pad = " " if len(delim) > 1 else ""
            return delim + pad + content + pad + delim
        return {"spaceIfRepeatingChar": True, "noEscape": True, "postprocess": post}
    m = re.search(r"language-(" + _NS + r"+)", node.get_attribute("class") or "")
    lang = m.group(1) if m else ""
    return {"noEscape": True, "prefix": "```" + lang + "\n", "postfix": "\n```", "childTranslators": ctx["visitor"].code_block_translators}


def _table_fn(ctx: dict) -> dict:
    """'table' 번역기 — 칸 너비를 맞춘 마크다운 표."""
    def post(c: dict):
        raw_rows = [re.sub(r"^(?:\|" + JS_WS_CLASS + r"+)?([^" + _LT + r"]+)" + JS_WS_CLASS + r"*\|" + JS_WS_CLASS + r"*\Z", r"\1", t, count=1)
                    for t, _ in _split_special(c["content"])]
        rows = []
        widths: list[int] = []
        for row in raw_rows:
            if not row:
                continue
            cols = []
            for i, col in enumerate(row.split(" |")):
                col = js_trim(col)
                if len(widths) < i + 1:
                    widths.append(js_len(col))
                elif widths[i] < js_len(col):
                    widths[i] = js_len(col)
                cols.append(col)
            rows.append(cols)
        if len(rows) < 1:
            return REMOVE_NODE
        res = ""
        caption = c["nodeMetadata"].get(id(c["node"]), {}).get("tableMeta", {}).get("caption")
        if caption:
            res += caption + "\n"
        for rn, cols in enumerate(rows):
            res += "| "
            for i in range(len(widths)):
                cc = cols[i] if i < len(cols) else ""
                cc += " " * max(0, widths[i] - js_len(cc))
                res += cc + " |" + (" " if i < len(widths) - 1 else "")
            res += "\n"
            if rn == 0:
                res += "|" + "".join(" " + "-" * w + " |" for w in widths) + "\n"
        return res
    return {"surroundingNewlines": 2, "childTranslators": ctx["visitor"].table_translators, "postprocess": post}


def _a_fn(ctx: dict) -> dict:
    """'a' 번역기."""
    node = ctx["node"]
    href = node.get_attribute("href")
    if not href:
        return {}
    enc = "".join({"(": "%28", ")": "%29", "_": "%5F", "*": "%2A"}.get(ch, ch) for ch in href)
    title = node.get_attribute("title")
    if node.text_content == href and ctx["options"]["useInlineLinks"]:
        return {"content": f"<{enc}>"}
    return {
        "postprocess": lambda c: re.sub(r"(?:\r?\n)+", " ", c["content"]),
        "childTranslators": ctx["visitor"].a_tag_translators,
        "prefix": "[",
        "postfix": "](" + enc + (f' "{title}"' if title else "") + ")",
    }


def _img_fn(ctx: dict) -> dict:
    """'img' 번역기 — data: 이미지는 무시."""
    node = ctx["node"]
    src = node.get_attribute("src") or ""
    if not src or re.match(r"^data:", src, re.I):
        return {"ignore": True}
    alt = node.get_attribute("alt") or ""
    title = node.get_attribute("title") or ""
    title_part = (' "' + title + '"') if title else ""      # ${title && ` "${title}"`}
    return {"content": "![" + alt + "](" + src + title_part + ")", "recurse": False}


def _surround_post(delim_key: str):
    """strong/del/em 공통 postprocess."""
    def post(c: dict):
        if _is_ws_only(c["content"]):
            return REMOVE_NODE
        return _tag_surround(c["content"], c["options"][delim_key])
    return post


_DEFAULT_TRANSLATORS = [                                     # defaultTranslators(순서 = 원문)
    ("pre", {"noEscape": True, "preserveWhitespace": True}),
    ("br", {"content": "  \n", "recurse": False}),
    ("hr", {"content": "---", "recurse": False}),
    ("h1,h2,h3,h4,h5,h6", _Fn(lambda ctx: {"prefix": "#" * int(ctx["node"].tag_name[1]) + " "})),
    ("strong,b", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("strongDelimiter")}),
    ("del,s,strike", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("strikeDelimiter")}),
    ("em,i", {"spaceIfRepeatingChar": True, "postprocess": _surround_post("emDelimiter")}),
    ("ol,ul", _Fn(lambda ctx: {"surroundingNewlines": 1 if ctx.get("listKind") else 2})),
    ("li", _Fn(_li_fn)),
    ("blockquote", {"postprocess": lambda c: re.sub(_BOL + r"(>*)" + _WS_NO_NL + r"?", lambda m: ">" + m.group(1) + " ", _trim_newlines(c["content"]))}),
    ("code", _Fn(_code_fn)),
    ("table", _Fn(_table_fn)),
    ("a", _Fn(_a_fn)),
    ("img", _Fn(_img_fn)),
]


def _caption_post(c: dict):
    """table caption — 표 메타에 저장하고 자신은 지움."""
    cap = js_trim(re.sub(r"(?:\r?\n)+", " ", c["content"]))
    if cap:
        c["nodeMetadata"][id(c["node"])]["tableMeta"]["caption"] = "__" + cap + "__"
    return REMOVE_NODE


def _cell_post(c: dict):
    """th/td — 줄바꿈을 공백으로, 첫 | 만 이스케이프(원문 string.replace)."""
    return js_trim(re.sub(r"(?:\r?\n)+", " ", _trim_newlines(c["content"]).replace("|", "\\|", 1)))


_DEFAULT_OPTIONS = {"codeFence": "```", "bulletMarker": "*", "indent": "  ", "codeBlockStyle": "fenced", "emDelimiter": "_",
                    "strongDelimiter": "**", "strikeDelimiter": "~~", "maxConsecutiveNewlines": 3, "useInlineLinks": True}


class NodeHtmlMarkdown:
    """NodeHtmlMarkdown(기본 옵션) — translate(Element) → 마크다운."""

    def __init__(self) -> None:
        self.options = dict(_DEFAULT_OPTIONS)
        self.translators = _Collection()
        self.code_block_translators = _Collection()
        self.a_tag_translators = _Collection()
        self.table_translators = _Collection()
        self.table_row_translators = _Collection()
        self.table_cell_translators = _Collection()
        for el in _IGNORE:                                   # 기본: 무시 요소
            self.translators.set(el, {"ignore": True, "recurse": False})
            self.code_block_translators.set(el, {"ignore": True, "recurse": False})
        for el in _BLOCK:                                    # 기본: 블록 요소
            self.translators.set(el, {"surroundingNewlines": 2})
            self.code_block_translators.set(el, {"surroundingNewlines": 2})
        dt = dict(_DEFAULT_TRANSLATORS)
        for k, v in _DEFAULT_TRANSLATORS:
            self.translators.set(k, v, True)
        for k, v in [("br", {"content": "\n", "recurse": False}), ("hr", {"content": "---", "recurse": False}),
                     ("h1,h2,h3,h4,h5,h6", {"prefix": "[", "postfix": "]"}), ("ol,ul", dt["ol,ul"]), ("li", dt["li"]),
                     ("tr", {"surroundingNewlines": True}), ("img", {"recurse": False})]:   # defaultCodeBlockTranslators
            self.code_block_translators.set(k, v, True)
        for k, v in [("br", {"content": "\n", "recurse": False}), ("hr", {"content": "\n", "recurse": False}),
                     ("pre", dt["pre"]), ("strong,b", dt["strong,b"]), ("del,s,strike", dt["del,s,strike"]),
                     ("em,i", dt["em,i"]), ("img", dt["img"])]:   # aTagTranslatorConfig
            self.a_tag_translators.set(k, v, True)
        cell = {"surroundingNewlines": False, "childTranslators": None, "prefix": " ", "postfix": " |", "postprocess": _cell_post}
        table_cfg = [
            ("caption", _Fn(lambda ctx: {"surroundingNewlines": False, "childTranslators": ctx["visitor"].table_cell_translators, "postprocess": _caption_post})),
            ("tr", _Fn(lambda ctx: {"surroundingNewlines": False, "childTranslators": ctx["visitor"].table_row_translators, "postfix": "\n", "prefix": "| ",
                                    "postprocess": lambda c: c["content"] if re.search(r" \|" + JS_WS_CLASS + r"*\Z", c["content"]) else REMOVE_NODE})),
            ("th,td", _Fn(lambda ctx: {**cell, "childTranslators": ctx["visitor"].table_cell_translators})),
        ]
        for k, v in table_cfg:
            self.table_translators.set(k, v, True)
        self.table_row_translators.set("th,td", table_cfg[2][1], True)
        for k in ("a", "strong,b", "del,s,strike", "em,i", "img"):   # tableCellTranslatorConfig
            self.table_cell_translators.set(k, dt[k], True)

    def translate(self, root: Element) -> str:
        """getMarkdownForHtmlNodes."""
        v = _Visitor(self, root)
        result = v.text
        mx = self.options["maxConsecutiveNewlines"]          # 줄바꿈 4개 이상 → 3개
        ws = JS_WS_CLASS
        result = re.sub(r"(?:\r?\n" + ws + r"*)+((?:\r?\n" + ws + r"*){" + str(mx) + r"})", r"\1", result)
        return _trim_newlines(result)


class _Visitor:
    """Visitor — 나무를 돌며 글을 쌓는다."""

    def __init__(self, inst: NodeHtmlMarkdown, root: Element):
        self.inst = inst
        self.options = inst.options
        self.code_block_translators = inst.code_block_translators
        self.a_tag_translators = inst.a_tag_translators
        self.table_translators = inst.table_translators
        self.table_row_translators = inst.table_row_translators
        self.table_cell_translators = inst.table_cell_translators
        self.node_metadata: dict[int, dict] = {}             # id(node) → metadata
        self.text = ""                                       # 결과
        self.trail = (0, 0)                                  # (공백, 줄바꿈)
        self.preserve: dict[int, bool] = {}                  # optimizeTree 결과
        self._optimize(root)
        self.visit(root, False, None)

    def _optimize(self, root) -> bool:
        """optimizeTree — 쓸 내용이 있는 마디 표시."""
        def visit(node) -> bool:
            res = False
            if isinstance(node, Text) or (isinstance(node, Element) and node.tag_name in _CONTENTLESS):
                res = True
            else:
                if not node.child_nodes:
                    t = self.inst.translators.get(node.tag_name) if node.tag else None   # 이름 없는 뿌리는 번역기 없음
                    if (isinstance(t, dict) and t.get("preserveIfEmpty")) or isinstance(t, _Fn):
                        res = True
                else:
                    for ch in node.child_nodes:
                        r = visit(ch)
                        res = res or r
            self.preserve[id(node)] = res
            return res
        return visit(root)

    def append(self, s: str, start_pos: Optional[int] = None, space_if_repeat: bool = False) -> None:
        """appendResult."""
        if not s and start_pos is None:
            return
        if start_pos is not None:
            self.text = self.text[:start_pos]
        self.text += (" " if space_if_repeat and self.text[-1:] == s[:1] and s else "") + s
        self.trail = _trailing_ws_info(self.text)

    def append_newlines(self, count: int) -> None:
        """appendNewlines."""
        self.append("\n" * max(0, int(count) - self.trail[1]))

    def visit(self, node, text_only: bool, md: Optional[dict]) -> None:
        """visitNode."""
        if not self.preserve.get(id(node)):
            return
        if isinstance(node, Text):                           # 글 마디
            mdx = md or {}
            if _is_ws_only(node.data) and not mdx.get("preserveWhitespace"):
                if not self.text or self.trail[0] > 0:
                    return
                self.append(" ")
                return
            self.append(_process_text(node.data if mdx.get("preserveWhitespace") else _trim_text(node.data), mdx))
            return
        if text_only or not isinstance(node, Element):
            return
        tag = node.tag_name if node.tag else None            # 뿌리(이름 없음)
        coll = md["translators"] if md and md.get("translators") else self.inst.translators
        tcf = coll.get(tag) if tag else None
        if tag in ("UL", "OL"):                              # 목록 정보
            prev = (md or {}).get("indentLevel")            # metadata?.indentLevel ?? -1
            md = {**(md or {}), "listItemNumber": 0, "listKind": tag, "indentLevel": (-1 if prev is None else prev) + 1}
        elif tag == "LI":
            if md and md.get("listKind") == "OL":
                md["listItemNumber"] = (md.get("listItemNumber") or 0) + 1
        elif tag == "PRE":
            md = {**(md or {}), "preserveWhitespace": True}
        elif tag == "TABLE":
            md = {**(md or {}), "tableMeta": {"node": node}}
        if md:
            self.node_metadata[id(node)] = md
        if not tcf:                                          # 번역기 없음 → 자식만
            for ch in list(node.child_nodes):
                self.visit(ch, text_only, md)
            return
        ctx = None
        if isinstance(tcf, _Fn):                             # 함수형
            ctx = self._ctx(node, md, tcf.base)
            cfg = {**(tcf.base or {}), **tcf.fn(ctx)}
        else:
            cfg = tcf
        if cfg.get("ignore"):
            return
        if cfg.get("noEscape") and not (md or {}).get("noEscape"):
            md = {**(md or {}), "noEscape": cfg["noEscape"]}
            self.node_metadata[id(node)] = md
        if cfg.get("childTranslators") is not None and cfg.get("childTranslators") is not (md or {}).get("translators"):
            md = {**(md or {}), "translators": cfg["childTranslators"]}
            self.node_metadata[id(node)] = md
        start_outer = len(self.text)
        if cfg.get("surroundingNewlines"):
            self.append_newlines(int(cfg["surroundingNewlines"]))
        if cfg.get("prefix"):
            self.append(cfg["prefix"])
        if isinstance(cfg.get("content"), str):
            self.append(cfg["content"], None, bool(cfg.get("spaceIfRepeatingChar")))
        else:
            start = len(self.text)
            for ch in list(node.child_nodes):
                self.visit(ch, cfg.get("recurse") is False, md)
            if cfg.get("postprocess"):
                c = {**(ctx or self._ctx(node, md, None)), "content": self.text[start:]}
                res = cfg["postprocess"](c)
                if res is REMOVE_NODE:
                    if tag == "LI" and md and md.get("listItemNumber"):
                        md["listItemNumber"] -= 1
                    self.append("", start_outer)
                    return
                if isinstance(res, str):
                    self.append(res, start, bool(cfg.get("spaceIfRepeatingChar")))
        if cfg.get("postfix"):
            self.append(cfg["postfix"])
        if cfg.get("surroundingNewlines"):
            self.append_newlines(int(cfg["surroundingNewlines"]))

    def _ctx(self, node, md, base) -> dict:
        """createTranslatorContext."""
        return {"node": node, "options": self.options, "parent": node.parent, "nodeMetadata": self.node_metadata,
                "visitor": self, "base": base, **(md or {})}


def html_to_markdown(root: Element) -> str:
    """NodeHtmlMarkdown.translate(article.content) — content 는 articleContent.innerHTML 이므로 root 의 '자식들'을 변환."""
    wrapper = Element("")                                    # node-html-parser 의 이름 없는 뿌리
    for c in list(root.child_nodes):                         # innerHTML = 자식들
        wrapper.append_child(c)
    return NodeHtmlMarkdown().translate(wrapper)


def html_string_to_markdown(html_text: str) -> str:
    """HTML 조각 글 → 마크다운(시험용 — NHM 단독 비교)."""
    b = _TreeBuilder()
    b._enter_body()                                          # 조각은 바로 본문으로
    b.feed(html_text)
    b.close()
    wrapper = Element("")
    wrapper.child_nodes = list(b.body.child_nodes)
    for c in wrapper.child_nodes:
        c.parent = wrapper
    return NodeHtmlMarkdown().translate(wrapper)


# ═════════════════════════ 4. 가져오기 + 도구 ═════════════════════════
FETCH_IMPL: Optional[Callable[[str], Any]] = None            # 시험용: async (url) → (status, statusText, 본문 bytes)


async def _http_get(url: str) -> tuple[int, str, bytes]:
    """fetch(url) — undici 기본처럼 리디렉션 따라가기. 연결 실패는 'fetch failed'."""
    if FETCH_IMPL is not None:                               # 시험에서 바꿔 끼움
        return await FETCH_IMPL(url)
    import httpx                                             # 의존성(이미 있음)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=None,
                                     headers={"user-agent": "node", "accept": "*/*", "accept-language": "*", "sec-fetch-mode": "cors"}) as c:
            r = await c.get(url)
            return r.status_code, r.reason_phrase, r.content
    except Exception:                                        # 네트워크 오류 등
        raise Exception("fetch failed")


def _check_url(query: str) -> str:
    """new URL(query) — 잘못되면 'Invalid URL'."""
    m = re.match(r"^[\x00-\x20]*([A-Za-z][A-Za-z0-9+.\-]*):", query)
    if not m:
        raise Exception("Invalid URL")
    scheme = m.group(1).lower()
    if scheme in _SPECIAL and scheme != "file":
        return _whatwg_href(query)                           # 호스트 없으면 Invalid URL
    return query


async def get_url_context_items(query: str) -> list[dict]:
    """getUrlContextItems — [{name, description, content(마크다운), uri}]. 파비콘 요청은 결과 글에 영향이 없어 생략."""
    url = _check_url(query)                                  # new URL
    status, status_text, body = await _http_get(url)         # 가져오기
    if not (200 <= status <= 299):                           # !resp.ok
        raise Exception(f"HTTP {status} {status_text}")
    text = body.decode("utf-8", errors="replace")            # resp.text() — 늘 UTF-8
    if text.startswith("\ufeff"):                            # TextDecoder 는 BOM 제거
        text = text[1:]
    doc = parse_html(text)                                   # new JSDOM(html)
    article = _Readability(doc).parse()                      # Readability
    markdown = html_to_markdown(article["content"]) if article else ""   # 마크다운(본문 없으면 "")
    title = (article or {}).get("title") or urlsplit(url).path or "/"   # article?.title || url.pathname
    return [{"description": url, "content": markdown, "name": title, "uri": {"type": "url", "value": url}}]


async def fetch_url_content_impl(args: dict) -> list[dict]:
    """core fetchUrlContentImpl — 항목마다 2만 자 넘으면 자르고 경고 항목 추가."""
    if not args or "url" not in args:                        # getStringArg
        raise Exception("`url` argument is required and must not be empty or whitespace-only. (type string)")
    url = args["url"]
    if isinstance(url, (dict, list)):                        # 객체로 파싱된 경우 → JSON 글
        import json
        url = json.dumps(url, ensure_ascii=False, separators=(",", ":"))
    elif not isinstance(url, str):
        raise Exception("`url` argument is required and must not be empty or whitespace-only. (type string)")
    elif not js_trim(url):
        raise Exception("Argument url must not be empty or whitespace-only")
    items = await get_url_context_items(url)
    truncated: list[str] = []
    out = []
    for it in items:
        if js_len(it["content"]) > DEFAULT_FETCH_URL_CHAR_LIMIT:   # 항목 한도
            truncated.append(url)
            it = {**it, "content": _js_substring(it["content"], DEFAULT_FETCH_URL_CHAR_LIMIT)}
        out.append(it)
    if truncated:
        out.append({"name": "Truncation warning", "description": "",
                    "content": f"The content from {', '.join(truncated)} was truncated because it exceeded the {DEFAULT_FETCH_URL_CHAR_LIMIT} character limit. If you need more content, consider fetching specific sections or using a more targeted approach."})
    return out


def _js_substring(s: str, end: int) -> str:
    """substring(0, end) — UTF-16 위치 기준."""
    if js_len(s) == len(s):
        return s[:end]
    u = s.encode("utf-16-le", errors="surrogatepass")
    return u[: end * 2].decode("utf-16-le", errors="surrogatepass")


def _get_fetch_max_chars() -> int:
    """CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH(기본 20000)."""
    return parse_env_number(os.environ.get("CONTINUE_CLI_FETCH_MAX_OUTPUT_LENGTH"), DEFAULT_FETCH_MAX_CHARS)


CONTEXT_ITEMS_IMPL: Optional[Callable[[dict], Any]] = None   # 시험용: fetchUrlContentImpl 바꿔 끼우기(cn 시험의 vi.mock 대응)


async def _preprocess(args: dict) -> PreprocessResult:
    """preprocess — 'Will fetch: <url>'."""
    url = args.get("url")
    return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Will fetch: {'undefined' if url is None and 'url' not in args else url}")])


async def _run(args: dict, context=None) -> str:
    """run — 경고 항목을 뺀 내용을 빈 줄로 잇고 2만 자로 자름. 실패는 'Error: …'."""
    url = args.get("url")
    try:
        impl = CONTEXT_ITEMS_IMPL or fetch_url_content_impl  # 구현
        items = await impl({"url": url})
        if len(items) == 0:
            raise ContinueError("Unspecified", f"Could not fetch content from {url}")
        combined = "\n\n".join(it["content"] for it in items if it.get("name") != "Truncation warning")
        r = truncate_output_from_end(combined, _get_fetch_max_chars(), "fetched content")
        return r.output
    except ContinueError:
        raise
    except Exception as e:
        raise Exception(f"Error: {e}")


FETCH_TOOL = Tool(
    name="Fetch",
    display_name="Fetch",
    description="Fetches content from a URL, converts to markdown, and handles long content with truncation",
    parameters={
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
        },
    },
    run=_run,
    preprocess=_preprocess,
    readonly=True,
    is_built_in=True,
)
