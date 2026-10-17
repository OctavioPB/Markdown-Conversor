"""Generic HTML -> IR mapping shared by the EPUB and Wikipedia readers.

Source-specific rules are registered as `Handler`s (matched per element before the default
mapping), never by forking the mapping below. Unknown elements keep their content and are
reported once per tag name.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from lxml import etree, html

from tomd.errors import UnreadableInputError
from tomd.ir import (
    Block,
    BlockQuote,
    Cell,
    Code,
    CodeBlock,
    Diagnostic,
    Emph,
    FootnoteDef,
    Heading,
    Inline,
    InlineMath,
    LineBreak,
    Link,
    ListBlock,
    ListItem,
    MathBlock,
    Paragraph,
    Provenance,
    Row,
    Strong,
    Table,
    Text,
    ThematicBreak,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import (
    HTML_DEFINITION_LIST_FLATTENED,
    HTML_UNKNOWN_ELEMENT,
    diagnose,
)

type Element = etree._Element

# Diagnostic codes owned by the HTML mapping.


_HTML_WS = re.compile(r"[ \t\n\r\f]+")

BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "center", "dd", "details",
        "div", "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "table", "ul",
    }
)  # fmt: skip
# Inline tags mapped transparently (their content is kept, the markup has no IR node).
TRANSPARENT_INLINE = frozenset(
    {
        "abbr", "bdi", "bdo", "data", "del", "font", "ins", "label", "mark", "q", "rb",
        "ruby", "s", "small", "span", "strike", "time", "u", "wbr",
    }
)  # fmt: skip
SILENT_DROP = frozenset(
    {"head", "link", "meta", "noscript", "rp", "rt", "script", "style", "template", "title"}
)
EMPH_TAGS = frozenset({"em", "i", "cite", "dfn", "var"})
STRONG_TAGS = frozenset({"strong", "b"})
CODE_TAGS = frozenset({"code", "kbd", "samp", "tt"})

_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_LANG_CLASS = re.compile(r"(?:^|\s)(?:lang|language)-([\w+#.-]+)")
_TEX_DISPLAYSTYLE = re.compile(r"^\{\\displaystyle\s*(.*)\}$", re.DOTALL)


_REPLACEMENT_UTF8 = "\ufffd".encode()


def invalid_utf8_count(data: bytes) -> int:
    """Invalid UTF-8 sequences in `data` (each becomes one U+FFFD when parsed)."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        decoded = data.decode("utf-8", errors="replace")
        return decoded.count("\ufffd") - data.count(_REPLACEMENT_UTF8)
    return 0


def parse_html(data: bytes) -> Element:
    """Parse an HTML document safely (no network, no entity expansion of external DTDs).

    The bytes are decoded here, invalid sequences replaced by U+FFFD: lxml decodes text
    lazily and would raise on first access, deep inside a reader."""
    text = data.decode("utf-8", errors="replace").replace("\x00", "\ufffd")
    parser = html.HTMLParser(no_network=True, remove_comments=True)
    try:
        root = html.document_fromstring(text, parser=parser)
    except (etree.ParserError, ValueError) as exc:
        raise UnreadableInputError(f"HTML could not be parsed: {exc}") from exc
    return root


def tag_of(el: Element) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname.lower() if tag.startswith("{") else tag.lower()


def classes(el: Element) -> frozenset[str]:
    return frozenset((el.get("class") or "").split())


def text_content(el: Element) -> str:
    return "".join(str(t) for t in el.itertext())


@dataclass(frozen=True, slots=True)
class Handler:
    """A source-specific rule. `block`/`inline` return the mapped nodes for that context;
    when the function for the current context is missing, the default mapping applies."""

    name: str
    matches: Callable[[Element], bool]
    block: Callable[[Element, "HtmlMapper"], list[Block]] | None = None
    inline: Callable[[Element, "HtmlMapper"], list[Inline]] | None = None
    # Whether a matching element splits a paragraph when it appears inside one.
    is_block: Callable[[Element], bool] | None = None


def drop_handler(name: str, matches: Callable[[Element], bool], code: str) -> Handler:
    """Handler that removes matching elements and reports it (severity info)."""

    def report(el: Element, m: "HtmlMapper") -> list[Block]:
        m.report_once(code, f"Removed {name} element(s).")
        return []

    def report_inline(el: Element, m: "HtmlMapper") -> list[Inline]:
        m.report_once(code, f"Removed {name} element(s).")
        return []

    return Handler(name, matches, block=report, inline=report_inline)


@dataclass
class HtmlMapper:
    source_ref: str
    handlers: Sequence[Handler] = ()
    resolve_href: Callable[[str], str] = lambda href: href
    diagnostics: list[Diagnostic] = field(default_factory=list)
    _reported: set[tuple[str, str]] = field(default_factory=set)

    # --- diagnostics ---------------------------------------------------------------------

    def report(self, code: str, message: str) -> None:
        self.diagnostics.append(diagnose(code, message, Provenance(self.source_ref)))

    def report_once(self, code: str, message: str) -> None:
        if (code, message) not in self._reported:
            self._reported.add((code, message))
            self.report(code, message)

    @property
    def provenance(self) -> Provenance:
        return Provenance(self.source_ref)

    # --- dispatch ------------------------------------------------------------------------

    def _handler(self, el: Element) -> Handler | None:
        for handler in self.handlers:
            if handler.matches(el):
                return handler
        return None

    def is_block(self, el: Element) -> bool:
        handler = self._handler(el)
        if handler is not None and handler.is_block is not None:
            return handler.is_block(el)
        return tag_of(el) in BLOCK_TAGS or (tag_of(el) == "math" and _is_display_math(el))

    # --- blocks --------------------------------------------------------------------------

    def blocks(self, el: Element) -> list[Block]:
        """Map the *content* of a container element to blocks."""
        out: list[Block] = []
        run: list[Inline] = []

        def flush() -> None:
            # Whitespace between block elements is formatting, not a paragraph.
            if any(not (isinstance(n, Text) and not n.value.strip()) for n in run):
                out.append(Paragraph(tuple(run), self.provenance))
            run.clear()

        if el.text:
            run.append(Text(_ws(el.text)))
        for child in el:
            if not isinstance(child.tag, str):
                if child.tail:
                    run.append(Text(_ws(child.tail)))
                continue
            if self.is_block(child):
                flush()
                out.extend(self.block(child))
            else:
                run.extend(self.inline(child))
            if child.tail:
                run.append(Text(_ws(child.tail)))
        flush()
        return out

    def block(self, el: Element) -> list[Block]:
        """Map one block-level element."""
        handler = self._handler(el)
        if handler is not None and handler.block is not None:
            return handler.block(el, self)
        tag = tag_of(el)
        prov = self.provenance
        match tag:
            case _ if tag in SILENT_DROP:
                return []
            case "h1" | "h2" | "h3" | "h4" | "h5" | "h6":
                return [Heading(int(tag[1]), tuple(self.inlines(el)), prov)]
            case "p":
                return self.blocks(el)
            case "ul" | "ol":
                return [self.list_block(el)]
            case "blockquote":
                return [BlockQuote(tuple(self.blocks(el)), prov)]
            case "pre":
                return [self.code_block(el)]
            case "hr":
                return [ThematicBreak(prov)]
            case "table":
                return [self.table(el)]
            case "dl":
                return self.definition_list(el)
            case "math":
                return self.math_block(el)
            case _:
                if tag not in BLOCK_TAGS:
                    self._unknown(tag)
                return self.blocks(el)

    def list_block(self, el: Element) -> ListBlock:
        ordered = tag_of(el) == "ol"
        start = _int_attr(el, "start", 1) if ordered else 1
        items: list[ListItem] = []
        for child in el:
            if tag_of(child) == "li":
                items.append(ListItem(tuple(self.blocks(child)), self.provenance))
            elif isinstance(child.tag, str):
                # Stray content directly inside <ul>/<ol>: keep it as its own item.
                blocks = self.block(child) if self.is_block(child) else []
                if not blocks:
                    inl = self.inline(child)
                    blocks = [Paragraph(tuple(inl), self.provenance)] if inl else []
                if blocks:
                    items.append(ListItem(tuple(blocks), self.provenance))
        return ListBlock(tuple(items), ordered, start, self.provenance)

    def code_block(self, el: Element) -> CodeBlock:
        code_el = next((c for c in el if tag_of(c) == "code"), None)
        language = None
        for candidate in (el, code_el):
            if candidate is not None:
                m = _LANG_CLASS.search(candidate.get("class") or "")
                if m:
                    language = m.group(1)
                    break
        return CodeBlock(text_content(el), language, self.provenance)

    def definition_list(self, el: Element) -> list[Block]:
        """No IR node for <dl>: terms become bold paragraphs, definitions their blocks."""
        out: list[Block] = []
        has_terms = False
        for child in el:
            tag = tag_of(child)
            if tag == "dt":
                has_terms = True
                out.append(Paragraph((Strong(tuple(self.inlines(child))),), self.provenance))
            elif tag == "dd":
                out.extend(self.blocks(child))
            elif isinstance(child.tag, str):
                out.extend(self.block(child) if self.is_block(child) else self.blocks(child))
        if has_terms:
            self.report_once(
                HTML_DEFINITION_LIST_FLATTENED, "Definition lists flattened to paragraphs."
            )
        return out

    def table(self, el: Element) -> Table:
        caption: tuple[Inline, ...] = ()
        header_trs: list[Element] = []
        body_trs: list[Element] = []
        for child in el:
            tag = tag_of(child)
            if tag == "caption":
                caption = tuple(self.inlines(child))
            elif tag == "thead":
                header_trs.extend(tr for tr in child if tag_of(tr) == "tr")
            elif tag in ("tbody", "tfoot"):
                body_trs.extend(tr for tr in child if tag_of(tr) == "tr")
            elif tag == "tr":
                body_trs.append(child)
        if not header_trs:
            # Without <thead>, leading rows made only of <th> are the header.
            while body_trs and _all_th(body_trs[0]):
                header_trs.append(body_trs.pop(0))
        return Table(
            tuple(self._row(tr) for tr in header_trs),
            tuple(self._row(tr) for tr in body_trs),
            caption,
            self.provenance,
        )

    def _row(self, tr: Element) -> Row:
        cells: list[Cell] = []
        for cell in tr:
            if tag_of(cell) in ("td", "th"):
                cells.append(
                    Cell(
                        tuple(self.blocks(cell)),
                        rowspan=_int_attr(cell, "rowspan", 1),
                        colspan=_int_attr(cell, "colspan", 1),
                    )
                )
        return Row(tuple(cells))

    def math_block(self, el: Element) -> list[Block]:
        tex = tex_of_math(el)
        if tex is None:
            return [Paragraph(self._unrecoverable_math(el), self.provenance)]
        return [MathBlock(tex, self.provenance)]

    # --- inlines -------------------------------------------------------------------------

    def inlines(self, el: Element) -> list[Inline]:
        """Map the *content* of an element to inlines (block children are flattened)."""
        out: list[Inline] = []
        if el.text:
            out.append(Text(_ws(el.text)))
        for child in el:
            if isinstance(child.tag, str):
                out.extend(self.inline(child))
            if child.tail:
                out.append(Text(_ws(child.tail)))
        return out

    def inline(self, el: Element) -> list[Inline]:
        """Map one element appearing in inline context."""
        handler = self._handler(el)
        if handler is not None and handler.inline is not None:
            return handler.inline(el, self)
        tag = tag_of(el)
        match tag:
            case _ if tag in SILENT_DROP:
                return []
            case _ if tag in EMPH_TAGS:
                return [Emph(tuple(self.inlines(el)))]
            case _ if tag in STRONG_TAGS:
                return [Strong(tuple(self.inlines(el)))]
            case _ if tag in CODE_TAGS:
                return [Code(text_content(el))]
            case "a":
                href = el.get("href")
                children = tuple(self.inlines(el))
                if not href:
                    return list(children)
                return [Link(children, self.resolve_href(href), el.get("title") or None)]
            case "br":
                return [LineBreak()]
            case "sup" | "sub":
                return self._script(el, _SUPERSCRIPT if tag == "sup" else _SUBSCRIPT)
            case "img":
                alt = _ws(el.get("alt") or "").strip()
                self.report_once(codes.IMAGE_DROPPED, "Inline images dropped (alt text kept).")
                return [Text(alt)] if alt else []
            case "math":
                tex = tex_of_math(el)
                return [InlineMath(tex)] if tex is not None else list(self._unrecoverable_math(el))
            case _:
                if tag not in TRANSPARENT_INLINE and tag not in BLOCK_TAGS:
                    self._unknown(tag)
                return self.inlines(el)

    def _script(self, el: Element, table: dict[int, int]) -> list[Inline]:
        content = self.inlines(el)
        if all(isinstance(n, Text) for n in content):
            text = "".join(n.value for n in content if isinstance(n, Text))
            mapped = text.translate(table)
            if all(ord(c) > 127 or c.isspace() for c in mapped):
                return [Text(mapped)]
        return content

    def _unrecoverable_math(self, el: Element) -> tuple[Inline, ...]:
        self.report(codes.MATH_UNRECOVERABLE, "Formula without recoverable LaTeX.")
        text = _ws(text_content(el)).strip()
        return (Code(text),) if text else ()

    def _unknown(self, tag: str) -> None:
        self.report_once(HTML_UNKNOWN_ELEMENT, f"Unknown element <{tag}> mapped by content.")


# --- Helpers ---------------------------------------------------------------------------


def tex_of_math(el: Element) -> str | None:
    """LaTeX from a MathML <math> element's TeX annotation, if present."""
    for node in el.iter():
        if tag_of(node) == "annotation" and (node.get("encoding") or "").lower() in (
            "application/x-tex",
            "tex",
            "latex",
        ):
            return strip_displaystyle(text_content(node))
    alttext = el.get("alttext")
    return strip_displaystyle(alttext) if alttext else None


def strip_displaystyle(tex: str) -> str:
    tex = tex.strip()
    m = _TEX_DISPLAYSTYLE.match(tex)
    return m.group(1).strip() if m else tex


def _is_display_math(el: Element) -> bool:
    return (el.get("display") or "").lower() == "block"


def _ws(value: str) -> str:
    return _HTML_WS.sub(" ", value)


def _int_attr(el: Element, name: str, default: int) -> int:
    try:
        value = int((el.get(name) or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _all_th(tr: Element) -> bool:
    cells = [c for c in tr if tag_of(c) in ("td", "th")]
    return bool(cells) and all(tag_of(c) == "th" for c in cells)


def hoist_footnote_defs(blocks: Sequence[Block]) -> list[Block]:
    """Move FootnoteDefs found anywhere in the tree to the top level (IR invariant)."""
    found: list[FootnoteDef] = []

    def strip(bs: Sequence[Block]) -> tuple[Block, ...]:
        out: list[Block] = []
        for b in bs:
            match b:
                case FootnoteDef():
                    found.append(replace(b, blocks=strip(b.blocks)))
                case ListBlock():
                    items = tuple(replace(it, blocks=strip(it.blocks)) for it in b.items)
                    out.append(replace(b, items=items))
                case BlockQuote():
                    out.append(replace(b, blocks=strip(b.blocks)))
                case Table():
                    out.append(
                        replace(
                            b,
                            header_rows=_strip_rows(b.header_rows, strip),
                            body_rows=_strip_rows(b.body_rows, strip),
                        )
                    )
                case _:
                    out.append(b)
        return tuple(out)

    body = list(strip(blocks))
    return body + list(found)


def _strip_rows(
    rows: tuple[Row, ...], strip: Callable[[Sequence[Block]], tuple[Block, ...]]
) -> tuple[Row, ...]:
    return tuple(Row(tuple(replace(c, blocks=strip(c.blocks)) for c in r.cells)) for r in rows)


def promote_lone_math(blocks: Sequence[Block]) -> list[Block]:
    """A paragraph whose only content is one formula is a displayed formula."""
    out: list[Block] = []
    for b in blocks:
        match b:
            case Paragraph(children):
                content = [n for n in children if not (isinstance(n, Text) and not n.value.strip())]
                if len(content) == 1 and isinstance(content[0], InlineMath):
                    out.append(MathBlock(content[0].tex, b.provenance))
                    continue
                out.append(b)
            case ListBlock():
                items = tuple(
                    replace(it, blocks=tuple(promote_lone_math(it.blocks))) for it in b.items
                )
                out.append(replace(b, items=items))
            case BlockQuote() | FootnoteDef():
                out.append(replace(b, blocks=tuple(promote_lone_math(b.blocks))))
            case _:
                out.append(b)
    return out


def blocks_to_inlines(blocks: Sequence[Block]) -> list[Inline]:
    """Flatten blocks into one line-broken inline run (for cells that must stay inline)."""
    lines: list[list[Inline]] = []
    for b in blocks:
        match b:
            case Paragraph(children) | Heading(_, children):
                lines.append(list(children))
            case ListBlock(items):
                for it in items:
                    lines.append(blocks_to_inlines(it.blocks))
            case BlockQuote(inner):
                lines.append(blocks_to_inlines(inner))
            case CodeBlock(code):
                lines.append([Code(code)])
            case MathBlock(tex):
                lines.append([InlineMath(tex)])
            case _:
                pass
    out: list[Inline] = []
    for line in lines:
        if not line:
            continue
        if out:
            out.append(LineBreak())
        out.extend(line)
    return out
