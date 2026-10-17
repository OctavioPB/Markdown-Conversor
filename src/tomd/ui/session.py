"""Review logic behind the desktop UI, free of widgets so it can be tested.

The UI shows what the pipeline produced; it never emits Markdown itself (the raw view is
the writer's file, the preview is drawn from the same IR). A review is built from the
batch manifest (findings, anchors, the Markdown file), so a document converted days ago
can be reviewed without converting it again; its IR, for the text preview, is read again
in the background (conversion is deterministic, so it is the same).
"""

from dataclasses import dataclass
from pathlib import Path

from tomd.ir import (
    Block,
    BlockQuote,
    Code,
    CodeBlock,
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
from tomd.pipeline.anchors import Anchor, block_page, first_text
from tomd.pipeline.manifest import Finding, LibraryEntry, Manifest
from tomd.ui.texts import failure_text, review_title

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Review:
    """One library document, as the review screen shows it."""

    entry: LibraryEntry
    markdown_path: Path
    markdown: str
    findings: list[Finding]
    anchors: list[Anchor]
    document: Document | None = None  # filled in when the background read finishes

    @classmethod
    def load(cls, manifest: Manifest, version: str, entry: LibraryEntry, out_dir: Path) -> "Review":
        path = out_dir / (entry.output or "")
        markdown = path.read_text(encoding="utf-8") if entry.output and path.is_file() else ""
        anchors = [
            Anchor(p, line, bbox) for p, line, bbox in manifest.anchors(entry.sha256, version)
        ]
        findings = manifest.findings(entry.sha256, version)
        return cls(entry, path, markdown, findings, anchors)

    @property
    def title(self) -> str:
        return self.entry.title or Path(self.entry.source).stem

    @property
    def is_pdf(self) -> bool:
        return self.entry.source.lower().endswith(".pdf") and Path(self.entry.source).is_file()

    @property
    def to_review(self) -> list[Finding]:
        """Warnings and errors, most severe first, then by page: the checklist."""
        important = [f for f in self.findings if f.severity != "info"]
        return sorted(important, key=lambda f: (_SEVERITY_ORDER[f.severity], f.page or 0, f.idx))

    @property
    def automatic(self) -> list[Finding]:
        """Lossless changes made on purpose (info), listed apart."""
        return [f for f in self.findings if f.severity == "info"]

    def checked_count(self) -> int:
        return sum(f.checked for f in self.to_review)

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


def what_to_review(entry: LibraryEntry) -> str:
    """The library's "Qué revisar" column, in plain words."""
    match entry.state:
        case "to_review":
            title = review_title(entry.first_code or "", entry.first_page)
            more = entry.to_check - 1
            return title + (f" · {more} más" if more > 0 else "")
        case "failed":
            return failure_text(entry.error)
        case "reviewed":
            return "Revisado" + (f" el {_date(entry.reviewed_at)}" if entry.reviewed_at else "")
        case "converting":
            return "En cola…"
        case _:
            return "—"


def short_date(value: str | None) -> str:
    return _date(value) if value else ""


def _date(value: str) -> str:
    months = ("ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sept.", "oct.",
              "nov.", "dic.")  # fmt: skip
    year, month, day = value[:10].split("-")
    return f"{int(day)} {months[int(month) - 1]} {year}"


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
    page = block_page(block)
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
        [" ".join(first_text(b) for b in cell.blocks) for cell in row.cells]
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
