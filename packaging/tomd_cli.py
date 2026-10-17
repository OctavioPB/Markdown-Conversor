"""Entry point of the installed command line (tomd-cli.exe): the same commands as `tomd`."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # batch workers start from this same executable
    from tomd.cli import main

    sys.exit(main())
