"""Wikipedia articles as files, so they go through the batch like any document.

The Parsoid HTML of the chosen revision is saved under the output folder; the batch then
converts that file (dispatch recognises Parsoid HTML), so an article gets a manifest entry,
a review state and its place in the library. Only Wikimedia is contacted (CLAUDE.md).
"""

import re
from pathlib import Path

from tomd.pipeline.dispatch import atomic_write
from tomd.readers.wikipedia.client import WikimediaClient, parse_article_ref

CACHE_DIR = ".wikipedia"  # inside the output folder; hidden, so batches never pick it up


def fetch_article(
    source: str,
    out_dir: Path,
    *,
    revision: int | None = None,
    client: WikimediaClient | None = None,
) -> Path:
    """Download an article (URL or `lang:Title`) and return the saved HTML file."""
    ref = parse_article_ref(source)
    client = client or WikimediaClient()
    revision = revision or client.latest_revision(ref)
    data = client.revision_html(ref, revision)
    name = re.sub(r"[^\w.-]+", "_", ref.title, flags=re.UNICODE).strip("_")[:80] or "article"
    target = out_dir / CACHE_DIR / f"{ref.lang}-{name}-{revision}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, data)
    return target
