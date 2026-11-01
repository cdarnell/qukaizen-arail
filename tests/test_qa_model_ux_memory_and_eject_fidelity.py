"""QA — Persistence & Honesty (real-OS subset).

Sprint: 2026-07-20-model-ux-unification
Reviewed at: BUILD_LOG.md @ 692b460 (branch qukaizen/model-ux-unification)

This file is the QA pass the architecture's Test Strategy explicitly
scoped to "the operator's own airgapped Mac" and the BUILD_LOG deferred as
"out of unit-test reach" (T-EJECT-OLLAMA / F-EJECTREAL, real warmth
cross-check). It is weighted, per the QA mandate, toward the two things
that have broken user trust in THIS codebase historically:

  (a) does displayed memory/fit information match reality under real
      conditions — verified against ACTUAL system state (psutil
      virtual_memory, live `ollama ps` / /api/ps), never "the UI didn't
      throw";
  (b) does every Unload button actually free memory for every model type
      the UI offers one on — verified against the LIVE ollama daemon
      (memory really drops off /api/ps), and the honesty contract checked
      for every runtime the rail can render an eject affordance on.

The real-daemon tests skip gracefully when ollama is absent (so CI without
a daemon stays green) but DO run on a machine with a live daemon. When
they run they mutate only a small disposable model (llama3.2:1b); they
never touch whatever the operator has resident.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

_CHAT_HTML = os.path.join(
    _REPO_ROOT, "src", "arail", "portal", "templates", "chat.html"
)

_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
# Small, disposable model to prove the real eject path. Chosen so the test
# never evicts whatever the operator actually has resident.
_DISPOSABLE_MODEL = "llama3.2:1b"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod
    return TestClient(app_mod.app), app_mod


def _chat_html_text() -> str:
    with open(_CHAT_HTML, "r", encoding="utf-8") as f:
        return f.read()


def _ollama_get(path: str, timeout: float = 2.0):
    req = urllib.request.Request(
        f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}{path}", method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ollama_post(path: str, payload: dict, timeout: float = 120.0):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ollama_daemon_up() -> bool:
    try:
        _ollama_get("/api/ps", timeout=1.5)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ollama_resident_ids() -> set[str]:
    data = _ollama_get("/api/ps", timeout=2.0)
    ids = set()
    for m in data.get("models") or []:
        name = m.get("name") or m.get("model")
        if name:
            ids.add(str(name))
    return ids


def _ollama_installed_ids() -> set[str]:
    data = _ollama_get("/api/tags", timeout=3.0)
    ids = set()
    for m in data.get("models") or []:
        name = m.get("name") or m.get("model")
        if name:
            ids.add(str(name))
    return ids


_daemon = pytest.mark.skipif(
    not _ollama_daemon_up(), reason="live ollama daemon not reachable"
)


# ===========================================================================
# (a) DISPLAYED MEMORY / FIT vs REAL SYSTEM STATE
# ===========================================================================

def test_memory_snapshot_free_matches_live_psutil_within_tolerance():
    """`_local_memory_snapshot().free_gb` must reflect real OS truth
    (psutil.virtual_memory().available), not a stale/constant/optimistic
    number. Two back-to-back reads on an idle-ish box should agree within
    a small band; the point is to catch a fabricated value (0, total, a
    constant), which would blow far past the band."""
    import psutil
    import arail.portal.app as app_mod

    ref_avail = psutil.virtual_memory().available / (1024 ** 3)
    snap = app_mod._local_memory_snapshot()
    free = float(snap["free_gb"])
    total = float(snap["total_gb"])

    assert total > 0, "total memory must be a real positive number"
    assert 0 < free < total, (
        f"free_gb={free} must be a real value strictly between 0 and "
        f"total_gb={total} (0 == blank lie, ==total == fallback lie)"
    )
    assert abs(free - ref_avail) <= 2.0, (
        f"snapshot free_gb={free} diverges from live psutil "
        f"available={ref_avail:.1f} by more than 2 GB — not tracking OS truth"
    )


def test_api_chat_models_hardware_free_matches_live_psutil():
    """End-to-end: the number the frontend actually renders
    (compact.hardware.free_gb) must be a REAL psutil reading, and the
    top-level `hardware` dead-field must be gone (F-BLANK / F-DEADFIELD).

    The endpoint reads memory MID-request, and the request itself transiently
    allocates a few GB (gallery scan / catalog / model_specs import), so its
    snapshot legitimately reads a little lower than a psutil sample taken just
    outside the call. We bracket the call with two psutil reads and require
    the rendered number to sit inside a generous envelope of them — tight
    enough to catch every lie class (0/blank, ==total fallback-lie, a stuck
    constant, negative) without flaking on the request's own memory pressure.
    The tight ±2 GB fidelity of the underlying snapshot is proven separately
    by test_memory_snapshot_free_matches_live_psutil_within_tolerance."""
    import psutil
    client, _ = _client()

    before = psutil.virtual_memory().available / (1024 ** 3)
    body = client.get("/api/chat/models").json()
    after = psutil.virtual_memory().available / (1024 ** 3)

    assert body.get("hardware") is None, (
        "top-level `hardware` must be DELETED (F-DEADFIELD) — the frontend "
        "reads compact.hardware"
    )
    hw = body["compact"]["hardware"]
    free = float(hw["free_gb"])
    total = float(hw["total_gb"])
    assert 0 < free < total, f"free_gb={free} is a lie (blank/total/negative)"
    assert abs(free - total) > 0.05, (
        f"free_gb={free} == total_gb={total} is the F-FALLBACKLIE signature"
    )
    lo = min(before, after) - 6.0  # endpoint reads lower mid-request
    hi = max(before, after) + 3.0
    assert lo <= free <= hi, (
        f"rendered free_gb={free} outside the live-psutil envelope "
        f"[{lo:.1f}, {hi:.1f}] (bracket {before:.1f}/{after:.1f}) — not a "
        f"real reading"
    )


def test_no_good_fit_chip_on_any_real_row_that_exceeds_free_memory():
    """F-FAKEFIT global invariant against the REAL installed model list on
    this machine: no row may render `Good` while its estimated need exceeds
    real free memory. This is the exact five-times-shipped lie the sprint
    exists to kill, checked against live data, not a fixture."""
    client, _ = _client()
    body = client.get("/api/chat/models").json()
    free_gb = float(body["compact"]["hardware"]["free_gb"])
    items = body["compact"]["local_models"]["items"]

    offenders = [
        (it["id"], it.get("estimated_vram_gb"), it["fit"]["verdict"])
        for it in items
        if it["fit"]["verdict"] == "Good"
        and isinstance(it.get("estimated_vram_gb"), (int, float))
        and it["estimated_vram_gb"] > free_gb
    ]
    assert not offenders, (
        f"models rendered 'Good' while needing more than {free_gb} GB free: "
        f"{offenders}"
    )


def test_near_oom_fit_is_honest_using_real_free_memory():
    """Near-OOM honesty derived from the machine's ACTUAL free memory: a
    model needing ~2x free must never say Good; ~1.05x must be Marginal at
    best; ~0.5x may be Good. Anchors the verdict math to real hardware, not
    a hand-picked constant."""
    import arail.portal.app as app_mod

    free = float(app_mod._local_memory_snapshot()["free_gb"])
    assert free > 0, "precondition: real free memory must be readable"

    v_over = app_mod._fit_verdict_label(free * 2.0, free)
    v_edge = app_mod._fit_verdict_label(free * 1.05, free)
    v_fits = app_mod._fit_verdict_label(free * 0.5, free)

    assert v_over == "Requires streaming", v_over
    assert v_edge in ("Marginal", "Requires streaming"), v_edge
    assert v_edge != "Good", "a model needing MORE than free memory cannot be Good"
    assert v_fits == "Good", v_fits


def test_psutil_import_failure_never_fabricates_optimistic_free_memory(monkeypatch):
    """F-FALLBACKLIE against the REAL darwin sysctl fallback path: if psutil
    is unavailable, the snapshot must NOT set free_gb=total_gb (a fake
    optimistic 'Good'). free_gb must stay 0 -> verdict Unknown."""
    import builtins
    import arail.portal.app as app_mod

    real_import = builtins.__import__

    def _no_psutil(name, *a, **kw):
        if name == "psutil":
            raise ImportError("simulated: psutil unavailable")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_psutil)
    snap = app_mod._local_memory_snapshot()

    # On this darwin box the sysctl branch still yields a real total.
    assert snap["free_gb"] == 0.0, (
        f"fallback must leave free_gb=0 (Unknown), got {snap['free_gb']}"
    )
    if snap["total_gb"] > 0:
        assert snap["free_gb"] != snap["total_gb"], (
            "free_gb must never equal total_gb on the fallback path — that is "
            "the fabricated-optimistic lie F-FALLBACKLIE closes"
        )
    assert app_mod._fit_verdict_label(14.0, snap["free_gb"]) == "Unknown"


# ===========================================================================
# (a) WARMTH DISPLAYED vs REAL `ollama ps`
# ===========================================================================

@_daemon
def test_warmth_probe_matches_live_ollama_ps_exactly():
    """Screen-vs-terminal: `_ollama_ps_resident_ids()` must equal what the
    live daemon reports as resident right now — no derivation from
    installed/tags, no stale client Set."""
    import arail.portal.app as app_mod

    truth = _ollama_resident_ids()
    probed = app_mod._ollama_ps_resident_ids()
    assert probed == truth, (
        f"warmth probe {probed} disagrees with live `ollama ps` {truth}"
    )


@_daemon
def test_installed_but_cold_ollama_row_is_not_marked_warm():
    """An installed model that is NOT resident must render warm=False; a
    resident one must render warm=True — both checked against the live
    daemon, so the dot can never claim warm for a cold model or vice versa
    (F-WARMDOT)."""
    import arail.portal.app as app_mod

    installed = _ollama_installed_ids()
    resident = _ollama_resident_ids()
    cold = installed - resident
    if not cold:
        pytest.skip("no installed-but-cold ollama model to check")

    cold_id = sorted(cold)[0]
    entry_cold = app_mod._build_local_model_entry(
        cold_id, runtime="ollama", size_gb=1.3, modified="", endpoint=None,
        current=None, detected_gb=36.0, free_gb=20.0,
        warm=(cold_id in app_mod._ollama_ps_resident_ids()),
    )
    assert entry_cold["warm"] is False, (
        f"{cold_id} is not in live `ollama ps` yet rendered warm=True"
    )

    if resident:
        hot_id = sorted(resident)[0]
        entry_hot = app_mod._build_local_model_entry(
            hot_id, runtime="ollama", size_gb=4.7, modified="", endpoint=None,
            current=None, detected_gb=36.0, free_gb=20.0,
            warm=(hot_id in app_mod._ollama_ps_resident_ids()),
        )
        assert entry_hot["warm"] is True, (
            f"{hot_id} IS resident in live `ollama ps` but rendered warm=False"
        )


# ===========================================================================
# (b) EVERY UNLOAD BUTTON — does it actually free memory?
# ===========================================================================

def test_rail_eject_button_is_offered_only_on_the_ollama_runtime():
    """QA-1, relocated (sprints/2026-08-11-two-slot-chat-models Phase 5):
    the per-row rail card this test originally pinned is gone — eject now
    lives on the resident chip itself (#resident-eject-btn), shown/hidden
    by updateChipWarmState(). The property QA-1 established still holds:
    the resident chip's eject is gated to the ollama runtime specifically
    (the only one /api/chat/eject can actually free in-process). The old
    disabled "can't hot-free" row affordance has no chip equivalent — a
    non-freeable warm resident model (mlx/cpu/cuda) just doesn't show an
    eject control at all, which is still honest (no false promise), just
    via omission rather than a disabled/explained button."""
    text = _chat_html_text()
    assert 'id="resident-eject-btn"' in text
    assert "if (ejectA) ejectA.hidden = !(warmA && State.currentRuntime === 'ollama');" in text, (
        "QA-1 fix not found — the resident chip's eject should be gated to "
        "the ollama runtime specifically"
    )


def test_eject_endpoint_never_reports_false_success_for_any_ui_runtime(monkeypatch):
    """(b) core contract: for EVERY runtime the rail can render an eject
    button on, the endpoint must never claim success it did not achieve.
    Only ollama (real `ollama stop`) can free in-process; mlx / mlx-openai /
    cpu / cuda cannot and must return ok:false + requires_restart, never a
    lying ok:true."""
    client, _ = _client()

    # In-process backends the UI offers an eject button on but that CANNOT
    # be hot-freed: must be honest ok:false, never a false success.
    for runtime in ("mlx-openai", "mlx", "cpu", "cuda"):
        body = client.post("/api/chat/eject", json={"runtime": runtime}).json()
        assert body["ok"] is False, f"{runtime}: eject claimed success it cannot deliver"
        assert body["requires_restart"] is True, f"{runtime}: must disclose restart need"
        assert body["freed"] == [], f"{runtime}: nothing was freed; must not claim otherwise"
        assert "requires_restart" in body and "notes" in body

    # ollama with no model target -> honest error, never a bare ok:true.
    body = client.post("/api/chat/eject", json={"runtime": "ollama"}).json()
    assert body["ok"] is False


def test_mlx_openai_unload_button_no_longer_overpromises():
    """QA-1, relocated (sprints/2026-08-11-two-slot-chat-models Phase 5):
    the endpoint stays honest (unchanged) — a warm mlx-openai resident
    model still gets ok:false + a restart note. The chip-level gate (see
    test above) means the resident chip's eject control isn't even shown
    for a non-ollama runtime, so there's no working-looking button whose
    promise could disagree with the endpoint's real capability."""
    client, _ = _client()
    body = client.post("/api/chat/eject", json={"runtime": "mlx-openai"}).json()
    assert body["ok"] is False
    assert body["freed"] == []
    assert any("restart" in n.lower() for n in body["notes"])
    text = _chat_html_text()
    assert "if (ejectA) ejectA.hidden = !(warmA && State.currentRuntime === 'ollama');" in text


@_daemon
def test_ollama_unload_button_actually_frees_memory_on_live_daemon():
    """(b) THE real one — the T-EJECT-OLLAMA / F-EJECTREAL check the build
    deferred as out-of-unit-test-reach. Make a small model genuinely
    resident, confirm it via the LIVE daemon, hit the arail eject endpoint
    (the exact code path the Unload button calls), then confirm the model
    really dropped off `ollama ps`. Memory freeing is proven against the
    daemon's own residency signal, not 'the UI didn't throw'.

    Never touches whatever the operator has resident — only llama3.2:1b."""
    import psutil
    client, _ = _client()

    if _DISPOSABLE_MODEL not in _ollama_installed_ids():
        pytest.skip(f"{_DISPOSABLE_MODEL} not installed; cannot run real eject")

    others_before = _ollama_resident_ids() - {_DISPOSABLE_MODEL}

    # 1) Make it genuinely resident (empty prompt = load only, keep_alive set).
    _ollama_post(
        "/api/generate",
        {"model": _DISPOSABLE_MODEL, "keep_alive": "120s"},
        timeout=120.0,
    )
    deadline = time.time() + 60
    while _DISPOSABLE_MODEL not in _ollama_resident_ids() and time.time() < deadline:
        time.sleep(0.5)
    assert _DISPOSABLE_MODEL in _ollama_resident_ids(), (
        "precondition failed: could not make the model resident to test eject"
    )
    avail_before = psutil.virtual_memory().available / (1024 ** 3)

    # 2) Hit the exact endpoint the Unload button calls.
    body = client.post(
        "/api/chat/eject",
        json={"runtime": "ollama", "model": _DISPOSABLE_MODEL},
    ).json()
    assert body["ok"] is True, f"real ollama eject must report ok:true, got {body}"
    assert body["freed"] == [f"ollama:{_DISPOSABLE_MODEL}"], body
    assert body["requires_restart"] is False

    # 3) Prove memory really freed: gone from the daemon's residency list.
    deadline = time.time() + 30
    while _DISPOSABLE_MODEL in _ollama_resident_ids() and time.time() < deadline:
        time.sleep(0.5)
    resident_after = _ollama_resident_ids()
    assert _DISPOSABLE_MODEL not in resident_after, (
        "eject reported ok:true but the model is STILL resident in `ollama ps` "
        "— the Unload button lied about freeing memory"
    )
    # The operator's own resident models must be untouched by our eject.
    assert others_before <= resident_after or others_before - resident_after == set(), (
        "eject of the disposable model must not have evicted other models"
    )
    avail_after = psutil.virtual_memory().available / (1024 ** 3)
    # Informational: freeing must not consume memory. (macOS unified memory
    # is noisy, so we assert direction, not magnitude.)
    assert avail_after >= avail_before - 2.0, (
        f"available fell after an eject ({avail_before:.1f}->{avail_after:.1f})"
    )


# ===========================================================================
# SECURITY — the only subprocess this sprint's surface reaches is
# `ollama stop <model>`. The model id is the one user-controlled value that
# flows toward it; prove it is validated (allowlist) BEFORE any subprocess
# runs, and passed as an argv element (no shell), so injection is
# structurally impossible.
# ===========================================================================

@pytest.mark.parametrize(
    "payload",
    [
        "llama3.2:1b; rm -rf /tmp/pwned",   # command chaining
        "$(touch /tmp/pwned)",               # command substitution
        "`id`",                              # backtick substitution
        "../../etc/passwd",                  # path traversal
        "a" * 300,                           # oversized (>256)
        "model\nollama stop other",          # newline injection
    ],
)
def test_eject_ollama_rejects_injection_before_any_subprocess(monkeypatch, payload):
    """An injection-laden / traversal / oversized model id must be refused
    by `_validate_local_model_id_relaxed` BEFORE `subprocess.run` is ever
    reached — validation-precedes-execution, not validation-after."""
    import arail.portal.app as app_mod

    called = {"n": 0}

    def _tripwire(*a, **kw):
        called["n"] += 1
        raise AssertionError(
            f"subprocess.run reached with an unvalidated id: {a!r}"
        )

    monkeypatch.setattr(subprocess, "run", _tripwire)
    client, _ = _client()

    body = client.post(
        "/api/chat/eject", json={"runtime": "ollama", "model": payload}
    ).json()
    assert body["ok"] is False
    assert "error" in body
    assert called["n"] == 0, "subprocess.run must not run for a rejected id"


def test_eject_ollama_passes_model_as_argv_not_shell_string(monkeypatch):
    """Even a validated id must reach `ollama stop` as a discrete argv
    element (no shell=True), so a name that survived the allowlist can never
    be reinterpreted by a shell."""
    import arail.portal.app as app_mod

    monkeypatch.setattr(
        app_mod, "_validate_local_model_id_relaxed", lambda m: (True, "")
    )
    seen = {}

    class _Done:
        returncode = 0
        stderr = ""
        stdout = "stopped"

    def _capture(args, *a, **kw):
        seen["args"] = args
        seen["shell"] = kw.get("shell", False)
        return _Done()

    monkeypatch.setattr(subprocess, "run", _capture)
    client, _ = _client()
    client.post("/api/chat/eject", json={"runtime": "ollama", "model": "qwen2.5:7b"})

    assert seen["args"] == ["ollama", "stop", "qwen2.5:7b"], seen
    assert seen["shell"] is False, "eject must never shell out the model id"


# ===========================================================================
# Keep-watch suppression on eject (sprints/2026-08-11-two-slot-chat-models
# Part 3) — a successful eject of the RESIDENT model must not have the
# background loop immediately re-warm it right back; ejecting some OTHER
# installed model must not suppress keep-watch on the resident slot at all.
# ===========================================================================

def test_ejecting_the_resident_model_suppresses_keepwatch(monkeypatch):
    import arail.portal.app as app_mod
    from arail.portal import model_warmth

    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    monkeypatch.setattr(
        app_mod, "_validate_local_model_id_relaxed", lambda m: (True, "")
    )

    class _Done:
        returncode = 0
        stderr = ""
        stdout = "stopped"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    called = []
    monkeypatch.setattr(model_warmth, "suppress_tier0_keepwatch", lambda *a, **k: called.append(1))

    client, _ = _client()
    body = client.post(
        "/api/chat/eject", json={"runtime": "ollama", "model": "llama-ai-eng:latest"}
    ).json()

    assert body["ok"] is True
    assert called == [1], "ejecting the resident model must suppress keep-watch"


def test_ejecting_a_different_model_does_not_suppress_keepwatch(monkeypatch):
    """Ejecting some other installed Ollama model — not the resident slot
    — must leave keep-watch alone; it only guards the resident model."""
    import arail.portal.app as app_mod
    from arail.portal import model_warmth

    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    monkeypatch.setattr(
        app_mod, "_validate_local_model_id_relaxed", lambda m: (True, "")
    )

    class _Done:
        returncode = 0
        stderr = ""
        stdout = "stopped"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())

    called = []
    monkeypatch.setattr(model_warmth, "suppress_tier0_keepwatch", lambda *a, **k: called.append(1))

    client, _ = _client()
    body = client.post(
        "/api/chat/eject", json={"runtime": "ollama", "model": "some-other-model:latest"}
    ).json()

    assert body["ok"] is True
    assert called == [], "ejecting a non-resident model must not suppress resident keep-watch"


def test_failed_eject_does_not_suppress_keepwatch(monkeypatch):
    """Nothing was actually frozen — suppressing keep-watch for a failed
    eject would just make the resident model incorrectly stay cold."""
    import arail.portal.app as app_mod
    from arail.portal import model_warmth

    monkeypatch.setenv("MODEL_NAME", "llama-ai-eng:latest")
    monkeypatch.setattr(
        app_mod, "_validate_local_model_id_relaxed", lambda m: (True, "")
    )

    class _Failed:
        returncode = 1
        stderr = "no such model"
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Failed())

    called = []
    monkeypatch.setattr(model_warmth, "suppress_tier0_keepwatch", lambda *a, **k: called.append(1))

    client, _ = _client()
    body = client.post(
        "/api/chat/eject", json={"runtime": "ollama", "model": "llama-ai-eng:latest"}
    ).json()

    assert body["ok"] is False
    assert called == []


# ===========================================================================
# (b) REGRESSION — HON-1: rail-card eject clears the warm dot before it
# confirms the eject succeeded (already filed as a dated follow-up; pinned
# here as a concrete, reproducible defect so it cannot silently persist).
# ===========================================================================

def test_hon1_rail_eject_only_clears_warm_dot_on_confirmed_success():
    """HON-1 (REVIEW.md #2), relocated (sprints/2026-08-11-two-slot-chat-
    models Phase 5): the separate rail-card/active-card eject handlers
    this test pinned as a matching pair are gone, unified into one shared
    ejectModel(side) that both chips call. A runtime whose eject returns
    ok:false (mlx-openai, cpu, cuda, a failed ollama stop) still must not
    flip the warm dot to cold — the dot only clears once the endpoint
    confirms something was actually freed."""
    text = _chat_html_text()
    assert "if (d.ok) {" in text and "if (model) State.warmModels.delete(model);" in text, (
        "HON-1 fix not found — ejectModel should only clear the warm dot "
        "when d.ok is true"
    )
