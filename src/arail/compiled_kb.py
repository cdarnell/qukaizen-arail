"""The Compiled Knowledge Base — the human-approved layer agents build on.

ARAIL's raw corpus (world terms, forged/grown drafts, ingested notes, and the
agents' own research/experiment outputs) is a *candidate pool*: created, but
unvetted. It must never silently become the truth agents experiment against.
This module is the gate DaC's lifecycle calls for (RAW -> COMPILED, promotion
requires provenance, a human approves): nothing crosses from raw corpus into
the Compiled KB without an explicit human approval.

Design (v1, deliberately lean and reversible):

  * The Compiled KB is a *manifest over the raw corpus*, not a second copy.
    ``lab/pkb/compiled/kb/approved.json`` records, per approved item, its
    pkb-relative path, a title, derived provenance, and the sha256 of the
    exact bytes approved (so a later raw edit is detectable as drift). The
    raw file stays put; approval is a signed pointer to a specific version.
  * ``rejected.json`` remembers dismissals so rejected candidates don't keep
    resurfacing in the review queue.
  * The retrieval gate is a query-time filter: ``pkb.search(..., approved_only
    =True)`` keeps only hits whose path is in ``approved_paths()``. Agents that
    experiment/develop (Researcher, chat RAG, goal drafter) pass approved_only;
    raw stays browsable in the DaC tab but is not what agents build on.

Never raises on read paths — a missing/corrupt manifest reads as "nothing
approved yet" (fail-closed: the gate errs toward *less* agent-visible truth,
never more).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "arail.compiled-kb/v1"

# Where the Compiled KB manifest lives under the pkb root. Sits beside
# docgen's compiled/docs/ (auto-docs) but is a distinct, human-owned tree.
_KB_SUBDIR = ("compiled", "kb")
_APPROVED_FILE = "approved.json"
_REJECTED_FILE = "rejected.json"
# Sticky revocation set: a term the operator explicitly revoked must not be
# silently re-approved by the next World mount. revoke() adds here; an
# explicit approve() (operator action) removes. See auto_approve_world_terms.
_UNAPPROVED_FILE = "unapproved.json"
# Per-root opt-out: presence (or unreadability) disables the mount-time
# world-term auto-approval hook for this root. Deliberately a file, not a
# spec field — it lives with the data and survives a re-mount.
_NO_AUTO_APPROVE_SENTINEL = "no-auto-approve"

# Candidate kinds surfaced in the review queue, mapped from source_kind /
# path. World per-term pages are the headline case; agent outputs are the
# "true experiment/research" the user wants to promote deliberately.
_WORLD_TERM_RE = re.compile(r"^sources/world-[^/]+/terms/[^/]+\.md$")


def _pkb_root() -> Path:
    from arail.config import PKB_ROOT
    return PKB_ROOT


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kb_dir(pkb_root: Path) -> Path:
    return pkb_root.joinpath(*_KB_SUBDIR)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt reads as empty (fail-closed)
        return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ── Manifest access ──────────────────────────────────────────────────────

def _approved_map(pkb_root: Path) -> dict[str, dict[str, Any]]:
    """path -> approved record. Tolerates legacy/list shapes."""
    raw = _load_json(_kb_dir(pkb_root) / _APPROVED_FILE, {})
    if isinstance(raw, dict):
        items = raw.get("items", raw)
    else:
        items = raw
    out: dict[str, dict[str, Any]] = {}
    if isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, dict):
                out[k] = v
    elif isinstance(items, list):
        for v in items:
            if isinstance(v, dict) and v.get("path"):
                out[v["path"]] = v
    return out


def _rejected_set(pkb_root: Path) -> set[str]:
    raw = _load_json(_kb_dir(pkb_root) / _REJECTED_FILE, [])
    if isinstance(raw, dict):
        raw = raw.get("items", [])
    return {p for p in raw if isinstance(p, str)} if isinstance(raw, list) else set()


def _unapproved_set(pkb_root: Path) -> set[str]:
    raw = _load_json(_kb_dir(pkb_root) / _UNAPPROVED_FILE, [])
    if isinstance(raw, dict):
        raw = raw.get("items", [])
    return {p for p in raw if isinstance(p, str)} if isinstance(raw, list) else set()


def _save_unapproved(pkb_root: Path, items: set[str]) -> None:
    _save_json(_kb_dir(pkb_root) / _UNAPPROVED_FILE,
               {"schema": SCHEMA, "items": sorted(items)})


def unapproved_paths(pkb_root: Path | None = None) -> set[str]:
    """Paths a human explicitly revoked — consulted by
    ``auto_approve_world_terms`` so a revoked term is not silently
    re-approved by the next mount. Fails open to the empty set (worst case:
    a revoked term becomes eligible for auto-approval again, never data
    loss)."""
    root = pkb_root or _pkb_root()
    try:
        return _unapproved_set(root)
    except Exception:  # noqa: BLE001
        return set()


def approved_paths(pkb_root: Path | None = None) -> set[str]:
    """The set of pkb-relative paths that are approved into the Compiled KB.

    This is the gate the retrieval layer consults. Fail-closed: any error
    yields the empty set (agents see no approved truth rather than raw)."""
    root = pkb_root or _pkb_root()
    try:
        return set(_approved_map(root).keys())
    except Exception:  # noqa: BLE001
        return set()


def is_approved(rel_path: str, pkb_root: Path | None = None) -> bool:
    return rel_path in approved_paths(pkb_root)


def rejected_paths(pkb_root: Path | None = None) -> set[str]:
    """The set of pkb-relative paths a human has dismissed from the review
    queue. Consumed by the knowledge-graph brain scope (a rejected agent
    output must not linger as a ghost candidate). Fails open to the empty
    set — worst case a dismissed item reappears as a ghost, never data loss."""
    root = pkb_root or _pkb_root()
    try:
        return _rejected_set(root)
    except Exception:  # noqa: BLE001
        return set()


def manifest_present(pkb_root: Path | None = None) -> bool:
    """True iff ``compiled/kb/approved.json`` exists and parses to a
    dict/list shape. False on missing, unreadable, or unparseable — a
    corrupt manifest is deliberately conflated with "never bootstrapped":
    both mean "do not claim the gate is merely empty; tell the operator to
    run bootstrap/doctor." Never raises."""
    root = pkb_root or _pkb_root()
    try:
        path = _kb_dir(root) / _APPROVED_FILE
        if not path.is_file():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(raw, (dict, list))
    except Exception:  # noqa: BLE001
        return False


def gate_state(pkb_root: Path | None = None, *, cheap: bool = False) -> dict[str, Any]:
    """Read-only summary of the Compiled-KB gate for operator-facing
    surfaces. Never raises; every field has a defined value on total
    failure. ``cheap=True`` skips the ``pending_count`` tree walk (hot
    callers: lab_brain per turn, researcher per query) and reports
    ``pending_count = -1`` (meaning "not computed")."""
    try:
        root = pkb_root or _pkb_root()
        enabled = gate_enabled()
        present = manifest_present(root)
        approved = approved_paths(root) if present else set()
        dangling = set(dangling_paths(root)) if present else set()
        live = approved - dangling
        pending = -1 if cheap else pending_count(root)
        if not enabled:
            state = "off"
        elif not present:
            state = "unbootstrapped"
        elif len(live) == 0:
            state = "empty"
        else:
            state = "populated"
        hints = {
            "off": "The approval gate is disabled (ARAIL_APPROVED_ONLY=off) — agents read the raw corpus.",
            "unbootstrapped": "Compiled KB has never been bootstrapped — run ./arailctl pkb bootstrap.",
            "empty": "Compiled KB is bootstrapped but nothing is approved yet — approve knowledge on the Knowledge page.",
            "populated": "",
        }
        return {
            "schema": "arail.kb-gate/v1",
            "enabled": enabled,
            "manifest_present": present,
            "approved_count": len(approved),
            "live_count": len(live),
            "pending_count": pending,
            "state": state,
            "hint": hints[state],
        }
    except Exception:  # noqa: BLE001 — total failure still reads as unbootstrapped
        return {
            "schema": "arail.kb-gate/v1",
            "enabled": gate_enabled(),
            "manifest_present": False,
            "approved_count": 0,
            "live_count": 0,
            "pending_count": -1 if cheap else 0,
            "state": "unbootstrapped",
            "hint": "Compiled KB has never been bootstrapped — run ./arailctl pkb bootstrap.",
        }


def list_approved(pkb_root: Path | None = None) -> list[dict[str, Any]]:
    root = pkb_root or _pkb_root()
    return sorted(_approved_map(root).values(),
                  key=lambda r: r.get("approved_at", ""), reverse=True)


# ── Provenance / classification ──────────────────────────────────────────

def _read_text(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _title_of(rel: str, text: str) -> str:
    # frontmatter title:, then first ATX heading, then filename stem.
    m = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip().strip("'\"")[:200]
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    return Path(rel).stem.replace("-", " ").replace("_", " ")[:200]


def _kind_of(rel: str) -> str:
    if _WORLD_TERM_RE.match(rel):
        return "world_term"
    if rel.startswith("sources/scout/"):
        return "scout_finding"
    if rel.startswith("agents/research/"):
        return "agent_research"
    if rel.startswith("agents/experiments/"):
        return "agent_experiment"
    if rel.startswith("agents/synthesis/"):
        return "agent_synthesis"
    if rel.startswith("agents/recommendations/"):
        return "agent_recommendation"
    if rel.startswith("agents/buddy/dreams/"):
        return "agent_dream"
    if rel.startswith("inbox/") or rel.startswith("notes/"):
        return "note"
    return "source"


# Public alias — the graph brain-scope and the lab brief classify nodes by
# the same path rules the review queue uses.
def kind_of(rel_path: str) -> str:
    return _kind_of(rel_path)


def _provenance_of(rel: str, text: str, kind: str) -> str:
    """Derived, never asserted. World terms carry a ``Source:`` line; agent
    outputs are provenance-labeled by their kind; everything else is 'user'."""
    if kind == "world_term":
        m = re.search(r"^Source:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()[:200]
        return "world-term"
    if kind == "scout_finding":
        # agenda_watch findings carry the watched feed URL as a Source: line
        m = re.search(r"^Source:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()[:200]
        return "scout-finding"
    if kind.startswith("agent_"):
        return kind
    if kind == "note":
        return "user-note"
    return "user"


def _world_of(rel: str) -> str | None:
    m = re.match(r"^sources/(world-[^/]+)/", rel)
    return m.group(1) if m else None


# ── The candidate feed (review queue) ────────────────────────────────────

# What may enter the queue. Bundle-machinery JSON, the auto-doc tree, the
# TOC index, the vector cache, and the Compiled-KB manifest itself never do.
_QUEUE_SUFFIXES = {".md", ".markdown", ".txt"}


# Lab plumbing that lives in the PKB but is CONFIG, not knowledge. An
# agent's AGENT.md describes how an agent behaves; a SKILL.md declares a
# capability; a README.md documents a folder. None of them is a claim about
# the world that an operator should be asked to "approve into the Compiled
# KB", and putting them in the queue buries the real candidates — the day
# two tutor coaches were added, their AGENT.md files appeared as knowledge
# awaiting review.
#
# Matched by FILENAME, deliberately, not by directory: agent *outputs*
# (agents/research/, agents/experiments/, agents/synthesis/,
# agents/recommendations/, agents/<id>/dreams/) are genuine candidates and
# live under the same agents/ tree as the config. Excluding the directory
# would throw the findings out with the plumbing.
_FURNITURE_FILENAMES = frozenset({
    "agent.md",        # agent config (the loader's discovery contract)
    "decisions.md",    # an agent's own config/decision log
    "readme.md",       # folder documentation
    "skill.md",        # a declared capability, not a fact
})


def _is_lab_furniture(rel: str) -> bool:
    """True for config/plumbing files that must never enter the review queue."""
    return Path(rel).name.lower() in _FURNITURE_FILENAMES


def _is_candidate(rel: str) -> bool:
    if rel.startswith("compiled/"):          # auto-docs + our own manifest
        return False
    if rel.startswith(".") or "/." in rel:   # dotfiles, .cache, .wiki-cache
        return False
    if rel == "index.md":                    # the TOC compile_index writes
        return False
    if Path(rel).suffix.lower() not in _QUEUE_SUFFIXES:
        return False
    if _is_lab_furniture(rel):               # config ≠ knowledge
        return False
    # world bundle machinery (spec/terms/roster/... json) already excluded by suffix
    return True


def list_pending(pkb_root: Path | None = None, *, limit: int = 500,
                 world: str | None = None) -> list[dict[str, Any]]:
    """Raw candidates awaiting a human decision: everything indexable that is
    neither already approved nor previously rejected. This is the review
    queue — agents propose (by creating raw content); the human approves.

    ``world`` scopes the queue to one World's knowledge (pass the mount's
    ``world-<slug>`` key). Without it the queue is the cross-World view — the
    root lab's honest "everything in this PKB" listing, where each row's
    ``world`` field says which World it belongs to.

    Scoping matters because the PKB root is shared by every World mounted
    into the same lab. An unscoped queue makes a freshly-forged World look
    like it inherited another World's glossary, which is the opposite of the
    promise that a new World starts with its own Knowledge Base.
    """
    root = pkb_root or _pkb_root()
    if not root.exists():
        return []
    approved = approved_paths(root)
    rejected = _rejected_set(root)
    out: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in approved or rel in rejected or not _is_candidate(rel):
            continue
        if world is not None and _world_of(rel) != world:
            continue
        text = _read_text(p)
        kind = _kind_of(rel)
        out.append({
            "path": rel,
            "title": _title_of(rel, text),
            "kind": kind,
            "provenance": _provenance_of(rel, text, kind),
            "world": _world_of(rel),
            "preview": text.strip()[:280],
            "sha256": _sha256(text),
        })
        if len(out) >= limit:
            break
    # world terms first (the headline promotion case), then scout findings
    # (time-sensitive "the agents noticed something" items), agent outputs, notes
    order = {"world_term": 0, "scout_finding": 1, "agent_research": 2,
             "agent_experiment": 2, "agent_synthesis": 2,
             "agent_recommendation": 2, "note": 3}
    out.sort(key=lambda r: (order.get(r["kind"], 3), r["title"].lower()))
    return out


def pending_paths(pkb_root: Path | None = None, *, world: str | None = None) -> list[str]:
    """Candidate paths awaiting review — the cheap variant of list_pending
    (same walk and filters, but no file reads / titles / hashes). For
    counts and digests: the lab brief and the hero stats hit this per
    request, so it must stay glob-only.

    Takes the same ``world`` scope as ``list_pending`` so a count can never
    disagree with the list it is counting."""
    root = pkb_root or _pkb_root()
    if not root.exists():
        return []
    approved = approved_paths(root)
    rejected = _rejected_set(root)
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in approved or rel in rejected or not _is_candidate(rel):
            continue
        if world is not None and _world_of(rel) != world:
            continue
        out.append(rel)
    return out


def pending_count(pkb_root: Path | None = None, *, world: str | None = None) -> int:
    return len(pending_paths(pkb_root, world=world))


# ── The gate: approve / reject / revoke ──────────────────────────────────

def _clean_rel(rel: str) -> str:
    # Defense-in-depth: normalize and reject traversal/absolute paths.
    rel = str(rel).replace("\\", "/").strip().lstrip("/")
    if ".." in Path(rel).parts or not rel:
        raise ValueError(f"illegal path: {rel!r}")
    return rel


def approve(paths: Iterable[str], pkb_root: Path | None = None, *,
            approver: str = "operator",
            extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Promote raw items into the Compiled KB. Requires each path to exist and
    to be a legitimate candidate; records provenance + a content hash of the
    exact approved bytes. Idempotent per path. Returns the new/updated records.

    ``extra`` is merged into each record (additive only — e.g.
    ``{"auto": True}`` for a seal-derived auto-approval, distinguishable and
    separately revocable from an operator's own click). An explicit approve()
    call — operator action or auto-approval alike — clears the path from the
    sticky ``unapproved.json`` revocation set: the whole point of that set is
    to survive a *mount*, not to survive an intentional re-approval.
    """
    root = pkb_root or _pkb_root()
    current = _approved_map(root)
    rejected = _rejected_set(root)
    unapproved = _unapproved_set(root)
    added: list[dict[str, Any]] = []
    for raw_rel in paths:
        try:
            rel = _clean_rel(raw_rel)
        except ValueError:
            continue  # traversal/absolute paths never enter the Compiled KB
        if not _is_candidate(rel):
            continue
        full = root / rel
        if not full.is_file():
            continue
        text = _read_text(full)
        kind = _kind_of(rel)
        rec = {
            "path": rel,
            "title": _title_of(rel, text),
            "kind": kind,
            "provenance": _provenance_of(rel, text, kind),
            "world": _world_of(rel),
            "sha256": _sha256(text),
            "approved_at": _now(),
            "approved_by": approver,
            "schema": SCHEMA,
        }
        if extra:
            rec.update(extra)
        current[rel] = rec
        rejected.discard(rel)
        unapproved.discard(rel)
        added.append(rec)
    _save_json(_kb_dir(root) / _APPROVED_FILE,
               {"schema": SCHEMA, "updated_at": _now(), "items": current})
    _save_json(_kb_dir(root) / _REJECTED_FILE,
               {"schema": SCHEMA, "items": sorted(rejected)})
    if added:
        _save_unapproved(root, unapproved)
    return added


def dangling_paths(pkb_root: Path | None = None) -> list[str]:
    """Approved paths whose raw file no longer exists on disk.

    The Compiled KB is a *manifest over the raw corpus*, not a copy — so an
    approval is only ever a pointer. When the raw file goes away the pointer
    survives, and because the retrieval gate is a query-time intersection
    (``approved_paths()`` filtered against live search hits), a dangling
    pointer can never match anything. Enough of them and the gate goes
    silently empty: agents get zero approved truth and nothing says why.

    The way this happens in practice is a World switch.
    ``world_mount._sweep_other_worlds()`` deletes every other
    ``sources/world-*/`` staged dir on mount — deliberately, because a World
    IS the lab's dataset — but it has no reason to know about the approval
    manifest, so the approvals for those terms outlive their files.

    Read-only. Never raises.
    """
    root = pkb_root or _pkb_root()
    try:
        if not root.is_dir():
            return []
        return sorted(rel for rel in _approved_map(root) if not (root / rel).is_file())
    except Exception:  # noqa: BLE001
        return []


def prune_dangling(pkb_root: Path | None = None) -> list[str]:
    """Drop approvals whose raw file is gone. Returns the paths removed.

    Reconciliation, not revocation: every path it removes already resolved
    to nothing, so the agent-visible truth is unchanged by definition — what
    changes is that the manifest stops lying about its size, and the review
    queue stops counting corpses.

    Refuses (returns ``[]``, touching nothing) when the pkb root itself is
    missing or unreadable. That case makes EVERY path look deleted, and the
    correct reading is "the lab is misconfigured", not "the operator revoked
    556 approvals" — a prune that fires there would destroy the manifest at
    exactly the moment it is least able to tell the difference.
    """
    root = pkb_root or _pkb_root()
    if not root.is_dir():
        return []
    current = _approved_map(root)
    dropped = sorted(rel for rel in current if not (root / rel).is_file())
    if not dropped:
        return []
    for rel in dropped:
        current.pop(rel, None)
    _save_json(_kb_dir(root) / _APPROVED_FILE,
               {"schema": SCHEMA, "updated_at": _now(), "items": current})
    return dropped


def reject(paths: Iterable[str], pkb_root: Path | None = None) -> int:
    """Dismiss candidates so they stop resurfacing. Reversible (a later
    approve re-admits them). Does not touch the raw file."""
    root = pkb_root or _pkb_root()
    rejected = _rejected_set(root)
    n = 0
    for raw_rel in paths:
        try:
            rel = _clean_rel(raw_rel)
        except ValueError:
            continue
        if rel not in rejected:
            rejected.add(rel)
            n += 1
    _save_json(_kb_dir(root) / _REJECTED_FILE,
               {"schema": SCHEMA, "items": sorted(rejected)})
    return n


def revoke(paths: Iterable[str], pkb_root: Path | None = None, *,
           sticky: bool = True) -> int:
    """Remove items from the Compiled KB (un-approve). The raw file remains;
    agents simply stop building on it. Fully reversible.

    ``sticky`` (default ``True``, the human-revocation path) also records
    each path in the ``unapproved.json`` set so a later World mount does not
    silently re-approve it via ``auto_approve_world_terms`` — without this, a
    human's per-term revocation would be undone by the next mount.
    ``sticky=False`` is for a mechanism-level rollback (see
    ``revoke_auto``), not a human judgment about a specific term: it
    un-approves now without poisoning future auto-approval, so the next
    mount/swap/bootstrap is free to re-approve the same term."""
    root = pkb_root or _pkb_root()
    current = _approved_map(root)
    unapproved = _unapproved_set(root)
    n = 0
    for raw_rel in paths:
        rel = str(raw_rel).replace("\\", "/").strip().lstrip("/")
        if rel in current:
            del current[rel]
            n += 1
        if sticky:
            unapproved.add(rel)
    _save_json(_kb_dir(root) / _APPROVED_FILE,
               {"schema": SCHEMA, "updated_at": _now(), "items": current})
    if n and sticky:
        _save_unapproved(root, unapproved)
    return n


def revoke_auto(pkb_root: Path | None = None) -> int:
    """Revoke every approval carrying ``auto: True`` — the one-step rollback
    for the whole mount-time auto-approval mechanism. Raw files untouched;
    fully reversible.

    Deliberately non-sticky (REVIEW.md ASK-1): this is a mechanism-level
    rollback, not 351 individual human judgments, so it must not mark every
    auto-approved path as explicitly revoked. A sticky rollback would be a
    one-way door — the *next* mount/swap/bootstrap would see every one of
    those paths in ``unapproved.json`` and refuse to re-approve any of them,
    with no un-stick command anywhere in the product. Non-sticky means the
    mechanism can be flipped back on (re-mount, re-swap, or re-run
    ``./arailctl pkb bootstrap``) and the World's terms return exactly as
    they were — the same reversibility every other revoke() call promises."""
    root = pkb_root or _pkb_root()
    current = _approved_map(root)
    auto_paths = [rel for rel, rec in current.items() if rec.get("auto") is True]
    return revoke(auto_paths, root, sticky=False)


# ── World-term auto-approval (mount-time, narrowly scoped) ───────────────

_WORLD_TERM_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def _safe_term_slug(raw: Any) -> str:
    """Mirror world_mount._safe_term_slug / world_corpus._safe_term_slug
    exactly — the staged term page filenames (and therefore approved_paths
    entries) were written with this sanitizer; reconstructing the path any
    other way risks silently missing or mis-scoping approved terms."""
    return _WORLD_TERM_SLUG_SAFE_RE.sub("-", str(raw).lower()).strip("-")[:80]


def _auto_approve_disabled(pkb_root: Path) -> bool:
    """True if either escape hatch is active. Both fail toward LESS
    auto-approval: env var off, or the sentinel file present/unreadable."""
    if os.getenv("ARAIL_AUTO_APPROVE_WORLD_TERMS", "on").strip().lower() in (
            "off", "0", "false", "no"):
        return True
    sentinel = _kb_dir(pkb_root) / _NO_AUTO_APPROVE_SENTINEL
    try:
        return sentinel.exists()
    except OSError:
        # Unreadable is treated as present (disabled) — fail toward less
        # agent-visible truth, never more.
        return True


def auto_approve_world_terms(
    world_slug: str, *,
    bundle_terms: Iterable[dict[str, Any]],
    seal_sha: str,
    pkb_root: Path | None = None,
    verified_seal: bool = True,
) -> list[dict[str, Any]]:
    """Auto-approve exactly the staged term pages for ``world_slug`` whose
    slug is present in ``bundle_terms`` — the reconciliation half of the
    scope invariant. ``bundle_terms`` MUST come from a bundle whose seal has
    already verified; this function does not verify seals and must never be
    called with unverified terms.

    ``verified_seal`` controls only the provenance label stamped into
    ``approved_by`` — it does not change what gets approved. Callers that
    pass a real ``seal.computed_sha256`` from ``verify_seal`` (``mount()``,
    ``swap()``) leave this ``True`` and get ``world-seal:<sha12>``.
    ``bootstrap()`` reads ``terms.json`` from the catalog directly, without
    calling ``verify_seal`` — it MUST pass ``verified_seal=False`` so the
    stamp reads ``world-terms:<sha12>`` and does not assert a verification
    that never happened (REVIEW.md BLOCK-3).

    Scope: a path is admitted iff (1) it matches
    ``sources/world-<slug>/terms/<term-slug>.md``, (2) ``<term-slug>`` is in
    the verified ``bundle_terms``, (3) ``_is_candidate`` passes, and (4) the
    file exists and is readable. Everything else — notes/, inbox/,
    conversations/, agents/**, sources/scout/, sources/seeds/, research/,
    skills/, hand-dropped .md inside terms/ not in terms.json — is
    unreachable by construction.

    Never raises. Returns ``[]`` (writes nothing) on: unknown slug, empty
    bundle_terms, missing staged dir, opt-out sentinel present/unreadable,
    or ``ARAIL_AUTO_APPROVE_WORLD_TERMS=off``.
    """
    try:
        root = pkb_root or _pkb_root()
        if not world_slug or not re.match(r"^[a-z0-9][a-z0-9-]*$", world_slug):
            return []
        if _auto_approve_disabled(root):
            return []
        terms_dir = root / "sources" / f"world-{world_slug}" / "terms"
        if not terms_dir.is_dir():
            return []

        bundle_slugs: set[str] = set()
        for t in bundle_terms:
            if not isinstance(t, dict):
                continue
            s = _safe_term_slug(t.get("slug", ""))
            if s:
                bundle_slugs.add(s)
        if not bundle_slugs:
            return []

        rejected = _rejected_set(root)
        unapproved = _unapproved_set(root)
        candidates: list[str] = []
        for slug in sorted(bundle_slugs):
            rel = f"sources/world-{world_slug}/terms/{slug}.md"
            if rel in rejected or rel in unapproved:
                continue
            if not _is_candidate(rel):
                continue
            full = root / rel
            try:
                if not full.is_file():
                    continue
            except OSError:
                continue
            candidates.append(rel)
        if not candidates:
            return []

        label = "world-seal" if verified_seal else "world-terms"
        approver = f"{label}:{str(seal_sha)[:12]}"
        return approve(candidates, root, approver=approver, extra={"auto": True})
    except Exception:  # noqa: BLE001 — best-effort; must never fail a mount
        return []


def bootstrap(pkb_root: Path | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Backfill/refresh the Compiled KB manifest for one root: resolve the
    staged World (if any) from the catalog copy and auto-approve its term
    pages. Always writes the manifest when not ``dry_run`` — even when zero
    terms qualify — so ``manifest_present()`` flips and ``gate_state`` moves
    from "unbootstrapped" to "empty". This is what makes a fresh lab honest.

    Never raises; a per-root failure is reported in ``skipped_reason``."""
    root = pkb_root or _pkb_root()
    result: dict[str, Any] = {
        "root": str(root), "world": None, "approved": 0,
        "skipped_reason": None, "dry_run": dry_run,
    }
    try:
        if not root.is_dir():
            result["skipped_reason"] = f"pkb root not found: {root}"
            return result

        # Discover the staged World, if any, from sources/world-<slug>/.
        world_slug: str | None = None
        sources_dir = root / "sources"
        if sources_dir.is_dir():
            for child in sorted(sources_dir.iterdir()):
                if child.is_dir() and child.name.startswith("world-"):
                    world_slug = child.name[len("world-"):]
                    break
        result["world"] = world_slug

        candidate_paths: list[str] = []
        if world_slug:
            try:
                from arail.world_catalog import resolve_world_bundle
                bundle = resolve_world_bundle(world_slug)
                terms = bundle.get("terms", [])
            except FileNotFoundError:
                result["skipped_reason"] = (
                    f"no bundle in catalog for {world_slug}; mount it once")
                terms = []
            except Exception as e:  # noqa: BLE001
                result["skipped_reason"] = f"failed to resolve catalog bundle: {e}"
                terms = []

            if terms:
                if dry_run:
                    for t in terms:
                        if not isinstance(t, dict):
                            continue
                        slug = _safe_term_slug(t.get("slug", ""))
                        if slug:
                            candidate_paths.append(
                                f"sources/world-{world_slug}/terms/{slug}.md")
                    result["approved"] = len(candidate_paths)
                    return result
                # NOT a verified seal: resolve_world_bundle() is a bare
                # json.loads of terms.json/spec.json with no verify_seal
                # call, and this sha covers only terms.json, not the whole
                # bundle. Stamped honestly via verified_seal=False below
                # (REVIEW.md BLOCK-3) — "world-terms:", not "world-seal:".
                try:
                    from arail.config import WORLDS_DIR
                    terms_raw = (Path(WORLDS_DIR) / world_slug / "terms.json").read_bytes()
                    seal_sha = hashlib.sha256(terms_raw).hexdigest()
                except Exception:  # noqa: BLE001
                    seal_sha = _sha256(json.dumps(terms, sort_keys=True))
                added = auto_approve_world_terms(
                    world_slug, bundle_terms=terms, seal_sha=seal_sha,
                    pkb_root=root, verified_seal=False)
                result["approved"] = len(added)
                return result

        # No World staged, or nothing qualified: still write the manifest
        # (possibly empty) so manifest_present() flips honestly.
        if not dry_run:
            approve([], root)
        return result
    except Exception as e:  # noqa: BLE001 — bootstrap must never itself raise
        result["skipped_reason"] = f"bootstrap failed: {e}"
        return result


# ── The retrieval gate toggle ────────────────────────────────────────────

def gate_enabled() -> bool:
    """Whether agent retrieval is scoped to the Compiled KB. Default ON — the
    hard gate the user asked for. ``ARAIL_APPROVED_ONLY=off`` reverts to the
    legacy raw-corpus behavior (a reversible escape hatch, e.g. for a brand
    new lab with nothing approved yet)."""
    return os.getenv("ARAIL_APPROVED_ONLY", "on").strip().lower() not in (
        "off", "0", "false", "no")


# ── CLI (./arailctl pkb prune | status) ──────────────────────────────────

def _cli(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="arailctl pkb",
        description="Inspect and reconcile the Compiled KB (the approved layer agents build on).",
    )
    sub = ap.add_subparsers(dest="op", required=True)
    p_prune = sub.add_parser(
        "prune", help="drop approvals whose raw file no longer exists")
    p_prune.add_argument("--dry-run", action="store_true",
                         help="list what would be dropped, change nothing")
    sub.add_parser("status", help="show live vs dangling approval counts")
    p_boot = sub.add_parser(
        "bootstrap",
        help="backfill the Compiled KB: auto-approve the staged World's term "
             "pages, or write an empty (present) manifest if none qualify")
    p_boot.add_argument("--dry-run", action="store_true",
                        help="print what would be approved, write nothing")
    p_revoke = sub.add_parser(
        "revoke",
        help="un-approve items (--auto: the one-step rollback for "
             "mount/swap/bootstrap auto-approval)")
    p_revoke.add_argument("--auto", action="store_true",
                          help="revoke every auto-approved item (non-sticky "
                               "— reversible by the next mount/swap/bootstrap)")
    args = ap.parse_args(argv)

    if args.op == "bootstrap":
        root = _pkb_root()
        result = bootstrap(root, dry_run=args.dry_run)
        label = "would approve" if args.dry_run else "approved"
        print(f"root     : {result['root']}")
        print(f"world    : {result['world'] or '(none staged)'}")
        print(f"{label:9}: {result['approved']}")
        if result["skipped_reason"]:
            print(f"note     : {result['skipped_reason']}")
        return 0

    if args.op == "revoke":
        if not args.auto:
            print("revoke currently only supports --auto "
                  "(per-path revocation is done from the Knowledge page)",
                  file=sys.stderr)
            return 2
        root = _pkb_root()
        n = revoke_auto(root)
        print(f"revoked  : {n} auto-approved item(s)")
        print("note     : non-sticky — the next mount/swap/bootstrap can "
              "re-approve these terms")
        return 0

    root = _pkb_root()
    if not root.is_dir():
        print(f"pkb root not found: {root}", file=sys.stderr)
        return 3

    approved = approved_paths(root)
    dangling = dangling_paths(root)

    if args.op == "status" or getattr(args, "dry_run", False):
        print(f"approved : {len(approved)}")
        print(f"live     : {len(approved) - len(dangling)}")
        print(f"dangling : {len(dangling)}")
        for rel in dangling[:20]:
            print(f"  - {rel}")
        if len(dangling) > 20:
            print(f"  … and {len(dangling) - 20} more")
        if dangling and args.op == "status":
            print("\nfix: ./arailctl pkb prune")
        return 0

    dropped = prune_dangling(root)
    if not dropped:
        print("Nothing to prune — every approval resolves to a file on disk.")
        return 0
    print(f"Pruned {len(dropped)} approval(s) whose raw file no longer exists.")
    print(f"Compiled KB now holds {len(approved_paths(root))} approved item(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
