# Build log: boot-time model auto-resolution

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `8aaf4a8d`
**Started:** 2026-10-20
**Branch:** `qukaizen/model-inference-alignment-38a991` (not pushed, no PR)

## Plan

Order follows ARCHITECTURE.md "Recommended implementation order".

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | router/backends.py, tests/router/test_keep_alive.py | keep_alive int -1 (F1); surface Ollama error text (FM7/FM8) | test-first | 8d39f07e |
| 2 | model_resolve.py (new), tests/test_model_resolve.py | fit/candidate helpers + resolve_boot, no wiring | test-first | 954ea16f |
| 3 | model_defaults.py, tests/test_model_defaults.py | v2 yaml: write_cache/write_pin/v1 migration; CLI | test-first | 478cc356 |
| 4 | registry/store.py, tests/registry | reconcile_from_resolution; drop Qwen fallback (F2/FM12) | test-first | e3b18b24 |
| 5 | portal/app.py, tests/conftest.py | resolver task, stamp, reconcile, delete _export_registry_env, A boot warm | integration | 469ce04e |
| 6 | deep_policy.py, model_warmth.py, app.py | B boot warm, boot_warm_gate, crash sentinel, /api/instance fields | unit | 0e06d909 |
| 7 | portal/models_api.py, app.py (chat default) | /api/models/boot v2, /api/models/pin, settle alias | TestClient | 73da4bb8 |
| 8 | _model_boot_banner.html | degraded strip; DOM tests | DOM | 959b4365 |
| 9 | scripts/status.sh, install.sh, docs/cli.md, yaml example, CLAUDE.md, BACKLOG | docs and CLI | pytest wrapper | 9931a03c |
| 10 | tests/test_model_resolve_integration.py | clean-machine, Ollama-down, loopback, perf, live-Ollama | integration | d27f0288 |

Follow-up commits (not in the original plan, see deltas): `8b9d5a0e` (stabilise one test), `be8d70ee` (remove dead `_boot_warm_explicit`).

## Execution

### Step 1 -- keep_alive fix (F1, FM7, FM8)
`_keep_alive()` returns int `-1` for the tier-0 pin; `ARAIL_OLLAMA_KEEP_ALIVE` values that parse as int are sent as int, `"5m"` passes through, empty omits. A shared `_raise_for_status_with_detail` appends Ollama's `error` field (160 chars max, response body only, never request headers) to HTTP errors in `OllamaNativeBackend.complete`/`stream_complete`. `test_keep_alive.py` assertions `== "-1"` changed to `== -1` plus `isinstance(int)`; new tests for 5m passthrough and error surfacing.
Verified live (Ollama 0.32.x on this Mac, `ai-engineer:latest`): int -1 returns 200; the old string form returns `400 ... time: missing unit in duration "-1"` and that text now appears in the raised error.
Commit: 8d39f07e

### Step 2 -- model_resolve
New `arail/model_resolve.py`: `resolve_boot` (pin > env > auto > none, per slot), loopback-only Ollama access (`OLLAMA_HOST`/`OLLAMA_PORT` honoured, non-loopback host treated as unreachable with no request), `/api/show` capped at 8, 5 s bounded wrapper, cache fallback when Ollama is down. `fit_primary/fit_secondary/slot_a_candidates/slot_b_candidates` moved here; `models_api` re-imports them.
Deltas: `resolve_boot` takes two extra optional keywords, `cache` (previous `resolved` block, needed for FM1) and `skip_b` (crashed deep model, for FM5). `resolve_boot_bounded` added for FM9. A resolved A with unknown parameter count is treated as not eligible (matches the ceiling's "unknown refuses" rule).
Commit: 954ea16f

### Step 3 -- model_defaults v2
`version: 2` with `pins` and `resolved`; `read()`, `write_cache`, `write_pin`, `update_warm`, `migrate_v1` (v1 `default_b: null` becomes `pins.b: none` plus a `pin_notes` entry; one-time `.model_defaults.v1.bak`), `stamp_env`, `env_preference`, deprecated `write_defaults` shim. CLI `--banner` shows source and reason; `--json` is v2 shaped. `resolve_slots()` lost `settled`.
Deltas: `apply()` also captures the pre-stamp `.env` value in `ARAIL_ENV_PREF_A/B` (first capture wins) so the resolver can treat `.env` as a preference ("not stamped by us" -- the architecture did not say how). Writers share an in-process `RLock` (added in step 7) so a pin write is never lost to a concurrent cache write (FM13). `models_api._boot_settlement` got a temporary bridge so this commit kept the suite green; removed in step 7.
Commit: 478cc356

### Step 4 -- registry reconcile
`reconcile_from_resolution(reg, resolution)`; tier1 with B none becomes `enabled=False, model_id=""`. `_seed_from_env` no longer falls back to Qwen when `AEROLLM_MODEL` is unset. Persisted empty `model_id` now round-trips (`_entry_from_dict` used to coerce it to "default").
Delta from the architecture: the `_seed_from_env` "env wins only when env moved" fingerprint was NOT removed. `reconcile_from_resolution` keeps `seed_state` in step so it is inert after boot, and removing it would have rewritten `tests/registry/test_env_write_through.py` for no behavioural gain. Flagged for the reviewer.
Commit: e3b18b24

### Step 5 -- portal wiring + A warm
`_boot_models()` startup task: crashed-model lookup, `resolve_and_apply` (cache write, env stamp, registry reconcile under `model_resolve.LOCK`), activity events, then warm A through the single `_warm_primary_router` path. Boot warm is default-on and independent of `ARAIL_AUTOCHECKS`; `ARAIL_BOOT_WARM=0` opts out (leaves the skip-reason globals untouched, so a quiet boot looks exactly as before). `_export_registry_env` deleted. `tests/conftest.py` gains an autouse hermetic fixture (warm off, resolver network reads stubbed to "Ollama down", per-test yaml and sentinel paths, env restored) and a `real_resolver_net` marker (pyproject) to opt out.
Tests rewritten: `test_registry_env_export.py` became `test_boot_models.py`; `test_warm_up.py`'s source-inspection pin of `if _autochecks_on or _boot_warm_explicit()` now pins the unconditional `asyncio.create_task(_boot_models())`.
Commit: 469ce04e

### Step 6 -- B warm, sentinel, instance fields
`deep_policy.boot_warm_gate/boot_warm_safe` (tier, wheel, memory pressure; not presence). `model_warmth.boot_warm_deep` loads B once under `inference_slot`, bracketed by a pid-stamped `.aerollm_boot_warm.inflight` sentinel (removed on success or Python failure; a stale sentinel with a dead pid makes the next boot skip that model; a live sentinel from another process defers). `/api/instance` gains `warm_a`/`warm_b`. The stale "resident <=3B" docstring in `_keep_alive` is corrected (FM16).
Test change: `test_instance_isolation_audit`'s allowed-field list gains the two keys.
Commit: 0e06d909

### Step 7 -- endpoints
`GET /api/models/boot` v2 (`hidden|degraded`, `resolution`, `warm`, `degraded`, `airgapped`, `hardware`; `?full=1` still returns candidate lists for the override UI). `POST /api/models/pin` (same validation as old settle plus realpath containment for slot B; absolute paths only when `AEROLLM_MODEL` already points there; B pin clears the crash sentinel; response never waits on the warm). `POST /api/models/settle` is a deprecated alias (`Deprecation`, `Link`, `deprecated: true`). `POST /api/chat/default` for resident and deep now persists as `pins.a`/`pins.b`. `_registry_write_through_resident` deleted (dead).
Deltas: a `cache_unsaved` degraded kind (FM10: "strip notes couldn't save model cache") was added to the architecture's closed list. `requires_restart` on `/api/chat/default slot=deep` is now computed from the resident runtime instead of a constant `False`.

Assertions removed from the PR #186 suites and their replacements:

| Removed | Replacement |
|---|---|
| `mode == "picker"` when never settled (3 tests) | `test_clean_machine_resolves_without_a_yaml`; candidate-row shape kept under `?full=1` |
| `slots.b.allow_none` | `test_pin_none_for_b_is_honoured_and_a_cannot_be_none` |
| `mode == "problem"` for vanished A / removed B dir | `test_pinned_model_missing_falls_through_and_is_degraded`, `test_pinned_deep_model_removed_from_disk_is_degraded` |
| "problem mode also assembles candidates" | `test_candidates_only_on_full_flag` |
| `stamp` changes with settlement | removed (no stamp) |
| v1 file asserts (`default_a:` / `default_b: null`) in settle test | v2 `pins` assertion |
| `settled` in `resolve_slots()` / `--json` (3 + 1 tests in `test_model_defaults.py`) | source/reason/version tests, `test_cli_json_v2_shape` |
| `_gather_slot_facts` presence/fit/install-hint tests (5) | `test_gather_slot_facts_is_a_projection_without_network` |
| `test_registry_env_export.py` (4 tests) | `test_boot_models.py`, `test_export_registry_env_is_gone` |
| `_boot_warm_explicit` gate tests (later, with the function) | scheduling test in `test_warm_up.py` |
| picker ids / esc() / hf-link checks in `test_model_boot_banner_dom.py` | see step 8 |

`git grep settled` in arail and the sibling repos found no external reader of `settled`.
Commit: 73da4bb8

### Step 8 -- banner
`_model_boot_banner.html` is 56 lines (was 310): one strip, hidden unless `mode == "degraded"`, `textContent` only, no links, fetch-once, "Change…" clicks the existing `#model-picker` Resident chip (else goes to `/chat`). DOM tests rewritten (docstring maps removed checks to replacements).
Commit: 959b4365

### Step 9 -- docs / CLI
`status.sh`: additive `models` object (schema stays `arail.status/v2`; precedent is the earlier additive `db`/`origin` keys, and docs/cli.md states keys are additive) plus a matching human line; `--json=instances` untouched. `install.sh` expected-model check reads v2 `resolved.a` / `pins.a` / v1 `default_a`. `docs/cli.md` §start rewritten, §status gains the models paragraph, §install line updated. `model_defaults.yaml.example` is v2 pins-only. One line in `CLAUDE.md`. `sprints/BACKLOG.md` entry to delete the shims next release. `start.sh` needed no change: `--banner`/`--get` keep their flags and now print source/reason from the cache.
New `tests/test_status_models_block.py`.
Commit: 9931a03c

### Step 10 -- clean-machine and Ollama-down
`tests/test_model_resolve_integration.py`: minimalist box with only `llama-ai-eng` (A resolves, B none "deep model needs maximus" via the real tier check, no degraded notes, yaml exists afterwards); portal booting `ready` with a genuinely closed Ollama port (degraded/ollama_down); resolver only touches loopback under `LAB_MODE=airgapped` for five host values; p95 < 1 s over ten models at 20 ms/request; live-Ollama tests (auto-skip when none answers).
Manual runs on this Mac against the real Ollama (it was up for part of the session): `resolve_boot` with `.env` preferring `ai-engineer:latest` returned it (source env, 7B, 4.36 GB) in 0.02 s with `nomic-embed-text` excluded; direct completion with the tier-0 pin returned 200.
Commit: d27f0288

### Follow-ups
- `8b9d5a0e`: `test_chat_default_deep_slot_success...` asserted `requires_restart is False`; with the value now computed it failed when another test left an `AeroLLMBackend._shared` runtime. The test now pins "nothing resident".
- `be8d70ee`: `_boot_warm_explicit()` had no callers once warm became default-on; removed with its tests. `start --warm` still works (it only polls/report; `ARAIL_TIER0_BOOT_WARM=1` is harmless).

## Architect feedback required

No blocking gap. Items the reviewer should look at (decisions I made where the spec was silent or I deviated):

1. `_seed_from_env` fingerprint logic kept (step 4) rather than "replaced". Inert after `reconcile_from_resolution`; removal would need a larger test rewrite.
2. Extra degraded kind `cache_unsaved` (FM10) beyond the closed list in the HTTP contract.
3. How "env preference not stamped by us" is detected: `ARAIL_ENV_PREF_A/B` env markers set by `apply()` (process-global; the conftest fixture restores them).
4. When Ollama is down, A resolves pin > last cache > `.env` (unverified). The architecture said "cache"; an explicit pin should not lose to a stale cache.
5. A8 (supersedes the 2026-07-23 quiet-boot contract for model warm) is implemented as written: warm runs with `ARAIL_AUTOCHECKS` off. Tests are hermetic via conftest, so no existing test exercises a real warm.
6. `GET /api/models/boot` can serve the on-disk cache if called before the boot task finishes; a first page load in that window may briefly show an empty resolution.
7. Not measured with a browser: the "time-to-/api/ready must not regress > 200 ms" target was checked as TestClient startup (head 17 ms vs base 16 ms median, warm on).
8. Bash CLI drivers (`tests/cli/*.sh`, wrapped by `test_cli_*.py`) self-skip without a worktree `.venv`; they were not run. `start.sh` was not changed, so the risk is the banner text only (no test greps it).

## Final state
Full arail suite (`PYTHONPATH=src .venv/bin/python -m pytest tests`, Ollama not running during the run):

| | passed | failed | skipped |
|---|---|---|---|
| baseline `8aaf4a8d` (clean worktree) | 5538 | 72 | 32 |
| this branch at `be8d70ee` | 5625 | 71 | 34 |

The failure set is a strict subset of the baseline's: zero new failures, and `test_model_separation.py::test_ollama_model_starts_with_llama` now passes. The 71 remaining failures are pre-existing and environmental on this machine (missing `dotenv` in system python for the bootstrap CLI tests, an absent `lab/worlds/photography` bundle, opencode `fake_popen`, token-compliance ratchet, `test_notice_byte_identical_to_sibling_when_available`, aerollm kv-budget tests, and similar). A run done while a live Ollama happened to be up produced a different but equally environmental set (live-daemon tests); those were re-checked against the baseline worktree under the same conditions and matched.

New/rewritten test files: `test_model_resolve.py` (27), `test_model_defaults.py` (rewritten, 34), `test_boot_models.py` (12), `test_boot_warm_deep.py` (16), `test_models_boot_endpoint.py` (13), `test_models_pin_endpoint.py` (18), `test_models_settle_endpoint.py` (12), `test_model_boot_banner_dom.py` (8), `test_status_models_block.py` (4), `test_model_resolve_integration.py` (10, 3 need a live Ollama and skip without one), `registry/test_reconcile_from_resolution.py` (7), `router/test_keep_alive.py` (+4).
Size: 34 files changed, +3435 / -1503 (net positive in tests and resolver; the picker template and settle drift logic are gone).
Not run: `tests/cli/*.sh` drivers (self-skip without a worktree `.venv`).

## Round 2 (after REVIEW.md verdict BLOCK)

Each defect got a test that was run red against the unfixed source first (source reverted, tests kept), then fixed. Four commits, one per finding.

| Finding | Fix | Red-first tests | Commit |
|---|---|---|---|
| B1 corrupt `model_defaults.yaml` overwritten | `write_cache` refuses when the file exists but is unreadable (bytes untouched, returns False); `write_pin` copies it to `.model_defaults.corrupt.bak` (timestamped if a different backup exists) before an explicit pin replaces it. `cache_unsaved` now says "unreadable ... left untouched, your pins are not applied this boot" vs "couldn't save ... not writable"; the duplicate activity line in `_boot_models` was dropped | `test_corrupt_yaml_is_never_overwritten_by_boot`, `test_unwritable_but_readable_yaml_keeps_the_save_wording`, `test_write_pin_backs_up_an_unreadable_file_before_replacing_it`, `test_write_cache_refuses_an_unreadable_file` (4 of 6 initially red; the 2 warm tests were split into the A2 commit) | 5027caf6 |
| B2 sentinel wedge via PID reuse | `_sentinel_is_live`: pid alive and not us, `started_at` younger than `SENTINEL_MAX_AGE_S` (600 s), and same boot id (`sysctl kern.boottime` / `/proc/.../boot_id`, stamped in the sentinel). Otherwise stale, so `crashed_model()` reports it and `_loading_elsewhere()` is False. A `deferred` B warm is now also added to the degraded strip (`warm_deferred`) | `test_live_but_old_pid_is_a_crash_not_a_wedge`, `test_sentinel_from_a_previous_boot_is_a_crash`, `test_same_boot_fresh_live_pid_still_defers`, `test_sentinel_without_started_at_is_stale`, `test_deferred_boot_warm_is_visible_in_the_degraded_strip` (5 red). Existing live-pid tests now write a fresh `started_at` | bd7c2f28 |
| A1 fallback ignored the crash list | `_fallback(..., skip_b)` drops a cached B equal to `skip_b` and records `crashed_last_boot`; both the exception path and `resolve_boot_bounded` timeout path pass it | `test_timeout_fallback_honours_the_crash_list`, `test_exception_fallback_honours_the_crash_list` (red), `test_fallback_keeps_a_cached_b_that_did_not_crash` (guard) | d10d6e94 |
| A2 re-resolve wiped warm | `resolve_and_apply` carries `warm.a/b` forward from this process's previous resolution for each slot whose resolved model is unchanged (never from disk, which would be a prior boot's value); a changed slot is cleared until its warm completes | `test_reresolve_keeps_warm_of_slot_whose_model_is_unchanged`, `test_reresolve_drops_warm_of_slot_whose_model_changed` (red) | 4eafcc61 |

Not changed (out of scope, per the instruction): the review's INFO items (A-only pin re-deciding B, IPv6 `OLLAMA_HOST`, `HTTPError.response`, `_resolve_a` size) and the QA asks (real `./arailctl start` timing, `tests/cli/*.sh`).

### Unforeseen debts recorded (not anticipated in ARCHITECTURE.md)
1. **Two writers of tier0/tier1 seed state.** The old `_seed_from_env` fingerprint ("env wins only when env moved") survives next to `reconcile_from_resolution`. `reconcile_from_resolution` keeps `seed_state` in step so it is inert after boot, but it is a second path that must stay consistent. Delete it together with the settle/`write_defaults` shims (BACKLOG entry covers the shims; this item should be added to it).
2. **Two locks instead of one.** The architecture specified a single lock (FM13); the code has `model_resolve.LOCK` and `model_defaults._WRITE_LOCK`. Ordering is always `LOCK` then `_WRITE_LOCK`, but nothing enforces it. Collapse to one lock when the shims go.

### Test results
- Sprint-related suites (resolve, resolve integration, defaults, boot models, deep warm, boot/pin/settle endpoints, banner DOM, status block, registry, router, chat default, warm-up, chat slots contract): 329 passed, 2 skipped, 0 failed when run together.
- Full suite (`PYTHONPATH=src .venv/bin/python -m pytest tests`, run from this worktree with the main checkout's venv): 5639 passed, 71 failed, 34 skipped, 7 xfailed. Same failure count as the round-1 result (71) and one below the clean baseline (72).
- Caveat on the set: `tests/test_models_settle_endpoint.py::test_settle_default_b_none_is_always_valid` failed in the full run but passes in isolation and in the sprint group. It is an order-dependent leak (a pre-existing failing `test_aerollm_defaults.py` kv-budget test leaves a resident runtime, which flips `restart_required`). Reproduced identically on the pre-round-2 commit `748342e8` with the same 4-file ordering, so it is not caused by round 2; it is the same class of problem as follow-up `8b9d5a0e`. A one-line test-isolation fix is a candidate for the next round (not done here: outside the findings). I did not retain the baseline's failure list, so strict subset-ness of the 71 against the 72 was checked by file/area (no failures in any sprint file other than this order leak), not name by name.
- One-off: `test_api_instance_exposes_warm_a_and_warm_b` failed once right after the B2 edit and passed on five reruns; not reproduced.

## Round 3 (after TEST_REPORT.md verdict FAIL)

| Finding | Fix | Tests | Commit |
|---|---|---|---|
| QA-1 (HIGH) MLX primary backend had `MODEL_NAME` overwritten with an Ollama tag | `model_defaults.primary_is_ollama()` (`MODEL_BACKEND` in `ollama_native`/`openai_compat`; unset means `mlx`, matching the registry seed). `stamp_env` skips slot A otherwise, so both `apply()` at import and `resolve_and_apply` leave `MODEL_NAME` alone. `env_preference()["a"]` is None for non-Ollama backends, so an MLX repo id no longer yields a bogus "isn't installed" note. Slot B stamping and the cache are unchanged; the registry reconcile already guarded on the tier-0 backend | The QA test `test_setup_maximus_mlx_env_keeps_a_loadable_primary_backend_model_pair` (red then green); new `test_non_ollama_primary_backend_keeps_its_model_name[mlx,cuda,cpu,unset]`, `test_non_ollama_backend_still_stamps_slot_b_and_caches_a`, `test_apply_from_cache_does_not_stamp_a_under_mlx` (red first). `test_model_defaults.py` and `test_models_settle_endpoint.py` fixtures now pin `MODEL_BACKEND=ollama_native`, because they relied on the unset default stamping A | c753cbf2, e89e61c7 (fixture) |
| QA-4 NUL byte in slot-B pin gave 500 | Reject `\x00` with 400 before `realpath` | strict xfail flipped | dd556019 (code), 1dc50ed0 (flip) |
| QA-5 out-of-range sentinel pid raised `OverflowError` | `_pid_alive` catches `OverflowError`/`ValueError`; treated as dead (stale) | strict xfail flipped | f216dba8 (code), 1dc50ed0 (flip) |
| QA-3 unreadable yaml dropped `b: none` for a boot | Trivial, so done: while the file exists but is unreadable, slot B is pinned off in memory for that boot (file untouched; the existing `cache_unsaved` note explains it). Removed from BACKLOG scope | strict xfail flipped | 1dc50ed0 |

Process note: the QA-4 and QA-5 code commits landed before their xfail flips (one test file held three flips), so those two intermediate commits have a strict-xfail XPASS; HEAD is consistent. Behaviour change worth stating in the PR: with no `MODEL_BACKEND` set, slot A no longer rewrites `MODEL_NAME` (a hand-made lab with no backend line is mlx per ModelRouter, so an Ollama tag there was the same bug as QA-1, TEST_REPORT #4).

Results: sprint-related tests (boot/resolve/defaults/pin/settle/banner/status/registry, plus the QA file) pass apart from pre-existing failures. Full suite: 5722 passed, 71 failed, 34 skipped, 7 xfailed (round 2: 71 failed). Name by name against the round-2 run: one test left the failure list (`test_settle_default_b_none_is_always_valid`, order leak fixed by QA's `7f82beef`) and one entered: `test_warm_up.py::test_api_instance_world_branch_exposes_warm_fields`. That one is a flaky race, not a regression: on the pre-round-3 commit `c753cbf2~1` it fails 3 of 6 solo runs (the boot task records warm status after the test's `monkeypatch.setattr`, the same mechanism QA fixed for the sibling root-branch test in `bccec418`). It is not fixed here (outside the findings); the same one-line sequencing fix applies. Count is within the 71 limit with no deterministic new failure.
