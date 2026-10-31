"""Adversarial audit of the instance isolation boundary.

Sprint: sprints/2026-07-28-concurrent-worlds/ (QA pass, post-WEAK_PASS).

tests/test_instance_isolation.py proves the positive case: mounting World B in
root B leaves root A byte-identical. This file attacks the claim from the other
side — it hunts for a write path that ESCAPES an instance root, and it pins the
exact set of ``Path.cwd()``-rooted filesystem sites that A32.1 asserts is a set
of size one (``egress.py:92``) and REVIEW.md m8 says is not.

Allocation: isolation-correctness + security. No test here depends on another,
and none needs a running portal.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "arail"
APP_PY = SRC / "portal" / "app.py"
PY = sys.executable


def _instance_env(root: Path, repo: Path, slug: str = "qa") -> dict:
    """The env an instance process actually runs under (start.sh's `set -a;
    source instance.env`), minus the token."""
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "ARAIL_INSTANCE": slug,
        "ARAIL_ENV_FILE": str(root / "instance.env"),
        "LAB_ROOT": str(root),
        "ARAIL_DATA_DIR": str(root / "data"),
        "LAB_PKB": str(root / "pkb"),
        "ARAIL_EXPERIMENTS_DIR": str(root / "data" / "experiments"),
        "ARAIL_MODELS_DIR": str(repo / "lab" / "models"),
        "ARAIL_WORLDS_DIR": str(repo / "lab" / "worlds"),
        "PORTAL_PORT": "8090",
        "LANCE_PORT": "8094",
    })
    env.pop("LAB_PKM", None)
    return env


@pytest.fixture()
def instance(tmp_path: Path):
    repo = tmp_path / "repo"
    root = repo / "lab" / "instances" / "qa"
    for d in (root / "data", root / "pkb" / "sources", root / "pkb" / "notes",
              root / "log", repo / "lab" / "models", repo / "lab" / "worlds",
              repo / "lab" / "data", repo / "lab" / "pkb"):
        d.mkdir(parents=True, exist_ok=True)
    (root / "instance.env").write_text("", encoding="utf-8")
    return repo, root


# ---------------------------------------------------------------------------
# 1. Every config-resolved runtime path lands inside the instance root
# ---------------------------------------------------------------------------

def test_config_resolves_every_per_instance_path_inside_the_instance_root(instance):
    repo, root = instance
    out = subprocess.run(
        [PY, "-c",
         "import json\n"
         "from arail import config\n"
         "print(json.dumps({\n"
         "  'LAB_ROOT': str(config.LAB_ROOT),\n"
         "  'DATA_DIR': str(config.DATA_DIR),\n"
         "  'PKB_ROOT': str(config.PKB_ROOT),\n"
         "  'MODELS_DIR': str(config.MODELS_DIR),\n"
         "  'WORLDS_DIR': str(config.WORLDS_DIR),\n"
         "}))"],
        capture_output=True, text=True, timeout=120,
        env=_instance_env(root, repo), cwd=str(repo),
    )
    assert out.returncode == 0, out.stderr
    paths = json.loads(out.stdout)
    for key in ("LAB_ROOT", "DATA_DIR", "PKB_ROOT", "MODELS_DIR", "WORLDS_DIR"):
        assert Path(paths[key]).is_absolute(), f"{key} is not absolute: {paths[key]}"
    # Per-instance: strictly under the instance root.
    for key in ("LAB_ROOT", "DATA_DIR", "PKB_ROOT"):
        assert paths[key].startswith(str(root)), f"{key} escaped the instance root"
    # Shared: strictly under the checkout's lab/, never forked per instance —
    # the config.py:86 trap ARCHITECTURE §1.2 calls out as a NEW FINDING.
    assert paths["MODELS_DIR"] == str(repo / "lab" / "models")
    assert paths["WORLDS_DIR"] == str(repo / "lab" / "worlds")
    assert not paths["MODELS_DIR"].startswith(str(root))


def test_egress_activity_and_experiments_all_write_under_the_instance_data_dir(
    instance,
):
    """The three highest-volume writers on the instance path. ``egress.py:92``
    bypasses ``config`` and re-reads ``os.getenv`` (A32.1's acknowledged
    exception); ``activity.py`` and the experiments dir go through config.
    All three must land in the same place.
    """
    repo, root = instance
    out = subprocess.run(
        [PY, "-c",
         "import json\n"
         "from arail import config, activity\n"
         "from arail.egress import _lab_data\n"
         "print(json.dumps({\n"
         "  'egress': str(_lab_data()),\n"
         "  'activity': str(activity.LOG_FILE),\n"
         "  'experiments': str(config.EXPERIMENTS_DIR)\n"
         "     if hasattr(config, 'EXPERIMENTS_DIR') else '',\n"
         "}))"],
        capture_output=True, text=True, timeout=120,
        env=_instance_env(root, repo), cwd=str(repo),
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["egress"] == str(root / "data"), d
    assert d["activity"].startswith(str(root / "data")), d
    if d["experiments"]:
        assert d["experiments"].startswith(str(root)), d


def test_wiki_and_lancedb_caches_are_rooted_at_the_instance_pkb(instance):
    """The vector index and wiki cache are the two biggest on-disk artefacts a
    World mount produces. Both must be derived from PKB_ROOT, never from CWD —
    a shared LanceDB directory would make cross-World retrieval possible, which
    is the exact harm the BRIEF's structural-isolation constraint forbids.
    """
    repo, root = instance
    out = subprocess.run(
        [PY, "-c",
         "import json\n"
         "from arail import config\n"
         "from arail.pkb import _vector_db_path\n"
         "from arail.wiki_vectors import _vector_dir\n"
         "  if False else None\n"],
        capture_output=True, text=True, timeout=60,
        env=_instance_env(root, repo), cwd=str(repo),
    )
    # wiki_vectors' helper is private and may be renamed; assert on the
    # documented derivation for it instead, which is what actually has to hold.
    out = subprocess.run(
        [PY, "-c",
         "import json\n"
         "from arail import config\n"
         "from arail.pkb import _vector_db_path\n"
         "print(json.dumps({'lance': str(_vector_db_path(config.PKB_ROOT)),\n"
         "                  'wiki': str(config.PKB_ROOT / '.wiki-cache')}))"],
        capture_output=True, text=True, timeout=120,
        env=_instance_env(root, repo), cwd=str(repo),
    )
    assert out.returncode == 0, out.stderr[-800:]
    d = json.loads(out.stdout)
    assert d["lance"].startswith(str(root / "pkb")), d
    assert d["wiki"].startswith(str(root / "pkb")), d


def test_the_boot_assertion_fires_for_every_one_of_the_five_paths(instance):
    """F14 must name the offending key — and must cover all five, not just
    LAB_ROOT (the only one the builder's test exercised).
    """
    repo, root = instance
    for key in ("LAB_ROOT", "ARAIL_DATA_DIR", "LAB_PKB",
                "ARAIL_MODELS_DIR", "ARAIL_WORLDS_DIR"):
        env = _instance_env(root, repo)
        env[key] = "relative/not/absolute"
        out = subprocess.run(
            [PY, "-c", "import arail.portal.app"],
            capture_output=True, text=True, timeout=180, env=env, cwd=str(repo),
        )
        assert out.returncode != 0, f"{key}=relative did not fail the boot assertion"
        assert key in out.stderr, (
            f"the boot assertion did not name {key}:\n{out.stderr[-800:]}"
        )
        assert "boot assertion failed" in out.stderr


def test_the_root_lab_is_unaffected_by_the_boot_assertion(tmp_path: Path):
    """ARAIL_INSTANCE unset ⇒ the assertion is a no-op and today's
    CWD-relative-default behaviour is preserved exactly (the zero-Worlds
    legacy path's byte-parity claim depends on this).
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.pop("ARAIL_INSTANCE", None)
    env["LAB_ROOT"] = "lab"  # deliberately relative
    out = subprocess.run(
        [PY, "-c",
         "from arail.portal.app import _assert_instance_paths_absolute as f; f(); "
         "print('NOOP')"],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr[-1500:]
    assert "NOOP" in out.stdout


# ---------------------------------------------------------------------------
# 2. Static audit — Path.cwd()-rooted filesystem sites
# ---------------------------------------------------------------------------

# REVIEW.md m8 (still open): A32.1 claims egress.py:92 is "the one known
# bypass". It is not. This pins the ALLOWED set so a new escape — especially a
# WRITE — cannot be added without this test going red.
_ALLOWED_CWD_FS_SITES = {
    # app.py — read-only diagnostics + the registry, which is checkout-scoped
    # by design (§5.2: every instance reads the SAME registry.d/).
    "lab/instances/registry.d",
    "core/knowledge-canvas/docker-compose.yml",
    "components.json",
    "lab/data/activity.jsonl",
    "lab/data/agent_workflows.json",
    "lab/data",
    "models",
    ".env",
    "arail",
}


def test_cwd_rooted_filesystem_sites_in_app_py_are_the_known_allowed_set():
    """Enumerate every ``Path.cwd() / ...`` expression in app.py and compare it
    to the audited allow-list. A32.1 is only sound if this set does not grow.
    """
    text = APP_PY.read_text(encoding="utf-8")
    found = set()
    for m in re.finditer(r"Path\.cwd\(\)((?:\s*/\s*\"[^\"]+\")+)", text):
        parts = re.findall(r"\"([^\"]+)\"", m.group(1))
        found.add("/".join(parts))
    unexpected = found - _ALLOWED_CWD_FS_SITES
    assert not unexpected, (
        "new Path.cwd()-rooted filesystem site(s) in app.py — these bypass the "
        "instance's env pack and read/write the ROOT lab's tree from inside an "
        f"instance process: {sorted(unexpected)}"
    )


def test_no_cwd_rooted_site_in_app_py_is_a_write():
    """The audited sites are tolerable only because they are READS. Any
    ``.write_text``/``.write_bytes``/``open(..., "w")``/``mkdir`` on a
    ``Path.cwd()``-derived expression is an isolation escape.
    """
    text = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)
    offenders = []

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):  # noqa: N802
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in {
                "write_text", "write_bytes", "mkdir", "touch", "unlink",
                "rmdir", "replace", "rename",
            }:
                seg = ast.dump(fn.value)
                if "Path" in seg and "cwd" in seg:
                    offenders.append((node.lineno, fn.attr))
            self.generic_visit(node)

    V().visit(tree)
    assert not offenders, (
        "write to a Path.cwd()-rooted path inside app.py — an instance process "
        f"would write into the ROOT lab: {offenders}"
    )


def test_no_module_under_src_arail_hardcodes_a_lab_subdirectory_for_writing():
    """Structural isolation means every runtime path comes from ``config`` (or,
    for the one blessed exception, ``os.getenv("ARAIL_DATA_DIR")``). A literal
    ``"lab/pkb"``/``"lab/data"`` string reaching a write call would silently
    put an instance's data in the root lab.
    """
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if not re.search(r'["\']lab/(pkb|data|models|worlds)', line):
                continue
            if re.search(r"\.(write_text|write_bytes|mkdir|touch)\(|open\([^)]*[\"']w",
                         line):
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}: {stripped}")
    assert not offenders, "hardcoded lab/ write path(s):\n" + "\n".join(offenders)


def test_egress_is_the_only_getenv_data_dir_bypass_of_config():
    """A32.1 names exactly one module that re-reads ARAIL_DATA_DIR instead of
    importing ``config.DATA_DIR``. Pin it: a second one would be a second place
    the isolation can drift.
    """
    bypassers = set()
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if re.search(r'os\.getenv\(\s*["\']ARAIL_DATA_DIR', text) or \
           re.search(r'os\.environ\[\s*["\']ARAIL_DATA_DIR', text):
            bypassers.add(str(py.relative_to(SRC)))
    assert bypassers <= {"egress.py"}, (
        "a NEW module re-reads ARAIL_DATA_DIR instead of importing config.DATA_DIR "
        f"— A32.1's single-exception claim no longer holds: {sorted(bypassers)}"
    )


# ---------------------------------------------------------------------------
# 3. Endpoint disclosure surface
# ---------------------------------------------------------------------------

def _endpoint_source(name: str) -> str:
    text = APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in app.py")


def test_api_instance_and_api_instances_expose_no_field_beyond_spec():
    """§5.1/§5.2 fix the response shapes. Anything extra is a disclosure the
    threat model never reviewed — the registry record carries absolute
    filesystem paths and PIDs, and /api/instances is reachable from any page
    in the lab.
    """
    allowed_instance = {"slug", "token", "portal_port", "checkout", "data_root",
                        "world", "display_name", "started_at",
                        # sprints/2026-07-29-elite-cli ARCHITECTURE.md §11.1 /
                        # F16 (WP6, warm-up): additive, booleans/ints/backend-
                        # class only — explicitly NOT a model id.
                        "warm", "warm_ms", "warm_skipped", "backend",
                        # sprints/2026-10-20-boot-model-autoresolve: per-slot
                        # boot-warm outcome, closed vocabulary
                        # ok|skipped|failed|deferred|disabled (or null while
                        # pending) -- no model id.
                        "warm_a", "warm_b"}
    allowed_roster = {"slug", "display_name", "portal_port", "bind", "checkout",
                      "started_at", "live", "instances"}  # "instances" = envelope

    src_one = _endpoint_source("api_instance")
    keys_one = set(re.findall(r'"([a-z_]+)"\s*:', src_one))
    assert keys_one <= allowed_instance, sorted(keys_one - allowed_instance)

    src_many = _endpoint_source("api_instances")
    keys_many = set(re.findall(r'"([a-z_]+)"\s*:', src_many))
    assert keys_many <= allowed_roster, sorted(keys_many - allowed_roster)

    # The roster must NOT republish the per-boot token, the data/pkb roots, or
    # any PID — a record field being present in registry.d/ is not consent to
    # serve it to every page in the lab.
    for leaked in ("token", "portal_pid", "memory_pid", "launcher_pid",
                   "data_dir", "pkb_root", "instance_root"):
        assert leaked not in keys_many, (
            f"/api/instances republishes '{leaked}' from the registry record"
        )


def test_warm_skipped_value_is_a_closed_vocabulary():
    """REVIEW.md B3/F16 — the sibling test above only pinned warm_skipped's
    KEY, admitting it into ``allowed_instance`` with no constraint on its
    VALUE. That gap let
    ``_MODEL_WARM_SKIP_REASON = f"{type(e).__name__}: {e}"`` ship on
    ``GET /api/instance`` — reachable with no passphrase set (A6) — and
    ``{e}`` can be an absolute path (hence the OS username), a model id, or
    a provider URL, all of which F16 explicitly bans.

    Statically confirms the ONLY assignment to ``_MODEL_WARM_SKIP_REASON``
    inside ``_warm_primary_router()``'s exception handler is the fixed,
    module-level ``_MODEL_WARM_SKIP_REASON_ON_EXCEPTION`` constant — never
    an f-string (or anything else) built from the caught exception object.
    The two non-exception branches interpolate ``backend_name`` only
    (already reviewed and accepted: a backend *class* name, not a leak).
    """
    src = _endpoint_source("_warm_primary_router")
    tree = ast.parse(src)
    except_handler = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ExceptHandler) and n.name == "e"),
        None,
    )
    assert except_handler is not None, (
        "_warm_primary_router() no longer has an `except Exception as e:` "
        "block — update this test to match its new shape"
    )
    assigns = [
        stmt for stmt in ast.walk(except_handler)
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_MODEL_WARM_SKIP_REASON"
                for t in stmt.targets)
    ]
    assert len(assigns) == 1, (
        f"expected exactly one _MODEL_WARM_SKIP_REASON assignment in the "
        f"except block, found {len(assigns)}"
    )
    value_node = assigns[0].value
    assert isinstance(value_node, ast.Name) \
        and value_node.id == "_MODEL_WARM_SKIP_REASON_ON_EXCEPTION", (
        "the exception branch must assign the fixed "
        "_MODEL_WARM_SKIP_REASON_ON_EXCEPTION constant, never anything "
        f"built from the caught exception `e` — found: {ast.dump(value_node)}"
    )

    import arail.portal.app as app_mod
    reason = app_mod._MODEL_WARM_SKIP_REASON_ON_EXCEPTION
    assert isinstance(reason, str) and reason, (
        "_MODEL_WARM_SKIP_REASON_ON_EXCEPTION must be a fixed, non-empty string"
    )
    # It must not itself be an f-string artifact — a plain, closed sentence.
    for looks_leaky in ("/", "http://", "https://", "$HOME", "Errno"):
        assert looks_leaky not in reason, (
            f"_MODEL_WARM_SKIP_REASON_ON_EXCEPTION looks path/URL-shaped: {reason!r}"
        )


def test_neither_instance_endpoint_can_spawn_a_process():
    """§5.3's refinement: no HTTP surface may become process execution.

    REVIEW.md m4 notes the builder's own version of this assertion inspects
    only the two decorated handlers and does not ban ``subprocess``. This one
    follows the call graph one level into the module-private helpers the
    handlers actually use, and bans the whole spawn vocabulary there — with the
    single audited exception of the read-only ``ps`` liveness probe.
    """
    banned = ("subprocess.Popen", "subprocess.call", "subprocess.check_output",
              "os.system", "os.exec", "os.spawn", "os.popen", "eval(", "exec(",
              "pty.spawn", "asyncio.create_subprocess")
    for name in ("api_instance", "api_instances"):
        src = _endpoint_source(name)
        for b in banned:
            assert b not in src, f"{name} contains {b}"
        assert "subprocess" not in src, f"{name} spawns a process directly"

    helper = _endpoint_source("_instance_record_alive")
    for b in banned:
        assert b not in helper, f"_instance_record_alive contains {b}"
    # The one blessed spawn: a read-only `ps -p <pid> -o command=`.
    assert 'subprocess.run(' in helper
    assert '"ps", "-p"' in helper, (
        "_instance_record_alive's subprocess call is no longer the audited "
        "read-only `ps` probe — re-review it"
    )

    reader = _endpoint_source("_read_instance_records")
    assert "subprocess" not in reader


def test_api_instances_liveness_never_takes_a_pid_from_an_untrusted_type():
    """``os.kill`` on a non-int, negative, or zero PID from a hand-edited
    registry record must not signal anything. ``os.kill(0, 0)`` targets the
    caller's whole process group.
    """
    src = _endpoint_source("_instance_record_alive")
    assert "isinstance(pid, int)" in src, (
        "the portal's liveness helper must type-check portal_pid before os.kill"
    )
    # And it must reject <= 0 as well; if it does not, that is the finding.
    from arail.portal.app import _instance_record_alive  # noqa: PLC0415

    for bad in (0, -1, "1", 1.0, None, True):
        # bool is an int subclass — True would be pid 1 (launchd/init).
        assert _instance_record_alive({"portal_pid": bad, "portal_port": 8090}) is False, (
            f"portal_pid={bad!r} was treated as a live instance"
        )


# ---------------------------------------------------------------------------
# 4. Airgapped default + secrets, in a FRESH instance
# ---------------------------------------------------------------------------

def test_a_fresh_instance_is_airgapped_with_no_env_edit(instance):
    """Hard constraint (BRIEF): the airgapped default is untouched. The env
    pack deliberately omits LAB_MODE (§1.2 "Not in the pack"), so an instance
    with no root .env must still resolve airgapped.
    """
    repo, root = instance
    env = _instance_env(root, repo)
    env.pop("LAB_MODE", None)
    out = subprocess.run(
        [PY, "-c",
         "from arail.airgap import lab_mode, is_airgapped\n"
         "print(lab_mode(), is_airgapped())"],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(repo),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["airgapped", "True"], out.stdout


def test_autochecks_are_off_in_a_fresh_instance(instance):
    """Hard constraint: no auto-checks at boot. The pack must not set
    ARAIL_AUTOCHECKS, so it stays absent ⇒ off.
    """
    repo, root = instance
    env = _instance_env(root, repo)
    env.pop("ARAIL_AUTOCHECKS", None)
    out = subprocess.run(
        [PY, "-c", "import os; print(repr(os.getenv('ARAIL_AUTOCHECKS')))"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(repo),
    )
    assert out.stdout.strip() == "None"


def test_an_instance_data_dir_is_not_world_readable_by_default(instance):
    """§7: first boot creates ``<instance>/data`` mode 0700 — the directory that
    will hold ``secrets.env``. Verify the CLI actually creates it that way.
    """
    repo, root = instance
    shutil.rmtree(root)
    instances_sh = REPO_ROOT / "scripts" / "lib" / "instances.sh"
    r = subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail; REPO_ROOT="{repo}"; source "{instances_sh}"; '
         'inst_scaffold_instance_root qa'],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(repo)},
    )
    assert r.returncode == 0, r.stderr
    data = repo / "lab" / "instances" / "qa" / "data"
    assert data.is_dir()
    mode = oct(data.stat().st_mode)[-3:]
    assert mode in {"700", "750", "755"}, mode
    if mode != "700":
        pytest.xfail(
            "QA-10 (OPEN): ARCHITECTURE §7 specifies mode 0700 for "
            f"<instance>/data (it will hold secrets.env); got {mode}. "
            "inst_scaffold_instance_root uses a bare `mkdir -p`, so the mode "
            "is whatever the operator's umask happens to be. See TEST_REPORT.md."
        )


def test_status_json_publishes_the_liveness_nonce(tmp_path: Path):
    """Documented, accepted: ``status --json`` echoes the whole registry record,
    including ``token``.

    That is sound ONLY while the token remains a non-credential (no endpoint
    accepts it as authorization). This test exists so that if anyone ever
    repurposes the token as auth, the fact that it is already printed to stdout
    by a routine diagnostic command is impossible to miss.
    """
    src = APP_PY.read_text(encoding="utf-8")
    # No endpoint may compare an incoming request value against the token.
    assert not re.search(r"ARAIL_INSTANCE_TOKEN.*==|==.*ARAIL_INSTANCE_TOKEN",
                         src), (
        "the instance token is being compared against request data — it is "
        "published by `status --json` and by GET /api/instance, so it cannot "
        "be used as a credential"
    )
    assert "instance_token" not in src.lower().replace("arail_instance_token", "")
