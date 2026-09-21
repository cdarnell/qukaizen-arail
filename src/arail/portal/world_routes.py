"""World Forge + term-editor + Curator-review API router.

Registered in ``portal/app.py`` as ``app.include_router(world_router)`` —
kept in its own module (the wiki_routes pattern) so app.py stays navigable.

Three surfaces:

  Forge   — POST /api/worlds/forge (202 + background job, one at a time) ·
            status / cancel / preview / confirm / discard. Draft is held in
            MEMORY until confirm; confirm seals into WORLDS_DIR/<slug>/ and
            mounts (or swaps).
  Terms   — structured editor over the MOUNTED world's catalog bundle:
            every successful write re-validates → gate → reseal → swap, so
            the mounted world is never seal-inconsistent.
  Review  — the Curator's on-demand reconcile pass; flags land in a
            seal-EXEMPT review.json sidecar (advisory, never sealed).

Security: every write carries the same CSRF envelope as /api/worlds/select
(Sec-Fetch-Site + Origin/Host — browser-enforced, unforgeable). Term fields
are length-capped and structurally validated here, contained again by the
SKILL.md sanitizers at reseal, and contained a third time by skills_loader
at load — plus textContent-only rendering client-side.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from arail import agent_context
from arail import world_forge as wf
from arail import world_mount as wm
from arail.activity import activity_log
from arail.portal import scheduler
from arail.world_theme import parse_world_theme

_log = logging.getLogger(__name__)

router = APIRouter()

ETA_MINUTES = {25: 4, 50: 8, 100: 15}
ALLOWED_SIZES = (25, 50, 100)
# Fetch (sourced-bootstrap) mode: no local model, so bigger + faster.
FETCH_SIZES = (25, 50, 100, 250, 512)
FETCH_ETA_MINUTES = {25: 1, 50: 1, 100: 2, 250: 3, 512: 6}
FETCH_STAGES = ("resolve", "harvest", "define", "link", "gate")
REVIEW_SCHEMA = "arail.world-review/v1"
REVIEW_BATCH = 16
# Growth pass: judge this many for corrections, propose up to this many new.
GROW_REVIEW_BATCH = 24
GROW_NEW_BATCH = 8


# ── shared helpers ──────────────────────────────────────────────────────

def _err(code: int, body: dict) -> JSONResponse:
    return JSONResponse(status_code=code, content=body)


def _csrf_reject(request: Request) -> Optional[JSONResponse]:
    """The select/import CSRF envelope. None when the request is acceptable."""
    sfs = request.headers.get("sec-fetch-site", "").strip().lower()
    if sfs in ("cross-site", "none"):
        return _err(403, {"error": "cross_site"})
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host and origin_host != host:
            return _err(403, {"error": "cross_origin"})
    return None


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _operator_source() -> str:
    """Provenance tag for hand-edited terms — honest: it is NOT model-asserted,
    so the rollup flips a dreamed world to 'mixed' on first human edit."""
    try:
        from arail.brand import load_brand
        b = load_brand()
        tag = wf.slugify(b.short_name or b.name) or "lab"
    except Exception:  # noqa: BLE001
        tag = "lab"
    return f"operator:{tag}"


def _mounted_catalog_dir() -> Optional[Path]:
    """The canonical, editable copy of the mounted world (mount always adopts
    into WORLDS_DIR)."""
    record = wm.current_mount()
    if record is None:
        return None
    catalog = wm._default_worlds_dir() / record.world
    if (catalog / "manifest.json").exists():
        return catalog
    bundle_dir = Path(record.bundle_dir)
    return bundle_dir if (bundle_dir / "manifest.json").exists() else None


def _load_terms(bundle_dir: Path) -> tuple[dict, list[dict]]:
    spec = json.loads((bundle_dir / "spec.json").read_bytes())
    terms = json.loads((bundle_dir / "terms.json").read_bytes()).get("terms", [])
    return spec, terms


def _review_router() -> Any:
    """Deep model when policy allows (maximus overnight etc.), else the
    default local router. NEVER tier-gated — minimalist reviews with the 1B."""
    try:
        from arail.agents import deep_policy
        if deep_policy.prefer_deep(foreground=True):
            deep = deep_policy.get_deep_router()
            if deep is not None:
                return deep
    except Exception:  # noqa: BLE001
        pass
    from arail.router import ModelRouter
    return ModelRouter(billing_source="agent")


# ═══════════════════════════ FORGE ════════════════════════════════════════

# Brains that reach a cloud gateway. An explicit operator choice of one of
# these must refuse loudly when airgapped (the silent local fallback inside
# _curation_router is for autonomous growth only).
_CLOUD_BRAINS = {"claude", "openrouter", "nim", "huggingface", "custom"}

_forge_lock = asyncio.Lock()
_forge_cancel = threading.Event()
_forge_state: dict[str, Any] = {"state": "idle"}
_forge_result: Optional[wf.ForgeResult] = None
_forge_face_overrides: dict = {}


def _forge_progress(stage: str, done: int, total: int, note: str) -> None:
    # Thread → async safe: replace an immutable snapshot (GIL-atomic).
    global _forge_state
    prior = _forge_state
    _forge_state = {
        **prior,
        "stage": stage,
        "stage_index": wf.FORGE_STAGES.index(stage) if stage in wf.FORGE_STAGES else 0,
        "stages_total": len(wf.FORGE_STAGES),
        "terms_found": done if stage == "discover" else prior.get("terms_found", 0),
        "message": note,
        "elapsed_s": round(time.monotonic() - prior.get("_t0", time.monotonic()), 1),
    }


async def _run_forge(params: wf.ForgeParams, brain: str = "local") -> None:
    global _forge_state, _forge_result
    try:
        router_ = _curation_router(brain)
        # L3 (ARCHITECTURE.md): world_routes is not an agent -- system_call
        # under the same label already used for the inference_slot, so
        # attribution and slot metrics agree on what this work is called.
        # dac_world (wf) itself stays ARAIL-free (no import arail), so the
        # wrap has to live here, around the to_thread hop that reaches it.
        with agent_context.system_call("world-forge"):
            if brain in _CLOUD_BRAINS:
                # Frontier API forge — no local model residency, so no
                # inference_slot: the cloud calls run beside chat's GPU work.
                result = await asyncio.to_thread(
                    wf.forge_world, params, router=router_,
                    progress_cb=_forge_progress, cancel=_forge_cancel,
                )
            else:
                async with scheduler.inference_slot("world-forge"):
                    result = await asyncio.to_thread(
                        wf.forge_world, params, router=router_,
                        progress_cb=_forge_progress, cancel=_forge_cancel,
                    )
        _forge_result = result
        _forge_state = {**_forge_state, "state": "done",
                        "terms_found": len(result.terms),
                        "message": f"{len(result.terms)} terms · tier {result.tier}"}
        activity_log.emit("forge", f"Forged '{params.subject}' — {len(result.terms)} terms, "
                                   f"tier {result.tier}. Preview it on the Worlds page.",
                          "success")
    except wf.ForgeCancelled:
        _forge_state = {**_forge_state, "state": "cancelled", "message": "cancelled"}
        activity_log.emit("forge", f"Forge of '{params.subject}' cancelled.", "info")
    except wf.GateRefused as e:
        _forge_state = {**_forge_state, "state": "error",
                        "message": "nothing usable produced — try a richer model or a clearer subject"}
        activity_log.emit("forge", f"Forge of '{params.subject}' produced nothing usable "
                                   f"({len(e.gate.unsourced)} unsourced, "
                                   f"{len(e.gate.dangling_edges)} dangling).", "warn")
    except Exception as e:  # noqa: BLE001
        _log.warning("world forge failed: %s", e)
        _forge_state = {**_forge_state, "state": "error", "message": str(e)[:200]}
        activity_log.emit("forge", f"Forge of '{params.subject}' failed: {e}", "error")


def _bootstrap_progress(stage: str, done: int, total: int, note: str) -> None:
    global _forge_state
    prior = _forge_state
    _forge_state = {
        **prior,
        "stage": stage,
        "stage_index": FETCH_STAGES.index(stage) if stage in FETCH_STAGES else 0,
        "stages_total": len(FETCH_STAGES),
        "source": "fetch",
        "terms_found": done if stage in ("define", "harvest") else prior.get("terms_found", 0),
        "max_terms": total if stage in ("define", "harvest") and total else prior.get("max_terms", 0),
        "message": note,
        "elapsed_s": round(time.monotonic() - prior.get("_t0", time.monotonic()), 1),
    }


async def _run_bootstrap(subject: str, slug: str, max_terms: int, consent_id: str) -> None:
    """Fetch-mode forge: build a sourced World from Wikipedia. No model use, so
    no inference_slot — the fetch runs beside chat."""
    global _forge_state, _forge_result
    from arail.world_sources import wikipedia as wk
    try:
        result = await asyncio.to_thread(
            wk.bootstrap_subject, subject, max_terms,
            consent_id=consent_id, progress_cb=_bootstrap_progress, cancel=_forge_cancel,
        )
        _forge_result = result
        _forge_state = {**_forge_state, "state": "done", "source": "fetch",
                        "terms_found": len(result.terms),
                        "message": f"{len(result.terms)} sourced terms · tier {result.tier}"}
        activity_log.emit("forge", f"Fetched '{subject}' — {len(result.terms)} sourced terms "
                                   f"from Wikipedia. Preview it on the Worlds page.", "success")
    except wk.BootstrapCancelled:
        _forge_state = {**_forge_state, "state": "cancelled", "message": "cancelled"}
        activity_log.emit("forge", f"Sourced bootstrap of '{subject}' cancelled.", "info")
    except wk.BootstrapEmpty as e:
        _forge_state = {**_forge_state, "state": "error",
                        "message": f"no usable content — try a broader subject ({e})"}
        activity_log.emit("forge", f"Sourced bootstrap of '{subject}' found nothing: {e}", "warn")
    except Exception as e:  # noqa: BLE001
        _log.warning("world bootstrap failed: %s", e)
        _forge_state = {**_forge_state, "state": "error", "message": str(e)[:200]}
        activity_log.emit("forge", f"Sourced bootstrap of '{subject}' failed: {e}", "error")


@router.post("/api/worlds/forge")
async def api_forge_start(request: Request):
    global _forge_state, _forge_result, _forge_face_overrides
    if (rej := _csrf_reject(request)) is not None:
        return rej
    body = await _json_body(request)

    subject = str(body.get("subject", "")).strip()
    if not (1 <= len(subject) <= 120):
        return _err(400, {"error": "bad_subject",
                          "message": "Give the world a subject (1–120 characters)."})
    slug = wf.slugify(str(body.get("slug", "")) or subject)
    if not slug or not wm._SLUG_RE.match(slug):
        return _err(400, {"error": "bad_slug"})
    source = "fetch" if str(body.get("source", "dream")).strip() == "fetch" else "dream"
    brain = str(body.get("brain", "local")).strip().lower() or "local"
    if source == "dream" and brain in _CLOUD_BRAINS:
        from arail.airgap import is_airgapped
        if is_airgapped():
            return _err(409, {
                "error": "airgapped",
                "message": "The lab is airgapped — the frontier API can't be "
                           "reached. Flip the Airgapped pill in the status bar, "
                           "or forge with the local model."})
    try:
        max_terms = int(body.get("max_terms", 25))
    except (TypeError, ValueError):
        max_terms = 25
    if source == "fetch":
        max_terms = max_terms if max_terms in FETCH_SIZES else max(8, min(512, max_terms))
    elif max_terms not in ALLOWED_SIZES:
        max_terms = max(8, min(150, max_terms))

    overrides: dict = {}
    if body.get("palette_hint") or body.get("personality"):
        from arail.ui_theme import PERSONALITIES, load_ui_theme
        hint = str(body.get("palette_hint", "")).strip()
        personality = str(body.get("personality", "")).strip()
        if hint:
            preset = load_ui_theme(hint)
            if preset.id == hint or preset.env_value == hint:
                overrides["palette_hint"] = hint
                if personality in PERSONALITIES:
                    # Full theme block from the preset's palette + the chosen
                    # personality — validated hard at seal time.
                    colors = preset.dark
                    overrides["theme"] = {
                        "schema": "dac.world-theme/v1",
                        "personality": personality,
                        "dark": {slot: getattr(colors, slot) for slot in (
                            "bg", "surface", "surface2", "border", "text", "muted",
                            "accent", "accent2", "positive", "warn", "danger", "info")},
                        "light": None,
                    }

    async with _forge_lock:
        if _forge_state.get("state") == "running":
            return _err(409, {"error": "forge_busy",
                              "message": "A forge is already running — cancel it or wait."})
        catalog = wm._default_worlds_dir() / slug
        if catalog.exists() and not body.get("overwrite"):
            return _err(409, {"error": "slug_exists", "slug": slug,
                              "message": f"A world named '{slug}' already exists."})
        _forge_cancel.clear()
        _forge_result = None
        _forge_face_overrides = overrides
        stages = FETCH_STAGES if source == "fetch" else wf.FORGE_STAGES
        _forge_state = {
            "state": "running", "subject": subject, "slug": slug, "source": source,
            "brain": brain if source == "dream" else "none",
            "max_terms": max_terms, "stage": stages[0], "stage_index": 0,
            "stages_total": len(stages), "terms_found": 0,
            "message": "starting…", "started_at": time.time(),
            "_t0": time.monotonic(), "elapsed_s": 0,
        }
        if source == "fetch":
            # The operator's explicit "Fetch real content" choice IS the consent
            # to a one-time Wikipedia fetch for this subject (recorded + audited).
            from arail.agents.consent import ConsentStore
            store = ConsentStore()
            req = store.request_access(
                "https://en.wikipedia.org/",
                f"World Forge sourced bootstrap: {subject[:100]}", agent="world-forge")
            if req.get("status") not in ("approved", "auto_approved"):
                store.approve(req["id"])
            asyncio.create_task(_run_bootstrap(subject, slug, max_terms, req["id"]))
        else:
            params = wf.ForgeParams(subject=subject, slug=slug, max_terms=max_terms)
            asyncio.create_task(_run_forge(params, brain))

    if source == "fetch":
        activity_log.emit("forge", f"Fetching '{subject}' ({max_terms} terms) from Wikipedia "
                                   "(one-time, consented, audited)…", "info")
        eta = FETCH_ETA_MINUTES.get(max_terms, max(1, max_terms // 90))
    else:
        brain_desc = (f"the frontier API ({brain})" if brain in _CLOUD_BRAINS
                      else "the local model")
        activity_log.emit("forge", f"Forging '{subject}' ({max_terms} terms) with {brain_desc}…",
                          "info")
        eta = ETA_MINUTES.get(max_terms, max(2, max_terms // 8))
    return JSONResponse(status_code=202,
                        content={"started": True, "slug": slug, "eta_minutes": eta})


@router.get("/api/worlds/forge/status")
async def api_forge_status():
    s = {k: v for k, v in _forge_state.items() if not k.startswith("_")}
    if _forge_state.get("state") == "running" and "_t0" in _forge_state:
        s["elapsed_s"] = round(time.monotonic() - _forge_state["_t0"], 1)
    return s


@router.post("/api/worlds/forge/cancel")
async def api_forge_cancel(request: Request):
    if (rej := _csrf_reject(request)) is not None:
        return rej
    if _forge_state.get("state") != "running":
        return {"ok": True, "state": _forge_state.get("state", "idle")}
    _forge_cancel.set()
    return {"ok": True, "state": "cancelling"}


@router.get("/api/worlds/forge/preview")
async def api_forge_preview():
    if _forge_state.get("state") != "done" or _forge_result is None:
        return _err(409, {"error": "no_result", "state": _forge_state.get("state", "idle")})
    r = _forge_result
    count_by_cat: dict[str, int] = {}
    for t in r.terms:
        count_by_cat[t["category"]] = count_by_cat.get(t["category"], 0) + 1
    warnings = []
    if len(r.terms) > wf.MAX_TERMS_SOFT or r.stats.get("skill_chars", 0) > wf.SKILL_CHAR_BUDGET:
        warnings.append("Large world: agent context (SKILL.md) may be truncated beyond ~150 terms.")
    shorts = sum(1 for t in r.terms if len(str(t.get("short", "")).strip()) < 12)
    if shorts:
        warnings.append(f"{shorts} terms have very short definitions — the model was terse; "
                        f"edit them in the DaC tab or Regenerate.")
    return {
        "slug": _forge_state.get("slug"),
        "subject": _forge_state.get("subject"),
        "display_name": r.spec.get("display_name"),
        "tier": r.tier,
        "counts": r.counts,
        "avg_edges": r.stats.get("avg_edges"),
        "skill_chars": r.stats.get("skill_chars"),
        "categories": [
            {"id": c["id"], "label": c["label"], "term_count": count_by_cat.get(c["id"], 0)}
            for c in r.spec.get("categories", [])
        ],
        "terms": r.terms,
        "warnings": warnings,
    }


@router.post("/api/worlds/forge/confirm")
async def api_forge_confirm(request: Request):
    global _forge_state, _forge_result
    if (rej := _csrf_reject(request)) is not None:
        return rej
    if _forge_state.get("state") != "done" or _forge_result is None:
        return _err(409, {"error": "no_result"})
    r = _forge_result
    slug = str(_forge_state.get("slug", ""))
    subject = str(_forge_state.get("subject", slug))
    catalog = wm._default_worlds_dir() / slug

    # ── In-place switching removed: forge-confirm is a fourth door onto the
    # same wm.swap()/_sweep_other_worlds() primitive as select/import/
    # import-zip (QA-1, TEST_REPORT.md). An empty root, or a re-forge of the
    # ALREADY-mounted World (same slug — swap() would sweep only its own
    # staged layer), stays allowed; forging over a DIFFERENT mounted World is
    # refused before any disk write (the rename dance below never runs).
    cur = wm.current_mount()
    if cur is not None and cur.world != slug:
        return _err(409, {
            "error": "in_place_switch_removed",
            "message": (
                f"'{cur.world}' is mounted in this lab. Switching Worlds in "
                "place was removed — one lab, one World. Unmount first (AI "
                "Lab default) on the Worlds page, then confirm this forge — "
                "or run the forged World as its own instance once it's in "
                f"the catalog: ./arailctl start --world {slug}"
            ),
        })

    tmp = catalog.parent / f".{slug}.forge-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        wf.write_bundle(tmp, r.spec, r.terms, face_overrides=_forge_face_overrides or None,
                        theme_validator=parse_world_theme)
        if catalog.exists():
            old = catalog.parent / f".{slug}.forge-old"
            if old.exists():
                shutil.rmtree(old)
            os.rename(catalog, old)
            os.rename(tmp, catalog)
            shutil.rmtree(old)
        else:
            catalog.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp, catalog)
        rec = wm.swap(catalog) if wm.current_mount() is not None else wm.mount(catalog)
    except Exception as e:  # noqa: BLE001
        _log.warning("forge confirm failed: %s", e)
        return _err(500, {"error": "confirm_failed", "message": str(e)[:200]})
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)

    _forge_state = {"state": "idle"}
    _forge_result = None
    activity_log.emit("forge", f"Mounted the forged world '{subject}' — the lab now studies it. "
                               f"The Researcher can verify dreamed terms; you can edit them in "
                               f"Knowledge → World Terms.", "success")
    suggestions = wf.goal_suggestions(r.spec, r.tier)
    return {"ok": True, "current": rec.world, "suggested_goal": suggestions[0],
            "goal_suggestions": suggestions}


@router.post("/api/worlds/forge/discard")
async def api_forge_discard(request: Request):
    global _forge_state, _forge_result
    if (rej := _csrf_reject(request)) is not None:
        return rej
    if _forge_state.get("state") == "running":
        return _err(409, {"error": "forge_busy", "message": "Cancel the running forge first."})
    _forge_state = {"state": "idle"}
    _forge_result = None
    return {"ok": True}


@router.delete("/api/worlds/{slug}")
async def api_world_delete(slug: str, request: Request):
    """Remove a world from the catalog (unmounts it first if mounted).
    Shipped worlds are re-importable, so deletion is allowed everywhere."""
    if (rej := _csrf_reject(request)) is not None:
        return rej
    slug = str(slug).strip()
    if not wm._SLUG_RE.match(slug):
        return _err(400, {"error": "bad_slug"})
    catalog = (wm._default_worlds_dir() / slug).resolve()
    if catalog.parent != wm._default_worlds_dir().resolve() or not catalog.exists():
        return _err(404, {"error": "not_found"})
    record = wm.current_mount()
    if record is not None and record.world == slug:
        wm.unmount(remove_staged=True)
    shutil.rmtree(catalog)
    activity_log.emit("forge", f"Deleted world '{slug}' from the catalog.", "info")
    return {"ok": True}


# ═══════════════════════════ PUSH TO TEAM ══════════════════════════════════
# The mounted world's catalog directory (WORLDS_DIR/<slug>/) already IS a
# valid dac.world-bundle/v1 bundle — manifest.json + the six sealed siblings
# are written at forge-confirm time and re-sealed on every terms edit. No new
# packaging step exists here on purpose: this route re-verifies the seal
# (defense in depth before anything leaves ARAIL) and hands the same
# directory straight to team-adopt, exactly the way a human would from a
# terminal. If verify_seal ever fails here, that's a real bug elsewhere in
# the mount/reseal path, not something this route should paper over.

TEAM_REPO_ENV = "TEAM_REPO_PATH"
_DEFAULT_TEAM_REPO = os.path.expanduser("~/ProJects/qukaizen-team")


def _team_repo_dir() -> Path:
    return Path(os.getenv(TEAM_REPO_ENV, _DEFAULT_TEAM_REPO)).expanduser()


@router.get("/api/admin/team/push-status")
async def api_team_push_status():
    """What the 'Push to TEAM' panel needs: the currently mounted world (if
    any), whether team-adopt is reachable, and which TEAM worlds exist to
    push into."""
    record = wm.current_mount()
    mounted = record.world if record else None

    team_repo = _team_repo_dir()
    team_adopt = team_repo / "bin" / "team-adopt"
    worlds_dir = team_repo / "worlds"
    team_worlds: list[str] = []
    if worlds_dir.is_dir():
        team_worlds = sorted(
            p.name for p in worlds_dir.iterdir()
            if p.is_dir() and (p / "world.json").exists()
        )

    return {
        "mounted_world": mounted,
        "team_repo": str(team_repo),
        "team_adopt_found": team_adopt.exists(),
        "team_worlds": team_worlds,
    }


@router.post("/api/admin/team/push")
async def api_team_push(request: Request):
    """Push the currently mounted ARAIL world into a TEAM world as a
    reference shelf. Same shape as run-scan / the scheduler's run-now:
    subprocess, captured output, a receipt-style JSON back to the caller.

    Body: {"team_world": "<slug under qukaizen-team/worlds/>"} — optional;
    omitted means a dry run (team-adopt prints its report, writes nothing).
    """
    if (rej := _csrf_reject(request)) is not None:
        return rej

    record = wm.current_mount()
    if record is None:
        return _err(400, {"error": "no_world_mounted",
                           "message": "Mount a world before pushing it to TEAM."})

    catalog = _mounted_catalog_dir()
    if catalog is None:
        return _err(404, {"error": "catalog_missing",
                           "message": f"Mount record points at '{record.world}' but its "
                                      "catalog directory has no manifest.json."})

    # Re-verify the seal right before this bundle leaves ARAIL. Reads the
    # same six files team-adopt is about to read.
    try:
        bundle = wm.load_bundle(catalog)
    except wm.BundleError as e:
        return _err(422, {"error": "bundle_unreadable", "message": e.user_message})

    seal = wm.verify_seal(bundle)
    if not seal.ok:
        activity_log.emit("team-push",
            f"Refused to push '{record.world}' — seal check failed: {seal.user_message}",
            "error")
        return _err(422, {"error": "seal_mismatch", "message": seal.user_message})

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    team_world = str(body.get("team_world") or "").strip()

    team_repo = _team_repo_dir()
    team_adopt = team_repo / "bin" / "team-adopt"
    if not team_adopt.exists():
        return _err(404, {"error": "team_adopt_not_found",
                           "message": f"No team-adopt at {team_adopt}. Set {TEAM_REPO_ENV} "
                                      "if qukaizen-team lives somewhere else on this machine."})

    cmd = ["python3", str(team_adopt), str(catalog)]
    into_dir = None
    if team_world:
        if not wm._SLUG_RE.match(team_world):
            return _err(400, {"error": "bad_team_world"})
        into_dir = team_repo / "worlds" / team_world
        if not (into_dir / "world.json").exists():
            return _err(404, {"error": "team_world_not_found",
                               "message": f"No world.json at {into_dir}."})
        cmd += ["--into", str(into_dir)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return _err(504, {"error": "timeout", "message": "team-adopt did not finish in 30s."})
    except OSError as e:
        return _err(500, {"error": "launch_failed", "message": str(e)})

    ok = proc.returncode == 0
    activity_log.emit(
        "team-push",
        (f"Pushed '{record.world}' to TEAM"
         + (f" world '{team_world}'" if team_world else " (dry run)")
         + (" — ok" if ok else f" — exit {proc.returncode}")),
        "info" if ok else "error",
    )
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "dry_run": not bool(team_world),
        "world": record.world,
        "team_world": team_world or None,
        "output": (proc.stdout + proc.stderr)[-6000:],
    }


# ═══════════════════════════ TERMS EDITOR ═════════════════════════════════

_reseal_lock = asyncio.Lock()


def _term_error(field: str, message: str) -> JSONResponse:
    return _err(400, {"error": "invalid_term", "field": field, "message": message})


def _validate_term_fields(body: dict, spec: dict, known: set[str],
                          self_slug: str) -> Optional[JSONResponse]:
    caps = (("short", wf.MAX_SHORT), ("definition", wf.MAX_DEFINITION),
            ("example", wf.MAX_EXAMPLE))
    for fld, cap in caps:
        if fld in body and body[fld] is not None:
            if not isinstance(body[fld], str):
                return _term_error(fld, "must be a string")
            if len(body[fld]) > cap:
                return _term_error(fld, f"too long (max {cap} characters)")
    if "term" in body and not (isinstance(body["term"], str) and 1 <= len(body["term"]) <= 120):
        return _term_error("term", "must be 1–120 characters")
    if "category" in body:
        declared = {str(c.get("id", "")) for c in spec.get("categories", [])}
        if str(body["category"]) not in declared:
            return _term_error("category",
                               f"must be one of the world's categories ({', '.join(sorted(declared))})")
    if "related" in body:
        rel = body["related"]
        if not isinstance(rel, list) or not all(isinstance(s, str) for s in rel):
            return _term_error("related", "must be a list of slugs")
        if len(rel) > 12:
            return _term_error("related", "too many associations (max 12)")
        for s in rel:
            if s == self_slug:
                return _term_error("related", "a term cannot relate to itself")
            if s not in known:
                return _term_error("related", f"unknown term '{s}' — associations must "
                                              f"point at terms in this world")
    if "aka" in body:
        aka = body["aka"]
        if not (isinstance(aka, list) and all(isinstance(s, str) and len(s) <= 120 for s in aka)):
            return _term_error("aka", "must be a list of short strings")
    return None


async def _reseal_and_swap(bundle_dir: Path, terms: list[dict]) -> Optional[JSONResponse]:
    """Gate → reseal → swap. Returns an error response or None on success."""
    spec = json.loads((bundle_dir / "spec.json").read_bytes())
    declared = {str(c.get("id", "")) for c in spec.get("categories", [])}
    gate = wf.assert_closed_sourced_graph(terms, declared)
    if not gate.ok:
        return _err(400, {"error": "gate_failed",
                          "dangling": gate.dangling_edges,
                          "unsourced": gate.unsourced,
                          "undeclared": gate.undeclared_category})
    try:
        await asyncio.to_thread(wf.reseal_bundle, bundle_dir, terms,
                                theme_validator=parse_world_theme)
        await asyncio.to_thread(wm.swap, bundle_dir)
    except Exception as e:  # noqa: BLE001
        _log.warning("world reseal/swap failed: %s", e)
        return _err(500, {"error": "reseal_failed", "message": str(e)[:200]})
    return None


def _live_tier(manifest: dict, terms: list[dict]) -> tuple[str, dict]:
    """Prefer the manifest's tier/counts, but derive live from the terms'
    sources when either is missing — legacy bundles sealed before the
    provenance fields existed must never show a bogus/blank tier."""
    tier = manifest.get("provenance_tier")
    counts = manifest.get("provenance_counts")
    if tier and counts:
        return tier, counts
    return wf.compute_provenance_tier([t.get("source") for t in terms])


def _terms_payload(bundle_dir: Path) -> dict:
    spec, terms = _load_terms(bundle_dir)
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    tier, counts = _live_tier(manifest, terms)
    for t in terms:
        t["tier_of_source"] = wf.tier_of_source(t.get("source"))
    return {
        "world": manifest.get("world"),
        "display_name": manifest.get("display_name"),
        "tier": tier,
        "counts": counts,
        "editable": True,
        "categories": spec.get("categories", []),
        "terms": terms,
    }


@router.get("/api/worlds/terms")
async def api_terms_list():
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return _err(409, {"error": "no_world_mounted",
                          "message": "Mount a world to edit its terms."})
    return _terms_payload(bundle_dir)


@router.put("/api/worlds/terms/{slug}")
async def api_term_update(slug: str, request: Request):
    if (rej := _csrf_reject(request)) is not None:
        return rej
    body = await _json_body(request)
    async with _reseal_lock:
        bundle_dir = _mounted_catalog_dir()
        if bundle_dir is None:
            return _err(409, {"error": "no_world_mounted"})
        spec, terms = _load_terms(bundle_dir)
        known = {t["slug"] for t in terms}
        target = next((t for t in terms if t["slug"] == slug), None)
        if target is None:
            return _err(404, {"error": "term_not_found", "slug": slug})
        if (bad := _validate_term_fields(body, spec, known, slug)) is not None:
            return bad
        changed = False
        for fld in ("term", "short", "definition", "example", "category", "related", "aka"):
            if fld in body and body[fld] is not None and body[fld] != target.get(fld):
                target[fld] = body[fld]
                changed = True
        if not changed:
            return {"ok": True, "unchanged": True}
        target["source"] = _operator_source()   # honest: a human touched it
        if (err := await _reseal_and_swap(bundle_dir, terms)) is not None:
            return err
        manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
        return {"ok": True, "term": target, "tier": manifest.get("provenance_tier"),
                "counts": manifest.get("provenance_counts")}


@router.post("/api/worlds/terms")
async def api_term_add(request: Request):
    if (rej := _csrf_reject(request)) is not None:
        return rej
    body = await _json_body(request)
    async with _reseal_lock:
        bundle_dir = _mounted_catalog_dir()
        if bundle_dir is None:
            return _err(409, {"error": "no_world_mounted"})
        spec, terms = _load_terms(bundle_dir)
        known = {t["slug"] for t in terms}
        name = str(body.get("term", "")).strip()
        new_slug = wf.slugify(name)
        if not new_slug:
            return _term_error("term", "give the term a name")
        if new_slug in known:
            return _err(409, {"error": "term_exists", "slug": new_slug})
        if (bad := _validate_term_fields(body, spec, known, new_slug)) is not None:
            return bad
        if "category" not in body:
            return _term_error("category", "pick a category")
        source = str(body.get("_draft_source") or "").strip()
        if not source or wf.tier_of_source(source) != "model-asserted":
            source = _operator_source()
        terms.append({
            "slug": new_slug, "term": name,
            "category": str(body["category"]),
            "short": str(body.get("short", "") or name)[:wf.MAX_SHORT],
            "definition": str(body.get("definition", "") or name)[:wf.MAX_DEFINITION],
            "example": str(body.get("example", ""))[:wf.MAX_EXAMPLE],
            "related": list(body.get("related") or []),
            "source": source,
        })
        if (err := await _reseal_and_swap(bundle_dir, terms)) is not None:
            terms.pop()
            return err
        manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
        return {"ok": True, "slug": new_slug, "tier": manifest.get("provenance_tier"),
                "counts": manifest.get("provenance_counts")}


@router.delete("/api/worlds/terms/{slug}")
async def api_term_delete(slug: str, request: Request):
    if (rej := _csrf_reject(request)) is not None:
        return rej
    async with _reseal_lock:
        bundle_dir = _mounted_catalog_dir()
        if bundle_dir is None:
            return _err(409, {"error": "no_world_mounted"})
        _spec, terms = _load_terms(bundle_dir)
        kept = [t for t in terms if t["slug"] != slug]
        if len(kept) == len(terms):
            return _err(404, {"error": "term_not_found", "slug": slug})
        if not kept:
            return _err(400, {"error": "last_term",
                              "message": "A world needs at least one term — delete the world instead."})
        for t in kept:   # auto-close inbound edges: deleting must always succeed
            t["related"] = [s for s in (t.get("related") or []) if s != slug]
        if (err := await _reseal_and_swap(bundle_dir, kept)) is not None:
            return err
        return {"ok": True, "deleted": slug}


@router.post("/api/worlds/terms/draft")
async def api_term_draft(request: Request):
    """Model-drafted single-term proposal — returned UNPERSISTED for review."""
    if (rej := _csrf_reject(request)) is not None:
        return rej
    body = await _json_body(request)
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return _err(409, {"error": "no_world_mounted"})
    name = str(body.get("term", "")).strip()
    if not (1 <= len(name) <= 120):
        return _term_error("term", "give the term a name")
    spec, terms = _load_terms(bundle_dir)
    subject = str(spec.get("display_name") or spec.get("slug"))

    def _draft() -> dict:
        from arail.router import ModelRouter
        router_ = ModelRouter(billing_source="agent")
        out: dict = {"short": "", "definition": "", "example": "", "related": []}
        r = router_.complete(
            f'Subject: "{subject}". Define the concept "{name}" as JSON: '
            f'{{"short":"one line","definition":"2-3 sentences","example":"one concrete example"}}.',
            max_tokens=700, temperature=0.2, top_p=0.9)
        parsed = wf.loose_json(getattr(r, "text", "") or "")
        model_name = getattr(r, "model", "") or "local"
        if isinstance(parsed, dict):
            out["short"] = str(parsed.get("short", ""))[:wf.MAX_SHORT]
            out["definition"] = str(parsed.get("definition", ""))[:wf.MAX_DEFINITION]
            out["example"] = str(parsed.get("example", ""))[:wf.MAX_EXAMPLE]
        roster = ", ".join(f"{t['slug']} ({t['term']})" for t in terms)
        r2 = router_.complete(
            f'Subject: "{subject}". From THIS list of known concepts:\n{roster}\n'
            f'Return JSON array "related" of the slugs most directly associated with '
            f'"{name}" (up to {wf.MAX_RELATED_PER_TERM}, choose ONLY slugs from the list).',
            max_tokens=400, temperature=0.1, top_p=0.9)
        parsed2 = wf.loose_json(getattr(r2, "text", "") or "")
        items = wf.first_array((parsed2 or {}).get("related", parsed2)
                               if isinstance(parsed2, dict) else parsed2)
        known = {t["slug"] for t in terms}
        seen: list[str] = []
        for x in items[:wf.MAX_RELATED_PER_TERM]:
            s = wf.slugify(str(x.get("slug") or x.get("term") or x)) if isinstance(x, dict) else wf.slugify(str(x))
            if s in known and s not in seen:
                seen.append(s)
        out["related"] = seen
        out["source"] = wf._source_tag_from_model(model_name)
        return out

    with agent_context.system_call("term-draft"):
        async with scheduler.inference_slot("term-draft"):
            proposal = await asyncio.to_thread(_draft)
    return {"proposal": proposal}


# ═══════════════════════════ CURATOR REVIEW ═══════════════════════════════

_review_lock = asyncio.Lock()
_review_state: dict[str, Any] = {"state": "idle"}
_review_cancel = threading.Event()


def _review_path(bundle_dir: Path) -> Path:
    return bundle_dir / "review.json"


async def _run_review(bundle_dir: Path, spec: dict, terms: list[dict]) -> None:
    global _review_state
    world = str(spec.get("slug", ""))
    display = str(spec.get("display_name", world))
    try:
        router_ = _review_router()
        with agent_context.system_call("world-review"):
            async with scheduler.inference_slot("world-review"):
                flags = await asyncio.to_thread(
                    wf.reconcile_terms, spec, terms,
                    router=router_, limit=REVIEW_BATCH, cancel=_review_cancel)
        doc = {
            "schema": REVIEW_SCHEMA,
            "world": world,
            "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": getattr(router_, "backend_name", None) or "local",
            "reviewed_count": min(REVIEW_BATCH, len(terms)),
            "flags": [f.to_dict() for f in flags],
        }
        _review_path(bundle_dir).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        _review_state = {"state": "done", "flags": len(flags)}
        activity_log.emit(
            "curator",
            f"Curator reviewed {doc['reviewed_count']} terms in '{display}' — "
            f"{len(flags)} flag{'s' if len(flags) != 1 else ''}"
            + (". See Knowledge → World Terms." if flags else ". All clear."),
            "success" if not flags else "warn")
    except Exception as e:  # noqa: BLE001
        _log.warning("world review failed: %s", e)
        _review_state = {"state": "error", "message": str(e)[:200]}
        activity_log.emit("curator", f"Curator review of '{display}' failed: {e}", "error")


@router.post("/api/worlds/review")
async def api_review_start(request: Request):
    global _review_state
    if (rej := _csrf_reject(request)) is not None:
        return rej
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return _err(409, {"error": "no_world_mounted"})
    async with _review_lock:
        if _review_state.get("state") == "running":
            return _err(409, {"error": "review_busy"})
        spec, terms = _load_terms(bundle_dir)
        _review_cancel.clear()
        _review_state = {"state": "running", "world": spec.get("slug")}
        asyncio.create_task(_run_review(bundle_dir, spec, terms))
    activity_log.emit("curator", f"Curator is reviewing '{spec.get('display_name')}'…", "info")
    return JSONResponse(status_code=202, content={"started": True})


@router.get("/api/worlds/review")
async def api_review_get():
    bundle_dir = _mounted_catalog_dir()
    flags: list = []
    doc: dict = {}
    if bundle_dir is not None and _review_path(bundle_dir).exists():
        try:
            doc = json.loads(_review_path(bundle_dir).read_bytes())
            if doc.get("schema") == REVIEW_SCHEMA:
                flags = doc.get("flags", [])
            else:
                doc = {}
        except Exception:  # noqa: BLE001
            doc = {}
    return {"state": _review_state.get("state", "idle"),
            "message": _review_state.get("message", ""),
            "reviewed_at": doc.get("reviewed_at"),
            "reviewed_count": doc.get("reviewed_count"),
            "flags": flags}


# ═══════════════════════ GROWTH ENGINE (organic evolution) ════════════════

_grow_lock = asyncio.Lock()
_grow_state: dict[str, Any] = {"state": "idle"}
_grow_cancel = threading.Event()
GROW_SCHEMA = "arail.world-evolution/v1"


def _evolution_path(bundle_dir: Path) -> Path:
    return bundle_dir / "evolution.json"


def _curation_router(brain: str) -> Any:
    """Which brain curates. 'auto'/'deep' → best local (deep when available);
    'local' → the on-GPU model; a provider id (e.g. 'claude', 'openrouter') →
    a router pointed at that cloud gateway, reusing the saved provider token
    (the exact plumbing the Chat Compute Source uses). Falls back to deep on
    any provider-setup failure so growth never hard-fails on a missing key."""
    brain = (brain or "auto").strip().lower()
    if brain in ("", "auto", "deep"):
        return _review_router()
    if brain == "local":
        from arail.router import ModelRouter
        return ModelRouter(billing_source="agent")
    # A cloud provider gateway (claude, openrouter, nim, huggingface, custom).
    try:
        from arail.portal.app import _provider_token, _PROVIDER_KEY_ENVS
        from arail.router import ModelRouter
        env = _PROVIDER_KEY_ENVS.get(brain)
        token = _provider_token(brain)
        if env and token:
            os.environ[env] = token
        backend = {"claude": "claude", "openrouter": "openrouter",
                   "custom": "openai_compat", "nim": "openai_compat",
                   "huggingface": "huggingface"}.get(brain, brain)
        return ModelRouter(backend=backend, billing_source="agent")
    except Exception as e:  # noqa: BLE001
        _log.warning("curation router for %r failed (%s); using deep/local", brain, e)
        return _review_router()


async def _run_grow(bundle_dir: Path, spec: dict, terms: list[dict], brain: str) -> None:
    """One growth pass: correct existing terms + add new ones, reversibly."""
    global _grow_state
    world = str(spec.get("slug", ""))
    display = str(spec.get("display_name", world))
    declared = {str(c.get("id", "")) for c in spec.get("categories", [])}
    try:
        router_ = _curation_router(brain)
        model_name = getattr(router_, "backend_name", None) or "local"

        def _prog(stage, done, total, note=""):
            global _grow_state
            _grow_state = {**_grow_state, "stage": stage, "message": note}

        with agent_context.system_call("world-grow"):
            async with scheduler.inference_slot("world-grow"):
                _grow_state = {**_grow_state, "stage": "reviewing"}
                flags = await asyncio.to_thread(
                    wf.reconcile_terms, spec, terms, router=router_,
                    limit=GROW_REVIEW_BATCH, cancel=_grow_cancel)
                _corrected, changes = wf.apply_corrections(terms, flags, declared)
                _grow_state = {**_grow_state, "stage": "growing"}
                new_terms = await asyncio.to_thread(
                    wf.propose_new_terms, spec, terms, router=router_,
                    limit=GROW_NEW_BATCH, cancel=_grow_cancel)

        merged = terms + new_terms
        # Re-gate + reseal + swap (auto-applied; fully reversible via the log).
        gate = wf.assert_closed_sourced_graph(merged, declared)
        if not gate.ok:
            present = {t["slug"] for t in merged}
            for t in merged:
                t["related"] = [r for r in t.get("related", []) if r in present and r != t["slug"]]
        await asyncio.to_thread(wf.reseal_bundle, bundle_dir, merged,
                                theme_validator=parse_world_theme)
        await asyncio.to_thread(wm.swap, bundle_dir)

        # Append a reversible evolution record.
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry = {
            "at": now, "model": model_name, "brain": brain,
            "added": [{"slug": t["slug"], "term": t["term"]} for t in new_terms],
            "corrections": changes,
        }
        doc = {"schema": GROW_SCHEMA, "world": world, "passes": []}
        if _evolution_path(bundle_dir).exists():
            try:
                prev = json.loads(_evolution_path(bundle_dir).read_bytes())
                if prev.get("schema") == GROW_SCHEMA:
                    doc = prev
            except Exception:  # noqa: BLE001
                pass
        doc.setdefault("passes", []).append(entry)
        _evolution_path(bundle_dir).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        _grow_state = {"state": "done", "added": len(new_terms), "corrected": len(changes)}
        activity_log.emit(
            "curator",
            f"World '{display}' evolved via {model_name}: +{len(new_terms)} new term"
            f"{'s' if len(new_terms) != 1 else ''}, {len(changes)} correction"
            f"{'s' if len(changes) != 1 else ''}. Reversible in the term editor.",
            "success")
    except Exception as e:  # noqa: BLE001
        _log.warning("world grow failed: %s", e)
        _grow_state = {"state": "error", "message": str(e)[:200]}
        activity_log.emit("curator", f"Growing '{display}' failed: {e}", "error")


@router.post("/api/worlds/grow")
async def api_grow_start(request: Request):
    global _grow_state
    if (rej := _csrf_reject(request)) is not None:
        return rej
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return _err(409, {"error": "no_world_mounted"})
    body = await _json_body(request)
    brain = str(body.get("brain", "auto"))
    async with _grow_lock:
        if _grow_state.get("state") == "running":
            return _err(409, {"error": "grow_busy"})
        spec, terms = _load_terms(bundle_dir)
        _grow_cancel.clear()
        _grow_state = {"state": "running", "world": spec.get("slug"), "stage": "starting", "brain": brain}
        asyncio.create_task(_run_grow(bundle_dir, spec, terms, brain))
    activity_log.emit("curator", f"Growing '{spec.get('display_name')}'…", "info")
    return JSONResponse(status_code=202, content={"started": True})


@router.get("/api/worlds/grow")
async def api_grow_get():
    bundle_dir = _mounted_catalog_dir()
    passes: list = []
    if bundle_dir is not None and _evolution_path(bundle_dir).exists():
        try:
            doc = json.loads(_evolution_path(bundle_dir).read_bytes())
            if doc.get("schema") == GROW_SCHEMA:
                passes = doc.get("passes", [])
        except Exception:  # noqa: BLE001
            pass
    return {"state": _grow_state.get("state", "idle"),
            "stage": _grow_state.get("stage", ""),
            "message": _grow_state.get("message", ""),
            "added": _grow_state.get("added"),
            "corrected": _grow_state.get("corrected"),
            "passes": passes}


@router.post("/api/worlds/grow/cancel")
async def api_grow_cancel(request: Request):
    if (rej := _csrf_reject(request)) is not None:
        return rej
    _grow_cancel.set()
    return {"ok": True}


def _scheduled_growth_enabled() -> bool:
    return os.getenv("ARAIL_WORLD_GROWTH", "on").strip().lower() not in ("off", "0", "false", "no")


async def growth_tick(last_grown_day: Optional[str]) -> Optional[str]:
    """One conservative autonomous-growth check on the mounted World. Uses the
    LOCAL brain — scheduled growth never auto-spends on a cloud API; a cloud
    brain is only ever used when the operator picks it for an on-demand pass.
    Runs only in the heavy (overnight) window, never while halted or while a
    grow is already running, and at most once per calendar day. Returns the
    day a pass ran (feed it back in on the next call). Called from the
    Librarian agent's loop, which owns the compiled-knowledge lifecycle."""
    global _grow_state
    if not _scheduled_growth_enabled():
        return last_grown_day
    from arail.scheduler import current_window, jobs_halted
    if current_window() != "heavy" or jobs_halted():
        return last_grown_day
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if last_grown_day == day or _grow_state.get("state") == "running":
        return last_grown_day
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return last_grown_day
    spec, terms = _load_terms(bundle_dir)
    _grow_cancel.clear()
    _grow_state = {"state": "running", "world": spec.get("slug"),
                   "stage": "scheduled", "brain": "auto"}
    activity_log.emit("librarian",
                      f"Overnight growth pass on '{spec.get('display_name')}'…", "info")
    await _run_grow(bundle_dir, spec, terms, "auto")
    return day


async def world_growth_loop() -> None:
    """Legacy standalone loop around growth_tick. The Librarian agent now owns
    the growth cadence — kept for direct callers/tests; no longer started at
    app boot. Returns immediately when disabled (ARAIL_WORLD_GROWTH=off)."""
    if not _scheduled_growth_enabled():
        return
    last_grown_day: Optional[str] = None
    while True:
        try:
            await asyncio.sleep(1800)  # check every 30 min
            last_grown_day = await growth_tick(last_grown_day)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _log.warning("world growth loop: %s", e)


# ═══════════════════════ world-first helpers ══════════════════════════════

@router.get("/api/worlds/goal-suggestions")
async def api_goal_suggestions():
    record = wm.current_mount()
    if record is None:
        return {"world": None, "suggestions": []}
    bundle_dir = _mounted_catalog_dir()
    if bundle_dir is None:
        return {"world": record.world, "suggestions": []}
    spec, terms = _load_terms(bundle_dir)
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    tier, _counts = _live_tier(manifest, terms)
    return {
        "world": record.world,
        "display_name": manifest.get("display_name"),
        "tier": tier,
        "suggestions": wf.goal_suggestions(spec, tier),
    }
