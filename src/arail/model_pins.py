"""Pin records for opt-in house models, verified against what Ollama serves.

Sprint 2026-11-18-aiml-slm-engine. ``chat/model_pins.json`` (schema
``arail.model-pins/v1``) names an Ollama tag plus the sha256 of the GGUF
weights blob that tag must serve. A tag alone never passes: ``verify`` reads
``/api/show`` for the exact tag and compares the weights digest, the chat
template digest and every declared serving parameter.

Never raises, loopback Ollama only, no downloads. The ``FROM`` line Ollama
reports contains an absolute home path; only its 64-hex digest is kept.

CLI (read-only)::

    python -m arail.model_pins [--json]

Exit codes: 0 match, 3 pending, 4 not installed, 5 mismatch/unverifiable,
2 usage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "arail.model-pins/v1"
PINS_PATH = Path(__file__).resolve().parent / "chat" / "model_pins.json"
# Ids that must stay guarded even when the file is unreadable.
KNOWN_FAMILIES = ("qkz-aiml-qwen3-1.7b",)
SHOW_TTL_S = 60.0
_HTTP_TIMEOUT_S = 1.5

_TAG_RE = re.compile(r"^qkz-[a-z0-9.-]+:[0-9]+\.[0-9]+\.[0-9]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_FROM_RE = re.compile(r"^\s*FROM\s+\S*sha256[-:]([0-9a-f]{64})\s*$", re.M | re.I)

_STATUSES = ("pending", "pinned", "revoked")
_TOP_KEYS = {"id", "status", "ollama_tag", "weights_sha256", "manifest_digest",
             "serving", "model", "release", "heldout", "filled_by", "note"}
_SERVING_KEYS = {"template_sha256", "parameters", "system", "thinking"}
_PARAM_KEYS = {"stop", "temperature", "top_k", "top_p", "repeat_penalty", "num_ctx"}
_THINK_KEYS = {"capable", "buddy"}
_MODEL_KEYS = {"base", "license", "parameter_count", "context_length", "quantization"}
_RELEASE_KEYS = {"public_name", "version", "isotope_id", "source_repo", "source_commit",
                 "release_record_sha256", "sealed", "verdict_sha256"}
_HELDOUT_KEYS = {"world", "world_sha256", "slugs", "slugs_sha256"}


@dataclass
class PinRecord:
    id: str
    state: str = "pending"          # pending | pinned | revoked | invalid
    reason: str = ""                # why invalid
    ollama_tag: Optional[str] = None
    weights_sha256: Optional[str] = None
    manifest_digest: Optional[str] = None
    template_sha256: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    system: Optional[str] = None
    thinking_capable: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PinCheck:
    # pending | revoked | invalid | not_installed | mismatch | unverifiable | match
    state: str
    reason: str
    id: str = ""
    tag: Optional[str] = None
    weights_sha256: Optional[str] = None
    manifest_digest: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.state == "match"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── loading ────────────────────────────────────────────────────────

def _path() -> Path:
    override = os.getenv("ARAIL_MODEL_PINS_FILE", "").strip()
    return Path(override) if override else PINS_PATH


def _unknown(d: Any, allowed: set, where: str) -> Optional[str]:
    if not isinstance(d, dict):
        return f"{where} must be an object"
    extra = sorted(set(d) - allowed)
    if extra:
        return f"unknown key {extra[0]!r} in {where}"
    return None


def _validate(rec: Any) -> Tuple[Optional[PinRecord], str, str]:
    """-> (record|None, id, error). Strict: unknown keys are rejected."""
    if not isinstance(rec, dict):
        return None, "", "record must be an object"
    rid = rec.get("id")
    if not isinstance(rid, str) or not rid:
        return None, "", "record has no id"
    err = _unknown(rec, _TOP_KEYS, "record")
    if err:
        return None, rid, err
    status = rec.get("status")
    if status not in _STATUSES:
        return None, rid, "status must be pending, pinned or revoked"
    serving = rec.get("serving") or {}
    for d, keys, where in ((serving, _SERVING_KEYS, "serving"),
                           (serving.get("parameters") or {}, _PARAM_KEYS, "serving.parameters"),
                           (serving.get("thinking") or {}, _THINK_KEYS, "serving.thinking"),
                           (rec.get("model") or {}, _MODEL_KEYS, "model"),
                           (rec.get("release") or {}, _RELEASE_KEYS, "release"),
                           (rec.get("heldout") or {}, _HELDOUT_KEYS, "heldout")):
        err = _unknown(d, keys, where)
        if err:
            return None, rid, err
    params = dict(serving.get("parameters") or {})
    tag = rec.get("ollama_tag")
    weights = rec.get("weights_sha256")
    manifest = rec.get("manifest_digest")
    tmpl = serving.get("template_sha256")
    for name, val in (("weights_sha256", weights), ("template_sha256", tmpl)):
        if val is not None and not (isinstance(val, str) and _HEX64.match(val)):
            return None, rid, f"{name} must be 64 lowercase hex characters"
    if manifest is not None and not (isinstance(manifest, str)
                                     and re.match(r"^(sha256:)?[0-9a-f]{64}$", manifest)):
        return None, rid, "manifest_digest must be a sha256 hex digest"
    if tag is not None and not isinstance(tag, str):
        return None, rid, "ollama_tag must be a string"
    if status == "pinned":
        if not (isinstance(tag, str) and _TAG_RE.match(tag)):
            return None, rid, "a pinned record needs a semver ollama_tag (never latest)"
        if weights is None:
            return None, rid, "a pinned record needs weights_sha256"
        if params.get("num_ctx") is None:
            return None, rid, "a pinned record needs serving.parameters.num_ctx"
        sc = (rec.get("release") or {}).get("source_commit")
        if not (isinstance(sc, str) and _HEX40.match(sc)):
            return None, rid, "a pinned record needs release.source_commit (40 hex)"
    return PinRecord(
        id=rid, state=status, ollama_tag=tag, weights_sha256=weights,
        manifest_digest=manifest, template_sha256=tmpl, parameters=params,
        system=serving.get("system"),
        thinking_capable=bool((serving.get("thinking") or {}).get("capable")),
        raw=rec), rid, ""


def _invalid_all(reason: str) -> Dict[str, PinRecord]:
    return {fid: PinRecord(id=fid, state="invalid", reason=reason)
            for fid in KNOWN_FAMILIES}


def load() -> Dict[str, PinRecord]:
    """All pin records by id. Never raises; no network. An unreadable or
    malformed file marks every known family ``invalid`` (unusable, like
    pending)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _invalid_all(f"pin file unreadable ({type(exc).__name__})")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return _invalid_all("pin file has the wrong schema")
    err = _unknown(data, {"schema", "pins"}, "pin file")
    pins = data.get("pins")
    if err or not isinstance(pins, list):
        return _invalid_all(err or "pin file has no pins list")
    out: Dict[str, PinRecord] = {}
    for rec in pins:
        pr, rid, perr = _validate(rec)
        if pr is None:
            return _invalid_all(perr)
        if rid in out:
            return _invalid_all(f"duplicate id {rid!r}")
        out[rid] = pr
    for fid in KNOWN_FAMILIES:
        out.setdefault(fid, PinRecord(id=fid, state="invalid",
                                      reason="no record for this family"))
    return out


def family_of(tag: Optional[str]) -> Optional[PinRecord]:
    """The pin record whose id equals the bare name of *tag*, if any."""
    if not tag or not isinstance(tag, str):
        return None
    bare = tag.strip().split(":", 1)[0].lower()
    return load().get(bare)


# ── verification ───────────────────────────────────────────────────

def _base() -> Optional[str]:
    from arail import model_resolve as _mr
    return _mr._ollama_base()


def _fetch_tags() -> Optional[Dict[str, str]]:
    """``{tag: manifest digest}`` from loopback ``/api/tags``, or None."""
    base = _base()
    if base is None:
        return None
    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        out = {}
        for m in data.get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                out[str(name)] = str(m.get("digest") or "")
        return out
    except Exception:  # noqa: BLE001
        return None


def _fetch_show(tag: str) -> Optional[Dict[str, Any]]:
    """Loopback ``/api/show`` for *tag*, or None."""
    base = _base()
    if base is None:
        return None
    try:
        req = urllib.request.Request(
            f"{base}/api/show", method="POST",
            data=json.dumps({"model": tag}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


_LOCK = threading.Lock()
_SHOW_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_CHECK_CACHE: Dict[Tuple[str, Optional[str]], Tuple[float, PinCheck]] = {}


def clear_caches() -> None:
    with _LOCK:
        _SHOW_CACHE.clear()
        _CHECK_CACHE.clear()


def _facts(tag: str, manifest: str) -> Optional[Dict[str, Any]]:
    """Weights/template/parameter facts for *tag*; cached while the manifest
    digest is unchanged. The raw ``FROM`` path is dropped here."""
    now = time.monotonic()
    key = (tag, manifest)
    with _LOCK:
        hit = _SHOW_CACHE.get(key)
        if hit and now - hit[0] < SHOW_TTL_S:
            return hit[1]
    show = _fetch_show(tag)
    if show is None:
        return None
    m = _FROM_RE.search(str(show.get("modelfile") or ""))
    facts = {
        "weights": m.group(1).lower() if m else None,
        "template_sha256": hashlib.sha256(
            str(show.get("template") or "").encode("utf-8")).hexdigest(),
        "parameters": _parse_params(str(show.get("parameters") or "")),
        "system": show.get("system"),
    }
    with _LOCK:
        _SHOW_CACHE[key] = (now, facts)
    return facts


def _parse_params(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            out.setdefault(parts[0].lower(), []).append(parts[1].strip().strip('"'))
    return out


def _short(d: str) -> str:
    return (d or "")[:8] + "…"


def _param_mismatch(declared: Dict[str, Any], seen: Dict[str, List[str]]) -> Optional[str]:
    for name, want in declared.items():
        if want is None:
            continue
        have = seen.get(name)
        if have is None:
            return name
        if name == "stop":
            wants = want if isinstance(want, list) else [want]
            if {str(w) for w in wants} != set(have):
                return name
            continue
        try:
            if float(have[0]) != float(want):
                return name
        except (TypeError, ValueError):
            return name
    return None


_NOT_STATES = {
    "pending": "{id} is not released yet, so there is nothing to install.",
    "revoked": "{id} was revoked; it cannot be used.",
}


def verify(record: PinRecord, tag: Optional[str] = None) -> PinCheck:
    """Check the model Ollama serves under *tag* against *record*. Never
    raises; loopback only. An explicit *tag* is used exactly -- there is no
    short-name fallback."""
    rid = record.id
    try:
        if record.state in _NOT_STATES:
            return PinCheck(record.state, _NOT_STATES[record.state].format(id=rid), id=rid)
        if record.state == "invalid":
            return PinCheck("invalid", f"{rid} pin record is invalid ({record.reason}); "
                            "it is treated as not released.", id=rid)
        want = record.ollama_tag
        use = want if (tag is None or tag == rid) else tag
        if not use:
            return PinCheck("pending", _NOT_STATES["pending"].format(id=rid), id=rid)
        tags = _fetch_tags()
        unver = PinCheck(
            "unverifiable",
            f"can't check `{use}` against its pin (Ollama is not reachable on "
            "loopback, or did not report the weights digest).", id=rid, tag=use)
        if tags is None:
            return unver
        if use not in tags:
            return PinCheck("not_installed",
                            f"{rid} is not installed (expected Ollama tag `{use}`).",
                            id=rid, tag=use)
        manifest = tags[use]
        facts = _facts(use, manifest)
        if facts is None or not facts["weights"]:
            unver.manifest_digest = manifest or None
            return unver
        seen = facts["weights"]
        base = dict(id=rid, tag=use, weights_sha256=seen, manifest_digest=manifest or None)
        if seen != (record.weights_sha256 or "").lower():
            return PinCheck(
                "mismatch",
                f"`{use}` is installed, but it is not the pinned build (weights "
                f"sha256:{_short(seen)}, pinned sha256:{_short(record.weights_sha256 or '')}).",
                **base)
        if record.template_sha256 and record.template_sha256 != facts["template_sha256"]:
            return PinCheck("mismatch", f"`{use}` is installed, but its chat template "
                            "differs from the pinned build.", **base)
        bad = _param_mismatch(record.parameters, facts["parameters"])
        if bad:
            return PinCheck("mismatch", f"`{use}` is installed, but its serving "
                            f"parameter `{bad}` differs from the pinned build.", **base)
        if record.manifest_digest:
            want_m = record.manifest_digest.replace("sha256:", "")
            if want_m != manifest.replace("sha256:", ""):
                return PinCheck("mismatch", f"`{use}` is installed, but its manifest "
                                "digest differs from the pinned build.", **base)
        return PinCheck("match", f"`{use}` matches the pinned build.", **base)
    except Exception as exc:  # noqa: BLE001 -- verification must never raise
        return PinCheck("unverifiable", f"can't check {rid} ({type(exc).__name__}).", id=rid)


def verify_cached(record: PinRecord, tag: Optional[str] = None,
                  ttl: float = SHOW_TTL_S) -> PinCheck:
    """``verify`` memoised for *ttl* seconds (per-turn use; FM8)."""
    key = (record.id, tag)
    now = time.monotonic()
    with _LOCK:
        hit = _CHECK_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    res = verify(record, tag)
    with _LOCK:
        _CHECK_CACHE[key] = (now, res)
    return res


# ── CLI ────────────────────────────────────────────────────────────

_EXIT = {"match": 0, "pending": 3, "invalid": 3, "revoked": 3,
         "not_installed": 4, "mismatch": 5, "unverifiable": 5}


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    rest = [a for a in args if a != "--json"]
    if rest:
        print("usage: python -m arail.model_pins [--json]", file=sys.stderr)
        return 2
    checks = [verify(r) for r in load().values()]
    if as_json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        for c in checks:
            print(f"{c.id}: {c.state} - {c.reason}")
    return max((_EXIT.get(c.state, 5) for c in checks), default=3)


if __name__ == "__main__":
    sys.exit(main())
