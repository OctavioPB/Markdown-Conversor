"""Conversions run in a separate process: the window stays responsive, a crash inside
PDFium or Tesseract cannot take it down, the conversion's memory is returned to the
system when it ends, and a running conversion can be cancelled."""

import multiprocessing as mp
import queue
import traceback
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any

from tomd.pipeline.dispatch import ConversionResult
from tomd.ui.session import ConversionRequest


@dataclass(frozen=True, slots=True)
class Succeeded:
    result: ConversionResult


@dataclass(frozen=True, slots=True)
class Failed:
    kind: str  # TomdError subclass name, or "crash"
    message: str
    exit_code: int


type Outcome = Succeeded | Failed


def _worker(request: ConversionRequest, results: "mp.Queue[Outcome]") -> None:
    from tomd.cli import exit_code
    from tomd.errors import TomdError
    from tomd.pipeline.dispatch import convert
    from tomd.readers.pdf.ocr import OcrMode

    try:
        result = convert(
            request.source,
            request.out_dir,
            revision=request.revision,
            pdf_backend=request.backend,
            ocr=OcrMode(request.ocr),
        )
        results.put(Succeeded(result))
    except TomdError as exc:
        results.put(Failed(type(exc).__name__, str(exc), exit_code(exc)))
    except Exception as exc:  # a bug: shown to the user instead of a silent failure
        text = "".join(traceback.format_exception(exc))[-4000:]
        results.put(Failed("crash", text, 1))


class ConversionJob:
    """One conversion in a spawned process; poll() it from the UI loop."""

    def __init__(self, request: ConversionRequest) -> None:
        context = mp.get_context("spawn")  # same behaviour on Windows, Linux and macOS
        self.request = request
        self._results: mp.Queue[Outcome] = context.Queue()
        self._process: BaseProcess = context.Process(
            target=_worker, args=(request, self._results), daemon=True
        )
        self._process.start()

    def poll(self) -> Outcome | None:
        """The outcome once the conversion has finished, else None."""
        try:
            outcome: Any = self._results.get_nowait()
        except queue.Empty:
            if self._process.is_alive():
                return None
            # Died without a word: killed, or a crash in native code.
            code = self._process.exitcode
            return Failed("crash", f"The conversion process ended unexpectedly (exit {code}).", 1)
        self._process.join(timeout=5)
        result: Outcome = outcome
        return result

    def cancel(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
