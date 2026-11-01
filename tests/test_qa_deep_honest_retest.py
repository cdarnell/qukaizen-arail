"""QA re-test after fix pass 3, sprint 2026-10-29-arail-deep-honest (TEST_REPORT.md, "Re-test after fix pass 3").

Attacks on the fixes of QA-1 to QA-13 as a hostile user would make them, not re-runs of the builder's tests.
A plain test that FAILS is an open finding (QA-14 onward). ``xfail(strict=True)`` marks behaviour that is
already filed debt (the TD row is named). A test that passes is a guard. Fake runtime, scratch folders and
subprocesses with timeouts only; no real wheel, model, lab or network.
"""
from __future__ import annotations

import errno
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arail import agent_context
from arail import deep_verdict as dv
from arail.agents import deep_policy
from arail.portal import world_routes as wr
from arail.registry.store import TIER1_ID
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_curator_honest import _R, _review, client, world  # noqa: F401  (fixtures)
from tests.test_deep_verdict import probe_result, store_path
from tests.test_qa_deep_honest_buddy import fast, gates, lab  # noqa: F401  (fixtures)

REPO = Path(__file__).resolve().parents[1]
CONTROLS = set(range(0x00, 0x20)) | {0x7F} | set(range(0x80, 0xA0))


def _raw_controls(text: str, allow: str = "\n") -> list:
    return sorted({hex(ord(c)) for c in text if ord(c) in CONTROLS and c not in allow})


# ── QA-6 and QA-7, everywhere: the clear reaches every surface, and a new failure still shows ───────

def _hold_lock_and_fail(error: str = "RuntimeError: rms_norm has 1 elements") -> None:
    """A deep failure whose write fails (another writer holds the lock past the 2 s cap): kept in memory."""
    import fcntl
    d = dv.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    held = os.open(d / "verdict.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        dv.note_call(False, error=error, consumer="agent:buddy")
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


def _tier1_health(reg) -> dict:
    return next(e for e in reg.to_state()["entries"] if e["id"] == TIER1_ID)["health"]


def test_qa_guard_after_an_unsaved_failure_and_a_pass_every_surface_agrees_and_a_new_failure_still_shows(lab):
    import arail.portal.app as app_mod
    with agent_context.agent_call("buddy", foreground=False):
        deep_policy.complete_preferring_deep("q1", foreground=False)          # deep answers
    _hold_lock_and_fail()                                                      # the next failure is not saved
    lab.reg.report_failure(TIER1_ID, RuntimeError("rms_norm"))                 # what complete_preferring_deep stores
    assert dv.current().state == "failing" and dv.current().store_error
    b = dv.identity()
    time.sleep(0.05)
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now()))       # ./arailctl deep probe: 5/5

    v = dv.current()
    assert v.state == "passing" and v.store_error is None
    text = "\n".join(dv.status_lines())
    assert "generation:   passing" in text and "NOT SAVED" not in text
    assert deep_policy.explain(foreground=False)[:2] == (True, "ok")
    block = app_mod._research_models_block()["deep"]
    assert (block["reason_code"], block["generation"]["state"]) == ("ok", "passing")
    assert _tier1_health(lab.reg)["status"] != "unhealthy"
    assert lab.reg.resolve("reasoning").entry is not None and lab.reg.resolve("reasoning").entry.id == TIER1_ID

    time.sleep(0.05)
    _hold_lock_and_fail("RuntimeError: a new failure after the pass")         # not gone where it must stay
    v = dv.current()
    assert v.state == "failing" and v.store_error, "a failure after the pass is shown, and so is that it was not saved"
    assert "NOT SAVED" in "\n".join(dv.status_lines())
    assert deep_policy.explain(foreground=False)[1] == "deep_failing"
    assert _tier1_health(lab.reg)["status"] == "unhealthy"


# ── QA-18: after a pass, the next unsaved failure reports the time and count from before the pass ───
# QA-6's fix hides a superseded in-memory failure at read time and leaves _P.mem alone. A later failure
# whose write also fails then _bumps_ that old record instead of starting a new one: the verdict says
# "since <a time before the pass>" and counts the failures the pass already cleared. The persisted path
# starts fresh (record_probe sets failure to null, the next failure gets since = now and count 1).

def test_qa18_a_new_unsaved_failure_after_a_pass_starts_a_fresh_record(fake_deep):
    fake_deep("pass")
    b = dv.identity()
    _hold_lock_and_fail("RuntimeError: before the pass")
    _hold_lock_and_fail("RuntimeError: before the pass, again")
    time.sleep(1.1)                                         # whole seconds: the stamps are ISO, to the second
    pass_ts = dv._now()
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=pass_ts))
    assert dv.current().state == "passing"
    time.sleep(1.1)
    _hold_lock_and_fail("RuntimeError: a new failure after the pass")
    v = dv.current()
    assert v.state == "failing"
    assert v.since >= dv._iso(pass_ts), f"says failing since {v.since}, before the pass at {dv._iso(pass_ts)}"
    assert v.failure["count"] == 1, f"counts {v.failure['count']} failures since the last pass; there was 1"


# ── QA-14: a FIFO named verdict.json hangs every reader and writer (and so the portal) ──────────────
# _read_store checks only for a symbolic link, then opens verdict.json with O_RDONLY and no O_NONBLOCK. A
# FIFO there blocks that open until some process opens it for writing, which never happens: current() hangs
# holding the module lock, so explain(), note_call(), deep status and the probe's record all hang behind it.
# /api/models/state is an async handler that calls registry.to_state() -> the overlay -> current() in the
# event loop, so the first status-bar poll freezes the whole portal. Planting it takes write access to
# lab/data/deep; TD42 already lets a folder another user owns be read, and this read does not return.

HANG = (
    "import os, sys, time, types\n"
    "root = sys.argv[1]; op = sys.argv[2]\n"
    "so = os.path.join(root, 'pkg', '_native.abi3.so'); os.makedirs(os.path.dirname(so), exist_ok=True)\n"
    "open(so, 'wb').write(b'NATIVE' * 100)\n"
    "top = types.ModuleType('aerollm_api'); top.__file__ = os.path.join(root, 'pkg', '__init__.py')\n"
    "nat = types.ModuleType('aerollm_api._native'); nat.__file__ = so\n"
    "sys.modules['aerollm_api'] = top; sys.modules['aerollm_api._native'] = nat\n"
    "from arail import deep_verdict as dv\n"
    "dv.identity()\n"
    "d = os.environ['ARAIL_DEEP_STATE_DIR']; os.makedirs(d, mode=0o700, exist_ok=True)\n"
    "os.mkfifo(os.path.join(d, 'verdict.json'))\n"
    "t = time.monotonic()\n"
    "if op == 'write':\n"
    "    dv.note_call(False, error='boom')\n"
    "v = dv.current()\n"
    "print(round(time.monotonic() - t, 2), v.state, v.source)\n"
)


@pytest.mark.parametrize("op", ["read", "write"])
def test_qa14_a_fifo_named_verdict_json_does_not_hang_readers_or_writers(tmp_path, op):
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1",
           "ARAIL_DEEP_STATE_DIR": str(tmp_path / "deep")}
    try:
        r = subprocess.run([sys.executable, "-c", HANG, str(tmp_path), op], env=env, capture_output=True, text=True,
                           timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{op}: deep_verdict did not return in 15 s with a FIFO at verdict.json (it blocks in open())")
    secs, state, source = r.stdout.split()
    assert float(secs) < 5 and state == "failing" and source == "store_unreadable", r.stdout + r.stderr


# ── QA-15: when flock itself fails, the probe says another probe is running ─────────────────────────
# _take_probe_lock returns "held by another probe" for EVERY flock error, not only EAGAIN/EWOULDBLOCK, so on
# a filesystem without lock support (ENOTSUP or ENOLCK on some network mounts; a FIFO named probe.lock gives
# EOPNOTSUPP) the probe prints "another deep probe is running — wait for it" and exits 5 every time. Nobody is
# running; the operator waits for nothing. The verdict writer reports the same error truthfully ("flock: ...").

@pytest.mark.parametrize("code", [errno.ENOTSUP, errno.ENOLCK], ids=["ENOTSUP", "ENOLCK"])
def test_qa15_a_lock_the_filesystem_cannot_take_is_not_reported_as_another_probe(fake_deep, monkeypatch, code):
    fake_deep("pass")
    import fcntl
    from arail import deep_probe as dp

    def no_locks(fd, op):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(fcntl, "flock", no_locks)
    pr = dp.execute({"json": False}, out=io.StringIO())
    line = next(ln for ln in pr.lines if ln.startswith("not run"))
    assert pr.exit_code == 5
    assert "another deep probe is running" not in line, f"false: no probe is running; flock failed: {line}"
    assert os.strerror(code) in line or "lock" in line.split("—")[0]


# ── QA-16: QA-5 stops at the CLI; the Curator's report and explain()'s detail carry controls to the log ──
# QA-5 cleans what `deep status`, `channel` and the probe print. Two sprint texts reach the activity log
# without that cleaning, and `./arailctl logs` (and `logs --since`, which Buddy reads) prints activity
# messages raw: (a) the Curator review, grow and forge reports quote _CountedRouter.first_error, the runtime
# error as raised, through world_routes' own _clip, which keeps controls; (b) explain()'s detail (the
# truth strip, agent traces, the Curator's "deep failing: ..." note, the researcher's run line) embeds the
# stored lab, consumer and since fields with no cleaning.

ESC = "\x1b[2K\x1b[1Ainjected\x1b[0m\x07"


def test_qa16_a_curator_report_never_carries_raw_control_characters_into_the_activity_log(world, monkeypatch, client):
    world.mount(6)

    class EscJudge:
        backend_name = "aerollm"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *a, **k):
            self.calls += 1
            if self.calls >= 2:
                raise RuntimeError("backend failed: " + ESC)
            return _R('{"correct": true, "category_ok": true, "better_category": "", "bad_edges": []}', "aerollm")

    monkeypatch.setattr(wr, "_review_router", lambda: EscJudge())
    st = _review(client)
    assert st["outcome"] == "partial"
    # The Curator writes through world_routes' own binding of activity_log. Some suite tests reload
    # arail.activity (tests/test_boot_security_scan.py), after which that binding and the module's current
    # object are two objects; read both.
    logs = {id(o): o for o in (wr.activity_log, sys.modules["arail.activity"].activity_log)}.values()
    lines = [e["message"] for log in logs for e in log.recent(200) if e["source"] == "curator"
             and "partial" in e["message"]]
    assert lines, "the partial-review line was written"
    assert not _raw_controls(lines[-1]), f"`./arailctl logs` would print {_raw_controls(lines[-1])} raw: {lines[-1]!r}"
    assert not _raw_controls(st["message"])


def test_qa16_explains_detail_never_carries_raw_control_characters_from_the_store(fake_deep, gates):
    fake_deep("pass")
    b = dv.identity()
    dv.state_dir().mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps({
        "schema": dv.SCHEMA, "updated_at": "x",
        "binaries": {b.sha256: {"failure": {"since": "2026-10-31T\x1b[2K:00Z", "since_ts": 1.0, "last_at": "x",
                                            "last_ts": 1.0, "source": "call", "consumer": "agent:" + ESC,
                                            "lab": "ai" + ESC, "error": "e", "last_error": "e", "count": 1},
                                "last_probe": None}}}))
    ok, code, detail = deep_policy.explain(foreground=False)
    assert code == "deep_failing"
    assert not _raw_controls(detail), f"explain() detail carries {_raw_controls(detail)}: {detail!r}"


def test_qa_guard_deep_status_and_channel_clean_every_stored_field(fake_deep):
    """The QA-5 fix itself, attacked through fields it lists: every row of `deep status` is printable even when
    each stored string field carries ESC, BEL, DEL and a C1 control (a hand-edited store)."""
    fd = fake_deep("pass")
    b = dv.identity()
    nasty = ESC + "\x7f\u009b31m"
    dv.state_dir().mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps({
        "schema": dv.SCHEMA, "updated_at": nasty,
        "binaries": {b.sha256: {"module": nasty, "native_path": nasty, "version": nasty, "bytes": 1,
                                "failure": {"since": nasty, "since_ts": 1.0, "last_at": nasty, "last_ts": 1.0,
                                            "source": "probe", "consumer": nasty, "lab": nasty, "error": nasty,
                                            "last_error": nasty, "count": 1},
                                "last_probe": {"started_at": nasty, "started_ts": 1.0, "at": nasty, "outcome": "fail",
                                               "passed": 1, "total": 5, "median_ok_s": 1.0,
                                               "first_error": {"call": 2, "kind": nasty, "error": nasty},
                                               "model": nasty, "ring_depth_source": nasty, "python": nasty,
                                               "lab": nasty}, "last_pass_at": None}}}))
    r = subprocess.run([sys.executable, "-m", "arail.deep_verdict", "status"], env=fd.env(), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0 and "FAILING" in r.stdout, r.stdout + r.stderr
    assert not _raw_controls(r.stdout), _raw_controls(r.stdout)


# ── QA-17: `deep probe` still exits 1 when the interpreter cannot run it ───────────────────────────
# QA-13's fix covers "no .venv and no PYTHON". An explicit PYTHON wins in arailctl, and with a PYTHON that
# cannot import arail (a stray `export PYTHON=python3`, before or after setup) or that does not exist,
# Python or bash exits 1: the documented code for "the probe ran and deep generation is failing", on a
# machine where nothing ran. docs/cli.md gives "not run" the code 5.

def _fresh_checkout(tmp_path: Path) -> Path:
    co = tmp_path / "fresh-checkout"
    (co / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "arailctl", co / "arailctl")
    shutil.copytree(REPO / "scripts" / "lib", co / "scripts" / "lib")
    return co


@pytest.mark.parametrize("python", ["/usr/bin/python3", "/nonexistent/python"],
                         ids=["a python without arail", "a python that does not exist"])
def test_qa17_deep_probe_with_a_python_that_cannot_run_it_exits_5_not_run(tmp_path, python):
    if python == "/usr/bin/python3" and not Path(python).exists():
        pytest.skip("no /usr/bin/python3 on this host")
    co = _fresh_checkout(tmp_path)
    e = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHON": python}
    r = subprocess.run(["bash", str(co / "arailctl"), "deep", "probe"], env=e, cwd=co, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 5, (f"exit {r.returncode}: 1 means 'the probe ran and deep is failing' (docs/cli.md); "
                               f"nothing ran: {r.stderr.strip()[:160]}")


# ── QA-19: the writer decides by a path-based read and writes through the folder's descriptor ──────
# QA-4 moved every write onto a descriptor of the state folder, but _locked_update still reads verdict.json
# by path (_read_store) to decide what to write. If the folder is swapped for a link at that moment, the
# read sees another folder ("missing", or an error), and the writer then replaces the real store with a
# fresh document (the failure count starts again) or moves the valid store aside as "corrupt". Measured with
# an atomic swap loop and 8 writers for 4 s: 34 valid stores moved aside and the count left at 10 of about
# 9,000 recorded failures. Nothing is ever written into the link's target (the QA-4 promise holds); what is
# lost is the record itself. The adversary needs write access to lab/data.

@pytest.mark.xfail(strict=True, reason="TD43 (QA-19): _locked_update decides by a path-based read of verdict.json and "
                                       "writes through the folder's descriptor, so a folder swapped at that moment costs "
                                       "the stored record. Fix with TD42: read through the same dir_fd.")
@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS renamex_np(RENAME_SWAP) to swap atomically")
def test_qa19_a_folder_swapped_at_the_writers_read_does_not_cost_the_stored_record(fake_deep, tmp_path, monkeypatch):
    import ctypes
    fake_deep("pass")
    b = dv.identity()
    for _ in range(5):
        dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    d = dv.state_dir()
    assert json.loads(store_path().read_text())["binaries"][b.sha256]["failure"]["count"] == 5
    victim = tmp_path / "victim"
    victim.mkdir()
    link = tmp_path / "planted-link"
    link.symlink_to(victim, target_is_directory=True)
    libc = ctypes.CDLL(None, use_errno=True)
    real_read = dv._read_store

    def swapped_read(*, use_cache=True):
        if use_cache:                              # an ordinary reader: leave it alone
            return real_read(use_cache=use_cache)
        assert libc.renamex_np(str(d).encode(), str(link).encode(), 2) == 0     # the folder becomes the link
        try:
            return real_read(use_cache=use_cache)
        finally:
            libc.renamex_np(str(d).encode(), str(link).encode(), 2)              # and back

    monkeypatch.setattr(dv, "_read_store", swapped_read)
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    monkeypatch.setattr(dv, "_read_store", real_read)
    assert list(victim.iterdir()) == [], "the QA-4 promise: nothing is written through the link"
    rec = json.loads(store_path().read_text())["binaries"][b.sha256]["failure"]
    assert rec["count"] == 6 and not list(d.glob("verdict.json.corrupt-*")), (
        f"the writer replaced the stored record it could not see: count {rec['count']} (was 5, plus 1), "
        f"{len(list(d.glob('verdict.json.corrupt-*')))} copies moved aside")


# ── QA-11 and QA-8 under hostile TMPDIR and checkout names ─────────────────────────────────────────

from tests.test_build_aerollm_honest import GOOD, MACHO, darwin_arm64, env, make_bundle, out  # noqa: E402,F401

SAFE_TMPDIRS = {"space": "with space", "glob": "glob*?[x]", "dollar": "cost$HOME", "backslash, not an escape": "bs\\dir"}
UNSAFE_TMPDIRS = {"apostrophe": "it's", "backslash-t (a Python escape)": "tab\\tdir", "newline": "new\nline"}


def _bundle_with_tmpdir(env, name: str):
    env.fake_importer()
    tmpdir = env.tmp / name
    tmpdir.mkdir()
    tarball, tar_sha = make_bundle(env.tmp, MACHO + b"BUNDLE\n" * 40)
    r = env.run("bundle", TMPDIR=tmpdir, AEROLLM_BUNDLE_FILE=tarball, AEROLLM_BUNDLE_SHA256=tar_sha,
                AEROLLM_BUNDLE_TAG="v1.1.0")
    return r, tmpdir


@darwin_arm64
@pytest.mark.parametrize("name", list(SAFE_TMPDIRS.values()), ids=list(SAFE_TMPDIRS))
def test_qa_guard_a_bundle_installs_under_a_tmpdir_with_spaces_globs_and_plain_backslashes(env, name):
    r, tmpdir = _bundle_with_tmpdir(env, name)
    assert r.returncode == 0, out(r)
    assert (env.site / "aerollm_api.abi3.so").read_bytes().startswith(MACHO)
    assert list(tmpdir.iterdir()) == [], "no temp left behind"


@darwin_arm64
@pytest.mark.xfail(strict=True, reason="TD36 (paths inside python -c): the MANIFEST read in bundle_install still puts "
                                       "$extract_dir in Python source, so this TMPDIR makes it read nothing and a correct "
                                       "bundle is refused with 'does not match MANIFEST.json's recorded sha256'")
@pytest.mark.parametrize("name", list(UNSAFE_TMPDIRS.values()), ids=list(UNSAFE_TMPDIRS))
def test_qa_td36_a_bundle_installs_under_a_tmpdir_with_an_apostrophe_an_escape_or_a_newline(env, name):
    r, _tmpdir = _bundle_with_tmpdir(env, name)
    assert r.returncode == 0, out(r)


def test_qa_guard_a_checkout_path_with_glob_characters_builds_and_verifies(env):
    env.repo = env.tmp / "q*?[x] glob"
    env.checkout("aerollm-api")
    env.fake_importer()
    r = env.run("build")
    assert r.returncode == 0, out(r)
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD


# ── QA-12 under a hostile child: bad bytes, a huge line, a line that never ends ─────────────────────

NOISY = (
    "\n_qa_orig = Runtime.generate\n"
    "def _qa_generate(self, prompt, **kw):\n"
    "    how = os.environ.get('QA_STDERR', '')\n"
    "    if self.calls == 0 and how == 'bytes':\n"
    "        os.write(2, b'head \\x00 nul \\xff\\xfe bad utf-8 \\x1b[2K esc \\x9b c1')     # no newline, ever\n"
    "    if self.calls == 0 and how.startswith('mb:'):\n"
    "        for _ in range(int(how[3:])):\n"
    "            os.write(2, b'x' * (1 << 20))\n"
    "        os.write(2, b'\\n')\n"
    "    if self.calls == 0 and how == 'endless':\n"
    "        for _ in range(48):\n"
    "            os.write(2, b'y' * (1 << 20))\n"
    "        while True:\n"
    "            time.sleep(1)\n"
    "    return _qa_orig(self, prompt, **kw)\n"
    "Runtime.generate = _qa_generate\n"
)
MEASURE = (
    "import io, json, resource, sys, time\n"
    "from arail import deep_probe as dp\n"
    "scale = 1 if sys.platform == 'darwin' else 1024\n"
    "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
    "t = time.monotonic(); buf = io.StringIO(); rc = dp.run(['--json'], out=buf)\n"
    "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
    "doc = json.loads(buf.getvalue())\n"
    "print(json.dumps({'rc': rc, 'secs': time.monotonic() - t, 'growth_mb': (after - before) / 2**20,\n"
    "                  'passed': doc.get('passed'), 'tail': doc.get('stderr_tail')}))\n"
)


def _noisy_probe(fd, how: str, timeout: int = 180, **env):
    with open(fd.package / "__init__.py", "a", encoding="utf-8") as fh:
        fh.write(NOISY)
    r = subprocess.run([sys.executable, "-c", MEASURE], env=fd.env(QA_STDERR=how, **env), capture_output=True,
                       text=True, timeout=timeout)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_qa_guard_nul_bad_utf8_and_escapes_on_a_last_line_without_newline_reach_the_record_printable(fake_deep):
    got = _noisy_probe(fake_deep("pass"), "bytes")
    assert got["rc"] == 0 and got["passed"] == 5
    last = [t for t in got["tail"] if t.startswith("head")]
    assert last and not _raw_controls(last[-1], allow=""), last


def test_qa_guard_a_256_mb_stderr_line_does_not_grow_the_supervisor(fake_deep):
    got = _noisy_probe(fake_deep("pass"), "mb:256", timeout=300)
    assert got["rc"] == 0 and got["growth_mb"] < 48, got["growth_mb"]
    assert max(len(t) for t in got["tail"]) <= 300


def test_qa_guard_a_child_that_never_ends_its_line_or_exits_is_stopped_at_the_deadline(fake_deep):
    got = _noisy_probe(fake_deep("pass"), "endless", ARAIL_DEEP_PROBE_CALL_TIMEOUT_S="3")
    assert got["rc"] == 1 and got["passed"] == 0
    assert got["secs"] < 30 and got["growth_mb"] < 48, got
    assert any(t.startswith("yyy") and len(t) <= 300 for t in got["tail"]), "the head of the endless line is kept"


# ── guards on the QA-4 store against racing processes ──────────────────────────────────────────────

RACER = (
    "import os, sys, time, types\n"
    "root, state, n, go = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])\n"
    "so = os.path.join(root, 'pkg', '_native.abi3.so')\n"
    "top = types.ModuleType('aerollm_api'); top.__file__ = os.path.join(root, 'pkg', '__init__.py')\n"
    "nat = types.ModuleType('aerollm_api._native'); nat.__file__ = so\n"
    "sys.modules['aerollm_api'] = top; sys.modules['aerollm_api._native'] = nat\n"
    "os.environ['ARAIL_DEEP_STATE_DIR'] = state\n"
    "from arail import deep_verdict as dv\n"
    "dv.identity()\n"
    "while time.time() < go: pass\n"
    "lost = 0\n"
    "for _ in range(n):\n"
    "    dv.note_call(False, error='boom', consumer='agent:racer')\n"
    "    if dv.current().store_error: lost += 1\n"
    "print(lost)\n"
)


LOCKER = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "state, go = sys.argv[1], float(sys.argv[2])\n"
    "os.environ['ARAIL_DEEP_STATE_DIR'] = state\n"
    "from arail import deep_probe as dp\n"
    "while time.time() < go: pass\n"
    "try:\n"
    "    fd, holder = dp._take_probe_lock(Path(state))\n"
    "    print('taken' if fd is not None else 'held')\n"
    "    if fd is not None: time.sleep(0.05); os.close(fd)\n"
    "except OSError as e:\n"
    "    print('oserror:' + str(e))\n"
)


@pytest.mark.parametrize("procs, rounds", [(8, 12), (16, 6)], ids=["8 writers and 4 probes x 12 fresh folders",
                                                                    "16 writers and 4 probes x 6 fresh folders"])
def test_qa_guard_a_cold_start_race_on_fresh_folders_loses_no_write_and_no_probe_lock(tmp_path, procs, rounds):
    so = tmp_path / "pkg" / "_native.abi3.so"
    so.parent.mkdir(parents=True)
    so.write_bytes(b"NATIVE" * 100)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    lost_total, short, lock_errors = 0, [], []
    for rnd in range(rounds):
        state = tmp_path / f"r{rnd}" / "deep"                     # does not exist yet: every process races to make it
        go = time.time() + 1.5
        ps = [subprocess.Popen([sys.executable, "-c", RACER, str(tmp_path), str(state), "3", str(go)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(procs)]
        ls = [subprocess.Popen([sys.executable, "-c", LOCKER, str(state), str(go)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        outs = [p.communicate(timeout=120) for p in ps]
        louts = [p.communicate(timeout=120) for p in ls]
        lost_total += sum(int(o.strip() or 0) for o, _e in outs)
        lock_errors += [o.strip() for o, _e in louts if o.strip() not in ("taken", "held")]
        doc = json.loads((state / "verdict.json").read_text())
        count = next(iter(doc["binaries"].values()))["failure"]["count"]
        if count != procs * 3:
            short.append((rnd, count))
    assert lost_total == 0 and not short, f"lost writes: {lost_total}; rounds short of {procs * 3}: {short}"
    assert lock_errors == [], f"a probe could not take its lock on a fresh folder: {lock_errors[:3]}"
