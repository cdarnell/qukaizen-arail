"""deep_verdict provenance and status text: the channel is proven or "unknown",
the shadowed copy is listed, every number in the generation block is taught,
and the two CLI entry points the build script calls behave.

C7 of sprints/2026-10-29-arail-deep-honest/ARCHITECTURE.md. TQ4 (first half).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from arail import deep_verdict as dv
from tests.fakes.fake_deep_runtime import fake_deep  # noqa: F401  (fixture)
from tests.test_deep_verdict import FAIL_ERR, probe_result

SRC = str(Path(dv.__file__).resolve().parents[1])


def _b64(hex_digest: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(hex_digest)).decode().rstrip("=")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


LIVE_BYTES = b"NATIVE-LIVE-2026-06-28" * 200
OLD_BYTES = b"NATIVE-OLD-2026-05-02" * 150
PIP_BYTES = b"NATIVE-AS-PIP-INSTALLED" * 180


@pytest.fixture
def live_site(tmp_path, monkeypatch):
    """A site dir that mimics the operator's venv: a package whose own native file was
    replaced outside pip, a shadowed top-level copy, and an editable pip record."""
    for name in [n for n in sys.modules if n.split(".")[0] in ("aerollm_api", "queuellm_api")]:
        monkeypatch.delitem(sys.modules, name)
    dv._reset_for_tests()
    site = tmp_path / "site"
    pkg = site / "aerollm_api"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    so = pkg / "aerollm_api.abi3.so"
    so.write_bytes(LIVE_BYTES)
    (site / "aerollm_api.abi3.so").write_bytes(OLD_BYTES)
    di = site / "aerollm_api-0.1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: aerollm-api\nVersion: 0.1.0\n")
    (di / "INSTALLER").write_text("pip\n")
    (di / "direct_url.json").write_text(json.dumps(
        {"dir_info": {"editable": True}, "url": "file:///Users/someone/src/aerollm/crates/aerollm-api"}))
    record = (f"aerollm_api/aerollm_api.abi3.so,sha256={_b64(_sha(PIP_BYTES))},{len(PIP_BYTES)}\n"
              "aerollm_api-0.1.0.dist-info/RECORD,,\n")
    (di / "RECORD").write_text(record)
    monkeypatch.syspath_prepend(str(site))
    top = types.ModuleType("aerollm_api")
    top.__file__ = str(pkg / "__init__.py")
    top.__version__ = "0.1.0-rc.2"
    native = types.ModuleType("aerollm_api.aerollm_api")
    native.__file__ = str(so)
    monkeypatch.setitem(sys.modules, "aerollm_api", top)
    monkeypatch.setitem(sys.modules, "aerollm_api.aerollm_api", native)
    yield types.SimpleNamespace(site=site, so=so, dist=di, record=di / "RECORD")
    dv._reset_for_tests()


def _lines(site) -> list[str]:
    return dv.status_lines(site_dir=site)


def _find(lines: list[str], label: str) -> str:
    for ln in lines:
        if ln.lstrip().startswith(label + ":"):
            return ln
    raise AssertionError(f"no {label!r} line in:\n" + "\n".join(lines))


# ── the live layout ────────────────────────────────────────────────────────

def test_status_for_the_live_layout(live_site):
    lines = _lines(live_site.site)
    assert all(ln.startswith("    ") for ln in lines), "four-space indent to sit under the script's lines"
    assert _find(lines, "import").endswith(f"{live_site.site}/aerollm_api/__init__.py (package)")
    assert _find(lines, "binary").endswith(str(live_site.so))
    sha = _find(lines, "sha256")
    assert _sha(LIVE_BYTES)[:12] + "…" in sha and f"{len(LIVE_BYTES)} bytes" in sha
    assert "version 0.1.0-rc.2" in sha and "modified " in sha
    sh = _find(lines, "shadowed")
    assert str(live_site.site / "aerollm_api.abi3.so") in sh
    assert _sha(OLD_BYTES)[:12] in sh and "never loaded" in sh
    ch = _find(lines, "channel")
    assert ch.split("channel:")[1].strip().startswith("unknown — pip package aerollm-api 0.1.0")
    assert "editable, file:///Users/someone/src/aerollm/crates/aerollm-api" in ch
    assert f"replaced outside pip (RECORD {_sha(PIP_BYTES)[:12]}…, file {_sha(LIVE_BYTES)[:12]}…)" in ch
    gen = _find(lines, "generation")
    assert "never tested on this binary" in gen and "an import is not a generation test" in gen


def test_the_channel_is_pip_when_the_record_hash_matches(live_site):
    live_site.so.write_bytes(PIP_BYTES)
    dv._reset_for_tests()
    ch = _find(_lines(live_site.site), "channel")
    assert "pip (aerollm-api 0.1.0, editable, file:///Users/someone/src/aerollm/crates/aerollm-api)" in ch
    assert "unknown" not in ch


def test_the_channel_is_bundled_only_when_the_marker_names_this_file(live_site):
    marker = live_site.site / "aerollm_api.bundle.json"
    marker.write_text(json.dumps({"sha256": _sha(LIVE_BYTES), "aerollm_version": "1.1.0",
                                  "aerollm_commit": "2ae56afb3f8807c5df29daccf37ffb2413966f51",
                                  "built_at": "2026-10-11T00:00:00Z"}))
    ch = _find(_lines(live_site.site), "channel")
    assert "bundled — QueueLLM 1.1.0 (2ae56af, built 2026-10-11T00:00:00Z)" in ch
    marker.write_text(json.dumps({"sha256": "f" * 64, "aerollm_version": "1.1.0"}))
    ch = _find(_lines(live_site.site), "channel")
    assert "unknown" in ch and "bundle marker is present but names a different binary" in ch
    assert "marker ffffffffffff…" in ch


def test_the_channel_is_source_build_only_when_the_build_marker_names_this_file(live_site):
    marker = live_site.site / "aerollm_api.build.json"
    marker.write_text(json.dumps({"artifact_sha256": _sha(LIVE_BYTES), "queuellm_commit": "e730d5fe" + "0" * 32,
                                  "queuellm_dirty": True, "built_at": "2026-10-29T14:00:00Z"}))
    ch = _find(_lines(live_site.site), "channel")
    assert "source build — QueueLLM commit e730d5f, dirty tree, built 2026-10-29T14:00:00Z" in ch
    marker.write_text(json.dumps({"artifact_sha256": _sha(LIVE_BYTES), "queuellm_commit": "e730d5fe" + "0" * 32,
                                  "queuellm_dirty": False, "built_at": "2026-10-29T14:00:00Z"}))
    ch = _find(_lines(live_site.site), "channel")
    assert "source build — QueueLLM commit e730d5f, built 2026-10-29T14:00:00Z" in ch, "clean says nothing extra"
    marker.write_text(json.dumps({"artifact_sha256": _sha(LIVE_BYTES), "queuellm_commit": "unknown",
                                  "queuellm_dirty": None, "built_at": "2026-10-29T14:00:00Z"}))
    ch = _find(_lines(live_site.site), "channel")
    assert "source build — QueueLLM commit unknown, tree state unknown, built 2026-10-29T14:00:00Z" in ch, \
        "a build whose tree state was never known is not shown as clean"
    marker.write_text(json.dumps({"artifact_sha256": "a" * 64}))
    ch = _find(_lines(live_site.site), "channel")
    assert "source-build marker is present but names a different binary" in ch


def test_no_record_at_all_says_so(live_site):
    import shutil
    shutil.rmtree(live_site.dist)
    ch = _find(_lines(live_site.site), "channel")
    assert ch.split("channel:")[1].strip() == "unknown — no install record"


def test_a_dist_that_does_not_list_the_file(live_site):
    live_site.record.write_text("aerollm_api-0.1.0.dist-info/RECORD,,\n")
    ch = _find(_lines(live_site.site), "channel")
    assert "does not list this file" in ch


def test_shadowed_lists_only_the_losers(live_site):
    got = dv.shadowed(str(live_site.so))
    assert [g["path"] for g in got] == [str(live_site.site / "aerollm_api.abi3.so")]
    assert got[0]["sha256"] == _sha(OLD_BYTES)
    assert dv.shadowed(None) and len(dv.shadowed(None)) == 2
    (live_site.site / "aerollm_api.abi3.so").unlink()
    assert dv.shadowed(str(live_site.so)) == []
    assert _find(_lines(live_site.site), "shadowed").split("shadowed:")[1].strip() == "none"


# ── the generation block ───────────────────────────────────────────────────

LABELS = ("Bar:", "Out of range:", "Fault lives in:")


def _gen_block(lines: list[str]) -> list[str]:
    i = next(k for k, ln in enumerate(lines) if ln.lstrip().startswith("generation:"))
    return lines[i:]


def test_generation_untested_has_no_numbers_and_says_what_to_run(live_site):
    block = _gen_block(_lines(live_site.site))
    assert len(block) == 1 and "./arailctl deep probe" in block[0]


def test_generation_failing_probe_is_taught(live_site):
    b = dv.identity()
    dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(), first_error=FAIL_ERR,
                                 at="2026-10-29T13:35:50Z"))
    block = _gen_block(_lines(live_site.site))
    head = block[0]
    assert "FAILING — 1/5 at the last probe (2026-10-29 13:35 UTC); first error at call 2:" in head
    taught = [ln for ln in block[1:] if ln.strip().startswith("1/5:")]
    assert len(taught) == 1 and all(lbl in taught[0] for lbl in LABELS)
    assert any("Only a ./arailctl deep probe that passes 5/5" in ln for ln in block)


def test_generation_passing_teaches_both_numbers(live_site):
    b = dv.identity()
    dv.record_probe(probe_result(b, "pass", passed=5, started_ts=dv._now(), at="2026-10-29T15:00:00Z",
                                 median_ok_s=21.3))
    block = _gen_block(_lines(live_site.site))
    assert "passing — 5/5 at the last probe (2026-10-29 15:00 UTC), median 21.3 s" in block[0]
    assert any(ln.strip().startswith("5/5:") and all(l in ln for l in LABELS) for ln in block)
    assert any(ln.strip().startswith("21.3 s:") and all(l in ln for l in LABELS) for ln in block)


def test_generation_failing_call_and_load_forms(live_site, tmp_path, monkeypatch):
    dv.identity()
    dv.note_call(False, error="RuntimeError: rms_norm", consumer="agent:buddy")
    block = _gen_block(_lines(live_site.site))
    assert block[0].split("generation:")[1].strip().startswith("FAILING — a deep call failed in lab 'root' (agent:buddy)")
    assert any(ln.strip().startswith("1:") and all(l in ln for l in LABELS) for ln in block), "the count is taught"
    # a fresh store, so the first failure of this binary is a load failure
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(tmp_path / "second-store"))
    dv._reset_for_tests()
    dv.identity()
    dv.note_load_failure(RuntimeError("model dir not found: /x"), persist=True, consumer="agent:buddy")
    block = _gen_block(_lines(live_site.site))
    assert "the deep model failed to load in lab 'root' (agent:buddy)" in block[0]


def test_generation_unreadable_store_and_not_saved_and_changed_on_disk(live_site, tmp_path, monkeypatch):
    dv.identity()
    store = dv.state_dir() / "verdict.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{broken")
    block = _gen_block(_lines(live_site.site))
    assert "FAILING — the verdict file is unreadable" in block[0] and str(store) in block[0]

    store.unlink()
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("ARAIL_DEEP_STATE_DIR", str(blocker / "deep"))
    dv.note_call(False, error="boom")
    block = _gen_block(_lines(live_site.site))
    assert any(ln.strip().startswith("NOT SAVED (") for ln in block)

    live_site.so.write_bytes(b"CHANGED AFTER LOAD" * 99)
    block = _gen_block(_lines(live_site.site))
    assert any("restart the lab to load the new build" in ln for ln in block)


def test_tq4_every_number_in_the_generation_block_carries_all_four_parts(live_site):
    import re
    b = dv.identity()
    dv.record_probe(probe_result(b, "fail", passed=1, started_ts=dv._now(), first_error=FAIL_ERR))
    dv.note_call(False, error="boom", consumer="agent:buddy")
    block = _gen_block(_lines(live_site.site))
    taught_prefixes = [ln.strip().split(":", 1)[0] for ln in block[1:] if all(l in ln for l in LABELS)]
    assert taught_prefixes, block
    head_numbers = re.findall(r"\b\d+/\d+\b", block[0])
    assert head_numbers and all(n in taught_prefixes for n in head_numbers)
    for ln in block[1:]:
        if "Meaning" in ln or "Bar:" in ln:
            assert all(l in ln for l in LABELS) and re.search(r"\b\d", ln.split(":", 1)[0])


def test_teach_line_has_all_four_parts_for_every_table_entry():
    for key in dv.TEACHING:
        line = dv.teach_line(key, "X")
        assert line.startswith("X: ") and all(l in line for l in LABELS)
        assert ".." not in line


# ── the CLI ────────────────────────────────────────────────────────────────

def _cli(env, *args, input_env=None):
    return subprocess.run([sys.executable, "-m", "arail.deep_verdict", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_status_with_the_fake_runtime(fake_deep):
    fd = fake_deep()
    r = _cli(fd.env(), "status")
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert _find(out, "import").endswith("aerollm_api/__init__.py (package)")
    assert fd.sha256[:12] in _find(out, "sha256") and "version 0.0.0-fake" in _find(out, "sha256")
    assert "unknown — no install record" in _find(out, "channel")
    assert "never tested" in _find(out, "generation")


def test_cli_status_when_the_runtime_is_not_importable(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC, "PYTHONDONTWRITEBYTECODE": "1",
           "ARAIL_DEEP_STATE_DIR": str(tmp_path / "deep")}
    r = _cli(env, "status")
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert "is not importable by" in _find(out, "import") and "./arailctl deep install" in _find(out, "import")
    assert _find(out, "generation").endswith("not applicable — nothing is installed")


def test_cli_identity_json_is_one_clean_line_even_when_native_code_prints(fake_deep):
    fd = fake_deep("stdout-noise")
    r = _cli(fd.env(), "identity", "--json")
    assert r.returncode == 0, r.stderr
    assert "native import banner" not in r.stdout and "native import banner" in r.stderr
    doc = json.loads(r.stdout)
    assert doc["sha256"] == fd.sha256 and doc["module"] == "aerollm_api" and doc["version"] == "0.0.0-fake"
    assert doc["native_path"] == str(fd.native_path)
    assert doc["resolves_to"].endswith("aerollm_api/__init__.py")


def test_cli_identity_json_exits_1_when_nothing_loads(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC, "PYTHONDONTWRITEBYTECODE": "1",
           "ARAIL_DEEP_STATE_DIR": str(tmp_path / "deep")}
    r = _cli(env, "identity", "--json")
    assert r.returncode == 1
    assert "error" in json.loads(r.stdout)


@pytest.mark.parametrize("args", [(), ("nope",), ("identity",), ("status", "extra")])
def test_cli_usage_errors_exit_2(args, tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC, "PYTHONDONTWRITEBYTECODE": "1",
           "ARAIL_DEEP_STATE_DIR": str(tmp_path / "deep")}
    r = _cli(env, *args)
    assert r.returncode == 2 and "usage:" in r.stderr


# ── import_target and the `target` / `channel` subcommands (used by build-aerollm.sh) ──────────────

def _site_with(tmp_path, *, package: bool, toplevel: bool, dist: bool):
    site = tmp_path / "tsite"
    site.mkdir()
    if package:
        (site / "aerollm_api").mkdir()
        (site / "aerollm_api" / "__init__.py").write_text("")
    if toplevel:
        (site / "aerollm_api.abi3.so").write_bytes(b"top-level" * 50)
    if dist:
        di = site / "aerollm_api-0.1.0.dist-info"
        di.mkdir()
        (di / "METADATA").write_text("Metadata-Version: 2.1\nName: aerollm-api\nVersion: 0.1.0\n")
        (di / "RECORD").write_text("aerollm_api/__init__.py,,\naerollm_api-0.1.0.dist-info/RECORD,,\n")
        (di / "direct_url.json").write_text(json.dumps(
            {"dir_info": {"editable": True}, "url": "file:///Users/someone/src/aerollm/crates/aerollm-api"}))
    return site


def test_import_target_is_free_when_nothing_is_found_or_the_dest_already_wins(tmp_path, monkeypatch):
    assert dv.import_target(str(tmp_path / "x" / "aerollm_api.abi3.so")) == {"free": True, "winner": None, "owner": ""}
    site = _site_with(tmp_path, package=False, toplevel=True, dist=False)
    monkeypatch.syspath_prepend(str(site))
    got = dv.import_target(str(site / "aerollm_api.abi3.so"))
    assert got["free"] is True and got["winner"] == str(site / "aerollm_api.abi3.so")


def test_import_target_names_the_owner_of_a_package_that_wins(tmp_path, monkeypatch):
    site = _site_with(tmp_path, package=True, toplevel=False, dist=True)
    monkeypatch.syspath_prepend(str(site))
    got = dv.import_target(str(site / "aerollm_api.abi3.so"))
    assert got["free"] is False and got["winner"] == str(site / "aerollm_api" / "__init__.py")
    assert got["owner"] == ("pip package aerollm-api 0.1.0, editable from "
                            "file:///Users/someone/src/aerollm/crates/aerollm-api")


def test_import_target_says_no_install_record_when_it_cannot_prove_an_owner(tmp_path, monkeypatch):
    site = _site_with(tmp_path, package=True, toplevel=False, dist=False)
    monkeypatch.syspath_prepend(str(site))
    got = dv.import_target(str(site / "aerollm_api.abi3.so"))
    assert got["free"] is False and got["owner"] == "no install record"


def test_import_target_names_a_pythonpath_entry(tmp_path, monkeypatch):
    site = _site_with(tmp_path, package=True, toplevel=False, dist=False)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setenv("PYTHONPATH", str(site))
    got = dv.import_target(str(tmp_path / "elsewhere" / "aerollm_api.abi3.so"))
    assert got["free"] is False and got["owner"] == "a PYTHONPATH entry"


def test_cli_target_prints_json_and_imports_nothing(tmp_path):
    site = _site_with(tmp_path, package=True, toplevel=False, dist=False)
    (site / "aerollm_api" / "__init__.py").write_text("raise RuntimeError('target must not import')\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{site}{os.pathsep}{SRC}", "PYTHONDONTWRITEBYTECODE": "1",
           "ARAIL_DEEP_STATE_DIR": str(tmp_path / "deep")}
    r = _cli(env, "target", str(site / "aerollm_api.abi3.so"))
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["free"] is False and doc["winner"].endswith("aerollm_api/__init__.py")


def test_cli_channel_prints_one_line(fake_deep):
    fd = fake_deep()
    r = _cli(fd.env(), "channel")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "unknown — no install record"
