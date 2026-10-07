# Review: QueueLLM display rename + honest local cost

**Date:** 2026-10-07
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `c8169b93` (log commit `86145282`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `20726778`
**Diff reviewed:** `origin/main` `5f775f1c`...`c8169b93` (60 files, +1791/-206)

## Verdict: WEAK_PASS

No BLOCK findings. The ASKs below are filed as follow-ups. They do not gate the PR.

## Spec adherence

- **Frozen surface held (F1-F5).**
  - `git diff --stat 5f775f1c...HEAD -- NOTICE THIRD-PARTY-LICENSES lab/worlds docs/archive BUNDLE.json` is empty. No historical sprint dir was touched.
  - The diff removes no identifier from `src/`, `scripts/` or `arailctl`. The only `-` lines that mention `"aerollm"` or `aerollm_api` are dict **values** (`"aerollm": "AeroLLM"` → `"QueueLLM"`) and an `info` message. In every case the key or module name is unchanged.
  - `AeroLLMBackend`, `tier1-aerollm`, `class_name`, `provider`/`runtime` values and `data-view` ids are untouched. `test_queuellm_display_ids.py` and `test_queuellm_rename_frozen_surface.py` pin them.
- **Env var names (F2).** I compared the set of `[A-Z_]*AERO[A-Z_]*` tokens in every changed file against base. It is identical for every production file. Only the new test files and sprint docs differ, as expected.
- **Scripts (F15).** `bash -n` passes on every `scripts/*.sh` and on `arailctl`. The sha and tag constants in `build-aerollm.sh` did not change (T-PIN).
- **Label JSON values changed:** `label` in `/api/chat/models` optional backends, `_OPTIONAL_CHAT_BACKEND_CONFIG.label`, and the system-graph and provider display maps. These are display fields by design (A1: nothing compares them). This matches the architecture.
- **Drift from the architecture (acknowledged in BUILD_LOG, accepted):**
  - A5 was false: registry notes persist. The builder added an exact-text note refresh.
  - The inventory under-counted. The guard caught the missing rows.
  - `package-aerollm-bundle.sh:77` and the shell comments still say aeroLLM/AeroLLM. Both are left on purpose, per A6 and the comment rule.

## Code quality findings

- [INFO] `_resolve_chat_cost` (app.py, about 50 lines) is over the 30-line guide. The extra length is the "local never reports a cloud charge" guard, which runs whatever attribution found. That guard is the right shape. I would not split it in this sprint.
- [INFO] One expression relies on Python precedence: `rec.get("energy_source") or "estimated" if ... else None` parses as `(a or b) if c else None`. That is correct. Parentheses would make it obvious.
- [INFO] The registry-note migration (`store.py`) is correct and narrow:
  - It replaces the note only when it equals `_LEGACY_TIER1_NOTE` exactly, so a hand-edited note survives (`test_user_edited_note_is_not_overwritten`).
  - It touches only the `tier1-aerollm` entry, which runs through the existing `existing.enabled` branch, and sets `changed` so the registry is persisted.
  - It is idempotent: after the first refresh the note no longer matches.
- [ASK] The guard allowlist entry `(?<=['"])aerollm(?=['"])` hides **any** quoted lowercase `aerollm` in any scanned file, not only inside validation messages. It is correct today because a quoted lowercase token is an id. But it would also hide a future `label: 'aerollm'`. Follow-up: narrow it to the specific message contexts, or require the match to sit inside a recognized id position.
- [ASK] The guard's shell scope is a hard-coded list of five files. `package-aerollm-bundle.sh` is excluded by design (A6). Any new user-facing script would be unscanned. Follow-up: glob `scripts/*.sh` with an explicit exclusion set instead.

## Security findings

- [INFO] No new input surface, network call, deserialization, or dependency. Cost fields are computed server-side from internal records. No user-controlled value reaches them.
- [INFO] Script edits are message-only. The `https`-only bundle URL check, sha verification, and platform guard in `build-aerollm.sh` are unchanged.

## Cost change assessment (correctness, honesty, compatibility)

- **Correct and honest.**
  - `cost_source()` is an allowlist: unknown backends, `None`, and `openai_compat` → `unpriced`, never `local` (F11, table test over every backend).
  - `cloud_cost_usd` is non-null only when the source is `billed_estimate`.
  - The chat result builder forces `null` for any non-billed backend, even if it is handed a poisoned record (`test_result_local_is_forced_null_even_with_a_poisoned_record`).
  - The owner's case (local Qwen3-30B via `aerollm`) now reports `cloud_cost_usd: null, cloud_cost_source: "local"`.
- **No fabricated numbers.** The simulated figure is still there, renamed honestly as `cloud_equivalent_usd` with `cloud_equivalent_source: "simulated"`. Energy carries `energy_source: "estimated"`. `test_non_null_usd_always_has_a_source` enforces this.
- **Backwards-compatible keys.**
  - `cloud_cost_usd` and `energy_cost_usd` stay in the chat result and in `/api/system/costs.last_record`. Only their value semantics change (A2: no in-repo renderer consumes them).
  - History records keep `cloud_usd`, `energy_usd` and `raw_cloud_usd`.
  - Legacy history records without a source are re-gated by backend in `_costs_record_fields`.
- **Attribution (F12).** The deep and runtime branches now pass the record from *this* call explicitly. Interleaved turns are tested.
- [INFO] The legacy history key `cloud_usd` still holds the simulated figure under a name that reads like a real charge. It was kept for compatibility, and sits next to the labelled keys. Retire it when the UI helpers move to `cloud_equivalent_usd`.

## Builder open items: dispositions

1. **`config/tuning.yml` `aerollm_package` pip ref.** Leave it, as the builder did. It is a value compared against `choices` and stored in experiment state, so changing it is a config migration, and the old URL redirects. The allowlist entry gives the reason. Follow-up: migrate `current` and `choices` together in the arail integration-surface sprint.
2. **Router-branch fallback race.** Accepted. The router path uses match-or-`unattributed`. The remaining window is two concurrent router turns with the **same** backend and model. Even then:
   - For a local backend, `cloud_cost_usd` is still forced to `null`. The worst case is a swapped simulated or energy figure for an identical model class.
   - For a cloud backend, the worst case is a swapped estimate between two calls to the same model.
   Neither fabricates a charge. Follow-up: have `router/core.py` return its cost record.
3. **54 pre-existing failures.** I spot-checked `tests/test_aerollm_defaults.py`, which the sprint touched. 4 tests fail with `KeyError: 'kwargs'` both on this branch and on a clean `origin/main` worktree, so the failure is not caused by this diff. My targeted run of 17 affected test modules: 216 passed, 1 skipped, and only those 4 failed. I did not re-run the full suite. QA should run it on a clean machine, as BUILD_LOG asks.

## Test coverage assessment

Every failure-mode row has a test or a documented acceptance:

| Row | Covered by |
|---|---|
| F1 | T-IDS |
| F2 | T-ENV |
| F3-F5 | T-PIN, frozen-surface tests, empty diff |
| F6 | negative lists parameterised over both names |
| F7 | `tests/registry/test_tier1_note_reseed.py` (3 tests) |
| F8, F9 | documented debt |
| F10 | `test_system_costs_last_record_handles_none_and_legacy` |
| F11 | `test_every_backend_has_a_pinned_source`, `test_unknown_backend_is_unpriced_never_local` |
| F12 | `test_interleaved_turns_each_report_their_own_cost` and the router unattributed/match tests |
| F13 | `test_model_defaults.py` banner offset |
| F14 | guard self-tests (flag and pass samples) |
| F15 | `bash -n`, sibling-build test |

The tests check behaviour: they go through `/api/chat/stream` and `/api/chat` with stubs, not through private internals. I did not measure line coverage. The new code is almost entirely reached by the tests listed above.

## Performance assessment

Not on a hot path. Per chat turn, the change adds one dict lookup and one dict build. The guard test has its own speed check (`test_guard_runs_fast_and_scans_real_files`).

## Tech debt delta

The architecture predicted most of this. **New, unanticipated items:**
- The registry-note refresh constant (`_LEGACY_TIER1_NOTE`) has to stay until no persisted registry carries the old text.
- The broad quoted-id guard allowlist and the hard-coded script list (ASKs above).
- About 20 user docs under `docs/` are outside the guard's scope (BUILD_LOG item 4).

**Already-predicted items, still open:**
- `terms.json` re-seal (F8).
- Seeded PKB files (F9).
- `_classify_model` prices every aerollm/airllm model as 70B.

Net debt is slightly positive in the guard and migration code, and negative for honesty: there is no longer a fabricated cloud cost.

## Required actions before merge

None blocking. File these follow-ups in SPRINT.md or tickets:
1. Narrow the `(?<=['"])aerollm(?=['"])` guard allowlist; glob `scripts/*.sh` in the guard.
2. Make `router/core.py` return its cost record to close the same-model router race.
3. Migrate the `config/tuning.yml` `aerollm_package` pip ref (`current` and `choices` together) in the arail integration-surface sprint.
4. Docs sweep for the ~20 `docs/` files, with the guard extended to cover them.
5. DDaC re-seal of the `terms.json` AeroLLM entries (F8).
6. QA: run the full suite on a clean machine and confirm the 54 failures match baseline.

## Re-review (loop 2)

**Date:** 2026-10-07
**Scope:** `3cdc6de9..12d44163` (3801b93a, 3c389cfc, da307caf, 3b6af1da, 12d44163)

### Verdict: BLOCK

There is one BLOCK, and it is cheap to fix. Everything else in loop 2 is sound.

### Skill-pack refresh (3801b93a)
- [INFO] Safety: I checked all four pinned hashes against `git show 5f775f1c:<pack>/<skill>/SKILL.md`, and all four match. Any hand edit changes the hash, so an edited file is skipped (tested). A missing or unreadable file returns False and falls through to normal behaviour. `force=True` is unchanged.
- [INFO] Idempotency: I verified this by hand in a temp PKB. I seeded the pre-rename `optimize-aerollm`, and the first `install_pack('model-building')` refreshed it (no "AeroLLM" left). The second call skipped all three skills, because the new file no longer matches a pre-rename hash.
- [INFO] Frozen ids: the pack ids, the skill id/dir `optimize-aerollm`, and `manifest.yaml` are untouched.
- [INFO] Reach: `skill_seed.py` calls `install_pack(force=False)` for `research-methodology` and `model-building` on boot, so those three skills refresh automatically. `setup-arail` (onboarding pack) refreshes only when the user installs it from the Skills tab. `pkb_seed.install_pack` is a separate function and is not affected.
- [ASK] No test covers the positive path, i.e. that an unedited pre-rename file **is** replaced. That path is the actual fix for QA failure 1. The two existing tests only prove the negatives. Add a test that writes `git`-pinned pre-rename bytes (or a fixture whose sha is injected into `_PRE_RENAME_SHA256` via monkeypatch), then asserts the skill is in `installed` and that a second call skips it.

### benchmark_models.py (3c389cfc)
- [INFO] Only message text changed. The `backend == "aerollm"` comparison is intact.

### Docs sweep (da307caf)
- [INFO] Frozen identifiers in the `-` lines survive verbatim in the `+` lines. I checked `ARAIL_FORCE_AEROLLM`, `ARAIL_AEROLLM_PRELOAD`, `aerollm_api`, `lab/data/aerollm-bench.jsonl`, and the `github.com/qukaizen/aerollm` URLs. No commands or paths changed.
- [INFO] `world-forge.md` tag `aerollm` → `queuellm`: tags are used only for display pills, the `/docs` hub filter text, and related-doc scoring by shared tags (`docs_registry.py:500-508`). No doc under `docs/` (excluding archive) carries either tag besides `world-forge.md`, so related ranking is unchanged. Nothing filters on `tag=aerollm`. Safe.
- [BLOCK] `docs/verification/aerollm-1.0.0-pin.md` lines 54 and 62 sit inside fenced **captured-output** blocks: `• AeroLLM ready (release wheel 1.0.0)` and `• AeroLLM (2nd inference) status`. The v1.0.0 tool printed exactly those strings. Rewriting them makes the evidence record claim output that never happened, which contradicts BUILD_LOG's own statement ("not its … outputs"). Fix: restore those two lines verbatim from `5f775f1c`, and exempt them from the guard narrowly. Use an allowlist entry for those exact strings, or a markdown rule that skips fenced blocks in `docs/verification/`. Do not exclude the whole file. Rewording the prose in that file is acceptable either way.

### Guard (3b6af1da)
- [INFO] Loop-1 ASKs resolved. The quoted-id catch-all is replaced by three context-anchored entries, and `label: 'aerollm'` is now flagged (self-test). Scripts are globbed with `package-aerollm-bundle.sh` excluded by name. `lab/tools`, `docs/**` (excluding archive), `BLUEPRINTS.md` and `AGENTS.md` are scanned, and the scope self-test asserts this.
- [INFO] The `_MD_IDENTIFIER` rule is broad. Any lowercase `aerollm` joined by `-_@/.~` is treated as code, so prose like "the aerollm-powered deep mode" would slip through. That is acceptable, because the capitalised spelling (the real prose risk) is always flagged. Note it as a known miss.
- Ran with a temp HOME: `test_skill_pack_pre_rename_refresh.py`, `test_no_user_visible_aerollm.py`, `test_queuellm_rename_qa.py`: 80 passed.

### Required actions before merge
1. [BLOCK] Restore the two captured-output lines in `docs/verification/aerollm-1.0.0-pin.md`, with a narrow guard exemption.
2. [ASK] Add a positive refresh-and-idempotency test for `_PRE_RENAME_SHA256`.
