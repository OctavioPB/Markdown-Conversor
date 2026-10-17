"""Review logic behind the desktop UI, free of widgets so it can be tested.

The UI shows what the pipeline produced; it never emits Markdown itself (the raw view is
the writer's file, the preview is drawn from the same IR). This module turns a conversion
result into what the window needs: diagnostic rows, page-to-line anchors that keep the
original and the Markdown in step, and styled preview runs.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from tomd.ir import (
    Block,
    BlockQuote,
    Code,
    CodeBlock,
    Diagnostic,
    Document,
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
    Strong,
    Table,
    Text,
    ThematicBreak,
)
from tomd.ir.diagnostics import CATALOG
from tomd.ir.walk import plain_text

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
# Letters and digits only: "_" and "*" are Markdown syntax, escaped or not in the output.
_WORD = re.compile(r"[^\W_]+")
_BETWEEN_WORDS = r"[\W_]+"
# Words of a block used to find it in the Markdown: enough to be unique in practice,
# few enough to survive escaping and emphasis markers between them.
_ANCHOR_WORDS = 4


@dataclass(frozen=True, slots=True)
class ConversionRequest:
    source: str  # file path or Wikipedia reference
    out_dir: Path
    revision: int | None = None
    backend: str = "heuristic"
    ocr: str = "auto"

    @property
    def is_pdf(self) -> bool:
        return self.source.lower().endswith(".pdf") and Path(self.source).is_file()


@dataclass(frozen=True, slots=True)
class DiagnosticRow:
    severity: str
    code: str
    page: int | None
    message: str
    action: str
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class Anchor:
    """Where a top-level block with a known page starts in the Markdown."""

    page: int
    line: int  # 1-based
    bbox: tuple[float, float, float, float] | None  # PDF points, y up


@dataclass
class Review:
    """One conversion as the window shows it."""

    request: ConversionRequest
    markdown_path: Path
    markdown: str
    document: Document | None
    diagnostics: tuple[Diagnostic, ...]
    anchors: list[Anchor] = field(default_factory=list)

    @classmethod
    def from_result(
        cls,
        request: ConversionRequest,
        markdown_path: Path,
        diagnostics: tuple[Diagnostic, ...],
        document: Document | None,
    ) -> "Review":
        markdown = markdown_path.read_text(encoding="utf-8")
        anchors = page_anchors(document, markdown) if document else []
        return cls(request, markdown_path, markdown, document, diagnostics, anchors)

    @property
    def title(self) -> str:
        if self.document and self.document.meta.title:
            return self.document.meta.title
        return self.markdown_path.stem

    def rows(self) -> list[DiagnosticRow]:
        return diagnostic_rows(self.diagnostics)

    def line_for_page(self, page: int) -> int | None:
        """First Markdown line of `page`, or of the nearest earlier page with content."""
        earlier = [a for a in self.anchors if a.page <= page]
        if not earlier:
            return None
        nearest = max(a.page for a in earlier)
        return min(a.line for a in earlier if a.page == nearest)

    def anchor_for_line(self, line: int) -> Anchor | None:
        """The block that contains Markdown `line` (the last one starting at or before it)."""
        found = None
        for anchor in self.anchors:  # in Markdown order
            if anchor.line > line:
                break
            found = anchor
        return found


def diagnostic_rows(diagnostics: tuple[Diagnostic, ...]) -> list[DiagnosticRow]:
    """Most severe first, then by page (document-wide diagnostics first) and code."""
    rows = [
        DiagnosticRow(
            d.severity,
            d.code,
            d.provenance.page if d.provenance else None,
            d.message,
            CATALOG[d.code].action if d.code in CATALOG else "",
            d.provenance.bbox if d.provenance else None,
        )
        for d in diagnostics
    ]
    return sorted(rows, key=lambda r: (_SEVERITY_ORDER[r.severity], r.page or 0, r.code))


def page_anchors(document: Document, markdown: str) -> list[Anchor]:
    """Where each top-level block with a page starts in the Markdown, found by its first
    words, in order. Best effort: a block whose words are not found (a formula, an image
    without caption) is skipped."""
    line_starts = [0] + [m.end() for m in re.finditer("\n", markdown)]
    anchors: list[Anchor] = []
    position = 0
    for block in document.blocks:
        page = _page(block)
        words = _WORD.findall(_first_text(block))[:_ANCHOR_WORDS]
        if page is None or not words:
            continue
        pattern = re.compile(_BETWEEN_WORDS.join(re.escape(w) for w in words))
        match = pattern.search(markdown, position)
        if match is None:
            continue
        position = match.end()
        provenance = getattr(block, "provenance", None)
        bbox = provenance.bbox if provenance is not None else None
        anchors.append(Anchor(page, _line_of(line_starts, match.start()), bbox))
    return anchors


def _page(block: Block) -> int | None:
    provenance = getattr(block, "provenance", None)
    return provenance.page if provenance is not None else None


def _first_text(block: Block) -> str:
    match block:
        case Heading() | Paragraph():
            return plain_text(block.children)
        case ListBlock():
            return next((_first_text(b) for item in block.items for b in item.blocks), "")
        case BlockQuote() | FootnoteDef():
            return next((_first_text(b) for b in block.blocks), "")
        case Table():
            if block.caption:
                return plain_text(block.caption)
            cells = [c for row in block.header_rows + block.body_rows for c in row.cells]
            return next((_first_text(b) for c in cells for b in c.blocks), "")
        case Image():
            return plain_text(block.caption)
        case CodeBlock():
            return block.code
        case _:
            return ""


def _line_of(line_starts: list[int], offset: int) -> int:
    low, high = 0, len(line_starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if line_starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return low + 1


# --- Preview ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Run:
    """A piece of preview text with display tags; `image` names an asset to show."""

    text: str
    tags: tuple[str, ...] = ()
    image: str | None = None


def preview_runs(document: Document) -> list[Run]:
    """The document as styled runs. Block starts carry a `page-N` tag so the view can
    scroll to a page."""
    out: list[Run] = []
    if document.meta.title:  # the writer prints the title as the document's H1
        out.append(Run(document.meta.title + "\n\n", ("h1",)))
    for block in document.blocks:
        _block(block, out, depth=0)
    return out


def _mark(block: Block) -> tuple[str, ...]:
    page = _page(block)
    return (f"page-{page}",) if page is not None else ()


def _block(block: Block, out: list[Run], depth: int) -> None:
    mark = _mark(block)
    indent = (f"indent-{min(depth, 4)}",) if depth else ()
    match block:
        case Heading():
            _inlines(block.children, out, (f"h{block.level}", *mark))
            out.append(Run("\n\n"))
        case Paragraph():
            _inlines(block.children, out, (*indent, *mark))
            out.append(Run("\n\n" if not depth else "\n"))
        case ListBlock():
            for number, item in enumerate(block.items, start=block.start):
                bullet = f"{number}. " if block.ordered else "• "
                out.append(Run("    " * depth + bullet, (*indent, *_mark(block))))
                for k, child in enumerate(item.blocks):
                    if k and isinstance(child, Paragraph):
                        out.append(Run("    " * (depth + 1)))
                    _block(child, out, depth + 1)
            if not depth:
                out.append(Run("\n"))
        case BlockQuote():
            for child in block.blocks:
                _block(child, out, depth + 1)
        case CodeBlock():
            out.append(Run(block.code.rstrip("\n") + "\n", ("code", *mark)))
            out.append(Run("\n"))
        case MathBlock():
            out.append(Run(f"$$ {block.tex} $$\n\n", ("math", *mark)))
        case Table():
            if block.caption:
                _inlines(block.caption, out, ("caption", *mark))
                out.append(Run("\n"))
            out.append(Run(_table_text(block) + "\n", ("table", *mark)))
        case Image():
            out.append(Run("", mark, image=block.src_asset))
            out.append(Run("\n"))
            if block.caption:
                _inlines(block.caption, out, ("caption",))
                out.append(Run("\n"))
            out.append(Run("\n"))
        case FootnoteDef():
            out.append(Run(f"[{block.key}] ", ("footnote", "bold", *mark)))
            for child in block.blocks:
                if isinstance(child, Paragraph):
                    _inlines(child.children, out, ("footnote",))
                    out.append(Run("\n"))
                else:
                    _block(child, out, depth + 1)
            out.append(Run("\n"))
        case ThematicBreak():
            out.append(Run("―" * 24 + "\n\n", ("rule",)))
        case _:
            return


def _inlines(nodes: tuple[Inline, ...], out: list[Run], tags: tuple[str, ...]) -> None:
    for node in nodes:
        match node:
            case Text():
                out.append(Run(node.value, tags))
            case Emph():
                _inlines(node.children, out, (*tags, "italic"))
            case Strong():
                _inlines(node.children, out, (*tags, "bold"))
            case Code():
                out.append(Run(node.value, (*tags, "code-inline")))
            case Link():
                _inlines(node.children, out, (*tags, "link"))
            case InlineMath():
                out.append(Run(f"${node.tex}$", (*tags, "math")))
            case FootnoteRef():
                out.append(Run(f"[{node.key}]", (*tags, "sup")))
            case LineBreak():
                out.append(Run("\n", tags))
        tags = tuple(t for t in tags if not t.startswith("page-"))  # mark only the start


def _table_text(table: Table) -> str:
    """A table as aligned plain text: cell text only (spans are not drawn)."""
    rows = [
        [" ".join(_first_text(b) for b in cell.blocks) for cell in row.cells]
        for row in table.header_rows + table.body_rows
    ]
    if not rows:
        return ""
    columns = max(len(r) for r in rows)
    widths = [max((len(r[i]) for r in rows if i < len(r)), default=0) for i in range(columns)]
    lines = []
    for index, row in enumerate(rows):
        cells = [(row[i] if i < len(row) else "").ljust(widths[i]) for i in range(columns)]
        lines.append(" │ ".join(cells).rstrip())
        if index == len(table.header_rows) - 1:
            lines.append("─┼─".join("─" * w for w in widths))
    return "\n".join(lines)
