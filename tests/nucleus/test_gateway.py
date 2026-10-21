"""arail.nucleus.providers.gateway — GatewayClient + profile_gate
(T-GW-1..5)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from arail.nucleus.errors import BudgetExhausted, ContractViolation, GatewayError, ProfileRefused
from arail.nucleus.providers.gateway import GatewayClient, profile_gate


# ── T-GW-1: gateway profile under airgapped -> ProfileRefused, verbatim notice ──

def test_profile_gate_airgapped_refuses_with_notice(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    from arail.airgap import AIRGAPPED_NOTICE

    with pytest.raises(ProfileRefused) as exc_info:
        profile_gate("gateway")
    assert str(exc_info.value) == AIRGAPPED_NOTICE


def test_profile_gate_hybrid_refuses_sprint2_message(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "hybrid")
    with pytest.raises(ProfileRefused, match="nucleus-sprint-2"):
        profile_gate("gateway")


def test_profile_gate_local_never_refuses(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    profile_gate("local")  # no raise


def test_gateway_client_construction_refuses_before_any_socket(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "airgapped")
    calls = {"connect": False}
    import socket

    def _boom_connect(self, *a, **kw):
        calls["connect"] = True
        raise AssertionError("should never get here")

    monkeypatch.setattr(socket.socket, "connect", _boom_connect)
    with pytest.raises(ProfileRefused):
        GatewayClient("https://gateway.example.invalid", "tok", build_id="b1")
    assert calls["connect"] is False


def test_egress_jsonl_unchanged_on_airgapped_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    egress_path = tmp_path / "egress.jsonl"
    with pytest.raises(ProfileRefused):
        GatewayClient("https://gateway.example.invalid", "tok", build_id="b1")
    assert not egress_path.exists()


# ── mock gateway server for T-GW-2..5 ──────────────────────────────

class _MockGatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")
        self.server.received_auth_headers.append(auth)  # type: ignore[attr-defined]

        if self.path == "/v1/nucleus/teach-402":
            self.send_response(402)
            self.end_headers()
            return
        if self.path == "/v1/nucleus/teach-missing-provenance":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"text": "no provenance here"}).encode())
            return
        if self.path == "/v1/nucleus/teach-redirect":
            self.send_response(302)
            self.send_header("Location", "https://evil.example.invalid/steal")
            self.end_headers()
            return
        if self.path == "/v1/nucleus/teach":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "build_id": body.get("build_id"), "pipeline_hash": "sha256:abc",
                "model": "claude-mock", "request_id": "req_1", "text": "a teacher answer",
            }).encode())
            return
        if self.path == "/v1/nucleus/judge":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "build_id": body.get("build_id"), "pipeline_hash": "sha256:abc",
                "model": "claude-mock", "request_id": "req_2", "verdict": "A",
            }).encode())
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def mock_gateway():
    server = HTTPServer(("127.0.0.1", 0), _MockGatewayHandler)
    server.received_auth_headers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    yield server, base_url
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _hybrid_mode(monkeypatch):
    monkeypatch.setenv("LAB_MODE", "hybrid")


# ── T-GW-2: happy path with provenance ────────────────────────────────

def test_teach_happy_path_with_provenance(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "sekret-token", build_id="build-1")
    result = client.teach(domain="linux-kernel", prompt="explain this commit")
    assert result["build_id"] == "build-1"
    assert result["pipeline_hash"] == "sha256:abc"
    assert result["model"] == "claude-mock"
    assert "request_id" in result


def test_judge_happy_path_with_provenance(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "sekret-token", build_id="build-1")
    result = client.judge(domain="linux-kernel", text_a="a", text_b="b")
    assert result["verdict"] == "A"
    assert result["build_id"] == "build-1"


# ── T-GW-2: 402 -> BudgetExhausted ─────────────────────────────────────

def test_402_raises_budget_exhausted(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "tok", build_id="build-1")
    with pytest.raises(BudgetExhausted):
        client._post("/v1/nucleus/teach-402", {"build_id": "build-1"})


# ── missing provenance -> ContractViolation ───────────────────────────

def test_missing_provenance_raises_contract_violation(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "tok", build_id="build-1")
    with pytest.raises(ContractViolation):
        client._post("/v1/nucleus/teach-missing-provenance", {"build_id": "build-1"})


# ── a 302 to another host -> GatewayError, never followed ────────────

def test_redirect_raises_gateway_error_not_followed(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "tok", build_id="build-1")
    with pytest.raises(GatewayError):
        client._post("/v1/nucleus/teach-redirect", {"build_id": "build-1"})


# ── token-echo trap: token absent from caplog / exception / repr ─────

def test_token_never_appears_in_repr(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "super-secret-token-xyz", build_id="build-1")
    assert "super-secret-token-xyz" not in repr(client)
    assert "redacted" in repr(client)


def test_token_never_appears_in_exception_message(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "super-secret-token-xyz", build_id="build-1")
    try:
        client._post("/v1/nucleus/teach-402", {"build_id": "build-1"})
    except BudgetExhausted as exc:
        assert "super-secret-token-xyz" not in str(exc)


def test_token_sent_only_as_bearer_header(mock_gateway):
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "super-secret-token-xyz", build_id="build-1")
    client.teach(domain="linux-kernel", prompt="x")
    assert server.received_auth_headers[-1] == "Bearer super-secret-token-xyz"


def test_token_never_appears_in_egress_jsonl(tmp_path, monkeypatch, mock_gateway):
    monkeypatch.setattr("arail.config.DATA_DIR", str(tmp_path))
    server, base_url = mock_gateway
    client = GatewayClient(base_url, "super-secret-token-xyz", build_id="build-1")
    client.teach(domain="linux-kernel", prompt="x")
    egress_path = tmp_path / "egress.jsonl"
    if egress_path.is_file():
        assert "super-secret-token-xyz" not in egress_path.read_text()


# ── https required, except loopback ────────────────────────────────

def test_https_required_for_non_loopback_host():
    with pytest.raises(GatewayError, match="https"):
        GatewayClient("http://gateway.example.invalid", "tok", build_id="b1")


def test_http_allowed_for_loopback(mock_gateway):
    server, base_url = mock_gateway
    GatewayClient(base_url, "tok", build_id="b1")  # no raise -- loopback


# ── exact host match against NUCLEUS_GATEWAY_URL ──────────────────────

def test_exact_host_match_enforced(monkeypatch, mock_gateway):
    server, base_url = mock_gateway
    monkeypatch.setenv("NUCLEUS_GATEWAY_URL", "https://the-real-gateway.example.invalid")
    with pytest.raises(GatewayError, match="does not exact-match"):
        GatewayClient(base_url, "tok", build_id="b1")
