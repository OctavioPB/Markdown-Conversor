"""Cut a recorded Parsoid article down to a few sections for the golden corpus.

    uv run python scripts/excerpt_wiki.py full.html out.html --keep "Historia" "Teorema"

Keeps <head>, the lead section (unless --no-lead), the top-level sections whose heading
matches one of --keep, and the reference list, pruned to the notes the kept text cites.
Each case should exercise one phenomenon, and a short expected.md is reviewable by hand.
"""

import argparse
import sys
from pathlib import Path

from lxml import etree, html

from tomd.readers.html_common import classes, parse_html, tag_of, text_content


def _heading_text(section: etree._Element) -> str | None:
    for child in section:
        if tag_of(child) in ("h2", "h3", "h4", "h5", "h6"):
            return " ".join(text_content(child).split())
    return None


def excerpt(data: bytes, keep: list[str], *, lead: bool) -> bytes:
    root = parse_html(data)
    body = next(c for c in root if tag_of(c) == "body")
    wanted = {k.casefold() for k in keep}
    found: set[str] = set()
    for section in [c for c in body if tag_of(c) == "section"]:
        title = _heading_text(section)
        has_refs = any("mw-references" in classes(e) for e in section.iter())
        is_lead = section.get("data-mw-section-id") == "0"
        if title is not None and title.casefold() in wanted:
            found.add(title.casefold())
            continue
        if (is_lead and lead) or has_refs:
            if has_refs and not is_lead:
                # Keep only the reference list, not the rest of that section.
                for child in list(section):
                    if tag_of(child) not in ("h2",) and not any(
                        "mw-references" in classes(e) for e in child.iter()
                    ):
                        section.remove(child)
            continue
        body.remove(section)
    missing = wanted - found
    if missing:
        raise SystemExit(f"Sections not found: {sorted(missing)}")

    cited = {
        a.get("href", "").split("#", 1)[-1]
        for sup in body.iter("sup")
        if "mw:Extension/ref" in (sup.get("typeof") or "")
        for a in sup.iter("a")
    }
    for ol in [e for e in body.iter("ol") if "mw-references" in classes(e)]:
        for li in list(ol):
            if li.get("id") not in cited:
                ol.remove(li)
    out: bytes = html.tostring(root, encoding="utf-8", doctype="<!DOCTYPE html>")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--keep", nargs="*", default=[], help="top-level section headings")
    parser.add_argument("--no-lead", action="store_true", help="drop the lead section")
    args = parser.parse_args(argv)
    data = excerpt(args.source.read_bytes(), args.keep, lead=not args.no_lead)
    args.target.write_bytes(data)
    print(f"{args.target}: {len(data)} bytes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
