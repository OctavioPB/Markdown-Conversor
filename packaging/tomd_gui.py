"""Entry point of the installed desktop app (tomd.exe, no console window)."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Conversions and batches run in child processes started from this same executable.
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--self-test"]:
        # Used by scripts/build_windows.py: the window can be created and tkdnd (drag and
        # drop) loaded from the package. Exit code 0 when both hold.
        from tomd.ui.app import make_root

        root = make_root()
        ok = hasattr(root, "drop_target_register")
        root.destroy()
        sys.exit(0 if ok else 3)
    from tomd.ui.app import run

    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
