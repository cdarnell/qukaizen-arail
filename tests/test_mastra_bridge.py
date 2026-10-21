"""Mastra Studio spike: env allowlist, output parsing, write-back, page states,
restricted-browser flags, loopback bind, and portal gating (T-U-1..4, 7, 8, 10, T-SEC-3/5)."""
from __future__ import annotations

import json
import math
import os
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
    assert env["PATH"] == "/bin"


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

def test_user_experiment_id_cannot_redirect_score(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    created, other = tr.create("h", "m", {}), tr.create("h2", "m", {})
    res = _run(tr, lambda *a: _line(experiment_id=created["id"]), other["id"])
    assert res == {"error": "experiment_mismatch"}
    assert tr._load(other["id"])["observations"] == [] and tr._load(created["id"])["observations"] == []
    ok = _run(tr, lambda *a: _line(experiment_id=created["id"]), created["id"])
    assert ok["experiment_id"] == created["id"]


@pytest.mark.parametrize("bad", ["../../etc", "a/b", "x" * 65, "..", "a b", ""])
def test_experiment_id_charset(tmp_path, bad):
    from arail.skills.experiment_tracker import ExperimentTracker
    called = []
    res = _run(ExperimentTracker(tmp_path), lambda *a: called.append(1), bad)
    assert res == {"error": "experiment_missing"} and not called


def test_child_experiment_id_charset_enforced(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    assert _run(tr, lambda *a: _line(experiment_id="../../x"), None) == {"error": "bad_child_output"}


def test_bad_score_from_child_writes_nothing(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    exp = tr.create("h", "m", {})
    assert _run(tr, lambda *a: '{"error":"bad_score"}\n', exp["id"]) == {"error": "bad_score"}
    assert tr._load(exp["id"])["observations"] == []


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
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args
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


# ── Fix round: env, stop, install, port, scorer, reset ───────────────

def test_node_options_and_friends_never_reach_child():
    parent = {"PATH": "/bin", "NODE_OPTIONS": "--inspect=0.0.0.0", "NODE_PATH": "/x",
              "NODE_EXTRA_CA_CERTS": "/ca.pem", "NODE_ENV": "production"}
    env = mb.mastra_env(parent)
    assert not [k for k in env if k.startswith("NODE_")]
    js = (mb.MASTRA_DIR / "run-once.mjs").read_text()
    grand = js[js.index("Object.fromEntries(["):js.index(".filter((k) => process.env[k])")]
    assert "NODE_" not in grand  # the Python MCP grandchild list


def test_real_node_child_env_dump_has_no_node_options(monkeypatch):
    import shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node missing")
    monkeypatch.setenv("NODE_OPTIONS", "--no-warnings")
    env = mb.mastra_env()
    out = subprocess.run([node, "-e", "console.log(JSON.stringify(Object.keys(process.env)))"],
                         env=env, capture_output=True, text=True).stdout
    assert "NODE_OPTIONS" not in json.loads(out)


def test_studio_port_is_fixed(monkeypatch):
    monkeypatch.setenv("MASTRA_STUDIO_PORT", "9999")
    assert mb.studio_port() == 4111


def _spawn_sleeper(tmp_path, name):
    import subprocess
    script = tmp_path / name
    script.write_text("#!/bin/bash\nexec sleep 120\n")
    script.chmod(0o755)
    # bash <script> keeps the script name in the command line; sleep after exec would not.
    script.write_text("#!/bin/bash\nsleep 120\n")
    return subprocess.Popen(["/bin/bash", str(script)], start_new_session=True)


def _spawn_studio_like():
    """A group leader whose argv/cwd look exactly like the one start_studio launches."""
    import subprocess
    return subprocess.Popen(["/bin/bash", "-c", 'exec -a "npm exec mastra dev" sleep 120'],
                            cwd=str(mb.MASTRA_DIR), start_new_session=True)


def test_stop_never_signals_unrelated_reused_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    victim = _spawn_sleeper(tmp_path, "unrelated.sh")
    try:
        mb._pid_file().write_text(f"{victim.pid} {victim.pid}")   # "reused" pid, not mastra
        res = mb.stop_studio()
        assert res["stale"] is True and not mb._pid_file().exists()
        assert victim.poll() is None            # still alive
        mb._pid_file().write_text(f"{victim.pid} 1")              # pgid mismatch
        assert mb.stop_studio().get("stale") is True and victim.poll() is None
        mb._pid_file().write_text("garbage")
        assert mb.stop_studio().get("stale") is True
        mb._pid_file().write_text("999999 999999")                # dead pid
        assert mb.stop_studio().get("stale") is True
    finally:
        victim.kill()
        victim.wait()


def test_stop_kills_a_genuine_studio_group_and_leaves_no_process(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    p = _spawn_studio_like()   # T-I-5 analogue
    import os, time
    mb._pid_file().write_text(f"{p.pid} {os.getpgid(p.pid)}")
    assert mb.stop_studio()["was_running"] is True
    p.wait(timeout=10)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.killpg(p.pid, 0)
    assert not mb._pid_file().exists()


def test_install_runs_ee_pin_check_and_fails_on_mismatch(monkeypatch):
    calls = []

    class R:
        def __init__(self, rc): self.returncode = rc

    def fake_run(argv, **kw):
        calls.append(argv)
        return R(0 if argv[0] == "npm" else 1)

    monkeypatch.setattr(mb, "node_version", lambda: (25, 0, 0))
    monkeypatch.setattr(mb.subprocess, "run", fake_run)
    assert mb.install() == 1
    assert calls[0][:2] == ["npm", "ci"] and "--ignore-scripts" in calls[0]
    assert calls[1][-2:] == [str(mb.REPO_ROOT / "scripts/mastra/check-ee-loads.mjs"), "--installed"]


def test_ee_installed_check_passes_on_current_install():
    import shutil, subprocess
    if not shutil.which("node") or not mb.is_installed():
        pytest.skip("Mastra not installed")
    r = subprocess.run([shutil.which("node"), str(mb.REPO_ROOT / "scripts/mastra/check-ee-loads.mjs"),
                        "--installed"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_start_studio_closes_log_handle():
    import inspect
    assert "with open(mastra_db_path().parent" in inspect.getsource(mb.start_studio)


def test_timeout_budget_fits_inside_kill():
    js = (mb.MASTRA_DIR / "run-once.mjs").read_text()
    probe = (mb.MASTRA_DIR / "src/mastra/run-probe.mjs").read_text()
    tool_min = int(re.search(r"timeout:\s*(\d+)\s*\*\s*60", js).group(1))
    scorer_s = int(re.search(r"AbortSignal\.timeout\((\d+)_?(\d+)\)", probe).expand(r"\1\2")) // 1000
    assert tool_min * 60 + scorer_s < mb.RUN_TIMEOUT_S


def _serve_reply(content):
    import http.server, threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            body = json.dumps({"message": {"content": content}}).encode()
            self.send_response(200); self.send_header("content-length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("reply,expect", [
    ("not json at all", "bad_score"), ('{"score": "high"}', "bad_score"), ('{"score": null}', "bad_score"),
    ('{"score": 1.5}', "bad_score"), ('{"score": -1}', "bad_score"), ("{}", "bad_score"),
    ('{"score": 0.25}', 0.25), ('{"score": 0}', 0)])
def test_scorer_never_coerces_unusable_reply_to_a_number(reply, expect):
    import shutil, subprocess
    node = shutil.which("node")
    if not node or not mb.is_installed():
        pytest.skip("Mastra not installed")
    srv = _serve_reply(reply)
    try:
        code = ("import('./src/mastra/run-probe.mjs').then(m=>m.scoreReport('g','r'))"
                ".then(v=>console.log('OK '+v),e=>console.log('ERR '+e.message))")
        out = subprocess.run([node, "-e", code], cwd=str(mb.MASTRA_DIR), capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"],
                                  "ARAIL_OLLAMA_URL": f"http://127.0.0.1:{srv.server_address[1]}"}).stdout.strip()
    finally:
        srv.shutdown()
    assert out == (f"ERR {expect}" if expect == "bad_score" else f"OK {expect}")


# T-I-4 / T-I-5: drive the real reset.sh (scoped `data` mode only) in a sandbox
def _reset_sandbox(tmp_path):
    import shutil
    fake = tmp_path / "fakerepo"
    (fake / "scripts").mkdir(parents=True)
    shutil.copy2(mb.REPO_ROOT / "scripts" / "reset.sh", fake / "scripts" / "reset.sh")
    return fake


def _reset(fake, env=None):
    import subprocess
    return subprocess.run(["/bin/bash", "scripts/reset.sh", "data", "--yes"], cwd=fake, capture_output=True,
                          text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(fake / "home"), **(env or {})})


def test_reset_wipes_lab_data_mastra(tmp_path):                                  # T-I-4
    fake = _reset_sandbox(tmp_path)
    d = fake / "lab" / "data" / "mastra" / "browser"
    d.mkdir(parents=True)
    (fake / "lab" / "data" / "mastra" / "mastra.db").write_text("db")
    r = _reset(fake)
    assert r.returncode == 0, r.stderr
    assert not (fake / "lab" / "data" / "mastra").exists()


def test_reset_stops_running_studio_but_not_unrelated_pid(tmp_path):            # T-I-5
    import os
    fake = _reset_sandbox(tmp_path)
    mdir = fake / "lab" / "data" / "mastra"
    mdir.mkdir(parents=True)
    studio = _spawn_studio_like()
    other = _spawn_sleeper(tmp_path, "unrelated.sh")
    try:
        (mdir / "studio.pid").write_text(f"{studio.pid} {os.getpgid(studio.pid)}")
        assert _reset(fake).returncode == 0
        studio.wait(timeout=10)
        (fake / "lab" / "data" / "mastra").mkdir(parents=True, exist_ok=True)
        (mdir / "studio.pid").write_text(f"{other.pid} {other.pid}")
        assert _reset(fake).returncode == 0
        assert other.poll() is None                      # unrelated process untouched
    finally:
        for p in (studio, other):
            p.kill(); p.wait()


# ── QA fix round ──────────────────────────────────────────────────────

def test_page_state_port_in_use_and_start_refuses(monkeypatch):
    nodev, T = (lambda: (25, 0, 0)), (lambda: True)
    st = mb.resolve_page_state(nodev, T, lambda: "foreign")
    assert st["state"] == "port_in_use" and "another program" in st["detail"]
    monkeypatch.setattr(mb, "resolve_page_state", lambda: st)
    assert mb.start_studio() == {"ok": False, "error": "port_in_use", "status": 409}


def test_foreign_listener_is_never_stopped(monkeypatch, tmp_path):
    import socket
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 4111))
    except OSError:
        pytest.skip("4111 busy")
    s.listen(1)
    try:
        assert mb.listener_state() == "foreign"
        res = mb.stop_studio()
        assert res["was_running"] is False
        assert s.fileno() != -1                       # we are still alive and listening
        assert mb.listener_state() == "foreign"
    finally:
        s.close()


def test_default_config_paths_are_absolute(tmp_path):
    import subprocess, sys
    code = ("import json, arail.mastra_bridge as mb; e = mb.mastra_env(); "
            "print(json.dumps({k: e[k] for k in ('ARAIL_MASTRA_DB','ARAIL_DATA_DIR','LAB_ROOT','LAB_PKB','ARAIL_ROOT')}))")
    out = json.loads(subprocess.run([sys.executable, "-c", code], cwd=str(mb.REPO_ROOT), capture_output=True,
                                    text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                                                    "PYTHONPATH": str(mb.REPO_ROOT / "src")}).stdout)
    assert all(os.path.isabs(v) for v in out.values()), out
    assert out["ARAIL_MASTRA_DB"].startswith(out["ARAIL_DATA_DIR"] + os.sep)


def test_relative_parent_values_cannot_leak_into_child_env():
    env = mb.mastra_env({"PATH": "/bin", "ARAIL_MASTRA_DB": "rel/x.db", "ARAIL_DATA_DIR": "rel", "LAB_ROOT": "lab"})
    assert os.path.isabs(env["ARAIL_MASTRA_DB"]) and os.path.isabs(env["ARAIL_DATA_DIR"]) and os.path.isabs(env["LAB_ROOT"])


def test_cli_prints_plain_message_on_oserror(monkeypatch, capsys):
    from arail import mastra_cli

    def boom():
        raise PermissionError(13, "Permission denied", "/x/lab/data/mastra")

    monkeypatch.setattr(mb, "start_studio", boom)
    assert mastra_cli.main(["start"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "Permission denied" in err


def test_reused_pid_with_mastra_path_in_argv_is_not_signalled(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    victim = subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)
    try:
        mb._pid_file().write_text(f"{victim.pid} {victim.pid}")
        res = mb.stop_studio(lambda pid: "npm exec mastra dev", lambda pid: "/somewhere/else")
        assert res.get("stale") is True and victim.poll() is None
    finally:
        victim.kill(); victim.wait()


def test_goal_length_rule_is_code_points_in_node_too():
    js = (mb.MASTRA_DIR / "run-once.mjs").read_text()
    assert "[...req.goal].length > 2000" in js


def test_refusal_codes_are_passed_through_by_node_driver():
    js = (mb.MASTRA_DIR / "run-once.mjs").read_text()
    probe = (mb.MASTRA_DIR / "src/mastra/run-probe.mjs").read_text()
    for code in ("researcher_busy", "goal_in_progress", "wrong_tier"):
        assert code in js and code in probe


def test_health_does_not_report_foreign_listener_as_studio(monkeypatch):
    import inspect
    from arail.portal import app as appmod
    src = inspect.getsource(appmod)
    assert '_mastra_bridge.listener_state)) != "foreign"' in src
