"""Work the window hands to separate processes: it stays responsive, a crash inside
PDFium or Tesseract cannot take it down, the memory is returned when the work ends, and
long work can be stopped.

- `BatchJob`: every conversion, of one file or many, goes through `tomd batch`, so each
  document lands in the library (the manifest) with its review state. Wikipedia articles
  are downloaded first and converted from the saved file.
- `PreviewJob`: reads a document again, without writing anything, to draw the text view
  of one converted earlier (conversion is deterministic: it is the same IR).
"""

import multiprocessing as mp
import queue
import traceback
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any

from tomd.ir import Document


@dataclass(frozen=True, slots=True)
class Failed:
    kind: str  # TomdError subclass name, or "crash"
    message: str
    exit_code: int


# --- Batches -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BatchLine:
    text: str  # a progress line of the batch ("[3/40] done  1.2s  folder/file.pdf")


@dataclass(frozen=True, slots=True)
class BatchEnded:
    statuses: dict[str, int]
    planned: int
    report: str | None
    interrupted: bool
    sources: list[str]  # the files actually converted (Wikipedia articles as saved files)


@dataclass(frozen=True, slots=True)
class BatchOptionsUI:
    out_dir: str
    ocr: str = "auto"
    backend: str = "heuristic"
    force: bool = False  # convert again even if done (e.g. "Volver a convertir con OCR")
    retry_failed: bool = False
    split: str = "auto"  # books by chapters (ADR-018)


def _batch_worker(sources: list[str], options: BatchOptionsUI, events: "mp.Queue[object]") -> None:
    from pathlib import Path

    from tomd.errors import TomdError
    from tomd.pipeline.batch import BatchOptions, run_batch
    from tomd.pipeline.fetch import fetch_article

    try:
        resolved: list[str] = []
        for source in sources:
            if Path(source).exists():
                resolved.append(source)
            else:  # a Wikipedia article: saved as a file, then converted like any other
                events.put(BatchLine(f"Descargando {source}…"))
                resolved.append(str(fetch_article(source, Path(options.out_dir))))
        summary = run_batch(
            [Path(source) for source in resolved],
            BatchOptions(
                out_dir=Path(options.out_dir), ocr=options.ocr, backend=options.backend,
                force=options.force, retry_failed=options.retry_failed,
                split=options.split,
            ),
            progress=lambda line: events.put(BatchLine(line)),
        )  # fmt: skip
        report = str(summary.report) if summary.report else None
        events.put(BatchEnded(dict(summary.statuses), summary.planned, report, False, resolved))
    except TomdError as exc:
        events.put(Failed(type(exc).__name__, str(exc), 1))
    except Exception as exc:  # shown in the window, never lost
        events.put(Failed("crash", "".join(traceback.format_exception(exc))[-4000:], 1))


class BatchJob:
    """Folders, files and articles converted by `tomd batch` in a separate process. Not
    a daemon: the batch starts its own workers. Stopping it loses nothing: the manifest
    keeps finished documents, so converting the same inputs again resumes."""

    def __init__(self, sources: list[str], options: BatchOptionsUI) -> None:
        from pathlib import Path

        context = mp.get_context("spawn")
        self.sources = sources
        # How the work is named in the window: the folder or file, or how many items.
        self.label = Path(sources[0]).name if len(sources) == 1 else f"{len(sources)} elementos"
        self._events: mp.Queue[object] = context.Queue()
        self._process: BaseProcess = context.Process(
            target=_batch_worker, args=(sources, options, self._events)
        )
        self._process.start()

    def events(self) -> list[object]:
        """Events received since the last call; a crash of the process becomes Failed."""
        out: list[object] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        if not out and not self._process.is_alive() and self._process.exitcode not in (0, None):
            code = self._process.exitcode
            out.append(Failed("crash", f"The batch ended unexpectedly (exit {code}).", 1))
        return out

    def cancel(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=10)


# --- Preview -----------------------------------------------------------------------------


def _preview_worker(source: str, backend: str, ocr: str, results: "mp.Queue[object]") -> None:
    from tomd.errors import TomdError
    from tomd.pipeline.dispatch import prepare_document, read_source
    from tomd.readers.pdf.ocr import OcrMode

    try:
        _, doc = prepare_document(read_source(source, pdf_backend=backend, ocr=OcrMode(ocr)))
        results.put(doc)
    except TomdError as exc:
        results.put(Failed(type(exc).__name__, str(exc), 1))
    except Exception as exc:
        results.put(Failed("crash", "".join(traceback.format_exception(exc))[-4000:], 1))


class PreviewJob:
    """The IR of one document, read again in the background; poll() from the UI loop."""

    def __init__(self, source: str, backend: str = "heuristic", ocr: str = "auto") -> None:
        context = mp.get_context("spawn")
        self.source = source
        self._results: mp.Queue[object] = context.Queue()
        self._process: BaseProcess = context.Process(
            target=_preview_worker, args=(source, backend, ocr, self._results), daemon=True
        )
        self._process.start()

    def poll(self) -> Document | Failed | None:
        try:
            outcome: Any = self._results.get_nowait()
        except queue.Empty:
            if self._process.is_alive():
                return None
            return Failed("crash", f"The preview ended unexpectedly ({self._process.exitcode}).", 1)
        self._process.join(timeout=5)
        result: Document | Failed = outcome
        return result

    def cancel(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
