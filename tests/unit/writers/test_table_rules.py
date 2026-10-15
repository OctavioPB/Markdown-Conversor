"""M17-M19: tables."""

from tests.builders import body, convert, doc, p
from tests.mdparse import inline_texts, parse
from tomd.ir import Cell, Code, Emph, InlineMath, LineBreak, ListBlock, ListItem, Row, Table, Text
from tomd.ir import diagnostics as codes


def _cell(*children: object) -> Cell:
    return Cell((p(*children),))  # type: ignore[arg-type]


def _codes(result: object) -> list[str]:
    return [d.code for d in result.diagnostics]  # type: ignore[attr-defined]


def test_M17_simple_table_is_gfm() -> None:
    t = Table((Row((_cell("a"), _cell("b"))),), (Row((_cell("1"), _cell("x | y"))),))
    result = convert(doc(t))
    assert body(result.text) == "| a | b |\n| --- | --- |\n| 1 | x \\| y |\n"
    assert inline_texts(result.text)[-1] == "x | y"
    assert _codes(result) == []


def test_M17_pipes_in_code_and_math_cells() -> None:
    t = Table((Row((_cell("h"),)),), (Row((_cell(Code("a|b"), " ", InlineMath("|x|")),)),))
    out = convert(doc(t)).text
    assert inline_texts(out)[-1] == "a|b $|x|$"


def test_M17_line_breaks_in_cells_become_br() -> None:
    t = Table((Row((_cell("h"),)),), (Row((_cell("a", LineBreak(), "b"),)),))
    out = convert(doc(t)).text
    assert "| a<br>b |" in out
    assert "table_open" in [tok.type for tok in parse(out)]


def test_M17_missing_header_promotes_first_row() -> None:
    t = Table((), (Row((_cell("a"),)), Row((_cell("b"),))))
    result = convert(doc(t))
    assert body(result.text) == "| a |\n| --- |\n| b |\n"
    assert _codes(result) == [codes.TABLE_HEADER_PROMOTED]


def test_M17_ragged_rows_are_padded() -> None:
    t = Table((Row((_cell("a"), _cell("b"))),), (Row((_cell("1"),)),))
    assert "| 1 |  |" in convert(doc(t)).text


def test_M18_spans_fall_back_to_html() -> None:
    t = Table(
        (Row((Cell((p("a"),), colspan=2),)),),
        (Row((_cell("1 < 2"), _cell(Emph((Text("e"),))))),),
    )
    result = convert(doc(t))
    assert body(result.text) == (
        '<table>\n<tr><th colspan="2">a</th></tr>\n'
        "<tr><td>1 &lt; 2</td><td><em>e</em></td></tr>\n</table>\n"
    )
    assert _codes(result) == [codes.TABLE_HTML_FALLBACK]
    assert [t.type for t in parse(result.text)][-1] == "html_block"


def test_M18_block_content_in_cells_falls_back_to_html() -> None:
    lst = ListBlock((ListItem((p("x"),)), ListItem((p("y"),))))
    t = Table((Row((_cell("h"),)),), (Row((Cell((lst,)),)),))
    result = convert(doc(t))
    assert "<td><ul><li><p>x</p></li><li><p>y</p></li></ul></td>" in result.text
    assert _codes(result) == [codes.TABLE_HTML_FALLBACK]


def test_M18_html_table_has_no_blank_lines() -> None:
    from tomd.ir import CodeBlock

    t = Table((), (Row((Cell((CodeBlock("a\n\nb"),)),)),))
    out = body(convert(doc(t)).text)
    assert "\n\n" not in out.strip("\n")
    assert "a&#10;&#10;b" in out


def test_M19_caption_is_italic_paragraph_before_table() -> None:
    t = Table((Row((_cell("a"),)),), (), caption=(Text("Tabla 1"),))
    assert body(convert(doc(t)).text).startswith("*Tabla 1*\n\n| a |")
