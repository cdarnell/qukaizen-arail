# Test report: Tier 1.2 — nomic-embed-text on the PKB retrieval path

**Date:** 2026-08-08
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `cc08cf7` (round 2) · `211804d` (round 1)
**Baseline:** `8cb5760`
**Prior:** [REVIEW4.md](./REVIEW4.md) (WEAK_PASS) · [REVIEW3.md](./REVIEW3.md) (BLOCK) · [REVIEW2.md](./REVIEW2.md) (BLOCK) · [ARCHITECTURE.md](./ARCHITECTURE.md)

**Round 1 verdict: FAIL** (QA-5).
**Round 2 verdict: WEAK_PASS** — QA-5 and QA-4 are genuinely dead, verified by
execution against scratch copies of the operator's real Worlds in all five
degraded states. Four documented low-severity residuals remain, all filed as
`xfail(strict=True)` reproducers.

**Would I ship this to someone's family? Yes — with the two paragraphs of
`pkb reembed` guidance at the end of this report in front of them first.**

---

## Round 2 — re-verification of the QA-5/QA-4 fix

**Commits:** `cc503d1` (fix) · `77d569b` (BACKLOG) · `cc08cf7` (BUILD_LOG)

### 1. QA-5 is dead, in every degraded state, on real corpora

The original end-to-end reproduction, re-run against scratch copies of the real
`video-games` and `qukaizen` Worlds. Every state now returns **200** with a
header the wire layer accepts:

| State | How it was reached | Result |
|---|---|---|
| `dimension` | real legacy 128-dim table, no sidecar | 200, 165-char header, `pkb reembed` intact |
| `empty` (search path) | index removed, `pkb.search()` | 200, 63-char header, command intact |
| `empty` (doctor path) | `.cache` removed, `ensure_ready(build=False)` | 200, command intact |
| `provider` (clean machine) | real `urllib` path at a closed loopback port | 200, `ollama pull nomic-embed-text` intact |
| hostile provider body | CR/LF/NUL + non-latin-1 + emoji injected into the reason | 200, no control chars, no injected headers |

In all five: zero control characters, zero non-ASCII bytes, **zero extra headers
injected**, and `h11.Connection.send` accepts the resulting `raw_headers` (the
exact call that rejected the old value). Not papered over — the 500 is gone
because the string can no longer contain a byte that causes it.

**Is the folded message still intelligible?** Yes, marginally. The em dash
becomes `?`:

> `pkb_pages index was built with a different embedding dimension than the current spec declares ? run \`./arailctl pkb reembed\` to upgrade.`

Readable, and the runnable command — the only part that matters operationally —
survives verbatim in every state. It reads like a mojibake artefact rather than
prose, which is a cosmetic cost worth one follow-up line: mapping `—` → `--`
before folding would read cleanly. Not blocking.

The multi-line `provider` message loses its line breaks and runs together
(`…Connection refused).Start it with:  ollama serve…`), but every sentence and
the `ollama pull` command survive. Also acceptable, also cosmetically improvable
(replace stripped newlines with a space rather than nothing).

One honest caveat, pre-existing and not introduced here: when *two* codes are
set at once, `embedding_status()` joins them with `"; "` and the 200-char
truncation can cut the actionable command off the end. Observed in the hostile
case, where a long provider message pushed `pkb reembed` past the limit.

### 2. QA-4 is closed, not xfail-flipped

Fed a hostile provider error body carrying
`boom\r\nX-Injected: yes\r\n\r\n<html>owned</html>\x00tail — é 💥` through the
real degraded path. The CR/LF/NUL are stripped, `X-Injected` and `Set-Cookie`
appear **nowhere** in the response headers, and the response serializes cleanly.
No injection is possible at this call site.

### 3. The new test is honest, not decorative

Four independent mutations in a scratch worktree, each confirming the test
detects a different failure:

| Mutation | Result |
|---|---|
| A — remove the ASCII fold, keep the strip | 5 red (all four real-message params + `non-latin1`) |
| B — remove the strip, keep the fold | 4 red (`crlf`/`lf`/`cr`/`nul`) |
| C — product message loses its em dash | red, with the test's own explicit message: *"trigger 'dimension''s real reason no longer contains an em dash"* |
| D — `check_read_path_health` stops degrading | red on *"trigger 'dimension' did not actually degrade retrieval_status()"* |

C and D are the ones that matter. The test does not merely *drive* the real code
path — it fails loudly if the trigger stops exercising it (D) or if the product's
message drifts away from the hazard (C). It cannot pass vacuously, and it cannot
drift out of sync with what the product emits. **This is the durable fix it
claims to be.**

Two things the builder's parameters did not reach, which I added rather than
asked for:

* `test_the_real_clean_machine_provider_message_can_actually_be_served` — the
  `provider` state is the only real reason that is **multi-line rather than
  em-dashed**, so it is the one that exercises the control-strip half of
  `_header_safe` on genuine product text. Driven through the real `urllib` path
  against a closed port.
* **QA-7** (below) — the residual class the fix does not cover.

### 4. QA-7 — new, LOW: the fix is narrower than the bug class

`_header_safe` removes CR/LF/NUL by **denylist** and ASCII-folds everything above
0x7f. Every *other* C0 control passes through unchanged — and they are not inert:

* `h11` rejects **VT (0x0b)** and **FF (0x0c)**;
* uvicorn's `httptools` transport rejects **29 of the 31**.

Both verified directly against `h11.Connection.send` and
`uvicorn…httptools_impl.HEADER_VALUE_RE`. That is the identical QA-5 500,
reached by a different byte, through the identical route QA-4 uses (up to 400
bytes of a provider's raw HTTP error body, off-box in `LAB_MODE=hybrid`; an
ANSI-coloured or non-JSON error page suffices).

Not blocking: it needs a non-default configuration *and* a hostile-or-broken
provider, and it is not the operator's shipping state. An **allowlist** (keep
printable ASCII plus space and tab) closes the whole class instead of three
members of it. Pinned by
`test_other_control_characters_are_also_removed` (`xfail(strict=True)`, 5 params).

### 5. QA-6's BACKLOG entry captures the hazard

`sprints/BACKLOG.md` now states the mechanism precisely — gate on by default,
nothing approved on all six real roots, `[]` returned *before* `_semantic_search`
runs, no degraded code set, `retrieval_status()` reporting `(True, "")` on the
same World where Buddy gets zero hits — and states the binding constraint: the
deferred context header **must not** be a naive `retrieval_status()` read, and
whoever builds it must first make the agent path consult the gate/approved state.
It links the pinning test. That is the hazard, correctly captured. The `_table()`
entry was also honestly re-filed at my measured 0.39–1.21 ms with the original
7.5 ms marked as overstated.

### 6. No regression, and one of my own defects fixed

Full suite at `1443c3f`: **52 failed, 4434 passed, 18 skipped, 11 xfailed, 7
errors** — identical failure and error counts to baseline `8cb5760`
(52F/4113P/7E), with the same single order-dependence ID swap already proven
identical in isolation. The 321 extra passes are the sprint's tests plus mine;
the 8 extra xfails are my strict reproducers.

**A defect in my own round-1 tests, found and fixed here:** three of my fixtures
called `importlib.reload(arail.dbspec.embed)`. That mints a *new*
`EmbeddingError` class, after which `pkb_index`'s call-time
`from arail.dbspec.embed import EmbeddingError` no longer matches the class other
test modules imported at collection time — silently downgrading C1's LOUD branch
to the generic SKIP branch for whatever ran next. It broke three
`test_c1_error_contract` tests when my files ran first, and it was
order-dependent, so it did not surface in the full-suite run. Replaced with
`monkeypatch` over the un-stubbed `embed_texts`; three consecutive combined runs
of all 15 QA-adjacent files now give **196 passed, 8 xfailed, 0 failed**. I am
recording this because it is exactly the kind of cross-test pollution I would
have filed against the builder.

### 7. The real-lab boundary held again

The 17,015-entry `stat` inventory was re-taken at the start of round 2 and again
at the end. `diff` is **empty** against the round-1 pre-QA snapshot in both
cases. Every reproduction in this round ran against scratch copies under the
session scratchpad.

---

## Round 1 (superseded, kept for the record)

One defect, found on the first probe of a real World's degraded state, broke a
user-facing surface on **four of the operator's five Worlds plus the root lab**,
and on **every clean machine that had not yet pulled the model**. It was a
regression introduced by this sprint. Everything else in this sprint held up
under attack — the lock, the shadow-swap, the egress guard and the read-only
`doctor` are all genuinely solid, and I say so below with the evidence.

---

## Disclosure — the operator's real lab

Read-only throughout, verified mechanically rather than asserted.

A `stat` inventory of every path under `/Users/netsushi/ProJects/qukaizen-arail/lab/`
(**17,015 entries**, mtime + size + path) was taken before any command ran and
again after the entire QA pass. `diff` is **empty**. Index mtimes, unchanged:

| Root | `pkb_pages.lance` mtime |
|---|---|
| `lab/pkb` (root lab) | 2026-08-08 12:00 |
| `lab/instances/ai` | 2026-08-03 17:14 |
| `lab/instances/debt-finance` | 2026-08-02 15:03 |
| `lab/instances/qukaizen` | 2026-08-07 09:10 |
| `lab/instances/video-games` | 2026-08-06 14:35 |
| `lab/instances/finance` | 2026-08-08 20:02 (REVIEW3's disclosed accident, untouched) |

Realistic corpora were obtained by copying `video-games/pkb` (6.4 MB, 318 files)
and `qukaizen/pkb` to the session scratchpad, and by copying `ai`'s `lancedb`
directory for the fragment-count measurement. Every write went to scratch.

---

## The finding that decides the verdict

### QA-5 — `/api/pkb/search` returns HTTP 500 on every degraded World

**Severity: HIGH. Regression. Blocks ship.**

`api_pkb_search` (`app.py:11224`) stamps `X-Retrieval-Reason: reason[:200]` when
retrieval is degraded. Starlette encodes response header values as **latin-1**.
Every degraded reason string `pkb_index` produces contains an **EM DASH**
(U+2014):

```
pkb_pages index was built with a different embedding dimension than the
current spec declares — run `./arailctl pkb reembed` to upgrade.
```

So constructing the response raises `UnicodeEncodeError` before it is returned.

Minimal repro, against a scratch copy of the real `qukaizen` World:

```
LAB_PKB=<scratch>/qkz python -c "
import asyncio
from arail import pkb
from arail.portal import app as A
pkb.search('hello', ROOT)          # sets the real 'dimension' degrade
A.pkb_search = lambda q: pkb.search(q, ROOT)
asyncio.run(A.api_pkb_search(q='hello'))"
→ UnicodeEncodeError: 'latin-1' codec can't encode character '—' in position 94
```

Blast radius — this endpoint backs the KB search box in `dashboard.html:1886`,
`agents.html:1554` and `docs_hub.html:367`:

* four of five real Worlds + the root lab (legacy 128-dim, no sidecar → `dimension`)
* any lab whose index has not been built yet (`empty`)
* any clean machine where `ollama pull nomic-embed-text` has not run (`provider`)

At `8cb5760` the endpoint was `return pkb_search(q.strip())` and could not 500.
The mechanism added to make degradation *honest* is what breaks the surface it
was supposed to be honest on.

Why the sprint's own tests missed it: `tests/test_pkb_search_api_status.py`
asserts against a **hand-written ASCII** reason ("…provenance disagrees with the
current spec"), not a string the product emits.

**Fix:** sanitise before the header — ASCII-fold or `reason.encode("ascii",
"replace").decode()`, and strip CR/LF/NUL at the same time (QA-4 below shares
this call site). Pinned by
`tests/test_qa_tier1_search_header_safety.py::test_the_real_degraded_messages_can_actually_be_served`,
which parametrises over the four real message texts.

---

## Test inventory

New files, all committed. **Round 2 state: 67 passing, 12 xfailed (strict defect
reproducers), 0 failing.** (Round 1 was 60 passing / 7 xfailed / 5 failing, the
5 being the QA-5 defect report, now green.)

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| **`tests/test_qa_tier1_buddy_retrieval.py`** — Buddy (30%) |
| 1 | `agent_retrieval_returns_nothing_and_reports_healthy_on_a_legacy_world` | Buddy | REVIEW4 QA item 1; C1's agent-status claim | pass (pins QA-6) |
| 2 | `ungated_search_on_a_legacy_world_does_set_the_dimension_code` | Buddy | the status contract works on the ungated path | pass |
| 3 | `keyword_fallback_on_a_legacy_world_cannot_answer_a_question` | Buddy | quantifies "degrades to keyword" honestly | pass |
| 4 | `agent_retrieval_is_semantic_once_provenance_agrees` | Happy | the +40.6pp path end-to-end | pass |
| 5 | `gate_off_on_a_legacy_world_surfaces_the_degrade_to_the_agent` | Buddy | the fix for #1 is reachable | pass |
| **`tests/test_qa_tier1_egress_guard.py`** — Security (20%) |
| 6–15 | `airgapped_refuses_every_non_loopback_form` ×10 | Security | FM16 adversarial hosts | pass |
| 16–19 | `loopback_forms_are_allowed` ×4 | Happy | incl. `[::1]`, uppercase | pass |
| 20 | `ollama_host_is_not_a_second_way_around_the_guard` | Security | the second env var feeding `ollama_root()` | pass |
| 21 | `hybrid_allows_non_loopback_because_the_operator_opted_in` | Happy | C3 opt-in | pass |
| 22–28 | `only_the_literal_hybrid_mode_opens_the_door` ×7 | Security | fail-closed on a `.env` typo | pass |
| 29 | `guard_raises_before_any_socket_is_opened` | Security | FM16 ordering | pass |
| 30–32 | `a_loopback_provider_cannot_redirect_corpus_text_elsewhere` (301/302/307) | Security | the one shape a string check cannot see | pass |
| 33 | `lock_checkpoint_and_sidecar_carry_no_corpus_text` | Security | new `.cache/` artefacts | pass |
| **`tests/test_qa_tier1_reembed_robustness.py`** — Edge |
| 34 | `reembed_is_idempotent_and_keeps_a_restorable_backup` | Happy | rollback layer 2 | pass |
| 35 | `reembed_defragments_the_index` | Edge | pins an unstated operator benefit | pass |
| 36 | `sigkill_midrun_leaves_the_live_table_byte_identical_and_resume_completes` | Edge | FM13 under SIGKILL (no handler) | pass |
| 37 | `a_dead_holders_lock_file_never_blocks_the_next_run` | Edge | ASK-2 recovery | pass |
| 38 | `corrupt_checkpoint_is_discarded_rather_than_wedging_resume` | Edge | malformed input | pass |
| 39 | `checkpoint_claiming_more_rows_than_the_shadow_has_is_discarded` | Regression | BLOCK-2 scenario 4, end-to-end | pass |
| 40 | `corrupt_provenance_sidecar_degrades_instead_of_serving` | Edge | truncated sidecar → not served | pass |
| 41 | `two_processes_racing_the_same_root_produce_exactly_one_winner` | Concurrency | REVIEW4 QA item 4, real processes | pass |
| 42 | `readonly_ensure_ready_still_sees_a_missing_provenance_sidecar` | Regression | REVIEW4 QA item 6 — ordering pin | pass |
| 43 | `readonly_ensure_ready_writes_nothing_at_all` | Security | BLOCK-3 as an inventory diff | pass |
| 44 | `readonly_ensure_ready_on_a_world_with_no_index_creates_no_directory` | Security | BLOCK-3 | pass |
| 45 | `two_reembeds_completing_in_the_same_second_do_not_crash` | Edge | QA-1 | **xfail (strict)** |
| 46 | `reembed_on_an_unwritable_cache_reports_actionably` | Edge | QA-2 (disk-full analogue) | **xfail (strict)** |
| 47 | `empty_corpus_does_not_leave_a_sidecar_without_a_table` | Edge | QA-3 | **xfail (strict)** |
| **`tests/test_qa_tier1_clean_machine.py`** — Setup (30%) |
| 48 | `first_ingest_without_a_provider_raises_with_the_pull_command` | Setup | FM15 message quality | pass |
| 49 | `first_ingest_without_a_provider_writes_nothing` | Setup | FM15 | pass |
| 50 | `provider_outage_leaves_an_existing_index_byte_identical` | Setup | FM10 — `replace()` is an overwrite drop | pass |
| 51 | `a_provider_outage_never_substitutes_hash_vectors` | Setup | the no-silent-fallback rule as behaviour | pass |
| 52 | `search_on_a_labless_machine_degrades_rather_than_raising` | Setup | /knowledge stays usable mid-pull | pass |
| 53 | `pkb_ingest_does_not_trigger_a_synchronous_embed_storm` | Regression | FM11, 25 searches → 0 embeds | pass |
| **`tests/setup_ladder/test_setup_ladder_qa_nomic.py`** — Setup (30%) |
| 54 | `nomic_pull_failure_never_fails_setup` | Setup | C5 warn-and-continue | pass |
| 55 | `nomic_pull_failure_prints_the_exact_recovery_command` | Setup | actionability | pass |
| 56 | `nomic_pull_failure_does_not_stop_the_default_model` | Setup | ladder independence | pass |
| 57 | `default_model_failure_does_not_stop_the_embedding_pull` | Setup | ladder independence | pass |
| 58 | `skip_model_download_also_skips_the_embedding_pull` | Setup | the documented escape hatch | pass |
| 59 | `skip_ollama_also_skips_the_embedding_pull` | Setup | escape hatch | pass |
| 60 | `embedding_pull_is_tier_independent` | Setup | PKB search is minimalist | pass |
| 61 | `embedding_pull_runs_at_most_once` | Setup | clean-machine wait | pass |
| **`tests/test_qa_tier1_search_header_safety.py`** — Security (20%) |
| 62–66 | `a_hostile_provider_message_cannot_split_the_response` ×5 | Security | QA-4 + QA-5 root cause | pass (round 2) |
| 67–70 | `the_real_degraded_messages_can_actually_be_served` ×4 (rewritten to trigger the real code paths) | Regression | **QA-5** | pass (round 2) |
| 71 | `the_real_clean_machine_provider_message_can_actually_be_served` | Setup | the multi-line `provider` reason, real urllib path (**added round 2**) | pass |
| 72–76 | `other_control_characters_are_also_removed` ×5 | Security | **QA-7** (**added round 2**) | **xfail (strict)** |
| 77 | `reason_header_is_length_bounded` | Security | DoS/proxy bound | pass |
| 78 | `healthy_search_keeps_the_bare_list_contract` | Regression | three templates do `r.json().forEach` | pass |

Allocation actually spent: 30% setup (17 tests) · 30% Buddy (5 tests + the real-World
measurement below) · 20% security (26 tests) · 10% happy · 10% regression. Matches
the repo's stated arail weighting.

---

## Failures

| # | Test | Symptom | Minimal repro | Severity |
|---|---|---|---|---|
| **QA-5** | `test_the_real_degraded_messages_can_actually_be_served` ×4, `…cannot_split_the_response[non-latin1]` | `GET /api/pkb/search` → 500 whenever retrieval is degraded | see above; any reason string containing `—` | **HIGH — blocks ship** |
| QA-6 | `test_agent_retrieval_returns_nothing_and_reports_healthy_on_a_legacy_world` (passes, pins the hazard) | Buddy's path reports `retrieval_status() == (True, "")` while returning zero rows | gate on + 0 approved paths → `pkb.search` returns `[]` before `_semantic_search` runs | **MEDIUM** |
| QA-1 | `test_two_reembeds_completing_in_the_same_second_do_not_crash` | bare `OSError(ENOTEMPTY)` escapes `main()`'s handlers → traceback | occupy `pkb_pages.lance.bak-<int(time.time())>`, re-run | LOW (no data loss; live table intact) |
| QA-2 | `test_reembed_on_an_unwritable_cache_reports_actionably` | `PermissionError` escapes `main()` → traceback instead of an exit code | `chmod 500 <pkb>/.cache` then re-run | LOW (disk-full/read-only class) |
| QA-3 | `test_empty_corpus_does_not_leave_a_sidecar_without_a_table` | provenance sidecar written for a table that was never created | `reembed` an empty PKB root | LOW |
| QA-4 | `…cannot_split_the_response[crlf/lf/cr/nul]` | CR/LF/NUL from a provider error body reach the header constructor | `retrieval_status()` returning `"boom\r\nX-Injected: yes"` | LOW (both uvicorn transports reject the write; outcome is a 500, not a split) |

QA-1/QA-2/QA-3 are `xfail(strict=True)`: when fixed they XPASS, which pytest
turns into a failure, forcing the marker off. QA-4/QA-5 are left **red on
purpose** — they are the defect report and the acceptance criterion.

---

## What I could not break

Recorded because the reviewer's carried list deserves closure, not silence.

**`doctor` is genuinely read-only, on the operator's real data.** Six real PKB
roots, `python -m arail.doctor` per root. Exit codes `3 / 3 / 3 / 3 / 0` for
`ai / debt-finance / qukaizen / video-games / finance`, and `3` for the root lab —
exactly as REVIEW3 recorded. Every message names `./arailctl pkb reembed`, a
command the operator can actually run. The 17,015-entry inventory diff after all
six runs is empty: **zero writes, zero new files, zero embeds beyond the 5-byte
reachability probe.** This is the check that produced a BLOCK two reviews ago;
it is clean now.

**`ensure_ready` ordering is already pinned.** REVIEW4 predicted the suite would
stay green if `if not build: return` were moved above the provenance check. It
does not: in a scratch worktree with exactly that mutation,
`test_doctor_embedding_status.py::test_provenance_mismatch_degrades_required`
fails. Test 42 adds the absent-sidecar variant (a partial restore / hand-cleaned
`.cache`), which the existing test does not cover.

**ASK-1 and ASK-2 fixes bite.** Reverting ASK-1 (`_pkb_root_cache` outside the
`if build:` guard) in a scratch worktree fails 3 tests including
`test_cross_world_contamination_probe` — and LanceDB's own log confirms World B
got a dataset written into it. Removing the `flock` call fails 5 tests. Neither
fix is decorative.

**The lock holds under real contention.** Two OS processes, same root, six
background CPU spinners, five trials: exactly one winner and one clean
`ReembedLocked` every time, live rows 16/16 (or 60/60) every time.

**SIGKILL mid-shadow-build is safe.** 40-row world, killed at 24/40: live table
**byte-identical** (sha256 over every file in `pkb_pages.lance`), checkpoint at
24, stale `reembed.lock` on disk but flock released by the kernel, `--resume`
completed to 40/40 and cleaned up `.next` and the checkpoint.

**The airgapped guard is a real allowlist.** 10 adversarial host forms refused
(`127.0.0.1.evil.example`, `localhost.evil.example`, `user@localhost@evil…`,
`localhost.`, `0.0.0.0`, `127.0.0.2`, decimal-encoded `2130706433`,
`[::ffff:127.0.0.1]`, plain external, https external); `OLLAMA_HOST` in both its
bare-host and full-URL shapes is guarded identically; anything that is not
literally `hybrid` fails closed. Redirects (301/302/307) from an allowed loopback
host do **not** deliver the request body elsewhere — urllib converts 302/301 to
GET (body dropped) and refuses 307.

---

## Buddy: what the operator actually gets (REVIEW4 QA item 1, answered)

Measured on a scratch copy of the real `video-games` World (318 files, 356 rows),
before and after a real `pkb reembed`, through `lab_brain.retrieve_chat_context` —
Buddy's own context builder.

**The nomic upgrade is real and large when retrieval runs.** Six natural-language
questions, top-3 hits:

| Question | Legacy 128-dim | After reembed |
|---|---|---|
| "should I turn on upscaling…" | `.wiki-cache/manifest.json`, `arail-portal-app.md`, `maximus.plan.md` | `terms/upscaling.md`, `terms/fsr.md`, `terms/xess.md` |
| "my new graphics driver made things worse" | `.wiki-cache/manifest.json`, `SKILL.md`, `world-video-games.md` | `terms/gpu-driver.md`, `terms/game-ready-driver.md`, `terms/driver-rollback.md` |
| "what makes a racing wheel feel realistic" | `.wiki-cache/manifest.json`, `SKILL.md`, `world-video-games.md` | `terms/sim-racing.md`, `terms/wheel-base.md`, `terms/force-feedback.md` |

Relevant top-1 went from 0/6 to 6/6. The `+40.6pp` headline is not overstated.
Retrieval latency was unchanged (349 ms → 332 ms mean per chat message, both
dominated by the multi-term fan-out in `retrieve_chat_context`).

**But on the operator's actual configuration, Buddy gets nothing either way, and
is told everything is fine.** The Compiled-KB gate ships **on** by default, and
`approved_paths()` is **empty on all six real PKB roots** (`ai`, `debt-finance`,
`qukaizen`, `video-games`, `finance`, root). `pkb.search(approved_only=True)`
returns `[]` *before* `_semantic_search` is reached, so:

* Buddy receives **0 hits** on a legacy World **and** on a re-embedded one;
* **no degraded code is set at all** on that path — `retrieval_status()` returns
  `(True, "")`.

REVIEW4's required plain-words statement, corrected by measurement:

> **On four of the operator's five Worlds plus the root lab, Buddy is not
> keyword-only — Buddy is zero-retrieval, because the Compiled-KB gate has
> nothing approved. And the C1 degraded status is not merely un-surfaced on that
> path; it is never set. A Buddy context header wired exactly as ARCHITECTURE.md
> C1 specifies would print "retrieval healthy" while Buddy got nothing.**

The gate and the early return both predate this sprint, so QA-6 is not a
regression — but it is decisive for the ship question, because it means the
deferred Buddy surface would ship a *false reassurance*, not a missing one. Test
5 shows the fix is cheap: with the gate off the agent path does reach
`_semantic_search` and does set `dimension`. Moving the gate's empty-set
short-circuit to *after* the health check (or having `search_for_agents` consult
`pkb_index.ensure_ready(build=False)`) closes it.

Two smaller quality notes from the same measurement, both pre-existing:
`.wiki-cache/manifest.json` is indexed and was the #1 hit for every legacy query
(`_PKB_TEXT_SUFFIXES` includes `.json`); and the "keyword fallback" is a
whole-query literal substring sweep (`re.escape(query)`), so a natural-language
question matches nothing at all — "degrades to keyword search" overstates what
the fallback does (test 3).

---

## Security review

| Surface | What I actually checked | Findings |
|---|---|---|
| Network I/O — egress | `_assert_local` against 10 adversarial host forms, both `OLLAMA_HOST` shapes, 7 `LAB_MODE` values, and 301/302/307 redirects from an allowed loopback host. Confirmed the raise happens before `urlopen` (monkeypatched to explode). | Clean. Strict allowlist `{127.0.0.1, ::1, localhost}`; fails closed on any typo. Residual, noted not filed: `localhost` is resolved by the OS, so a hostile `/etc/hosts` would defeat it — a host-compromise scenario, not an app defect. |
| Untrusted input → response headers | Traced provider-controlled bytes: `_post` puts ≤400 bytes of the provider's HTTP error body into `EmbeddingError` → `set_degraded("provider", str(e))` → `X-Retrieval-Reason`. Tested CR/LF/NUL and non-latin-1. Verified independently that `h11.Connection.send` raises `LocalProtocolError` and `uvicorn…httptools_impl.HEADER_VALUE_RE` rejects — so no response splitting on the wire. | **QA-4** (low) and **QA-5** (high) — same call site, one sanitisation fixes both. |
| File I/O — diagnostic writes | 17,015-entry mtime+size inventory of the real lab before/after six `doctor` runs; per-test inventory assertion in test 43. | Clean. `doctor` writes nothing. |
| File I/O — locking | `flock(LOCK_EX\|LOCK_NB)` on an fd that is never unlinked; SIGKILL release; 5 contended trials across real processes; a dead-PID lock file left in place. | Clean. The kernel is the sole arbiter; `release()` no longer unlinks, which closes REVIEW4 ASK-2's third-process hazard. |
| File I/O — permissions | `chmod 500` on `.cache` during a re-embed. | **QA-2** (low): raw `PermissionError`, no data loss. |
| Deserialization | `pkb_provenance.read` and `_load_checkpoint` both `json.loads` inside `try/except → None`; no `pickle`, no `yaml.load`, no `eval` anywhere on this path. Fed both a truncated file (tests 38, 40). | Clean. |
| Secrets | Grepped the new artefacts for corpus text (test 33): `reembed.lock` holds a PID only; the sidecar holds model/dim/sha/rows/timestamp; the checkpoint holds pkb-relative paths. No `lab/data/secrets.env` read on any path touched here. | Clean. |
| Injection into LanceDB predicates | `pkb_index._flush` escapes `'` → `''` for `delete(f"path = '{escaped}'")`. Paths are derived from `relative_to(root)`, never from a request. No second escaping site was added by this sprint. | Clean, unchanged. |
| Dependencies | No new third-party dependency. New runtime requirement is the `nomic-embed-text` Ollama model (274 MB), pulled warn-and-continue. | Clean. |

---

## Performance

No BENCHMARK.md; the change is not on a latency-sensitive hot path and the
architect's own threshold was informational. Measured anyway, on real data:

| Measurement | Value |
|---|---|
| `VectorIndex._table()` open, 356-row / 12-fragment index | **0.39 ms** median, 0.48 max |
| `_table()` open, real `ai` index (381 rows, **2421 fragments**, 2472 versions) | **1.21 ms** median, **531 ms** cold first open |
| `check_read_path_health` | 0.49 ms |
| `search_vector` k=12 | 2.49 ms |
| `pkb.search()` end to end | 20.9 ms median (dominated by the `embed_query` round trip) |
| `pkb reembed`, 356 rows, real Ollama | 5.5 s wall, 37–164 rows/s |
| Buddy `retrieve_chat_context` per message | 332 ms (nomic) vs 349 ms (legacy keyword) |

REVIEW3's 7.5 ms `_table()` does not reproduce at 0.39–1.21 ms; the triple-open
is ~3.6 ms warm even on the operator's most fragmented World, so the BACKLOG
deferral of the memoisation is comfortably justified. The 531 ms **cold** open on
`ai` is the number worth knowing, and `pkb reembed` incidentally fixes it —
the shadow build collapses 2421 fragments to 1 (pinned by test 35).

---

## Regression

Full suite (`pytest tests -q -p no:randomly`, 4400+ tests) at **both** revisions,
independently, in separate worktrees:

| Revision | Result |
|---|---|
| `8cb5760` (baseline) | **52 failed, 4113 passed, 18 skipped, 3 xfailed, 7 errors** (13:35) |
| `211804d` (HEAD) | **52 failed, 4364 passed, 18 skipped, 3 xfailed, 7 errors** (08:25) |

Identical counts. Diffing the 59 failure IDs gives one swap:
`test_dashboard_layout_v2::test_dashboard_renders_with_no_current_goal` (base) ↔
`test_onboarding::test_dashboard_unblocks_after_onboarding` (HEAD). Run in
isolation, **both revisions produce byte-identical results** for those two files
(1 failed / 24 passed, the same third test). That is the pre-existing
order-dependence REVIEW3 and REVIEW4 both warned about, not a regression.

**No new failure, no deleted test, no new skip.** The 251 extra passes at HEAD
are this sprint's own new tests.

---

## Coverage delta

Line coverage was not instrumented — `coverage` is not installed in the venv and
adding it was out of scope for this pass. Test-count delta on the surface:

* sprint tests over the five touched modules: 322 (builder's count) / 379 (REVIEW4's wider set)
* added by QA: **72** (60 pass, 7 xfail, 5 red-by-design)
* newly-covered behaviours with no prior test: the agent-path status gap, the
  real degraded-message strings reaching a header, redirect egress, the
  `OLLAMA_HOST` bypass shape, `LAB_MODE` typo fail-closed, SIGKILL (as opposed to
  SIGINT) recovery, cross-process lock contention, backup-name collision,
  unwritable `.cache`, empty-corpus sidecar, `ensure_ready` ordering with an
  *absent* sidecar, the four `setup.sh` skip/failure branches for the nomic pull.

---

## Required before ship

1. ~~**Fix QA-5.**~~ **Done** (`cc503d1`), re-verified in round 2 §1.
2. ~~**Decide QA-6 explicitly.**~~ **Done** (`77d569b`) — recorded in
   `sprints/BACKLOG.md` with the binding constraint on the deferred header;
   round 2 §5 confirms the entry captures the hazard.
3. **File QA-1/QA-2/QA-3/QA-7** in `sprints/BACKLOG.md` with their reproductions,
   or fix them (each is under ten lines). All four are `xfail(strict=True)`, so a
   fix removes its own marker. **This is the only outstanding item, and it is why
   this is WEAK_PASS rather than PASS.**

---

## Before the operator runs `pkb reembed` on a real World

This is the next thing that will actually happen to their data, so it is worth
stating plainly. All numbers measured on scratch copies of the real Worlds.

**It is fast, and it is safe to run with the lab up.** The largest World (`ai`,
419 rows) re-embeds in **4.3 s**; `video-games` (356 rows) in **5.6 s**.
`--dry-run` gives an honest ETA first (predicted 5 s, actual 5.6 s) and writes
nothing. I ran a search loop against a World while it was being swapped
underneath: 94 searches, **zero exceptions** — the in-flight ones caught the
missing-file error, degraded, and fell through to keyword results. No need to
stop the World; expect a moment of keyword-only results.

**It roughly doubles the index directory until they clean up.** The old table is
kept as `pkb_pages.lance.bak-<timestamp>` and is **never auto-pruned**. On `ai`
that is a 79 MB backup next to a 2 MB new table — the re-embed *defragments*
2421 fragments down to one, so deleting the `.bak-` dir once they are satisfied
reclaims **77 MB**. Until then, disk goes up, not down. That backup is also the
documented rollback: stop the lab, move it back over `pkb_pages.lance`, delete
`pkb_pages.provenance.json`, restart.

**Run it from the primary checkout, not a worktree.** `collect_pending_rows`
includes the docs-registry slice, which is global and resolved from whichever
checkout the process runs in — 38 of `video-games`' 356 rows are docs pages. Run
from a worktree and that worktree's `docs/` lands in the operator's World index.

**Nothing user-authored is at risk in either direction.** `pkb_pages` is a
derived index over files that stay on disk; the worst case is rebuilding it.
That is why this change is rollback-safe.

**Do all five Worlds, not one.** Until a World is re-embedded it stays
`dimension`-degraded, which now means keyword-only retrieval rather than a 500 —
but on natural-language questions the keyword fallback is a whole-query literal
substring sweep and returns nothing at all. `./arailctl pkb reembed --all` covers
the root lab plus every registered World.

**And it will not fix Buddy on its own.** Per QA-6, the Compiled-KB gate has
nothing approved on any of the six real roots, so Buddy keeps getting zero hits
until knowledge is approved into the Compiled KB — re-embedding improves
`/knowledge` search immediately, but the agent path only after approval.

Not gating, carried forward: the `_initialized` test-seam sweep (29 dead
assignments), REVIEW2 ASK-4's LAN-Ollama documentation (now four reviews old),
`.wiki-cache/manifest.json` being indexed and out-ranking real content on the
keyword path, and the `spec_sha256` field that the sidecar writes but
`agrees_with_spec` never checks (a prefix change in `models.hcl` would not be
detected as a provenance disagreement).

---

## Notes for the next QA pass

* **The pattern behind QA-5 is worth hunting elsewhere:** a test that asserts
  against a *hand-written* stand-in for a product string will miss anything about
  the real string — encoding, length, control characters. Anywhere the codebase
  moves an internal message into a protocol field (headers, SSE event names,
  filenames, shell arguments), parametrise over the real constants.
* Every message this sprint added uses an em dash. If sanitisation is added at
  one call site only, the next surface that reflects a reason will regress the
  same way. Prefer fixing it at the message source or adding a shared helper.
* `_degraded_codes` is still a process-global; the one-root-per-process invariant
  is now documented in the module header, which is the right place. The next time
  anyone proposes an in-process multi-World server loop, that header is the
  tripwire — re-read the BACKLOG entry before touching it.
* Under-tested and untouched by this sprint: `wiki_vectors`, `agent_workflows`
  and `experiments` still carry 128-dim hash vectors in the same lab. The two
  vector spaces are recorded but not exercised together anywhere.
* The Compiled-KB gate is the single highest-leverage untested surface in this
  repo. It silently converts the whole retrieval stack into a no-op on a fresh
  lab, and nothing in the UI says so.
