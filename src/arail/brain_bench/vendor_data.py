"""Loader for the stamped Buddy prompt copy (vendor/buddy_prompt.v1.json)."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


@lru_cache(maxsize=1)
def prompt() -> Dict[str, Any]:
    return json.loads((VENDOR_DIR / "buddy_prompt.v1.json").read_text(encoding="utf-8"))


def provenance() -> Dict[str, Any]:
    return json.loads((VENDOR_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))


def prompt_sha256() -> str:
    return hashlib.sha256((VENDOR_DIR / "buddy_prompt.v1.json").read_bytes()).hexdigest()


def stamp_ok() -> bool:
    """The vendored file still matches the digest its PROVENANCE stamp records."""
    want = provenance()["files"]["buddy_prompt.v1.json"].split(":", 1)[1]
    return want == prompt_sha256()
