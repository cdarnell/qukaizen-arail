"""QA paranoid probes for World Mount — gaps not covered by builder tests.

These are independent, deterministic tests written by QA. They do NOT modify
production code. They probe atomicity windows, integrity coverage of non-terms
files, injection inertness through the real prompt path, and degrade-gracefully
edges on a clean machine.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import arail.world_mount as wm

FIX = Path(__file__).parent / "fixtures" / "world-bundles"
PHYSICS = FIX / "physics"
HOSTILE = FIX / "hostile"
TAMPERED = FIX / "tampered"


# ── Integrity: non-terms file tamper must be caught ──────────────────────────

def test_seal_catches_altered_face_json(tmp_path):
    """Alter face.json bytes WITHOUT updating manifest.files[face.json].
    verify_seal must REFUSE and name face.json as the suspect."""
    bdir = tmp_path / "bundle"
    shutil.copytree(PHYSICS, bdir)
    # flip a byte in face.json (in a field that is not the world slug);
    # leave manifest unchanged so verify_seal catches the hash mismatch
    face = (bdir / "face.json").read_bytes()
    (bdir / "face.json").write_bytes(face.replace(b"domain_framing", b"domain_frXming", 1))
    bundle = wm.load_bundle(bdir)
    seal = wm.verify_seal(bundle)
    assert seal.ok is False, "altered face.json passed the seal — integrity hole"
    assert "face.json" in seal.user_message


def test_seal_catches_altered_spec_json(tmp_path):
    bdir = tmp_path / "bundle"
    shutil.copytree(PHYSICS, bdir)
    spec = (bdir / "spec.json").read_bytes()
    (bdir / "spec.json").write_bytes(spec.replace(b"quantities", b"qXantities", 1))
    bundle = wm.load_bundle(bdir)
    seal = wm.verify_seal(bundle)
    assert seal.ok is False
    assert "spec.json" in seal.user_message


def test_seal_recompute_is_over_raw_bytes_not_reserialized(tmp_path):
    """Add insignificant whitespace to terms.json (semantically identical JSON).
    A naive re-serialize-then-hash would PASS; raw-bytes hashing must FAIL."""
    bdir = tmp_path / "bundle"
    shutil.copytree(PHYSICS, bdir)
    raw = (bdir / "terms.json").read_bytes()
    # append a trailing newline — parses identically, different bytes
    (bdir / "terms.json").write_bytes(raw + b"\n")
    bundle = wm.load_bundle(bdir)
    seal = wm.verify_seal(bundle)
    assert seal.ok is False, "whitespace-mutated terms passed — hash is not over raw bytes"


# ── Atomicity: staging failure leaves no orphan pointer ──────────────────────

def test_staging_failure_leaves_no_orphan_pointer(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    data_dir.mkdir()
    pkb_root.mkdir()

    def boom(*a, **k):
        raise OSError("disk full mid-stage")

    monkeypatch.setattr(wm, "_stage_files", boom)
    with pytest.raises(Exception):
        wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    # No pointer must exist
    assert wm.current_mount(data_dir) is None
    assert not (data_dir / wm.MOUNT_RECORD_NAME).exists()
    # No half-staged final dir
    assert not (pkb_root / "sources" / "world-physics").exists()


def test_pointer_written_after_staging(tmp_path):
    """Positive: a clean mount yields a pointer AND a complete staged dir."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    rec = wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    staged = pkb_root / "sources" / "world-physics"
    assert staged.exists()
    assert (staged / "terms.json").exists()
    assert (data_dir / wm.MOUNT_RECORD_NAME).exists()
    assert wm.current_mount(data_dir).world == "physics"


def test_no_lingering_staging_dir_after_mount(tmp_path):
    """The .staging-<slug> temp dir must not survive a successful mount."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    assert not (pkb_root / "sources" / ".staging-physics").exists()


# ── Clean-machine degrade-gracefully ─────────────────────────────────────────

def test_current_mount_clean_machine_no_record(tmp_path):
    assert wm.current_mount(tmp_path) is None


def test_unmount_no_record_is_graceful(tmp_path):
    assert wm.unmount(data_dir=tmp_path, pkb_root=tmp_path) is False


def test_mount_on_fresh_empty_dirs(tmp_path):
    """DATA_DIR and PKB_ROOT do not pre-exist — mount must create them."""
    data_dir = tmp_path / "fresh" / "data"
    pkb_root = tmp_path / "fresh" / "pkb"
    rec = wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    assert rec.world == "physics"
    assert wm.current_mount(data_dir) is not None


# ── Injection inertness through the consumer surfaces ────────────────────────

def test_hostile_definition_never_in_dict_entry_fields(tmp_path):
    """The injection string must appear ONLY as inert term content, mapped to
    TermEntry fields — never promoted into any control field."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    rec = wm.mount(HOSTILE, data_dir=data_dir, pkb_root=pkb_root)
    entries = wm.get_mounted_dict_terms(rec)
    # The injection lives in the 'definition' field, which term_to_dict_entry
    # deliberately does NOT map into the list TermEntry — so the glossary list
    # never even surfaces it. The term shell IS present (by slug).
    shell = [e for e in entries if e.get("key") == "hostile-injection"]
    assert shell, "hostile term shell should be present"
    e = shell[0]
    assert e["can_generate"] is False
    assert "ignore previous" not in json.dumps(e).lower(), \
        "injection definition must not be in the list entry"
    assert not any(k in e for k in ("system", "prompt", "instruction"))
    # The definition surfaces ONLY via mounted_terms (expand path) as raw data.
    raw = [t for t in wm.mounted_terms(rec) if t.get("slug") == "hostile-injection"][0]
    assert "ignore previous" in raw["definition"].lower()  # inert data only


def test_hostile_face_clean_so_framing_block_has_no_injection(tmp_path, monkeypatch):
    """Mount hostile and assert the Buddy framing block (which uses face.json
    only) contains NO term/definition text."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    wm.mount(HOSTILE, data_dir=data_dir, pkb_root=pkb_root)
    import arail.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir, raising=False)
    # current_mount default reads DATA_DIR via _default_data_dir
    monkeypatch.setattr(wm, "_default_data_dir", lambda: data_dir)
    from arail.agents import _builtin_buddy as bud
    block = bud._world_framing_block()
    assert "ignore previous" not in block.lower()
    assert "hostile-injection" not in block.lower()
    # but the legitimate face framing IS present
    assert "WORLD FRAMING" in block
    assert "measurement" in block.lower()


def test_compose_prompt_byte_identical_when_unmounted(tmp_path, monkeypatch):
    """Zero-regression: with no world mounted, _compose_prompt yields no
    WORLD FRAMING block."""
    monkeypatch.setattr(wm, "_default_data_dir", lambda: tmp_path)  # empty → no mount
    from arail.agents import _builtin_buddy as bud
    out = bud._compose_prompt("test observation")
    assert "WORLD FRAMING" not in out


def test_framing_cap_enforced_on_overlong_domain(tmp_path, monkeypatch):
    """Feed an over-long domain_framing via a crafted face.json and assert the
    block is truncated at the cap."""
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    bdir = tmp_path / "bundle"
    shutil.copytree(PHYSICS, bdir)
    face = json.loads((bdir / "face.json").read_text())
    face["domain_framing"] = "X" * 5000
    face["vocabulary_register"] = "Y" * 5000
    (bdir / "face.json").write_text(json.dumps(face))
    # reseal: recompute face hash into manifest
    import hashlib
    man = json.loads((bdir / "manifest.json").read_text())
    man["files"]["face.json"] = hashlib.sha256((bdir / "face.json").read_bytes()).hexdigest()
    (bdir / "manifest.json").write_text(json.dumps(man))
    wm.mount(bdir, data_dir=data_dir, pkb_root=pkb_root)
    monkeypatch.setattr(wm, "_default_data_dir", lambda: data_dir)
    from arail.agents import _builtin_buddy as bud
    # Isolate the goal store: a real lab may have an active goal whose text
    # ("...study partner...") contributes its own X/Y characters on top of
    # the truncated framing fields, breaking the character-count assertion
    # below. The goal is irrelevant to this test's purpose (framing cap).
    monkeypatch.setattr(bud._host, "get_current_goal", lambda: None)
    block = bud._world_framing_block()
    assert block.count("X") <= bud._MAX_WORLD_DOMAIN_FRAMING
    assert block.count("Y") <= bud._MAX_WORLD_VOCAB_REGISTER


# ── Gate: hostile term in an UNDECLARED category must be refused ──────────────

def test_gate_refuses_undeclared_category(tmp_path):
    bdir = tmp_path / "bundle"
    shutil.copytree(PHYSICS, bdir)
    terms = json.loads((bdir / "terms.json").read_text())
    terms["terms"][0]["category"] = "evil-undeclared"
    raw = json.dumps(terms).encode()
    (bdir / "terms.json").write_bytes(raw)
    import hashlib
    man = json.loads((bdir / "manifest.json").read_text())
    h = hashlib.sha256(raw).hexdigest()
    man["world_sha256"] = h
    man["files"]["terms.json"] = h
    (bdir / "manifest.json").write_text(json.dumps(man))
    bundle = wm.load_bundle(bdir)
    assert wm.verify_seal(bundle).ok  # seal passes (we resealed)
    with pytest.raises(wm.GateViolation):
        wm.check_categories(bundle)


# ── Face flip: KB-only mount writes NO env keys ──────────────────────────────

def test_mount_writes_no_env(tmp_path):
    """Mount must NEVER write face keys to .env — identity flips live from the
    sidecar. An operator's existing .env is left byte-for-byte untouched."""
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n")
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    content = env.read_text()
    assert "LAB_INTENT" not in content
    assert "LAB_THEME" not in content
    assert content == "EXISTING=1\n"


def test_mount_does_not_touch_operator_brand_env(tmp_path):
    """Mounting reports the World identity via the resolver, but the operator's
    own .env LAB_NAME/LAB_LOGO are physically untouched (no env write at all)."""
    from arail.identity import effective_identity

    env = tmp_path / ".env"
    env.write_text("LAB_NAME=MyLab\nLAB_LOGO=logo.png\n")
    data_dir = tmp_path / "data"
    pkb_root = tmp_path / "pkb"
    wm.mount(PHYSICS, data_dir=data_dir, pkb_root=pkb_root)
    # .env is untouched — no LAB_INTENT or face keys written.
    assert env.read_text() == "LAB_NAME=MyLab\nLAB_LOGO=logo.png\n"
    # Identity reflects the World from the sidecar, not from env.
    ident = effective_identity(data_dir)
    assert ident.name == "Physics — Measurement & Units"
    assert ident.intent == "other"
