"""Generate the synthetic EPUB cases of the golden corpus (phase 2).

Each case is written by hand here: its files (OPF, navigation, XHTML, images) and the
Markdown it should become. The expected text therefore comes from the case itself, never
from tomd's output; drafts still start with "# DRAFT" until reviewed. ZIP entries carry a
fixed date, so the files are byte-for-byte reproducible.

    uv run python scripts/make_synthetic_epubs.py            # (re)write all cases
    uv run python scripts/make_synthetic_epubs.py --check    # fail if anything would change
"""

import argparse
import hashlib
import io
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "epub"
DRAFT = "# DRAFT — requiere revisión humana (generado desde la especificación del caso)\n\n"
_DATE = (2000, 1, 1, 0, 0, 0)

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


@dataclass
class Case:
    files: dict[str, bytes]  # path inside the EPUB (under OEBPS/ unless absolute-ish)
    expected: str
    phenomena: list[str]
    known_limits: list[str] = field(default_factory=list)


def xhtml(body: str, title: str = "x", lang: str = "en", encoding: str = "utf-8") -> bytes:
    text = (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
        f' lang="{lang}" xml:lang="{lang}">\n<head><title>{title}</title>'
        '<link rel="stylesheet" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )
    return text.encode(encoding)


def png(width: int, height: int, colour: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def opf(
    title: str,
    lang: str,
    items: list[tuple[str, str, str, str]],  # id, href, media type, properties
    spine: list[tuple[str, bool]],  # idref, linear
    *,
    version: str = "3.0",
    extra_meta: str = "",
    toc: str | None = None,
) -> bytes:
    manifest = "\n".join(
        f'    <item id="{i}" href="{h}" media-type="{m}"'
        + (f' properties="{p}"' if p else "")
        + "/>"
        for i, h, m, p in items
    )
    refs = "\n".join(
        f'    <itemref idref="{i}"' + ("" if linear else ' linear="no"') + "/>"
        for i, linear in spine
    )
    toc_attr = f' toc="{toc}"' if toc else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000000</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">Synthetic Author</dc:creator>
    <dc:language>{lang}</dc:language>
    <dc:rights>Public domain (synthetic test document)</dc:rights>
{extra_meta}  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine{toc_attr}>
{refs}
  </spine>
</package>
""".encode()


def nav(entries: list[tuple[str, str, list[tuple[str, str]]]]) -> bytes:
    """EPUB 3 navigation document: (href, label, [(href, label)...])."""
    items = []
    for href, label, children in entries:
        sub = "".join(f'<li><a href="{h}">{lab}</a></li>' for h, lab in children)
        items.append(
            f'<li><a href="{href}">{label}</a>' + (f"<ol>{sub}</ol>" if sub else "") + "</li>"
        )
    return xhtml(
        f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{"".join(items)}</ol></nav>',
        "Contents",
    )


def ncx(points: list[tuple[str, str, int]]) -> bytes:
    """EPUB 2 NCX: (src, label, depth), nested by depth."""
    out, depth = [], 0
    for index, (src, label, level) in enumerate(points, start=1):
        while depth >= level:
            out.append("</navPoint>")
            depth -= 1
        out.append(
            f'<navPoint id="np{index}" playOrder="{index}">'
            f"<navLabel><text>{label}</text></navLabel>"
            f'<content src="{src}"/>'
        )
        depth = level
    out.extend("</navPoint>" for _ in range(depth))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/>'
        f"<docTitle><text>x</text></docTitle><navMap>{''.join(out)}</navMap></ncx>\n"
    ).encode()


CSS = b"p { text-indent: 1em; } .chapter-title, .subhead { font-weight: bold; }\n"


# --- Cases -------------------------------------------------------------------------------


def case_epub3_notes() -> Case:
    ch1 = xhtml(
        '<section epub:type="chapter"><h1>Chapter One</h1>\n'
        '<p>It was a <em>dark</em> and stormy night.<sup><a href="endnotes.xhtml#note-1" '
        'id="noteref-1" epub:type="noteref">1</a></sup> The rain fell in torrents.</p>\n'
        '<span epub:type="pagebreak" id="page-2" title="2"/>\n'
        "<p>Three things were certain:</p>\n"
        "<ul><li>the wind</li><li>the cold</li><li>the <strong>long</strong> road</li></ul>\n"
        "<blockquote><p>Nothing endures but change.</p></blockquote>\n"
        "</section>",
        "Chapter One",
    )
    ch2 = xhtml(
        '<section epub:type="chapter"><h1>Chapter Two</h1>\n<h2>A section</h2>\n'
        '<p>As told in <a href="chapter1.xhtml">the first chapter</a>, the road was long.'
        '<sup><a href="endnotes.xhtml#note-2" id="noteref-2" epub:type="noteref">2</a></sup> '
        'More at <a href="https://example.org/road">the road site</a>.</p>\n'
        "<table><thead><tr><th>Day</th><th>Miles</th></tr></thead>"
        "<tbody><tr><td>Monday</td><td>12</td></tr><tr><td>Tuesday</td><td>9</td></tr></tbody></table>\n"
        "</section>",
        "Chapter Two",
    )
    notes = xhtml(
        '<section epub:type="endnotes"><h2>Endnotes</h2><ol>\n'
        '<li id="note-1" epub:type="endnote"><p>Bulwer-Lytton opened a novel this way. '
        '<a href="chapter1.xhtml#noteref-1" epub:type="backlink">↩</a></p></li>\n'
        '<li id="note-2" epub:type="endnote"><p>About forty miles in all. '
        '<a href="chapter2.xhtml#noteref-2" epub:type="backlink">↩</a></p></li>\n'
        "</ol></section>",
        "Endnotes",
    )
    files = {
        "OEBPS/content.opf": opf(
            "The Synthetic Notes Book",
            "en",
            [
                ("nav", "toc.xhtml", "application/xhtml+xml", "nav"),
                ("ch1", "chapter1.xhtml", "application/xhtml+xml", ""),
                ("ch2", "chapter2.xhtml", "application/xhtml+xml", ""),
                ("notes", "endnotes.xhtml", "application/xhtml+xml", ""),
                ("css", "style.css", "text/css", ""),
            ],
            [("nav", False), ("ch1", True), ("ch2", True), ("notes", True)],
        ),
        "OEBPS/toc.xhtml": nav(
            [("chapter1.xhtml", "Chapter One", []), ("chapter2.xhtml", "Chapter Two", [])]
        ),
        "OEBPS/chapter1.xhtml": ch1,
        "OEBPS/chapter2.xhtml": ch2,
        "OEBPS/endnotes.xhtml": notes,
        "OEBPS/style.css": CSS,
    }
    expected = """# The Synthetic Notes Book

## Chapter One

It was a *dark* and stormy night.[^1] The rain fell in torrents.

Three things were certain:

- the wind
- the cold
- the **long** road

> Nothing endures but change.

## Chapter Two

### A section

As told in the first chapter, the road was long.[^2] More at [the road site](https://example.org/road).

| Day | Miles |
| --- | --- |
| Monday | 12 |
| Tuesday | 9 |

[^1]: Bulwer-Lytton opened a novel this way.

[^2]: About forty miles in all.
"""
    return Case(
        files, expected, ["epub3", "endnotes_other_file", "lists", "simple_tables", "lang_en"]
    )


def case_epub2_css_headings() -> Case:
    # Latin-1 on purpose: older EPUBs declare it, and the reader must honour it.
    ch1 = xhtml(
        '<div class="pg-boilerplate" id="pg-header"><p>The Project Gutenberg eBook of '
        "Novela de prueba. This eBook is for the use of anyone anywhere.</p></div>\n"
        '<p class="chapter-title" id="cap1">Capítulo I</p>\n'
        "<p>Era una mañana fría de otoño y el camino estaba desierto."
        '<a id="FNanchor_1" href="#Footnote_1" class="fnanchor">[1]</a> Nadie sabía por qué.</p>\n'
        '<div class="subhead">Una sección breve</div>\n'
        "<p>La niña miró el río; ¿quién habría pensado en el invierno?</p>\n"
        '<div class="footnote"><p><a id="Footnote_1" href="#FNanchor_1">[1]</a> '
        "En el original, «desierto» lleva acento.</p></div>",
        "Capítulo I",
        lang="es",
        encoding="iso-8859-1",
    )
    ch2 = xhtml(
        '<p class="chapter-title"><a id="cap2"></a>Capítulo II</p>\n'
        "<p>Al día siguiente llegó la lluvia, y con ella el olvido.</p>",
        "Capítulo II",
        lang="es",
    )
    files = {
        "OEBPS/content.opf": opf(
            "Novela de prueba",
            "es",
            [
                ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
                ("ch1", "cap1.xhtml", "application/xhtml+xml", ""),
                ("ch2", "cap2.xhtml", "application/xhtml+xml", ""),
                ("css", "style.css", "text/css", ""),
            ],
            [("ch1", True), ("ch2", True)],
            version="2.0",
            toc="ncx",
        ),
        "OEBPS/toc.ncx": ncx(
            [("cap1.xhtml#cap1", "Capítulo I", 1), ("cap2.xhtml#cap2", "Capítulo II", 1)]
        ),
        "OEBPS/cap1.xhtml": ch1,
        "OEBPS/cap2.xhtml": ch2,
        "OEBPS/style.css": CSS,
    }
    expected = """# Novela de prueba

## Capítulo I

Era una mañana fría de otoño y el camino estaba desierto.[^1] Nadie sabía por qué.

### Una sección breve

La niña miró el río; ¿quién habría pensado en el invierno?

## Capítulo II

Al día siguiente llegó la lluvia, y con ella el olvido.

[^1]: En el original, «desierto» lleva acento.
"""
    return Case(
        files,
        expected,
        ["epub2", "ncx", "css_headings", "footnotes_same_file", "boilerplate", "latin1", "lang_es"],
    )


def case_images() -> Case:
    figure, ornament, cover = png(100, 60, "red"), png(8, 8, "black"), png(60, 90, "navy")
    chapter = xhtml(
        "<h1>Figures</h1>\n<p>Text before the figure.</p>\n"
        '<figure><img src="images/figure.png" alt="A red rectangle"/>'
        "<figcaption>Figure 1. A red rectangle.</figcaption></figure>\n"
        '<p><img src="images/ornament.png" alt=""/>An ornament opens this paragraph.</p>\n'
        '<p>The missing picture <img src="images/missing.png" alt="lost"/> is gone.</p>',
        "Figures",
    )
    files = {
        "OEBPS/content.opf": opf(
            "Pictures and figures",
            "en",
            [
                ("cover-page", "cover.xhtml", "application/xhtml+xml", ""),
                ("ch1", "chapter.xhtml", "application/xhtml+xml", ""),
                ("nav", "toc.xhtml", "application/xhtml+xml", "nav"),
                ("cover", "images/cover.png", "image/png", "cover-image"),
                ("fig", "images/figure.png", "image/png", ""),
                ("orn", "images/ornament.png", "image/png", ""),
            ],
            [("cover-page", True), ("ch1", True)],
        ),
        "OEBPS/toc.xhtml": nav([("chapter.xhtml", "Figures", [])]),
        "OEBPS/cover.xhtml": xhtml('<div><img src="images/cover.png" alt="Cover"/></div>', "Cover"),
        "OEBPS/chapter.xhtml": chapter,
        "OEBPS/images/cover.png": cover,
        "OEBPS/images/figure.png": figure,
        "OEBPS/images/ornament.png": ornament,
    }
    digest = hashlib.sha256(figure).hexdigest()[:12]
    expected = f"""# Pictures and figures

## Figures

Text before the figure.

![A red rectangle](pictures-and-figures.assets/{digest}.png)

*Figure 1. A red rectangle.*

An ornament opens this paragraph.

The missing picture

is gone.
"""
    return Case(
        files,
        expected,
        ["images", "figure_caption", "cover", "decorative_images", "missing_resource", "lang_en"],
        ["A missing image splits its paragraph in two (images are blocks in the IR)."],
    )


def case_fixed_layout() -> Case:
    pages = [
        xhtml('<div class="page"><h1>Page one</h1><p>The hero wakes up.</p></div>', "1"),
        xhtml('<div class="page"><p>The hero goes outside. The sun is bright.</p></div>', "2"),
    ]
    files = {
        "OEBPS/content.opf": opf(
            "A fixed layout story",
            "en",
            [
                ("p1", "page1.xhtml", "application/xhtml+xml", ""),
                ("p2", "page2.xhtml", "application/xhtml+xml", ""),
                ("nav", "toc.xhtml", "application/xhtml+xml", "nav"),
            ],
            [("p1", True), ("p2", True)],
            extra_meta='    <meta property="rendition:layout">pre-paginated</meta>\n',
        ),
        "OEBPS/toc.xhtml": nav([("page1.xhtml", "Page one", [])]),
        "OEBPS/page1.xhtml": pages[0],
        "OEBPS/page2.xhtml": pages[1],
    }
    expected = """# A fixed layout story

## Page one

The hero wakes up.

The hero goes outside. The sun is bright.
"""
    return Case(files, expected, ["fixed_layout", "lang_en"])


CASES: dict[str, Callable[[], Case]] = {
    "synthetic-epub3-notes": case_epub3_notes,
    "synthetic-epub2-css-headings": case_epub2_css_headings,
    "synthetic-epub-images": case_images,
    "synthetic-epub-fixed-layout": case_fixed_layout,
}


def build_epub(files: dict[str, bytes]) -> bytes:
    """mimetype first and stored (EPUB OCF rule), then the rest in name order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        first = zipfile.ZipInfo("mimetype", _DATE)
        archive.writestr(first, b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        entries = {"META-INF/container.xml": CONTAINER.encode(), **files}
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, _DATE)
            archive.writestr(
                info, entries[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )
    return buffer.getvalue()


def write_case(name: str, check: bool) -> bool:
    case = CASES[name]()
    case_dir = ROOT / name
    limits = "".join(f"\n  - {limit!r}" for limit in case.known_limits) or " []"
    outputs = {
        "input.epub": build_epub(case.files),
        "case.yaml": (
            'source: "Synthetic, generated by scripts/make_synthetic_epubs.py (public domain)"\n'
            f"phenomena: [{', '.join(case.phenomena)}]\n"
            f"known_limits:{limits}\n"
        ).encode(),
    }
    expected = case_dir / "expected.md"
    if not expected.exists() or expected.read_text(encoding="utf-8").startswith("# DRAFT"):
        outputs["expected.md"] = (DRAFT + case.expected).encode()
    changed = False
    for filename, data in outputs.items():
        path = case_dir / filename
        if path.exists() and path.read_bytes() == data:
            continue
        changed = True
        if not check:
            case_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if any file would change")
    args = parser.parse_args(argv)
    changed = [name for name in CASES if write_case(name, args.check)]
    for name in changed:
        print(("would change: " if args.check else "wrote: ") + name, file=sys.stderr)
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
