"""The fake deep runtime must never leak into a test that did not ask for it.

Found in the step 5 full-suite run: a test that used ``monkeypatch.delitem(sys.modules, ...)`` on
the loaded fake had the entries restored AFTER the fake's own teardown, so ``import aerollm_api``
started succeeding in tests/test_aerollm_defaults.py. The conftest purge at the start of each
test is the guard; these two tests, which run in this order, are its regression test.
"""
from __future__ import annotations

import sys

from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)


def _natives() -> list[str]:
    return [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]


def test_a_the_leaky_pattern_leaves_entries_for_monkeypatch_to_restore(fake_deep, monkeypatch):
    fake_deep()
    assert dv.identity() is not None and _natives()
    for name in _natives():
        monkeypatch.delitem(sys.modules, name)      # restored at teardown, after fake_deep cleaned up


def test_b_the_next_test_starts_without_the_fake():
    assert _natives() == [], "a fake aerollm_api leaked from the test before"
    assert not [p for p in sys.path if "fakepkg" in p]


def test_c_a_real_module_is_never_purged(monkeypatch):
    import types
    real = types.ModuleType("aerollm_api")
    real.__file__ = "/site-packages/aerollm_api/aerollm_api.abi3.so"
    real.__version__ = "1.1.0"
    monkeypatch.setitem(sys.modules, "aerollm_api", real)
    # the purge ran before this test body; run it again the way the next test would
    for name in [n for n in list(sys.modules) if n.split(".")[0] == "aerollm_api"]:
        mod = sys.modules[name]
        if getattr(mod, "__version__", None) == "0.0.0-fake" or "/fakedeep/" in str(getattr(mod, "__file__", "")):
            sys.modules.pop(name)
    assert sys.modules["aerollm_api"] is real
