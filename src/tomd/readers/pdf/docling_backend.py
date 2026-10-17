"""`docling` PDF backend (ADR-005, ADR-015): local layout models on CPU.

Optional: installed with the `docling` extra, only for the phase 5 benchmark until the
benchmark ADR decides the default backend. Everything docling-specific lives here; no
other module imports docling.
"""

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tomd.errors import UnsupportedSourceError
from tomd.ir import (
    Asset,
    Block,
    Cell,
    CodeBlock,
    Diagnostic,
    Document,
    DocumentMeta,
    Heading,
    Image,
    Inline,
    ListBlock,
    ListItem,
    MathBlock,
    Paragraph,
    Provenance,
    Row,
    Table,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.readers.pdf.classify import NUMBERED_HEADING

# docling labels whose content is page furniture, not text flow.
_FURNITURE = frozenset({"page_header", "page_footer"})


@dataclass
class DoclingBackend:
    name: str = "docling"
    _converter: Any = field(default=None, repr=False)

    def convert(self, path: Path) -> Document:
        converter = self._converter or _make_converter()
        self._converter = converter
        result = converter.convert(str(path))
        return _to_ir(result.document, path)


def _make_converter() -> Any:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise UnsupportedSourceError(
            "The docling backend needs the optional extra: uv sync --extra docling"
        ) from exc
    options = PdfPipelineOptions()
    options.do_ocr = False  # OCR is phase 6, with its own detector
    options.generate_picture_images = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _to_ir(dl: Any, path: Path) -> Document:
    source = path.name
    blocks: list[Block] = []
    assets: dict[str, Asset] = {}
    diagnostics: list[Diagnostic] = []
    title: str | None = None
    list_stack: list[tuple[int, list[ListItem], bool]] = []  # (level, items, ordered)

    def flush_lists(down_to: int = -1) -> None:
        while list_stack and list_stack[-1][0] > down_to:
            _, items, ordered = list_stack.pop()
            lst = ListBlock(tuple(items), ordered)
            if list_stack:
                parent = list_stack[-1][1]
                last = parent[-1]
                parent[-1] = ListItem((*last.blocks, lst), last.provenance)
            else:
                blocks.append(lst)

    for item, level in dl.iterate_items():
        label = str(getattr(item, "label", "")).split(".")[-1].lower()
        if label in _FURNITURE or label == "caption":
            continue  # captions are attached to their table/picture below
        prov = _provenance(item, source)
        if label == "list_item":
            ordered = bool(getattr(item, "enumerated", False))
            while list_stack and list_stack[-1][0] > level:
                flush_lists(list_stack[-2][0] if len(list_stack) > 1 else -1)
            if not list_stack or list_stack[-1][0] < level:
                list_stack.append((level, [], ordered))
            list_stack[-1][1].append(ListItem((Paragraph(_text(item.text)),), prov))
            continue
        flush_lists()
        match label:
            case "title":
                title = title or item.text.strip()
                blocks.append(Heading(1, _text(item.text), prov))
            case "section_header":
                text = item.text.strip()
                if title is None and not any(isinstance(b, Heading) for b in blocks):
                    # docling labels most titles as section headers: the first heading
                    # becomes the document title, as in the heuristic backend.
                    title = text
                    blocks.append(Heading(1, _text(text), prov))
                    continue
                heading_level = min(int(getattr(item, "level", 1)) + 1, 6)
                numbered = NUMBERED_HEADING.match(text)
                if numbered:  # same rule as the heuristic backend: numbering sets the level
                    heading_level = min(numbered.group(1).count(".") + 2, 6)
                blocks.append(Heading(heading_level, _text(text), prov))
            case "code":
                blocks.append(CodeBlock(item.text, getattr(item, "code_language", None), prov))
            case "formula":
                if item.text.strip():
                    blocks.append(MathBlock(item.text, prov))
                else:
                    diagnostics.append(
                        diagnose(codes.MATH_UNRECOVERABLE, "Formula without LaTeX.", prov)
                    )
            case "table":
                blocks.append(_table(item, dl, prov))
            case "picture":
                image = _picture(item, dl, prov, assets, diagnostics)
                if image is not None:
                    blocks.append(image)
            case _:
                text = getattr(item, "text", "")
                if text and text.strip():
                    blocks.append(Paragraph(_text(text), prov))
    flush_lists()

    meta = DocumentMeta(
        title=title,
        source_type="pdf",
        source=source,
        source_sha256=_sha256(path),
        backend="docling",
    )
    return Document(meta, tuple(blocks), tuple(assets.values()), tuple(diagnostics))


def _text(value: str) -> tuple[Inline, ...]:
    return (Text(" ".join(value.split())),)


def _caption(item: Any, dl: Any) -> tuple[Inline, ...]:
    getter = getattr(item, "caption_text", None)
    text = getter(dl) if callable(getter) else ""
    return _text(text) if text and text.strip() else ()


def _table(item: Any, dl: Any, prov: Provenance | None) -> Table:
    data = item.data
    by_row: dict[int, list[tuple[int, Cell]]] = {}
    header_rows: set[int] = set()
    for cell in data.table_cells:
        row = cell.start_row_offset_idx
        blocks = (Paragraph(_text(cell.text)),) if cell.text.strip() else ()
        by_row.setdefault(row, []).append(
            (cell.start_col_offset_idx, Cell(blocks, max(cell.row_span, 1), max(cell.col_span, 1)))
        )
        if getattr(cell, "column_header", False):
            header_rows.add(row)
    rows = [Row(tuple(c for _, c in sorted(by_row[r], key=lambda x: x[0]))) for r in sorted(by_row)]
    head_count = 0
    while head_count in header_rows:
        head_count += 1
    return Table(tuple(rows[:head_count]), tuple(rows[head_count:]), _caption(item, dl), prov)


def _picture(
    item: Any,
    dl: Any,
    prov: Provenance | None,
    assets: dict[str, Asset],
    diagnostics: list[Diagnostic],
) -> Image | None:
    caption = _caption(item, dl)
    pil = item.get_image(dl) if hasattr(item, "get_image") else None
    if pil is None:
        diagnostics.append(diagnose(codes.IMAGE_DROPPED, "Picture not rendered.", prov))
        return None
    buffer = io.BytesIO()
    pil.save(buffer, format="PNG")
    asset = Asset.from_bytes(buffer.getvalue(), "image/png")
    assets.setdefault(asset.sha256, asset)
    return Image(asset.sha256, "", caption, prov)


def _provenance(item: Any, source: str) -> Provenance | None:
    provs = getattr(item, "prov", None)
    if not provs:
        return Provenance(source)
    first = provs[0]
    bbox = getattr(first, "bbox", None)
    box = (bbox.l, bbox.b, bbox.r, bbox.t) if bbox is not None else None
    return Provenance(source, getattr(first, "page_no", None), box)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
