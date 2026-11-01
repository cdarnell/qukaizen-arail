"""R4 — catalog back-compat regression test.

Sprint: 2026-05-18-provider-aware-chat-dropdown, Phase A step 3 (F-CATALOG).

Verifies:
1. Legacy YAML rows (no provider/ctx fields) still load and as_dict() works.
2. Rows WITH provider/ctx fields survive as_dict() — not silently dropped.
3. Existing test_chat_model_sync.py behavior unaffected (covered by running it).
"""

from __future__ import annotations

import pytest

from arail.chat import CatalogEntry, load_catalog


# ---------------------------------------------------------------------------
# R4a — legacy row (no new fields) still loads
# ---------------------------------------------------------------------------

def test_catalog_entry_legacy_row_loads():
    """A CatalogEntry created without provider/ctx fields works fine."""
    e = CatalogEntry(
        id="qwen2.5:7b",
        name="Qwen 2.5 7B",
        family="qwen",
        size_gb=4.7,
        released="2024-09",
        source="ollama",
        good_at=["chat"],
        description="test",
        install="ollama pull qwen2.5:7b",
        tier="recommended",
    )
    d = e.as_dict()
    assert d["id"] == "qwen2.5:7b"
    assert d["provider"] is None
    assert d["ctx"] is None


def test_catalog_entry_legacy_row_provider_defaults_none():
    """Legacy rows default provider to None, not a missing key."""
    e = CatalogEntry(
        id="ai-eng:latest",
        name="AI Engineer",
        family="qukaizen",
        size_gb=2.0,
        released="2026-05",
        source="ollama",
        good_at=["chat"],
        description="test",
        install="",
        tier="recommended",
    )
    d = e.as_dict()
    assert "provider" in d
    assert d["provider"] is None


def test_catalog_entry_legacy_row_ctx_defaults_none():
    """Legacy rows default ctx to None, not a missing key."""
    e = CatalogEntry(
        id="ai-eng:latest",
        name="AI Engineer",
        family="qukaizen",
        size_gb=2.0,
        released="2026-05",
        source="ollama",
        good_at=["chat"],
        description="test",
        install="",
        tier="recommended",
    )
    d = e.as_dict()
    assert "ctx" in d
    assert d["ctx"] is None


# ---------------------------------------------------------------------------
# R4b — cloud row WITH provider/ctx survives as_dict()
# ---------------------------------------------------------------------------

def test_catalog_entry_cloud_row_provider_survives_as_dict():
    """F-CATALOG: provider field must appear in as_dict() output."""
    e = CatalogEntry(
        id="claude-opus-4-7",
        name="Claude Opus 4.7",
        family="claude",
        size_gb=0,
        released="",
        source="cloud",
        good_at=[],
        description="",
        install="",
        tier="flagship",
        provider="claude",
        ctx="200K tokens",
    )
    d = e.as_dict()
    assert d["provider"] == "claude"
    assert d["ctx"] == "200K tokens"


def test_catalog_entry_cloud_row_all_known_keys_present():
    """as_dict() must include all fields (no accidental omission)."""
    e = CatalogEntry(
        id="grok-3",
        name="Grok 3",
        family="xai",
        size_gb=0,
        released="",
        source="cloud",
        good_at=["chat", "code"],
        description="xAI flagship",
        install="",
        tier="flagship",
        provider="xai",
        ctx="131072 tokens",
    )
    d = e.as_dict()
    expected_keys = {
        "id", "name", "family", "size_gb", "released", "source",
        "good_at", "description", "install", "tier", "provider", "ctx",
    }
    assert expected_keys.issubset(d.keys()), f"Missing keys: {expected_keys - d.keys()}"


# ---------------------------------------------------------------------------
# R4c — load_catalog() still loads when catalog has no cloud rows
# ---------------------------------------------------------------------------

def test_load_catalog_returns_list():
    """load_catalog() returns a list (may be empty in test env)."""
    result = load_catalog()
    assert isinstance(result, list)


def test_load_catalog_all_entries_have_provider_key():
    """Every loaded entry has provider in its as_dict() output."""
    for entry in load_catalog():
        d = entry.as_dict()
        assert "provider" in d, f"Entry {entry.id!r} missing 'provider' in as_dict()"
        assert "ctx" in d, f"Entry {entry.id!r} missing 'ctx' in as_dict()"


# ---------------------------------------------------------------------------
# hf_repo — structured HuggingFace repo id for source: hf|mlx rows, added
# so the boot model-selection banner can link straight to huggingface.co
# instead of parsing a repo id out of a free-text `install` shell command.
# ---------------------------------------------------------------------------

def test_catalog_entry_hf_repo_defaults_to_empty_string():
    """Legacy/ollama rows (no hf_repo in YAML) must default to "" — never
    a missing key, and never None (as_dict() distinguishes hf_repo="" from
    hf_url=None deliberately: hf_repo is always a string)."""
    e = CatalogEntry(
        id="qwen2.5:7b", name="Qwen 2.5 7B", family="qwen", size_gb=4.7,
        released="2024-09", source="ollama", good_at=["chat"],
        description="test", install="ollama pull qwen2.5:7b", tier="recommended",
    )
    d = e.as_dict()
    assert d["hf_repo"] == ""
    assert d["hf_url"] is None


def test_catalog_entry_hf_repo_derives_hf_url():
    e = CatalogEntry(
        id="olmoe-test", name="OLMoE test", family="olmoe", size_gb=4.0,
        released="2025-01", source="mlx", good_at=["chat"], description="test",
        install="hf download mlx-community/OLMoE-1B-7B-0125-Instruct-4bit",
        tier="optional", hf_repo="mlx-community/OLMoE-1B-7B-0125-Instruct-4bit",
    )
    d = e.as_dict()
    assert d["hf_repo"] == "mlx-community/OLMoE-1B-7B-0125-Instruct-4bit"
    assert d["hf_url"] == "https://huggingface.co/mlx-community/OLMoE-1B-7B-0125-Instruct-4bit"


def test_every_hf_or_mlx_catalog_row_carries_a_matching_hf_repo():
    """hf_repo isn't optional decoration for source: hf|mlx rows with a
    real install command — it's how the boot banner derives an HF link
    instead of parsing one out of a shell command. The one exception is
    the __TODO_DEEP_MODEL__ operator-configured placeholder, which has no
    real repo and an empty install command."""
    for entry in load_catalog():
        if entry.source not in ("hf", "mlx"):
            continue
        if entry.id.startswith("__"):
            continue
        assert entry.hf_repo, (
            f"catalog entry {entry.id!r} (source={entry.source!r}) has no "
            "hf_repo — the boot banner can't derive an HF link for it")
        assert entry.hf_repo in entry.install, (
            f"catalog entry {entry.id!r}: hf_repo {entry.hf_repo!r} does not "
            f"appear in its own install command {entry.install!r} — they've "
            "drifted apart")


def test_ollama_sourced_rows_never_carry_hf_repo():
    """An ollama-sourced row has no HF download path — hf_repo must stay
    unset so the banner never shows a nonsensical HF link next to an
    `ollama pull` command."""
    for entry in load_catalog():
        if entry.source == "ollama":
            assert entry.hf_repo == "", (
                f"ollama-sourced entry {entry.id!r} unexpectedly carries "
                f"hf_repo={entry.hf_repo!r}")
