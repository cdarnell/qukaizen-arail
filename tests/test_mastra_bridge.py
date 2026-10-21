"""Mastra Studio spike: env allowlist, output parsing, write-back, page states,
restricted-browser flags, loopback bind, and portal gating (T-U-1..4, 7, 8, 10, T-SEC-3/5)."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import mastra_bridge as mb

GOOD = {"mastra_trace_id": "abc123", "scorer_id": mb.SCORER_ID, "score": 0.5,
        "mastra_version": "1.34.0", "core_version": "1.76.0",
        "experiment_id": "e1", "arail_trace_id": "r1"}


# ── T-U-3 / T-U-4 / EE guard: _mastra_env ────────────────────────────

@pytest.mark.parametrize("parent_val", ["0", "", "false"])
def test_env_forces_all_three_flags(parent_val):
    env = mb.mastra_env({"PATH": "/bin", "MASTRA_TELEMETRY_DISABLED": parent_val,
                         "MASTRA_OFFLINE": parent_val, "MASTRA_AUTO_REFRESH_PROVIDERS": "true"})
    assert env["MASTRA_TELEMETRY_DISABLED"] == "1"
    assert env["MASTRA_OFFLINE"] == "1"
    assert env["MASTRA_AUTO_REFRESH_PROVIDERS"] == "false"


def test_env_drops_secrets_tokens_and_ee_license_vars():
    parent = {
        "PATH": "/bin", "HOME": "/h", "NODE_OPTIONS": "--x",
        "MASTRA_PLATFORM_ACCESS_TOKEN": "t", "OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k",
        "GITHUB_TOKEN": "t", "NODE_AUTH_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s",
        "MASTRA_LICENSE_KEY": "l", "MASTRA_EE_LICENSE": "l", "MASTRA_LICENSE_URL": "u",
        "SOME_LICENSE_KEY": "l", "RANDOM_UNLISTED": "x", "ARAIL_PASSWORD": "p",
    }
    env = mb.mastra_env(parent)
    for bad in ("MASTRA_PLATFORM_ACCESS_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
                "NODE_AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY", "MASTRA_LICENSE_KEY", "MASTRA_EE_LICENSE",
                "MASTRA_LICENSE_URL", "SOME_LICENSE_KEY", "RANDOM_UNLISTED", "ARAIL_PASSWORD"):
        assert bad not in env
    assert env["PATH"] == "/bin" and env["NODE_OPTIONS"] == "--x"


def test_ee_guard_fails_if_any_license_var_survives():
    """Guard test: no variable that could switch on Mastra EE features may reach the child."""
    env = mb.mastra_env({"PATH": "/bin", "MASTRA_LICENSE_KEY": "x", "MASTRA_EE_LICENSE": "x"})
    assert not [k for k in env if re.search(r"LICENSE|_EE", k, re.I) and k != "ARAIL_PYTHON"]


# ── T-U-2: page states ────────────────────────────────────────────────

def test_page_state_each_of_five():
    nodev = lambda v: (lambda: v)  # noqa: E731
    T, F = (lambda: True), (lambda: False)
    assert mb.resolve_page_state(nodev(None), T, T)["state"] == "node_missing"
    old = mb.resolve_page_state(nodev((20, 1, 0)), T, T)
    assert old["state"] == "node_too_old" and "v20.1.0" in old["detail"]
    assert mb.resolve_page_state(nodev((22, 12, 9)), T, T)["state"] == "node_too_old"
    assert mb.resolve_page_state(nodev((22, 13, 0)), F, T)["state"] == "not_installed"
    assert mb.resolve_page_state(nodev((25, 9, 0)), T, F)["state"] == "not_running"
    assert mb.resolve_page_state(nodev((25, 9, 0)), T, T)["state"] == "running"


def test_page_detail_has_no_traceback_text():
    for v in (None, (20, 0, 0)):
        assert "Traceback" not in mb.resolve_page_state(lambda v=v: v, lambda: True, lambda: True)["detail"]


# ── T-U-7: child output parser ────────────────────────────────────────

def _line(**kw):
    return json.dumps({**GOOD, **kw}) + "\n"


def test_parser_accepts_valid_line():
    assert mb.parse_child_output(_line())["score"] == 0.5
    assert mb.parse_child_output(_line(score=1))["score"] == 1.0


@pytest.mark.parametrize("bad", [
    "garbage\n", "", "\n\n", _line() + _line(), "[1,2]\n",
    '{"mastra_trace_id":"a","scorer_id":"s","score":NaN,"mastra_version":"1","core_version":"1"}\n',
    _line(score=1.5), _line(score=-0.1), _line(score=True), _line(score="0.5"), _line(score=None),
    _line(mastra_trace_id=""), _line(mastra_trace_id="x" * 129), _line(scorer_id=5),
    _line(experiment_id=7), "x" * (10 * 1024 * 1024),
])
def test_parser_rejects(bad):
    with pytest.raises(mb.ChildOutputError):
        mb.parse_child_output(bad)


def test_parser_rejects_missing_fields():
    obj = dict(GOOD)
    del obj["scorer_id"]
    with pytest.raises(mb.ChildOutputError):
        mb.parse_child_output(json.dumps(obj))


def test_parser_surfaces_child_error_code():
    with pytest.raises(mb.ChildOutputError, match="env_MASTRA_OFFLINE_not_1"):
        mb.parse_child_output('{"error":"env_MASTRA_OFFLINE_not_1"}\n')


@pytest.mark.parametrize("bad", ["garbage", _line(score=1.5), "x" * (1 << 20)])
def test_bad_output_writes_nothing(bad, tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    exp = tr.create("h", "m", {})
    res = _run(tr, lambda *a: bad, exp["id"])
    assert res["error"] in ("bad_child_output",) or res["error"]
    assert tr._load(exp["id"])["observations"] == []


def _run(tr, spawn, exp_id=None, **kw):
    kw.setdefault("check_model", lambda: True)
    import arail.mastra_bridge as m
    orig = (m.is_installed, m.node_version)
    m.is_installed, m.node_version = (lambda: True), (lambda: (25, 0, 0))
    try:
        return m.run_probe("measure it", exp_id, tracker=tr, spawn=spawn, **kw)
    finally:
        m.is_installed, m.node_version = orig


# ── T-U-8: exact observation contract ─────────────────────────────────

def test_write_back_contract(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    exp = tr.create("h", "m", {})
    res = _run(tr, lambda *a: _line(experiment_id=exp["id"]), None)
    assert res == {"experiment_id": exp["id"], "mastra_trace_id": "abc123", "score": 0.5}
    obs = tr._load(exp["id"])["observations"]
    assert len(obs) == 1
    assert obs[0]["observation"] == "mastra probe score (not a 3DEval measurement)"
    assert obs[0]["data"] == {
        "source": "mastra", "kind": "probe", "trace_id": "abc123", "arail_trace_id": "r1",
        "scorer_id": mb.SCORER_ID, "score": 0.5, "mastra_version": "1.34.0", "core_version": "1.76.0"}


# ── T-U-10: failure paths ─────────────────────────────────────────────

def test_experiment_missing(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    assert _run(tr, lambda *a: _line(), "nope") == {"error": "experiment_missing"}
    assert _run(tr, lambda *a: _line(experiment_id="ghost"), None) == {"error": "experiment_missing"}


def test_model_unavailable_spawns_nothing(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    called = []
    res = _run(ExperimentTracker(tmp_path), lambda *a: called.append(1), None, check_model=lambda: False)
    assert res == {"error": "model_unavailable"} and not called


def test_timeout_writes_nothing(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    exp = tr.create("h", "m", {})

    def boom(*a):
        raise TimeoutError

    assert _run(tr, boom, exp["id"]) == {"error": "timeout"}
    assert tr._load(exp["id"])["observations"] == []


def test_invalid_goal():
    assert mb.run_probe("") == {"error": "invalid_goal"}


# ── T-SEC-5: no shell ─────────────────────────────────────────────────

def test_spawn_uses_argv_not_shell(monkeypatch):
    seen = {}

    class P:
        pid = 1
        def communicate(self, payload=None, timeout=None):
            seen["payload"] = payload
            return ("{}", "")

    def fake_popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return P()

    monkeypatch.setattr(mb.subprocess, "Popen", fake_popen)
    evil = '"; rm -rf / #$(x)'
    mb._spawn_child(json.dumps({"goal": evil}), {}, 5)
    assert seen["shell"] is False and isinstance(seen["argv"], list)
    assert evil not in " ".join(seen["argv"]) and evil in seen["payload"]
    assert seen["start_new_session"] is True


# ── Restricted browser + loopback bind ────────────────────────────────

def test_browser_args_block_non_loopback():
    args = mb.browser_args()
    rules = next(a for a in args if a.startswith("--host-resolver-rules="))
    assert "MAP * ~NOTFOUND" in rules and "EXCLUDE 127.0.0.1" in rules and "EXCLUDE localhost" in rules
    assert any(a.startswith("--proxy-server=http://127.0.0.1:") for a in args)
    assert any(a.startswith("--user-data-dir=") and "/mastra/browser" in a for a in args)
    assert args[-1].startswith("http://127.0.0.1:")


def test_studio_server_host_is_loopback_in_config():
    cfg = (mb.MASTRA_DIR / "src" / "mastra" / "config.mjs").read_text()
    assert re.search(r"server:\s*\{\s*host:\s*'127\.0\.0\.1'\s*\}", cfg)
    assert "0.0.0.0" not in cfg


def test_listener_loopback_check():
    assert mb.is_loopback_listener(["127.0.0.1:4111"], 4111)
    assert mb.is_loopback_listener(["127.0.0.1:4111", "[::1]:4111"], 4111)
    assert not mb.is_loopback_listener(["*:4111"], 4111)
    assert not mb.is_loopback_listener(["127.0.0.1:4111", "*:4111"], 4111)
    assert not mb.is_loopback_listener([], 4111)


def test_run_once_mjs_requires_all_three_flags_and_no_ee_vars():
    js = (mb.MASTRA_DIR / "run-once.mjs").read_text()
    for flag in mb.REQUIRED_FLAGS:
        assert flag in js
    assert "MASTRA_PLATFORM_ACCESS_TOKEN" in js and "MASTRA_LICENSE_KEY" in js and "MASTRA_EE_LICENSE" in js


# ── T-U-1 / T-SEC-3: portal gating ────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    from arail.portal.app import app
    return TestClient(app, raise_server_exceptions=False)


def test_registry_entries():
    from arail.portal.app import _OPTIONAL_SERVICES, _TIER_SURFACES
    assert _OPTIONAL_SERVICES["mastra-studio"] == "maximus"
    assert "mastra" not in _TIER_SURFACES["minimalist"]
    assert "mastra" in _TIER_SURFACES["maximus"]


def test_page_404_on_minimalist_and_200_on_maximus(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert client.get("/mastra").status_code == 404
    for path in ("/api/mastra/start", "/api/mastra/stop", "/api/mastra/run-once"):
        assert client.post(path, json={}).status_code == 404
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = client.get("/mastra")
    assert r.status_code == 200
    assert 'data-state="' in r.text


def test_page_never_links_to_studio_url(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "running", "detail": "up"})
    html = client.get("/mastra").text
    assert "./arailctl mastra open" in html and "~16 non-loopback" not in html or "16 non-loopback" in html
    assert not re.search(r'(href|src)="https?://(127\.0\.0\.1|localhost):4111', html)
    assert "<iframe" not in html


@pytest.mark.parametrize("path", ["/api/mastra/start", "/api/mastra/stop", "/api/mastra/run-once"])
def test_csrf_blocks_cross_site(client, monkeypatch, path):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = client.post(path, json={"goal": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post(path, json={"goal": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_run_once_endpoint_validates(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    r = client.post("/api/mastra/run-once", json={"goal": ""})
    assert r.status_code == 400 and r.json() == {"error": "invalid_goal"}


def test_services_dict_reports_mastra_only_when_up_and_maximus(monkeypatch):
    from arail.portal import app as appmod
    kw = dict(portal_up=True, kc_up=True, ttyd_up=False, notebook_up=False, lance_up=False,
              marimo_running=False, open_notebook_running=False, ollama_up=False,
              neo4j_up=False, opencode_up=False)
    monkeypatch.setenv("LAB_TIER", "maximus")
    assert appmod._build_services_dict(**kw, mastra_studio_up=True)["mastra-studio"] is True
    assert "mastra-studio" not in appmod._build_services_dict(**kw)
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert "mastra-studio" not in appmod._build_services_dict(**kw, mastra_studio_up=True)


# ── EE chunk pin (owner decision 2026-10-21) ──────────────────────────

def _check_ee(tmp_path, urls):
    import shutil, subprocess
    node = shutil.which("node")
    if not node or not (mb.MASTRA_DIR / "node_modules" / "@mastra" / "core").is_dir():
        pytest.skip("Mastra not installed")
    f = tmp_path / "urls.txt"
    f.write_text("\n".join(urls) + "\n")
    return subprocess.run([node, str(mb.REPO_ROOT / "scripts/mastra/check-ee-loads.mjs"), str(f)],
                          capture_output=True, text=True)


def test_ee_check_allows_only_pinned_chunk(tmp_path):
    pin = dict(l.split("=") for l in (mb.REPO_ROOT / "scripts/mastra/ee-chunk.pin").read_text().splitlines()
               if l and not l.startswith("#"))
    base = "file:///x/integrations/mastra-studio/node_modules/"
    assert _check_ee(tmp_path, [base + pin["chunk"], base + "@mastra/core/dist/agent-1.js"]).returncode == 0
    assert _check_ee(tmp_path, [base + "@mastra/core/dist/ee-OTHER.js"]).returncode == 1
    assert _check_ee(tmp_path, [base + "@mastra/core/dist/auth/ee/index.js"]).returncode == 1


def test_third_party_licences_state_no_ee_use():
    readme = (mb.REPO_ROOT / "THIRD-PARTY-LICENSES" / "mastra" / "README.md").read_text()
    assert "not used, not redistributed" in readme and "Apache-2.0" in readme
