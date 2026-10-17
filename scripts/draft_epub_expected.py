"""Draft expected.md for real EPUB corpus cases with Pandoc as an independent oracle.

Never imports tomd's reader or writer. The EPUB policy (docs/ARCHITECTURE.md, phase 2)
is re-applied here on the XHTML: spine order; navigation, cover and boilerplate out;
note calls and notes swapped for markers (Pandoc's EPUB reader drops cross-file
endnotes); internal links unwrapped. Pandoc converts each document's HTML, and a few
mechanical rewrites bring its Markdown to the dialect (docs/MARKDOWN_SPEC.md).

Heuristics are deliberately not re-applied (headings from CSS classes or the table of
contents, tables of contents typed into the text): where tomd applies them, the draft and
tomd differ, and the reviewer decides.

    uv run python scripts/draft_epub_expected.py              # real cases without expected.md
    uv run python scripts/draft_epub_expected.py --case se-time-machine --force
"""

import argparse
import hashlib
import io
import posixpath
import re
import sys
import unicodedata
import urllib.parse
import zipfile
from pathlib import Path

import pypandoc
from lxml import etree, html
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "epub"
DRAFT = "# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)\n\n"
PANDOC_FORMAT = (
    "commonmark_x-attributes-bracketed_spans-fenced_divs-raw_attribute-implicit_figures"
    "-smart-superscript-subscript-strikeout-task_lists-definition_lists-raw_html"
)
OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
NAVIGATION = {"toc", "loi", "lot", "lov", "landmarks", "page-list"}
NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
REF = "QQREF{}QQ"
REF_PATTERN = re.compile(r"QQREF(\d+)QQ")
IMG = "QQIMG{}QQ"
IMG_PATTERN = re.compile(r"QQIMG(\d+)QQ")
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/svg+xml": "svg",
              "image/webp": "webp"}  # fmt: skip
XML = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=True)


def types(el: etree._Element) -> set[str]:
    return set((el.get("epub:type") or "").split()) | set((el.get("role") or "").split())


def resolve(base: str, href: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(href)
    path = urllib.parse.unquote(parts.path)
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    return joined, urllib.parse.unquote(parts.fragment)


def remove(el: etree._Element) -> None:
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


def unwrap(el: etree._Element) -> None:
    """Replace an element by its content (lxml.html keeps text and tail in order)."""
    if isinstance(el, html.HtmlElement) and el.getparent() is not None:
        el.drop_tag()


def slugify(title: str) -> str:
    # Same rule as the writer's documented slug: ASCII, lowercase, hyphens (README).
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "document"


class Book:
    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path)
        container = etree.fromstring(self.zip.read("META-INF/container.xml"), XML)
        rootfile = container.find(".//c:rootfile", CONTAINER_NS)
        assert rootfile is not None
        self.opf_path = str(rootfile.get("full-path"))
        opf = etree.fromstring(self.zip.read(self.opf_path), XML)
        title = opf.find(".//dc:title", OPF_NS)
        assert title is not None, "the OPF has no dc:title"
        self.title = " ".join("".join(str(t) for t in title.itertext()).split())
        self.items = {
            str(el.get("id")): (resolve(self.opf_path, el.get("href") or "")[0], el)
            for el in opf.iterfind("opf:manifest/opf:item", OPF_NS)
        }
        nav = {p for p, el in self.items.values() if "nav" in (el.get("properties") or "")}
        guide_toc = {
            resolve(self.opf_path, r.get("href") or "")[0]
            for r in opf.iterfind("opf:guide/opf:reference", OPF_NS)
            if (r.get("type") or "").lower() == "toc"
        }
        self.spine = [
            self.items[str(r.get("idref"))][0]
            for r in opf.iterfind("opf:spine/opf:itemref", OPF_NS)
            if self.items[str(r.get("idref"))][0] not in nav | guide_toc
        ]
        self.media = {p: el.get("media-type") or "" for p, el in self.items.values()}


def draft(case_dir: Path) -> str:
    book = Book(case_dir / "input.epub")
    slug = slugify(book.title)
    bodies: dict[str, etree._Element] = {}
    for path in book.spine:
        text = book.zip.read(path).decode("utf-8", errors="replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        root = html.document_fromstring(text, parser=html.HTMLParser(remove_comments=True))
        body = root.find("body")
        bodies[path] = body if body is not None else root

    # Policy: boilerplate, page markers, semantic navigation.
    for body in bodies.values():
        for el in list(body.iter()):
            if not isinstance(el.tag, str):
                continue
            cls = set((el.get("class") or "").split())
            boilerplate = "pg-boilerplate" in cls or el.get("id") in ("pg-header", "pg-footer")
            page_marker = {"pagebreak", "doc-pagebreak"} & types(el)
            if boilerplate or page_marker or NAVIGATION & types(el):
                remove(el)

    # Notes: calls become markers numbered by first call; notes are collected.
    targets: dict[tuple[str, str], int] = {}
    for path, body in bodies.items():
        for a in list(body.iter("a")):
            if "noteref" in types(a) or "doc-noteref" in types(a):
                key = resolve(path, a.get("href") or "")
                number = targets.setdefault(key, len(targets) + 1)
                sup = a.getparent()
                marker = etree.Element("span")
                marker.text = REF.format(number)
                holder = sup if sup is not None and sup.tag == "sup" and len(sup) == 1 else a
                marker.tail = holder.tail
                holder.getparent().replace(holder, marker)  # type: ignore[union-attr]
    notes: dict[int, str] = {}
    for path, body in bodies.items():
        for el in list(body.iter()):
            if isinstance(el.tag, str) and NOTE_TYPES & types(el):
                for back in [a for a in el.iter("a") if "backlink" in types(a)]:
                    remove(back)
                found = targets.get((path, el.get("id") or ""))
                if found is not None:
                    notes[found] = to_md(
                        "".join(html.tostring(c, encoding="unicode") for c in el) or (el.text or "")
                    )
                remove(el)

    # Images: decorative ones out, the rest as markers resolved after Pandoc.
    images: list[str] = []
    for path, body in bodies.items():
        for img in list(body.iter("img", "image")):
            src = img.get("src") or img.get("xlink:href") or ""
            target = resolve(path, src)[0] if src else ""
            data = book.zip.read(target) if target in book.zip.namelist() else None
            if data is None:
                remove(img)
                continue
            if not target.endswith(".svg"):
                with Image.open(io.BytesIO(data)) as picture:
                    if min(picture.size) < 32:
                        remove(img)
                        continue
            ext = EXTENSIONS.get(book.media.get(target, ""), "bin")
            alt = " ".join((img.get("alt") or "").split())
            images.append(f"![{alt}]({slug}.assets/{hashlib.sha256(data).hexdigest()[:12]}.{ext})")
            marker = etree.Element("p")
            marker.text = IMG.format(len(images) - 1)
            figure = next((a for a in img.iterancestors() if a.tag == "figure"), None)
            if img.tag == "image":  # SVG <image> inside <svg>
                svg = next((a for a in img.iterancestors() if a.tag == "svg"), img)
                svg.getparent().replace(svg, marker)  # type: ignore[union-attr]
            elif figure is None:
                img.getparent().replace(img, marker)  # type: ignore[union-attr]
            else:
                img.getparent().replace(img, marker)  # type: ignore[union-attr]

    # Links between documents of the book keep their text only.
    for body in bodies.values():
        for a in list(body.iter("a")):
            href = a.get("href") or ""
            if not re.match(r"^(https?|mailto|ftp):", href):
                unwrap(a)

    parts: list[str] = []
    for body in bodies.values():
        md = to_md(html.tostring(body, encoding="unicode"))
        lines = [line for line in md.splitlines() if line.strip()]
        if lines and all(line.startswith("#") for line in lines):
            continue  # a notes page left with its heading only, or an emptied page
        if lines and all(IMG_PATTERN.fullmatch(line.strip()) for line in lines):
            continue  # a cover page
        if md.strip():
            parts.append(md.strip())
    body_md = "\n\n".join(parts)
    body_md = IMG_PATTERN.sub(lambda m: images[int(m.group(1))], body_md)
    body_md = REF_PATTERN.sub(lambda m: f"[^{m.group(1)}]", body_md)
    body_md = relevel(dialect(body_md), book.title)
    out = f"# {book.title}\n\n{body_md}\n"
    for number in sorted(notes):
        out += f"\n[^{number}]: " + notes[number].strip().replace("\n\n", "\n\n    ") + "\n"
    return re.sub(r"\n{3,}", "\n\n", out)


def to_md(fragment: str) -> str:
    md: str = pypandoc.convert_text(
        fragment, PANDOC_FORMAT, format="html", extra_args=["--wrap=none"]
    )
    return md.replace("\r\n", "\n")


def dialect(md: str) -> str:
    # Pandoc writes headings containing <br> in setext style; the dialect is ATX only
    # (M05), with the line breaks flattened to spaces (M08).
    def atx(m: re.Match[str]) -> str:
        level = "#" if m.group(2).startswith("=") else "##"
        parts = (part.rstrip("\\").strip() for part in m.group(1).split("\n"))
        return f"{level} " + " ".join(part for part in parts if part)

    md = re.sub(r"(?m)^((?:[^\n]*\\\n)*[^\n]+)\n(=+|-+)$", atx, md)
    md = re.sub(r"(?m)^-{5,}$", "---", md)  # thematic breaks
    md = re.sub(r"(?m)^(\s*)\* ", r"\1- ", md)  # bullet marker
    # Runs of <br/>: the writer keeps line breaks inside text only (no empty lines, none
    # at the start or end of a paragraph).
    paragraphs = []
    for block in md.split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip() != "\\"]
        if lines and lines[-1].endswith("\\"):
            lines[-1] = lines[-1][:-1].rstrip()
        if lines:
            paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


def relevel(md: str, title: str) -> str:
    """One H1 (the title): a heading equal to it is removed, levels shift under it and
    gaps close (M06, M07)."""
    lines = md.split("\n")
    out: list[str] = []
    headings = [len(m.group(1)) for line in lines if (m := re.match(r"^(#{1,6}) ", line))]
    shift = 1 if 1 in headings else 0
    stack: list[tuple[int, int]] = []
    for line in lines:
        m = re.match(r"^(#{1,6}) (.*)$", line)
        if not m:
            out.append(line)
            continue
        level, text = len(m.group(1)) + shift, m.group(2).strip()
        if level == 2 and shift and _key(text) == _key(title) and not stack:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        new = min(stack[-1][1] + 1 if stack else 2, 6)
        stack.append((level, new))
        out.append("#" * new + " " + text)
    return "\n".join(out)


def _key(text: str) -> str:
    return " ".join(text.casefold().split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    for case_dir in sorted(ROOT.iterdir()):
        if not (case_dir / "source.txt").exists():  # synthetic cases have their own script
            continue
        if args.case and case_dir.name != args.case:
            continue
        expected = case_dir / "expected.md"
        if expected.exists() and not args.force:
            continue
        if expected.exists() and not expected.read_text(encoding="utf-8").startswith("# DRAFT"):
            print(f"skip {case_dir.name}: reviewed expected.md is never overwritten")
            continue
        expected.write_text(DRAFT + draft(case_dir), encoding="utf-8", newline="\n")
        print(f"wrote {expected.relative_to(ROOT.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
