"""QA-6 security pass: the Compiled-KB gate must not widen.

ARCHITECTURE.md S1-S5 plus the attacks QA ran against the scope invariant
itself rather than trusting round 1. The load-bearing assertion throughout is
not "the path is unapproved" but "the search RAN and still did not surface the
token" (``empty_reason == "no_match"``) — the first proves the gate was shut,
the second proves the gate is shut *around a working search*.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from arail import compiled_kb as ckb
from arail import pkb as pkb_mod

TOKEN = "ACCT-XYZ-4417"


def _mk(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", raising=False)
    monkeypatch.delenv("ARAIL_APPROVED_ONLY", raising=False)


DEBT_TERMS = [
    {"slug": "apr", "term": "APR"},
    {"slug": "401k-loan", "term": "401(k) loan"},
    {"slug": "debt-consolidation", "term": "Debt consolidation"},
]

# Every place a human's personal financial material actually lands, per
# ARCHITECTURE.md § "On debt-finance specifically".
PLANTED = {
    "notes/personal-balances.md": f"# Balances\nchase card {TOKEN} balance 8,412.09\n",
    "inbox/statement.md": f"statement for account {TOKEN}\n",
    "conversations/c1/transcript.jsonl": json.dumps(
        {"role": "user", "text": f"my account is {TOKEN}"}) + "\n",
    "agents/research/2026-01-01_debt_report.md": f"the operator's {TOKEN} appears here\n",
    "agents/dreams/2026-01-02_dream.md": f"dreamt about {TOKEN}\n",
    "sources/scout/finding-1.md": f"scouted {TOKEN}\n",
    "sources/seeds/seed-1.md": f"seed {TOKEN}\n",
}


@pytest.fixture()
def debt_root(tmp_path):
    """A debt-finance-shaped PKB root: public glossary terms plus personal
    material in every non-terms location."""
    root = tmp_path / "pkb"
    root.mkdir()
    for t in DEBT_TERMS:
        _mk(root, f"sources/world-debt-finance/terms/{t['slug']}.md",
            f"---\ntitle: {t['term']}\n---\n\n"
            f"{t['term']} is public domain vocabulary. Source: irs.gov\n")
    for rel, text in PLANTED.items():
        _mk(root, rel, text)
    return root


def _bootstrap_like(root):
    return ckb.auto_approve_world_terms(
        "debt-finance", bundle_terms=DEBT_TERMS, seal_sha="a" * 64,
        pkb_root=root, verified_seal=False)


# ── S1: the named case ───────────────────────────────────────────────────

def test_s1_planted_personal_token_is_neither_approved_nor_retrievable(debt_root):
    _bootstrap_like(debt_root)
    approved = ckb.approved_paths(debt_root)

    # (a) every approved path is a debt-finance term page
    assert approved and all(
        p.startswith("sources/world-debt-finance/terms/") and p.endswith(".md")
        for p in approved)
    # (b) no planted path is approved
    assert approved.isdisjoint(PLANTED)
    # (e) every approved slug is in the bundle's terms
    bundle_slugs = {t["slug"] for t in DEBT_TERMS}
    assert {p.rsplit("/", 1)[-1][:-3] for p in approved} <= bundle_slugs

    # (c) agents get nothing for a query that matches the token verbatim
    assert pkb_mod.search_for_agents(TOKEN, debt_root) == []
    # (d) THE load-bearing one: the search ran (gate non-empty) and still
    # did not surface it. "gate_empty" here would mean we only proved the
    # gate was shut, not that retrieval is correctly scoped.
    out = pkb_mod.retrieve_for_agents(TOKEN, debt_root)
    assert out["hits"] == []
    assert out["empty_reason"] == "no_match", out
    assert out["gate"]["state"] == "populated"


@pytest.mark.parametrize("rel", sorted(PLANTED))
def test_s1_each_planted_surface_stays_gated(debt_root, rel):
    """inbox/, conversations/, agents/**, scout/, seeds/ get the same
    treatment as notes/ — one test per surface so a failure names it."""
    _bootstrap_like(debt_root)
    assert rel not in ckb.approved_paths(debt_root)
    raw = {r["path"] for r in pkb_mod.search(TOKEN, debt_root)}
    if rel.endswith(".jsonl"):
        # Conversation transcripts are not even in _PKB_TEXT_SUFFIXES, so the
        # raw browse cannot see them either. Belt AND braces — assert that
        # rather than skipping, so a future suffix change trips this test.
        assert rel not in raw
    else:
        # the raw corpus does contain it — proving the search would have found
        # it if the gate were the only thing standing in the way
        assert rel in raw
    assert pkb_mod.retrieve_for_agents(TOKEN, debt_root)["empty_reason"] == "no_match"


def test_s1_term_pages_themselves_are_retrievable(debt_root):
    """The converse: the gate is not simply returning [] for everything."""
    _bootstrap_like(debt_root)
    out = pkb_mod.retrieve_for_agents("public domain vocabulary", debt_root)
    assert out["empty_reason"] is None
    assert {h["path"] for h in out["hits"]} == ckb.approved_paths(debt_root)


# ── Attacking the scope invariant directly ───────────────────────────────

@pytest.mark.parametrize("evil", [
    "../../../notes/personal-balances",
    "/etc/passwd",
    "../../notes/personal-balances.md",
    "sources/world-x/terms/../../../notes/personal-balances",
    "..",
    ".",
    "./../inbox/statement",
    "%2e%2e%2fnotes%2fpersonal-balances",
    "notes/personal-balances.md\x00",
])
def test_s3_traversal_slugs_approve_exactly_the_bundle_terms_and_nothing_else(
        debt_root, evil):
    """Exact-set assertion, not the round-1 ``== set() or all(...)`` which
    passed under both outcomes (REVIEW.md ASK-6)."""
    terms = DEBT_TERMS + [{"slug": evil, "term": "evil"}]
    ckb.auto_approve_world_terms(
        "debt-finance", bundle_terms=terms, seal_sha="a" * 64, pkb_root=debt_root)
    assert ckb.approved_paths(debt_root) == {
        f"sources/world-debt-finance/terms/{t['slug']}.md" for t in DEBT_TERMS}


@pytest.mark.parametrize("slug", ["../../../notes/personal-balances", "/etc/passwd"])
def test_s3_traversal_slug_does_not_reach_a_real_file_even_if_one_exists(
        debt_root, slug):
    """Sanitization collapses the traversal to a flat name; plant a file at
    the collapsed name too and confirm the *escape* still failed — the only
    thing reachable is inside terms/."""
    collapsed = ckb._safe_term_slug(slug)
    _mk(debt_root, f"sources/world-debt-finance/terms/{collapsed}.md", "planted")
    ckb.auto_approve_world_terms(
        "debt-finance", bundle_terms=[{"slug": slug}], seal_sha="a" * 64,
        pkb_root=debt_root)
    approved = ckb.approved_paths(debt_root)
    assert approved == {f"sources/world-debt-finance/terms/{collapsed}.md"}
    # containment is what matters: nothing outside the World's terms dir
    assert all(p.startswith("sources/world-debt-finance/terms/") for p in approved)


@pytest.mark.parametrize("world_slug", [
    "../root", "debt/../..", "..", "/abs", "Debt-Finance", "debt finance",
    "debt-finance\x00", "", "-leading",
])
def test_hostile_world_slug_is_rejected_outright(debt_root, world_slug):
    added = ckb.auto_approve_world_terms(
        world_slug, bundle_terms=DEBT_TERMS, seal_sha="a" * 64, pkb_root=debt_root)
    assert added == []
    assert ckb.approved_paths(debt_root) == set()


def test_symlink_in_terms_dir_pointing_at_notes(debt_root):
    """ASK-4: a symlink under terms/ whose name is in terms.json. Document
    the actual behavior; the security question is whether the *content* of
    notes/ becomes agent-reachable."""
    link = debt_root / "sources/world-debt-finance/terms/leaked.md"
    try:
        link.symlink_to(debt_root / "notes/personal-balances.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    ckb.auto_approve_world_terms(
        "debt-finance", bundle_terms=DEBT_TERMS + [{"slug": "leaked"}],
        seal_sha="a" * 64, pkb_root=debt_root)
    approved = ckb.approved_paths(debt_root)
    if "sources/world-debt-finance/terms/leaked.md" in approved:
        # Pre-existing surface (REVIEW ASK-4). It requires local write access
        # inside the staged dir, which a swap wipes. Pin the blast radius:
        # the token becomes reachable ONLY through the symlink path, never
        # through notes/ itself.
        hits = {h["path"] for h in pkb_mod.retrieve_for_agents(TOKEN, debt_root)["hits"]}
        assert "notes/personal-balances.md" not in hits
        pytest.xfail("ASK-4: symlink under terms/ is approved and hashed through")
    assert "sources/world-debt-finance/terms/leaked.md" not in approved


def test_slug_collision_across_sanitization_approves_one_file_once(debt_root):
    _mk(debt_root, "sources/world-debt-finance/terms/rate-cap.md", "x")
    added = ckb.auto_approve_world_terms(
        "debt-finance",
        bundle_terms=[{"slug": "rate cap"}, {"slug": "Rate/Cap"}, {"slug": "rate--cap"}],
        seal_sha="a" * 64, pkb_root=debt_root)
    assert len(added) == 1
    assert ckb.approved_paths(debt_root) == {
        "sources/world-debt-finance/terms/rate-cap.md"}


def test_unicode_nfc_vs_nfd_slugs_do_not_widen_scope(debt_root):
    """NFC "caf\u00e9" and NFD "cafe\u0301" sanitize to DIFFERENT names
    ("caf" vs "cafe") — the sanitizer is not normalization-aware. That is
    safe only because the page writer (world_mount) and the approver
    (compiled_kb) sanitize the *same* terms.json string with the *same*
    function. Assert exactly that, and that neither form escapes terms/."""
    from arail import world_mount
    nfc, nfd = "caf\u00e9", "cafe\u0301"
    for form in (nfc, nfd):
        assert ckb._safe_term_slug(form) == world_mount._safe_term_slug(form)
    assert ckb._safe_term_slug(nfc) == "caf"
    assert ckb._safe_term_slug(nfd) == "cafe"

    _mk(debt_root, "sources/world-debt-finance/terms/caf.md", "unicode term")
    for form in (nfc, nfd):
        ckb.auto_approve_world_terms(
            "debt-finance", bundle_terms=[{"slug": form}], seal_sha="a" * 64,
            pkb_root=debt_root)
    # the NFD form finds no staged page at all — fails closed, never wider
    assert ckb.approved_paths(debt_root) == {
        "sources/world-debt-finance/terms/caf.md"}


def test_slug_sanitizer_parity_with_world_mount_and_world_corpus():
    """A one-character divergence between the writer's sanitizer and the
    approver's silently un-approves an entire World (REVIEW round-2 tech
    debt). Assert parity over adversarial inputs rather than by eye."""
    from arail import world_mount
    samples = ["apr", "401(k) Loan", "  spaced  ", "CAPS", "café",
               "a" * 200, "../../etc/passwd", "", "---", "9lives",
               "emoji-\U0001f600", "tab\tsep", "semi;colon", "back\\slash"]
    wm = getattr(world_mount, "_safe_term_slug", None)
    assert wm is not None, "world_mount._safe_term_slug vanished — parity untestable"
    for s in samples:
        assert ckb._safe_term_slug(s) == wm(s), s
    try:
        from arail.world_catalog import _safe_term_slug as wc
    except Exception:  # pragma: no cover - optional import
        wc = None
    if wc is not None:
        for s in samples:
            assert ckb._safe_term_slug(s) == wc(s), s


def test_terms_json_slug_that_is_not_a_string(debt_root):
    for bad in ({"slug": None}, {"slug": 12}, {"slug": ["a"]}, {"slug": {"a": 1}},
                {"noslug": "apr"}, "notadict", None, 42):
        ckb.auto_approve_world_terms(
            "debt-finance", bundle_terms=[bad], seal_sha="a" * 64, pkb_root=debt_root)
    assert ckb.approved_paths(debt_root) == set()


def test_case_insensitive_fs_mismatch_fails_closed(debt_root):
    """macOS: bundle slug 'apr', staged page 'APR.md'. Approval may succeed
    on a case-insensitive FS, but retrieval must not surface a file under a
    name the operator did not approve."""
    p = debt_root / "sources/world-debt-finance/terms/apr.md"
    p.unlink()
    _mk(debt_root, "sources/world-debt-finance/terms/APR.md", "uppercase page")
    ckb.auto_approve_world_terms(
        "debt-finance", bundle_terms=[{"slug": "apr"}], seal_sha="a" * 64,
        pkb_root=debt_root)
    hits = {h["path"] for h in pkb_mod.retrieve_for_agents("uppercase page", debt_root)["hits"]}
    # either nothing (case-sensitive FS or closed mismatch) or the exact
    # approved rel — never a path the manifest does not contain.
    assert hits <= ckb.approved_paths(debt_root)


# ── S2 / S4 / S5 escape hatches ──────────────────────────────────────────

def test_s2_hand_dropped_file_in_terms_dir_never_approved(debt_root):
    _mk(debt_root, "sources/world-debt-finance/terms/planted-by-agent.md",
        f"agent wrote {TOKEN}")
    _bootstrap_like(debt_root)
    assert ("sources/world-debt-finance/terms/planted-by-agent.md"
            not in ckb.approved_paths(debt_root))
    assert pkb_mod.retrieve_for_agents(TOKEN, debt_root)["empty_reason"] == "no_match"


def test_s4_sentinel_present_disables_auto_approval(debt_root):
    kb = debt_root / "compiled" / "kb"
    kb.mkdir(parents=True)
    (kb / "no-auto-approve").write_text("")
    assert _bootstrap_like(debt_root) == []
    assert ckb.approved_paths(debt_root) == set()


def test_s4_sentinel_check_raising_oserror_is_treated_as_disabled(
        debt_root, monkeypatch):
    """The real contract behind ASK-6's weak test: exercise it by making the
    existence check itself raise, which chmod 000 cannot do as root/POSIX."""
    real_exists = pathlib.Path.exists

    def _exists(self, *a, **k):
        if self.name == "no-auto-approve":
            raise OSError("unreadable")
        return real_exists(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "exists", _exists)
    assert _bootstrap_like(debt_root) == []
    assert ckb.approved_paths(debt_root) == set()


@pytest.mark.parametrize("val", ["off", "OFF", "0", "false", "no", " off "])
def test_s5_env_off_disables_hook_but_not_the_gate(debt_root, monkeypatch, val):
    monkeypatch.setenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", val)
    assert _bootstrap_like(debt_root) == []
    assert ckb.approved_paths(debt_root) == set()
    assert pkb_mod.retrieve_for_agents(TOKEN, debt_root)["empty_reason"] == "gate_empty"


def test_gate_off_is_the_only_way_the_raw_corpus_is_reachable(debt_root, monkeypatch):
    """Negative control: prove the planted token IS reachable when the
    operator explicitly disables the gate. If this ever stops being the
    only path, the fail-closed claim is untestable."""
    monkeypatch.setenv("ARAIL_APPROVED_ONLY", "off")
    out = pkb_mod.retrieve_for_agents(TOKEN, debt_root)
    assert any(h["path"] == "notes/personal-balances.md" for h in out["hits"])
    assert out["gate"]["state"] == "off"
