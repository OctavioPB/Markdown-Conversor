"""Root of the IR tree."""

from dataclasses import dataclass

from tomd.ir.diagnostics import Diagnostic
from tomd.ir.nodes import Asset, Block, DocumentMeta


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    """One entry of the source's own table of contents (PDF bookmarks, EPUB nav): where
    the publisher says a chapter or section starts (ADR-018). Source information, not
    presentation: the writer never prints it; it guides splitting a book."""

    title: str
    level: int  # 1 = top level
    page: int | None = None  # PDF: 1-based page the entry points to, when it has one
    target: str | None = None  # EPUB: "path/in/zip.xhtml" or "path#fragment"


@dataclass(frozen=True, slots=True)
class Document:
    meta: DocumentMeta
    blocks: tuple[Block, ...]
    assets: tuple[Asset, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    outline: tuple[OutlineEntry, ...] = ()  # in source order
