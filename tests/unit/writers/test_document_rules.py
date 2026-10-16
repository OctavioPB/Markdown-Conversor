"""M01-M04 and M22: document-level rules."""

import yaml

from tests.builders import convert, doc, p
from tomd.ir import (
    Code,
    Diagnostic,
    Document,
    DocumentMeta,
    Heading,
    LineBreak,
    ListBlock,
    ListItem,
    Text,
)
from tomd.writers.markdown import render_markdown


def test_M01_utf8_lf_and_single_trailing_newline() -> None:
    text = convert(doc(p("línea uno\r\ncon CRLF"), p("fin\n\n"))).text
    assert "\r" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    text.encode("utf-8")  # must be encodable


def test_M01_empty_document_has_single_trailing_newline() -> None:
    text = convert(doc(title=None)).text
    assert text.endswith("---\n") and not text.endswith("\n\n")


def test_M02_front_matter_order_and_omitted_keys() -> None:
    meta = DocumentMeta(
        title='Teorema "de" Bayes',
        source_type="wikipedia",
        source="https://es.wikipedia.org/wiki/Teorema_de_Bayes",
        source_sha256="9f2c",
        converter_version="0.3.0",
        backend="parsoid",
        language="es",
        authors=("Ana", "Luis"),
        license="CC BY-SA 4.0",
        revision_id=158234411,
    )
    out = render_markdown(Document(meta, (p("x"),)), slug="s").text
    front = out.split("---\n")[1]
    keys = [line.split(":")[0] for line in front.splitlines() if not line.startswith(" ")]
    assert keys == [
        "title",
        "source_type",
        "source",
        "source_sha256",
        "converter_version",
        "backend",
        "language",
        "authors",
        "license",
        "revision_id",
        "diagnostics_count",
    ]
    data = yaml.safe_load(front)
    assert data["title"] == 'Teorema "de" Bayes'
    assert data["authors"] == ["Ana", "Luis"]
    assert data["revision_id"] == 158234411
    assert "source_type: wikipedia" in front and "language: es" in front


def test_M02_missing_values_are_omitted_not_null() -> None:
    out = render_markdown(Document(DocumentMeta(None, "pdf", "a.pdf"), ()), slug="s").text
    assert "null" not in out
    assert yaml.safe_load(out.split("---\n")[1]) == {
        "source_type": "pdf",
        "source": "a.pdf",
        "diagnostics_count": 0,
    }


def test_M02_yaml_reserved_words_and_odd_characters_are_quoted() -> None:
    meta = DocumentMeta(
        "Line\u2028sep\x01\ufeff", "epub", "a\\b: c # d", language="no", backend="Docling"
    )
    out = render_markdown(Document(meta, ()), slug="s").text
    data = yaml.safe_load(out.split("---\n")[1])
    assert data["language"] == "no"  # Norwegian, not boolean False
    assert data["backend"] == "Docling"
    assert data["source"] == "a\\b: c # d"
    assert data["title"] == "Line\u2028sep\x01\ufeff"


def test_M02_diagnostics_count_includes_writer_diagnostics() -> None:
    from tomd.ir import Image

    d = Document(
        DocumentMeta("T", "epub", "x"),
        (Image("missing", "alt"),),
        diagnostics=(Diagnostic("x.y", "info", "pre-existing"),),
    )
    result = render_markdown(d, slug="s")
    assert len(result.diagnostics) == 2
    assert "diagnostics_count: 2" in result.text


def test_M03_one_blank_line_between_blocks() -> None:
    d = doc(
        p("a"),
        Heading(2, (Text("h"),)),
        ListBlock((ListItem((p("i"),)),)),
        p("b"),
    )
    text = convert(d).text
    assert "\n\n\n" not in text
    assert "a\n\n## h\n\n- i\n\nb\n" in text


def test_M04_paragraph_is_one_line() -> None:
    long = " ".join(["palabra"] * 200)
    text = convert(doc(p(long + "\n" + long))).text
    para = [line for line in text.splitlines() if line.startswith("palabra")]
    assert len(para) == 1


def test_M04_only_line_breaks_split_lines() -> None:
    assert convert(doc(p("a", LineBreak(), "b"))).text.endswith("a\\\nb\n")


def test_M22_identical_bytes_for_identical_ir() -> None:
    d = doc(p("a ", Code("x"), " *b*"), ListBlock((ListItem((p("c"),)),), ordered=True))
    outputs = {convert(d).text.encode("utf-8") for _ in range(5)}
    assert len(outputs) == 1


def test_M22_no_absolute_paths_or_timestamps() -> None:
    text = convert(doc(p("x"))).text
    assert "C:\\" not in text and "/home/" not in text
    assert "date" not in text.split("---")[1]


def test_M01_lone_surrogates_are_replaced_with_diagnostic() -> None:
    from tomd.ir import diagnostics as codes

    result = render_markdown(
        Document(DocumentMeta("T", "pdf", "x"), (p("a" + chr(0xD835) + "b"),)), slug="s"
    )
    result.text.encode("utf-8")  # must not raise
    assert chr(0xFFFD) in result.text
    assert [d.code for d in result.diagnostics] == [codes.TEXT_INVALID_UNICODE]
