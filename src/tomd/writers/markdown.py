"""IR -> Markdown serializer. The only module in tomd that emits Markdown.

The contract is docs/MARKDOWN_SPEC.md (rules M01-M22); degradation of structures that do
not fit the dialect follows ADR-006 and ADR-011. Rendering is a pure function of the IR.
"""

import html
import re
import string
import unicodedata
from dataclasses import dataclass, replace

from tomd.ir import (
    Asset,
    Block,
    BlockQuote,
    Cell,
    Code,
    CodeBlock,
    Diagnostic,
    Document,
    DocumentMeta,
    Emph,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    Inline,
    InlineMath,
    LineBreak,
    Link,
    ListBlock,
    MathBlock,
    Paragraph,
    Row,
    Strong,
    Table,
    Text,
    ThematicBreak,
)
from tomd.ir import diagnostics as codes


@dataclass(frozen=True, slots=True)
class RenderResult:
    text: str
    diagnostics: tuple[Diagnostic, ...]  # the document's plus those emitted while writing


def render_markdown(doc: Document, *, slug: str) -> RenderResult:
    """Serialize `doc`. `slug` names the asset folder: `<slug>.assets/<hash>.<ext>`."""
    return _Writer(doc, slug).render()


def asset_filename(asset: Asset) -> str:
    return f"{asset.sha256[:12]}.{_MEDIA_EXTENSIONS.get(asset.media_type, 'bin')}"


def is_gfm_table(table: Table) -> bool:
    """M17: GFM only without spans, with inline-only cells and at most one header row."""
    rows = table.header_rows + table.body_rows
    if not rows or len(table.header_rows) > 1:
        return False
    return all(
        cell.rowspan == 1 and cell.colspan == 1 and _is_inline_cell(cell)
        for row in rows
        for cell in row.cells
    )


_MEDIA_EXTENSIONS = {
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/tiff": "tif",
    "image/webp": "webp",
}

# Separates two consecutive lists of the same kind (or two block quotes) so a parser does
# not merge them (ADR-011).
_BLOCK_SEPARATOR = "<!-- -->"
_FOOTNOTE_INDENT = "    "

# --- Front matter (M02) ----------------------------------------------------------------

# Values of closed vocabularies are written unquoted when unambiguous in YAML 1.1/1.2.
_PLAIN_SCALAR = re.compile(r"[a-z][a-z0-9_-]*\Z")
# Line/paragraph separators, BOM and non-characters: not printable in YAML double quotes.
_YAML_UNSAFE = frozenset({0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF})
_YAML_RESERVED = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null"})


def _yaml_quoted(value: str) -> str:
    out = ['"']
    for c in value:
        code = ord(c)
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif 0xD800 <= code <= 0xDFFF:
            out.append("\\ufffd")
        elif code < 0x20 or 0x7F <= code <= 0x9F or code in _YAML_UNSAFE:
            out.append(f"\\u{code:04x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _yaml_symbol(value: str) -> str:
    if _PLAIN_SCALAR.match(value) and value not in _YAML_RESERVED:
        return value
    return _yaml_quoted(value)


def _front_matter(meta: DocumentMeta, diagnostics_count: int) -> str:
    lines = ["---"]
    if meta.title:
        lines.append(f"title: {_yaml_quoted(meta.title)}")
    lines.append(f"source_type: {_yaml_symbol(meta.source_type)}")
    lines.append(f"source: {_yaml_quoted(meta.source)}")
    if meta.source_sha256:
        lines.append(f"source_sha256: {_yaml_quoted(meta.source_sha256)}")
    if meta.converter_version:
        lines.append(f"converter_version: {_yaml_quoted(meta.converter_version)}")
    if meta.backend:
        lines.append(f"backend: {_yaml_symbol(meta.backend)}")
    if meta.language:
        lines.append(f"language: {_yaml_symbol(meta.language)}")
    if meta.authors:
        lines.append("authors:")
        lines.extend(f"  - {_yaml_quoted(a)}" for a in meta.authors)
    if meta.license:
        lines.append(f"license: {_yaml_quoted(meta.license)}")
    if meta.revision_id is not None:
        lines.append(f"revision_id: {meta.revision_id}")
    lines.append(f"diagnostics_count: {diagnostics_count}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --- Inline preparation ----------------------------------------------------------------

_ASCII_WS = re.compile(r"[ \t\n\r\f\v]+")
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_NEWLINES = re.compile(r"\r\n|[\r\n]")


def _collapse(value: str) -> str:
    """ADR-011: ASCII whitespace runs collapse to one space; Unicode spaces are kept."""
    return _ASCII_WS.sub(" ", _CONTROL.sub("", value))


def _is_ws(c: str | None) -> bool:
    return c is None or c.isspace()


def _is_punct(c: str | None) -> bool:
    return c is not None and unicodedata.category(c)[0] in "PS"


def _prepare(nodes: tuple[Inline, ...], *, flatten: bool) -> tuple[Inline, ...]:
    """Normalize whitespace and move it out of emphasis and links, then trim each line.

    `flatten` (headings, M08) replaces emphasis by its content and line breaks by spaces.
    """
    lines: list[list[Inline]] = [[]]
    for node in _clean(nodes, flatten=flatten, in_link=False):
        if isinstance(node, LineBreak):
            lines.append([])
        else:
            lines[-1].append(node)
    out: list[Inline] = []
    for line in lines:
        line = _merge_text(line)
        # Parsers trim every kind of whitespace at line edges (markdown-it uses str.strip).
        if line and isinstance(line[0], Text):
            line[0] = Text(line[0].value.lstrip())
        if line and isinstance(line[-1], Text):
            line[-1] = Text(line[-1].value.rstrip())
        line = [n for n in line if not (isinstance(n, Text) and not n.value)]
        if not line:
            continue
        if out:
            out.append(LineBreak())
        out.extend(line)
    return tuple(out)


def _clean(nodes: tuple[Inline, ...], *, flatten: bool, in_link: bool) -> list[Inline]:
    out: list[Inline] = []
    for node in nodes:
        match node:
            case Text(value):
                out.append(Text(_collapse(value)))
            case Code(value):
                value = _CONTROL.sub("", _NEWLINES.sub(" ", value))
                if value:
                    out.append(Code(value))
            case InlineMath(tex):
                tex = _collapse(tex).strip()
                if tex:
                    out.append(InlineMath(tex))
            case LineBreak():
                out.append(Text(" ") if flatten else node)
            case Emph(children) | Strong(children) if flatten:
                out.extend(_clean(children, flatten=flatten, in_link=in_link))
            case Link(children) if in_link:  # links cannot nest
                out.extend(_clean(children, flatten=flatten, in_link=True))
            case Emph(children) | Strong(children) | Link(children):
                inner = _merge_text(
                    _clean(children, flatten=flatten, in_link=in_link or isinstance(node, Link))
                )
                lead, inner, trail = _hoist_whitespace(inner)
                out.append(Text(lead))
                if inner or isinstance(node, Link):
                    out.append(replace(node, children=tuple(inner)))
                out.append(Text(trail))
            case FootnoteRef():
                out.append(node)
    return out


def _hoist_whitespace(nodes: list[Inline]) -> tuple[str, list[Inline], str]:
    lead = trail = ""
    if nodes and isinstance(nodes[0], Text):
        value = nodes[0].value
        stripped = value.lstrip()
        lead = value[: len(value) - len(stripped)]
        nodes = [Text(stripped), *nodes[1:]] if stripped else nodes[1:]
    if nodes and isinstance(nodes[-1], Text):
        value = nodes[-1].value
        stripped = value.rstrip()
        trail = value[len(stripped) :]
        nodes = [*nodes[:-1], Text(stripped)] if stripped else nodes[:-1]
    return lead, nodes, trail


def _merge_text(nodes: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    for node in nodes:
        if isinstance(node, Text) and out and isinstance(out[-1], Text):
            out[-1] = Text(_collapse_spaces(out[-1].value + node.value))
        else:
            out.append(node)
    return out


def _collapse_spaces(value: str) -> str:
    return re.sub(" {2,}", " ", value)


# --- Inline escaping (M10) -------------------------------------------------------------

_ASCII_PUNCT = frozenset(string.punctuation)
_ALWAYS_ESCAPE = frozenset("*`[]<$")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_ATX_OPENER = re.compile(r"#{1,6}(?: |\Z)")
_ORDERED_MARKER = re.compile(r"[0-9]{1,9}([.)])(?: |\Z)")
_DASHES_ONLY = re.compile(r"[- ]+\Z")
_EQUALS_ONLY = re.compile(r"[= ]+\Z")


def _line_start_escapes(s: str, *, continuation: bool) -> int | None:
    """Index of the character that must be escaped so `s` cannot open a block construct.

    `continuation` lines (after a hard break) can also become a setext underline or a
    table delimiter row, so their leading `-`, `=`, `:` and `|` are escaped too.
    """
    if not s:
        return None
    first = s[0]
    if first == "#" and _ATX_OPENER.match(s):
        return 0
    if first == "-" and (continuation or len(s) == 1 or s[1] == " " or _DASHES_ONLY.match(s)):
        return 0
    if first == "+" and (len(s) == 1 or s[1] == " "):
        return 0
    if first == ">":
        return 0
    if continuation and (first in ":|" or (first == "=" and _EQUALS_ONLY.match(s))):
        return 0
    m = _ORDERED_MARKER.match(s)
    if m:
        return m.start(1)
    return None


def _escape_text(
    s: str,
    *,
    table: bool,
    prev: str | None,
    nxt: str | None,
    force: int | None = None,
) -> str:
    out: list[str] = []
    last = len(s) - 1
    for i, c in enumerate(s):
        p = s[i - 1] if i else prev
        n = s[i + 1] if i < last else nxt
        if i == force or c in _ALWAYS_ESCAPE:
            escape = True
        elif c == "\\":
            escape = n is None or n in _ASCII_PUNCT
        elif c == "_":
            # Intraword underscores can neither open nor close emphasis.
            escape = not (p is not None and n is not None and p.isalnum() and n.isalnum())
        elif c == "&":
            escape = _ENTITY.match(s, i) is not None
        elif c == "~":
            escape = p == "~" or n == "~"
        elif c == "!":
            escape = n == "["  # "![" opens an image
        elif c == "^":
            # "^[" opens an inline footnote; "[^" a footnote reference or definition, whose
            # label scan ignores backslash escapes (markdown-it footnote plugin).
            escape = n == "[" or p == "["
        elif c == "|":
            escape = table
        else:
            escape = False
        out.append("\\" + c if escape else c)
    return "".join(out)


def _escape_tex(tex: str) -> str:
    """A bare `$` would close the math span early; `\\$` means the same in LaTeX.

    A trailing odd backslash (invalid TeX) would escape the closing `$`, so it is doubled.
    """
    tex = re.sub(r"(?<!\\)((?:\\\\)*)\$", r"\1\\$", tex)
    trailing = len(tex) - len(tex.rstrip("\\"))
    return tex + "\\" if trailing % 2 else tex


def _code_span(value: str, *, table: bool) -> str:
    if table:
        value = value.replace("|", "\\|")
    longest = max((len(m) for m in re.findall("`+", value)), default=0)
    fence = "`" * (longest + 1)
    pad = value[0] == "`" or value[-1] == "`" or (value[0] == value[-1] == " " and value.strip())
    return f"{fence} {value} {fence}" if pad else f"{fence}{value}{fence}"


def _link_destination(url: str, *, table: bool) -> str:
    url = _NEWLINES.sub("", url)
    if not url or any(c in " ()<>" or ord(c) < 0x20 for c in url):
        dest = "<" + re.sub(r"([\\<>])", r"\\\1", url) + ">"
    else:
        # "\\" always reads as one backslash; a lone one could escape the closing ")".
        dest = url.replace("\\", "\\\\")
    return dest.replace("|", "\\|") if table else dest


def _link_title(title: str, *, table: bool) -> str:
    quoted = '"' + re.sub(r'(["\\])', r"\\\1", _collapse(title)) + '"'
    return quoted.replace("|", "\\|") if table else quoted


@dataclass(slots=True)
class _Seg:
    """A rendered inline. Emphasis is resolved once its neighbours are known."""

    text: str
    emph: str | None = None  # "*" or "**" while unresolved
    inner_starts_delim: bool = False
    inner_ends_delim: bool = False
    md_delim: bool = False  # resolved emphasis written with * delimiters

    @property
    def first(self) -> str | None:
        return "*" if self.emph else (self.text[0] if self.text else None)

    @property
    def last(self) -> str | None:
        return "*" if self.emph else (self.text[-1] if self.text else None)


@dataclass(frozen=True, slots=True)
class _Rendered:
    text: str
    starts_delim: bool
    ends_delim: bool


class _InlineRenderer:
    def __init__(self, *, table: bool, line_starts: bool) -> None:
        self.table = table
        self.line_starts = line_starts  # apply block-start escaping (paragraphs)

    def render(
        self, nodes: tuple[Inline, ...], *, prev: str | None = None, at_start: bool = True
    ) -> _Rendered:
        segs: list[_Seg] = []
        at_line_start = self.line_starts and at_start
        continuation = False
        after_fnref = False
        for i, node in enumerate(nodes):
            seg_prev = segs[-1].last if segs else prev
            nxt_node = nodes[i + 1] if i + 1 < len(nodes) else None
            match node:
                case Text(value):
                    force = None
                    if at_line_start:
                        force = _line_start_escapes(value, continuation=continuation)
                    elif after_fnref and value[:1] in ("(", ":"):
                        force = 0  # "[^1](x)" is a link, "[^1]:" a definition
                    nxt = None
                    if isinstance(nxt_node, Link | FootnoteRef):
                        nxt = "["
                    elif isinstance(nxt_node, Text) and nxt_node.value:
                        nxt = nxt_node.value[0]
                    text = _escape_text(
                        value, table=self.table, prev=seg_prev, nxt=nxt, force=force
                    )
                    segs.append(_Seg(text))
                case Code(value):
                    segs.append(_Seg(_code_span(value, table=self.table)))
                case InlineMath(tex):
                    tex = _escape_tex(tex)
                    tex = tex.replace("|", "\\|") if self.table else tex
                    segs.append(_Seg(f"${tex}$"))
                case FootnoteRef(key):
                    segs.append(_Seg(f"[^{_footnote_label(key)}]"))
                case LineBreak():
                    segs.append(_Seg("<br>" if self.table else "\\\n"))
                case Link(children, href, title):
                    inner = self.inline_child(children, prev="[")
                    dest = _link_destination(href, table=self.table)
                    if title:
                        dest += " " + _link_title(title, table=self.table)
                    segs.append(_Seg(f"[{inner.text}]({dest})"))
                case Emph(children) | Strong(children):
                    delim = "*" if isinstance(node, Emph) else "**"
                    inner = self.inline_child(children, prev="*")
                    segs.append(_Seg(inner.text, delim, inner.starts_delim, inner.ends_delim))
            after_fnref = isinstance(node, FootnoteRef)
            at_line_start = self.line_starts and isinstance(node, LineBreak)
            continuation = continuation or isinstance(node, LineBreak)
        return self._resolve(segs, prev)

    def inline_child(self, nodes: tuple[Inline, ...], *, prev: str) -> _Rendered:
        return self.render(nodes, prev=prev, at_start=False)

    @staticmethod
    def _resolve(segs: list[_Seg], prev: str | None) -> _Rendered:
        out: list[str] = []
        prev_md = False
        for i, seg in enumerate(segs):
            if seg.emph is None:
                out.append(seg.text)
                prev_md = False
                continue
            before = out[-1][-1] if out and out[-1] else prev
            after = segs[i + 1].first if i + 1 < len(segs) else None
            first, last = seg.text[0], seg.text[-1]
            opens = not _is_ws(first) and (
                not _is_punct(first) or _is_ws(before) or _is_punct(before)
            )
            closes = not _is_ws(last) and (not _is_punct(last) or _is_ws(after) or _is_punct(after))
            clash = seg.inner_starts_delim or seg.inner_ends_delim or prev_md
            seg.md_delim = opens and closes and not clash
            if seg.md_delim:
                out.append(f"{seg.emph}{seg.text}{seg.emph}")
            else:
                tag = "em" if seg.emph == "*" else "strong"
                out.append(f"<{tag}>{seg.text}</{tag}>")
            prev_md = seg.md_delim
        starts = bool(segs) and segs[0].md_delim
        ends = bool(segs) and segs[-1].md_delim
        return _Rendered("".join(out), starts, ends)


def _footnote_label(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "-", key) or "-"


# --- Blocks ----------------------------------------------------------------------------


def _is_inline_cell(cell: Cell) -> bool:
    return not cell.blocks or (len(cell.blocks) == 1 and isinstance(cell.blocks[0], Paragraph))


def _is_tight_item(blocks: tuple[Block, ...]) -> bool:
    match blocks:
        case () | (Paragraph(),):
            return True
        case (Paragraph(), ListBlock() as sub):
            # Only a list starting at 1 (or bulleted) may interrupt the paragraph.
            return not sub.ordered or sub.start == 1
        case _:
            return False


def _indent(text: str, first: str, rest: str) -> str:
    lines = text.split("\n")
    head = [first + lines[0]]
    return "\n".join(head + [rest + line if line else "" for line in lines[1:]])


def _needs_separator(prev: Block, block: Block) -> bool:
    if isinstance(prev, ListBlock) and isinstance(block, ListBlock):
        return prev.ordered == block.ordered
    return isinstance(prev, BlockQuote) and isinstance(block, BlockQuote)


class _Writer:
    def __init__(self, doc: Document, slug: str) -> None:
        self.doc = doc
        self.slug = slug
        self.assets = {a.sha256: a for a in doc.assets}
        self.diagnostics: list[Diagnostic] = []

    def render(self) -> RenderResult:
        body = tuple(b for b in self.doc.blocks if not isinstance(b, FootnoteDef))
        notes = sorted(
            (b for b in self.doc.blocks if isinstance(b, FootnoteDef)),
            key=lambda d: (0, int(d.key), "") if d.key.isdigit() else (1, 0, d.key),
        )
        parts: list[str] = []
        if self.doc.meta.title:
            parts.append(self.heading(Heading(1, (Text(self.doc.meta.title),))))
        parts.append(self.blocks(body))
        parts.extend(self.footnote_def(d) for d in notes)
        markdown = "\n\n".join(p for p in parts if p)

        diagnostics = self.doc.diagnostics + tuple(self.diagnostics)
        front = _front_matter(self.doc.meta, len(diagnostics))
        text = f"{front}\n{markdown}\n" if markdown else front
        return RenderResult(text, diagnostics)

    def blocks(self, blocks: tuple[Block, ...], sep: str = "\n\n") -> str:
        parts: list[str] = []
        prev: Block | None = None
        for block in blocks:
            rendered = self.block(block)
            if not rendered:
                continue
            if prev is not None and _needs_separator(prev, block):
                parts.append(_BLOCK_SEPARATOR)
            parts.append(rendered)
            prev = block
        return sep.join(parts)

    def block(self, block: Block) -> str:
        match block:
            case Heading():
                return self.heading(block)
            case Paragraph(children):
                return self.paragraph(children)
            case ListBlock():
                return self.list_block(block)
            case BlockQuote(blocks):
                content = self.blocks(blocks)
                lines = content.split("\n") if content else []
                return "\n".join("> " + line if line else ">" for line in lines)
            case CodeBlock(code, language):
                return self.code_block(code, language)
            case MathBlock(tex):
                tex = _NEWLINES.sub("\n", tex).strip("\n")
                return f"$$\n{_escape_tex(tex)}\n$$" if tex.strip() else ""
            case Table():
                return self.table(block)
            case Image():
                return self.image(block)
            case FootnoteDef():
                return self.footnote_def(block)
            case ThematicBreak():
                return "---"

    def heading(self, heading: Heading) -> str:
        nodes = _prepare(heading.children, flatten=True)
        text = _InlineRenderer(table=False, line_starts=False).render(nodes).text
        if not text:
            return ""
        if text.endswith("#"):  # would be read as a closing sequence
            text = text[:-1] + "\\#"
        return "#" * heading.level + " " + text

    def paragraph(self, children: tuple[Inline, ...]) -> str:
        nodes = _prepare(children, flatten=False)
        return _InlineRenderer(table=False, line_starts=True).render(nodes).text

    def list_block(self, lst: ListBlock) -> str:
        tight = all(_is_tight_item(it.blocks) for it in lst.items)
        sep = "\n" if tight else "\n\n"
        start = max(lst.start, 0)
        items: list[str] = []
        for i, item in enumerate(lst.items):
            marker = f"{start + i}." if lst.ordered else "-"
            content = self.blocks(item.blocks, sep)
            pad = " " * (len(marker) + 1)
            if not content:
                items.append(marker)
            elif not lst.ordered and isinstance(item.blocks[0], ThematicBreak):
                # "- ---" would be read as a single thematic break; "- ***" is not.
                items.append(_indent("***" + content[3:], marker + " ", pad))
            else:
                items.append(_indent(content, marker + " ", pad))
        return sep.join(items)

    def code_block(self, code: str, language: str | None) -> str:
        code = _NEWLINES.sub("\n", code).rstrip("\n")
        if not code.strip():
            return ""
        longest = max((len(m) for m in re.findall("`+", code)), default=0)
        fence = "`" * max(3, longest + 1)
        info = (language or "").replace("`", "").split()
        return f"{fence}{info[0] if info else ''}\n{code}\n{fence}"

    def footnote_def(self, fdef: FootnoteDef) -> str:
        label = f"[^{_footnote_label(fdef.key)}]:"
        content = self.blocks(fdef.blocks)
        return _indent(content, label + " ", _FOOTNOTE_INDENT) if content else label

    def caption(self, caption: tuple[Inline, ...]) -> str:
        return self.paragraph((Emph(caption),)) if caption else ""

    def image(self, image: Image) -> str:
        caption = self.caption(image.caption)
        asset = self.assets.get(image.src_asset)
        if asset is None:
            self.diagnostics.append(
                Diagnostic(
                    codes.IMAGE_DROPPED,
                    "warning",
                    f"Image asset {image.src_asset[:12]!r} not found; caption kept.",
                    image.provenance,
                )
            )
            return caption
        alt = _escape_text(_collapse(image.alt).strip(), table=False, prev="[", nxt="]")
        dest = _link_destination(f"{self.slug}.assets/{asset_filename(asset)}", table=False)
        return "\n\n".join(p for p in (f"![{alt}]({dest})", caption) if p)

    def table(self, table: Table) -> str:
        caption = self.caption(table.caption)
        if not table.header_rows and not table.body_rows:
            self.diagnostics.append(
                Diagnostic(
                    codes.TABLE_EMPTY_DROPPED, "info", "Empty table dropped.", table.provenance
                )
            )
            return caption
        if is_gfm_table(table):
            rendered = self.gfm_table(table)
        else:
            self.diagnostics.append(
                Diagnostic(
                    codes.TABLE_HTML_FALLBACK,
                    "info",
                    "Table has spans, block content or several header rows; emitted as HTML.",
                    table.provenance,
                )
            )
            rendered = _HtmlRenderer(self).table(table)
        return "\n\n".join(p for p in (caption, rendered) if p)

    def gfm_table(self, table: Table) -> str:
        header, body = table.header_rows, table.body_rows
        if not header:
            self.diagnostics.append(
                Diagnostic(
                    codes.TABLE_HEADER_PROMOTED,
                    "info",
                    "Table without header row; first row promoted to header.",
                    table.provenance,
                )
            )
            header, body = body[:1], body[1:]
        rows = header + body
        width = max(len(r.cells) for r in rows) or 1
        renderer = _InlineRenderer(table=True, line_starts=False)

        def line(row: Row) -> str:
            cells = [
                renderer.render(_prepare(_cell_inlines(c), flatten=False)).text for c in row.cells
            ]
            cells += [""] * (width - len(cells))
            return "| " + " | ".join(cells) + " |"

        delimiter = "| " + " | ".join(["---"] * width) + " |"
        return "\n".join([line(header[0]), delimiter, *(line(r) for r in body)])


def _cell_inlines(cell: Cell) -> tuple[Inline, ...]:
    if cell.blocks and isinstance(cell.blocks[0], Paragraph):
        return cell.blocks[0].children
    return ()


# --- HTML fallback for tables (M18) ----------------------------------------------------


class _HtmlRenderer:
    """Minimal HTML for tables Markdown cannot express.

    Markdown is not parsed inside an HTML block, and a blank line would end the block, so
    everything is HTML and newlines inside content are written as `&#10;`.
    """

    def __init__(self, writer: _Writer) -> None:
        self.writer = writer

    def table(self, table: Table) -> str:
        lines = ["<table>"]
        for row in table.header_rows:
            lines.append(self.row(row, "th"))
        for row in table.body_rows:
            lines.append(self.row(row, "td"))
        lines.append("</table>")
        return "\n".join(lines)

    def row(self, row: Row, tag: str) -> str:
        cells: list[str] = []
        for cell in row.cells:
            attrs = ""
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
            cells.append(f"<{tag}{attrs}>{self.cell(cell)}</{tag}>")
        return "<tr>" + "".join(cells) + "</tr>"

    def cell(self, cell: Cell) -> str:
        if _is_inline_cell(cell):
            return self.inlines(_cell_inlines(cell))
        return "".join(self.block(b) for b in cell.blocks)

    def block(self, block: Block) -> str:
        match block:
            case Paragraph(children):
                return f"<p>{self.inlines(children)}</p>"
            case Heading(_, children):
                return f"<p><strong>{self.inlines(children)}</strong></p>"
            case ListBlock(items, ordered, start):
                tag = "ol" if ordered else "ul"
                attrs = f' start="{start}"' if ordered and start != 1 else ""
                lis = "".join(
                    "<li>" + "".join(self.block(b) for b in it.blocks) + "</li>" for it in items
                )
                return f"<{tag}{attrs}>{lis}</{tag}>"
            case BlockQuote(blocks) | FootnoteDef(_, blocks):
                inner = "".join(self.block(b) for b in blocks)
                return (
                    f"<blockquote>{inner}</blockquote>" if isinstance(block, BlockQuote) else inner
                )
            case CodeBlock(code):
                return f"<pre><code>{_html_text(code.rstrip())}</code></pre>"
            case MathBlock(tex):
                return f"<p>{_html_text('$$' + tex.strip() + '$$')}</p>"
            case Table():
                return self.table(block)
            case Image(src_asset, alt):
                asset = self.writer.assets.get(src_asset)
                if asset is None:
                    return ""
                src = _html_attr(f"{self.writer.slug}.assets/{asset_filename(asset)}")
                return f'<img src="{src}" alt="{_html_attr(_collapse(alt))}">'
            case ThematicBreak():
                return "<hr>"

    def inlines(self, nodes: tuple[Inline, ...]) -> str:
        out: list[str] = []
        for node in _prepare(nodes, flatten=False):
            match node:
                case Text(value):
                    out.append(_html_text(value))
                case Code(value):
                    out.append(f"<code>{_html_text(value)}</code>")
                case InlineMath(tex):
                    out.append(_html_text(f"${tex}$"))
                case FootnoteRef(key):
                    out.append(f"[^{_footnote_label(key)}]")
                case LineBreak():
                    out.append("<br>")
                case Emph(children):
                    out.append(f"<em>{self.inlines(children)}</em>")
                case Strong(children):
                    out.append(f"<strong>{self.inlines(children)}</strong>")
                case Link(children, href, title):
                    attrs = f' href="{_html_attr(href)}"'
                    if title:
                        attrs += f' title="{_html_attr(title)}"'
                    out.append(f"<a{attrs}>{self.inlines(children)}</a>")
        return "".join(out)


def _html_text(value: str) -> str:
    return html.escape(_NEWLINES.sub("\n", value), quote=False).replace("\n", "&#10;")


def _html_attr(value: str) -> str:
    return html.escape(_NEWLINES.sub("\n", value)).replace("\n", "&#10;")
