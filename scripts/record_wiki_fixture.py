"""Record a Wikipedia article's Parsoid HTML for tests or the golden corpus.

Usage:
    uv run python scripts/record_wiki_fixture.py https://es.wikipedia.org/wiki/X out/dir
    uv run python scripts/record_wiki_fixture.py es:X out/dir --revision 123

Writes `input.html` and `fetch.json` (URL, revision, User-Agent) to the output directory.
This is the only script in the repository that touches the network.
"""

import argparse
import json
import sys
from pathlib import Path

from tomd.readers.wikipedia.client import WikimediaClient, parse_article_ref


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--revision", type=int)
    args = parser.parse_args(argv)

    ref = parse_article_ref(args.article)
    client = WikimediaClient()
    revision = args.revision or client.latest_revision(ref)
    data = client.revision_html(ref, revision)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "input.html").write_bytes(data)
    meta = {"url": ref.url, "revision_id": revision, "user_agent": client.user_agent}
    (args.out_dir / "fetch.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{ref.url} @ {revision}: {len(data)} bytes -> {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
