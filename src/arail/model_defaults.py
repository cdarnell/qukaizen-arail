"""ARAIL's model defaults — pins plus the last-resolved cache, in one file.

``model_defaults.yaml`` (repo root, gitignored, per-machine like .env —
see model_defaults.yaml.example) has two sections (schema ``version: 2``):

* ``pins``     — operator intent, preserved across boots. ``null`` means
  "auto". Slot B may be the literal ``none`` to keep the deep slot off.
* ``resolved`` — a cache of what the portal's boot resolver
  (``arail.model_resolve``) picked last time. Rewritten every boot, never a
  gate, never compared for "drift".

Deliberately NOT a rewrite of the ~30 call sites that read MODEL_NAME /
AEROLLM_MODEL: ``apply()`` stamps the cached values into those SAME env
vars, once, at import, so CLI tools and agents in non-portal processes see
the last-resolved models. The portal re-resolves at startup (the only
resolver; there is no second implementation here). A missing or malformed
file is not an error.

A legacy v1 file (top-level ``default_a`` / ``default_b``, no ``version``)
is read as pins; it is migrated to v2 by ``migrate_v1`` (portal only).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import warnings
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# CWD-relative, matching config.py's convention for .env/lab.conf/etc. —
# every ARAIL entry point (start.sh, the launchd plist's WorkingDirectory,
# pytest's rootdir) already runs with CWD at the repo root.
_DEFAULT_PATH = Path("model_defaults.yaml")

# Serialises read-modify-write cycles within this process so a pin write can
# never be lost to a concurrent cache write (FM13). Cross-process writers are
# last-writer-wins on `resolved`; pins are only ever written by write_pin.
_WRITE_LOCK = threading.RLock()

VERSION = 2
NONE_PIN = "none"

# apply() overwrites MODEL_NAME / AEROLLM_MODEL; remember what .env had so
# the resolver can treat it as a preference ("not stamped by us").
_ENV_PREF = {"a": ("MODEL_NAME", "ARAIL_ENV_PREF_A"),
             "b": ("AEROLLM_MODEL", "ARAIL_ENV_PREF_B")}

_HEADER = (
    "# ARAIL model defaults — managed by ARAIL. `resolved` is rewritten on\n"
    "# every boot; edit only `pins` (null = auto; slot b may be `none`).\n"
    "# See model_defaults.yaml.example. Deleting this file just means\n"
    "# \"re-resolve with no pins\".\n"
)


def _path(path: "Path | None" = None) -> Path:
    if path is not None:
        return path
    override = os.getenv("ARAIL_MODEL_DEFAULTS_FILE", "").strip()
    return Path(override) if override else _DEFAULT_PATH


def _norm_pin(slot: str, v: Any) -> "str | None":
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "auto":
        return None
    if s.lower() == NONE_PIN:
        return NONE_PIN if slot == "b" else None
    return s


def read(path: "Path | None" = None) -> dict[str, Any]:
    """Normalised view of the file; never raises.

    ``{"exists", "version", "legacy", "ok", "pins": {a, b}, "resolved": {},
    "pin_notes": {}}``. A v1 file reports ``legacy=True`` with
    ``default_a`` as pin a and a present-but-null ``default_b`` as pin b
    ``"none"`` (the operator's crash workaround survives migration).
    """
    p = _path(path)
    out: dict[str, Any] = {"exists": p.exists(), "version": None, "legacy": False,
                           "ok": True, "pins": {"a": None, "b": None},
                           "resolved": {}, "pin_notes": {}}
    if not out["exists"]:
        return out
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — a bad file must never block boot
        _log.warning("model_defaults: failed to read %s: %s", p, e)
        out["ok"] = False
        return out
    if not isinstance(data, dict):
        _log.warning("model_defaults: %s did not parse to a mapping — ignoring", p)
        out["ok"] = False
        return out
    if data.get("version") == VERSION or "pins" in data or "resolved" in data:
        out["version"] = VERSION
        pins = data.get("pins") if isinstance(data.get("pins"), dict) else {}
        out["pins"] = {"a": _norm_pin("a", pins.get("a")),
                       "b": _norm_pin("b", pins.get("b"))}
        res = data.get("resolved")
        out["resolved"] = res if isinstance(res, dict) else {}
        notes = data.get("pin_notes")
        out["pin_notes"] = notes if isinstance(notes, dict) else {}
        return out
    if "default_a" in data or "default_b" in data:
        out["legacy"] = True
        out["pins"]["a"] = _norm_pin("a", data.get("default_a"))
        if "default_b" in data:
            b = data.get("default_b")
            out["pins"]["b"] = _norm_pin("b", b) if b else NONE_PIN
    return out


def _resolved_model(resolved: dict, slot: str) -> "tuple[bool, str | None]":
    """(present, model) for a slot in a ``resolved`` block."""
    s = resolved.get(slot)
    if isinstance(s, dict) and "model" in s:
        return True, (str(s["model"]) if s.get("model") else None)
    return False, None


def apply(path: "Path | None" = None) -> dict[str, Any]:
    """Stamp the cached (or, failing that, pinned) models into os.environ.
    Returns what was stamped (possibly empty); never raises.

    - slot a → MODEL_NAME (Chat Studio's Box A / primary model)
    - slot b → AEROLLM_MODEL (Box B / AeroLLM's deep model); a "none"
      slot clears any .env-set AEROLLM_MODEL so "not configured" stays
      honestly "not configured".

    Path resolution: explicit ``path`` > ``ARAIL_MODEL_DEFAULTS_FILE`` >
    CWD default (mirrors config.py's ARAIL_ENV_FILE pattern, so importing
    arail.config in a test never hydrates a developer's real file).
    """
    data = read(path)
    if not data["exists"] or not data["ok"]:
        return {}

    desired: dict[str, "tuple[bool, str | None]"] = {}
    for slot in ("a", "b"):
        present, model = _resolved_model(data["resolved"], slot)
        if not present:
            pin = data["pins"][slot]
            if pin is None:
                present, model = False, None
            else:
                present, model = True, (None if pin == NONE_PIN else pin)
        desired[slot] = (present, model)

    return stamp_env({slot: {"model": desired[slot][1]}
                      for slot in ("a", "b") if desired[slot][0]})


def stamp_env(resolved: dict[str, Any]) -> dict[str, Any]:
    """Stamp a ``resolved``-shaped mapping (``{a: {model}, b: {model}}``)
    into MODEL_NAME / AEROLLM_MODEL. A slot whose model is empty clears
    its env var. Slots absent from *resolved* are left alone. Remembers
    the pre-stamp value once (see ``env_preference``)."""
    result: dict[str, Any] = {}
    for slot, key in (("a", "default_a"), ("b", "default_b")):
        s = resolved.get(slot)
        if not isinstance(s, dict) or "model" not in s:
            continue
        model = str(s["model"]) if s.get("model") else None
        env_name, marker = _ENV_PREF[slot]
        os.environ.setdefault(marker, os.environ.get(env_name, ""))
        if model:
            os.environ[env_name] = model
        elif slot == "b":
            os.environ.pop(env_name, None)
        else:
            continue            # an unresolved A never erases MODEL_NAME
        result[key] = model
    return result


def env_preference() -> dict[str, "str | None"]:
    """What .env / the shell asked for, ignoring values apply() stamped."""
    out: dict[str, "str | None"] = {}
    for slot, (env_name, marker) in _ENV_PREF.items():
        raw = os.environ[marker] if marker in os.environ else os.environ.get(env_name, "")
        out[slot] = raw.strip() or None
    return out


# ── writers ────────────────────────────────────────────────────────

def _dump(pins: dict, resolved: dict, notes: dict) -> str:
    import yaml
    body: dict[str, Any] = {"version": VERSION, "pins": {"a": pins.get("a"), "b": pins.get("b")}}
    if notes:
        body["pin_notes"] = dict(notes)
    body["resolved"] = resolved or {}
    return _HEADER + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def _atomic_write(p: Path, content: str) -> None:
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


def _write_cache_unlocked(resolution: dict[str, Any], path: "Path | None" = None) -> bool:
    """Replace the ``resolved`` block (atomic); pins are re-emitted as read.
    *resolution* is ``BootResolution.to_dict()`` (plus optional ``warm``).
    Returns False (after one warning) when the file can't be written —
    a read-only checkout must never block boot (FM10)."""
    p = _path(path)
    cur = read(p)
    pins = cur["pins"] if cur["ok"] else {"a": None, "b": None}
    resolved = {k: v for k, v in resolution.items() if k != "degraded"}
    resolved.setdefault("warm", (cur["resolved"].get("warm") or {}))
    try:
        _atomic_write(p, _dump(pins, resolved, cur["pin_notes"]))
        return True
    except OSError as e:
        _log.warning("model_defaults: couldn't save model cache to %s: %s", p, e)
        return False


def _update_warm_unlocked(slot: str, status: str, path: "Path | None" = None) -> bool:
    """Record a slot's warm outcome inside ``resolved.warm``."""
    p = _path(path)
    cur = read(p)
    if not cur["ok"]:
        return False
    resolved = dict(cur["resolved"])
    warm = dict(resolved.get("warm") or {})
    warm[slot] = status
    resolved["warm"] = warm
    try:
        _atomic_write(p, _dump(cur["pins"], resolved, cur["pin_notes"]))
        return True
    except OSError as e:
        _log.warning("model_defaults: couldn't record warm state: %s", e)
        return False


def _write_pin_unlocked(slot: str, value: "str | None", path: "Path | None" = None) -> Path:
    """Set one pin. *value*: a model id, ``"none"`` (slot b only), or
    ``None`` / ``"auto"`` to clear. Preserves the other pin and the cache.
    A legacy v1 file is upgraded in the same write. Raises ValueError for
    a bad slot / ``none`` on slot a, OSError if unwritable."""
    if slot not in ("a", "b"):
        raise ValueError(f"slot must be 'a' or 'b', got {slot!r}")
    if (value is not None and str(value).strip().lower() == NONE_PIN and slot == "a"):
        raise ValueError("slot a cannot be pinned to 'none'")
    p = _path(path)
    cur = read(p)
    pins = dict(cur["pins"]) if cur["ok"] else {"a": None, "b": None}
    pins[slot] = _norm_pin(slot, value)
    notes = dict(cur["pin_notes"])
    notes.pop(slot, None)
    _atomic_write(p, _dump(pins, cur["resolved"] if cur["ok"] else {}, notes))
    return p


def _migrate_v1_unlocked(path: "Path | None" = None) -> bool:
    """Rewrite a legacy v1 file as v2 (portal resolver only). Keeps a
    one-time ``.model_defaults.v1.bak`` beside it. True iff migrated."""
    p = _path(path)
    cur = read(p)
    if not cur["legacy"]:
        return False
    notes = {}
    if cur["pins"]["b"] == NONE_PIN:
        notes["b"] = "migrated from v1 default_b: null"
    try:
        bak = p.with_name(".model_defaults.v1.bak")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
        _atomic_write(p, _dump(cur["pins"], {}, notes))
    except OSError as e:
        _log.warning("model_defaults: couldn't migrate %s: %s", p, e)
        return False
    return True


def write_cache(resolution: dict[str, Any], path: "Path | None" = None) -> bool:
    """Replace the ``resolved`` block (atomic); pins are re-emitted as read.
    *resolution* is ``BootResolution.to_dict()`` (plus optional ``warm``).
    Returns False (after one warning) when the file can't be written --
    a read-only checkout must never block boot (FM10)."""
    with _WRITE_LOCK:
        return _write_cache_unlocked(resolution, path)


def update_warm(slot: str, status: str, path: "Path | None" = None) -> bool:
    """Record a slot's warm outcome inside ``resolved.warm``."""
    with _WRITE_LOCK:
        return _update_warm_unlocked(slot, status, path)


def write_pin(slot: str, value: "str | None", path: "Path | None" = None) -> Path:
    """Set one pin. *value*: a model id, ``"none"`` (slot b only), or
    ``None`` / ``"auto"`` to clear. Preserves the other pin and the cache.
    A legacy v1 file is upgraded in the same write. Raises ValueError for
    a bad slot / ``none`` on slot a, OSError if unwritable."""
    with _WRITE_LOCK:
        return _write_pin_unlocked(slot, value, path)


def migrate_v1(path: "Path | None" = None) -> bool:
    """Rewrite a legacy v1 file as v2 (portal resolver only). Keeps a
    one-time ``.model_defaults.v1.bak`` beside it. True iff migrated."""
    with _WRITE_LOCK:
        return _migrate_v1_unlocked(path)


def write_defaults(
    default_a: str, default_b: "str | None" = None, path: "Path | None" = None
) -> Path:
    """DEPRECATED shim — maps to ``write_pin`` for both slots (a missing
    default_b becomes the ``none`` pin, as the old explicit-null did).
    Remove next release."""
    warnings.warn("model_defaults.write_defaults() is deprecated; use write_pin()",
                  DeprecationWarning, stacklevel=2)
    write_pin("a", default_a, path)
    return write_pin("b", default_b or NONE_PIN, path)


# ── reporting ──────────────────────────────────────────────────────

def resolve_slots() -> dict[str, Any]:
    """What's active for each slot right now, with where it came from.

    Routes through ``arail.config`` (lazy import — avoids the circular
    import at module load time) so importing it applies both .env and this
    file exactly the way the running portal does.
    """
    import arail.config  # noqa: F401 — import side effect: applies .env + this file

    p = _path()
    data = read(p)
    res = data["resolved"]

    def _meta(slot: str) -> dict[str, Any]:
        s = res.get(slot) if isinstance(res.get(slot), dict) else {}
        return {"source": s.get("source"), "reason": s.get("reason"),
                "size_gb": s.get("size_gb")}

    return {
        "path": str(p),
        "version": data["version"],
        "pins": data["pins"],
        "default_a": os.environ.get("MODEL_NAME") or "llama-ai-eng",
        "default_b": os.environ.get("AEROLLM_MODEL") or None,
        "a": _meta("a"),
        "b": _meta("b"),
    }


def _fmt_gb(x: "float | None") -> str:
    return f"{x:.1f} GB" if isinstance(x, (int, float)) else "size unknown"


def _gather_slot_facts() -> dict[str, Any]:
    """Projection of the cached resolution (no network, no second
    computation). A resolved slot is installed and fits by construction."""
    slots = resolve_slots()
    a_model, b_model = slots["default_a"], slots["default_b"]
    a_hint = None
    if slots["a"]["source"] in (None, "none") or not a_model:
        a_hint = ("ollama pull llama3.2:1b && ollama create llama-ai-eng "
                  "-f models/ai-eng/Modelfile.default")
    return {
        "a": {"model": a_model, "source": slots["a"]["source"],
              "reason": slots["a"]["reason"], "size_gb": slots["a"]["size_gb"],
              "install_command": a_hint},
        "b": ({"model": b_model, "source": slots["b"]["source"],
               "reason": slots["b"]["reason"], "size_gb": slots["b"]["size_gb"]}
              if b_model else {"model": None, "source": slots["b"]["source"],
                               "reason": slots["b"]["reason"], "size_gb": None}),
    }


def report() -> str:
    """The honest per-slot lines for the CLI readiness banner, including
    where each choice came from. Reads the cache only; never raises."""
    facts = _gather_slot_facts()
    a, b = facts["a"], facts["b"]
    lines = ["  Models:"]
    lines.append(
        f"    A (resident):  {a['model'] or '(none)':<28} {_fmt_gb(a['size_gb']):>10}   "
        f"source: {a['source'] or 'env/default'}")
    if a["reason"]:
        lines.append(f"                   {a['reason']}")
    if a["install_command"]:
        lines.append(f"                   {a['install_command']}")
    if b["model"]:
        lines.append(
            f"    B (aeroLLM):   {b['model']:<28} {_fmt_gb(b['size_gb']):>10}   "
            f"source: {b['source'] or 'env/default'}")
    else:
        lines.append("    B (aeroLLM):   (none)")
    if b["reason"]:
        lines.append(f"                   {b['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover — thin CLI shim, exercised via subprocess tests
    import argparse
    import json
    import sys

    _parser = argparse.ArgumentParser(
        prog="python -m arail.model_defaults",
        description=(
            "Read-only report of the two model slots (pins + last-resolved "
            "cache). Never writes and never resolves — the portal is the "
            "only resolver. Used by start.sh's readiness banner."
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
        print(resolve_slots().get(_args.get) or "")
    elif _args.json:
        _d = read()
        _s = resolve_slots()
        print(json.dumps({"version": _d["version"], "path": _s["path"],
                          "pins": _d["pins"], "resolved": _d["resolved"],
                          "default_a": _s["default_a"], "default_b": _s["default_b"]}))
    else:
        print(report())
    sys.exit(0)
