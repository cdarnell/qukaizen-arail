"""T-ENV (F2), T-PIN (F4) and the sealed-bytes checks (F3, F5) for the
QueueLLM display rename.

The rename changes what a person reads. Everything a machine reads must be
byte-for-byte what it was at the sprint base: env var names, ids, package and
module names, the hash-pinned bundle, the bundled NOTICE, sealed world files.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE = "5f775f1c"        # origin/main when sprint 2026-10-07-queuellm-display-rename opened

# Machine-read tokens. Their per-file occurrence counts must not shrink.
ENV_VAR = re.compile(r"[A-Z0-9_]*AERO(?:LLM)?_[A-Z0-9_]*|[A-Z0-9_]*_AEROLLM\b")
FROZEN_ID = re.compile(
    r"libaerollm_api|aerollm[_-]api|AeroLLMBackend|tier1-aerollm|backend_aerollm|"
    r"aerollm-mlx|aerollm-cuda|tn-arch-aerollm|show_aerollm|aerollm_status|"
    r"aerollm_model|aerollm_preload_loop|_record_aerollm_bench|optimize-aerollm|"
    r"aerollm_version|runtime: 'aerollm'|value=\"aerollm\"|"
    r"(?<=[\"'])aerollm(?=[\"'])"
)


def _git(*args: str) -> str | None:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _base_available() -> bool:
    return _git("cat-file", "-e", f"{BASE}^{{commit}}") is not None


needs_base = pytest.mark.skipif(
    not _base_available(), reason=f"base commit {BASE} not in this clone"
)


def _changed_files() -> list[str]:
    out = _git("diff", "--name-only", "--diff-filter=M", BASE) or ""
    skip = ("sprints/", "tests/", "docs/archive/", "retros/", "learnings/")
    return [f for f in out.splitlines() if not f.startswith(skip)]


@needs_base
def test_env_vars_and_frozen_ids_unchanged_in_every_modified_file():
    drift = []
    for rel in _changed_files():
        old = _git("show", f"{BASE}:{rel}")
        new_path = ROOT / rel
        if old is None or not new_path.is_file():
            continue
        new = new_path.read_text(encoding="utf-8")
        for name, rx in (("env var", ENV_VAR), ("frozen id", FROZEN_ID)):
            before, after = Counter(rx.findall(old)), Counter(rx.findall(new))
            lost = before - after          # a rename deletes tokens; adding is fine
            if lost:
                drift.append(f"{rel}: {name} tokens lost or renamed: {dict(lost)}")
    assert not drift, "frozen machine-read tokens drifted:\n" + "\n".join(drift)


@needs_base
def test_sealed_and_frozen_paths_are_byte_identical_to_base():
    frozen = ["NOTICE", "THIRD-PARTY-LICENSES", "licenses", "lab/worlds",
              "pyproject.toml", "uv.lock", "CHANGELOG.md", "models/graduated",
              "eval", "research/aerollm", "docs/archive"]
    out = _git("diff", "--stat", BASE, "--", *frozen)
    assert out == "", f"frozen paths changed:\n{out}"


def test_bundle_pin_constants_in_build_script_are_untouched():
    text = (ROOT / "scripts" / "build-aerollm.sh").read_text(encoding="utf-8")
    for line in (
        'AEROLLM_BUNDLE_REPO="${AEROLLM_BUNDLE_REPO:-cdarnell/qukaizen-arail}"',
        'AEROLLM_BUNDLE_TAG="${AEROLLM_BUNDLE_TAG:-$(_read_bundle_pin aerollm_bundle_tag)}"',
        'AEROLLM_BUNDLE_TAG="${AEROLLM_BUNDLE_TAG:-v1.1.0}"',
        'AEROLLM_BUNDLE_SHA256="${AEROLLM_BUNDLE_SHA256:-$(_read_bundle_pin aerollm_bundle_sha256)}"',
        'AEROLLM_PIP_SPEC="${AEROLLM_PIP_SPEC:-aerollm-api}"',
    ):
        assert line in text, line


def test_pyproject_bundle_pin_is_untouched():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'aerollm_bundle_tag = "v1.1.0"' in text
    assert ('aerollm_bundle_sha256 = '
            '"57f30364738580d4a7808f9165ddfdf692eb52afaa435e491b4eda39afb63e93"') in text


def test_bundled_notice_and_bundle_manifest_are_byte_identical():
    """Rebranding the NOTICE belongs to the bundle re-pin sprint (A4)."""
    def sha(p: str) -> str:
        return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
    assert sha("NOTICE") == (
        "8cc646636af545841abe680820222b82792fe718e978e31d97418febc7e7dfe9")
    assert sha("THIRD-PARTY-LICENSES/aerollm/BUNDLE.json") == (
        "3f3e85a73dce7a230ecad76eddd6c3bc9597f0f9a50de00aabcbd7cf1a574880")
