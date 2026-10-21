"""SQLite manifest of a batch (ADR-008): the single source of truth of its state.

A document is keyed by (sha256 of the input, converter version): the same file under two
paths converts once, and a new version reconverts everything. Every state change is
committed at once, so an interrupted batch resumes from the last finished document.
Dates live here, never in the Markdown (ADR-010).
"""

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "3"
FINISHED = ("done", "failed", "drm", "timeout", "memory", "crashed")
FAILED = ("failed", "drm", "timeout", "memory", "crashed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inputs (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inputs_sha ON inputs (sha256);
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    source TEXT NOT NULL,          -- first path seen with this content
    status TEXT NOT NULL,          -- pending, running, done, failed, drm, timeout, memory, crashed
    output TEXT,                   -- Markdown file name in the output folder
    title TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    peak_mb REAL,                  -- worker peak while converting it (an upper bound)
    diagnostics INTEGER,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (sha256, version)
);
CREATE TABLE IF NOT EXISTS diagnostics (
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (sha256, version, code)
);
CREATE TABLE IF NOT EXISTS slugs (slug TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
-- Schema 2: what the desktop UI needs to review a document converted earlier.
CREATE TABLE IF NOT EXISTS findings (
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    idx INTEGER NOT NULL,          -- order in the document's diagnostics
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    page INTEGER,
    bbox TEXT,                     -- JSON [x0, y0, x1, y1] in PDF points, or NULL
    checked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sha256, version, idx)
);
CREATE TABLE IF NOT EXISTS anchors (
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    idx INTEGER NOT NULL,
    page INTEGER NOT NULL,
    line INTEGER NOT NULL,
    bbox TEXT,
    part INTEGER NOT NULL DEFAULT 0,  -- schema 3: the file of a split book (0 = index.md)
    PRIMARY KEY (sha256, version, idx)
);
-- Schema 3: the files of a book split by chapters (ADR-018), in reading order.
CREATE TABLE IF NOT EXISTS parts (
    sha256 TEXT NOT NULL,
    version TEXT NOT NULL,
    idx INTEGER NOT NULL,          -- 0 = index.md, then the chapters
    name TEXT NOT NULL,            -- file name inside the book's folder
    title TEXT,
    PRIMARY KEY (sha256, version, idx)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    options TEXT NOT NULL,
    summary TEXT
);
"""


@dataclass(frozen=True, slots=True)
class Task:
    sha256: str
    source: str  # the path to convert


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnostic of a converted document, as the review screen lists it."""

    code: str
    severity: str
    message: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    checked: bool = False
    idx: int = 0


# How the library shows a document, from its conversion status and its review.
REVIEW_STATES = ("converting", "to_review", "reviewed", "ready", "failed")


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    sha256: str
    source: str
    title: str | None
    output: str | None
    state: str  # one of REVIEW_STATES
    error: str | None
    finished_at: str | None
    reviewed_at: str | None
    to_check: int  # warnings and errors not checked yet
    findings: int  # warnings and errors
    first_code: str | None = None  # the first of those still to check, for the list
    first_page: int | None = None
    parts: int = 0  # files of a book split by chapters, index included; 0 for one file


def _bbox_json(bbox: tuple[float, float, float, float] | None) -> str | None:
    return json.dumps([round(v, 2) for v in bbox]) if bbox else None


def _bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    x0, y0, x1, y1 = json.loads(value)
    return (float(x0), float(y0), float(x1), float(y1))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit with explicit transactions; the desktop UI reads while a batch
        # writes, so a busy database is waited for instead of failing.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring an older manifest to the current schema (tables are created above)."""
        row = self.db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(documents)")}
        if "reviewed_at" not in columns:  # schema 1 -> 2
            self.db.execute("ALTER TABLE documents ADD COLUMN reviewed_at TEXT")
        anchor_columns = {r[1] for r in self.db.execute("PRAGMA table_info(anchors)")}
        if "part" not in anchor_columns:  # schema 2 -> 3 (the parts table is created above)
            self.db.execute("ALTER TABLE anchors ADD COLUMN part INTEGER NOT NULL DEFAULT 0")
        if row is None:
            self.db.execute("INSERT INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
        elif row[0] != SCHEMA_VERSION:
            self.db.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (SCHEMA_VERSION,))

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # --- Inputs ------------------------------------------------------------------------

    def cached_hash(self, path: str, size: int, mtime_ns: int) -> str | None:
        row = self.db.execute(
            "SELECT sha256 FROM inputs WHERE path = ? AND size = ? AND mtime_ns = ?",
            (path, size, mtime_ns),
        ).fetchone()
        return str(row[0]) if row else None

    def record_input(self, path: str, size: int, mtime_ns: int, sha256: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO inputs (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
            (path, size, mtime_ns, sha256),
        )

    # --- Planning ----------------------------------------------------------------------

    def plan(
        self, inputs: Iterable[tuple[str, str]], version: str, *, retry_failed: bool, force: bool
    ) -> tuple[list[Task], dict[str, int]]:
        """Tasks to run for (path, sha256) inputs, in path order; and why others are
        skipped. Entries left `running` by an interrupted run run again."""
        tasks: list[Task] = []
        skipped = {"done": 0, "failed": 0, "duplicate": 0}
        seen: set[str] = set()
        with self._transaction():
            for path, sha in sorted(inputs):
                if sha in seen:
                    skipped["duplicate"] += 1
                    continue
                seen.add(sha)
                row = self.db.execute(
                    "SELECT status FROM documents WHERE sha256 = ? AND version = ?", (sha, version)
                ).fetchone()
                status = row[0] if row else None
                if not force and status == "done":
                    skipped["done"] += 1
                    continue
                if not force and not retry_failed and status in FAILED:
                    skipped["failed"] += 1
                    continue
                if row is None:
                    self.db.execute(
                        "INSERT INTO documents (sha256, version, source, status) "
                        "VALUES (?, ?, ?, 'pending')",
                        (sha, version, path),
                    )
                else:
                    self.db.execute(
                        "UPDATE documents SET status = 'pending', source = ? "
                        "WHERE sha256 = ? AND version = ?",
                        (path, sha, version),
                    )
                tasks.append(Task(sha, path))
        return tasks, skipped

    # --- Progress ----------------------------------------------------------------------

    def started(self, task: Task, version: str) -> None:
        self.db.execute(
            "UPDATE documents SET status = 'running', started_at = ?, attempts = attempts + 1 "
            "WHERE sha256 = ? AND version = ?",
            (_now(), task.sha256, version),
        )

    def claim_slug(self, slug: str, sha256: str) -> str:
        """The output name for a document: its title's slug, or `<slug>-<sha8>` when
        another document already holds it. Recorded, so it never changes afterwards."""
        with self._transaction():
            mine = self.db.execute("SELECT slug FROM slugs WHERE sha256 = ?", (sha256,)).fetchone()
            if mine:
                return str(mine[0])
            holder = self.db.execute("SELECT sha256 FROM slugs WHERE slug = ?", (slug,)).fetchone()
            final = slug if holder is None else f"{slug}-{sha256[:8]}"
            self.db.execute("INSERT INTO slugs (slug, sha256) VALUES (?, ?)", (final, sha256))
        return final

    def finished(
        self,
        task: Task,
        version: str,
        *,
        status: str,
        duration_s: float,
        peak_mb: float | None,
        output: str | None = None,
        title: str | None = None,
        error: str | None = None,
        diagnostics: dict[tuple[str, str], int] | None = None,
        findings: list[Finding] | None = None,
        anchors: list[tuple[int, int, tuple[float, float, float, float] | None]] | None = None,
        parts: list[tuple[str, str | None]] | None = None,
        part_anchors: list[tuple[int, int, int, tuple[float, float, float, float] | None]]
        | None = None,
    ) -> None:
        """`parts` (name, title) and `part_anchors` (part, page, line, bbox) describe a
        book split by chapters; a single file has `anchors` and no parts."""
        with self._transaction():
            self.db.execute(
                "UPDATE documents SET status = ?, output = ?, title = ?, error = ?, "
                "duration_s = ?, peak_mb = ?, diagnostics = ?, finished_at = ? "
                "WHERE sha256 = ? AND version = ?",
                (
                    status, output, title, error, round(duration_s, 3),
                    round(peak_mb, 1) if peak_mb is not None else None,
                    sum((diagnostics or {}).values()), _now(), task.sha256, version,
                ),
            )  # fmt: skip
            self.db.execute(
                "DELETE FROM diagnostics WHERE sha256 = ? AND version = ?", (task.sha256, version)
            )
            for (code, severity), count in sorted((diagnostics or {}).items()):
                self.db.execute(
                    "INSERT INTO diagnostics VALUES (?, ?, ?, ?, ?)",
                    (task.sha256, version, code, severity, count),
                )
            key = (task.sha256, version)
            # A new conversion starts a new review: old findings, checks and anchors go.
            self.db.execute("DELETE FROM findings WHERE sha256 = ? AND version = ?", key)
            self.db.execute("DELETE FROM anchors WHERE sha256 = ? AND version = ?", key)
            self.db.execute("DELETE FROM parts WHERE sha256 = ? AND version = ?", key)
            for idx, (name, part_title) in enumerate(parts or []):
                self.db.execute(
                    "INSERT INTO parts VALUES (?, ?, ?, ?, ?)", (*key, idx, name, part_title)
                )
            self.db.execute(
                "UPDATE documents SET reviewed_at = NULL WHERE sha256 = ? AND version = ?", key
            )
            for idx, f in enumerate(findings or []):
                self.db.execute(
                    "INSERT INTO findings (sha256, version, idx, code, severity, message, page, "
                    "bbox) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*key, idx, f.code, f.severity, f.message, f.page, _bbox_json(f.bbox)),
                )
            placed = [(0, page, line, bbox) for page, line, bbox in anchors or []]
            for idx, (part, page, line, bbox) in enumerate(placed + list(part_anchors or [])):
                self.db.execute(
                    "INSERT INTO anchors VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*key, idx, page, line, _bbox_json(bbox), part),
                )

    def reset_running(self, version: str) -> int:
        """Documents left `running` (the batch was interrupted) go back to `pending`."""
        cursor = self.db.execute(
            "UPDATE documents SET status = 'pending' WHERE status = 'running' AND version = ?",
            (version,),
        )
        return cursor.rowcount

    # --- Runs --------------------------------------------------------------------------

    def start_run(self, options: dict[str, object]) -> int:
        cursor = self.db.execute(
            "INSERT INTO runs (started_at, options) VALUES (?, ?)",
            (_now(), json.dumps(options, sort_keys=True)),
        )
        return int(cursor.lastrowid or 0)

    def end_run(self, run_id: int, summary: dict[str, object]) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
            (_now(), json.dumps(summary, sort_keys=True), run_id),
        )

    # --- Queries for the report --------------------------------------------------------

    def status_counts(self, version: str) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) FROM documents WHERE version = ? GROUP BY status ORDER BY 1",
            (version,),
        )
        return {str(s): int(n) for s, n in rows}

    def top_diagnostics(self, version: str, limit: int) -> list[tuple[str, str, int, int]]:
        """(code, severity, occurrences, documents), most frequent first."""
        rows = self.db.execute(
            "SELECT code, severity, SUM(count), COUNT(*) FROM diagnostics WHERE version = ? "
            "GROUP BY code, severity ORDER BY 3 DESC, 1 LIMIT ?",
            (version, limit),
        )
        return [(str(c), str(s), int(n), int(d)) for c, s, n, d in rows]

    def slowest(self, version: str, limit: int) -> list[tuple[str, float, float | None]]:
        rows = self.db.execute(
            "SELECT source, duration_s, peak_mb FROM documents WHERE version = ? AND status = "
            "'done' ORDER BY duration_s DESC, source LIMIT ?",
            (version, limit),
        )
        return [(str(s), float(d), p) for s, d, p in rows]

    def failures(self, version: str, limit: int) -> list[tuple[str, str, str]]:
        marks = ", ".join("?" for _ in FAILED)
        rows = self.db.execute(
            f"SELECT source, status, COALESCE(error, '') FROM documents WHERE version = ? "
            f"AND status IN ({marks}) ORDER BY status, source LIMIT ?",
            (version, *FAILED, limit),
        )
        return [(str(s), str(st), str(e)) for s, st, e in rows]

    def peak(self, version: str) -> float | None:
        row = self.db.execute(
            "SELECT MAX(peak_mb) FROM documents WHERE version = ?", (version,)
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def total_time(self, version: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(duration_s), 0) FROM documents WHERE version = ?", (version,)
        ).fetchone()
        return float(row[0])

    # --- Library and review (desktop UI) -----------------------------------------------

    def library(self, version: str) -> list[LibraryEntry]:
        """Every document of this converter version, newest first."""
        rows = self.db.execute(
            "SELECT d.sha256, d.source, d.title, d.output, d.status, d.error, d.finished_at, "
            "d.reviewed_at, "
            "(SELECT COUNT(*) FROM findings f WHERE f.sha256 = d.sha256 AND f.version = "
            "d.version AND f.severity != 'info' AND f.checked = 0), "
            "(SELECT COUNT(*) FROM findings f WHERE f.sha256 = d.sha256 AND f.version = "
            "d.version AND f.severity != 'info'), "
            "(SELECT f.code FROM findings f WHERE f.sha256 = d.sha256 AND f.version = "
            "d.version AND f.severity != 'info' AND f.checked = 0 ORDER BY f.idx LIMIT 1), "
            "(SELECT f.page FROM findings f WHERE f.sha256 = d.sha256 AND f.version = "
            "d.version AND f.severity != 'info' AND f.checked = 0 ORDER BY f.idx LIMIT 1), "
            "(SELECT COUNT(*) FROM parts p WHERE p.sha256 = d.sha256 AND p.version = d.version) "
            "FROM documents d WHERE d.version = ? "
            "ORDER BY COALESCE(d.finished_at, d.started_at, '') DESC, d.source",
            (version,),
        )
        return [
            LibraryEntry(
                str(sha), str(source), title, output,
                review_state(str(status), reviewed_at, int(important)),
                error, finished_at, reviewed_at, int(unchecked), int(important),
                first_code, first_page, int(parts),
            )
            for sha, source, title, output, status, error, finished_at, reviewed_at, unchecked,
            important, first_code, first_page, parts in rows
        ]  # fmt: skip

    def findings(self, sha256: str, version: str) -> list[Finding]:
        rows = self.db.execute(
            "SELECT idx, code, severity, message, page, bbox, checked FROM findings "
            "WHERE sha256 = ? AND version = ? ORDER BY idx",
            (sha256, version),
        )
        return [
            Finding(str(c), str(s), str(m), p, _bbox(b), bool(k), int(i))
            for i, c, s, m, p, b, k in rows
        ]

    def anchors(
        self, sha256: str, version: str, part: int = 0
    ) -> list[tuple[int, int, tuple[float, float, float, float] | None]]:
        """Where pages start in the Markdown of one file (`part` of a split book)."""
        rows = self.db.execute(
            "SELECT page, line, bbox FROM anchors WHERE sha256 = ? AND version = ? AND part = ? "
            "ORDER BY idx",
            (sha256, version, part),
        )
        return [(int(p), int(line), _bbox(b)) for p, line, b in rows]

    def parts(self, sha256: str, version: str) -> list[tuple[str, str | None]]:
        """The files of a book split by chapters, (name, title), index first; [] for one file."""
        rows = self.db.execute(
            "SELECT name, title FROM parts WHERE sha256 = ? AND version = ? ORDER BY idx",
            (sha256, version),
        )
        return [(str(name), title) for name, title in rows]

    def set_checked(self, sha256: str, version: str, idx: int, checked: bool) -> None:
        self.db.execute(
            "UPDATE findings SET checked = ? WHERE sha256 = ? AND version = ? AND idx = ?",
            (int(checked), sha256, version, idx),
        )

    def set_reviewed(self, sha256: str, version: str, reviewed: bool) -> None:
        """Mark a document reviewed (allowed with items still unchecked) or not."""
        self.db.execute(
            "UPDATE documents SET reviewed_at = ? WHERE sha256 = ? AND version = ?",
            (_now() if reviewed else None, sha256, version),
        )

    def forget(self, sha256s: Iterable[str]) -> None:
        """Remove documents from the library, under every converter version: their state,
        findings, checks and anchors. Adding the same file again converts it again. The
        claimed file names stay, so a reconversion writes to the same `.md` and never takes
        the name of a file left on disk."""
        keys = [(sha,) for sha in sha256s]
        with self._transaction():
            for table in ("documents", "diagnostics", "findings", "anchors", "parts", "inputs"):
                self.db.executemany(f"DELETE FROM {table} WHERE sha256 = ?", keys)

    def outputs(self) -> list[tuple[str | None, str]]:
        """(output, source) of every document, under every converter version."""
        rows = self.db.execute("SELECT output, source FROM documents ORDER BY source")
        return [(output, str(source)) for output, source in rows]

    def forget_all(self) -> None:
        """Empty the library (and the run history); file names stay claimed, as in forget."""
        with self._transaction():
            tables = ("documents", "diagnostics", "findings", "anchors", "parts", "inputs", "runs")
            for table in tables:
                self.db.execute(f"DELETE FROM {table}")


def review_state(status: str, reviewed_at: str | None, important: int) -> str:
    if status in ("pending", "running"):
        return "converting"
    if status != "done":
        return "failed"
    if reviewed_at:
        return "reviewed"
    return "to_review" if important else "ready"
