"""«Para RAG» (ADR-019): what is left out, what stays, and that every loss is reported."""

from dataclasses import replace

from tomd.ir import (
    Asset,
    Block,
    Document,
    DocumentMeta,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    OutlineEntry,
    Paragraph,
    Provenance,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.transforms.rag import NON_CONTENT_TITLE, drop_non_content

SRC = "book.pdf"


def h(level: int, text: str, page: int = 1) -> Heading:
    return Heading(level, (Text(text),), Provenance(SRC, page))


def p(text: str, page: int = 1, *extra: object) -> Paragraph:
    return Paragraph((Text(text), *extra), Provenance(SRC, page))  # type: ignore[arg-type]


def book(*blocks: Block, outline: tuple[OutlineEntry, ...] = ()) -> Document:
    return Document(DocumentMeta("Book", "pdf", SRC), blocks, outline=outline)


def texts(doc: Document) -> list[str]:
    return [str(b) for b in doc.blocks]


def dropped(doc: Document) -> list[str]:
    return [d.message for d in doc.diagnostics if d.code == codes.DOC_SECTION_DROPPED]


def test_titles_that_are_not_content() -> None:
    out = ["About the Author", "About the Technical Editor", "Acknowledgments", "Contents",
           "Contents at a Glance", "Index", "Índice", "10 References", "A.2 Bibliography",
           "Further Reading", "List of Figures", "Sobre los autores", "Agradecimientos",
           "Praise for Software Architect", "Cover", "Copyright Page", "EULA"]  # fmt: skip
    kept = ["Preface", "Introduction", "Glossary", "Appendix A", "Notes", "My References",
            "References in Java", "Chapter 1 Basics", "Index Structures"]  # fmt: skip
    assert all(NON_CONTENT_TITLE.match(t) for t in out)
    assert not any(NON_CONTENT_TITLE.match(t) for t in kept)


def test_a_section_goes_until_the_next_heading_of_its_level() -> None:
    doc = book(
        h(2, "Preface"), p("Why this book."),
        h(2, "Chapter 1"), p("Real content."),
        h(3, "1.1 Routing"), p("More content."),
        h(3, "Further Reading"), p("Book A."), h(4, "Online"), p("Site B."),
        h(3, "1.2 Switching"), p("Still content."),
        h(2, "About the Author"), p("She lives in Madrid."),
    )  # fmt: skip
    result = drop_non_content(doc)
    kept = " ".join(texts(result))
    for gone in ("Further Reading", "Book A.", "Online", "Site B.", "About the Author", "Madrid"):
        assert gone not in kept
    for stays in ("Preface", "Why this book.", "1.1 Routing", "1.2 Switching", "Still content."):
        assert stays in kept
    assert dropped(result) == [
        "Section «Further Reading» left out (RAG mode).",
        "Section «About the Author» left out (RAG mode).",
    ]


def test_whole_pages_of_top_level_outline_entries_go_mid_page_ones_do_not() -> None:
    doc = book(
        p("Cover art text", 1), p("All rights reserved.", 2),  # no heading of their own
        h(2, "Chapter 1", 3), p("Text of chapter one.", 3),
        h(3, "Further Reading", 4), p("A list.", 4), h(3, "Wrap-up", 4), p("Real end.", 4),
        outline=(
            OutlineEntry("Cover", 1, page=1), OutlineEntry("Copyright Page", 1, page=2),
            OutlineEntry("Chapter 1", 1, page=3),
            OutlineEntry("Further Reading", 2, page=4),  # shares page 4 with "Wrap-up"
            OutlineEntry("Wrap-up", 2, page=4),
        ),
    )  # fmt: skip
    result = drop_non_content(doc)
    kept = " ".join(texts(result))
    assert "Cover art text" not in kept and "All rights reserved." not in kept
    assert "A list." not in kept
    assert "Real end." in kept and "Text of chapter one." in kept  # page 4 is not dropped
    assert [e.title for e in result.outline] == ["Chapter 1", "Wrap-up"]
    assert "Section «Cover» left out (RAG mode)." in dropped(result)


def test_notes_and_images_of_left_out_parts_go_with_them() -> None:
    picture = Asset.from_bytes(b"png", "image/png")
    portrait = Asset.from_bytes(b"jpg", "image/jpeg")
    doc = replace(
        book(
            h(2, "Chapter 1"), p("Claim.", 1, FootnoteRef("1")),
            Image(picture.sha256, "", (), Provenance(SRC, 1)),
            h(2, "About the Author"), p("Bio.", 2, FootnoteRef("2")),
            Image(portrait.sha256, "", (), Provenance(SRC, 2)),
            FootnoteDef("1", (Paragraph((Text("Source."),)),)),
            FootnoteDef("2", (Paragraph((Text("Award."),)),)),
        ),
        assets=(picture, portrait),
    )  # fmt: skip
    result = drop_non_content(doc)
    assert result.assets == (picture,)
    notes = [b for b in result.blocks if isinstance(b, FootnoteDef)]
    assert [n.key for n in notes] == ["1"] and "Source." in str(notes[0])
    assert not [d for d in result.diagnostics if d.code.startswith("footnote.")]


def test_a_document_with_nothing_to_leave_out_is_unchanged() -> None:
    doc = book(h(2, "Chapter 1"), p("Content."), outline=(OutlineEntry("Chapter 1", 1, page=1),))
    assert drop_non_content(doc) == doc
