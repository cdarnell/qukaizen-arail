"""arail.nucleus.providers.base — shape sanity (Caps default, immutability)."""

from __future__ import annotations

from arail.nucleus.providers.base import Caps, Decoding, Generation, Prompt, TopNGeneration


def test_caps_defaults():
    caps = Caps()
    assert caps.generate is True
    assert caps.logprobs_topn is False
    assert caps.max_top_n == 0


def test_dataclasses_are_frozen():
    prompt = Prompt(item_id="a", text="hi")
    try:
        prompt.text = "changed"  # type: ignore[misc]
        assert False, "Prompt should be frozen"
    except Exception:
        pass
