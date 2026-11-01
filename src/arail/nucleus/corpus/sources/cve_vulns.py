"""``kind: cve`` — a local commit-sha -> CVE-id mapping.

Reads ``<path>/mapping.json``: ``{"<sha>": "CVE-2026-NNNNN", ...}``. This is
a deliberately simple format for sprint 1 — the real kernel `vulns` repo's
own layout (per-CVE JSON records under ``cve/published/<year>/``) is a
richer schema than the fixture/test-corpus scope of this sprint needs;
mapping to this flat format is a small, explicit pre-processing step the
operator does once when staging item 10 for real (ARCHITECTURE.md
Assumption 9). License: treated as public vulnerability metadata,
redistributable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from arail.nucleus.errors import DomainConfigError

LICENSE = "CC0-1.0"
REDISTRIBUTABLE = True


def extract(vulns_path: Path) -> Dict[str, str]:
    vulns_path = Path(vulns_path)
    mapping_file = vulns_path / "mapping.json"
    if not mapping_file.is_file():
        raise DomainConfigError(f"no mapping.json at {mapping_file}")
    try:
        data = json.loads(mapping_file.read_text())
    except (OSError, ValueError) as exc:
        raise DomainConfigError(f"{mapping_file} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DomainConfigError(f"{mapping_file} must be a JSON object of sha -> CVE id")
    return {str(k): str(v) for k, v in data.items()}
