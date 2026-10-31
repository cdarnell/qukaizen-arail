"""arail.nucleus.providers.queuellm — worker-side adapter."""

from __future__ import annotations

import json
import sys
import types

import pytest

from arail.nucleus.providers.base import Decoding, Prompt
from arail.nucleus.runtime_names import PY_MODULE


class _FakeRuntimeWritesLogprobs:
    def __init__(self, *a, **kw):
        pass

    def generate(self, prompt, *, logprobs=None, logprobs_top_n=None, **kw):
        if logprobs:
            with open(logprobs, "w") as f:
                f.write(json.dumps({"token_id": 7, "text": "hi", "topn_ids": [7, 8, 9],
                                    "topn_logprobs": [-0.1, -1.0, -2.0]}) + "\n")
            return None
        return "plain output"

    def close(self):
        pass


@pytest.fixture
def fake_module(monkeypatch):
    fake = types.ModuleType(PY_MODULE)
    fake.Runtime = _FakeRuntimeWritesLogprobs
    fake.__version__ = "0.1.0-fake"
    monkeypatch.setitem(sys.modules, PY_MODULE, fake)
    return fake


def test_generate_plain(fake_module, tmp_path):
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    provider = QueueLLMProvider("/models/x", run_dir=tmp_path)
    out = provider.generate([Prompt(item_id="a", text="hello")], Decoding())
    assert out[0].text == "plain output"
    provider.close()


def test_generate_with_topn_parses_and_deletes_tmp_file(fake_module, tmp_path):
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    provider = QueueLLMProvider("/models/x", run_dir=tmp_path)
    out = provider.generate_with_topn([Prompt(item_id="a", text="hello")], Decoding(), top_n=3)
    gen = out[0]
    assert gen.token_ids == [7]
    assert gen.topn_ids == [[7, 8, 9]]
    assert gen.topn_logprobs == [[-0.1, -1.0, -2.0]]

    tmp_file = tmp_path / "extract" / "tmp" / "a.jsonl"
    assert not tmp_file.exists()  # deleted immediately after parsing
    provider.close()


def test_provenance_reports_module_version(fake_module, tmp_path):
    from arail.nucleus.providers.queuellm import QueueLLMProvider

    provider = QueueLLMProvider("/models/x", run_dir=tmp_path)
    prov = provider.provenance(features=["logprobs"])
    assert prov["module_version"] == "0.1.0-fake"
    assert prov["features"] == ["logprobs"]
    provider.close()
