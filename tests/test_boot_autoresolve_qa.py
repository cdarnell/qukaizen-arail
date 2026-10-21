"""QA pass for sprints/2026-10-20-boot-model-autoresolve (TEST_REPORT.md).

Gaps the builder/reviewer suites did not cover: setup-on-clean-machine shapes
(setup.sh's maximus/mlx .env, missing/odd paths), hand-edited and garbage
model_defaults.yaml variants, malformed Ollama responses, odd crash
sentinels, pin-endpoint adversarial input, and the two retired banners.
Every test is hermetic: no real Ollama, no real lab dir, no real yaml.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arail import model_defaults as md
from arail import model_resolve as mr

REPO = Path(__file__).resolve().parents[1]
_REAL_SCAN = mr._scan_models_dir


def _tag(name, gb=1.3, ps="1.2B"):
    return {"id": name, "size_gb": gb, "family": "llama", "parameter_size": ps}


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """Isolated yaml + registry + env, stubbed machine facts (Ollama up with
    llama-ai-eng only, no deep model on disk, minimalist-like B)."""
    from arail.registry import core as reg_core
    f = tmp_path / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(f))
    monkeypatch.setenv("ARAIL_MODEL_REGISTRY_FILE", str(tmp_path / "reg.json"))
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("MODEL_BACKEND", "ollama_native")
    for k in ("MODEL_NAME", "AEROLLM_MODEL", "ARAIL_ENV_PREF_A", "ARAIL_ENV_PREF_B"):
        monkeypatch.delenv(k, raising=False)
    state = {"tags": [_tag("llama-ai-eng:latest")]}
    monkeypatch.setattr(mr, "_fetch_ollama_tags", lambda: state["tags"])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 32.0)
    monkeypatch.setattr(mr, "_secondary_cap_b", lambda: 14.0)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [])
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: "deep model needs maximus")
    mr.last_resolution().clear()
    reg_core.reset_registry()
    state["file"] = f
    yield state
    reg_core.reset_registry()
    mr.last_resolution().clear()
    for k in ("MODEL_NAME", "AEROLLM_MODEL", "ARAIL_ENV_PREF_A", "ARAIL_ENV_PREF_B"):
        os.environ.pop(k, None)


# ── setup-on-clean-machine ─────────────────────────────────────────

def test_setup_maximus_mlx_env_keeps_a_loadable_primary_backend_model_pair(lab, monkeypatch):
    """setup.sh on a maximus Mac writes MODEL_BACKEND=mlx and
    MODEL_NAME=mlx-community/Qwen3-8B-4bit. The primary router builds its
    backend from MODEL_BACKEND and loads MODEL_NAME. Boot must not stamp an
    Ollama tag into MODEL_NAME while the backend is MLX: on a real boot that
    pair makes the boot warm die with HFValidationError ('llama-ai-eng:latest'
    is not a repo id) and leaves the primary chat backend unloadable."""
    monkeypatch.setenv("MODEL_BACKEND", "mlx")
    monkeypatch.setenv("MODEL_NAME", "mlx-community/Qwen3-8B-4bit")
    mr.resolve_and_apply()
    backend = os.environ.get("MODEL_BACKEND")
    name = os.environ.get("MODEL_NAME")
    ollama_tag = name in {t["id"] for t in lab["tags"]}
    assert not (backend in ("mlx", "cuda", "cpu") and ollama_tag), (
        f"MODEL_BACKEND={backend} but MODEL_NAME was stamped to Ollama tag {name}")


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_odd_models_dir_resolves_b_none_without_raising(lab, monkeypatch, tmp_path, kind):
    target = tmp_path / "nope" / "models"
    if kind == "file":
        target = tmp_path / "models-is-a-file"
        target.write_text("not a dir")
    monkeypatch.setenv("ARAIL_MODELS_DIR", str(target))
    monkeypatch.setattr(mr, "_scan_models_dir", _REAL_SCAN)      # real scanner
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest"
    assert out["b"]["model"] is None


def test_yaml_in_a_directory_that_does_not_exist_still_boots(lab, monkeypatch, tmp_path):
    target = tmp_path / "missing-dir" / "deeper" / "model_defaults.yaml"
    monkeypatch.setenv("ARAIL_MODEL_DEFAULTS_FILE", str(target))
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest"
    # Either it was created, or the strip says it couldn't be saved — never silent.
    assert target.exists() or any(d["kind"] == "cache_unsaved" for d in out["degraded"])


def test_only_an_embedding_model_installed_gives_a_none_with_the_setup_command(lab, monkeypatch):
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["embedding"])
    lab["tags"] = [{"id": "nomic-embed-text:latest", "size_gb": 0.3,
                    "family": "nomic-bert", "parameter_size": "137M"}]
    out = mr.resolve_and_apply()
    assert out["a"]["model"] is None
    text = json.dumps(out)
    assert "ollama create llama-ai-eng" in text


def test_unicode_model_name_round_trips_through_the_cache(lab):
    lab["tags"] = [_tag("llama-ai-eng:latest"), _tag("模型-ü:7b", 4.0, "7B")]
    md.write_pin("a", "模型-ü:7b")
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "模型-ü:7b"
    again = md.read()
    assert again["ok"] and again["pins"]["a"] == "模型-ü:7b"
    assert again["resolved"]["a"]["model"] == "模型-ü:7b"


# ── malformed Ollama responses (resolver must never raise) ─────────

class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("body", [
    b"[]",                                     # list, not object
    b'"oops"',                                 # bare string
    b'{"models": [{"name": "x", "size": "12"}]}',      # size as string
    b'{"models": [{"name": "x", "details": "q"}]}',    # details as string
    b'{"models": {"name": "x"}}',             # models as object
    b"not json at all",
    b"",
])
def test_malformed_tags_body_never_escapes_the_resolver(monkeypatch, body):
    monkeypatch.setattr(mr, "_ollama_base", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(mr.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    try:
        tags = mr._fetch_ollama_tags()
    except Exception as exc:  # pragma: no cover - this is the failure
        pytest.fail(f"_fetch_ollama_tags raised {type(exc).__name__}: {exc}")
    assert tags is None or isinstance(tags, list)


def test_resolve_boot_never_raises_on_malformed_tags(monkeypatch):
    monkeypatch.setattr(mr, "_fetch_ollama_tags",
                        lambda: [{"id": "x", "size_gb": "big", "family": None,
                                  "parameter_size": object()}])
    monkeypatch.setattr(mr, "_fetch_capabilities", lambda n: ["completion"])
    monkeypatch.setattr(mr, "_total_ram_gb", lambda: 16.0)
    res = mr.resolve_boot(pins={}, env_pref={})
    assert res.a is not None and res.b is not None


# ── hand-edited / garbage model_defaults.yaml ──────────────────────

GARBAGE = [
    b"\x00\xff\xfe binary \x89PNG",
    b"- a\n- b\n",                         # list at top level
    b"just a scalar\n",
    b"",                                   # empty file
    b"version: 2\npins: [a, b]\n",         # pins not a mapping
    b"version: 2\npins: {a: 123, b: 4.5}\nresolved: 7\n",
    b"version: '2'\npins: {b: none}\n",    # string version
    b"version: 3\npins: {b: none}\n",      # future version
    b"default_a: llama-ai-eng\ndefault_b: None\n",   # v1, string 'None'
    b"default_a: null\ndefault_b: ''\n",    # v1, empty
    b"version: 2\npins: {a: llama-ai-eng:latest, b: none}\n  bad: [indent\n",
    b"!!python/object/apply:os.system ['echo pwned']\n",   # unsafe tag
]


@pytest.mark.parametrize("raw", GARBAGE, ids=range(len(GARBAGE)))
def test_garbage_yaml_never_blocks_boot_and_is_never_silently_destroyed(lab, raw):
    lab["file"].write_bytes(raw)
    out = mr.resolve_and_apply()
    assert out["a"]["model"] == "llama-ai-eng:latest"
    after = lab["file"].read_bytes()
    data = md.read()
    # Either the bytes survived, or the file is valid AND kept the intent
    # that was readable (a 'none' B pin must never be lost).
    if after != raw:
        assert data["ok"], "rewrote the file into something unreadable"
        if b"b: none" in raw and md.read.__module__:
            assert data["pins"]["b"] == "none", "b: none pin lost on rewrite"


def test_unsafe_yaml_tag_is_not_executed(lab, tmp_path):
    marker = tmp_path / "pwned"
    lab["file"].write_text(f"!!python/object/apply:os.system ['touch {marker}']\n")
    mr.resolve_and_apply()
    md.apply()
    assert not marker.exists()


def test_v1_string_none_for_b_keeps_the_deep_slot_off(lab):
    lab["file"].write_text("default_a: llama-ai-eng\ndefault_b: None\n")
    out = mr.resolve_and_apply()
    assert out["b"]["model"] is None


def test_cli_banner_json_get_survive_garbage_yaml(tmp_path):
    f = tmp_path / "model_defaults.yaml"
    f.write_bytes(b"\x00\xff garbage: [\n")
    env = dict(os.environ, ARAIL_MODEL_DEFAULTS_FILE=str(f),
               PYTHONPATH=str(REPO / "src"), ARAIL_ENV_FILE=str(tmp_path / ".env"))
    for args in (["--banner"], ["--json"], ["--get", "default_a"]):
        p = subprocess.run([sys.executable, "-m", "arail.model_defaults", *args],
                           env=env, capture_output=True, text=True, timeout=60)
        assert "Traceback" not in p.stderr, (args, p.stderr[-800:])
        assert p.returncode == 0, (args, p.returncode, p.stderr[-400:])
    assert f.read_bytes() == b"\x00\xff garbage: [\n"


@pytest.mark.xfail(strict=True, reason=(
    "TEST_REPORT.md QA-3 (LOW): an unreadable yaml drops a `b: none` pin for "
    "the boot, so auto-B is picked and warmed - the SIGABRT workaround is "
    "lost for one boot until the crash sentinel catches it"))
def test_unreadable_yaml_with_b_none_does_not_auto_pick_a_deep_model(lab, monkeypatch, tmp_path):
    d = tmp_path / "models" / "Qwen2.5-7B-Instruct-4bit"
    d.mkdir(parents=True)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [
        {"id": "Qwen2.5-7B-Instruct-4bit", "path": str(d), "params_b": 7.0,
         "size_gb": 4.0}])
    lab["file"].write_text("version: 2\npins: {a: null, b: none}\n  oops: [\n")
    out = mr.resolve_and_apply()
    assert out["b"]["model"] is None


# ── crash sentinel oddities ────────────────────────────────────────

@pytest.fixture
def sentinel(monkeypatch, tmp_path):
    from arail.portal import model_warmth as mw
    p = tmp_path / ".aerollm_boot_warm.inflight"
    monkeypatch.setattr(mw, "sentinel_path", lambda: p)
    return mw, p


@pytest.mark.parametrize("raw", [
    "", "{", "[]", "null", '"str"',
    json.dumps({"model": "M", "pid": "abc", "started_at": time.time()}),
    json.dumps({"model": "M", "pid": -1, "started_at": time.time()}),
    json.dumps({"model": "M", "pid": 0, "started_at": time.time()}),
    json.dumps({"model": "M", "pid": 1, "started_at": "yesterday"}),
    json.dumps({"model": "M", "pid": 1, "started_at": time.time() + 3600}),  # future
    json.dumps({"model": None, "pid": 1}),
])
def test_odd_sentinels_never_raise_and_never_wedge(sentinel, raw):
    mw, p = sentinel
    p.write_text(raw)
    crashed = mw.crashed_model()          # must not raise
    elsewhere = mw._loading_elsewhere()   # must not raise
    # A sentinel can never be BOTH a crash and a live load elsewhere.
    assert not (crashed and elsewhere)
    # Garbage pid / future or unparsable timestamps are never "live".
    assert elsewhere is False


@pytest.mark.xfail(strict=True, raises=OverflowError, reason=(
    "TEST_REPORT.md QA-5 (LOW): _pid_alive lets OverflowError from os.kill "
    "escape for a pid > INT_MAX; _boot_models catches it but skips the whole "
    "resolve for that boot"))
def test_sentinel_with_an_out_of_range_pid_is_stale_not_an_exception(sentinel):
    mw, p = sentinel
    p.write_text(json.dumps({"model": "M", "pid": 2 ** 40, "started_at": time.time()}))
    assert mw.crashed_model() == "M"


def test_stale_sentinel_skips_b_on_a_real_boot_path(lab, sentinel, monkeypatch, tmp_path):
    mw, p = sentinel
    d = tmp_path / "models" / "Bad-7B"
    d.mkdir(parents=True)
    monkeypatch.setattr(mr, "_deep_slot_available", lambda: None)
    monkeypatch.setattr(mr, "_scan_models_dir", lambda: [
        {"id": "Bad-7B", "path": str(d), "params_b": 7.0, "size_gb": 4.0}])
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    p.write_text(json.dumps({"model": "Bad-7B", "pid": dead.pid,
                             "started_at": time.time() - 30}))
    out = mr.resolve_and_apply(skip_b=mw.crashed_model())
    assert out["b"]["model"] is None
    assert any(d["kind"] == "crashed_last_boot" for d in out["degraded"])


def test_boot_warm_zero_never_touches_either_slot(lab, monkeypatch):
    import arail.portal.app as app_mod
    from arail.portal import model_warmth as mw
    monkeypatch.setenv("ARAIL_BOOT_WARM", "0")
    monkeypatch.setattr(app_mod, "_get_primary_router",
                        lambda: pytest.fail("A warmed with ARAIL_BOOT_WARM=0"))

    async def no_b(model):
        pytest.fail("B warmed with ARAIL_BOOT_WARM=0")
    monkeypatch.setattr(mw, "boot_warm_deep", no_b)
    app_mod._MODEL_WARM = False
    asyncio.run(app_mod._boot_models())
    assert app_mod._MODEL_WARM is True
    # resolution still happened and was cached
    assert md.read()["resolved"]["a"]["model"] == "llama-ai-eng:latest"


# ── retired banners can never come back ────────────────────────────

def test_retired_drift_and_bare_400_banners_are_gone_from_shipped_code():
    hay = []
    for root in ("src/arail", "scripts"):
        for p in (REPO / root).rglob("*"):
            if p.suffix in (".py", ".html", ".js", ".sh") and p.is_file():
                hay.append((p, p.read_text(encoding="utf-8", errors="ignore")))
    for p, text in hay:
        assert "no longer matches the settled value" not in text, p
        assert "settled value" not in text, p


def test_ollama_400_surfaces_the_real_error_not_a_bare_bad_request(monkeypatch):
    from arail.router import backends as b
    import requests

    class R:
        status_code = 400
        reason = "Bad Request"
        url = "http://127.0.0.1:11434/api/chat"
        text = '{"error":"time: missing unit in duration \\"-1\\""}'

        def json(self):
            return json.loads(self.text)

        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error: Bad Request for url: " + self.url,
                                     response=self)
    with pytest.raises(Exception) as ei:
        b._raise_for_status_with_detail(R())
    msg = str(ei.value)
    assert "missing unit in duration" in msg
    assert "Authorization" not in msg and "Bearer" not in msg


@pytest.mark.parametrize("raw,expected", [
    (" -1 ", -1), ("0", 0), ("+5", 5), ("5m", "5m"), ("1.5", "1.5"), ("-1m", "-1m"),
])
def test_keep_alive_env_values_are_typed_for_ollama(monkeypatch, raw, expected):
    from arail.router import backends as b
    monkeypatch.setenv("ARAIL_OLLAMA_KEEP_ALIVE", raw)
    be = b.OllamaNativeBackend.__new__(b.OllamaNativeBackend)
    be.model = "x"
    monkeypatch.setattr(be.__class__, "_is_registry_tier0_model", lambda self: False,
                        raising=False)
    got = be._keep_alive()
    assert got == expected and type(got) is type(expected)


# ── pin endpoint: adversarial input ────────────────────────────────

@pytest.fixture
def client(lab, monkeypatch):
    from fastapi.testclient import TestClient
    import arail.portal.app as app_mod

    def installed():
        return [{"id": t["id"], "runtime": "ollama", "size_gb": 1.0,
                 "modified": "", "endpoint": "x"} for t in lab["tags"]]
    monkeypatch.setattr("arail.chat._ollama_installed_models", lambda *a, **k: installed())
    (Path(os.environ["ARAIL_MODELS_DIR"])).mkdir(parents=True, exist_ok=True)
    with TestClient(app_mod.app) as c:
        yield c


@pytest.mark.parametrize("model", [
    "../outside", "../../../../etc", "/etc", "/", "~", "~/models/x",
    "sub/../../outside", "x" * 10000, "..", ".",
])
def test_pin_b_rejects_traversal_absolute_and_garbage(client, lab, model):
    r = client.post("/api/models/pin", json={"slot": "b", "model": model})
    assert r.status_code == 400, (model[:40], r.status_code, r.text[:300])
    assert md.read()["pins"]["b"] in (None, "none")


@pytest.mark.xfail(strict=True, raises=ValueError, reason=(
    "TEST_REPORT.md QA-4 (LOW): a NUL byte in a slot-B pin raises "
    "ValueError('embedded null byte') from os.path.realpath -> HTTP 500, not 400"))
def test_pin_b_with_a_nul_byte_is_a_400_not_a_500(client):
    r = client.post("/api/models/pin", json={"slot": "b", "model": "a\x00b"})
    assert r.status_code == 400


def test_blank_pin_clears_to_auto(client, lab):
    md.write_pin("b", "none")
    r = client.post("/api/models/pin", json={"slot": "b", "model": "\n"})
    assert r.status_code == 200 and md.read()["pins"]["b"] is None


def test_pin_b_rejects_symlink_escaping_the_models_dir(client, tmp_path):
    outside = tmp_path / "outside-model"
    outside.mkdir()
    link = Path(os.environ["ARAIL_MODELS_DIR"]) / "escape"
    link.symlink_to(outside)
    r = client.post("/api/models/pin", json={"slot": "b", "model": "escape"})
    assert r.status_code == 400


@pytest.mark.parametrize("body", [
    {"slot": "c", "model": "x"}, {"slot": None, "model": "x"}, {"slot": "a", "model": 5},
    {"slot": "a", "model": ["x"]}, {"slot": "b", "model": {"$ne": 1}}, {}, [],
])
def test_pin_rejects_malformed_bodies_without_a_500(client, body):
    r = client.post("/api/models/pin", json=body)
    assert 400 <= r.status_code < 500, (body, r.status_code, r.text[:200])


def test_pin_a_with_a_bogus_tag_suffix_never_reaches_env_or_registry(client, lab):
    """Validation accepts any tag whose base name is installed (same bar as the
    old settle; TEST_REPORT.md QA-6 INFO). What matters: only an installed id
    is ever stamped into MODEL_NAME / the tier-0 registry entry."""
    r = client.post("/api/models/pin", json={"slot": "a", "model": "llama-ai-eng:../../etc"})
    installed = {t["id"] for t in lab["tags"]}
    if r.status_code == 200:
        assert r.json()["resolution"]["a"]["model"] in installed
        assert os.environ.get("MODEL_NAME") in installed
        tier0 = next(e for e in r.json()["state"]["entries"] if e["id"] == "tier0-local")
        assert tier0["model_id"] in installed
    else:
        assert r.status_code == 400


def test_pin_errors_and_boot_payload_never_contain_secrets(client, monkeypatch):
    secret = "sk-ant-QA-SECRET-should-never-appear-0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("OLLAMA_HOST", f"http://user:{secret}@127.0.0.1:11434")
    bodies = [client.post("/api/models/pin", json={"slot": "a", "model": "nope:1b"}).text,
              client.post("/api/models/pin", json={"slot": "b", "model": "/etc"}).text,
              client.get("/api/models/boot").text,
              client.get("/api/models/boot?full=1").text,
              client.get("/api/instance").text]
    for t in bodies:
        assert secret not in t


def test_airgapped_remains_the_default_lab_mode(monkeypatch):
    monkeypatch.delenv("LAB_MODE", raising=False)
    from arail import airgap
    assert airgap.is_airgapped() is True


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_pin_refuses_cross_site_requests(client, lab, headers):
    md.write_pin("b", "none")
    r = client.post("/api/models/pin", json={"slot": "b", "model": None}, headers=headers)
    assert r.status_code == 403, (r.status_code, r.text[:200])
    assert md.read()["pins"]["b"] == "none"      # the crash workaround survives
