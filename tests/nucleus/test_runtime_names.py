"""arail.nucleus.runtime_names — the queuellm <-> frozen aerollm surface.

T-RT-1: the mapping itself. T-RT-2: a grep invariant enforcing that no
other file under src/arail/nucleus/** spells the frozen names, and that
no file in the package writes to os.environ.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from arail.nucleus import runtime_names as rn
from arail.nucleus.errors import DomainConfigError

NUCLEUS_SRC = Path(__file__).resolve().parents[2] / "src" / "arail" / "nucleus"


# ── T-RT-1: mapping ─────────────────────────────────────────────────

def test_frozen_constants():
    assert rn.USER_RUNTIME == "queuellm"
    assert rn.BACKEND_ID == "aerollm"
    assert rn.PY_MODULE == "aerollm_api"
    assert rn.REGISTRY_ID == "tier1-aerollm"


def test_resolve_queuellm():
    binding = rn.resolve_user_runtime("queuellm")
    assert binding.user_name == "queuellm"
    assert binding.backend_id == "aerollm"
    assert binding.py_module == "aerollm_api"
    assert binding.registry_id == "tier1-aerollm"


def test_resolve_ollama():
    binding = rn.resolve_user_runtime("ollama")
    assert binding.backend_id == "ollama"
    assert binding.py_module is None


def test_resolve_airllm():
    binding = rn.resolve_user_runtime("airllm")
    assert binding.backend_id == "airllm"


def test_resolve_unknown_raises_domain_config_error():
    with pytest.raises(DomainConfigError, match="runtime must be one of"):
        rn.resolve_user_runtime("gguf-shim")


def test_resolve_stub_rejected_without_env(monkeypatch):
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)
    with pytest.raises(DomainConfigError, match="ARAIL_NUCLEUS_STUB"):
        rn.resolve_user_runtime("stub")


def test_resolve_stub_accepted_with_env(monkeypatch):
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    binding = rn.resolve_user_runtime("stub")
    assert binding.backend_id == "stub"


def test_resolve_is_case_insensitive_and_strips():
    assert rn.resolve_user_runtime(" QueueLLM ").backend_id == "aerollm"


# ── runtime_provenance ──────────────────────────────────────────────

class _FakeModule:
    __file__ = "/nonexistent/aerollm_api.so"
    __version__ = "0.1.0-rc.2"


def test_runtime_provenance_shape_when_module_file_missing():
    prov = rn.runtime_provenance(_FakeModule())
    assert prov["runtime"] == "queuellm"
    assert prov["backend_id"] == "aerollm"
    assert prov["module"] == "aerollm_api"
    assert prov["module_version"] == "0.1.0-rc.2"
    assert prov["so_sha256"] is None
    assert prov["source"] == "unknown"
    assert prov["features"] == "unknown"


def test_runtime_provenance_features_passthrough():
    prov = rn.runtime_provenance(_FakeModule(), features=["logprobs"])
    assert prov["features"] == ["logprobs"]


def test_runtime_provenance_local_build_when_so_present_but_unmatched(tmp_path):
    fake_so = tmp_path / "aerollm_api.so"
    fake_so.write_bytes(b"not the real extension")

    class _Mod:
        __file__ = str(fake_so)
        __version__ = "9.9.9"

    prov = rn.runtime_provenance(_Mod())
    assert prov["so_sha256"] is not None
    # Won't match the real bundle's recorded sha (different bytes), so this
    # is reported as a local build, never silently as "bundle".
    assert prov["source"] in ("local-build", "unknown")


# ── T-RT-2: grep invariants ──────────────────────────────────────────

_FROZEN_NAME_RE = re.compile(r"\baerollm|\bAERO_|\bQUEUELLM_", re.IGNORECASE)
_ENV_WRITE_RE = re.compile(r"os\.environ\s*\[[^\]]*\]\s*=")


def test_no_frozen_names_leak_outside_runtime_names_module():
    offenders = []
    for path in NUCLEUS_SRC.rglob("*.py"):
        if path.name == "runtime_names.py":
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FROZEN_NAME_RE.search(line):
                offenders.append(f"{path.relative_to(NUCLEUS_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, "frozen names leaked outside runtime_names.py:\n" + "\n".join(offenders)


def test_no_os_environ_writes_anywhere_in_nucleus_package():
    offenders = []
    for path in NUCLEUS_SRC.rglob("*.py"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ENV_WRITE_RE.search(line):
                offenders.append(f"{path.relative_to(NUCLEUS_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, "os.environ writes found in arail.nucleus:\n" + "\n".join(offenders)
