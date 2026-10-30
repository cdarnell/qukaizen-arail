"""setup.sh and the install hints stop claiming readiness or a route that no longer builds main.

7b of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md (C7, last paragraph). The wheel-missing health
detail is covered with the registry tests; it is repeated here only as a pointer check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_setup_sh_no_longer_claims_readiness():
    setup = (REPO / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "aerollm_api already present — generation not tested here; run ./arailctl deep probe." in setup
    assert "QueueLLM installed — run ./arailctl deep probe to test generation." in setup
    for gone in ("QueueLLM ready", "QueueLLM is the deep-mode 2nd inference."):
        assert gone not in setup
    # the failure message still names the outside-user route (test_aerollm_bundle_qa_hardening pins this too)
    window = setup[setup.find("QueueLLM not installed"):][:400]
    assert "deep install" in window


def test_the_import_error_hint_says_rebuild_needs_a_pre_rename_checkout():
    import arail.router.backends as backends
    sys_mod = sys.modules.pop("aerollm_api", None)
    sys.modules["aerollm_api"] = None            # `import aerollm_api` raises ImportError
    backends.AeroLLMBackend._shared.clear()
    try:
        with pytest.raises(ImportError) as ei:
            backends.AeroLLMBackend()
    finally:
        sys.modules.pop("aerollm_api", None)
        if sys_mod is not None:
            sys.modules["aerollm_api"] = sys_mod
        backends.AeroLLMBackend._shared.clear()
    msg = str(ei.value)
    assert "the pre-rename crates/aerollm-api" in msg and "refuses a checkout with the renamed crates/queuellm-api" in msg
    assert msg.index("deep install") < msg.index("deep rebuild") < msg.index("deep update")
    assert "build from the local sibling repo" not in msg


def test_the_wheel_missing_health_detail_points_at_deep_install():
    from arail.registry import health
    src = Path(health.__file__).read_text(encoding="utf-8")
    assert "aerollm_api wheel not installed " in src and "(./arailctl deep install)" in src
    assert "(./arailctl deep rebuild)" not in src
