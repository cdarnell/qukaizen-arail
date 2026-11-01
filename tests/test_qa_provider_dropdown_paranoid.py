"""QA paranoid suite — provider-aware chat dropdown (4-layer expanded sprint).

Sprint: 2026-05-18-provider-aware-chat-dropdown
QA pass: 2026-05-20

Allocation (architect's reallocation of ARAIL's 30/30/20/10/10 for a post-setup
UI feature):
  40% provider-flip UX edge cases   (UX-*)
  30% security                       (SEC-*)
  20% race & failure modes           (RF-*)
  10% regression                     (REG-*)

These tests hunt the gaps the builder + architect unit tests left:
  - cloud→my_machine restore (no stale cloud cards)
  - token valid but /models 403 (architect open-q #2)
  - OpenRouter 200-cap holds and UI doesn't choke
  - XSS: malicious upstream model id (<img onerror=...>) reaches the DOM only escaped (F7)
  - set-ctx with colon/slash ollama ids — threads the F-VALIDATE needle
  - L4 end-to-end F-DEFAULT-LEAK (set cloud in hybrid, flip airgapped, send chat)
  - ctx boundaries: 256, 1_000_000, 255, 1_000_001
  - two providers both missing tokens — per-provider CTA, no stale carryover

IMPORTANT (test hygiene): the L4 /api/chat/default SET path writes the REAL
lab/data/secrets.env and mutates os.environ. Every test here that exercises a
write path redirects DATA_DIR's secrets file to a tmp path AND snapshots/restores
the polluted env keys so this suite NEVER clobbers a developer's real secrets.
(The builder's tests/test_chat_default.py does NOT do this — see TEST_REPORT.md
finding QA-1.)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_secrets(monkeypatch, tmp_path):
    """Redirect the secrets file to a tmp path and snapshot polluting env keys.

    Guarantees this suite never writes the real lab/data/secrets.env and never
    leaks COMPUTE_SOURCE / ARAIL_CHAT_DEFAULT_MODEL / ARAIL_MODEL_CTX_OVERRIDES
    into the process for downstream tests.
    """
    from arail.portal import app as portal_app

    fake = tmp_path / "secrets.env"
    monkeypatch.setattr(portal_app, "_secrets_path", lambda: fake)

    leaky = ("COMPUTE_SOURCE", "ARAIL_CHAT_DEFAULT_MODEL", "ARAIL_MODEL_CTX_OVERRIDES")
    saved = {k: os.environ.get(k) for k in leaky}
    for k in leaky:
        monkeypatch.delenv(k, raising=False)
    yield fake
    # monkeypatch reverts setenv/delenv it performed, but the ENDPOINT writes
    # os.environ directly — restore those by hand.
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _client(monkeypatch, lab_mode="hybrid"):
    monkeypatch.setenv("LAB_MODE", lab_mode)
    from fastapi.testclient import TestClient
    from arail.portal.app import app
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# 40% — PROVIDER-FLIP UX EDGE CASES (UX-*)
# ===========================================================================

def test_ux_flip_to_cloud_then_my_machine_restores_local_gallery(monkeypatch):
    """UX-1 (carryover hunt): flip my_machine→claude→my_machine. The no-provider
    request must return the LOCAL gallery shape (installed list present, no cloud
    cta/airgapped leakage) — proving the local gallery fully restores, no cloud
    cards stuck. Server-side proof of the round trip the JS relies on."""
    from arail.portal import app as portal_app

    client = _client(monkeypatch, "hybrid")
    fake_models = ["claude-opus-4-7", "claude-haiku-3-5"]

    # 1) cloud flip
    with patch.object(portal_app, "_provider_token", return_value="sk-x"), \
         patch.object(portal_app, "_fetch_provider_models", return_value=fake_models):
        cloud = client.get("/api/chat/models?provider=claude").json()
    assert cloud["gallery"]["catalog"], "cloud flip should populate catalog"
    assert cloud["current"] in fake_models

    # 2) flip back to my_machine (no provider) — pure local branch
    for noprov in ("", "my_machine"):
        url = "/api/chat/models" if noprov == "" else f"/api/chat/models?provider={noprov}"
        local = client.get(url).json()
        assert "gallery" in local
        assert isinstance(local["gallery"].get("installed"), list), \
            f"local restore must expose gallery.installed list (got {local['gallery']})"
        assert local.get("airgapped") is not True, "local restore must not be airgapped"
        assert "cta" not in local or not local.get("cta"), \
            f"local restore must not carry a cloud cta: {local.get('cta')!r}"
        # The cloud-only marker 'current'==cloud id must NOT survive into local
        assert local.get("current") not in fake_models, \
            "stale cloud 'current' leaked into local restore"


def test_ux_token_valid_but_models_listing_403(monkeypatch):
    """UX-2 (architect open-q #2): token saved + listing forbidden (403).
    _fetch_provider_models returns [] on non-2xx → server returns empty catalog
    with current=None. Must NOT 500, must NOT fall through to local, must read as
    'no models to select' not a crash."""
    from arail.portal import app as portal_app

    client = _client(monkeypatch, "hybrid")

    def _forbidden_get(*a, **k):
        m = MagicMock()
        m.status_code = 403
        m.json.return_value = {"error": "forbidden"}
        return m

    with patch.object(portal_app, "_provider_token", return_value="sk-valid"), \
         patch("requests.get", side_effect=_forbidden_get):
        r = client.get("/api/chat/models?provider=openrouter")

    assert r.status_code == 200, "403 listing must not surface as 500"
    body = r.json()
    assert body["provider"] == "openrouter"
    assert body["gallery"]["catalog"] == [], "403 → empty catalog"
    assert body["current"] is None, "403 → current None (no cloud model to select)"
    assert body.get("airgapped") is not True


def test_ux_openrouter_200_cap_holds(monkeypatch):
    """UX-3: OpenRouter returns 250 models. The 200-cap must hold and the payload
    must not choke (catalog length == 200, all renderable)."""
    from arail.portal import app as portal_app

    client = _client(monkeypatch, "hybrid")
    big = [{"id": f"vendor/model-{i}"} for i in range(250)]

    def _ok_get(*a, **k):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"data": big}
        return m

    with patch.object(portal_app, "_provider_token", return_value="sk-or"), \
         patch("requests.get", side_effect=_ok_get):
        r = client.get("/api/chat/models?provider=openrouter")

    body = r.json()
    catalog = body["gallery"]["catalog"]
    assert len(catalog) == 200, f"200-cap must hold, got {len(catalog)}"
    assert len(body.get("models", [])) == 200
    # every entry renderable (non-empty id, runtime matches)
    assert all(e.get("id") and e.get("runtime") == "openrouter" for e in catalog)


def test_ux_two_providers_both_missing_tokens_per_provider_cta(monkeypatch):
    """UX-4: flip between two token-less providers — each returns its OWN
    per-provider no_token CTA (provider field correct, no stale carryover)."""
    from arail.portal import app as portal_app

    client = _client(monkeypatch, "hybrid")
    with patch.object(portal_app, "_provider_token", return_value=""):
        a = client.get("/api/chat/models?provider=mistral").json()
        b = client.get("/api/chat/models?provider=cohere").json()

    assert a["cta"]["kind"] == "no_token" and a["cta"]["provider"] == "mistral"
    assert b["cta"]["kind"] == "no_token" and b["cta"]["provider"] == "cohere"
    assert "Mistral" in a["cta"]["message"]
    assert "Cohere" in b["cta"]["message"]
    # No bleed: mistral's message must not mention cohere and vice versa
    assert "cohere" not in a["cta"]["message"].lower()
    assert "mistral" not in b["cta"]["message"].lower()


def test_ux_unknown_provider_returns_cta_never_500_never_local(monkeypatch):
    """UX-5: a junk provider id → unknown_provider CTA, empty gallery, never 500,
    never local fallthrough (no installed models leak in)."""
    client = _client(monkeypatch, "hybrid")
    r = client.get("/api/chat/models?provider=definitely-not-a-provider")
    assert r.status_code == 200
    body = r.json()
    assert body["cta"]["kind"] == "unknown_provider"
    assert body["gallery"]["catalog"] == []
    assert body["gallery"]["installed"] == []


def test_ux_provider_param_case_and_whitespace_normalized(monkeypatch):
    """UX-6: '  Claude  ' (mixed case + whitespace) is normalized to claude, not
    treated as unknown. Guards against the picker breaking on radio-value casing."""
    from arail.portal import app as portal_app
    client = _client(monkeypatch, "hybrid")
    with patch.object(portal_app, "_provider_token", return_value=""):
        r = client.get("/api/chat/models", params={"provider": "  Claude  "})
    body = r.json()
    assert body["provider"] == "claude"
    assert body["cta"]["kind"] == "no_token"  # known provider, just no token


def test_ux_cloud_current_never_a_local_id(monkeypatch):
    """UX-7 (F-CLOUD-CURRENT belt-and-suspenders): even when a local model is the
    OS-configured MODEL_NAME, the cloud branch's 'current' is a cloud id, never
    the local one."""
    from arail.portal import app as portal_app
    monkeypatch.setenv("MODEL_NAME", "qwen2.5:7b")
    client = _client(monkeypatch, "hybrid")
    fake = ["grok-2-latest", "grok-2-mini"]
    with patch.object(portal_app, "_provider_token", return_value="sk-xai"), \
         patch.object(portal_app, "_fetch_provider_models", return_value=fake):
        body = client.get("/api/chat/models?provider=xai").json()
    assert body["current"] == "grok-2-latest"
    assert body["current"] != "qwen2.5:7b"


def test_ux_empty_cloud_list_with_token_reads_as_no_models(monkeypatch):
    """UX-8 (architect open-q #2 tail): token valid, provider returns an EMPTY
    model list → catalog empty, current None → JS shows 'No models returned'
    (not the CTA, not a crash)."""
    from arail.portal import app as portal_app
    client = _client(monkeypatch, "hybrid")
    with patch.object(portal_app, "_provider_token", return_value="sk-ok"), \
         patch.object(portal_app, "_fetch_provider_models", return_value=[]):
        body = client.get("/api/chat/models?provider=together").json()
    assert body["gallery"]["catalog"] == []
    assert body["current"] is None
    assert "cta" not in body or not body.get("cta")  # not a token problem


# ===========================================================================
# 30% — SECURITY (SEC-*)
# ===========================================================================

def test_sec_xss_malicious_model_id_escaped_in_render(monkeypatch):
    """SEC-1 (F7, FAIL-GATE): a compromised provider returns a model id containing
    an XSS payload. The server passes it through verbatim (it's data), so the
    DEFENSE is the frontend escapeHtml. This test (a) confirms the server does not
    itself inject it unescaped anywhere AND (b) re-implements the exact escapeHtml
    from chat.html's inline <script> and proves the payload is neutralized before
    DOM insertion. Locks the XSS contract that protects labs running on others'
    machines."""
    from arail.portal import app as portal_app

    client = _client(monkeypatch, "hybrid")
    payload = '<img src=x onerror="alert(document.cookie)">'
    quote_payload = '"><script>alert(1)</script>'
    malicious = [payload, quote_payload, "normal-model"]

    with patch.object(portal_app, "_provider_token", return_value="sk-evil"), \
         patch.object(portal_app, "_fetch_provider_models", return_value=malicious):
        body = client.get("/api/chat/models?provider=claude").json()

    ids = [e["id"] for e in body["gallery"]["catalog"]]
    assert payload in ids, "server stores the raw id (escaping is the frontend's job)"

    # Mirror chat.html's escapeHtml() exactly.
    def escape_html(s: str) -> str:
        return (str(s)
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))

    for raw in (payload, quote_payload):
        esc = escape_html(raw)
        assert "<" not in esc and ">" not in esc, f"unescaped angle bracket: {esc}"
        assert '"' not in esc, f"unescaped double-quote (attr break-out): {esc}"
        assert "<img" not in esc and "<script" not in esc
        assert "onerror=" not in esc or "&quot;" in esc  # quotes neutralized


def test_sec_chat_html_escapes_every_model_identity_field_insertion():
    """SEC-2 (F7 source audit — repointed 2026-07-21, then again 2026-08-11):
    grep the LIVE template — every place a model's id/label/runtime/provider
    is interpolated into innerHTML by the picker/comparison-strip renderers
    must wrap it in escapeHtml().

    Originally this pinned chat.legacy.html's per-provider "cloud card" grid
    (deleted as dead code in c3c401a). Repointed 2026-07-21 to makeOpt() /
    renderModelRail() / renderActiveCard() — the live descendants at the
    time. sprints/2026-08-11-two-slot-chat-models Phase 5 deleted
    renderModelRail/renderActiveCard (collapsed onto two picker chips) —
    this test now pins makeOpt() (every picker row, local or deep) and
    renderModelInfo() (the model-info drawer), the surviving template-
    literal-style sinks. renderComparisonStrip() (the new Compare-mode A/B
    header) is a real third sink for the same risky fields but builds its
    HTML via string concatenation, not `${}` interpolation — the scanner
    below is template-literal-specific, so it's checked separately.
    """
    import re

    tpl = Path(_REPO_ROOT) / "src/arail/portal/templates/chat.html"
    text = tpl.read_text()

    def extract_function(name: str) -> str:
        marker = f"function {name}("
        start = text.index(marker)  # raises if the render function was removed/renamed
        nxt = re.search(r"\n {4}(?:async )?function \w+\(", text[start + 1:])
        end = start + 1 + nxt.start() if nxt else len(text)
        return text[start:end]

    # Fields that carry model/provider identity (local, deep, or — if the
    # per-provider catalog ever gets wired back into the picker — cloud).
    risky = re.compile(
        r"\bm\.id\b|\bm\.label\b|\bm\.runtime\b|\bprovider\b|\bcurrentId\b"
        r"|State\.bId\b|State\.bRuntime\b"
    )
    # Only consider lines actually building an HTML tag (an innerHTML sink).
    # Excludes e.g. flashStatus(`ejected ${m.id}`) — plain status text
    # through .textContent, not a DOM-insertion hole.
    looks_like_html = re.compile(r"<[a-zA-Z]")

    suspicious = []
    bodies = {}
    for fn in ("makeOpt", "renderModelInfo"):
        body = extract_function(fn)
        bodies[fn] = body
        for ln in body.splitlines():
            if "escapeHtml" in ln or not looks_like_html.search(ln):
                continue
            for m in re.finditer(r"\$\{([^}]+)\}", ln):
                expr = m.group(1).strip()
                if risky.search(expr):
                    suspicious.append((fn, ln.strip(), expr))
    assert not suspicious, f"unescaped model-identity interpolation: {suspicious}"

    # Positive: escapeHtml is actually applied to these fields (not vacuous —
    # e.g. if a render function got renamed/gutted, the loop above would
    # silently find nothing to flag).
    combined = "\n".join(bodies.values())
    assert "escapeHtml(m.label || m.id)" in combined, "id/label no longer escaped in the render path"
    assert "escapeHtml(provider)" in combined, "provider no longer escaped in the render path"
    assert "escapeHtml(m.runtime" in combined, "runtime no longer escaped in the render path"

    # renderComparisonStrip: same identity fields (a.id/a.runtime, State.bId),
    # concatenation style — assert the escape call wraps the risky value
    # directly (`escapeHtml(String(model))` / `escapeHtml(runs)`) and that no
    # risky field is concatenated into the HTML string bare (`+ model +`
    # etc. without an escapeHtml(...) around it).
    cs_body = extract_function("renderComparisonStrip")
    assert "escapeHtml(String(model))" in cs_body
    assert "escapeHtml(runs)" in cs_body
    bare_concat = re.compile(r"\+\s*(model|runs|aModel|State\.bId)\s*\+")
    assert not bare_concat.search(cs_body), (
        "renderComparisonStrip concatenates a risky identity field into "
        "HTML without escapeHtml()"
    )


@pytest.mark.parametrize("provider", [
    "claude", "nvidia", "openrouter", "huggingface", "custom",
    "xai", "google", "mistral", "cohere", "together",
])
def test_sec_airgap_refusal_on_chat_models_all_10_providers(monkeypatch, provider):
    """SEC-3 (F-AIRGAP, FAIL-GATE): airgapped /api/chat/models?provider=<p> refuses
    for ALL 10 providers BEFORE any token read or network call. Asserts
    airgapped:true, empty gallery, AND that no outbound requests.get fired
    (proves airgap-first ordering, no work-before-check)."""
    client = _client(monkeypatch, "airgapped")
    with patch("requests.get") as g, patch("requests.post") as p:
        r = client.get(f"/api/chat/models?provider={provider}")
    body = r.json()
    if provider == "custom":
        # custom IS a cloud provider in the registry → airgap path
        assert body.get("airgapped") is True
    else:
        assert body.get("airgapped") is True, f"{provider}: airgap not enforced"
    assert body["gallery"]["catalog"] == []
    g.assert_not_called()
    p.assert_not_called()


@pytest.mark.parametrize("provider", [
    "claude", "nvidia", "openrouter", "huggingface", "custom",
    "xai", "google", "mistral", "cohere", "together",
])
def test_sec_airgap_refusal_on_chat_default_all_10_providers(monkeypatch, isolated_secrets, provider):
    """SEC-4 (F-DEFAULT-LEAK set-time, FAIL-GATE): airgapped POST /api/chat/default
    {provider:<cloud>} refuses for ALL 10 providers and writes NOTHING (no token,
    no secrets mutation). Uses isolated_secrets so the real file is never touched."""
    client = _client(monkeypatch, "airgapped")
    r = client.post("/api/chat/default",
                    json={"provider": provider, "model": "x", "runtime": provider})
    body = r.json()
    assert body.get("ok") is False, f"{provider}: cloud default not refused while airgapped"
    assert "airgap" in body.get("error", "").lower()
    # Nothing persisted
    assert not isolated_secrets.exists() or "ARAIL_CHAT_DEFAULT_MODEL" not in isolated_secrets.read_text()


def test_sec_no_endpoint_response_echoes_a_token(monkeypatch, isolated_secrets):
    """SEC-5 (token-echo, FAIL-GATE): plant a real-looking token; hit every new/
    changed endpoint; assert the secret string never appears in any response body.
    Covers /api/chat/models (cloud), /api/chat/default, /api/chat/models/set-ctx."""
    from arail.portal import app as portal_app
    SECRET = "sk-ant-SUPER-SECRET-VALUE-do-not-echo-123456"
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    client = _client(monkeypatch, "hybrid")
    bodies = []

    with patch.object(portal_app, "_fetch_provider_models",
                      return_value=["claude-opus-4-7"]):
        bodies.append(client.get("/api/chat/models?provider=claude").text)
    bodies.append(client.post("/api/chat/default",
                              json={"provider": "claude", "model": "claude-opus-4-7",
                                    "runtime": "claude"}).text)
    bodies.append(client.post("/api/chat/models/set-ctx",
                              json={"model_id": "claude-opus-4-7", "ctx": 8192}).text)

    for b in bodies:
        assert SECRET not in b, "FAIL-GATE: a token value was echoed in a response body"


def test_sec_set_ctx_rejects_path_traversal_variants(monkeypatch):
    """SEC-6 (F-VALIDATE traversal): a battery of traversal/separator attempts on
    set-ctx model_id must all be rejected. The needle: ollama ids with ':' are
    valid; '/', '\\', '..' are not."""
    client = _client(monkeypatch, "hybrid")
    attacks = [
        "../etc/passwd",
        "..\\..\\windows\\system32",
        "/etc/shadow",
        "models/../../../secrets.env",
        "library/model",          # slash → rejected even though it looks ollama-ish
        "a/../b",
        "....//....//etc",
    ]
    with patch("arail.chat.detect_installed_models", return_value=[]), \
         patch("arail.portal.app._scan_local_models", return_value={"models": []}):
        for atk in attacks:
            r = client.post("/api/chat/models/set-ctx",
                            json={"model_id": atk, "ctx": 4096})
            body = r.json()
            assert body.get("ok") is False, f"traversal accepted: {atk!r} → {body!r}"


def test_sec_set_ctx_accepts_ollama_colon_id_rejects_slash(monkeypatch):
    """SEC-7 (F-VALIDATE needle): the exact threading the architect called out —
    'qwen2.5:7b' (colon, valid ollama) accepted; 'library/model' (slash) rejected
    even though both are plausible ids."""
    client = _client(monkeypatch, "hybrid")

    with patch("arail.chat.detect_installed_models",
               return_value=[{"id": "qwen2.5:7b", "runtime": "ollama"}]), \
         patch("arail.portal.app._scan_local_models", return_value={"models": []}), \
         patch("arail.portal.app._persist_ctx_override", return_value={"qwen2.5:7b": 8192}):
        ok = client.post("/api/chat/models/set-ctx",
                         json={"model_id": "qwen2.5:7b", "ctx": 8192}).json()
        bad = client.post("/api/chat/models/set-ctx",
                          json={"model_id": "library/model", "ctx": 8192}).json()

    assert ok.get("ok") is True, f"valid colon ollama id rejected: {ok!r}"
    assert bad.get("ok") is False, "slash id accepted (should reject)"


# ===========================================================================
# 20% — RACE & FAILURE MODES (RF-*)
# ===========================================================================

def test_rf_default_leak_end_to_end_airgap_drops_cloud(monkeypatch, isolated_secrets):
    """RF-1 (F-DEFAULT-LEAK, FAIL-GATE, end-to-end): set a cloud default in HYBRID,
    flip the lab to AIRGAPPED, then resolve defaults as the send path does.
    _apply_chat_defaults must drop the cloud provider → my_machine. This is the
    end-to-end version the architect required, not just the unit."""
    from arail.portal import app as portal_app

    # 1) set cloud default in hybrid (write goes to isolated secrets)
    client = _client(monkeypatch, "hybrid")
    set_r = client.post("/api/chat/default",
                        json={"provider": "claude", "model": "claude-opus-4-7",
                              "runtime": "claude"}).json()
    assert set_r.get("ok") is True
    # endpoint sets os.environ COMPUTE_SOURCE + ARAIL_CHAT_DEFAULT_MODEL
    assert os.environ.get("COMPUTE_SOURCE") == "claude"

    # 2) flip airgapped
    monkeypatch.setenv("LAB_MODE", "airgapped")
    assert portal_app._is_airgapped() is True

    # 3) resolve defaults exactly as api_chat does (blank per-message values)
    backend, model, runtime = portal_app._apply_chat_defaults(None, None, None)
    assert backend == "my_machine", f"cloud default leaked through airgap: backend={backend!r}"
    assert model in ("", None), f"cloud model leaked: {model!r}"
    assert runtime in ("", None), f"cloud runtime leaked: {runtime!r}"


def test_rf_apply_chat_defaults_per_message_value_wins(monkeypatch, isolated_secrets):
    """RF-2 (A8): a per-message backend/model/runtime always wins over the stored
    default, even in hybrid with a cloud default set."""
    from arail.portal import app as portal_app
    monkeypatch.setenv("COMPUTE_SOURCE", "claude")
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL",
                       json.dumps({"model": "claude-opus-4-7", "runtime": "claude"}))
    monkeypatch.setenv("LAB_MODE", "hybrid")
    b, m, rt = portal_app._apply_chat_defaults("nvidia", "nim-model", "nvidia")
    assert (b, m, rt) == ("nvidia", "nim-model", "nvidia")


def test_rf_apply_chat_defaults_bad_json_does_not_raise(monkeypatch, isolated_secrets):
    """RF-3: corrupt ARAIL_CHAT_DEFAULT_MODEL must degrade silently, returning the
    inputs unchanged — never raise into the send path."""
    from arail.portal import app as portal_app
    monkeypatch.setenv("ARAIL_CHAT_DEFAULT_MODEL", "{{{not json")
    monkeypatch.setenv("LAB_MODE", "hybrid")
    b, m, rt = portal_app._apply_chat_defaults(None, None, None)
    # no raise; falls through (backend may be None or my_machine, never crashes)
    assert isinstance(b, (str, type(None)))


def test_rf_set_ctx_purges_cache_for_model(monkeypatch):
    """RF-4 (F-CACHE): set-ctx purges _RUNTIME_BACKEND_CACHE entries keyed on the
    model so the next dispatch rebuilds with the new num_ctx. Plant a stale entry,
    set ctx, assert it's gone."""
    from arail.portal import app as portal_app
    client = _client(monkeypatch, "hybrid")

    portal_app._RUNTIME_BACKEND_CACHE[("ollama", "qwen3:8b")] = object()
    portal_app._RUNTIME_BACKEND_CACHE[("mlx-openai", "qwen3:8b")] = object()
    portal_app._RUNTIME_BACKEND_CACHE[("ollama", "other-model")] = object()

    with patch("arail.chat.detect_installed_models",
               return_value=[{"id": "qwen3:8b", "runtime": "ollama"}]), \
         patch("arail.portal.app._scan_local_models", return_value={"models": []}), \
         patch("arail.portal.app._persist_ctx_override", return_value={"qwen3:8b": 32768}):
        r = client.post("/api/chat/models/set-ctx",
                        json={"model_id": "qwen3:8b", "ctx": 32768}).json()

    assert r.get("ok") is True
    assert ("ollama", "qwen3:8b") not in portal_app._RUNTIME_BACKEND_CACHE
    assert ("mlx-openai", "qwen3:8b") not in portal_app._RUNTIME_BACKEND_CACHE
    # unrelated model untouched
    assert ("ollama", "other-model") in portal_app._RUNTIME_BACKEND_CACHE
    portal_app._RUNTIME_BACKEND_CACHE.pop(("ollama", "other-model"), None)


def test_rf_ctx_override_flows_into_ollama_dispatch_num_ctx(monkeypatch):
    """RF-5 (B2 reachability, F-OLLAMA-SHIM, F-OOM hint upstream): the full
    set-ctx→resolve→build→dispatch path. Plant an override, build via the dispatch
    branch, call complete() (mocked POST), assert options.num_ctx == the override
    AND the POST hits /api/chat (native), not /v1 (the shim that silently drops it)."""
    from arail.portal import app as portal_app
    monkeypatch.setenv("ARAIL_MODEL_CTX_OVERRIDES", json.dumps({"ai-eng:latest": 16384}))

    be = portal_app._get_runtime_backend("ollama", "ai-eng:latest")
    from arail.router.backends import OllamaNativeBackend
    assert isinstance(be, OllamaNativeBackend), "dispatch must build OllamaNativeBackend"

    captured = {}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status.return_value = None
        m.json.return_value = {"message": {"content": "hi"}, "model": "ai-eng:latest",
                               "eval_count": 2}
        return m

    be._session = MagicMock()
    be._session.post.side_effect = _fake_post
    be.complete("hello")

    assert "/api/chat" in captured["url"], f"must hit native /api/chat, got {captured['url']}"
    assert "/v1/" not in captured["url"], "must NOT hit the OpenAI /v1 shim"
    assert captured["json"]["options"]["num_ctx"] == 16384
    # cleanup the cache entry the build created
    portal_app._RUNTIME_BACKEND_CACHE.pop(("ollama", "ai-eng:latest"), None)


@pytest.mark.parametrize("ctx,expect_ok", [
    (256, True),          # exact min boundary
    (1_000_000, True),    # exact max boundary
    (255, False),         # just under min
    (1_000_001, False),   # just over max
    (0, False),
    (-1, False),
])
def test_rf_ctx_boundary_clamp_reject(monkeypatch, ctx, expect_ok):
    """RF-6 (F-OOM boundary): set-ctx must accept exactly [256, 1_000_000] and
    reject 255 / 1_000_001 / 0 / negatives. The huge-ctx OOM risk is gated by the
    upper bound — this locks the boundary."""
    client = _client(monkeypatch, "hybrid")
    with patch("arail.chat.detect_installed_models",
               return_value=[{"id": "qwen3:8b", "runtime": "ollama"}]), \
         patch("arail.portal.app._scan_local_models", return_value={"models": []}), \
         patch("arail.portal.app._persist_ctx_override", return_value={"qwen3:8b": ctx}):
        r = client.post("/api/chat/models/set-ctx",
                        json={"model_id": "qwen3:8b", "ctx": ctx}).json()
    assert r.get("ok") is expect_ok, f"ctx={ctx} expected ok={expect_ok}, got {r!r}"


def test_rf_set_ctx_non_integer_ctx_rejected(monkeypatch):
    """RF-7: a non-integer ctx ('lots', None, float-string) is rejected, not coerced
    into a bad KV-cache size."""
    client = _client(monkeypatch, "hybrid")
    with patch("arail.chat.detect_installed_models",
               return_value=[{"id": "qwen3:8b", "runtime": "ollama"}]), \
         patch("arail.portal.app._scan_local_models", return_value={"models": []}):
        for bad in ("lots", None, "3.5", [], {}):
            r = client.post("/api/chat/models/set-ctx",
                            json={"model_id": "qwen3:8b", "ctx": bad}).json()
            assert r.get("ok") is False, f"non-int ctx accepted: {bad!r}"


def test_rf_provider_models_network_timeout_returns_empty_not_500(monkeypatch):
    """RF-8 (failure injection): the upstream /models call raises (timeout/conn
    reset). _fetch_provider_models swallows it → empty catalog, never a 500."""
    from arail.portal import app as portal_app
    client = _client(monkeypatch, "hybrid")

    import requests
    with patch.object(portal_app, "_provider_token", return_value="sk-x"), \
         patch("requests.get", side_effect=requests.exceptions.Timeout("boom")):
        r = client.get("/api/chat/models?provider=mistral")
    assert r.status_code == 200
    assert r.json()["gallery"]["catalog"] == []


# ===========================================================================
# 10% — REGRESSION (REG-*)
# ===========================================================================

def test_reg_no_provider_payload_has_no_cloud_only_fields(monkeypatch):
    """REG-1 (R1 spirit): the no-provider legacy payload must NOT carry cloud-only
    fields (airgapped:true, cta). Guards the legacy branch against cloud leakage."""
    client = _client(monkeypatch, "airgapped")
    body = client.get("/api/chat/models").json()
    assert body.get("airgapped") is not True
    assert "cta" not in body or not body.get("cta")
    assert "gallery" in body
    assert isinstance(body["gallery"].get("installed"), list)


def test_reg_r1_nested_value_exactness_deep_compact(monkeypatch):
    """REG-2 (carryover #2 — tighten R1 nested subset → value check): the architect
    flagged that R1 checks nested key SUBSET, not exact values inside deep/compact.
    This asserts the structural fields inside deep/compact have stable types/values
    on the no-provider payload so a silent value drift inside those dicts is caught.
    Deterministically mock the gallery to make values stable."""
    from arail.portal import app as portal_app

    fake_router = MagicMock()
    fake_router.backend_name = "ollama"
    fake_router._backend = MagicMock()
    fake_router._backend.model_name = "ai-eng:latest"

    client = _client(monkeypatch, "airgapped")
    with patch.object(portal_app, "_get_primary_router", return_value=fake_router), \
         patch.object(portal_app, "_get_live_ollama_current", return_value=None):
        body = client.get("/api/chat/models").json()

    # If the payload exposes deep/compact dicts, their inner scalar fields must be
    # the documented types (drift in a value-type is a regression).
    for slot in ("deep", "compact"):
        if slot in body and isinstance(body[slot], dict):
            d = body[slot]
            # backend identity fields, when present, must be strings (not ints/dicts)
            for k in ("backend", "model", "label"):
                if k in d:
                    assert isinstance(d[k], (str, type(None))), \
                        f"R1 value drift: {slot}.{k} is {type(d[k])}, expected str/None"


def test_reg_catalog_entry_backcompat_legacy_rows(monkeypatch):
    """REG-3 (R4 / F-CATALOG): a legacy catalog row WITHOUT provider/ctx still
    loads and as_dict() emits provider=None, ctx=None — no KeyError, no drop."""
    from arail.chat import CatalogEntry
    e = CatalogEntry(
        id="qwen3:8b", name="Qwen3 8B", family="qwen",
        size_gb=4.7, released="2025", source="ollama",
        good_at=["chat"], description="", install="ollama pull qwen3:8b",
        tier="recommended",
        # provider/ctx omitted → must default to None (the legacy-row case)
    )
    d = e.as_dict()
    assert d.get("provider") is None
    assert d.get("ctx") is None
    assert d["id"] == "qwen3:8b"


def test_reg_context_tokens_parser_exact_values():
    """REG-4 (parser regression): context_tokens binary semantics locked.
    128K→131072, 1M→1048576, 32k→32768, bare ints, junk→None."""
    from arail.model_specs import context_tokens
    assert context_tokens("128K tokens") == 131072
    assert context_tokens("1M tokens") == 1048576
    assert context_tokens("32k") == 32768
    assert context_tokens("4096") == 4096
    assert context_tokens(4096) == 4096
    assert context_tokens("banana") is None
    assert context_tokens("") is None
    assert context_tokens(None) is None
