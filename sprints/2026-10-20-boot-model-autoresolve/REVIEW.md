# Review: boot-time model auto-resolution

**Date:** 2026-10-20
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `c49f8046` (branch HEAD `889e6dd7`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `ec5ab833`

## Verdict: BLOCK

Most of the work is solid. The F1 fix is correct and minimal. The single direction resolution → env + registry kills F2 at its root, and the hardcoded Qwen fallback is gone. FM coverage is broad. Two defects found by direct probing break promises this sprint makes explicitly: the yaml's crash workaround must survive, and the sentinel must not wedge B. Each fix is small.

## Spec adherence

The ten-step order was followed and every step has its own commit. Interface contracts were honoured with the deviations listed below. Leftover check: `git grep` finds no `_export_registry_env`, `_boot_settlement`, `_boot_warm_explicit`, `_registry_write_through_resident`, `settled`, or "re-run the picker" in src/scripts/docs/tests. The only exception is `test_export_registry_env_is_gone`, which is intentional. `_BOOT_CANDIDATES_CACHE` remains, but only for `?full=1`, which the architecture allows. `start.sh` / `install.sh` / `status.sh` read the v2 shape.

Builder-flagged deviations:

1. `_seed_from_env` fingerprint kept. **Accepted (INFO).** `reconcile_from_resolution` keeps `seed_state` in step (`test_reconcile_is_idempotent_and_keeps_seed_state_in_step`). Recorded as debt below.
2. Extra `cache_unsaved` degraded kind. **Accepted**, but its trigger is wrong when the file is corrupt (see BLOCK 1).
3. `ARAIL_ENV_PREF_A/B` markers. **Accepted (INFO).** They are process-global and inherited by child processes. That is harmless because the value is the pre-stamp `.env` value. An `.env` edit is only seen after a restart, which matches existing env semantics.
4. Ollama down gives pin > cache > env. **Accepted.** An explicit pin should beat a stale cache.
5. A8 (boot warm independent of `ARAIL_AUTOCHECKS`). **Accepted as operator decision.** One consequence should be stated in the PR: a maximus box with no v1 yaml now loads auto-B in-process on the first boot after upgrade. If that model SIGABRTs, the first boot crashes once and FM5 covers the next. That only holds if BLOCK 2 is fixed.
6. A first page load can be served from the stale on-disk cache. **Accepted (INFO).**
7. The perf target was measured on TestClient, not a real `/api/ready`. **ASK** for QA to measure it on the real `./arailctl start`.
8. `tests/cli/*.sh` were not run. **ASK** for QA to run them with a worktree `.venv` (`start.sh` banner text changed shape).

## Code quality findings

- [BLOCK] **B1: a corrupt or hand-mangled `model_defaults.yaml` is silently overwritten and the operator's pins are destroyed with no backup.** `resolve_and_apply` → `write_cache` → `_write_cache_unlocked` treats `cur["ok"] == False` as "pins = none" and then atomically writes over the file. I reproduced this in a scratch dir: a v2 file with `pins: {a: llama-ai-eng, b: none}` plus one malformed line. After one `resolve_and_apply()` it reads `pins: {a: null, b: null}`, and `saved=True` even though the strip text says "couldn't read or save". `b: none` is exactly the operator's SIGABRT workaround. Losing it to a typo means the next boot auto-picks and loads the crashing model. FM10 says "pins treated as none", which applies in memory only. It never licensed erasing the file. Fix: when `read()` is not ok, either skip the write (resolve in memory only) or first copy the unreadable file to `.model_defaults.corrupt.bak`, and make the degraded detail say which happened. `_update_warm_unlocked` already refuses on `not ok`; `write_cache` must match. Add a test: corrupt file → bytes unchanged (or a backup exists) after `resolve_and_apply`. `write_pin` has the same `cur["ok"] else {}` pattern. An explicit operator pin overwriting a corrupt file is more defensible, but it should also back up.
- [BLOCK] **B2: the crash sentinel can wedge slot B indefinitely through PID reuse, and while that happens it also stops detecting the crash.** `crashed_model()` and `_loading_elsewhere()` decide on `_pid_alive(pid)` alone, and `PermissionError` counts as alive. `started_at` is written but never read. After a SIGABRT and a machine reboot, the dead portal's pid is commonly reused by a long-lived system process. I reproduced this with the sentinel pid set to `1`: `crashed_model() → None`, `_loading_elsewhere() → True`. The effect is that every boot returns `warm_b = deferred` with the message "Another ARAIL instance is loading the deep model", forever, and the model that crashed is no longer on the skip list. Nothing appears in the degraded strip, because deferral is only an activity `info`. The only exit is an undocumented `POST /api/models/pin` for B. Fix: treat a sentinel as "loading elsewhere" only if it is younger than a bound, for example `started_at` within 10 min (a deep load is minutes, not hours). Older sentinels are a crash for `crashed_model()` regardless of pid liveness. Ideally also stamp process start time or boot id (`sysctl kern.boottime`) so a sentinel from a previous boot is always stale. Tests: live-but-old pid → crashed, not deferred; previous boot → crashed.
- [ASK] **A1: FM5 hole on the fallback path.** `_fallback()` (resolver timeout or exception) returns cache B without applying `skip_b`. The cache written during the crashing boot names the crashing model, because the resolve runs before the warm. So boot N+1 with a slow `/api/show` or a resolver exception warms the crashed model again. Apply `skip_b` in `_fallback` (B → none, `crashed_last_boot`) and add a test.
- [ASK] **A2: `warm` state is wiped by every re-resolve.** `resolve_and_apply` sets `out["warm"] = {}` and `write_cache` persists it, and `_LAST.clear()` drops the in-memory copy. So pinning A erases `warm.b` (shown as `null` in `/api/models/boot` and `status`) even though B is still resident. Carry forward the warm values of slots that weren't re-warmed.
- [INFO] A pin on slot A re-resolves B as well. If the new A is larger, B can be dropped (`insufficient_ram`) and tier1 is disabled while a B runtime is still resident in-process. The registry and the runtime then disagree until restart. Either compute `restart_required` for this case too or don't re-decide B on an A-only pin.
- [INFO] `_ollama_base()` can't parse a bracketed IPv6 `OLLAMA_HOST` (`[::1]:11434`), so it is treated as "Ollama down". This fails safe (no egress), but the result is a misleading `ollama_down`.
- [INFO] `_validate_pin` realpath containment rejects a model dir that is a symlink pointing outside `ARAIL_MODELS_DIR`. This is stricter than before and a sane default. Mention it in docs/cli.md.
- [INFO] `_raise_for_status_with_detail` rebuilds the exception with `type(exc)(msg)`, which drops `HTTPError.response`. No caller in `src/` reads `.response`, so it is harmless today. Passing `response=exc.response` would make it future-proof.
- [INFO] `_resolve_a` is ~85 lines with nested closures. It is readable, but a split (eligibility filter / precedence / ranking) would help, and `rank()` re-loads the catalog for every candidate.

## Security findings

- [INFO] Secrets: `_raise_for_status_with_detail` reads only the response body, truncated to 160 chars. Request headers are never touched (`test_complete_error_includes_ollama_error_text`). Activity events and degraded details carry model ids and models-dir paths only. No `secrets.env` read or write is added.
- [INFO] Airgap: nothing in the diff changes the `LAB_MODE` default or `is_airgapped()`. The resolver goes through `_ollama_base()`, which refuses any non-loopback host before making a request. That is tested under `LAB_MODE=airgapped` for five host values. The strip renders `textContent` only, with no links. `hf_url` is suppressed when airgapped.
- [INFO] Path traversal: `POST /api/models/pin` for B now does realpath containment. Absolute paths are accepted only when `AEROLLM_MODEL` already points there. This is a new and correct hardening.
- [INFO] Model paths: there is no absolute default. `ARAIL_MODELS_DIR` defaults to the relative `lab/models`.
- [INFO] One-PKB-root invariant: the diff does not touch `PKB_ROOT` or `pkb_index`. The sentinel lives under the per-instance `DATA_DIR`.
- [INFO] Boot never blocks: `_boot_models` is a task, the resolver is `to_thread` with a 5 s hard cap, and Ollama down still reaches `ready` (`test_portal_starts_ready_with_ollama_down`). A chat turn at boot can queue behind a multi-GB B load under `inference_slot`, which the architecture accepted.

## Test coverage assessment

I ran the sprint-related suites (resolve, defaults, boot models, deep warm, boot/pin/settle endpoints, banner DOM, status block, integration, registry, router, chat default, warm-up, isolation audit, chat slots contract): **331 passed, 2 skipped, 1 failed**. The failure, `test_egress_is_the_only_getenv_data_dir_bypass_of_config` (flags `hardware.py`), is pre-existing on main (`hardware.py` was last touched at `2e0a19cb` and is not in this diff).

Every FM1–FM18 row maps to a named test. FM8 and FM12 live in `router/test_keep_alive.py` and `registry/test_reconcile_from_resolution.py` without FM labels. The gaps are exactly the BLOCK and ASK items: FM10 has no test asserting the corrupt file is preserved, FM5 has no PID-reuse or previous-boot test, and FM5 has no resolver-fallback test. The removed PR #186 assertions are listed with their replacements in BUILD_LOG, as the architecture required.

## Performance assessment

`resolve_boot` p95 < 1 s over 10 stubbed models is tested. The `/api/ready` regression target was only approximated on TestClient (17 vs 16 ms median), so QA should measure it on a real start.

## Tech debt delta

As predicted: yaml mixes pins and cache, there is one release of shims (BACKLOG entry filed), and the sentinel heuristic. Debt the architecture did not anticipate, to be recorded:
- `_seed_from_env` fingerprint logic survives alongside `reconcile_from_resolution`, so there are two writers of tier0/tier1 seed state. It is inert after boot, but it is a second path to keep consistent. Delete it with the shims.
- There are two locks (`model_resolve.LOCK`, `model_defaults._WRITE_LOCK`) instead of the single lock the architecture specified. The ordering today is always LOCK → _WRITE_LOCK, but nothing enforces that.

## Required actions before merge

1. **B1:** never overwrite an unreadable `model_defaults.yaml` without a backup. Skip the cache write (or back up first) when `read()` is not ok. Make `cache_unsaved` truthful. Add a test that a corrupt file's bytes or backup survive `resolve_and_apply`.
2. **B2:** bound the sentinel by age and/or boot id so PID reuse can neither wedge B in `deferred` nor hide a crash. Add tests for a live-but-stale pid and for a previous-boot sentinel.
3. **A1:** apply `skip_b` in `_fallback()` and add a test.
4. **A2:** preserve `warm` for slots not re-warmed across `resolve_and_apply`.
5. QA: real `./arailctl start` time-to-ready vs main, and the `tests/cli/*.sh` drivers with a worktree `.venv`.
6. Add the two unanticipated debt items above to ARCHITECTURE.md's tech-debt section (or BACKLOG) on re-review.

---

# Round 2 re-review

**Date:** 2026-10-21
**Build:** BUILD_LOG.md "Round 2" at 579d6038 (fix commits 5027caf6, bd7c2f28, d10d6e94, 4eafcc61)

## Verdict: WEAK_PASS

All four round-1 findings are fixed and verified by code reading plus scratch repros. No new BLOCK. Three new ASKs are follow-ups, not merge blockers.

## Re-verification of round-1 findings

- **B1 (corrupt yaml overwritten): fixed.** Repro: wrote an unparseable `model_defaults.yaml`, called `write_cache`. It returned `False` and the bytes were unchanged. `write_pin` on the corrupt file wrote `.model_defaults.corrupt.bak` before replacing the file. The degraded note now tells "unreadable" apart from "not writable".
- **B2 (sentinel PID reuse wedge): fixed.** Repro with `_sentinel_is_live` and pid=1 (always alive):
  - fresh, same boot: live (correct)
  - 1 h old: stale
  - other boot id: stale
  - missing `started_at`: stale (this also covers sentinels written before this fix)
  
  A deferred warm now appears in the degraded strip (`warm_deferred`).
- **A1 (fallback ignored crash list): fixed.** `_fallback` takes `skip_b` on both the exception path and the timeout path and drops a cached B that matches it. The tests for both paths pass. A guard test confirms a non-crashed cached B is kept.
- **A2 (re-resolve wiped warm): fixed.** Warm status carries forward only from this process's `_LAST` and only when the slot's model is unchanged. The keep and drop tests pass.

Targeted suites: 102 passed (defaults, boot models, deep warm, resolve). Filtered round-2 tests: 6 passed.

## Regression hunt

- [INFO] **`.bak` accumulation is bounded.** One backup per distinct corrupt content, made only on an explicit pin, which then rewrites a valid file. Repeating with identical corrupt bytes produced one timestamped extra and no growth. Edge case: if two different corrupt contents are backed up in the same second, `_backup_unreadable` skips the write because the timestamped name already exists, and it returns a path to a backup holding different bytes. This is only a cosmetic risk.
- [INFO] **Crash list does expire.** The sentinel is cleared by a successful warm, by a failed-warm exception, and by any explicit B pin (`models_api.py:348`). Clearing B's pin counts as a B pin, so the message "clear the pin to retry" is accurate. A crashed model is not skipped forever.
- [ASK] **The 600 s sentinel age can mislabel a slow live load as a crash.** I checked whether this causes a double load: it does not. A second instance that boots while a load older than 600 s is still running gets `crashed_model() == model`. It therefore skips B and shows a false "crashed last boot" note instead of loading twice. The loading instance clears the sentinel when it finishes, so the next boot is clean. Because the operator runs Worlds serially (see memory), the risk is low. Follow-up: measure a cold 7B aerollm load on the slowest supported disk and set `SENTINEL_MAX_AGE_S` to at least 2x that, or refresh `started_at` as a heartbeat during the load.
- [ASK] **macOS boot id includes a localtime-rendered date string.** `sysctl -n kern.boottime` returns `{ sec = N, usec = M } <ctime>`. It was stable across repeated reads. However, the ctime part is rendered in the current timezone, so a TZ or DST change between `write_sentinel` and the check makes the ids differ. A live load would then read as stale, which is the same mislabel as above. This only matters inside the 10-minute window. Follow-up: compare only `sec`/`usec`. If the lookup fails (returns None), the check falls back to age plus pid, which is safe.
- [ASK] **`_warm_slot_b` appends `warm_deferred` to `last_resolution()["degraded"]` without holding `model_resolve.LOCK`.** A concurrent pin re-resolve can replace `_LAST`, and the note is then lost; nothing gets corrupted. If the note is lost, it never shows in the degraded strip. Follow-up: append under the lock, or route the note through `resolve_and_apply` state.
- [INFO] The two unforeseen debts the builder recorded (two seed-state writers, two locks with conventional ordering) were not predicted in ARCHITECTURE.md. Add both to the `sprints/BACKLOG.md` shim-removal entry. The builder already proposed this.

## Required actions before merge
None blocking. File the three ASKs above and the two recorded debts as follow-ups in `sprints/BACKLOG.md`. QA still owns the real `./arailctl start` timing and `tests/cli/*.sh` from round 1.
