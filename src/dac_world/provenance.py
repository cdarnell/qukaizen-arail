"""Provenance recognizer -- port of DDaC's ``src/provenance.ts``.

The ``model:`` regex; tier is DERIVED from the corpus, never asserted.
Moved verbatim from qukaizen-arail's ``src/arail/world_forge.py`` as part of
the ``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).
"""

from __future__ import annotations

import re
from typing import Optional

# The `:` in the body class is load-bearing: without it an Ollama name:version
# tag (model:qwen2.5:7b) would launder a dreamed World to "sourced".
_MODEL_SOURCE_RE = re.compile(r"^model:[a-z0-9][a-z0-9._:/-]*$", re.I)


def tier_of_source(source: Optional[str]) -> str:
    return "model-asserted" if _MODEL_SOURCE_RE.match(str(source or "").strip()) else "sourced"


def compute_provenance_tier(sources: list[Optional[str]]) -> tuple[str, dict]:
    """Roll a corpus up to a World tier. "mixed" is computed, never authored."""
    model = sum(1 for s in sources if tier_of_source(s) == "model-asserted")
    total = len(sources)
    sourced = total - model
    tier = ("sourced" if total == 0 or model == 0
            else "model-asserted" if sourced == 0
            else "mixed")
    return tier, {"model": model, "sourced": sourced, "total": total}
