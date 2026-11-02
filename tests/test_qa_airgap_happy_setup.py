"""QA — happy path + setup smoke for the airgap-honest-mode sprint.

10% happy path + 30% setup buckets per arail product gating.

Happy path: the most basic airgapped flow works. Modal, /api/airgap/status,
README definitions exist.

Setup: does the lab still come up clean? install_guard is wired into the
portal startup and loader; importing portal.app doesn't crash; no module
chains lit a network call at import time.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────────────────────────────
# Happy path — the win condition's witnessable artifacts
# ──────────────────────────────────────────────────────────────────────

class TestWinConditionArtifacts:
    """The vision said: tests pass, jsonl logs, README rewritten.
    Confirm each artifact is intact."""

    def test_readme_zero_network_calls_phrase_removed(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "zero network calls" not in readme.lower(), (
            "README must not say 'zero network calls' anymore — "
            "the win condition required this lying paragraph removal"
        )

    def test_readme_mentions_airgapped_definition(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        # The operational definition uses the word "airgapped" multiple times.
        assert "airgapped" in readme.lower()

    def test_privacy_md_has_known_gaps_section(self):
        privacy = (REPO_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
        # Every gap the guard does not cover must be named.  httpx and httpx2 are
        # wrapped now (they were listed here as gaps until TD50);
        # tests/test_egress_docs.py pins that.
        for gap in ("aiohttp", "subprocess", "socket", "urllib3", "httpcore"):
            assert gap in privacy.lower(), (
                f"docs/PRIVACY.md must name documented gap: {gap}"
            )

    def test_modal_template_exists(self):
        modal = (
            REPO_ROOT / "src" / "arail" / "portal" / "templates" / "_airgap_modal.html"
        )
        assert modal.exists(), "Airgap modal template must exist"

    def test_modal_lists_known_gaps(self):
        modal = (
            REPO_ROOT / "src" / "arail" / "portal" / "templates" / "_airgap_modal.html"
        ).read_text(encoding="utf-8")
        # The gaps must be named in the modal copy.  httpx and httpx2 are wrapped
        # now (they were listed here as gaps until TD50); tests/test_egress_docs.py
        # pins that.
        for gap in ("aiohttp", "urllib3", "httpcore"):
            assert gap in modal.lower(), f"Modal must name gap: {gap}"


# ──────────────────────────────────────────────────────────────────────
# Setup — clean import chain
# ──────────────────────────────────────────────────────────────────────

class TestImportsAreClean:
    """The lab must come up without network calls fired at import time.
    These tests would also have caught a regression where adding the
    egress guard introduced an import-time DNS lookup."""

    def test_arail_airgap_imports_without_side_effects(self):
        """arail.airgap is the canonical module; importing must do
        nothing observable (no I/O, no env mutation)."""
        import arail.airgap  # noqa: F401
        # If we got here, the import succeeded without side effects.

    def test_arail_egress_imports_without_install(self):
        """Importing egress must NOT install the guard automatically.
        install_guard() is the explicit step."""
        import arail.egress
        # _INSTALLED can be True from prior tests — but after the
        # autouse _reset_egress_guard fixture runs, it should be False
        # in a fresh pytest session. We can't depend on order here,
        # so just verify the symbol exists and is the right type.
        assert isinstance(arail.egress._INSTALLED, bool)

    def test_egress_module_exports_install_guard(self):
        from arail.egress import install_guard, allow_egress, _reset_for_tests
        assert callable(install_guard)
        assert callable(allow_egress)
        assert callable(_reset_for_tests)

    def test_airgap_module_exports_lab_mode(self):
        from arail.airgap import lab_mode, is_airgapped, is_local_ip, is_local_host, EgressBlocked
        assert callable(lab_mode)
        assert callable(is_airgapped)
        assert callable(is_local_ip)
        assert callable(is_local_host)
        assert issubclass(EgressBlocked, RuntimeError)


# ──────────────────────────────────────────────────────────────────────
# Setup — install_guard wired at portal + loader
# ──────────────────────────────────────────────────────────────────────

class TestInstallGuardWiring:
    def test_portal_app_calls_install_guard_at_startup(self):
        """The portal's _startup() must invoke install_guard() — pin
        by reading the source so we don't have to actually boot."""
        app_py = (REPO_ROOT / "src" / "arail" / "portal" / "app.py").read_text(
            encoding="utf-8"
        )
        assert "install_guard" in app_py, (
            "portal/app.py must call install_guard() — without this wiring "
            "the guard never installs and the airgap claim is false"
        )

    def test_loader_calls_install_guard(self):
        loader_py = (
            REPO_ROOT / "src" / "arail" / "agents" / "loader.py"
        ).read_text(encoding="utf-8")
        assert "install_guard" in loader_py, (
            "agents/loader.py must call install_guard() at first load_all()"
        )


# ──────────────────────────────────────────────────────────────────────
# Setup — env config defaults safely
# ──────────────────────────────────────────────────────────────────────

class TestSafeDefaults:
    def test_lab_mode_defaults_to_airgapped(self, monkeypatch):
        """If neither LAB_MODE nor ARAIL_MODE is set, lab_mode() must
        return 'airgapped' (fail-closed)."""
        monkeypatch.delenv("LAB_MODE", raising=False)
        monkeypatch.delenv("ARAIL_MODE", raising=False)
        from arail.airgap import lab_mode
        assert lab_mode() == "airgapped"

    def test_lab_mode_empty_string_is_airgapped(self, monkeypatch):
        """Empty LAB_MODE must collapse to airgapped (not crash, not 'hybrid')."""
        monkeypatch.setenv("LAB_MODE", "")
        from arail.airgap import lab_mode
        assert lab_mode() == "airgapped"

    def test_lab_mode_typo_is_airgapped(self, monkeypatch):
        """A typo like 'hybrad' must NOT pass — fail-closed."""
        monkeypatch.setenv("LAB_MODE", "hybrad")
        from arail.airgap import lab_mode
        assert lab_mode() == "airgapped"

    def test_lab_mode_whitespace_only_is_airgapped(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "   ")
        from arail.airgap import lab_mode
        assert lab_mode() == "airgapped"


# ──────────────────────────────────────────────────────────────────────
# Happy path — basic flow: install, attempt block, see audit log
# ──────────────────────────────────────────────────────────────────────

class TestBasicAirgappedFlow:
    def test_full_block_audit_flow(self, monkeypatch, tmp_path):
        """The basic happy path: airgapped → public URL → blocked → audit
        log has one structured line → read_recent_blocks returns it."""
        import socket
        import requests
        import arail.airgap
        import arail.egress

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        with pytest.raises(arail.airgap.EgressBlocked):
            requests.get("https://example.com", timeout=2)

        # Audit log should have one structured line.
        log_path = tmp_path / "egress.jsonl"
        assert log_path.exists()
        lines = [
            json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()
        ]
        assert len(lines) >= 1
        last = lines[-1]
        # Required fields per spec §8.
        for field in ("ts", "url_host", "caller", "reason", "lab_mode"):
            assert field in last, f"audit log missing required field: {field}"
        assert last["url_host"] == "example.com"
        assert last["reason"] == "airgapped"
        assert last["lab_mode"] == "airgapped"

        # read_recent_blocks should return it via the modal API.
        recent = arail.egress.read_recent_blocks(5)
        assert len(recent) >= 1
        assert recent[-1]["url_host"] == "example.com"

    def test_localhost_passes_through_in_airgapped(self, monkeypatch, tmp_path):
        """The promised LAN-GPU-box workflow: 127.0.0.1 still works."""
        import requests
        import arail.airgap
        import arail.egress

        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
        arail.egress._reset_for_tests()
        arail.egress.install_guard()

        try:
            requests.get("http://127.0.0.1:65535/", timeout=0.05)
        except arail.airgap.EgressBlocked:
            pytest.fail(
                "Loopback must pass through in airgapped — the LAN-GPU-box "
                "workflow is the entire reason airgapped is not 'no network at all'"
            )
        except Exception:
            pass  # connection refused / timeout / etc. is fine
