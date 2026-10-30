"""scripts/build-aerollm.sh tells the truth.

C7 and F5 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. Tests S1 to S11 are named for the
architecture's list. Each runs the real script against a throwaway venv (no pip), a fake ``cargo`` on
PATH that records its argv and writes an artifact, and a tiny import hook that lets a dummy file named
aerollm_api.abi3.so act as the native module, so identity and the sha256 check work with no compiled code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "scripts" / "build-aerollm.sh"
darwin_arm64 = pytest.mark.skipif(
    not (sys.platform == "darwin" and os.uname().machine == "arm64"),
    reason="the bundled channel is macOS-arm64-only by design (F4)")

FALSE_STRINGS = ("No QueueLLM sibling repo", "QueueLLM ready", "(missing → release/bundled channel)",
                 "(found → source/dev channel)")
GOOD = b"GOOD-ARTIFACT-BYTES\n" * 200
POISONED = b"GOOD-PREFIX\n" * 50 + b"cannot execute tool metal: downloadComponent\n" + b"X" * 100

FINDER = '''\
import importlib.abc, importlib.machinery, os, sys, types
SITE = os.path.dirname(os.path.abspath(__file__))
SO = os.path.join(SITE, "aerollm_api.abi3.so")
DECOY = os.path.join(SITE, "decoy", "aerollm_api.abi3.so")


class _Loader(importlib.abc.Loader):
    def __init__(self, path):
        self.path = path

    def create_module(self, spec):
        m = types.ModuleType(spec.name)
        m.__file__ = self.path
        m.__version__ = "9.9.9-fake"
        return m

    def exec_module(self, module):
        pass


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "aerollm_api" or not os.path.exists(SO):
            return None
        # the flag appears only once the (fake) build has run, so the pre-copy check sees a free target
        use = DECOY if os.path.exists(os.path.join(SITE, "mismatch.flag")) and os.path.exists(DECOY) else SO
        return importlib.machinery.ModuleSpec(name, _Loader(use), origin=use)


sys.meta_path.insert(0, Finder())
'''

FAKE_CARGO = """#!/usr/bin/env bash
set -euo pipefail
echo "$PWD :: $*" >> "${FAKE_CARGO_LOG:?}"
mkdir -p "$PWD/target/release"
cp "${FAKE_CARGO_ARTIFACT:?}" "$PWD/target/release/libaerollm_api.dylib"
if [[ -n "${FAKE_CARGO_TOUCH:-}" ]]; then touch "$FAKE_CARGO_TOUCH"; fi
"""


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Env:
    """One scenario: a venv, its site dir, an optional checkout and a fake cargo."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        venv = tmp / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
        self.py = venv / "bin" / "python"
        self.site = Path(subprocess.check_output(
            [str(self.py), "-c", "import sysconfig; print(sysconfig.get_path('platlib'))"], text=True).strip())
        self.bin = tmp / "fake-bin"
        self.bin.mkdir()
        cargo = self.bin / "cargo"
        cargo.write_text(FAKE_CARGO)
        cargo.chmod(0o755)
        self.cargo_log = tmp / "cargo.log"
        self.artifact = tmp / "artifact.bin"
        self.artifact.write_bytes(GOOD)
        self.repo = tmp / "queuellm"
        self.tmpdir = tmp / "tmpdir"
        self.tmpdir.mkdir()

    # -- fixtures ---------------------------------------------------------
    def checkout(self, crate: "str | None", *, git: bool = True, dirty: bool = False) -> Path:
        self.repo.mkdir()
        (self.repo / ".gitignore").write_text("target/\n")        # a real checkout ignores cargo's output
        if crate:
            (self.repo / "crates" / crate).mkdir(parents=True)
            (self.repo / "crates" / crate / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
        if git:
            for cmd in (["init", "-q"], ["add", "-A"],
                        ["-c", "user.email=t@t.test", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", *cmd], cwd=self.repo, check=True, capture_output=True)
            if dirty:
                (self.repo / "uncommitted.txt").write_text("x\n")
        return self.repo

    def head(self) -> str:
        return subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()

    def fake_importer(self, *, decoy: bytes = b"") -> None:
        (self.site / "zz_fake_aerollm_finder.py").write_text(FINDER)
        (self.site / "zz_fake_aerollm.pth").write_text("import zz_fake_aerollm_finder\n")
        if decoy:
            (self.site / "decoy").mkdir(exist_ok=True)
            (self.site / "decoy" / "aerollm_api.abi3.so").write_bytes(decoy)

    def pip_owned_package(self, *, record: bool = True) -> Path:
        """A pip install of aerollm-api: a package dir that wins the import over any top-level copy."""
        pkg = self.site / "aerollm_api"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        if record:
            di = self.site / "aerollm_api-0.1.0.dist-info"
            di.mkdir()
            (di / "METADATA").write_text("Metadata-Version: 2.1\nName: aerollm-api\nVersion: 0.1.0\n")
            (di / "RECORD").write_text("aerollm_api/__init__.py,,\naerollm_api-0.1.0.dist-info/RECORD,,\n")
            (di / "direct_url.json").write_text(json.dumps(
                {"dir_info": {"editable": True}, "url": "file:///Users/someone/src/aerollm/crates/aerollm-api"}))
        return pkg / "__init__.py"

    # -- running ----------------------------------------------------------
    def run(self, mode: str, *args: str, **extra) -> subprocess.CompletedProcess:
        env = {**os.environ, "NO_COLOR": "1", "PYTHON": str(self.py), "HOME": str(self.tmp),
               "ARAIL_AEROLLM_REPO": str(self.repo), "ARAIL_DEEP_STATE_DIR": str(self.tmp / "state"),
               "TMPDIR": str(self.tmpdir),
               "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_CARGO_LOG": str(self.cargo_log),
               "FAKE_CARGO_ARTIFACT": str(self.artifact)}
        for k in ("AEROLLM_CHANNEL", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "AEROLLM_BUNDLE_FILE",
                  "AEROLLM_BUNDLE_SHA256", "PYTHONPATH"):
            env.pop(k, None)
        env.update({k: str(v) for k, v in extra.items()})
        return subprocess.run(["bash", str(BUILD), mode, *args], env=env, capture_output=True, text=True,
                              timeout=180)

    def listing(self) -> list[str]:
        return sorted(str(p.relative_to(self.site)) for p in self.site.rglob("*") if "__pycache__" not in p.parts)

    def cargo_ran(self) -> bool:
        return self.cargo_log.exists() and bool(self.cargo_log.read_text().strip())


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


# ── S1: no checkout ────────────────────────────────────────────────────────

def test_s1_no_checkout_gets_the_true_message(env):
    r = env.run("build")
    assert r.returncode == 1
    text = out(r)
    assert f"No QueueLLM checkout at {env.repo}." in text
    assert "git clone https://github.com/cdarnell/qukaizen-queuellm" in text and "ARAIL_AEROLLM_REPO" in text
    assert "No QueueLLM sibling repo" not in text
    assert not env.cargo_ran()


# ── S2, S3: a checkout this ARAIL cannot build ─────────────────────────────

def test_s2_the_renamed_crate_is_refused_with_the_true_reason(env):
    env.checkout("queuellm-api")
    before = env.listing()
    r = env.run("build")
    assert r.returncode == 1
    text = out(r)
    assert (f"{env.repo} has the renamed crate crates/queuellm-api (module queuellm_api). This ARAIL can build only "
            "the pre-rename crate crates/aerollm-api, so deep rebuild stops here. Nothing was built or installed.") in text
    assert "For the prebuilt QueueLLM: ./arailctl deep install." in text
    assert not env.cargo_ran(), "cargo is never invoked for the renamed crate"
    assert env.listing() == before, "SEC5: a refused install leaves the site dir identical"
    assert "No QueueLLM sibling repo" not in text


def test_s3_a_checkout_with_neither_crate_is_refused(env):
    env.checkout(None)
    before = env.listing()
    r = env.run("build")
    assert r.returncode == 1
    assert (f"{env.repo} has neither crates/aerollm-api nor crates/queuellm-api, so it is not a QueueLLM checkout "
            "this ARAIL can build. Nothing was built or installed.") in out(r)
    assert not env.cargo_ran() and env.listing() == before


# ── S4: a copy the import would never load ─────────────────────────────────

def test_s4_a_pip_owned_package_shadows_the_copy_so_nothing_is_built_or_copied(env):
    env.checkout("aerollm-api")
    init = env.pip_owned_package()
    before = env.listing()
    r = env.run("build")
    assert r.returncode == 1
    text = out(r)
    assert (f"import aerollm_api in {env.py} loads {init} (pip package aerollm-api 0.1.0, editable from "
            f"file:///Users/someone/src/aerollm/crates/aerollm-api). A copy at {env.site}/aerollm_api.abi3.so "
            "would never load. Nothing was installed.") in text
    assert f"remove that first, for example: {env.py} -m pip uninstall aerollm-api (this changes your venv)." in text
    assert env.listing() == before, "SEC5: nothing copied"
    assert not env.cargo_ran(), "the check runs before the minutes-long build, not after"


def test_s4_the_owner_is_named_only_as_far_as_it_can_be_proven(env, tmp_path):
    env.checkout("aerollm-api")
    init = env.pip_owned_package(record=False)
    assert "(no install record)" in out(env.run("build"))
    # a package that comes from a PYTHONPATH entry
    other = tmp_path / "elsewhere"
    (other / "aerollm_api").mkdir(parents=True)
    (other / "aerollm_api" / "__init__.py").write_text("")
    init.unlink()
    (env.site / "aerollm_api").rmdir()
    r = env.run("build", PYTHONPATH=other)
    assert r.returncode == 1 and f"loads {other}/aerollm_api/__init__.py (a PYTHONPATH entry)" in out(r)


# ── S5: a poisoned build ───────────────────────────────────────────────────

def test_s5_an_artifact_with_the_metal_error_marker_is_refused(env):
    env.checkout("aerollm-api")
    env.artifact.write_bytes(POISONED)
    before = env.listing()
    r = env.run("build")
    assert r.returncode == 1
    assert ("The build carries the Metal toolchain error marker (downloadComponent). It would fail at runtime. "
            "Not installed. See QueueLLM docs/self-hosted-runner-setup.md.") in out(r)
    assert env.cargo_ran()
    assert env.listing() == before and not (env.site / "aerollm_api.abi3.so").exists()


# ── S6: a good build is installed, verified and recorded ───────────────────

def test_s6_a_good_build_is_installed_by_sha256_and_leaves_a_build_marker(env):
    env.checkout("aerollm-api")
    env.fake_importer()
    r = env.run("build")
    assert r.returncode == 0, out(r)
    digest = sha(GOOD)
    assert (f"QueueLLM installed (source build, sha256 {digest[:12]}…). "
            "Generation is not tested yet: run ./arailctl deep probe.") in out(r)
    assert "ready" not in out(r).lower().replace("already", "")
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD
    marker = json.loads((env.site / "aerollm_api.build.json").read_text())
    assert marker["schema"] == "arail.aerollm-build/v1" and marker["built_by"] == "scripts/build-aerollm.sh"
    assert marker["queuellm_commit"] == env.head() and re.fullmatch(r"[0-9a-f]{40}", marker["queuellm_commit"])
    assert marker["queuellm_dirty"] is False and marker["crate"] == "crates/aerollm-api"
    assert marker["artifact_sha256"] == digest and marker["artifact_bytes"] == len(GOOD)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", marker["built_at"])
    assert env.cargo_ran() and "-p aerollm-api" in env.cargo_log.read_text()
    # status now proves the channel from the marker
    st = env.run("status")
    assert re.search(r"channel:\s+source build — QueueLLM commit " + env.head()[:7], st.stdout), st.stdout


def test_s6_the_marker_records_a_dirty_checkout(env):
    env.checkout("aerollm-api", dirty=True)
    env.fake_importer()
    assert env.run("build").returncode == 0
    assert json.loads((env.site / "aerollm_api.build.json").read_text())["queuellm_dirty"] is True


def test_s6_auto_builds_when_the_checkout_has_the_old_crate(env):
    env.checkout("aerollm-api")
    env.fake_importer()
    r = env.run("auto")
    assert r.returncode == 0, out(r)
    assert "Local sibling repo found → building from source (dev channel)." in out(r)
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD


# ── S7: the loaded file is not the one we installed ────────────────────────

def test_s7_a_sha256_mismatch_removes_the_copy_and_exits_1(env):
    env.checkout("aerollm-api")
    env.fake_importer(decoy=b"SOME-OTHER-BINARY\n" * 90)
    r = env.run("build", FAKE_CARGO_TOUCH=env.site / "mismatch.flag")
    assert r.returncode == 1
    decoy = env.site / "decoy" / "aerollm_api.abi3.so"
    assert (f"Installed {env.site}/aerollm_api.abi3.so, but import aerollm_api loads {decoy} "
            f"(sha256 {sha(decoy.read_bytes())[:12]}…). Removed the copy.") in out(r)
    assert not (env.site / "aerollm_api.abi3.so").exists()
    assert not (env.site / "aerollm_api.build.json").exists(), "no marker for a build that did not load"


def test_s7_a_previous_file_is_put_back_on_a_mismatch(env):
    env.checkout("aerollm-api")
    env.fake_importer(decoy=b"SOME-OTHER-BINARY\n" * 90)
    previous = b"PREVIOUS-INSTALL\n" * 40
    (env.site / "aerollm_api.abi3.so").write_bytes(previous)
    r = env.run("build", FAKE_CARGO_TOUCH=env.site / "mismatch.flag")
    assert r.returncode == 1
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == previous
    assert "The file that was there before was put back." in out(r)
    assert list(env.tmpdir.iterdir()) == [], "the temp backup of the previous file is removed too"


# ── S8: auto never switches channel on its own ─────────────────────────────

def test_s8_auto_with_the_renamed_crate_refuses_and_never_reads_the_bundle(env):
    env.checkout("queuellm-api")
    before = env.listing()
    nowhere = env.tmp / "must-not-be-read.tar.gz"
    r = env.run("auto", AEROLLM_BUNDLE_FILE=nowhere, AEROLLM_BUNDLE_SHA256="0" * 64)
    assert r.returncode == 1
    text = out(r)
    assert "has the renamed crate crates/queuellm-api" in text
    assert ("Not switching to another channel on my own. Prebuilt instead: ./arailctl deep install "
            "(or AEROLLM_CHANNEL=bundle).") in text
    for forbidden in ("Using local bundle tarball", "AEROLLM_BUNDLE_FILE", "Downloading", "bundled binary",
                      "Checksum", str(nowhere)):
        assert forbidden not in text, forbidden
    assert not env.cargo_ran() and env.listing() == before


def test_s8_auto_with_neither_crate_refuses_the_same_way(env):
    env.checkout(None)
    r = env.run("auto")
    assert r.returncode == 1 and "has neither crates/aerollm-api nor crates/queuellm-api" in out(r)
    assert "Not switching to another channel on my own." in out(r)


def test_s8_auto_with_no_checkout_still_goes_to_the_bundle_as_before(env):
    r = env.run("auto")                     # no checkout, no credentials: the outside user's route
    assert "No QueueLLM checkout, no release credentials → installing the bundled binary (bundled channel)." in out(r)
    assert "Not switching to another channel" not in out(r)


def test_s8_a_forced_channel_still_wins(env):
    env.checkout("queuellm-api")
    r = env.run("auto", AEROLLM_CHANNEL="dev")
    assert r.returncode == 1 and "has the renamed crate" in out(r), "dev means build, and build refuses"
    r = env.run("auto", AEROLLM_CHANNEL="nonsense")
    assert r.returncode == 2 and "AEROLLM_CHANNEL must be one of" in out(r)


# ── S9: status against a fixture that mimics the live layout ───────────────

LIVE_INIT = '''\
import os, sys, types
_n = types.ModuleType(__name__ + ".aerollm_api")
_n.__file__ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aerollm_api.abi3.so")
sys.modules[_n.__name__] = _n
__version__ = "0.1.0-rc.2"
'''


def _live_layout(env: Env) -> bytes:
    native = b"NATIVE-LIVE-2026-06-28\n" * 300
    pkg = env.site / "aerollm_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(LIVE_INIT)
    (pkg / "aerollm_api.abi3.so").write_bytes(native)
    (env.site / "aerollm_api.abi3.so").write_bytes(b"NATIVE-OLD-2026-05-02\n" * 200)    # shadowed
    di = env.site / "aerollm_api-0.1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: aerollm-api\nVersion: 0.1.0\n")
    (di / "direct_url.json").write_text(json.dumps(
        {"dir_info": {"editable": True}, "url": "file:///Users/someone/src/aerollm/crates/aerollm-api"}))
    import base64
    recorded = hashlib.sha256(b"WHAT-PIP-ACTUALLY-INSTALLED").digest()
    (di / "RECORD").write_text(
        f"aerollm_api/aerollm_api.abi3.so,sha256={base64.urlsafe_b64encode(recorded).decode().rstrip('=')},20260240\n"
        "aerollm_api-0.1.0.dist-info/RECORD,,\n")
    return native


def test_s9_status_names_what_it_finds_and_proves_only_what_it_can(env):
    env.checkout("queuellm-api")
    native = _live_layout(env)
    r = env.run("status")
    assert r.returncode == 0, out(r)
    text = r.stdout
    assert "• QueueLLM (2nd inference) status" in text
    assert f"repo:         {env.repo}" in text
    assert "crate:        crates/queuellm-api, the renamed crate; deep rebuild cannot build it yet" in text
    assert f"python:       {env.py}" in text
    assert f"import:       aerollm_api -> {env.site}/aerollm_api/__init__.py (package)" in text
    assert f"binary:       {env.site}/aerollm_api/aerollm_api.abi3.so" in text
    assert sha(native)[:12] + "…" in text and f"{len(native)} bytes" in text and "version 0.1.0-rc.2" in text
    assert re.search(rf"shadowed:\s+{re.escape(str(env.site))}/aerollm_api\.abi3\.so · sha256 [0-9a-f]{{12}}… · "
                     r"modified \d{4}-\d\d-\d\d · on the path, never loaded", text)
    ch = next(ln for ln in text.splitlines() if ln.lstrip().startswith("channel:"))
    assert "unknown — pip package aerollm-api 0.1.0 (editable, file:///Users/someone/src/aerollm/crates/aerollm-api)" in ch
    assert "but its binary was replaced outside pip" in ch and sha(native)[:12] in ch
    assert re.search(r"generation:\s+never tested on this binary — run ./arailctl deep probe "
                     r"\(an import is not a generation test\)", text)
    for forbidden in FALSE_STRINGS:
        assert forbidden not in out(r)


def test_s9_status_shows_a_recorded_failure_and_teaches_it(env):
    env.checkout("queuellm-api")
    native = _live_layout(env)
    state = env.tmp / "state"
    state.mkdir()
    (state / "verdict.json").write_text(json.dumps({
        "schema": "arail.deep-verdict/v1", "updated_at": "2026-10-29T13:36:02Z",
        "binaries": {sha(native): {
            "module": "aerollm_api", "native_path": "x", "bytes": len(native), "version": "0.1.0-rc.2",
            "last_pass_at": None,
            "last_probe": {"started_at": "2026-10-29T13:35:50Z", "started_ts": 1.0, "at": "2026-10-29T13:36:02Z",
                           "outcome": "fail", "passed": 1, "total": 5, "median_ok_s": 9.65,
                           "first_error": {"call": 2, "kind": "long", "error": "RuntimeError: [rms_norm] has 1 elements"}},
            "failure": {"since": "2026-10-29T13:36:02Z", "since_ts": 2.0, "last_at": "2026-10-29T13:36:02Z",
                        "last_ts": 2.0, "source": "probe", "consumer": "deep-probe", "lab": "root",
                        "error": "RuntimeError: [rms_norm] has 1 elements", "last_error": "x", "count": 1}}}}))
    text = env.run("status").stdout
    assert "generation:   FAILING — 1/5 at the last probe (2026-10-29 13:36 UTC); first error at call 2:" in text
    block = text.split("generation:", 1)[1].split("bg pressure:", 1)[0]
    assert all(lbl in block for lbl in ("Bar:", "Out of range:", "Fault lives in:")), block


def test_s9_the_bg_pressure_number_is_taught(env):
    text = env.run("status").stdout
    line = next(ln for ln in text.splitlines() if "bg pressure:" in ln)
    assert "0.60 (default)" in line
    teach = text.split("bg pressure:", 1)[1]
    for part in ("the Metal memory-pressure ceiling", "Bar:", "Out of range:", "Fault lives in:",
                 "clamped 0.30 to 0.75"):
        assert part in teach, part
    custom = env.run("status", ARAIL_AEROLLM_BG_PRESSURE_PCT="0.45").stdout
    assert "bg pressure:  0.45" in custom and "0.45: the Metal memory-pressure ceiling" in custom


def test_s9_status_with_nothing_installed(env):
    env.checkout("aerollm-api")
    text = env.run("status").stdout
    assert "crate:        crates/aerollm-api, the pre-rename crate; deep rebuild can build it" in text
    assert re.search(r"import:\s+aerollm_api is not importable by .* — run: ./arailctl deep install", text)
    assert re.search(r"channel:\s+none", text) and "not applicable — nothing is installed" in text


# ── S10: the false lines never come back, in any mode ──────────────────────

@pytest.mark.parametrize("state", ["no-repo", "old", "new", "none"])
def test_s10_no_mode_prints_a_false_line(env, state):
    if state != "no-repo":
        env.checkout({"old": "aerollm-api", "new": "queuellm-api", "none": None}[state])
        env.fake_importer()
    for mode in ("status", "build", "auto", "bundle", "update"):
        r = env.run(mode, AEROLLM_BUNDLE_FILE=env.tmp / "absent.tar.gz")
        text = out(r)
        for forbidden in FALSE_STRINGS:
            assert forbidden not in text, (mode, state, forbidden)
        assert not re.search(r"channel:\s+(release|dev)\b", text), (mode, state, text)


# ── S11: the bundle channel checks the import target before F7 ─────────────

@darwin_arm64
def test_s11_bundle_refuses_a_shadowed_target_before_anything_else_and_force_does_not_skip_it(env):
    init = env.pip_owned_package()
    before = env.listing()
    absent = env.tmp / "never-looked-at.tar.gz"
    for extra in ((), ("--force",)):
        r = env.run("bundle", *extra, AEROLLM_BUNDLE_FILE=absent, AEROLLM_BUNDLE_SHA256="0" * 64)
        assert r.returncode == 1
        text = out(r)
        assert f"import aerollm_api in {env.py} loads {init} (pip package aerollm-api 0.1.0" in text
        assert "A copy at" in text and "would never load. Nothing was installed." in text
        assert "does not exist" not in text and "Checksum" not in text, "the tarball was never looked at"
        assert env.listing() == before


@darwin_arm64
def test_f7_says_only_what_is_known(env):
    (env.site / "aerollm_api.abi3.so").write_bytes(b"half-written")
    r = env.run("bundle", AEROLLM_BUNDLE_FILE=env.tmp / "absent.tar.gz", AEROLLM_BUNDLE_SHA256="0" * 64)
    text = out(r)
    assert r.returncode == 1
    assert f"an aerollm_api.abi3.so with no install record is already at {env.site}/aerollm_api.abi3.so." in text
    assert "Refusing to overwrite it. Re-run with --force" in text
    assert "looks like a DEV or RELEASE install" not in text
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == b"half-written"


def test_a_good_install_leaves_no_temp_backups_behind(env):
    env.checkout("aerollm-api")
    env.fake_importer()
    (env.site / "aerollm_api.abi3.so").write_bytes(b"PREVIOUS\n" * 30)
    assert env.run("build").returncode == 0
    assert list(env.tmpdir.iterdir()) == []
    assert (env.site / "aerollm_api.abi3.so").read_bytes() == GOOD
