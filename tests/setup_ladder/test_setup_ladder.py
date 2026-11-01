"""Setup-ladder mock tests for install_models().

MODEL-TIERS-V2 (2026-05-31): the default path is now a simple persona-wrap:
  ollama pull llama3.2:1b → ollama create llama-ai-eng -f Modelfile.default

The self-hosted GGUF fetch ladder is dormant behind ARAIL_AI_ENG_SELFHOSTED=1.
Tests are split accordingly:
  - Default persona-wrap path (the shipping default)
  - Dormant self-hosted ladder (ARAIL_AI_ENG_SELFHOSTED=1)
  - Legacy alias migration (ai-engineer:latest → adopt as deep, not as default)
  - Idempotency, skip, offline

All external commands are stubbed (see conftest); no download, no model load,
no real ollama. OOM-safe by construction.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# A. DEFAULT PATH — persona-wrap (llama3.2:1b + llama-ai-eng)
# ---------------------------------------------------------------------------

def test_default_path_pulls_llama_1b_and_creates_llama_ai_eng(ladder):
    """Default path: pulls llama3.2:1b, creates llama-ai-eng. No HF ladder."""
    r = ladder()
    assert r.ladder_exit == 0
    assert r.called("PULL llama3.2:1b"), "must pull llama3.2:1b as the default base"
    assert r.called("CREATE create llama-ai-eng"), "must create llama-ai-eng persona"
    # Must NOT run the self-hosted ladder
    assert not r.called("PULL hf.co/"), "default path must not invoke the HF ladder"
    assert not r.called("CURL"), "default path must not curl any mirror"


def test_default_path_does_not_pull_qwen_7b(ladder):
    """Default path must not pull the 7B deep model."""
    r = ladder()
    assert r.ladder_exit == 0
    assert not r.called("PULL qwen2.5:7b"), "default must not pull the 7B deep model"


def test_default_path_offline_warns_and_continues(ladder):
    """When llama3.2:1b pull fails (offline), setup warns and continues (exit 0)."""
    r = ladder(llama_pull_ok="0")
    assert r.ladder_exit == 0, "setup must not abort on offline pull failure"
    assert not r.called("CREATE create llama-ai-eng"), \
        "must not create persona when pull failed"
    # Must print a manual command hint
    assert "llama3.2:1b" in r.output, "manual recovery command must mention llama3.2:1b"


def test_llama_pull_ok_but_create_fails_warns_and_continues(ladder):
    """Pull succeeds, create fails → warns, setup continues (exit 0)."""
    r = ladder(llama_pull_ok="1", create_ok="0")
    assert r.ladder_exit == 0
    assert r.called("PULL llama3.2:1b")
    assert r.called("CREATE create llama-ai-eng")


# ---------------------------------------------------------------------------
# B. IDEMPOTENCY
# ---------------------------------------------------------------------------

def test_llama_ai_eng_already_installed_is_idempotent_skip(ladder):
    """llama-ai-eng already present → no persona pull, no curl, clean skip.

    nomic-embed-text (C5, arail2-tier1-integration) is a separate,
    independent pull in the same ladder and is NOT present in this test's
    $QA_INSTALLED, so it is expected to still attempt a pull — scope the
    assertion to the ai-eng persona models specifically.
    """
    r = ladder(installed="llama-ai-eng")
    assert r.ladder_exit == 0
    assert not r.called("PULL llama3.2:1b"), "llama-ai-eng present → skip"
    assert not r.called("PULL qwen2.5:7b")
    assert not r.called("CURL")


def test_ai_eng_already_installed_is_idempotent_skip(ladder):
    """Legacy ai-eng present → no persona pull, clean skip (back-compat
    idempotency). See test_llama_ai_eng_already_installed_is_idempotent_skip
    for why the nomic-embed-text pull is excluded from this assertion."""
    r = ladder(installed="ai-eng")
    assert r.ladder_exit == 0
    assert not r.called("PULL llama3.2:1b")
    assert not r.called("PULL qwen2.5:7b")
    assert not r.called("CURL")


def test_nomic_embed_text_pulled_when_absent(ladder):
    """C5: nomic-embed-text is pulled independently of which ai-eng branch
    ran, following the llama3.2:1b warn-and-continue pattern."""
    r = ladder(installed="llama-ai-eng")
    assert r.ladder_exit == 0
    assert r.called("PULL nomic-embed-text")


def test_nomic_embed_text_already_installed_is_idempotent_skip(ladder):
    r = ladder(installed="llama-ai-eng nomic-embed-text")
    assert r.ladder_exit == 0
    assert not r.called("PULL nomic-embed-text")


# ---------------------------------------------------------------------------
# C. LEGACY ALIAS MIGRATION
# ---------------------------------------------------------------------------

def test_legacy_ai_engineer_not_aliased_to_1b_default(ladder):
    """Legacy ai-engineer:latest (7B) is NOT aliased to llama-ai-eng (1B).

    MODEL-TIERS-V2: the 7B is adopted as the DEEP model; the 1B default is
    installed fresh from llama3.2:1b. This prevents the v1 footgun of making
    the '1B default' secretly a 7B via ollama cp.
    """
    r = ladder(installed="ai-engineer:latest")
    assert r.ladder_exit == 0
    # Must NOT alias the 7B to the 1B default name
    assert not r.called("CP cp ai-engineer:latest llama-ai-eng"), (
        "setup must not alias the 7B ai-engineer to llama-ai-eng (size mislabeling)"
    )
    # Must install the 1B default separately
    assert r.called("PULL llama3.2:1b"), (
        "when ai-engineer:latest exists, must still install the 1B default"
    )


def test_legacy_ai_engineer_aliased_not_repulled(ladder):
    """Legacy ai-engineer:latest present → 1B default still installed; no alias to 1B name."""
    r = ladder(installed="ai-engineer:latest", cp_ok="1")
    assert r.ladder_exit == 0
    # No aliasing of 7B → llama-ai-eng (that's the mislabel guard)
    assert not r.called("CP cp ai-engineer:latest llama-ai-eng")
    # The 1B default IS pulled (since llama-ai-eng wasn't present)
    assert r.called("PULL llama3.2:1b")


# ---------------------------------------------------------------------------
# D. SKIP_OLLAMA
# ---------------------------------------------------------------------------

def test_skip_ollama_makes_zero_pull_attempts(ladder):
    """ARAIL_SKIP_OLLAMA=1 → graceful skip, zero pulls, zero curls."""
    r = ladder(env={"ARAIL_SKIP_OLLAMA": "1"})
    assert r.ladder_exit == 0
    assert not r.called("PULL"), "skip must make no pull attempts"
    assert not r.called("CURL")


# ---------------------------------------------------------------------------
# E. DORMANT SELF-HOSTED GGUF LADDER (ARAIL_AI_ENG_SELFHOSTED=1)
# ---------------------------------------------------------------------------

def test_selfhosted_flag_activates_hf_ladder(ladder):
    """ARAIL_AI_ENG_SELFHOSTED=1 → HF-primary pull attempted."""
    r = ladder(hf_ok="1", env={"ARAIL_AI_ENG_SELFHOSTED": "1"})
    assert r.ladder_exit == 0
    assert r.called("PULL hf.co/"), "selfhosted flag must invoke the HF pull"


def test_hf_primary_success_single_pull_no_create_no_mirror(ladder):
    """HF pull succeeds → single `ollama pull hf.co/...`, no create, no curl."""
    r = ladder(hf_ok="1", env={"ARAIL_AI_ENG_SELFHOSTED": "1"})
    assert r.ladder_exit == 0
    assert r.calls("PULL hf.co/") == 1, "expected exactly one HF pull"
    assert not r.called("CURL"), "HF success must not touch any mirror curl"
    assert not r.called("CREATE create ai-eng"), "HF native pull must not run ollama create"
    assert not r.called("PULL qwen"), "must not pull the preview base on success"


def test_hf_primary_success_respects_env_repo_and_quant(ladder):
    """Operator env overrides flow into the HF pull reference."""
    r = ladder(hf_ok="1", env={
        "ARAIL_AI_ENG_SELFHOSTED": "1",
        "ARAIL_AI_ENG_HF_REPO": "myorg/myrepo",
        "ARAIL_AI_ENG_QUANT": "Q8_0",
    })
    assert r.ladder_exit == 0
    assert r.called("PULL hf.co/myorg/myrepo:Q8_0")


def test_hf_404_then_github_mirror_digest_match_creates(ladder):
    """HF fails, mirror download verifies, ollama create runs from local gguf."""
    pinned = "a" * 64
    r = ladder(hf_ok="0", curl_ok="1", fake_sha=pinned,
               env={"ARAIL_AI_ENG_SELFHOSTED": "1",
                    "ARAIL_AI_ENG_SHA256": pinned})
    assert r.ladder_exit == 0
    assert r.calls("PULL hf.co/") == 1, "HF attempted first"
    assert r.called("CURL"), "mirror download attempted after HF 404"
    assert r.called("CREATE create ai-eng"), "verified blob must reach ollama create"
    assert not r.called("PULL qwen"), "preview net must NOT run after mirror success"


def test_mirror_digest_mismatch_fails_closed_no_create(ladder):
    """Downloaded blob whose sha != pinned digest is discarded; never created."""
    pinned = "a" * 64
    r = ladder(hf_ok="0", curl_ok="1", fake_sha="b" * 64,
               preview_pull_ok="1", env={"ARAIL_AI_ENG_SELFHOSTED": "1",
                                         "ARAIL_AI_ENG_SHA256": pinned})
    assert r.ladder_exit == 0
    assert r.called("CURL"), "mirror download attempted"
    assert "sha256 mismatch" in r.output.lower() or "discard" in r.output.lower()
    # Falls through to preview net
    assert r.called("PULL qwen")


def test_placeholder_digest_disables_mirror_no_download(ladder):
    """A placeholder sha must skip the mirror WITHOUT downloading (fail-closed)."""
    r = ladder(hf_ok="0", curl_ok="1", preview_pull_ok="1",
               env={"ARAIL_AI_ENG_SELFHOSTED": "1",
                    "ARAIL_AI_ENG_SHA256": "__PLACEHOLDER_SHA256__"})
    assert r.ladder_exit == 0
    assert not r.called("CURL"), (
        "placeholder digest must disable the mirror BEFORE any curl download"
    )
    assert "placeholder" in r.output.lower()
    assert r.called("PULL qwen")


def test_all_hosts_fail_falls_to_preview_net(ladder):
    """HF 404 + placeholder sha → preview net (qwen2.5:1.5b)."""
    r = ladder(hf_ok="0", curl_ok="0", preview_pull_ok="1",
               env={"ARAIL_AI_ENG_SELFHOSTED": "1"})
    assert r.ladder_exit == 0
    assert r.called("PULL qwen2.5:1.5b"), "preview net pulls the 1.5B base"
    assert r.called("CREATE create ai-eng"), "preview Modelfile builds ai-eng"


def test_preview_net_narrative_has_no_qwen_marketing(ladder):
    """The self-hosted ladder's info-level HF pull line must not mention qwen.

    The preview net fallback warns that artifacts are not reachable (neutral)
    and the recovery warn may mention qwen2.5:1.5b (class-c internal ref, not
    info-level marketing). Assert the 'Fetching ai-eng' info line is qwen-free.
    """
    r = ladder(hf_ok="0", preview_pull_ok="1",
               env={"ARAIL_AI_ENG_SELFHOSTED": "1"})
    # The neutral info line says "Fetching ai-eng (self-hosted HuggingFace primary)"
    info_lines = [ln for ln in r.output.splitlines()
                  if "[arail]" in ln and "Fetching ai-eng" in ln]
    assert info_lines, "expected a neutral 'Fetching ai-eng' info line"
    for ln in info_lines:
        assert "qwen" not in ln.lower(), f"info line must not mention qwen: {ln}"


def test_total_failure_exits_clean_and_prints_manual_command(ladder):
    """Everything fails (offline) → ladder returns 0, prints a recovery command."""
    r = ladder(hf_ok="0", curl_ok="0", preview_pull_ok="0",
               env={"ARAIL_AI_ENG_SELFHOSTED": "1"})
    assert r.ladder_exit == 0, "setup must NOT abort on total ai-eng failure"
    # The manual command may be the preview fallback (qwen1.5b) or the HF URL
    has_recovery = (
        "ollama pull hf.co/" in r.output
        or "ollama pull qwen2.5" in r.output
        or "Run manually" in r.output
    )
    assert has_recovery, "must print some manual recovery command"


def test_cdn_skipped_when_url_empty(ladder):
    """Empty CDN url (default) → only HF + GitHub curl, never a third curl."""
    pinned = "a" * 64
    r = ladder(hf_ok="0", curl_ok="0", fake_sha=pinned,
               env={"ARAIL_AI_ENG_SELFHOSTED": "1",
                    "ARAIL_AI_ENG_SHA256": pinned})
    assert r.calls("CURL") == 1, "empty CDN url must not trigger a second mirror curl"


def test_cdn_fires_when_url_set(ladder):
    """A set CDN url adds a second mirror attempt after GitHub."""
    pinned = "a" * 64
    r = ladder(hf_ok="0", curl_ok="0", fake_sha=pinned,
               env={"ARAIL_AI_ENG_SELFHOSTED": "1",
                    "ARAIL_AI_ENG_SHA256": pinned,
                    "ARAIL_AI_ENG_CDN_URL": "https://cdn.example.com/ai-eng.gguf"})
    assert r.calls("CURL") == 2, "GitHub + CDN = two mirror attempts"


# ---------------------------------------------------------------------------
# E2. MAXIMUS DEEP-PERSONA OFFER (Surface A — ai-engineer 7B, MODEL-TIERS-V2 §3)
#     Behavioral coverage of the offer/gating logic. OOM-safe: the qwen2.5:7b
#     "pull" is the stubbed ollama (no bytes). These are the tests for item 3
#     (deep model gating) that previously had only source-grep coverage.
# ---------------------------------------------------------------------------

def test_minimalist_default_does_not_offer_or_pull_deep_7b(ladder):
    """On the default minimalist tier with no deep flag, the 7B is neither
    auto-pulled nor force-installed. (The 1B default is the only auto-install.)
    """
    r = ladder()  # default tier = minimalist, no ARAIL_INSTALL_DEEP_PERSONA
    assert r.ladder_exit == 0
    assert not r.called("PULL qwen2.5:7b"), (
        "minimalist default must never auto-pull the 7B deep model"
    )
    assert not r.called("CREATE create ai-engineer"), (
        "minimalist default must never create the deep ai-engineer persona"
    )


def test_maximus_deep_offer_is_command_not_autopull_in_source():
    """The maximus deep-persona block must OFFER a manual command and gate the
    actual pull behind ARAIL_INSTALL_DEEP_PERSONA=1 — NOT auto-pull on tier
    alone (honors the OOM-sensitivity rule: no surprise 4.7 GB pull).

    Source-level guard: the harness can't set LAB_TIER (setup.sh resets it to ""
    at top level; the tier is only captured inside main(), which the harness
    skips). So the offer-only branch is asserted against the source structure.
    """
    import pathlib
    setup = (pathlib.Path(__file__).resolve().parents[2]
             / "scripts" / "setup.sh").read_text()
    # The auto-pull of the 7B must be inside an ARAIL_INSTALL_DEEP_PERSONA gate.
    lines = setup.splitlines()
    pull_idx = next(i for i, l in enumerate(lines)
                    if "ollama pull qwen2.5:7b 2>&1" in l)
    # Walk backward to the nearest controlling conditional.
    preceding = "\n".join(lines[max(0, pull_idx - 8):pull_idx])
    assert "ARAIL_INSTALL_DEEP_PERSONA" in preceding, (
        "the 7B auto-pull must be gated behind ARAIL_INSTALL_DEEP_PERSONA"
    )
    # The else branch must print the manual offer command.
    assert "ollama pull qwen2.5:7b && ollama create ai-engineer" in setup, (
        "maximus must print the manual deep-persona install command (offer)"
    )
    assert "set ARAIL_INSTALL_DEEP_PERSONA=1" in setup, (
        "the offer must tell the user how to enable auto-install"
    )


def test_deep_persona_flag_auto_installs_7b_via_modelfile_deep(ladder):
    """ARAIL_INSTALL_DEEP_PERSONA=1 → setup pulls qwen2.5:7b and creates the
    ai-engineer persona from Modelfile.deep (the explicit opt-in)."""
    r = ladder(env={"ARAIL_INSTALL_DEEP_PERSONA": "1"})
    assert r.ladder_exit == 0
    assert r.called("PULL qwen2.5:7b"), (
        "ARAIL_INSTALL_DEEP_PERSONA=1 must pull the 7B base"
    )
    assert r.called("CREATE create ai-engineer"), (
        "ARAIL_INSTALL_DEEP_PERSONA=1 must create the ai-engineer deep persona"
    )
    # Crucially, the deep create must use Modelfile.deep (7B), NOT the 1B default.
    deep_creates = [ln for ln in r.calllog.splitlines()
                    if "CREATE create ai-engineer" in ln]
    assert deep_creates, "expected a deep ai-engineer create call"
    assert any("Modelfile.deep" in ln for ln in deep_creates), (
        "deep persona must be built from Modelfile.deep, not Modelfile.default"
    )


def test_deep_persona_auto_install_still_installs_1b_default(ladder):
    """The deep-persona opt-in must NOT replace the 1B default — both install.
    Regression guard against the deep flag cannibalizing the everyday default.
    """
    r = ladder(env={"ARAIL_INSTALL_DEEP_PERSONA": "1"})
    assert r.ladder_exit == 0
    assert r.called("PULL llama3.2:1b"), "1B default must still install"
    assert r.called("CREATE create llama-ai-eng"), "1B persona must still be created"
    assert r.called("PULL qwen2.5:7b"), "deep 7B installs alongside, not instead"


def test_existing_ai_engineer_not_repulled_when_deep_requested(ladder):
    """If ai-engineer:latest already exists, the deep block adopts it — no
    re-pull of the 4.7 GB 7B even when the deep persona is explicitly requested.
    (Driven via the flag because the harness can't set LAB_TIER=maximus.)
    """
    r = ladder(installed="ai-engineer:latest",
               env={"ARAIL_INSTALL_DEEP_PERSONA": "1"})
    assert r.ladder_exit == 0
    assert not r.called("PULL qwen2.5:7b"), (
        "existing ai-engineer:latest must be adopted, not re-pulled"
    )


def test_deep_7b_pull_failure_is_graceful(ladder):
    """If the 7B pull fails (offline), setup warns and continues (exit 0),
    and prints the manual recovery command. No abort, no partial-state crash."""
    r = ladder(preview_pull_ok="0", env={"ARAIL_INSTALL_DEEP_PERSONA": "1"})
    assert r.ladder_exit == 0, "deep pull failure must not abort setup"
    assert r.called("PULL qwen2.5:7b")
    assert "ollama pull qwen2.5:7b" in r.output, "manual recovery command printed"


# ---------------------------------------------------------------------------
# F. GENERIC SAFETY GUARDS
# ---------------------------------------------------------------------------

def test_total_failure_does_not_pull_any_heavy_deep_model(ladder):
    """No failure path may pull a 70B/deep model or the sentinel."""
    r = ladder(hf_ok="0", curl_ok="0", preview_pull_ok="0",
               llama_pull_ok="0")
    assert not r.called("PULL hf.co/meta-llama")
    assert not r.called("__TODO_DEEP_MODEL__")
    assert not r.called("70B")
    assert not r.called("405B")
