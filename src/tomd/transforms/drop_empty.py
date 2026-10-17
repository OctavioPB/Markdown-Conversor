"""Remove empty paragraphs, headings, containers and edge line breaks."""

from dataclasses import replace

from tomd.ir import (
    Block,
    BlockQuote,
    Code,
    CodeBlock,
    Document,
    Emph,
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
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.ir.walk import map_block_children, map_inline_children


def drop_empty(doc: Document) -> Document:
    diagnostics = list(doc.diagnostics)

    def blocks(bs: tuple[Block, ...]) -> tuple[Block, ...]:
        out: list[Block] = []
        for block in bs:
            block = map_block_children(block, blocks, _block_inlines)
            if isinstance(block, ListBlock):
                block = replace(block, items=tuple(it for it in block.items if it.blocks))
            if isinstance(block, Table | Image) and not _has_content(block.caption):
                block = replace(block, caption=())
            if isinstance(block, Table) and not block.header_rows and not block.body_rows:
                diagnostics.append(
                    diagnose(codes.TABLE_EMPTY_DROPPED, "Empty table dropped.", block.provenance)
                )
                if not _has_content(block.caption):
                    continue
                block = Paragraph((Emph(block.caption),), block.provenance)  # M19 caption
            if not _is_empty(block):
                out.append(block)
        return tuple(out)

    new_blocks = blocks(doc.blocks)
    return replace(doc, blocks=new_blocks, diagnostics=tuple(diagnostics))


def _is_empty(block: Block) -> bool:
    match block:
        case Heading() | Paragraph():
            return not _has_content(block.children)
        case ListBlock():
            return not block.items
        case BlockQuote():
            return not block.blocks
        case CodeBlock():
            return not block.code.strip()
        case MathBlock():
            return not block.tex.strip()
        case _:
            return False


def _has_content(nodes: tuple[Inline, ...]) -> bool:
    """Whether the inlines render to something visible once whitespace is collapsed."""
    for node in nodes:
        match node:
            case Text(value) if value.strip():
                return True
            case Emph(children) | Strong(children) if _has_content(children):
                return True
            case Code() | InlineMath() | FootnoteRef() | Link():
                return True
    return False


def _block_inlines(nodes: tuple[Inline, ...]) -> tuple[Inline, ...]:
    out = list(_inlines(nodes))
    while out and isinstance(out[0], LineBreak):
        out.pop(0)
    while out and isinstance(out[-1], LineBreak):
        out.pop()
    return tuple(out)


def _inlines(nodes: tuple[Inline, ...]) -> tuple[Inline, ...]:
    out: list[Inline] = []
    for node in nodes:
        node = map_inline_children(node, _inlines)
        match node:
            case Text(value) | Code(value) if value == "":
                continue
            case InlineMath(tex) if not tex.strip():
                continue
            case Emph(children) | Strong(children) if not children:
                continue
            case Link(children, href) if not children and not href:
                continue
        out.append(node)
    return tuple(out)
