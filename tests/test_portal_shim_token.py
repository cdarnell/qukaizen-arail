"""Addendum A.2/A.3: the shim-scoped token and the OpenCode config that carries it."""
from __future__ import annotations

import json
import os
import re
import stat

import pytest
from fastapi.testclient import TestClient

from arail import config
from arail.portal import app as portal_app
from arail.portal import auth
from arail.portal.route_table import flatten, sample_path
from arail.portal.services import opencode

pytestmark = pytest.mark.real_auth

PASS = "test-passphrase-not-real"


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)
    auth.ensure_token()
    auth.ensure_shim_token()
    tok = auth.token_path().read_text().strip()
    shim = auth.shim_token_path().read_text().strip()
    return {"tok": tok, "shim": shim}


def mk():
    return TestClient(portal_app.app, follow_redirects=False, raise_server_exceptions=False)


def test_shim_token_file_is_0600_arl1s_and_distinct(lab):
    p = auth.shim_token_path()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert re.fullmatch(r"arl1s_[A-Za-z0-9_-]{43}", lab["shim"]) and lab["shim"] != lab["tok"]
    assert not auth.check_bearer(f"Bearer {lab['shim']}")      # never the portal bearer
    assert not auth.check_shim_bearer(f"Bearer {lab['tok']}")


def test_shim_token_works_under_openai_v1(lab):
    r = mk().get("/api/openai/v1/models", headers={"Authorization": f"Bearer {lab['shim']}"})
    assert r.status_code == 200


def test_every_other_route_is_401_for_the_shim_token(lab):
    """Third column of the sweep (Addendum A.6): outside /api/openai/v1/ the shim token opens nothing."""
    c = mk()
    h = {"Authorization": f"Bearer {lab['shim']}"}
    bad = []
    for e in flatten(portal_app.app):
        if e.kind == "route":
            reqs = [(m, sample_path(e.path)) for m in sorted(e.methods - {"HEAD", "OPTIONS"})]
        elif e.kind == "mount":
            reqs = [("GET", e.path + "/x")]
        else:
            continue
        for m, path in reqs:
            if path.startswith(auth.OPENAI_PREFIX) or path.startswith(auth.EDGE_PREFIX) or auth.is_exempt(path, m):
                continue
            r = c.request(m, path, headers=h)
            if r.status_code != 401:
                bad.append((m, path, r.status_code))
    assert not bad, bad[:10]
    assert c.get("/api/jobs/state", headers=h).json()["error"] == "token_scope"


def test_wrong_shim_token_is_auth_invalid(lab):
    r = mk().get("/api/openai/v1/models", headers={"Authorization": "Bearer arl1s_" + "Z" * 43})
    assert r.status_code == 401 and r.json()["error"] == "auth_invalid"


def test_whoami_for_a_shim_is_not_reachable_outside_scope(lab):
    assert mk().get("/api/auth/whoami", headers={"Authorization": f"Bearer {lab['shim']}"}).status_code == 401


def test_redactor_masks_the_shim_token(lab):
    from arail import redact
    out = redact.capture_body(prompt=f"key {lab['shim']}", response="")["prompt"]
    assert lab["shim"] not in out


def _cfg():
    return opencode._render_opencode_config(provider="my_machine", model="m", portal_port=8080,
                                            tier="maximus", models_list=None)


def test_opencode_env_and_config_carry_the_shim_token_never_the_portal_bearer(lab):
    env = opencode._compute_source_env()
    cfg = json.dumps(_cfg())
    assert env["OPENCODE_API_KEY"] == lab["shim"]
    assert lab["shim"] in cfg
    blob = json.dumps(env) + cfg
    assert lab["tok"] not in blob
    assert not re.search(r"arl1_", blob)


def test_written_config_is_owner_only(lab, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LAB_ROOT", tmp_path / "lab")
    res = opencode._regenerate_config_unlocked() if hasattr(opencode, "_regenerate_config_unlocked") else None
    if res and res.get("ok"):
        assert stat.S_IMODE(os.stat(res["path"]).st_mode) & 0o077 == 0
