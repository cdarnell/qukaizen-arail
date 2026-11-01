"""`./arailctl reset` must target the same tree the portal actually reads.

scripts/reset.sh used to hardcode `lab/pkb`. A user who relocated their
knowledge base with LAB_PKB got a *false success*: reset printed "Knowledge
base removed" while the real KB survived untouched. That silently breaks two
promises the repo makes out loud:

  * docs/agents.md — "Wiping memory is always one command: delete the
    file/dir, or run `./arailctl reset pkb`."
  * _builtin_buddy.py — "Same path whether we're the PKB copy or the builtin
    fallback — so 'wipe the PKB' genuinely wipes Buddy's memory."

Two layers of cover here:
  1. End-to-end — drive the real reset.sh in a sandbox repo and assert it
     deletes the LAB_PKB tree and leaves the default lab/pkb alone.
  2. Equivalence — pin reset.sh's resolvers against the real
     arail.config resolver, so the shell and Python copies cannot drift.

SAFETY: only ever drive the *scoped* modes (pkb, pkb-seeds, program, models,
data) here. `full`/`stop`/`destroy` call stop_services() (pgrep+kill against
the whole machine) and destroy_lab() rm -rf's the repo root and $HOME
code-server dirs. Those must never run under pytest.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RESET_SH = REPO_ROOT / "scripts" / "reset.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash required")


def _make_sandbox(tmp_path: Path) -> Path:
    """A throwaway repo root holding a copy of the real reset.sh.

    reset.sh derives REPO_ROOT from its own location and cd's there, so a
    copy under tmp is fully self-contained — it can't touch the real repo.
    """
    fake = tmp_path / "fakerepo"
    (fake / "scripts").mkdir(parents=True)
    shutil.copy2(RESET_SH, fake / "scripts" / "reset.sh")
    return fake


def _run_reset(fake_repo: Path, mode: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, "scripts/reset.sh", mode, "--yes"],
        cwd=fake_repo,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(fake_repo / "home"), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# End-to-end: LAB_PKB is honored
# ---------------------------------------------------------------------------

def test_reset_pkb_targets_lab_pkb_override_and_not_default(tmp_path):
    """With LAB_PKB set, reset wipes THAT tree and leaves lab/pkb alone."""
    fake = _make_sandbox(tmp_path)

    custom = tmp_path / "custom-pkb"
    (custom / "notes").mkdir(parents=True)
    (custom / "notes" / "private.md").write_text("user note", encoding="utf-8")

    # The default tree must be a non-target when the override is set.
    default = fake / "lab" / "pkb"
    default.mkdir(parents=True)
    (default / "decoy.md").write_text("decoy", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_PKB": str(custom)})
    assert res.returncode == 0, res.stdout + res.stderr

    assert not custom.exists(), (
        "reset pkb reported success but the LAB_PKB knowledge base survived — "
        f"this is the false-success bug.\n{res.stdout}"
    )
    assert (default / "decoy.md").exists(), (
        "reset pkb wiped the default lab/pkb even though LAB_PKB pointed elsewhere"
    )


def test_reset_pkb_reports_the_resolved_path_not_a_hardcoded_one(tmp_path):
    """The user-facing message names the tree actually removed."""
    fake = _make_sandbox(tmp_path)
    custom = tmp_path / "custom-pkb"
    custom.mkdir()
    (custom / "note.md").write_text("x", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_PKB": str(custom)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert str(custom) in res.stdout, (
        f"reset should name the resolved PKB path it removed.\n{res.stdout}"
    )


def test_reset_pkb_missing_override_dir_does_not_touch_default(tmp_path):
    """A LAB_PKB pointing at nothing must no-op, not fall back to lab/pkb."""
    fake = _make_sandbox(tmp_path)
    default = fake / "lab" / "pkb"
    default.mkdir(parents=True)
    (default / "decoy.md").write_text("decoy", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_PKB": str(tmp_path / "nonexistent")})
    assert res.returncode == 0, res.stdout + res.stderr
    assert (default / "decoy.md").exists(), (
        "reset fell back to wiping lab/pkb when the LAB_PKB dir was absent"
    )


def test_reset_pkb_legacy_lab_pkm_is_honored(tmp_path):
    """Legacy LAB_PKM still resolves (config.py accepts it with a warning)."""
    fake = _make_sandbox(tmp_path)
    legacy = tmp_path / "legacy-pkb"
    legacy.mkdir()
    (legacy / "note.md").write_text("x", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_PKM": str(legacy)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert not legacy.exists(), f"LAB_PKM was ignored by reset pkb\n{res.stdout}"
    assert "deprecated" in res.stdout.lower(), (
        "reset should mirror config.py's LAB_PKM deprecation warning"
    )


def test_lab_pkb_wins_over_legacy_lab_pkm(tmp_path):
    """When both are set, LAB_PKB wins — same precedence as config.py."""
    fake = _make_sandbox(tmp_path)
    new = tmp_path / "new-pkb"
    old = tmp_path / "old-pkb"
    for d in (new, old):
        d.mkdir()
        (d / "note.md").write_text("x", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_PKB": str(new), "LAB_PKM": str(old)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert not new.exists(), "LAB_PKB should have been the reset target"
    assert old.exists(), "LAB_PKM tree must survive when LAB_PKB is set"


def test_reset_pkb_seeds_honors_lab_pkb(tmp_path):
    """The granular seed reset resolves through the override too."""
    fake = _make_sandbox(tmp_path)
    custom = tmp_path / "custom-pkb"
    seeds = custom / "sources" / "seeds"
    seeds.mkdir(parents=True)
    (seeds / "primer.md").write_text("seed", encoding="utf-8")
    keep = custom / "notes"
    keep.mkdir()
    (keep / "mine.md").write_text("user note", encoding="utf-8")

    res = _run_reset(fake, "pkb-seeds", {"LAB_PKB": str(custom)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert not seeds.exists(), f"pkb-seeds ignored LAB_PKB\n{res.stdout}"
    assert (keep / "mine.md").exists(), "pkb-seeds must not touch user notes"


def test_reset_lab_root_derives_pkb_when_no_explicit_override(tmp_path):
    """LAB_ROOT alone relocates the KB — pkb defaults to $LAB_ROOT/pkb."""
    fake = _make_sandbox(tmp_path)
    lab_root = tmp_path / "elsewhere"
    pkb = lab_root / "pkb"
    pkb.mkdir(parents=True)
    (pkb / "note.md").write_text("x", encoding="utf-8")

    res = _run_reset(fake, "pkb", {"LAB_ROOT": str(lab_root)})
    assert res.returncode == 0, res.stdout + res.stderr
    assert not pkb.exists(), f"reset ignored LAB_ROOT when deriving the PKB\n{res.stdout}"


def test_reset_models_and_data_honor_their_overrides(tmp_path):
    """Same false-success class for ARAIL_MODELS_DIR / ARAIL_DATA_DIR."""
    fake = _make_sandbox(tmp_path)
    models = tmp_path / "custom-models"
    models.mkdir()
    (models / "w.bin").write_text("weights", encoding="utf-8")
    data = tmp_path / "custom-data"
    data.mkdir()
    (data / "activity.jsonl").write_text("{}", encoding="utf-8")

    r1 = _run_reset(fake, "models", {"ARAIL_MODELS_DIR": str(models)})
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert not models.exists(), f"reset models ignored ARAIL_MODELS_DIR\n{r1.stdout}"

    r2 = _run_reset(fake, "data", {"ARAIL_DATA_DIR": str(data)})
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not data.exists(), f"reset data ignored ARAIL_DATA_DIR\n{r2.stdout}"


def test_reset_pkb_purges_pkb_derived_rows_from_arail_db(tmp_path):
    """ADR-0005: <data_dir>/arail.db lives OUTSIDE $PKB_DIR, but
    content_refs (pkb_pages/wiki_nodes) and "document" entities are derived
    straight from PKB content. `reset pkb` must purge them too, or a wipe
    is a false success for exactly the rows PKB content produced — the gap
    ADR-0005 flagged and required a drift test for.

    Rows for a lance_table NOT wiped by reset_pkb (agent_workflows lives
    under $DATA_DIR, untouched here) and a non-document entity kind (goal)
    must survive — this purge is scoped, not a blanket wipe of arail.db.
    """
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI required")
    import sqlite3

    fake = _make_sandbox(tmp_path)
    pkb = fake / "lab" / "pkb"
    pkb.mkdir(parents=True)
    (pkb / "note.md").write_text("x", encoding="utf-8")

    data_dir = fake / "lab" / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "arail.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE content_refs (id TEXT PRIMARY KEY, lance_table TEXT NOT NULL, row_key TEXT);
        CREATE TABLE entities (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT);
        INSERT INTO content_refs VALUES ('c1', 'pkb_pages', 'note.md');
        INSERT INTO content_refs VALUES ('c2', 'wiki_nodes', 'note');
        INSERT INTO content_refs VALUES ('c3', 'agent_workflows', 'agent-1');
        INSERT INTO entities VALUES ('e1', 'document', 'note.md');
        INSERT INTO entities VALUES ('e2', 'goal', 'ship it');
        """
    )
    conn.commit()
    conn.close()

    res = _run_reset(fake, "pkb", {})
    assert res.returncode == 0, res.stdout + res.stderr

    conn = sqlite3.connect(db_file)
    remaining_refs = {row[0] for row in conn.execute(
        "SELECT lance_table FROM content_refs")}
    remaining_kinds = {row[0] for row in conn.execute(
        "SELECT kind FROM entities")}
    conn.close()

    assert remaining_refs == {"agent_workflows"}, (
        f"reset pkb should purge pkb_pages/wiki_nodes content_refs and leave "
        f"others alone; got {remaining_refs}\n{res.stdout}"
    )
    assert remaining_kinds == {"goal"}, (
        f"reset pkb should purge 'document' entities and leave others alone; "
        f"got {remaining_kinds}\n{res.stdout}"
    )


# ---------------------------------------------------------------------------
# Equivalence: the shell resolver must agree with arail.config
# ---------------------------------------------------------------------------

def _bash_resolved_pkb(env: dict[str, str], home: Path) -> str:
    """Run reset.sh's own _resolve_pkb_root, extracted from the real file.

    Mirrors tests/shell_source_safety_driver.sh: sed the function bodies out
    rather than sourcing reset.sh, whose entry-point `case` would execute.
    """
    src = RESET_SH.read_text(encoding="utf-8")
    fns = []
    for name in ("_expand_tilde", "_resolve_lab_root", "_resolve_pkb_root"):
        start = src.index(f"{name}() {{")
        end = src.index("\n}\n", start) + len("\n}\n")
        fns.append(src[start:end])
    script = "set -uo pipefail\n" + "\n".join(fns) + "\n_resolve_pkb_root\n"

    res = subprocess.run(
        [_BASH, "-c", script],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), **env},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


def _python_resolved_pkb(env: dict[str, str], home: Path, tmp_path: Path) -> str:
    """Ask the real arail.config, in a fresh interpreter.

    config.py resolves at import time, so each case needs its own process.
    ARAIL_ENV_FILE is pinned at an empty file: without it python-dotenv walks
    up and can find a parent checkout's .env (config.py says so explicitly),
    which would silently contaminate the comparison.
    """
    empty_env = tmp_path / "empty.env"
    empty_env.touch()
    res = subprocess.run(
        [sys.executable, "-c", "from arail.config import PKB_ROOT; print(PKB_ROOT, end='')"],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "ARAIL_ENV_FILE": str(empty_env),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({}, id="defaults"),
        pytest.param({"LAB_PKB": "/tmp/x-pkb"}, id="lab_pkb"),
        pytest.param({"LAB_PKM": "/tmp/x-legacy"}, id="legacy_lab_pkm"),
        pytest.param({"LAB_PKB": "/tmp/new", "LAB_PKM": "/tmp/old"}, id="pkb_beats_pkm"),
        pytest.param({"LAB_ROOT": "/tmp/x-lab"}, id="lab_root"),
        pytest.param({"LAB_ROOT": "/tmp/x-lab/"}, id="lab_root_trailing_slash"),
        pytest.param({"LAB_ROOT": "/"}, id="lab_root_is_filesystem_root"),
        pytest.param({"LAB_ROOT": "relative-lab"}, id="lab_root_relative"),
        pytest.param({"LAB_PKB": "~/tilde-pkb"}, id="tilde_expansion"),
        pytest.param({"LAB_ROOT": "~/tilde-lab"}, id="tilde_lab_root"),
        pytest.param({"LAB_ROOT": "/tmp/ignored", "LAB_PKB": "/tmp/wins"}, id="pkb_beats_lab_root"),
    ],
)
def test_shell_and_python_resolve_the_same_pkb_root(env, tmp_path):
    """reset.sh and arail.config must never disagree about where the KB is.

    This is the anti-drift pin: whichever side someone edits next, the other
    has to keep up or this fails.
    """
    home = tmp_path / "home"
    home.mkdir()

    from_bash = _bash_resolved_pkb(env, home)
    from_python = _python_resolved_pkb(env, home, tmp_path)

    # Compare as paths, not strings: we care about the same directory, so
    # `lab//pkb` vs `lab/pkb` is agreement, not a failure.
    assert Path(from_bash) == Path(from_python), (
        f"reset.sh resolved {from_bash!r} but arail.config resolved "
        f"{from_python!r} for env={env}"
    )
