"""Where each page of the source starts in the Markdown.

The batch stores these anchors in the manifest, so the desktop UI can keep the original
and the Markdown in step (and highlight a block) for documents converted long before,
without converting them again.
"""

import re
from dataclasses import dataclass

from tomd.ir import (
    Block,
    BlockQuote,
    CodeBlock,
    Document,
    FootnoteDef,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Table,
)
from tomd.ir.walk import plain_text

# Letters and digits only: "_" and "*" are Markdown syntax, escaped or not in the output.
_WORD = re.compile(r"[^\W_]+")
_BETWEEN_WORDS = r"[\W_]+"
# Words of a block used to find it in the Markdown: enough to be unique in practice,
# few enough to survive escaping and emphasis markers between them.
_ANCHOR_WORDS = 4


@dataclass(frozen=True, slots=True)
class Anchor:
    """Where a top-level block with a known page starts in the Markdown."""

    page: int
    line: int  # 1-based
    bbox: tuple[float, float, float, float] | None  # PDF points, y up


def page_anchors(document: Document, markdown: str) -> list[Anchor]:
    """Where each top-level block with a page starts in the Markdown, found by its first
    words, in order. Best effort: a block whose words are not found (a formula, an image
    without caption) is skipped."""
    line_starts = [0] + [m.end() for m in re.finditer("\n", markdown)]
    anchors: list[Anchor] = []
    position = 0
    for block in document.blocks:
        page = block_page(block)
        words = _WORD.findall(first_text(block))[:_ANCHOR_WORDS]
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


def block_page(block: Block) -> int | None:
    provenance = getattr(block, "provenance", None)
    return provenance.page if provenance is not None else None


def first_text(block: Block) -> str:
    """The text a block starts with (for finding it, or for a table's plain rendering)."""
    match block:
        case Heading() | Paragraph():
            return plain_text(block.children)
        case ListBlock():
            return next((first_text(b) for item in block.items for b in item.blocks), "")
        case BlockQuote() | FootnoteDef():
            return next((first_text(b) for b in block.blocks), "")
        case Table():
            if block.caption:
                return plain_text(block.caption)
            cells = [c for row in block.header_rows + block.body_rows for c in row.cells]
            return next((first_text(b) for c in cells for b in c.blocks), "")
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
