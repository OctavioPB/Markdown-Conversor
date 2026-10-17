"""EPUB reader: container and package, DRM, and each EPUB-specific mapping rule.

The EPUBs are built with the helpers of scripts/make_synthetic_epubs.py, so every test
states exactly the XHTML it feeds in.
"""

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from tests.builders import bare
from tomd.errors import DrmProtectedError, UnreadableInputError
from tomd.ir import (
    Document,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Link,
    Paragraph,
    Table,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.pipeline.dispatch import read_source
from tomd.readers.epub import package as package_module
from tomd.readers.epub.reader import read_epub
from tomd.transforms import apply_default_transforms

ROOT = Path(__file__).resolve().parents[3]


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_synthetic_epubs", ROOT / "scripts" / "make_synthetic_epubs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_synthetic_epubs"] = module
    spec.loader.exec_module(module)
    return module


GEN = _generator()
XHTML = "application/xhtml+xml"


def book(
    tmp_path: Path,
    chapters: dict[str, str],
    *,
    extra: dict[str, bytes] | None = None,
    nav_entries: list[tuple[str, str, list[tuple[str, str]]]] | None = None,
    version: str = "3.0",
) -> Path:
    """An EPUB with one spine document per chapter (name -> body XHTML)."""
    items = [(f"c{i}", name, XHTML, "") for i, name in enumerate(chapters)]
    files: dict[str, bytes] = {
        f"OEBPS/{name}": GEN.xhtml(body_xhtml) for name, body_xhtml in chapters.items()
    }
    if nav_entries is not None:
        items.append(("nav", "toc.xhtml", XHTML, "nav"))
        files["OEBPS/toc.xhtml"] = GEN.nav(nav_entries)
    files["OEBPS/content.opf"] = GEN.opf(
        "Test Book", "en", items, [(i[0], True) for i in items if i[0] != "nav"], version=version
    )
    files.update(extra or {})
    path = tmp_path / "book.epub"
    path.write_bytes(GEN.build_epub(files))
    return path


def convert(path: Path) -> Document:
    """The reader's IR after the default transforms (what the writer receives), without
    provenance."""
    doc = apply_default_transforms(read_epub(path))
    return Document(doc.meta, bare(doc.blocks), doc.assets, doc.diagnostics)  # type: ignore[arg-type]


def _codes(doc: Document) -> set[str]:
    return {d.code for d in doc.diagnostics}


# --- Container, package, DRM -------------------------------------------------------------


def test_metadata_comes_from_the_opf(tmp_path: Path) -> None:
    doc = read_epub(book(tmp_path, {"a.xhtml": "<p>Text.</p>"}))
    assert doc.meta.title == "Test Book" and doc.meta.source_type == "epub"
    assert doc.meta.authors == ("Synthetic Author",) and doc.meta.language == "en"
    assert doc.meta.license == "Public domain (synthetic test document)"
    assert len(doc.meta.source_sha256 or "") == 64


def test_spine_order_not_zip_order(tmp_path: Path) -> None:
    path = book(tmp_path, {"z.xhtml": "<p>First.</p>", "a.xhtml": "<p>Second.</p>"})
    texts = [b.children for b in convert(path).blocks if isinstance(b, Paragraph)]
    assert texts == [(Text("First."),), (Text("Second."),)]


@pytest.mark.parametrize(
    ("drm_file", "scheme"),
    [
        ("META-INF/rights.xml", "Adobe ADEPT"),
        ("META-INF/sinf.xml", "Apple FairPlay"),
        ("META-INF/license.lcpl", "Readium LCP"),
    ],
)
def test_drm_schemes_are_rejected(tmp_path: Path, drm_file: str, scheme: str) -> None:
    path = book(tmp_path, {"a.xhtml": "<p>x</p>"}, extra={drm_file: b"<x/>"})
    with pytest.raises(DrmProtectedError, match=scheme):
        read_epub(path)


def _encryption(algorithm: str) -> bytes:
    return (
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        'xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
        f'<enc:EncryptionMethod Algorithm="{algorithm}"/></enc:EncryptedData></encryption>'
    ).encode()


def test_encrypted_content_is_drm_but_font_obfuscation_is_not(tmp_path: Path) -> None:
    fonts = _encryption("http://www.idpf.org/2008/embedding")
    path = book(tmp_path, {"a.xhtml": "<p>Readable.</p>"}, extra={"META-INF/encryption.xml": fonts})
    assert convert(path).blocks == (Paragraph((Text("Readable."),)),)
    aes = _encryption("http://www.w3.org/2001/04/xmlenc#aes128-cbc")
    path = book(tmp_path, {"a.xhtml": "<p>x</p>"}, extra={"META-INF/encryption.xml": aes})
    with pytest.raises(DrmProtectedError):
        read_epub(path)


def test_damaged_containers_are_unreadable(tmp_path: Path) -> None:
    not_zip = tmp_path / "not.epub"
    not_zip.write_bytes(b"<html>not a zip</html>")
    with pytest.raises(UnreadableInputError, match="not a valid EPUB"):
        read_epub(not_zip)
    no_container = tmp_path / "bare.epub"
    with zipfile.ZipFile(no_container, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
    with pytest.raises(UnreadableInputError, match=r"container.xml"):
        read_epub(no_container)


def test_oversized_archives_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_module, "_MAX_TOTAL", 100)  # stands in for a zip bomb
    with pytest.raises(UnreadableInputError, match="too large"):
        read_epub(book(tmp_path, {"a.xhtml": "<p>" + "x" * 500 + "</p>"}))


def test_missing_spine_document_is_reported(tmp_path: Path) -> None:
    path = book(tmp_path, {"a.xhtml": "<p>Here.</p>"})
    with zipfile.ZipFile(path) as src:
        entries = {i.filename: src.read(i) for i in src.infolist()}
    entries["OEBPS/content.opf"] = (
        entries["OEBPS/content.opf"]
        .replace(b'<itemref idref="c0"/>', b'<itemref idref="c0"/><itemref idref="ghost"/>')
        .replace(
            b"</manifest>",
            b'<item id="ghost" href="ghost.xhtml" media-type="application/xhtml+xml"/></manifest>',
        )
    )
    with zipfile.ZipFile(path, "w") as out:
        for name, data in entries.items():
            out.writestr(name, data)
    doc = convert(path)
    assert codes.EPUB_MISSING_RESOURCE in _codes(doc)
    assert doc.blocks == (Paragraph((Text("Here."),)),)


def test_declared_encoding_is_honoured(tmp_path: Path) -> None:
    path = book(tmp_path, {"a.xhtml": "<p>x</p>"})
    latin = GEN.xhtml("<p>Año, canción, pingüino.</p>", encoding="iso-8859-1")
    with zipfile.ZipFile(path) as src:
        entries = {i.filename: src.read(i) for i in src.infolist()}
    entries["OEBPS/a.xhtml"] = latin
    with zipfile.ZipFile(path, "w") as out:
        for name, data in entries.items():
            out.writestr(name, data)
    assert convert(path).blocks == (Paragraph((Text("Año, canción, pingüino."),)),)


# --- Notes ---------------------------------------------------------------------------------


def test_epub3_noterefs_link_notes_in_another_file(tmp_path: Path) -> None:
    path = book(
        tmp_path,
        {
            "a.xhtml": '<p>Call.<a href="n.xhtml#n1" epub:type="noteref" id="r1">1</a></p>',
            "n.xhtml": '<aside id="n1" epub:type="footnote"><p>The note. '
            '<a href="a.xhtml#r1" epub:type="backlink">↩</a></p></aside>',
        },
    )
    doc = convert(path)
    assert doc.blocks == (
        Paragraph((Text("Call."), FootnoteRef("1"))),
        FootnoteDef("1", (Paragraph((Text("The note. "),)),)),
    )


def test_a_bracketed_number_link_is_a_note_but_a_letter_needs_a_superscript(
    tmp_path: Path,
) -> None:
    path = book(
        tmp_path,
        {
            "a.xhtml": '<p>One<a href="#f1">[1]</a> and option <a href="#f2">b</a>.</p>'
            '<p id="f1"><a href="#x">[1]</a> Real note.</p>'
            '<p id="f2">Section b of the rules.</p>',
        },
    )
    blocks = convert(path).blocks
    assert blocks[0] == Paragraph((Text("One"), FootnoteRef("1"), Text(" and option b.")))
    assert FootnoteDef("1", (Paragraph((Text(" Real note."),)),)) in blocks
    assert Paragraph((Text("Section b of the rules."),)) in blocks


def test_link_to_a_heading_is_never_a_note(tmp_path: Path) -> None:
    path = book(tmp_path, {"a.xhtml": '<h2 id="s2">2</h2><p>See <a href="#s2">[2]</a>.</p>'})
    assert not any(isinstance(b, FootnoteDef) for b in convert(path).blocks)


# --- Headings ------------------------------------------------------------------------------


def test_toc_targets_and_heading_classes_become_headings(tmp_path: Path) -> None:
    path = book(
        tmp_path,
        {
            "a.xhtml": '<p id="c1">The First Part</p><p class="subhead">A detail</p>'
            '<p class="chapter-title">This paragraph has a heading class but is far too long '
            "to be a heading, because it goes on and on about many things and it keeps going "
            "well beyond the length of any real heading in any real book that we have ever "
            "seen in a library, a shop or a reading list.</p>"
        },
        nav_entries=[("a.xhtml#c1", "The First Part", [])],
    )
    doc = convert(path)
    # Under the title (H1): the TOC entry at depth 1 is a section, the "subhead" class
    # (level 4) closes up below it (M07).
    assert doc.blocks[0] == Heading(2, (Text("The First Part"),))
    assert doc.blocks[1] == Heading(3, (Text("A detail"),))
    assert isinstance(doc.blocks[2], Paragraph)
    assert codes.EPUB_HEADING_INFERRED in _codes(doc)


# --- Links, navigation, boilerplate ---------------------------------------------------------


def test_internal_links_keep_text_external_links_stay(tmp_path: Path) -> None:
    path = book(
        tmp_path,
        {"a.xhtml": '<p><a href="b.xhtml">Back</a> and <a href="https://x.org/">out</a>.</p>'},
    )
    doc = convert(path)
    assert doc.blocks == (
        Paragraph((Text("Back and "), Link((Text("out"),), "https://x.org/"), Text("."))),
    )
    assert codes.EPUB_INTERNAL_LINK_DROPPED in _codes(doc)


def test_in_text_tables_of_contents_are_dropped_but_content_tables_stay(tmp_path: Path) -> None:
    chapters = "".join(
        f'<p class="toc"><a href="c.xhtml#c{i}">Chapter {i}</a></p>' for i in range(4)
    )
    path = book(
        tmp_path,
        {
            "a.xhtml": "<p><b>CONTENTS</b></p><p><br/></p>" + chapters
            + "<table><tr><td>adv.</td><td>adverb</td></tr><tr><td>m.</td><td>masculine</td>"
            "</tr></table><nav epub:type=\"loi\"><ol><li><a href=\"c.xhtml#i1\">A map</a></li>"
            "</ol></nav>",
        },
    )  # fmt: skip
    doc = convert(path)
    assert [type(b) for b in doc.blocks] == [Table]
    assert codes.EPUB_TOC_SKIPPED in _codes(doc)


def test_boilerplate_and_page_markers_are_removed(tmp_path: Path) -> None:
    path = book(
        tmp_path,
        {
            "a.xhtml": '<section class="pg-boilerplate"><p>*** START OF THE EBOOK ***</p></section>'
            '<p>Story<span epub:type="pagebreak" title="12">12</span> goes on.</p>'
        },
    )
    doc = convert(path)
    assert doc.blocks == (Paragraph((Text("Story goes on."),)),)
    assert {codes.EPUB_BOILERPLATE_DROPPED, codes.EPUB_PAGE_MARKERS_DROPPED} <= _codes(doc)


def test_epub_dispatch(tmp_path: Path) -> None:
    doc = read_source(str(book(tmp_path, {"a.xhtml": "<p>Via the pipeline.</p>"})))
    assert doc.meta.source_type == "epub"
