"""Wikipedia reader: Parsoid HTML (core REST API, ADR-013) -> IR.

All Wikipedia-specific behaviour is expressed as `Handler`s on top of `html_common`.
"""

import hashlib
import json
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from tomd.ir import (
    Block,
    Cell,
    CodeBlock,
    Document,
    DocumentMeta,
    Emph,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Inline,
    InlineMath,
    Link,
    MathBlock,
    Paragraph,
    Row,
    Table,
)
from tomd.ir import diagnostics as codes
from tomd.ir.walk import unwrap_emphasis
from tomd.readers.html_common import (
    Element,
    Handler,
    HtmlMapper,
    blocks_to_inlines,
    classes,
    drop_handler,
    hoist_footnote_defs,
    parse_html,
    promote_lone_math,
    strip_displaystyle,
    tag_of,
    tex_of_math,
    text_content,
)
from tomd.readers.wikipedia.client import ArticleRef, WikimediaClient, parse_article_ref

LICENSE = "CC BY-SA 4.0"
BACKEND = "parsoid"
WIKI_TEMPLATE_DROPPED = "wiki.template_dropped"

# Templates that carry navigation or maintenance, not article content.
_DROPPED_CLASSES = {
    "navbox": "navigation box",
    "vertical-navbox": "navigation box",
    "navbox-styles": "navigation box",
    "mw-authority-control": "authority control",
    "ambox": "maintenance box",
    "ombox": "maintenance box",
    "tmbox": "maintenance box",
    "mbox-small": "maintenance box",
    "metadata": "metadata box",
    "sistersitebox": "sister project box",
    "side-box": "side box",
    "mw-editsection": "edit link",
    "toc": "table of contents",
    "mw-empty-elt": "empty element",
    "sidebar": "series sidebar",
    "navbar": "navigation links",
    "shortdescription": "short description",
    "Inline-Template": "inline maintenance tag",
}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_HIGHLIGHT_LANG = re.compile(r"(?:^|\s)mw-highlight-lang-([\w+#.-]+)")
_REVISION_ABOUT = re.compile(r"/revision/(\d+)$")


def _has_class(*names: str) -> Callable[[Element], bool]:
    wanted = frozenset(names)
    return lambda el: not wanted.isdisjoint(classes(el))


def _typeof(el: Element) -> frozenset[str]:
    return frozenset((el.get("typeof") or "").split())


def _data_mw(el: Element) -> dict[str, object]:
    try:
        value = json.loads(el.get("data-mw") or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


# --- Math -------------------------------------------------------------------------------


def _is_math(el: Element) -> bool:
    return "mw:Extension/math" in _typeof(el) or "mwe-math-element" in classes(el)


def _is_display_math(el: Element) -> bool:
    if "mwe-math-element-block" in classes(el):
        return True
    attrs = _data_mw(el).get("attrs")
    return isinstance(attrs, dict) and attrs.get("display") == "block"


def _math_tex(el: Element) -> str | None:
    body = _data_mw(el).get("body")
    if isinstance(body, dict) and isinstance(body.get("extsrc"), str) and body["extsrc"].strip():
        return strip_displaystyle(str(body["extsrc"]))
    for node in el.iter():
        if tag_of(node) == "math":
            tex = tex_of_math(node)
            if tex:
                return tex
    return None


def _math_block(el: Element, m: HtmlMapper) -> list[Block]:
    tex = _math_tex(el)
    if tex is None:
        return m.math_block(el)  # reports math.unrecoverable
    return [MathBlock(tex, m.provenance)]


def _math_inline(el: Element, m: HtmlMapper) -> list[Inline]:
    tex = _math_tex(el)
    if tex is None:
        m.report(codes.MATH_UNRECOVERABLE, "Formula without recoverable LaTeX.", "warning")
        return []
    return [InlineMath(tex)]


# --- References -------------------------------------------------------------------------


def _is_ref(el: Element) -> bool:
    return "mw:Extension/ref" in _typeof(el)


def _ref_key(el: Element) -> str | None:
    for a in el.iter("a"):
        fragment = urllib.parse.urlsplit(a.get("href") or "").fragment
        if fragment:
            return urllib.parse.unquote(fragment)
    return None


def _ref_inline(el: Element, m: HtmlMapper) -> list[Inline]:
    key = _ref_key(el)
    return [FootnoteRef(key)] if key else []


def _is_reference_list(el: Element) -> bool:
    return tag_of(el) == "ol" and "mw-references" in classes(el)


def _reference_list(el: Element, m: HtmlMapper) -> list[Block]:
    defs: list[Block] = []
    for li in el:
        key = li.get("id")
        if tag_of(li) != "li" or not key:
            continue
        text = next((c for c in li.iter() if "mw-reference-text" in classes(c)), None)
        if text is None:
            for backlink in [c for c in li.iter() if "mw-cite-backlink" in classes(c)]:
                backlink.getparent().remove(backlink)  # type: ignore[union-attr]
            text = li
        defs.append(FootnoteDef(key, tuple(m.blocks(text)), m.provenance))
    return defs


# --- Sections, figures, infoboxes ------------------------------------------------------


def _section(el: Element, m: HtmlMapper) -> list[Block]:
    """A section emptied by extracting its notes (e.g. "References") loses its heading."""
    blocks = m.blocks(el)
    content = [b for b in blocks if not isinstance(b, FootnoteDef)]
    if len(content) == 1 and isinstance(content[0], Heading):
        return [b for b in blocks if isinstance(b, FootnoteDef)]
    return blocks


def _is_file(el: Element) -> bool:
    return any(t.startswith("mw:File") for t in _typeof(el))


def _figure_block(el: Element, m: HtmlMapper) -> list[Block]:
    m.report_once(codes.IMAGE_DROPPED, "Wikipedia images are not downloaded; captions kept.")
    caption = next((c for c in el if tag_of(c) == "figcaption"), None)
    if caption is None:
        return []
    inlines = unwrap_emphasis(tuple(m.inlines(caption)))
    return [Paragraph((Emph(inlines),), m.provenance)] if inlines else []


def _is_highlight(el: Element) -> bool:
    return tag_of(el) == "div" and "mw-highlight" in classes(el)


def _highlight_block(el: Element, m: HtmlMapper) -> list[Block]:
    """<syntaxhighlight>: the language is on the wrapper, the code in its <pre>."""
    pre = next((c for c in el.iter() if tag_of(c) == "pre"), None)
    if pre is None:
        return m.blocks(el)
    lang = _HIGHLIGHT_LANG.search(el.get("class") or "")
    return [CodeBlock(text_content(pre), lang.group(1) if lang else None, m.provenance)]


def _is_hidden(el: Element) -> bool:
    return bool(_HIDDEN_STYLE.search(el.get("style") or ""))


def _file_inline(el: Element, m: HtmlMapper) -> list[Inline]:
    m.report_once(codes.IMAGE_DROPPED, "Wikipedia images are not downloaded; captions kept.")
    return []


def _wikilink(el: Element, m: HtmlMapper) -> list[Inline]:
    """Wiki links: absolute URL, no title attribute (it only repeats the target).

    Links to articles that do not exist yet ("red links", class `new`) point to an edit
    page, so only their text is kept.
    """
    children = tuple(m.inlines(el))
    href = el.get("href")
    if not href or "new" in classes(el):
        return list(children)
    return [Link(children, m.resolve_href(href), None)]


@dataclass
class _Infoboxes:
    tables: list[Table] = field(default_factory=list)

    def handle(self, el: Element, m: HtmlMapper) -> list[Block]:
        m.report(codes.WIKI_INFOBOX_FLATTENED, "Infobox flattened to a two-column table.")
        title: list[Inline] = []
        rows: list[Row] = []
        caption = next((c for c in el if tag_of(c) == "caption"), None)
        if caption is not None:
            title = m.inlines(caption)
        pending_label = False  # last row is a label (<th>) still waiting for its value
        for tr in el.iter("tr"):
            cells = [c for c in tr if tag_of(c) in ("th", "td")]
            if not cells or _nearest_table(tr) is not el:
                continue
            if len(cells) >= 2:
                label = blocks_to_inlines(m.blocks(cells[0]))
                value = blocks_to_inlines([b for c in cells[1:] for b in m.blocks(c)])
                rows.append(Row((_cell(label), _cell(value))))
                pending_label = False
                continue
            text = blocks_to_inlines(m.blocks(cells[0]))
            if not text:
                continue
            if not title and not rows:
                title = text
            elif pending_label and tag_of(cells[0]) == "td":
                # A full-width value under a full-width label ("Influenced by" + list).
                rows[-1] = Row((rows[-1].cells[0], _cell(text)))
                pending_label = False
            else:
                rows.append(Row((_cell(text), _cell([]))))
                pending_label = tag_of(cells[0]) == "th"
        header = (Row((_cell(title), _cell([]))),) if title else ()
        self.tables.append(Table(header, tuple(rows), (), m.provenance))
        return []


def _cell(inlines: list[Inline]) -> Cell:
    return Cell((Paragraph(tuple(inlines)),) if inlines else ())


def _nearest_table(el: Element) -> Element | None:
    for ancestor in el.iterancestors():
        if tag_of(ancestor) == "table":
            return ancestor
    return None


def _handlers(infoboxes: _Infoboxes) -> list[Handler]:
    handlers = [
        Handler("math", _is_math, _math_block, _math_inline, is_block=_is_display_math),
        # Hidden content (display:none) is not part of the rendered article. Checked after
        # math, whose MathML lives in a hidden accessibility span.
        Handler("hidden", _is_hidden, block=lambda el, m: [], inline=lambda el, m: []),
        Handler("syntaxhighlight", _is_highlight, block=_highlight_block),
        Handler("ref", _is_ref, inline=_ref_inline),
        Handler("references", _is_reference_list, block=_reference_list),
        Handler("section", lambda el: tag_of(el) == "section", block=_section),
        Handler(
            "infobox",
            lambda el: tag_of(el) == "table" and "infobox" in classes(el),
            block=infoboxes.handle,
            inline=lambda el, m: [],
            is_block=lambda el: True,
        ),
        Handler(
            "figure",
            lambda el: tag_of(el) == "figure" or (tag_of(el) == "figure-inline"),
            block=_figure_block,
            inline=_file_inline,
            is_block=lambda el: tag_of(el) == "figure",
        ),
        Handler("file", _is_file, block=lambda el, m: [], inline=_file_inline),
        Handler(
            "citation",
            lambda el: tag_of(el) == "cite" and "citation" in classes(el),
            inline=lambda el, m: m.inlines(el),
        ),
        Handler(
            "wikilink",
            lambda el: tag_of(el) == "a" and "mw:WikiLink" in (el.get("rel") or "").split(),
            inline=_wikilink,
        ),
    ]
    for cls, name in _DROPPED_CLASSES.items():
        handlers.append(drop_handler(name, _has_class(cls), WIKI_TEMPLATE_DROPPED))
    return handlers


# --- Reader -----------------------------------------------------------------------------


@dataclass
class WikipediaReader:
    client: WikimediaClient | None = None
    revision: int | None = None

    def read(self, source: str) -> Document:
        ref = parse_article_ref(source)
        client = self.client or WikimediaClient()
        revision = self.revision or client.latest_revision(ref)
        return read_parsoid_html(client.revision_html(ref, revision), fallback=ref)


def read_parsoid_html(data: bytes, *, fallback: ArticleRef | None = None) -> Document:
    """Convert a Parsoid HTML document (as served by the core REST API) to IR."""
    root = parse_html(data)
    head = next((c for c in root if tag_of(c) == "head"), None)
    body = next((c for c in root if tag_of(c) == "body"), None)
    if body is None:
        body = root

    canonical = _canonical_url(head, fallback)
    base = _base_url(head, fallback)
    infoboxes = _Infoboxes()
    mapper = HtmlMapper(
        source_ref=canonical,
        handlers=_handlers(infoboxes),
        resolve_href=lambda href: _resolve(base, href),
    )
    blocks = promote_lone_math(hoist_footnote_defs(mapper.blocks(body)))
    blocks = list(infoboxes.tables) + blocks  # ADR-006: infobox right after the title

    title = None
    if head is not None:
        title_el = next((c for c in head if tag_of(c) == "title"), None)
        title = text_content(title_el).strip() if title_el is not None else None
    if not title and fallback is not None:
        title = fallback.title.replace("_", " ")

    language = body.get("lang") or (fallback.lang if fallback else None)
    meta = DocumentMeta(
        title=title or None,
        source_type="wikipedia",
        source=canonical,
        source_sha256=hashlib.sha256(data).hexdigest(),
        backend=BACKEND,
        language=language,
        license=LICENSE,
        revision_id=_revision_id(root),
    )
    return Document(meta, tuple(blocks), (), tuple(mapper.diagnostics))


def _canonical_url(head: Element | None, fallback: ArticleRef | None) -> str:
    if head is not None:
        for link in head.iter("link"):
            if link.get("rel") == "dc:isVersionOf" and link.get("href"):
                return _absolute(str(link.get("href")))
    return fallback.url if fallback is not None else ""


def _base_url(head: Element | None, fallback: ArticleRef | None) -> str:
    if head is not None:
        for base in head.iter("base"):
            if base.get("href"):
                return _absolute(str(base.get("href")))
    return f"https://{fallback.host}/wiki/" if fallback is not None else ""


def _absolute(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def _resolve(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, _absolute(href)) if base else _absolute(href)


def _revision_id(root: Element) -> int | None:
    m = _REVISION_ABOUT.search(root.get("about") or "")
    return int(m.group(1)) if m else None


__all__ = ["WikipediaReader", "read_parsoid_html"]
