"""Stress test for `tomd batch` (PLAN phase 7), on generated inputs.

The phase is done when a batch of >= 500 PDFs from the private corpus finishes, survives
an interruption and resumes without duplicates, within the memory budget. The private
corpus is not readable from here, so this script builds an equivalent batch in a
temporary folder: corpus PDFs and EPUBs made unique, exact duplicates and damaged files.
It starts the batch, kills it hard mid-way (no clean shutdown, as a crash or power cut),
resumes it, and checks the result. The same check, on the private corpus:

    uv run tomd batch ~/tomd-private-corpus/sample.txt -o out/private
    # interrupt with Ctrl+C, run it again, then:
    uv run python scripts/stress_batch.py --verify out/private

    uv run python scripts/stress_batch.py --count 520 --kill-after 40
"""

import argparse
import io
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"


def build_inputs(folder: Path, count: int, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    pdfs = sorted((CORPUS / "pdf").glob("*/input.pdf"))
    epubs = sorted((CORPUS / "epub").glob("*/input.epub"))
    made = {"pdf": 0, "epub": 0, "duplicate": 0, "damaged": 0}
    for index in range(count):
        roll = rng.random()
        if roll < 0.02:  # damaged: truncated somewhere in the middle
            data = rng.choice(pdfs).read_bytes()
            (folder / f"damaged-{index:04d}.pdf").write_bytes(data[: len(data) // 3])
            made["damaged"] += 1
        elif roll < 0.04 and index:  # an exact duplicate of an earlier file
            earlier = rng.choice(sorted(folder.iterdir()))
            (folder / f"dup-{index:04d}{earlier.suffix}").write_bytes(earlier.read_bytes())
            made["duplicate"] += 1
        elif roll < 0.25:
            data = rng.choice(epubs).read_bytes()
            buffer = io.BytesIO(data)
            with zipfile.ZipFile(buffer, "a") as archive:
                archive.comment = f"stress copy {index}".encode()  # unique bytes, same book
            (folder / f"book-{index:04d}.epub").write_bytes(buffer.getvalue())
            made["epub"] += 1
        else:
            data = rng.choice(pdfs).read_bytes() + f"\n% stress copy {index}\n".encode()
            (folder / f"doc-{index:04d}.pdf").write_bytes(data)
            made["pdf"] += 1
    return made


def verify(out: Path, memory_mb: int) -> list[str]:
    """Problems found in a finished batch (empty when all is well)."""
    problems: list[str] = []
    db = sqlite3.connect(out / "manifest.sqlite")
    try:
        running = db.execute("SELECT COUNT(*) FROM documents WHERE status = 'running'").fetchone()
        if running[0]:
            problems.append(f"{running[0]} document(s) still 'running'")
        pending = db.execute("SELECT COUNT(*) FROM documents WHERE status = 'pending'").fetchone()
        if pending[0]:
            problems.append(f"{pending[0]} document(s) still 'pending'")
        outputs = [o for (o,) in db.execute("SELECT output FROM documents WHERE status='done'")]
        if len(outputs) != len(set(outputs)):
            problems.append("two documents share an output file")
        missing = [o for o in outputs if not (out / o).is_file()]
        if missing:
            problems.append(f"{len(missing)} output file(s) missing, e.g. {missing[0]}")
        files = {p.name for p in out.glob("*.md")} - {"report.md"}
        extra = files - set(outputs)
        if extra:
            problems.append(f"{len(extra)} Markdown file(s) not in the manifest (duplicates?)")
        peak = db.execute("SELECT MAX(peak_mb) FROM documents").fetchone()[0]
        if peak is not None and peak > memory_mb:
            problems.append(f"peak memory {peak:.0f} MB over the {memory_mb} MB budget")
    finally:
        db.close()
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", type=int, default=520)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--kill-after", type=float, default=40.0, help="seconds before the kill")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--verify", type=Path, help="only check an existing batch output")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.verify:
        problems = verify(args.verify, args.memory_mb)
        print("\n".join(problems) or "OK: no duplicates, nothing left running or pending.")
        return 1 if problems else 0

    with tempfile.TemporaryDirectory(prefix="tomd-stress-") as tmp:
        inputs, out = Path(tmp) / "in", Path(tmp) / "out"
        inputs.mkdir()
        made = build_inputs(inputs, args.count, args.seed)
        print(f"inputs: {made}")
        command = [
            sys.executable, "-m", "tomd.cli", "batch", str(inputs), "-o", str(out),
            "--workers", str(args.workers), "--memory-mb", str(args.memory_mb), "--ocr", "never",
        ]  # fmt: skip
        started = time.monotonic()
        first = subprocess.Popen(
            command, cwd=ROOT, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        )
        try:
            first.wait(timeout=args.kill_after)
            print("the first run finished before the kill; use a shorter --kill-after")
        except subprocess.TimeoutExpired:
            first.kill()  # no clean shutdown: the manifest keeps 'running' rows
            first.wait()
        db = sqlite3.connect(out / "manifest.sqlite")
        before = dict(
            db.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall()
        )
        db.close()
        print(f"killed after {time.monotonic() - started:.0f} s; manifest then: {before}")
        time.sleep(2)  # orphaned workers notice the closed pipe and exit
        second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        resumed = [
            line
            for line in second.stderr.splitlines()
            if "resuming" in line or "to convert" in line
        ]
        print("\n".join(resumed))
        db = sqlite3.connect(out / "manifest.sqlite")
        after = dict(
            db.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall()
        )
        attempts = db.execute("SELECT MAX(attempts), SUM(attempts > 1) FROM documents").fetchone()
        peak = db.execute("SELECT MAX(peak_mb) FROM documents").fetchone()[0]
        db.close()
        print(
            f"after resuming: {after}; converted more than once: {attempts[1]} "
            f"(max attempts {attempts[0]}); peak {peak} MB"
        )
        print(f"total {time.monotonic() - started:.0f} s")
        problems = verify(out, args.memory_mb)
        print(
            "\n".join(problems)
            or "OK: no duplicates, nothing left running or pending, within the memory budget."
        )
        return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
