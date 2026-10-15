"""Small helpers to build IR documents in tests."""

from tomd.ir import Asset, Block, Document, DocumentMeta, Inline, Paragraph, Text
from tomd.transforms import apply_default_transforms
from tomd.writers.markdown import RenderResult, render_markdown


def meta(title: str | None = "Title", **fields: object) -> DocumentMeta:
    return DocumentMeta(title=title, source_type="epub", source="book.epub", **fields)  # type: ignore[arg-type]


def doc(*blocks: Block, title: str | None = "Title", assets: tuple[Asset, ...] = ()) -> Document:
    return Document(meta=meta(title), blocks=blocks, assets=assets)


def p(*children: Inline | str) -> Paragraph:
    return Paragraph(tuple(Text(c) if isinstance(c, str) else c for c in children))


def convert(document: Document, *, slug: str = "book") -> RenderResult:
    return render_markdown(apply_default_transforms(document), slug=slug)


def body(markdown: str) -> str:
    """Strip front matter and the title H1 from writer output."""
    _, _, rest = markdown.partition("\n---\n\n")
    if rest.startswith("# "):
        rest = rest.split("\n\n", 1)[1] if "\n\n" in rest else ""
    return rest
