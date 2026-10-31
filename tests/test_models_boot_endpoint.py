"""GET /api/models/boot -- the boot strip's data source (v2).

Boot auto-resolves both model slots (sprints/2026-10-20-boot-model-
autoresolve); this endpoint only reports the result. ``mode`` is
``hidden`` (nothing to say) or ``degraded`` (something actionable). There is
no picker, no "settled" state and no drift problem.

Rewritten from the PR #186 suite. Removed assertions and their replacements:
  * mode == "picker" when never settled           -> unsettled boots resolve
    on their own: test_clean_machine_resolves_without_a_yaml
  * picker always assembles candidates / row shape -> candidates only on
    ?full=1 (test_candidates_only_on_full_flag, test_candidate_row_shape)
  * slots.b.allow_none                             -> pins.b "none" is honoured
    (test_pin_none_for_b_*, in test_models_pin_endpoint.py)
  * mode == "problem" for vanished A / removed B   -> resolver falls through
    and reports degraded/missing (test_pinned_model_missing_*)
  * "env no longer matches the settled value"      -> must never appear
    (test_operator_v1_yaml_shape_reports_no_drift)
  * stamp changes when settlement changes          -> no stamp; removed
"""
from __future__ import annotations

import pytest

from arail import model_resolve as mr


def _tag(name, gb=0.9, ps="1.2B"):
    return {"id": name, "size_gb": gb, "family": "llama", "parameter_size": ps}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from arail.registry import core as reg_core
    from arail.portal import models_api

    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "model_registry.json"))
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(tmp_path / "model_defaults.yaml"))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "models").mkdir()
    reg_core.reset_registry()
    models_api._BOOT_CANDIDATES_CACHE["payload"] = None
    models_api._BOOT_CANDIDATES_CACHE["ts"] = 0.0
    _machine(monkeypatch, [_tag("llama-ai-eng:latest")])

    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    # Boot resolution runs as a startup task; tests drive it explicitly so the
    # order is deterministic.
    with TestClient(app_mod.app) as c:
        _wait_for_boot_resolution()
        yield c
    reg_core.reset_registry()
    models_api._BOOT_CANDIDATES_CACHE["payload"] = None


def _wait_for_boot_resolution(timeout=3.0):
    """The startup task resolves asynchronously; wait so tests that follow
    never race it."""
    import time
    end = time.time() + timeout
    while not mr.last_resolution() and time.time() < end:
        time.sleep(0.01)


def _machine(monkeypatch, tags, *, deep=None):
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: tags)
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: deep)
    installed = [{"id": t["id"], "runtime": "ollama", "size_gb": t["size_gb"],
                  "modified": "", "endpoint": "x"} for t in (tags or [])]
    monkeypatch.setattr("arail.chat._ollama_installed_models",
                        lambda *a, **kw: installed)


def _resolve(**kw):
    return mr.resolve_and_apply(**kw)


# ---------------------------------------------------------------------------
# Shape and modes
# ---------------------------------------------------------------------------

def test_clean_machine_resolves_without_a_yaml(client, tmp_path):
    # The fixture started the portal with no yaml; boot wrote one by itself.
    from arail import model_defaults as md
    assert md.read(tmp_path / "model_defaults.yaml")["pins"] == {"a": None, "b": None}
    body = client.get("/api/models/boot").json()
    assert body["mode"] == "hidden"
    assert body["resolution"]["a"]["model"] == "llama-ai-eng:latest"
    assert body["resolution"]["b"]["model"] is None
    assert (tmp_path / "model_defaults.yaml").exists()      # one exists after boot


def test_shape_has_no_picker_or_settlement_keys(client):
    _resolve()
    body = client.get("/api/models/boot").json()
    assert {"mode", "resolution", "warm", "degraded", "airgapped", "hardware"} <= set(body)
    for gone in ("settlement", "slots", "stamp", "picker", "problem"):
        assert gone not in body
    assert body["mode"] in ("hidden", "degraded")


def test_ollama_down_is_degraded_but_ready(client, monkeypatch):
    _machine(monkeypatch, None)
    _resolve()
    body = client.get("/api/models/boot").json()
    assert body["mode"] == "degraded"
    assert any(d["kind"] == "ollama_down" for d in body["degraded"])
    assert client.get("/api/ready").json()["ready"] is True


def test_operator_v1_yaml_shape_reports_no_drift(client, monkeypatch, tmp_path):
    """F2 regression: default_b: null + a registry/env that used to name
    Qwen must not produce 'env no longer matches the settled value (None)'."""
    (tmp_path / "model_defaults.yaml").write_text(
        "# Qwen2.5-7B SIGABRTs in aerollm_backend_mlx_native\n"
        "default_a: llama-ai-eng:latest\ndefault_b: null\n")
    monkeypatch.setenv("AEROLLM_MODEL", "Qwen2.5-7B-Instruct-4bit")
    _resolve()
    r = client.get("/api/models/boot")
    assert "drift" not in r.text and "no longer matches" not in r.text
    body = r.json()
    assert body["resolution"]["b"] == {"model": None, "source": "pin",
                                       "reason": "pinned off", "size_gb": None}
    assert body["mode"] == "hidden"


def test_pinned_model_missing_falls_through_and_is_degraded(client, monkeypatch, tmp_path):
    (tmp_path / "model_defaults.yaml").write_text(
        "version: 2\npins:\n  a: ghost:3b\n  b: null\nresolved: {}\n")
    _resolve()
    body = client.get("/api/models/boot").json()
    assert body["resolution"]["a"]["model"] == "llama-ai-eng:latest"
    miss = [d for d in body["degraded"] if d["kind"] == "missing"]
    assert miss and "ollama pull ghost:3b" in miss[0]["detail"]


def test_pinned_deep_model_removed_from_disk_is_degraded(client, monkeypatch, tmp_path):
    (tmp_path / "model_defaults.yaml").write_text(
        "version: 2\npins:\n  a: null\n  b: Gone-7B-4bit\nresolved: {}\n")
    _resolve()
    body = client.get("/api/models/boot").json()
    assert body["resolution"]["b"]["model"] is None
    assert any(d["slot"] == "b" and d["kind"] == "missing" for d in body["degraded"])


def test_boot_endpoint_before_resolution_serves_the_disk_cache(client, tmp_path):
    from arail import model_defaults as md
    md.write_cache({"a": {"model": "cached:1b", "source": "auto", "reason": None,
                          "size_gb": 1.0},
                    "b": {"model": None, "source": "none", "reason": None,
                          "size_gb": None}}, path=tmp_path / "model_defaults.yaml")
    mr.last_resolution().clear()
    body = client.get("/api/models/boot").json()
    assert body["resolution"]["a"]["model"] == "cached:1b"


# ---------------------------------------------------------------------------
# Candidates (override UI) -- only on ?full=1
# ---------------------------------------------------------------------------

def test_candidates_only_on_full_flag(client):
    _resolve()
    assert "candidates" not in client.get("/api/models/boot").json()
    body = client.get("/api/models/boot?full=1").json()
    assert len(body["candidates"]["a"]) > 0


def test_candidate_row_shape(client):
    row = client.get("/api/models/boot?full=1").json()["candidates"]["a"][0]
    for key in ("id", "name", "source", "size_gb", "present", "fit", "install_command", "hf_url"):
        assert key in row, f"missing {key!r} in candidate row: {row}"
    assert set(row["fit"].keys()) == {"verdict", "detail"}


def test_airgapped_suppresses_hf_urls_on_slot_b(client, monkeypatch):  # FM14
    monkeypatch.setenv("LAB_MODE", "airgapped")
    body = client.get("/api/models/boot?full=1").json()
    assert body["airgapped"] is True
    for row in body["candidates"]["b"]:
        assert row["hf_url"] is None


def test_non_airgapped_shows_hf_urls_when_available(client, monkeypatch):
    monkeypatch.setenv("LAB_MODE", "hybrid")
    body = client.get("/api/models/boot?full=1").json()
    assert body["airgapped"] is False
    assert any(row["hf_url"] for row in body["candidates"]["b"])


def test_no_cloud_source_rows_ever_appear_in_either_slot(client, monkeypatch):
    monkeypatch.setenv("LAB_MODE", "hybrid")
    body = client.get("/api/models/boot?full=1").json()
    for slot in ("a", "b"):
        assert "cloud" not in {row["source"] for row in body["candidates"][slot]}


def test_oversized_primary_candidate_marked_too_big(client):
    rows = {row["id"]: row for row in
            client.get("/api/models/boot?full=1").json()["candidates"]["a"]}
    assert rows["phi4:14b"]["fit"]["verdict"] == "too_big"
