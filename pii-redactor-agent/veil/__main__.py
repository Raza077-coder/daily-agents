"""Allow `python -m veil ...` as an alias for the `veil` entry point."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
