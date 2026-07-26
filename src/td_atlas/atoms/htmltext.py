"""Turn TouchDesigner's offline MediaWiki mirror into agent-readable text.

The mirror is rendered HTML rather than wikitext, so the article body has to be
lifted out of the site chrome. Two things are stripped beyond the obvious
navigation: the "[edit]" section links MediaWiki injects into every heading, and
the glossary tooltips TouchDesigner attaches to terms like "TOP" or "Operator",
which otherwise append a page of unrelated definitions to every article.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Content lives between these markers in every mirrored page.
_BODY_START = re.compile(r'<div[^>]*id="mw-content-text"', re.I)
_BODY_END = re.compile(r'<div[^>]*class="[^"]*printfooter', re.I)

_DROP_TAGS = {"script", "style", "noscript", "sup"}

# Classes and ids whose subtree carries no article content. Matched as whole
# tokens: substring matching would drop any element whose class merely happens
# to contain a short entry like "toc".
_DROP_TOKENS = frozenset(
    {
        "mw-editsection",
        "mw-jump-link",
        "mw-indicators",
        "mw-references",
        "mw-navigation",
        "toc",
        "navbox",
        # The family footer ("Add • Analyze • ... • ZED Select") that MediaWiki
        # appends to every operator page.
        "catlist",
        "navigation-not-searchable",
        "catlinks",
        "printfooter",
        "versionselector",
        # Glossary tooltips, which would otherwise append a page of unrelated
        # definitions to every article.
        "mw-lingo-tooltip",
        "lingo-tooltip",
    }
)

_BLOCK_TAGS = {
    "p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "blockquote", "dd", "dt", "section", "table", "ul", "ol",
}

_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####",
             "h6": "######"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_depth = 0
        self._drop_tag: str | None = None
        self._pending_heading: str | None = None
        self._in_pre = 0
        self._cell = False

    # -- helpers -----------------------------------------------------------

    def _should_drop(self, attrs: list[tuple[str, str | None]]) -> bool:
        for key, value in attrs:
            if key not in ("class", "id") or not value:
                continue
            if any(token in _DROP_TOKENS for token in value.lower().split()):
                return True
        return False

    def _emit(self, text: str) -> None:
        if text:
            self._parts.append(text)

    # -- parser hooks ------------------------------------------------------

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._drop_depth:
            if tag == self._drop_tag:
                self._drop_depth += 1
            return
        if tag in _DROP_TAGS or self._should_drop(attrs):
            self._drop_tag = tag
            self._drop_depth = 1
            return

        if tag == "pre":
            self._in_pre += 1
            self._emit("\n\n```\n")
        elif tag in _HEADINGS:
            self._pending_heading = _HEADINGS[tag]
            self._emit("\n\n")
        elif tag == "li":
            self._emit("\n- ")
        elif tag in ("td", "th"):
            self._emit(" | " if self._cell else "\n| ")
            self._cell = True
        elif tag == "tr":
            self._cell = False
            self._emit("\n")
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._drop_depth:
            if tag == self._drop_tag:
                self._drop_depth -= 1
                if self._drop_depth == 0:
                    self._drop_tag = None
            return
        if tag == "pre":
            self._in_pre = max(0, self._in_pre - 1)
            self._emit("\n```\n")
        elif tag in _HEADINGS:
            self._pending_heading = None
            self._emit("\n")
        elif tag == "tr":
            self._cell = False
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        if self._in_pre:
            self._emit(data)
            return
        text = data.replace(" ", " ")
        if not text.strip():
            # Preserve a single separating space between inline elements.
            if self._parts and not self._parts[-1].endswith((" ", "\n")):
                self._emit(" ")
            return
        if self._pending_heading:
            self._emit(f"{self._pending_heading} ")
            self._pending_heading = None
        self._emit(re.sub(r"\s+", " ", text))

    def text(self) -> str:
        return "".join(self._parts)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Table rows collapse to a single blank pipe when a row held only markup.
    text = re.sub(r"^\|\s*$", "", text, flags=re.M)
    return text.strip()


def html_to_text(html: str) -> str:
    """Extract the article body of a mirrored wiki page as plain text."""
    start = _BODY_START.search(html)
    body = html[start.start():] if start else html
    end = _BODY_END.search(body)
    if end:
        body = body[: end.start()]

    parser = _Extractor()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        # A malformed page should degrade to whatever was parsed, not abort
        # a 2000-page build.
        pass
    return _tidy(parser.text())


_TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)


def page_title(html: str) -> str | None:
    """The page title, minus the site suffix MediaWiki appends."""
    match = _TITLE.search(html)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return re.sub(r"\s*-\s*Derivative\s*$", "", title, flags=re.I)
