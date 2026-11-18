"""Harness integration against a fake loopback Ollama (T-H1..T-H9, T-H11), plus pure unit checks.

The stub is a threaded HTTP server: no real model, no network beyond 127.0.0.1."""
from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arail.brain_bench import metrics, report, run as R, tasks as T, verdict

W_LLAMA, W_QWEN, W_CTRL = "a1" * 32, "b2" * 32, "c3" * 32
FAR = (datetime.now(timezone.utc) + timedelta(days=3000)).isoformat()


class Stub:
    def __init__(self, thinking=("qwen3:1.7b", "qkz-aiml-qwen3-1.7b:control-merge"), context=8192):
        self.tags = {"llama-ai-eng:latest": W_LLAMA, "qwen3:1.7b": W_QWEN,
                     "qkz-aiml-qwen3-1.7b:control-merge": W_CTRL}
        self.thinking = set(thinking)
        self.context = context
        self.loaded = {"llama-ai-eng:latest": FAR, "unrelated:1b": FAR}
        self.requests = []
        self.chat_bodies = []
        self.flip_after = None       # (tag, n): serve other weights after n chats on that tag
        self.slow_s = 0.0
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                stub.requests.append(("GET", self.path, None))
                if self.path == "/api/version":
                    return self._send({"version": "0.40.11"})
                if self.path == "/api/tags":
                    return self._send({"models": [
                        {"name": n, "model": n, "digest": "m-" + w[:8],
                         "details": {"family": "llama" if n.startswith("llama") else "qwen3", "parameter_size": "1.2B",
                                     "quantization_level": "Q8_0", "runner": "ggml"}} for n, w in stub.tags.items()]})
                if self.path == "/api/ps":
                    return self._send({"models": [{"name": n, "model": n, "expires_at": e, "size_vram": 1,
                                                   "context_length": stub.context} for n, e in stub.loaded.items()]})
                self._send({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                stub.requests.append(("POST", self.path, body))
                tag = body.get("model")
                if self.path == "/api/show":
                    w = stub.tags.get(tag)
                    if w is None:
                        return self._send({"error": "not found"}, 404)
                    caps = ["completion"] + (["thinking"] if tag in stub.thinking else [])
                    return self._send({"modelfile": f"FROM /Users/someone/.ollama/blobs/sha256-{w}\n", "template": "t",
                                       "parameters": "", "capabilities": caps, "model_info": {}})
                if self.path == "/api/generate":
                    if body.get("keep_alive") == 0:
                        stub.loaded.pop(tag, None)
                    else:
                        stub.loaded[tag] = FAR if body.get("keep_alive") == -1 else \
                            (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                    return self._send({"done": True})
                if self.path == "/api/chat":
                    stub.chat_bodies.append(body)
                    n = sum(1 for b in stub.chat_bodies if b["model"] == tag)
                    if stub.flip_after and stub.flip_after[0] == tag and n > stub.flip_after[1]:
                        stub.tags[tag] = "ff" * 32
                    time.sleep(stub.slow_s)
                    stub.loaded[tag] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                    fmt = body.get("format")
                    if isinstance(fmt, dict):
                        enum = fmt["properties"]["cite"].get("items", {}).get("enum") or []
                        text = json.dumps({"kind": "answer" if enum else "abstain", "answer": "ok",
                                           "cite": enum[:1], "propose": "none"})
                    else:
                        text = "It removes one component to measure how much it contributes."
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for part in (text[: len(text) // 2], text[len(text) // 2:]):
                        self.wfile.write((json.dumps({"message": {"content": part}, "done": False}) + "\n").encode())
                    self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                                                  "eval_count": 7, "prompt_eval_count": 90}) + "\n").encode())
                    return
                self._send({}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def stub(monkeypatch):
    s = Stub()
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1")
    monkeypatch.setenv("OLLAMA_PORT", str(s.port))
    yield s
    s.close()


def opts(tmp_path, **kw):
    o = {"baseline": "llama-ai-eng:latest", "candidate": "qkz-aiml-qwen3-1.7b:control-merge",
         "candidate_label": "UNTRAINED stand-in", "reference": "qwen3:1.7b",
         "near_aa": ["qwen3:1.7b", "qkz-aiml-qwen3-1.7b:control-merge"], "out": str(tmp_path / "run"),
         "max_minutes": 5.0, "limit_per_category": 1}
    o.update(kw)
    return o


def load(tmp_path):
    return json.loads((tmp_path / "run" / "results.json").read_text())


def test_two_item_run_end_to_end_isolation_and_residency(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-reach-the-child")
    lab = T.REPO_ROOT / "lab" / "pkb"
    before = lab.stat().st_mtime if lab.exists() else None
    rc = R.run(opts(tmp_path))
    res = load(tmp_path)
    assert rc in (R.EXIT_OK, R.EXIT_UNTRUSTED)
    assert res["schema"] == "arail.buddy_brain_ab/v1" and res["subset"] == 1 and res["complete"] is True
    assert (tmp_path / "run" / "rows.jsonl").exists() and (tmp_path / "run" / "report.md").exists()
    assert res["verdict"]["printed"] is False and "subset" in res["verdict"]["withheld_reason"]
    # T-H5: scrubbed child env and an isolated root
    keys = res["isolation"]["env_keys"]
    assert "OPENAI_API_KEY" not in keys and not any(k.endswith("_API_KEY") for k in keys)
    assert res["isolation"]["lab_mode"] == "airgapped" and "brain-bench-" in res["isolation"]["lab_root"]
    sc = {c["id"]: c for c in res["self_checks"]}
    assert sc["SC8"]["passed"] is True, sc["SC8"]
    assert sc["SC5"]["passed"] is True and sc["SC6"]["passed"] is True and sc["SC3"]["passed"] is True
    if before is not None:
        assert lab.stat().st_mtime == before
    # nothing but loopback chat/show/tags/ps/generate/version was used: no pull, no create
    paths = {p for _m, p, _b in stub.requests}
    assert paths <= {"/api/version", "/api/tags", "/api/show", "/api/ps", "/api/chat", "/api/generate"}
    # think=false only for thinking-capable models; greedy sampling on every call
    for b in stub.chat_bodies:
        if b["model"] in stub.thinking:
            assert b["think"] is False
        else:
            assert "think" not in b
        assert b["options"]["temperature"] == 0 and b["options"]["seed"] == R.SEED
        assert b["messages"][0]["role"] == "system" and "BUDDY-TURN" not in b["messages"][0]["content"]
    arms = {a["id"]: a for a in res["arms"]}
    assert arms["reference"]["think_sent"] is False and arms["baseline"]["think_sent"] is None
    # T-H7: residency restored: the pinned model is pinned again, the arms the run loaded are gone
    assert res["ollama"]["restored"] is True
    assert set(stub.loaded) == {"llama-ai-eng:latest", "unrelated:1b"}
    assert stub.loaded["llama-ai-eng:latest"] == FAR


def test_t_h2_non_loopback_host_refuses_before_any_request(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5")

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert R.run(opts(tmp_path)) == R.EXIT_REFUSED


def test_t_h1_edited_tasks_file_refuses(stub, tmp_path):
    bad = tmp_path / "tasks.jsonl"
    shutil.copy(T.TASKS_PATH, bad)
    bad.write_text(bad.read_text().replace("What is", "Whats", 1))
    assert R.run(opts(tmp_path, tasks=bad)) == R.EXIT_REFUSED
    assert not stub.chat_bodies


def test_missing_arm_and_pending_pin_refuse(stub, tmp_path):
    assert R.run(opts(tmp_path, reference="nope:1b")) == R.EXIT_REFUSED
    assert R.run(opts(tmp_path, candidate="pin:qkz-aiml-qwen3-1.7b")) == R.EXIT_REFUSED
    assert not stub.chat_bodies


def test_t_h3_digest_changing_mid_block_fails_sc5(stub, tmp_path):
    stub.flip_after = ("qwen3:1.7b", 5)   # after the 3 calibration calls, inside the first block
    rc = R.run(opts(tmp_path))
    sc = {c["id"]: c for c in load(tmp_path)["self_checks"]}
    assert sc["SC5"]["passed"] is False and rc == R.EXIT_UNTRUSTED
    assert "NO VERDICT" in (tmp_path / "run" / "report.md").read_text()


def test_t_h6_small_context_fails_sc5(stub, tmp_path):
    stub.context = 2048
    assert R.run(opts(tmp_path)) == R.EXIT_UNTRUSTED
    sc = {c["id"]: c for c in load(tmp_path)["self_checks"]}
    assert sc["SC5"]["passed"] is False and "context" in sc["SC5"]["detail"]


def test_t_h9_estimate_over_the_cap_refuses(stub, tmp_path):
    stub.slow_s = 0.05
    assert R.run(opts(tmp_path, max_minutes=0.0005)) == R.EXIT_REFUSED
    assert not (tmp_path / "run" / "results.json").exists()


def test_dry_run_sends_nothing_to_a_model(stub, tmp_path):
    assert R.run(opts(tmp_path, dry_run=True, limit_per_category=None)) == R.EXIT_OK
    assert not stub.chat_bodies


# ── pure checks (no subprocess, no Ollama) ───────────────────────────────────

def _runner(rows):
    r = R.Runner.__new__(R.Runner)
    r.o = {"near_aa": []}
    r.rows, r.checks, r.warnings, r.arm_info = rows, {}, [], {}
    return r


def _row(arm, tid, mode, ok, content="x", cat="domain"):
    g = {"checks": {"cite.correct": ok, "cite.off_grammar": False, "cite.wrong_chip": False,
                    "abstain.correct": False, "template.usable": ok, "format.leak": False},
         "format_fail": False, "pass": ok}
    return {"arm": arm, "task_id": tid, "category": cat, "mode": mode, "grade": g, "content": content, "pass": ok}


def test_t_h4_a_grade_difference_fails_sc1_and_a_byte_difference_only_warns():
    r = _runner([_row("baseline", "D-1", "structured", True, "a"), _row("aa", "D-1", "structured", True, "b")])
    sc = {c["id"]: c for c in r.self_checks([], True)}
    assert sc["SC1"]["passed"] is True and any("differ in bytes" in w for w in r.warnings)
    r = _runner([_row("baseline", "D-1", "structured", True), _row("aa", "D-1", "structured", False)])
    assert {c["id"]: c for c in r.self_checks([], True)}["SC1"]["passed"] is False


def test_t_h4_sc7_rerun_grade_diff_fails():
    r = _runner([_row("candidate", "D-1", "structured", True), _row("rerun:candidate", "D-1", "structured", False)])
    assert {c["id"]: c for c in r.self_checks([], True)}["SC7"]["passed"] is False


def test_sc6_off_grammar_cite_means_the_harness_is_broken():
    row = _row("baseline", "D-1", "structured", True)
    row["grade"]["checks"]["cite.off_grammar"] = True
    sc = {c["id"]: c for c in _runner([row]).self_checks([], True)}
    assert sc["SC6"]["passed"] is False and "harness is broken" in sc["SC6"]["detail"]


def _results(**kw):
    res = {"schema": "arail.buddy_brain_ab/v1", "run_id": "r", "git_sha": "abcdef12", "git_dirty": False, "complete": True,
           "subset": None, "wall_s": 1.0, "tasks": {"sha256": "ab" * 32},
           "rules": {"status": "proposal"}, "ollama": {"version": "0.40.11"},
           "sampling": {"seed": 1, "num_predict": {}}, "warnings": [], "failures": [], "latency": {},
           "arms": [{"id": "baseline", "role": "baseline", "label": "default", "served_model": "llama-ai-eng:latest"},
                    {"id": "candidate", "role": "candidate", "label": "mine",
                     "served_model": "qkz-aiml-qwen3-1.7b:control-merge", "thinking_capable": True}],
           "metrics": {"baseline": {"M1": {"value": 10.0, "numerator": 1, "denominator": 10, "unit": "x"}},
                       "candidate": {"M1": {"value": 20.0, "numerator": 2, "denominator": 10, "unit": "x"}}},
           "comparisons": [], "self_checks": [{"id": "SC1", "passed": True, "expected": "e", "observed": "o"}]}
    res.update(kw)
    res["verdict"] = verdict.decide(res, json.loads(T.RULES_PATH.read_text()))
    return res


RULES = json.loads(T.RULES_PATH.read_text())


def test_t_h11_a_control_merge_arm_is_always_labelled_untrained():
    md = report.render(_results(), RULES)
    row = next(line for line in md.splitlines() if line.startswith("| candidate"))
    assert "UNTRAINED stand-in: plumbing only, never evidence about the house model" in row


def test_t_h10_the_render_guard_trips_on_an_unknown_metric():
    res = _results()
    res["metrics"]["candidate"]["M99"] = {"value": 1, "numerator": 1, "denominator": None, "unit": "x"}
    with pytest.raises(metrics.UnknownMetric):
        report.render(res, RULES)


def test_a_failed_self_check_withholds_the_verdict_and_shows_the_banner():
    res = _results(self_checks=[{"id": "SC1", "passed": False, "expected": "e", "observed": "o"}])
    assert res["verdict"]["printed"] is False and "HARNESS NOT TRUSTWORTHY" in res["verdict"]["withheld_reason"]
    md = report.render(res, RULES)
    assert "HARNESS NOT TRUSTWORTHY: NO VERDICT" in md


def test_verdict_is_printed_only_when_everything_passed_on_the_full_set():
    res = _results()
    assert res["verdict"]["printed"] is True and res["verdict"]["rules_status"] == "proposal"
    assert res["verdict"]["p2"]["advice"] == "does_not_meet"        # R8/R9/R11 cannot be met by an unsealed stand-in
    assert _results(subset=3)["verdict"]["printed"] is False
    assert _results(complete=False)["verdict"]["printed"] is False
    assert _results(self_checks=[{"id": "SC2", "passed": None, "expected": "e", "observed": "o"}])["verdict"]["printed"] is False
