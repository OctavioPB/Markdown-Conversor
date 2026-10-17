"""Review logic of the desktop UI (no widgets)."""

import contextlib
from pathlib import Path

import pytest

from tomd.ir import (
    Diagnostic,
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
from tomd.ir import diagnostics as codes
from tomd.pipeline.dispatch import convert
from tomd.readers.pdf.ocr import OcrMode
from tomd.ui.job import ConversionJob, Failed, Succeeded
from tomd.ui.session import (
    ConversionRequest,
    Review,
    diagnostic_rows,
    page_anchors,
    preview_runs,
)

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
TWO_PAGES = CORPUS / "pdf" / "synthetic-page-break" / "input.pdf"


def _review(tmp_path: Path, source: Path = TWO_PAGES) -> Review:
    result = convert(str(source), tmp_path, ocr=OcrMode.NEVER)
    request = ConversionRequest(str(source), tmp_path)
    return Review.from_result(request, result.markdown_path, result.diagnostics, result.document)


def test_anchors_follow_pages_in_markdown_order(tmp_path: Path) -> None:
    review = _review(tmp_path)
    pages = [a.page for a in review.anchors]
    lines = [a.line for a in review.anchors]
    assert pages == sorted(pages) and set(pages) == {1, 2}
    assert lines == sorted(lines)
    first, second = review.line_for_page(1), review.line_for_page(2)
    assert first is not None and second is not None and first < second
    anchor = review.anchor_for_line(second)
    assert anchor is not None and anchor.page == 2 and anchor.bbox is not None
    # Lines in the front matter belong to no block.
    assert review.anchor_for_line(1) is None


def test_line_for_page_falls_back_to_the_previous_page() -> None:
    doc = Document(
        DocumentMeta("T", "pdf", "x.pdf"),
        (Paragraph((Text("Alpha beta gamma delta"),), Provenance("x.pdf", 1)),),
    )
    review = Review(
        ConversionRequest("x.pdf", Path()), Path("x.md"), "Alpha beta gamma delta\n", doc, ()
    )
    review.anchors = page_anchors(doc, review.markdown)
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


def test_diagnostic_rows_are_sorted_and_carry_the_catalog_action() -> None:
    rows = diagnostic_rows(
        (
            Diagnostic(codes.PDF_FURNITURE_REMOVED, "info", "3 lines."),
            Diagnostic(codes.PDF_OCR_LOW_CONFIDENCE, "warning", "p2", Provenance("x", 2)),
            Diagnostic(codes.DOC_TITLE_MISSING, "error", "none"),
        )
    )
    assert [r.severity for r in rows] == ["error", "warning", "info"]
    assert rows[1].page == 2 and "Proofread" in rows[1].action
    assert rows[2].action == ""


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


def test_figures_become_image_runs(tmp_path: Path) -> None:
    review = _review(tmp_path, CORPUS / "pdf" / "arxiv-mistral-7b" / "input.pdf")
    assert review.document is not None
    images = [r for r in preview_runs(review.document) if r.image]
    assert images and all(r.image in {a.sha256 for a in review.document.assets} for r in images)


def test_job_converts_in_another_process(tmp_path: Path) -> None:
    job = ConversionJob(ConversionRequest(str(TWO_PAGES), tmp_path, ocr="never"))
    outcome = None
    for _ in range(600):  # up to 60 s
        outcome = job.poll()
        if outcome is not None:
            break
        import time

        time.sleep(0.1)
    assert isinstance(outcome, Succeeded)
    assert outcome.result.markdown_path.is_file() and outcome.result.document is not None


def test_job_reports_errors_with_exit_code(tmp_path: Path) -> None:
    from tomd.cli import EXIT_UNSUPPORTED

    job = ConversionJob(ConversionRequest(str(tmp_path / "missing.txt"), tmp_path))
    outcome = None
    for _ in range(600):
        outcome = job.poll()
        if outcome is not None:
            break
        import time

        time.sleep(0.1)
    assert isinstance(outcome, Failed)
    assert outcome.kind == "UnsupportedSourceError" and outcome.exit_code == EXIT_UNSUPPORTED


@pytest.fixture
def tk_root():  # type: ignore[no-untyped-def]
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    yield root
    with contextlib.suppress(tkinter.TclError):
        root.destroy()


def test_window_loads_a_review_and_navigates(tmp_path: Path, tk_root: object) -> None:
    from tomd.ui.app import TomdApp

    app = TomdApp(tk_root, tmp_path)  # type: ignore[arg-type]
    review = _review(tmp_path)
    app._add_review(review)
    app.root.update()
    assert app.pdf is not None and app.page_label.get() == "1 / 2"
    assert app.raw.get("1.0", "end-1c") == review.markdown
    assert app.preview.get("1.0", "end-1c").strip()
    app.show_page(2)
    line = review.line_for_page(2)
    assert app.raw.tag_ranges("current") and str(app.raw.tag_ranges("current")[0]) == f"{line}.0"
    # Clicking a line of page 1 brings the original back to page 1, highlighted.
    first = review.line_for_page(1)
    app.raw.mark_set("insert", f"{first}.0")
    app._markdown_clicked()
    assert app.page == 1 and app._highlight is not None
    app.find_text.set("párrafo")
    app.find_next()
    app.close()


def test_convert_button_runs_a_job_and_adds_the_result(tmp_path: Path, tk_root: object) -> None:
    import time

    from tomd.ui.app import TomdApp

    app = TomdApp(tk_root, tmp_path)  # type: ignore[arg-type]
    app.source.set(str(TWO_PAGES))
    app.ocr.set("never")
    app.start_conversion()
    assert app.job is not None
    deadline = time.monotonic() + 60
    while app.job is not None and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.05)
    assert app.job is None and len(app.reviews) == 1
    assert app.reviews[0].markdown_path.is_file()
    assert app.preview.get("1.0", "1.end") == app.reviews[0].title  # the title comes first
    app.close()


def test_convert_folder_runs_the_batch_and_shows_the_report(
    tmp_path: Path, tk_root: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import time

    from tomd.ui import app as app_module
    from tomd.ui.app import TomdApp

    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    folder = tmp_path / "library"
    folder.mkdir()
    shutil.copyfile(TWO_PAGES, folder / "a.pdf")
    shutil.copyfile(CORPUS / "epub" / "synthetic-epub3-notes" / "input.epub", folder / "b.epub")
    app = TomdApp(tk_root, tmp_path / "out")  # type: ignore[arg-type]
    app.ocr.set("never")
    app.convert_folder(str(folder))
    deadline = time.monotonic() + 120
    while app.batch is not None and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.05)
    assert app.batch is None
    assert app.history.get("end") == "Informe: library"
    assert "done | 2" in app.raw.get("1.0", "end")  # the report, shown in the Markdown tab
    assert (tmp_path / "out" / "parrafos-a-traves-de-paginas.md").is_file()
    app.close()


# --- Drag and drop -------------------------------------------------------------------------


def _wait(app: object, attribute: str, seconds: float = 120) -> None:
    import time

    deadline = time.monotonic() + seconds
    while getattr(app, attribute) is not None and time.monotonic() < deadline:
        app.root.update()  # type: ignore[attr-defined]
        time.sleep(0.05)


def test_dropped_file_converts_dropped_folders_batch(
    tmp_path: Path, tk_root: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from tomd.ui import app as app_module
    from tomd.ui.app import TomdApp

    shown: list[str] = []
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda title, text: shown.append(text))
    app = TomdApp(tk_root, tmp_path / "out")  # type: ignore[arg-type]
    app.ocr.set("never")

    app.handle_drop([str(tmp_path / "notes.txt")])  # nothing usable: explained, nothing runs
    assert app.job is None and app.batch is None and "notes.txt" in shown[-1]

    app.handle_drop([str(TWO_PAGES)])  # one document: converted and shown
    assert app.job is not None
    app.handle_drop([str(TWO_PAGES)])  # busy: told to wait
    assert "en curso" in app.status.get()
    _wait(app, "job")
    assert len(app.reviews) == 1 and app.reviews[0].markdown_path.is_file()

    other = tmp_path / "other.pdf"
    shutil.copyfile(CORPUS / "pdf" / "synthetic-lists" / "input.pdf", other)
    app.handle_drop([str(TWO_PAGES), str(other)])  # several: one batch
    assert app.batch is not None and app.batch.label == "2 elementos"
    _wait(app, "batch")
    assert app.history.get("end") == "Informe: 2 elementos"
    app.close()


def test_drop_event_reaches_the_window_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tkinter = pytest.importorskip("tkinter")
    pytest.importorskip("tkinterdnd2")
    from types import SimpleNamespace

    from tomd.ui.app import TomdApp, make_root

    try:
        root = make_root()
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    app = TomdApp(root, tmp_path / "out")
    assert app.enable_drop()  # tkdnd loaded
    received: list[list[str]] = []
    monkeypatch.setattr(app, "handle_drop", received.append)
    root.update()
    # A drop is handled once, whether it lands on an inner area or on the window.
    app.raw.event_generate("<<Drop>>", when="now")
    root.update()
    root.event_generate("<<Drop>>", when="now")
    root.update()
    assert len(received) == 2
    # tkdnd hands the paths over as a Tcl list: braces around paths with spaces.
    target = tmp_path / "with space" / "a.pdf"
    other = tmp_path / "b.epub"
    data = "{" + target.as_posix() + "} " + other.as_posix()
    action = app._on_drop(SimpleNamespace(data=data, action="copy"))
    assert action == "copy"
    assert received[-1] == [target.as_posix(), other.as_posix()]
    assert app._on_drop(SimpleNamespace(data=data, action="copy")) == "copy"
    assert len(received) == 3  # the same dispatch: not handled a second time
    app.close()
