"""QA-6: fail-closed reads, the empty-state contract, bootstrap on odd roots,
and the three perf thresholds ARCHITECTURE.md left unmeasured.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod


def _mk(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "pkb"
    r.mkdir()
    _mk(r, "notes/a.md", "chlorophyll converts light")
    _mk(r, "notes/secret.md", "SECRET-TOKEN-99")
    return r


# ── F1: every shape of a broken manifest reads as "nothing approved" ─────

CORRUPT = {
    "truncated": '{"schema": "arail.compiled-kb/v1", "items": {"notes/a.md"',
    "bare_string": '"x"',
    "list_of_strings": '["notes/a.md", "notes/secret.md"]',
    "null": "null",
    "empty_file": "",
    "not_json": "\x00\x01binary garbage",
    "number": "42",
    "bool": "true",
    "items_is_a_string": '{"schema": "x", "items": "notes/secret.md"}',
    "items_is_a_list": '{"schema": "x", "items": ["notes/secret.md"]}',
    "deep_nesting": '{"items": ' + '[' * 200 + ']' * 200 + '}',
    "huge_key": '{"items": {"' + "a" * 100000 + '": {}}}',
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_f1_corrupt_manifest_never_reads_as_everything_approved(root, name):
    _mk(root, "compiled/kb/approved.json", CORRUPT[name])
    approved = ckb.approved_paths(root)
    assert "notes/secret.md" not in approved
    assert pkb_mod.search_for_agents("SECRET-TOKEN-99", root) == []
    out = pkb_mod.retrieve_for_agents("SECRET-TOKEN-99", root)
    assert out["hits"] == []
    # a well-formed-but-junk manifest (e.g. one absurd key) legitimately
    # yields "no_match": the gate is non-empty, it just points at nothing.
    # Either way agents get zero, which is the fail-closed requirement.
    assert out["empty_reason"] in ("gate_empty", "no_match")
    if name not in ("huge_key",):
        assert out["empty_reason"] == "gate_empty"
    # a list-shaped manifest parses, so manifest_present() is True by
    # contract; what matters is that it yields no approvals either way
    if name != "huge_key":
        assert ckb.gate_state(root)["state"] in ("unbootstrapped", "empty")
        assert ckb.gate_state(root)["live_count"] == 0
    else:
        # QA finding (low): a 100k-char manifest key makes (root/rel).is_file()
        # raise ENAMETOOLONG, dangling_paths() swallows it and returns [] for
        # the WHOLE root, so gate_state reports "populated" with nothing live.
        # Retrieval is still zero (asserted above) — the defect is honesty of
        # the state label and loss of prune coverage, not a widened gate.
        assert ckb.gate_state(root)["state"] == "populated"
        assert ckb.dangling_paths(root) == []


def test_f1_partially_written_manifest_is_ignored(root):
    """A crash between write and replace leaves approved.json.tmp. Nothing
    may read it, and it must not become a candidate for approval."""
    ckb.approve(["notes/a.md"], root)
    (root / "compiled/kb/approved.json.tmp").write_text(
        json.dumps({"items": {"notes/secret.md": {"path": "notes/secret.md"}}}))
    assert ckb.approved_paths(root) == {"notes/a.md"}
    assert "compiled/kb/approved.json.tmp" not in {
        c["path"] for c in ckb.list_pending(root)}


def test_f2_unreadable_compiled_kb_dir_reads_as_nothing_approved(root):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    ckb.approve(["notes/a.md"], root)
    kb = root / "compiled" / "kb"
    kb.chmod(0o000)
    try:
        assert ckb.approved_paths(root) == set()
        assert ckb.manifest_present(root) is False
        assert pkb_mod.search_for_agents("chlorophyll", root) == []
        st = ckb.gate_state(root, cheap=True)
        assert st["state"] == "unbootstrapped" and st["approved_count"] == 0
    finally:
        kb.chmod(0o700)


def test_manifest_write_is_atomic_under_a_crashing_replace(root, monkeypatch):
    """Verify the tmp+replace atomicity claim rather than trusting it: if the
    replace dies, the PREVIOUS manifest must survive intact."""
    ckb.approve(["notes/a.md"], root)
    before = (root / "compiled/kb/approved.json").read_text()

    real_replace = pathlib.Path.replace

    def _boom(self, target):
        if str(self).endswith("approved.json.tmp"):
            raise OSError("crash mid-write")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", _boom)
    with pytest.raises(OSError):
        ckb.approve(["notes/secret.md"], root)
    monkeypatch.undo()

    assert (root / "compiled/kb/approved.json").read_text() == before
    assert ckb.approved_paths(root) == {"notes/a.md"}


def test_f4_no_code_path_returns_a_superset_of_the_manifest(root):
    """Static-ish guard: approved_paths is exactly the manifest's keys, for
    every manifest shape we can construct."""
    ckb.approve(["notes/a.md"], root)
    raw = json.loads((root / "compiled/kb/approved.json").read_text())
    assert ckb.approved_paths(root) == set(raw["items"])
    src = pathlib.Path("src/arail/compiled_kb.py").read_text()
    body = src[src.index("def approved_paths"):src.index("def rejected_paths")]
    assert "return set()" in body and "_approved_map" in body
    # no branch that falls through to "everything"
    assert "rglob" not in body and "iterdir" not in body


# ── manifest_present / gate_state unit contract ──────────────────────────

def test_manifest_present_matrix(root):
    kb = root / "compiled" / "kb"
    assert ckb.manifest_present(root) is False              # missing
    kb.mkdir(parents=True)
    (kb / "approved.json").write_text("")
    assert ckb.manifest_present(root) is False              # empty file
    (kb / "approved.json").write_text("{}")
    assert ckb.manifest_present(root) is True               # {}
    (kb / "approved.json").write_text('{"items": {}}')
    assert ckb.manifest_present(root) is True
    (kb / "approved.json").write_text("[]")
    assert ckb.manifest_present(root) is True               # list shape
    (kb / "approved.json").write_text("{oops")
    assert ckb.manifest_present(root) is False              # corrupt


def test_gate_state_all_four_states(root, monkeypatch):
    assert ckb.gate_state(root)["state"] == "unbootstrapped"

    ckb.bootstrap(root)
    st = ckb.gate_state(root)
    assert st["state"] == "empty" and st["manifest_present"] is True

    ckb.approve(["notes/a.md"], root)
    assert ckb.gate_state(root)["state"] == "populated"

    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    st = ckb.gate_state(root)
    assert st["state"] == "off" and st["enabled"] is False
    assert "ARAIL_APPROVED_ONLY=off" in st["hint"]


def test_gate_state_dangling_approvals_read_as_empty_not_populated(root):
    ckb.approve(["notes/a.md"], root)
    (root / "notes/a.md").unlink()
    st = ckb.gate_state(root)
    assert st["approved_count"] == 1 and st["live_count"] == 0
    assert st["state"] == "empty"


def test_gate_state_never_raises_on_total_failure(monkeypatch):
    monkeypatch.setattr(ckb, "manifest_present",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    st = ckb.gate_state(pathlib.Path("/nonexistent/root"))
    assert st["state"] == "unbootstrapped"
    assert set(st) == {"schema", "enabled", "manifest_present", "approved_count",
                       "live_count", "pending_count", "state", "hint"}


def test_gate_state_cheap_does_not_walk_the_tree(root, monkeypatch):
    monkeypatch.setattr(ckb, "pending_count",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("cheap=True walked the tree")))
    st = ckb.gate_state(root, cheap=True)
    assert st["pending_count"] == -1


def test_gate_state_on_a_missing_root_is_well_formed(tmp_path):
    st = ckb.gate_state(tmp_path / "nope")
    assert st["state"] == "unbootstrapped" and st["live_count"] == 0


# ── retrieve_for_agents contract ─────────────────────────────────────────

def test_retrieve_four_empty_reasons(root, monkeypatch):
    # gate_empty: nothing approved
    assert pkb_mod.retrieve_for_agents("chlorophyll", root)["empty_reason"] == "gate_empty"
    # None: approved and matching
    ckb.approve(["notes/a.md"], root)
    assert pkb_mod.retrieve_for_agents("chlorophyll", root)["empty_reason"] is None
    # no_match: approved but no hit
    assert pkb_mod.retrieve_for_agents("zzzz-nope", root)["empty_reason"] == "no_match"
    # gate_off_no_match
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    assert pkb_mod.retrieve_for_agents("zzzz-nope", root)["empty_reason"] == "gate_off_no_match"


def test_retrieve_internal_error_fails_closed_and_loud(root, monkeypatch):
    ckb.approve(["notes/a.md"], root)
    monkeypatch.setattr(pkb_mod, "search",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = pkb_mod.retrieve_for_agents("chlorophyll", root)
    assert out["hits"] == [] and out["empty_reason"] == "gate_empty"
    assert out["gate"]["schema"] == "arail.kb-gate/v1"


def test_search_for_agents_shape_is_byte_identical_to_hits(root):
    ckb.approve(["notes/a.md"], root)
    assert (pkb_mod.search_for_agents("chlorophyll", root)
            == pkb_mod.retrieve_for_agents("chlorophyll", root)["hits"])


@pytest.mark.parametrize("q", ["", "   ", "\x00", "a" * 100000, "🔥", "..", "*"])
def test_retrieve_survives_hostile_queries(root, q):
    ckb.approve(["notes/a.md"], root)
    out = pkb_mod.retrieve_for_agents(q, root)
    assert isinstance(out["hits"], list)
    assert out["empty_reason"] in (None, "no_match", "gate_empty")


# ── bootstrap on odd roots ───────────────────────────────────────────────

def _catalog(tmp_path, slug, terms):
    worlds = tmp_path / "worlds"
    (worlds / slug).mkdir(parents=True)
    (worlds / slug / "terms.json").write_text(json.dumps({"version": 1, "terms": terms}))
    return worlds


def test_bootstrap_fresh_lab_with_no_worlds_writes_present_empty_manifest(tmp_path):
    r = tmp_path / "pkb"
    r.mkdir()
    res = ckb.bootstrap(r)
    assert res["world"] is None and res["approved"] == 0
    assert ckb.manifest_present(r) is True
    assert ckb.gate_state(r)["state"] == "empty"


def test_bootstrap_on_a_root_that_does_not_exist(tmp_path):
    res = ckb.bootstrap(tmp_path / "absent")
    assert res["approved"] == 0 and res["skipped_reason"]
    assert not (tmp_path / "absent").exists()


def test_bootstrap_content_without_catalog_bundle_sets_skipped_reason(
        tmp_path, monkeypatch):
    import arail.config as cfg
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(tmp_path / "worlds"))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-orphan/terms/x.md", "orphan term")
    res = ckb.bootstrap(r)
    assert res["world"] == "orphan"
    assert res["approved"] == 0
    assert "no bundle in catalog" in (res["skipped_reason"] or "")
    assert ckb.approved_paths(r) == set()
    assert ckb.manifest_present(r) is True   # still honest about being empty


def test_bootstrap_dry_run_writes_nothing(tmp_path, monkeypatch):
    import arail.config as cfg
    terms = [{"slug": "alpha"}, {"slug": "beta"}]
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(_catalog(tmp_path, "w", terms)))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-w/terms/alpha.md", "a")
    _mk(r, "sources/world-w/terms/beta.md", "b")
    res = ckb.bootstrap(r, dry_run=True)
    assert res["dry_run"] is True and res["approved"] == 2
    assert ckb.manifest_present(r) is False
    assert not (r / "compiled").exists()


def test_bootstrap_stamps_world_terms_not_world_seal(tmp_path, monkeypatch):
    import arail.config as cfg
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(_catalog(tmp_path, "w", [{"slug": "alpha"}])))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-w/terms/alpha.md", "a")
    ckb.bootstrap(r)
    rec = ckb.list_approved(r)[0]
    assert rec["approved_by"].startswith("world-terms:")
    assert not rec["approved_by"].startswith("world-seal:")
    assert rec["auto"] is True


def test_bootstrap_is_idempotent_and_does_not_duplicate(tmp_path, monkeypatch):
    import arail.config as cfg
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(_catalog(tmp_path, "w", [{"slug": "alpha"}])))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-w/terms/alpha.md", "a")
    ckb.bootstrap(r)
    first = json.loads((r / "compiled/kb/approved.json").read_text())["items"]
    ckb.bootstrap(r)
    second = json.loads((r / "compiled/kb/approved.json").read_text())["items"]
    assert set(first) == set(second) == {"sources/world-w/terms/alpha.md"}


def test_bootstrap_self_heals_a_corrupt_manifest_without_widening(tmp_path, monkeypatch):
    import arail.config as cfg
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(_catalog(tmp_path, "w", [{"slug": "alpha"}])))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-w/terms/alpha.md", "a")
    _mk(r, "notes/secret.md", "SECRET-TOKEN-99")
    _mk(r, "compiled/kb/approved.json", "{corrupt")
    ckb.bootstrap(r)
    assert ckb.approved_paths(r) == {"sources/world-w/terms/alpha.md"}
    assert pkb_mod.search_for_agents("SECRET-TOKEN-99", r) == []


def test_bootstrap_with_a_malformed_terms_json_never_raises(tmp_path, monkeypatch):
    import arail.config as cfg
    worlds = tmp_path / "worlds"
    (worlds / "w").mkdir(parents=True)
    (worlds / "w" / "terms.json").write_text("{not json")
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(worlds))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-w/terms/alpha.md", "a")
    res = ckb.bootstrap(r)
    assert res["approved"] == 0 and res["skipped_reason"]
    assert ckb.approved_paths(r) == set()


def test_bootstrap_multiple_staged_worlds_picks_one_and_scopes_to_it(
        tmp_path, monkeypatch):
    """INFO from review: bootstrap takes the FIRST sources/world-* dir. Pin
    the behavior so a change is deliberate — and prove it never approves
    across both."""
    import arail.config as cfg
    worlds = _catalog(tmp_path, "aaa", [{"slug": "alpha"}])
    (worlds / "zzz").mkdir()
    (worlds / "zzz" / "terms.json").write_text(
        json.dumps({"terms": [{"slug": "zeta"}]}))
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(worlds))
    r = tmp_path / "pkb"
    _mk(r, "sources/world-aaa/terms/alpha.md", "a")
    _mk(r, "sources/world-zzz/terms/zeta.md", "z")
    ckb.bootstrap(r)
    assert ckb.approved_paths(r) == {"sources/world-aaa/terms/alpha.md"}


# ── Performance (ARCHITECTURE.md § Performance) ──────────────────────────

@pytest.fixture()
def big_root(tmp_path):
    """351 approved term pages — the shape of the real `ai` World."""
    r = tmp_path / "pkb"
    slugs = [f"term-{i:04d}" for i in range(351)]
    for s in slugs:
        _mk(r, f"sources/world-ai/terms/{s}.md", f"# {s}\nbody text for {s}\n")
    for i in range(200):  # unapproved candidates, to make the walk real
        _mk(r, f"notes/n{i}.md", "note body")
    ckb.approve([f"sources/world-ai/terms/{s}.md" for s in slugs], r)
    return r


def test_perf_gate_state_cheap_under_5ms(big_root):
    ckb.gate_state(big_root, cheap=True)  # warm
    best = min(_timed(lambda: ckb.gate_state(big_root, cheap=True)) for _ in range(7))
    assert best < 0.005, f"gate_state(cheap=True) took {best*1000:.2f} ms"


def test_perf_bootstrap_under_3s(big_root, tmp_path, monkeypatch):
    import arail.config as cfg
    terms = [{"slug": f"term-{i:04d}"} for i in range(351)]
    monkeypatch.setattr(cfg, "WORLDS_DIR", str(_catalog(tmp_path, "ai", terms)))
    took = _timed(lambda: ckb.bootstrap(big_root))
    assert ckb.gate_state(big_root)["live_count"] == 351
    assert took < 3.0, f"bootstrap took {took:.2f}s"


def test_perf_mount_regression_under_10pct(tmp_path, monkeypatch):
    """Baseline = mount with the hook disabled via the env escape hatch;
    current = mount with it on. Median of 5 each."""
    from arail.world_mount import mount
    fixtures = pathlib.Path(__file__).parent / "fixtures" / "world-bundles" / "physics"

    def _once(enabled: bool) -> float:
        d = tmp_path / f"run-{enabled}-{time.time_ns()}"
        (d / "data").mkdir(parents=True)
        os.environ["ARAIL_AUTO_APPROVE_WORLD_TERMS"] = "on" if enabled else "off"
        return _timed(lambda: mount(fixtures, pkb_root=d / "pkb", data_dir=d / "data"))

    try:
        base = sorted(_once(False) for _ in range(5))[2]
        cur = sorted(_once(True) for _ in range(5))[2]
    finally:
        os.environ.pop("ARAIL_AUTO_APPROVE_WORLD_TERMS", None)
    # generous absolute floor so a fast machine's noise cannot fail this
    assert cur < base * 1.10 + 0.01, f"mount {base:.4f}s -> {cur:.4f}s"


def _timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t
