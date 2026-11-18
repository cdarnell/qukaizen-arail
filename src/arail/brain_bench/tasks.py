"""Frozen task set + sealed Worlds: loading and hash checks (FM19)."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = REPO_ROOT / "bench" / "buddy_brain"
WORLDS_DIR = REPO_ROOT / "lab" / "worlds"
TASKS_PATH = EVAL_DIR / "tasks.v1.jsonl"
MANIFEST_PATH = EVAL_DIR / "tasks.v1.manifest.json"
RULES_PATH = EVAL_DIR / "rules.v1.json"


class TaskError(RuntimeError):
    """A frozen input failed a check. ``code`` names the check; it never holds a value."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def world_seal(slug: str, worlds_dir: Optional[Path] = None) -> str:
    base = Path(worlds_dir) if worlds_dir else WORLDS_DIR
    return json.loads((base / slug / "manifest.json").read_text(encoding="utf-8"))["world_sha256"]


def load_world_cards(slug: str, worlds_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    base = Path(worlds_dir) if worlds_dir else WORLDS_DIR
    doc = json.loads((base / slug / "terms.json").read_text(encoding="utf-8"))
    return {t["slug"]: t for t in doc["terms"]}


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        return json.loads(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TaskError("manifest_unreadable") from None


def verify(tasks_path: Optional[Path] = None, manifest_path: Optional[Path] = None,
           worlds_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Check the tasks file sha256 and every World seal against the manifest.
    Returns the manifest; raises TaskError (fixed codes) on any mismatch."""
    tp = Path(tasks_path or TASKS_PATH)
    man = load_manifest(manifest_path)
    try:
        got = sha256_file(tp)
    except OSError:
        raise TaskError("tasks_unreadable") from None
    if got != man.get("tasks_sha256"):
        raise TaskError("tasks_hash_mismatch")
    for slug, want in (man.get("worlds") or {}).items():
        try:
            seal = world_seal(slug, worlds_dir)
        except (OSError, KeyError, ValueError):
            raise TaskError("world_unreadable") from None
        if seal != want:
            raise TaskError("world_seal_mismatch")
    return man


def load_tasks(tasks_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    out = []
    for line in Path(tasks_path or TASKS_PATH).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def counts_by_category(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(t["category"] for t in tasks))


def select(tasks: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    return [t for t in tasks if mode in t.get("modes", [])]
