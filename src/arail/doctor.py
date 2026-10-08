"""Explicit lab checkup — the user-invoked home for every probe.

Because the portal boots quietly (no automatic package/version/model checks —
see ``arail.autochecks``), this module is where a user goes to actually run
them. Invoked via ``./arailctl doctor`` (which also does the venv/import
smoke-test). Everything here is on-demand: probing packages, models, and the
network is fine *because the user asked*.

Sections:
  • Environment   — lab mode + egress guard state (load-bearing airgap check)
  • Models        — one registry preflight + the tier table
  • Knowledge base— PKB index readiness/staleness
  • Components     — installed package versions (importlib.metadata)
  • Updates        — remote update check (``--updates``; hybrid only)

Run: ``python -m arail.doctor`` or ``python -m arail.doctor --updates``.

Exit-code contract (ARCHITECTURE.md sprints/2026-07-29-elite-cli §12/§13):
  0  healthy — every required check passed (optional/info findings are
     reported but never fail the run by default)
  3  degraded — a required check failed (egress guard not installed, PKB
     root unwritable), OR ``--strict`` promoted an info-level finding
     (e.g. no model installed) to degraded
This module never returns 1 itself — "broken" (no .venv, `import arail`
fails) is caught by ``./arailctl doctor``'s bash wrapper before this module
is even importable.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata


@dataclass
class Finding:
    """One doctor check's outcome.

    level="required": a failure (ok=False) always degrades the exit code.
    level="info": a failure only degrades under --strict (ARCHITECTURE.md
    §5.2: "optional binaries/models missing = INFO").
    """

    name: str
    level: str  # "required" | "info"
    ok: bool
    detail: str = ""


_FINDINGS: list[Finding] = []


def _record(name: str, level: str, ok: bool, detail: str = "") -> None:
    _FINDINGS.append(Finding(name, level, ok, detail))


def _p(line: str = "") -> None:
    print(line, flush=True)


def _section(title: str) -> None:
    _p()
    _p(f"── {title} " + "─" * max(0, 60 - len(title)))


def check_environment() -> None:
    _section("Environment")
    try:
        from arail import airgap
        mode = airgap.lab_mode()
        _p(f"  lab mode          : {mode}"
           + ("  (airgapped — no cloud egress)" if airgap.is_airgapped()
              else "  (hybrid — cloud providers allowed)"))
    except Exception as e:  # noqa: BLE001
        _p(f"  lab mode          : ? ({type(e).__name__}: {e})")
    try:
        from arail import egress
        # doctor runs in its own process — install the guard here too so this
        # checkup's own probes (esp. --updates) obey the same airgap policy the
        # portal enforces, and the report reflects a genuinely guarded process.
        egress.install_guard()
        installed = bool(getattr(egress, "_INSTALLED", False))
        _p(f"  egress guard      : {'installed' if installed else 'NOT installed'}")
        # Required check (ARCHITECTURE.md §5.2): the guard is expected to be
        # installable in every supported environment (including CI, A8) —
        # if it isn't, that's a real problem, not an optional one.
        _record("egress_guard", "required", installed)
    except Exception as e:  # noqa: BLE001
        _p(f"  egress guard      : ? ({type(e).__name__}: {e})")
        _record("egress_guard", "required", False, str(e))
    _p(f"  ARAIL_AUTOCHECKS  : {os.getenv('ARAIL_AUTOCHECKS', '0')} "
       "(background checks/warmers; default off = quiet boot)")


def check_models() -> None:
    _section("Models")
    try:
        from arail.registry import get_registry, health
        reg = get_registry()
        reg._ensure_loaded()
        # One explicit preflight — announce=False so we format the table here
        # instead of spraying the activity log.
        health.run_preflight(reg, announce=False)
        entries = list(reg.entries.values())
        if not entries:
            _p("  (no models configured)")
            # Info, not required (ARCHITECTURE.md §5.2: "optional
            # binaries/models missing = INFO") — the CI runner legitimately
            # has none pulled (A8); --strict is what promotes this.
            _record("model_installed", "info", False, "no models configured")
            return
        any_healthy = False
        for e in entries:
            if not e.enabled:
                continue
            h = e.health
            if h.status == "healthy":
                any_healthy = True
            where = e.endpoint or ("QueueLLM (in-process)"
                                   if e.provider_type == "aerollm" else "local")
            lat = f", {h.latency_ms:.0f}ms" if h.latency_ms is not None else ""
            detail = f" — {h.detail}" if h.detail else ""
            tier = f"tier{e.tier}" if e.tier is not None else "     "
            _p(f"  [{tier}] {e.display_name} @ {where} "
               f"({h.status}{lat}){detail}")
        _record("model_installed", "info", any_healthy)
    except Exception as e:  # noqa: BLE001
        _p(f"  model check failed: {type(e).__name__}: {e}")


def check_knowledge_base() -> None:
    _section("Knowledge base (PKB index)")
    try:
        from arail.pkb_index import ensure_ready
        # REVIEW3.md BLOCK-3: doctor is a diagnostic and must never build,
        # write, or embed anything -- build=False makes ensure_ready
        # genuinely read-only (inspect existing state and report; the
        # detailed status, including whether an index exists at all, is
        # printed by the embedding-status block right below). Before this,
        # a World with no pkb_pages table yet took ensure_ready's default
        # build=True path, which ran a full embed_documents() pass over
        # the whole corpus and wrote a brand-new index -- reproduced by
        # the reviewer, by accident, on the operator's real `finance`
        # World.
        ensure_ready(build=False)
        _p("  pkb_pages index   : checked (read-only — doctor never builds)")
    except Exception as e:  # noqa: BLE001
        _p(f"  pkb_pages index   : NOT ready ({type(e).__name__}: {e})")

    # C4/C5 (arail2-tier1-integration): the embedding provider and the
    # vector-index provenance sidecar. Required — ARCHITECTURE.md is
    # explicit that no query should be served from a table whose
    # provenance disagrees with the spec, and doctor is where that
    # disagreement (or a plain unreachable-Ollama outage the search path
    # would otherwise degrade silently into) becomes visible to a human.
    try:
        from arail.dbspec.embed import probe as embed_probe
        ok, detail = embed_probe()
        _p(f"  embedding provider: {'reachable' if ok else 'NOT reachable'} — {detail}")
    except Exception as e:  # noqa: BLE001
        _p(f"  embedding provider: ? ({type(e).__name__}: {e})")

    try:
        from arail import pkb_index
        embed_ok, embed_reason = pkb_index.embedding_status()
        if embed_ok:
            _p("  vector index status: ok")
        else:
            _p(f"  vector index status: degraded — {embed_reason}")
        # REVIEW2.md: couple the exit code to the structured reason CODE,
        # never to a substring match on prose. "dimension" and "provenance"
        # are both C4 violations — no query should ever be served from a
        # table whose vector width or provenance disagrees with the spec —
        # and both are REQUIRED (exit 3), including the dimension-mismatch
        # case, which is the common state of every legacy 128-dim lab
        # (previously INFO-only here because the message didn't contain the
        # word "provenance" — a real gap, now fixed). "provider" (Ollama
        # simply not reachable/pulled yet) and "empty" (index not built
        # yet) stay INFO-only, matching "no model configured" (A8: a clean
        # machine/CI runner legitimately has neither, and doctor must stay
        # exit 0 there unless --strict is passed) — C5 promises setup never
        # fails on this, and doctor must not contradict that promise.
        codes = pkb_index.degraded_codes()
        # "backend" (defect B, sprints/2026-08-10-arail2-persistence-
        # instantiated): LanceDB is a hard dep in both tiers, so an
        # interpreter that cannot import it is a broken environment, not an
        # optional-feature gap — same tier as "dimension"/"provenance".
        required_codes = set(codes) & {"dimension", "provenance", "backend"}
        _record("embedding_provenance", "required", not required_codes, embed_reason)
        _record("embedding_reachable", "info", embed_ok, embed_reason)
    except Exception as e:  # noqa: BLE001
        _record("embedding_reachable", "info", False, str(e))

    # Required check (ARCHITECTURE.md §5.2/§13): "PKB root unwritable" is one
    # of doctor's named degraded conditions. Probed with os.access rather
    # than an actual write, so a healthy run never leaves a stray file.
    try:
        from arail.pkb import _pkb_root
        root = _pkb_root()
        _p(f"  pkb root          : {root}")
        writable = os.access(str(root), os.W_OK) if root.exists() \
            else os.access(str(root.parent), os.W_OK)
        if not writable:
            _p("  pkb root writable : NO")
        _record("pkb_writable", "required", writable, str(root))
    except Exception as e:  # noqa: BLE001
        _record("pkb_writable", "required", False, str(e))

    # The Compiled-KB gate is the ONLY thing agents build on, and it fails
    # closed by design — every failure mode ends in "zero approved hits",
    # which is indistinguishable from "nothing approved yet" and reports no
    # error anywhere. That silence is how a lab ran for two weeks with 554
    # of 556 approvals pointing at files a World switch had deleted, while
    # the Researcher kept concluding its hypotheses were "not measurable".
    # Doctor is where that becomes visible.
    try:
        from arail import compiled_kb, world_mount
        approved = compiled_kb.approved_paths()
        dangling = compiled_kb.dangling_paths()
        live = len(approved) - len(dangling)
        _p(f"  compiled KB       : {live} live / {len(approved)} approved")
        if dangling:
            _p(f"  dangling approvals: {len(dangling)} point at files that no longer exist")
            _p("                      fix: ./arailctl pkb prune")
        # An empty gate is worth naming even when nothing dangles: it means
        # agents have no approved truth at all, which is a legitimate state
        # on a fresh lab and a bug-shaped one on a lab with a World mounted.
        if live == 0:
            rec = world_mount.current_mount()
            if rec is not None:
                _p(f"  ⚠ no approved knowledge while World '{rec.world}' is mounted —")
                _p("    agents will find nothing. Approve terms on /dac (Knowledge).")
        _record("compiled_kb_dangling", "info", not dangling,
                f"{len(dangling)} dangling of {len(approved)}")
    except Exception as e:  # noqa: BLE001
        _record("compiled_kb_dangling", "info", True, f"skipped: {e}")


def check_provisioning() -> None:
    """The class check (sprints/2026-08-10-arail2-persistence-instantiated
    §5): every declared mechanism names its own instantiation predicate.
    "Declared and not instantiated" is always a finding here, never
    silence — this is what would have caught defect A (the relational
    store) and defect B (the vector backend) before either shipped."""
    _section("Provisioning (declared vs. instantiated)")
    try:
        from arail import provisioning
        from arail import config
        # REVIEW.md ASK-1: repo_root=os.getcwd() mis-reported (a silent,
        # non-degrading "unavailable" on a healthy DB) whenever doctor ran
        # from a subdirectory. Derived from the package location instead —
        # the same technique arail.dbspec.ensure.DEFAULT_SPEC_DIR already
        # uses — so it no longer depends on where the caller happened to
        # be standing.
        from arail.dbspec.ensure import DEFAULT_SPEC_DIR
        repo_root = DEFAULT_SPEC_DIR.parent
        assertions = provisioning.evaluate_all(
            repo_root=str(repo_root), data_dir=str(config.DATA_DIR))
        for a in assertions:
            mark = "OK" if not a.finding else ("MISSING" if a.declared else "off")
            _p(f"  [{a.tier:<8}] {a.key:<22}: {mark}"
               + (f" — {a.detail}" if a.detail else ""))
            if a.finding and a.action:
                _p(f"             fix: {a.action}")
            _record(f"provisioning_{a.key}", a.tier, not a.finding, a.detail)
    except Exception as e:  # noqa: BLE001
        _p(f"  provisioning check failed: {type(e).__name__}: {e}")


def check_components() -> None:
    _section("Components (installed package versions)")
    # importlib.metadata only — no subprocess. The portal's Admin "Check
    # versions" button covers shell tools (ollama/npm/docker) on demand.
    for pkg in ("fastapi", "uvicorn", "lancedb", "pydantic", "requests",
                "httpx", "neo4j"):
        try:
            _p(f"  {pkg:<16}: {importlib_metadata.version(pkg)}")
        except importlib_metadata.PackageNotFoundError:
            _p(f"  {pkg:<16}: not installed")
        except Exception:  # noqa: BLE001
            _p(f"  {pkg:<16}: ?")
    # ollama is a shell tool, not a Python package — probe it here because the
    # user explicitly asked for a checkup.
    import shutil
    import subprocess as sp
    if shutil.which("ollama"):
        try:
            r = sp.run(["ollama", "--version"], capture_output=True,
                       text=True, timeout=5)
            _p(f"  ollama          : {r.stdout.strip() or r.stderr.strip() or '?'}")
        except Exception:  # noqa: BLE001
            _p("  ollama          : present (version probe failed)")
    else:
        _p("  ollama          : not on PATH")


def check_updates() -> None:
    _section("Updates (remote check)")
    try:
        from arail import airgap
        if airgap.is_airgapped():
            _p("  skipped — lab is airgapped (set LAB_MODE=hybrid to check)")
            return
    except Exception:  # noqa: BLE001
        pass
    import json
    import subprocess as sp
    from pathlib import Path
    manifest_path = Path.cwd() / "components.json"
    if not manifest_path.exists():
        _p("  no components.json found")
        return
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:  # noqa: BLE001
        _p(f"  could not read components.json: {e}")
        return
    checked = 0
    for c in manifest.get("components", []):
        ccmd = c.get("check_cmd")
        if not ccmd:
            continue
        checked += 1
        try:
            r = sp.run(ccmd, shell=True, capture_output=True, text=True, timeout=30)
            status = "ok" if r.returncode == 0 else f"exit {r.returncode}"
            head = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
            _p(f"  {c.get('name', '?'):<24}: {status}  {head}")
        except Exception as e:  # noqa: BLE001
            _p(f"  {c.get('name', '?'):<24}: probe failed ({type(e).__name__})")
    if not checked:
        _p("  no components define a check_cmd")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m arail.doctor",
        description="Explicit lab checkup (models, KB, components, egress).")
    ap.add_argument("--updates", action="store_true",
                    help="also run the remote update check (hybrid mode only)")
    ap.add_argument("--strict", action="store_true",
                    help="promote optional/info findings (missing model, "
                         "missing optional binary) to degraded")
    args = ap.parse_args(argv)

    _FINDINGS.clear()
    _p("ARAIL doctor — explicit checkup (nothing here runs automatically at boot)")
    check_environment()
    check_models()
    check_knowledge_base()
    check_provisioning()
    check_components()
    if args.updates:
        check_updates()

    # Exit-code contract (ARCHITECTURE.md §12/§13): a failed "required"
    # finding always degrades; a failed "info" finding only does under
    # --strict. ./arailctl doctor's bash wrapper folds this module's exit
    # code into its own findings tally (uvicorn presence, optional
    # binaries) to produce the final `doctor` exit code.
    degraded = any(
        not f.ok and (f.level == "required" or args.strict)
        for f in _FINDINGS
    )
    _p()
    if degraded:
        _p("doctor: degraded — see findings above"
            + (" (--strict)" if args.strict else ""))
    else:
        _p("doctor: done")
    return 3 if degraded else 0


if __name__ == "__main__":
    sys.exit(main())
