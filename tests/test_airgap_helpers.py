"""Unit tests for src/arail/airgap.py — the egress policy helpers.

These tests cover:
- lab_mode() env-var fallback chain and fail-closed behaviour
- is_local_ip() for all address families
- is_local_host() for literals, hostnames, and the DNS-trust edge case
- should_allow_egress() in both airgapped and hybrid modes
"""

from __future__ import annotations

import socket

import pytest

import arail.airgap as airgap


# ── lab_mode ──────────────────────────────────────────────────────────

class TestLabMode:
    def test_default_is_airgapped(self, monkeypatch):
        monkeypatch.delenv("LAB_MODE", raising=False)
        monkeypatch.delenv("ARAIL_MODE", raising=False)
        assert airgap.lab_mode() == "airgapped"

    def test_lab_mode_hybrid(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "hybrid")
        assert airgap.lab_mode() == "hybrid"

    def test_lab_mode_airgapped_explicit(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        assert airgap.lab_mode() == "airgapped"

    def test_lab_mode_garbage_fails_closed(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "garbage")
        assert airgap.lab_mode() == "airgapped"

    def test_lab_mode_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "  hybrid  ")
        assert airgap.lab_mode() == "hybrid"

    def test_arail_mode_fallback(self, monkeypatch):
        monkeypatch.delenv("LAB_MODE", raising=False)
        monkeypatch.setenv("ARAIL_MODE", "hybrid")
        assert airgap.lab_mode() == "hybrid"

    def test_lab_mode_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setenv("ARAIL_MODE", "hybrid")
        assert airgap.lab_mode() == "airgapped"

    def test_is_airgapped_true_by_default(self, monkeypatch):
        monkeypatch.delenv("LAB_MODE", raising=False)
        monkeypatch.delenv("ARAIL_MODE", raising=False)
        assert airgap.is_airgapped() is True

    def test_is_airgapped_false_in_hybrid(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "hybrid")
        assert airgap.is_airgapped() is False


# ── is_local_ip ───────────────────────────────────────────────────────

class TestIsLocalIp:
    def test_loopback_ipv4(self):
        assert airgap.is_local_ip("127.0.0.1") is True

    def test_loopback_ipv4_other(self):
        assert airgap.is_local_ip("127.0.0.5") is True

    def test_rfc1918_10(self):
        assert airgap.is_local_ip("10.0.0.5") is True

    def test_rfc1918_172_16(self):
        assert airgap.is_local_ip("172.16.5.5") is True

    def test_rfc1918_172_31(self):
        assert airgap.is_local_ip("172.31.255.255") is True

    def test_rfc1918_172_32_not_local(self):
        # 172.32.x.x is NOT in the RFC1918 range (172.16.0.0/12 = 172.16–31)
        assert airgap.is_local_ip("172.32.0.1") is False

    def test_rfc1918_192_168(self):
        assert airgap.is_local_ip("192.168.1.50") is True

    def test_link_local_ipv4(self):
        assert airgap.is_local_ip("169.254.1.1") is True

    def test_public_ipv4(self):
        assert airgap.is_local_ip("8.8.8.8") is False

    def test_loopback_ipv6(self):
        assert airgap.is_local_ip("::1") is True

    def test_link_local_ipv6(self):
        assert airgap.is_local_ip("fe80::1") is True

    def test_public_ipv6(self):
        # 2606:4700:4700::1111 is Cloudflare's public DNS — genuinely public.
        # Note: 2001:db8::/32 is classified as private by Python's ipaddress
        # module (documentation prefix, RFC 3849), so we use a different address.
        assert airgap.is_local_ip("2606:4700:4700::1111") is False

    def test_zone_id_stripped(self):
        # fe80::1%en0 — zone identifier must be stripped before parsing
        assert airgap.is_local_ip("fe80::1%en0") is True

    def test_garbage_string(self):
        assert airgap.is_local_ip("not-an-ip") is False

    def test_empty_string(self):
        assert airgap.is_local_ip("") is False


# ── is_local_host ─────────────────────────────────────────────────────

class TestIsLocalHost:
    def test_loopback_literal(self):
        assert airgap.is_local_host("127.0.0.1") is True

    def test_localhost_name(self, monkeypatch):
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "127.0.0.1")
        assert airgap.is_local_host("localhost") is True

    def test_ipv6_loopback_literal(self):
        assert airgap.is_local_host("::1") is True

    def test_rfc1918_192_168(self):
        assert airgap.is_local_host("192.168.1.50") is True

    def test_rfc1918_10(self):
        assert airgap.is_local_host("10.0.0.5") is True

    def test_rfc1918_172_16(self):
        assert airgap.is_local_host("172.16.5.5") is True

    def test_link_local(self):
        assert airgap.is_local_host("169.254.1.1") is True

    def test_public_ip(self):
        assert airgap.is_local_host("8.8.8.8") is False

    def test_public_hostname_not_local(self, monkeypatch):
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        assert airgap.is_local_host("huggingface.co") is False

    def test_resolution_failure_is_not_local(self, monkeypatch):
        def _raise(h):
            raise socket.gaierror("no such host")
        monkeypatch.setattr(socket, "gethostbyname", _raise)
        assert airgap.is_local_host("nonexistent.invalid") is False

    def test_empty_host(self):
        assert airgap.is_local_host("") is False

    def test_dns_rebind_trust(self, monkeypatch):
        """DNS rebind: if the resolver says evil.example.com → 127.0.0.1,
        we trust it — documented limit of the v1 threat model.
        This test PINS the documented behavior (not a security guarantee)."""
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "127.0.0.1")
        assert airgap.is_local_host("evil.example.com") is True


# ── should_allow_egress ───────────────────────────────────────────────

class TestShouldAllowEgress:
    def test_local_url_always_allowed(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        ok, reason = airgap.should_allow_egress("http://127.0.0.1:11434/api/tags")
        assert ok is True
        assert reason == "local"

    def test_local_url_allowed_in_hybrid(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "hybrid")
        ok, reason = airgap.should_allow_egress("http://127.0.0.1:11434/api/tags")
        assert ok is True
        assert reason == "local"

    def test_public_url_blocked_in_airgapped(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        ok, reason = airgap.should_allow_egress("https://huggingface.co/api/papers")
        assert ok is False
        assert reason == "airgapped"

    def test_public_url_allowed_in_hybrid(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "hybrid")
        monkeypatch.setattr(socket, "gethostbyname", lambda h: "151.101.64.81")
        ok, reason = airgap.should_allow_egress("https://huggingface.co/api/papers")
        assert ok is True
        assert reason == "hybrid"

    def test_invalid_url_denied(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        ok, reason = airgap.should_allow_egress("not a url")
        assert ok is False
        assert reason == "invalid"

    def test_empty_url_denied(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        ok, reason = airgap.should_allow_egress("")
        assert ok is False
        assert reason == "invalid"

    def test_rfc1918_url_allowed_in_airgapped(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        ok, reason = airgap.should_allow_egress("http://192.168.1.50:11434/api/tags")
        assert ok is True
        assert reason == "local"

    def test_10x_url_allowed_in_airgapped(self, monkeypatch):
        monkeypatch.setenv("LAB_MODE", "airgapped")
        ok, reason = airgap.should_allow_egress("http://10.0.0.5/x")
        assert ok is True
        assert reason == "local"


# ── AIRGAPPED_NOTICE (moved from portal/app.py providers_status; T-REG-2) ──

class TestAirgappedNotice:
    def test_notice_is_the_pre_move_chat_string(self):
        # Byte-identical to the string that used to be inlined in
        # portal/app.py::providers_status before it moved here
        # (sprints/2026-09-23-nucleus-sprint-1, ARCHITECTURE.md §3 N8/§4.11).
        assert airgap.AIRGAPPED_NOTICE == (
            "Lab is in airgapped mode. Only My Machine is usable. "
            "To enable cloud providers, set LAB_MODE=hybrid in .env and restart."
        )

    def test_providers_status_endpoint_uses_the_shared_constant(self):
        from arail.portal import app as portal_app

        assert portal_app.AIRGAPPED_NOTICE is airgap.AIRGAPPED_NOTICE
