"""Cut a public-domain EPUB down to a corpus case (phase 2).

Keeps the chosen spine documents (by index), plus the files holding the notes they call;
shortens long documents at a block boundary; removes notes that no kept call refers to;
and prunes the manifest, spine, guide and table of contents to match. Untouched files are
copied byte for byte. Writes `input.epub`, `case.yaml` and `source.txt`; the expected
draft comes from `scripts/draft_epub_expected.py`.

    uv run python scripts/excerpt_epub.py book.epub se-time-machine --keep 0,1,2,9,15 \\
        --source "Standard Ebooks ... (CC0)" --url https://standardebooks.org/... \\
        --phenomena epub3,endnotes,lang_en --max-chars 15000
"""

import argparse
import posixpath
import re
import sys
import urllib.parse
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "epub"
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}
XHTML = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=True)
_DATE = (2000, 1, 1, 0, 0, 0)
NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}


def resolve(base: str, href: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(href)
    path = urllib.parse.unquote(parts.path)
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    return joined, urllib.parse.unquote(parts.fragment)


def local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def text_len(el: etree._Element) -> int:
    return len("".join(str(t) for t in el.itertext()).strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path)
    parser.add_argument("case")
    parser.add_argument("--keep", required=True, help="spine indices, e.g. 0,1,9")
    parser.add_argument("--max-chars", type=int, default=0, help="shorten longer documents")
    parser.add_argument("--source", required=True, help="edition and license for case.yaml")
    parser.add_argument("--url", required=True)
    parser.add_argument("--phenomena", required=True)
    parser.add_argument(
        "--strip-pg",
        action="store_true",
        help="remove the Project Gutenberg header, footer and license (its terms allow it "
        "for public-domain redistribution without the trademark)",
    )
    args = parser.parse_args(argv)

    src = zipfile.ZipFile(args.epub)
    container = etree.fromstring(src.read("META-INF/container.xml"), PARSER)
    rootfile = container.find(".//c:rootfile", NS)
    assert rootfile is not None, "no rootfile in container.xml"
    opf_path = str(rootfile.get("full-path"))
    opf = etree.fromstring(src.read(opf_path), PARSER)
    manifest: dict[str, tuple[str, etree._Element]] = {
        str(el.get("id")): (resolve(opf_path, el.get("href") or "")[0], el)
        for el in opf.iterfind("opf:manifest/opf:item", NS)
        if el.get("id")
    }
    itemrefs = list(opf.iterfind("opf:spine/opf:itemref", NS))
    wanted = {int(i) for i in args.keep.split(",")}
    kept_docs = [manifest[str(r.get("idref"))][0] for i, r in enumerate(itemrefs) if i in wanted]

    docs: dict[str, etree._Element] = {p: etree.fromstring(src.read(p), PARSER) for p in kept_docs}
    if args.strip_pg:
        for root in docs.values():
            strip_pg(root)
    if args.max_chars:
        for root in docs.values():
            shorten(root, args.max_chars)
    # Files with notes called from kept documents come along.
    for path, root in list(docs.items()):
        for a in root.iter(f"{XHTML}a"):
            target, fragment = resolve(path, a.get("href") or "")
            if fragment and "noteref" in (a.get(EPUB_TYPE) or "") and target not in docs:
                docs[target] = etree.fromstring(src.read(target), PARSER)
                kept_docs.append(target)
    prune_notes(docs)

    referenced = referenced_resources(docs, opf_path, manifest)
    keep_items = {
        item_id for item_id, (path, el) in manifest.items()
        if path in docs or path in referenced
        or "nav" in (el.get("properties") or "").split()
        or el.get("media-type") in ("application/x-dtbncx+xml", "text/css")
    }  # fmt: skip
    for item_id, (_, el) in manifest.items():
        if item_id not in keep_items:
            el.getparent().remove(el)  # type: ignore[union-attr]
    for ref in itemrefs:
        if manifest[str(ref.get("idref"))][0] not in docs:
            ref.getparent().remove(ref)
    for ref in list(opf.iterfind("opf:guide/opf:reference", NS)):
        if resolve(opf_path, ref.get("href") or "")[0] not in docs:
            ref.getparent().remove(ref)

    kept_paths = {manifest[i][0] for i in keep_items}
    outputs: dict[str, bytes] = {}
    for item_id in keep_items:
        path, el = manifest[item_id]
        if path in docs:
            continue
        data = src.read(path)
        if "nav" in (el.get("properties") or "").split():
            data = prune_nav(etree.fromstring(data, PARSER), path, set(docs))
        elif el.get("media-type") == "application/x-dtbncx+xml":
            data = prune_ncx(etree.fromstring(data, PARSER), path, set(docs))
        outputs[path] = data
    for path, root in docs.items():
        outputs[path] = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    outputs[opf_path] = etree.tostring(opf, xml_declaration=True, encoding="utf-8")
    outputs["META-INF/container.xml"] = src.read("META-INF/container.xml")
    assert kept_paths <= set(outputs) | {opf_path}, "a kept resource was not written"

    case_dir = ROOT / args.case
    case_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(case_dir / "input.epub", "w") as out:
        out.writestr(
            zipfile.ZipInfo("mimetype", _DATE),
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        for name in sorted(outputs):
            out.writestr(
                zipfile.ZipInfo(name, _DATE),
                outputs[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    phenomena = ", ".join(p.strip() for p in args.phenomena.split(","))
    (case_dir / "case.yaml").write_text(
        f"source: {args.source!r}\nphenomena: [{phenomena}]\nknown_limits: []\n",
        encoding="utf-8",
        newline="\n",
    )
    (case_dir / "source.txt").write_text(
        f"{args.url}\nExcerpt: spine {args.keep}"
        + (f", documents shortened to ~{args.max_chars} characters" if args.max_chars else "")
        + "; notes not called from the excerpt removed (scripts/excerpt_epub.py).\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {case_dir / 'input.epub'} ({len(outputs)} files)", file=sys.stderr)
    return 0


def strip_pg(root: etree._Element) -> None:
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if "pg-boilerplate" in (el.get("class") or "").split() or el.get("id") in (
            "pg-header",
            "pg-footer",
            "pg-machine-header",
        ):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)


def shorten(root: etree._Element, max_chars: int) -> None:
    """Drop body content after `max_chars` characters, at a block boundary: descend into
    the container that crosses the limit and cut there."""
    body = next((e for e in root if local(e) == "body"), root)
    container, budget = body, max_chars
    while True:
        total, cut = 0, None
        children = [c for c in container if isinstance(c.tag, str)]
        for index, child in enumerate(children):
            size = text_len(child)
            if total + size > budget:
                cut = index
                break
            total += size
        if cut is None:
            return
        crossing = children[cut]
        for later in children[cut + 1 :]:
            container.remove(later)
        inner = [c for c in crossing if isinstance(c.tag, str)]
        if local(crossing) in ("section", "div", "article", "body") and inner:
            container, budget = crossing, budget - total
            continue
        if total:  # the crossing block itself goes when something came before it
            container.remove(crossing)
        return


def prune_notes(docs: dict[str, etree._Element]) -> None:
    """Remove note definitions that no kept call refers to."""
    called: set[tuple[str, str]] = set()
    for path, root in docs.items():
        for a in root.iter(f"{XHTML}a"):
            if "noteref" in (a.get(EPUB_TYPE) or ""):
                called.add(resolve(path, a.get("href") or ""))
    for path, root in docs.items():
        for el in list(root.iter()):
            if not isinstance(el.tag, str):
                continue
            types = set((el.get(EPUB_TYPE) or "").split())
            if types & NOTE_TYPES and (path, el.get("id") or "") not in called:
                el.getparent().remove(el)  # type: ignore[union-attr]


def referenced_resources(
    docs: dict[str, etree._Element], opf_path: str, manifest: dict[str, tuple[str, etree._Element]]
) -> set[str]:
    out: set[str] = set()
    for path, root in docs.items():
        for el in root.iter():
            for name in ("src", "href", "{http://www.w3.org/1999/xlink}href"):
                value = el.get(name)
                if value and not re.match(r"^[a-z]+:", value):
                    out.add(resolve(path, value)[0])
    # Links to other documents of the book are not resources: only what is shown.
    documents = {
        p for p, el in manifest.values()
        if (el.get("media-type") or "") in ("application/xhtml+xml", "text/html")
    }  # fmt: skip
    return out - documents


def prune_nav(root: etree._Element, path: str, docs: set[str]) -> bytes:
    for li in list(root.iter(f"{XHTML}li")):
        a = li.find(f"{XHTML}a")
        if a is not None and resolve(path, a.get("href") or "")[0] not in docs:
            nested = li.find(f"{XHTML}ol")
            if nested is None or not len(nested):
                li.getparent().remove(li)  # type: ignore[union-attr]
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def prune_ncx(root: etree._Element, path: str, docs: set[str]) -> bytes:
    for point in list(root.iter("{http://www.daisy.org/z3986/2005/ncx/}navPoint")):
        content = point.find("ncx:content", NS)
        outside = content is not None and resolve(path, content.get("src") or "")[0] not in docs
        if outside and point.find("ncx:navPoint", NS) is None:
            point.getparent().remove(point)  # type: ignore[union-attr]
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
