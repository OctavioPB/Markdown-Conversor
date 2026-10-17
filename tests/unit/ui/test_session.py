"""Review logic of the desktop UI (no widgets): anchors, reviews from the manifest,
library wording, the text preview and the background preview job."""

import time
from pathlib import Path

from tomd import __version__
from tomd.ir import (
    Document,
    DocumentMeta,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    Provenance,
    Strong,
    Text,
)
from tomd.pipeline.anchors import page_anchors
from tomd.pipeline.batch import MANIFEST_NAME, BatchOptions, run_batch
from tomd.pipeline.manifest import Finding, LibraryEntry, Manifest
from tomd.ui.job import Failed, PreviewJob
from tomd.ui.session import Review, preview_runs, short_date, what_to_review

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
TWO_PAGES = CORPUS / "pdf" / "synthetic-page-break" / "input.pdf"


def _entry(state: str = "to_review", **changes: object) -> LibraryEntry:
    values: dict[str, object] = dict(
        sha256="s", source="/in/x.pdf", title="X", output="x.md", state=state, error=None,
        finished_at="2026-10-17T09:00:00", reviewed_at=None, to_check=3, findings=3,
        first_code="pdf.reading_order_uncertain", first_page=2,
    )  # fmt: skip
    values.update(changes)
    return LibraryEntry(**values)  # type: ignore[arg-type]


def _review(tmp_path: Path) -> Review:
    run_batch([TWO_PAGES], BatchOptions(out_dir=tmp_path, ocr="never"))
    manifest = Manifest(tmp_path / MANIFEST_NAME)
    (entry,) = manifest.library(__version__)
    review = Review.load(manifest, __version__, entry, tmp_path)
    manifest.close()
    return review


def test_review_loads_from_the_manifest(tmp_path: Path) -> None:
    review = _review(tmp_path)
    assert review.markdown and review.markdown_path.is_file() and review.is_pdf
    assert review.title == "Párrafos a través de páginas" or review.title
    pages = [a.page for a in review.anchors]
    lines = [a.line for a in review.anchors]
    assert pages == sorted(pages) and set(pages) == {1, 2}
    assert lines == sorted(lines)
    first, second = review.line_for_page(1), review.line_for_page(2)
    assert first is not None and second is not None and first < second
    anchor = review.anchor_for_line(second)
    assert anchor is not None and anchor.page == 2 and anchor.bbox is not None
    assert review.anchor_for_line(1) is None  # the front matter belongs to no block


def test_checklist_puts_errors_first_and_info_apart() -> None:
    findings = [
        Finding("pdf.furniture_removed", "info", "", idx=0),
        Finding("pdf.reading_order_uncertain", "warning", "", 3, idx=1, checked=True),
        Finding("pdf.page_unreadable", "error", "", 5, idx=2),
        Finding("pdf.garbage_text", "warning", "", 1, idx=3),
    ]
    review = Review(_entry(), Path("x.md"), "", findings, [])
    assert [f.idx for f in review.to_review] == [2, 3, 1]
    assert [f.idx for f in review.automatic] == [0]
    assert review.checked_count() == 1


def test_line_for_page_falls_back_to_the_previous_page() -> None:
    doc = Document(
        DocumentMeta("T", "pdf", "x.pdf"),
        (Paragraph((Text("Alpha beta gamma delta"),), Provenance("x.pdf", 1)),),
    )
    markdown = "Alpha beta gamma delta\n"
    review = Review(_entry(), Path("x.md"), markdown, [], page_anchors(doc, markdown))
    assert review.line_for_page(3) == 1  # page 3 has no block: show where page 1 is
    assert review.line_for_page(0) is None


def test_anchor_matching_survives_markdown_escaping() -> None:
    doc = Document(
        DocumentMeta("T", "pdf", "x.pdf"),
        (
            Paragraph((Text("Intro text here."),), Provenance("x.pdf", 1)),
            Paragraph(
                (Strong((Text("Bold"),)), Text(" start_of *page* two")), Provenance("x.pdf", 2)
            ),
        ),
    )
    markdown = "Intro text here.\n\n**Bold** start\\_of \\*page\\* two\n"
    assert [(a.page, a.line) for a in page_anchors(doc, markdown)] == [(1, 1), (2, 3)]


def test_library_wording() -> None:
    assert what_to_review(_entry()) == "Orden de las columnas · pág. 2 · 2 más"
    assert what_to_review(_entry(to_check=1)) == "Orden de las columnas · pág. 2"
    assert "DRM" in what_to_review(_entry("failed", error="DrmProtectedError: x"))
    assert what_to_review(_entry("reviewed", reviewed_at="2026-03-05T10:00:00")) == (
        "Revisado el 5 mar. 2026"
    )
    assert what_to_review(_entry("ready")) == "—"
    assert short_date("2026-10-17T09:00:00") == "17 oct. 2026" and short_date(None) == ""


def test_preview_runs_style_blocks_and_mark_pages() -> None:
    doc = Document(
        DocumentMeta("T", "pdf", "x.pdf"),
        (
            Heading(2, (Text("Section"),), Provenance("x.pdf", 1)),
            Paragraph((Text("Plain "), Strong((Text("bold"),))), Provenance("x.pdf", 1)),
            ListBlock(
                (ListItem((Paragraph((Text("item"),)),)),), provenance=Provenance("x.pdf", 2)
            ),
        ),
    )
    runs = preview_runs(doc)
    assert ("Section", ("h2", "page-1")) in [(r.text, r.tags) for r in runs]
    assert any(r.text == "bold" and "bold" in r.tags for r in runs)
    assert any(r.text == "• " and "page-2" in r.tags for r in runs)
    # The page mark sits on the first run of a block only.
    assert sum("page-1" in r.tags for r in runs) == 2


def _poll(job: PreviewJob, seconds: float = 60) -> object:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        outcome = job.poll()
        if outcome is not None:
            return outcome
        time.sleep(0.1)
    job.cancel()
    raise AssertionError("preview did not finish")


def test_preview_job_reads_the_document_again() -> None:
    outcome = _poll(PreviewJob(str(TWO_PAGES), ocr="never"))
    assert isinstance(outcome, Document) and outcome.blocks


def test_figures_become_image_runs() -> None:
    outcome = _poll(PreviewJob(str(CORPUS / "pdf" / "arxiv-mistral-7b" / "input.pdf")))
    assert isinstance(outcome, Document)
    images = [r for r in preview_runs(outcome) if r.image]
    assert images and all(r.image in {a.sha256 for a in outcome.assets} for r in images)


def test_preview_job_reports_errors(tmp_path: Path) -> None:
    outcome = _poll(PreviewJob(str(tmp_path / "missing.txt")))
    assert isinstance(outcome, Failed) and outcome.kind == "UnsupportedSourceError"
