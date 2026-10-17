"""Command-line entry point."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from tomd import __version__
from tomd.errors import (
    DrmProtectedError,
    NetworkError,
    SourceNotFoundError,
    TomdError,
    UnreadableInputError,
    UnsupportedSourceError,
)
from tomd.ir.diagnostics import CATALOG, Diagnostic

# Exit codes (README). 2 is left to argparse, which uses it for command-line errors.
EXIT_OK = 0
EXIT_FAILED = 1  # any other TomdError
EXIT_UNSUPPORTED = 3
EXIT_UNREADABLE = 4
EXIT_DRM = 5
EXIT_NOT_FOUND = 6
EXIT_NETWORK = 7
EXIT_INTERRUPTED = 130  # Ctrl+C (128 + SIGINT), as shells report it
_EXIT_CODES: tuple[tuple[type[TomdError], int], ...] = (
    (UnsupportedSourceError, EXIT_UNSUPPORTED),
    (UnreadableInputError, EXIT_UNREADABLE),
    (DrmProtectedError, EXIT_DRM),
    (SourceNotFoundError, EXIT_NOT_FOUND),
    (NetworkError, EXIT_NETWORK),
)


def exit_code(exc: TomdError) -> int:
    return next((code for kind, code in _EXIT_CODES if isinstance(exc, kind)), EXIT_FAILED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tomd",
        description="Convert PDF and EPUB files and Wikipedia articles to Markdown.",
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

    batch = sub.add_parser(
        "batch", help="convert many files, resumable, with a SQLite manifest (ADR-008)"
    )
    batch.add_argument(
        "inputs", nargs="+", type=Path, help="folders, .pdf/.epub files, or .txt lists of paths"
    )
    batch.add_argument("-o", "--out", type=Path, default=Path("out"), help="output folder")
    batch.add_argument("--workers", type=int, default=1, help="worker processes (default 1)")
    batch.add_argument(
        "--timeout", type=float, default=600.0, help="seconds per document (default 600)"
    )
    batch.add_argument(
        "--memory-mb", type=int, default=4096, help="memory limit per worker (default 4096)"
    )
    batch.add_argument("--backend", default="heuristic", help="PDF backend (ADR-005)")
    batch.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    batch.add_argument(
        "--retry-failed", action="store_true", help="convert again documents that failed"
    )
    batch.add_argument(
        "--force", action="store_true", help="convert everything again, even if done"
    )

    ui = sub.add_parser("ui", help="desktop window to convert and review documents")
    ui.add_argument("source", nargs="?", help="document to put in the source field")
    ui.add_argument("-o", "--out", type=Path, help="output folder (default: ./out)")

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
    if args.command == "batch":
        return _batch(args)
    if args.command == "ui":
        from tomd.ui.app import run

        return run(args.source, args.out)
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
        return exit_code(exc)
    print(result.markdown_path)
    _print_diagnostics(result.diagnostics)
    return EXIT_OK


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _print_diagnostics(diagnostics: Sequence[Diagnostic]) -> None:
    """One line per code, most severe first, with the catalog's advice where there is
    some. The full list (with pages) is in the front matter count and the IR."""
    counts = Counter(d.code for d in diagnostics)
    severity = {d.code: d.severity for d in diagnostics}
    for code in sorted(counts, key=lambda c: (_SEVERITY_ORDER[severity[c]], c)):
        print(f"  {severity[code]:7} {code}: {counts[code]}", file=sys.stderr)
        info = CATALOG.get(code)
        if info and info.action and severity[code] != "info":
            print(f"          -> {info.action}", file=sys.stderr)


def _batch(args: argparse.Namespace) -> int:
    from tomd.pipeline.batch import BatchOptions, run_batch

    options = BatchOptions(
        out_dir=args.out,
        workers=max(1, args.workers),
        timeout_s=args.timeout,
        memory_mb=args.memory_mb,
        backend=args.backend,
        ocr=args.ocr,
        retry_failed=args.retry_failed,
        force=args.force,
    )
    summary = run_batch(args.inputs, options)
    if summary.report is not None:
        print(summary.report)
    if summary.interrupted:
        return EXIT_INTERRUPTED
    failed = sum(n for status, n in summary.statuses.items() if status != "done")
    return EXIT_FAILED if failed else EXIT_OK


def _debug_pdf(source: Path, page: int, stage: str, out: Path | None) -> int:
    from tomd.readers.pdf.debug import render_debug_svg

    try:
        svg = render_debug_svg(source, page, stage)
    except TomdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code(exc)
    except ValueError as exc:  # a page or stage out of range
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    target = out or source.with_name(f"{source.stem}.p{page}.{stage}.svg")
    target.write_text(svg, encoding="utf-8")
    print(target)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
