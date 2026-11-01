"""ARCHITECTURE.md §7/§9 compliance block + F10 (licence drift is a build
break, not a surprise).

Apache-2.0 requires ARAIL, as a redistributor of a compiled Object form of
AeroLLM, to carry LICENSE + NOTICE + attribution alongside the binary and
in the repo. These tests assert the compliance material exists, is
non-empty, matches the upstream files byte-for-byte, and stays in lockstep
with the version pinned in pyproject.toml.
"""
from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = REPO / "THIRD-PARTY-LICENSES" / "aerollm"
AEROLLM_SIBLING = pathlib.Path.home() / "ProJects" / "qukaizen-queuellm"


def test_compliance_files_exist_and_nonempty():
    for name in ("LICENSE", "NOTICE", "README.md", "BUNDLE.json"):
        p = TPL_DIR / name
        assert p.exists(), p
        assert p.stat().st_size > 0, p


def test_root_notice_mentions_aerollm():
    notice = (REPO / "NOTICE").read_text()
    assert "AeroLLM" in notice
    assert "THIRD-PARTY-LICENSES/aerollm" in notice


def test_bundle_json_matches_pinned_tag():
    bundle = json.loads((TPL_DIR / "BUNDLE.json").read_text())
    pyproject = (REPO / "pyproject.toml").read_text()
    m = re.search(r'(?m)^aerollm_bundle_tag\s*=\s*"([^"]+)"', pyproject)
    assert m, "pyproject.toml is missing [tool.arail.package-sources] aerollm_bundle_tag"
    pinned_tag = m.group(1)
    assert bundle["arail_release"] == pinned_tag, (
        f"THIRD-PARTY-LICENSES/aerollm/BUNDLE.json.arail_release "
        f"({bundle['arail_release']!r}) is out of sync with pyproject.toml's "
        f"aerollm_bundle_tag ({pinned_tag!r}) — re-run "
        f"scripts/package-aerollm-bundle.sh and refresh BUNDLE.json (F10)."
    )
    assert bundle["schema"] == "arail.aerollm-bundle/v1"
    assert re.fullmatch(r"[0-9a-f]{40}", bundle["aerollm_commit"])


def test_notice_byte_identical_to_sibling_when_available():
    # This assertion only has teeth on a maintainer machine with the
    # sibling repo checked out; on CI/forks without it, skip rather than
    # fail (the compliance material is committed and doesn't need the
    # sibling to exist to be correct).
    if not (AEROLLM_SIBLING / "NOTICE").exists():
        return
    assert (TPL_DIR / "NOTICE").read_bytes() == (AEROLLM_SIBLING / "NOTICE").read_bytes()


def test_license_is_full_apache2_text_not_upstream_stub():
    # Upstream's own LICENSE file (~/ProJects/qukaizen-queuellm/LICENSE) is
    # only the Apache-2.0 header boilerplate + copyright line (17 lines) —
    # NOT the full ~200-line license text. Apache-2.0 §4(a) requires giving
    # recipients "a copy of this License," and NOTICE says "See the LICENSE
    # file for the full license text" — so byte-identity to upstream's stub
    # would make that sentence false. This bundle's LICENSE must therefore
    # carry the full, standard Apache-2.0 text (verbatim from
    # apache.org/licenses/LICENSE-2.0.txt), not a copy of upstream's stub.
    # See sprints/2026-08-05-arail-bundled-aerollm/REVIEW.md finding A1.
    text = (TPL_DIR / "LICENSE").read_text()
    for marker in (
        "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",
        "1. Definitions.",
        "2. Grant of Copyright License.",
        "7. Disclaimer of Warranty.",
        "END OF TERMS AND CONDITIONS",
    ):
        assert marker in text, f"LICENSE is missing full-text marker: {marker!r}"
    assert len(text.splitlines()) > 150, "LICENSE looks like the upstream stub, not the full text"
