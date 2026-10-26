"""Books split by chapters through `tomd convert`, `tomd batch` and the manifest (ADR-018,
phase 3): folders on disk, parts and anchors in the manifest, removal, deadlines."""

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from tomd import __version__
from tomd.cli import main
from tomd.pipeline import batch as batch_module
from tomd.pipeline.batch import MANIFEST_NAME, BatchOptions, run_batch
from tomd.pipeline.book import INDEX_NAME
from tomd.pipeline.dispatch import convert, remove_output
from tomd.pipeline.manifest import Manifest, Task
from tomd.readers.pdf.ocr import OcrMode

sys.path.insert(0, str(Path(__file__).parent / "writers"))
from test_book_rules import _pdf_book

CHAPTER_FILES = [
    "01-chapter-1-basics.md", "02-chapter-2-routing.md", "03-chapter-3-security.md",
]  # fmt: skip


def test_convert_writes_a_folder_and_cli_prints_its_index(tmp_path: Path, capsys: object) -> None:
    book = _pdf_book(tmp_path / "networks.pdf")
    result = convert(str(book), tmp_path / "out", ocr=OcrMode.NEVER, split="chapters")
    assert result.markdown_path == tmp_path / "out" / "networks" / INDEX_NAME
    assert sorted(p.name for p in result.markdown_path.parent.glob("*.md")) == [
        *CHAPTER_FILES, INDEX_NAME,
    ]  # fmt: skip
    # `none` keeps one file; `auto` leaves a short book whole.
    single = convert(str(book), tmp_path / "one", ocr=OcrMode.NEVER, split="none")
    assert single.markdown_path == tmp_path / "one" / "networks.md"
    whole = convert(str(book), tmp_path / "auto", ocr=OcrMode.NEVER)
    assert whole.markdown_path.name == "networks.md"
    code = main(["convert", str(book), "-o", str(tmp_path / "cli"), "--ocr", "never",
                 "--split", "chapters"])  # fmt: skip
    assert code == 0
    printed = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert printed.endswith(str(Path("networks") / INDEX_NAME))


def test_batch_records_the_parts_of_a_book(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    _pdf_book(folder / "networks.pdf")
    out = tmp_path / "out"
    options = BatchOptions(out_dir=out, ocr="never", split="chapters")
    summary = run_batch([folder], options, progress=lambda line: None)
    assert summary.statuses == {"done": 1}
    manifest = Manifest(out / MANIFEST_NAME)
    (entry,) = manifest.library(__version__)
    assert entry.output == f"networks/{INDEX_NAME}"
    parts = manifest.parts(entry.sha256, __version__)
    assert [name for name, _ in parts] == [INDEX_NAME, *CHAPTER_FILES]
    assert parts[0][1] == "Networks" and parts[2][1] == "Chapter 2 Routing"
    # Anchors per file: chapter 2 starts on its own page, at its own line numbers.
    routing = manifest.anchors(entry.sha256, __version__, part=2)
    assert routing and {page for page, _, _ in routing} <= {4, 5}
    assert manifest.anchors(entry.sha256, __version__, part=0) == []
    manifest.close()
    assert (out / "networks" / "02-chapter-2-routing.md").is_file()
    # Done: a second run converts nothing.
    again = run_batch([folder], options, progress=lambda line: None)
    assert again.planned == 0


def test_removing_a_book_deletes_its_folder_only(tmp_path: Path) -> None:
    out = tmp_path / "out"
    convert(str(_pdf_book(tmp_path / "networks.pdf")), out, ocr=OcrMode.NEVER, split="chapters")
    mine = out / "my-notes"  # a folder of the user's, no index.md: never touched
    mine.mkdir()
    (mine / "a.md").write_text("mine", encoding="utf-8")
    assert remove_output(out, f"my-notes/{INDEX_NAME}", "") == []
    assert remove_output(out, f"../networks/{INDEX_NAME}", "") == []  # outside: refused
    removed = remove_output(out, f"networks/{INDEX_NAME}", "")
    assert removed == [(out / "networks").resolve()] and not (out / "networks").exists()
    assert (mine / "a.md").exists()


def test_pages_extend_a_documents_deadline(tmp_path: Path) -> None:
    manifest = Manifest(tmp_path / MANIFEST_NAME)
    worker = SimpleNamespace(started=100.0, deadline=700.0,
                             conn=SimpleNamespace(recv=lambda: ("pages", 400)))  # fmt: skip
    options = BatchOptions(out_dir=tmp_path, timeout_s=600.0, timeout_per_page_s=3.0)
    outcome = batch_module._receive(
        worker, Task("s", "book.pdf"), manifest, "v", options,  # type: ignore[arg-type]
        lambda *a: tmp_path,
    )  # fmt: skip
    assert outcome is None  # still working
    assert worker.deadline == 100.0 + 600.0 + 400 * 3.0
    manifest.close()


def test_a_schema_2_manifest_gains_parts(tmp_path: Path) -> None:
    path = tmp_path / MANIFEST_NAME
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE anchors (sha256 TEXT NOT NULL, version TEXT NOT NULL, idx INTEGER "
        "NOT NULL, page INTEGER NOT NULL, line INTEGER NOT NULL, bbox TEXT, "
        "PRIMARY KEY (sha256, version, idx));"
        "INSERT INTO anchors VALUES ('s', 'v', 0, 3, 12, NULL);"
    )
    db.commit()
    db.close()
    manifest = Manifest(path)
    assert manifest.anchors("s", "v") == [(3, 12, None)]  # old anchors are part 0
    assert manifest.parts("s", "v") == []
    manifest.close()


def _book_with_extras(path: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle("Networks")
    line = "Packets travel between hosts across links that join routers in a network."
    for n, title in enumerate(("Chapter 1 Basics", "Chapter 2 Routing", "About the Author")):
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(72, A4[1] - 72, title)
        canvas.bookmarkPage(f"p{n}")
        canvas.addOutlineEntry(title, f"p{n}", level=0)
        canvas.setFont("Helvetica", 10)
        for i in range(20):
            canvas.drawString(72, A4[1] - 112 - 14 * i, line)
        canvas.showPage()
    canvas.save()
    return path


def test_rag_mode_leaves_out_what_is_not_content(tmp_path: Path) -> None:
    source = _book_with_extras(tmp_path / "networks.pdf")
    plain = convert(str(source), tmp_path / "plain", ocr=OcrMode.NEVER, split="chapters")
    assert "about-the-author" in " ".join(p.name for p in plain.markdown_path.parent.iterdir())
    rag = convert(str(source), tmp_path / "rag", ocr=OcrMode.NEVER, split="chapters", rag=True)
    names = sorted(p.name for p in rag.markdown_path.parent.glob("*.md"))
    assert names == ["01-chapter-1-basics.md", "02-chapter-2-routing.md", INDEX_NAME]
    codes = [d.code for d in rag.diagnostics]
    assert "doc.section_dropped" in codes
    # The batch and the command line carry the option to the worker.
    out = tmp_path / "batch"
    run_batch([source], BatchOptions(out_dir=out, ocr="never", split="chapters", rag=True),
              progress=lambda line: None)  # fmt: skip
    assert not list((out / "networks").glob("*about-the-author*"))
    assert main(["convert", str(source), "-o", str(tmp_path / "cli"), "--ocr", "never",
                 "--split", "chapters", "--rag"]) == 0  # fmt: skip
    assert not list((tmp_path / "cli" / "networks").glob("*about-the-author*"))
