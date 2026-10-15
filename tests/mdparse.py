"""Re-parse writer output with markdown-it-py and compare block structure against the IR."""

from functools import cache
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

from tomd.ir import (
    Block,
    BlockQuote,
    CodeBlock,
    Document,
    FootnoteDef,
    Heading,
    Image,
    ListBlock,
    MathBlock,
    Paragraph,
    Table,
    ThematicBreak,
)
from tomd.writers.markdown import is_gfm_table

type Sig = tuple[Any, ...]


@cache
def parser() -> MarkdownIt:
    return (
        MarkdownIt("commonmark")
        .enable("table")
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(dollarmath_plugin, allow_space=False)
    )


def parse(markdown: str) -> list[Token]:
    return parser().parse(markdown)


def inline_texts(markdown: str) -> list[str]:
    """Rendered text of every inline token (paragraphs, headings, cells), in order."""
    out: list[str] = []
    for token in parse(markdown):
        if token.type == "inline":
            out.append(_inline_text(token.children or []))
    return out


def _inline_text(children: list[Token]) -> str:
    parts: list[str] = []
    for child in children:
        match child.type:
            case "text" | "code_inline":
                parts.append(child.content)
            case "softbreak" | "hardbreak":
                parts.append("\n")
            case "math_inline":
                parts.append(f"${child.content}$")
            case "html_inline":
                parts.append(f"<html:{child.content}>")
            case "footnote_ref":
                parts.append(f"[^{child.meta['label']}]")
            case "image":
                parts.append(f"<img:{child.content}>")
            case _:
                parts.append("")
    return "".join(parts)


# --- Block signatures ------------------------------------------------------------------

_SKIPPED_HTML = {"<!-- -->"}


def token_tree(tokens: list[Token]) -> list[Sig]:
    pos = 0

    def level(closing: str | None) -> list[Sig]:
        nonlocal pos
        out: list[Sig] = []
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if closing is not None and tok.type == closing:
                return out
            match tok.type:
                case "front_matter" | "inline" | "footnote_anchor":
                    pass
                case "html_block":
                    if tok.content.strip() not in _SKIPPED_HTML:
                        out.append(("html",))
                case "paragraph_open":
                    level("paragraph_close")
                    out.append(("p",))
                case "heading_open":
                    level("heading_close")
                    out.append(("h", int(tok.tag[1])))
                case "bullet_list_open":
                    out.append(("ul", tuple(level("bullet_list_close"))))
                case "ordered_list_open":
                    start = int(tok.attrs.get("start", 1))
                    out.append(("ol", start, tuple(level("ordered_list_close"))))
                case "list_item_open":
                    out.append(("li", tuple(level("list_item_close"))))
                case "blockquote_open":
                    out.append(("quote", tuple(level("blockquote_close"))))
                case "fence":
                    out.append(("code", tok.info.strip()))
                case "math_block":
                    out.append(("math",))
                case "hr":
                    out.append(("hr",))
                case "table_open":
                    rows, cols = _table_shape(tokens, pos)
                    while tokens[pos].type != "table_close":
                        pos += 1
                    pos += 1
                    out.append(("table", rows, cols))
                case "footnote_block_open":
                    out.append(("footnotes", tuple(level("footnote_block_close"))))
                case "footnote_open":
                    out.append(("fn", tuple(level("footnote_close"))))
                case other:
                    raise AssertionError(f"unexpected token {other}")
        return out

    return level(None)


def _table_shape(tokens: list[Token], pos: int) -> tuple[int, int]:
    rows = cols = 0
    for tok in tokens[pos:]:
        if tok.type == "table_close":
            break
        if tok.type == "tr_open":
            rows += 1
        if tok.type in ("th_open", "td_open") and rows == 1:
            cols += 1
    return rows, cols


def ir_tree(doc: Document) -> list[Sig]:
    """Expected signature of a *transformed* document."""
    out: list[Sig] = [("h", 1)] if doc.meta.title else []
    out += _blocks(tuple(b for b in doc.blocks if not isinstance(b, FootnoteDef)))
    notes = [b for b in doc.blocks if isinstance(b, FootnoteDef)]
    if notes:
        out.append(("footnotes", tuple(("fn", tuple(_blocks(n.blocks))) for n in notes)))
    return out


def _blocks(blocks: tuple[Block, ...]) -> list[Sig]:
    out: list[Sig] = []
    for block in blocks:
        match block:
            case Heading(level):
                out.append(("h", level))
            case Paragraph():
                out.append(("p",))
            case ListBlock(items, ordered, start):
                lis = tuple(("li", tuple(_blocks(it.blocks))) for it in items)
                out.append(("ol", start, lis) if ordered else ("ul", lis))
            case BlockQuote(blocks):
                out.append(("quote", tuple(_blocks(blocks))))
            case CodeBlock(_, language):
                out.append(("code", (language or "").split()[0] if language else ""))
            case MathBlock():
                out.append(("math",))
            case ThematicBreak():
                out.append(("hr",))
            case Image(_, _, caption):
                out.append(("p",))
                if caption:
                    out.append(("p",))
            case Table(header, body, caption):
                if caption:
                    out.append(("p",))
                if not header and not body:
                    pass  # dropped with table.empty_dropped
                elif is_gfm_table(block):
                    rows = header + body
                    out.append(("table", len(rows), max(len(r.cells) for r in rows)))
                else:
                    out.append(("html",))
            case FootnoteDef():
                raise AssertionError("FootnoteDef must be top-level")
    return out
