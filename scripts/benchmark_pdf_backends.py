"""Benchmark PDF backends on the PDF corpus (PLAN phase 5, ADR-005).

For every case in tests/corpus/pdf and every backend, a fresh worker process converts
the input; the parent records wall time, time per page, peak memory (working set / RSS)
and the corpus metrics against expected.md. The markdown report feeds the ADR that picks
the default backend.

    uv run python scripts/benchmark_pdf_backends.py                     # heuristic only
    uv sync --extra docling
    uv run python scripts/benchmark_pdf_backends.py --backends heuristic docling --include-drafts
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_corpus  # noqa: E402  (sibling script)

# The worker measures its own peak memory: on Windows the venv python.exe is a launcher
# that starts the real interpreter, so measuring the child process from outside would
# report the launcher.
WORKER = """
import ctypes, json, sys
from pathlib import Path
from tomd.pipeline.dispatch import read_source, render_document
doc = read_source(sys.argv[1], pdf_backend=sys.argv[2])
_, result, _ = render_document(doc)
Path(sys.argv[3]).write_text(result.text, encoding="utf-8")
if sys.platform == "win32":
    class C(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (n, ctypes.c_size_t) for n in ("peak", "ws", "a", "b", "c", "d", "e", "f")]
    c = C(); c.cb = ctypes.sizeof(C)
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p  # 64-bit pseudo-handle
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    me = k32.GetCurrentProcess()
    psapi.GetProcessMemoryInfo(me, ctypes.byref(c), c.cb)
    peak = c.peak / 2**20
else:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps({"peak_mb": peak}))
"""


@dataclass
class Run:
    case: str
    backend: str
    ok: bool
    seconds: float
    pages: int
    peak_mb: float
    metrics: dict[str, float]
    error: str = ""


def _page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def _run_worker(source: Path, backend: str, out: Path) -> tuple[bool, float, float, str]:
    start = time.perf_counter()
    process = subprocess.run(
        [sys.executable, "-c", WORKER, str(source), backend, str(out)],
        capture_output=True,
        check=False,
    )
    seconds = time.perf_counter() - start
    if process.returncode:
        lines = process.stderr.decode("utf-8", "replace").strip().splitlines()
        return False, seconds, 0.0, lines[-1] if lines else "unknown error"
    report = json.loads(process.stdout.decode("utf-8").strip().splitlines()[-1])
    return True, seconds, float(report["peak_mb"]), ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", nargs="+", default=["heuristic"])
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "benchmark")
    args = parser.parse_args(argv)
    # Reports carry accents and typographic spaces; Windows consoles default to cp1252.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    cases = [
        c
        for c in eval_corpus.discover_cases(eval_corpus.CORPUS_ROOT)
        if c.source == "pdf"
        and (c.path / "expected.md").is_file()
        and (args.include_drafts or not c.is_draft)
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    runs: list[Run] = []
    for case in cases:
        expected = (case.path / "expected.md").read_text(encoding="utf-8")
        if expected.startswith(eval_corpus.DRAFT_MARKER):
            expected = expected.partition("\n")[2]
        pages = _page_count(case.input_path)
        for backend in args.backends:
            target = args.out / f"{case.name}.{backend}.md"
            ok, seconds, peak, error = _run_worker(case.input_path, backend, target)
            metrics: dict[str, float] = {}
            if ok:
                output = target.read_text(encoding="utf-8")
                metrics["text_similarity"] = eval_corpus.text_similarity(output, expected)
                metrics["heading_f1"] = eval_corpus.multiset_f1(
                    eval_corpus.headings(output), eval_corpus.headings(expected)
                )
                order = eval_corpus.reading_order(output, expected)
                if order is not None:
                    metrics["reading_order"] = order
            runs.append(Run(case.case_id, backend, ok, seconds, pages, peak, metrics, error))
            print(
                f"{case.case_id} [{backend}]: {'ok' if ok else 'FAILED ' + error}", file=sys.stderr
            )

    lines = [
        "# Benchmark de backends PDF",
        "",
        "Cada conversión corre en un proceso nuevo: el tiempo incluye el arranque y, para",
        "docling, la carga de modelos. La memoria es el pico del proceso.",
        "",
        "| Caso | Backend | Similitud | F1 títulos | Orden | s/página | Pico MB |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        m = r.metrics
        cells = [
            f"{m[k]:.3f}" if k in m else "—"
            for k in ("text_similarity", "heading_f1", "reading_order")
        ]
        status = "" if r.ok else f" (falla: {r.error})"
        lines.append(
            f"| {r.case}{status} | {r.backend} | {' | '.join(cells)} | "
            f"{r.seconds / max(r.pages, 1):.2f} | {r.peak_mb:.0f} |"
        )
    lines += ["", "## Promedios por backend", ""]
    for backend in args.backends:
        mine = [r for r in runs if r.backend == backend and r.ok]
        if not mine:
            lines.append(f"- {backend}: sin conversiones correctas")
            continue
        sim = sum(r.metrics["text_similarity"] for r in mine) / len(mine)
        spp = sum(r.seconds for r in mine) / sum(max(r.pages, 1) for r in mine)
        peak = max(r.peak_mb for r in mine)
        lines.append(f"- {backend}: similitud {sim:.3f}, {spp:.2f} s/página, pico {peak:.0f} MB")
    report = "\n".join(lines) + "\n"
    (args.out / "report.md").write_text(report, encoding="utf-8")
    (args.out / "runs.json").write_text(
        json.dumps([r.__dict__ for r in runs], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.stdout.write(report)
    return 0 if all(r.ok for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
