"""Renumber footnotes 1..n by first reference and move definitions to the end."""

from dataclasses import replace

from tomd.ir import Block, Diagnostic, Document, FootnoteDef, FootnoteRef, Inline
from tomd.ir import diagnostics as codes
from tomd.ir.walk import iter_inlines, map_block_children, map_inline_children


def renumber_footnotes(doc: Document) -> Document:
    diagnostics = list(doc.diagnostics)
    defs: dict[str, FootnoteDef] = {}
    body: list[Block] = []
    for block in doc.blocks:
        if not isinstance(block, FootnoteDef):
            body.append(block)
        elif block.key in defs:
            diagnostics.append(
                Diagnostic(
                    codes.FOOTNOTE_DUPLICATE_DEFINITION,
                    "warning",
                    f"Footnote {block.key!r} defined more than once; keeping the first.",
                    block.provenance,
                )
            )
        else:
            defs[block.key] = block

    numbers: dict[str, int] = {}
    order: list[str] = []
    missing: list[str] = []

    def visit(blocks: tuple[Block, ...]) -> None:
        for node in iter_inlines(blocks):
            if not isinstance(node, FootnoteRef) or node.key in numbers:
                continue
            if node.key in defs:
                order.append(node.key)
                numbers[node.key] = len(order)
            elif node.key not in missing:
                missing.append(node.key)

    visit(tuple(body))
    i = 0
    while i < len(order):  # references inside definitions are numbered after the body
        visit(defs[order[i]].blocks)
        i += 1

    for key in missing:
        diagnostics.append(
            Diagnostic(
                codes.FOOTNOTE_MISSING_DEFINITION,
                "warning",
                f"Footnote reference {key!r} has no definition; reference dropped.",
            )
        )
    for key, fdef in defs.items():
        if key not in numbers:
            diagnostics.append(
                Diagnostic(
                    codes.FOOTNOTE_ORPHAN_DEFINITION,
                    "warning",
                    f"Footnote {key!r} is never referenced; definition dropped.",
                    fdef.provenance,
                )
            )

    def inlines(nodes: tuple[Inline, ...]) -> tuple[Inline, ...]:
        out: list[Inline] = []
        for node in nodes:
            if isinstance(node, FootnoteRef):
                if node.key in numbers:
                    out.append(FootnoteRef(str(numbers[node.key])))
                continue
            out.append(map_inline_children(node, inlines))
        return tuple(out)

    def blocks(bs: tuple[Block, ...]) -> tuple[Block, ...]:
        return tuple(map_block_children(b, blocks, inlines) for b in bs)

    new_defs = tuple(
        replace(defs[key], key=str(numbers[key]), blocks=blocks(defs[key].blocks)) for key in order
    )
    return replace(doc, blocks=blocks(tuple(body)) + new_defs, diagnostics=tuple(diagnostics))
