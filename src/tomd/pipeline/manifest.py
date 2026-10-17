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

SCHEMA_VERSION = "1"
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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit txns
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,)
        )

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
    ) -> None:
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
