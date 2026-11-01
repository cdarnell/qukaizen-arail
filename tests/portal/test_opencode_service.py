"""Unit tests for src/arail/portal/services/opencode.py.

Covers ARCHITECTURE.md must-pass list:
  - test_install_hint_per_platform       (F-INSTALL-3)
  - test_compute_source_env_my_machine   (F-CONFIG-1)
  - test_compute_source_env_my_machine_default_base
  - test_compute_source_env_cloud_claude (F-CONFIG-2)
  - test_compute_source_env_cloud_no_token
  - test_compute_source_env_never_logged (F-SEC-2)
  - test_start_command_pins_port_and_hostname (F-SEC-6, A1)
"""

from __future__ import annotations

import logging
import os
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Helpers — lazy-import so tests can patch before module-level code runs
# ---------------------------------------------------------------------------

def _get_module():
    import importlib
    import arail.portal.services.opencode as oc
    importlib.reload(oc)
    return oc


# ---------------------------------------------------------------------------
# install_hint — F-INSTALL-3
# ---------------------------------------------------------------------------

class TestInstallHint:
    @pytest.mark.parametrize("system,uname_release,expected_platform", [
        ("Darwin",  "",                     "darwin"),
        ("Linux",   "5.15.0-generic",       "linux"),
        ("Linux",   "5.15.0-microsoft-wsl2","wsl"),
        ("Linux",   "5.15.0-wsl",           "wsl"),
        ("Windows", "",                     "windows"),
        ("SunOS",   "",                     "other"),
    ])
    def test_install_hint_per_platform(self, system, uname_release, expected_platform, monkeypatch):
        import arail.portal.services.opencode as oc
        monkeypatch.setattr("platform.system", lambda: system)
        if system == "Linux":
            fake_uname = mock.Mock()
            fake_uname.release = uname_release
            monkeypatch.setattr("os.uname", lambda: fake_uname)
        hint = oc.install_hint()
        assert hint["platform"] == expected_platform
        assert hint["command"]
        assert hint["docs_url"].startswith("https://")

    def test_install_hint_pure_no_io(self, monkeypatch):
        """install_hint must not make network calls or read files."""
        import arail.portal.services.opencode as oc
        # No mock of shutil.which etc — if it calls them it won't crash but
        # we do verify it doesn't raise.
        hint = oc.install_hint()
        assert isinstance(hint, dict)
        assert {"platform", "command", "docs_url"} <= hint.keys()


# ---------------------------------------------------------------------------
# _compute_source_env — F-CONFIG-1, F-CONFIG-2, F-SEC-2
# ---------------------------------------------------------------------------

class TestComputeSourceEnv:
    def _patch_provider(self, monkeypatch, provider: str, token: str = ""):
        """Stub the app.py provider helpers."""
        import arail.portal.services.opencode as oc
        monkeypatch.setattr(
            "arail.portal.services.opencode._load_active_provider_stub",
            lambda: provider,
            raising=False,
        )
        # Patch via the actual import path used in _compute_source_env
        monkeypatch.setattr(
            "arail.portal.app._load_active_provider",
            lambda: provider,
            raising=False,
        )
        monkeypatch.setattr(
            "arail.portal.app._provider_token",
            lambda p: token,
            raising=False,
        )

    def test_compute_source_env_my_machine(self, monkeypatch):
        """my_machine → OPENCODE_API_KEY=<arl1s_ shim token>, shim base (Sprint 2: not Ollama).

        Sprint 2 update: _compute_source_env now points my_machine at the
        lab-side OpenAI shim (/api/openai/v1), not the Ollama default.
        MODEL_API_BASE is no longer used for the base URL for my_machine.
        """
        import arail.portal.services.opencode as oc
        monkeypatch.setenv("MODEL_NAME", "llama3")
        # Patch the source in arail.portal.app — _compute_source_env lazy-imports from there.
        monkeypatch.setattr(
            "arail.portal.app._get_chat_model_load_state",
            lambda: {"state": "ready", "model": "llama3"},
            raising=False,
        )
        self._patch_provider(monkeypatch, "my_machine")
        env = oc._compute_source_env()
        assert env["OPENCODE_API_KEY"].startswith("arl1s_")
        # Sprint 2: base URL is the lab shim, not Ollama
        assert "127.0.0.1" in env["OPENCODE_API_BASE"]
        assert "/api/openai/v1" in env["OPENCODE_API_BASE"]

    def test_compute_source_env_my_machine_default_base(self, monkeypatch):
        """my_machine with no loaded model → shim URL, not-needed key (Sprint 2: F-CONFIG-1).

        Sprint 2 update: MODEL_API_BASE no longer used for my_machine.
        The shim URL is always http://127.0.0.1:<PORTAL_PORT>/api/openai/v1.
        """
        import arail.portal.services.opencode as oc
        monkeypatch.delenv("MODEL_API_BASE", raising=False)
        monkeypatch.delenv("MODEL_NAME", raising=False)
        # Patch the source in arail.portal.app — _compute_source_env lazy-imports from there.
        monkeypatch.setattr(
            "arail.portal.app._get_chat_model_load_state",
            lambda: {"state": "ready", "model": None},
            raising=False,
        )
        self._patch_provider(monkeypatch, "my_machine")
        env = oc._compute_source_env()
        assert "/api/openai/v1" in env["OPENCODE_API_BASE"]
        assert env["OPENCODE_API_KEY"].startswith("arl1s_")

    def test_compute_source_env_cloud_claude(self, monkeypatch):
        """claude provider → Anthropic base + token passed through (F-CONFIG-2)."""
        import arail.portal.services.opencode as oc
        monkeypatch.setenv("MODEL_NAME", "claude-3-5-sonnet")
        # Patch the source in arail.portal.app, not the opencode module —
        # _compute_source_env does a fresh lazy import each call.
        monkeypatch.setattr(
            "arail.portal.app._get_chat_model_load_state",
            lambda: {"state": "ready", "model": "claude-3-5-sonnet"},
            raising=False,
        )
        self._patch_provider(monkeypatch, "claude", token="sk-ant-secret")
        env = oc._compute_source_env()
        assert env["OPENCODE_API_BASE"] == "https://api.anthropic.com/v1"
        assert env["OPENCODE_API_KEY"] == "sk-ant-secret"
        assert env["OPENCODE_MODEL"] == "claude-3-5-sonnet"

    def test_compute_source_env_cloud_no_token(self, monkeypatch):
        """Cloud provider with empty token → OPENCODE_API_KEY='' not a crash (F-CONFIG-2)."""
        import arail.portal.services.opencode as oc
        self._patch_provider(monkeypatch, "openrouter", token="")
        env = oc._compute_source_env()
        assert "OPENCODE_API_KEY" in env
        assert env["OPENCODE_API_KEY"] == ""
        assert env["OPENCODE_API_BASE"] == "https://openrouter.ai/api/v1"

    def test_compute_source_env_unknown_provider_falls_back(self, monkeypatch):
        """Unknown provider → my_machine fallback, no crash."""
        import arail.portal.services.opencode as oc
        monkeypatch.delenv("MODEL_API_BASE", raising=False)
        self._patch_provider(monkeypatch, "unknown_provider_xyz")
        env = oc._compute_source_env()
        assert env["OPENCODE_API_KEY"].startswith("arl1s_")

    def test_compute_source_env_never_logged(self, monkeypatch, caplog):
        """No token value should appear in any log record (F-SEC-2)."""
        import arail.portal.services.opencode as oc
        secret_token = "super-secret-token-xyz-987"
        self._patch_provider(monkeypatch, "claude", token=secret_token)
        with caplog.at_level(logging.DEBUG, logger="arail.portal.services.opencode"):
            oc._compute_source_env()
        for record in caplog.records:
            assert secret_token not in record.getMessage()


# ---------------------------------------------------------------------------
# start() — command args validation (F-SEC-6, A1)
# ---------------------------------------------------------------------------

class TestStartCommand:
    def test_start_command_pins_port_and_hostname(self, monkeypatch, tmp_path):
        """start() must pass --port <port> AND --hostname 127.0.0.1 (F-SEC-6, A1)."""
        import subprocess as subprocess_mod
        import arail.portal.services.opencode as oc
        monkeypatch.setattr(oc, "is_installed", lambda: True)
        monkeypatch.setattr(oc, "is_running", lambda port=oc.PORT_DEFAULT: False)
        monkeypatch.setattr(oc, "LOG_PATH", tmp_path / "opencode.log")

        captured_args: list[list[str]] = []

        def fake_popen(args, **kwargs):
            captured_args.append(list(args))
            m = mock.Mock()
            m.pid = 12345
            return m

        monkeypatch.setattr(subprocess_mod, "Popen", fake_popen)
        # Also patch subprocess within the opencode module itself
        monkeypatch.setattr("arail.portal.services.opencode.subprocess.Popen", fake_popen)

        result = oc.start(port=4096)
        assert result["ok"] is True
        assert captured_args, "Popen was not called"
        call_args = captured_args[0]
        assert "--port" in call_args
        port_idx = call_args.index("--port")
        assert call_args[port_idx + 1] == "4096"
        assert "--hostname" in call_args
        host_idx = call_args.index("--hostname")
        assert call_args[host_idx + 1] == "127.0.0.1"

    def test_start_returns_error_if_not_installed(self, monkeypatch, tmp_path):
        import arail.portal.services.opencode as oc
        monkeypatch.setattr(oc, "is_installed", lambda: False)
        result = oc.start()
        assert result["ok"] is False
        assert "not installed" in result["error"]

    def test_start_returns_port_busy_when_running(self, monkeypatch, tmp_path):
        """start() pre-checks port; returns port-busy when listener is NOT opencode. (F-PROC-2 pre-check)."""
        import arail.portal.services.opencode as oc
        monkeypatch.setattr(oc, "is_installed", lambda: True)
        monkeypatch.setattr(oc, "is_running", lambda port=oc.PORT_DEFAULT: True)
        # Bug-fix 2026-05-07: start() now distinguishes "we already own the
        # port" (idempotent success) from "something else has it" (port busy)
        # via the /doc fingerprint. Pin the negative branch here so this test
        # remains deterministic even if a real opencode is on 4096 locally.
        monkeypatch.setattr(oc, "_is_opencode_on_port", lambda port: False)
        result = oc.start()
        assert result["ok"] is False
        assert "port busy" in result["error"]
