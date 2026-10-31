"""The live check's driver and wrapper (step 8 of sprints/2026-10-29-arail-deep-honest).

The check itself needs the real wheel and the real model, so it is not part of pytest. What is
tested here is everything around it: the teaching on every number (TQ4, second half), the exit
rule, the wrapper's isolation, the detectors, and a dry run of all nine rows against the fake
runtime. The dry run is also the proof that the check can fail: a good wheel and a stale verdict
both make it exit 1.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes.fake_deep_runtime import TEMPLATE, _NATIVE_BYTES, FakeDeep

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "scripts" / "deep_honest_win.py"
WRAPPER = REPO / "scripts" / "deep-honest-win.sh"


def _load_driver():
    spec = importlib.util.spec_from_file_location("deep_honest_win", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["deep_honest_win"] = mod            # dataclasses look the module up by name
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True                   # no scripts/__pycache__ from a test
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    return mod


w = _load_driver()
PARTS = ("meaning", "threshold", "out_of_range", "failure_lives_in")


def result(row, status="PASS", attempted=None, failed=None, checks=()):
    r = w.RowResult(row=row, status=status, attempted=attempted, failed=failed)
    for ok, silent in checks:
        r.check("a check", ok, "what it saw", "what it wanted", silent=silent)
    return r


def all_pass():
    return [result(row) for row in w.ROW_IDS]


# ── TQ4, second half: every row has a filled teaching cell for each number ─────

def test_tq4_every_row_has_four_filled_parts_for_each_number():
    assert set(w.BARS) == set(w.ROW_IDS) == set(w.TITLES) == set(w.SHORT)
    for row in w.ROW_IDS:
        t = w.teaching_for(row)
        assert set(t) == set(w.NUMBERS), row
        for num in w.NUMBERS:
            assert set(t[num]) == set(PARTS), (row, num)
            for part in PARTS:
                assert len(t[num][part].split()) >= 4, (row, num, part, "a real sentence, not a placeholder")


def test_tq4_the_bar_is_per_row_and_the_rest_is_shared():
    bars = {row: w.teaching_for(row)["attempted"]["threshold"] for row in w.ROW_IDS}
    assert len(set(bars.values())) == len(bars), "each row states its own bar for the number it shows"
    for num in w.NUMBERS:
        shown = [row for row in w.ROW_IDS if not isinstance(w.BARS[row][num], tuple)]
        assert shown, num
        for part in ("meaning", "out_of_range", "failure_lives_in"):
            assert len({w.teaching_for(r)[num][part] for r in shown}) == 1, (num, part)


def test_tq4_a_number_that_is_not_counted_says_why_in_all_four_parts():
    t = w.teaching_for("W7")["attempted"]
    assert t["meaning"] == "not counted in this row"
    assert "subprocess" in t["threshold"] and "not applicable" in t["out_of_range"]
    assert "not applicable" in t["failure_lives_in"]


def test_tq4_the_report_teaches_every_number_it_shows():
    rows = all_pass()
    rows[1] = result("W1", attempted=5, failed=4)
    text = "\n".join(w.render_table(rows) + w.render_teaching(rows))
    assert "deep calls attempted = 5" in text and "deep calls failed = 4" in text
    for row in w.ROW_IDS:
        block = " ".join(text.split("%s  %s" % (row, w.TITLES[row]), 1)[1].split())     # undo the wrapping
        for num in w.NUMBERS:
            for part in PARTS:
                first_words = " ".join(w.teaching_for(row)[num][part].split()[:3])
                assert first_words in block, (row, num, part)
    assert "what it is:" in text and "the bar and why:" in text
    assert "out of range:" in text and "the fault lives in:" in text


def test_tq4_the_verdict_and_the_total_time_are_taught_too():
    code, lines = w.render_verdict(all_pass(), 187.2)
    text = "\n".join(lines)
    assert code == 0 and "9 of 9 rows passed" in text and "total time 187 s" in text
    assert "the bar and why: all of them" in text
    for part in ("what it is", "the bar and why", "out of range", "the fault lives in"):
        assert text.count(part + ":") >= 2, part                    # once for the verdict, once for the time


# ── the exit rule and the counts ──────────────────────────────────────────────

@pytest.mark.parametrize("status,code", [(w.PASS, 0), (w.FAIL, 1), (w.INCONCLUSIVE, 1), (w.NOT_RUN, 1)])
def test_exit_is_zero_only_when_every_row_passes(status, code):
    rows = all_pass()
    rows[4] = result("W4", status=status)
    got, lines = w.render_verdict(rows, 10.0)
    assert got == code
    if code:
        assert "W4 %s" % status in "\n".join(lines)


def test_a_partial_run_does_not_claim_the_whole_check_passed():
    _, lines = w.render_verdict([result("W5"), result("W6")], 5.0)
    text = "\n".join(lines)
    assert "2 of 2 rows passed" in text and "partial run" in text
    assert "Every surface said failing" not in text


def test_silent_outcomes_count_only_failed_silent_checks():
    r = result("W1", checks=[(True, True), (False, True), (False, True), (False, False)])
    assert r.silent == 2, "a failed check that is not a silent kind is a failure, not a silent outcome"
    rows = all_pass()
    rows[1] = r
    row = [ln for ln in w.render_table(rows) if ln.startswith("W1")][0]
    assert row.split()[-2] == "2"


def test_a_row_that_did_not_run_shows_dashes_and_still_teaches():
    rows = all_pass()
    rows[3] = w.RowResult(row="W3", status=w.NOT_RUN, reason="not run: W0 is FAIL")
    line = [ln for ln in w.render_table(rows) if ln.startswith("W3")][0]
    assert line.split()[-5:-2] == ["-", "-", "-"] and line.endswith("NOT RUN")
    assert "deep calls attempted = -" in "\n".join(w.render_teaching(rows))


def test_the_summary_json_carries_the_teaching_of_every_row():
    doc = w.summary_json(all_pass(), 12.3, 0)
    assert doc["schema"] == "arail.deep-honest-win/v1" and doc["exit_code"] == 0
    for row in doc["rows"]:
        assert set(row["teaching"]) == set(w.NUMBERS) and row["title"]
        assert all(all(row["teaching"][n][p].strip() for p in PARTS) for n in w.NUMBERS)
    assert set(doc["total_time_teaching"]) == set(PARTS)


# ── the detectors can fire ────────────────────────────────────────────────────

def _git_show(path):
    p = subprocess.run(["git", "-C", str(REPO), "show", "62d5ebdf:" + path], capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip("the base commit 62d5ebdf is not in this clone")
    return p.stdout


def test_the_false_claim_detectors_fire_on_the_old_build_script_and_not_on_the_new_one():
    old, new = _git_show("scripts/build-aerollm.sh"), (REPO / "scripts" / "build-aerollm.sh").read_text(encoding="utf-8")
    literal = [(label, rx) for label, rx in w.FORBIDDEN if not label.startswith("a channel")]
    assert len(literal) == 4
    for label, rx in literal:
        assert rx.search(old), "the old script printed " + label
        assert not rx.search(new), "the new script must not contain " + label
    chan = dict(w.FORBIDDEN)["a channel: line that says release or dev"]
    assert chan.search("    channel:      release\n") and chan.search("    channel:      dev\n")
    assert not chan.search("    channel:      unknown — no install record\n")
    assert not chan.search("    channel:      pip (aerollm-api 0.1.0rc2, editable)\n")


def test_the_manifest_sees_a_changed_byte_a_new_file_and_a_touch(tmp_path):
    site = tmp_path / "site-packages"
    pkg = site / "aerollm_api"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("x = 1\n")
    (pkg / "aerollm_api.abi3.so").write_bytes(b"\x00" * 64)
    (site / "aerollm_api-0.1.0.dist-info").mkdir()
    (site / "aerollm_api-0.1.0.dist-info" / "RECORD").write_text("a\n")
    (site / "numpy").mkdir()
    (site / "numpy" / "x.py").write_text("n\n")
    base_m, base_l = w.site_manifest(site)
    assert set(base_m) == {"aerollm_api/__init__.py", "aerollm_api/aerollm_api.abi3.so",
                           "aerollm_api-0.1.0.dist-info/RECORD"}, "only aerollm and queuellm entries are recorded"
    assert "numpy" in base_l, "but the whole top level is listed"
    assert w.site_manifest(site) == (base_m, base_l)
    (pkg / "aerollm_api.abi3.so").write_bytes(b"\x00" * 63 + b"\x01")
    assert w.site_manifest(site)[0] != base_m, "one changed byte"
    (pkg / "aerollm_api.abi3.so").write_bytes(b"\x00" * 64)
    os.utime(pkg / "aerollm_api.abi3.so", (1, 1))
    assert w.site_manifest(site)[0] != base_m, "a touch is a change"
    (site / "aerollm_api.abi3.so").write_bytes(b"shadow")
    assert "aerollm_api.abi3.so" in w.site_manifest(site)[0], "a new top-level file"
    (site / "newpkg").mkdir()
    assert w.site_manifest(site)[1] != base_l, "a new top-level entry shows in the listing"


# ── the wrapper: isolation, the four keys, clean-up, exit codes ───────────────

HELPER = '''
import json, os, sys
a = sys.argv[1:]
env_from = a[a.index("--env-from") + 1]
root = a[a.index("--root") + 1]
json.dump({"argv": a, "env_file": open(env_from).read(), "env": dict(os.environ),
           "env_mode": oct(os.stat(env_from).st_mode & 0o777), "root_mode": oct(os.stat(root).st_mode & 0o777),
           "root": root}, open(sys.argv[0] + ".log", "w"))
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''


@pytest.fixture()
def stub(tmp_path):
    helper = tmp_path / "helper.py"
    helper.write_text(HELPER)
    sh = tmp_path / "stubpython"
    sh.write_text('#!/bin/bash\nexec "%s" "%s" "$@"\n' % (sys.executable, helper))
    sh.chmod(0o755)
    src = tmp_path / "operator.env"
    src.write_text("# the operator's .env\nARAIL_PASSWORD=hunter2\nOPENAI_API_KEY=sk-secret\nLAB_TIER=maximus\n"
                   "AEROLLM_MODEL=Qwen2.5-7B-Instruct-4bit\nARAIL_MODELS_DIR=/models\nAEROLLM_KV_BUDGET_PCT=0.60\n"
                   "MODEL_BACKEND=ollama_native\n")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "home").mkdir()
    return type("S", (), {"sh": sh, "src": src, "log": Path(str(helper) + ".log"), "tmp": tmp_path})


def run_wrapper(stub, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(stub.tmp / "home"), "TMPDIR": str(stub.tmp / "tmp"),
           "ARAIL_WIN_PYTHON": str(stub.sh), "ARAIL_WIN_ENV_FROM": str(stub.src),
           "ARAIL_WIN_QUEUELLM": str(stub.tmp / "queuellm"),
           "ARAIL_CANARY": "do-not-leak", "AERO_RING_DEPTH": "9", "MODEL_NAME": "operator-model"}
    env.update(extra)
    return subprocess.run(["bash", str(WRAPPER), "--only", "W5"], env=env, capture_output=True, text=True, timeout=60)


def test_the_wrapper_copies_exactly_the_four_keys_and_nothing_else(stub):
    p = run_wrapper(stub)
    assert p.returncode == 0, p.stderr
    log = json.loads(stub.log.read_text())
    keys = sorted(line.split("=", 1)[0] for line in log["env_file"].splitlines())
    assert keys == sorted(["AEROLLM_KV_BUDGET_PCT", "AEROLLM_MODEL", "ARAIL_MODELS_DIR", "LAB_TIER"])
    assert "hunter2" not in log["env_file"] and "sk-secret" not in log["env_file"]
    assert "ollama_native" not in log["env_file"], "the fast tier is not copied from the operator's .env"
    assert log["env_mode"] == "0o600" and log["root_mode"] == "0o700"


def test_the_wrapper_runs_the_driver_with_no_inherited_environment(stub):
    p = run_wrapper(stub)
    assert p.returncode == 0, p.stderr
    log = json.loads(stub.log.read_text())
    env = log["env"]
    assert "ARAIL_CANARY" not in env and "AERO_RING_DEPTH" not in env and "MODEL_NAME" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["HOME"] == log["root"] + "/home" and env["TMPDIR"] == log["root"] + "/tmp"
    argv = log["argv"]
    assert argv[argv.index("--python") + 1] == str(stub.sh), "the rows run under the python it was pointed at"
    assert argv[argv.index("--queuellm") + 1] == str(stub.tmp / "queuellm")
    assert argv[argv.index("--repo") + 1] == str(REPO) and argv[-2:] == ["--only", "W5"]


def test_the_wrapper_removes_its_scratch_root(stub):
    p = run_wrapper(stub)
    assert p.returncode == 0, p.stderr
    root = Path(json.loads(stub.log.read_text())["root"])
    assert not root.exists(), "the scratch root is removed when the run ends"
    assert not list((stub.tmp / "tmp").glob("arail-deep-honest.*"))


def test_the_wrapper_keeps_the_root_only_when_asked(stub):
    p = run_wrapper(stub, ARAIL_WIN_KEEP="1")
    root = Path(json.loads(stub.log.read_text())["root"])
    try:
        assert root.is_dir() and "scratch root kept" in p.stderr
        assert (root / "env.scratch").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_wrapper_passes_a_failing_exit_code_on(stub, tmp_path):
    failing = tmp_path / "failpython"
    failing.write_text('#!/bin/bash\nexit 3\n')
    failing.chmod(0o755)
    p = run_wrapper(stub, ARAIL_WIN_PYTHON=str(failing))
    assert p.returncode == 3
    assert not list((tmp_path / "tmp").glob("arail-deep-honest.*")), "the root is removed on failure too"


def test_the_wrapper_refuses_a_missing_python_or_env_before_making_anything(stub, tmp_path):
    p = run_wrapper(stub, ARAIL_WIN_PYTHON=str(tmp_path / "no-such-python"))
    assert p.returncode == 2 and "ARAIL_WIN_PYTHON" in p.stderr
    p = run_wrapper(stub, ARAIL_WIN_ENV_FROM=str(tmp_path / "no-such.env"))
    assert p.returncode == 2 and "ARAIL_WIN_ENV_FROM" in p.stderr
    assert not list((tmp_path / "tmp").glob("arail-deep-honest.*"))


# ── a dry run of all nine rows against the fake runtime ───────────────────────

MODEL = "Qwen2.5-7B-Instruct-4bit"          # a catalog name: Chat checks the size of the model


def make_tree(root: Path) -> dict:
    pkg_dir = root / "fakepkg"
    package = pkg_dir / "aerollm_api"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    native = package / "_native.abi3.so"
    native.write_bytes(_NATIVE_BYTES)
    models = root / "models"
    (models / MODEL).mkdir(parents=True)
    (models / MODEL / "config.json").write_text('{"fake": true}\n', encoding="utf-8")
    (models / MODEL / "weights.bin").write_bytes(b"\0" * 2048)
    fd = FakeDeep(root=root, pkg_dir=pkg_dir, package=package, native_path=native,
                  count_file=root / "calls.jsonl", models_dir=models, model_name=MODEL)
    fd.with_tokenizer()
    crate = root / "queuellm" / "crates" / "queuellm-api"       # the renamed crate: the build script must refuse it
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname = 'queuellm-api'\n")
    env = root / "env.scratch"
    env.write_text("LAB_TIER=maximus\nAEROLLM_MODEL=%s\nARAIL_MODELS_DIR=%s\nAEROLLM_KV_BUDGET_PCT=0.60\n"
                   % (MODEL, models))
    return {"pkg_dir": pkg_dir, "sha": fd.sha256, "env": env, "sibling": root / "queuellm",
            "count": root / "calls.jsonl"}


def run_driver(tree, tmp, name, mode, *extra):
    root = tmp / name
    root.mkdir()
    out = tmp / (name + ".json")
    cmd = [sys.executable, str(DRIVER), "--root", str(root), "--repo", str(REPO), "--python", sys.executable,
           "--env-from", str(tree["env"]), "--queuellm", str(tree["sibling"]), "--expect-sha256", tree["sha"],
           "--expect-version", "0.0.0-fake", "--pythonpath-prepend", str(tree["pkg_dir"]),
           "--extra-env", "ARAIL_FAKE_DEEP_MODE=" + mode, "--extra-env", "ARAIL_FAKE_DEEP_COUNT_FILE=" + str(tree["count"]),
           "--extra-env", "AEROLLM_RING_DEPTH=2", "--json-out", str(out), *extra]
    env = {"PATH": os.environ["PATH"], "HOME": str(root / "home-outer"), "TMPDIR": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    doc = json.loads(out.read_text()) if out.exists() else None
    return type("Run", (), {"p": p, "doc": doc, "rows": {r["row"]: r for r in (doc or {}).get("rows", [])}})


def git_porcelain():
    return subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all"],
                          capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def dry(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("win")
    tree = make_tree(tmp / "tree")
    before = git_porcelain()
    good = run_driver(tree, tmp, "good", "fail2")
    after = git_porcelain()
    return type("Dry", (), {"tree": tree, "tmp": tmp, "good": good, "git_before": before, "git_after": after})


def test_dry_run_a_broken_wheel_is_reported_honestly_on_every_row(dry):
    g = dry.good
    assert g.p.returncode == 0, g.p.stdout[-4000:] + g.p.stderr[-1500:]
    assert {r: g.rows[r]["status"] for r in w.ROW_IDS} == {r: "PASS" for r in w.ROW_IDS}
    assert [g.rows[r]["silent"] for r in w.ROW_IDS] == [0] * 9
    nums = {r: (g.rows[r]["attempted"], g.rows[r]["failed"]) for r in w.ROW_IDS}
    assert nums["W0"] == (0, 0) and nums["W1"] == (5, 4) and nums["W2"] == (2, 1)
    assert nums["W3"] == (6, 5) and nums["W5"] == (5, 4) and nums["W6"] == (0, 0)
    assert nums["W4"][0] >= 2 and nums["W4"][1] == nums["W4"][0] - 1
    assert nums["W7"] == (None, None) and nums["W8"] == (None, None)


def test_dry_run_prints_the_table_and_teaches_every_number(dry):
    out = dry.good.p.stdout
    for part in ("SUMMARY", "WHAT THE NUMBERS MEAN", "RESULT", "total time"):
        assert part in out
    assert out.count("deep calls attempted =") == 9 and out.count("silent outcomes =") == 9
    assert "9 of 9 rows passed. Every surface said failing and nothing said deep was fine." in out
    for row in dry.good.doc["rows"]:
        assert all(all(row["teaching"][n][p].strip() for p in PARTS) for n in w.NUMBERS)


def test_dry_run_every_row_checked_what_it_claims(dry):
    rows = dry.good.rows

    def names(row):
        return {c["name"] for c in rows[row]["checks"]}
    for row in ("W1", "W2", "W3", "W4", "W6"):
        for surface in ("explain(foreground=True)", "explain(foreground=False)", "GET /api/research/status (truth strip)",
                        "GET /api/models/state (tier 1)", "POST /api/models/health/refresh (Check), then GET",
                        "./arailctl deep status", "boot line 'Deep model loaded at boot'"):
            assert surface in names(row), (row, surface)
    assert "every fast call after the failure carries the deep_failing stamp" in names("W2")
    assert "no 'All clear' in the activity log" in names("W3")
    assert "no 'evolved via' line in the activity log" in names("W4")
    assert "terms.json, manifest.json and evolution.json are unchanged" in names("W4")
    assert "passed 1 of 5" in names("W5") and "the binary is the day-1 wheel (sha256)" in names("W5")
    assert any(n.startswith("the old false claim is gone") for n in names("W7"))
    assert "no probe child process is left" in names("W8")


def test_dry_run_leaves_the_checkout_and_the_python_untouched(dry):
    assert dry.git_before == dry.git_after, "the check wrote a tracked or untracked file into the checkout"
    assert not list(dry.tree["pkg_dir"].rglob("__pycache__")), "PYTHONDONTWRITEBYTECODE reached the rows"
    roots = [p for p in dry.tmp.iterdir() if p.name == "good"]
    assert roots and (roots[0] / "W5" / "deep" / "verdict.json").exists(), "the verdict went to the scratch folder"
    mode = (roots[0] / "W5" / "deep").stat().st_mode & 0o777
    assert mode == 0o700


def test_dry_run_the_rows_get_separate_state_but_w6_and_w7_read_w5s(dry):
    root = dry.tmp / "good"
    for row in ("W1", "W2", "W3", "W4", "W5"):
        assert (root / row / "deep" / "verdict.json").exists(), row
    for row in ("W6", "W7", "W8"):
        assert not (root / row / "deep").exists(), row + " reads W5's folder and writes nothing of its own"


def test_dry_run_a_good_wheel_is_not_reported_as_broken(dry):
    run = run_driver(dry.tree, dry.tmp, "goodwheel", "pass", "--only", "W1,W5")
    assert run.p.returncode == 1, "a wheel that answers every call must not satisfy a check built for the broken one"
    assert run.rows["W1"]["status"] == "FAIL" and run.rows["W5"]["status"] == "FAIL"
    failed = {c["name"] for c in run.rows["W1"]["checks"] if not c["ok"]}
    assert "Chat turn 2 says it failed" in failed
    assert any(c["name"] == "passed 1 of 5" and not c["ok"] for c in run.rows["W5"]["checks"])


def test_dry_run_a_surface_that_says_passing_is_a_silent_outcome(dry):
    """W5 on a good wheel records a passing verdict; W6 then reads it. Every surface says the wheel is
    fine, which is exactly what the check must call silent when the wheel is meant to be failing."""
    run = run_driver(dry.tree, dry.tmp, "stale", "pass", "--only", "W5,W6")
    w6 = run.rows["W6"]
    assert w6["status"] == "FAIL" and run.p.returncode == 1
    assert w6["silent"] >= 6, [c["name"] for c in w6["checks"] if not c["ok"]]
    bad = {c["name"] for c in w6["checks"] if not c["ok"]}
    assert "explain(foreground=True)" in bad and "GET /api/models/state (tier 1)" in bad
    assert "./arailctl deep status" in bad


def test_dry_run_w6_and_w7_need_w5_and_say_so(dry):
    run = run_driver(dry.tree, dry.tmp, "alone", "fail2", "--only", "W6,W7")
    assert run.p.returncode == 1
    for row in ("W6", "W7"):
        assert run.rows[row]["status"] == "INCONCLUSIVE" and "W5 left no verdict" in run.rows[row]["reason"]


def test_dry_run_a_stopped_w0_stops_the_wheel_rows_but_not_the_postflight(dry, tmp_path):
    bad_env = tmp_path / "env.scratch"
    bad_env.write_text("LAB_TIER=maximus\n")                       # three of the four keys are missing
    tree = dict(dry.tree, env=bad_env)
    run = run_driver(tree, dry.tmp, "nokeys", "fail2")
    assert run.p.returncode == 1
    assert run.rows["W0"]["status"] == "INCONCLUSIVE" and "AEROLLM_MODEL" in run.rows["W0"]["reason"]
    assert [run.rows[r]["status"] for r in ("W1", "W2", "W3", "W4", "W5", "W6", "W7")] == ["NOT RUN"] * 7
    assert run.rows["W8"]["status"] == "INCONCLUSIVE", "W8 has no W0 manifest to compare with, and says so"
