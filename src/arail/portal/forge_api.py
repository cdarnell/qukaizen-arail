"""Model Forge (`/forge`) — a read-only DNA-card viewer (ARCHITECTURE.md
§4.13, item 5 reduced). Static on page load; no SSE, no build-triggering
here (that's the CLI's job — see docs/nucleus.md).

Every path param is regex-validated and realpath-confirmed under
FORGE_ROOT before touching disk (T-FORGE-2). Model outputs render only
inside markdown-it-py(html=False) + Jinja's default autoescape — never
raw HTML (T-FORGE-3).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

# JSON-only router. The
# two HTML page routes (/forge, /forge/{shard}/{version}) live directly in
# app.py instead, next to build_page() -- that's this codebase's actual
# convention (app.py owns `templates`/`_identity_ctx()`/`_require_surface()`
# as module-level singletons; a second Jinja2Templates instance here would
# miss the tier_surfaces/brand/lab_tier globals app.py registers on its own).
forge_router = APIRouter()

_SHARD_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MAX_CARD_BYTES = 256 * 1024


def safe_card_dir(shard: str, version: str) -> Path | None:
    """Public alias of _safe_card_dir -- app.py's page routes call this."""
    return _safe_card_dir(shard, version)


def _forge_root() -> Path:
    from arail.nucleus.paths import forge_root

    return forge_root()


def _safe_card_dir(shard: str, version: str) -> Path | None:
    if not _SHARD_RE.match(shard) or not _VERSION_RE.match(version):
        return None
    root = _forge_root().resolve()
    candidate = (root / shard / version).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _load_card_safely(card_path: Path) -> Dict[str, Any] | None:
    if not card_path.is_file() or card_path.stat().st_size > _MAX_CARD_BYTES:
        return None
    try:
        return yaml.safe_load(card_path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def _list_cards() -> List[Dict[str, Any]]:
    root = _forge_root()
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for shard_dir in sorted(root.iterdir()):
        if not shard_dir.is_dir() or not _SHARD_RE.match(shard_dir.name):
            continue
        for version_dir in sorted(shard_dir.iterdir()):
            if not version_dir.is_dir() or not _VERSION_RE.match(version_dir.name):
                continue
            card = _load_card_safely(version_dir / "dna-card.yaml")
            if card is None:
                continue
            out.append({
                "shard": shard_dir.name, "version": version_dir.name,
                "decision": card.get("fidelity", {}).get("decision", "?"),
                "runtime": card.get("runtime", "?"), "built": card.get("built", "?"),
            })
    return out


def _list_in_progress_runs() -> List[Dict[str, Any]]:
    import json

    from arail.nucleus.paths import nucleus_data

    runs_dir = nucleus_data() / "runs"
    out: List[Dict[str, Any]] = []
    if not runs_dir.is_dir():
        return out
    for run_dir in sorted(runs_dir.iterdir()):
        run_json = run_dir / "run.json"
        if not run_json.is_file():
            continue
        try:
            data = json.loads(run_json.read_text())
        except (OSError, ValueError):
            continue
        phases = data.get("phases", [])
        if phases and all(p.get("status") == "done" for p in phases):
            continue  # finished builds show up via _list_cards() instead
        out.append({"build_id": data.get("build_id", run_dir.name),
                   "domain": data.get("domain", "?"),
                   "phases": [{"phase": p.get("phase"), "status": p.get("status")} for p in phases]})
    return out


@forge_router.get("/api/forge/cards")
async def api_forge_cards() -> JSONResponse:
    return JSONResponse({"cards": _list_cards(), "in_progress": _list_in_progress_runs()})


def _verify_badge(card: Dict[str, Any]) -> str:
    signed = card.get("signed")
    if not signed:
        return "invalid"
    try:
        from arail.nucleus.cards.seal import verify as seal_verify

        result = seal_verify(card, fast=True)
    except Exception:  # noqa: BLE001 — a broken verify path must render "invalid", not 500
        return "invalid"
    # B2 (2026-09-23 review): a card whose metrics were hand-edited after
    # signing must never render "trusted" just because its signature is
    # otherwise self-consistent -- card_sha256 mismatch means SOMETHING
    # in the card doesn't match what was signed, regardless of key trust.
    if result.card_hash != "match":
        return "tampered"
    if result.signature != "valid":
        return "invalid"
    if signed.get("key_fingerprint") == "ephemeral-stub":
        return "STUB"
    return result.key  # "trusted" | "untrusted"


def list_cards() -> List[Dict[str, Any]]:
    return _list_cards()


def list_in_progress_runs() -> List[Dict[str, Any]]:
    return _list_in_progress_runs()


def load_card_safely(card_path: Path) -> Dict[str, Any] | None:
    return _load_card_safely(card_path)


def verify_badge(card: Dict[str, Any]) -> str:
    return _verify_badge(card)


def render_report_html(report_path: Path) -> str:
    if not report_path.is_file():
        return ""
    try:
        from markdown_it import MarkdownIt

        md = MarkdownIt("commonmark", {"html": False}).enable("table")
        return md.render(report_path.read_text())
    except Exception:  # noqa: BLE001 — a broken renderer must never 500 the page
        return ""
