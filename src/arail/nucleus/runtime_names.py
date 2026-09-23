"""queuellm ↔ frozen aerollm surface.

THE ONLY place in ``arail.nucleus`` where the strings ``aerollm``,
``aerollm_api``, or ``AERO`` may appear (enforced by a grep test,
tests/nucleus/test_runtime_names.py::test_no_frozen_names_leak_outside_this_module,
T-RT-2). Every other nucleus module imports ``USER_RUNTIME`` /
``resolve_user_runtime`` / ``runtime_provenance`` from here instead of
spelling the backend name itself.

Nucleus sets no environment variables for the runtime — every knob
(``ring_depth``, ``kv_memory_budget``, ``draft_model``) is passed as a
``Runtime(...)`` constructor kwarg by ``providers/queuellm.py`` (commit 9),
so the ``AERO_*`` vs ``QUEUELLM_*`` deprecation question (workspace
CLAUDE.md, "AeroLLM → QueueLLM") never arises here, whatever bundle version
is installed. See ARCHITECTURE.md §3 N1 / §4.1.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from arail.nucleus.errors import DomainConfigError

# ── frozen surface — never rename these values, only this module may spell them ──
USER_RUNTIME = "queuellm"
BACKEND_ID = "aerollm"
PY_MODULE = "aerollm_api"
REGISTRY_ID = "tier1-aerollm"

_KNOWN_USER_RUNTIMES = ("queuellm", "ollama", "airllm")

# Repo root, derived from this file's location (src/arail/nucleus/runtime_names.py
# -> repo root is four parents up), not cwd — cli invocations may run from
# anywhere the shell wrapper was called from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUNDLE_JSON = _REPO_ROOT / "THIRD-PARTY-LICENSES" / "aerollm" / "BUNDLE.json"


@dataclass(frozen=True)
class RuntimeBinding:
    """What ``resolve_user_runtime`` resolves a user-facing runtime name to."""

    user_name: str
    backend_id: str
    py_module: Optional[str]
    registry_id: Optional[str]


def resolve_user_runtime(name: str) -> RuntimeBinding:
    """Map a user-facing ``domain.yaml runtime:`` value to its backend.

    ``stub`` is accepted only when ``ARAIL_NUCLEUS_STUB=1`` — it is a CI/test
    seam, never a real domain config choice (T-STUB-2).
    """
    n = (name or "").strip().lower()
    if n == "queuellm":
        return RuntimeBinding(USER_RUNTIME, BACKEND_ID, PY_MODULE, REGISTRY_ID)
    if n == "ollama":
        return RuntimeBinding("ollama", "ollama", None, None)
    if n == "airllm":
        return RuntimeBinding("airllm", "airllm", "airllm", None)
    if n == "stub":
        if os.getenv("ARAIL_NUCLEUS_STUB", "").strip() != "1":
            raise DomainConfigError(
                "runtime 'stub' is only accepted when ARAIL_NUCLEUS_STUB=1 "
                "(it is a CI/test seam, not a real domain config choice)"
            )
        return RuntimeBinding("stub", "stub", None, None)
    raise DomainConfigError(
        f"runtime must be one of {'|'.join(_KNOWN_USER_RUNTIMES)}; got {name!r}"
    )


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def _bundle_metadata() -> dict:
    if not _BUNDLE_JSON.is_file():
        return {}
    try:
        return json.loads(_BUNDLE_JSON.read_text())
    except (OSError, ValueError):
        return {}


def buddy_deep_model_env_value() -> Optional[str]:
    """Read Buddy's configured deep-mode model dir, if any.

    Only this module may spell the frozen env var name (T-RT-2) — this
    is a READ, not a write (arail.nucleus never writes env vars; see the
    module docstring), used by preflight.py's declared-Buddy-reserve
    estimate. Honors QUEUELLM_MODEL first, then the frozen AEROLLM_MODEL
    alias, mirroring the workspace's AeroLLM -> QueueLLM deprecation
    order (queuellm repo CLAUDE.md: QUEUELLM_* wins when both are set).
    """
    return os.getenv("QUEUELLM_MODEL") or os.getenv("AEROLLM_MODEL")


def runtime_provenance(module, *, features: Optional[list] = None) -> dict:
    """Provenance dict recorded on the DNA card for a QueueLLM-backed role.

    Compares the *actually loaded* extension module's file sha256 against
    the pinned bundle's recorded sha256 to say whether this process is
    running the shipped bundle or a local rebuild (F1: the shipped bundle
    lacks ``unstable-api``/logprobs; a local ``--features unstable-api``
    rebuild is a different, larger .so with a different hash).

    ``features`` is the list of unstable-api-gated capabilities the caller's
    capability probe (providers/queuellm.py, commit 9) found present; it is
    ``None`` here (recorded as "unknown") because this function itself never
    probes the runtime — probing requires calling into it, which is the
    provider's job, not this pure-metadata module's.
    """
    module_file = getattr(module, "__file__", None)
    module_path = Path(module_file).resolve() if module_file else None
    so_sha256 = _sha256_file(module_path) if module_path else None

    bundle = _bundle_metadata()
    bundle_tag = bundle.get("arail_release")
    bundle_so_sha256 = bundle.get("sha256")

    if so_sha256 is not None and bundle_so_sha256 is not None and so_sha256 == bundle_so_sha256:
        source = "bundle"
    elif so_sha256 is not None:
        source = "local-build"
    else:
        source = "unknown"

    return {
        "runtime": USER_RUNTIME,
        "backend_id": BACKEND_ID,
        "module": PY_MODULE,
        "module_version": getattr(module, "__version__", None),
        "so_sha256": so_sha256,
        "bundle_tag": bundle_tag,
        "bundle_so_sha256": bundle_so_sha256,
        "source": source,
        "features": list(features) if features is not None else "unknown",
    }
