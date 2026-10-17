"""The desktop window (design D): library, review, adding documents, drag and drop."""

import contextlib
import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tomd.pipeline.batch import BatchOptions, run_batch

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
TWO_PAGES = CORPUS / "pdf" / "synthetic-page-break" / "input.pdf"
LISTS = CORPUS / "pdf" / "synthetic-lists" / "input.pdf"
FIXED = CORPUS / "epub" / "synthetic-epub-fixed-layout" / "input.epub"
TWO_COLUMNS = CORPUS / "pdf" / "acl-2022-adapler" / "input.pdf"


@pytest.fixture
def tk_root() -> Iterator[Any]:
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    yield root
    with contextlib.suppress(tkinter.TclError):
        root.destroy()


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Message boxes record their text instead of waiting for a click."""
    from tomd.ui import app as app_module

    shown: list[str] = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(app_module.messagebox, name, lambda _t, text, **_k: shown.append(text))
    monkeypatch.setattr(app_module.messagebox, "askokcancel", lambda *_a, **_k: True)
    return shown


def _app(root: Any, out: Path, settings: Path | None = None) -> Any:
    from tomd.ui.app import TomdApp

    app = TomdApp(root, out, settings)
    app.ocr = "never"
    return app


def _wait_batch(app: Any, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while (app.batch is not None or app.queue) and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.05)
    assert app.batch is None, "batch did not finish"
    app.root.update()


def test_first_use_shows_the_drop_zone(tmp_path: Path, tk_root: Any, quiet: list[str]) -> None:
    app = _app(tk_root, tmp_path / "out")
    tk_root.update()
    assert not app.entries
    assert app.library.empty.winfo_manager() == "grid"
    assert app.library.list_area.winfo_manager() == ""
    app.close()


def test_library_lists_states_filters_and_searches(
    tmp_path: Path, tk_root: Any, quiet: list[str]
) -> None:
    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    tk_root.update()
    library = app.library
    assert library.list_area.winfo_manager() == "grid"
    states = sorted(e.state for e in app.entries)
    assert states == ["ready", "to_review"]
    assert len(library.tree.get_children()) == 2
    library.set_filter("to_review")
    (item,) = library.tree.get_children()
    assert library.tree.item(item, "values")[2] == "Libro de páginas fijas"
    assert "Por revisar · 1" in str(library.filter_buttons["to_review"].cget("text"))
    library.set_filter("all")
    library.search.set("fixed layout")
    assert len(library.tree.get_children()) == 1
    library.search.set("nothing like this")
    assert not library.tree.get_children()
    app.close()


def test_review_checklist_mark_and_next(tmp_path: Path, tk_root: Any, quiet: list[str]) -> None:
    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED, TWO_COLUMNS], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    pending = [e for e in app.entries if e.state == "to_review"]
    assert len(pending) == 2

    app.review_pending()
    tk_root.update()
    first = app.review
    assert first is not None and first.entry.sha256 == pending[0].sha256
    view = app.review_view
    assert view.raw.get("1.0", "end-1c") == first.markdown
    assert len(view._checks) == len(first.to_review) >= 1
    assert "0 de" in str(view.check_title.cget("text"))

    # Checking an item persists it and updates the count.
    finding, var = view._checks[0]
    var.set(True)
    view._check_command(finding, var)()
    assert "1 de" in str(view.check_title.cget("text"))
    assert app.manifest.findings(first.entry.sha256, app.version)[finding.idx].checked

    # Marking reviewed (allowed with items unchecked) goes to the next pending document.
    view._toggle_reviewed()
    tk_root.update()
    assert app._entry(first.entry.sha256).state == "reviewed"
    second = app.review
    assert second is not None and second.entry.sha256 == pending[1].sha256
    # The last one: back to the library.
    view._toggle_reviewed()
    assert app.review is None
    assert not [e for e in app.entries if e.state == "to_review"]
    app.close()


def test_review_pdf_follows_pages_and_findings(
    tmp_path: Path, tk_root: Any, quiet: list[str]
) -> None:
    out = tmp_path / "out"
    run_batch([TWO_PAGES], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    tk_root.deiconify()  # the page is drawn to the canvas width
    app.open_entry(app.entries[0])
    tk_root.update()
    view, review = app.review_view, app.review
    assert view.pdf is not None and view.page_label.get() == "pág. 1 de 2"
    view.show_page(2)
    line = review.line_for_page(2)
    assert str(view.raw.tag_ranges("current")[0]) == f"{line}.0"
    first = review.line_for_page(1)
    view.raw.mark_set("insert", f"{first}.0")
    view._markdown_clicked()
    assert view.page == 1 and view._highlight is not None
    # The text view arrives from the background read.
    deadline = time.monotonic() + 60
    while review.document is None and time.monotonic() < deadline:
        tk_root.update()
        time.sleep(0.05)
    assert review.document is not None
    assert view.preview.get("1.0", "1.end") == review.document.meta.title
    app.close()


def test_added_file_converts_and_opens_its_review(
    tmp_path: Path, tk_root: Any, quiet: list[str]
) -> None:
    app = _app(tk_root, tmp_path / "out")
    app.add([str(tmp_path / "notes.txt")])  # nothing usable: explained, nothing runs
    assert app.batch is None and "notes.txt" in quiet[-1]

    app.handle_drop([str(TWO_PAGES)])
    assert app.batch is not None and app.batch_open_after
    app.handle_drop([str(LISTS)])  # busy: queued, not lost
    assert len(app.queue) == 1
    _wait_batch(app)
    assert len(app.entries) == 2
    assert app.review is not None  # the single file opened its review
    assert app.review.markdown_path.is_file()
    app.close()


def test_several_items_fill_the_library(tmp_path: Path, tk_root: Any, quiet: list[str]) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    shutil.copyfile(TWO_PAGES, folder / "a.pdf")
    shutil.copyfile(FIXED, folder / "b.epub")
    app = _app(tk_root, tmp_path / "out")
    app.add([str(folder)])
    assert app.batch is not None and app.batch.label == "in" and not app.batch_open_after
    _wait_batch(app)
    assert app.review is None and len(app.entries) == 2
    assert "2 convertido" in app.status.get()
    app.close()


def test_options_are_remembered(tmp_path: Path, tk_root: Any, quiet: list[str]) -> None:
    settings = tmp_path / "settings.json"
    from tomd.ui.app import TomdApp

    settings.write_text(json.dumps({"out_dir": str(tmp_path / "saved"), "ocr": "always"}))
    app = TomdApp(tk_root, None, settings)
    assert app.out_dir == tmp_path / "saved" and app.ocr == "always"
    app.set_out_dir(tmp_path / "other")
    app._save_settings()
    assert json.loads(settings.read_text())["out_dir"] == str(tmp_path / "other")
    app.close()


def test_drop_event_reaches_the_window_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tkinter = pytest.importorskip("tkinter")
    pytest.importorskip("tkinterdnd2")
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
    app.library.tree.event_generate("<<Drop>>", when="now")
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
