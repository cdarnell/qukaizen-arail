"""arail.nucleus.cards.certified_models (T-LEDGER-1..4)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from arail.nucleus.cards import certified_models as cm
from arail.nucleus.errors import RefusedByPolicy


@dataclass
class _FakeVerify:
    key: str = "trusted"
    signature: str = "valid"
    card_hash: str = "match"
    eval_hash: str = "match"
    chain: str = "match"

    @property
    def all_ok(self):
        return (self.signature == "valid" and self.key == "trusted" and
               self.card_hash == "match" and self.eval_hash in ("match", "skipped") and
               self.chain in ("match", "skipped"))


def _card(**overrides):
    card = {"shard": "qkz-x", "version": "0.1.0", "runtime": "queuellm", "signed": {"k": "v"},
           "fidelity": {"decision": "CERTIFIED", "achieved": 0.8}, "built": "2026-01-01T00:00:00+00:00"}
    card.update(overrides)
    return card


# ── T-LEDGER-1: default append goes to the local ledger; docs untouched ──

def test_default_append_writes_local_ledger_only(tmp_path):
    docs_path = tmp_path / "docs" / "CERTIFIED_MODELS.md"
    path = cm.append(_card(), verify_result=_FakeVerify(), nucleus_data=tmp_path / "nucleus")
    assert path == tmp_path / "nucleus" / "CERTIFIED_SHARDS.md"
    assert path.is_file()
    assert not docs_path.exists()


# ── T-LEDGER-2: --publish-row: bytes outside markers identical, rows untouched ──

def test_publish_row_preserves_bytes_outside_markers(tmp_path):
    docs_path = tmp_path / "CERTIFIED_MODELS.md"
    docs_path.write_text("# Some doc\n\nUnrelated content.\n")
    before_prefix = docs_path.read_text()

    cm.append(_card(shard="qkz-a"), publish_row=True, verify_result=_FakeVerify(), docs_path=docs_path)

    after = docs_path.read_text()
    assert after.startswith(before_prefix.rstrip("\n"))
    assert cm.BEGIN_MARKER in after and cm.END_MARKER in after


def test_publish_row_second_call_does_not_touch_first_row(tmp_path):
    docs_path = tmp_path / "CERTIFIED_MODELS.md"
    cm.append(_card(shard="qkz-a", version="0.1.0"), publish_row=True, verify_result=_FakeVerify(),
             docs_path=docs_path)
    text_after_first = docs_path.read_text()
    cm.append(_card(shard="qkz-b", version="0.1.0"), publish_row=True, verify_result=_FakeVerify(),
             docs_path=docs_path)
    text_after_second = docs_path.read_text()
    assert "qkz-a" in text_after_second
    assert "qkz-b" in text_after_second
    # the qkz-a row line is byte-identical between the two writes
    a_row_line = next(ln for ln in text_after_first.splitlines() if "qkz-a" in ln)
    assert a_row_line in text_after_second.splitlines()


# ── T-LEDGER-3: re-certify same version -> upsert, no duplicate; stub refused ──

def test_upsert_same_shard_version_no_duplicate(tmp_path):
    cm.append(_card(shard="qkz-a", version="0.1.0"), verify_result=_FakeVerify(), nucleus_data=tmp_path)
    path = cm.append(_card(shard="qkz-a", version="0.1.0", fidelity={"decision": "BETA", "achieved": 0.5},
                           built="2026-02-01T00:00:00+00:00"),
                     verify_result=_FakeVerify(), nucleus_data=tmp_path)
    text = path.read_text()
    assert text.count("qkz-a") == 1
    assert "BETA" in text  # the row was updated, not duplicated


def test_stub_card_refused(tmp_path):
    with pytest.raises(RefusedByPolicy):
        cm.append(_card(runtime="stub"), verify_result=_FakeVerify(), nucleus_data=tmp_path)


def test_unsigned_card_refused(tmp_path):
    with pytest.raises(RefusedByPolicy):
        cm.append(_card(signed=None), verify_result=_FakeVerify(), nucleus_data=tmp_path)


def test_untrusted_key_refused(tmp_path):
    with pytest.raises(RefusedByPolicy):
        cm.append(_card(), verify_result=_FakeVerify(key="untrusted"), nucleus_data=tmp_path)


def test_not_all_green_refused(tmp_path):
    with pytest.raises(RefusedByPolicy):
        cm.append(_card(), verify_result=_FakeVerify(card_hash="mismatch"), nucleus_data=tmp_path)


# ── T-LEDGER-4: markdown/HTML injection escaped ───────────────────────

def test_pipe_and_script_and_newline_escaped(tmp_path):
    card = _card(shard="qkz-a|<script>evil</script>\ninjected")
    path = cm.append(card, verify_result=_FakeVerify(), nucleus_data=tmp_path)
    text = path.read_text()
    assert "<script>" not in text
    assert "\ninjected" not in text  # newline collapsed, no extra row created
    assert text.count("\n|") <= 3  # header + separator + one data row, no injected row
