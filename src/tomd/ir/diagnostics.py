"""Diagnostics: every information loss or doubtful inference is reported, never silent."""

from dataclasses import dataclass
from typing import Literal

from tomd.ir.nodes import Provenance

Severity = Literal["info", "warning", "error"]

# Catalog of codes (ADR-006, ADR-011). Readers may add source-specific codes with their
# own prefix (e.g. "pdf.", "wiki."); writer and transform codes live here.
TABLE_HTML_FALLBACK = "table.html_fallback"
TABLE_HEADER_PROMOTED = "table.header_promoted"
TABLE_EMPTY_DROPPED = "table.empty_dropped"
WIKI_INFOBOX_FLATTENED = "wiki.infobox_flattened"
MATH_UNRECOVERABLE = "math.unrecoverable"
IMAGE_DROPPED = "image.dropped"
PDF_READING_ORDER_UNCERTAIN = "pdf.reading_order_uncertain"
FOOTNOTE_ORPHAN_DEFINITION = "footnote.orphan_definition"
FOOTNOTE_MISSING_DEFINITION = "footnote.missing_definition"
FOOTNOTE_DUPLICATE_DEFINITION = "footnote.duplicate_definition"
HEADING_LEVEL_CLAMPED = "heading.level_clamped"
DOC_TITLE_MISSING = "doc.title_missing"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    provenance: Provenance | None = None
