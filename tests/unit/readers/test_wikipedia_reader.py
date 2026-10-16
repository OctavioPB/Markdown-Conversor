"""Wikipedia handlers on small Parsoid-shaped snippets."""

from dataclasses import replace

from tests.builders import bare
from tomd.ir import (
    Code,
    Document,
    Emph,
    FootnoteDef,
    FootnoteRef,
    Heading,
    InlineMath,
    Link,
    MathBlock,
    Paragraph,
    Table,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.readers.wikipedia.reader import WIKI_TEMPLATE_DROPPED, read_parsoid_html

HEAD = (
    '<head><base href="//es.wikipedia.org/wiki/"/>'
    '<link rel="dc:isVersionOf" href="//es.wikipedia.org/wiki/Prueba"/>'
    "<title>Prueba</title></head>"
)


def _doc(body: str) -> Document:
    html = (
        '<!DOCTYPE html><html about="//es.wikipedia.org/wiki/Especial:Redirigir/revision/99">'
        f'{HEAD}<body lang="es" class="parsoid-body">{body}</body></html>'
    )
    doc = read_parsoid_html(html.encode("utf-8"))
    return replace(doc, blocks=bare(doc.blocks))  # type: ignore[arg-type]


def _codes(doc) -> list[str]:  # type: ignore[no-untyped-def]
    return [d.code for d in doc.diagnostics]


def _math(tex: str, block: bool = False) -> str:
    cls = "mwe-math-element-block" if block else "mwe-math-element-inline"
    return (
        f'<span class="mwe-math-element {cls}" typeof="mw:Extension/math" '
        f'data-mw=\'{{"name":"math","attrs":{{}},"body":{{"extsrc":"{tex}"}}}}\'>'
        f'<math alttext="{{\\displaystyle x}}"><annotation encoding="application/x-tex">'
        f'{{\\displaystyle {tex}}}</annotation></math><img alt="fallback"/></span>'
    )


def test_metadata_from_parsoid_head() -> None:
    doc = _doc("<section><p>x</p></section>")
    meta = doc.meta
    assert meta.title == "Prueba"
    assert meta.source == "https://es.wikipedia.org/wiki/Prueba"
    assert meta.revision_id == 99
    assert meta.language == "es"
    assert meta.license == "CC BY-SA 4.0"
    assert meta.backend == "parsoid"
    assert meta.source_type == "wikipedia"
    assert meta.source_sha256 and len(meta.source_sha256) == 64


def test_inline_math_uses_original_tex_and_drops_fallback_image() -> None:
    doc = _doc(f"<p>Sea {_math('x^2')} real.</p>")
    assert doc.blocks == (Paragraph((Text("Sea "), InlineMath("x^2"), Text(" real."))),)
    assert codes.IMAGE_DROPPED not in _codes(doc)


def test_block_math_and_lone_indented_formula_become_math_blocks() -> None:
    doc = _doc(f"<p>{_math('a', block=True)}</p><dl><dd>{_math('b')}</dd></dl>")
    assert [b for b in doc.blocks if isinstance(b, MathBlock)] == [MathBlock("a"), MathBlock("b")]


def test_math_without_tex_is_reported() -> None:
    doc = _doc('<p><span class="mwe-math-element" typeof="mw:Extension/math">x</span></p>')
    assert codes.MATH_UNRECOVERABLE in _codes(doc)


def test_references_become_footnotes_and_empty_section_is_removed() -> None:
    body = (
        '<section><p>Dato<sup class="mw-ref reference" typeof="mw:Extension/ref">'
        '<a href="./Prueba#cite_note-1">[1]</a></sup>.</p></section>'
        '<section><h2>Referencias</h2><div class="listaref"><ol class="mw-references references">'
        '<li id="cite_note-1"><span class="mw-cite-backlink"><a href="./Prueba#cite_ref-1">↑</a>'
        '</span> <span class="mw-reference-text">Fuente <i>A</i>.</span></li></ol></div></section>'
    )
    doc = _doc(body)
    assert doc.blocks == (
        Paragraph((Text("Dato"), FootnoteRef("cite_note-1"), Text("."))),
        FootnoteDef("cite_note-1", (Paragraph((Text("Fuente "), Emph((Text("A"),)), Text("."))),)),
    )


def test_section_with_other_content_keeps_heading() -> None:
    doc = _doc("<section><h2>Historia</h2><p>Texto</p></section>")
    assert isinstance(doc.blocks[0], Heading)


def test_infobox_is_flattened_and_moved_first() -> None:
    body = (
        "<section><p>Intro</p>"
        '<table class="infobox"><tbody>'
        '<tr><th colspan="2">Thomas Bayes</th></tr>'
        "<tr><th>Nacimiento</th><td>1702<br/>Londres</td></tr>"
        "<tr><th>Campo</th><td><ul><li>Estadística</li><li>Teología</li></ul></td></tr>"
        "</tbody></table></section>"
    )
    doc = _doc(body)
    table = doc.blocks[0]
    assert isinstance(table, Table)
    assert len(table.header_rows) == 1 and len(table.body_rows) == 2
    assert all(len(r.cells) == 2 for r in table.body_rows)
    assert doc.blocks[1] == Paragraph((Text("Intro"),))
    assert codes.WIKI_INFOBOX_FLATTENED in _codes(doc)


def test_navigation_and_maintenance_templates_are_dropped_with_diagnostic() -> None:
    body = (
        '<section><div class="navbox"><p>Nav</p></div>'
        '<table class="ambox"><tr><td>Aviso</td></tr></table>'
        '<div class="mw-authority-control">CA</div><p>Texto</p></section>'
    )
    doc = _doc(body)
    assert doc.blocks == (Paragraph((Text("Texto"),)),)
    assert _codes(doc).count(WIKI_TEMPLATE_DROPPED) == 3


def test_figures_keep_caption_and_report_dropped_image() -> None:
    body = (
        '<figure typeof="mw:File/Thumb"><a href="./Archivo:X.jpg"><img src="//x"/></a>'
        "<figcaption>Un retrato</figcaption></figure>"
    )
    doc = _doc(body)
    assert doc.blocks == (Paragraph((Emph((Text("Un retrato"),)),)),)
    assert codes.IMAGE_DROPPED in _codes(doc)


def test_wikilinks_are_absolute_and_red_links_become_text() -> None:
    body = (
        '<p><a rel="mw:WikiLink" href="./Probabilidad" title="Probabilidad">prob</a> y '
        '<a rel="mw:WikiLink" class="new" href="./X?action=edit&amp;redlink=1">roja</a> '
        '<a rel="mw:ExtLink" class="external" href="https://e.org">ext</a></p>'
    )
    (para,) = _doc(body).blocks
    assert isinstance(para, Paragraph)
    assert para.children == (
        Link((Text("prob"),), "https://es.wikipedia.org/wiki/Probabilidad"),
        Text(" y "),
        Text("roja"),
        Text(" "),
        Link((Text("ext"),), "https://e.org"),
    )


def test_citation_is_not_italic_and_superscripts_use_unicode() -> None:
    body = '<p><cite class="citation book">Libro</cite> km<sup>2</sup> <code>x</code></p>'
    (para,) = _doc(body).blocks
    assert isinstance(para, Paragraph)
    assert para.children == (Text("Libro"), Text(" km"), Text("²"), Text(" "), Code("x"))
