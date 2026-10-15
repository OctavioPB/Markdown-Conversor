"""M05-M08: headings."""

from tests.builders import body, convert, doc, p
from tests.mdparse import inline_texts, parse
from tomd.ir import Code, Emph, Heading, LineBreak, Strong, Text


def _heading_levels(markdown: str) -> list[int]:
    return [int(t.tag[1]) for t in parse(markdown) if t.type == "heading_open"]


def test_M05_atx_only() -> None:
    text = convert(doc(Heading(2, (Text("Sección"),)), p("x"))).text
    assert "## Sección\n" in text
    assert "\n---\n\n#" in text
    assert "===" not in text and "\n--\n" not in text


def test_M05_trailing_hash_is_not_a_closing_sequence() -> None:
    text = convert(doc(Heading(2, (Text("C #"),)))).text
    assert inline_texts(text)[-1] == "C #"


def test_M06_exactly_one_h1_equal_to_title() -> None:
    d = doc(Heading(1, (Text("Capítulo 1"),)), p("a"), Heading(1, (Text("Capítulo 2"),)))
    text = convert(d).text
    assert _heading_levels(text) == [1, 2, 2]
    assert inline_texts(text)[0] == "Title"
    assert 'title: "Title"' in text


def test_M06_h1_repeating_title_is_merged() -> None:
    text = convert(doc(Heading(1, (Text("Title"),)), Heading(2, (Text("S"),)))).text
    assert _heading_levels(text) == [1, 2]


def test_M06_first_h1_becomes_title_when_missing() -> None:
    text = convert(doc(Heading(1, (Text("Libro"),)), p("x"), title=None)).text
    assert 'title: "Libro"' in text
    assert _heading_levels(text) == [1]


def test_M07_no_skipped_levels() -> None:
    d = doc(
        Heading(2, (Text("a"),)),
        Heading(4, (Text("b"),)),
        Heading(4, (Text("c"),)),
        Heading(3, (Text("d"),)),
        Heading(6, (Text("e"),)),
    )
    assert _heading_levels(convert(d).text) == [1, 2, 3, 3, 3, 4]


def test_M08_no_emphasis_in_headings() -> None:
    h = Heading(2, (Text("Uno "), Emph((Text("dos"),)), Strong((Text("tres"),)), LineBreak()))
    text = body(convert(doc(h, Heading(2, (Text("x"), Code("y"))))).text)
    assert text.startswith("## Uno dostres\n")
    assert "*" not in text and "<em>" not in text
