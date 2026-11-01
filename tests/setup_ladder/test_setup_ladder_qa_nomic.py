"""QA (2026-08-08-arail2-tier1-integration): the clean-machine story for
``nomic-embed-text``.

arail is cloned and run by friends and family, so ``setup.sh`` gaining a
274 MB model pull is the highest-risk part of this sprint for the 30%
setup-on-a-clean-machine allocation. The promise (C5) is: warn and
continue, never fail setup, always print the exact command to run later,
and never let the lab come up pretending it has embeddings when it does
not.

Every external command is stubbed by ``conftest.py`` — nothing here
downloads a byte.
"""
from __future__ import annotations


def test_nomic_pull_failure_never_fails_setup(ladder):
    """Offline machine: the pull fails. Setup must still exit 0."""
    r = ladder(preview_pull_ok="0")
    assert r.ladder_exit == 0, "an offline embedding pull must not abort setup"
    assert r.called("PULL nomic-embed-text")


def test_nomic_pull_failure_prints_the_exact_recovery_command(ladder):
    """A warning nobody can act on is not a warning. The output must carry a
    copy-pasteable command and say what degrades until it is run."""
    r = ladder(preview_pull_ok="0")
    assert "ollama pull nomic-embed-text" in r.output
    assert "semantic search" in r.output.lower()


def test_nomic_pull_failure_does_not_stop_the_default_model_from_installing(ladder):
    """The lab must still be usable for chat when only the embedding pull
    failed — the two ladders are independent."""
    r = ladder(preview_pull_ok="0", llama_pull_ok="1")
    assert r.called("PULL llama3.2:1b")
    assert r.called("CREATE create llama-ai-eng")
    assert r.ladder_exit == 0


def test_default_model_failure_does_not_stop_the_embedding_pull(ladder):
    """…and the other direction."""
    r = ladder(llama_pull_ok="0", preview_pull_ok="1")
    assert r.called("PULL nomic-embed-text")
    assert r.ladder_exit == 0


def test_skip_model_download_also_skips_the_embedding_pull(ladder):
    """``ARAIL_SKIP_MODEL_DOWNLOAD=1`` is the documented slow/offline
    escape hatch. A new pull must inherit it rather than surprising the
    operator with 274 MB."""
    r = ladder(env={"ARAIL_SKIP_MODEL_DOWNLOAD": "1"})
    assert r.ladder_exit == 0
    assert not r.called("PULL nomic-embed-text")


def test_skip_ollama_also_skips_the_embedding_pull(ladder):
    r = ladder(env={"ARAIL_SKIP_OLLAMA": "1"})
    assert r.ladder_exit == 0
    assert not r.called("PULL nomic-embed-text")


def test_embedding_pull_is_tier_independent(ladder):
    """PKB search is a minimalist feature, so the embedding model must not
    be gated behind the maximus tier."""
    for tier in ("minimalist", "maximus"):
        r = ladder(env={"LAB_TIER": tier})
        assert r.called("PULL nomic-embed-text"), f"missing on {tier}"


def test_embedding_pull_runs_at_most_once(ladder):
    """A duplicated pull would double the clean-machine wait for no gain."""
    r = ladder()
    assert r.calls("PULL nomic-embed-text") == 1
