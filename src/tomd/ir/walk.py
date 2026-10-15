"""Generic helpers to rebuild and traverse the IR tree."""

from collections.abc import Callable, Iterator
from dataclasses import replace

from tomd.ir.nodes import (
    Block,
    BlockQuote,
    Code,
    Emph,
    FootnoteDef,
    Heading,
    Image,
    Inline,
    InlineMath,
    LineBreak,
    Link,
    ListBlock,
    Paragraph,
    Row,
    Strong,
    Table,
    Text,
)

type BlocksFn = Callable[[tuple[Block, ...]], tuple[Block, ...]]
type InlinesFn = Callable[[tuple[Inline, ...]], tuple[Inline, ...]]


def map_block_children(block: Block, on_blocks: BlocksFn, on_inlines: InlinesFn) -> Block:
    """Return `block` with its direct child block and inline sequences transformed."""
    match block:
        case Heading() | Paragraph():
            return replace(block, children=on_inlines(block.children))
        case ListBlock():
            items = tuple(replace(it, blocks=on_blocks(it.blocks)) for it in block.items)
            return replace(block, items=items)
        case BlockQuote() | FootnoteDef():
            return replace(block, blocks=on_blocks(block.blocks))
        case Table():

            def rows(rs: tuple[Row, ...]) -> tuple[Row, ...]:
                return tuple(
                    Row(tuple(replace(c, blocks=on_blocks(c.blocks)) for c in r.cells)) for r in rs
                )

            return replace(
                block,
                header_rows=rows(block.header_rows),
                body_rows=rows(block.body_rows),
                caption=on_inlines(block.caption),
            )
        case Image():
            return replace(block, caption=on_inlines(block.caption))
        case _:
            return block


def map_inline_children(node: Inline, on_inlines: InlinesFn) -> Inline:
    """Return `node` with its direct inline children transformed."""
    match node:
        case Emph() | Strong() | Link():
            return replace(node, children=on_inlines(node.children))
        case _:
            return node


def iter_inlines(blocks: tuple[Block, ...]) -> Iterator[Inline]:
    """Yield every inline node in document order (depth-first, pre-order)."""
    for block in blocks:
        yield from _block_inlines(block)


def _block_inlines(block: Block) -> Iterator[Inline]:
    match block:
        case Heading() | Paragraph():
            yield from _walk_inlines(block.children)
        case ListBlock():
            for item in block.items:
                yield from iter_inlines(item.blocks)
        case BlockQuote() | FootnoteDef():
            yield from iter_inlines(block.blocks)
        case Table():
            yield from _walk_inlines(block.caption)
            for row in block.header_rows + block.body_rows:
                for cell in row.cells:
                    yield from iter_inlines(cell.blocks)
        case Image():
            yield from _walk_inlines(block.caption)
        case _:
            return


def _walk_inlines(nodes: tuple[Inline, ...]) -> Iterator[Inline]:
    for node in nodes:
        yield node
        if isinstance(node, Emph | Strong | Link):
            yield from _walk_inlines(node.children)


def plain_text(nodes: tuple[Inline, ...]) -> str:
    """Concatenate the textual content of inline nodes (markup dropped)."""
    parts: list[str] = []
    for node in _walk_inlines(nodes):
        match node:
            case Text(value) | Code(value):
                parts.append(value)
            case InlineMath(tex):
                parts.append(tex)
            case LineBreak():
                parts.append(" ")
    return "".join(parts)
