"""``python -m arail.nucleus`` → arail.nucleus.cli.main()."""

from __future__ import annotations

import sys

from arail.nucleus.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
