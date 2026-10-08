"""ARAIL's model defaults — the one file to check for what's active.

Historically "which model does chat use by default" and "which model
does AeroLLM load" were each answered by a chain of .env vars, hardcoded
fallback constants, and installed-model detection spread across
app.py/router/backends.py — nobody could answer "what's active" without
reading code. `model_defaults.yaml` (repo root, gitignored, per-machine
like .env — see model_defaults.yaml.example) is now the single,
authoritative source for these two settings.

Deliberately NOT a rewrite of the ~30 call sites that read MODEL_NAME /
AEROLLM_MODEL: `apply()` stamps this file's values into those SAME env
vars, once, at import — every existing reader sees the new source of
truth for free, and nothing downstream needs to change. A missing or
malformed file is not an error; it just means nothing is overridden and
today's .env-based defaults keep working exactly as before.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# CWD-relative, matching config.py's convention for .env/lab.conf/etc. —
# every ARAIL entry point (start.sh, the launchd plist's WorkingDirectory,
# pytest's rootdir) already runs with CWD at the repo root.
_DEFAULT_PATH = Path("model_defaults.yaml")


def apply(path: Path | None = None) -> dict[str, Any]:
    """Load model_defaults.yaml (if present) and stamp its values into
    os.environ. Returns what it found (possibly empty); never raises.

    - default_a → MODEL_NAME (Chat Studio's Box A / primary model)
    - default_b → AEROLLM_MODEL (Box B / AeroLLM's deep model)

    An explicit `null`/blank default_b clears any .env-set AEROLLM_MODEL
    rather than leaving a stale value able to silently win — "not
    configured" must stay honestly "not configured".

    Path resolution, mirroring config.py's ARAIL_ENV_FILE pattern exactly
    (same bug class, same fix): ``ARAIL_MODEL_DEFAULTS_FILE`` overrides
    the default CWD-relative lookup when set. Without this, importing
    arail.config in a test process would hydrate whatever's on THIS
    machine's real model_defaults.yaml into every test — the exact
    "developer's real .env leaks into the test suite" bug that
    ARAIL_ENV_FILE / tests/conftest.py's session-level isolation already
    exists to prevent for .env. `path=` (explicit arg) always wins over
    both, for direct unit tests.
    """
    if path is not None:
        p = path
    else:
        override = os.getenv("ARAIL_MODEL_DEFAULTS_FILE", "").strip()
        p = Path(override) if override else _DEFAULT_PATH
    if not p.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — a bad file must never block boot
        _log.warning("model_defaults: failed to read %s: %s", p, e)
        return {}
    if not isinstance(data, dict):
        _log.warning("model_defaults: %s did not parse to a mapping — ignoring", p)
        return {}

    result: dict[str, Any] = {}

    default_a = data.get("default_a")
    if default_a:
        os.environ["MODEL_NAME"] = str(default_a)
        result["default_a"] = str(default_a)

    if "default_b" in data:
        default_b = data.get("default_b")
        if default_b:
            os.environ["AEROLLM_MODEL"] = str(default_b)
            result["default_b"] = str(default_b)
        else:
            os.environ.pop("AEROLLM_MODEL", None)
            result["default_b"] = None

    return result


def write_defaults(
    default_a: str, default_b: "str | None" = None, path: Path | None = None
) -> Path:
    """Atomically write default_a/default_b to model_defaults.yaml.

    Mirrors apply()'s path resolution exactly (explicit arg >
    ARAIL_MODEL_DEFAULTS_FILE > CWD default) so a write always lands
    where the next apply() call reads it back from.

    File existence (with a truthy ``default_a``) IS the "boot picker has
    been settled" signal the boot banner uses — deliberately no separate
    marker file. Deleting this file is the supported way to re-run the
    picker on the next boot.
    """
    if path is not None:
        p = path
    else:
        override = os.getenv("ARAIL_MODEL_DEFAULTS_FILE", "").strip()
        p = Path(override) if override else _DEFAULT_PATH

    import yaml
    body = yaml.safe_dump(
        {"default_a": default_a, "default_b": default_b},
        sort_keys=False, default_flow_style=False,
    )
    header = (
        "# ARAIL's model defaults — the ONE file to check for what's active.\n"
        "# Written by the boot model-selection banner "
        "(POST /api/models/settle) —\n"
        "# see model_defaults.yaml.example for the full explanation.\n"
        "# Delete this file to re-run the boot picker on the next start.\n"
        "#\n"
        "# default_a — the primary, fast, always-resident chat model\n"
        "#             (an installed Ollama model tag).\n"
        "# default_b — the model QueueLLM (the deep / 2nd inference) loads\n"
        "#             (a directory name under ARAIL_MODELS_DIR), or null\n"
        "#             for \"no deep model configured\".\n"
    )
    content = header + body

    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".model_defaults.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def resolve_slots() -> dict[str, Any]:
    """What's actually configured for each slot right now.

    Deliberately routes through ``arail.config`` (lazy import — avoids the
    circular import at module load time, since config.py itself imports
    this module) rather than re-implementing the .env → model_defaults.yaml
    precedence: importing it applies both layers exactly the way the
    running portal does, so this report can never disagree with what
    actually answers a chat request.
    """
    import arail.config  # noqa: F401 — import side effect: applies .env + this file

    default_a = os.environ.get("MODEL_NAME") or "llama-ai-eng"
    default_b = os.environ.get("AEROLLM_MODEL") or None

    override = os.getenv("ARAIL_MODEL_DEFAULTS_FILE", "").strip()
    p = Path(override) if override else _DEFAULT_PATH
    settled = False
    if p.exists():
        try:
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            settled = isinstance(data, dict) and bool(data.get("default_a"))
        except Exception:  # noqa: BLE001
            pass

    return {
        "settled": settled,
        "path": str(p),
        "default_a": default_a,
        "default_b": default_b,
    }


def _fmt_gb(x: "float | None") -> str:
    return f"{x:.1f} GB" if isinstance(x, (int, float)) else "size unknown"


def _gather_slot_facts() -> dict[str, Any]:
    """Presence / size / fit for whatever resolve_slots() reports.

    Best-effort throughout: any lookup failure degrades to "unknown"
    rather than raising — this feeds a boot-time report that must never
    crash the banner it's printed from.
    """
    slots = resolve_slots()
    a = slots["default_a"]
    a_present = False
    a_size_gb: "float | None" = None
    try:
        from arail.chat import _ollama_installed_models
        short_a = a.split(":", 1)[0] if a else ""
        for m in _ollama_installed_models():
            if m["id"] == a or m["id"].split(":", 1)[0] == short_a:
                a_present = True
                a_size_gb = m.get("size_gb")
                break
    except Exception:  # noqa: BLE001
        pass

    a_fit = "unknown"
    if a:
        try:
            from arail import model_specs as _specs
            from arail.registry.ceiling import PRIMARY_CEILING_B
            a_params, _a_src = _specs.resolve_params_b(a)
            if a_params is not None:
                a_fit = "fits" if a_params < PRIMARY_CEILING_B else "too_big"
        except Exception:  # noqa: BLE001
            pass

    b = slots["default_b"]
    b_present = False
    b_size_gb: "float | None" = None
    b_path: "str | None" = None
    if b:
        models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
        b_path = b if os.path.isabs(b) else os.path.join(models_dir, b)
        b_present = os.path.isdir(b_path)
        if b_present:
            try:
                total = sum(
                    f.stat().st_size for f in Path(b_path).rglob("*") if f.is_file()
                )
                b_size_gb = round(total / (1024 ** 3), 1) if total else None
            except OSError:
                pass

    b_fit = "unknown"
    b_cap: "float | None" = None
    if b:
        try:
            from arail import hardware as _hw
            from arail import model_specs as _specs
            b_cap = _hw.secondary_model_cap_b()
            params, _src = _specs.resolve_params_b(b, b_path if b_present else None)
            if params is not None:
                b_fit = "fits" if params <= b_cap else "too_big"
        except Exception:  # noqa: BLE001
            pass

    return {
        "a": {
            "model": a, "present": a_present, "size_gb": a_size_gb,
            "fit": a_fit,
            "install_command": (
                f"ollama pull {a}" if a and not a_present else None),
        },
        "b": ({
            "model": b, "present": b_present, "size_gb": b_size_gb,
            "fit": b_fit, "cap_b": b_cap,
            "install_command": (
                f"hf download mlx-community/{b} --local-dir {b_path}"
                if b and not b_present else None),
        } if b else None),
    }


def _fit_label(fit: str, cap_b: "float | None" = None) -> str:
    if fit == "fits":
        return "fits"
    if fit == "too_big":
        return f"too big (cap ~{cap_b:g}B)" if cap_b else "too big"
    return "unknown"


def report() -> str:
    """The honest per-slot lines for the CLI readiness banner: name,
    size, on-disk?, fits?, plus the install command when a slot is
    absent. Never raises."""
    facts = _gather_slot_facts()
    a = facts["a"]
    lines = ["  Models:"]
    lines.append(
        f"    A (resident):  {a['model']:<28} {_fmt_gb(a['size_gb']):>10}   "
        f"on disk: {'yes' if a['present'] else 'NO':<3}   fits: {_fit_label(a['fit'])}"
    )
    if a["install_command"]:
        lines.append(f"                   {a['install_command']}")

    b = facts["b"]
    if b is None:
        lines.append("    B (QueueLLM):  (not configured)")
    else:
        lines.append(
            f"    B (QueueLLM):  {b['model']:<28} {_fmt_gb(b['size_gb']):>10}   "
            f"on disk: {'yes' if b['present'] else 'NO':<3}   "
            f"fits: {_fit_label(b['fit'], b['cap_b'])}"
        )
        if b["install_command"]:
            lines.append(f"                   {b['install_command']}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover — thin CLI shim, exercised via subprocess tests
    import argparse
    import json
    import sys

    _parser = argparse.ArgumentParser(
        prog="python -m arail.model_defaults",
        description=(
            "Read-only report of the two model-selection slots "
            "(default_a / default_b). Never writes anything — used by "
            "start.sh's readiness banner."
        ),
    )
    _parser.add_argument(
        "--banner", action="store_true",
        help="print the multi-line readiness-banner block (default)")
    _parser.add_argument(
        "--get", choices=("default_a", "default_b"),
        help="print just one resolved slot value (empty line if unset)")
    _parser.add_argument(
        "--json", action="store_true", help="print the full report as JSON")
    _args = _parser.parse_args()

    if _args.get:
        _slots = resolve_slots()
        print(_slots.get(_args.get) or "")
    elif _args.json:
        _slots = resolve_slots()
        _slots["facts"] = _gather_slot_facts()
        print(json.dumps(_slots))
    else:
        print(report())
    sys.exit(0)
