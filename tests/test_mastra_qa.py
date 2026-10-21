"""QA pass for the Mastra Studio spike (sprints/2026-10-13-mastra-studio-spike/TEST_REPORT.md).

Weighting: security 40 / setup+lifecycle 30 / failure-mode grace 20 / happy+regression 10.
Tests named ``test_defect_*`` reproduce a finding in TEST_REPORT.md and are EXPECTED TO
FAIL until the builder fixes it. Real-process tests skip when node / Mastra / Ollama are absent.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arail import mastra_bridge as mb
from arail.mcp import researcher_tool as rt

REPO = mb.REPO_ROOT
PY = sys.executable
NODE = shutil.which("node")
SECRETS = {
    "NODE_OPTIONS": "--no-warnings", "NODE_PATH": "/qa/x", "NODE_EXTRA_CA_CERTS": "/qa/ca.pem",
    "MASTRA_LICENSE_KEY": "qa-lic", "MASTRA_EE_LICENSE": "qa-ee", "MASTRA_EE_FEATURES": "qa-ee2",
    "MASTRA_PLATFORM_ACCESS_TOKEN": "qa-plat", "ANTHROPIC_API_KEY": "qa-ant", "HF_TOKEN": "qa-hf",
    "AWS_ACCESS_KEY_ID": "qa-aws", "AWS_SECRET_ACCESS_KEY": "qa-aws-s", "AWS_PROFILE": "qa-prof",
}


def _need_node_mastra():
    if not NODE or not mb.is_installed():
        pytest.skip("node / Mastra install missing")


def _need_ollama():
    _need_node_mastra()
    if not mb.model_available():
        pytest.skip("ollama not serving on loopback")


def _sandbox_env(tmp_path, **extra):
    """Clean env for a real CLI run against a throwaway lab (never the worktree lab)."""
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(tmp_path)),
           "LAB_ROOT": str(tmp_path / "lab"), "LAB_TIER": "maximus", "LAB_MODE": "airgapped",
           "PYTHONPATH": str(REPO / "src")}
    env.update(extra)
    return env


def _cli(args, env, timeout=300):
    r = subprocess.run([PY, "-m", "arail.mastra_cli", *args], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=timeout)
    return r


# ═══ SECURITY (portal surface) ════════════════════════════════════════

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    monkeypatch.setenv("LAB_TIER", "maximus")
    from arail.portal.app import app
    return TestClient(app, raise_server_exceptions=False)


MUTATING = ["/api/mastra/start", "/api/mastra/stop", "/api/mastra/run-once"]


@pytest.fixture()
def no_side_effects(monkeypatch):
    """Any call that reaches the bridge is a test failure (gate must stop it first)."""
    def boom(*a, **k):
        raise AssertionError("bridge reached past the gate")
    monkeypatch.setattr(mb, "start_studio", boom)
    monkeypatch.setattr(mb, "stop_studio", boom)
    monkeypatch.setattr(mb, "run_probe", boom)


@pytest.mark.parametrize("path", MUTATING)
@pytest.mark.parametrize("headers", [
    {"Origin": "http://127.0.0.1:4111"},               # Studio's own origin (same-site, other port)
    {"Origin": "http://localhost:4111"},
    {"Origin": "null"},                                 # sandboxed iframe
    {"Origin": "http://testserver.evil.example"},
    {"Origin": "http://testserver@evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "none"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:4111"},
    {"Origin": "http://evil.example", "Referer": "http://testserver/mastra"},  # Referer can't launder Origin
])
def test_mutating_routes_refuse_forged_origin(client, no_side_effects, path, headers):
    r = client.post(path, json={"goal": "x"}, headers=headers)
    assert r.status_code == 403, (headers, r.status_code, r.text)


@pytest.mark.parametrize("path", MUTATING)
def test_tier_404_comes_before_csrf(client, monkeypatch, no_side_effects, path):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    # A global CSRF middleware may answer 403 before the route's tier 404; either is a refusal
    # (INFO in TEST_REPORT.md). Without the hostile header the tier gate must answer 404.
    r = client.post(path, json={"goal": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code in (403, 404)
    assert client.post(path, json={"goal": "x"}).status_code == 404


@pytest.mark.parametrize("path", MUTATING)
def test_non_loopback_bind_refuses_mutation(client, monkeypatch, no_side_effects, path):
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    r = client.post(path, json={"goal": "x"})
    assert r.status_code == 403 and r.json()["error"] == "bind_not_loopback"


@pytest.mark.parametrize("path", MUTATING)
def test_get_on_mutating_routes_has_no_side_effect(client, no_side_effects, path):
    assert client.get(path).status_code in (404, 405)


def test_minimalist_mastra_page_is_404(client, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert client.get("/mastra").status_code == 404


@pytest.mark.parametrize("body,ctype", [
    (b"not json", "application/json"), (b"[1,2]", "application/json"), (b"\"goal\"", "application/json"),
    (b"", "application/json"), (b'{"goal": "x"}', "text/plain"), (b"\xff\xfe", "application/json"),
])
def test_run_once_malformed_body_is_plain_400(client, monkeypatch, body, ctype):
    calls = []
    monkeypatch.setattr(mb, "run_probe", lambda g, e: calls.append((g, e)) or
                        ({"error": "invalid_goal"} if not isinstance(g, str) else {"error": "model_unavailable"}))
    r = client.post("/api/mastra/run-once", content=body, headers={"Content-Type": ctype})
    assert r.status_code in (400, 502)
    assert "Traceback" not in r.text and r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("state", ["node_missing", "node_too_old", "not_installed", "not_running", "running"])
def test_page_never_embeds_or_links_studio_in_any_state(client, monkeypatch, state):
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": state, "detail": "d"})
    html = client.get("/mastra").text
    low = html.lower()
    assert "<iframe" not in low and "<frame" not in low and "<embed" not in low and "<object" not in low
    assert ":4111" not in html                         # no link, no fetch, no form action to Studio
    assert "window.open" not in html and "location.href" not in html


def test_page_escapes_detail_xss(client, monkeypatch):
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "node_missing", "detail": evil})
    html = client.get("/mastra").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_page_result_rendering_uses_textcontent_not_innerhtml(client, monkeypatch):
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "running", "detail": "d"})
    html = client.get("/mastra").text
    html = html[html.index('id="mastra-page"'):]          # the sprint's own markup + script only
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html


def test_run_once_error_echo_is_json_not_html(client, monkeypatch):
    monkeypatch.setattr(mb, "run_probe", lambda g, e: {"error": "<script>alert(1)</script>"})
    r = client.post("/api/mastra/run-once", json={"goal": "x"})
    assert r.headers["content-type"].startswith("application/json")


def test_hostile_trace_id_is_stored_as_data_and_research_ui_escapes_observations(tmp_path):
    from arail.skills.experiment_tracker import ExperimentTracker
    tr = ExperimentTracker(tmp_path)
    eid = tr.create(hypothesis="qa xss", methodology="m", variables={})["id"]
    parsed = {"mastra_trace_id": "<script>alert(1)</script>", "scorer_id": mb.SCORER_ID, "score": 0.5,
              "mastra_version": "1.34.0", "core_version": "1.76.0"}
    mb.write_back(tr, eid, parsed)
    rec = tr._load(eid)
    obs = rec["observations"][-1]
    assert obs["observation"] == mb.OBSERVATION_TEXT          # the rendered text is our constant
    ui = (REPO / "src/arail/portal/templates/research.html").read_text()
    assert "${esc(o.observation || '')}" in ui and "o.data" not in ui   # data never rendered


# ═══ SECURITY (inputs into the bridge) ════════════════════════════════

@pytest.mark.parametrize("exp_id", ["../../etc", "..", "a/b", "a\\b", "x" * 65, "e\x00", "é", "", " ",
                                    "e1;rm -rf /", "$(id)", "e1\n", 5, ["e1"], {"x": 1}])
def test_bad_experiment_id_rejected_before_any_spawn(exp_id):
    spawned = []
    r = mb.run_probe("valid goal", exp_id, tracker=object(),
                     spawn=lambda *a: spawned.append(a), check_model=lambda: True)
    assert r == {"error": "experiment_missing"} and not spawned


@pytest.mark.parametrize("goal", [None, "", "   ", "\n\t", "x" * 2001, "y" * 10_000, 123, ["g"], {"g": 1}, b"bytes"])
def test_invalid_goals_refused_before_spawn(goal):
    spawned = []
    r = mb.run_probe(goal, None, tracker=object(), spawn=lambda *a: spawned.append(a), check_model=lambda: True)
    assert r == {"error": "invalid_goal"} and not spawned


@pytest.mark.parametrize("goal", [
    "x" * 1999, "x" * 2000,
    "מה הסיבה? ‮evil‬ والقمر",          # RTL + bidi override
    "nul\x00byte and \x07bell \x1b[31m ansi",
    '"}, "exp_id": "victim", "x": {"',               # JSON injection
    "$(rm -rf ~); `id` | cat /etc/passwd > /dev/null && echo $HOME; '\"\\",
    "퟿ emoji 🧪",
])
def test_hostile_goal_travels_verbatim_as_json_and_cannot_inject_exp_id(goal, monkeypatch):
    monkeypatch.setattr(mb, "node_version", lambda: (25, 0, 0))
    monkeypatch.setattr(mb, "is_installed", lambda: True)
    seen = {}

    def spawn(payload, env, timeout):
        seen["p"] = json.loads(payload)
        return json.dumps({"error": "qa_stop"})
    r = mb.run_probe(goal, None, tracker=object(), spawn=spawn, check_model=lambda: True)
    assert r == {"error": "qa_stop"}
    assert seen["p"] == {"goal": goal, "exp_id": None}


def test_defect_goal_length_counted_differently_by_python_and_node(tmp_path):
    """F-6: Python counts code points, run-once.mjs counts UTF-16 units: a 2000-emoji goal
    passes the bridge and is then refused by Node as bad_request."""
    _need_node_mastra()
    goal = "🧪" * 2000
    assert rt.validate_goal(goal) is None                  # Python accepts
    data = tmp_path / "data"
    (data / "mastra").mkdir(parents=True)
    env = mb.mastra_env({"PATH": os.environ["PATH"], "HOME": str(tmp_path),
                         "ARAIL_DATA_DIR": str(data), "ARAIL_MASTRA_DB": str(data / "mastra" / "mastra.db"),
                         "ARAIL_PYTHON": "/usr/bin/false"})
    out = subprocess.run([NODE, "run-once.mjs"], cwd=str(mb.MASTRA_DIR), env=env,
                         input=json.dumps({"goal": goal, "exp_id": None}), capture_output=True,
                         text=True, timeout=60).stdout.strip()
    assert json.loads(out.splitlines()[-1]).get("error") != "bad_request"


@pytest.mark.parametrize("stdout", [
    '{"score": NaN, "mastra_trace_id":"a","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"score": Infinity, "mastra_trace_id":"a","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"score": true, "mastra_trace_id":"a","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"score": "0.5", "mastra_trace_id":"a","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"score": 0.5, "mastra_trace_id":"' + "a" * 129 + '","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"score": 0.5, "mastra_trace_id":"","scorer_id":"s","mastra_version":"v","core_version":"c"}',
    '{"error": {"nested": "obj"}}', '{"error": "' + "e" * 200 + '"}',
    "[]", "null", "0.5", '"str"', "﻿{}", "{}\n{}", "x" * (70 * 1024),
    b"\xff\xfe\x00garbage",
])
def test_malformed_child_output_writes_nothing(stdout, monkeypatch):
    monkeypatch.setattr(mb, "node_version", lambda: (25, 0, 0))
    monkeypatch.setattr(mb, "is_installed", lambda: True)

    class T:
        writes = []
        def _load(self, e): return {}
        def observe(self, *a): self.writes.append(a)
    t = T()
    r = mb.run_probe("g", None, tracker=t, spawn=lambda *a: stdout, check_model=lambda: True)
    assert "error" in r and not t.writes
    assert len(r["error"]) <= mb.MAX_ID_LEN


def test_child_bad_score_path_writes_nothing(monkeypatch):
    monkeypatch.setattr(mb, "node_version", lambda: (25, 0, 0))
    monkeypatch.setattr(mb, "is_installed", lambda: True)

    class T:
        writes = []
        def _load(self, e): return {}
        def observe(self, *a): self.writes.append(a)
    t = T()
    r = mb.run_probe("g", None, tracker=t, spawn=lambda *a: '{"error":"bad_score"}\n', check_model=lambda: True)
    assert r == {"error": "bad_score"} and not t.writes


# ═══ SECURITY (child + grandchild environment, real processes) ═══════

def test_real_node_child_and_python_grandchild_env_has_no_secrets(tmp_path, monkeypatch):
    """Spawn the real run-once.mjs; ARAIL_PYTHON is a shim that dumps the MCP grandchild env.
    Also proves the timeout kills the whole group including the grandchild."""
    _need_node_mastra()
    dump = tmp_path / "grand.env"
    shim = tmp_path / "pyshim.sh"
    shim.write_text(f"#!/bin/bash\nenv > '{dump}'\necho \"PGID=$(ps -o pgid= -p $$ | tr -d ' ')\" >> '{dump}'\nexec sleep 300\n")
    shim.chmod(0o755)
    data = tmp_path / "data"
    (data / "mastra").mkdir(parents=True)
    parent = {**os.environ, **SECRETS, "ARAIL_DATA_DIR": str(data),
              "ARAIL_MASTRA_DB": str(data / "mastra" / "mastra.db"), "ARAIL_PYTHON": str(shim)}
    env = mb.mastra_env(parent)
    for k in SECRETS:
        assert k not in env
    t0 = time.time()
    with pytest.raises(TimeoutError):
        mb._spawn_child(json.dumps({"goal": "qa env", "exp_id": None}), env, 12)
    assert time.time() - t0 < 30
    text = dump.read_text()
    keys = {ln.split("=", 1)[0] for ln in text.splitlines() if "=" in ln}
    leaked = keys & set(SECRETS) | {k for k in keys if k.startswith(("NODE_", "AWS_", "MASTRA_EE", "MASTRA_LICENSE"))}
    assert not leaked, leaked
    for flag, val in mb.REQUIRED_FLAGS.items():
        assert f"{flag}={val}" in text
    time.sleep(0.5)
    pgid = int(text.split("PGID=")[1].split()[0])
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)                                 # node + shim + sleep all gone


def test_run_once_mjs_refuses_each_license_var_even_if_injected(tmp_path):
    _need_node_mastra()
    data = tmp_path / "data"
    base = mb.mastra_env({"PATH": os.environ["PATH"], "HOME": str(tmp_path), "ARAIL_DATA_DIR": str(data),
                          "ARAIL_MASTRA_DB": str(data / "mastra" / "mastra.db")})
    for k in ("MASTRA_LICENSE_KEY", "MASTRA_EE_LICENSE", "MASTRA_PLATFORM_ACCESS_TOKEN"):
        r = subprocess.run([NODE, "run-once.mjs"], cwd=str(mb.MASTRA_DIR), env={**base, k: "x"},
                           input='{"goal":"g"}', capture_output=True, text=True, timeout=60)
        assert r.returncode == 3 and len(r.stdout.strip().splitlines()) == 1, (k, r.stdout)
    for k in mb.REQUIRED_FLAGS:
        env = {**base}
        env.pop(k)
        r = subprocess.run([NODE, "run-once.mjs"], cwd=str(mb.MASTRA_DIR), env=env,
                           input='{"goal":"g"}', capture_output=True, text=True, timeout=60)
        assert r.returncode == 3


@pytest.mark.parametrize("stdin", ["not json", "", '{"goal": 5}', '{"goal": "' + "x" * 2001 + '"}', "[]"])
def test_run_once_mjs_malformed_request_is_one_json_line(tmp_path, stdin):
    _need_node_mastra()
    data = tmp_path / "data"
    env = mb.mastra_env({"PATH": os.environ["PATH"], "HOME": str(tmp_path), "ARAIL_DATA_DIR": str(data),
                         "ARAIL_MASTRA_DB": str(data / "mastra" / "mastra.db")})
    r = subprocess.run([NODE, "run-once.mjs"], cwd=str(mb.MASTRA_DIR), env=env, input=stdin,
                       capture_output=True, text=True, timeout=60)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and json.loads(lines[0]) == {"error": "bad_request"}


# ═══ SETUP / LIFECYCLE ════════════════════════════════════════════════

def test_defect_default_config_db_path_is_relative_and_child_refuses_it(tmp_path):
    """F-1 (HIGH): with no LAB_ROOT/ARAIL_DATA_DIR in the env (the default install),
    ARAIL_MASTRA_DB is the relative 'lab/data/mastra/mastra.db'; run-once.mjs resolves it
    against integrations/mastra-studio and exits bad_db_path, so every CLI/portal run fails."""
    code = ("import json, arail.mastra_bridge as mb; e = mb.mastra_env(); "
            "print(json.dumps({k: e[k] for k in ('ARAIL_MASTRA_DB', 'ARAIL_DATA_DIR')}))")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(REPO / "src")}
    out = json.loads(subprocess.run([PY, "-c", code], cwd=str(REPO), env=env, capture_output=True,
                                    text=True, timeout=60).stdout)
    db = Path(out["ARAIL_MASTRA_DB"])
    resolved_from_node_cwd = (mb.MASTRA_DIR / db).resolve() if not db.is_absolute() else db
    assert str(resolved_from_node_cwd).startswith(out["ARAIL_DATA_DIR"] + os.sep), out


def test_defect_foreign_listener_on_4111_is_not_reported_as_studio(monkeypatch):
    """F-3: anything listening on 127.0.0.1:4111 makes the page say 'running', start returns
    ok/already_running, stop does nothing and /api/system/health reports mastra-studio up."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 4111))
    except OSError:
        pytest.skip("4111 busy")
    s.listen(1)
    try:
        monkeypatch.setattr(mb, "node_version", lambda: (25, 0, 0))
        monkeypatch.setattr(mb, "is_installed", lambda: True)
        assert mb.resolve_page_state()["state"] != "running"
    finally:
        s.close()


def _group_with_orphan(tmp_path):
    """A group leader (argv mentions mastra) with a child in its group; returns (leader, child_pid)."""
    script = tmp_path / "mastra-dev-sim.sh"
    pidf = tmp_path / "child.pid"
    script.write_text(f"#!/bin/bash\nsleep 300 &\necho $! > '{pidf}'\nwait\n")
    script.chmod(0o755)
    leader = subprocess.Popen(["/bin/bash", str(script)], start_new_session=True)
    for _ in range(50):
        if pidf.exists() and pidf.read_text().strip():
            break
        time.sleep(0.1)
    return leader, int(pidf.read_text())


def test_defect_stop_after_leader_crash_orphans_studio(tmp_path, monkeypatch):
    """F-2: the real Studio tree is npm exec -> mastra dev -> node .mastra/output (all one pgid).
    If the npm leader dies, stop_studio() sees a dead pid, calls it stale, deletes the pid file
    and leaves the listener running with no way to stop it from ARAIL."""
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    leader, child = _group_with_orphan(tmp_path)
    try:
        mb._pid_file().write_text(f"{leader.pid} {leader.pid}")
        os.kill(leader.pid, signal.SIGKILL)
        leader.wait()
        mb.stop_studio()
        time.sleep(0.5)
        alive = True
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            alive = False
        assert not alive, "Studio group member survived stop_studio after its leader crashed"
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_defect_stop_kills_unrelated_group_leader_whose_argv_mentions_mastra(tmp_path, monkeypatch):
    """F-5 (LOW): the reused-pid guard is a substring match on 'mastra'. Any shell job whose
    argv contains a path like integrations/mastra-studio/... passes it and is killed."""
    monkeypatch.setattr(mb, "mastra_db_path", lambda: tmp_path / "mastra" / "mastra.db")
    (tmp_path / "mastra").mkdir()
    unrelated = subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
    try:
        mb._pid_file().write_text(f"{unrelated.pid} {unrelated.pid}")
        res = mb.stop_studio(command_of=lambda pid: "vim /repo/integrations/mastra-studio/notes.md")
        assert res.get("stale") is True and unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_defect_readonly_data_dir_gives_plain_error_not_traceback(tmp_path, monkeypatch):
    """F-4: a read-only lab/data makes start_studio raise PermissionError (CLI prints a raw
    traceback; portal returns a bare 500) instead of a documented plain error."""
    ro = tmp_path / "data"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setattr(mb, "mastra_db_path", lambda: ro / "mastra" / "mastra.db")
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "not_running", "detail": ""})
    try:
        res = mb.start_studio()
        assert res.get("ok") is False and isinstance(res.get("error"), str)
    finally:
        ro.chmod(0o755)


def test_readonly_start_via_portal_leaks_no_traceback(client, tmp_path, monkeypatch):
    ro = tmp_path / "data"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setattr(mb, "mastra_db_path", lambda: ro / "mastra" / "mastra.db")
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "not_running", "detail": ""})
    try:
        r = client.post("/api/mastra/start", json={})
        assert "Traceback" not in r.text and "PermissionError" not in r.text
    finally:
        ro.chmod(0o755)


def test_open_without_chrome_exits_2_and_launches_nothing(monkeypatch, capsys):
    from arail import mastra_cli
    launched = []
    monkeypatch.setattr(mb, "find_browser", lambda: None)
    monkeypatch.setattr(mastra_cli.subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert mastra_cli.main(["open"]) == 2 and not launched
    err = capsys.readouterr().err
    assert "Traceback" not in err and "Chrome" in err


def test_open_never_falls_back_to_default_browser(monkeypatch):
    from arail import mastra_cli
    launched = []
    monkeypatch.setattr(mb, "find_browser", lambda: "/qa/chrome")
    monkeypatch.setattr(mb, "browser_profile_dir", lambda: Path("/private/tmp/claude-501/qa-mastra-prof"))
    monkeypatch.setattr(mastra_cli.subprocess, "Popen", lambda argv, **k: launched.append((argv, k)))
    assert mastra_cli.main(["open"]) == 0
    argv, kw = launched[0]
    assert argv[0] == "/qa/chrome" and kw.get("shell") is False
    assert "/usr/bin/open" not in argv and not any(a.startswith("--remote-debugging") for a in argv)
    assert any(a.startswith("--user-data-dir=") for a in argv)


@pytest.mark.parametrize("path", ["/usr/bin:/bin", "FAKE20"])
def test_clean_machine_node_states_are_plain_cli_messages(tmp_path, path):
    if path == "FAKE20":
        fb = tmp_path / "bin"
        fb.mkdir()
        (fb / "node").write_text("#!/bin/sh\necho v20.11.0\n")
        (fb / "node").chmod(0o755)
        path = f"{fb}:/usr/bin:/bin"
    env = _sandbox_env(tmp_path, PATH=path)
    for args in (["status"], ["start"], ["install"], ["run-once", "g"]):
        r = _cli(args, env, timeout=60)
        assert "Traceback" not in r.stdout + r.stderr, (args, r.stderr)
    st = json.loads(_cli(["status"], env).stdout)["state"]
    assert st in ("node_missing", "node_too_old")
    assert _cli(["install"], env).returncode == 2


def test_ollama_down_is_model_unavailable_and_spawns_nothing(tmp_path):
    _need_node_mastra()
    r = _cli(["run-once", "g"], _sandbox_env(tmp_path, ARAIL_OLLAMA_URL="http://127.0.0.1:9"), timeout=60)
    assert json.loads(r.stdout) == {"error": "model_unavailable"} and r.returncode == 1


# ── run lock (finding 1 of REVIEW.md) ────────────────────────────────

def test_lock_released_when_holder_is_kill_9d(tmp_path, monkeypatch):
    lp = tmp_path / "mastra" / "run-once.lock"
    monkeypatch.setattr(rt, "lock_path", lambda: lp)
    holder = subprocess.Popen([PY, "-c", (
        "import fcntl,os,sys,time,pathlib; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True);"
        "fd=os.open(p,os.O_RDWR|os.O_CREAT,0o600); fcntl.flock(fd,fcntl.LOCK_EX); print('held',flush=True); time.sleep(300)"),
        str(lp)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with rt.exclusive_run_lock() as got:
            assert got is False                         # live holder in another process
        holder.send_signal(signal.SIGKILL)
        holder.wait()
        with rt.exclusive_run_lock() as got:
            assert got is True                          # kernel released it; nothing stale
        assert lp.exists()                              # never unlinked
    finally:
        if holder.poll() is None:
            holder.kill()


def test_run_once_tool_refuses_while_other_process_holds_lock(tmp_path, monkeypatch):
    lp = tmp_path / "mastra" / "run-once.lock"
    lp.parent.mkdir(parents=True)
    monkeypatch.setattr(rt, "lock_path", lambda: lp)
    monkeypatch.setenv("LAB_TIER", "maximus")
    called = []

    async def fake(goal):
        called.append(goal)
        return {}
    monkeypatch.setattr(rt, "_run_locked", fake)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert asyncio.run(rt.run_once("g")) == {"error": rt.RESEARCHER_BUSY}
        assert not called
    finally:
        os.close(fd)


def test_concurrent_real_probes_exactly_one_runs(tmp_path):
    """Three simultaneous ./arailctl-equivalent run-once calls against one sandbox lab."""
    _need_ollama()
    env = _sandbox_env(tmp_path)
    procs = [subprocess.Popen([PY, "-m", "arail.mastra_cli", "run-once", f"Summarise photosynthesis {i}"],
                              cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for i in range(3)]
    outs = [json.loads(p.communicate(timeout=900)[0]) for p in procs]
    ok = [o for o in outs if "error" not in o]
    # exactly one run gets the lock; llama3.2:1b sometimes gives an unusable score -> bad_score
    ran = [o for o in outs if not str(o.get("error", "")).startswith("run_failed")]
    assert len(ran) == 1 and len(ok) <= 1, outs
    exps = list((tmp_path / "lab" / "data" / "experiments").glob("*.json"))
    probes = sum(1 for f in exps for o in json.loads(f.read_text()).get("observations", [])
                 if (o.get("data") or {}).get("source") == "mastra")
    assert probes == len(ok)
    time.sleep(0.5)
    assert subprocess.run(["pgrep", "-f", "arail.mcp.researcher_tool"], capture_output=True).returncode != 0


def test_defect_busy_code_is_lost_and_surfaces_as_run_failed(tmp_path):
    """F-7: when the run lock is held, the MCP tool returns researcher_busy, but run-once.mjs
    collapses it into 'run_failed:Error' so the CLI/page cannot tell the user why."""
    _need_ollama()
    lp = tmp_path / "lab" / "data" / "mastra" / "run-once.lock"
    lp.parent.mkdir(parents=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        out = json.loads(_cli(["run-once", "qa busy probe"], _sandbox_env(tmp_path)).stdout)
    finally:
        os.close(fd)
    assert out == {"error": "researcher_busy"}, out


def test_owner_goal_never_archived_by_probe(tmp_path):
    _need_ollama()
    env = _sandbox_env(tmp_path)
    setup = ("from arail.agents.researcher import researcher; from arail.skills.goal_parser import GoalParser;"
             "researcher.goal_store.set_goal(GoalParser().parse_offline('OWNER GOAL qa'));"
             "print(researcher.goal_store.get_current()['id'])")
    gid = subprocess.run([PY, "-c", setup], cwd=str(REPO), env=env, capture_output=True, text=True).stdout.strip()
    out = json.loads(_cli(["run-once", "qa probe"], env).stdout)
    assert "error" in out
    after = subprocess.run([PY, "-c", "from arail.agents.researcher import researcher;"
                            "c=researcher.goal_store.get_current(); print(c['id'], c['status'])"],
                           cwd=str(REPO), env=env, capture_output=True, text=True).stdout.split()
    assert after == [gid, "active"]


def test_real_studio_start_stop_leaves_nothing(tmp_path):
    """T-I-5 for real: start Studio from a sandbox lab, check loopback-only LISTEN, stop, no leftovers."""
    _need_node_mastra()
    if mb.port_open(4111):
        pytest.skip("4111 busy")
    env = _sandbox_env(tmp_path)
    r = _cli(["start"], env, timeout=120)
    pidf = tmp_path / "lab" / "data" / "mastra" / "studio.pid"
    try:
        assert json.loads(r.stdout) == {"ok": True}, r.stdout + r.stderr
        pgid = int(pidf.read_text().split()[1])
        addrs = mb.listener_addresses(4111)
        assert addrs and mb.is_loopback_listener(addrs, 4111), addrs
        assert json.loads(_cli(["stop"], env).stdout)["was_running"] is True
        time.sleep(1)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
        assert not mb.port_open(4111) and not pidf.exists()
    finally:
        if pidf.exists():
            try:
                os.killpg(int(pidf.read_text().split()[1]), signal.SIGKILL)
            except (ProcessLookupError, ValueError, IndexError):
                pass


# ═══ HONESTY / REGRESSION ═════════════════════════════════════════════

def test_user_facing_text_makes_no_unmeasured_claims(client, monkeypatch):
    monkeypatch.setattr(mb, "resolve_page_state", lambda: {"state": "running", "detail": "d"})
    import re as _re
    raw = client.get("/mastra").text
    html = _re.sub(r"<(style|script)[^>]*>.*?</\1>", "", raw[raw.index('id="mastra-page"'):], flags=_re.S)
    docs = (REPO / "docs" / "cli.md").read_text()
    docs = docs[docs.index("### `mastra <op>`"):]
    docs = docs[:docs.find("\n### ", 5) if docs.find("\n### ", 5) > 0 else None]
    for text in (html, docs):
        low = text.lower()
        for claim in ("guarantee", "fully airgapped", "zero egress", "proven", "100%", "never leaves"):
            assert claim not in low, claim
    assert "not a 3DEval measurement" in html
    assert "has not yet confirmed" in html               # pcap pending is stated, not hidden


def test_probe_observation_label_is_honest():
    assert "not a 3DEval measurement" in mb.OBSERVATION_TEXT
    probe = (mb.MASTRA_DIR / "src/mastra/run-probe.mjs").read_text()
    assert "not a 3DEval measurement" in probe


def test_setup_path_untouched_by_branch():
    r = subprocess.run(["git", "diff", "--name-only", "20f334b4", "HEAD", "--", "scripts/setup.sh",
                        "pyproject.toml", "tests/setup_ladder"], cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("base commit not available")
    assert r.stdout.strip() == ""
