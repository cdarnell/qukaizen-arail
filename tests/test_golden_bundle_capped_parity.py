"""Cross-repo capped-path golden-bundle parity test (F7) — ARAIL side.

Mirror of qukaizen-dac's ``tests/python/test_golden_bundle_capped_parity.py``.
``tests/fixtures/golden-bundle-capped/`` here is byte-identical to
qukaizen-dac's ``tests/python/fixtures/golden-bundle-capped/`` (both
committed, not generated at test time — see
``sprints/2026-08-27-heavy-world-model/BUILD_LOG.md`` step 6 in qukaizen-dac
for how they were produced). Unlike the original 2-term ``golden-bundle``
fixture (which always takes ``_skill_terms_capped``'s under-budget early
return and can never exercise the capped branch), this is a 300-term
synthetic corpus, deliberately over the measured SKILL budget, mixing
bare-string and typed ``{slug,rel}`` related edges across the SAME corpus
(the F5 degree-by-slug regression shape) — so a future selector rewrite
that reintroduces the ``str(dict)`` degree bug is caught by parity here,
which the 2-term fixture structurally cannot do.

This test asserts:
1. The golden fixture round-trips ARAIL's own consumer
   (``world_mount.load_bundle`` + ``verify_seal`` + ``check_compat`` +
   ``check_categories``).
2. A bundle freshly emitted through THIS repo's ``world_forge`` shim (i.e.
   through ``dac_world`` via the shim, exactly as ARAIL's portal would call
   it) with the same pinned spec/terms/``created_at`` is byte-identical to
   the committed fixture — proving the shim doesn't silently diverge from
   what qukaizen-dac itself would emit.
3. The fixture actually exercises the capped branch (kept < 300 terms, the
   honest over-budget note is present) — proving this fixture is not vacuous.
"""

from __future__ import annotations

import json
from pathlib import Path

import arail.world_forge as wf
import arail.world_mount as wm

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "golden-bundle-capped"

SPEC = {
    "slug": "golden-capped",
    "display_name": "Golden Capped Fixture",
    "categories": [
        {"id": "alpha", "label": "Alpha"},
        {"id": "beta", "label": "Beta"},
    ],
    "knowledge_sources": [
        {"kind": "model", "ref": "model:test", "trust": "model-asserted", "holder": "test"},
    ],
}

CREATED_AT = "1970-01-01T00:00:00.000Z"
N_TERMS = 300


def _make_terms(n: int = N_TERMS) -> list[dict]:
    """Deterministic >253-term synthetic corpus — byte-for-byte the same
    generator as qukaizen-dac's twin test, so both repos regenerate the
    identical fixture from source."""
    terms = []
    for i in range(n):
        slug = f"capped-term-{i:04d}"
        cat = "alpha" if i % 2 == 0 else "beta"
        nxt1 = f"capped-term-{(i + 1) % n:04d}"
        nxt2 = f"capped-term-{(i + 2) % n:04d}"
        if i % 3 == 0:
            related = [{"slug": nxt1, "rel": "prerequisite-of"}, nxt2]
        elif i % 3 == 1:
            related = [nxt1, {"slug": nxt2, "rel": "part-of"}]
        else:
            related = [nxt1, nxt2]
        terms.append({
            "slug": slug,
            "term": f"Capped Term {i:04d}",
            "category": cat,
            "short": f"A deterministic fixture term number {i:04d} used to exercise the measured-size capped-selection path.",
            "definition": f"Long-form definition text for capped term {i:04d}, unused by SKILL.md rendering but present for gate/provenance completeness in this synthetic corpus.",
            "example": f"Example usage sentence referencing capped term {i:04d}.",
            "related": related,
            "source": "model:test",
        })
    return terms


TERMS = _make_terms()


def test_fixture_actually_exercises_the_capped_branch():
    skill = (FIXTURE_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "most connected" in skill, "fixture must exercise the CAPPED branch (honest note present)"
    terms = json.loads((FIXTURE_DIR / "terms.json").read_bytes())["terms"]
    assert len(terms) == N_TERMS
    assert len(terms) > 253


def test_golden_capped_fixture_round_trips_arail_consumer():
    bundle = wm.load_bundle(FIXTURE_DIR)
    assert wm.verify_seal(bundle).ok
    wm.check_compat(bundle)
    wm.check_categories(bundle)
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_bytes())
    assert manifest["world"] == "golden-capped"
    assert manifest["world_sha256"] == manifest["files"]["terms.json"]


def test_bundle_emitted_through_the_shim_is_byte_identical_to_the_golden_capped_fixture(tmp_path):
    out = wf.write_bundle(tmp_path / "golden-capped", SPEC, TERMS, created_at=CREATED_AT)
    for path in sorted(FIXTURE_DIR.iterdir()):
        fresh = (out / path.name).read_bytes()
        golden = path.read_bytes()
        assert fresh == golden, f"{path.name} diverged from the committed capped-path golden fixture"
