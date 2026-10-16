"""Command-line entry point."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from tomd import __version__
from tomd.errors import TomdError

EXIT_OK = 0
EXIT_FAILED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tomd",
        description="Convert PDF, EPUB and Wikipedia articles to Markdown.",
    )
    parser.add_argument("--version", action="version", version=f"tomd {__version__}")
    sub = parser.add_subparsers(dest="command")

    convert = sub.add_parser("convert", help="convert one file or Wikipedia article")
    convert.add_argument("source", help="file path, Wikipedia URL or lang:Title")
    convert.add_argument("-o", "--out", type=Path, default=Path("out"), help="output folder")
    convert.add_argument("--revision", type=int, help="Wikipedia revision id to convert")
    convert.add_argument("--backend", default="heuristic", help="PDF backend (ADR-005)")
    convert.add_argument(
        "--ocr",
        default="auto",
        choices=["auto", "always", "never"],
        help="OCR scanned PDF pages (ADR-017): auto = only pages without usable text",
    )

    debug = sub.add_parser("debug-pdf", help="SVG overlay of one PDF page at one layout stage")
    debug.add_argument("source", type=Path)
    debug.add_argument("--page", type=int, default=1)
    debug.add_argument(
        "--stage", default="classify", choices=["lines", "blocks", "order", "classify"]
    )
    debug.add_argument(
        "-o", "--out", type=Path, help="SVG file (default: <source>.p<N>.<stage>.svg)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "convert":
        return _convert(args.source, args.out, args.revision, args.backend, args.ocr)
    if args.command == "debug-pdf":
        return _debug_pdf(args.source, args.page, args.stage, args.out)
    parser.print_help()
    return EXIT_OK


def _convert(source: str, out: Path, revision: int | None, backend: str, ocr: str) -> int:
    from tomd.pipeline.dispatch import convert
    from tomd.readers.pdf.ocr import OcrMode

    try:
        result = convert(source, out, revision=revision, pdf_backend=backend, ocr=OcrMode(ocr))
    except TomdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(result.markdown_path)
    counts = Counter(d.code for d in result.diagnostics)
    for code, n in sorted(counts.items()):
        print(f"  {code}: {n}", file=sys.stderr)
    return EXIT_OK


def _debug_pdf(source: Path, page: int, stage: str, out: Path | None) -> int:
    from tomd.readers.pdf.debug import render_debug_svg

    try:
        svg = render_debug_svg(source, page, stage)
    except (TomdError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    target = out or source.with_name(f"{source.stem}.p{page}.{stage}.svg")
    target.write_text(svg, encoding="utf-8")
    print(target)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
