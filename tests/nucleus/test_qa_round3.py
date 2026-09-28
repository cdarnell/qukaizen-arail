"""QA pass, nucleus-sprint-1 (REVIEW.md round 3 target list).

Pinning tests. A test marked ``xfail(strict=True)`` documents a known,
ticketed defect (sprints/BACKLOG.md "Model Forge real-runtime wiring",
round-3 tickets). It asserts the CORRECT behaviour, so it xfails today
and fails loudly (XPASS -> strict failure) the moment the fix lands,
which is the signal to drop the marker. Every other test here pins
current behaviour that is correct.

No network: every test runs on tmp dirs, and the socket guard tests
assert it. Nothing is written under the checkout's lab/ or models/
(tests/nucleus/conftest.py's autouse isolation plus per-test tmp roots).
"""

from __future__ import annotations

import json
import os
import random
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from arail.nucleus import build as build_mod
from arail.nucleus import certify as certify_mod
from arail.nucleus import cli
from arail.nucleus import preflight as pf
from arail.nucleus.errors import RefusedByPolicy

from tests.nucleus.test_build_phases import staged_context  # noqa: F401  (fixture)
from tests.nucleus.test_certify import _run_phase

PHASES = ("PA", "PA2", "PB", "fuse", "PC")

# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _build_all_phases(ctx):
    for phase in PHASES:
        _run_phase(ctx, phase)
    rd = build_mod._run_dir(ctx)
    (rd / "context.json").write_text(json.dumps(ctx, sort_keys=True))
    return rd


def _forge_root(ctx) -> Path:
    from arail.nucleus.paths import forge_root

    return forge_root()


def _cards_under(root: Path):
    return sorted(root.rglob("dna-card.yaml")) if root.exists() else []


def _ledger_text(ctx) -> str:
    from arail.nucleus.cards import certified_models

    path = certified_models._default_local_path(build_mod._nucleus_data(ctx))
    return path.read_text() if path.exists() else ""


def _cli_certify(build_id, monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "maximus")
    code = cli.main(["certify", build_id])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _rewrite_json(path: Path, **updates):
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data, sort_keys=True))


# ═════════════════════════════════════════════════════════════════════
# 1. SECURITY: stub laundering (REVIEW round 3 variants a/b/c + more)
# ═════════════════════════════════════════════════════════════════════


def test_laundering_a_forged_context_flag_refused_exit_3_no_card_no_ledger(
        staged_context, tmp_path, monkeypatch, capsys):
    """(a) context.json says stub:false/queuellm, stamps untouched."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    _rewrite_json(rd / "context.json", stub=False, provider="queuellm")
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    code, _out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 3
    assert "phase output disagrees" in err
    for phase in PHASES:
        assert phase in err
    assert "Traceback" not in err
    assert _cards_under(_forge_root(staged_context)) == []
    assert "qkz-kernel" not in _ledger_text(staged_context)


def test_laundering_reverse_stub_true_over_real_looking_stamps_refused(
        staged_context, tmp_path, monkeypatch, capsys):
    """The other direction: context says stub (env agrees), but every
    phase stamp claims a real runtime. Must refuse, not sign a stub card
    over a run whose record says it was real."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    for phase in PHASES:
        _rewrite_json(rd / "phase_output" / f"{phase}.json", provider="queuellm")

    code, _out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 3
    assert "phase output disagrees" in err
    assert _cards_under(_forge_root(staged_context)) == []


def _forge_all_stamps_real(rd):
    for phase in PHASES:
        _rewrite_json(rd / "phase_output" / f"{phase}.json", provider="queuellm")


@pytest.mark.xfail(strict=True, reason=(
    "R3-A5 (BACKLOG umbrella): certify must refuse stub:false outright while "
    "B7 stands -- no non-stub build path exists in sprint 1. Today, forging "
    "context.json AND every phase stamp signs a lab-key card and ledgers it."))
def test_certify_refuses_stub_false_while_b7_stands(staged_context, tmp_path, monkeypatch, capsys):
    """(b) context + all five stamps forged to queuellm."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    _rewrite_json(rd / "context.json", stub=False, provider="queuellm")
    _forge_all_stamps_real(rd)
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    code, _out, _err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 3
    assert _cards_under(_forge_root(staged_context)) == []
    assert "qkz-kernel" not in _ledger_text(staged_context)


def test_laundering_b_current_behaviour_signs_and_ledgers_a_real_looking_card(
        staged_context, tmp_path, monkeypatch, capsys):
    """Characterisation of (b), so the report's severity claim is backed
    by a test: today it exits 0 with runtime 'queuellm', a trusted lab
    key, and a ledger row. The strict xfail above flips when fixed; this
    one must then be deleted."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    _rewrite_json(rd / "context.json", stub=False, provider="queuellm")
    _forge_all_stamps_real(rd)
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    code, out, _err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 0
    from arail.nucleus.cards.dna_v2 import load_card

    (card_path,) = _cards_under(_forge_root(staged_context))
    card = load_card(card_path)
    assert card["runtime"] == "queuellm"
    assert card["signed"]["key_fingerprint"] != "ephemeral-stub"
    assert "qkz-kernel" in _ledger_text(staged_context)
    assert "ledger: None" not in out


@pytest.mark.xfail(strict=True, reason=(
    "R3-A5 (BACKLOG umbrella): certify's stamp cross-check `continue`s past a "
    "missing phase_output/<phase>.json. Deleting PA/PA2/PB/PC stamps and "
    "forging fuse's is enough to sign and ledger a lab-key card."))
def test_certify_refuses_when_phase_stamps_deleted(staged_context, tmp_path, monkeypatch, capsys):
    """(c) PA, PA2, PB, PC stamps deleted; fuse forged; context forged."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    _rewrite_json(rd / "context.json", stub=False, provider="queuellm")
    for phase in ("PA", "PA2", "PB", "PC"):
        (rd / "phase_output" / f"{phase}.json").unlink()
    _rewrite_json(rd / "phase_output" / "fuse.json", provider="queuellm")
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)

    code, _out, _err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 3
    assert _cards_under(_forge_root(staged_context)) == []
    assert "qkz-kernel" not in _ledger_text(staged_context)


def test_certify_missing_fuse_output_refuses_exit_3_no_card(staged_context, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    (rd / "phase_output" / "fuse.json").unlink()

    code, _out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 3
    assert "no fuse phase output" in err
    assert "Traceback" not in err
    # fuse already minted the (empty) shard dir; no card may be in it.
    assert _cards_under(_forge_root(staged_context)) == []
    assert "qkz-kernel" not in _ledger_text(staged_context)


def test_certify_missing_metrics_json_fails_closed_without_a_card(staged_context, tmp_path, monkeypatch, capsys):
    """Pins current behaviour: a missing eval/metrics.json surfaces as an
    uncaught FileNotFoundError -> exit 1 'internal error: ...' (not a
    plain exit-3 refusal naming the PC step -- a failure-mode-grace nit,
    filed in TEST_REPORT). It fails CLOSED: no card, no ledger row, no
    traceback."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    (rd / "eval" / "metrics.json").unlink()

    code, _out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 1
    assert err.startswith("internal error:")
    assert "metrics.json" in err
    assert "Traceback" not in err
    assert _cards_under(_forge_root(staged_context)) == []
    assert "qkz-kernel" not in _ledger_text(staged_context)


def test_certify_context_missing_on_disk_fails_closed(staged_context, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    rd = _build_all_phases(staged_context)
    (rd / "context.json").unlink()

    code, _out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code != 0
    assert "Traceback" not in err
    assert _cards_under(_forge_root(staged_context)) == []


@pytest.mark.parametrize("bad_id", ["../../etc", "kernel/../../x", "KERNEL-20260101T000000Z-abcd",
                                    "kernel-20260101T000000Z-abcd/..", "kernel-20260101T000000Z-ABCD"])
def test_certify_rejects_non_canonical_build_ids_exit_3(bad_id, monkeypatch, capsys):
    code, _out, err = _cli_certify(bad_id, monkeypatch, capsys)
    assert code == 3
    assert "build_id must match" in err
    assert "Traceback" not in err


def test_certify_without_build_id_is_a_plain_refusal(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "maximus")
    code = cli.main(["certify"])
    err = capsys.readouterr().err
    assert code == 3
    assert "usage: nucleus certify" in err


def test_certify_unknown_but_well_formed_build_id_fails_without_traceback(monkeypatch, capsys):
    """Pins current behaviour: exit 1 'internal error: [Errno 2] ...
    context.json' rather than a plain 'no such build' (exit 3) the way
    `status` does. Failure-mode-grace nit, filed in TEST_REPORT."""
    code, _out, err = _cli_certify("kernel-20260101T000000Z-abcd", monkeypatch, capsys)
    assert code == 1
    assert err.startswith("internal error:")
    assert "Traceback" not in err


@pytest.mark.xfail(strict=True, reason=(
    "LOW (TEST_REPORT F-QA-3): every id/slug/version validator uses re.match "
    "with a `$` anchor, which also matches before a trailing newline, so "
    "'<valid>\\n' passes. No traversal is possible (only a trailing \\n gets "
    "through), but it lets newline-bearing dir names be minted. Use "
    "re.fullmatch or \\Z."))
def test_validators_reject_trailing_newline():
    from arail.nucleus.domain import _SLUG_RE
    from arail.nucleus.paths import _SEMVER_RE, _SHARD_RE, validate_build_id
    from arail.portal import forge_api

    with pytest.raises(RefusedByPolicy):
        validate_build_id("kernel-20260101T000000Z-abcd\n")
    assert not _SHARD_RE.match("qkz-x\n")
    assert not _SEMVER_RE.match("0.1.0\n")
    assert not _SLUG_RE.match("kernel\n")
    assert forge_api.safe_card_dir("qkz-x\n", "0.1.0") is None


# ═════════════════════════════════════════════════════════════════════
# 2. SECURITY: Buddy guard (R3 / R3-A4) on real model dirs
# ═════════════════════════════════════════════════════════════════════

BUDDY_DIRNAME = "Qwen2.5-7B-Instruct-4bit"   # == models.ALIASES["ai-engineer"]
STUDENT_DIRNAME = "Qwen2.5-3B-Instruct-4bit"  # == models.ALIASES["qwen2.5-3b-instruct"]


def _real_model(path: Path, *, num_parameters: int, weight_bytes: int = 0, sparse_bytes: int = 0) -> Path:
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps({"num_parameters": num_parameters}))
    if weight_bytes:
        (path / "model.safetensors").write_bytes(b"\x01" * weight_bytes)
    if sparse_bytes:
        # Sparse: st_size is large, disk usage is ~0. Lets preflight see a
        # multi-GB model deterministically without writing GBs.
        with open(path / "model.safetensors", "wb") as f:
            f.write(b"\x01" * 4096)
            f.truncate(sparse_bytes)
    return path


@pytest.fixture
def phase_c_dirs(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    buddy = _real_model(models_dir / BUDDY_DIRNAME, num_parameters=7_000_000_000, weight_bytes=5_000_000)
    _real_model(models_dir / "base", num_parameters=1_000_000_000, weight_bytes=500_000)
    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    from arail.nucleus.models import resolve_model

    return SimpleNamespace(models_dir=models_dir, buddy=buddy,
                           base=resolve_model("base"), judge=resolve_model("ai-engineer"))


def _phase_c_refusal(base, judge):
    with pytest.raises(pf.PreflightRefusal) as exc_info:
        pf.run_preflight(SimpleNamespace(name="kernel"), capacity={"ram_gb": 4.0, "vram_gb": 3.0, "disk_gb": 1.0},
                         buddy=pf.BuddyReserve(), memory_budget_gb=0.001, student_model=None,
                         base_student_model=base, judge_model=judge, capability_probes={})
    assert exc_info.value.phase == "C"
    return exc_info.value


@pytest.mark.xfail(strict=True, reason=(
    "R3-A4 (BACKLOG umbrella): preflight protects QUEUELLM_MODEL first, but "
    "arail's AeroLLMBackend loads only AEROLLM_MODEL. With the two diverging, "
    "preflight advises dropping the model Buddy actually runs."))
def test_preflight_protects_aerollm_model_when_queuellm_model_differs(phase_c_dirs, monkeypatch):
    monkeypatch.setenv("QUEUELLM_MODEL", "base")
    monkeypatch.setenv("AEROLLM_MODEL", str(phase_c_dirs.buddy))
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert BUDDY_DIRNAME not in refusal.drop


def test_env_mismatch_current_behaviour_advises_dropping_buddy(phase_c_dirs, monkeypatch):
    """Characterisation of R3-A4 so the report's claim is test-backed."""
    monkeypatch.setenv("QUEUELLM_MODEL", "base")
    monkeypatch.setenv("AEROLLM_MODEL", str(phase_c_dirs.buddy))
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.drop == [BUDDY_DIRNAME]
    assert refusal.protected == ["base"]


def test_aerollm_model_alone_is_protected(phase_c_dirs, monkeypatch):
    monkeypatch.setenv("AEROLLM_MODEL", str(phase_c_dirs.buddy))
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.drop == ["base"]


def test_both_env_vars_naming_buddy_is_protected(phase_c_dirs, monkeypatch):
    monkeypatch.setenv("AEROLLM_MODEL", str(phase_c_dirs.buddy))
    monkeypatch.setenv("QUEUELLM_MODEL", BUDDY_DIRNAME)
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.drop == ["base"]


@pytest.mark.xfail(strict=True, reason=(
    "R3-A4 (BACKLOG umbrella): with both env vars unset, AeroLLMBackend "
    "defaults to Qwen2.5-7B-Instruct-4bit (also the ai-engineer judge target), "
    "but preflight protects nothing and advises dropping it on maximus."))
def test_preflight_protects_backend_default_when_env_unset(phase_c_dirs, monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert BUDDY_DIRNAME not in refusal.drop


def test_empty_string_env_is_treated_as_unset(phase_c_dirs, monkeypatch):
    monkeypatch.setenv("QUEUELLM_MODEL", "")
    monkeypatch.setenv("AEROLLM_MODEL", str(phase_c_dirs.buddy))
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.drop == ["base"]


@pytest.mark.parametrize("value", ["./" + BUDDY_DIRNAME, "../" + BUDDY_DIRNAME, "~/" + BUDDY_DIRNAME])
def test_relative_or_tilde_buddy_path_does_not_crash_preflight(phase_c_dirs, monkeypatch, value):
    """Odd operator configs degrade to a plain refusal, never a crash.
    (They are not recognised as Buddy -- the backend itself only accepts a
    bare name or an absolute path, so that matches what Buddy would load.)"""
    monkeypatch.setenv("AEROLLM_MODEL", value)
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.phase == "C"


def test_buddy_path_pointing_at_missing_dir_does_not_crash(phase_c_dirs, monkeypatch, tmp_path):
    monkeypatch.setenv("AEROLLM_MODEL", str(tmp_path / "does-not-exist"))
    refusal = _phase_c_refusal(phase_c_dirs.base, phase_c_dirs.judge)
    assert refusal.drop == [BUDDY_DIRNAME]  # nothing on disk to protect by identity


def test_phase_c_property_buddy_never_dropped_over_200_combos(monkeypatch):
    """T-PRE-3 only ever sizes a teacher (Phase A). This covers Phase C:
    random sizes, random choice of which candidate is Buddy's model,
    random budget; asserts phase C was genuinely reached often enough to
    mean something, Buddy never dropped, and the drop is the largest
    non-protected candidate."""
    rng = random.Random(20260923)
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    reached_c = 0
    for _ in range(200):
        sizes = {name: rng.uniform(0.1, 40.0) for name in ("student", "base", "judge")}
        cands = {n: SimpleNamespace(name=n, params_est_b=0.1, weights_bytes=int(g * 1024 ** 3), config={})
                 for n, g in sizes.items()}
        buddy_name = rng.choice(["student", "base", "judge", None])
        if buddy_name:
            monkeypatch.setenv("AEROLLM_MODEL", buddy_name)
        else:
            monkeypatch.delenv("AEROLLM_MODEL", raising=False)
        ram = rng.uniform(4, 64)
        try:
            pf.run_preflight(SimpleNamespace(name="k"), capacity={"ram_gb": ram, "vram_gb": ram, "disk_gb": 1},
                             buddy=pf.BuddyReserve(), student_model=None, base_student_model=cands["base"],
                             judge_model=cands["judge"], capability_probes={})
        except pf.PreflightRefusal as refusal:
            assert refusal.phase == "C"
            reached_c += 1
            assert buddy_name not in refusal.drop
            droppable = [n for n in ("base", "judge") if n != buddy_name]
            expected = max(droppable, key=lambda n: sizes[n])
            assert refusal.drop == [expected]
    assert reached_c >= 50, reached_c


# ── `nucleus plan` output text on a clean machine (setup + Buddy) ─────

@pytest.fixture
def plan_env(tmp_path, monkeypatch):
    """A clean-machine-style lab: a student (config only), Buddy's deep
    model as a sparse 3 GiB dir that is also the `ai-engineer` judge,
    capacity pinned so Phase B is green and Phase C is red."""
    models_dir = tmp_path / "models"
    _real_model(models_dir / STUDENT_DIRNAME, num_parameters=500_000_000)
    buddy = _real_model(models_dir / BUDDY_DIRNAME, num_parameters=7_000_000_000, sparse_bytes=3 * 1024 ** 3)
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    (domains_dir / "kernel.yaml").write_text(
        'student:\n  base: qwen2.5-3b-instruct\nteacher:\n  profile: local\n  model: auto\n'
        'corpus:\n  sources: [git:linux]\n  cutoff: "2025-01-01"\n'
        'eval:\n  cert_set: frozen\n  eyeball_prompts: kernel.eyeball.txt\n  dev_fraction: 0.1\n  cert_n: 20\n'
        'fidelity:\n  target: 0.5\n')
    (domains_dir / "kernel.eyeball.txt").write_text("\n".join(f"p{i}" for i in range(10)) + "\n")

    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))
    monkeypatch.setattr("arail.config.MODEL_NAME", "")
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", domains_dir)
    monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
    monkeypatch.delenv("AEROLLM_MODEL", raising=False)
    monkeypatch.setenv("LAB_TIER", "minimalist")  # plan is a minimalist verb
    monkeypatch.setattr(pf, "_capacity", lambda: {"ram_gb": 8.0, "vram_gb": 6.0, "disk_gb": 100.0})
    monkeypatch.setattr(pf, "measure_buddy_reserve", lambda: pf.BuddyReserve())
    return SimpleNamespace(models_dir=models_dir, buddy=buddy, tmp_path=tmp_path)


def _plan_output(capsys):
    code = cli.main(["plan", "kernel"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _drop_line(out: str) -> str:
    (line,) = [ln for ln in out.splitlines() if ln.startswith("preflight: WOULD REFUSE")]
    return line.split("Drop:", 1)[1].split("(", 1)[0].strip() if "Drop:" in line else ""


def _plan_forms(env):
    link = env.tmp_path / "buddy-link"
    link.symlink_to(env.buddy, target_is_directory=True)
    outside = env.tmp_path / "elsewhere" / "copy"
    shutil.copytree(env.buddy, outside)
    return [
        ("bare name", "AEROLLM_MODEL", BUDDY_DIRNAME),
        ("absolute path", "AEROLLM_MODEL", str(env.buddy)),
        ("symlink", "AEROLLM_MODEL", str(link)),
        ("copy outside models dir", "AEROLLM_MODEL", str(outside)),
        ("QUEUELLM_MODEL absolute", "QUEUELLM_MODEL", str(env.buddy)),
    ]


def test_plan_refusal_text_is_plain_and_never_names_buddy_in_every_form(plan_env, monkeypatch, capsys):
    for label, var, value in _plan_forms(plan_env):
        monkeypatch.delenv("QUEUELLM_MODEL", raising=False)
        monkeypatch.delenv("AEROLLM_MODEL", raising=False)
        monkeypatch.setenv(var, value)
        code, out, err = _plan_output(capsys)
        assert code == 0, (label, err)
        assert "Traceback" not in out + err, label
        assert "domain 'kernel' loads cleanly" in out, label
        line = [ln for ln in out.splitlines() if ln.startswith("preflight: WOULD REFUSE")]
        assert line, (label, out)
        assert "Phase C needs" in line[0] and "GB but the budget is" in line[0], label
        assert _drop_line(out) == STUDENT_DIRNAME, (label, line[0])
        assert f"Protected, never evicted: {value}" in line[0], (label, line[0])


def test_plan_control_without_buddy_names_the_judge(plan_env, capsys):
    """Control: with no Buddy configured the judge (largest) is the drop,
    so the test above is not passing for a size reason."""
    code, out, err = _plan_output(capsys)
    assert code == 0, err
    assert _drop_line(out) == BUDDY_DIRNAME


@pytest.mark.xfail(strict=True, reason=(
    "R3-A4 (BACKLOG umbrella): user-visible through `nucleus plan` -- with "
    "QUEUELLM_MODEL naming another model and AEROLLM_MODEL naming Buddy's, the "
    "advisory text says 'Drop: Qwen2.5-7B-Instruct-4bit', Buddy's model."))
def test_plan_output_never_advises_dropping_buddy_under_env_mismatch(plan_env, monkeypatch, capsys):
    monkeypatch.setenv("QUEUELLM_MODEL", STUDENT_DIRNAME)
    monkeypatch.setenv("AEROLLM_MODEL", str(plan_env.buddy))
    code, out, _err = _plan_output(capsys)
    assert code == 0
    assert _drop_line(out) != BUDDY_DIRNAME


def test_plan_is_advisory_only_and_writes_nothing(plan_env, monkeypatch, capsys):
    """`plan <slug>` on a clean machine creates nothing under the lab's
    data or models dirs (T-SETUP-1) and never touches Buddy's dir."""
    from arail.config import DATA_DIR

    before_models = sorted(p.relative_to(plan_env.models_dir) for p in plan_env.models_dir.rglob("*"))
    data_dir = Path(DATA_DIR)
    before_data = sorted(data_dir.rglob("*")) if data_dir.exists() else []
    monkeypatch.setenv("AEROLLM_MODEL", str(plan_env.buddy))
    code, _out, _err = _plan_output(capsys)
    assert code == 0
    assert sorted(p.relative_to(plan_env.models_dir) for p in plan_env.models_dir.rglob("*")) == before_models
    assert (sorted(data_dir.rglob("*")) if data_dir.exists() else []) == before_data


# ═════════════════════════════════════════════════════════════════════
# 3. SECURITY: hand-edited cards vs `verify` (fast + non-fast) and the
#    /forge badge
# ═════════════════════════════════════════════════════════════════════


@pytest.fixture
def trusted_card_dir(staged_context, tmp_path, monkeypatch):
    """A real certify-produced card (stub pipeline), re-sealed with a
    lab key so it verifies `trusted` -- the only way to get a trusted
    card while B7 refuses real builds. Returns the card dir."""
    from arail.nucleus.cards import seal as seal_mod
    from arail.nucleus.cards.dna_v2 import card_sha256, load_card, write_card

    key_path = tmp_path / "keys" / "signing.ed25519"
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(key_path))
    _build_all_phases(staged_context)
    result = certify_mod.run_certify(staged_context["build_id"], context=staged_context)
    card_dir = Path(result["card_dir"])
    card = load_card(card_dir / "dna-card.yaml")
    card.pop("signed")
    gates = seal_mod.build_gate_results(card_sha256=card_sha256(card), eval_hash=card["eval_hash"],
                                        decision=card["fidelity"]["decision"], contamination_overlap=0.0)
    payload = seal_mod.build_payload(pipeline_run_id=staged_context["build_id"], chain_hash="c" * 64,
                                     gate_results=gates)
    sealed = seal_mod.sign(payload, key_path=key_path)
    card["signed"] = sealed.signed
    write_card(card, card_dir / "dna-card.yaml")
    (card_dir / "seal.json").write_text(json.dumps(sealed.seal_json))
    monkeypatch.setenv("LAB_TIER", "minimalist")  # verify is a minimalist verb
    return card_dir


def _verify(card_dir, capsys, *, fast):
    argv = ["verify", str(card_dir)] + (["--fast"] if fast else [])
    code = cli.main(argv)
    out = capsys.readouterr().out
    fields = dict(ln.split(": ", 1) for ln in out.splitlines() if ": " in ln)
    return code, fields


def _badge(card_dir):
    from arail.nucleus.cards.dna_v2 import load_card
    from arail.portal import forge_api

    return forge_api.verify_badge(load_card(card_dir / "dna-card.yaml"))


def test_untampered_trusted_card_fast_verify_exit_0_and_badge_trusted(trusted_card_dir, capsys):
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert code == 0
    assert fields == {"signature": "valid", "key": "trusted", "card_hash": "match",
                      "eval_hash": "skipped", "chain": "skipped"}
    assert _badge(trusted_card_dir) == "trusted"


def test_cli_verify_non_fast_exits_3_even_when_everything_checkable_matches(trusted_card_dir, capsys):
    """A9 pin: chain re-derivation isn't implemented, so a non-fast verify
    of a trusted, untampered card reports chain: not_checked and exits 3.
    Honest, but undocumented (A9, BACKLOG umbrella)."""
    code, fields = _verify(trusted_card_dir, capsys, fast=False)
    assert fields["signature"] == "valid"
    assert fields["key"] == "trusted"
    assert fields["card_hash"] == "match"
    assert fields["eval_hash"] == "match"
    assert fields["chain"] == "not_checked"
    assert code == 3


def _edit_card(card_dir, mutate):
    import yaml

    path = card_dir / "dna-card.yaml"
    card = yaml.safe_load(path.read_text())
    mutate(card)
    from arail.nucleus.cards.dna_v2 import dump_card_yaml

    path.write_text(dump_card_yaml(card))  # a hand edit: no schema validation on the way in


_TAMPERS = {
    "closed metric": lambda c: c["closed_ended"]["subsystem_routing"].__setitem__("macro_f1", 0.99),
    "open_ended lc": lambda c: c["open_ended"]["eyeball_explanation"].__setitem__("lc_win_rate_vs_base", 0.99),
    "baselines": lambda c: c["baselines"]["base_student"].__setitem__("closed.mean_f1", 0.0),
    "decision upgraded": lambda c: c["fidelity"].__setitem__("decision", "CERTIFIED"),
    "composite value": lambda c: c["composite"].__setitem__("value", 0.99),
    "runtime relabelled": lambda c: c.__setitem__("runtime", "queuellm"),
    "executable filled in": lambda c: c["executable"].__setitem__("patch_applies", {"rate": 1.0, "n": 5}),
    "extra top-level key": lambda c: c.__setitem__("note", "certified by hand"),
}


@pytest.mark.parametrize("label", sorted(_TAMPERS))
def test_hand_edited_card_is_caught_by_verify_both_modes_and_badge(trusted_card_dir, capsys, label):
    _edit_card(trusted_card_dir, _TAMPERS[label])
    for fast in (True, False):
        code, fields = _verify(trusted_card_dir, capsys, fast=fast)
        assert fields["card_hash"] == "mismatch", (label, fast)
        assert fields["signature"] == "valid", (label, fast)  # the seal itself is intact
        assert code == 3, (label, fast)
    assert _badge(trusted_card_dir) == "tampered", label


def test_transplanted_seal_from_another_card_is_caught(trusted_card_dir, tmp_path, capsys):
    """A valid, trusted seal lifted from card X onto card Y: signature
    and key both check out, card_hash must not."""
    from arail.nucleus.cards import seal as seal_mod
    from arail.nucleus.cards.dna_v2 import card_sha256, load_card, write_card

    card = load_card(trusted_card_dir / "dna-card.yaml")
    other = dict(card)
    other.pop("signed")
    other["version"] = "9.9.9"
    gates = seal_mod.build_gate_results(card_sha256=card_sha256(other), eval_hash=other["eval_hash"],
                                        decision="CERTIFIED", contamination_overlap=0.0)
    foreign = seal_mod.sign(seal_mod.build_payload(pipeline_run_id="x", chain_hash="d" * 64, gate_results=gates),
                            key_path=Path(os.environ["NUCLEUS_SIGNING_KEY_PATH"]))
    card["signed"] = foreign.signed
    write_card(card, trusted_card_dir / "dna-card.yaml")
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert fields["signature"] == "valid" and fields["key"] == "trusted"
    assert fields["card_hash"] == "mismatch"
    assert code == 3
    assert _badge(trusted_card_dir) == "tampered"


def test_edited_eval_config_lock_caught_by_non_fast_verify_only(trusted_card_dir, capsys):
    lock = trusted_card_dir / "eval-config.lock"
    data = json.loads(lock.read_text())
    data["scoring"]["bootstrap_n"] = 10
    lock.write_text(json.dumps(data))
    code, fields = _verify(trusted_card_dir, capsys, fast=False)
    assert fields["eval_hash"] == "mismatch"
    assert code == 3
    # --fast and the /forge badge never read the lock (by design: fast).
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert code == 0
    assert _badge(trusted_card_dir) == "trusted"


def test_malformed_eval_config_lock_is_a_mismatch_not_a_crash(trusted_card_dir, capsys):
    (trusted_card_dir / "eval-config.lock").write_text("{not json")
    code, fields = _verify(trusted_card_dir, capsys, fast=False)
    assert fields["eval_hash"] == "mismatch"
    assert code == 3


def test_editing_domain_eyeball_file_after_certify_does_not_change_verify(trusted_card_dir, staged_context, capsys):
    """The lock carries the eyeball bytes as judged-at-certify; the live
    domain file is not part of the sealed artifact."""
    eyeball = Path(staged_context["domains_dir"]) / "kernel.eyeball.txt"
    eyeball.write_text("\n".join(f"edited {i}" for i in range(10)) + "\n")
    code, fields = _verify(trusted_card_dir, capsys, fast=False)
    assert fields["eval_hash"] == "match"
    assert fields["card_hash"] == "match"


def test_edited_build_report_is_not_covered_by_the_seal(trusted_card_dir, capsys):
    """Pins a documented gap (TEST_REPORT F-QA-4, LOW): build-report.md
    (which /forge renders on the detail page) is not hashed into the
    card or the seal, so a hand-edited report still shows under a
    `trusted` badge and verifies clean."""
    report = trusted_card_dir / "build-report.md"
    report.write_text(report.read_text().replace("COMPATIBLE", "CERTIFIED").replace("KNOWN_ISSUE", "CERTIFIED"))
    code, _fields = _verify(trusted_card_dir, capsys, fast=True)
    assert code == 0
    assert _badge(trusted_card_dir) == "trusted"


def test_nucleus_verify_reads_the_card_seal_not_seal_json(trusted_card_dir, capsys):
    """Pins current behaviour (TEST_REPORT F-QA-5, LOW): `nucleus verify`
    checks the card's embedded `signed:` block only. A corrupted
    seal.json next to it (what `qkz isotope verify` reads) goes
    unnoticed by `nucleus verify` and the badge."""
    seal_path = trusted_card_dir / "seal.json"
    data = json.loads(seal_path.read_text())
    data["dna_seal"]["signature_hex"] = "00" * 64
    seal_path.write_text(json.dumps(data))
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert code == 0 and fields["signature"] == "valid"


def test_malformed_signature_hex_reports_invalid_exit_3(trusted_card_dir, capsys):
    _edit_card(trusted_card_dir, lambda c: c["signed"].__setitem__("signature_hex", "zz-not-hex"))
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert fields["signature"] == "invalid"
    assert code == 3
    assert _badge(trusted_card_dir) == "invalid"


@pytest.mark.xfail(strict=True, reason=(
    "Round-1 ASK, still open (BACKLOG umbrella, seal.py): a malformed "
    "public_key_hex raises in bytes.fromhex/from_public_bytes OUTSIDE "
    "verify_signature's try, so `verify` exits 1 'internal error' instead of "
    "reporting signature: invalid (exit 3)."))
def test_malformed_public_key_hex_reports_invalid_exit_3(trusted_card_dir, capsys):
    _edit_card(trusted_card_dir, lambda c: c["signed"].__setitem__("public_key_hex", "zz-not-hex"))
    code, fields = _verify(trusted_card_dir, capsys, fast=True)
    assert code == 3
    assert fields.get("signature") == "invalid"


def test_malformed_public_key_hex_current_behaviour_fails_closed(trusted_card_dir, capsys):
    _edit_card(trusted_card_dir, lambda c: c["signed"].__setitem__("public_key_hex", "zz-not-hex"))
    code = cli.main(["verify", str(trusted_card_dir), "--fast"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("internal error:")
    assert "Traceback" not in captured.err
    assert _badge(trusted_card_dir) == "invalid"  # badge swallows it, never a 500


def test_stub_card_verify_exits_3_and_badge_is_stub(staged_context, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    _build_all_phases(staged_context)
    card_dir = Path(certify_mod.run_certify(staged_context["build_id"], context=staged_context)["card_dir"])
    monkeypatch.setenv("LAB_TIER", "minimalist")
    code, fields = _verify(card_dir, capsys, fast=True)
    assert fields["key"] == "ephemeral-stub"
    assert code == 3
    assert _badge(card_dir) == "STUB"
    # a stub card hand-edited reads tampered (tamper outranks stub).
    _edit_card(card_dir, _TAMPERS["decision upgraded"])
    assert _badge(card_dir) == "tampered"


def test_verify_usage_and_missing_card_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    assert cli.main(["verify"]) == 2
    assert cli.main(["verify", str(tmp_path / "nope")]) == 3
    assert "no dna-card.yaml" in capsys.readouterr().err
    assert cli.main(["verify", "qkz-none@0.1.0"]) == 3


@pytest.mark.xfail(strict=True, reason=(
    "LOW (TEST_REPORT F-QA-6): `verify <shard>@<ver>` joins both halves onto "
    "FORGE_ROOT without the shard/semver validation /forge applies, so "
    "`../../x@y` reads a dna-card.yaml outside FORGE_ROOT. Read-only, "
    "operator-supplied today; matters once the deferred MCP tools pass "
    "model-chosen targets."))
def test_verify_shard_at_version_rejects_traversal(tmp_path, monkeypatch, capsys):
    from arail.nucleus.cards.seal import _resolve_card_dir
    from arail.nucleus.paths import forge_root

    resolved = _resolve_card_dir("../../outside@x").resolve()
    resolved.relative_to(forge_root().resolve())  # raises ValueError today


# ═════════════════════════════════════════════════════════════════════
# 4. HAPPY / math: open_lc_judge.score degenerate cases (R3-A3)
# ═════════════════════════════════════════════════════════════════════

_R3_A3 = ("R3-A3 (BACKLOG umbrella, ARCHITECTURE §9 item 10): open_lc_judge.score "
          "has no intercept-only fallback when Δlen has zero variance (singular "
          "Hessian -> b0 stays 0 -> lc 0.5 whatever the outcomes) and no "
          "regularisation/flag under separation (lc runs to 0/1). Required before "
          "Gate B; unreachable at Gate A (real judge unwired, B7).")


def _judged(outcomes):
    from arail.nucleus.evals.open_lc_judge import JudgedPair

    return [JudgedPair(f"i{k}", ("model", "baseline"), "A", won) for k, won in enumerate(outcomes)]


def _score(outcomes, len_model, len_baseline):
    from arail.nucleus.evals.open_lc_judge import score

    judged = _judged(outcomes)
    return score(judged, len_model={j.item_id: len_model[k] for k, j in enumerate(judged)},
                 len_baseline={j.item_id: len_baseline[k] for k, j in enumerate(judged)}, seed=42)


EIGHT_OF_TEN = [True] * 8 + [False] * 2


@pytest.mark.xfail(strict=True, reason=_R3_A3)
def test_lc_equal_lengths_degenerate_falls_back_to_raw_win_rate():
    r = _score(EIGHT_OF_TEN, [100] * 10, [100] * 10)
    assert r.raw_win_rate == 0.8
    assert r.lc_win_rate == pytest.approx(0.8, abs=1e-4)


@pytest.mark.xfail(strict=True, reason=_R3_A3)
def test_lc_constant_delta_degenerate_falls_back_to_raw_win_rate():
    r = _score(EIGHT_OF_TEN, [107] * 10, [100] * 10)
    assert r.lc_win_rate == pytest.approx(0.8, abs=1e-4)


@pytest.mark.xfail(strict=True, reason=_R3_A3)
def test_lc_all_wins_equal_lengths_degenerate_is_not_a_coin_flip():
    r = _score([True] * 10, [100] * 10, [100] * 10)
    assert r.raw_win_rate == 1.0
    assert r.lc_win_rate == pytest.approx(1.0, abs=1e-4)


@pytest.mark.xfail(strict=True, reason=_R3_A3)
def test_lc_quasi_separation_degenerate_is_flagged_or_shrunk():
    # Δlen 0..9; the two losses are exactly the two longest -> separable.
    r = _score(EIGHT_OF_TEN, [100 + d for d in range(10)], [100] * 10)
    assert r.raw_win_rate == 0.8
    assert r.unreliable or 0.0 < r.lc_win_rate < 1.0


def test_lc_generic_varying_lengths_is_finite_and_in_range():
    """Control: the non-degenerate path the stub fixture exercises."""
    rng = random.Random(3)
    outcomes = [rng.random() < 0.7 for _ in range(40)]
    r = _score(outcomes, [100 + rng.randint(-30, 30) for _ in range(40)], [100] * 40)
    assert 0.0 < r.lc_win_rate < 1.0
    assert r.ci95[0] <= r.raw_win_rate <= r.ci95[1]


def test_lc_single_valid_item_uses_raw_rate():
    r = _score([True], [100], [90])
    assert r.lc_win_rate == 1.0


def test_lc_empty_input_is_zero_and_not_a_crash():
    from arail.nucleus.evals.open_lc_judge import score

    r = score([], len_model={}, len_baseline={}, seed=1)
    assert (r.n, r.lc_win_rate, r.ci95) == (0, 0.0, (0.0, 0.0))


# ═════════════════════════════════════════════════════════════════════
# 5. REGRESSION: eval_hash changes exactly when a yardstick input
#    changes, and never otherwise (certify-level, beyond T-HASH-1..3)
# ═════════════════════════════════════════════════════════════════════

from tests.nucleus.test_certify import _certify_and_read_card_eval_hash  # noqa: E402


def _edit_domain_yaml(ctx, old, new):
    path = Path(ctx["domains_dir"]) / "kernel.yaml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_eval_hash_invariant_to_build_id_version_and_signing_key(staged_context, tmp_path, monkeypatch):
    h1, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)
    h2, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=2)
    assert h1 == h2


def test_eval_hash_invariant_to_fidelity_target_and_top_n(staged_context, tmp_path, monkeypatch):
    """target is the pass bar (signed on the card), top_n is a distillation
    knob; neither is what the cert set measures."""
    h1, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)
    _edit_domain_yaml(staged_context, "target: 0.6", "target: 0.9")
    _edit_domain_yaml(staged_context, "top_n: 3", "top_n: 7")
    h2, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=2)
    assert h1 == h2


def test_eval_hash_changes_when_judge_rubric_changes(staged_context, tmp_path, monkeypatch):
    h1, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)
    monkeypatch.setattr(build_mod, "_STUB_JUDGE_RUBRIC", "stub-judge-rubric/v2: something else")
    h2, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=2)
    assert h1 != h2


def test_eval_hash_changes_when_contamination_params_change(staged_context, tmp_path, monkeypatch):
    h1, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)
    from arail.nucleus.evals import contamination

    real = contamination.ContaminationChecker

    class _Wider(real):
        def check(self):
            report = super().check()
            return contamination.ContaminationReport(
                method=report.method, params={**report.params, "n_gram": 99},
                overlap=report.overlap, temporal_leak=report.temporal_leak,
                top_offenders=report.top_offenders)

    monkeypatch.setattr(contamination, "ContaminationChecker", _Wider)
    h2, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=2)
    assert h1 != h2


def test_eval_config_lock_carries_no_path_build_id_host_or_metric(staged_context, tmp_path, monkeypatch):
    import socket as _socket

    _h, ctx = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)
    from arail.nucleus.cards.dna_v2 import load_card
    from arail.nucleus.paths import forge_root

    card_dir = forge_root() / "qkz-kernel" / ctx["version"]
    lock_text = (card_dir / "eval-config.lock").read_text()
    card = load_card(card_dir / "dna-card.yaml")
    for needle in (str(tmp_path), ctx["build_id"], _socket.gethostname(), "/Users/", "/private/",
                   str(card["composite"]["value"]), str(card["open_ended"]["eyeball_explanation"]["lc_win_rate_vs_base"])):
        assert needle not in lock_text, needle


@pytest.mark.xfail(strict=True, reason=(
    "Round-3 INFO, ticketed (BACKLOG umbrella 'Eyeball bytes read at certify "
    "time'): certify hashes the eyeball file as it is at CERTIFY time, not "
    "the bytes PC judged. Editing it between PC and certify yields an "
    "eval_hash describing a prompt set PC never judged."))
def test_eval_hash_follows_eyeball_file_edited_between_pc_and_certify(staged_context, tmp_path, monkeypatch):
    h_clean, _ = _certify_and_read_card_eval_hash(staged_context, tmp_path, monkeypatch, version_suffix=1)

    ctx = dict(staged_context, build_id="kernel-20260102T000000Z-0002", version="0.2.0")
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "k2.ed25519"))
    for phase in PHASES:
        _run_phase(ctx, phase)
    eyeball = Path(ctx["domains_dir"]) / "kernel.eyeball.txt"
    eyeball.write_text("\n".join(f"swapped after PC {i}" for i in range(10)) + "\n")
    result = certify_mod.run_certify(ctx["build_id"], context=ctx)
    from arail.nucleus.cards.dna_v2 import load_card

    card = load_card(Path(result["card_dir"]) / "dna-card.yaml")
    # PC judged exactly the same bytes as the clean run -> same yardstick.
    assert card["eval_hash"] == h_clean


# ═════════════════════════════════════════════════════════════════════
# 6. SECURITY: certify-produced seals against the REAL Rust `qkz`
# ═════════════════════════════════════════════════════════════════════

import subprocess  # noqa: E402

QKZ_BIN = os.getenv("NUCLEUS_QKZ_BIN")
_needs_qkz = [pytest.mark.requires_qkz_bin,
              pytest.mark.skipif(not QKZ_BIN or not os.path.isfile(QKZ_BIN), reason="NUCLEUS_QKZ_BIN not set")]


def _qkz_verify(seal_path: Path):
    return subprocess.run([QKZ_BIN, "isotope", "verify", "qa-probe", "--from-file", str(seal_path)],
                          capture_output=True, timeout=20)


@pytest.mark.requires_qkz_bin
@pytest.mark.skipif(not QKZ_BIN or not os.path.isfile(QKZ_BIN), reason="NUCLEUS_QKZ_BIN not set")
def test_real_qkz_verifies_certify_produced_stub_seal(staged_context, tmp_path, monkeypatch):
    """T-SEAL-8 used a hand-built minimal payload; this is the seal.json
    that `certify` actually writes (ephemeral stub key, real gate values)."""
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    _build_all_phases(staged_context)
    card_dir = Path(certify_mod.run_certify(staged_context["build_id"], context=staged_context)["card_dir"])
    result = _qkz_verify(card_dir / "seal.json")
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()


@pytest.mark.requires_qkz_bin
@pytest.mark.skipif(not QKZ_BIN or not os.path.isfile(QKZ_BIN), reason="NUCLEUS_QKZ_BIN not set")
def test_real_qkz_verifies_lab_key_seal_and_rejects_tampering(trusted_card_dir, tmp_path):
    seal_path = trusted_card_dir / "seal.json"
    ok = _qkz_verify(seal_path)
    assert ok.returncode == 0, ok.stdout.decode() + ok.stderr.decode()

    original = json.loads(seal_path.read_text())
    tampers = {
        "signature": lambda s: s.__setitem__("signature_hex", "00" * 64),
        "gate value": lambda s: s["gate_results"][0].__setitem__("value", "0" * 64),
        "run id": lambda s: s.__setitem__("pipeline_run_id", "someone-else"),
        "chain hash": lambda s: s.__setitem__("chain_hash", "e" * 64),
    }
    for label, mutate in tampers.items():
        data = json.loads(json.dumps(original))
        mutate(data["dna_seal"])
        probe = tmp_path / f"seal-{label.replace(' ', '-')}.json"
        probe.write_text(json.dumps(data))
        result = _qkz_verify(probe)
        assert result.returncode != 0, label


# ═════════════════════════════════════════════════════════════════════
# 7. SECURITY: airgapped + gateway refusal, zero egress across the
#    whole pipeline (T-GW-1 at CLI level; T-EGR-1 scope, round-1 ASK)
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("profile", ["gateway", "mixed"])
def test_cli_build_with_gateway_profile_in_airgapped_refuses_with_standard_notice(
        staged_context, monkeypatch, capsys, profile):
    from arail.airgap import AIRGAPPED_NOTICE
    from arail.config import DATA_DIR

    _edit_domain_yaml(staged_context, "profile: local", f"profile: {profile}")
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", Path(staged_context["domains_dir"]))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    monkeypatch.setenv("LAB_TIER", "maximus")

    connects = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr, *a, **k: connects.append(addr))
    egress = Path(DATA_DIR) / "egress.jsonl"
    before = egress.read_bytes() if egress.exists() else None

    code = cli.main(["build", "kernel", "--profile", "local"])
    err = capsys.readouterr().err
    assert code == 3
    assert AIRGAPPED_NOTICE in err
    assert connects == []
    assert (egress.read_bytes() if egress.exists() else None) == before
    runs = Path(staged_context["nucleus_data"]) / "runs"
    assert not runs.exists() or not any(runs.iterdir())  # refused before any run dir


def test_cli_build_with_gateway_profile_in_hybrid_refuses_as_sprint_2(staged_context, monkeypatch, capsys):
    _edit_domain_yaml(staged_context, "profile: local", "profile: gateway")
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", Path(staged_context["domains_dir"]))
    monkeypatch.setenv("LAB_MODE", "hybrid")
    monkeypatch.setenv("LAB_TIER", "maximus")
    assert cli.main(["build", "kernel", "--profile", "local"]) == 3
    assert "nucleus-sprint-2" in capsys.readouterr().err


def test_zero_egress_across_every_phase_and_certify_in_process(staged_context, tmp_path, monkeypatch):
    """Round-1 ASK: T-EGR-1 only guarded stage + CertStore.create. This
    guards PA, PA2, PB, fuse, PC and certify (+ its ledger/activity
    writes) against any non-loopback connect or DNS lookup, and checks
    egress.jsonl is never created."""
    from arail.config import DATA_DIR

    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(tmp_path / "keys" / "signing.ed25519"))
    monkeypatch.setenv("LAB_MODE", "airgapped")
    attempts = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    loopback = {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(self, address, *a, **kw):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in loopback:
            attempts.append(("connect", address))
            raise AssertionError(f"non-loopback connect: {address}")
        return real_connect(self, address, *a, **kw)

    def guarded_getaddrinfo(host, *a, **kw):
        if host not in loopback:
            attempts.append(("dns", host))
            raise AssertionError(f"non-loopback DNS lookup: {host}")
        return real_getaddrinfo(host, *a, **kw)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    _build_all_phases(staged_context)
    certify_mod.run_certify(staged_context["build_id"], context=staged_context)
    assert attempts == []
    assert not (Path(DATA_DIR) / "egress.jsonl").exists()
    assert not (Path(staged_context["nucleus_data"]).parent / "egress.jsonl").exists()


# ═════════════════════════════════════════════════════════════════════
# 8. SECURITY: secrets -- key 0600, private dirs 0700, never echoed
# ═════════════════════════════════════════════════════════════════════


def test_nucleus_private_dirs_are_0700_and_run_dirs_private(staged_context, tmp_path, monkeypatch):
    from arail.nucleus.paths import ensure_nucleus_data_layout

    root = ensure_nucleus_data_layout()
    for sub in ("", "keys", "corpus", "domains", "runs"):
        assert (root / sub).stat().st_mode & 0o777 == 0o700, sub
    rd = _build_all_phases(staged_context)
    for sub in ("eval", "extract", "train", "phase_output"):
        assert (rd / sub).stat().st_mode & 0o077 == 0, sub


def test_signing_key_is_0600_and_its_bytes_never_leave_the_key_file(staged_context, tmp_path, monkeypatch, capsys):
    """A lab-key card: the private key bytes (raw or hex) appear in no
    card, seal, report, lock, ledger, activity log, or CLI output."""
    key_path = tmp_path / "keys" / "signing.ed25519"
    monkeypatch.setenv("NUCLEUS_SIGNING_KEY_PATH", str(key_path))
    rd = _build_all_phases(staged_context)
    _rewrite_json(rd / "context.json", stub=False, provider="queuellm")
    _forge_all_stamps_real(rd)                      # (b) path: the only way to use the lab key today
    monkeypatch.delenv("ARAIL_NUCLEUS_STUB", raising=False)
    code, out, err = _cli_certify(staged_context["build_id"], monkeypatch, capsys)
    assert code == 0

    assert key_path.stat().st_mode & 0o777 == 0o600
    import base64

    raw = key_path.read_bytes()
    assert len(raw) == 32
    needles = [raw.hex(), raw.hex().upper(), base64.b64encode(raw).decode()]
    haystacks = [out, err]
    from arail.config import DATA_DIR

    for root in (_forge_root(staged_context), Path(DATA_DIR), rd):
        for f in root.rglob("*"):
            if f.is_file() and f != key_path and f.stat().st_size < 5_000_000:
                haystacks.append(f.read_bytes().decode("latin-1"))
    for hay in haystacks:
        for needle in needles:
            assert needle not in hay
        assert raw.decode("latin-1") not in hay


def test_gateway_token_from_secrets_env_never_echoed(tmp_path, monkeypatch, caplog):
    """Token sourced from secrets.env (the real path, not token=): it must
    not appear in repr, in the exception a failed call raises, in logs,
    or in egress.jsonl. DNS and connect are both blocked -- no network."""
    import logging

    from arail.nucleus.providers import gateway

    token = "qkz-build-TOKEN-5f3c9a"
    (tmp_path / "secrets.env").write_text(f"NUCLEUS_GATEWAY_TOKEN={token}\n")
    os.chmod(tmp_path / "secrets.env", 0o600)
    monkeypatch.setattr("arail.config.DATA_DIR", tmp_path)
    monkeypatch.setenv("LAB_MODE", "hybrid")
    monkeypatch.setenv("NUCLEUS_GATEWAY_URL", "https://gw.example.invalid")

    def _no_network(*a, **k):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    caplog.set_level(logging.DEBUG)

    client = gateway.GatewayClient("https://gw.example.invalid", build_id="b1")
    assert client._token == token          # really read from secrets.env
    assert token not in repr(client)
    with pytest.raises(Exception) as exc_info:
        client.teach(domain="kernel", prompt="p")
    assert token not in str(exc_info.value)
    assert token not in repr(exc_info.value)
    assert token not in caplog.text
    egress = tmp_path / "egress.jsonl"
    assert not egress.exists() or token not in egress.read_text()


# ═════════════════════════════════════════════════════════════════════
# 9. REGRESSION: frozen names (T-RT-2 widened) and /build retirement
# ═════════════════════════════════════════════════════════════════════

REPO = Path(__file__).resolve().parents[2]


def test_frozen_names_only_in_runtime_names_across_all_sprint_surfaces():
    """T-RT-2 greps src/arail/nucleus only. The sprint also added
    portal/forge_api.py and templates/forge.html; neither may spell the
    frozen runtime names either."""
    import re

    pattern = re.compile(r"aerollm|AERO_|QUEUELLM_", re.IGNORECASE)
    files = sorted((REPO / "src" / "arail" / "nucleus").rglob("*.py")) + [
        REPO / "src" / "arail" / "portal" / "forge_api.py",
        REPO / "src" / "arail" / "portal" / "templates" / "forge.html",
    ]
    offenders = []
    for f in files:
        if f.name == "runtime_names.py":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{f.relative_to(REPO)}:{n}: {line.strip()}")
    assert offenders == []


def test_no_env_writes_in_forge_surfaces():
    import re

    for f in [REPO / "src" / "arail" / "portal" / "forge_api.py"]:
        assert not re.search(r"os\.environ\[[^\]]+\]\s*=|os\.putenv|os\.environ\.(setdefault|update)\(", f.read_text())


def test_build_retirement_leaves_no_live_route_nav_or_import():
    import re

    app_src = (REPO / "src" / "arail" / "portal" / "app.py").read_text()
    build_routes = re.findall(r'@app\.(?:get|post|put|delete)\("(/(?:api/)?build[^"]*)"', app_src)
    assert build_routes == ["/build"]  # the 308 -> /forge redirect only
    nav = (REPO / "src" / "arail" / "portal" / "templates" / "_nav.html").read_text()
    assert 'href="/build' not in nav
    for f in (REPO / "src").rglob("*.py"):
        text = f.read_text()
        assert "from arail.build" not in text and "import arail.build" not in text, f
        assert "build_api" not in text, f
    assert not (REPO / "src" / "arail" / "build").exists()
    assert not (REPO / "src" / "arail" / "portal" / "build_api.py").exists()
    assert not (REPO / "src" / "arail" / "portal" / "templates" / "build.html").exists()


def test_build_retirement_known_cosmetic_leftovers_are_exactly_the_reviewed_ones():
    """REVIEW round 1 INFO named two cosmetic leftovers. Pin them so a
    NEW dangling reference is caught, and the list shrinks as they are
    cleaned up (update this test when one is removed)."""
    switcher = (REPO / "src" / "arail" / "portal" / "templates" / "_model_switcher.html").read_text()
    assert '<option value="build">Model Building</option>' in switcher
    plan_doc = (REPO / "docs" / "maximus.plan.md").read_text()
    assert "the `/build` tab explainer" in plan_doc


# ═════════════════════════════════════════════════════════════════════
# 10. SECURITY/SETUP: domain + plan inputs
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("slug", ["../../etc/passwd", "/etc/passwd", "Kernel", "-kernel", "kernel.yaml",
                                  "a" * 64, "kernel;rm -rf ~", ""])
def test_cli_plan_rejects_unsafe_domain_slugs_without_touching_disk(slug, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("arail.nucleus.domain._DOMAINS_DIR", tmp_path / "domains")
    monkeypatch.setenv("LAB_TIER", "minimalist")
    code = cli.main(["plan", slug]) if slug else cli.main(["plan", "intent", "--name", slug])
    err = capsys.readouterr().err
    assert code == 2
    assert "Traceback" not in err
    assert not (tmp_path / "domains").exists() or not any((tmp_path / "domains").iterdir())


def test_plan_intent_with_newlines_cannot_inject_domain_keys(tmp_path, monkeypatch):
    """The intent string is interpolated into the template's first
    comment line (and the deferred Buddy skill will call this same API
    with model-written text). A newline must not let it set a field."""
    from arail.nucleus.domain import load_domain
    from arail.nucleus.errors import DomainConfigError
    from arail.nucleus.plan import write_domain_template

    evil = 'x\nteacher:\n  profile: gateway\nruntime: stub\nshard: pwned\nrefresh: 1'
    path = write_domain_template(evil, "inj", domains_dir=tmp_path)
    try:
        domain = load_domain("inj", domains_dir=tmp_path)
    except DomainConfigError:
        return  # refused outright: also safe
    assert domain.teacher_profile == "local"
    assert domain.runtime == "queuellm"
    assert domain.shard == "qkz-inj"
    assert path.parent == tmp_path


def test_plan_create_refuses_to_overwrite_an_existing_domain(tmp_path):
    from arail.nucleus.errors import DomainConfigError
    from arail.nucleus.plan import write_domain_template

    write_domain_template("first", "dup", domains_dir=tmp_path)
    before = (tmp_path / "dup.yaml").read_bytes()
    with pytest.raises(DomainConfigError, match="already exists"):
        write_domain_template("second", "dup", domains_dir=tmp_path)
    assert (tmp_path / "dup.yaml").read_bytes() == before


def test_domain_yaml_bomb_and_oversize_rejected(tmp_path):
    from arail.nucleus.domain import load_domain
    from arail.nucleus.errors import DomainConfigError

    (tmp_path / "big.yaml").write_text("a: " + "x" * 2_000_000)
    with pytest.raises(DomainConfigError):
        load_domain("big", domains_dir=tmp_path)
    (tmp_path / "bomb.yaml").write_text(
        'a: &a ["x","x","x","x","x","x","x","x","x"]\n'
        'b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]\n'
        'c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]\n'
        'd: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]\n'
        'e: [*d,*d,*d,*d,*d,*d,*d,*d,*d]\n')
    with pytest.raises(DomainConfigError):
        load_domain("bomb", domains_dir=tmp_path)


def test_domain_python_yaml_tags_are_not_executed(tmp_path):
    from arail.nucleus.domain import load_domain
    from arail.nucleus.errors import DomainConfigError

    marker = tmp_path / "pwned"
    (tmp_path / "tag.yaml").write_text(
        f"student: !!python/object/apply:os.system ['touch {marker}']\n")
    with pytest.raises(DomainConfigError):
        load_domain("tag", domains_dir=tmp_path)
    assert not marker.exists()


# ═════════════════════════════════════════════════════════════════════
# 11. HAPPY/decision honesty + SETUP: pins for ticketed behaviour
# ═════════════════════════════════════════════════════════════════════


def test_decide_unmeasured_residency_does_not_cap():
    """Pins current behaviour (BACKLOG umbrella, 'residency: unmeasured
    permits CERTIFIED'): only 'violated' caps. Every card this sprint is
    'unmeasured'; v1-open's cap is what keeps Gate A cards at COMPATIBLE."""
    from arail.nucleus.evals import composite as c

    assert c.decide(0.9, 0.75, beats_base=True, residency_status="unmeasured",
                    formula_id="composite/v1") == "CERTIFIED"
    assert c.decide(0.9, 0.75, beats_base=True, residency_status="unmeasured",
                    formula_id="composite/v1-open") == "COMPATIBLE"
    assert c.decide(0.9, 0.75, beats_base=True, residency_status="violated",
                    formula_id="composite/v1") == "COMPATIBLE"


@pytest.mark.parametrize("achieved,target,beats,expected", [
    (0.75, 0.75, True, "COMPATIBLE"),        # exactly at target: v1-open cap
    (0.7, 0.75, True, "COMPATIBLE"),          # within the 0.05 margin
    (0.6999, 0.75, True, "BETA"),            # just outside the margin
    (0.99, 0.75, False, "KNOWN_ISSUE"),      # never beats base -> never ships
    (0.99, 0.75, None, "KNOWN_ISSUE"),       # unknown base treated as not-beaten
])
def test_decide_boundaries_under_v1_open(achieved, target, beats, expected):
    from arail.nucleus.evals import composite as c

    assert c.decide(achieved, target, beats_base=beats, residency_status="unmeasured",
                    formula_id="composite/v1-open") == expected


def test_first_build_without_a_cert_set_fails_at_pa2_after_phase_a(tmp_path, monkeypatch):
    """Pins the round-1 setup ASK (BACKLOG umbrella '--new-cert-version is
    undocumented'): without a minted cert set, PA runs and extracts,
    then PA2 refuses. Refusal text must at least name the fix."""
    from tests.nucleus.test_build_phases import _load_fixture_module

    built = _load_fixture_module().build(tmp_path / "fixture")
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    (domains_dir / "kernel.yaml").write_text(
        f'student:\n  base: qwen2.5-3b-instruct\ncorpus:\n  sources: [git:linux, cve:vulns]\n'
        f'  cutoff: "{built.cutoff}"\neval:\n  cert_set: frozen\n  eyeball_prompts: kernel.eyeball.txt\n'
        f'  dev_fraction: 0.1\n  cert_n: 5\nfidelity:\n  target: 0.6\n')
    (domains_dir / "kernel.eyeball.txt").write_text("\n".join(f"p{i}" for i in range(10)) + "\n")
    models_dir = tmp_path / "models"
    _real_model(models_dir / STUDENT_DIRNAME, num_parameters=3_000_000_000)
    data_dir = tmp_path / "data"
    monkeypatch.setattr("arail.config.DATA_DIR", str(data_dir))
    monkeypatch.setattr("arail.config.MODELS_DIR", str(models_dir))
    monkeypatch.setenv("ARAIL_NUCLEUS_STUB", "1")
    from arail.nucleus.corpus.stage import stage as stage_corpus
    from arail.nucleus.domain import load_domain
    from arail.nucleus.models import resolve_model

    domain = load_domain("kernel", domains_dir=domains_dir, model_resolver=resolve_model)
    stage_corpus(domain, provided_sources={"linux": built.repo_dir, "vulns": built.vulns_dir},
                 nucleus_data=data_dir / "nucleus")
    ctx = {"domain_name": "kernel", "build_id": "kernel-20260101T000000Z-0c0c",
           "domains_dir": str(domains_dir), "nucleus_data": str(data_dir / "nucleus"),
           "stub": True, "provider": "stub"}
    pa = _run_phase(ctx, "PA")
    assert pa["n_extracted_this_run"] > 0          # Phase A work already done...
    with pytest.raises(RefusedByPolicy) as exc_info:
        _run_phase(ctx, "PA2")                     # ...before the missing cert set surfaces
    assert "cert" in str(exc_info.value).lower()


# ═════════════════════════════════════════════════════════════════════
# 12. Brief §7 "contamination blocks >= 1 %": the exact boundary and the
#     date-compare edge (round-1 ASK, BACKLOG umbrella "Contamination")
# ═════════════════════════════════════════════════════════════════════

_LONG = " ".join(f"word{i}" for i in range(30))


def _checker_with_dups(n_dups, *, n_cert=800, train_date="2026-01-01"):
    from arail.nucleus.evals import contamination as cm

    cert = [{"id": f"c{i}", "date": "2026-07-01", "text": f"unique cert text number {i} " + _LONG}
            for i in range(n_cert)]
    checker = cm.ContaminationChecker(cert, cutoff="2026-06-01")
    for i in range(n_dups):
        checker.observe_train_doc(f"unique cert text number {i} " + _LONG, date=train_date)
    checker.observe_train_doc("an unrelated train document with its own words " * 3, date=train_date)
    return checker.check()


def test_contamination_exactly_one_percent_blocks():
    report = _checker_with_dups(8)
    assert report.overlap == 8 / 800
    assert report.contaminated is True


def test_contamination_just_under_one_percent_passes():
    report = _checker_with_dups(99, n_cert=10_000)
    assert report.overlap == 99 / 10_000
    assert report.contaminated is False


def test_contamination_train_item_after_cutoff_is_a_temporal_leak():
    report = _checker_with_dups(0, train_date="2026-06-02")
    assert report.temporal_leak == "2026-06-02"
    assert report.contaminated is True


@pytest.mark.xfail(strict=True, reason=(
    "Round-1 ASK, still open (BACKLOG umbrella 'Contamination scope'): dates are "
    "compared as strings, so a train item timestamped ON the cutoff day "
    "('2026-06-01T12:00:00Z' > '2026-06-01') is reported as a temporal leak "
    "and would block certify."))
def test_contamination_timestamp_on_cutoff_day_is_not_a_leak():
    report = _checker_with_dups(0, train_date="2026-06-01T12:00:00Z")
    assert report.temporal_leak == "none"


# ═════════════════════════════════════════════════════════════════════
# 13. QA round 2 (F3 root cause): the nucleus-owned half of the full-suite
#     order dependency
# ═════════════════════════════════════════════════════════════════════

import importlib.util as _ilu  # noqa: E402
import sys  # noqa: E402


@pytest.mark.skipif(_ilu.find_spec("mlx_lm") is None,
                    reason="only meaningful where mlx_lm is installed (Apple Silicon dev box); "
                           "CI's ubuntu runner has no mlx_lm, so this would trivially pass there")
@pytest.mark.xfail(strict=True, reason=(
    "F3 (TEST_REPORT round 2, BACKLOG 'tests/nucleus combined with the full root suite'): "
    "preflight._probe_mlx_lm_version() does a real `import mlx_lm` in-process. On a Mac "
    "with mlx_lm installed, that leaves mlx_lm in sys.modules for the rest of the pytest "
    "process; router.core._is_importable('mlx_lm') then returns True where it otherwise "
    "would not, ModelRouter auto-detects 'mlx', and /api/chat/models falls back to its "
    "3-key error payload. Probe the version without importing (importlib.util.find_spec + "
    "importlib.metadata.version)."))
def test_mlx_lm_version_probe_does_not_import_mlx_lm_into_the_process(monkeypatch):
    for name in [m for m in sys.modules if m == "mlx_lm" or m.startswith("mlx_lm.")]:
        monkeypatch.delitem(sys.modules, name)
    row = pf._probe_mlx_lm_version()
    assert row.available != "absent"          # it really did find mlx_lm on this machine
    assert "mlx_lm" not in sys.modules
