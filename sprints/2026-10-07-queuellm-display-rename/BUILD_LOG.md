# Build log: queuellm-display-rename

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `20726778`
**Base:** origin/main `5f775f1c`
**Started:** 2026-10-07

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `src/arail/costs.py`, `src/arail/portal/app.py` | `cost_source()`, honest cost fields, explicit record passing into `_build_chat_result`, `/api/system/costs` passthrough | T-COST-CLASS/LOCAL/CLOUD/UNATTR/RACE, T-STREAM (`tests/test_honest_local_cost.py`) | `ebd5b622` |
| 2 | `tests/test_no_user_visible_aerollm.py` | token-aware guard + self-tests, `xfail(strict=True)` | self-tests green, guard xfails | `7fd6a224`, `2b506f24`, `3cb54563` |
| 3 | Python display strings + retargeted tests | rename per rule | T-BANNER, T-IDS, T-RESEED | `306609b2` |
| 4 | portal templates + negative-assertion tests | rename per rule | T-NEG, T-IDS (rendered pages), JS harness | `896f3185` |
| 5 | `arailctl`, `scripts/*.sh` | rename messages | `bash -n`, T-ENV, T-PIN (`tests/test_queuellm_rename_frozen_surface.py`) | `f32e5d03` |
| 6 | seeded content, YAML/TOML, skill packs, `docs/cli.md` | rename per rule | skill manifest hash check, related suites | `24a03530` |
| 7 | guard test | drop `xfail`; record F7/F8/F9 | full suite | `dfebc8c3` |

Test runner: `PYTHONPATH=src /Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest`. This worktree has no venv of its own, so `PYTHONPATH=src` makes the shared venv import this worktree's code. Verified that `arail.__file__` resolves into this worktree.

## Execution

### Step 1: honest cost (`ebd5b622`)
As planned, with these deltas and findings:
- `CostRecord` gained `cloud_cost_usd`, `cloud_cost_source`, `cloud_equivalent_source`, `energy_source` and an `energy_cost_usd` property. History dicts gained `cloud_cost_source`, `cloud_equivalent_usd/_source`, `energy_cost_usd`, `energy_source`, and `tokens_in`/`tokens_out`. The last two were read by `/api/system/costs` but never written (same class of bug as the `energy_cost_usd` key mismatch), so I wrote them. No existing key was removed.
- New helper `costs.record_to_fields()` normalises a `CostRecord` or a history dict (including pre-sprint records with a fabricated `cloud_cost_usd` and no source). New `_resolve_chat_cost()` in `app.py` holds the attribution rules from the architecture: explicit record first, `get_last_record()` only when backend and model match, otherwise `cloud_cost_source: "unattributed"`; a non-`billed_estimate` backend is forced to `cloud_cost_usd: None` regardless of the record.
- `/api/system/costs` re-gates legacy records by backend, so an old persisted `aerollm` record no longer shows `0.008`.
- **A2 verified**: no template or static JS reads `cloud_cost_usd`.
- **A7 verified**: `track()` returns the `CostRecord`.
- **openai_compat.py needed no change.** It never emits `cloud_cost_usd`; it only calls `track()`, which is now gated at the source. The architecture's "apply the same gate in openai_compat" is satisfied by construction. `tests/portal/test_openai_compat.py` is green.
- Router branch of `/api/chat/stream` (final_response from `router.complete_stream`) uses the match-or-unattributed fallback; a same-backend, same-model race between two concurrent router calls is still theoretically possible there. Closing it needs `router/core.py` to return the record, which is outside this sprint.

### Step 2: guard (`7fd6a224`, `2b506f24`, `3cb54563`)
`tests/test_no_user_visible_aerollm.py` with 35 tests including the self-tests. Two follow-up fixes were needed because the first extractor under-reported:
- multi-line literals are now reported at the matching line (`2b506f24`);
- a `/*` in HTML text blanked the lines after it, hiding `research.html:304-317` from the guard. `/* */` and `//` are now stripped only inside `<script>`/`<style>`, and a self-test pins it (`3cb54563`). Jinja `{# #}` comments are stripped too.
Initial count was 177 hits, then 146 once the extractor was corrected; the architecture's inventory was a subset.

### Step 3: Python display strings (`306609b2`)
All rows of the Python table, plus inventory gaps found by the guard (the guard, not the table, is the authority per the architecture):
- `doctor.py:122` and `registry/health.py:199`: `"aerollm (in-process)"` shown in doctor output and the preflight tier line.
- `portal/model_warmth.py:149`: `log.warning("aerollm preload tick failed")`.
- `router/backends.py:1425, 1445`: `"aerollm will auto-detect KV budget"` in the KV-budget reason string.
- `app.py:4340, 4394`: multi-line string continuations of the two rows in the table.
Banner: padding reduced by one space so the A and B model columns still line up (T-BANNER).
Retargeted tests: `test_aerollm_compute_source`, `test_aerollm_defaults`, `test_deep_policy_explain` (not in the architecture list), `test_lever_handoff`, `test_model_defaults`, `test_model_ux_phase0_eject_honesty`.
**F7 / A5 outcome: A5 was wrong.** `_seed_from_env` writes the Tier-1 `note` only when the entry is absent or the env moved, so an existing lab's persisted registry kept "aeroLLM" (the note is exposed through `ModelEntry.to_dict`). Fix applied per the F7 recovery: on load, the note is replaced only when it is byte-equal to the exact legacy built-in text, so a hand-edited note survives (`tests/registry/test_tier1_note_reseed.py`, 3 tests). The generated `model_defaults.yaml` header comment is written once and is not rewritten for existing labs; that is cosmetic and left as is.
Not renamed on purpose: `test_program_drafter.py:117` (the goal text is supplied by the test fixture, not by `src/`).

### Step 4: templates (`896f3185`)
Every row of the template table. `chat.html:1818` (`ref-note` backend list) did contain AeroLLM and was renamed. `research.html` link text became `QueueLLM tuning` (the automated rename first produced `QueueLLM-tuning`, corrected by hand). `_model_boot_banner.html:5` is a Jinja comment and was reverted to the original text.
F6: the oversell phrase list in `test_model_ux_phase0_oversell_copy.py` is now `old names + queuellm twins` with a meta-test; `test_model_ux_phase0_headers.py` rejects both names; eject-honesty rejects both. `tests/js/research_summary_harness.mjs:131` was deliberately left alone: its fixture (lines 123-124) still contains the text "aeroLLM" as a different goal's experiment title, so the absence assertion remains meaningful and not vacuous. The JS harness passes.
T-IDS: template ids/values asserted in `tests/test_queuellm_display_ids.py`, plus a rendered-page check of `/chat`, `/research`, `/tuning` (found `/tuning` needs `LAB_TIER=maximus`).
Retargeted: `test_research_page_dom.py` (`QueueLLM tuning</a> loop`), `test_aerollm_model_ready.py:151`.

### Step 5: arailctl and scripts (`f32e5d03`)
All rows of the script table; `bash -n` is clean on `arailctl`, `setup.sh`, `upgrade.sh`, `build-aerollm.sh`, `blueprint.sh`. `./arailctl help | grep -i aero` returns only the `benchmark, aerollm` alias line. My first automated pass changed `$(aerollm_version)` (a shell function call) to `$(QueueLLM_version)`; caught in review of the diff and reverted, and `aerollm_version` is now a frozen token in the guard. T-PIN and T-ENV were added and shown to fail on a simulated `ARAIL_SKIP_AEROLLM_PROBE` rename. Retargeted `test_aerollm_bundle_qa_hardening.py:481` and `test_aerollm_local_sibling_build.py:36`.

### Step 6: seeded content, catalog, skill packs, docs (`24a03530`)
- **Skill manifest**: `skill_packs/manifest.yaml` has no content hashes, so no regeneration was needed. Skill `id`s, the `optimize-aerollm/` directory and `tags:` lists are unchanged.
- **`config/tuning*.yml`**: the `rationale`/description text is rendered by `tuning.html`, so it was renamed. The pinned pip ref `git+https://github.com/cdarnell/aerollm@main` (knob `current` and `choices`, lines 60 and 64) was **not** changed; see "Architect feedback required".
- **`catalog/models.toml`**: I could not find a consumer that renders `notes` (`blueprint.sh` does not read it); renamed anyway as specified. The prose mentioned `crates/aerollm-backend-mlx-native/…`; the crate directory is `queuellm-backend-mlx-native` in the engine repo, so the path was corrected rather than blindly renamed.
- `docs/cli.md`: config keys `aerollm_bundle_tag/_sha256`, the `aerollm` alias and env vars stay. `deep status` prints a `bundle:` provenance line from an inline `python -c` in `build-aerollm.sh` ("aerollm {ver}"); the extractor first missed it. It now reads `QueueLLM {ver}` and the doc matches. The guard scans inline `print(` output now.
- **F9**: seeded PKB/research text (`builtin_seed.py`, `pkb_seed.py`) changes only for freshly seeded labs; existing labs keep their (possibly user-edited) files. Not rewritten.

### Step 7: flip the guard (`dfebc8c3`)
`xfail` removed; the guard reports zero hits. As an independent cross-check I listed every remaining case-insensitive `aero\s*llm` in all scanned files after frozen-token removal (245 lines). All are comments, docstrings, identifiers (`aerollm_ring_depth`, `_probe_aerollm`, dict keys) or the alias line. Docstrings on FastAPI route handlers (`app.py` 9523, 14036) are visible in the OpenAPI `/docs` page; the architecture says leave docstrings, so they were left.

## Architect feedback required

1. **`config/tuning.yml` knob `aerollm_package` (lines 60, 64) holds a pinned pip ref `git+https://github.com/cdarnell/aerollm@main`.** Rule 7 says rewrite old GitHub URLs, but this value is a string compared against `choices` and recorded in persisted experiment state, so changing it is a config migration. It is allowlisted in the guard with that reason. Decision needed: leave, or migrate both `current` and `choices` together (the old URL still redirects).
2. **A5 was false** (registry notes persist). Handled with an exact-text refresh; flagging because the architecture's assumption list was wrong, and the same "seeded once, persisted forever" pattern holds for anything else written at first run (F9).
3. **The architecture's inventory under-counted.** Missing rows are listed under steps 3 and 6. No design change is needed, but the "guard is the authority" principle was what caught them.
4. **User docs outside `docs/cli.md`** (about 20 files under `docs/`, plus `README.md` line 97 which is a kept migration note) still say AeroLLM. They were out of the architecture's scope; a follow-up docs sweep is a one-liner with this guard extended to them.
5. The router-branch race noted in Step 1 needs `router/core.py` to return its cost record.

## Follow-ups to file in SPRINT.md (from the architecture)
1. DDaC world re-seal for the `lab/worlds/*/terms.json` "AeroLLM" entries (F8). `welcome.html:724` now says "QueueLLM — 32 sourced terms" while the sealed glossary still defines AeroLLM.
2. `_classify_model` prices every aerollm/airllm model as 70B, inflating the simulated "Cloud equiv"/"Net saved" totals.
3. Frozen-surface rename sprint (env vars to `QUEUELLM_*` before engine 2.0.0, package, ids, NOTICE re-pin).
4. SPRINT.md UX traps stay out of scope.

## Final state

- Commits since the plan: 10 (SHAs in the table above; skeleton `7d1f9fc4`).
- Diff vs base excluding `sprints/`: 57 files, +1312 / -206. `NOTICE`, `THIRD-PARTY-LICENSES/`, `lab/worlds/`, `pyproject.toml`, `uv.lock` have an empty diff against `5f775f1c` (asserted by `tests/test_queuellm_rename_frozen_surface.py`).
- New test files: `test_honest_local_cost.py` (19), `test_no_user_visible_aerollm.py` (35), `test_queuellm_display_ids.py` (6), `test_queuellm_rename_frozen_surface.py` (5), `registry/test_tier1_note_reseed.py` (3), plus one added test in `test_model_defaults.py` and one in `test_model_ux_phase0_oversell_copy.py`.
- Full-suite results are recorded in the section below.

### Full-suite result (reported as measured)

| Run | Checkout | Result |
|---|---|---|
| Baseline #1 (start of sprint, clean checkout of `5f775f1c`+plan) | scratch worktree | **6803 passed, 37 skipped, 29 xfailed, 0 failed** |
| Baseline #2 (same checkout, ~1 h later) | scratch worktree | 6803 passed, **54 failed**, 37 skipped, 29 xfailed |
| Branch HEAD `dfebc8c3` | fresh scratch worktree | 6873 passed, **54 failed**, 37 skipped, 29 xfailed |

The branch adds 70 passing tests and **no new failures**: the 54 failing test ids on the branch are identical to the 54 on baseline #2 (diffed). I could not fully root-cause why baseline #1 was green and baseline #2 was not; the venv is unchanged since 2026-09-25, a fresh checkout reproduces it, and nothing in this sprint touches those tests. The failures are in unrelated areas (`test_w9_embedder_swap`, `test_reset_stop_scope`, `test_shell_source_safety` needing `tomllib` on the system `python3`, `test_token_compliance`, `test_aerollm_defaults` stub, `test_swarm_goal_surfaces`, and others), so this looks like machine state drift during the session. A reviewer should re-run the suite on a clean machine before relying on "0 failed". Two further notes:

- Running the suite repeatedly in one checkout also pollutes the ignored `lab/` state, so subset comparisons against baseline were always done baseline-vs-branch on the same file list.
- `tests/test_aerollm_bundle_compliance.py::test_notice_byte_identical_to_sibling_when_available` fails on this machine because the sibling `qukaizen-queuellm` has the rebranded NOTICE (known, per the workspace CLAUDE.md, until the bundle re-pin sprint). It fails identically on baseline.

Targeted runs that touched the sprint (all green after the retargets, compared to baseline on identical file lists): cost/chat/recap/router/openai subset, aero/deep/registry/doctor subset (175 files), template-related subset (131 files), seed/skill/catalog/tuning/docs subset (240 files; 28 pre-existing order-dependent failures identical on baseline), `tests/registry`, `tests/setup_ladder`, bundle suites, and `node tests/js/research_summary_harness.mjs` (passes).

### Owner witness (not run here)
On a maximus lab: run one deep answer and confirm the picker header reads "DEEP · QUEUELLM", the chip reads "QueueLLM", and the raw `/api/chat/stream` final event shows `"cloud_cost_usd": null, "cloud_cost_source": "local"`. I did not touch `arail-buddy-wt` or any live lab.

## Loop 2

Scope: QA failures 1 and 2, the orchestrator's docs sweep, REVIEW follow-up 1.

| # | Files | Change | Commit |
|---|---|---|---|
| L2-1 | `src/arail/skill_packs/__init__.py`, `tests/test_skill_pack_pre_rename_refresh.py` | `install_pack(force=False)` replaces an installed `SKILL.md` only when its sha256 equals a pre-rename shipped file (4 files: optimize-aerollm, frontier-local-models, understanding-precision, setup-arail). Hand-edited files never match and survive. Same shape as the F7 registry-note migration. | `3801b93a` |
| L2-2 | `lab/tools/benchmark_models.py` | The warning and the skip reason say QueueLLM. The backend id `"aerollm"` and the TODO comment are unchanged. | `3c389cfc` |
| L2-3 | `docs/*.md` (not `docs/archive/`), `BLUEPRINTS.md` | Prose-only sweep of 20 files that the portal renders under `/docs/`. Env var names, package, crate and path names, repo URLs, commands and "formerly" notes are verbatim. The `world-forge.md` frontmatter tag `aerollm` became `queuellm`, because it shows on the `/docs` index card. | `da307caf` |
| L2-4 | `tests/test_no_user_visible_aerollm.py`, `tests/test_queuellm_rename_qa.py` | The guard now scans `docs/**` (not archive), `BLUEPRINTS.md`, `AGENTS.md`, `lab/tools/*.py` and `scripts/*.sh` (all but `package-aerollm-bundle.sh`, per A6). The quoted-lowercase allowlist is replaced by three specific entries. QA's strict xfail is now a normal test. | `3b6af1da` |

Deltas and judgement calls:
- **Markdown identifier rule.** The guard treats lowercase `aerollm` joined by `- _ @ / .` (repo, crate, path, test and knob names), `import aerollm`, `aerollm = "` (pyproject extra), `grep -i aerollm` and `backend: aerollm` as code in markdown. A standalone word and every capitalised spelling are prose and flagged. Self-tests cover both directions.
- **QA helper gap.** QA's `_visible_hits` did not exempt the frozen env var `ARAIL_FORCE_AEROLLM`. I added the same `_AEROLLM` suffix pattern the guard already has. Nothing was renamed.
- **`docs/verification/aerollm-1.0.0-pin.md`** is a dated evidence record. I changed its prose mentions only (repo and sprint names), not its commands, paths, outputs or the file name. This is judgement. Revert that file if the owner wants it left as written.
- **Skill pack refresh covers only the skills with pre-rename hashes.** A pack skill that gains "AeroLLM" text in the future needs its own hash entry. Skills seeded inline in `skill_seed.py` carry no old name.
- **Not done:** the runtime-interpolated backend id in activity-log prose (`app.py:7505`, `:7486`), and the `build-aerollm.sh` crate-dir bug. Both stay filed for later.

Test results (temp HOME, same machine; base `5f775f1c` in a separate checkout):
- The sprint, QA and guard files pass: `test_queuellm_rename_qa.py` 35 passed (the strict xfail is now a normal test), `test_no_user_visible_aerollm.py` 43 passed, `test_skill_pack_pre_rename_refresh.py` 2 passed.
- Full suite on base: 73 failed, 7 errors. The FAILED id sets match except one: `tests/test_cli_qa_edge.py::test_qa_edge_driver_scenarios` failed once on HEAD when other jobs ran on the machine. It passes in isolation (11 passed). It is a CLI subprocess driver and the loop touches nothing it exercises, so I treat it as load-sensitive. The HEAD run's error ids were not captured. No new deterministic failure.
