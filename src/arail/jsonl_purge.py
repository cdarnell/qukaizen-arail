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
) -> dict:
    """For each path in *paths* that exists: stream it, parse each line as
    JSON, call ``has_body(event)`` to detect a body, ``strip_body(event)``
    to remove it **in place** (the caller stamps whatever "purged" marker
    its own schema uses), write the (possibly mutated) event back out, then
    atomically replace the original file. Malformed lines pass through
    byte-identical rather than being dropped or raising. Never raises — a
    failure on one path is logged and the next path is still attempted; the
    original file for that path is left exactly as it was and no stray
    ``*.purge_tmp`` survives.

    QA F7 (TEST_REPORT.md): a path's records only ever counted toward
    ``purged`` once, at ``strip_body`` time, regardless of whether the
    ``os.replace`` that actually landed the rewrite ever happened — so a
    failed replace still reported ``{"purged": N}`` while every body
    stayed on disk unredacted. Each path's count is now accumulated in a
    local counter and only folded into the total *after* ``os.replace``
    for that path succeeds; a path whose replace fails contributes 0.

    Returns ``{"purged": <int, only from paths whose replace succeeded>,
    "ok": <bool, True iff every existing path replaced successfully>,
    "errors": [{"path": str, "error": str}, ...]}``. Callers that also
    hold an in-memory copy of the same records (B2's pattern) must check
    ``ok`` before clearing it — REVIEW.md F8: clearing memory after a
    failed disk rewrite hides the leak instead of removing it.
    """
    purged = 0
    ok = True
    errors: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        tmp_path = path.with_name(path.name + ".purge_tmp")
        path_purged = 0
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
                            path_purged += 1
                    except Exception:  # noqa: BLE001 - one bad record must not abort the purge
                        dst.write(line)
                        continue
                    dst.write(json.dumps(event, default=str) + "\n")
            os.replace(tmp_path, path)
            # Only now, after the replace that actually landed the
            # rewrite, does this path's count become real.
            purged += path_purged
        except OSError as e:
            ok = False
            errors.append({"path": str(path), "error": str(e)})
            _log.warning("jsonl_purge: purge failed for %s: %s", path, e)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return {"purged": purged, "ok": ok, "errors": errors}
