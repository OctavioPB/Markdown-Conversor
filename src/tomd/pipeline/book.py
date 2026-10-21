"""A book split by chapters on disk (ADR-018): one folder named after the book with
`index.md`, one `NN-<chapter>.md` per chapter and the shared `assets/`.

    <book-slug>/
      index.md
      00-preliminares.md      (only when the text before chapter 1 is long enough)
      01-<chapter-slug>.md
      ...
      assets/<hash>.<ext>
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from tomd.ir import Asset, Diagnostic, Document, Link, ListBlock, ListItem, Paragraph, Text
from tomd.transforms.split import SplitBook
from tomd.writers.markdown import asset_filename, render_markdown

INDEX_NAME = "index.md"
ASSETS_DIR = "assets"
# Chapter files this module writes; a reconversion removes the stale ones (and only them).
_CHAPTER_FILE = re.compile(r"\d{2,3}-[a-z0-9-]*\.md\Z")


@dataclass(frozen=True, slots=True)
class RenderedBook:
    files: tuple[tuple[str, str], ...]  # (name inside the folder, Markdown), index first
    assets: tuple[Asset, ...]
    diagnostics: tuple[Diagnostic, ...]  # every part's, plus those emitted while writing


def chapter_names(book: SplitBook) -> list[str]:
    """`NN-<slug>.md` per chapter, `00-` for the part before chapter 1; the number keeps
    the reading order in any file browser and makes names unique."""
    from tomd.pipeline.dispatch import slugify  # dispatch imports this module

    width = max(2, len(str(len(book.chapters))))
    return [
        f"{(c.number or 0):0{width}d}-{slugify(c.document.meta.title or 'capitulo')}.md"
        for c in book.chapters
    ]


def index_document(book: SplitBook, names: list[str]) -> Document:
    """The book's own page: its front matter, any short front text, and the chapters as
    links in reading order."""
    items = tuple(
        ListItem((Paragraph((Link((Text(c.document.meta.title or name),), name),)),))
        for c, name in zip(book.chapters, names, strict=True)
    )
    return replace(book.book, blocks=(*book.book.blocks, ListBlock(items)))


def render_book(book: SplitBook) -> RenderedBook:
    names = chapter_names(book)
    files: list[tuple[str, str]] = []
    diagnostics: list[Diagnostic] = []
    index = render_markdown(index_document(book, names), slug="index", assets_dir=ASSETS_DIR)
    files.append((INDEX_NAME, index.text))
    diagnostics.extend(index.diagnostics)
    assets: dict[str, Asset] = {}
    for chapter, name in zip(book.chapters, names, strict=True):
        result = render_markdown(chapter.document, slug=name[:-3], assets_dir=ASSETS_DIR)
        files.append((name, result.text))
        diagnostics.extend(result.diagnostics)
        for asset in chapter.document.assets:
            assets.setdefault(asset.sha256, asset)
    return RenderedBook(tuple(files), tuple(assets.values()), tuple(diagnostics))


def write_book(out_dir: Path, folder: str, rendered: RenderedBook) -> Path:
    """Write the book's folder atomically file by file; returns the path of `index.md`.
    Chapter files of an earlier conversion that this one no longer has are removed."""
    from tomd.pipeline.dispatch import atomic_write  # dispatch imports this module

    target = out_dir / folder
    target.mkdir(parents=True, exist_ok=True)
    if rendered.assets:
        (target / ASSETS_DIR).mkdir(exist_ok=True)
        for asset in rendered.assets:
            atomic_write(target / ASSETS_DIR / asset_filename(asset), asset.data)
    names = {name for name, _ in rendered.files}
    for name, text in rendered.files:
        atomic_write(target / name, text.encode("utf-8"))
    for old in sorted(target.iterdir()):
        if old.is_file() and _CHAPTER_FILE.match(old.name) and old.name not in names:
            old.unlink()
    return target / INDEX_NAME
