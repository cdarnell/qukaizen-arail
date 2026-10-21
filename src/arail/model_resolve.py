"""Boot-time model auto-resolution — slot A (fast/resident, Ollama) and
slot B (deep/streaming, aeroLLM) picked from what is installed and what
this machine can hold.

Sprint 2026-10-20-boot-model-autoresolve. Precedence per slot::

    pin (model_defaults.yaml ``pins.<slot>``)
      > env preference (.env MODEL_NAME / AEROLLM_MODEL, if not stamped by us)
      > auto (installed x fits x ranked)
      > none (with an honest reason)

Every candidate at every level is validated (installed, completion
capable, under the ceiling, inside the RAM budget). An invalid pin or env
value never blocks anything: it falls through to the next level and
records a ``reason``. The one exception is a pin of ``"none"`` for B,
which is honoured as-is (it is the operator's crash workaround).

``resolve_boot`` never raises and only ever talks to a loopback Ollama.
This module owns the fit/candidate helpers formerly in
``portal/models_api.py``.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

# Same ceiling as the aeroLLM preload loop: resident A + B together may
# claim at most this fraction of total RAM.
RAM_BUDGET_FRACTION = 0.60
# /api/show is one request per model; cap it so a big install can't stall boot.
SHOW_CAP = 8
# Overall budget for the resolver when run via resolve_boot_bounded().
RESOLVE_TIMEOUT_S = 5.0
_HTTP_TIMEOUT_S = 1.5

# Held around resolve + cache write so a pin write racing a boot resolve
# cannot interleave with the cache rewrite (FM13).
LOCK = threading.RLock()

_TIER_ORDER = {"recommended": 0, "flagship": 1, "optional": 2}
SHIPPED_DEFAULT = "llama-ai-eng"

PULL_HINT = ("ollama pull llama3.2:1b && ollama create llama-ai-eng "
             "-f models/ai-eng/Modelfile.default")


# ── result types ───────────────────────────────────────────────────

@dataclass
class SlotResolution:
    model: Optional[str] = None
    source: str = "none"          # pin | env | auto | cache | none
    reason: Optional[str] = None
    size_gb: Optional[float] = None
    params_b: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BootResolution:
    a: SlotResolution = field(default_factory=SlotResolution)
    b: SlotResolution = field(default_factory=SlotResolution)
    degraded: List[Dict[str, Any]] = field(default_factory=list)
    resolved_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"resolved_at": self.resolved_at, "a": self.a.to_dict(),
                "b": self.b.to_dict(), "degraded": list(self.degraded)}


# ── fit helpers (moved verbatim from portal/models_api.py) ─────────

def fit_primary(model_id: str) -> Dict[str, Any]:
    from arail import model_specs as _specs
    from arail.registry.ceiling import PRIMARY_CEILING_B
    params, _src = _specs.resolve_params_b(model_id)
    if params is None:
        return {"verdict": "unknown", "detail": "parameter count unknown"}
    if params < PRIMARY_CEILING_B:
        return {"verdict": "fits", "detail": f"~{params:g}B"}
    return {"verdict": "too_big",
            "detail": f"~{params:g}B — at/over the {PRIMARY_CEILING_B:g}B "
                      "primary ceiling"}


def fit_secondary(model_id: str, model_path: Optional[str]) -> Dict[str, Any]:
    from arail import hardware as _hw
    from arail import model_specs as _specs
    cap = _hw.secondary_model_cap_b()
    params, _src = _specs.resolve_params_b(model_id, model_path)
    if params is None:
        return {"verdict": "unknown", "detail": "parameter count unknown"}
    if params <= cap:
        return {"verdict": "fits", "detail": f"~{params:g}B (cap ~{cap:g}B)"}
    return {"verdict": "too_big",
            "detail": f"~{params:g}B — over the ~{cap:g}B this machine's "
                      "discovered RAM can hold stably"}


def slot_a_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    """Installed Ollama models plus catalog suggestions (override UI)."""
    from arail.chat import _ollama_installed_models, load_catalog
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _ollama_installed_models():
        rid = m["id"]
        rows.append({"id": rid, "name": rid, "source": "ollama",
                     "size_gb": m.get("size_gb"), "present": True,
                     "fit": fit_primary(rid), "install_command": None,
                     "hf_url": None})
        seen.add(rid)
        seen.add(rid.split(":", 1)[0])
    catalog = sorted(
        (e for e in load_catalog() if e.source == "ollama"),
        key=lambda e: _TIER_ORDER.get(e.tier, 3))
    for e in catalog:
        if e.id in seen or e.id.split(":", 1)[0] in seen:
            continue
        rows.append({"id": e.id, "name": e.name, "source": "ollama",
                     "size_gb": e.size_gb, "present": False,
                     "fit": fit_primary(e.id),
                     "install_command": e.install or None, "hf_url": None})
        seen.add(e.id)
    return rows


def slot_b_candidates(airgapped: bool) -> List[Dict[str, Any]]:
    """Installed model dirs plus catalog suggestions (override UI)."""
    from arail.chat import _mlx_dir_installed_models, load_catalog
    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _mlx_dir_installed_models():
        rid = m["id"]
        path = os.path.join(models_dir, rid)
        rows.append({"id": rid, "name": rid, "source": "mlx",
                     "size_gb": m.get("size_gb"), "present": True,
                     "fit": fit_secondary(rid, path), "install_command": None,
                     "hf_url": None})
        seen.add(rid)
    catalog = sorted(
        (e for e in load_catalog()
         if e.source in ("mlx", "hf") and not e.id.startswith("__")),
        key=lambda e: _TIER_ORDER.get(e.tier, 3))
    for e in catalog:
        if e.id in seen:
            continue
        path = os.path.join(models_dir, e.id)
        present = os.path.isdir(path)
        hf_url = None if (present or airgapped) else (
            f"https://huggingface.co/{e.hf_repo}" if e.hf_repo else None)
        rows.append({"id": e.id, "name": e.name, "source": e.source,
                     "size_gb": e.size_gb, "present": present,
                     "fit": fit_secondary(e.id, path if present else None),
                     "install_command": None if present else (e.install or None),
                     "hf_url": hf_url})
        seen.add(e.id)
    return rows


# ── loopback-only Ollama access ────────────────────────────────────

def _ollama_base() -> Optional[str]:
    """``http://host:port`` for the local Ollama, or None when the
    configured host is not loopback (resolver never leaves the machine)."""
    raw = (os.getenv("OLLAMA_HOST", "127.0.0.1") or "127.0.0.1").strip()
    raw = raw.split("://", 1)[-1].rstrip("/")
    host, _, port_in_host = raw.partition(":")
    host = host or "127.0.0.1"
    port = os.getenv("OLLAMA_PORT", "").strip() or port_in_host or "11434"
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return None
        except ValueError:
            return None
    return f"http://{host}:{port}"


def _fetch_ollama_tags() -> Optional[List[Dict[str, Any]]]:
    """Installed Ollama models, or None when Ollama is unreachable."""
    base = _ollama_base()
    if base is None:
        return None
    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        return None
    out: List[Dict[str, Any]] = []
    for m in data.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model")
        if not name:
            continue
        size_b = m.get("size") or 0
        details = m.get("details") or {}
        out.append({
            "id": str(name),
            "size_gb": round(size_b / (1024 ** 3), 2) if size_b else None,
            "family": str(details.get("family") or ""),
            "parameter_size": str(details.get("parameter_size") or ""),
        })
    return out


def _fetch_capabilities(name: str) -> Optional[List[str]]:
    """``/api/show`` capabilities for *name*, or None if unavailable."""
    base = _ollama_base()
    if base is None:
        return None
    try:
        req = urllib.request.Request(
            f"{base}/api/show", method="POST",
            data=json.dumps({"model": name}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        caps = data.get("capabilities")
        return [str(c) for c in caps] if isinstance(caps, list) else None
    except Exception:  # noqa: BLE001
        return None


def _looks_like_embedding(name: str, family: str) -> bool:
    n = name.lower()
    return "embed" in n or "embed" in family.lower() or family.lower() in (
        "bert", "nomic-bert")


def _is_completion_capable(m: Dict[str, Any], show_budget: List[int]) -> bool:
    if show_budget[0] > 0:
        show_budget[0] -= 1
        caps = _fetch_capabilities(m["id"])
        if caps is not None:
            return "completion" in caps
    return not _looks_like_embedding(m["id"], m.get("family", ""))


# ── environment facts ──────────────────────────────────────────────

def _total_ram_gb() -> Optional[float]:
    try:
        from arail import hardware as _hw
        return float(_hw.load_or_discover().total_ram_gb)
    except Exception:  # noqa: BLE001
        return None


def _secondary_cap_b() -> Optional[float]:
    try:
        from arail import hardware as _hw
        return float(_hw.secondary_model_cap_b())
    except Exception:  # noqa: BLE001
        return None


def _scan_models_dir() -> List[Dict[str, Any]]:
    try:
        from arail.chat import _mlx_dir_installed_models
        return list(_mlx_dir_installed_models())
    except Exception:  # noqa: BLE001
        return []


def _deep_slot_available() -> Optional[str]:
    """None when slot B can exist at all, else the honest reason."""
    try:
        import importlib.util
        from arail import tier as _tier
        if not _tier.is_maximus():
            return "deep model needs maximus"
        if importlib.util.find_spec("aerollm_api") is None:
            return "aerollm runtime is not installed"
    except Exception:  # noqa: BLE001
        return "deep model unavailable"
    return None


def _short(model_id: str) -> str:
    return model_id.split(":", 1)[0]


def _params_for_a(m: Dict[str, Any]) -> Optional[float]:
    from arail import model_specs as _specs
    params, _src = _specs.resolve_params_b(m["id"])
    if params is not None:
        return params
    ps = (m.get("parameter_size") or "").strip().upper()
    try:
        if ps.endswith("B"):
            return float(ps[:-1])
        if ps.endswith("M"):
            return float(ps[:-1]) / 1000.0
    except ValueError:
        pass
    return None


# ── slot A ─────────────────────────────────────────────────────────

def _match_installed(name: str, pool: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for m in pool:
        if m["id"] == name:
            return m
    for m in pool:
        if _short(m["id"]) == _short(name):
            return m
    return None


def _resolve_a(pin, env_pref, tags, cache_a, total_ram, notes) -> SlotResolution:
    from arail.registry.ceiling import PRIMARY_CEILING_B

    if tags is None:
        notes.append({"slot": "a", "kind": "ollama_down",
                      "detail": "Ollama isn't running — start it with `ollama serve`"})
        # Can't verify anything: an explicit pin is still the operator's intent,
        # then the last verified result, then the .env preference.
        if pin:
            return SlotResolution(model=pin, source="pin",
                                  reason="Ollama unreachable; unverified")
        if cache_a:
            return SlotResolution(model=cache_a, source="cache",
                                  reason="Ollama unreachable; using last resolved model")
        if env_pref:
            return SlotResolution(model=env_pref, source="env",
                                  reason="Ollama unreachable; unverified")
        return SlotResolution(reason="Ollama unreachable and no previous model")

    show_budget = [SHOW_CAP]
    budget_gb = total_ram * RAM_BUDGET_FRACTION if total_ram else None
    eligible: List[Dict[str, Any]] = []
    for m in tags:
        if not _is_completion_capable(m, show_budget):
            continue
        params = _params_for_a(m)
        m = dict(m, params_b=params)
        if params is None or params >= PRIMARY_CEILING_B:
            m["_why"] = ("parameter count unknown" if params is None
                         else f"~{params:g}B is at/over the {PRIMARY_CEILING_B:g}B ceiling")
            m["_ok"] = False
        elif budget_gb is not None and (m.get("size_gb") or 0) > budget_gb:
            m["_why"] = "too large for this machine's memory budget"
            m["_ok"] = False
        else:
            m["_ok"] = True
        eligible.append(m)

    def _res(m, source, reason=None):
        return SlotResolution(model=m["id"], source=source, reason=reason,
                              size_gb=m.get("size_gb"), params_b=m.get("params_b"))

    reasons: List[str] = []
    for source, val in (("pin", pin), ("env", env_pref)):
        if not val:
            continue
        m = _match_installed(val, eligible)
        if m is None:
            hint = f"ollama pull {val}"
            reasons.append(f"`{val}` ({'pinned' if source == 'pin' else 'from .env'}) "
                           f"isn't installed. Run `{hint}`")
            notes.append({"slot": "a", "kind": "missing", "detail": reasons[-1]})
            continue
        if not m["_ok"]:
            reasons.append(f"`{val}` can't be used: {m['_why']}")
            notes.append({"slot": "a", "kind": "unfit", "detail": reasons[-1]})
            continue
        return _res(m, source)

    ok = [m for m in eligible if m["_ok"]]
    if not ok:
        if not eligible and not tags:
            notes.append({"slot": "a", "kind": "missing",
                          "detail": f"No chat model installed. Run: {PULL_HINT}"})
        else:
            notes.append({"slot": "a", "kind": "missing",
                          "detail": f"No usable chat model installed. Run: {PULL_HINT}"})
        return SlotResolution(reason="no completion-capable model installed")

    def rank(m):
        if _short(m["id"]) == SHIPPED_DEFAULT:
            return (0, 0, 0)
        try:
            from arail.chat import load_catalog
            order = [e for e in load_catalog() if e.source == "ollama"]
        except Exception:  # noqa: BLE001
            order = []
        for e in sorted(order, key=lambda e: _TIER_ORDER.get(e.tier, 3)):
            if _short(e.id) == _short(m["id"]) and e.tier in ("recommended", "flagship"):
                return (1, _TIER_ORDER[e.tier], m.get("size_gb") or 0)
        return (2, 0, m.get("size_gb") or 0)

    best = min(ok, key=rank)
    return _res(best, "auto", " ".join(reasons) or None)


# ── slot B ─────────────────────────────────────────────────────────

def _b_candidate(name: str, dirs: List[Dict[str, Any]], cap_b, budget_left_gb):
    """Validate one B candidate. Returns (candidate|None, reason, kind)."""
    from arail import model_specs as _specs
    models_dir = os.getenv("ARAIL_MODELS_DIR", "lab/models")
    path = name if os.path.isabs(name) else os.path.join(models_dir, name)
    if not os.path.isdir(path):
        return None, (f"`{name}` was not found at {path}. Try: "
                      f"hf download mlx-community/{name} --local-dir {path}"), "missing"
    row = next((d for d in dirs if d["id"] == name), None)
    size_gb = row.get("size_gb") if row else None
    params, _src = _specs.resolve_params_b(name, path)
    if params is None:
        return None, f"`{name}`: parameter count unknown", "unfit"
    if cap_b is None or params > cap_b:
        return None, (f"`{name}` (~{params:g}B) is over the ~{cap_b or 0:g}B this "
                      "machine can hold stably"), "unfit"
    if budget_left_gb is not None and size_gb and size_gb > budget_left_gb:
        return None, (f"`{name}` needs ~{size_gb:g} GB but only ~{max(budget_left_gb, 0):.1f} GB "
                      "is left after the fast model"), "insufficient_ram"
    meta = _specs.lookup(name) or {}
    return {"id": name, "size_gb": size_gb, "params_b": params,
            "moe": bool(meta.get("moe")) or "moe" in name.lower()}, None, None


def _resolve_b(pin, env_pref, a, total_ram, skip_b, notes) -> SlotResolution:
    if pin and str(pin).strip().lower() == "none":
        return SlotResolution(source="pin", reason="pinned off")
    unavailable = _deep_slot_available()
    if unavailable:
        return SlotResolution(reason=unavailable)

    dirs = _scan_models_dir()
    cap_b = _secondary_cap_b()
    a_gb = a.size_gb or 0.0
    budget_left = (total_ram * RAM_BUDGET_FRACTION - a_gb) if total_ram else None
    reasons: List[str] = []

    def _skipped(name):
        if skip_b and name == skip_b:
            msg = (f"{name} crashed or was killed while loading last boot — "
                   "skipped. Pin a different deep model or clear the pin to retry.")
            reasons.append(msg)
            notes.append({"slot": "b", "kind": "crashed_last_boot", "detail": msg})
            return True
        return False

    def _out(c, source):
        return SlotResolution(model=c["id"], source=source,
                              reason=" ".join(reasons) or None,
                              size_gb=c["size_gb"], params_b=c["params_b"])

    for source, val in (("pin", pin), ("env", env_pref)):
        if not val or _skipped(val):
            continue
        c, why, kind = _b_candidate(val, dirs, cap_b, budget_left)
        if c is None:
            reasons.append(why)
            notes.append({"slot": "b", "kind": kind, "detail": why})
            continue
        return _out(c, source)

    cands = []
    ram_blocked = None
    for d in dirs:
        if _skipped(d["id"]):
            continue
        c, why, kind = _b_candidate(d["id"], dirs, cap_b, budget_left)
        if c is None:
            if kind == "insufficient_ram":
                ram_blocked = why
            continue
        cands.append(c)
    if cands:
        order = _catalog_order_b()
        best = min(cands, key=lambda c: (0 if c["moe"] else 1,
                                         order.get(c["id"], 3),
                                         -(c["params_b"] or 0)))
        return _out(best, "auto")
    if ram_blocked:
        notes.append({"slot": "b", "kind": "insufficient_ram", "detail": ram_blocked})
        return SlotResolution(reason="insufficient_ram: " + ram_blocked)
    return SlotResolution(reason=" ".join(reasons) or "no deep model installed")


def _catalog_order_b() -> Dict[str, int]:
    try:
        from arail.chat import load_catalog
        return {e.id: _TIER_ORDER.get(e.tier, 3) for e in load_catalog()
                if e.source in ("mlx", "hf")}
    except Exception:  # noqa: BLE001
        return {}


# ── entry points ───────────────────────────────────────────────────

def resolve_boot(*, pins: Optional[Dict[str, Any]] = None,
                 env_pref: Optional[Dict[str, Any]] = None,
                 now: Optional[float] = None,
                 cache: Optional[Dict[str, Any]] = None,
                 skip_b: Optional[str] = None) -> BootResolution:
    """Resolve both slots. Always returns; never raises; loopback only.

    *pins* / *env_pref* are ``{"a": str|None, "b": str|None}`` (B pin may be
    the literal ``"none"``). *cache* is the previous ``resolved`` block, used
    only when Ollama is unreachable. *skip_b* names a deep model that
    crashed the process on the previous boot.
    """
    pins = pins or {}
    env_pref = env_pref or {}
    cache = cache or {}
    notes: List[Dict[str, Any]] = []
    ts = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))
    try:
        total_ram = _total_ram_gb()
        tags = _fetch_ollama_tags()
        cache_a = ((cache.get("a") or {}).get("model")
                   if isinstance(cache.get("a"), dict) else None)
        a = _resolve_a(pins.get("a"), env_pref.get("a"), tags, cache_a,
                       total_ram, notes)
        b = _resolve_b(pins.get("b"), env_pref.get("b"), a, total_ram,
                       skip_b, notes)
        return BootResolution(a=a, b=b, degraded=notes, resolved_at=stamp)
    except Exception as exc:  # noqa: BLE001 — boot must never fail on this
        _log.warning("model_resolve: resolver failed: %s", exc)
        return _fallback(cache, stamp, "resolver_timeout",
                         f"Model resolver failed ({type(exc).__name__}); using last result",
                         skip_b=skip_b)


def _fallback(cache: Dict[str, Any], stamp: str, kind: str, detail: str,
              skip_b: Optional[str] = None) -> BootResolution:
    """Last-known result when the resolver can't run. The cache written during
    a crashing boot names the crashing deep model, so *skip_b* is honoured
    here too (FM5): that model is never re-picked from the cache."""
    notes: List[Dict[str, Any]] = [{"slot": "a", "kind": kind, "detail": detail}]

    def _slot(key):
        c = cache.get(key) if isinstance(cache.get(key), dict) else {}
        model = c.get("model")
        if key == "b" and model and skip_b and model == skip_b:
            msg = (f"{model} crashed or was killed while loading last boot — "
                   "skipped. Pin a different deep model or clear the pin to retry.")
            notes.append({"slot": "b", "kind": "crashed_last_boot", "detail": msg})
            return SlotResolution(model=None, source="none", reason=msg)
        return SlotResolution(model=model, source="cache" if model else "none",
                              reason=detail, size_gb=c.get("size_gb"))
    a, b = _slot("a"), _slot("b")
    return BootResolution(a=a, b=b, resolved_at=stamp, degraded=notes)


def resolve_boot_bounded(*, timeout: float = RESOLVE_TIMEOUT_S, **kw) -> BootResolution:
    """``resolve_boot`` under a hard wall-clock cap (FM9). On timeout the
    cache is kept and a ``resolver_timeout`` degraded note is recorded."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                 thread_name_prefix="model-resolve")
    fut = pool.submit(resolve_boot, **kw)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return _fallback(kw.get("cache") or {}, stamp, "resolver_timeout",
                         f"Model resolver took over {timeout:g}s; using last result",
                         skip_b=kw.get("skip_b"))
    finally:
        pool.shutdown(wait=False)


# ── resolve + persist + stamp + reconcile (the portal's single entry) ──

_LAST: Dict[str, Any] = {}


def last_resolution() -> Dict[str, Any]:
    """The resolution this process produced (``BootResolution.to_dict()``
    plus ``warm``); empty until ``resolve_and_apply`` has run."""
    return _LAST


def resolve_and_apply(*, skip_b: Optional[str] = None,
                      timeout: float = RESOLVE_TIMEOUT_S) -> Dict[str, Any]:
    """Resolve both slots, rewrite the yaml cache (pins untouched), stamp
    MODEL_NAME / AEROLLM_MODEL, and reconcile the registry. Runs under
    ``LOCK`` (FM13). Blocking: call via ``asyncio.to_thread``. Never raises
    for resolver problems; returns the resolution dict with ``saved``
    (cache written?) and ``migrated`` (v1 file upgraded?)."""
    from arail import model_defaults as md
    with LOCK:
        prev = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _LAST.items()}
        migrated = md.migrate_v1()
        data = md.read()
        # An unreadable file may hold `b: none` (the operator's crash
        # workaround): keep the deep slot off for this boot rather than
        # auto-picking a model that may be the one that crashes (QA-3).
        pins = data["pins"] if data["ok"] else (
            {"a": None, "b": "none"} if data["exists"] else {"a": None, "b": None})
        res = resolve_boot_bounded(
            timeout=timeout, pins=pins, env_pref=md.env_preference(),
            cache=data["resolved"], skip_b=skip_b)
        out = res.to_dict()
        # A re-resolve (e.g. pinning slot A) must not wipe the warm status of
        # a slot that is still the same model: carry it forward from this
        # process's last resolution (never from disk -- that is a prior boot).
        prev_warm = prev.get("warm") or {}
        out["warm"] = {
            s: prev_warm[s] for s in ("a", "b")
            if prev_warm.get(s)
            and (prev.get(s) or {}).get("model")
            and (prev.get(s) or {}).get("model") == (out.get(s) or {}).get("model")}
        out["saved"] = md.write_cache(
            {k: v for k, v in out.items() if k not in ("saved", "migrated")})
        out["migrated"] = migrated
        out["file_ok"] = bool(data["ok"])
        if not out["file_ok"]:
            out["degraded"].append({
                "slot": "a", "kind": "cache_unsaved",
                "detail": "model_defaults.yaml is unreadable (fix or delete it) "
                          "-- left untouched, your pins are not applied this "
                          "boot; resolved in memory only"})
        elif not out["saved"]:
            out["degraded"].append({
                "slot": "a", "kind": "cache_unsaved",
                "detail": "couldn't save the model cache (model_defaults.yaml "
                          "not writable) -- resolved in memory only"})
        md.stamp_env(out)
        try:
            from arail.registry import get_registry
            from arail.registry import store as reg_store
            reg = get_registry()
            reg._ensure_loaded()
            reg_store.reconcile_from_resolution(reg, out)
        except Exception as exc:  # noqa: BLE001 — registry hiccup must not block boot
            _log.warning("model_resolve: registry reconcile skipped: %s", exc)
        _LAST.clear()
        _LAST.update(out)
    return out
