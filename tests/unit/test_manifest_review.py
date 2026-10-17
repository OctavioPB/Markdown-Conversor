"""The manifest as the desktop library: findings, anchors, review states (schema v2)."""

import sqlite3
from pathlib import Path

from tomd.pipeline.batch import MANIFEST_NAME, BatchOptions, run_batch
from tomd.pipeline.manifest import Finding, Manifest, Task, review_state

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
TWO_PAGES = CORPUS / "pdf" / "synthetic-page-break" / "input.pdf"
FIXED = CORPUS / "epub" / "synthetic-epub-fixed-layout" / "input.epub"
VERSION = "test"


def test_review_state_from_status_and_findings() -> None:
    assert review_state("pending", None, 0) == "converting"
    assert review_state("running", None, 3) == "converting"
    assert review_state("timeout", None, 0) == "failed"
    assert review_state("done", None, 2) == "to_review"
    assert review_state("done", None, 0) == "ready"
    assert review_state("done", "2026-10-17T10:00:00", 2) == "reviewed"


def _converted(manifest: Manifest, sha: str) -> None:
    manifest.plan([(f"/in/{sha}.pdf", sha)], VERSION, retry_failed=False, force=False)
    manifest.finished(
        Task(sha, f"/in/{sha}.pdf"), VERSION, status="done", duration_s=1.0, peak_mb=None,
        output=f"{sha}.md", title=sha.upper(),
        findings=[
            Finding("pdf.furniture_removed", "info", "x"),
            Finding("pdf.reading_order_uncertain", "warning", "y", 3, (1.0, 2.0, 3.0, 4.0)),
            Finding("pdf.garbage_text", "warning", "z", 5),
        ],
        anchors=[(1, 8, None), (3, 20, (1.0, 2.0, 3.0, 4.0))],
    )  # fmt: skip


def test_findings_checks_and_review_mark(tmp_path: Path) -> None:
    manifest = Manifest(tmp_path / MANIFEST_NAME)
    _converted(manifest, "aaa")
    (entry,) = manifest.library(VERSION)
    assert (entry.state, entry.to_check, entry.findings) == ("to_review", 2, 2)
    assert (entry.first_code, entry.first_page) == ("pdf.reading_order_uncertain", 3)
    findings = manifest.findings("aaa", VERSION)
    assert [f.idx for f in findings] == [0, 1, 2]
    assert findings[1].bbox == (1.0, 2.0, 3.0, 4.0) and not findings[1].checked
    assert manifest.anchors("aaa", VERSION)[1] == (3, 20, (1.0, 2.0, 3.0, 4.0))

    manifest.set_checked("aaa", VERSION, 1, True)
    (entry,) = manifest.library(VERSION)
    assert entry.to_check == 1 and entry.first_code == "pdf.garbage_text"
    # Marking reviewed is allowed with an item unchecked.
    manifest.set_reviewed("aaa", VERSION, True)
    (entry,) = manifest.library(VERSION)
    assert entry.state == "reviewed" and entry.reviewed_at
    manifest.set_reviewed("aaa", VERSION, False)
    assert manifest.library(VERSION)[0].state == "to_review"

    # Converting again starts a new review: checks and the mark go.
    manifest.set_reviewed("aaa", VERSION, True)
    _converted(manifest, "aaa")
    (entry,) = manifest.library(VERSION)
    assert entry.state == "to_review" and entry.to_check == 2 and entry.reviewed_at is None
    manifest.close()


def test_old_manifest_gains_the_review_column(tmp_path: Path) -> None:
    path = tmp_path / MANIFEST_NAME
    Manifest(path).close()
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE documents DROP COLUMN reviewed_at")  # as a v1 manifest was
    db.commit()
    db.close()
    manifest = Manifest(path)
    _converted(manifest, "bbb")
    assert manifest.library(VERSION)[0].state == "to_review"
    manifest.close()


def test_batch_stores_findings_and_anchors(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_batch([TWO_PAGES, FIXED], BatchOptions(out_dir=out, ocr="never"))
    from tomd import __version__

    manifest = Manifest(out / MANIFEST_NAME)
    entries = {Path(e.source).suffix: e for e in manifest.library(__version__)}
    pdf, epub = entries[".pdf"], entries[".epub"]
    assert pdf.state == "ready" and epub.state == "to_review"
    assert epub.first_code == "epub.fixed_layout"
    pages = [page for page, _, _ in manifest.anchors(pdf.sha256, __version__)]
    assert set(pages) == {1, 2} and pages == sorted(pages)
    manifest.close()
