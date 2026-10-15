"""Fuse adjacent Text nodes."""

from dataclasses import replace

from tomd.ir import Block, Document, Inline, Text
from tomd.ir.walk import map_block_children, map_inline_children


def merge_adjacent_text(doc: Document) -> Document:
    return replace(doc, blocks=_blocks(doc.blocks))


def _blocks(blocks: tuple[Block, ...]) -> tuple[Block, ...]:
    return tuple(map_block_children(b, _blocks, _inlines) for b in blocks)


def _inlines(nodes: tuple[Inline, ...]) -> tuple[Inline, ...]:
    out: list[Inline] = []
    for node in nodes:
        node = map_inline_children(node, _inlines)
        if isinstance(node, Text) and out and isinstance(out[-1], Text):
            out[-1] = Text(out[-1].value + node.value)
        else:
            out.append(node)
    return tuple(out)
