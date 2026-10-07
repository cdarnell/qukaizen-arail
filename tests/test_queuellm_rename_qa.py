"""QA pass for the QueueLLM display rename + honest local cost sprint.

Sprint: sprints/2026-10-07-queuellm-display-rename/. These tests cover what
the builder's suites did not: rendered pages across every parameter-free GET
route, labs that already existed before the upgrade, error paths reached through
the chat endpoints, cost fields on the runtime and router branches of
both chat paths, legacy persisted state, and env-var compatibility.

All state goes to tmp_path. Nothing reads or writes the real ~/.arail or a
live lab.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from arail.router.backends import ModelResponse

ROOT = Path(__file__).resolve().parents[1]
BASE = "5f775f1c"
AERO = re.compile(r"aero\s*llm", re.I)


def _visible_hits(text: str) -> list[str]:
    """User-visible old-name fragments in rendered output.

    Removes comments, ids, selectors, env var names and migration notes,
    which are frozen or invisible (ARCHITECTURE.md rules 1-3, 5). Returns
    what is left.
    """
    body = re.sub(r"<!--.*?-->|\{#.*?#\}", "", text, flags=re.S)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"(?m)(^|[^:'\"])//[^\n]*$", r"\1", body)
    body = re.sub(r"['\"`][a-z0-9_:.\-/]*aerollm[a-z0-9_:.\-/]*['\"`]", "", body)
    body = re.sub(r"[#.\w/-]*aerollm[\w-]*", "", body)
    body = re.sub(r"[A-Z0-9_]*AERO(?:LLM)?_[A-Z0-9_]*", "", body)
    body = re.sub(r"[A-Z0-9_]*_AEROLLM\b", "", body)   # ARAIL_FORCE_AEROLLM
    body = re.sub(r"AeroLLMBackend", "", body)
    body = re.sub(r"[Ff]ormerly AeroLLM|AeroLLM was renamed", "", body)
    return [m.group(0) for m in re.finditer(r".{0,50}aero\s*llm.{0,50}", body, re.I | re.S)]


def _base_text(path: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{BASE}:{path}"],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture
def fresh_pkb(monkeypatch, tmp_path):
    """A brand-new PKB root, so seeded content comes from this build."""
    pkb = tmp_path / "pkb"
    from arail import config
    import arail.skills_loader as sl

    monkeypatch.setattr(config, "PKB_ROOT", pkb, raising=False)
    monkeypatch.setattr(config, "PKM_ROOT", pkb, raising=False)
    monkeypatch.setattr(sl, "_pkb_root", lambda: pkb)
    return pkb


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    from arail import costs as costs_mod
    import arail.config as config_mod

    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(costs_mod, "DATA_DIR", tmp_path, raising=False)
    costs_mod.CostTracker._instance = None
    fresh = costs_mod.CostTracker()
    monkeypatch.setattr(costs_mod, "cost_tracker", fresh)
    yield fresh
    costs_mod.CostTracker._instance = None


# ═════════════════════════════════════════════════════════════════════════
# 1. Rendered portal surfaces
# ═════════════════════════════════════════════════════════════════════════
_SKIP_ROUTE_PARTS = ("stream", "events", "sse", "openapi", "redoc", "watch", "tail")
# Routes that render repo markdown (docs/, BLUEPRINTS.md, AGENTS.md). The
# builder left those docs out of scope and the reviewer accepted that as a
# follow-up. They are covered by the repo-docs test below, not this one.
_REPO_DOC_ROUTES = {"/docs", "/docs/INDEX.md", "/docs/design.md", "/design",
                    "/blueprints-overview", "/blueprints-guide",
                    "/porting-manifest"}
# Routes that echo seeded research-program files from lab/pkb/research
# (F9: accepted, existing labs keep their possibly-edited files).
_SEEDED_RESEARCH_ROUTES = {"/api/lab/brief", "/api/pkb/review"}


def test_every_parameter_free_get_route_renders_no_user_visible_aerollm(
        monkeypatch, fresh_pkb):
    """Hit every parameter-free GET route on a maximus lab with a fresh PKB.
    Covers pages, partials and JSON that the UI renders."""
    monkeypatch.setenv("LAB_TIER", "maximus")
    from arail import skill_seed
    from fastapi.testclient import TestClient
    from arail.portal import app as appmod

    skill_seed.ensure_starter_skills(pkb_root=fresh_pkb)
    client = TestClient(appmod.app, raise_server_exceptions=False)
    hits, hit_routes = [], 0
    for route in appmod.app.routes:
        path = getattr(route, "path", "")
        if "GET" not in getattr(route, "methods", set()) or "{" in path:
            continue
        if any(p in path for p in _SKIP_ROUTE_PARTS):
            continue
        if path in _REPO_DOC_ROUTES or path in _SEEDED_RESEARCH_ROUTES:
            continue
        try:
            resp = client.get(path, timeout=5)
        except Exception:  # noqa: BLE001 - a route that hangs is not this test's concern
            continue
        hit_routes += 1
        for frag in _visible_hits(resp.text):
            hits.append(f"{path}: {frag!r}")
    assert hit_routes >= 50, f"route sweep reached only {hit_routes} routes"
    assert not hits, "\n".join(hits)


def test_portal_rendered_repo_docs_have_no_user_visible_aerollm(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    from fastapi.testclient import TestClient
    from arail.portal import app as appmod

    client = TestClient(appmod.app, raise_server_exceptions=False)
    paths = sorted(_REPO_DOC_ROUTES)
    paths += ["/docs/" + str(p.relative_to(ROOT / "docs"))
              for p in (ROOT / "docs").rglob("*.md") if "archive" not in p.parts]
    hits = []
    for path in paths:
        text = re.sub(r"<[^>]+>", " ", client.get(path).text)
        hits += [f"{path}: {h!r}" for h in _visible_hits(text)]
    assert not hits, f"{len(hits)} hits, e.g.\n" + "\n".join(hits[:15])


def test_existing_lab_installed_skill_pack_does_not_keep_aerollm(
        monkeypatch, fresh_pkb):
    """An existing lab installed the skill packs before the rename. On
    upgrade, an unedited installed SKILL.md must not keep "AeroLLM". It is
    shown in the Skills tab and Forge picker and is injected into the chat
    system prompt, so the deep model can repeat the old name in answers.

    This is the registry-note problem (F7) again, in a place the build did not
    cover. F9 accepted stale seeded *research* files. It did not cover skill
    packs.
    """
    monkeypatch.setenv("LAB_TIER", "maximus")
    rel = "src/arail/skill_packs/model-building/optimize-aerollm/SKILL.md"
    legacy = _base_text(rel)
    if legacy is None:
        pytest.skip("base commit not available")
    assert "Optimize AeroLLM" in legacy  # the fixture is really pre-rename

    from arail import skill_seed
    from arail.skills_loader import _skills_dir

    # The lab as it was before the upgrade: same seeding, old shipped text.
    skill_seed.ensure_starter_skills(pkb_root=fresh_pkb)
    installed = _skills_dir(fresh_pkb) / "optimize-aerollm" / "SKILL.md"
    assert installed.exists(), "pack layout changed; update this test"
    installed.write_text(legacy, encoding="utf-8")

    # Upgrade: the new build boots against the existing lab.
    skill_seed.ensure_starter_skills(pkb_root=fresh_pkb)

    from fastapi.testclient import TestClient
    from arail.portal import app as appmod
    client = TestClient(appmod.app, raise_server_exceptions=False)
    hits = _visible_hits(client.get("/api/skills/list").text)
    hits += _visible_hits(client.get("/api/chat/system-prompt").text)
    assert not hits, "\n".join(hits[:10])


def test_user_edited_installed_skill_survives_upgrade(monkeypatch, fresh_pkb):
    """The opposite case: a hand-edited skill must never be overwritten."""
    from arail import skill_seed
    from arail.skills_loader import _skills_dir

    skill_seed.ensure_starter_skills(pkb_root=fresh_pkb)
    installed = _skills_dir(fresh_pkb) / "optimize-aerollm" / "SKILL.md"
    edited = installed.read_text(encoding="utf-8") + "\n\nMy own AeroLLM notes.\n"
    installed.write_text(edited, encoding="utf-8")
    skill_seed.ensure_starter_skills(pkb_root=fresh_pkb)
    assert installed.read_text(encoding="utf-8") == edited


def test_benchmark_tool_messages_say_queuellm():
    """ARCHITECTURE.md inventory rows lab/tools/benchmark_models.py:340 and
    :362. `arailctl benchmark` prints them. The guard does not scan lab/tools/,
    so nothing caught the miss."""
    from tests.test_no_user_visible_aerollm import scan_text

    path = ROOT / "lab" / "tools" / "benchmark_models.py"
    hits = scan_text("py", path.read_text(encoding="utf-8"))
    assert not hits, [f"lab/tools/benchmark_models.py:{n}: {t}" for n, t in hits]


# ═════════════════════════════════════════════════════════════════════════
# 2. arailctl / script terminal output
# ═════════════════════════════════════════════════════════════════════════
def _run(cmd, tmp_path, **env):
    full = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
            "TERM": "dumb", **env}
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          env=full, timeout=60)


def test_arailctl_help_mentions_old_name_only_as_the_cli_alias(tmp_path):
    out = _run(["bash", str(ROOT / "arailctl"), "help"], tmp_path)
    assert out.returncode == 0, out.stderr[-500:]
    text = out.stdout + out.stderr
    lines = [ln for ln in text.splitlines() if AERO.search(ln)]
    assert lines and all("alias: benchmark, aerollm" in ln for ln in lines), lines
    assert re.search(r"deep <op>\s+QueueLLM 2nd inference", text)


def test_deep_status_output_says_queuellm(tmp_path):
    py = shutil.which("python3")
    if py is None:
        pytest.skip("no python3")
    out = _run(["bash", str(ROOT / "scripts" / "build-aerollm.sh"), "status"],
               tmp_path, PYTHON=py)
    text = out.stdout + out.stderr
    assert "QueueLLM (2nd inference) status" in text
    # `aerollm_api:` is the module name (frozen); `crates/aerollm-api` is a path.
    shown = re.sub(r"aerollm_api|crates/aerollm-api|AEROLLM_\w+", "", text)
    assert not AERO.search(shown), shown


# ═════════════════════════════════════════════════════════════════════════
# 3. Error paths
# ═════════════════════════════════════════════════════════════════════════
def test_not_ready_message_uses_new_name_and_quotes_frozen_env_var(monkeypatch):
    from arail.portal import app as appmod

    monkeypatch.setenv("AEROLLM_MODEL", "Qwen3-30B-A3B-4bit")
    out = appmod._optional_backend_error_result(
        "aerollm", RuntimeError("QueueLLM model dir not found: /x"))
    assert out["reply"].startswith("QueueLLM isn't ready on this lab.")
    assert "AEROLLM_MODEL" in out["reply"]          # the var arail actually reads
    assert "QUEUELLM_MODEL" not in out["reply"]
    assert out["backend"] == "aerollm"               # id frozen
    assert not _visible_hits(out["reply"])


def test_not_ready_reaches_the_stream_and_non_stream_endpoints(monkeypatch, tracker):
    from fastapi.testclient import TestClient
    from arail.portal import app as appmod

    err = appmod._optional_backend_error_result(
        "aerollm", RuntimeError("QueueLLM model dir not found: lab/models/x"))
    monkeypatch.setattr(appmod, "_prepare_chat_context",
                        lambda *a, **k: {"error_result": err})
    monkeypatch.setattr(appmod, "_restore_chat_context", lambda c: None,
                        raising=False)
    client = TestClient(appmod.app, raise_server_exceptions=False)
    r = client.post("/api/chat/stream", json={"message": "hi"})
    events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    final = [e for e in events if e.get("type") == "final"][0]
    assert final["reply"].startswith("QueueLLM isn't ready")
    assert not _visible_hits(json.dumps(events))

    out = asyncio.run(appmod._run_chat_completion(
        message="hi", history=[], backend_override="aerollm",
        model_override=None, temperature=0.7, top_p=None, max_tokens=8))
    assert out["reply"].startswith("QueueLLM isn't ready")


def test_missing_model_dir_honours_aerollm_model_and_ignores_queuellm_model(
        monkeypatch, tmp_path):
    """AEROLLM_MODEL is the frozen var arail reads. Now that every label says
    QueueLLM, a user may guess QUEUELLM_MODEL. That var is not read, and the
    error must name the var that is."""
    import sys
    import types

    from arail.router import backends

    stub = types.ModuleType("aerollm_api")
    stub.Runtime = object
    stub.__version__ = "stub"
    monkeypatch.setitem(sys.modules, "aerollm_api", stub)
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AEROLLM_MODEL", "Honoured-Model-4bit")
    monkeypatch.setenv("QUEUELLM_MODEL", "Ignored-Model-4bit")

    monkeypatch.setattr(backends.AeroLLMBackend, "_shared", {})
    with pytest.raises(RuntimeError) as ei:
        backends.AeroLLMBackend()
    msg = str(ei.value)
    assert msg.startswith("QueueLLM model dir not found:")
    assert "Honoured-Model-4bit" in msg and "Ignored-Model-4bit" not in msg
    assert "Set AEROLLM_MODEL" in msg
    assert not _visible_hits(msg)


@pytest.mark.parametrize("role,params", [("secondary", 2000.0), ("primary", 400.0)])
def test_ceiling_rejection_messages_say_queuellm(monkeypatch, role, params):
    """The oversize-model rejection (the closest arail has to "dense model
    rejected") names the deep engine."""
    from arail.registry import ceiling

    monkeypatch.setattr(ceiling._model_specs, "resolve_params_b",
                        lambda mid, path=None: (params, "override"))
    with pytest.raises(ceiling.ModelCeilingViolation) as ei:
        ceiling.resolve_answering_model("Some-Dense-405B", role=role,
                                        backend="aerollm")
    msg = str(ei.value)
    assert "QueueLLM" in msg
    assert not _visible_hits(msg)


# ═════════════════════════════════════════════════════════════════════════
# 4. Cost fields across backends and both chat paths
# ═════════════════════════════════════════════════════════════════════════
class _Stub:
    def __init__(self, backend, model):
        self.backend_name, self.model_name = backend, model
        self._b = backend

    def complete(self, prompt, max_tokens, temperature, top_p, **kw):
        return ModelResponse(text="ok", model=self.model_name, tokens_used=20,
                             backend=self._b, latency_ms=500.0)


class _StubRouter:
    """Mimics router.core: tracks the call itself, then yields the response."""

    def __init__(self, backend, model):
        self.backend_name, self.model = backend, model

    def stream_complete(self, prompt, **kw):
        from arail import costs
        yield "o"
        yield "k"
        costs.cost_tracker.track(self.backend_name, self.model, 400, 20, 500.0, "ui")
        yield ModelResponse(text="ok", model=self.model, tokens_used=20,
                            backend=self.backend_name, latency_ms=500.0)

    def complete(self, prompt, **kw):
        from arail import costs
        costs.cost_tracker.track(self.backend_name, self.model, 400, 20, 500.0, "ui")
        return ModelResponse(text="ok", model=self.model, tokens_used=20,
                             backend=self.backend_name, latency_ms=500.0)


def _ctx(*, runtime=None, router=None):
    def make(*_a, **_k):
        return {"wants_deep": False, "optional_backend_name": None,
                "deep_backend": None, "runtime_backend": runtime,
                "router": router, "active_backend": runtime or router,
                "prompt": "x" * 1600, "sources": [], "model_provenance": None,
                "frozen_system": None, "claude_messages": None,
                "error_result": None}
    return make


def _final_stream(appmod):
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app, raise_server_exceptions=False)
    r = client.post("/api/chat/stream", json={"message": "hi"})
    events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    finals = [e for e in events if e.get("type") == "final"]
    assert len(finals) == 1, r.text[:400]
    return finals[0]


def _non_stream(appmod):
    return asyncio.run(appmod._run_chat_completion(
        message="hi", history=[], backend_override=None, model_override=None,
        temperature=0.7, top_p=None, max_tokens=8))


def _assert_cost_contract(out):
    for key in ("cloud_cost_usd", "cloud_cost_source", "cloud_equivalent_usd",
                "cloud_equivalent_source", "energy_cost_usd", "energy_source"):
        assert key in out, key
    if out["cloud_cost_usd"] is not None:
        assert out["cloud_cost_source"] == "billed_estimate"
    for usd, src in (("cloud_equivalent_usd", "cloud_equivalent_source"),
                     ("energy_cost_usd", "energy_source")):
        if out[usd] is not None:
            assert out[src], (usd, out)


@pytest.mark.parametrize("path", ["stream", "non_stream"])
@pytest.mark.parametrize("backend,want_source,want_null", [
    ("ollama_native", "local", True),
    ("openai_compat", "unpriced", True),
    ("claude", "billed_estimate", False),
])
def test_runtime_branch_cost(monkeypatch, tracker, path, backend, want_source, want_null):
    from arail.portal import app as appmod

    monkeypatch.setattr(appmod, "_prepare_chat_context",
                        _ctx(runtime=_Stub(backend, "m-1")))
    monkeypatch.setattr(appmod, "_restore_chat_context", lambda c: None, raising=False)
    out = _final_stream(appmod) if path == "stream" else _non_stream(appmod)
    _assert_cost_contract(out)
    assert out["cloud_cost_source"] == want_source
    assert (out["cloud_cost_usd"] is None) is want_null


@pytest.mark.parametrize("path", ["stream", "non_stream"])
@pytest.mark.parametrize("backend,want_source,want_null", [
    ("mlx", "local", True),
    ("aerollm", "local", True),
    ("openrouter", "billed_estimate", False),
])
def test_router_branch_cost_attributes_its_own_record(
        monkeypatch, tracker, path, backend, want_source, want_null):
    from arail.portal import app as appmod

    monkeypatch.setattr(appmod, "_prepare_chat_context",
                        _ctx(router=_StubRouter(backend, "m-2")))
    monkeypatch.setattr(appmod, "_restore_chat_context", lambda c: None, raising=False)
    out = _final_stream(appmod) if path == "stream" else _non_stream(appmod)
    _assert_cost_contract(out)
    assert out["cloud_cost_source"] == want_source
    assert (out["cloud_cost_usd"] is None) is want_null
    if not want_null:
        assert out["cloud_cost_usd"] == tracker.get_last_record()["cloud_cost_usd"]


def test_router_branch_local_never_reports_charge_when_unattributed(monkeypatch, tracker):
    """Another call's cloud record is last in the tracker: the local turn must not
    borrow it, and must still say local."""
    from arail.portal import app as appmod

    class _NoTrackRouter(_StubRouter):
        def stream_complete(self, prompt, **kw):
            yield "x"
            yield ModelResponse(text="x", model="m-3", tokens_used=1,
                                backend="aerollm", latency_ms=1.0)

    tracker.track("claude", "claude-sonnet", 10_000, 1000, 10.0, "agent")
    monkeypatch.setattr(appmod, "_prepare_chat_context",
                        _ctx(router=_NoTrackRouter("aerollm", "m-3")))
    monkeypatch.setattr(appmod, "_restore_chat_context", lambda c: None, raising=False)
    out = _final_stream(appmod)
    assert out["cloud_cost_usd"] is None
    assert out["cloud_cost_source"] == "local"
    assert out["cloud_equivalent_usd"] is None and out["energy_cost_usd"] is None


@pytest.mark.parametrize("variant", ["AeroLLM", "AEROLLM", " aerollm", "aerollm ",
                                     "Claude", "queuellm", "aerollm\x00"])
def test_cost_source_is_exact_match_and_unknown_spellings_are_unpriced(variant):
    from arail.costs import cost_source
    assert cost_source(variant) == "unpriced"


def test_zero_token_cloud_call_still_reports_a_sourced_number(tracker):
    from arail.portal import app as appmod
    rec = tracker.track("claude", "claude-sonnet", 0, 0, 1.0, "ui")
    out = appmod._resolve_chat_cost(
        ModelResponse(text="", model="claude-sonnet", tokens_used=0,
                      backend="claude", latency_ms=1.0), rec)
    assert out["cloud_cost_source"] == "billed_estimate"
    assert isinstance(out["cloud_cost_usd"], float) and out["cloud_cost_usd"] >= 0


def test_legacy_costs_json_from_older_build_loads_and_is_regated(monkeypatch, tmp_path):
    """A costs.json written before this sprint: aerollm history carries the
    fabricated cloud_cost_usd and no source fields. Load it as a fresh boot."""
    from arail import costs as costs_mod
    import arail.config as config_mod

    (tmp_path / "costs.json").write_text(json.dumps({
        "total_calls": 2, "total_cloud_usd": 0.02,
        "history": [
            {"ts": 1.0, "backend": "claude", "model": "claude-sonnet",
             "cloud_cost_usd": 0.0123, "cloud_usd": 0.0123, "energy_usd": 0.0},
            {"ts": 2.0, "backend": "aerollm", "model": "Qwen3-30B-A3B",
             "cloud_cost_usd": 0.008267, "cloud_usd": 0.008267,
             "energy_usd": 0.0004},
        ],
    }))
    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(costs_mod, "DATA_DIR", tmp_path, raising=False)
    costs_mod.CostTracker._instance = None
    t = costs_mod.CostTracker()
    monkeypatch.setattr(costs_mod, "cost_tracker", t)
    try:
        from fastapi.testclient import TestClient
        from arail.portal import app as appmod
        body = TestClient(appmod.app).get("/api/system/costs").json()["last_record"]
        assert body["cloud_cost_usd"] is None
        assert body["cloud_cost_source"] == "local"

        # The router fallback must also re-gate a legacy record it matches.
        out = appmod._resolve_chat_cost(ModelResponse(
            text="", model="Qwen3-30B-A3B", tokens_used=1, backend="aerollm",
            latency_ms=1.0))
        assert out["cloud_cost_usd"] is None and out["cloud_cost_source"] == "local"

        # A legacy cloud record keeps its figure and gains a source.
        t._history.append(t._history.pop(0))
        out = appmod._resolve_chat_cost(ModelResponse(
            text="", model="claude-sonnet", tokens_used=1, backend="claude",
            latency_ms=1.0))
        assert out["cloud_cost_usd"] == 0.0123
        assert out["cloud_cost_source"] == "billed_estimate"
    finally:
        costs_mod.CostTracker._instance = None


def test_cost_fields_carry_no_prompt_text(monkeypatch, tracker):
    """Metadata only: no prompt content leaks into the cost fields or history."""
    from arail.portal import app as appmod

    secret = "CANARY-7f3a-private-prompt-text"

    class _S(_Stub):
        pass

    def make(*_a, **_k):
        c = _ctx(runtime=_S("ollama_native", "m"))()
        c["prompt"] = secret * 10
        return c

    monkeypatch.setattr(appmod, "_prepare_chat_context", make)
    monkeypatch.setattr(appmod, "_restore_chat_context", lambda c: None, raising=False)
    out = _final_stream(appmod)
    cost_keys = {k: v for k, v in out.items() if "cost" in k or "source" in k
                 or k.endswith("_usd")}
    assert secret not in json.dumps(cost_keys)
    assert secret not in json.dumps(tracker.get_last_record())
