"""IR nodes: an immutable tree describing document structure, not presentation.

The IR is deliberately more expressive than Markdown (cell spans, blocks inside cells), so
that the decision of how to degrade is taken in one place: the Markdown writer (ADR-006).
"""

import hashlib
from dataclasses import dataclass, field
from typing import Literal

SourceType = Literal["epub", "wikipedia", "pdf"]


@dataclass(frozen=True, slots=True)
class Provenance:
    source_ref: str  # file inside the EPUB, URL, PDF path
    page: int | None = None  # PDF only
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = 1.0  # < 1.0 for inferred structure (PDF, OCR)


# --- Inline nodes ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Emph:
    children: tuple["Inline", ...]


@dataclass(frozen=True, slots=True)
class Strong:
    children: tuple["Inline", ...]


@dataclass(frozen=True, slots=True)
class Code:
    value: str


@dataclass(frozen=True, slots=True)
class Link:
    children: tuple["Inline", ...]
    href: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class InlineMath:
    tex: str


@dataclass(frozen=True, slots=True)
class FootnoteRef:
    key: str


@dataclass(frozen=True, slots=True)
class LineBreak:
    pass


type Inline = Text | Emph | Strong | Code | Link | InlineMath | FootnoteRef | LineBreak


# --- Block nodes -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Heading:
    level: int  # 1..6
    children: tuple[Inline, ...]
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 6:
            raise ValueError(f"heading level must be in 1..6, got {self.level}")


@dataclass(frozen=True, slots=True)
class Paragraph:
    children: tuple[Inline, ...]
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class ListItem:
    blocks: tuple["Block", ...]
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class ListBlock:
    items: tuple[ListItem, ...]
    ordered: bool = False
    start: int = 1
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class BlockQuote:
    blocks: tuple["Block", ...]
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class CodeBlock:
    code: str
    language: str | None = None
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class MathBlock:
    tex: str
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    blocks: tuple["Block", ...]
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True, slots=True)
class Row:
    cells: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class Table:
    header_rows: tuple[Row, ...]
    body_rows: tuple[Row, ...]
    caption: tuple[Inline, ...] = ()
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class Image:
    src_asset: str  # sha256 of an Asset in Document.assets
    alt: str
    caption: tuple[Inline, ...] = ()
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class FootnoteDef:
    """Footnote body. Only valid at the top level of Document.blocks."""

    key: str
    blocks: tuple["Block", ...]
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class ThematicBreak:
    provenance: Provenance | None = None


type Block = (
    Heading
    | Paragraph
    | ListBlock
    | BlockQuote
    | CodeBlock
    | MathBlock
    | Table
    | Image
    | FootnoteDef
    | ThematicBreak
)


# --- Document ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Asset:
    sha256: str
    media_type: str
    data: bytes = field(repr=False)

    @classmethod
    def from_bytes(cls, data: bytes, media_type: str) -> "Asset":
        return cls(sha256=hashlib.sha256(data).hexdigest(), media_type=media_type, data=data)


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    """Front matter fields, in ADR-010 order."""

    title: str | None
    source_type: SourceType
    source: str
    source_sha256: str | None = None
    converter_version: str | None = None
    backend: str | None = None
    language: str | None = None
    authors: tuple[str, ...] = ()
    license: str | None = None
    revision_id: int | None = None  # Wikipedia only
    # A chapter of a book split by chapters (ADR-018); written right after `title`.
    book: str | None = None  # the book's title
    chapter: int | None = None  # 1-based; None for the index and the front part
    chapters: int | None = None  # how many chapters the book has
