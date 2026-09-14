"""Cross-repo golden-bundle parity test (Failures F2/F9) — ARAIL side.

Mirror of qukaizen-ddac's ``tests/python/test_golden_bundle_parity.py``.
``tests/fixtures/golden-bundle/`` here is byte-identical to qukaizen-ddac's
``tests/python/fixtures/golden-bundle/`` (both committed, not generated at
test time — see BUILD_LOG.md step 7 in qukaizen-ddac's sprint folder for how
they were produced). This test asserts:

1. The golden fixture round-trips ARAIL's own consumer
   (``world_mount.load_bundle`` + ``verify_seal`` + ``check_compat`` +
   ``check_categories``).
2. A bundle freshly emitted through THIS repo's ``world_forge`` shim (i.e.
   through ``dac_world`` via the shim, exactly as ARAIL's portal would call
   it) with the same pinned spec/terms/``created_at`` is byte-identical to
   the committed fixture — proving the shim doesn't silently diverge from
   what qukaizen-ddac itself would emit.
"""

from __future__ import annotations

import json
from pathlib import Path

import arail.world_forge as wf
import arail.world_mount as wm

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "golden-bundle"

SPEC = {
    "slug": "golden",
    "display_name": "Golden Fixture",
    "categories": [
        {"id": "alpha", "label": "Alpha"},
        {"id": "beta", "label": "Beta"},
    ],
    "knowledge_sources": [
        {"kind": "model", "ref": "model:test", "trust": "model-asserted", "holder": "test"},
    ],
}

TERMS = [
    {"slug": "first-term", "term": "First Term", "category": "alpha",
     "short": "The first fixture term.",
     "definition": "A stable, hand-authored fixture term used for cross-repo parity testing.",
     "example": "Used identically in both repos' golden-bundle tests.",
     "related": ["second-term"], "source": "model:test"},
    {"slug": "second-term", "term": "Second Term", "category": "beta",
     "short": "The second fixture term.",
     "definition": "Pairs with First Term to exercise category + related-graph closure.",
     "example": "Also used identically in both repos' golden-bundle tests.",
     "related": ["first-term"], "source": "model:test"},
]

CREATED_AT = "1970-01-01T00:00:00.000Z"


def test_golden_fixture_round_trips_arail_consumer():
    bundle = wm.load_bundle(FIXTURE_DIR)
    assert wm.verify_seal(bundle).ok
    wm.check_compat(bundle)
    wm.check_categories(bundle)
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_bytes())
    assert manifest["world"] == "golden"
    assert manifest["world_sha256"] == manifest["files"]["terms.json"]


def test_bundle_emitted_through_the_shim_is_byte_identical_to_the_golden_fixture(tmp_path):
    out = wf.write_bundle(tmp_path / "golden", SPEC, TERMS, created_at=CREATED_AT)
    for path in sorted(FIXTURE_DIR.iterdir()):
        fresh = (out / path.name).read_bytes()
        golden = path.read_bytes()
        assert fresh == golden, f"{path.name} diverged from the committed golden fixture"
