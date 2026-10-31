"""A5 - the buddy.host/v1 contract is vendored byte for byte, with a hash stamp.

ARCHITECTURE.md §5.3: Buddy owns the contract; ARAIL carries a byte-identical copy
and a PROVENANCE.json stamp (the pattern of qukaizen-site's gate.generated.ts,
INTEGRATION_AUDIT §4). `--check` is sibling-free; with the sibling present it also
compares bytes. Every drift is loud.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "sync-buddy-contract.py"
VENDORED = REPO / "src" / "arail" / "buddy_host" / "contract"
SOURCE_DIR = "buddy/src/buddy/contracts"
NO_SIBLING = {"QKZ_BUDDY_PATH": "/nonexistent/qukaizen-buddy"}


def _run(*args, env=None, cwd=REPO):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e, cwd=cwd, timeout=60)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def _fake_sibling(tmp_path, contract_bytes=b'{"contract":"buddy.host/v1"}\n', cases=None):
    """A tiny qukaizen-buddy checkout with one commit holding the contract dir."""
    root = tmp_path / "qukaizen-buddy"
    d = root / SOURCE_DIR
    (d / "conformance").mkdir(parents=True)
    (d / "buddy.host.v1.json").write_bytes(contract_bytes)
    for name, data in (cases or {"000-a.json": b'{"name":"a"}\n', "001-b.json": b'{"name":"b"}\n'}).items():
        (d / "conformance" / name).write_bytes(data)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "contract")
    return root


# ---------------------------------------------------------------------------
# the vendored copy in this repo
# ---------------------------------------------------------------------------

def test_package_exposes_the_sha256_of_the_vendored_bytes():
    from arail import buddy_host
    data = (VENDORED / "buddy.host.v1.json").read_bytes()
    assert buddy_host.CONTRACT_SHA256 == hashlib.sha256(data).hexdigest()
    assert buddy_host.CONTRACT["contract"] == "buddy.host/v1"
    stamp = json.loads((VENDORED / "PROVENANCE.json").read_text())
    assert stamp["files"]["buddy.host.v1.json"] == "sha256:" + buddy_host.CONTRACT_SHA256


def test_stamp_shape():
    stamp = json.loads((VENDORED / "PROVENANCE.json").read_text())
    assert stamp["schema"] == "arail.vendor-stamp/v1"
    assert stamp["source_repo"] == "cdarnell/qukaizen-buddy"
    assert stamp["source_dir"] == SOURCE_DIR
    assert len(stamp["source_commit"]) == 40 and set(stamp["source_commit"]) <= set("0123456789abcdef")
    assert stamp["sync"] == 'python scripts/sync-buddy-contract.py --from "$QKZ_BUDDY_PATH"'
    assert stamp["check"] == "python scripts/sync-buddy-contract.py --check"
    assert list(stamp["files"]) == sorted(stamp["files"])
    assert all(v.startswith("sha256:") and len(v) == 71 for v in stamp["files"].values())
    lines = "".join(f"{p} {v[7:]}\n" for p, v in sorted(stamp["files"].items()))
    assert stamp["tree_digest"] == "sha256:" + hashlib.sha256(lines.encode()).hexdigest()
    assert len(stamp["synced_on"]) == 10


def test_every_vendored_file_is_stamped_and_every_stamp_has_a_file():
    stamp = json.loads((VENDORED / "PROVENANCE.json").read_text())
    on_disk = {"buddy.host.v1.json"} | {f"conformance/{p.name}" for p in (VENDORED / "conformance").glob("*.json")}
    assert set(stamp["files"]) == on_disk
    assert len(on_disk) > 100


def test_the_sibling_free_check_passes_on_the_repo():
    r = _run("--check", env=NO_SIBLING)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok" in r.stdout.lower()


def test_check_against_the_real_sibling_when_it_is_present():
    default = (REPO.parent / "qukaizen-buddy")
    sibling = pathlib.Path(os.environ.get("QKZ_BUDDY_PATH") or default)
    if not (sibling / SOURCE_DIR / "buddy.host.v1.json").exists():
        pytest.skip("no qukaizen-buddy sibling checkout here")
    r = _run("--check", env={"QKZ_BUDDY_PATH": str(sibling)})
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# check: every kind of drift is caught
# ---------------------------------------------------------------------------

@pytest.fixture
def vendored_copy(tmp_path):
    dst = tmp_path / "contract"
    shutil.copytree(VENDORED, dst)
    return dst


def test_check_catches_an_edited_case(vendored_copy):
    f = next((vendored_copy / "conformance").glob("*.json"))
    f.write_bytes(f.read_bytes() + b" ")
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and f.name in r.stdout + r.stderr


def test_check_catches_an_edited_contract(vendored_copy):
    f = vendored_copy / "buddy.host.v1.json"
    f.write_bytes(f.read_bytes().replace(b"buddy.host/v1", b"buddy.host/v2", 1))
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and "buddy.host.v1.json" in r.stdout + r.stderr


def test_check_catches_a_missing_file(vendored_copy):
    f = next((vendored_copy / "conformance").glob("*.json"))
    f.unlink()
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and f.name in r.stdout + r.stderr


def test_check_catches_an_unstamped_extra_file(vendored_copy):
    (vendored_copy / "conformance" / "999-sneaky.json").write_text("{}")
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and "999-sneaky.json" in r.stdout + r.stderr


def test_check_catches_a_tampered_stamp(vendored_copy):
    stamp_path = vendored_copy / "PROVENANCE.json"
    stamp = json.loads(stamp_path.read_text())
    stamp["tree_digest"] = "sha256:" + "0" * 64
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n")
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and "tree_digest" in r.stdout + r.stderr


def test_check_catches_a_missing_stamp(vendored_copy):
    (vendored_copy / "PROVENANCE.json").unlink()
    r = _run("--check", "--vendor-dir", str(vendored_copy), env=NO_SIBLING)
    assert r.returncode == 1 and "PROVENANCE" in r.stdout + r.stderr


def test_importing_the_package_refuses_a_contract_that_does_not_match_its_stamp(tmp_path):
    bad = tmp_path / "pkg"
    shutil.copytree(REPO / "src" / "arail" / "buddy_host", bad / "buddy_host")
    f = bad / "buddy_host" / "contract" / "buddy.host.v1.json"
    f.write_bytes(f.read_bytes() + b"\n")
    code = ("import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('bh', r'{bad / 'buddy_host' / '__init__.py'}')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "try:\n    spec.loader.exec_module(m)\nexcept Exception as e:\n    print(type(e).__name__); sys.exit(0)\n"
            "sys.exit(7)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ContractStampError" in r.stdout, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def test_sync_copies_byte_for_byte_and_stamps_the_source_commit(tmp_path):
    sib = _fake_sibling(tmp_path)
    vend = tmp_path / "vend"
    r = _run("--from", str(sib), "--vendor-dir", str(vend))
    assert r.returncode == 0, r.stdout + r.stderr
    src = sib / SOURCE_DIR
    assert (vend / "buddy.host.v1.json").read_bytes() == (src / "buddy.host.v1.json").read_bytes()
    for p in (src / "conformance").glob("*.json"):
        assert (vend / "conformance" / p.name).read_bytes() == p.read_bytes()
    stamp = json.loads((vend / "PROVENANCE.json").read_text())
    assert stamp["source_commit"] == _git(sib, "rev-parse", "HEAD")
    assert set(stamp["files"]) == {"buddy.host.v1.json", "conformance/000-a.json", "conformance/001-b.json"}
    assert _run("--check", "--vendor-dir", str(vend), env=NO_SIBLING).returncode == 0


def test_sync_removes_vendored_cases_the_source_no_longer_has(tmp_path):
    sib = _fake_sibling(tmp_path)
    vend = tmp_path / "vend"
    assert _run("--from", str(sib), "--vendor-dir", str(vend)).returncode == 0
    (sib / SOURCE_DIR / "conformance" / "001-b.json").unlink()
    _git(sib, "add", "-A"); _git(sib, "commit", "-q", "-m", "drop b")
    assert _run("--from", str(sib), "--vendor-dir", str(vend)).returncode == 0
    assert not (vend / "conformance" / "001-b.json").exists()
    assert "conformance/001-b.json" not in json.loads((vend / "PROVENANCE.json").read_text())["files"]


def test_sync_refuses_a_dirty_source_unless_told_otherwise(tmp_path):
    sib = _fake_sibling(tmp_path)
    (sib / SOURCE_DIR / "buddy.host.v1.json").write_bytes(b'{"changed":true}\n')
    vend = tmp_path / "vend"
    r = _run("--from", str(sib), "--vendor-dir", str(vend))
    assert r.returncode == 2 and "uncommitted" in (r.stdout + r.stderr).lower()
    assert not vend.exists()
    assert _run("--from", str(sib), "--vendor-dir", str(vend), "--allow-dirty").returncode == 0


def test_sync_from_a_missing_source_is_a_usage_error(tmp_path):
    r = _run("--from", str(tmp_path / "nope"), "--vendor-dir", str(tmp_path / "vend"))
    assert r.returncode == 2


def test_check_with_the_sibling_present_reports_changed_bytes(tmp_path):
    sib = _fake_sibling(tmp_path)
    vend = tmp_path / "vend"
    assert _run("--from", str(sib), "--vendor-dir", str(vend)).returncode == 0
    (sib / SOURCE_DIR / "conformance" / "000-a.json").write_bytes(b'{"name":"a","v":2}\n')
    _git(sib, "add", "-A"); _git(sib, "commit", "-q", "-m", "edit a")
    r = _run("--check", "--vendor-dir", str(vend), env={"QKZ_BUDDY_PATH": str(sib)})
    assert r.returncode == 3 and "000-a.json" in r.stdout + r.stderr


def test_check_with_the_sibling_present_notes_a_moved_commit_with_equal_bytes(tmp_path):
    sib = _fake_sibling(tmp_path)
    vend = tmp_path / "vend"
    assert _run("--from", str(sib), "--vendor-dir", str(vend)).returncode == 0
    (sib / "README.md").write_text("other work\n")
    _git(sib, "add", "-A"); _git(sib, "commit", "-q", "-m", "unrelated")
    r = _run("--check", "--vendor-dir", str(vend), env={"QKZ_BUDDY_PATH": str(sib)})
    assert r.returncode == 0 and "moved" in (r.stdout + r.stderr).lower()


def test_check_with_the_sibling_present_catches_a_case_the_source_added(tmp_path):
    sib = _fake_sibling(tmp_path)
    vend = tmp_path / "vend"
    assert _run("--from", str(sib), "--vendor-dir", str(vend)).returncode == 0
    (sib / SOURCE_DIR / "conformance" / "002-c.json").write_bytes(b'{"name":"c"}\n')
    _git(sib, "add", "-A"); _git(sib, "commit", "-q", "-m", "add c")
    r = _run("--check", "--vendor-dir", str(vend), env={"QKZ_BUDDY_PATH": str(sib)})
    assert r.returncode == 3 and "002-c.json" in r.stdout + r.stderr
