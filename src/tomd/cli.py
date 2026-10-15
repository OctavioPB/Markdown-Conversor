"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from tomd import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tomd",
        description="Convert PDF, EPUB and Wikipedia articles to Markdown.",
    )
    parser.add_argument("--version", action="version", version=f"tomd {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
