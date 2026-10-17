# PyInstaller spec: two executables sharing one folder (scripts/build_windows.py).
#   tomd.exe      desktop app, no console window
#   tomd-cli.exe  command line
# Tesseract and the third-party licenses are added by the build script afterwards.
# ruff: noqa

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (provided by PyInstaller)
ICON = str(ROOT / "build" / "tomd.ico")
# Never needed by the installed app: the optional docling backend (models of several GB)
# and the development tools.
EXCLUDES = [
    "docling", "torch", "transformers", "numpy", "pandas", "matplotlib", "scipy",
    "reportlab", "pytest", "hypothesis", "mypy", "ruff", "pypandoc", "rapidfuzz",
    "markdown_it", "mdit_py_plugins", "IPython",
]


def analysis(script: str):
    return Analysis(  # noqa: F821
        [str(ROOT / "packaging" / script)],
        pathex=[str(ROOT / "src")],
        hiddenimports=["tomd.cli", "tomd.ui.app", "tomd.pipeline.batch"],
        excludes=EXCLUDES,
        noarchive=False,
    )


gui = analysis("tomd_gui.py")
cli = analysis("tomd_cli.py")

gui_exe = EXE(  # noqa: F821
    PYZ(gui.pure),  # noqa: F821
    gui.scripts,
    [],
    exclude_binaries=True,
    name="tomd",
    icon=ICON,
    console=False,
    upx=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure),  # noqa: F821
    cli.scripts,
    [],
    exclude_binaries=True,
    name="tomd-cli",
    icon=ICON,
    console=True,
    upx=False,
)
COLLECT(  # noqa: F821
    gui_exe,
    gui.binaries,
    gui.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    name="tomd",
    upx=False,
)
