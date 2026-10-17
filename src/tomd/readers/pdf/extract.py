"""Character extraction with pypdfium2 (§1, ADR-014). Pages are yielded one at a time and
PDFium objects are closed right after, to stay within the memory budget."""

import ctypes
import io
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image as PILImage

from tomd.errors import DrmProtectedError, UnreadableInputError
from tomd.readers.pdf.model import Char, PageInfo, PdfFigure, Ruling
from tomd.readers.pdf.text import is_garbage, normalize_char

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
# Weight and slant from font names. Besides "Bold"/"Italic": TeX's Computer Modern
# (CMBX bold, CMTI/CMSL text italic and slanted, CMMI math italic) and URW Nimbus as used
# by LaTeX ("-Medi" is the bold weight, "ReguItal" the italic).
_BOLD_NAME = re.compile(
    r"bold|black|heavy|semibold|demibold|demi\b|-bd|,bd|cmbx|cmb\d|-medi\b|medi(?=ital)",
    re.IGNORECASE,
)
_ITALIC_NAME = re.compile(
    r"italic|oblique|ital\b|-it\b|-it[A-Z]|,it|cmti|cmsl|cmmi|cmbxti", re.IGNORECASE
)
_MONO_NAME = re.compile(r"mono|courier|consol|code|typewriter|cmtt|fixed", re.IGNORECASE)
# PDF font descriptor flags (ISO 32000-1, table 123).
_FLAG_FIXED_PITCH = 1 << 0
_FLAG_ITALIC = 1 << 6
_FLAG_FORCE_BOLD = 1 << 18
# Paths thinner than this (points) are rules; table borders are 0.25-2 pt wide.
_RULE_THICKNESS = 2.0
# Shorter paths are glyph-like marks (underlines of one letter, bullets), not rules.
_MIN_RULE_LENGTH = 8.0
# Characters rotated by more than this (radians, ~3°) are not horizontal text.
_UPRIGHT_TOLERANCE = 0.05
# Weights of 600 and above are semibold or heavier (CSS/OpenType convention).
_BOLD_WEIGHT = 600


@dataclass(frozen=True, slots=True)
class PdfMetadata:
    title: str | None
    author: str | None
    language: str | None
    page_count: int
    encrypted: bool


# "Failed to load document (PDFium: Data format error)." -> "Data format error"; PDFium
# reports "Success" for some failures, which says nothing.
_PDFIUM_REASON = re.compile(r"PDFium: ([^)]+)\)")


@contextmanager
def open_pdf(path: Path) -> Iterator[pdfium.PdfDocument]:
    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise DrmProtectedError(
                f"{path.name} is password-protected; tomd does not remove protection "
                "(ADR-009). Convert an unprotected copy."
            ) from exc
        reason = _PDFIUM_REASON.search(str(exc))
        detail = reason.group(1) if reason and reason.group(1) != "Success" else "unknown cause"
        raise UnreadableInputError(
            f"{path.name} could not be opened as a PDF ({detail}): it is damaged, truncated "
            "or not a PDF. Re-export it from its source or repair it (`qpdf in.pdf out.pdf`)."
        ) from exc
    try:
        yield doc
    finally:
        doc.close()


def read_metadata(doc: pdfium.PdfDocument) -> PdfMetadata:
    info = doc.get_metadata_dict(skip_empty=True)
    encrypted = pdfium_c.FPDF_GetSecurityHandlerRevision(doc.raw) != -1
    return PdfMetadata(
        title=(info.get("Title") or "").strip() or None,
        author=(info.get("Author") or "").strip() or None,
        language=_catalog_language(doc),
        page_count=len(doc),
        encrypted=encrypted,
    )


def _catalog_language(doc: pdfium.PdfDocument) -> str | None:
    getter = getattr(pdfium_c, "FPDFCatalog_GetLanguage", None)
    if getter is None:
        return None
    size = getter(doc.raw, None, 0)
    if size <= 2:
        return None
    buffer = ctypes.create_string_buffer(size)
    getter(doc.raw, buffer, size)
    value = buffer.raw[: size - 2].decode("utf-16-le", errors="ignore").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class PageContent:
    info: PageInfo
    chars: list[Char]
    figures: list[PdfFigure]  # images of at least `min_figure_area` × page area
    rulings: list[Ruling]  # straight lines that may form table grids


# Images larger than this many pixels are skipped before decoding: 40 MP is beyond any
# figure at print resolution (a full A3 photo at 300 dpi is 35 MP) and decodes to 160 MB.
MAX_IMAGE_PIXELS = 40_000_000


def iter_pages(
    doc: pdfium.PdfDocument, min_figure_area: float = 0.0, max_image_pixels: int = MAX_IMAGE_PIXELS
) -> Iterator[PageContent]:
    """Yield each page's content. Small, huge or unreadable images are only counted."""
    for index in range(len(doc)):
        try:
            page = doc[index]
        except pdfium.PdfiumError:
            # A damaged page object: skip it, keep the rest of the document.
            yield PageContent(PageInfo(index + 1, 0.0, 0.0, 0, 0, unreadable=True), [], [], [])
            continue
        try:
            width, height = page.get_size()
            textpage = page.get_textpage()
            try:
                chars, garbage, rotated = _page_chars(textpage)
            finally:
                textpage.close()
            figures, skipped, small = _page_figures(
                page, index + 1, min_figure_area * width * height, max_image_pixels
            )
            rulings = _page_rulings(page)
            struct = _has_struct_tree(page)
            empty = pdfium_c.FPDFPage_CountObjects(page) == 0
        finally:
            page.close()
        info = PageInfo(
            index + 1,
            width,
            height,
            len(chars),
            garbage,
            skipped,
            struct,
            rotated,
            empty=empty,
            small_image_count=small,
        )
        yield PageContent(info, chars, figures, rulings)


def _page_rulings(page: pdfium.PdfPage) -> list[Ruling]:
    """Horizontal and vertical rules: thin path objects, and the edges of stroked
    rectangles (tables drawn cell by cell). Curves and complex paths are ignored."""
    rulings: list[Ruling] = []
    fill = ctypes.c_int()
    stroke = ctypes.c_int()
    for obj, (x0, y0, x1, y1) in _objects_in_page_space(page, pdfium_c.FPDF_PAGEOBJ_PATH):
        width, height = x1 - x0, y1 - y0
        if height <= _RULE_THICKNESS and width >= _MIN_RULE_LENGTH:
            rulings.append(Ruling(True, (y0 + y1) / 2, x0, x1))
        elif width <= _RULE_THICKNESS and height >= _MIN_RULE_LENGTH:
            rulings.append(Ruling(False, (x0 + x1) / 2, y0, y1))
        elif width >= _MIN_RULE_LENGTH and height >= _MIN_RULE_LENGTH:
            segments = pdfium_c.FPDFPath_CountSegments(obj.raw)
            pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
            if stroke.value and segments in (4, 5):  # a stroked rectangle
                rulings += [
                    Ruling(True, y0, x0, x1),
                    Ruling(True, y1, x0, x1),
                    Ruling(False, x0, y0, y1),
                    Ruling(False, x1, y0, y1),
                ]
    return rulings


def _page_figures(
    page: pdfium.PdfPage, number: int, min_area: float, max_pixels: int
) -> tuple[list[PdfFigure], int, int]:
    """Figures, images lost (unreadable or too large) and images skipped as too small."""
    figures: list[PdfFigure] = []
    skipped = small = 0
    for obj, (x0, y0, x1, y1) in _objects_in_page_space(page, pdfium_c.FPDF_PAGEOBJ_IMAGE):
        if (x1 - x0) * (y1 - y0) < min_area:
            small += 1
            continue
        try:
            px_width, px_height = obj.get_px_size()
        except pdfium.PdfiumError:
            skipped += 1
            continue
        if px_width * px_height > max_pixels:  # a decompression bomb, or a damaged size
            skipped += 1
            continue
        buffer = io.BytesIO()
        try:
            obj.extract(buffer)  # JPEG/JPX copied byte for byte; other formats as PNG
        except (pdfium.PdfiumError, OSError, ValueError, PILImage.DecompressionBombError):
            # Damaged image data, or a format PIL cannot encode: this image only is lost.
            skipped += 1
            continue
        data = buffer.getvalue()
        media_type = _media_type(data)
        if media_type is None:
            skipped += 1
            continue
        figures.append(PdfFigure(number, (x0, y0, x1, y1), data, media_type))
    return figures, skipped, small


def _media_type(data: bytes) -> str | None:
    """Media type from the file signature (magic bytes)."""
    for media_type, signatures in _SIGNATURES:
        if any(data.startswith(s) or data[4:].startswith(s) for s in signatures):
            return media_type
    return None


# Signatures written as hex so no binary escapes live in the source.
_SIGNATURES: tuple[tuple[str, tuple[bytes, ...]], ...] = (
    ("image/jpeg", (bytes.fromhex("ffd8ff"),)),
    ("image/png", (bytes.fromhex("89504e47"),)),
    ("image/jp2", (bytes.fromhex("6a5020200d0a870a"), b"ftypjp2")),
    ("image/tiff", (bytes.fromhex("49492a00"), bytes.fromhex("4d4d002a"))),
)


type _Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# Form XObjects nest; deeper nesting than this is pathological and ignored.
_MAX_FORM_DEPTH = 15


def _objects_in_page_space(
    page: pdfium.PdfPage, wanted: int
) -> Iterator[tuple[Any, tuple[float, float, float, float]]]:
    """Page objects of one type with their bounds in page coordinates.

    PDFium reports the bounds of objects nested in form XObjects in the form's own space,
    so the matrices of the enclosing forms are composed and applied here.
    """
    forms: dict[int, _Matrix] = {}  # level -> page-space matrix of the open form
    for obj in page.get_objects(max_depth=_MAX_FORM_DEPTH):
        level = obj.level
        parent = forms.get(level - 1, _IDENTITY) if level else _IDENTITY
        if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            forms[level] = _compose(tuple(obj.get_matrix().get()), parent)
            for deeper in [k for k in forms if k > level]:
                del forms[deeper]
        if obj.type != wanted:
            continue
        bounds = obj.get_bounds()
        yield obj, _transform(bounds, parent) if level else bounds


def _compose(inner: _Matrix, outer: _Matrix) -> _Matrix:
    a, b, c, d, e, f = inner
    A, B, C, D, E, F = outer
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _transform(
    bounds: tuple[float, float, float, float], m: _Matrix
) -> tuple[float, float, float, float]:
    a, b, c, d, e, f = m
    x0, y0, x1, y1 = bounds
    xs, ys = [], []
    for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
        xs.append(a * x + c * y + e)
        ys.append(b * x + d * y + f)
    return min(xs), min(ys), max(xs), max(ys)


def _has_struct_tree(page: pdfium.PdfPage) -> bool:
    tree = pdfium_c.FPDF_StructTree_GetForPage(page.raw)
    if not tree:
        return False
    try:
        return bool(pdfium_c.FPDF_StructTree_CountChildren(tree) > 0)
    finally:
        pdfium_c.FPDF_StructTree_Close(tree)


def _page_chars(textpage: pdfium.PdfTextPage) -> tuple[list[Char], int, int]:
    raw = textpage.raw
    name_buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    origin_x = ctypes.c_double()
    origin_y = ctypes.c_double()
    fonts: dict[tuple[bytes, int, int], tuple[str, bool, bool, bool]] = {}
    chars: list[Char] = []
    garbage = 0
    rotated = 0
    count = textpage.count_chars()
    skip_next = False
    for i in range(count):
        if skip_next:  # low half of a surrogate pair already combined
            skip_next = False
            continue
        if pdfium_c.FPDFText_IsGenerated(raw, i) == 1:
            continue  # PDFium's own line breaks and spaces; lines are rebuilt here
        if not _is_upright(pdfium_c.FPDFText_GetCharAngle(raw, i)):
            rotated += 1  # margin stamps (arXiv), rotated labels: not part of the text flow
            continue
        code = pdfium_c.FPDFText_GetUnicode(raw, i)
        if code == 0:
            continue
        if 0xD800 <= code <= 0xDFFF:
            # PDFium returns UTF-16 code units: characters beyond the BMP (math italic
            # letters, emoji) span two indices. A lone half becomes U+FFFD (garbage).
            low = pdfium_c.FPDFText_GetUnicode(raw, i + 1) if i + 1 < count else 0
            if code <= 0xDBFF and 0xDC00 <= low <= 0xDFFF:
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                skip_next = True
            else:
                code = 0xFFFD
        text = normalize_char(chr(code))
        if not text:
            continue
        if any(is_garbage(c) for c in text):
            garbage += 1
        # Horizontal extent from the advance-based ("loose") box: ink boxes exclude side
        # bearings, so narrow glyphs would look like word gaps. Vertical extent from ink.
        x0, _, x1, _ = textpage.get_charbox(i, loose=True)
        _, y0, _, y1 = textpage.get_charbox(i)
        pdfium_c.FPDFText_GetCharOrigin(raw, i, ctypes.byref(origin_x), ctypes.byref(origin_y))
        pdfium_c.FPDFText_GetFontInfo(raw, i, name_buffer, len(name_buffer), ctypes.byref(flags))
        weight = pdfium_c.FPDFText_GetFontWeight(raw, i)
        key = (name_buffer.value, flags.value, weight)
        if key not in fonts:
            fonts[key] = _font_style(name_buffer.value, flags.value, weight)
        font, bold, italic, mono = fonts[key]
        size = pdfium_c.FPDFText_GetFontSize(raw, i)
        chars.append(
            Char(
                text=text,
                x0=x0,
                x1=x1,
                top=y1,
                bottom=y0,
                baseline=origin_y.value,
                size=round(size, 2),
                font=font,
                bold=bold,
                italic=italic,
                mono=mono,
            )
        )
    return chars, garbage, rotated


def _is_upright(angle: float) -> bool:
    """PDFium reports the rotation in radians in [0, 2π); -1 means unknown."""
    if angle < 0:
        return True
    return angle < _UPRIGHT_TOLERANCE or angle > 2 * math.pi - _UPRIGHT_TOLERANCE


def _font_style(raw_name: bytes, flags: int, weight: int) -> tuple[str, bool, bool, bool]:
    name = _SUBSET_PREFIX.sub("", raw_name.decode("latin-1", errors="replace"))
    bold = weight >= _BOLD_WEIGHT or bool(flags & _FLAG_FORCE_BOLD) or bool(_BOLD_NAME.search(name))
    italic = bool(flags & _FLAG_ITALIC) or bool(_ITALIC_NAME.search(name))
    mono = bool(flags & _FLAG_FIXED_PITCH) or bool(_MONO_NAME.search(name))
    return name, bold, italic, mono
