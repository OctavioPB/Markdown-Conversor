"""Cut a PDF down to a few pages for the golden corpus (one phenomenon per case).

    uv run python scripts/excerpt_pdf.py source.pdf tests/corpus/pdf/<case>/input.pdf --pages 1 2

Pages are 1-based. Also writes `source.txt` next to the excerpt: the raw text of each
page as PDFium extracts it (content-stream order, no layout analysis), to transcribe
`expected.md` from the source rather than from tomd's output (golden-corpus skill).
"""

import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    args = parser.parse_args(argv)

    src = pdfium.PdfDocument(str(args.source))
    out = pdfium.PdfDocument.new()
    try:
        out.import_pages(src, [p - 1 for p in args.pages])
        args.target.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(args.target))
        texts = []
        for index in range(len(out)):
            page = out[index]
            textpage = page.get_textpage()
            texts.append(f"===== page {args.pages[index]} =====\n{textpage.get_text_range()}")
            textpage.close()
            page.close()
        (args.target.parent / "source.txt").write_text(
            "\n".join(texts).replace("\r\n", "\n") + "\n", encoding="utf-8"
        )
    finally:
        out.close()
        src.close()
    print(f"{args.target}: pages {args.pages}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
