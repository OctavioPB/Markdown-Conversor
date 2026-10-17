"""Guarantee a single H1 (the document title) and no skipped heading levels (ADR-011)."""

from dataclasses import replace

from tomd.ir import Block, Diagnostic, Document, Heading, Inline
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.ir.walk import map_block_children, plain_text

MAX_LEVEL = 6
# The writer emits the title as H1, so body headings start at H2.
FIRST_BODY_LEVEL = 2


def normalize_headings(doc: Document) -> Document:
    diagnostics = list(doc.diagnostics)
    blocks = doc.blocks
    title = _collapse(doc.meta.title or "")

    first_h1 = next((b for b in blocks if isinstance(b, Heading) and b.level == 1), None)
    if title:
        # An H1 that repeats the title is merged into it.
        same = next(
            (
                b
                for b in blocks
                if isinstance(b, Heading)
                and b.level == 1
                and _key(plain_text(b.children)) == _key(title)
            ),
            None,
        )
        if same is not None:
            blocks = tuple(b for b in blocks if b is not same)
    elif first_h1 is not None:
        title = _collapse(plain_text(first_h1.children))
        blocks = tuple(b for b in blocks if b is not first_h1)
    else:
        diagnostics.append(diagnose(codes.DOC_TITLE_MISSING, "Document has no title and no H1."))

    shift = 1 if any(h.level == 1 for h in _all_headings(blocks)) else 0
    blocks = _Releveler(shift, diagnostics).blocks(blocks)

    meta = replace(doc.meta, title=title or None)
    return replace(doc, meta=meta, blocks=blocks, diagnostics=tuple(diagnostics))


class _Releveler:
    """Stack-based compression of levels: H2, H4, H4, H3 -> H2, H3, H3, H3."""

    def __init__(self, shift: int, diagnostics: list[Diagnostic]) -> None:
        self._shift = shift
        self._stack: list[tuple[int, int]] = []  # (shifted source level, output level)
        self._diagnostics = diagnostics

    def blocks(self, blocks: tuple[Block, ...]) -> tuple[Block, ...]:
        return tuple(self._block(b) for b in blocks)

    def _block(self, block: Block) -> Block:
        if not isinstance(block, Heading):
            return map_block_children(block, self.blocks, _identity)
        level = block.level + self._shift
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        new = self._stack[-1][1] + 1 if self._stack else FIRST_BODY_LEVEL
        if new > MAX_LEVEL:
            self._diagnostics.append(
                diagnose(
                    codes.HEADING_LEVEL_CLAMPED,
                    f"Heading nested deeper than H{MAX_LEVEL}; clamped to H{MAX_LEVEL}.",
                    block.provenance,
                )
            )
            new = MAX_LEVEL
        self._stack.append((level, new))
        return replace(block, level=new)


def _identity(nodes: tuple[Inline, ...]) -> tuple[Inline, ...]:
    return nodes


def _all_headings(blocks: tuple[Block, ...]) -> list[Heading]:
    found: list[Heading] = []

    def visit(bs: tuple[Block, ...]) -> tuple[Block, ...]:
        for b in bs:
            if isinstance(b, Heading):
                found.append(b)
            else:
                map_block_children(b, visit, _identity)
        return bs

    visit(blocks)
    return found


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _key(text: str) -> str:
    return _collapse(text).casefold()
