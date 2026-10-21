"""QA-BLIND-1 (W4, security) — authored from ARCHITECTURE.md's contracts #2
and #5 only, without reading the builder's tests for this surface
(the ingress-spine sprint's standing held-out-authorship rule).

The contract under test, restated from the spec:

  * With the flight recorder **off** (the fresh-lab default), no ``prompt``
    or ``response`` body reaches ``DATA_DIR`` at all.
  * With it **on**, bodies are captured but pass *both* redaction passes
    first, so a planted key-shaped string appears **nowhere** under
    ``DATA_DIR`` and the record's ``redactions`` count is >= 1.
  * ``capture_body`` fails **closed**: any failure inside it yields ``None``
    (no body), never a partially-redacted body.

Every assertion here greps the *whole* ``DATA_DIR`` tree rather than the one
file the spec names, and every "absence" assertion is preceded by a
*presence* assertion (a trace record was actually written), because
``assert planted not in tree`` over an empty tree proves nothing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from arail import agent_context, agent_trace, config, redact
from arail.router.backends import BaseBackend, ModelResponse
from arail.router.core import ModelRouter

# A key-shaped string that is NOT in secrets.env — the shape pass is the
# only thing that can catch this one (ARCHITECTURE.md #5, pass 2).
PLANTED_SHAPE_KEY = "sk-QAplanted0000000000000000deadbeef"
# A value that is ONLY findable via the known-value pass (no vendor prefix,
# no `key:`-style assignment, nothing the shape patterns match).
PLANTED_OPAQUE_SECRET = "zqx7Hvv2mmQQtt41ppLLee88"


class _FakeBackend(BaseBackend):
    """Echoes a deterministic response that embeds the prompt's secret, so a
    leak can enter the tree through either the prompt or the response half."""

    def __init__(self, reply: str = "ack") -> None:
        self.reply = reply
        self.calls: list[str] = []

    def complete(self, prompt, max_tokens=512, temperature=0.7, top_p=None,
                 *, system=None, messages=None):
        self.calls.append(prompt)
        return ModelResponse(text=self.reply, model="fake-qa",
                             tokens_used=7, backend="fake",
                             latency_ms=1.0)

    def health_check(self) -> bool:
        return True


@pytest.fixture
def isolated_costs(monkeypatch, tmp_path):
    """Keep ``cost_tracker``'s un-isolated singleton out of the real lab.

    The repo conftest deliberately does not isolate ``costs.json`` (see its
    docstring); driving a real ``ModelRouter.complete()`` from a test
    therefore writes the developer's actual ``lab/data/costs.json``. Every
    test in this file drives the chokepoint, so every test needs this.
    """
    from arail.costs import cost_tracker
    monkeypatch.setattr(cost_tracker, "_data_path", tmp_path / "qa-costs.json")
    monkeypatch.setattr(cost_tracker, "calls_by_source", {})
    yield


def _router(reply: str = "ack") -> tuple[ModelRouter, _FakeBackend]:
    be = _FakeBackend(reply)
    return ModelRouter.from_backend(be, "fake"), be


def _tree_text(root: Path) -> str:
    """Every byte of every regular file under *root*, concatenated.

    The spec says "grep the whole tree, not just the file you expect" — so
    this walks, rather than opening ``agent_traces.jsonl`` by name.

    ``secrets.env`` itself is excluded: it is the secret's *legitimate*
    0600 home (README's "tokens to lab/data/secrets.env" rule), and in the
    test environment it happens to sit in the same tmp dir DATA_DIR points
    at. Everything else — traces, activity log, flight-recorder state,
    costs, notices — is in scope.
    """
    chunks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "secrets.env":
            continue
        try:
            chunks.append(f"\n--- {path} ---\n")
            chunks.append(path.read_text(errors="replace"))
        except OSError:
            continue
    return "".join(chunks)


def _trace_lines(root: Path) -> list[dict]:
    out: list[dict] = []
    for name in ("agent_traces.jsonl.1", "agent_traces.jsonl"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _body_keys_in_tree(root: Path) -> list[str]:
    """Any non-null ``prompt``/``response`` value anywhere in any JSON
    structure under *root*, reported by JSON path for the bug report."""
    hits: list[str] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("prompt", "response") and isinstance(v, str) and v:
                    hits.append(f"{where}.{k}")
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line.startswith(("{", "[")):
                continue
            try:
                walk(json.loads(line), f"{path.name}:{lineno}")
            except ValueError:
                continue
    return hits


# ---------------------------------------------------------------------------
# QA-BLIND-1a — recorder OFF is the fresh-lab default and writes no bodies
# ---------------------------------------------------------------------------

def test_recorder_off_by_default_writes_no_body_anywhere_under_data_dir(
        isolated_costs):
    root = config.DATA_DIR
    assert agent_trace.recorder_on() is False, (
        "the flight recorder must be OFF on a lab that has never flipped it "
        "(operator decision OQ1)")

    router, _ = _router(reply=f"here is the key {PLANTED_SHAPE_KEY}")
    with agent_context.agent_call("researcher"):
        router.complete(f"remember {PLANTED_SHAPE_KEY} for later")

    # Presence first: a trace really was written, so the absence assertions
    # below are about a populated tree, not an empty one.
    traces = _trace_lines(root)
    assert len(traces) == 1, f"expected exactly one trace on disk, got {traces}"
    assert traces[0]["attribution"] == "agent:researcher"
    assert traces[0]["bodies"] is None

    assert _body_keys_in_tree(root) == []
    assert PLANTED_SHAPE_KEY not in _tree_text(root)


def test_recorder_off_writes_no_body_for_a_streamed_call_either(isolated_costs):
    """The recorder-off contract has to hold on both chokepoint methods; the
    streaming half is the one that also carries TTFT bookkeeping."""
    root = config.DATA_DIR
    router, _ = _router(reply=f"streamed {PLANTED_SHAPE_KEY}")
    with agent_context.agent_call("buddy"):
        items = list(router.stream_complete(f"stream {PLANTED_SHAPE_KEY}"))

    assert items, "the fake backend must have yielded something"
    traces = _trace_lines(root)
    assert len(traces) == 1 and traces[0]["streamed"] is True
    assert traces[0]["bodies"] is None
    assert _body_keys_in_tree(root) == []
    assert PLANTED_SHAPE_KEY not in _tree_text(root)


# ---------------------------------------------------------------------------
# QA-BLIND-1b — recorder ON captures, but the planted key never lands
# ---------------------------------------------------------------------------

def test_recorder_on_captures_a_body_but_redacts_the_planted_shape_key(
        isolated_costs):
    root = config.DATA_DIR
    agent_trace.set_recorder_enabled(True)
    assert agent_trace.recorder_on() is True

    router, _ = _router(reply=f"echo {PLANTED_SHAPE_KEY}")
    with agent_context.agent_call("researcher"):
        router.complete(f"use {PLANTED_SHAPE_KEY} please")

    traces = _trace_lines(root)
    assert len(traces) == 1, f"expected one trace, got {len(traces)}"
    bodies = traces[0]["bodies"]
    # Presence first: a body really was captured...
    assert isinstance(bodies, dict), "recorder ON must capture a body"
    assert bodies["prompt"], "captured prompt must be non-empty"
    assert bodies["response"], "captured response must be non-empty"
    # ...and only then does "the key is absent" mean anything.
    assert bodies["redactions"] >= 1, bodies
    assert PLANTED_SHAPE_KEY not in _tree_text(root), (
        "a shape-matched key reached disk with the recorder on")
    assert redact.REDACTED in bodies["prompt"]


def test_recorder_on_redacts_a_value_that_is_only_in_secrets_env(
        isolated_costs, tmp_path):
    """The known-value pass, in isolation: an opaque secret with no
    recognisable shape is caught *only* because it is in secrets.env."""
    secrets_file = Path(os.environ["ARAIL_SECRETS_FILE"])
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(f"NIM_API_KEY={PLANTED_OPAQUE_SECRET}\n")
    redact._reset_cache_for_tests()

    # Control: the shape pass alone cannot see this value.
    assert PLANTED_OPAQUE_SECRET in PLANTED_OPAQUE_SECRET  # sanity, no-op
    agent_trace.set_recorder_enabled(True)
    router, _ = _router(reply="ok")
    with agent_context.agent_call("librarian"):
        router.complete(f"auth with {PLANTED_OPAQUE_SECRET}")

    traces = _trace_lines(config.DATA_DIR)
    assert len(traces) == 1
    bodies = traces[0]["bodies"]
    assert isinstance(bodies, dict) and bodies["prompt"]
    assert bodies["redactions"] >= 1
    assert PLANTED_OPAQUE_SECRET not in _tree_text(config.DATA_DIR)


def test_a_key_shaped_string_not_in_secrets_env_is_still_redacted(
        isolated_costs):
    """The distinction the brief asks for: present-in-secrets.env vs
    key-shaped-but-absent. Both must be redacted, by different passes."""
    secrets_file = Path(os.environ["ARAIL_SECRETS_FILE"])
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text("UNRELATED_KEY=somethingelseentirely\n")
    redact._reset_cache_for_tests()

    agent_trace.set_recorder_enabled(True)
    router, _ = _router(reply="ok")
    with agent_context.agent_call("researcher"):
        router.complete(f"token hf_{'a' * 24} and {PLANTED_SHAPE_KEY}")

    traces = _trace_lines(config.DATA_DIR)
    assert isinstance(traces[0]["bodies"], dict)
    assert traces[0]["bodies"]["redactions"] >= 2
    tree = _tree_text(config.DATA_DIR)
    assert PLANTED_SHAPE_KEY not in tree
    assert f"hf_{'a' * 24}" not in tree


# ---------------------------------------------------------------------------
# QA-BLIND-1c — the R1 variant: an existing-but-unreadable secrets.env
# ---------------------------------------------------------------------------

@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read a chmod 000 file, so the test "
                           "would pass vacuously")
def test_unreadable_secrets_env_fails_closed_and_captures_no_body(
        isolated_costs):
    """``chmod 000 secrets.env`` with the recorder ON.

    "Couldn't read the secret list" must never be indistinguishable from
    "the secret list is empty": the known-value pass cannot run, so
    ``capture_body`` must return ``None`` (metadata kept, body dropped)
    rather than a shape-pass-only body that still carries every opaque
    secret the file would have caught.
    """
    secrets_file = Path(os.environ["ARAIL_SECRETS_FILE"])
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(f"NIM_API_KEY={PLANTED_OPAQUE_SECRET}\n")
    os.chmod(secrets_file, 0o000)
    redact._reset_cache_for_tests()
    try:
        assert not os.access(secrets_file, os.R_OK), (
            "precondition: the file must really be unreadable")
        agent_trace.set_recorder_enabled(True)
        router, _ = _router(reply="ok")
        with agent_context.agent_call("researcher"):
            router.complete(f"auth with {PLANTED_OPAQUE_SECRET}")

        traces = _trace_lines(config.DATA_DIR)
        assert len(traces) == 1, "the metadata record must still be written"
        assert traces[0]["bodies"] is None, (
            "an unreadable secrets.env must fail CLOSED — no body at all")
        assert PLANTED_OPAQUE_SECRET not in _tree_text(config.DATA_DIR)
    finally:
        os.chmod(secrets_file, 0o600)
        redact._reset_cache_for_tests()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read a chmod 000 file")
def test_secrets_env_unreadable_is_distinguishable_from_absent(isolated_costs):
    """Three states, three behaviours — unreadable must not collapse into
    absent. Absent is legitimately "zero known values" (shape pass only);
    unreadable is a failure and must drop the body."""
    secrets_file = Path(os.environ["ARAIL_SECRETS_FILE"])
    secrets_file.parent.mkdir(parents=True, exist_ok=True)

    # (1) absent -> shape pass only, body still captured
    if secrets_file.exists():
        secrets_file.unlink()
    redact._reset_cache_for_tests()
    assert redact.capture_body("plain prompt", "plain response") is not None

    # (2) present + readable + empty -> also legitimately zero values
    secrets_file.write_text("")
    redact._reset_cache_for_tests()
    assert redact.capture_body("plain prompt", "plain response") is not None

    # (3) present + unreadable -> fail closed
    secrets_file.write_text(f"K={PLANTED_OPAQUE_SECRET}\n")
    os.chmod(secrets_file, 0o000)
    redact._reset_cache_for_tests()
    try:
        assert redact.capture_body("plain prompt", "plain response") is None
    finally:
        os.chmod(secrets_file, 0o600)
        redact._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# QA-BLIND-1d — shape-pass coverage probes the spec's own pattern list
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("planted", [
    "sk-" + "A" * 20,
    "hf_" + "b" * 24,
    "nvapi-" + "c" * 20,
    "ghp_" + "d" * 20,
    "gho_" + "e" * 20,
    "github_pat_" + "f" * 20,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "Bearer " + "g" * 24,
    "api_key = " + "h" * 12,
    "API-KEY: " + "i" * 12,
    "password=" + "j" * 12,
    "passphrase: " + "k" * 12,
])
def test_every_documented_shape_pattern_is_redacted_before_disk(planted):
    """One row per pattern in ARCHITECTURE.md #5's shape list. These run
    against ``capture_body`` directly so a failure names the pattern."""
    body = redact.capture_body(f"leading text {planted} trailing text", "")
    assert body is not None
    assert body["redactions"] >= 1, f"{planted!r} was not redacted at all"
    assert planted.split()[-1] not in body["prompt"], (
        f"{planted!r} survived into the captured prompt")


@pytest.mark.xfail(strict=True, reason=(
    "BACKLOG 'QA fix loop (TEST_REPORT.md, commit 9d0e083f)' -> QA F1: "
    "redact.py:52-54's assignment pattern requires the key name to be "
    "followed by optional whitespace then ':'/'=', so a JSON-quoted "
    '{\"api_key\": \"...\"} value is not redacted. Ruled DEBT at re-test '
    "(2026-09-22): the recorder is admin-only and off by default, which "
    "bounds it, and the pattern list is ARCHITECTURE.md #5 implemented "
    "exactly as specified -- a spec change, not a build defect. strict=True "
    "so closing it turns this red instead of passing quietly."))
def test_json_quoted_api_key_is_redacted_before_disk():
    """A JSON-shaped prompt is the ordinary case for an agent that pastes a
    config fragment or a tool response into its prompt. The assignment
    pattern must survive the quotes a real payload puts between the key
    name and the ``:``.
    """
    secret = "Zq9" + "m" * 18
    body = redact.capture_body('{"api_key": "%s"}' % secret, "")
    assert body is not None
    assert secret not in body["prompt"], (
        'a JSON-quoted {"api_key": "..."} value reached the captured body '
        "unredacted — the shape pattern requires the key name to be "
        "immediately followed by optional whitespace then ':'/'=', which a "
        "quoted JSON key never is")


def test_redaction_survives_the_truncation_boundary():
    """Redact-then-truncate (ARCHITECTURE.md #5, "Order matters"): a key
    straddling the 2000-char prompt cap must not be half-copied into the
    stored body."""
    key = "sk-" + "Z" * 40
    prompt = ("x" * 1990) + key + ("y" * 500)
    body = redact.capture_body(prompt, "")
    assert body is not None
    assert body["truncated"] is True
    assert "sk-ZZZ" not in body["prompt"], (
        "a key fragment survived at the truncation boundary")


def test_a_broken_redact_module_does_not_raise_into_an_inference(
        isolated_costs, monkeypatch):
    """ARCHITECTURE.md "Hot-path cost budget": *"When the trace write fails,
    it must never fail or slow an inference."* REVIEW.md D6 is the same
    finding for two other imports on this path, and both were guarded.

    The body-capture site does ``from arail import redact`` inside
    ``complete()``, outside any ``try``. This test makes that import fail the
    way a half-installed or shadowed module does, with the recorder on, and
    asserts the inference still returns its answer.
    """
    import sys

    import arail

    agent_trace.set_recorder_enabled(True)
    router, _ = _router(reply="ok")

    monkeypatch.delattr(arail, "redact", raising=False)
    monkeypatch.setitem(sys.modules, "arail.redact", None)

    with agent_context.agent_call("researcher"):
        resp = router.complete("nothing secret here")

    assert resp.text == "ok", (
        "observability must never be the reason an inference fails — a "
        "broken arail.redact import on the body-capture path raised into "
        "ModelRouter.complete()")


def test_a_raising_capture_body_does_not_raise_into_an_inference(
        isolated_costs, monkeypatch):
    """The same promise, one layer in: ``capture_body`` is documented as
    fail-closed, but the *call site* is what must be robust — the router
    cannot borrow its own no-raise guarantee from a leaf module's internal
    discipline."""
    import arail.redact as redact_mod
    agent_trace.set_recorder_enabled(True)

    def _boom(prompt, response):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(redact_mod, "capture_body", _boom)
    router, _ = _router(reply="ok")

    with agent_context.agent_call("researcher"):
        resp = router.complete(f"secret {PLANTED_SHAPE_KEY}")

    assert resp.text == "ok", "the inference itself must be unaffected"
    traces = _trace_lines(config.DATA_DIR)
    assert len(traces) == 1 and traces[0]["outcome"] == "ok"
    assert traces[0]["bodies"] is None
    assert PLANTED_SHAPE_KEY not in _tree_text(config.DATA_DIR)


def test_recorder_flag_is_latched_at_call_start_not_at_capture_time(
        isolated_costs):
    """F10, the safer half: flipping the recorder ON *during* a call must not
    retroactively capture that call's body."""
    agent_trace.set_recorder_enabled(False)

    class _FlipsRecorderMidCall(_FakeBackend):
        def complete(self, prompt, max_tokens=512, temperature=0.7,
                     top_p=None, *, system=None, messages=None):
            agent_trace.set_recorder_enabled(True)
            return super().complete(prompt, max_tokens, temperature, top_p,
                                    system=system, messages=messages)

    be = _FlipsRecorderMidCall("ok")
    router = ModelRouter.from_backend(be, "fake")
    with agent_context.agent_call("researcher"):
        router.complete(f"mid-flight {PLANTED_SHAPE_KEY}")

    traces = _trace_lines(config.DATA_DIR)
    assert len(traces) == 1
    assert agent_trace.recorder_on() is True, "the flip really happened"
    assert traces[0]["bodies"] is None, (
        "off->on mid-call must not capture the in-flight call")


def test_redactions_count_is_not_silently_zero_when_a_body_is_stored(
        isolated_costs):
    """Guard against the inverse vacuity: a stored body with a planted key
    and ``redactions == 0`` would mean the count is decorative."""
    agent_trace.set_recorder_enabled(True)
    router, _ = _router(reply=PLANTED_SHAPE_KEY)
    with agent_context.agent_call("browser"):
        router.complete("nothing secret here")

    traces = _trace_lines(config.DATA_DIR)
    bodies = traces[0]["bodies"]
    assert isinstance(bodies, dict)
    assert bodies["redactions"] >= 1, (
        "the response half of the body must be redacted and counted too")
    assert PLANTED_SHAPE_KEY not in _tree_text(config.DATA_DIR)


def test_no_body_survives_a_recorder_off_then_on_round_trip_after_purge(
        isolated_costs):
    """Operator decision (c): the read-gate is reversible, the purge is
    permanent. After a purge, turning the recorder back on must not
    resurrect the purged bodies from disk."""
    agent_trace.set_recorder_enabled(True)
    router, _ = _router(reply=f"resp {PLANTED_SHAPE_KEY}")
    with agent_context.agent_call("researcher"):
        router.complete(f"prompt {PLANTED_OPAQUE_SECRET}")

    assert isinstance(_trace_lines(config.DATA_DIR)[0]["bodies"], dict)

    result = agent_trace.purge_flight_recorder_bodies()
    assert result["purged"] >= 1, result
    agent_trace.set_recorder_enabled(True)

    after = _trace_lines(config.DATA_DIR)
    assert len(after) == 1, "the purge must preserve the line count"
    assert after[0]["bodies"] is None
    assert after[0]["bodies_purged"] is True
    assert agent_trace.find(after[0]["trace_id"])["bodies"] is None


def test_recorder_state_reports_lan_exposure_for_the_admin_banner(
        monkeypatch):
    """Operator decision (b): the LAN-bind x live-recorder combination has to
    be *knowable* from the recorder's own state, not only from the template.
    Both branches, because a banner that is always on is the same as no
    banner."""
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    assert agent_trace.recorder_state()["lan_exposed"] is False
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    assert agent_trace.recorder_state()["lan_exposed"] is True
