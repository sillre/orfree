"""``python -m orfree``."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--gui":
        from .gui import main as gui_main

        sys.exit(gui_main())
    sys.exit(main())
