"""The draft pipeline -- port of DDaC's ``scripts/forge-world.mts`` (7-stage
pipeline: SPEC -> SEED -> DISCOVER BFS -> LINK -> DEFINE -> CLOSE -> GATE).

Moved from qukaizen-arail's ``src/arail/world_forge.py`` as part of the
``dac_world`` migration — see
``sprints/2026-07-19-dac-generates-arail-worlds/ARCHITECTURE.md`` (qukaizen-dac).

Framework-free by design: sync functions, injectable router, tolerant
never-raise model-output parsing. The caller (ARAIL's portal layer) owns
async wrapping (``inference_slot`` + ``asyncio.to_thread``), locking, and
endpoints.

**Delta from the moved code (documented in BUILD_LOG.md's "Scope note"):**
the original ``world_forge.forge_world`` defaulted ``router=None`` to
constructing ``arail.router.ModelRouter`` inline. That is a literal
``import arail`` and is forbidden inside this package (Failure F4 — see
ARCHITECTURE.md). ``router`` is now a required parameter: passing ``None``
raises ``ValueError`` instead of silently reaching back into ARAIL. No
existing call site relied on the old fallback (verified: every
``forge_world(...)`` call in qukaizen-arail already passes ``router=``
explicitly).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .gate import GateRefused, GateResult, assert_closed_sourced_graph
from .parsing import first_array, loose_json, slugify
from .provenance import _MODEL_SOURCE_RE, compute_provenance_tier
from .skill import estimate_skill_chars

_log = logging.getLogger(__name__)

# ── Caps (DDaC forge-world.mts DEFINE stage) ─────────────────────────────
MAX_SHORT = 200
MAX_DEFINITION = 600
MAX_EXAMPLE = 300
MAX_RELATED_PER_TERM = 5
MAX_TERMS_SOFT = 150


class ForgeCancelled(Exception):
    """The cancel event was set; nothing was written to disk."""


@dataclass
class ForgeParams:
    subject: str
    slug: str
    max_terms: int = 25
    n_categories: Optional[int] = None
    n_seeds: Optional[int] = None

    def normalized(self) -> "ForgeParams":
        subject = str(self.subject).strip()[:120]
        slug = slugify(self.slug or subject)
        max_terms = max(8, min(150, int(self.max_terms)))
        # Knob mapping: 25 -> 4 cats / 3 seeds - 50 -> 5/4 - 100 -> 6/5.
        n_cats = self.n_categories or (4 if max_terms <= 30 else 5 if max_terms <= 65 else 6)
        n_seeds = self.n_seeds or (3 if max_terms <= 30 else 4 if max_terms <= 65 else 5)
        return ForgeParams(subject, slug, max_terms, n_cats, n_seeds)


@dataclass
class ForgeResult:
    spec: dict
    terms: list[dict]
    gate: GateResult
    tier: str
    counts: dict
    source_tag: str
    stats: dict


# Stage names in pipeline order -- the UI's progress rows key off these.
FORGE_STAGES = ("spec", "seed", "discover", "link", "define", "gate")

ProgressCb = Callable[[str, int, int, str], None]


def _source_tag_from_model(model_name: str) -> str:
    """model:<name> -- lowercase, strip :latest, ':'->'/' (provenance-safe)."""
    name = str(model_name or "").strip().lower()
    name = re.sub(r":latest$", "", name).replace(":", "/")
    tag = f"model:{name}" if name else "model:local"
    return tag if _MODEL_SOURCE_RE.match(tag) else "model:local"


def forge_world(
    params: ForgeParams,
    *,
    router: Any = None,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[threading.Event] = None,
) -> ForgeResult:
    """Draft a World from a subject. Pure in-memory — no disk until sealing.

    Prompts/temperatures are ported verbatim from forge-world.mts. Every model
    response goes through loose_json (tolerant; None -> skip, like the spike).
    ``cancel`` is checked before every model call.

    ``router`` is REQUIRED (an object with ``.complete(prompt, ...)``); unlike
    the original ARAIL implementation this never constructs a router itself
    (see module docstring — that fallback did ``import arail``, which is
    forbidden in this package).
    """
    p = params.normalized()
    if not p.subject:
        raise ValueError("subject required")
    if router is None:
        raise ValueError(
            "router is required (dac_world.forge_world does not construct one; "
            "the caller must inject an object with .complete(prompt, ...))"
        )

    calls = 0
    repair_events = 0
    t0 = time.monotonic()
    source_tag: Optional[str] = None

    def progress(stage: str, done: int, total: int, note: str = "") -> None:
        if progress_cb:
            try:
                progress_cb(stage, done, total, note)
            except Exception:  # noqa: BLE001 — progress must never kill a forge
                pass

    def call_model(prompt: str, temperature: float = 0.3) -> Any:
        nonlocal calls, repair_events, source_tag
        if cancel is not None and cancel.is_set():
            raise ForgeCancelled()
        calls += 1
        try:
            resp = router.complete(prompt, max_tokens=700, temperature=temperature, top_p=0.9)
        except Exception as e:  # noqa: BLE001 — one bad call must not kill the run
            _log.warning("dac_world.forge: model call failed (skipping): %s", e)
            return None
        if source_tag is None and getattr(resp, "model", None):
            source_tag = _source_tag_from_model(resp.model)
        parsed = loose_json(getattr(resp, "text", "") or "")
        if parsed is None:
            repair_events += 1
        return parsed

    # 1. SPEC — categories for the subject.
    progress("spec", 0, 1, "mapping the subject into categories")
    spec_res = call_model(
        f'You are mapping the subject "{p.subject}" into a knowledge World. '
        f'Return JSON: an array "categories" of {p.n_categories} broad sub-areas, '
        f'each {{"id":"kebab-slug","label":"Title Case"}}.'
    )
    raw_cats = first_array((spec_res or {}).get("categories", spec_res) if isinstance(spec_res, dict) else spec_res)
    cats: list[dict] = []
    for c in raw_cats:
        if isinstance(c, dict):
            cid = slugify(str(c.get("id") or c.get("label") or ""))
            label = str(c.get("label") or c.get("id") or "")
        else:
            cid, label = slugify(str(c)), str(c)
        if cid and cid not in {x["id"] for x in cats}:
            cats.append({"id": cid, "label": label})
    cats = cats[: p.n_categories]
    if not cats:
        cats = [{"id": slugify(p.subject), "label": p.subject}]
    declared = {c["id"] for c in cats}
    progress("spec", 1, 1, ", ".join(sorted(declared)))

    # 2. SEED — terms per category.
    terms: dict[str, dict] = {}
    for i, c in enumerate(cats):
        r = call_model(
            f'Subject: "{p.subject}". For the sub-area "{c["label"]}", return JSON array '
            f'"terms" of {p.n_seeds} key concepts, each {{"term":"short name"}}. '
            f"Only fundamental, well-known concepts."
        )
        items = first_array((r or {}).get("terms", r) if isinstance(r, dict) else r)
        for t in items[: p.n_seeds]:
            name = str(t.get("term") or t.get("name") or t).strip() if isinstance(t, dict) else str(t).strip()
            s = slugify(name)
            if s and s not in terms:
                terms[s] = {"slug": s, "term": name, "category": c["id"],
                            "related": [], "source": None}
        progress("seed", i + 1, len(cats), f"{len(terms)} terms seeded")

    # 3. DISCOVER — multi-pass BFS: a real queue so late finds also expand.
    queue = list(terms.keys())
    while queue and len(terms) < p.max_terms:
        ft = terms[queue.pop(0)]
        r = call_model(
            f'Subject: "{p.subject}". For the concept "{ft["term"]}", return JSON array '
            f'"related" of up to 4 directly-associated concepts in this subject, '
            f'each {{"term":"short name"}}.'
        )
        items = first_array((r or {}).get("related", r) if isinstance(r, dict) else r)
        for x in items[:4]:
            name = str(x.get("term") or x.get("name") or x).strip() if isinstance(x, dict) else str(x).strip()
            s = slugify(name)
            if not s or s in terms or len(terms) >= p.max_terms:
                continue
            terms[s] = {"slug": s, "term": name, "category": ft["category"],
                        "related": [], "source": None}
            queue.append(s)  # newly-discovered terms get expanded too
        progress("discover", len(terms), p.max_terms, f"exploring from {ft['term']}")

    # 3b. LINK — connect every term to the EXISTING set: dense AND closed by
    # construction (edges only ever point at known slugs).
    roster = ", ".join(f"{t['slug']} ({t['term']})" for t in terms.values())
    for i, t in enumerate(terms.values()):
        r = call_model(
            f'Subject: "{p.subject}". From THIS list of known concepts:\n{roster}\n'
            f'Return JSON array "related" of the slugs most directly associated with '
            f'"{t["term"]}" (up to {MAX_RELATED_PER_TERM}, choose ONLY slugs from the list, '
            f'exclude "{t["slug"]}").',
            temperature=0.1,
        )
        items = first_array((r or {}).get("related", r) if isinstance(r, dict) else r)
        for x in items[:MAX_RELATED_PER_TERM]:
            s = slugify(str(x.get("slug") or x.get("term") or x)) if isinstance(x, dict) else slugify(str(x))
            if s in terms and s != t["slug"] and s not in t["related"]:
                t["related"].append(s)
        progress("link", i + 1, len(terms), "")

    # 4. DEFINE — prose per term.
    defined = 0
    for i, t in enumerate(terms.values()):
        r = call_model(
            f'Subject: "{p.subject}". Define the concept "{t["term"]}" as JSON: '
            f'{{"short":"one line","definition":"2-3 sentences","example":"one concrete example"}}.',
            temperature=0.2,
        )
        if isinstance(r, dict):
            t["short"] = str(r.get("short") or "")[:MAX_SHORT]
            t["definition"] = str(r.get("definition") or r.get("short") or t["term"])[:MAX_DEFINITION]
            t["example"] = str(r.get("example") or "")[:MAX_EXAMPLE]
            if t["definition"]:
                defined += 1
        else:
            t["short"] = t["term"]
            t["definition"] = t["term"]
            t["example"] = ""
        # A garbage one-token short from a small model reads terribly in the
        # glossary — fall back to the definition's first line.
        if len(t["short"].strip()) < 3:
            t["short"] = t["definition"][:MAX_SHORT]
        progress("define", i + 1, len(terms), f"{defined} defined")

    # Stamp provenance now that the source tag is known.
    tag = source_tag or "model:local"
    for t in terms.values():
        t["source"] = tag

    # 5. CLOSE — drop dangling/self edges (belt and suspenders for the gate).
    present = set(terms.keys())
    for t in terms.values():
        t["related"] = [s for s in t["related"] if s in present and s != t["slug"]]

    term_list = list(terms.values())
    gate = assert_closed_sourced_graph(term_list, declared)
    if not term_list:
        raise GateRefused(gate, "nothing usable produced — try a richer model or a clearer subject")
    tier, counts = compute_provenance_tier([t["source"] for t in term_list])
    progress("gate", 1, 1, f"{'pass' if gate.ok else 'FAIL'} · tier {tier}")

    display = re.sub(r"\b\w", lambda m: m.group(0).upper(), p.subject)
    spec = {
        "_forged_notice": f'DREAMED by {tag} from "{p.subject}" in the ARAIL World Forge. '
                          f"Model-asserted, UNVERIFIED.",
        "slug": p.slug,
        "display_name": display,
        "categories": cats,
        "knowledge_sources": [{"kind": "model", "ref": tag, "trust": "model-asserted",
                               "holder": tag.removeprefix("model:")}],
    }
    n_edges = sum(len(t["related"]) for t in term_list)
    return ForgeResult(
        spec=spec, terms=term_list, gate=gate, tier=tier, counts=counts, source_tag=tag,
        stats={
            "calls": calls,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "avg_edges": round(n_edges / len(term_list), 2),
            "defined": defined,
            "total": len(term_list),
            "repair_events": repair_events,
            "skill_chars": estimate_skill_chars(len(term_list)),
        },
    )
