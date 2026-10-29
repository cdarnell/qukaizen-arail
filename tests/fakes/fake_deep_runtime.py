"""The fake deep runtime fixture (sprint 2026-10-29-arail-deep-honest).

``fake_deep(mode)`` installs a pure-Python ``aerollm_api`` (see
``aerollm_api_template.py``) in a tmp dir, puts it first on ``sys.path``, and
points ``AEROLLM_MODEL`` and ``ARAIL_MODELS_DIR`` at a tmp model dir. It also
resets every cache that would carry state between tests. Nothing here needs
Metal or compiled code, so it runs on Linux CI.

Use in a test::

    from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401

    def test_x(fake_deep):
        fd = fake_deep("fail2")            # call 1 answers, later calls raise
        backend = fd.backend()             # ARAIL's own AeroLLMBackend
        ...

For a subprocess, pass ``fd.env()``. The mode is read by the fake at every
call from ``ARAIL_FAKE_DEEP_MODE``, which only the fake reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
TEMPLATE = Path(__file__).with_name("aerollm_api_template.py")

MODEL_NAME = "fake-model"
_NATIVE_BYTES = b"FAKE-NATIVE-BINARY-A\n" * 64

# The real bug's message, as the fake raises it. Tests assert on pieces of it.
BUG_FRAGMENT = "[rms_norm]"


@dataclass
class FakeDeep:
    root: Path
    pkg_dir: Path           # <root>/fakepkg, the directory to put on sys.path
    package: Path           # <root>/fakepkg/aerollm_api
    native_path: Path       # the dummy extension file
    count_file: Path
    models_dir: Path
    model_name: str = MODEL_NAME
    _mp: Any = field(default=None, repr=False)

    # -- identity -------------------------------------------------------
    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.native_path.read_bytes()).hexdigest()

    def rewrite_native(self, data: bytes) -> None:
        """Replace the dummy native file: a 'new build' with its own sha256."""
        self.native_path.write_bytes(data)

    # -- mode -----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self._mp.setenv("ARAIL_FAKE_DEEP_MODE", mode)

    # -- subprocess env -------------------------------------------------
    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(self.pkg_dir), str(SRC_DIR)])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # A subprocess must never write into the checkout's lab/ or a real lab.
        env["ARAIL_DATA_DIR"] = str(self.root / "lab-data")
        env["LAB_ROOT"] = str(self.root / "lab")
        env["LAB_PKB"] = str(self.root / "lab" / "pkb")
        env.pop("ARAIL_INSTANCE", None)
        env.update(extra)
        return env

    # -- what the fake saw ---------------------------------------------
    def events(self) -> list[dict]:
        if not self.count_file.exists():
            return []
        out = []
        for line in self.count_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def gen_calls(self) -> list[dict]:
        return [e for e in self.events() if e.get("ev") == "gen"]

    # -- ARAIL's own backend, over the fake ----------------------------
    def backend(self):
        """A fresh ``AeroLLMBackend`` over the fake runtime."""
        from arail.router.backends import AeroLLMBackend
        AeroLLMBackend._shared = {}
        return AeroLLMBackend()


def install(tmp_path: Path, monkeypatch, mode: str = "pass") -> FakeDeep:
    """Install the fake and reset caches. Returns the handle."""
    root = tmp_path / "fakedeep"
    pkg_dir = root / "fakepkg"
    package = pkg_dir / "aerollm_api"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    native = package / "_native.abi3.so"
    native.write_bytes(_NATIVE_BYTES)

    models_dir = root / "models"
    model = models_dir / MODEL_NAME
    model.mkdir(parents=True)
    (model / "config.json").write_text('{"fake": true}\n', encoding="utf-8")
    (model / "weights.bin").write_bytes(b"\0" * 2048)

    count_file = root / "calls.jsonl"

    # No stale real or fake runtime from an earlier test.
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(pkg_dir))

    monkeypatch.setenv("AEROLLM_MODEL", MODEL_NAME)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("ARAIL_FAKE_DEEP_MODE", mode)
    monkeypatch.setenv("ARAIL_FAKE_DEEP_COUNT_FILE", str(count_file))
    # A deterministic ring depth keeps the backend off the runtime profile.
    monkeypatch.setenv("AEROLLM_RING_DEPTH", "2")
    monkeypatch.delenv("ARAIL_DEEP_PROBE_CHILD", raising=False)

    fd = FakeDeep(root=root, pkg_dir=pkg_dir, package=package, native_path=native,
                  count_file=count_file, models_dir=models_dir, _mp=monkeypatch)
    _reset_caches(monkeypatch)
    return fd


def _reset_caches(monkeypatch) -> None:
    from arail import deep_verdict
    from arail.agents import deep_policy
    from arail.router.backends import AeroLLMBackend

    monkeypatch.setattr(AeroLLMBackend, "_shared", {})
    deep_policy._reset_for_tests()
    deep_verdict._reset_for_tests()


def _uninstall() -> None:
    from arail import deep_verdict
    from arail.agents import deep_policy
    from arail.router.backends import AeroLLMBackend

    for inst in list((getattr(AeroLLMBackend, "_shared", None) or {}).values()):
        try:
            inst._close()
        except Exception:  # noqa: BLE001 - teardown must not mask the test result
            pass
    deep_policy._reset_for_tests()
    deep_verdict._reset_for_tests()
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        sys.modules.pop(name, None)


@pytest.fixture
def fake_deep(tmp_path, monkeypatch):
    """Factory fixture: ``fd = fake_deep("fail2")``."""
    made: list[FakeDeep] = []

    def _make(mode: str = "pass") -> FakeDeep:
        if made:                      # one install per test; a second call switches the mode
            made[0].set_mode(mode)
            return made[0]
        fd = install(tmp_path, monkeypatch, mode)
        made.append(fd)
        return fd

    yield _make
    if made:
        _uninstall()
