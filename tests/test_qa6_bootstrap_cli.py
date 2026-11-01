"""QA-6: ./arailctl pkb bootstrap --world / --all-instances.

These exercise the real shell code (arailctl + scripts/lib/instances.sh)
inside a sandbox repo whose lab/ is a temp dir — the operator's own lab/ and
lab/instances/ are never touched. The sandbox stubs .venv/bin/activate to
point PYTHONPATH at the real src/, so the Python half is the real one too.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    box = tmp_path / "repo"
    box.mkdir()
    shutil.copy2(REPO / "arailctl", box / "arailctl")
    (box / "arailctl").chmod(0o755)
    shutil.copytree(REPO / "scripts", box / "scripts")
    (box / ".venv" / "bin").mkdir(parents=True)
    shim = box / ".venv" / "bin"
    (shim / "activate").write_text(
        f'export PYTHONPATH="{SRC}"\nexport PATH="{shim}:$PATH"\n')
    # this environment has python3 but no `python`; arailctl calls `python`
    # after activating the venv, which a real setup would have provided.
    (shim / "python").write_text('#!/usr/bin/env bash\nexec python3 "$@"\n')
    (shim / "python").chmod(0o755)
    (box / "lab").mkdir()
    return box


def _instance(box: pathlib.Path, slug: str, *, world: str | None = None,
              terms: list[dict] | None = None, secrets: str | None = None):
    inst = box / "lab" / "instances" / slug
    (inst / "pkb").mkdir(parents=True)
    (inst / "data").mkdir(parents=True)
    reg = box / "lab" / "instances" / "registry.d"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"{slug}.json").write_text(json.dumps({"slug": slug, "pid": 1}))
    if secrets is not None:
        s = inst / "data" / "secrets.env"
        s.write_text(secrets)
        s.chmod(0o600)
    if world and terms is not None:
        td = inst / "pkb" / "sources" / f"world-{world}" / "terms"
        td.mkdir(parents=True)
        for t in terms:
            (td / f"{t['slug']}.md").write_text(f"# {t['slug']}\nbody\n")
        wd = box / "lab" / "worlds" / world
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "terms.json").write_text(json.dumps({"version": 1, "terms": terms}))
    return inst


def _clean_env(box: pathlib.Path) -> dict:
    """Built from scratch, not inherited: sibling tests in a full-suite run
    export LAB_*/ARAIL_* vars that would otherwise redirect the sandbox's
    PKB root and make these tests order-dependent."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR")
           if k in os.environ}
    env["ARAIL_WORLDS_DIR"] = str(box / "lab" / "worlds")
    return env


def _run(box: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = _clean_env(box)
    return subprocess.run(
        ["bash", str(box / "arailctl"), "pkb", "bootstrap", *args],
        cwd=box, env=env, capture_output=True, text=True)


TERMS = [{"slug": "alpha"}, {"slug": "beta"}]


@pytest.fixture()
def box(tmp_path):
    b = _sandbox(tmp_path)
    (b / "lab" / "pkb").mkdir()
    return b


def test_world_slug_bootstraps_only_that_instance(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    _instance(box, "video-games", world="video-games", terms=[{"slug": "rpg"}])

    r = _run(box, "--world", "debt-finance")
    assert r.returncode == 0, r.stderr
    df = box / "lab/instances/debt-finance/pkb/compiled/kb/approved.json"
    vg = box / "lab/instances/video-games/pkb/compiled/kb/approved.json"
    assert df.is_file()
    assert not vg.exists(), "--world <slug> touched a sibling World"
    items = json.loads(df.read_text())["items"]
    assert set(items) == {"sources/world-debt-finance/terms/alpha.md",
                          "sources/world-debt-finance/terms/beta.md"}


@pytest.mark.parametrize("bad", [
    "../../etc", "..", ".", "/etc", "a/b", "Debt-Finance", "-x",
    "debt-finance;touch pwned", "$(id)", "`id`", "*",
])
def test_hostile_world_slug_is_refused_and_writes_nothing(box, bad):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    before = sorted(p for p in box.rglob("*") if p.is_file())
    r = _run(box, "--world", bad)
    assert r.returncode != 0
    after = sorted(p for p in box.rglob("*") if p.is_file())
    assert before == after, f"slug {bad!r} caused a write"
    assert not (box / "pwned").exists()


def test_empty_world_slug_falls_back_to_the_root_lab(box):
    """QA finding (INFO): `--world ""` is not rejected — the `-n` guard
    treats it as "no target" and bootstraps the root lab. Contained (the
    root lab is a legitimate target of this verb) but it is a silent
    substitution, not a refusal. Pinned so a change is deliberate."""
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    r = _run(box, "--world", "")
    assert r.returncode == 0
    assert (box / "lab/pkb/compiled/kb/approved.json").is_file()
    assert not (box / "lab/instances/debt-finance/pkb/compiled").exists()


def test_world_and_all_instances_together_is_refused(box):
    r = _run(box, "--world", "debt-finance", "--all-instances")
    assert r.returncode != 0
    assert "mutually exclusive" in (r.stderr + r.stdout)


def test_all_instances_covers_root_plus_every_instance(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    _instance(box, "video-games", world="video-games", terms=[{"slug": "rpg"}])
    r = _run(box, "--all-instances")
    assert r.returncode == 0, r.stderr + r.stdout
    for rel, n in [("lab/pkb", 0),
                   ("lab/instances/debt-finance/pkb", 2),
                   ("lab/instances/video-games/pkb", 1)]:
        man = box / rel / "compiled/kb/approved.json"
        assert man.is_file(), f"{rel} not bootstrapped"
        assert len(json.loads(man.read_text())["items"]) == n


def test_all_instances_writes_nothing_outside_lab(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    outside = {p for p in box.rglob("*")
               if p.is_file() and "lab" not in p.relative_to(box).parts}
    before = {p: p.stat().st_mtime_ns for p in outside}
    _run(box, "--all-instances")
    assert {p: p.stat().st_mtime_ns for p in outside} == before


def test_per_instance_secrets_are_never_read_written_or_linked(box):
    """CLAUDE.md: per-instance secrets are never shared or auto-copied."""
    a = _instance(box, "debt-finance", world="debt-finance", terms=TERMS,
                  secrets="ANTHROPIC_API_KEY=sk-aaa\n")
    b = _instance(box, "video-games", world="video-games", terms=[{"slug": "rpg"}],
                  secrets="ANTHROPIC_API_KEY=sk-bbb\n")
    sec = [a / "data/secrets.env", b / "data/secrets.env"]
    before = [(p.read_text(), p.stat().st_mode, p.stat().st_mtime_ns) for p in sec]

    r = _run(box, "--all-instances")
    assert r.returncode == 0

    after = [(p.read_text(), p.stat().st_mode, p.stat().st_mtime_ns) for p in sec]
    assert before == after
    assert not any(p.is_symlink() for p in sec)
    # no secret value leaked into stdout/stderr or into any manifest
    blob = r.stdout + r.stderr
    for manifest in box.rglob("approved.json"):
        blob += manifest.read_text()
    assert "sk-aaa" not in blob and "sk-bbb" not in blob
    # and no new secrets.env appeared anywhere (e.g. copied to the root lab)
    assert {p.resolve() for p in box.rglob("secrets.env")} == {p.resolve() for p in sec}


def test_stale_registry_entry_without_a_pkb_dir_is_skipped_not_fatal(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    reg = box / "lab/instances/registry.d"
    (reg / "ghost.json").write_text(json.dumps({"slug": "ghost"}))
    r = _run(box, "--all-instances")
    assert r.returncode == 0, r.stderr
    assert "ghost" in (r.stdout + r.stderr)
    assert (box / "lab/instances/debt-finance/pkb/compiled/kb/approved.json").is_file()
    assert not (box / "lab/instances/ghost").exists()


def test_corrupt_registry_record_does_not_stop_the_run(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    (box / "lab/instances/registry.d/broken.json").write_text("{not json")
    (box / "lab/instances/broken/pkb").mkdir(parents=True)
    r = _run(box, "--all-instances")
    assert r.returncode == 0, r.stderr
    assert (box / "lab/instances/debt-finance/pkb/compiled/kb/approved.json").is_file()


def test_ask7_registry_filename_that_is_not_a_valid_slug(box):
    """REVIEW ASK-7: --all-instances does not run slugs through
    inst_valid_slug. Judge exploitability empirically: a registry file named
    '...json' yields slug '..' -> lab/instances/../pkb == lab/pkb, which the
    same run already bootstrapped. Assert containment: nothing outside lab/
    is written and no manifest appears anywhere unexpected."""
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    (box / "lab/instances/registry.d/...json").write_text("{}")
    r = _run(box, "--all-instances")
    manifests = {p.relative_to(box).as_posix() for p in box.rglob("approved.json")}
    assert manifests <= {
        "lab/pkb/compiled/kb/approved.json",
        "lab/instances/debt-finance/pkb/compiled/kb/approved.json",
        "lab/instances/pkb/compiled/kb/approved.json",
    }, manifests
    assert all(p.startswith("lab/") for p in manifests)
    assert r.returncode in (0, 1)


def test_dry_run_writes_no_manifest_anywhere(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    r = _run(box, "--all-instances", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert list(box.rglob("approved.json")) == []


def test_bootstrap_exit_code_when_a_root_fails(box):
    """A per-root failure is reported in skipped_reason but the CLI still
    exits 0 — documented here so the behavior is deliberate, not assumed."""
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    inst = box / "lab/instances/nopkb"
    (inst).mkdir(parents=True)
    (box / "lab/instances/registry.d/nopkb.json").write_text("{}")
    r = _run(box, "--all-instances")
    assert r.returncode == 0
    assert "nopkb" in (r.stdout + r.stderr)


def test_root_only_bootstrap_leaves_instances_alone(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    r = _run(box, "--world", "root")
    assert r.returncode == 0, r.stderr
    assert (box / "lab/pkb/compiled/kb/approved.json").is_file()
    assert not (box / "lab/instances/debt-finance/pkb/compiled").exists()


def test_arailctl_shell_syntax_is_clean():
    assert subprocess.run(["bash", "-n", str(REPO / "arailctl")]).returncode == 0
    assert subprocess.run(["bash", "-n", str(REPO / "scripts" / "install.sh")]).returncode == 0


# ── `./arailctl install` wiring (setup/regression 30%+10%) ───────────────

_HARNESS = r'''
set -euo pipefail
REPO_ROOT="{box}"
_install_line() {{ printf '%s\n' "$1"; }}
{fn}
_install_kb_bootstrap
echo "STILL-ALIVE"
'''


def _install_fn() -> str:
    src = (REPO / "scripts" / "install.sh").read_text()
    start = src.index("_install_kb_bootstrap() {")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_install_bootstrap_hook_runs_and_is_non_fatal(box):
    _instance(box, "debt-finance", world="debt-finance", terms=TERMS)
    script = _HARNESS.format(box=box, fn=_install_fn())
    env = _clean_env(box)
    r = subprocess.run(["bash", "-c", script], cwd=box, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "STILL-ALIVE" in r.stdout
    assert (box / "lab/pkb/compiled/kb/approved.json").is_file()
    # install bootstraps the ROOT lab only — instances need the explicit verb
    assert not (box / "lab/instances/debt-finance/pkb/compiled").exists()


def test_install_bootstrap_hook_degrades_when_python_fails(box):
    """A failing bootstrap must degrade install, never hard-fail it."""
    (box / ".venv" / "bin" / "python").write_text(
        '#!/usr/bin/env bash\nexit 9\n')
    script = _HARNESS.format(box=box, fn=_install_fn())
    env = _clean_env(box)
    r = subprocess.run(["bash", "-c", script], cwd=box, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "STILL-ALIVE" in r.stdout
    assert "bootstrap skipped" in r.stdout


def test_install_bootstrap_hook_is_a_noop_without_a_venv(tmp_path):
    box = _sandbox(tmp_path)
    (box / "lab" / "pkb").mkdir()
    shutil.rmtree(box / ".venv")
    script = _HARNESS.format(box=box, fn=_install_fn())
    r = subprocess.run(["bash", "-c", script], cwd=box, env=_clean_env(box),
                       capture_output=True, text=True)
    assert r.returncode == 0 and "STILL-ALIVE" in r.stdout
    assert not (box / "lab/pkb/compiled").exists()


def test_bootstrap_on_a_lab_with_no_worlds_at_all(tmp_path):
    box = _sandbox(tmp_path)
    (box / "lab" / "pkb").mkdir()
    r = _run(box, "--all-instances")
    assert r.returncode == 0, r.stderr
    man = box / "lab/pkb/compiled/kb/approved.json"
    assert man.is_file() and json.loads(man.read_text())["items"] == {}


def test_bootstrap_on_a_completely_fresh_lab_dir(tmp_path):
    box = _sandbox(tmp_path)  # no lab/pkb at all
    r = _run(box)
    assert r.returncode == 0, r.stderr
    assert "root" in r.stdout
