"""Entry point for the packaged application."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orfree.gui import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
