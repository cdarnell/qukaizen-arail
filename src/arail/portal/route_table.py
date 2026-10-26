"""Flatten a FastAPI app's route table, including routers pulled in with ``include_router``.

On FastAPI 0.140 ``app.routes`` holds ``_IncludedRouter`` objects for every ``include_router``
call; walking only the top level silently skips them (ARCHITECTURE.md, correction 4). Used by the
auth sweep tests and ``scripts/s4-win.sh`` so "every route" means every route.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    path: str
    methods: frozenset  # empty for mounts and websockets
    kind: str           # "route" | "mount" | "websocket"


def _walk(routes, prefix: str, out: list) -> None:
    for r in routes:
        name = type(r).__name__
        if name == "_IncludedRouter":
            ctx = getattr(r, "include_context", None)
            inner = getattr(r, "original_router", None)
            sub = prefix + (getattr(ctx, "prefix", "") or "")
            _walk(getattr(inner, "routes", []), sub, out)
        elif name == "Mount":
            out.append(Entry(prefix + r.path, frozenset(), "mount"))
        elif "websocket" in name.lower():
            out.append(Entry(prefix + r.path, frozenset(), "websocket"))
        elif hasattr(r, "path"):
            methods = frozenset(getattr(r, "methods", None) or ())
            out.append(Entry(prefix + r.path, methods, "route"))
        elif hasattr(r, "routes"):
            _walk(r.routes, prefix, out)


def flatten(app) -> list:
    out: list = []
    _walk(app.routes, "", out)
    return out


def sample_path(path: str) -> str:
    """Fill ``{param}`` / ``{param:path}`` placeholders with a harmless value."""
    import re
    return re.sub(r"\{[^}]+\}", "x", path)
