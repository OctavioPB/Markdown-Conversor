"""Build the Windows app and its installer.

    uv run python scripts/build_windows.py      # dist/tomd/ + dist/installer/tomd-setup-<v>.exe
    uv run python scripts/build_windows.py --no-installer

Steps: icon -> PyInstaller (packaging/tomd.spec) -> Tesseract (tesseract.exe, only the
DLLs it needs, and the spa/eng/osd data) -> THIRD_PARTY_LICENSES.txt -> a smoke test
of the packaged executable -> Inno Setup (packaging/tomd.iss).

Needs: PyInstaller (dev dependency), Tesseract installed (winget install
UB-Mannheim.TesseractOCR) with its data in %LOCALAPPDATA%\\tomd\\tessdata or TOMD_TESSDATA,
and Inno Setup 6 (winget install JRSoftware.InnoSetup) for the installer.
"""

import argparse
import importlib.metadata as metadata
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BUILD, DIST = ROOT / "build", ROOT / "dist"
APP = DIST / "tomd"
TESSERACT = Path(os.environ.get("TOMD_TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
LANGUAGES = ("spa", "eng", "osd")
# Runtime Python packages shipped in the app, for the licenses file.
PACKAGES = (
    "tomd", "lxml", "pypdfium2", "pypdfium2_raw", "pillow", "pytesseract", "packaging",
    "tkinterdnd2",
)  # fmt: skip
ISCC = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]
# DLLs every Windows has: never copied.
_SYSTEM_DLLS = ("api-ms-", "ext-ms-")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-installer", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    version = metadata.version("tomd")

    for folder in (BUILD / "tomd", BUILD / "tomd_gui", BUILD / "tomd_cli", APP):
        if folder.exists():
            shutil.rmtree(folder)  # only the build's own folders
    BUILD.mkdir(exist_ok=True)
    make_icon(BUILD / "tomd.ico")

    step("PyInstaller")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD), str(ROOT / "packaging" / "tomd.spec")],
        check=True, cwd=ROOT,
    )  # fmt: skip

    step("Tesseract")
    copied = copy_tesseract(APP / "tesseract")
    print(f"  tesseract.exe + {copied} DLL(s), languages: {', '.join(LANGUAGES)}")

    step("Licenses")
    write_licenses(APP / "THIRD_PARTY_LICENSES.txt")

    step("Smoke test of the packaged executable")
    smoke_test(APP / "tomd-cli.exe")
    gui = subprocess.run([str(APP / "tomd.exe"), "--self-test"], timeout=60)
    if gui.returncode != 0:
        raise SystemExit(f"  window: FAILED (exit {gui.returncode}; 3 = no drag and drop)")
    print("  window and drag and drop: ok")

    size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file()) / 2**20
    print(f"  app folder: {size:.0f} MB")
    if args.no_installer:
        return 0
    step("Installer")
    iscc = next((p for p in ISCC if p.is_file()), None) or shutil.which("ISCC")
    if iscc is None:
        print("  Inno Setup not found: winget install JRSoftware.InnoSetup")
        return 1
    subprocess.run(
        [str(iscc), f"/DAppVersion={version}", f"/DSourceDir={APP}",
         f"/DOutputDir={DIST / 'installer'}", str(ROOT / "packaging" / "tomd.iss")],
        check=True, cwd=ROOT / "packaging", stdout=subprocess.DEVNULL,
    )  # fmt: skip
    setup = DIST / "installer" / f"tomd-setup-{version}.exe"
    print(f"  {setup} ({setup.stat().st_size / 2**20:.0f} MB)")
    return 0


def step(name: str) -> None:
    print(f"\n== {name}", flush=True)


def make_icon(target: Path) -> None:
    """A plain icon: "md" on a blue rounded square, at the sizes Windows uses."""
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=48, fill=(29, 78, 137, 255))
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("segoeuib.ttf", 120)
    except OSError:
        font = ImageFont.load_default(120)
    draw.text((size / 2, size / 2 + 4), "md", font=font, fill="white", anchor="mm")
    image.save(
        target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )


# --- Tesseract ----------------------------------------------------------------------------


def copy_tesseract(target: Path) -> int:
    exe = TESSERACT / "tesseract.exe"
    if not exe.is_file():
        raise SystemExit(
            f"Tesseract not found in {TESSERACT}: winget install UB-Mannheim.TesseractOCR"
        )
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, target)
    needed = dll_closure(exe, TESSERACT)
    for dll in needed:
        shutil.copy2(TESSERACT / dll, target)
    data = Path(
        os.environ.get("TOMD_TESSDATA") or Path(os.environ["LOCALAPPDATA"]) / "tomd" / "tessdata"
    )
    (target / "tessdata").mkdir(exist_ok=True)
    for lang in LANGUAGES:
        shutil.copy2(data / f"{lang}.traineddata", target / "tessdata")
    for name in ("LICENSE", "AUTHORS"):
        if (TESSERACT / "doc" / name).is_file():
            shutil.copy2(TESSERACT / "doc" / name, target / f"TESSERACT_{name}.txt")
    return len(needed)


def dll_closure(exe: Path, folder: Path) -> list[str]:
    """DLLs of `folder` that `exe` loads, directly or through other DLLs."""
    available = {p.name.lower(): p.name for p in folder.glob("*.dll")}
    found: set[str] = set()
    pending = [exe]
    while pending:
        for name in pe_imports(pending.pop()):
            key = name.lower()
            if key in available and key not in found and not key.startswith(_SYSTEM_DLLS):
                found.add(key)
                pending.append(folder / available[key])
    return sorted(available[k] for k in found)


def pe_imports(path: Path) -> list[str]:
    """Names in a PE file's import and delay-import tables (enough of the format to
    follow DLL dependencies; no third-party parser needed)."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    sections_count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20B else 96)
    sections: list[tuple[int, int, int]] = []
    table = optional + optional_size
    for i in range(sections_count):
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, table + i * 40 + 8)
        sections.append((int(vaddr), int(max(vsize, rsize)), int(raddr)))

    def offset(rva: int) -> int | None:
        for vaddr, size, raddr in sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + raddr
        return None

    def name_at(rva: int) -> str:
        start = offset(rva)
        if start is None:
            return ""
        return data[start : data.index(b"\0", start)].decode("ascii", "replace")

    names: list[str] = []
    for index, stride, name_field in ((1, 20, 12), (13, 32, 4)):  # imports, delay imports
        rva = struct.unpack_from("<I", data, directories + index * 8)[0]
        cursor = offset(rva) if rva else None
        while cursor is not None and cursor + stride <= len(data):
            name_rva = struct.unpack_from("<I", data, cursor + name_field)[0]
            if not name_rva:
                break
            names.append(name_at(name_rva))
            cursor += stride
    return names


# --- Licenses -----------------------------------------------------------------------------


def write_licenses(target: Path) -> None:
    parts = [
        "tomd incluye software de terceros. Sus licencias, una por componente:\n",
        "tomd includes third-party software. Their licenses, one per component:\n",
    ]
    for package in PACKAGES:
        try:
            dist = metadata.distribution(package)
        except metadata.PackageNotFoundError:
            continue
        texts = []
        for entry in dist.files or []:
            if any(k in entry.name.upper() for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                file = Path(str(entry.locate()))
                if file.is_file():
                    texts.append(_read(file))
        expression = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        parts.append(_section(f"{dist.metadata['Name']} {dist.version}", expression, texts))
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        title = f"Python {sys.version.split()[0]} (incl. Tcl/Tk)"
        parts.append(_section(title, "PSF-2.0", [_read(python_license)]))
    parts.append(_section(
        "tkdnd 2 (drag and drop for Tk, shipped inside tkinterdnd2)", "BSD-3-Clause",
        ["Copyright (c) Georgios Petasis. Distributed under the BSD-style license of Tcl/Tk; "
         "see https://github.com/petasis/tkdnd/blob/master/license.terms"],
    ))  # fmt: skip
    tesseract_license = TESSERACT / "doc" / "LICENSE"
    if tesseract_license.is_file():
        title = "Tesseract OCR 5 and its libraries (UB Mannheim build); tessdata_fast data"
        parts.append(_section(title, "Apache-2.0", [_read(tesseract_license)]))
    target.write_text("\n".join(parts), encoding="utf-8", newline="\r\n")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _section(title: str, expression: str, texts: list[str]) -> str:
    rule = "=" * 78
    body = "\n\n".join(t.strip() for t in texts) or f"License: {expression}"
    return (
        f"\n{rule}\n{title}" + (f"  [{expression}]" if expression else "") + f"\n{rule}\n\n{body}\n"
    )


# --- Smoke test ---------------------------------------------------------------------------


def smoke_test(cli: Path) -> None:
    """The packaged command line converts a digital PDF, a scan (bundled Tesseract) and an
    EPUB, and runs a batch (workers started from the frozen executable)."""
    corpus = ROOT / "tests" / "corpus"
    cases = {
        "pdf": corpus / "pdf" / "synthetic-lists" / "input.pdf",
        "scan": corpus / "pdf-ocr" / "scan-clean-300dpi" / "input.pdf",
        "epub": corpus / "epub" / "synthetic-epub3-notes" / "input.epub",
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("TOMD_")}  # no dev paths
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if "Tesseract" not in p)
    with tempfile.TemporaryDirectory(prefix="tomd-smoke-") as tmp:
        version = subprocess.run([str(cli), "--version"], capture_output=True, text=True, env=env)
        print(f"  {version.stdout.strip()}")
        for name, source in cases.items():
            result = subprocess.run(
                [str(cli), "convert", str(source), "-o", str(Path(tmp) / name)],
                capture_output=True, text=True, encoding="utf-8", env=env,
            )  # fmt: skip
            if result.returncode != 0:
                raise SystemExit(f"  {name}: FAILED\n{result.stderr}")
            if name == "scan" and "pdf.ocr_applied" not in result.stderr:
                raise SystemExit(
                    f"  scan: OCR did not run with the bundled Tesseract\n{result.stderr}"
                )
            print(f"  {name}: ok")
        folder = Path(tmp) / "library"
        folder.mkdir()
        for name in ("pdf", "epub"):
            shutil.copyfile(cases[name], folder / cases[name].name.replace("input", name))
        batch = subprocess.run(
            [str(cli), "batch", str(folder), "-o", str(Path(tmp) / "batch"), "--ocr", "never"],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )  # fmt: skip
        if batch.returncode != 0:
            raise SystemExit(f"  batch: FAILED\n{batch.stderr}")
        print("  batch: ok")


if __name__ == "__main__":
    raise SystemExit(main())
