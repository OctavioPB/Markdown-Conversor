"""Draft expected.md for Wikipedia corpus cases with Pandoc as an independent oracle
(ADR-015). Never imports tomd's reader or writer: the policies of ADR-006/ADR-013 and the
writer dialect (docs/MARKDOWN_SPEC.md) are re-applied here on the HTML, Pandoc converts
it, and a few mechanical rewrites bring Pandoc's Markdown to the dialect.

The result starts with "# DRAFT" and must be reviewed by a human before it counts.

    uv run python scripts/draft_wiki_expected.py                  # cases without expected.md
    uv run python scripts/draft_wiki_expected.py --case es-teorema-de-bayes --force
"""

import argparse
import html as html_lib
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import pypandoc
from lxml import etree, html

ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "wikipedia"
DRAFT = "# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)\n\n"

# Template classes removed by the Wikipedia policy (navigation and maintenance).
DROP_CLASSES = {
    "navbox", "vertical-navbox", "navbox-styles", "mw-authority-control", "ambox", "ombox",
    "tmbox", "mbox-small", "metadata", "sistersitebox", "side-box", "mw-editsection", "toc",
    "mw-empty-elt", "sidebar", "navbar", "shortdescription", "Inline-Template",
}  # fmt: skip
SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
PANDOC_FORMAT = (
    "commonmark_x-attributes-bracketed_spans-fenced_divs-raw_attribute-implicit_figures"
    "-smart-superscript-subscript-strikeout-task_lists-definition_lists-raw_html"
)
REF_MARK = "QQREF{}QQ"
REF_PATTERN = re.compile(r"QQREF(\d+)QQ")
TABLE_MARK = "QQTABLE{}QQ"
TABLE_PATTERN = re.compile(r"QQTABLE(\d+)QQ")
HEADINGS = ("h2", "h3", "h4", "h5", "h6")
BLOCK_IN_CELL = ("p", "ul", "ol", "div", "table", "dl", "pre", "blockquote")
INLINE_TAGS = {"i": "em", "em": "em", "b": "strong", "strong": "strong", "code": "code"}

type Element = etree._Element


@dataclass
class Prepared:
    title: str
    body: str
    definitions: list[str] = field(default_factory=list)  # by reference index
    tables: list[str] = field(default_factory=list)  # minimal HTML, by table index


def classes(el: Element) -> set[str]:
    return set((el.get("class") or "").split())


def tag(el: Element) -> str:
    return el.tag.lower() if isinstance(el.tag, str) else ""


def text_of(el: Element) -> str:
    return " ".join("".join(str(t) for t in el.itertext()).split())


def parent_of(el: Element) -> Element:
    parent = el.getparent()
    assert parent is not None
    return parent


def remove(el: Element) -> None:
    """Remove an element but keep its tail text."""
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def replace_with(old: Element, new: Element) -> None:
    new.tail = old.tail
    parent_of(old).replace(old, new)


def to_md(fragment: str) -> str:
    out = pypandoc.convert_text(fragment, PANDOC_FORMAT, format="html", extra_args=["--wrap=none"])
    return str(out).replace("\r\n", "\n")  # Pandoc writes CRLF on Windows


def prepare(data: bytes) -> Prepared:
    root = html.document_fromstring(data)
    head = root.find("head")
    body = root.find("body")
    assert body is not None
    title_el = head.find("title") if head is not None else None
    title = text_of(title_el) if title_el is not None else ""
    base = "https://en.wikipedia.org/wiki/"
    if head is not None:
        for b in head.iter("base"):
            href = b.get("href") or ""
            base = "https:" + href if href.startswith("//") else href or base

    for el in list(body.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        if tag(el) in ("style", "link", "meta", "script") or classes(el) & DROP_CLASSES:
            remove(el)

    _math(body)
    for el in list(body.iter()):
        hidden = re.search(r"display\s*:\s*none", el.get("style") or "", re.I) is not None
        if isinstance(el.tag, str) and el.getparent() is not None and hidden and tag(el) != "math":
            remove(el)

    _infoboxes(body)
    _figures(body)
    _links(body, base)
    for cite in [e for e in body.iter("cite") if "citation" in classes(e)]:
        cite.tag = "span"
    _scripts(body)
    _code(body)
    prepared = Prepared(title, "")
    _references(body, prepared)  # last: definitions need absolute links already
    _tables(body, prepared)
    prepared.body = etree.tostring(body, encoding="unicode", method="html")
    return prepared


def _math(body: Element) -> None:
    """Keep the MathML (Pandoc reads its TeX annotation) and drop the fallback image. The
    TeX is the original wikitext source (data-mw extsrc), as in the Wikipedia policy."""
    for span in [e for e in body.iter() if "mwe-math-element" in classes(e)]:
        math = next((m for m in span.iter() if tag(m) == "math"), None)
        if math is None:
            continue
        if "mwe-math-element-block" in classes(span):
            math.set("display", "block")
        try:
            body_mw = json.loads(span.get("data-mw") or "{}").get("body", {})
            source = body_mw.get("extsrc") if isinstance(body_mw, dict) else None
        except (ValueError, AttributeError):
            source = None
        annotation = next((a for a in math.iter() if tag(a) == "annotation"), None)
        if isinstance(source, str) and source.strip() and annotation is not None:
            annotation.text = source
        replace_with(span, math)


def _infoboxes(body: Element) -> None:
    """Infobox -> two-column table right after the title (ADR-006)."""
    for box in [t for t in body.iter("table") if "infobox" in classes(t)]:
        rows: list[tuple[str, str]] = []
        caption = box.find("caption")
        head_text = text_of(caption) if caption is not None else ""
        for tr in box.iter("tr"):
            cells = [c for c in tr if tag(c) in ("th", "td")]
            if not cells:
                continue
            if len(cells) >= 2:
                rows.append((_cell_html(cells[0]), " ".join(_cell_html(c) for c in cells[1:])))
            elif text_of(cells[0]):
                if not head_text and not rows:
                    head_text = text_of(cells[0])
                elif rows and not rows[-1][1] and tag(cells[0]) == "td":
                    rows[-1] = (rows[-1][0], _cell_html(cells[0]))
                else:
                    rows.append((_cell_html(cells[0]), ""))
        table = (
            f"<table><thead><tr><th>{html_lib.escape(head_text)}</th><th></th></tr></thead><tbody>"
        )
        table += "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows) + "</tbody></table>"
        new = html.fragment_fromstring(table)
        new.set("data-infobox", "1")
        remove(box)
        body.insert(0, new)


def _cell_html(cell: Element) -> str:
    inner = (cell.text or "") + "".join(
        etree.tostring(c, encoding="unicode", method="html") for c in cell
    )
    inner = re.sub(r"</?(ul|ol|div|p)[^>]*>", " ", inner)
    inner = re.sub(r"<li[^>]*>", "", inner).replace("</li>", "<br>")
    return re.sub(r"(<br>\s*)+$", "", inner.strip())


def _figures(body: Element) -> None:
    """Figures -> italic caption paragraph (images are not downloaded); inline files out."""
    for fig in [e for e in body.iter() if tag(e) == "figure"]:
        cap = fig.find("figcaption")
        if cap is None or not text_of(cap):
            remove(fig)
            continue
        for i in list(cap.iter("i")):
            i.tag = "span"  # emphasis inside an italic caption is unwrapped
        new = etree.Element("p")
        em = etree.SubElement(new, "em")
        em.text = cap.text
        for child in list(cap):
            em.append(child)
        replace_with(fig, new)
    for el in [
        e
        for e in body.iter()
        if any(t.startswith("mw:File") for t in (e.get("typeof") or "").split())
    ]:
        remove(el)


def _links(body: Element, base: str) -> None:
    """Absolute URLs, no titles; red links (class new) become plain text."""
    for a in list(body.iter("a")):
        href = a.get("href")
        if "new" in classes(a) or not href:
            a.tag = "span"
            a.attrib.clear()
            continue
        if href.startswith("//"):
            href = "https:" + href
        a.attrib.clear()
        a.set("href", urllib.parse.urljoin(base, href))


def _scripts(body: Element) -> None:
    """<sup>/<sub>: Unicode superscripts/subscripts when every character has one."""
    for el in [e for e in body.iter() if tag(e) in ("sup", "sub")]:
        if "mw:Extension/ref" in (el.get("typeof") or "").split():
            continue
        text = text_of(el)
        mapped = text.translate(SUPERSCRIPT if tag(el) == "sup" else SUBSCRIPT)
        tail = el.tail
        if text and all(ord(c) > 127 or c.isspace() for c in mapped):
            el.clear()  # lxml's clear() also drops the tail
            el.text = mapped
        el.tag = "span"
        el.tail = tail


def _code(body: Element) -> None:
    for div in [e for e in body.iter("div") if "mw-highlight" in classes(e)]:
        m = re.search(r"mw-highlight-lang-([\w+#.-]+)", div.get("class") or "")
        pre = next(div.iter("pre"), None)
        if pre is None:
            continue
        code = "".join(str(t) for t in pre.itertext())
        lang = f' class="language-{m.group(1)}"' if m else ""
        replace_with(
            div,
            html.fragment_fromstring(
                f"<pre><code{lang}>{html_lib.escape(code, quote=False)}</code></pre>"
            ),
        )


def _references(body: Element, out: Prepared) -> None:
    keys: list[str] = []
    # Exact token: "mw:Extension/references" (the list itself) must not match.
    for sup in [e for e in body.iter() if "mw:Extension/ref" in (e.get("typeof") or "").split()]:
        a = next((x for x in sup.iter("a") if "#" in (x.get("href") or "")), None)
        marker = ""
        if a is not None:
            key = urllib.parse.unquote((a.get("href") or "").split("#", 1)[1])
            if key not in keys:
                keys.append(key)
            marker = REF_MARK.format(keys.index(key))
        tail = sup.tail
        sup.clear()  # lxml's clear() also drops the tail text
        sup.tag = "span"
        sup.text = marker
        sup.tail = tail
    definitions: dict[str, str] = {}
    for ol in [e for e in body.iter("ol") if "mw-references" in classes(e)]:
        for li in ol:
            text = next((c for c in li.iter() if "mw-reference-text" in classes(c)), None)
            li_id = li.get("id")
            if li_id and text is not None:
                definitions[li_id] = etree.tostring(text, encoding="unicode", method="html")
        remove(ol)
    for section in list(body.iter("section")):
        content = [c for c in section if tag(c) not in HEADINGS]
        has_heading = any(tag(c) in HEADINGS for c in section)
        if has_heading and not any(
            text_of(c) or tag(c) in ("table", "math", "pre") for c in content
        ):
            remove(section)  # emptied by moving its notes to the end
    out.definitions = [definitions.get(k, "") for k in keys]


def _tables(body: Element, out: Prepared) -> None:
    """Tables the dialect cannot express as GFM become minimal HTML (M18); simple ones go
    to Pandoc as pipe tables, with the first row as header if there is none (M17)."""
    for table in [t for t in body.iter("table")]:
        if table.getparent() is None or any(tag(a) == "table" for a in table.iterancestors()):
            continue
        rows = [tr for tr in table.iter("tr") if _owner(tr) is table]
        cells = [c for tr in rows for c in tr if tag(c) in ("th", "td")]
        spans = any(int(c.get("rowspan") or 1) > 1 or int(c.get("colspan") or 1) > 1 for c in cells)
        blocks = any(tag(d) in BLOCK_IN_CELL for c in cells for d in c.iter() if d is not c)
        leading = 0  # header rows: leading rows made only of <th> (or the <thead>)
        while leading < len(rows) and all(
            tag(c) == "th" for c in rows[leading] if tag(c) in ("th", "td")
        ):
            leading += 1
        if not rows:
            remove(table)
            continue
        if not spans and not blocks and leading <= 1:
            out.tables.append(_gfm_table(rows))  # M17 (first row promoted if no header)
        else:
            out.tables.append(_minimal_table(table, rows))  # M18
        placeholder = etree.Element("p")
        placeholder.text = TABLE_MARK.format(len(out.tables) - 1)
        caption = table.find("caption")
        if caption is not None and text_of(caption):
            cap = etree.Element("p")
            em = etree.SubElement(cap, "em")
            em.text = text_of(caption)
            parent_of(table).insert(parent_of(table).index(table), cap)
        replace_with(table, placeholder)


CELL_SEPARATOR = "QQCELLQQ"


def _gfm_table(rows: list[Element]) -> str:
    """Pipe table; every cell converted by Pandoc in one batch, line breaks as <br>."""
    grid = [[c for c in tr if tag(c) in ("th", "td")] for tr in rows]
    fragments = [
        (c.text or "") + "".join(etree.tostring(x, encoding="unicode", method="html") for x in c)
        for row in grid
        for c in row
    ]
    joined = f"<p>{CELL_SEPARATOR}</p>".join(f"<div>{f}</div>" for f in fragments)
    converted = to_md(joined).split(CELL_SEPARATOR)
    texts = iter(_cell_md(text) for text in converted)
    md_rows = [[next(texts) for _ in row] for row in grid]
    width = max(len(r) for r in md_rows)
    md_rows = [r + [""] * (width - len(r)) for r in md_rows]
    lines = ["| " + " | ".join(md_rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in md_rows[1:]]
    return "\n".join(lines)


def _cell_md(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\\\n", "<br>", text)  # hard line breaks
    text = re.sub(r"\n\s*\n", "<br>", text)
    text = " ".join(text.split("\n"))
    return re.sub(r"(?<!\\)\|", r"\\|", " ".join(text.split(" ")).strip())


def _owner(tr: Element) -> Element | None:
    return next((a for a in tr.iterancestors() if tag(a) == "table"), None)


def _minimal_table(table: Element, rows: list[Element]) -> str:
    lines = ["<table>"]
    for tr in rows:
        cells = []
        for c in tr:
            if tag(c) not in ("th", "td"):
                continue
            attrs = ""
            if int(c.get("rowspan") or 1) > 1:
                attrs += f' rowspan="{c.get("rowspan")}"'
            if int(c.get("colspan") or 1) > 1:
                attrs += f' colspan="{c.get("colspan")}"'
            cells.append(f"<{tag(c)}{attrs}>{_inline_html(c)}</{tag(c)}>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _inline_html(el: Element) -> str:
    """Cell content as minimal HTML: text, emphasis, code, links and line breaks only."""
    html_text = _inline_parts(el)
    html_text = re.sub(r"[ \t\n\r\f]+", " ", html_text).strip()  # ASCII whitespace only
    return re.sub(r"(\s*<br>)+$", "", html_text)  # a trailing line break carries nothing


def _inline_parts(el: Element) -> str:
    out = [html_lib.escape(el.text or "", quote=False)]
    for child in el:
        t = tag(child)
        if t == "math":  # formulas keep their TeX, as in the rest of the document
            annotation = next((a for a in child.iter() if tag(a) == "annotation"), None)
            tex = text_of(annotation) if annotation is not None else text_of(child)
            out.append(html_lib.escape(f"${tex}$", quote=False))
            out.append(html_lib.escape(child.tail or "", quote=False))
            continue
        if t == "table":  # a table nested in a cell keeps its structure
            nested_rows = [tr for tr in child.iter("tr") if _owner(tr) is child]
            out.append(_minimal_table(child, nested_rows))
            out.append(html_lib.escape(child.tail or "", quote=False))
            continue
        inner = _inline_parts(child)
        if t in INLINE_TAGS:
            out.append(f"<{INLINE_TAGS[t]}>{inner}</{INLINE_TAGS[t]}>")
        elif t == "a" and child.get("href"):
            out.append(f'<a href="{html_lib.escape(child.get("href") or "")}">{inner}</a>')
        elif t == "br":
            out.append("<br>")
        elif t in ("p", "li", "div"):
            out.append(f"<p>{inner}</p>")
        elif t in ("ul", "ol"):
            out.append(
                f"<{t}>" + "".join(f"<li>{_inline_parts(li)}</li>" for li in child) + f"</{t}>"
            )
        else:
            out.append(inner)
        out.append(html_lib.escape(child.tail or "", quote=False))
    return "".join(out)


def _display_math(block: str) -> str:
    """A paragraph (possibly quoted) that is a single formula becomes a display block."""
    lines = block.split("\n")
    quoted = all(line.startswith(">") for line in lines)
    body = "\n".join(re.sub(r"^> ?", "", line) for line in lines) if quoted else block
    m = re.fullmatch(r"\${1,2}([^$]+)\${1,2}", body.strip())
    if not m:
        return block
    out = "$$\n" + m.group(1).strip("\n ") + "\n$$"
    if quoted:
        out = "\n".join("> " + line if line else ">" for line in out.split("\n"))
    return out


def to_dialect(markdown: str, prepared: Prepared) -> str:
    md = re.sub(r"\{\\displaystyle\s*(.*?)\}(?=\$)", r"\1", markdown)
    # Display math: "$$x$$" on its own line -> fenced form (M15); a paragraph that is
    # only one inline formula is displayed math too.
    # Display formulas inside a paragraph split it (Pandoc writes them as "$$x$$" inline).
    previous = None
    while previous != md:  # until no formula shares its line with text
        previous = md
        md = re.sub(r"(?m)^(?!>)(.*\S)[ ]*(\$\$[^$]+\$\$)", r"\1\n\n\2", md)  # text before
        md = re.sub(r"(?m)^(?!>)(\$\$[^$]+\$\$)[ ]*(\S.*)$", r"\1\n\n\2", md)  # text after
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = "\n\n".join(_display_math(block) for block in md.split("\n\n"))
    # Whitespace inside inline TeX is not significant; collapse it as the dialect does.
    md = re.sub(r"(?<!\$)\$([^$]+)\$(?!\$)", lambda m: "$" + " ".join(m.group(1).split()) + "$", md)
    # M11: URLs with parentheses go between angle brackets.
    md = re.sub(r"\]\((https?://[^\s()]*(?:\([^\s()]*\)[^\s()]*)+)\)", r"](<\1>)", md)
    # Pandoc layout quirks: trailing spaces, padded list markers, "``` lang" fences.
    md = re.sub(r"(?m)^(`{3,}) (\w)", r"\1\2", md)
    md = re.sub(r"(?m)[ \t]+$", "", md)
    md = re.sub(r"(?m)^(\s*)([-*]|\d+\.)\s{2,}(?=\S)", r"\1\2 ", md)
    md = TABLE_PATTERN.sub(lambda m: prepared.tables[int(m.group(1))], md)
    numbers: dict[int, int] = {}

    def ref(m: re.Match[str]) -> str:
        index = int(m.group(1))
        if not prepared.definitions[index]:
            return ""
        numbers.setdefault(index, len(numbers) + 1)
        return f"[^{numbers[index]}]"

    md = REF_PATTERN.sub(ref, md)
    notes = []
    i = 0
    while i < len(numbers):  # notes cited inside notes are numbered after the body
        index = next(k for k, v in numbers.items() if v == i + 1)
        text = " ".join(to_md(prepared.definitions[index]).split())
        notes.append(f"[^{i + 1}]: {REF_PATTERN.sub(ref, text)}")
        i += 1
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md + ("\n\n" + "\n\n".join(notes) if notes else "") + "\n"


def draft(case_dir: Path) -> str:
    prepared = prepare((case_dir / "input.html").read_bytes())
    return DRAFT + f"# {prepared.title}\n\n" + to_dialect(to_md(prepared.body), prepared)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case")
    parser.add_argument("--force", action="store_true", help="overwrite existing DRAFT files")
    args = parser.parse_args(argv)
    for case_dir in sorted(p for p in ROOT.iterdir() if (p / "input.html").is_file()):
        if args.case and case_dir.name != args.case:
            continue
        target = case_dir / "expected.md"
        if target.exists():
            if not target.read_text(encoding="utf-8").startswith("# DRAFT"):
                print(
                    f"skip {case_dir.name}: reviewed expected.md is never touched", file=sys.stderr
                )
                continue
            if not args.force:
                print(f"skip {case_dir.name}: draft exists (use --force)", file=sys.stderr)
                continue
        target.write_text(draft(case_dir), encoding="utf-8", newline="\n")
        print(f"drafted {case_dir.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
