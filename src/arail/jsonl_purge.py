"""Generic streaming JSONL body-purge.

Shared by ``activity.py``'s legacy-bodies purge and ``agent_trace.py``'s
flight-recorder purge — REVIEW.md decision (c) (sprint
2026-09-20-buddy-front-and-center, operator-binding): "use the same purge
mechanism as the legacy-bodies purge (one mechanism, not two)". This module
has no dependency on either caller (nor on anything under ``arail.agents``
or ``arail.portal``), so importing it creates no new layering edge between
``activity.py`` and ``agent_trace.py`` — they stay siblings.

Streams each path line-by-line, atomic temp-file + ``os.replace`` per file,
malformed lines pass through byte-identical, every non-body field preserved
exactly, and the exact line count never changes.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable

_log = logging.getLogger(__name__)


def purge_jsonl_bodies(
    paths: list[Path],
    has_body: Callable[[dict], bool],
    strip_body: Callable[[dict], None],
) -> int:
    """For each path in *paths* that exists: stream it, parse each line as
    JSON, call ``has_body(event)`` to detect a body, ``strip_body(event)``
    to remove it **in place** (the caller stamps whatever "purged" marker
    its own schema uses), write the (possibly mutated) event back out, then
    atomically replace the original file. Malformed lines pass through
    byte-identical rather than being dropped or raising. Returns the total
    number of records purged across all paths. Never raises — a failure on
    one path is logged and the next path is still attempted.
    """
    purged = 0
    for path in paths:
        if not path.exists():
            continue
        tmp_path = path.with_name(path.name + ".purge_tmp")
        try:
            with open(path, "r") as src, open(tmp_path, "w") as dst:
                for line in src:
                    raw = line.rstrip("\n")
                    if not raw.strip():
                        dst.write(line)
                        continue
                    try:
                        event = json.loads(raw)
                    except (ValueError, TypeError):
                        dst.write(line)
                        continue
                    try:
                        if has_body(event):
                            strip_body(event)
                            purged += 1
                    except Exception:  # noqa: BLE001 - one bad record must not abort the purge
                        dst.write(line)
                        continue
                    dst.write(json.dumps(event, default=str) + "\n")
            os.replace(tmp_path, path)
        except OSError as e:
            _log.warning("jsonl_purge: purge failed for %s: %s", path, e)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return purged
