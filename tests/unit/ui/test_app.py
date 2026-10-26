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


def test_forget_one_document_keeps_or_deletes_its_files(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.ui import app as app_module

    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    first, second = app.entries
    answers: list[bool | None] = [None, False, True]
    monkeypatch.setattr(app_module, "ask_forget", lambda *_a, **_k: answers.pop(0))

    app.forget([first])  # cancelled: nothing changes
    assert len(app.entries) == 2

    app.open_entry(second)
    app.forget([second])  # the list only: the file stays, the open review closes
    assert app.review is None and [e.sha256 for e in app.entries] == [first.sha256]
    assert (out / str(second.output)).is_file()
    assert "siguen en la carpeta" in app.status.get()

    app.forget([first])  # with its files
    assert not app.entries and not (out / str(first.output)).exists()
    assert app.library.empty.winfo_manager() == "grid"  # back to the first-use screen
    app.close()


def test_clear_library_and_not_while_converting(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.ui import app as app_module

    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    monkeypatch.setattr(app_module, "ask_forget", lambda *_a, **_k: False)
    app.batch = object()  # a conversion in progress
    app.clear_library()
    assert len(app.entries) == 2 and "Espera" in quiet[-1]
    app.batch = None
    outputs = [out / str(e.output) for e in app.entries]
    app.clear_library()
    assert not app.entries
    assert all(path.is_file() for path in outputs)  # the list only: files kept
    app.clear_library()
    assert "ya está vacía" in quiet[-1]
    app.close()


# --- Books split by chapters (ADR-018) -----------------------------------------------------


def _book_library(tmp_path: Path) -> Path:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parents[1] / "writers"))
    from test_book_rules import _pdf_book

    folder = tmp_path / "in"
    folder.mkdir()
    _pdf_book(folder / "networks.pdf")
    out = tmp_path / "out"
    run_batch([folder], BatchOptions(out_dir=out, ocr="never", split="chapters"))
    return out


def test_a_book_is_one_row_and_its_review_moves_between_chapters(
    tmp_path: Path, tk_root: Any, quiet: list[str]
) -> None:
    out = _book_library(tmp_path)
    app = _app(tk_root, out)
    (entry,) = app.entries
    (item,) = app.library.tree.get_children()
    assert app.library.tree.item(item, "values")[0] == "PDF · 3 partes"
    tk_root.deiconify()
    app.open_entry(entry)
    tk_root.update()
    view, review = app.review_view, app.review
    assert review is not None and review.is_book and review.part == 1
    assert "# Chapter 1 Basics" in view.raw.get("1.0", "end")
    assert view.part_box.winfo_manager() == "pack"
    assert view.part_box.cget("values")[0] == "Índice del libro"

    view.select_part(2)  # the selector
    assert "# Chapter 2 Routing" in view.raw.get("1.0", "end")
    assert review.markdown_path.name == "02-chapter-2-routing.md"
    view.show_page(6)  # turning to a page of chapter 3 shows chapter 3
    assert review.part == 3 and view.part_box.current() == 3

    # The text view: the IR read again, cut like the book on disk.
    deadline = time.monotonic() + 60
    while review.part_documents is None and time.monotonic() < deadline:
        tk_root.update()
        time.sleep(0.05)
    assert review.part_documents is not None and len(review.part_documents) == 3
    assert view.preview.get("1.0", "1.end") == "Chapter 3 Security"
    app.close()


def test_removing_a_book_deletes_its_folder(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.ui import app as app_module

    out = _book_library(tmp_path)
    app = _app(tk_root, out)
    labels: list[str] = []

    def answer(*_a: object, files_label: str = "", **_k: object) -> bool:
        labels.append(files_label)
        return True

    monkeypatch.setattr(app_module, "ask_forget", answer)
    app.forget([app.entries[0]])
    assert "carpeta" in labels[0] and "3 archivos" in labels[0]
    assert not (out / "networks").exists() and not app.entries
    app.close()


def test_the_split_option_is_remembered_and_used(tmp_path: Path, tk_root: Any) -> None:
    from tomd.ui.app import TomdApp

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"split": "none"}))
    app = TomdApp(tk_root, tmp_path / "out", settings)
    assert app.split == "none"
    app.split = "chapters"
    app._save_settings()
    assert json.loads(settings.read_text())["split"] == "chapters"
    settings.write_text(json.dumps({"split": "nonsense"}))
    assert TomdApp(tk_root, tmp_path / "out2", settings).split == "auto"
    app.close()  # destroys the shared root: last


def test_several_documents_are_selected_and_removed_together(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.ui import app as app_module

    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED, LISTS], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    library = app.library
    rows = library.tree.get_children()
    library.tree.selection_set(rows[:2])
    tk_root.update()
    assert len(library.selected_entries()) == 2 and library.selected() is None
    assert "(2)" in str(library.forget_button.cget("text"))
    assert "disabled" in library.open_button.state()  # opening is for one document
    texts: list[str] = []

    def answer(_root: object, _title: str, text: str, **_k: object) -> bool:
        texts.append(text)
        return False  # the list only

    monkeypatch.setattr(app_module, "ask_forget", answer)
    library._forget_selected()
    assert "Los 2 documentos seleccionados" in texts[0]
    assert len(app.entries) == 1
    library._select_all()
    assert len(library.selected_entries()) == 1
    app.close()


def test_retry_runs_every_selected_failure(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4 truncated " + name.encode())
    out = tmp_path / "out"
    run_batch([folder], BatchOptions(out_dir=out, ocr="never"))
    app = _app(tk_root, out)
    started: list[list[str]] = []
    monkeypatch.setattr(app, "start_batch", lambda sources, *_a, **_k: started.append(sources))
    app.library.tree.selection_set(app.library.tree.get_children())
    tk_root.update()
    assert "(2)" in str(app.library.retry_button.cget("text"))
    app.library._retry_selected()
    assert len(started) == 1 and len(started[0]) == 2
    app.close()


def test_starting_from_scratch_forgets_everything(
    tmp_path: Path, tk_root: Any, quiet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.pipeline.batch import MANIFEST_NAME
    from tomd.ui import app as app_module

    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED], BatchOptions(out_dir=out, ocr="never"))
    mine = out / "my-notes.txt"
    mine.write_text("mine", encoding="utf-8")
    app = _app(tk_root, out)
    outputs = [out / str(e.output) for e in app.entries]
    answers = [False, True]
    monkeypatch.setattr(app_module, "ask_forget", lambda *_a, **_k: answers.pop(0))

    app.reset()  # memory only: the files stay
    assert not app.entries and all(p.is_file() for p in outputs)
    assert (out / MANIFEST_NAME).is_file()  # a fresh, empty library
    again = run_batch([TWO_PAGES], BatchOptions(out_dir=out, ocr="never"))
    assert again.planned == 1  # converted again: tomd remembered nothing
    app.refresh()

    app.reset()  # with the files
    assert not any(p.exists() for p in outputs) and not (out / "report.md").exists()
    assert mine.is_file()  # not tomd's: never touched
    app.close()


def test_the_rag_option_is_remembered_and_sent_to_the_batch(
    tmp_path: Path, tk_root: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tomd.ui.app import TomdApp

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"rag": True}))
    app = TomdApp(tk_root, tmp_path / "out", settings)
    assert app.rag is True
    sent: list[Any] = []
    monkeypatch.setattr(app, "start_batch", lambda sources, options, **_k: sent.append(options))
    app.add([str(TWO_PAGES)])
    assert sent[0].rag is True
    app.rag = False
    app._save_settings()
    assert json.loads(settings.read_text())["rag"] is False
    app.close()
