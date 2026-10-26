"""Batch conversion (PLAN phase 7, ADR-008): many documents, resumable, isolated.

The parent process owns the manifest and the output folder (one writer for both). Worker
processes convert: each runs under a memory limit (`limits.apply_memory_limit`), one
document at a time, with a timeout watched by the parent. A worker that times out, runs
out of memory or dies is replaced, its document recorded with that status, and the
batch goes on. The final name of a document (`<slug>` or `<slug>-<sha8>` on a title
collision) is decided by the parent before the worker renders, because the Markdown
links its images under that name.
"""

import hashlib
import multiprocessing as mp
import os
import signal
import sys
import time
import traceback
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from multiprocessing.connection import Connection, wait
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

from tomd import __version__
from tomd.pipeline.manifest import Finding, Manifest, Task

SUFFIXES = frozenset({".pdf", ".epub"})
MANIFEST_NAME = "manifest.sqlite"
REPORT_NAME = "report.md"
# A worker is replaced after this many documents, so that memory fragmentation and the
# never-decreasing peak counter stay bounded.
_TASKS_PER_WORKER = 50
_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class BatchOptions:
    out_dir: Path
    workers: int = 1
    timeout_s: float = 600.0
    memory_mb: int = 4096
    backend: str = "heuristic"
    ocr: str = "auto"
    retry_failed: bool = False
    force: bool = False
    # Long books need longer: the worker reports a PDF's pages and gets this much more
    # time per page (OCR runs at ~1-3 s a page; a 431-page book then has ~30 min).
    timeout_per_page_s: float = 3.0
    split: str = "auto"  # books by chapters (ADR-018)
    split_level: int = 1
    rag: bool = False  # leave out what is not content (ADR-019)


@dataclass
class BatchSummary:
    planned: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    statuses: Counter[str] = field(default_factory=Counter)
    missing_inputs: list[str] = field(default_factory=list)
    interrupted: bool = False
    report: Path | None = None


# --- Inputs ----------------------------------------------------------------------------------


def discover(sources: Iterable[Path]) -> tuple[list[Path], list[str]]:
    """Files to convert from folders (recursive), files and list files (`.txt`, one path
    per line, `#` comments, relative to the list). Returns (files, entries not found)."""
    files: set[Path] = set()
    missing: list[str] = []
    for source in sources:
        if source.is_dir():
            files.update(
                p for p in source.rglob("*")
                if p.is_file() and p.suffix.lower() in SUFFIXES
                and not any(part.startswith(".") for part in p.relative_to(source).parts)
            )  # fmt: skip
        elif source.suffix.lower() == ".txt" and source.is_file():
            for raw in source.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                path = Path(line) if Path(line).is_absolute() else source.parent / line
                if path.is_file():
                    files.add(path)
                else:
                    missing.append(line)
        elif source.is_file():
            files.add(source)
        else:
            missing.append(str(source))
    return sorted(p.resolve() for p in files), missing


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def hash_inputs(files: list[Path], manifest: Manifest) -> list[tuple[str, str]]:
    """(path, sha256) pairs, reusing hashes of files unchanged since the last run."""
    pairs: list[tuple[str, str]] = []
    for path in files:
        stat = path.stat()
        key = str(path)
        sha = manifest.cached_hash(key, stat.st_size, stat.st_mtime_ns)
        if sha is None:
            sha = sha256_of(path)
            manifest.record_input(key, stat.st_size, stat.st_mtime_ns, sha)
        pairs.append((key, sha))
    return pairs


# --- Worker side -----------------------------------------------------------------------------


def _worker_main(
    conn: Connection,
    memory_mb: int,
    backend: str,
    ocr: str,
    split: str,
    split_level: int,
    rag: bool = False,
) -> None:
    """Convert documents sent by the parent until told to stop."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the parent's to handle
    from tomd.errors import DrmProtectedError, TomdError
    from tomd.pipeline.anchors import page_anchors
    from tomd.pipeline.book import render_book
    from tomd.pipeline.dispatch import prepare_document, read_source
    from tomd.pipeline.limits import apply_memory_limit, peak_memory_mb
    from tomd.readers.pdf.ocr import OcrMode
    from tomd.transforms.rag import drop_non_content
    from tomd.transforms.split import split_document
    from tomd.writers.markdown import render_markdown

    if memory_mb > 0:
        apply_memory_limit(memory_mb)
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message[0] == "stop":
            return
        _, source = message
        try:
            pages = _page_count(source)
            if pages:
                conn.send(("pages", pages))  # the parent extends this document's deadline
            doc = read_source(source, pdf_backend=backend, ocr=OcrMode(ocr))
            slug, doc = prepare_document(doc)
            if rag:
                doc = drop_non_content(doc)
            book, doc = split_document(doc, mode=split, level=split_level)
            conn.send(("slug", slug))
            final = conn.recv()[1]
            if book is not None:
                rendered = render_book(book)
                part_anchors = [
                    (part, a.page, a.line, a.bbox)
                    for part, chapter in enumerate(book.chapters, start=1)
                    for a in page_anchors(chapter.document, rendered.files[part][1])
                ]
                parts = [(name, _part_title(name, book, i)) for i, (name, _) in
                         enumerate(rendered.files)]  # fmt: skip
                conn.send(
                    ("book", rendered, _wire(rendered.diagnostics), part_anchors, parts,
                     doc.meta.title, peak_memory_mb())
                )  # fmt: skip
                continue
            result = render_markdown(doc, slug=final)
            anchors = [(a.page, a.line, a.bbox) for a in page_anchors(doc, result.text)]
            conn.send(
                ("done", result.text, doc.assets, _wire(result.diagnostics), anchors,
                 doc.meta.title, peak_memory_mb())
            )  # fmt: skip
        except MemoryError:
            conn.send(("error", "memory", "Memory limit exceeded.", peak_memory_mb()))
            return  # leave with a clean heap: the parent starts a new worker
        except DrmProtectedError as exc:
            conn.send(("error", "drm", str(exc), peak_memory_mb()))
        except TomdError as exc:
            conn.send(("error", "failed", f"{type(exc).__name__}: {exc}", peak_memory_mb()))
        except Exception as exc:  # a bug in tomd: recorded, never fatal to the batch
            detail = "".join(traceback.format_exception(exc))[-2000:]
            conn.send(("error", "failed", f"Internal error: {detail}", peak_memory_mb()))


def _wire(diagnostics: Any) -> list[tuple[Any, ...]]:
    """Diagnostics as plain tuples for the pipe: (code, severity, message, page, bbox)."""
    return [
        (d.code, d.severity, d.message,
         d.provenance.page if d.provenance else None,
         d.provenance.bbox if d.provenance else None)
        for d in diagnostics
    ]  # fmt: skip


def _part_title(name: str, book: Any, index: int) -> str | None:
    """Title of file `index` of a rendered book: the book's for index.md, else the chapter's."""
    if index == 0:
        title: str | None = book.book.meta.title
        return title
    chapter_title: str | None = book.chapters[index - 1].document.meta.title
    return chapter_title


def _page_count(source: str) -> int:
    """Pages of a PDF, read in the worker (a damaged file may crash PDFium, and only the
    worker may crash); 0 for other sources or when the file cannot be opened."""
    if not source.lower().endswith(".pdf"):
        return 0
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(source)
        try:
            return len(pdf)
        finally:
            pdf.close()
    except Exception:  # read_source reports the real problem
        return 0


@dataclass
class _Worker:
    process: BaseProcess
    conn: Any  # Connection (PipeConnection on Windows)
    done: int = 0
    task: Task | None = None
    deadline: float = 0.0
    started: float = 0.0


# --- Parent side -----------------------------------------------------------------------------


class _Pool:
    def __init__(self, options: BatchOptions) -> None:
        self.options = options
        self.context = mp.get_context("spawn")
        self.workers: list[_Worker] = []

    def start(self) -> _Worker:
        parent, child = self.context.Pipe()
        o = self.options
        process = self.context.Process(
            target=_worker_main,
            args=(child, o.memory_mb, o.backend, o.ocr, o.split, o.split_level, o.rag),
            daemon=True,
        )
        process.start()
        child.close()
        worker = _Worker(process, parent)
        self.workers.append(worker)
        return worker

    def stop(self, worker: _Worker, *, kill: bool = False) -> None:
        if worker in self.workers:
            self.workers.remove(worker)
        if not kill and worker.process.is_alive():
            try:
                worker.conn.send(("stop",))
            except (BrokenPipeError, OSError):
                kill = True
        if kill and worker.process.is_alive():
            worker.process.terminate()
        worker.process.join(timeout=10)
        worker.conn.close()

    def shutdown(self, *, kill: bool) -> None:
        for worker in list(self.workers):
            self.stop(worker, kill=kill)


def run_batch(
    sources: list[Path],
    options: BatchOptions,
    *,
    progress: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> BatchSummary:
    options.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(options.out_dir / MANIFEST_NAME)
    summary = BatchSummary()
    version = __version__
    try:
        files, summary.missing_inputs = discover(sources)
        for entry in summary.missing_inputs:
            progress(f"not found, skipped: {entry}")
        reset = manifest.reset_running(version)
        if reset:
            progress(f"resuming: {reset} document(s) left running by an interrupted batch")
        tasks, summary.skipped = manifest.plan(
            hash_inputs(files, manifest), version, retry_failed=options.retry_failed,
            force=options.force,
        )  # fmt: skip
        summary.planned = len(tasks)
        skipped = ", ".join(f"{n} {k}" for k, n in summary.skipped.items() if n)
        progress(
            f"{len(files)} input(s); {len(tasks)} to convert"
            + (f"; skipped {skipped}" if skipped else "")
        )
        run_id = manifest.start_run(_options_record(options))
        try:
            _schedule(tasks, options, manifest, version, summary, progress)
        except KeyboardInterrupt:
            summary.interrupted = True
            progress("interrupted: finished documents are kept; run the same command to resume")
        finally:
            manifest.reset_running(version)
            manifest.end_run(run_id, {"statuses": dict(summary.statuses),
                                      "interrupted": summary.interrupted})  # fmt: skip
        summary.report = write_report(manifest, version, options.out_dir, summary)
    finally:
        manifest.close()
    return summary


def _schedule(
    tasks: list[Task],
    options: BatchOptions,
    manifest: Manifest,
    version: str,
    summary: BatchSummary,
    progress: Callable[[str], None],
) -> None:
    from tomd.pipeline.dispatch import write_output

    pool = _Pool(options)
    queue = deque(tasks)
    total, finished = len(tasks), 0
    idle: list[_Worker] = []
    clean = False
    try:
        while queue or any(w.task for w in pool.workers):
            # Hand out work.
            while queue and (idle or len(pool.workers) < max(1, options.workers)):
                worker = idle.pop() if idle else pool.start()
                next_task = queue.popleft()
                manifest.started(next_task, version)
                worker.task, worker.started = next_task, time.monotonic()
                worker.deadline = worker.started + options.timeout_s
                worker.conn.send(("convert", next_task.source))
            busy = [w for w in pool.workers if w.task]
            next_deadline = min(w.deadline for w in busy)
            ready = wait([w.conn for w in busy], timeout=max(0.0, next_deadline - time.monotonic()))
            for worker in busy:
                task = worker.task
                assert task is not None
                if worker.conn in ready:
                    outcome = _receive(worker, task, manifest, version, options, write_output)
                elif time.monotonic() >= worker.deadline:
                    allowed = worker.deadline - worker.started
                    outcome = ("timeout", f"No result after {allowed:.0f} s.")
                    pool.stop(worker, kill=True)
                    manifest.finished(task, version, status="timeout",
                                      duration_s=allowed, peak_mb=None,
                                      error=outcome[1])  # fmt: skip
                else:
                    continue
                if outcome is None:  # asked for its name; still working
                    continue
                status, note = outcome
                finished += 1
                summary.statuses[status] += 1
                elapsed = time.monotonic() - worker.started
                worker.task = None
                name = "/".join(Path(task.source).parts[-2:])
                line = f"[{finished}/{total}] {status:8} {elapsed:6.1f}s  {name}"
                progress(line + (f"  ({note})" if note else ""))
                if status in ("memory", "crashed") and worker in pool.workers:
                    pool.stop(worker, kill=True)
                elif worker in pool.workers:
                    worker.done += 1
                    if worker.done >= _TASKS_PER_WORKER:
                        pool.stop(worker)
                    else:
                        idle.append(worker)
        clean = True
    finally:
        pool.shutdown(kill=not clean)


def _receive(
    worker: _Worker,
    task: Task,
    manifest: Manifest,
    version: str,
    options: BatchOptions,
    write_output: Callable[..., Path],
) -> tuple[str, str] | None:
    """Handle one message from a busy worker. Returns (status, note) when the document
    is finished, None while the conversation goes on."""
    elapsed = time.monotonic() - worker.started
    try:
        message: Any = worker.conn.recv()
    except (EOFError, OSError):
        code = worker.process.exitcode if not worker.process.is_alive() else None
        worker.process.join(timeout=5)
        code = worker.process.exitcode if code is None else code
        status = "memory" if _is_memory_exit(code) else "crashed"
        error = f"Worker ended unexpectedly (exit code {code})."
        manifest.finished(task, version, status=status, duration_s=elapsed, peak_mb=None,
                          error=error)  # fmt: skip
        return status, error
    kind = message[0]
    if kind == "pages":
        worker.deadline = (
            worker.started + options.timeout_s + message[1] * options.timeout_per_page_s
        )
        return None
    if kind == "slug":
        worker.conn.send(("slug", manifest.claim_slug(message[1], task.sha256)))
        return None
    if kind == "book":
        from tomd.pipeline.book import INDEX_NAME, write_book

        _, rendered, diagnostics, part_anchors, parts, title, peak = message
        slug = manifest.claim_slug("", task.sha256)  # already claimed: returns the record
        write_book(options.out_dir, slug, rendered)
        counts = Counter((code, severity) for code, severity, *_ in diagnostics)
        findings = [Finding(c, s, m, p, b) for c, s, m, p, b in diagnostics]
        manifest.finished(task, version, status="done", duration_s=elapsed, peak_mb=peak,
                          output=f"{slug}/{INDEX_NAME}", title=title, diagnostics=dict(counts),
                          findings=findings, parts=parts, part_anchors=part_anchors)  # fmt: skip
        warnings = sum(n for (_, sev), n in counts.items() if sev != "info")
        note = f"split into {len(parts)} files" + (f", {warnings} warning(s)" if warnings else "")
        return "done", note
    if kind == "done":
        _, text, assets, diagnostics, anchors, title, peak = message
        slug = manifest.claim_slug("", task.sha256)  # already claimed: returns the record
        path = write_output(options.out_dir, slug, text, assets)
        counts = Counter((code, severity) for code, severity, *_ in diagnostics)
        findings = [Finding(c, s, m, p, b) for c, s, m, p, b in diagnostics]
        manifest.finished(task, version, status="done", duration_s=elapsed, peak_mb=peak,
                          output=path.name, title=title, diagnostics=dict(counts),
                          findings=findings, anchors=anchors)  # fmt: skip
        warnings = sum(n for (_, sev), n in counts.items() if sev != "info")
        return "done", f"{warnings} warning(s)" if warnings else ""
    _, status, error, peak = message
    manifest.finished(task, version, status=status, duration_s=elapsed, peak_mb=peak,
                      error=error)  # fmt: skip
    return status, error.splitlines()[0][:120]


def _is_memory_exit(code: int | None) -> bool:
    """Native code that cannot allocate under the job limit aborts; Windows reports it
    as out of memory (0xC0000017) or as a failed allocation abort (0xC0000409)."""
    if code is None:
        return False
    unsigned = code & 0xFFFFFFFF
    return unsigned in (0xC0000017, 0xC0000409) or code == -9


def _options_record(options: BatchOptions) -> dict[str, object]:
    return {
        "workers": options.workers, "timeout_s": options.timeout_s,
        "memory_mb": options.memory_mb, "backend": options.backend, "ocr": options.ocr,
        "retry_failed": options.retry_failed, "force": options.force,
        "timeout_per_page_s": options.timeout_per_page_s, "split": options.split,
        "split_level": options.split_level, "rag": options.rag,
        "version": __version__, "pid": os.getpid(),
    }  # fmt: skip


# --- Report ----------------------------------------------------------------------------------


def write_report(manifest: Manifest, version: str, out_dir: Path, summary: BatchSummary) -> Path:
    """`report.md`: the state of the whole batch for this converter version."""
    counts = manifest.status_counts(version)
    total = sum(counts.values())
    lines = [f"# Informe del lote (tomd {version})", ""]
    this_run = ", ".join(f"{n} {s}" for s, n in sorted(summary.statuses.items()))
    skipped = ", ".join(f"{n} {k}" for k, n in summary.skipped.items() if n)
    lines.append(
        "Esta ejecución: "
        + (this_run or "nada que convertir")
        + (f"; omitidos: {skipped}" if skipped else "")
        + (" — **interrumpida**, se reanuda con la misma orden." if summary.interrupted else ".")
    )
    if summary.missing_inputs:
        lines.append(f"Entradas no encontradas: {len(summary.missing_inputs)}.")
    lines += ["", "## Estado de todos los documentos", "", "| Estado | Documentos |", "|---|---|"]
    lines += [f"| {status} | {n} |" for status, n in sorted(counts.items())]
    lines += [f"| **total** | **{total}** |", ""]
    peak = manifest.peak(version)
    lines.append(
        f"Tiempo total de conversión: {manifest.total_time(version):.0f} s. "
        + (f"Memoria pico de un worker: {peak:.0f} MB." if peak is not None else "")
    )
    top = manifest.top_diagnostics(version, 15)
    if top:
        lines += ["", "## Diagnósticos más frecuentes", ""]
        lines += ["| Código | Severidad | Apariciones | Documentos |", "|---|---|---|---|"]
        lines += [f"| `{c}` | {s} | {n} | {d} |" for c, s, n, d in top]
    slow = manifest.slowest(version, 10)
    if slow:
        lines += ["", "## Documentos más lentos", "", "| Documento | Segundos | MB pico |",
                  "|---|---|---|"]  # fmt: skip
        lines += [f"| {_short(s)} | {d:.1f} | {p if p is not None else '—'} |" for s, d, p in slow]
    failures = manifest.failures(version, 50)
    if failures:
        lines += ["", "## Fallos", "", "| Documento | Estado | Motivo |", "|---|---|---|"]
        lines += [
            f"| {_short(s)} | {st} | {e.splitlines()[0][:160].replace('|', '/') if e else ''} |"
            for s, st, e in failures
        ]
        lines += [
            "",
            "Reintentar los fallidos: `tomd batch <entradas> -o <salida> --retry-failed`.",
        ]
    target = out_dir / REPORT_NAME
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def _short(path: str) -> str:
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return path
