"""``python -m arail.buddy_host`` - what ``./arailctl buddy`` runs."""

from __future__ import annotations

import sys

from arail.buddy_host.link import main

if __name__ == "__main__":
    sys.exit(main())
