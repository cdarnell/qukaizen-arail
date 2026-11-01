# Review: DaC generates ARAIL Worlds (`dac_world` migration)

**Date:** 2026-07-19
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `qukaizen-dac@40269b5` (commits `c7a3969`→`40269b5`); `qukaizen-arail@859f577` (commits `2eb41ea`, `a6dc2a0`, `859f577`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `9c32537` (incl. sidecar-preservation addendum)

## Verdict: WEAK_PASS

No BLOCKs. The nine failure modes I identified at design time (F1–F9) plus the addendum's sidecar policy are all correctly implemented and *genuinely* tested — I re-ran both suites and read every moved file rather than trusting the self-report. Two ASKs remain as documented follow-ups: the ARAIL dependency is pinned to a mutable branch, and step 9 (the superseding ADR / VISION / CLAUDE.md reconciliation) is undone, so the governing docs currently assert the *opposite* of what the code does. Neither blocks merge; both need a home.

## Spec adherence

Strong. Every interface contract in ARCHITECTURE.md was honored, and the two deltas the builder flagged are the *correct* engineering calls, not drift:

- **`validate_bundle_content` (the actual fix, F1):** called in **both** `write_bundle` (`seal.py:199`, after `_build_face`, before `mkdir`/any write) and `reseal_bundle` (`seal.py:290`, before the tmp/old sibling dance). The move from the ARAIL hotfix preserved this exactly — I confirmed the call sites in the *final* `dac_world/seal.py`, not just the `2eb41ea` hotfix. `write_bundle` raising leaves no directory (`mkdir` is at line 220, strictly after the validator); `reseal_bundle` raising leaves no `.reseal-tmp`/`.reseal-old` siblings (validator runs before they are created). Both verified by test (`test_write_bundle_refuses_placeholder_face_content`, `test_reseal_bundle_refuses_placeholder_face_content`).
- **`router=None` → `ValueError` (F4 delta):** reasonable and verified safe. I grepped every `forge_world(` caller in ARAIL: both production sites (`world_routes.py:175,181`) pass `router=router_`; all six test sites pass a router explicitly. No caller relied on the removed `arail.router.ModelRouter` fallback. No regression.
- **`theme_validator` injection (F4 delta):** wired at exactly the 3 ARAIL call sites the doc predicted — `write_bundle` (`world_routes.py:421-422`), `reseal_bundle` in `_reseal_and_swap` (`539-540`), `reseal_bundle` in the grow loop (`888-889`) — all passing `theme_validator=parse_world_theme`. Fail-closed when a theme override is present but no validator injected (`seal.py:102-106`; test `test_face_theme_override_without_injected_validator_fails_closed`). Correct.
- **Files beyond the doc's list (`parsing.py`, `reconcile.py`):** justified. Both are pure, model-free, stdlib-only, use the same injected-`router` pattern, and are depended on by ARAIL's portal via `wf.<name>`. I read both in full and grepped them specifically (the doc's extra-scrutiny target): **zero** `arail` imports. Leaving them in the shim would have recreated the drift this migration exists to kill.

Implementation order followed; step 8 correctly *stopped* mid-sweep on the real sidecar bug and routed it back to design (the addendum) rather than improvising — exactly the protocol.

## Code quality findings

- [INFO] The pure core is a faithful, well-annotated port. `from __future__ import annotations` in every module keeps the `list[dict]`/`dict[str, bytes]` hints working on the declared `requires-python = ">=3.9"` (verified: imports and full suite run clean under system Python 3.9.6).
- [INFO] `reseal_bundle`'s sidecar loop (`seal.py:304-316`) dispatches `copytree` for directories and `copy2` for files — the nested-directory case the addendum called out is handled and tested (`test_reseal_preserves_sidecar_directory`). Collision-safe: regenerated names are skipped before the copy, so `write_bundle`'s tmp output is never clobbered by a stale carry-over.
- [INFO] `REGENERATED_FILES` is derived from `SEALED_FILES` (`seal.py:46`), so it cannot silently drift from what `write_bundle` iterates; `test_reseal_regenerated_files_not_treated_as_sidecars` is a real guard (it would fail loudly if a future 11th output were added without updating the set).

## Security findings

- [INFO] **F4 (no back-import) — verified independently, not on faith.** Precise import-statement grep (`^\s*(import|from)\s+arail`) across `dac_world/` returns nothing; the only `arail` mentions are docstring prose. The static CI guard (`test_no_arail_backimport.py`) is genuinely AST-based (walks `ast.Import`/`ast.ImportFrom` over `rglob("*.py")`), so it cannot false-positive on the prose and cannot false-negative on an aliased import. Solid permanent net.
- [INFO] **F6 (provenance laundering) — holds after the move.** `_build_face` force-derives `schema`/`world`/`provenance_tier`/`provenance_counts` *last* (`seal.py:113-118`), after applying the `_FACE_DISPLAY_KEYS` overrides, so an authored `face_override` can never relabel a dreamed World `sourced`. Tier itself is computed by `compute_provenance_tier`, never asserted. Test `test_face_integrity_fields_force_derived` confirms an override of `provenance_tier` does not stick.
- [INFO] **F7 (SKILL.md injection) — sanitizers byte-for-byte preserved.** `sanitize_frontmatter_scalar` (CR/LF collapse + quote-escape) and `sanitize_body_field` (CR/LF collapse + ZWNJ-neutralize a leading `#`/`-`/`>`/`` ` `` control token) are intact in `skill.py`; the parametrized adversarial containment test passes, and asserts no injected physical line starts a frontmatter fence, H1/H2, or code fence.
- [INFO] **Sidecar containment-posture change (addendum) reviewed and accepted.** Reseal now preserves *any* non-regenerated file (denylist), not a 2-name allow-list. The addendum's threat-model argument holds for ARAIL specifically: single-user by charter (no `user_id`, ever — matches the workspace "arail-never-multi-user" constraint), reseal byte-copies and never interprets sidecars, and load-bearing content still goes through `validate_bundle_content` + `verify_seal`. The warn-loud step (`_log.warning` on any name outside `KNOWN_SIDECARS`) recovers visibility without the data-loss cost. The competing risk (silently deleting the Growth Engine's reversible-changes log) was *realized* this sprint; keeping an inert stray file is the right trade.

## Test coverage assessment

Every F-row has a corresponding, non-tautological test. I re-ran both suites:

- **DaC `tests/python/`:** 34/34 passing (via ARAIL's venv, so the `requires_arail` cross-repo round-trip assertions actually executed rather than skipping). Also confirmed `import dac_world` resolves standalone with no ARAIL present.
- **ARAIL side** (`test_dac_world_shim_smoke` + `test_golden_bundle_parity` + `test_world_forge_seal` + `test_world_forge_pipeline` + `test_world_forge_gate`): 64/64 passing.

Adversarial checks I specifically demanded:

- **Item 10 (sidecar tests are a real regression net, not builder bias):** confirmed. `test_reseal_preserves_arbitrary_sidecar` is parametrized over `totally-novel-sidecar.json` — a name that never appeared in any allow-list, past or present. This case **fails** against the old hardcoded `("model.json", "review.json")` loop and passes only under a genuine preserve-everything-not-regenerated policy. `test_reseal_warns_on_unknown_sidecar` asserts the warning fires for an unknown name and *not* for a `KNOWN_SIDECARS` entry. These test the policy, not a fixed name set.
- **Item 7 / F9 (golden bundle asserts byte-identity, not something weaker):** confirmed. Both repos' parity tests assert `fresh == golden` per file (`assert (out/name).read_bytes() == path.read_bytes()`), and the committed fixtures are byte-identical across repos (`diff -rq` → IDENTICAL). This is real byte parity for a pinned `created_at`, on both the direct-`dac_world` path (DaC) and the shim path (ARAIL).

**Gaps (minor, non-blocking):**

- [INFO] The golden parity loop iterates the *fixture's* files, so an unexpected **extra** file emitted by `write_bundle` (one not in the fixture) would not be caught. Low risk — the manifest's `files` hash map covers the sealed set; a stray non-sealed output is unlikely. Worth a one-line symmetric-set assertion if this test is ever hardened.
- [INFO] `reseal_bundle` pre-validates `overrides` (preserved display fields) *and* `write_bundle` re-validates the fully-built `face` with defaults filled. If a user hand-deleted a face key that `write_bundle` would otherwise default, the pre-check could raise `ContentInvalid: empty` for a field the sealer would have supplied. This is fail-closed and only reachable by manual bundle editing — acceptable, noted for completeness.

## Performance assessment

Out of scope per the architecture doc (forge is model-bound/interactive; sealing is trivial I/O). Nothing on a hot path. Concur.

## Tech debt delta vs ARCHITECTURE.md prediction

Matches the doc's forecast, plus the addendum's net-negative sidecar fix (bug *class* closed permanently). No unanticipated debt introduced by the implementation itself. The two predicted debts that remain open are the ASKs below.

- [INFO] Item 9 verified: `dac_compiler.py` is **untouched** since `38aa690` (`git diff` empty), as the invariant required. `lancedb_sink.py`/`world_to_toon.py` also untouched. The `dac_world` package is cleanly isolated (`pyproject.toml` includes only `dac_world*`).

## Required actions before merge

None are hard blockers. The following must be tracked as follow-up tickets (not left as unowned TODOs):

1. **[ASK] Move ARAIL's `dac_world` dependency pin off the mutable branch.** `qukaizen-arail/pyproject.toml:45` pins `dac_world @ git+ssh://...@qukaizen/hungry-bouman-d0761f` — a *branch*. A force-push, rebase, or branch deletion silently changes or breaks ARAIL's reproducible build (this is Failure F3's exact blast radius). The builder flagged this. Move the pin to a tag or commit SHA once this branch merges to `main`. Until then, local dual-repo dev is on an editable install (verified: `dac_world` resolves to this worktree in ARAIL's venv), so nothing is broken *today* — but the git pin is not yet reproducible.
2. **[ASK] Land step 9 — the doc/ADR reconciliation.** This migration deliberately reversed the "no cross-repo runtime imports" stance that ARAIL's `docs/adr/0002-chat-memory-and-the-dac-boundary.md`, both repos' `CLAUDE.md`, and DaC's `VISION.md`/`BLUEPRINT.md` still assert. Assumption #1 required the superseding ADR *in both repos* as a condition of proceeding. The human sign-off happened (build proceeded); the ADRs and doc edits did not. Until they land, the governing docs contradict the code — a reader of VISION/ADR-0002 will believe the opposite of what ships. Also decide the DaC TypeScript forge's fate (retire vs designate reference), or the repo carries three Python + one TS copy of the logic — the "positive (bad)" debt branch the doc warned about.

Both are documentation/governance follow-ups, not code defects. The code itself is correct, isolated, and well-covered — WEAK_PASS: ship with these two notes tracked.
