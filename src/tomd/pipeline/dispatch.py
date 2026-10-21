"""Choose a reader for a source and run the full conversion (reader -> transforms -> writer)."""

import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from tomd import __version__
from tomd.errors import UnsupportedSourceError
from tomd.ir import Asset, Diagnostic, Document
from tomd.readers.epub.reader import EpubReader
from tomd.readers.pdf.ocr import OcrMode
from tomd.readers.pdf.reader import DEFAULT_BACKEND, PdfReader
from tomd.readers.wikipedia.client import parse_article_ref
from tomd.readers.wikipedia.reader import WikipediaReader, read_parsoid_html
from tomd.transforms import apply_default_transforms
from tomd.writers.markdown import RenderResult, asset_filename, render_markdown

# Bytes that identify a Parsoid HTML document (a recorded Wikipedia article).
_PARSOID_MARKERS = (b"parsoid-body", b'property="mw:htmlVersion"')
_SLUG_MAX = 80
# tomd's front matter keys come within its first lines (title, book, chapter... then
# source_sha256 and converter_version, ADR-010).
_FRONT_MATTER_LINES = 12


@dataclass(frozen=True, slots=True)
class ConversionResult:
    markdown_path: Path
    diagnostics: tuple[Diagnostic, ...]
    document: Document | None = None  # the transformed IR the Markdown was written from


def read_source(
    source: str,
    *,
    revision: int | None = None,
    pdf_backend: str = DEFAULT_BACKEND,
    ocr: OcrMode = OcrMode.AUTO,
) -> Document:
    path = Path(source)
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            data = path.read_bytes()
            if any(marker in data for marker in _PARSOID_MARKERS):
                return read_parsoid_html(data)
            raise UnsupportedSourceError(
                f"{source}: generic HTML is out of scope; only Parsoid HTML from Wikipedia "
                "(as saved by scripts/record_wiki_fixture.py) is supported."
            )
        if suffix == ".pdf":
            return PdfReader(backend=pdf_backend, ocr=ocr).read(source)
        if suffix == ".epub":
            return EpubReader().read(source)
        raise UnsupportedSourceError(
            f"{source}: unsupported file type {suffix or '(none)'!r}; tomd converts .pdf and "
            ".epub files, Wikipedia Parsoid .html files and Wikipedia articles."
        )
    try:
        parse_article_ref(source)
    except UnsupportedSourceError:
        raise UnsupportedSourceError(
            f"{source}: no such file, and not a Wikipedia article (give a URL such as "
            "https://es.wikipedia.org/wiki/Teorema_de_Pitágoras or lang:Title)."
        ) from None
    return WikipediaReader(revision=revision).read(source)


def prepare_document(doc: Document) -> tuple[str, Document]:
    """Stamp the converter version and apply the transforms. Returns the slug the title
    suggests and the document ready to render."""
    doc = replace(doc, meta=replace(doc.meta, converter_version=__version__))
    doc = apply_default_transforms(doc)
    return slugify(doc.meta.title or Path(doc.meta.source).stem or "document"), doc


def render_document(doc: Document) -> tuple[str, RenderResult, Document]:
    """Apply transforms and render. Returns (slug, result, transformed document)."""
    slug, doc = prepare_document(doc)
    return slug, render_markdown(doc, slug=slug), doc


def write_output(out_dir: Path, slug: str, text: str, assets: tuple[Asset, ...]) -> Path:
    """`<slug>.md` and `<slug>.assets/`, each file written atomically."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if assets:
        assets_dir = out_dir / f"{slug}.assets"
        assets_dir.mkdir(exist_ok=True)
        for asset in assets:
            atomic_write(assets_dir / asset_filename(asset), asset.data)
    target = out_dir / f"{slug}.md"
    atomic_write(target, text.encode("utf-8"))
    return target


def remove_output(out_dir: Path, output: str | None, source: str) -> list[Path]:
    """Delete what a conversion wrote: `<slug>.md`, `<slug>.assets/` and, for a Wikipedia
    article, the HTML downloaded into the output folder. Only paths inside `out_dir` are
    touched (never the user's original). Returns what was deleted."""
    import shutil

    from tomd.pipeline.book import INDEX_NAME
    from tomd.pipeline.fetch import CACHE_DIR

    root = out_dir.resolve()
    candidates: list[Path] = []
    folder = Path(output).parent if output else Path()
    if output and folder != Path():
        # A book split by chapters (ADR-018): its whole folder, but only a folder of the
        # output directory that holds the book's index (never a folder of the user's).
        book = (out_dir / folder.parts[0]).resolve()
        if book.parent == root and (book / INDEX_NAME).is_file():
            candidates.append(book)
    elif output:
        markdown = out_dir / Path(output).name
        candidates += [markdown, markdown.with_suffix(".assets")]
    if source:
        downloaded = Path(source)
        if downloaded.resolve().parent == root / CACHE_DIR:
            candidates.append(downloaded)
    removed: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved.parent not in (root, root / CACHE_DIR) or not resolved.exists():
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        removed.append(resolved)
    return removed


def reset_output(out_dir: Path, *, delete_files: bool) -> int:
    """Start the output folder from scratch: forget everything tomd remembers about it
    (the manifest: documents, reviews, claimed file names, run history, cached hashes)
    and, with `delete_files`, delete what it wrote there (each document's files, the
    downloaded articles, the batch report). Returns how many documents' files could not
    be deleted (open in another program)."""
    from tomd.pipeline.batch import MANIFEST_NAME, REPORT_NAME
    from tomd.pipeline.manifest import Manifest

    manifest_path = out_dir / MANIFEST_NAME
    failed = 0
    if delete_files and manifest_path.is_file():
        manifest = Manifest(manifest_path)
        try:
            outputs = manifest.outputs()
        finally:
            manifest.close()
        for output, source in outputs:
            try:
                remove_output(out_dir, output, source)
            except OSError:
                failed += 1
    if delete_files:
        # Also what tomd wrote before an earlier fresh start, which no manifest lists any
        # more: recognised by tomd's own front matter, never by name.
        for output in _written_by_tomd(out_dir):
            try:
                remove_output(out_dir, output, "")
            except OSError:
                failed += 1
        _clear_article_cache(out_dir)
        (out_dir / REPORT_NAME).unlink(missing_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        (out_dir / f"{MANIFEST_NAME}{suffix}").unlink(missing_ok=True)
    return failed


def _written_by_tomd(out_dir: Path) -> list[str]:
    """Outputs in `out_dir` that tomd wrote (as manifest-style output names): `.md` files,
    and books' `<folder>/index.md`, whose front matter carries tomd's own keys."""
    from tomd.pipeline.book import INDEX_NAME

    if not out_dir.is_dir():
        return []
    found: list[str] = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.suffix == ".md" and _has_tomd_front_matter(path):
            found.append(path.name)
        elif path.is_dir() and _has_tomd_front_matter(path / INDEX_NAME):
            found.append(f"{path.name}/{INDEX_NAME}")
    return found


def _has_tomd_front_matter(path: Path) -> bool:
    """The file opens with the front matter tomd writes (ADR-010): `---`, then within its
    first lines both `source_sha256:` and `converter_version:`."""
    try:
        with path.open(encoding="utf-8") as fh:
            head = [fh.readline() for _ in range(_FRONT_MATTER_LINES)]
    except (OSError, UnicodeDecodeError):
        return False
    if not head or head[0].strip() != "---":
        return False
    keys = {line.split(":", 1)[0] for line in head[1:] if ":" in line}
    return {"source_sha256", "converter_version"} <= keys


def _clear_article_cache(out_dir: Path) -> None:
    """The Wikipedia articles tomd downloaded into the output folder."""
    from tomd.pipeline.fetch import CACHE_DIR

    cache = out_dir / CACHE_DIR
    if cache.is_dir():
        for path in cache.glob("*.html"):
            path.unlink(missing_ok=True)
        if not any(cache.iterdir()):
            cache.rmdir()


def convert(
    source: str,
    out_dir: Path,
    *,
    revision: int | None = None,
    pdf_backend: str = DEFAULT_BACKEND,
    ocr: OcrMode = OcrMode.AUTO,
    split: str = "auto",
    split_level: int = 1,
) -> ConversionResult:
    """Convert one source. A book may be split by chapters (ADR-018): then the result is
    the book's folder and `markdown_path` its `index.md`."""
    from tomd.pipeline.book import render_book, write_book
    from tomd.transforms.split import split_document

    doc = read_source(source, revision=revision, pdf_backend=pdf_backend, ocr=ocr)
    slug, doc = prepare_document(doc)
    book, doc = split_document(doc, mode=split, level=split_level)
    if book is not None:
        rendered = render_book(book)
        return ConversionResult(write_book(out_dir, slug, rendered), rendered.diagnostics, doc)
    result = render_markdown(doc, slug=slug)
    target = write_output(out_dir, slug, result.text, doc.assets)
    return ConversionResult(target, result.diagnostics, doc)


def slugify(title: str) -> str:
    """Deterministic, filesystem-safe slug: accents stripped, lowercase, dashes."""
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:_SLUG_MAX].strip("-")
    return slug or "document"


def atomic_write(target: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
