# Test report: P1 — observability + guardrails over the agents that already exist

**Date:** 2026-09-22
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `edb507f8` (R-loop verified); QA
commits `fe3c5cb5`…`c6fe2579` on `qukaizen/arail-buddy-front-and-center`
**Worktree:** `~/ProJects/arail-buddy-wt` — the main checkout was not touched
**Verdict:** **FAIL**

Not a design failure. The spine is sound, the hot-path number reproduces
exactly, W1–W4 all pass, and the blind tests found the mechanism working as
specified. The FAIL is carried by **eight defects**, two of which are
security-facing (a purge that reports success it did not achieve, and an
`Authorization` header fragment on disk with the recorder off) and one of
which is a **21-test regression** the previous sweeps missed because it only
appears on a worktree that has run the suite enough times.

---

## Allocation spent vs the 30/30/20/10/10 target

263 new tests in seven files. Counted by the concern each test asserts, not
by file:

| Slice | Target | Spent | Tests | Where |
|---|---|---|---|---|
| Setup / clean machine | 30% | **29%** | 76 | `test_qa_setup_fresh_lab.py` (50) + the failure-injection and two-roots rows in `test_qa_win_conditions_and_hotpath.py` (26) |
| Buddy | 30% | **30%** | 79 | `test_qa_buddy.py` (26) + `test_qa_blind_hold.py`'s speaker/hold-behaviour half (22) + `test_qa_blind_attribution.py`'s hop/reentrancy half (31) |
| Security | 20% | **21%** | 56 | `test_qa_security_surface.py` (48) + `test_qa_blind_recorder_secrets.py`'s R1/fail-closed rows (8) |
| Happy path | 10% | **10%** | 27 | W1–W4 rows + the recorder-on capture path in `test_qa_blind_recorder_secrets.py` |
| Regression | 10% | **10%** | 25 | `/metrics`, `per_label_snapshot`, `slot_pressure`, chat's `ui` bucket, F18's burst, the 181→366-file differential, order/idempotence sweeps |

Held-out authorship honoured: **QA-BLIND-1, -2 and -3 were written from
ARCHITECTURE.md's contracts alone**, before opening
`test_flight_recorder.py`, `test_halt_gate.py`, `test_speech_gate_wiring.py`
or `test_agent_attribution_wiring.py`. Three of the eight defects below came
out of those three files.

---

## Win conditions

| W | Result | Proven by |
|---|---|---|
| **W1** — every agent model call visible within 2 s with seven fields | **PASS** | `test_w1_three_scripted_agent_calls_each_carry_all_seven_fields` (3/3 calls, every field non-null, `deep_reason_detail` compared to a live `explain()` rather than a copy, TTFT `measured` on the streaming path and an explicit `non_streaming` with a null number elsewhere), `test_w1_the_lane_view_shows_all_seven_fields_for_each_call`, `test_w1_the_lane_card_renders_a_column_header_for_every_field` (markup, not bare words), `test_w1_is_push_based_so_the_two_second_bound_cannot_be_missed` (the subscriber's queue holds the event **before `record()` returns** — asserted synchronously), `test_w1_a_record_from_a_foreign_thread_still_wakes_the_subscriber`, and one marked wall-clock row measuring < 2 s delivery |
| **W2** — ≥ 2 agent ids, 0 in a generic `agent` bucket | **PASS** | `test_a_realistic_session_has_no_generic_agent_bucket_and_no_unattributed` (5 calls → `ui` 1, `agent:*` 2, `sys:*` 2, `unattributed` **0**), `test_w2_no_generic_agent_bucket_after_a_scripted_session`, `test_legacy_agent_bucket_is_renamed_once_and_idempotently`, `test_migration_merges_rather_than_clobbers_an_existing_legacy_key` |
| **W3** — the kill switch holds for 60 s | **PASS** | `test_no_new_admitted_agent_trace_for_a_simulated_sixty_seconds` (132 refusals, 0 backend calls, 0 admitted agent traces, simulated clock), all 22 parameterised `FIXED_LANES` × {`complete`, `stream_complete`} rows, `test_w3_the_admin_hold_control_stops_every_agent_end_to_end` through the real HTTP control, `test_hold_survives_a_simulated_process_restart` |
| **W4** — recorder off writes 0 bodies | **PASS** | `test_recorder_off_by_default_writes_no_body_anywhere_under_data_dir`, `…_for_a_streamed_call_either`, `test_w4_recorder_off_means_zero_bodies_over_a_scripted_session` (20 calls, whole-tree grep), and the four recorder-off read-path tests. **Caveat:** two *non-body* leak paths reach disk with the recorder off — see F2 and F6. W4's letter holds; its spirit is dented. |
| **W5** — the operator's witness line | **OPEN — operator's alone** | Not written, and not QA's to write. `test_w5_is_the_operators_line_and_is_not_faked_here` currently **fails**: `SPRINT.md` never names W5 or "witness", so the one win condition that needs the operator has no row, placeholder or phase in the ledger and can be shipped past silently. |

---

## Test inventory

| # | Test file | Category | Covers | Status |
|---|---|---|---|---|
| 1 | `test_qa_blind_recorder_secrets.py` (27) | security / happy | **QA-BLIND-1**: recorder off/on, whole-tree grep, the three `secrets.env` states incl. `chmod 000` (R1), all 12 documented shape patterns, redact-then-truncate boundary, F10 latching, purge-then-re-enable | 24 pass, **3 fail** (F1, F4, F5) |
| 2 | `test_qa_blind_hold.py` (44) | Buddy / security | **QA-BLIND-2**: 11 lanes × 2 methods refused, 4 `sys:*` + `ui` + unattributed admitted, admission-control-not-cancellation, 7 speech funnels both directions, SRE exemption, F15 restart, in-flight counter under 10 threads | 44 pass |
| 3 | `test_qa_blind_attribution.py` (31) | Buddy / regression | **QA-BLIND-3**: scripted session, 4 attribution hops, `spawn_thread` vs bare `Thread`, F5 shared cached router, subprocess single-writer, 16 hostile ids, reentrancy, legacy migration | 31 pass |
| 4 | `test_qa_buddy.py` (26) | Buddy | attribution end-to-end through the real `deep_policy` chain; loader contract; deep→fast fallback; hold (0 calls, 0 lines, daemon lines continue); **`dream()` to completion** incl. idempotence, model-unavailable placeholder, PKB write failure, recorder on/off; `_normalize_keep_alive` int; `ARAIL_AGENT_STREAM_FAST` default-on + Ollama-down fallback; TTFT truth table | 25 pass, 1 xfail (S3) |
| 5 | `test_qa_setup_fresh_lab.py` (50) | setup | empty DATA_DIR boot; honest empty states; 8 tier-gate rows + 2 before-body-parse rows; legacy scan × 9 log shapes incl. 10 MB and unreadable; `flight_recorder.json` × 11 broken states; unwritable/not-a-dir DATA_DIR; no filesystem enumeration; two roots | 49 pass, **1 fail** (F3) |
| 6 | `test_qa_security_surface.py` (48) | security | recorder-off on all four read paths + `/api/activity/recent`; no secret in `error_class`, `call_site`, metadata, or the lanes payload; CSRF × 5 shapes × 3 endpoints; untrusted Host; no-GET-mutates; purge fidelity, idempotence, atomicity, malformed lines; LAN-bind both branches + 8 classification rows | 43 pass, **4 fail** (F2, F6, F7, F8), 1 xfail (R7) |
| 7 | `test_qa_win_conditions_and_hotpath.py` (37) | happy / perf / regression | W1–W4; 10 column headers; push-based delivery; hot path × 5 configurations; rotation ceiling; drop counter; F18 burst; 9 hostile-field no-raise rows; 8-thread JSONL integrity; counter under concurrency; slow SSE subscriber; subscriber cleanup; 2 real subprocesses × 2 roots; `/metrics`; chat bucket | 35 pass, **1 fail** (W5 ledger), 1 xfail (abandoned stream) |

Running the seven files together: **251 passed, 9 failed, 3 xfailed** in 8 s.
Identical result in reverse file order and under two independent shuffles —
no order dependence, no non-idempotence. Run alongside the sprint's own 27
files and the repo's 27 pre-existing `test_qa_*` files (61 files, 1,700
tests): **1,624 passed, 9 failed, 6 xfailed** — the same nine failures, so
none of them is an interaction artefact and none of the builder's tests break
in the presence of mine. (One interaction *was* found and fixed in my own
code: a hand-built `CostTracker` stand-in that only worked when
`test_costs_legacy_migration.py` had not run first; it now allocates and runs
the real `__init__` against a patched `DATA_DIR`.)

---

## Failures

Ranked by severity. Every row has a committed reproducing test.

### Must fix before ship

| # | Symptom | file:line | Reproducing test | Concrete failure scenario |
|---|---|---|---|---|
| **F7** | The legacy-bodies purge reports `{"purged": 4}` while **every body is still on disk**. `purge_jsonl_bodies` counts records as it streams and catches `OSError` around the `os.replace`, returning the pre-replace count. | `src/arail/jsonl_purge.py:44-72` (`purged += 1` inside the loop, `except OSError` at :67) | `test_purge_leaves_the_original_intact_when_the_replace_fails` | A family lab's disk is full, or `lab/data` is on a read-only mount after a failed update. The operator sees the disclosure notice, presses **[Purge]**, is told 4 bodies were removed, and dismisses the notice. All four unredacted prompt bodies remain in `activity.jsonl`. The original file is correctly left intact and no stray `.purge_tmp` survives — only the *claim* is false. |
| **F8** | The same failed purge **clears the in-memory buffer anyway**, so `GET /api/activity/recent` (every tier, no auth) shows a clean log over an unpurged file. | `src/arail/activity.py:264-270` — the memory pass runs unconditionally after `purge_jsonl_bodies` returns | `test_purge_does_not_clear_memory_when_the_disk_half_failed` | Compounding F7: after the false success the operator cannot even *see* what leaked. The evidence is hidden, not removed. Fix both together: have the shared helper return a per-path result and only clear memory for paths that actually replaced. |
| **F2** | The goal-parser's out-of-process leg writes up to 80 chars of the **child's raw exception message** into `error_class`, which the schema documents as a class name — on disk, recorder-independent. An `Authorization: Bearer …` fragment reaches `agent_traces.jsonl` with the recorder **off**. | `src/arail/skills/goal_parser/__init__.py:250` (`error_class=str(payload.get("error", "unknown"))[:80]`), producer at `_subprocess_runner.py:82-88` (`f"{type(e).__name__}: {e}"`) | `test_the_subprocess_error_field_is_a_class_name_not_the_childs_message` | A cloud Compute Source is configured and the key is stale. The child's `requests`/httpx error text quotes the request, the parent stamps 80 chars of it into the trace store, and the sprint's headline promise — "no bodies on disk with the recorder off" — is violated by a field nobody looks at. Filed as BACKLOG **S2**; this raises it from theoretical to demonstrated. Fix: `error_class=type(exc).__name__` on the child side, and an allow-list (`[A-Za-z_][A-Za-z0-9_]*`) on the parent side. |
| **F4** | `from arail import redact` at the body-capture site sits **outside any `try`**, so a broken/shadowed module raises into `ModelRouter.complete()` after the backend has already produced an answer. | `src/arail/router/core.py:282` and `:371` | `test_a_broken_redact_module_does_not_raise_into_an_inference` | REVIEW.md **D6**'s third instance. The builder guarded `_slot_info`'s and `_halted`'s imports; this one was missed. Cost is an inference whose answer is computed, billed, and then thrown away as an exception the agent never expected. Two lines of `try/except`. |
| **F5** | The `redact.capture_body(...)` **call** at the same two sites is likewise unguarded, so the chokepoint borrows its no-raise guarantee from a leaf module's internal discipline instead of owning it. | `src/arail/router/core.py:283`, `:373` | `test_a_raising_capture_body_does_not_raise_into_an_inference` | Same blast radius as F4, one layer in. Fold both into one `try` and the whole class is closed. |
| **F9** | **21 pre-existing tests fail only on this branch.** All of `tests/test_recap_{core,paranoid,robotouille_mock}.py`, with `ResultKind.COST_EXCEEDED: Cost ceiling $5.0000 reached (billed $6.2049)`. | `tests/conftest.py:291-296` (docstring) — the hermeticity fixture deliberately does not isolate `costs.json` | `ARAIL_DATA_DIR=$(mktemp -d) pytest tests/test_recap_core.py tests/test_recap_paranoid.py tests/test_recap_robotouille_mock.py` → **54 passed**; the same command without it → **21 failed, 33 passed** | Root cause below. This is the cheap one and it is cheap to fix. |

**F9 in full.** `cost_tracker` is a process-global singleton bound to the
real `lab/data/costs.json` at import, before any fixture runs. Every test
that drives a `ModelRouter` bills it. This worktree's file now holds **2,917
tracked calls / $6.2049 billed**, of which the top buckets are
`agent:researcher` 1044, `agent:buddy` 749, `unattributed` 664 — i.e. almost
entirely fake-backend test traffic, now neatly per-agent-attributed thanks to
this sprint. The pristine main checkout, after its own full sweep, holds 42
calls / $0.137. One sweep of the sprint's own 25 files adds 38 calls /
$0.076, so ~66 sweeps crosses recap's $5.00 hard ceiling; this sprint ran
well past that. Three consequences, in order of importance:

1. **It disables a real product guardrail in the developer's lab.** recap's
   cost ceiling is exhausted, so recap refuses to run at all.
2. **It pollutes the exact surface W2 is measured on.** The operator's cost
   page attributes 1,044 researcher calls and 749 Buddy calls that never
   happened.
3. It makes the suite progressively redder on any machine that runs it twice,
   which is why the earlier 181-file sweeps reported 0 branch-only failures
   and my 366-file sweep reports 21.

The builder's conftest docstring calls this leak "pre-existing and out of
this sprint's scope." It was a defensible call before the sprint added
thousands of billed test calls; it is not now. The fix is the three lines my
own test files already use (`monkeypatch.setattr(cost_tracker, "_data_path",
…)` plus zeroing the accumulators) lifted into
`_isolated_agent_observability_data_root`. The operator may also want to
decide whether this worktree's `lab/data/costs.json` should be reset — I did
not touch it.

### Fix or file as debt (builder's call, not ship-blocking)

| # | Symptom | file:line | Reproducing test | Note |
|---|---|---|---|---|
| **F1** | A JSON-quoted `{"api_key": "<16 chars>"}` value is **not** redacted. The assignment pattern requires the key name to be followed by optional whitespace then `:`/`=`; a quoted JSON key never is. | `src/arail/redact.py:52-54` | `test_json_quoted_api_key_is_redacted_before_disk` | A spec gap, not a build defect — the builder implemented ARCHITECTURE.md #5's pattern list exactly. But a JSON config fragment or a tool response pasted into an agent prompt is the ordinary case, and with the recorder on that value reaches disk. One `["']?` in the pattern closes it. All 12 documented patterns pass. |
| **F3** | `activity.scan_for_legacy_bodies()` raises `AttributeError` on any `activity.jsonl` line whose JSON is not a dict — `{"data": "oops"}`, `[1,2,3]`, `123`, `"str"` — against its own "Never raises" docstring. Only `OSError` is caught. | `src/arail/activity.py:200` and `:212-226` | `test_scan_with_malformed_lines_counts_the_valid_ones_and_survives` | 500s `GET /api/admin/legacy-bodies`, and since `loadLegacyBodiesNotice()` runs on **every** admin page load, the notice breaks permanently for a lab with one corrupt line. Reachable via a torn write on a crash. The purge path already survives this (it wraps `has_body` in `except Exception`); the scan does not. |
| **F6** | Malformed JSON on `POST /api/admin/agents/hold` (and the recorder toggle) is a **500** — `await request.json()` is unguarded. | `src/arail/portal/app.py:6326`, `:6344` | `test_malformed_json_on_a_mutating_admin_endpoint_is_not_a_500` | Curl-only, after the tier gate, no information disclosed. Low. |
| **F10** | `config.PKB_ROOT` is not isolated by the conftest, so tests write the developer's real `lab/pkb`, **and** `dream_daemon._dream_once`'s "already dreamed today" check reads it — meaning any test of `_dream_once` that does not patch `_dream_file_for` silently no-ops on a machine that has dreamed today. | `src/arail/agents/dream_daemon.py:71-78` vs `_builtin_buddy.py:1445` (two different resolutions of the same path) | `test_dream_once_idempotence_reads_the_unisolated_real_pkb_root` | Evidence in the tree: `lab/pkb/agents/buddy/dreams/2026-09-22.md` (written 22:37 on 21 Sep, during the fix loop) and `lab/pkb/agents/buddy/state.json` (rewritten by the full sweep). Same silently-vacuous class the orchestrator already caught once. My tests isolate it; the suite should too. |
| **F11** | Running the suite — **on main as well as on this branch** — starts the job daemon, which executes the operator's real configured world jobs as subprocesses (`python3 lab/spec_drift.py /Users/netsushi/ProJects/qukaizen-team`) and writes receipts to a repo-root `logs/` that is **not** in `.gitignore`. | `src/arail/agents/job_daemon.py:59` (`LOGS_ROOT = Path(os.getenv("ARAIL_SCHEDULER_LOG_DIR", "logs/scheduler"))`) | Observed, not asserted: 24 receipts here, 7 in the pristine baseline checkout, all written during test sweeps | **Pre-existing, not a regression** — but it means `git status` is dirty after any sweep, and a test run executes real code against a sibling repo. For a product whose thesis is "it runs on other people's machines" this deserves a BACKLOG entry: default `LOGS_ROOT` under `DATA_DIR`, and add `logs/` to `.gitignore`. |

### Documented as xfail(strict) rather than failed

Three accepted-debt behaviours are pinned so closing them turns the suite red
instead of passing silently:

- **BACKLOG S3, now live (R5).** Buddy's dream announcement writes 160 chars
  of raw model output to `activity.jsonl`, unredacted and
  recorder-independent, and the legacy-bodies scanner can never find it
  because it is not a `prompt_trace`
  (`test_dream_preview_is_not_redacted_and_is_recorder_independent`).
  Exercised, not reasoned about: with the recorder off, a planted
  `sk-…` in the model's reflection reaches disk.
- **REVIEW.md R7.** `POST /api/admin/flight-recorder {"purge": true}` with no
  `enabled` key turns the recorder off and resets `changed_at`
  (`test_a_purge_only_post_does_not_silently_turn_the_recorder_off`).
- **New, small.** An **abandoned stream leaves no trace at all**:
  `stream_complete` records only on the terminal `ModelResponse` or in
  `except Exception`, and `GeneratorExit` is neither — so a partially
  consumed agent stream is invisible in the lane view, against contract #3's
  "exactly one record per call" and W1's "every agent model call is visible"
  (`test_an_abandoned_stream_is_still_accounted_for`).

---

## Security review

Each row names what was actually checked.

| Surface | Checked | Findings |
|---|---|---|
| **Body capture (the sprint's own surface)** | Recorder off: 20-call session + one streamed call, whole-`DATA_DIR`-tree grep plus a recursive JSON walk for any non-null `prompt`/`response` at any depth. Recorder on: planted key absent from the tree, `redactions >= 1`, and `REDACTED` present in the stored prompt. Presence asserted before every absence. | Clean for bodies. **F2** (child error text) and **S3** (dream preview) are non-body paths that do reach disk with the recorder off. |
| **Redaction** | All 12 documented shape patterns, one parameterised row each. Known-value pass in isolation with an opaque secret only findable via `secrets.env`. Three `secrets.env` states: absent → shape-pass-only body (correct), present+empty → same (correct), present+`chmod 000` → `capture_body` returns `None` (**R1's fix verified, not assumed**, and verified distinguishable from absent). Redact-then-truncate at the 2000-char boundary. `capture_body` fail-closed when `_redact_strict` raises. | **F1** — quoted-JSON `api_key` not matched. |
| **Metadata fields** | `error_class` with a backend exception whose message carries `?api_key=` and `Authorization: Bearer` → only `RuntimeError` on disk (correct for the in-process path). `call_site` matched against `^[\w.]+:\d+$`. Every non-`bodies` field of a captured record grepped for the planted key. A hostile agent id (`buddy"><img src=x onerror=…>`) checked for survival into the lanes payload. | **F2** on the out-of-process path only. In-process metadata is clean; the sanitiser strips markup and caps at 64 chars. |
| **Tier gate / unauthenticated surface** | 8 rows: all four new GETs (**including the SSE stream**, which REVIEW.md noted was missing from the builder's parameterised list) and all four new POSTs 404 on minimalist. Two rows confirm the 404 lands **before** `await request.json()` by posting `{not json at all`. `/api/agents/prompts` with the recorder flag file planted on a minimalist lab serves no body keys. No new `GET` mutates recorder, hold or notice state. | Clean. `_require_surface("admin")` is the first statement in all six handlers. |
| **CSRF / DNS rebinding** | Per mutating endpoint (hold, recorder, purge): `Sec-Fetch-Site: cross-site` → 403 `cross_site`; `Sec-Fetch-Site: none` → 403; mismatched `Origin` → 403 `cross_origin`; `Origin: null` → 403; and the **positive** row (`same-origin` + matching `Origin` → 200), so a blanket-refusal regression would be caught. Untrusted `Host` → 403 `untrusted_host` ahead of the tier gate. | Clean. Inherited from `local_trust_boundary`, no new CSRF code, and now pinned. |
| **File I/O** | `chmod 000` on `secrets.env`, `activity.jsonl` and `flight_recorder.json`; a read-only `DATA_DIR`; a `DATA_DIR` that is a regular file; `ENOSPC` injected at the `open` boundary. Purge atomicity by failing `os.replace`: original byte-identical, no stray `.purge_tmp`. Rotation bounded to exactly two files across three generations. No path traversal reachable — the only user-controlled string that reaches a path is the agent id, and it is sanitised to `[a-z0-9_.-]{1,64}` with `..` broken first (`../../etc/passwd` → `etc_passwd`, verified). | **F7/F8** (purge honesty), **F3** (scan raises). Traversal clean. |
| **Network I/O** | No new outbound call. The streaming fast path uses the same `/api/chat` endpoint with `stream:true` and a 120 s timeout; `ARAIL_AGENT_STREAM_FAST` default-on verified to fall back to `complete()` on `ConnectionError` and to return `None` (not raise) when everything is down. No retry loop added, so no retry storm. | Clean. |
| **Deserialization** | Every parse on the new paths is `json.loads` on local files with a bounded `except`. No `pickle`, no `yaml.load`, no `eval` anywhere in `agent_trace.py`, `agent_context.py`, `redact.py`, `jsonl_purge.py` (grepped). `record()` survives 9 hostile field shapes including unserialisable objects, NaN, complex, bytes and control characters. | Clean except **F3**'s non-dict JSON. |
| **Crypto** | None added. `trace_id` is `secrets.token_hex(8)` — a CSPRNG, 64 bits, used only as a correlation id and never as a capability (the drill-in endpoint is tier-gated, not id-gated, so guessing one buys nothing). No comparison of secret material is performed on these paths, so constant-time compare does not arise. `secrets.env` remains the only secret store, still `0600`, still never echoed. | Clean. |
| **Dependencies** | `git diff main...HEAD -- pyproject.toml` is empty — no new dependency. No `AERO_*` / `aerollm-api` / frozen-identifier change in the diff (re-verified). | Clean. |
| **LAN exposure** | Both branches of the banner: `BIND_ADDR=0.0.0.0` + recorder on → `lan_exposed: true` in the snapshot and the warning element plus "non-loopback" copy in the page; loopback → false; LAN + recorder **off** → no warning (so it is not noise on every LAN-bound lab). Eight `bind_is_loopback` classification rows incl. `""`, whitespace and case. The airgap toggle's gate asserted to still agree with the shared helper on four values. | Clean. |

Out of scope and already filed: the portal has **no authentication**. Nothing
in this sprint widens that; `/api/agents/prompts` is a strict reduction
(every-tier unredacted 5000-char bodies before, metadata-only behind an
admin-only toggle after), which the tests confirm.

---

## Performance

`BENCHMARK.md` not filed separately; the numbers are here and are reproduced
by `pytest -m timing tests/test_qa_win_conditions_and_hotpath.py -s`.

**Baseline claim:** REVIEW.md measured 0.048 ms p95 for the full chokepoint
additions. **Reproduced on this machine**, three independent runs, 2000
`record()` calls each after 200 warm-up:

| Configuration | p50 | p95 | max | Budget | Verdict |
|---|---|---|---|---|---|
| `record()` with disk append (run 1 / 2 / 3) | 32.3 / 32.8 / 31.8 µs | **48.0 / 48.8 / 46.4 µs** | 1010 / 88 / 77 µs | 1.0 ms p95 | **pass, 20× headroom** |
| Builder's own `test_agent_trace_perf.py`, same box | — | **46.8 µs** | — | 1.0 ms | **claim confirmed** |
| Full ring (maxlen 50, evicting every append) | — | 50–58 µs | — | 1.0 ms | pass |
| Rotating file (6 MB → `os.replace` during the run) | — | 46–64 µs | — | 1.0 ms | pass |
| `ENOSPC` on every write | — | 7.2–7.8 µs | — | 1.0 ms | pass (fails fast) |
| Unwritable `DATA_DIR` | — | — | — | — | 5/5 counted in `dropped_writes`, ring intact |

Spread across runs is ±2.4 µs (5%) on p95; the single 1010 µs outlier in run
1 is the first `open()` on a cold file, and another agent session was running
a test suite on this box at the time. DE4's kill line is 5 ms — **~100×
headroom**. `fast_path_ms` p95 delta was not re-measured independently;
REVIEW.md's structural finding stands that `/api/admin/agent-lanes` is itself
in `FAST_PATH_PREFIXES` and so contaminates that metric (filed as D7).

Bounded-growth checks: rotation produces **exactly two files** across three
6 MB generations and never a `.jsonl.2`; the ring honours `ARAIL_TRACE_RING`
clamping; a burst of 1000 traces leaves `activity_log.recent(200)`
byte-identical (F18).

Concurrency: 8 threads × 25 calls through one router → **200 intact JSONL
lines**, every one parseable, all four attributions present, none lost or
interleaved. `_total_recorded` held at exactly 1600 under 8×200 concurrent
`record()` calls (the unlocked `+=` did not drift in this run; worth knowing
it is unlocked). Peak `in_flight` was exactly 10 with ten threads inside the
backend, and 0 after a raising backend. Concurrent recorder toggles never
produced a torn state file. A slow SSE subscriber is dropped after its 100-
slot queue fills, **without costing a single record** (300/300 recorded) —
the documented D7 behaviour and nothing worse. The subscriber list returns to
empty when the generator closes.

---

## Regression differential (re-established independently)

Widest slice affordable: the **whole `tests/` tree**, 366 files, both sides,
same interpreter, same box.

| | Failed | Passed | Skipped | xfail | Wall |
|---|---|---|---|---|---|
| `qukaizen/arail-buddy-front-and-center` @ `edb507f8` | **61** | 6035 | 19 | 7 | 600 s |
| pristine `main` @ `236504ca` (read-only checkout) | **41** | 5753 | 18 | 7 | 578 s |

- **Fail on both: 40.** Pre-existing; unchanged.
- **Fail only on this branch: 21.** All of `test_recap_{core,paranoid,robotouille_mock}.py` — **finding F9**, one root cause, proven by
  `ARAIL_DATA_DIR=$(mktemp -d)` flipping them to 54/54 pass.
- **Fail only on main: 1.** `test_onboarding.py::test_dashboard_unblocks_after_onboarding` — the branch legitimately fixes it (it now
  monkeypatches `_world_prompt_marker` itself, which is the documented
  remedy for the new autouse fixture's pre-seeded onboarding marker).

This supersedes the earlier "17 fail on both, 0 only on this branch" figure,
which came from a 181-file slice that did not include `test_recap_*`.

**Audit of the new autouse fixture's blast radius.** I looked for other
pre-existing tests whose meaning the fixture silently changed, as the
reviewer suspected. Result: the 366-file differential shows no *further*
first-run-state casualties beyond the one already fixed — but two adjacent
un-isolated roots remain and both bit during this pass: `costs.json` (F9,
21 tests) and `PKB_ROOT` (F10, silent vacuity). The fixture correctly
isolates what it claims to: after a full 6115-test sweep the real tree
contains **no `agent_traces.jsonl`, no `flight_recorder.json`, no
`legacy_bodies_notice.json`** — verified by a before/after `find lab -type f`
diff. What the same diff *does* show changing is `lab/data/costs.json`,
`lab/pkb/agents/buddy/state.json`, `lab/data/agent_workflows.json`,
`lab/data/egress.jsonl` and ~1640 LanceDB transaction files — of which only
the first two are in this sprint's neighbourhood.

**Order dependence and idempotence.** The seven QA files were run in declared
order, reverse order, and two independent shuffles: **identical results every
time** (251 pass / 9 fail / 3 xfail). No stray pytest processes were left by
this pass; one unrelated pytest run from a different agent session was active
on the box during part of it, which is noted against the timing numbers.

---

## Coverage delta

Coverage was not instrumented for this sprint (no `pytest-cov` in the pinned
venv, and adding it would have changed the hot-path measurement). Behavioural
delta instead, against REVIEW.md's own coverage assessment:

| Item REVIEW.md called uncovered | Now |
|---|---|
| **F9** — no test pins any `speech_gate` call site | 7 funnels asserted behaviourally in both directions (held and not held), incl. the two Buddy lines decision (a) added |
| **F16** — no coverage at all | user-defined agent id refused while held; bare-`Thread` degradation asserted to land in the *unattributed* lane, never a wrong agent id |
| **F3** — static guard never written | asserted behaviourally instead: `spawn_thread` carries context, a bare `Thread` does not |
| W1's wall-clock SSE test "does not exist" | exists, `@pytest.mark.timing`, plus the synchronous `q.qsize() == 1` ordering assertion the architecture actually specified |
| SSE stream absent from the F13 parameterised list | included, and its recorder-off read gate driven through `subscribe()` directly |
| `test_agent_trace.py:85` "would pass vacuously as root" | every privileged-file test in this pass carries an explicit `geteuid() == 0` skip |
| F20 "identity test is tautological" | the same backend asked both ways and the two strings compared to each other |
| Untested-in-practice `dream()` path | driven to completion, 8 tests |

---

## Notes for the next QA pass

1. **Un-isolated process-global singletons are this repo's recurring defect
   class.** Three found so far in one sprint: `config.DATA_DIR` (caught by
   the orchestrator), `cost_tracker._data_path` (F9), `config.PKB_ROOT`
   (F10). The pattern to look for: a module-level object that binds a path at
   import time, before any fixture can run. `activity.LOG_FILE`,
   `pkb_index`'s `_degraded_codes`, and `agent_workflows`' LanceDB table are
   the next candidates. A single `_isolated_lab_roots` fixture covering all
   of them would retire the class.
2. **"Never raises" docstrings are a claim, and two of three were false.**
   `record()` genuinely never raises (9 hostile-input rows). `capture_body`
   genuinely fails closed. `scan_for_legacy_bodies` (F3) and
   `purge_jsonl_bodies` (F7's honesty half) do not hold up. Next pass: grep
   for "never raises" / "best-effort" and test each one with a non-`OSError`
   exception.
3. **Failure-injection asymmetry.** Every path here handles `OSError`
   carefully and nothing handles `AttributeError`/`TypeError` from malformed
   *data*. The disk is well defended; the file *contents* are not.
4. **The out-of-process leg is the thin spot.** One writer, one protocol, and
   the only place in the sprint where free-form text crosses into the trace
   store (F2). When the inference gateway sprint lands, that protocol is
   where to look first.
5. **Under-tested still:** the actual `admin.html` JavaScript (asserted only
   as rendered markup — no DOM test harness exists in this repo); the
   `/api/agents/status` V7 fix beyond a fresh-lab zero; `overlap_pct` with a
   genuinely contended slot (all samples here were `in_flight == 0`); and the
   `world_routes` / `job_daemon` system-call labels, which have source-text
   coverage but no functional coverage — the builder conceded this as
   deviation #6 and it is still true.
6. **W5 needs the operator.** One row in `SPRINT.md`, in his own words,
   signed, after ten minutes at the lane view on an idle lab. W1's fields now
   render, so the screen can support the test. Until that line exists the
   sprint has four of five win conditions, not five.
