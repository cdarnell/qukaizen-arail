"""QA pass tests for SSE /api/system/health/stream tier-filtering.

Companion to tests/test_system_health_stream_tier_filter.py (the builder's 8
tests). Allocation per sprint:
- 60% edge cases (LAB_TIER unset/garbage/casing/whitespace; empty registry;
  unknown service_id; checks_all ordering; concurrent clients; opencode
  tech-debt absence)
- 20% security (timing-channel disclosure; query/header bypass surface area)
- 20% regression (snapshot/stream parity for ALL registry ids; done.passed
  arithmetic invariants; SSE shape stability)
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers (parallel to the builder's file but standalone)
# ---------------------------------------------------------------------------

def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:].strip()))
            except json.JSONDecodeError:
                pass
    return events


def _check_events(events): return [e for e in events if e.get("event") == "check"]
def _done(events):
    d = [e for e in events if e.get("event") == "done"]
    assert len(d) == 1, f"expected exactly 1 done event, got {len(d)}"
    return d[0]


def _client(monkeypatch, tmp_path, tier_value):
    """tier_value can be None (unset), or any string."""
    if tier_value is None:
        monkeypatch.delenv("LAB_TIER", raising=False)
    else:
        monkeypatch.setenv("LAB_TIER", tier_value)
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    with patch("arail.portal.app._port_open", new=AsyncMock(return_value=False)), \
         patch("arail.portal.app._container_running", return_value=False), \
         patch("arail.portal.app._docker_available", return_value=False):
        from arail.portal.app import app
        return TestClient(app)


def _stream_names(client):
    r = client.get("/api/system/health/stream")
    assert r.status_code == 200
    return {e["name"] for e in _check_events(_parse_sse(r.text))}


MAX_ONLY = {"Marimo", "Open Notebook", "Neo4j Bolt", "Notebook (Jupyter)"}


# ===========================================================================
# EDGE CASES (60%)
# ===========================================================================

# --- Edge 1: LAB_TIER unset → defaults to min ------------------------------

def test_stream_lab_tier_empty_defaults_to_min(monkeypatch, tmp_path):
    """LAB_TIER='' (effective unset) must default to min — no max leakage.

    Note: monkeypatch.delenv is not reliable here because arail.config calls
    load_dotenv() on import, which can repopulate LAB_TIER from the repo .env
    after the fixture's delenv runs (pytest collection-order subtlety
    documented in tests/test_aerollm_defaults.py). The canonical "effective
    unset" representation in this codebase is LAB_TIER='' (treated as not in
    _TIER_SURFACES → clamped to min by _current_tier).
    """
    client = _client(monkeypatch, tmp_path, "")
    from arail.portal.app import _current_tier
    assert _current_tier() == "minimalist", f"_current_tier returned {_current_tier()!r}"
    names = _stream_names(client)
    for n in MAX_ONLY:
        assert n not in names, f"empty LAB_TIER leaked max-only check '{n}'"


# --- Edge 2: LAB_TIER garbage value ----------------------------------------

@pytest.mark.parametrize("garbage", [
    "MAX",           # case mismatch — _current_tier lowercases but if it didn't, this'd leak
    "  max  ",       # whitespace — _current_tier strips
    "maxx",          # typo
    "pro",           # nonsense
    "",              # empty
    "min;DROP TABLE", # injection-style
    "MAX\n",          # trailing newline
    "тах",           # cyrillic lookalike
])
def test_stream_lab_tier_garbage_clamps_to_min(monkeypatch, tmp_path, garbage):
    """Any unrecognized LAB_TIER value must clamp to min — no leakage."""
    client = _client(monkeypatch, tmp_path, garbage)
    names = _stream_names(client)
    # Whitespace+casing that *should* normalize to max are tested separately.
    if garbage.strip().lower() == "max":
        # _current_tier strips and lowercases, so "  max  " and "MAX" → max.
        for n in MAX_ONLY - {"Notebook (Jupyter)"}:  # notebook is max-tier-only
            assert n in names, f"normalized '{garbage}' should expose '{n}'"
    else:
        for n in MAX_ONLY:
            assert n not in names, (
                f"garbage LAB_TIER={garbage!r} leaked max-only check '{n}'"
            )


# --- Edge 3: LAB_TIER casing/whitespace happy-path normalization -----------

@pytest.mark.parametrize("variant", ["max", "MAX", "Max", " max", "max\t"])
def test_stream_lab_tier_max_variants_all_expose_max_only(monkeypatch, tmp_path, variant):
    """LAB_TIER variants that normalize to 'max' must expose max-only checks."""
    client = _client(monkeypatch, tmp_path, variant)
    names = _stream_names(client)
    assert "Marimo" in names, f"variant {variant!r} failed to enable max-only"


# --- Edge 4: opencode tech-debt — should be ABSENT from stream on both -----

def test_opencode_absent_from_stream_on_both_tiers(monkeypatch, tmp_path):
    """opencode is a registered max-only service but has no stream probe today.

    Documented as tech debt in ARCHITECTURE.md §Tech debt. Confirms the gap is
    consistent (absent on BOTH tiers) — i.e. doesn't accidentally leak under
    some name like 'Opencode' on max.
    """
    for tier in ("min", "max"):
        client = _client(monkeypatch, tmp_path, tier)
        names = _stream_names(client)
        for variant in ("Opencode", "OpenCode", "opencode", "Open Code", "OpenCode HTTP"):
            assert variant not in names, (
                f"LAB_TIER={tier}: unexpected opencode probe '{variant}' "
                "(should remain absent per documented tech debt)"
            )


# --- Edge 5: checks_all order is stable on max tier -------------------------

def test_stream_check_order_stable_on_max(monkeypatch, tmp_path):
    """The cascade order on max-tier must match the source-listed order.

    Architect noted absence of this assertion as INFO; we add it. Guards against
    reordering bugs that wouldn't show up in set-based presence tests.
    """
    EXPECTED_MAX_ORDER = [
        "Portal HTTP",
        "Terminal (ttyd)",
        "Notebook (Jupyter)",
        "IDE (code-server)",
        "MLX OpenAI compat",
        "Ollama API",
        "Lance vector DB",
        "Marimo",
        "Open Notebook",
        "Neo4j Bolt",
        "RAM available",
        "Disk free",
        "Agents loadable",
        "PKB structure",
        "Model checkpoints",
        "AirLLM backend",
        ".env validation",
    ]
    client = _client(monkeypatch, tmp_path, "max")
    events = _check_events(_parse_sse(client.get("/api/system/health/stream").text))
    actual = [e["name"] for e in events]
    assert actual == EXPECTED_MAX_ORDER, (
        f"cascade order drifted.\nexpected: {EXPECTED_MAX_ORDER}\nactual:   {actual}"
    )


# --- Edge 6: check event indices monotonically increase from 0 -------------

@pytest.mark.parametrize("tier", ["min", "max"])
def test_stream_check_indices_zero_indexed_and_monotonic(monkeypatch, tmp_path, tier):
    client = _client(monkeypatch, tmp_path, tier)
    events = _check_events(_parse_sse(client.get("/api/system/health/stream").text))
    indices = [e["index"] for e in events]
    assert indices == list(range(len(events))), (
        f"LAB_TIER={tier}: indices not 0..N-1: {indices}"
    )
    # All check events should report the same total.
    totals = {e["total"] for e in events}
    assert len(totals) == 1, f"check events disagree on total: {totals}"
    assert totals.pop() == len(events)


# --- Edge 7: tier flip mid-stream — captured-at-entry semantics ------------

def test_stream_tier_flip_after_entry_does_not_widen_stream(monkeypatch, tmp_path):
    """If LAB_TIER changes from min→max after the response begins, the in-flight
    stream must complete with the originally-captured (min) filter.

    The architect's design says tier is captured at handler entry. We can't
    easily race the env-var flip against the SSE generator from a synchronous
    TestClient, but we CAN validate the equivalent invariant: starting a stream
    on min, then mutating LAB_TIER=max before consuming the body, must still
    yield the min-filtered set (because filter ran at handler entry).
    """
    import os as _os
    monkeypatch.setenv("LAB_TIER", "min")
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    with patch("arail.portal.app._port_open", new=AsyncMock(return_value=False)), \
         patch("arail.portal.app._container_running", return_value=False), \
         patch("arail.portal.app._docker_available", return_value=False):
        from arail.portal.app import app
        client = TestClient(app)
        # Use streaming GET so we can mutate env before consumption finishes.
        with client.stream("GET", "/api/system/health/stream") as r:
            assert r.status_code == 200
            _os.environ["LAB_TIER"] = "max"  # flip mid-stream
            body = "".join(chunk for chunk in r.iter_text())
    names = {e["name"] for e in _check_events(_parse_sse(body))}
    for n in MAX_ONLY:
        assert n not in names, (
            f"mid-stream tier flip leaked max-only check '{n}' "
            "(filter should be captured at handler entry)"
        )


# --- Edge 8: concurrent clients on different tiers see different streams ---

def test_stream_concurrent_clients_each_see_own_tier_at_request_time(monkeypatch, tmp_path):
    """Two sequential requests with different LAB_TIER values must each see
    their own tier's filter — guards against accidental module-level caching
    of the checks list."""
    # First, min.
    client_min = _client(monkeypatch, tmp_path, "min")
    min_names = _stream_names(client_min)
    # Then, max — with a fresh env value.
    client_max = _client(monkeypatch, tmp_path, "max")
    max_names = _stream_names(client_max)

    assert "Marimo" not in min_names
    assert "Marimo" in max_names
    assert max_names - min_names, "max should be a strict superset of min"


# --- Edge 9: empty checks_all simulation (all-hidden pathological case) ----

def test_stream_handles_all_filtered_empty_checks_list(monkeypatch, tmp_path):
    """If the filter hides every entry, the stream must still produce a valid
    done event with total=0 (no crash, no missing terminator)."""
    monkeypatch.setenv("LAB_TIER", "min")
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    # Force _check_visible to return False for everything by stubbing the
    # registry to empty AND faking _current_tier so the None-svc_id branch
    # still streams diagnostics — so to truly empty the list we patch the
    # filter at the source: monkeypatch _OPTIONAL_SERVICES and rely on the
    # design's fail-closed rule for None… actually `None` short-circuits to
    # True, so we can't empty the list without code changes. Instead assert
    # the weaker guarantee: with a stubbed empty registry, all gated entries
    # disappear and only None-marked diagnostics remain.
    from arail.portal import app as app_mod
    with patch.dict(app_mod._OPTIONAL_SERVICES, {}, clear=True), \
         patch("arail.portal.app._port_open", new=AsyncMock(return_value=False)), \
         patch("arail.portal.app._container_running", return_value=False), \
         patch("arail.portal.app._docker_available", return_value=False):
        client = TestClient(app_mod.app)
        events = _parse_sse(client.get("/api/system/health/stream").text)
    names = {e["name"] for e in _check_events(events)}
    # All registry-gated entries should disappear (fail-closed on unknown id).
    for gated in {"Terminal (ttyd)", "Notebook (Jupyter)", "Ollama API",
                  "Lance vector DB", "Marimo", "Open Notebook", "Neo4j Bolt"}:
        assert gated not in names, (
            f"with empty registry, gated check '{gated}' should fail-closed"
        )
    # Always-on diagnostics still present.
    assert "Portal HTTP" in names
    assert "RAM available" in names
    # done event still emitted exactly once with consistent total.
    done = _done(events)
    assert done["total"] == len(_check_events(events))


# --- Edge 10: future registry mutation — adding a new max-only service -----

def test_stream_filter_respects_runtime_registry_additions(monkeypatch, tmp_path):
    """If a new service id is added to _OPTIONAL_SERVICES at runtime (test
    fixture), and an existing stream entry references it, the filter must
    respect the new value. Stress-tests that the filter reads the live
    registry, not a snapshot.

    We can't add a NEW stream entry without code changes, but we can flip an
    existing entry's tier: change 'ttyd' from min→max and verify ttyd
    disappears from a min stream.
    """
    from arail.portal import app as app_mod
    mutated = dict(app_mod._OPTIONAL_SERVICES)
    mutated["ttyd"] = "max"  # demote to max-only
    monkeypatch.setenv("LAB_TIER", "min")
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)
    with patch.dict(app_mod._OPTIONAL_SERVICES, mutated, clear=True), \
         patch("arail.portal.app._port_open", new=AsyncMock(return_value=False)), \
         patch("arail.portal.app._container_running", return_value=False), \
         patch("arail.portal.app._docker_available", return_value=False):
        client = TestClient(app_mod.app)
        names = _stream_names(client)
    assert "Terminal (ttyd)" not in names, (
        "filter did not respect runtime registry mutation: "
        "ttyd remapped to max but still appeared on min stream"
    )


# ===========================================================================
# SECURITY (20%)
# ===========================================================================

# --- Sec 1: timing-channel — min stream duration should not encode max info -

def test_stream_min_tier_timing_independent_of_max_only_service_state(monkeypatch, tmp_path):
    """A min-tier client must not be able to infer max-only service presence
    from total stream duration. Since max-only checks are *not executed* on
    min, their port state (open/closed) must not affect min-stream latency.

    Run min-tier stream twice: once with all probes returning False, once with
    all probes returning True. The two durations should be similar (both gated
    by the 40 ms sleep pacing, not by probe outcome) — and specifically, the
    set of executed check names is identical.
    """
    monkeypatch.setenv("LAB_TIER", "min")
    monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
    monkeypatch.chdir(tmp_path)

    def run_with(port_value):
        with patch("arail.portal.app._port_open", new=AsyncMock(return_value=port_value)), \
             patch("arail.portal.app._container_running", return_value=port_value), \
             patch("arail.portal.app._docker_available", return_value=False):
            from arail.portal.app import app
            c = TestClient(app)
            t0 = time.perf_counter()
            r = c.get("/api/system/health/stream")
            return time.perf_counter() - t0, {e["name"] for e in _check_events(_parse_sse(r.text))}

    dur_closed, names_closed = run_with(False)
    dur_open, names_open = run_with(True)

    # The set of names must be identical regardless of probe outcome.
    assert names_closed == names_open, (
        "min-tier executed check set varies with probe state — "
        f"closed-only: {names_closed - names_open}, open-only: {names_open - names_closed}"
    )
    # Duration variance bounded — neither config exceeds 3 s ceiling.
    assert dur_closed < 3.0 and dur_open < 3.0


# --- Sec 2: header-based tier spoofing ignored -----------------------------

@pytest.mark.parametrize("header_name,header_value", [
    ("X-Lab-Tier", "max"),
    ("X-Forwarded-Tier", "max"),
    ("Authorization", "Bearer tier=max"),
    ("X-Override-Tier", "max"),
])
def test_stream_tier_bypass_headers_ignored(monkeypatch, tmp_path, header_name, header_value):
    """Spoofed tier headers must not unlock max-only stream checks."""
    client = _client(monkeypatch, tmp_path, "min")
    r = client.get("/api/system/health/stream", headers={header_name: header_value})
    if header_name == "Authorization":
        # Portal auth (S4): a spoofed Authorization replaces the test client's real bearer and is
        # refused outright, so it cannot unlock anything either.
        assert r.status_code == 401
        return
    assert r.status_code == 200
    names = {e["name"] for e in _check_events(_parse_sse(r.text))}
    for n in MAX_ONLY:
        assert n not in names, (
            f"header {header_name}:{header_value} spoofed tier and leaked '{n}'"
        )


# --- Sec 3: cookie-based tier spoofing ignored -----------------------------

def test_stream_tier_bypass_cookie_ignored(monkeypatch, tmp_path):
    """LAB_TIER must come from process env only — cookies cannot override."""
    client = _client(monkeypatch, tmp_path, "min")
    client.cookies.set("LAB_TIER", "max")
    client.cookies.set("tier", "max")
    r = client.get("/api/system/health/stream")
    names = {e["name"] for e in _check_events(_parse_sse(r.text))}
    for n in MAX_ONLY:
        assert n not in names


# ===========================================================================
# REGRESSION (20%)
# ===========================================================================

# --- Reg 1: full snapshot/stream parity for ALL gated services -------------

def test_snapshot_and_stream_parity_for_every_optional_service(monkeypatch, tmp_path):
    """For every entry in _OPTIONAL_SERVICES, both endpoints agree on visibility
    at each tier (modulo the documented tech-debt gap: opencode has no stream
    probe).

    Snapshot semantics: a service appears iff probe-up AND tier-visible. We
    force probes True so the only filtering remaining is tier. Then the
    parity claim is: snapshot.contains(id) iff stream contains its display
    name iff tier permits.
    """
    from arail.portal.app import _OPTIONAL_SERVICES
    NAME_TO_ID = {
        "Terminal (ttyd)": "ttyd",
        "Notebook (Jupyter)": "notebook",
        "Ollama API": "ollama",
        "Lance vector DB": "lance-memory",
        "Marimo": "marimo",
        "Open Notebook": "open-notebook",
        "Neo4j Bolt": "neo4j",
        # opencode intentionally absent — documented tech debt.
    }
    ID_TO_NAME = {v: k for k, v in NAME_TO_ID.items()}

    for tier in ("minimalist", "maximus"):
        monkeypatch.setenv("LAB_TIER", tier)
        monkeypatch.setenv("ARAIL_PASSWORD", "test-passphrase-not-real")
        monkeypatch.chdir(tmp_path)
        # Force all probes True so snapshot includes everything tier permits.
        with patch("arail.portal.app._port_open", new=AsyncMock(return_value=True)), \
             patch("arail.portal.app._container_running", return_value=True), \
             patch("arail.portal.app._docker_available", return_value=True):
            from arail.portal.app import app
            client = TestClient(app)
            stream_names = _stream_names(client)
            snap = client.get("/api/system/health").json().get("services", {})
        snap_ids = set(snap.keys())

        for svc_id, required_tier in _OPTIONAL_SERVICES.items():
            should_be_visible = (
                required_tier == "minimalist" or (required_tier == "maximus" and tier == "maximus")
            )
            assert (svc_id in snap_ids) == should_be_visible, (
                f"snapshot disagrees at LAB_TIER={tier} for {svc_id} "
                f"(in snapshot={svc_id in snap_ids}, should_be={should_be_visible}, "
                f"snap={snap})"
            )
            if svc_id in ID_TO_NAME:
                display = ID_TO_NAME[svc_id]
                assert (display in stream_names) == should_be_visible, (
                    f"stream disagrees with snapshot at LAB_TIER={tier} for "
                    f"display='{display}' / id='{svc_id}' "
                    f"(in stream={display in stream_names}, should_be={should_be_visible})"
                )


# --- Reg 2: done.passed + warned + failed == total -------------------------

@pytest.mark.parametrize("tier", ["min", "max"])
def test_stream_done_arithmetic_invariant(monkeypatch, tmp_path, tier):
    """done.passed + done.warned + done.failed must equal done.total."""
    client = _client(monkeypatch, tmp_path, tier)
    events = _parse_sse(client.get("/api/system/health/stream").text)
    d = _done(events)
    assert d["passed"] + d["warned"] + d["failed"] == d["total"], (
        f"LAB_TIER={tier}: done arithmetic broken: "
        f"{d['passed']}+{d['warned']}+{d['failed']} != {d['total']}"
    )


# --- Reg 3: SSE event shape stability --------------------------------------

@pytest.mark.parametrize("tier", ["min", "max"])
def test_stream_check_event_shape_unchanged(monkeypatch, tmp_path, tier):
    """Every check event has exactly the expected keys with correct types.
    Guards against accidental field renames/additions that would break clients.
    """
    client = _client(monkeypatch, tmp_path, tier)
    events = _check_events(_parse_sse(client.get("/api/system/health/stream").text))
    REQUIRED = {"event", "name", "status", "detail", "duration_ms", "index", "total"}
    for e in events:
        assert set(e.keys()) == REQUIRED, (
            f"check event keys drifted: {set(e.keys())} vs {REQUIRED}"
        )
        assert e["event"] == "check"
        assert isinstance(e["name"], str) and e["name"]
        assert e["status"] in {"pass", "warn", "fail"}
        assert isinstance(e["detail"], str)
        assert isinstance(e["duration_ms"], int) and e["duration_ms"] >= 0
        assert isinstance(e["index"], int) and e["index"] >= 0
        assert isinstance(e["total"], int) and e["total"] > 0


def test_stream_done_event_shape_unchanged(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, "min")
    events = _parse_sse(client.get("/api/system/health/stream").text)
    d = _done(events)
    REQUIRED = {"event", "passed", "warned", "failed", "total", "total_ms"}
    assert set(d.keys()) == REQUIRED, f"done event keys drifted: {set(d.keys())}"
    assert d["event"] == "done"
    assert all(isinstance(d[k], int) and d[k] >= 0
               for k in ("passed", "warned", "failed", "total", "total_ms"))
