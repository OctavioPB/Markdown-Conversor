"""Diagnostics: every information loss or doubtful inference is reported, never silent.

Every code lives in `CATALOG`, with its severity, what it means and what the user can do
about it. Emitters call `diagnose()`, which takes the severity from the catalog, so one
code always has one severity. `docs/DIAGNOSTICS.md` is generated from the catalog
(`scripts/gen_diagnostics_doc.py`).

Severity policy:
- error: the output is not a usable document as asked (ADR-011: no title at all).
- warning: content was lost, or is probably wrong; a person should look at it.
- info: a deliberate transformation, or an inference worth knowing; nothing to fix.
"""

from dataclasses import dataclass
from typing import Literal

from tomd.ir.nodes import Provenance

Severity = Literal["info", "warning", "error"]

# --- Codes (ADR-006, ADR-011) ----------------------------------------------------------------
# Document and text
DOC_TITLE_MISSING = "doc.title_missing"
DOC_SPLIT_SKIPPED = "doc.split_skipped"
TEXT_INVALID_UNICODE = "text.invalid_unicode"
HEADING_LEVEL_CLAMPED = "heading.level_clamped"
# Tables, math, images, footnotes
TABLE_HTML_FALLBACK = "table.html_fallback"
TABLE_HEADER_PROMOTED = "table.header_promoted"
TABLE_EMPTY_DROPPED = "table.empty_dropped"
MATH_UNRECOVERABLE = "math.unrecoverable"
IMAGE_DROPPED = "image.dropped"
IMAGE_SKIPPED_SMALL = "image.skipped_small"
FOOTNOTE_ORPHAN_DEFINITION = "footnote.orphan_definition"
FOOTNOTE_MISSING_DEFINITION = "footnote.missing_definition"
FOOTNOTE_DUPLICATE_DEFINITION = "footnote.duplicate_definition"
# HTML (Wikipedia and future HTML-based readers)
HTML_UNKNOWN_ELEMENT = "html.unknown_element"
HTML_DEFINITION_LIST_FLATTENED = "html.definition_list_flattened"
WIKI_INFOBOX_FLATTENED = "wiki.infobox_flattened"
WIKI_TEMPLATE_DROPPED = "wiki.template_dropped"
# EPUB
EPUB_FIXED_LAYOUT = "epub.fixed_layout"
EPUB_MISSING_RESOURCE = "epub.missing_resource"
EPUB_HEADING_INFERRED = "epub.heading_inferred"
EPUB_INTERNAL_LINK_DROPPED = "epub.internal_link_dropped"
EPUB_COVER_SKIPPED = "epub.cover_skipped"
EPUB_TOC_SKIPPED = "epub.toc_skipped"
EPUB_BOILERPLATE_DROPPED = "epub.boilerplate_dropped"
EPUB_PAGE_MARKERS_DROPPED = "epub.page_markers_dropped"
EPUB_NOTES_HEADING_DROPPED = "epub.notes_heading_dropped"
# PDF
PDF_PAGE_UNREADABLE = "pdf.page_unreadable"
PDF_PAGE_EMPTY = "pdf.page_empty"
PDF_PERMISSIONS_RESTRICTED = "pdf.permissions_restricted"
PDF_STRUCT_TREE_IGNORED = "pdf.struct_tree_ignored"
PDF_NO_TEXT_LAYER = "pdf.no_text_layer"
PDF_GARBAGE_TEXT = "pdf.garbage_text"
PDF_OCR_APPLIED = "pdf.ocr_applied"
PDF_OCR_LOW_CONFIDENCE = "pdf.ocr_low_confidence"
PDF_OCR_UNAVAILABLE = "pdf.ocr_unavailable"
PDF_READING_ORDER_UNCERTAIN = "pdf.reading_order_uncertain"
PDF_LOW_CONFIDENCE = "pdf.low_confidence"
PDF_ROTATED_TEXT_DROPPED = "pdf.rotated_text_dropped"
PDF_FURNITURE_REMOVED = "pdf.furniture_removed"
PDF_FOOTNOTE_UNMATCHED = "pdf.footnote_unmatched"
PDF_TABLE_NOT_RECONSTRUCTED = "pdf.table_not_reconstructed"


@dataclass(frozen=True, slots=True)
class CodeInfo:
    severity: Severity
    meaning: str  # what happened and what it costs the output
    action: str  # what the user can do; "" when there is nothing to do


_OCR_SETUP = (
    "Install Tesseract and the spa/eng language data (README, section OCR), or point "
    "TOMD_TESSERACT and TOMD_TESSDATA at them, then convert again."
)
_REPAIR_PDF = (
    "Re-export the PDF from its source, or repair it (`qpdf in.pdf out.pdf`), and convert again."
)

CATALOG: dict[str, CodeInfo] = {
    DOC_TITLE_MISSING: CodeInfo(
        "error",
        "The document has no title in its metadata and no top-level heading; the output "
        "file is named after the source file.",
        "Set a title in the source (PDF document properties) or check that the first "
        "heading was recognised.",
    ),
    DOC_SPLIT_SKIPPED: CodeInfo(
        "info",
        "The book was not split by chapters: no reliable chapter boundaries were found (no "
        "usable bookmarks or table of contents, and no recognisable chapter headings). The "
        "whole book is one file; nothing is lost.",
        "",
    ),
    TEXT_INVALID_UNICODE: CodeInfo(
        "warning",
        "The source contains invalid characters (broken UTF-8, lone surrogates); each was "
        "replaced by U+FFFD.",
        "The source file is damaged: obtain a fresh copy (fetch the article again).",
    ),
    HEADING_LEVEL_CLAMPED: CodeInfo(
        "warning",
        "A heading is nested deeper than H6, which Markdown cannot express; it was set to H6.",
        "",
    ),
    TABLE_HTML_FALLBACK: CodeInfo(
        "info",
        "A table with merged cells, several header rows or block content in cells cannot "
        "be a GFM table; it is emitted as an HTML table.",
        "",
    ),
    TABLE_HEADER_PROMOTED: CodeInfo(
        "info",
        "A table had no header row; its first row became the header (GFM requires one).",
        "",
    ),
    TABLE_EMPTY_DROPPED: CodeInfo("info", "A table without any text was dropped.", ""),
    MATH_UNRECOVERABLE: CodeInfo(
        "warning",
        "A formula had no recoverable LaTeX; its plain-text rendering (or nothing) is in "
        "the output.",
        "Check the formula against the source.",
    ),
    IMAGE_DROPPED: CodeInfo(
        "warning",
        "An image is missing from the output: unreadable, too large to decode safely, or "
        "not downloaded (Wikipedia). Its caption, if any, is kept.",
        "",
    ),
    IMAGE_SKIPPED_SMALL: CodeInfo(
        "info",
        "Images too small to be figures (icons, logos, decorations) were skipped.",
        "",
    ),
    FOOTNOTE_ORPHAN_DEFINITION: CodeInfo(
        "warning",
        "A footnote definition is never referenced in the text; it was dropped.",
        "",
    ),
    FOOTNOTE_MISSING_DEFINITION: CodeInfo(
        "warning",
        "A footnote reference has no definition; the reference was dropped.",
        "",
    ),
    FOOTNOTE_DUPLICATE_DEFINITION: CodeInfo(
        "warning",
        "A footnote is defined more than once; the first definition was kept.",
        "",
    ),
    HTML_UNKNOWN_ELEMENT: CodeInfo(
        "info",
        "An HTML element tomd does not know was converted from its content (text kept, "
        "semantics lost).",
        "",
    ),
    HTML_DEFINITION_LIST_FLATTENED: CodeInfo(
        "info",
        "Definition lists have no Markdown syntax; terms and definitions became paragraphs.",
        "",
    ),
    WIKI_INFOBOX_FLATTENED: CodeInfo(
        "info",
        "The infobox became a two-column table placed after the title (ADR-006).",
        "",
    ),
    WIKI_TEMPLATE_DROPPED: CodeInfo(
        "info",
        "Navigation and maintenance templates (navboxes, hatnotes, edit links) were removed.",
        "",
    ),
    EPUB_FIXED_LAYOUT: CodeInfo(
        "warning",
        "The EPUB has a fixed layout (pre-paginated: comics, picture books, magazines); its "
        "text is converted, but its meaning may depend on the page design.",
        "Check the output against the book; a PDF edition may convert better.",
    ),
    EPUB_MISSING_RESOURCE: CodeInfo(
        "warning",
        "A content document listed in the spine is missing from the EPUB or damaged; its "
        "text is missing. The rest of the book was converted.",
        "The EPUB is damaged: obtain a fresh copy.",
    ),
    EPUB_HEADING_INFERRED: CodeInfo(
        "info",
        "Headings were inferred from the table of contents or from CSS class names, because "
        "the book does not mark them with <h1>-<h6>.",
        "",
    ),
    EPUB_INTERNAL_LINK_DROPPED: CodeInfo(
        "info",
        "Links between parts of the book point to places that do not exist in a single "
        "Markdown file; their text was kept, the link removed.",
        "",
    ),
    EPUB_COVER_SKIPPED: CodeInfo(
        "info",
        "The cover page (only the cover image) was left out of the text.",
        "",
    ),
    EPUB_TOC_SKIPPED: CodeInfo(
        "info",
        "A table of contents (its own page, or typed into the text) or a list of "
        "illustrations was left out: its links lead nowhere in Markdown, and the headings "
        "carry the structure.",
        "",
    ),
    EPUB_BOILERPLATE_DROPPED: CodeInfo(
        "info",
        "Distributor boilerplate (e.g. the Project Gutenberg header and license) was removed.",
        "",
    ),
    EPUB_PAGE_MARKERS_DROPPED: CodeInfo(
        "info",
        "Print page-break markers (page numbers of the paper edition) were removed.",
        "",
    ),
    EPUB_NOTES_HEADING_DROPPED: CodeInfo(
        "info",
        "A notes section became Markdown footnotes; its now-empty heading was removed.",
        "",
    ),
    PDF_PAGE_UNREADABLE: CodeInfo(
        "warning",
        "A page could not be loaded (damaged page object); its content is missing.",
        _REPAIR_PDF,
    ),
    PDF_PAGE_EMPTY: CodeInfo(
        "info",
        "A page has no content: it is blank, or its content stream is damaged and could "
        "not be decoded.",
        "If the page shows text in a PDF viewer: " + _REPAIR_PDF,
    ),
    PDF_PERMISSIONS_RESTRICTED: CodeInfo(
        "info",
        "The PDF sets permission flags (copy, print); they do not stop a local conversion. "
        "Password-protected PDFs are rejected instead (ADR-009).",
        "",
    ),
    PDF_STRUCT_TREE_IGNORED: CodeInfo(
        "info",
        "The PDF is tagged, but its structure tree is not used yet; the layout was "
        "inferred from the page.",
        "",
    ),
    PDF_NO_TEXT_LAYER: CodeInfo(
        "warning",
        "A page is a scan without a text layer; without OCR its text is missing.",
        "Convert with `--ocr auto` (the default) and Tesseract installed. " + _OCR_SETUP,
    ),
    PDF_GARBAGE_TEXT: CodeInfo(
        "warning",
        "A page's text layer is unreadable (broken font encoding); the extracted text is "
        "mostly garbage.",
        "Convert with `--ocr always` to read the page image instead.",
    ),
    PDF_OCR_APPLIED: CodeInfo(
        "info",
        "A page was read by OCR; its text may contain recognition errors. The message "
        "gives the mean confidence and any orientation, skew or resolution fix.",
        "",
    ),
    PDF_OCR_LOW_CONFIDENCE: CodeInfo(
        "warning",
        "OCR confidence on a page is low (poor scan quality): expect wrong words.",
        "Proofread the page, or rescan it at 300 dpi or more.",
    ),
    PDF_OCR_UNAVAILABLE: CodeInfo(
        "warning",
        "A page needs OCR but Tesseract or its language data was not found; the page's "
        "text is missing or garbage.",
        _OCR_SETUP,
    ),
    PDF_READING_ORDER_UNCERTAIN: CodeInfo(
        "warning",
        "The column layout of a page is ambiguous; paragraphs may be out of order.",
        "Check the paragraph order of that page against the source.",
    ),
    PDF_LOW_CONFIDENCE: CodeInfo(
        "info",
        "A block was classified with low confidence (a heading guessed from font size "
        "only, a guessed hyphen join).",
        "",
    ),
    PDF_ROTATED_TEXT_DROPPED: CodeInfo(
        "info",
        "Rotated text (margin stamps such as the arXiv identifier, rotated axis labels) "
        "was dropped.",
        "",
    ),
    PDF_FURNITURE_REMOVED: CodeInfo(
        "info",
        "Running headers, footers and page numbers repeated across pages were removed.",
        "",
    ),
    PDF_FOOTNOTE_UNMATCHED: CodeInfo(
        "info",
        "Text that looks like a footnote has no matching call on its page; it was kept as "
        "ordinary text.",
        "",
    ),
    PDF_TABLE_NOT_RECONSTRUCTED: CodeInfo(
        "warning",
        "Something on the page looks like a table (a table caption, or rows aligned in "
        "columns) but it was not rebuilt with confidence; its content stays as paragraphs "
        "and the row and column structure is lost.",
        "Compare that page with the source. If the table matters, convert with "
        "`--backend docling` or copy it by hand.",
    ),
}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    provenance: Provenance | None = None


def diagnose(code: str, message: str, provenance: Provenance | None = None) -> Diagnostic:
    """A diagnostic with the catalog's severity for `code` (unknown codes are a bug)."""
    return Diagnostic(code, CATALOG[code].severity, message, provenance)
