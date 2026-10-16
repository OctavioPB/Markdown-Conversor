"""Choose a reader for a source and run the full conversion (reader -> transforms -> writer)."""

import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from tomd import __version__
from tomd.errors import UnsupportedSourceError
from tomd.ir import Diagnostic, Document
from tomd.readers.pdf.reader import DEFAULT_BACKEND, PdfReader
from tomd.readers.wikipedia.client import parse_article_ref
from tomd.readers.wikipedia.reader import WikipediaReader, read_parsoid_html
from tomd.transforms import apply_default_transforms
from tomd.writers.markdown import RenderResult, asset_filename, render_markdown

# Bytes that identify a Parsoid HTML document (a recorded Wikipedia article).
_PARSOID_MARKERS = (b"parsoid-body", b'property="mw:htmlVersion"')
_SLUG_MAX = 80


@dataclass(frozen=True, slots=True)
class ConversionResult:
    markdown_path: Path
    diagnostics: tuple[Diagnostic, ...]


def read_source(
    source: str, *, revision: int | None = None, pdf_backend: str = DEFAULT_BACKEND
) -> Document:
    path = Path(source)
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            data = path.read_bytes()
            if any(marker in data for marker in _PARSOID_MARKERS):
                return read_parsoid_html(data)
            raise UnsupportedSourceError(f"Generic HTML is out of scope: {source}")
        if suffix == ".pdf":
            return PdfReader(backend=pdf_backend).read(source)
        if suffix == ".epub":
            raise UnsupportedSourceError(f"No reader for {suffix} yet (see PLAN.md): {source}")
        raise UnsupportedSourceError(f"Unrecognised file type: {source}")
    try:
        parse_article_ref(source)
    except UnsupportedSourceError:
        raise UnsupportedSourceError(f"Not a file or a Wikipedia article: {source}") from None
    return WikipediaReader(revision=revision).read(source)


def render_document(doc: Document) -> tuple[str, RenderResult, Document]:
    """Apply transforms and render. Returns (slug, result, transformed document)."""
    doc = replace(doc, meta=replace(doc.meta, converter_version=__version__))
    doc = apply_default_transforms(doc)
    slug = slugify(doc.meta.title or Path(doc.meta.source).stem or "document")
    return slug, render_markdown(doc, slug=slug), doc


def convert(
    source: str,
    out_dir: Path,
    *,
    revision: int | None = None,
    pdf_backend: str = DEFAULT_BACKEND,
) -> ConversionResult:
    doc = read_source(source, revision=revision, pdf_backend=pdf_backend)
    slug, result, doc = render_document(doc)
    out_dir.mkdir(parents=True, exist_ok=True)
    if doc.assets:
        assets_dir = out_dir / f"{slug}.assets"
        assets_dir.mkdir(exist_ok=True)
        for asset in doc.assets:
            _atomic_write(assets_dir / asset_filename(asset), asset.data)
    target = out_dir / f"{slug}.md"
    _atomic_write(target, result.text.encode("utf-8"))
    return ConversionResult(target, result.diagnostics)


def slugify(title: str) -> str:
    """Deterministic, filesystem-safe slug: accents stripped, lowercase, dashes."""
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:_SLUG_MAX].strip("-")
    return slug or "document"


def _atomic_write(target: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
