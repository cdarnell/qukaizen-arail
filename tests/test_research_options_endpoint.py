"""GET /api/research/options — mounted, unmounted, and corrupt-bundle paths.

Same isolated-lab fixture convention as test_world_forge_api.py: the
world_mount default-dir seams are monkeypatched to tmp dirs, and the mount
record is written directly (the endpoint only reads). The endpoint must
degrade to the generic seeds — never 500 — when the bundle is unreadable.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import arail.world_mount as wm
from arail.portal.app import app


def _client():
    return TestClient(app)


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    worlds = tmp_path / "worlds"
    data = tmp_path / "data"
    pkb = tmp_path / "pkb"
    worlds.mkdir(), data.mkdir()
    monkeypatch.setattr(wm, "_default_worlds_dir", lambda: worlds)
    monkeypatch.setattr(wm, "_default_data_dir", lambda: data)
    monkeypatch.setattr(wm, "_default_pkb_root", lambda: pkb)
    yield tmp_path, worlds, data, pkb


def _write_bundle(worlds, slug="botany"):
    bundle = worlds / slug
    bundle.mkdir(parents=True, exist_ok=True)
    spec = {"slug": slug, "display_name": "Botany",
            "categories": [{"id": "plants", "label": "Plants"},
                           {"id": "care", "label": "Care"}]}
    terms = [{"slug": "snake-plant", "term": "Snake Plant",
              "category": "plants", "short": "s", "definition": "d",
              "source": "https://example.org"},
             {"slug": "watering", "term": "Watering", "category": "care",
              "short": "s", "definition": "d",
              "source": "https://example.org"}]
    (bundle / "manifest.json").write_text(json.dumps({
        "schema": "dac.world-bundle/v1", "world": slug,
        "display_name": "Botany", "provenance_tier": "sourced",
        "provenance_counts": {"model": 0, "sourced": 2, "total": 2}}))
    (bundle / "spec.json").write_text(json.dumps(spec))
    (bundle / "terms.json").write_text(json.dumps({"version": 1,
                                                   "terms": terms}))
    (bundle / "face.json").write_text(json.dumps({
        "world": slug, "name": "Botany", "tagline": "Plants, honestly."}))
    (bundle / "agenda.json").write_text(json.dumps({
        "schema": "dac.world-agenda/v1", "world": slug,
        "watches": [{"node": "plants", "feeds": ["https://x/1"]}]}))
    return bundle


def _mount(data, worlds, slug="botany"):
    record = {"world": slug, "bundle_version": 1, "world_sha256": "x",
              "mounted_at": "2026-08-13T00:00:00Z",
              "bundle_dir": str(worlds / slug),
              "staged_dir": str(data / "staged"), "pin": {}}
    (data / "world-mount.json").write_text(json.dumps(record))


def test_options_mounted_world(lab):
    _tmp, worlds, data, _pkb = lab
    _write_bundle(worlds)
    _mount(data, worlds)
    r = _client().get("/api/research/options")
    assert r.status_code == 200
    body = r.json()
    assert body["world"] == "botany"
    assert body["display_name"] == "Botany"
    assert body["tagline"] == "Plants, honestly."
    assert body["provenance_tier"] == "sourced"
    assert body["term_count"] == 2
    kinds = [o["kind"] for o in body["options"]]
    assert kinds[0] == "default"
    assert kinds.count("deepen") == 2
    assert "watch" in kinds
    assert isinstance(body["airgapped"], bool)


def test_options_unmounted_returns_generic(lab):
    r = _client().get("/api/research/options")
    assert r.status_code == 200
    body = r.json()
    assert body["world"] is None
    assert body["display_name"] is None
    assert [o["kind"] for o in body["options"]] == ["generic"] * 3


def test_options_corrupt_bundle_degrades_to_generic(lab):
    _tmp, worlds, data, _pkb = lab
    bundle = _write_bundle(worlds)
    _mount(data, worlds)
    (bundle / "spec.json").write_text("{not json")
    r = _client().get("/api/research/options")
    assert r.status_code == 200
    body = r.json()
    assert body["world"] is None
    assert [o["kind"] for o in body["options"]] == ["generic"] * 3


def test_options_missing_bundle_dir_degrades_to_generic(lab):
    _tmp, worlds, data, _pkb = lab
    _mount(data, worlds, slug="ghost")  # record points at nothing
    r = _client().get("/api/research/options")
    assert r.status_code == 200
    assert r.json()["world"] is None
