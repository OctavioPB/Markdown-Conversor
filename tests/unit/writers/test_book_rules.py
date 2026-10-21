"""MARKDOWN_SPEC M23-M26: a book split by chapters on disk (ADR-018)."""

import io
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from tomd.ir import (
    Document,
    DocumentMeta,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Paragraph,
    Provenance,
    Text,
)
from tomd.pipeline.book import ASSETS_DIR, INDEX_NAME, chapter_names, render_book, write_book
from tomd.pipeline.dispatch import prepare_document, read_source
from tomd.readers.pdf.ocr import OcrMode
from tomd.transforms.split import Chapter, SplitBook, split_document

LINE = "Packets travel between hosts across links that join routers in a network of networks."
CHAPTERS = ("Chapter 1 Basics", "Chapter 2 Routing", "Chapter 3 Security")


def _pdf_book(path: Path) -> Path:
    """Title page, three chapters of two pages each, bookmarks for the chapters and an
    image in chapter 2."""
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setTitle("Networks")
    canvas.setFont("Helvetica-Bold", 24)
    canvas.drawString(72, 700, "Networks")
    canvas.showPage()
    top = A4[1] - 72
    for n, title in enumerate(CHAPTERS, start=1):
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(72, top, title)
        canvas.bookmarkPage(f"c{n}")
        canvas.addOutlineEntry(title, f"c{n}", level=0)
        for page in range(2):
            y = top - 40 if page == 0 else top
            canvas.setFont("Helvetica", 10)
            for _ in range(24):
                canvas.drawString(72, y, LINE)
                y -= 14
            if n == 2 and page == 0:
                image = PILImage.new("RGB", (120, 80), (40, 90, 160))
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 72, y - 100,
                                 120, 80)  # fmt: skip
            canvas.showPage()
    canvas.save()
    return path


def _split(doc: Document) -> SplitBook:
    _, prepared = prepare_document(doc)
    book, _ = split_document(prepared, mode="chapters")
    assert book is not None
    return book


def _rendered(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    doc = read_source(str(_pdf_book(tmp_path / "networks.pdf")), ocr=OcrMode.NEVER)
    index = write_book(tmp_path / "out", "networks", render_book(_split(doc)))
    files = {p.name: p.read_text(encoding="utf-8") for p in index.parent.glob("*.md")}
    return index, files


def test_M23_book_folder_layout(tmp_path: Path) -> None:
    index, files = _rendered(tmp_path)
    assert index == tmp_path / "out" / "networks" / INDEX_NAME
    assert sorted(files) == [
        "01-chapter-1-basics.md", "02-chapter-2-routing.md", "03-chapter-3-security.md",
        INDEX_NAME,
    ]  # fmt: skip
    assert len(list((index.parent / ASSETS_DIR).iterdir())) == 1
    # More than 99 chapters: three digits, still in reading order.
    meta = DocumentMeta("Big", "pdf", "big.pdf")
    many = SplitBook(
        Document(meta, ()),
        tuple(
            Chapter(n, Document(DocumentMeta(f"Part {n}", "pdf", "big.pdf"), ()))
            for n in range(1, 101)
        ),
    )
    names = chapter_names(many)
    assert names[0] == "001-part-1.md" and names[-1] == "100-part-100.md"


def test_M23_stale_chapter_files_are_removed_and_nothing_else(tmp_path: Path) -> None:
    index, _ = _rendered(tmp_path)
    folder = index.parent
    (folder / "04-old-chapter.md").write_text("old", encoding="utf-8")
    (folder / "notes-of-mine.md").write_text("mine", encoding="utf-8")
    doc = read_source(str(tmp_path / "networks.pdf"), ocr=OcrMode.NEVER)
    write_book(tmp_path / "out", "networks", render_book(_split(doc)))
    assert not (folder / "04-old-chapter.md").exists()
    assert (folder / "notes-of-mine.md").exists()


def test_M24_each_chapter_is_a_document_of_its_own(tmp_path: Path) -> None:
    _, files = _rendered(tmp_path)
    routing = files["02-chapter-2-routing.md"]
    assert routing.count("\n# ") == 1 and "\n# Chapter 2 Routing\n" in routing
    assert "](assets/" in routing  # the image, in the shared folder
    assert all(text.endswith("\n") and not text.endswith("\n\n") for text in files.values())


def test_M24_footnotes_are_numbered_within_each_chapter() -> None:
    meta = DocumentMeta("Notes", "pdf", "n.pdf")
    text = "A chapter long enough to stand as a file of its own in the book folder here. " * 2

    def para(page: int, *refs: str) -> Paragraph:
        calls = tuple(FootnoteRef(r) for r in refs)
        return Paragraph((Text(text), *calls), Provenance("n.pdf", page))

    blocks = (
        Heading(2, (Text("Chapter 1"),), Provenance("n.pdf", 1)), para(1, "1"),
        Heading(2, (Text("Chapter 2"),), Provenance("n.pdf", 2)), para(2, "2"),
        FootnoteDef("1", (Paragraph((Text("First."),)),)),
        FootnoteDef("2", (Paragraph((Text("Second."),)),)),
    )  # fmt: skip
    files = dict(render_book(_split(Document(meta, blocks))).files)
    assert files["01-chapter-1.md"].endswith("[^1]: First.\n")
    assert files["02-chapter-2.md"].endswith("[^1]: Second.\n")  # 1..n again


def test_M25_front_matter_of_chapters_and_index(tmp_path: Path) -> None:
    _, files = _rendered(tmp_path)
    head = files["01-chapter-1-basics.md"].split("---\n")[1].splitlines()
    assert head[:4] == [
        'title: "Chapter 1 Basics"', 'book: "Networks"', "chapter: 1", "chapters: 3",
    ]  # fmt: skip
    index_head = files[INDEX_NAME].split("---\n")[1].splitlines()
    assert index_head[:2] == ['title: "Networks"', "chapters: 3"]
    source = [ln for ln in head if ln.startswith("source_sha256:")]
    assert source and source[0] in index_head  # every part names the same source


def test_M25_front_part_has_no_chapter_number() -> None:
    meta = DocumentMeta("Libro", "pdf", "libro.pdf", language="es")
    long = "Un texto de prólogo lo bastante largo para tener un archivo propio en el libro. " * 3
    blocks = (
        Heading(2, (Text("Prólogo"),), Provenance("libro.pdf", 1)),
        Paragraph((Text(long),), Provenance("libro.pdf", 1)),
        Heading(2, (Text("Capítulo 1"),), Provenance("libro.pdf", 2)),
        Paragraph((Text(long),), Provenance("libro.pdf", 2)),
        Heading(2, (Text("Capítulo 2"),), Provenance("libro.pdf", 3)),
        Paragraph((Text(long),), Provenance("libro.pdf", 3)),
    )
    rendered = render_book(_split(Document(meta, blocks)))
    name, text = rendered.files[1]
    assert name == "00-preliminares.md"
    assert 'title: "Preliminares"\nbook: "Libro"\nchapters: 2\n' in text


def test_M26_index_lists_the_chapters_in_order(tmp_path: Path) -> None:
    _, files = _rendered(tmp_path)
    body = files[INDEX_NAME].split("---\n", 2)[2]
    assert body.startswith("\n# Networks\n")
    assert "- [Chapter 1 Basics](01-chapter-1-basics.md)\n" in body
    links = [ln for ln in body.splitlines() if ln.startswith("- [")]
    assert [ln.split("](")[1] for ln in links] == [
        "01-chapter-1-basics.md)", "02-chapter-2-routing.md)", "03-chapter-3-security.md)",
    ]  # fmt: skip


def test_book_output_is_deterministic(tmp_path: Path) -> None:
    doc = read_source(str(_pdf_book(tmp_path / "networks.pdf")), ocr=OcrMode.NEVER)
    assert render_book(_split(doc)) == render_book(_split(doc))
