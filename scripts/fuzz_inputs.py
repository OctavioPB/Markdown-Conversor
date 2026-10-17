"""Fuzz the readers with malformed inputs (PLAN phase 8).

Each mutated input (truncated, bit-flipped, with a broken xref or garbled streams...) is
converted in its own subprocess, with a timeout, so that a crash inside PDFium or a hang
is caught instead of taking the run down. The contract every input must meet:

  - it converts, possibly with diagnostics, or
  - it fails with a `TomdError` (a clear, user-facing error).

Anything else is a finding: another exception, a crash, a timeout, or a peak memory over
the worker budget (CLAUDE.md: about 4 GB). Mutations are deterministic for a given seed.

    uv run python scripts/fuzz_inputs.py --iterations 200 --seed 1
    uv run python scripts/fuzz_inputs.py --keep out/fuzz-findings   # keep failing inputs
"""

import argparse
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"
# Small seeds keep a run fast; one real paper brings fonts, images and complex streams.
SEEDS = [
    *sorted((CORPUS / "pdf").glob("synthetic-*/input.pdf")),
    CORPUS / "pdf" / "emnlp-2023-main-1" / "input.pdf",
    *sorted((CORPUS / "wikipedia").glob("*/input.html"))[:3],
    *sorted((CORPUS / "epub").glob("synthetic-*/input.epub")),
    CORPUS / "epub" / "se-frankenstein" / "input.epub",
]
MEMORY_BUDGET_MB = 4096
TIMEOUT_S = 60.0

type Mutation = Callable[[bytes, random.Random], bytes]


# --- Mutations ------------------------------------------------------------------------------


def truncate(data: bytes, rng: random.Random) -> bytes:
    return data[: rng.randrange(0, len(data))]


def flip_bytes(data: bytes, rng: random.Random) -> bytes:
    out = bytearray(data)
    for _ in range(rng.randint(1, 32)):
        out[rng.randrange(len(out))] = rng.randrange(256)
    return bytes(out)


def splice_garbage(data: bytes, rng: random.Random) -> bytes:
    at = rng.randrange(len(data))
    return data[:at] + rng.randbytes(rng.randint(1, 4096)) + data[at:]


def drop_chunk(data: bytes, rng: random.Random) -> bytes:
    at = rng.randrange(len(data))
    return data[:at] + data[at + rng.randint(1, 2048) :]


def break_xref(data: bytes, rng: random.Random) -> bytes:
    """Remove or corrupt the cross-reference table and trailer (PDFium must rebuild)."""
    for token in (b"xref", b"startxref", b"trailer", b"/Root"):
        if token in data and rng.random() < 0.6:
            data = data.replace(token, rng.randbytes(len(token)))
    return data


def bad_lengths(data: bytes, rng: random.Random) -> bytes:
    """Stream /Length values that lie: negative, zero, or far past the end of the file."""
    choices = [b"0", b"-1", b"99999999", str(len(data) * 4).encode()]
    return re.sub(rb"/Length \d+", lambda _: b"/Length " + rng.choice(choices), data)


def garble_streams(data: bytes, rng: random.Random) -> bytes:
    """Random bytes inside compressed streams (Flate errors, broken images and fonts)."""

    def garble(m: re.Match[bytes]) -> bytes:
        body = bytearray(m.group(2))
        for _ in range(max(1, len(body) // 50)):
            if body:
                body[rng.randrange(len(body))] = rng.randrange(256)
        return m.group(1) + bytes(body) + m.group(3)

    return re.sub(rb"(stream\r?\n)(.*?)(endstream)", garble, data, flags=re.DOTALL)


def cyclic_refs(data: bytes, rng: random.Random) -> bytes:
    """Point object references at their own object (cycles in pages and resources)."""
    return re.sub(rb"(\d+) 0 R", lambda m: b"1 0 R" if rng.random() < 0.3 else m.group(0), data)


def html_nesting(data: bytes, rng: random.Random) -> bytes:
    depth = rng.choice([1_000, 10_000, 100_000])
    at = data.find(b"<body")
    at = data.find(b">", at) + 1 if at >= 0 else 0
    return data[:at] + b"<div><span>" * depth + b"x" + data[at:]


def html_entities(data: bytes, rng: random.Random) -> bytes:
    junk = [b"&#0;", b"&#xD800;", b"&#99999999;", b"&bogus;", b"\x00", b"\xff\xfe", b"&"]
    out = data
    for _ in range(rng.randint(1, 50)):
        at = rng.randrange(len(out))
        out = out[:at] + rng.choice(junk) + out[at:]
    return out


def html_unclosed(data: bytes, rng: random.Random) -> bytes:
    """Delete closing tags so that tables, lists and math never end."""
    return re.sub(
        rb"</(table|tr|td|ul|ol|li|math|section|p)>",
        lambda m: b"" if rng.random() < 0.5 else m.group(0),
        data,
    )


def _rezip(data: bytes, rng: random.Random, change: Callable[[str, bytes], bytes | None]) -> bytes:
    """Apply `change` to one member of a ZIP (None deletes it); raw bytes if not a ZIP."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as src:
            members = [(i.filename, src.read(i)) for i in src.infolist()]
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError):
        return flip_bytes(data, rng)
    target = rng.choice([name for name, _ in members if name != "mimetype"] or ["mimetype"])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, content in members:
            new = change(name, content) if name == target else content
            if new is not None:
                dst.writestr(name, new)
    return out.getvalue()


def zip_member_truncated(data: bytes, rng: random.Random) -> bytes:
    return _rezip(data, rng, lambda _, b: b[: rng.randrange(0, len(b) + 1)])


def zip_member_garbled(data: bytes, rng: random.Random) -> bytes:
    return _rezip(data, rng, lambda _, b: flip_bytes(b, rng) if b else rng.randbytes(64))


def zip_member_deleted(data: bytes, rng: random.Random) -> bytes:
    return _rezip(data, rng, lambda _, b: None)


def zip_member_nested(data: bytes, rng: random.Random) -> bytes:
    """Deeply nested markup or a huge attribute in one XML member."""
    payload = rng.choice([b"<div>" * 50_000, b'<p class="' + b"x" * 1_000_000 + b'">'])
    return _rezip(data, rng, lambda _, b: b.replace(b"<body", payload + b"<body", 1))


EPUB_MUTATIONS: list[Mutation] = [
    truncate,
    flip_bytes,
    drop_chunk,
    zip_member_truncated,
    zip_member_garbled,
    zip_member_deleted,
    zip_member_nested,
]
PDF_MUTATIONS: list[Mutation] = [
    truncate,
    flip_bytes,
    splice_garbage,
    drop_chunk,
    break_xref,
    bad_lengths,
    garble_streams,
    cyclic_refs,
]
HTML_MUTATIONS: list[Mutation] = [
    truncate,
    flip_bytes,
    drop_chunk,
    html_nesting,
    html_entities,
    html_unclosed,
]


# --- Worker (one input per subprocess) -------------------------------------------------------


def _peak_memory_mb() -> float:
    if sys.platform == "win32":
        import ctypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (n, ctypes.c_size_t) for n in ("peak", "ws", "a", "b", "c", "d", "e", "f")
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p  # 64-bit pseudo-handle
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return float(counters.peak) / 2**20
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def run_one(path: Path, ocr: str) -> dict[str, object]:
    """Convert one input in this process; report the outcome as a dict."""
    from tomd.errors import TomdError
    from tomd.pipeline.dispatch import read_source, render_document
    from tomd.readers.pdf.ocr import OcrMode

    try:
        _, result, _ = render_document(read_source(str(path), ocr=OcrMode(ocr)))
        outcome, detail = "converted", f"{len(result.text)} chars"
    except TomdError as exc:
        outcome, detail = "rejected", f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # the finding this script exists to catch
        import traceback

        outcome, detail = "exception", "".join(traceback.format_exception(exc))[-3000:]
    return {"outcome": outcome, "detail": detail, "peak_mb": round(_peak_memory_mb(), 1)}


# --- Driver ----------------------------------------------------------------------------------


@dataclass
class Finding:
    name: str
    outcome: str
    detail: str


def _check(path: Path, ocr: str, timeout: float) -> dict[str, object]:
    command = [sys.executable, str(Path(__file__).resolve()), "--run-one", str(path), "--ocr", ocr]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "detail": f"> {timeout:.0f} s", "peak_mb": 0.0}
    lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if proc.returncode != 0 or not lines:
        stderr = proc.stderr.decode("utf-8", "replace")[-2000:]
        return {"outcome": "crash", "detail": f"exit {proc.returncode}: {stderr}", "peak_mb": 0.0}
    result: dict[str, object] = json.loads(lines[-1])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ocr", default="never", choices=["auto", "always", "never"])
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    parser.add_argument("--keep", type=Path, help="folder for inputs that produced findings")
    parser.add_argument("--run-one", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.run_one:
        print(json.dumps(run_one(args.run_one, args.ocr)))
        return 0

    rng = random.Random(args.seed)
    outcomes: Counter[str] = Counter()
    rejections: Counter[str] = Counter()
    peak_max = 0.0
    findings: list[Finding] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tomd-fuzz-") as tmp:
        for i in range(args.iterations):
            seed_path = SEEDS[i % len(SEEDS)]
            mutations = {".pdf": PDF_MUTATIONS, ".epub": EPUB_MUTATIONS}.get(
                seed_path.suffix, HTML_MUTATIONS
            )
            mutation = rng.choice(mutations)
            data = mutation(seed_path.read_bytes(), rng)
            name = f"{i:04d}-{seed_path.parent.name}-{mutation.__name__}{seed_path.suffix}"
            path = Path(tmp) / name
            path.write_bytes(data)
            result = _check(path, args.ocr, args.timeout)
            outcome = str(result["outcome"])
            peak = float(result.get("peak_mb", 0.0))  # type: ignore[arg-type]
            if outcome in ("converted", "rejected") and peak > MEMORY_BUDGET_MB:
                outcome = "memory"
                result["detail"] = f"peak {peak:.0f} MB > {MEMORY_BUDGET_MB} MB"
            outcomes[outcome] += 1
            peak_max = max(peak_max, peak)
            if outcome == "rejected":
                # Group messages by shape: file names and numbers vary per input.
                rejections[re.sub(r"\S+\.(pdf|html)|\d+", "…", str(result["detail"]))] += 1
            if outcome not in ("converted", "rejected"):
                findings.append(Finding(name, outcome, str(result["detail"])))
                if args.keep:
                    args.keep.mkdir(parents=True, exist_ok=True)
                    (args.keep / name).write_bytes(data)
    elapsed = time.monotonic() - started

    print(
        f"{args.iterations} inputs in {elapsed:.0f} s (seed {args.seed}): "
        + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items()))
        + f"; peak memory {peak_max:.0f} MB"
    )
    for message, count in rejections.most_common():
        print(f"  rejected x{count}: {message}")
    for f in findings:
        print(f"\n## {f.name} [{f.outcome}]\n{f.detail}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
