"""Entry point of the installed desktop app (tomd.exe, no console window)."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Conversions and batches run in child processes started from this same executable.
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--self-test"]:
        # Used by scripts/build_windows.py: the whole window (library, review, theme, a
        # manifest) builds from the package and tkdnd (drag and drop) loads. Exit code 0
        # when both hold.
        import tempfile
        from pathlib import Path

        from tomd.ui.app import TomdApp, make_root

        with tempfile.TemporaryDirectory() as folder:
            root = make_root()
            root.withdraw()
            app = TomdApp(root, Path(folder))  # no settings file: nothing is saved
            ok = app.enable_drop()
            root.update()
            app.close()
        sys.exit(0 if ok else 3)
    from tomd.ui.app import run

    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
