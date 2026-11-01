"""Adversarial coverage for the answering-model ceiling (arail.registry.ceiling).

Phase 1 review (sprints/.../model-inference-hardening) found >=8B models
could become the answering model via at least six independent paths: an
unvalidated send-path override, ``ids[0]`` "first installed" fallbacks, a
first-GGUF-on-disk scan, a name-regex parser that defaults unknown sizes to
"small", a shipped 70B AIRLLM_MODEL example, and model_defaults.yaml
overriding .env. These tests exercise the chokepoint (arail.registry.ceiling)
directly — no FastAPI app needed, so no dotenv/portal import chain.
"""

from __future__ import annotations

import os
import struct
import tempfile

import pytest

os.environ.setdefault("LAB_ROOT", tempfile.mkdtemp(prefix="arail-ceiling-test-"))

from arail import hardware  # noqa: E402
from arail.registry.ceiling import (  # noqa: E402
    ModelCeilingViolation,
    PRIMARY_CEILING_B,
    resolve_answering_model,
)


class TestPrimaryCeiling:
    def test_refuses_70b_named_tag(self):
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model("llama3.1:70b", role="primary", backend="ollama_native")

    def test_refuses_exactly_8b_strict(self):
        """Operator's rule is strict: params must be < 8B, not <= 8B."""
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model("qwen3:8b", role="primary", backend="ollama_native")

    def test_allows_just_under_8b(self):
        prov = resolve_answering_model("qwen2.5:7b", role="primary", backend="ollama_native")
        assert prov.params_b == 7.0
        assert prov.role == "primary"

    def test_allows_the_shipped_default(self):
        """llama-ai-eng has no digit in its name — must resolve via the
        catalog override added to model_specs.py, not get refused as
        unknown. If this test fails, day-one setup is broken."""
        prov = resolve_answering_model("llama-ai-eng", role="primary", backend="ollama_native")
        assert prov.params_b == 1.0
        assert prov.param_source == "override"

    def test_refuses_unparseable_name_never_defaults_small(self):
        """The old must_stream() explicitly treated unparseable names as
        'small' (the review's #4 finding). The ceiling must do the
        opposite: unknown size on the primary path is a hard refusal."""
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model(
                "my-opaque-checkpoint", role="primary", backend="ollama_native"
            )

    def test_refuses_empty_model_id(self):
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model("", role="primary", backend="ollama_native")

    def test_metadata_wins_over_a_misleading_name(self):
        """An opaquely-named file — or, worse, one lying in its name — must
        be judged by its actual on-disk metadata when a path is given."""
        # 1000 x 1000 x 1000 float32 tensor ≈ 1e9 params ≈ 1.0B, deliberately
        # named to look like an 8B+ model.
        import json

        header = {
            "weight": {"dtype": "F32", "shape": [1000, 1000, 1000], "data_offsets": [0, 4_000_000_000]},
        }
        payload = json.dumps(header).encode()
        with tempfile.NamedTemporaryFile(suffix=".safetensors", delete=False) as f:
            f.write(struct.pack("<Q", len(payload)))
            f.write(payload)
            path = f.name
        try:
            prov = resolve_answering_model(
                "totally-not-a-70b-honest", role="primary", backend="cpu", model_path=path
            )
            assert prov.param_source == "metadata"
            assert prov.params_b == pytest.approx(1.0, rel=0.01)
        finally:
            os.unlink(path)

    def test_metadata_refuses_an_oversized_opaque_gguf(self):
        """The CPUBackend first-GGUF-on-disk bypass: a safetensors file
        whose real param count is >=8B must be refused even though its
        filename gives no hint."""
        import json

        # ~9B params via one big tensor.
        header = {
            "weight": {"dtype": "F32", "shape": [90000, 100000], "data_offsets": [0, 1]},
        }
        payload = json.dumps(header).encode()
        with tempfile.NamedTemporaryFile(suffix=".safetensors", delete=False) as f:
            f.write(struct.pack("<Q", len(payload)))
            f.write(payload)
            path = f.name
        try:
            with pytest.raises(ModelCeilingViolation):
                resolve_answering_model(
                    "a", role="primary", backend="cpu", model_path=path
                )
        finally:
            os.unlink(path)


class TestSecondaryCeiling:
    def test_secondary_capped_by_discovered_hardware(self, monkeypatch):
        profile = hardware.HardwareProfile(
            total_ram_gb=16.0, accelerator="mlx", platform_system="Darwin",
            platform_machine="arm64", below_min_supported=False, source="test",
        )
        monkeypatch.setattr(hardware, "load_or_discover", lambda **_: profile)
        cap = hardware.secondary_model_cap_b(profile)
        # A model comfortably over the 16GB-band cap must be refused...
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model("llama3.1:70b", role="secondary", backend="aerollm")
        # ...but the current AeroLLM default (7B) must fit under it.
        prov = resolve_answering_model("Qwen2.5-7B-Instruct-4bit", role="secondary", backend="aerollm")
        assert prov.params_b == 7.0
        assert prov.params_b < cap

    def test_secondary_allows_more_on_bigger_hardware(self, monkeypatch):
        profile = hardware.HardwareProfile(
            total_ram_gb=64.0, accelerator="mlx", platform_system="Darwin",
            platform_machine="arm64", below_min_supported=False, source="test",
        )
        monkeypatch.setattr(hardware, "load_or_discover", lambda **_: profile)
        # 70B fits comfortably in the 64GB band's cap, unlike the 16GB case above.
        prov = resolve_answering_model("llama3.1:70b", role="secondary", backend="airllm")
        assert prov.params_b == 70.0

    def test_secondary_refuses_unknown_size_too(self):
        """A cap you can't verify against isn't a cap — unknown params
        refuse for the secondary role exactly like the primary role."""
        with pytest.raises(ModelCeilingViolation):
            resolve_answering_model("__TODO_DEEP_MODEL__", role="secondary", backend="airllm")


class TestHardwareDiscovery:
    def test_never_assumes_a_specific_machine(self):
        """Regression guard for the operator's explicit instruction: don't
        build for an M4/M5 Max 36GB box. discover_hardware() must reflect
        whatever machine it's actually run on, with a 16GB floor only as a
        conservative fallback when detection fails — never a fixed target."""
        profile = hardware.discover_hardware()
        assert profile.total_ram_gb >= hardware.MIN_SUPPORTED_RAM_GB
        assert profile.accelerator in ("mlx", "cuda", "cpu", "unknown")

    def test_persist_and_reload_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LAB_ROOT", str(tmp_path))
        monkeypatch.delenv("ARAIL_DATA_DIR", raising=False)
        profile = hardware.discover_hardware()
        hardware.persist(profile)
        loaded = hardware.load_or_discover()
        assert loaded.total_ram_gb == profile.total_ram_gb
        assert (tmp_path / "data" / "hardware.json").exists()
