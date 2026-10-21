"""Librarian term scout — finds the terms a mounted World is missing.

The MCP-in-2023 loop: a phrase starts recurring across the lab's own
signals (PKB inbox, research notes, approved docs). When it turns up in
enough independent places, the Librarian drafts a term proposal for the
mounted World and files it for the operator's review. Nothing compiles
in without a human click, every proposal passes the closed-sourced-graph
gate on approval, and provenance stays honest:

- locally-drafted definitions are ``model:<name>`` → tier *model-asserted*
  ("dreamed");
- only a real captured URL (consented Wikipedia enrichment, when the lab
  isn't airgapped) earns *sourced*.

Portal-free like ``world_forge`` — the portal routes and the Librarian
agent import this module, never the other way round.

State lives in a per-world sidecar ``librarian-scout.json`` beside the
sealed bundle files (the ``review.json``/``evolution.json`` precedent —
sidecars are never part of the seal):

    {"schema": "arail.librarian-scout/v1",
     "last_scan": "<iso8601>",
     "candidates": {<slug>: {"term", "evidence": [...], "count",
                             "kinds": [...], "first_seen"}},
     "proposals":  [{"id", "slug", "term", "category", "short",
                     "definition", "example", "related", "source",
                     "tier", "evidence", "status"}],
     "rejected":   {<slug>: {"ts", "by"}}}

``rejected`` is the never-re-propose memory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from arail import world_forge as wf

log = logging.getLogger(__name__)

SCHEMA = "arail.librarian-scout/v1"
EVIDENCE_WINDOW_DAYS = 30
MAX_EVIDENCE_PER_CANDIDATE = 12
MAX_PROPOSALS_PER_SCAN = 5
_TEXT_SUFFIXES = {".md", ".txt", ".markdown"}
_MAX_FILE_BYTES = 256_000

# Capitalized multi-word phrases ("Model Context Protocol") and standalone
# acronyms ("MCP"). Deliberately cheap — mining never calls a model.
_PHRASE_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{2,}(?:[ \-][A-Z][a-zA-Z]{2,}){1,3})\b")
_ACRONYM_RE = re.compile(r"\b([A-Z]{3,6})\b")

# Phrase-leading words that mark prose, not terminology.
_STOP_STARTS = {
    "the", "this", "that", "these", "those", "with", "from", "when",
    "what", "where", "which", "while", "then", "your", "their", "our",
    "and", "for", "but", "not", "you", "are", "was", "were", "has",
    "have", "how", "why", "who", "its", "any", "all", "one", "two",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}
_STOP_ACRONYMS = {
    "THE", "AND", "NOT", "FOR", "PDF", "URL", "HTML", "JSON", "YAML",
    "TODO", "NOTE", "WARN", "INFO", "ERROR", "README", "FAQ", "USA",
    "UTC", "GMT", "IMPORTANT",
}


# ── sidecar I/O ─────────────────────────────────────────────────────────

def sidecar_path(bundle_dir: Path) -> Path:
    return bundle_dir / "librarian-scout.json"


def _skeleton() -> dict:
    return {"schema": SCHEMA, "last_scan": None,
            "candidates": {}, "proposals": [], "rejected": {}}


def load_sidecar(bundle_dir: Path) -> dict:
    path = sidecar_path(bundle_dir)
    if not path.exists():
        return _skeleton()
    try:
        doc = json.loads(path.read_bytes())
        if doc.get("schema") != SCHEMA:
            return _skeleton()
        for key, default in (("candidates", {}), ("proposals", []),
                             ("rejected", {})):
            doc.setdefault(key, default)
        return doc
    except Exception:  # noqa: BLE001 — corrupt sidecar never blocks a scan
        return _skeleton()


def save_sidecar(bundle_dir: Path, doc: dict) -> None:
    path = sidecar_path(bundle_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _mounted_bundle_dir() -> Optional[Path]:
    from arail import world_mount as wm
    record = wm.current_mount()
    if record is None:
        return None
    catalog = wm._default_worlds_dir() / record.world
    return catalog if (catalog / "manifest.json").exists() else None


def load_mounted_sidecar() -> Optional[dict]:
    bundle_dir = _mounted_bundle_dir()
    if bundle_dir is None:
        return None
    return load_sidecar(bundle_dir)


# ── mining (no model calls) ─────────────────────────────────────────────

def _signal_files(pkb_root: Path) -> Iterable[tuple[str, Path]]:
    """Yield (kind, path) pairs of the lab's textual signals."""
    for kind, sub in (("pkb", "inbox"), ("pkb", "sources"),
                      ("research", "research"), ("research", "experiments"),
                      ("docs", "compiled")):
        base = pkb_root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES:
                yield kind, p


def _extract_phrases(text: str) -> list[str]:
    out: list[str] = []
    for m in _PHRASE_RE.finditer(text):
        phrase = m.group(1).strip()
        if phrase.split()[0].lower() in _STOP_STARTS:
            continue
        out.append(phrase)
    for m in _ACRONYM_RE.finditer(text):
        if m.group(1) not in _STOP_ACRONYMS:
            out.append(m.group(1))
    return out


def _excerpt(text: str, phrase: str, radius: int = 90) -> str:
    idx = text.find(phrase)
    if idx < 0:
        return ""
    lo, hi = max(0, idx - radius), min(len(text), idx + len(phrase) + radius)
    return " ".join(text[lo:hi].split())


def mine_candidates(pkb_root: Path, known_slugs: set[str],
                    rejected: dict, since_ts: float = 0.0) -> list[dict]:
    """Scan signal files changed since ``since_ts`` for candidate phrases.

    Returns [{"slug", "term", "kind", "path", "excerpt", "ts"}] — one entry
    per (phrase, file). Pure text scanning; no model involved.
    """
    found: list[dict] = []
    for kind, path in _signal_files(pkb_root):
        try:
            st = path.stat()
            if st.st_mtime <= since_ts or st.st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(errors="replace")
        except OSError:
            continue
        seen_here: set[str] = set()
        for phrase in _extract_phrases(text):
            slug = wf.slugify(phrase)
            if (not slug or slug in known_slugs or slug in rejected
                    or slug in seen_here or len(slug) < 3):
                continue
            seen_here.add(slug)
            try:
                rel = str(path.relative_to(pkb_root))
            except ValueError:
                rel = path.name
            found.append({"slug": slug, "term": phrase, "kind": kind,
                          "path": rel, "excerpt": _excerpt(text, phrase),
                          "ts": st.st_mtime})
    return found


def merge_evidence(doc: dict, found: list[dict],
                   now: Optional[float] = None) -> dict:
    """Fold freshly-mined sightings into the candidate accumulator and
    expire evidence older than the rolling window."""
    now = now or time.time()
    horizon = now - EVIDENCE_WINDOW_DAYS * 86400
    cands: dict = doc.setdefault("candidates", {})
    for hit in found:
        slug = hit["slug"]
        c = cands.setdefault(slug, {"term": hit["term"], "evidence": [],
                                    "first_seen": now})
        if any(e.get("path") == hit["path"] and e.get("kind") == hit["kind"]
               for e in c["evidence"]):
            continue  # one sighting per file
        c["evidence"].append({"kind": hit["kind"], "path": hit["path"],
                              "excerpt": hit["excerpt"], "ts": hit["ts"]})
        c["evidence"] = c["evidence"][-MAX_EVIDENCE_PER_CANDIDATE:]
    stale = []
    for slug, c in cands.items():
        c["evidence"] = [e for e in c["evidence"] if e.get("ts", now) >= horizon]
        c["count"] = len(c["evidence"])
        c["kinds"] = sorted({e["kind"] for e in c["evidence"]})
        if not c["evidence"]:
            stale.append(slug)
    for slug in stale:
        del cands[slug]
    return doc


def _ubiquity_mentions() -> int:
    try:
        return max(2, int(os.getenv("ARAIL_LIBRARIAN_UBIQUITY", "4")))
    except ValueError:
        return 4


def ripe_candidates(doc: dict) -> list[tuple[str, dict]]:
    """Ubiquity threshold: a candidate ripens when its evidence spans ≥2
    distinct signal kinds OR reaches the mention floor — a term must recur
    across independent signals before it earns a proposal."""
    proposed = {p["slug"] for p in doc.get("proposals", [])}
    ripe = []
    for slug, c in doc.get("candidates", {}).items():
        if slug in proposed:
            continue
        if len(c.get("kinds", [])) >= 2 or c.get("count", 0) >= _ubiquity_mentions():
            ripe.append((slug, c))
    ripe.sort(key=lambda kv: (-kv[1].get("count", 0), kv[0]))
    return ripe


# ── enrichment (consented, airgap-aware) ────────────────────────────────

def _try_wikipedia_source(term: str) -> Optional[tuple[str, str]]:
    """Consented one-page Wikipedia lookup → (url, extract) or None.

    Requires the lab to be off airgap AND a standing/auto-approved consent
    for wikipedia.org (the Curator pattern) — a scheduled agent never
    self-approves egress. Any failure degrades to the dreamed tier."""
    try:
        from arail.airgap import is_airgapped
        if is_airgapped():
            return None
        from arail.agents.consent import ConsentStore
        store = ConsentStore()
        req = store.request_access(
            "https://en.wikipedia.org/",
            f"Librarian term scout: source '{term[:80]}'", agent="librarian")
        if req.get("status") not in ("approved", "auto_approved"):
            return None
        import requests
        title = term.replace(" ", "_")
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            timeout=10, headers={"accept": "application/json"})
        if r.status_code != 200:
            return None
        data = r.json()
        url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page")
        extract = str(data.get("extract") or "").strip()
        if url and extract and data.get("type") == "standard":
            return url, extract
    except Exception as e:  # noqa: BLE001
        log.debug("librarian scout: wikipedia enrichment skipped: %s", e)
    return None


# ── drafting (one model call per ripe candidate) ────────────────────────

def draft_proposal(slug: str, candidate: dict, spec: dict,
                   terms: list[dict], router: Any) -> Optional[dict]:
    """Draft one term proposal with the given router. Returns a proposal
    dict (status=pending) or None when the model produced nothing usable."""
    cats = [str(c.get("id", "")) for c in spec.get("categories", [])]
    known = [t["slug"] for t in terms]
    excerpts = " / ".join(
        e.get("excerpt", "") for e in candidate.get("evidence", [])[:3])
    prompt = (
        f'The knowledge World "{spec.get("display_name", spec.get("slug"))}" '
        f'is missing the term "{candidate["term"]}", which keeps appearing '
        f'in the lab\'s notes: "{excerpts[:600]}". '
        f'Return JSON: {{"category": one of {cats}, '
        f'"short": "<=100 char gloss", '
        f'"definition": "2-3 sentence definition", '
        f'"example": "one concrete usage sentence", '
        f'"related": [up to 4 slugs from {known[:60]}]}}.'
    )
    try:
        # L3 (ARCHITECTURE.md): librarian_scout is a top-level module the
        # loader never touches, called via to_thread from
        # _builtin_librarian.py's scout_once() -- attributed to "librarian"
        # (the FIXED_LANES id), not "librarian_scout" (not itself a lane).
        from arail import agent_context
        with agent_context.agent_call("librarian"):
            resp = router.complete(prompt, max_tokens=500, temperature=0.3, top_p=0.9)
    except Exception as e:  # noqa: BLE001
        log.warning("librarian scout: draft call failed: %s", e)
        return None
    parsed = wf.loose_json(getattr(resp, "text", "") or "")
    if not isinstance(parsed, dict):
        return None
    category = str(parsed.get("category", ""))
    if category not in cats:
        category = cats[0] if cats else ""
    related = [s for s in (parsed.get("related") or [])
               if isinstance(s, str) and s in set(known) and s != slug][:4]
    definition = str(parsed.get("definition", "")).strip()
    if not definition:
        return None

    model_name = getattr(resp, "model", None) or "local"
    source = wf._source_tag_from_model(model_name)
    enriched = _try_wikipedia_source(candidate["term"])
    if enriched is not None:
        source, extract = enriched[0], enriched[1]
        definition = extract[:wf.MAX_DEFINITION] or definition

    return {
        "id": uuid.uuid4().hex[:12],
        "slug": slug,
        "term": candidate["term"],
        "category": category,
        "short": str(parsed.get("short", "") or candidate["term"])[:wf.MAX_SHORT],
        "definition": definition[:wf.MAX_DEFINITION],
        "example": str(parsed.get("example", ""))[:wf.MAX_EXAMPLE],
        "related": related,
        "source": source,
        "tier": wf.tier_of_source(source),
        "evidence": candidate.get("evidence", []),
        "status": "pending",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ── the full pass ───────────────────────────────────────────────────────

def scout_mounted_world(router: Any = None,
                        pkb_root: Optional[Path] = None) -> dict:
    """One complete scout pass on the mounted World: mine → merge →
    ripen → draft → file. Blocking (call via a thread from async code).
    Returns {"world", "mined", "ripe", "proposed"}."""
    bundle_dir = _mounted_bundle_dir()
    if bundle_dir is None:
        return {"world": None, "mined": 0, "ripe": 0, "proposed": 0}

    spec = json.loads((bundle_dir / "spec.json").read_bytes())
    terms = json.loads((bundle_dir / "terms.json").read_bytes())
    if isinstance(terms, dict):
        terms = terms.get("terms", [])
    known = {t["slug"] for t in terms}
    for t in terms:
        for aka in (t.get("aka") or []):
            known.add(wf.slugify(aka))

    if pkb_root is None:
        from arail.pkb import _pkb_root
        pkb_root = _pkb_root()

    doc = load_sidecar(bundle_dir)
    since = 0.0
    if doc.get("last_scan"):
        try:
            since = time.mktime(time.strptime(doc["last_scan"],
                                              "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except (ValueError, OverflowError):
            since = 0.0

    found = mine_candidates(pkb_root, known, doc.get("rejected", {}), since)
    merge_evidence(doc, found)
    ripe = ripe_candidates(doc)

    proposed = 0
    if ripe:
        if router is None:
            from arail.router import ModelRouter
            router = ModelRouter(billing_source="agent")
        for slug, candidate in ripe[:MAX_PROPOSALS_PER_SCAN]:
            proposal = draft_proposal(slug, candidate, spec, terms, router)
            if proposal is not None:
                doc["proposals"].append(proposal)
                proposed += 1

    doc["last_scan"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_sidecar(bundle_dir, doc)
    return {"world": spec.get("slug"), "mined": len(found),
            "ripe": len(ripe), "proposed": proposed}
