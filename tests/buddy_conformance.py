"""Runs the vendored buddy.host/v1 conformance cases against ARAIL's real edge routes.

The case format and its placeholders are defined in the contract
(``conformance.case_schema`` / ``placeholders`` / ``setup_keys``). Each case gets a fresh
world built from its ``setup`` (a hermetic data dir, a linked credential, a fake
researcher, a stand-in brain), then every request is sent through ``TestClient`` against
the real portal app. ``expect`` keys the runner does not understand fail the case: an
unchecked expectation must never pass silently.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from arail import agent_trace, config, scheduler
from arail.buddy_host import CONTRACT, CONTRACT_DIR, CONTRACT_SHA256, admission, brain, budget, ledger, link
from arail.portal import host_edge_routes
from arail.portal import scheduler as inference
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

CASES_DIR = CONTRACT_DIR / "conformance"
FAKE_PSEUDONYM = "p_0123456789"
_NOW_RE = re.compile(r"\$NOW(?:([+-])(\d+)s)?")

PATHS = {name: CONTRACT["transport"]["base_path"] + m["path"] for name, m in CONTRACT["methods"].items()}
HTTP = {name: m["http_method"] for name, m in CONTRACT["methods"].items()}
CORE_NAME = {"handshake": "_core_handshake", "trace.record": "_core_trace_record",
             "hold_all": "_core_hold_all", "pause_research": "_core_pause_research",
             "brain.chat": "_core_brain_chat", "brain.models": "_core_brain_models"}


def load_cases() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES_DIR.glob("*.json"))]


def resolve(value: Any, now: dt.datetime) -> Any:
    conf = CONTRACT["conformance"]
    if isinstance(value, dict):
        return {k: resolve(v, now) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, now) for v in value]
    if not isinstance(value, str):
        return value

    def _now(m):
        delta = int(m.group(2) or 0) * (-1 if m.group(1) == "-" else 1)
        return (now + dt.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = _NOW_RE.sub(_now, value)
    for token, repl in (("$CONTRACT_SHA256", CONTRACT_SHA256), ("$WRONG_CREDENTIAL", conf["wrong_credential"]),
                        ("$CREDENTIAL", conf["fake_credential"]), ("$FAKE_REPLY", conf["fake_reply"])):
        out = out.replace(token, repl)
    return out


def subset(actual: Any, expected: Any, path: str = "$") -> list[str]:
    """Problems where ``expected`` is not contained in ``actual`` (dicts by key, lists by index prefix)."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {type(actual).__name__}"]
        out: list[str] = []
        for k, v in expected.items():
            if k not in actual:
                out.append(f"{path}.{k}: missing")
            else:
                out += subset(actual[k], v, f"{path}.{k}")
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return [f"{path}: expected a list of at least {len(expected)}, got {actual!r}"]
        out = []
        for i, v in enumerate(expected):
            out += subset(actual[i], v, f"{path}[{i}]")
        return out
    return [] if actual == expected else [f"{path}: expected {expected!r}, got {actual!r}"]


class FakeOllama(BaseBackend):
    """Stands in for OllamaNativeBackend: records what it is called with, streams the fake reply."""

    def __init__(self, reply: str, *, uses_all_max_tokens: bool = False) -> None:
        self.reply = reply
        self.uses_all_max_tokens = uses_all_max_tokens
        self.calls: list[dict] = []

    def complete(self, *a, **kw):  # pragma: no cover - the edge only streams
        raise NotImplementedError

    def stream_complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None, *, system=None, messages=None,
                        response_schema=None):
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens,
                           "response_schema": response_schema})
        mid = max(1, len(self.reply) // 2)
        yield self.reply[:mid]
        yield self.reply[mid:]
        used = max_tokens if self.uses_all_max_tokens else 12
        yield ModelResponse(text=self.reply, model="llama-ai-eng", tokens_used=used, backend="ollama_native",
                            latency_ms=5.0)

    def health_check(self):
        return True


class FakeResearcher:
    def __init__(self, status: str) -> None:
        self.status = status
        self.pause_calls = 0

    def pause(self) -> None:
        self.pause_calls += 1
        self.status = "paused"


def _entry(kind: str) -> Any:
    base = dict(id="tier0", display_name="Llama AI Engineer", model_id="llama-ai-eng", enabled=True,
                endpoint="http://127.0.0.1:11434/v1", provider_type="local", backend="ollama_native")
    if kind == "cloud":
        base.update(provider_type="anthropic", backend="claude", endpoint=None, model_id="claude-x")
    elif kind == "non_ollama":
        base.update(backend="openai_compat", endpoint="http://127.0.0.1:1234/v1")
    return SimpleNamespace(**base)


class World:
    """One case's world. Built from ``setup``; everything is undone by pytest's monkeypatch."""

    def __init__(self, setup: dict, method: str, tmp_path: pathlib.Path, monkeypatch) -> None:
        self.setup, self.method, self.mp = setup, method, monkeypatch
        data = tmp_path / "lab-data"
        data.mkdir()
        monkeypatch.setattr(config, "DATA_DIR", data)
        monkeypatch.setattr("arail.agents.consent.CONSENT_DIR", tmp_path / "consent")
        for reset in (link._cache.update,):
            reset(key=None, rec=None)
        for mod in (ledger, budget, admission, brain):
            mod._reset_for_tests()
        host_edge_routes._seen_instances.clear()
        agent_trace._reset_for_tests()
        scheduler._reset_halt_for_tests()

        s = setup
        monkeypatch.delenv("ARAIL_INSTANCE", raising=False)
        if s.get("root_lab", True) is False:
            monkeypatch.setenv("ARAIL_INSTANCE", "some-world")
        monkeypatch.setenv("BIND_ADDR", "127.0.0.1" if s.get("bind_loopback", True) else "0.0.0.0")
        monkeypatch.setenv("LAB_MODE", s.get("lab_mode", "hybrid"))
        monkeypatch.setenv("LAB_TIER", s.get("tier", "maximus"))
        monkeypatch.setenv("PORTAL_PORT", "8080")

        if s.get("linked", True):
            self._link()
        if s.get("consent", True):
            from arail.agents.consent import ConsentStore
            ConsentStore().add_domain("https://api.telegram.org/")
        if s.get("held", False):
            scheduler.halt_all_jobs()
        if s.get("hold_readable", True) is False:
            path = scheduler._halt_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{corrupt")
            scheduler._halt_loaded = False
            scheduler._halted = False
            scheduler._halt_load_failed = False

        self.researcher = FakeResearcher(s.get("research_status", "running"))
        monkeypatch.setattr(host_edge_routes, "_researcher", lambda: self.researcher)

        kind = s.get("brain", "local")
        monkeypatch.setattr(brain, "resolve_entry", lambda: None if kind == "none" else _entry(kind))
        monkeypatch.setattr(brain, "probe_structured_output", lambda entry: kind != "no_structured_output")
        monkeypatch.setattr(brain, "catalog_family", lambda model_id: "llama" if model_id.startswith("llama") else None)

        used = s.get("budget_used") or {}
        calls = {FAKE_PSEUDONYM: int(s["calls_last_minute"])} if s.get("calls_last_minute") else None
        if used or calls:
            budget.default_budget()._seed(turns=int(used.get("turns", 0)), tokens=int(used.get("tokens", 0)), calls=calls)

        self.backend = FakeOllama(CONTRACT["conformance"]["fake_reply"],
                                  uses_all_max_tokens=s.get("brain_tokens", "few") == "max")
        monkeypatch.setattr(brain, "build_router", lambda: ModelRouter.from_backend(self.backend, "ollama_native"))
        if s.get("slot") == "busy":
            import contextlib

            @contextlib.asynccontextmanager
            async def _busy(label="chat", *, acquire_timeout_s=None):
                raise inference.InferenceSlotTimeout("busy")
                yield  # pragma: no cover

            monkeypatch.setattr(inference, "inference_slot", _busy)

        if s.get("fault") == "internal":
            def boom(*a, **kw):
                raise RuntimeError("injected fault")
            name = CORE_NAME[method]
            if hasattr(host_edge_routes, name):
                monkeypatch.setattr(host_edge_routes, name, boom)

        self.client = TestClient(host_edge_routes_app(), client=(s.get("peer", "127.0.0.1"), 50000))

    def _link(self) -> None:
        sha = link.credential_sha256(CONTRACT["conformance"]["fake_credential"])
        rec = {"schema": link.ARAIL_LINK_SCHEMA, "credential_sha256": sha, "credential_id": sha[:8],
               "linked_at": "2026-10-20T14:00:00Z", "buddy_home": "/nonexistent", "edge_base_url": link.edge_base_url(),
               "contract_sha256": CONTRACT_SHA256, "consent_request_id": "00000000"}
        link._ensure_arail_dir()
        link._write_private(link.link_path(), link._json_bytes(rec))

    # ---- sending
    def send(self, req: dict, now: dt.datetime):
        headers = resolve(req["headers"], now)
        body = resolve(req["body"], now)
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            kwargs["content"] = json.dumps(body)
        return self.client.request(HTTP[self.method], PATHS[self.method], **kwargs)

    # ---- checking
    def check(self, resp, expect: dict, now: dt.datetime) -> list[str]:
        problems: list[str] = []
        expect = resolve(expect, now)
        if resp.status_code != expect["status"]:
            problems.append(f"status {resp.status_code} != {expect['status']} ({resp.text[:300]})")
        try:
            body = resp.json()
        except ValueError:
            body = None
        code = expect["error_code"]
        got = body["error"]["code"] if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
        if got != code:
            problems.append(f"error_code {got!r} != {code!r}")
        if expect["status"] >= 400:
            for k, v in CONTRACT["errors"]["response_headers"].items():
                if resp.headers.get(k) != v:
                    problems.append(f"error response lacks {k}: {v}")
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                problems += subset(body, {"error": {"type": "buddy_host_error"}})
        if "stream" not in expect and expect["body_subset"]:
            problems += subset(body, expect["body_subset"])
        elif "stream" in expect:
            problems += self.check_stream(resp, expect["stream"])
        for k, v in (expect.get("headers_subset") or {}).items():
            if resp.headers.get(k) != v:
                problems.append(f"header {k}: {resp.headers.get(k)!r} != {v!r}")
        if "brain_received" in expect:
            problems += self.check_brain(expect["brain_received"])
        for key, want in (expect.get("edge_only") or {}).items():
            problems += self.check_edge_only(key, want)
        return problems

    def check_stream(self, resp, want: dict) -> list[str]:
        from jsonschema import Draft202012Validator
        problems: list[str] = []
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        if ctype != want["media_type"]:
            problems.append(f"media type {ctype!r} != {want['media_type']!r}")
        frames = [f for f in resp.text.split("\n\n") if f.strip()]
        if not frames or frames[-1].strip() != "data: [DONE]":
            problems.append("the stream does not end with data: [DONE]")
        chunk_schema = Draft202012Validator(CONTRACT["methods"]["brain.chat"]["response"]["stream"]["chunk"])
        text, finish, usage = "", None, False
        for f in frames[:-1]:
            if not f.startswith("data: "):
                problems.append(f"not an SSE data frame: {f[:60]!r}")
                continue
            chunk = json.loads(f[6:])
            for err in chunk_schema.iter_errors(chunk):
                problems.append(f"chunk violates the contract: {err.message[:100]}")
            for ch in chunk.get("choices", []):
                text += ch["delta"].get("content") or ""
                finish = ch["finish_reason"] or finish
            usage = usage or isinstance(chunk.get("usage"), dict)
        if text != want["assembled_content"]:
            problems.append(f"assembled content {text!r} != {want['assembled_content']!r}")
        if finish != want["finish_reason"]:
            problems.append(f"finish_reason {finish!r} != {want['finish_reason']!r}")
        if want.get("final_usage") and not usage:
            problems.append("no chunk carries usage")
        if want.get("ends_with_done") is False:
            problems.append("ends_with_done false is not a supported expectation")
        return problems

    def check_brain(self, want: dict) -> list[str]:
        if not self.backend.calls:
            return ["the fake brain was never called"]
        return subset(self.backend.calls[-1], want, "brain_received")

    def check_edge_only(self, key: str, want: Any) -> list[str]:
        ring = agent_trace.ring(500)
        if key == "researcher_pause_calls":
            return [] if self.researcher.pause_calls == want else [f"researcher.pause called {self.researcher.pause_calls}x, want {want}"]
        if key == "ledger_rows":
            n = ledger.default_ledger().count()
            return [] if n == want else [f"ledger has {n} rows, want {want}"]
        if key == "lab_held_after":
            return [] if scheduler.jobs_halted() == want else [f"jobs_halted() is not {want}"]
        if key == "budget_turns_used_after":
            used = budget.default_budget().snapshot()["turns_used"]
            return [] if used == want else [f"budget turns_used is {used}, want {want}"]
        if key == "trace":
            hits = [r for r in ring if not subset(r, want)]
            return [] if hits else [f"no trace record matches {want}"]
        if key == "ring":
            tail = [r for r in ring if r.get("record_type") == "event" and r.get("origin") == "edge"][-len(want):]
            problems: list[str] = []
            if len(tail) < len(want):
                return [f"ring has {len(tail)} edge events, want {len(want)}"]
            for i, w in enumerate(want):
                problems += subset(tail[i], w, f"ring[{i}]")
            return problems
        return [f"edge_only check {key!r} is not implemented by the runner"]

    def run(self, case: dict) -> list[str]:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        problems = [f"[step 0] {p}" for p in self.check(self.send(case["request"], now), case["expect"], now)]
        for i, step in enumerate(case.get("followups", []), 1):
            problems += [f"[step {i}] {p}" for p in self.check(self.send(step["request"], now), step["expect"], now)]
        return problems


def host_edge_routes_app():
    from arail.portal.app import app
    return app
