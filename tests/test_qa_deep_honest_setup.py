"""QA, sprint 2026-10-29-arail-deep-honest: setup on a clean machine.

scripts/build-aerollm.sh in every mode against the hostile inputs a stranger's machine provides, and
`deep probe` / `deep status` with no wheel, a broken wheel, no HOME, a read-only lab, a replaced binary
and the macOS system Python. Written by the QA pass (sprints/2026-10-29-arail-deep-honest/TEST_REPORT.md).

A plain test that FAILS is an open QA finding (QA-n). ``xfail(strict=True)`` marks filed debt (TD-n)
or behaviour that predates the sprint: it does not turn the suite red today and fails the day the debt
is paid, so the marker goes with the fix. A test that passes is a guard QA added.

The build-script tests reuse the harness of tests/test_build_aerollm_honest.py (a throwaway venv with
no pip, a fake cargo on PATH, a fake importer); nothing touches a real venv, a real QueueLLM checkout
or the network.
"""
from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_build_aerollm_honest import (  # noqa: F401  (the harness and its fixture)
    BUILD, FINDER, GOOD, MACHO, darwin_arm64, env, make_bundle, out, sha)

REPO = Path(__file__).resolve().parents[1]


def _previous_bundle(env, tag: str = "v0.9.0") -> tuple[bytes, bytes]:
    """A working bundled install already in the venv (a different tag, so the script reads the new file)."""
    env.fake_importer()
    old_so = MACHO + b"PREVIOUS-WORKING-BUNDLE\n" * 40
    old_marker = json.dumps({"arail_release": tag, "sha256": sha(old_so), "aerollm_version": "0.9.0"}).encode()
    (env.site / "aerollm_api.abi3.so").write_bytes(old_so)
    (env.site / "aerollm_api.bundle.json").write_bytes(old_marker)
    return old_so, old_marker


def _intact(env, old_so: bytes, old_marker: bytes) -> None:
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == old_so, "the working install was damaged"
    assert (env.site / "aerollm_api.bundle.json").read_bytes() == old_marker, "its install record was damaged"


# ── QA-8: after a source build, `deep install` says there is "no install record" ────────────────────
# The sprint made a source build write aerollm_api.build.json (the install record C7 asks for). The
# bundle path's F7 guard still looks only for aerollm_api.bundle.json, so over a verified source build
# it prints "an aerollm_api.abi3.so with no install record is already at ...". Refusing is right
# (never overwrite a maintainer's build silently); the sentence is false, and VISION counts "a false
# line from scripts/build-aerollm.sh, in any mode" as a silent outcome.

@darwin_arm64
def test_qa8_install_over_a_source_build_names_the_build_record_instead_of_saying_there_is_none(env):
    env.checkout("aerollm-api")
    env.fake_importer()
    assert env.run("build").returncode == 0
    assert (env.site / "aerollm_api.build.json").exists()
    tarball, tar_sha = make_bundle(env.tmp, MACHO + b"BUNDLE\n" * 40)
    r = env.run("bundle", AEROLLM_BUNDLE_FILE=tarball, AEROLLM_BUNDLE_SHA256=tar_sha)
    text = out(r)
    assert r.returncode == 1 and (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD, text
    assert "no install record" not in text, (
        "false: this script wrote aerollm_api.build.json for that file; the refusal says: "
        + text.strip().splitlines()[0])


# ── the bundle channel against what a stranger's machine hands it ───────────────────────────────────

def test_qa_guard_a_non_apple_host_is_refused_before_any_file_is_read(env):
    fake_uname = env.bin / "uname"
    fake_uname.write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; *) echo Linux;; esac\n')
    fake_uname.chmod(0o755)
    before = env.listing()
    absent = env.tmp / "never-read.tar.gz"
    for mode in ("bundle", "auto"):                    # auto, with no checkout and no index credentials
        r = env.run(mode, AEROLLM_BUNDLE_FILE=absent, AEROLLM_BUNDLE_SHA256="0" * 64)
        assert r.returncode == 1, out(r)
        assert "The bundled QueueLLM channel is macOS-arm64-only." in out(r)
        assert "does not exist" not in out(r) and "Checksum" not in out(r)
        assert env.listing() == before


@darwin_arm64
@pytest.mark.parametrize("kind", ["a directory", "an empty file", "an unreadable file"])
def test_qa_guard_a_bad_bundle_file_is_refused_and_the_previous_install_survives(env, kind):
    if kind == "an unreadable file" and os.geteuid() == 0:
        pytest.skip("root can read a mode-000 file")
    old_so, old_marker = _previous_bundle(env)
    bad = env.tmp / "bundle-input"
    if kind == "a directory":
        bad.mkdir()
    else:
        bad.write_bytes(b"" if kind == "an empty file" else b"x" * 100)
        if kind == "an unreadable file":
            bad.chmod(0)
    try:
        r = env.run("bundle", AEROLLM_BUNDLE_FILE=bad, AEROLLM_BUNDLE_SHA256=sha(b""), AEROLLM_BUNDLE_TAG="v1.1.0")
    finally:
        if bad.is_file():
            bad.chmod(0o600)
    assert r.returncode != 0, out(r)
    _intact(env, old_so, old_marker)
    assert list(env.tmpdir.iterdir()) == [], "no temp copy is left behind"


@darwin_arm64
def test_qa_guard_a_truncated_tarball_with_a_matching_pin_is_refused_and_the_previous_install_survives(env):
    old_so, old_marker = _previous_bundle(env)
    tarball, _ = make_bundle(env.tmp, MACHO + b"NEW\n" * 4000)
    data = tarball.read_bytes()
    tarball.write_bytes(data[: len(data) // 2])        # a download cut off half way
    r = env.run("bundle", AEROLLM_BUNDLE_FILE=tarball, AEROLLM_BUNDLE_SHA256=sha(tarball.read_bytes()),
                AEROLLM_BUNDLE_TAG="v1.1.0")
    assert r.returncode == 1 and "Could not extract the bundle tarball" in out(r), out(r)
    _intact(env, old_so, old_marker)
    assert list(env.tmpdir.iterdir()) == []


def _evil_tarball(tmp: Path, kind: str, outside: Path) -> tuple[Path, str]:
    """The four expected members, plus a member that tries to write outside the extract folder."""
    so = MACHO + b"BUNDLE\n" * 40
    members = {"aerollm_api.abi3.so": so, "LICENSE": b"l\n", "NOTICE": b"n\n",
               "MANIFEST.json": json.dumps({"sha256": sha(so), "aerollm_version": "1.1.0"}).encode()}
    tarball = tmp / f"evil-{kind}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        if kind == "dot-dot":
            ti = tarfile.TarInfo("../../../escaped.txt")
            payload = b"written outside\n"
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))
        else:                                          # a symlink to a folder outside, then a file through it
            link = tarfile.TarInfo("escape")
            link.type, link.linkname = tarfile.SYMTYPE, str(outside)
            tf.addfile(link)
            ti = tarfile.TarInfo("escape/pwned.txt")
            payload = b"written through a symlink\n"
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))
    return tarball, sha(tarball.read_bytes())


@darwin_arm64
@pytest.mark.parametrize("kind", ["dot-dot", "symlink"])
def test_qa_guard_a_tarball_member_cannot_write_outside_the_extract_folder(env, kind):
    env.fake_importer()
    outside = env.tmp / "outside"
    outside.mkdir()
    tarball, tar_sha = _evil_tarball(env.tmp, kind, outside)
    r = env.run("bundle", AEROLLM_BUNDLE_FILE=tarball, AEROLLM_BUNDLE_SHA256=tar_sha, AEROLLM_BUNDLE_TAG="v1.1.0")
    assert list(outside.iterdir()) == [], f"a member was written outside the extract folder: {out(r)}"
    assert not list(env.tmp.rglob("escaped.txt")), out(r)
    if r.returncode == 0:                              # bsdtar skipped the hostile member and installed the rest
        assert (env.site / "aerollm_api.abi3.so").read_bytes().startswith(MACHO)


# ── QA-9 (TD14): Ctrl-C while an install is being verified ──────────────────────────────────────────
# bundle_install copies the new .so and its marker over the old ones in place, backs the old ones up
# to $TMPDIR under random names, and only verify_loaded puts them back. Its EXIT trap removes the
# extract folder, not the backups, and restores nothing. A Ctrl-C (or a closed terminal) during the
# verification import leaves the new, never-verified .so and marker installed, the old working pair
# only in $TMPDIR/arail-aerollm-prev*.XXXXXX, and no message saying where.

FINDER_SLOW = FINDER.replace(
    '            if b"FAILS-TO-IMPORT" in fh.read():       # a native module the loader rejects\n',
    '            data = fh.read()\n'
    '            if b"SLOW-VERIFY" in data:                  # the verification import takes a while\n'
    '                import time; time.sleep(30)\n'
    '            if b"FAILS-TO-IMPORT" in data:            # a native module the loader rejects\n')


@darwin_arm64
@pytest.mark.xfail(strict=True, reason="TD14: an install interrupted during verification is not rolled back")
def test_qa9_td14_ctrl_c_during_verification_puts_the_previous_install_back(env):
    assert FINDER_SLOW != FINDER, "the harness changed; update the slow finder"
    old_so, old_marker = _previous_bundle(env)
    (env.site / "zz_fake_aerollm_finder.py").write_text(FINDER_SLOW)
    tarball, tar_sha = make_bundle(env.tmp, MACHO + b"SLOW-VERIFY\n" * 40)
    run_env = {**os.environ, "NO_COLOR": "1", "PYTHON": str(env.py), "HOME": str(env.tmp),
               "ARAIL_AEROLLM_REPO": str(env.repo), "ARAIL_DEEP_STATE_DIR": str(env.tmp / "state"),
               "TMPDIR": str(env.tmpdir), "AEROLLM_BUNDLE_FILE": str(tarball), "AEROLLM_BUNDLE_SHA256": tar_sha,
               "AEROLLM_BUNDLE_TAG": "v1.1.0"}
    run_env.pop("PYTHONPATH", None)
    p = subprocess.Popen(["bash", str(BUILD), "bundle"], env=run_env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and b"SLOW-VERIFY" not in (env.site / "aerollm_api.abi3.so").read_bytes():
            time.sleep(0.05)
        time.sleep(1.0)                                # the copy is done; the verification import is sleeping
        os.killpg(p.pid, signal.SIGINT)                # what Ctrl-C sends to the foreground process group
        p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    _intact(env, old_so, old_marker)


# ── ARAIL_AEROLLM_REPO as a stranger sets it ────────────────────────────────────────────────────────

HOSTILE = ["with space", "it's quoted", 'say "hi"', "$(touch PWNED1)", "`touch PWNED2`"]


@pytest.mark.parametrize("name", HOSTILE, ids=["space", "single quote", "double quote", "$()", "backticks"])
def test_qa_guard_a_checkout_at_a_hostile_path_builds_and_runs_nothing_it_names(env, name):
    env.repo = env.tmp / name
    env.checkout("aerollm-api")
    env.fake_importer()
    r = env.run("build")
    assert r.returncode == 0, out(r)
    marker = json.loads((env.site / "aerollm_api.build.json").read_text())
    assert marker["queuellm_commit"] == env.head() and marker["queuellm_dirty"] is False
    assert not list(env.tmp.rglob("PWNED*")) and not list(REPO.glob("PWNED*")), "a path was run as a command"


# ── QA-11: a checkout path with a backslash or a newline can never be built, and the refusal misleads ──
# sha256_of() takes field 1 of `shasum -a 256 <file>`. For a file name with a backslash or a newline,
# shasum escapes the line and prefixes the digest with "\", so the artifact's "sha256" never equals
# the loaded file's. The script removes a correct install and says "import aerollm_api loads <the same
# file> (sha256 ...)", which reads as a different binary winning the import. Hashing stdin
# (`shasum -a 256 < "$1"`) has no file name to escape.

@pytest.mark.parametrize("name", ["back\\slash", "new\nline"], ids=["backslash", "newline"])
def test_qa11_a_checkout_path_with_a_backslash_or_newline_builds_and_verifies(env, name):
    env.repo = env.tmp / name
    env.checkout("aerollm-api")
    env.fake_importer()
    r = env.run("build")
    assert r.returncode == 0, "a correct build was refused by its own sha256 check:\n" + out(r)
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD


@pytest.mark.parametrize("kind", ["unset (the default under HOME)", "a missing path", "a file, not a folder"])
def test_qa_guard_no_usable_checkout_is_refused_with_the_true_reason(env, kind):
    extra = {}
    if kind.startswith("unset"):
        extra["ARAIL_AEROLLM_REPO"] = None
        where = env.tmp / "ProJects" / "qukaizen-queuellm"
    elif kind == "a missing path":
        where = env.tmp / "nope"
        extra["ARAIL_AEROLLM_REPO"] = where
    else:
        where = env.tmp / "a-file"
        where.write_text("not a checkout\n")
        extra["ARAIL_AEROLLM_REPO"] = where
    before = env.listing()
    for mode in ("build", "auto"):
        r = env.run(mode, **extra) if mode == "build" else env.run(mode, AEROLLM_CHANNEL="dev", **extra)
        assert r.returncode == 1, out(r)
        assert f"No QueueLLM checkout at {where}." in out(r)
        assert "For the prebuilt QueueLLM: ./arailctl deep install." in out(r) and "git clone" not in out(r)
        assert not env.cargo_ran() and env.listing() == before


def test_qa_guard_a_relative_checkout_path_with_a_leading_dash_fails_and_installs_nothing(env):
    work = env.tmp / "cwd"
    work.mkdir()
    env.repo = work / "-dash-checkout"
    env.checkout("aerollm-api")
    env.fake_importer()
    before = env.listing()
    run_env = {**os.environ, "NO_COLOR": "1", "PYTHON": str(env.py), "HOME": str(env.tmp),
               "ARAIL_AEROLLM_REPO": "-dash-checkout", "TMPDIR": str(env.tmpdir),
               "ARAIL_DEEP_STATE_DIR": str(env.tmp / "state"),
               "PATH": f"{env.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_CARGO_LOG": str(env.cargo_log),
               "FAKE_CARGO_ARTIFACT": str(env.artifact)}
    r = subprocess.run(["bash", str(BUILD), "build"], env=run_env, cwd=work, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode != 0, "a path the script cannot cd into is not a success"
    assert env.listing() == before


def test_qa_guard_a_checkout_with_both_crates_builds_the_pre_rename_one(env):
    env.checkout("aerollm-api")
    (env.repo / "crates" / "queuellm-api").mkdir(parents=True)
    (env.repo / "crates" / "queuellm-api" / "Cargo.toml").write_text('[package]\nname = "y"\n')
    env.fake_importer()
    r = env.run("build")
    assert r.returncode == 0, out(r)
    assert "-p aerollm-api" in env.cargo_log.read_text() and "queuellm-api" not in env.cargo_log.read_text()


def test_qa_guard_a_read_only_site_packages_is_refused_before_the_build(env):
    env.checkout("aerollm-api")
    env.site.chmod(0o555)
    try:
        r = env.run("build")
    finally:
        env.site.chmod(0o755)
    assert r.returncode == 1 and "Cannot install into" in out(r), out(r)
    assert not env.cargo_ran(), "refused before the minutes-long build"


@pytest.mark.parametrize("case", ["detached HEAD", "git unusable"])
def test_qa_guard_the_build_marker_on_a_detached_head_and_without_git(env, case):
    env.checkout("aerollm-api")
    env.fake_importer()
    head = env.head()
    if case == "detached HEAD":
        subprocess.run(["git", "checkout", "-q", "--detach", head], cwd=env.repo, check=True, capture_output=True)
    else:
        fake_git = env.bin / "git"
        fake_git.write_text("#!/bin/sh\necho 'git: unusable on this machine' >&2\nexit 127\n")
        fake_git.chmod(0o755)
    r = env.run("build")
    assert r.returncode == 0, out(r)
    marker = json.loads((env.site / "aerollm_api.build.json").read_text())
    if case == "detached HEAD":
        assert (marker["queuellm_commit"], marker["queuellm_dirty"]) == (head, False)
    else:
        assert (marker["queuellm_commit"], marker["queuellm_dirty"]) == ("unknown", None), \
            "no git is no evidence of a commit or a clean tree"


@pytest.mark.xfail(strict=True, reason="pre-existing (not this sprint's change): with HOME unset and no "
                                       "ARAIL_AEROLLM_REPO, `set -u` stops every mode at $HOME, status included")
def test_qa_preexisting_deep_status_runs_without_HOME(env):
    r = env.run("status", HOME=None, ARAIL_AEROLLM_REPO=None)
    assert r.returncode == 0 and "QueueLLM (2nd inference) status" in out(r), out(r)


# ── deep probe and deep status on a machine that is not the maintainer's ────────────────────────────

def _probe(fd, *args, env_extra=None, drop=(), timeout=120):
    e = fd.env(**(env_extra or {}))
    for k in drop:
        e.pop(k, None)
    return subprocess.run([sys.executable, "-m", "arail.deep_probe", *args], env=e, capture_output=True,
                          text=True, timeout=timeout)


def test_qa_guard_an_unimportable_wheel_is_a_recorded_load_failure_that_names_the_import_error(fake_deep):
    fd = fake_deep("pass")
    (fd.package / "__init__.py").write_text(
        'raise ImportError("dlopen(aerollm_api.abi3.so): symbol not found: _mlx_device (QA stand-in)")\n')
    r = _probe(fd, "--json")
    doc = json.loads(r.stdout)
    assert r.returncode == 1 and doc["outcome"] == "fail", r.stderr
    assert doc["first_error"]["kind"] == "load" and "symbol not found" in doc["first_error"]["error"]
    assert doc["binary"]["sha256"] == fd.sha256, "keyed by the native file the import would load"
    rec = dv.record_for(fd.sha256)
    assert rec and rec["failure"]["source"] == "probe"


def test_qa_guard_the_probe_runs_without_HOME(fake_deep):
    fd = fake_deep("pass")
    r = _probe(fd, "--json", drop=("HOME",))
    assert r.returncode == 0 and json.loads(r.stdout)["passed"] == 5, r.stderr


def test_qa_guard_a_read_only_lab_is_not_run_says_why_and_records_nothing(fake_deep, tmp_path):
    fd = fake_deep("pass")
    ro = tmp_path / "read-only-lab"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        r = _probe(fd, env_extra={"ARAIL_DEEP_STATE_DIR": str(ro / "data" / "deep")})
    finally:
        ro.chmod(0o755)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "the probe lock could not be taken" in r.stdout and "check the permissions of that folder" in r.stdout
    assert list(ro.iterdir()) == [] and fd.gen_calls() == [], "not run: no folder made, no deep call"


@pytest.mark.parametrize("store", ["a directory", "mode 000", "another schema", "a symlink"])
def test_qa_guard_a_passing_probe_repairs_a_store_it_cannot_read(fake_deep, tmp_path, store):
    fd = fake_deep("pass")
    d = dv.state_dir()
    d.mkdir(parents=True)
    p = d / "verdict.json"
    if store == "a directory":
        p.mkdir()
    elif store == "mode 000":
        p.write_text('{"schema": "arail.deep-verdict/v1", "binaries": {}}')
        p.chmod(0)
    elif store == "another schema":
        p.write_text('{"schema": "arail.deep-verdict/v9", "binaries": {}}')
    else:
        target = tmp_path / "elsewhere.json"
        target.write_text("{}")
        p.symlink_to(target)
    r = _probe(fd)
    assert r.returncode == 0 and "recorded  passing" in r.stdout, r.stdout + r.stderr
    dv.identity()
    assert dv.current().state == "passing"
    assert list(d.glob("verdict.json.corrupt-*")), "the unreadable store was moved aside, not overwritten"
    if store == "a symlink":
        assert (tmp_path / "elsewhere.json").read_text() == "{}"


def test_qa_guard_a_replaced_binary_starts_untested_in_a_new_process(fake_deep):
    fd = fake_deep("pass")
    dv.identity()
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    fd.rewrite_native(b"A-NEW-BUILD\n" * 64)
    r = subprocess.run([sys.executable, "-m", "arail.deep_verdict", "status"], env=fd.env(), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "never tested on this binary" in r.stdout and "FAILING" not in r.stdout
    code = "from arail.agents import deep_policy; print(deep_policy.explain(foreground=True)[1])"
    r2 = subprocess.run([sys.executable, "-c", code], env=fd.env(LAB_TIER="maximus"), capture_output=True,
                        text=True, timeout=60)
    assert r2.stdout.strip().splitlines()[-1] == "ok", r2.stdout + r2.stderr


# ── QA-13: before setup, `./arailctl deep probe` exits 1, the code that means "deep is failing" ─────
# arailctl's deep branch dies with exit 1 when .venv is missing (die() always exits 1). For every other
# op that is the CLI's generic failure, but `deep probe` documents its own codes (docs/cli.md): 1 is
# "the probe ran and fewer than 5 answered", 5 is "not run, nothing recorded". A script or a future
# /deep caller that reads the code is told deep generation failed on a machine where nothing ran.

def test_qa13_deep_probe_before_setup_exits_5_not_run(tmp_path):
    import shutil
    co = tmp_path / "fresh-checkout"
    (co / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "arailctl", co / "arailctl")
    shutil.copytree(REPO / "scripts" / "lib", co / "scripts" / "lib")
    e = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    r = subprocess.run(["bash", str(co / "arailctl"), "deep", "probe"], env=e, cwd=co, capture_output=True,
                       text=True, timeout=60)
    assert "missing .venv" in r.stderr, r.stderr
    assert r.returncode == 5, f"exit {r.returncode}: docs/cli.md gives 1 the meaning 'the probe ran and deep is failing'"


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no macOS system python3")
def test_qa_guard_deep_status_runs_under_the_system_python_before_setup(tmp_path):
    """A clean Mac runs `bash scripts/build-aerollm.sh status` with PYTHON unset: python3 is the system one."""
    e = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
         "ARAIL_DEEP_STATE_DIR": str(tmp_path / "state"), "PYTHONPATH": str(REPO / "src")}
    r = subprocess.run(["/usr/bin/python3", "-m", "arail.deep_verdict", "status"], env=e, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "aerollm_api is not importable by" in r.stdout and "run: ./arailctl deep install" in r.stdout


# ── QA-12: the supervisor holds a whole stderr line in memory, however long ─────────────────────────
# _gather_stderr reads the child's stderr with readline() and clips each line to 300 characters only
# after reading it, so one long line with no newline (a native progress bar redrawn with "\r", or a
# runaway log) is held whole. Measured: a 300 MB line took the supervisor from 26 MB to 1,334 MB. The
# supervisor exists so a misbehaving child cannot take it down; on a 16 GB Mac that already holds a
# model copy, this is the kind of spike that ends in swap. Reading bounded chunks keeps the tail bounded.

HARNESS = (
    "import io, json, resource, sys\n"
    "from arail import deep_probe as dp\n"
    "scale = 1 if sys.platform == 'darwin' else 1024\n"
    "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
    "buf = io.StringIO(); rc = dp.run(['--json'], out=buf)\n"
    "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
    "print(json.dumps({'rc': rc, 'growth_mb': (after - before) / 2**20, 'passed': json.loads(buf.getvalue())['passed']}))\n"
)


def test_qa12_a_long_stderr_line_from_the_child_does_not_balloon_the_supervisor(fake_deep):
    fd = fake_deep("pass")
    with open(fd.package / "__init__.py", "a", encoding="utf-8") as fh:
        fh.write("\n_qa_orig = Runtime.generate\n"
                 "def _qa_generate(self, prompt, **kw):\n"
                 "    if self.calls == 0:\n"
                 "        for _ in range(int(os.environ.get('QA_STDERR_MB', '0'))):\n"
                 "            os.write(2, b'x' * (1 << 20))          # one line, no newline\n"
                 "        os.write(2, b'\\n')\n"
                 "    return _qa_orig(self, prompt, **kw)\n"
                 "Runtime.generate = _qa_generate\n")
    r = subprocess.run([sys.executable, "-c", HARNESS], env=fd.env(QA_STDERR_MB="64"), capture_output=True,
                       text=True, timeout=180)
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["rc"] == 0 and got["passed"] == 5, r.stderr[-500:]
    assert got["growth_mb"] < 48, (
        f"a 64 MB stderr line grew the supervisor by {got['growth_mb']:.0f} MB; it keeps only 300 characters of it")


# ── QA-10 (TD18): the probe's child outlives a supervisor stopped by SIGTERM or SIGHUP ───────────────

def _start(fd, mode: str):
    fd.set_mode(mode)
    p = subprocess.Popen([sys.executable, "-m", "arail.deep_probe"], env=fd.env(), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if line.startswith("call 1/5"):
            return p
        if not line and p.poll() is not None:
            break
    p.kill()
    raise AssertionError("the probe never reached call 1")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.xfail(strict=True, reason="TD18: the probe handles SIGINT only; on SIGTERM or SIGHUP its child, in a "
                                       "session of its own, runs on (forever if its call hangs) holding a model copy")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_qa10_td18_a_stopped_probe_stops_its_child(fake_deep, sig):
    fd = fake_deep("hang2")
    p = _start(fd, "hang2")                            # call 2 never returns
    child = next(e["pid"] for e in fd.events() if e.get("ev") == "init")
    try:
        time.sleep(0.5)
        p.send_signal(sig)
        p.communicate(timeout=30)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.1)
        assert not _alive(child), f"the probe child (pid {child}) outlived its supervisor"
    finally:
        try:
            os.killpg(child, signal.SIGKILL)           # never leave the orphan behind
        except (ProcessLookupError, PermissionError):
            pass


# ── hints: nothing sends a user with no checkout to a build this ARAIL refuses ──────────────────────

QUALIFIERS = ("pre-rename", "maintainer", "Maintainer", "refuses", "stops here", "can build it", "cannot build",
              "runs `build`", "| DEV |", "do not read it", "`crate:` line", "build-aerollm.sh build",
              "only `exec bash", "deep rebuild and auto", "dispatches to")
# Developer-facing text a user never sees (a class docstring), reviewed and accepted in REVIEW.md item 2.
ALLOWED = {("src/arail/router/backends.py", "(``./arailctl deep rebuild``). Apple Silicon")}


def _user_facing_files():
    for root, pats in ((REPO / "src" / "arail", ("*.py", "*.html", "*.js")), (REPO / "scripts", ("*.sh", "*.py")),
                       (REPO / "docs", ("*.md",))):
        for pat in pats:
            yield from root.rglob(pat)
    yield from (REPO / "README.md", REPO / "arailctl", REPO / "AGENTS.md")


def test_qa_guard_no_user_facing_text_sends_a_user_without_a_checkout_to_deep_rebuild_or_a_clone():
    bad = []
    for path in _user_facing_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            window = " ".join(lines[max(0, i - 2): i + 3])
            rel = str(path.relative_to(REPO))
            if any(rel == f and s in line for f, s in ALLOWED):
                continue
            if "deep rebuild" in line and not any(q in window for q in QUALIFIERS):
                bad.append(f"{path.relative_to(REPO)}:{i + 1}: {line.strip()[:120]}")
            if re.search(r"git clone\S*\s+\S*(queuellm|aerollm)", line):
                bad.append(f"{path.relative_to(REPO)}:{i + 1}: {line.strip()[:120]}")
    assert not bad, "unqualified hints to a build this ARAIL refuses:\n" + "\n".join(bad)
