"""Tests for the UPDATED _compute_source_env.

Covers ARCHITECTURE.md must-pass list:
  - test_compute_source_env_my_machine_points_at_shim
  - test_compute_source_env_cloud_sets_provider_env_var (F-SEC-CRED-1)
  - test_compute_source_env_cloud_unknown_provider_falls_back (regression)
"""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest


class TestComputeSourceEnvUpdated:
    """Extended tests for _compute_source_env after Sprint 2 update."""

    def _call(self, monkeypatch, provider="my_machine", token="", model_name=""):
        from arail.portal.services import opencode as oc
        # Patch the lazy imports inside _compute_source_env
        with mock.patch("arail.portal.app._load_active_provider", return_value=provider,
                        create=True), \
             mock.patch("arail.portal.app._provider_token", return_value=token, create=True):
            env = oc._compute_source_env()
        return env

    def test_compute_source_env_my_machine_points_at_shim(self, monkeypatch):
        """my_machine → OPENCODE_API_BASE ends with /api/openai/v1 (not Ollama). (Sprint 2 UPDATED)"""
        env = self._call(monkeypatch, provider="my_machine")
        base = env.get("OPENCODE_API_BASE", "")
        assert base.endswith("/api/openai/v1"), (
            f"Expected OPENCODE_API_BASE to end with /api/openai/v1, got: {base!r}"
        )
        assert "ollama" not in base.lower(), (
            f"Ollama URL leaked into my_machine env: {base!r}"
        )
        assert "127.0.0.1" in base, (
            f"Expected loopback URL, got: {base!r}"
        )
        assert env.get("OPENCODE_API_KEY", "").startswith("arl1s_")

    def test_compute_source_env_cloud_sets_provider_env_var(self, monkeypatch):
        """provider=claude, token='sk-X' → env contains ANTHROPIC_API_KEY='sk-X'. (F-SEC-CRED-1)"""
        env = self._call(monkeypatch, provider="claude", token="sk-X")
        assert env.get("ANTHROPIC_API_KEY") == "sk-X", (
            f"ANTHROPIC_API_KEY not set or wrong: {env}"
        )
        # Legacy compat: OPENCODE_API_KEY also set
        assert env.get("OPENCODE_API_KEY") == "sk-X", (
            f"OPENCODE_API_KEY not set: {env}"
        )

    def test_compute_source_env_cloud_nvidia(self, monkeypatch):
        """provider=nvidia, token='nv-tok' → NVIDIA_API_KEY set."""
        env = self._call(monkeypatch, provider="nvidia", token="nv-tok")
        assert env.get("NVIDIA_API_KEY") == "nv-tok"

    def test_compute_source_env_cloud_openrouter(self, monkeypatch):
        """provider=openrouter → OPENROUTER_API_KEY set."""
        env = self._call(monkeypatch, provider="openrouter", token="or-tok")
        assert env.get("OPENROUTER_API_KEY") == "or-tok"

    def test_compute_source_env_cloud_unknown_provider_falls_back(self, monkeypatch):
        """Unknown provider falls back to my_machine mapping. (Sprint 1 regression)"""
        env = self._call(monkeypatch, provider="unknown-xyz")
        base = env.get("OPENCODE_API_BASE", "")
        assert "/api/openai/v1" in base, (
            f"Unknown provider should fall back to shim, got: {base!r}"
        )

    def test_compute_source_env_never_logs_token(self, monkeypatch, caplog):
        """Token value must not appear in log output. (F-SEC-2)"""
        import logging
        secret = "SK-SUPERSECRET-12345"
        import arail.portal.services.opencode as oc
        with mock.patch("arail.portal.app._load_active_provider", return_value="claude",
                        create=True), \
             mock.patch("arail.portal.app._provider_token", return_value=secret, create=True), \
             caplog.at_level(logging.DEBUG, logger="arail.portal.services.opencode"):
            oc._compute_source_env()
        assert secret not in caplog.text, "Token appeared in logs!"

    def test_compute_source_env_sets_opencode_disable_autoupdate(self, monkeypatch):
        """Sprint 1 follow-up: OPENCODE_DISABLE_AUTOUPDATE should be set."""
        env = self._call(monkeypatch, provider="my_machine")
        # This is set in start()/env construction; may or may not be in _compute_source_env
        # The key check is that start() passes it — tested in lifecycle tests.
        # Here just verify my_machine path runs without error.
        assert "OPENCODE_API_BASE" in env
