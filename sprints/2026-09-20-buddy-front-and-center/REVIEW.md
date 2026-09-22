# Review: P1 — observability + guardrails over the agents that already exist

**Date:** 2026-09-21
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `d0d3437d` (slices `ffcb1ba3`…`9247f32e`, defect loops `fc9311a6`, `1bedf165`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `600c9541`
**Vision:** [VISION.md](./VISION.md) at `2884569f`
**Diff reviewed:** `77871c70..HEAD`, 57 files, +7340/−195
**Mode:** review

Everything below was verified by reading the code in this worktree and, where
noted, by running it. Where the BUILD_LOG's claim and the code disagree, the
code wins. I ran the sprint's 20 test files (**256 passed**) and the
metrics/scheduler/costs/activity regression set (**40 passed**) myself rather
than accepting the log.

---

## Verdict: BLOCK

Four blocking findings. Two are security defects I reproduced (redaction
degrades **open**, and the legacy-bodies purge leaves the bodies being served
over an unauthenticated every-tier endpoint). Two are binding operator
decisions from `SPRINT.md` that are **not implemented and not declared as
deviations** in `BUILD_LOG.md` (the existing halt control was never relabelled;
the legacy-bodies notice + Purge button has no UI at all). Plus W1 — the
sprint's first win condition — cannot pass as built, because four of its seven
mandatory fields are in the JSON but not on screen.

The spine is good. The chokepoint instrumentation is genuinely cheap
(**measured p95 0.048 ms**, 20× under the design budget, 100× under DE4's kill
line), the TTFT honesty contract is implemented exactly as specified, the
attribution mechanism works across all four hops, the tier gate on all eight
new admin endpoints is real and server-side, and the hermeticity work the
orchestrator forced is correct. This is a BLOCK on six concrete, mostly
one-to-twenty-line fixes — not a redesign.

---

## Failure-mode cross-reference (F1–F20)

| # | Status | Code | Test | Notes |
|---|---|---|---|---|
| **F1** trace disk write fails | **mitigated as designed (code); detection half incomplete (UI)** | `agent_trace.py:141-147,159-171` | `test_agent_trace.py:85,102` (real `chmod` read-only dir) | `record()` has no raise path; `dropped_writes` reaches `lanes_snapshot().drops`. But `admin.html` never renders `drops` — F1's "surfaced … on the Admin card" is absent. See D3. |
| **F2** rotation `os.replace` fails | **mitigated differently** | `agent_trace.py:179-184` | `test_agent_trace.py:118` | Rotation failure is swallowed by a bare `except OSError: pass` and does **not** increment `dropped_writes`, contrary to F2's "same counter". The write then proceeds to the un-rotated file, so the 10 MB ceiling can be exceeded silently. Low impact, wrong instrument. |
| **F3** attribution lost across a thread hop | **partially mitigated** | `agent_context.py:236-247` (`spawn_thread`) | `test_agent_context_propagation.py`, `test_agent_context.py:184` | Detection half is real (unattributed lane + `call_site`). **The designed static guard does not exist**, and `spawn_thread` is never used in production — `_builtin_presence.py:127` still constructs a bare `threading.Thread`. See D5. |
| **F4** new agent author forgets the context | **mitigated as designed** | `loader.py:361-371` (L1) | `test_agent_attribution_wiring.py:31,55` | L1 wraps `start()` only; the architecture also named `tick()`/`dream()`/`ask()`. `dream()` is covered by L2 (`dream_daemon.py:96-101`); there is no `tick()`/`ask()` caller outside `start()`'s own task in `src/` (verified by grep), so the gap is theoretical today. |
| **F5** two agents share a cached router, attribution crosses | **mitigated as designed** | contextvar is call-scoped | `test_router_trace_chokepoint.py` (F5) | Correct by construction; test drives two concurrent tasks through one router. |
| **F6** TTFT faked from latency | **mitigated as designed** | `core.py:287-346` | `test_router_ttft.py` (13 tests, one per truth-table row + the never-equals-`latency_ms` invariant) | `ttft_ms` is only ever a `perf_counter()` delta. All five truth-table rows implemented. The one undesigned case the builder added (error *after* a real TTFT keeps the measurement) is the right call. |
| **F7** a halted agent still calls a model | **mitigated as designed for attributed calls; NOT for unattributed ones** | `core.py:232-238,304-310`; `agent_context.py:363-374` | `test_halt_gate.py:119,133,180` | See D5/F16: `halt_gate` deliberately admits `ctx is None`, so any call that lost its context escapes the hold. Correct per contract §4, false per F16's claim. |
| **F8** `AgentHeldError` crashes an agent loop | **mitigated as designed** | `deep_policy.py:254-258,271-280,284-285`; `_builtin_drafter.py:170-183` | `test_halt_survival.py` (8 tests, one per real call site) | Single-refusal guarantee (deep branch returns `None`, never also tries fast) implemented and tested. A7's `forge._voice:321` citation was my error — the builder is right that it is inside a template literal. |
| **F9** a halted agent still speaks | **mitigated at 5 sites; the UI claim overshoots the code** | `_builtin_buddy.py:1380-1386`, `_builtin_librarian.py:268-276`, `_builtin_presence.py:80-90`, `_builtin_debt_advisor.py:799-809`, `_builtin_consolidation_analyzer.py:870-893` | `test_halt_gate.py:70-92` tests `speech_gate()` **in isolation** — no test asserts any of the five wired sites actually goes silent | Two of Buddy's four emit sites are ungated (`_builtin_buddy.py:1263` boot notice; `:1473` dream announcement, which carries a 160-char model-output preview). 5 of 84 emit sites in `src/arail/agents/` are gated. See D4/D9. |
| **F10** recorder toggled mid-request | **mitigated as designed** | `core.py:231,283` (latched at call start) | `test_flight_recorder.py:119,136,237` (both directions + next-call) | Exactly the designed asymmetry. Good. |
| **F11** a secret reaches disk in a captured body | **NOT mitigated — `capture_body` can return unredacted text** | `redact.py:120-126` | `test_redact.py:141` tests the *outer* wrapper only | **Reproduced.** See B1. |
| **F12** legacy bodies already on disk | **mechanism built and tested; the disclosure half is absent, and the purge is incomplete** | `activity.py:232-272`; `app.py:6235-6258` | `test_legacy_bodies_purge.py` (12), `test_legacy_bodies_admin_endpoints.py` (9) | No boot scan, no Admin notice, no Purge button anywhere in any template — `scan_for_legacy_bodies` has exactly one caller, the endpoint itself. And the purge leaves the in-memory copy served. See B2 and B3. |
| **F13** new GET endpoints widen the unauthenticated surface | **mitigated as designed** (gate), **test gap** | all eight new `/api/admin/*` handlers call `_require_surface("admin")` first, before any body parse — verified by an independent route audit of `app.py` | `test_admin_agent_lanes_endpoints.py:44`, `test_legacy_bodies_admin_endpoints.py` | `/api/admin/agent-trace-stream` — the endpoint that serialises **whole records including bodies** — is missing from the parameterised `_ENDPOINTS` list (`tests/…:37`). Its gate is correct in code but untested. |
| **F14** two World instances leak into each other | **mitigated as designed** | `agent_trace._trace_path`/`_recorder_path`, `redact._secrets_path`, `scheduler._halt_path`, `activity._legacy_notice_path` all resolve `DATA_DIR` lazily; no `glob`/`walk`/`iterdir` in `agent_trace.py` | `test_agent_trace.py:139,159`; `test_halt_gate.py`/`test_admin_agent_lanes_endpoints.py` (F14) | Verified independently: no `importlib.reload` anywhere in `src/`, and `config.DATA_DIR` is assigned once at `config.py:85` and never rebound in `src/`. One caveat: `_recorder_enabled` is a process-global cache, so it is per-World only because `DATA_DIR` is per-process — the same invariant `pkb_index.py` already documents. |
| **F15** portal restart mid-hold silently un-holds | **mitigated as designed** | `scheduler.py:233-280` (+ `halt_changed_at()`) | `test_halt_persistence.py` (+2 new) | Correct. |
| **F16** a user-defined loader agent ignores the contract | **NOT mitigated as designed — the halt half is false** | `agent_context.py:368` | none (no test drives a user-defined agent at all) | F16 claimed "It cannot ignore halt — the chokepoint refuses." It can: a bare `threading.Thread` inside `start()` drops the context (A3, proven by the sprint's own S0 test), and `halt_gate` admits `ctx is None`. Neither `docs/agents.md`'s new section nor the UI copy says so. This is a contradiction **in my own design document** between §4 and F16; the builder implemented §4 correctly. See D5. |
| **F17** the switch's UI copy drifts from what the code does | **behaviour tested; copy NOT tested; and the copy overclaims** | `admin.html:1659-1678` | `test_admin_agent_lanes_endpoints.py:186-231` | The test asserts phrases against a **hardcoded duplicate of the copy declared in the test file itself** — `admin.html` can drift freely without a red test. F17's whole point was that it cannot. Separately, "stop speaking" is not what the code does (F9) and "agents stop calling models" is not true for unattributed/user-defined agents (F16). See B4 and D4. |
| **F18** trace volume evicts activity history | **mitigated as designed (weakly)** | separate stores | `test_agent_trace.py:287` | `agent_trace.py` imports nothing from `activity.py`, so the test asserts a structural impossibility. Passing, low information. |
| **F19** the chat tab regresses | **mitigated as designed** | `core.py:150-155` (`billing_source == "ui"` short-circuits before any context lookup) | `test_router_trace_chokepoint.py` (F19) + 97 chat-adjacent tests green | Correct. |
| **F20** `ARAIL_AGENT_STREAM_FAST` changes Buddy's output or breaks | **mitigated as designed, with one untested claim and one undocumented behaviour change** | `deep_policy.py:200-225,271-280` | `test_deep_policy_stream_fast.py` (11) | Env default, falsy spellings, unconditional fallback, no-`stream_complete` router: all real. But `test_joined_stream_text_equals_complete_text` uses a fake whose two methods return the same hardcoded literal — it is tautological. See D1 and D2. |

**Named but unaddressed in-scope item, not declared as a deviation:** the
architecture's Security test list and its own "In scope" sentence required
*"`BIND_ADDR` non-loopback **and** recorder on → a warn-level activity line and
an Admin banner naming the combination."* There is no `BIND_ADDR` code and no
test in this diff. See B5.

---

## Spec adherence

**What landed as specified.** The wedge is intact: one context module, one
bounded trace store, the chokepoint recording exactly one record per call, L1
loader + L2 daemons + eleven L3 sites, the subprocess round-trip, the TTFT
status enum, `halt_gate`/`speech_gate`, `redact.py`, the V7 `tokens_out` fix
with its label corrected, `slot_pressure()` as a three-global read with
`snapshot()`/`per_label_snapshot()`/`/metrics` untouched, the `calls_by_source`
legacy-key migration, docs, and five BACKLOG entries.

**Scope discipline: clean.** None of the four cuts leaked into code. No
gateway, no per-agent budgets, no typed-action scaffold, no
scrub/conveyor/gauges (the card is a plain table). `/metrics` and
`per_label_snapshot()` are byte-unchanged and `tests/test_health_metrics.py`
passes. Nothing in the frozen aerollm-named surface was renamed — the only
`aerollm`/`AERO_` hits in the diff are pre-existing test names, docs prose, and
an existing `deep_policy._aerollm_importable` monkeypatch.

**Where it drifted.**

1. **Two binding operator decisions in `SPRINT.md` are unimplemented and
   undeclared** — the relabel of the existing halt control (B3) and the
   legacy-bodies Admin notice + Purge button (B2). BUILD_LOG.md declares
   nineteen deviations across seven slices with reasons; these two are not
   among them. That is the failure mode the deviation log exists to prevent.
2. **W1's seven fields are not all on screen** (B6).
3. **The generic `user-defined` lane** is computed (`agent_trace.py:411`) but
   never rendered (D3). The architecture put it in `lanes[]`; the builder put
   it in a sibling key and then nobody displayed it.
4. **`/api/admin/agent-trace/{trace_id}`** — "the 'why?' drill-in" — has no UI
   caller. Curl-reachable only. Debt.
5. The architecture required that QA's security+regression allocation tilt be
   "recorded in `SPRINT.md`, not silently applied." `SPRINT.md` has no such
   line. Orchestrator action, not builder.

**Builder's six "architect feedback required" items — my answers.**

1. `SYS_LANES`'s inferred fourth label (`recap`) — **confirmed correct.** The
   four non-agent callers are `world-forge`(+`term-draft`/`world-review`/
   `world-grow` from the same module), `dictionary`, `goal-parser`, `recap`.
   Note `SYS_LANES` is currently a constant nothing reads — no sys lane is
   rendered or emitted by `lanes_snapshot()`. Dead constant; either render sys
   lanes or drop it.
2. `librarian`'s empty-reason — **builder is right, doc was wrong.**
   `librarian_scout.draft_proposal` does reach `router.complete()`; the generic
   empty-reason is the honest label. Same for adding `curator`/`forge` to
   `_MODEL_FREE_LANES`: I re-read `forge.py` and confirm `_voice()` lives
   inside `_AGENT_PY_TEMPLATE`.
3. A7's `forge._voice:321` citation — **my error, builder is right.** A
   grep matched inside a template literal. A7's conclusion is unaffected.
4. Speech-gate site selection — **accept as reasonable**, but see D4: the
   selection is not what the UI copy claims, and no test pins any of the five
   sites.
5. The 17 ungated `/api/admin/*` endpoints — **confirmed real.** My independent
   audit of every `@app.<verb>("/api/admin/…")` route in `app.py` finds 16
   ungated pre-existing handlers (including `POST /api/admin/cleanup/prune`,
   `POST /api/admin/models/load`, `POST /api/admin/models/unload`,
   `POST /api/admin/scheduler/jobs/{job_id}/run`) and all 9 gated ones are this
   sprint's 8 plus `POST /api/admin/security/run-scan`. Correctly filed, correctly
   not fixed here. My contract #6's citation of `/api/admin/security` as
   precedent was wrong.
6. The deep branch left on `.complete()` — **builder's reading is correct.**
   Finding #2 was prose observation, not an S3 action item. `non_streaming` is
   exactly as honest as `emulated_stream`. No change.

---

## Security findings

### B1 — [BLOCK] `capture_body()` returns unredacted secrets when the known-value pass raises: redaction degrades **open**

`src/arail/redact.py:120-126`

```python
    try:
        for value in _known_values():
            ...
    except Exception:  # noqa: BLE001
        pass
```

The architecture's contract is structural, not advisory: *"`capture_body` … cannot
return unredacted text: it either returns a dict whose strings have passed
**both** redaction passes, or `None`."* As built, a failure anywhere in
`_known_values()` silently skips the entire known-value pass and the body is
still returned — with `redactions: 0`, so nothing downstream can tell.

`_parse_secrets_env` catches only `OSError` (`redact.py:81`).
`Path.read_text()` on a `secrets.env` that is not valid UTF-8 raises
`UnicodeDecodeError`, a `ValueError` — it escapes, and `redact()` swallows it.

**Reproduced** (`ARAIL_SECRETS_FILE`/`DATA_DIR` pointed at a temp root, one
non-UTF-8 byte inside the key value):

```
secrets.env valid   → 'here is ***REDACTED*** in a prompt', redactions=1
secrets.env non-UTF8→ {'prompt': 'here is supersecretvalue123 in a prompt',
                       'response': 'resp', 'truncated': False, 'redactions': 0}
_known_values() raised: UnicodeDecodeError
```

**Failure scenario.** Friend's lab, `maximus`, flight recorder on (they flipped
it once to debug Buddy). Their `secrets.env` picked up one non-UTF-8 byte — a
key pasted from a terminal with a stray `\xff`, or a file written by a
non-Python tool. Every provider key in that file is now written verbatim into
`lab/data/agent_traces.jsonl` and served by `/api/agents/prompts` and three
admin endpoints. W4's "secrets.env material is redacted" is false, silently.

**What would satisfy me.** `capture_body` must fail closed on a *pass* failure,
not just on its own exception. Either (a) `redact()` re-raises so
`capture_body`'s outer `except` returns `None`, or (b) `redact()` returns a
third value (`known_pass_ok: bool`) and `capture_body` returns `None` when it is
False. Plus `_parse_secrets_env` should catch `(OSError, ValueError,
UnicodeDecodeError)` and — separately — read with `errors="replace"` so a
mangled file still yields its other values. Plus a test that patches
`_known_values` to raise and asserts `capture_body(...) is None`. The existing
`test_redact.py:141` patches `redact.redact` itself, which only exercises the
outer wrapper.

### B2 — [BLOCK] `purge_legacy_bodies()` leaves the bodies being served over an unauthenticated every-tier endpoint

`src/arail/activity.py:232-272` · `src/arail/portal/app.py:3088`

The purge streams both files, strips the bodies, and `os.replace`s — correctly
and atomically. It never touches `activity_log._buffer`, the 200-event
in-memory ring that `GET /api/activity/recent` (every tier, no auth) and
`GET /api/activity/stream` serve directly. The operator gets
`{"purged": N}` and believes it is done.

**Reproduced:**

```
on disk before purge: True
purge result: {'purged': 1}
on disk after purge:  False
still served by /api/activity/recent (in-memory): True   ← the body
```

**Failure scenario.** Operator reads the (not-yet-built, B2 overlaps B3) notice,
presses Purge, tells a friend the lab no longer has their prompts on it. Until
the next portal restart, `curl localhost:8080/api/activity/recent?n=200`
returns every one of them. Any local process on the machine can do that.

**What would satisfy me.** `purge_legacy_bodies()` must also strip
`prompt`/`response` from `activity_log._buffer` in place (the same technique
`tests/conftest.py`'s fixture and `test_autoresearch_e2e_fake_aerollm.py`'s
`_fresh_events()` already use), and a test must assert
`json.dumps(activity_log.recent(200))` contains no planted body after a purge —
not just that the file doesn't.

### B5 — [BLOCK] the widened-bind × live-recorder banner was dropped silently

No `BIND_ADDR` code and no test in this diff.

The architecture put this **in scope** explicitly, in the same paragraph that
put portal authentication out of scope: *"In scope: the loud banner when a
widened bind meets a live recorder."* It is the only mitigation this sprint
offered for the case that actually matters on someone else's machine —
`BIND_ADDR=0.0.0.0` plus bodies on means every host on the LAN can read
redacted prompts and responses from an endpoint with no credential check
(`_host_is_trusted`, `app.py:667-681`, deliberately accepts a wildcard bind).

**Failure scenario.** Friend follows a blog post, sets `BIND_ADDR=0.0.0.0` to
reach the lab from their laptop, flips the recorder on to see what Buddy is
thinking. Nothing anywhere tells them the combination they just created. Café
Wi-Fi.

**What would satisfy me.** Either build it (a warn-level `activity_log.emit`
at the recorder-flip and at boot when `BIND_ADDR` is non-loopback and the
recorder is on, plus a line in the Admin card's recorder copy), or take it to
the operator as an explicit deferral recorded in `SPRINT.md`. What is not
acceptable is that it vanished without appearing in the deviation log.

### S1 — [ASK] bodies captured while the recorder was on stay readable after it is turned off, and there is no way to purge them

`src/arail/agent_trace.py:377` (`"last": last` — the whole record),
`:216` (`find()`), `src/arail/portal/app.py:6290-6302` (SSE serialises the whole record)

`/api/agents/prompts` correctly checks the *current* recorder state
(`app.py:5211`) before adding body keys. The three admin endpoints do not: they
return the full record, bodies included, whatever the toggle now says. And
`agent_traces.jsonl` keeps them on disk (up to 10 MB) with no purge path — the
sprint built a purge for the *old* store and none for the new one.

Admin-gated, so this is not tier escalation. But "I turned the recorder off"
does not mean what an operator will assume it means, and the sprint's own F12
principle (disclose, offer a purge) is not applied to the store it created.

**What would satisfy me.** Either gate body fields on `recorder_on()` in all
four read paths (one helper, four call sites), or — better — add
`POST /api/admin/flight-recorder` an `{enabled: false, purge: true}` option that
drops `bodies` from the ring and rewrites `agent_traces.jsonl` the way
`purge_legacy_bodies` does. Then say which one it is in the recorder copy.

### S2 — [ASK] the subprocess path writes up to 80 chars of arbitrary exception text into a field the schema documents as a class name — on disk, with the recorder off

`src/arail/skills/goal_parser/__init__.py:250`

```python
error_class=str(payload.get("error", "unknown"))[:80],
```

The child emits `f"{type(e).__name__}: {e}"`
(`_subprocess_runner.py:78-84`). Everywhere else in this sprint `error_class`
is `type(exc).__name__` and nothing else — deliberately, because that is the
one field guaranteed to contain no payload. Here it is free text from an
arbitrary backend exception, persisted to `agent_traces.jsonl` regardless of
the flight recorder, outside `redact.capture_body`'s reach.

**Failure scenario.** A cloud backend raises an error whose message echoes the
request (several HTTP clients include the URL and, for some providers, a
fragment of the request body). Eighty characters of it land on disk in a store
the operator was told contains metadata only.

**What would satisfy me.** `error_class=str(...).split(":", 1)[0][:64]`, or keep
the detail in a new `error_detail` field that goes through `redact.redact()` and
is only populated when the recorder is on. Either is one line.

### S3 — [INFO] Buddy's dream announcement still puts model output into `activity.jsonl`, ungated and unredacted

`src/arail/agents/_builtin_buddy.py:1473-1480` — `data={"preview": reflection[:160]}`.

Pre-existing, not introduced here, and not a `prompt_trace` body — so W4's
letter holds and `_has_legacy_body` (`activity.py:222`) will not find it. But it
does mean "0 response bodies in activity.jsonl" is true of the convention, not
of the file. **QA must know this**: QA-BLIND-1's "grep the whole `DATA_DIR`
tree" can legitimately hit a planted string here via the dream path, and that
would be a real finding about a real (pre-existing) leak, not a false positive.

### S4 — [INFO] what I checked and found clean

- **Trace `attributes`, error strings, `call_site`.** The record carries no
  credential-adjacent material: `provider` is a provider *name*, `entry_id` a
  registry id, `error_class` a class name (except S2), `call_site` a
  `module:lineno` from one `sys._getframe` (`agent_context.py:96-111`). The
  `system` prompt is deliberately **not** captured — `capture_body(prompt,
  response.text)` only (`core.py:277`). No `Authorization` header value can
  reach the store on any path I could find.
- **`_note_drop`'s stdlib warning** (`agent_trace.py:166`) logs the exception
  only — a `TypeError`/`OSError` string, no record content. Correctly not routed
  through `activity_log` (which would recurse onto the same disk).
- **CSRF on the mutating routes.** `local_trust_boundary` (`app.py:685-707`) is
  registered last, so it is the outermost middleware and rejects
  `Sec-Fetch-Site: cross-site|none` and mismatched `Origin` for
  `POST/PUT/PATCH/DELETE` before any handler runs. The two new POSTs therefore
  get exactly the same protection as every other state-changing route — no
  weaker, no stronger. Tested at `test_admin_agent_lanes_endpoints.py:132,167`.
- **Tier gate ordering.** Every new handler's first statement is
  `_require_surface("admin")`, before `await request.json()`. A minimalist
  client gets a bare 404 with no body parse and no route disclosure.
- **`agent_id` sanitisation.** `_sanitize_agent_id` (`agent_context.py:70-89`)
  breaks `..` first, then whitelists `[a-z0-9_.-]`, then truncates to 64. The id
  reaches a JSON file, a `calls_by_source` key and a DOM attribute; all three
  are safe. `admin.html` escapes every interpolation it renders.
- **Purge atomicity.** Temp file in the same directory + `os.replace`
  (`activity.py:249-265`). A crash mid-rewrite leaves the original intact and an
  orphan `.purge_tmp` that the next purge overwrites. No `fsync` on file or
  directory, so a *power* loss could still yield a truncated file — the same
  standard as the pre-existing rotation, so not a regression. The real gap is
  B2, not atomicity.

---

## Code quality findings

### B3 — [BLOCK] the existing halt control's meaning changed and its label did not

`src/arail/portal/templates/_nav.html:118-119,177` — **untouched by this diff.**

```html
<button class="btn btn-sm btn-red" id="btn-halt"
        title="Cancel all running jobs — portal stays up">⏸ Halt jobs</button>
...
if(!confirm('Halt all running jobs? The portal and services stay up — this cancels agent work only.'))return;
```

`SPRINT.md`'s binding decision: *"**One halt switch, wider meaning** (operator)
… **Relabelled so the UI states exactly what it holds.** No second flag."* The
flag is shared as decided. The relabel did not happen. And this control — not
the new Admin card — is the one that appears in the global status bar on **every
page and every tier**, including `minimalist`, where the Admin card does not
exist at all.

**Failure scenario.** Friend on a minimalist lab notices a long-running job and
presses "Halt jobs", reading the tooltip as "stop the batch". Buddy goes
permanently silent — inference refused at the chokepoint, `_emit` gated — and
the state survives a restart (`halt.json`). Nothing in the UI they can reach
explains it. They conclude the lab is broken. This is exactly the
failure-mode-grace item in arail's paranoid checklist.

**What would satisfy me.** Relabel `_nav.html`'s button, `title`, and `confirm`
string to the same clauses the Admin card uses (agents stop calling models;
proactive lines stop; in-flight calls finish; SRE's crash watcher is exempt;
this World only; survives a restart), and extend F17's test to assert the copy
in **both** templates against one shared source of truth.

### B4 — [BLOCK] F17's test cannot detect the drift it exists to detect

`tests/test_admin_agent_lanes_endpoints.py:186-231`

`HOLD_ALL_AGENTS_COPY` is a hardcoded duplicate of the copy, declared in the
test file. The test then asserts `"agents stop calling models" in rendered`
where `rendered` is that same constant. `admin.html:1659-1678` builds its copy
independently in JS. The three behaviours *are* genuinely asserted — that half
is good — but the copy is compared to itself, so the template can say anything
and the test stays green. F17's stated mitigation was *"Copy and behaviour
cannot diverge without a red test."*

**What would satisfy me.** Read `admin.html`, extract the template literal, and
assert every clause of it against the behaviour asserted in the same test body —
so that editing the copy without editing the behaviour (or vice versa) is red.
Same for `_nav.html` once B3 lands.

### B6 — [BLOCK] W1 cannot pass: four of its seven mandatory fields are in the JSON but not on screen

`src/arail/portal/templates/admin.html:1696-1698`

```js
'<table class="pr-table"><tr><th>Agent</th><th>Calls</th>' +
'<th>Brain</th><th>TTFT</th><th>Tokens out</th><th>Deep reason</th></tr>'
```

W1 requires, on `Admin → Agents`, per call: **agent id · model · backend ·
brain+effort · TTFT-or-explicit-`n/a` · tokens in/out · `explain()` reason code
verbatim**. The card renders agent id, brain, TTFT, tokens_out, reason. Missing:
**model, backend, tokens_in, effort.** `lanes_snapshot()` already returns all of
them inside `lane.last` (`agent_trace.py:377`), so this is a display gap, not a
data gap — but W1 is measured on the screen, and W5 (the narration test) is
measured on the screen too: "which brain answered — the 1B or the deep model?"
is not answerable without the model name.

TTFT rendering itself is correct and honest: `lane.ttft_ms != null ? … :
(lane.ttft_status || 'n/a')` — a number never appears for a non-`measured`
status. `n/a` is representable end to end. Good.

**What would satisfy me.** Four more columns (or a two-line-per-lane layout),
and a test that asserts each of W1's seven field names appears in the rendered
`admin.html` lane markup.

### D1 — [ASK] `ARAIL_AGENT_STREAM_FAST` removes the 120 s total-generation ceiling from Buddy's fast calls, undocumented

`src/arail/router/backends.py:2117-2124` vs `:2059-2064`

I compared the two request bodies line by line. They are identical except
`stream` — same `/api/chat`, same `messages`, same `options.num_ctx`/
`num_predict`, same `keep_alive`, same `think` handling, same `timeout=120`. The
joined `full_text` is what the terminal `ModelResponse.text` carries, and I
confirmed every backend's `stream_complete` sets `text=full_text` (lines 336,
423, 669, 951, 2150), so `_join_stream`'s preference for the terminal text can
never silently discard arrived deltas. **That part is sound.**

The behaviour change nobody wrote down: with `requests`, `timeout=120` on a
non-streamed POST bounds the whole response; on `stream=True` it is a *per-read*
socket timeout. Total generation time becomes unbounded as long as one token
arrives every 120 s. Buddy's fast call runs in `asyncio.to_thread`, so a stalled
stream holds a default-executor worker indefinitely, and enough of them starve
`to_thread` process-wide.

**Failure scenario.** A reasoning model on a thermally-throttled 16 GB box
emits a token every couple of minutes for an hour. Before: the call died at
120 s and Buddy fell back. After (default ON): the thread never returns, and
the *next* Buddy tick queues behind it.

**What would satisfy me.** Either a wall-clock budget inside `_join_stream`
(break and fall back to `complete()` past N seconds), or `timeout=(connect,
read)` plus a documented note in `docs/agent-observability.md` and the BACKLOG
that streaming changes the timeout semantics. And one test that a stream which
stalls is abandoned.

### D2 — [ASK] the "identical string" claim is tested tautologically

`tests/test_deep_policy_stream_fast.py:63-72`

`_StreamingRouter.stream_complete` and `.complete` both return the same
hardcoded `self.text`, so `assert out_streamed == out_complete` cannot fail.
The architecture asked for exactly this test, so this is my spec's weakness as
much as the builder's — but the meaningful, *buildable* assertion is missing: a
structural test that `OllamaNativeBackend.stream_complete` and `.complete`
construct the same request body except `stream`. I verified that by hand (D1);
nothing stops the next edit to one of them from diverging.

Also worth stating honestly somewhere user-facing: with `temperature > 0`,
streaming and non-streaming are different *samples*. "Deltas joined to the
identical string" is true of the transport, not of the generation. The operator
should not be told Buddy's output is unchanged.

### D3 — [ASK] three things the snapshot computes and the card never shows

`admin.html:1696-1722` renders `lanes`, `hold`, `recorder`, `unattributed`. It
does not render:

- **`user_defined.calls`** (`agent_trace.py:411`) — a user-defined loader
  agent's model calls are counted and then invisible. OQ4 said user-defined
  agents "render as a generic lane"; VISION note 9 said "Nothing is ever
  hidden". The architecture's own `lanes_snapshot` shape put it *in* `lanes[]`.
- **`drops.dropped_writes`** — F1's detection half. A full disk is a silent
  silence on the card.
- **`slot.overlap_pct` / `samples`** — DE1's instrument. This number is the
  entire justification for deferring the gateway; the operator has to `curl` for
  it.

Also unwired: `/api/admin/agent-trace/{trace_id}` (no UI caller) and
`agent_trace.SYS_LANES` (a constant nothing reads — `lanes_snapshot()` emits no
`sys:*` lane at all, so the four non-agent callers have no lane despite being
attributed correctly).

**What would satisfy me.** Render `user_defined`, `dropped_writes` and
`overlap_pct`; either emit `sys:*` lanes or delete `SYS_LANES`.

### D4 — [ASK] "stop speaking" is not what the code does

5 of 84 `emit` sites under `src/arail/agents/` are gated. The builder's
selection is defensible and documented (findings vs. diagnostics), and
QA-BLIND-2 is the designated confirmation — but the *copy* says
"stop speaking", and two of Buddy's own four emit sites are ungated
(`_builtin_buddy.py:1263` "Buddy is online — obsessing over your goal", emitted
on every portal start even while held; `:1473` the dream announcement).

Combined with F16 (a user-defined agent that spawns a bare thread keeps calling
models while held), the Admin control currently makes two claims broader than
the code. F17 exists to stop precisely this.

**What would satisfy me.** Narrow the copy to what is true — e.g. "agents stop
calling models and stop posting findings and suggestions. Operational and error
lines, and SRE's crash alerts, continue." — or gate more sites. **This needs the
operator**, because it is his "I want the lab to shut up for ten minutes" that
is being partially delivered.

### D5 — [ASK] F3's static guard was never written, `spawn_thread` is dead code, and F16's halt claim is false as a result

`agent_context.py:236-247` (never called from `src/`),
`_builtin_presence.py:127` (still a bare `threading.Thread`).

F3's designed mitigation was a *static test* forbidding `run_in_executor` and
bare `threading.Thread(` under `src/arail/agents/`, with `spawn_thread` as the
escape hatch for the one legitimate site. Neither half landed: no test, and the
one site was not converted. Today nothing leaks (presence calls no model), but
the guard that would catch the next author is absent, and F16's "it cannot
ignore halt" is false because an unattributed call is admitted
(`agent_context.py:368`; `test_halt_gate.py:56` tests that it is admitted —
correctly, per contract §4).

**What would satisfy me.** (a) Convert `_builtin_presence.py:127` to
`spawn_thread` so the shim has a production caller. (b) Add the static test over
`src/arail/agents/` with an explicit allowlist. (c) One honest sentence in
`docs/agents.md`'s new section and in the Admin copy: a model call that has lost
its context is **not held** — it is only made visible.

### D6 — [ASK] observability can raise into an inference through two unguarded imports

`src/arail/router/core.py:161` and `src/arail/agent_context.py:369`

```python
    @staticmethod
    def _slot_info() -> dict:
        from arail.portal import scheduler as _inference_scheduler   # ← outside the try
        try:
            slot = _inference_scheduler.slot_pressure()
```

`_halted()` (`core.py:172-178`) puts its import *inside* the try; `_slot_info`
does not, and neither does `halt_gate`. `arail.portal` is a namespace package
(no `__init__.py`), imported here from contexts that are not the portal — the
goal-parser subprocess, `lab/tools/model_router.py`, CLI paths. An `ImportError`
there propagates straight out of `complete()`. The architecture's rule is
absolute: *"When the trace write fails, it must never fail or slow an
inference."*

**What would satisfy me.** Move both imports inside their `try`. Two lines.

### D7 — [ASK] the SSE subscriber is silently dropped on backpressure, and the admin card amplifies every trace into a full snapshot fetch

`agent_trace.py:255-258` · `admin.html:1735`

`_fanout`'s same-loop branch treats `QueueFull` as "subscriber is dead" and
removes it; the generator then blocks on `await q.get()` forever. The browser
sees an open connection with no frames and never reconnects, so the lane view
goes stale with no indication. (The cross-thread branch drops the frame instead
and keeps the subscriber — the asymmetry is unintentional.)

Separately, `AGENT_LANES_ES.onmessage = () => loadAgentLanes()` issues one full
`/api/admin/agent-lanes` request per trace record. During a burst that is one
HTTP round-trip and one 500-record snapshot build per inference. And because
`/api/admin/agent-lanes` was added to `FAST_PATH_PREFIXES`
(`portal/scheduler.py:70`), those requests are **timed into `fast_path_ms`** —
the exact metric DE4 uses to decide whether this sprint's instrumentation is too
heavy. The architecture worried about this for the SSE route and missed it for
the snapshot route.

**What would satisfy me.** Close the response (or emit a `retry:`/error frame)
instead of orphaning a full subscriber; debounce `loadAgentLanes` to ~250 ms;
and when DE4's number is taken, either exclude `/api/admin/agent-lanes` from the
comparison or take the baseline with the Admin tab closed — recorded in
`SPRINT.md` either way, or the number will be misread.

### D8 — [INFO] smaller things

- **`/api/agents/status`'s legacy fallback sums only the first matching event.**
  `app.py:5094` — `if src not in per_agent_tokens:` is re-checked each iteration,
  but the branch inserts `src` on its first hit, so events 2..n are skipped.
  Under-reports in a deprecated fallback path.
- **`costs.calls_by_source` key cardinality is now unbounded in principle.**
  Every distinct `agent:<id>` / `sys:<label>` becomes a persisted key in a file
  `cost_tracker` rewrites in full on every call. Bounded in practice by agent
  folders and fixed labels; a buggy agent minting ids per call would degrade
  every inference. Worth a cardinality cap in the gateway sprint.
- **Streamed traces report `model` from the client, not the server.**
  `OllamaNativeBackend.stream_complete` sets `model=self.model_name`;
  `complete()` uses `data.get("model", …)`. Cosmetic divergence between a
  streamed and a non-streamed trace for the same model.
- **A stream abandoned before its terminal `ModelResponse` produces no trace at
  all** (`core.py:346` — `GeneratorExit` is a `BaseException`, so the `except
  Exception` never fires; the `finally` decrements correctly). Postcondition #1
  ("exactly one record per call") does not hold for a client disconnect
  mid-stream. Chat only, no counter leak, low impact.
- **A purged legacy event renders as "flight recorder off"**
  (`agents.html:1148`) rather than "purged", even though `body_purged: true` is
  right there in the dict.
- **`test_admin_template_uses_sse_not_setinterval_for_lanes`
  (`tests/…:257`)** guards its assertion with `if lanes_section_start != -1:`.
  Rename the element and the test passes asserting nothing. Same class as the
  vacuous passes the orchestrator already caught twice — make it
  `assert lanes_section_start != -1`.
- **`pytest.mark.perf`** — confirm it is registered in `pytest.ini`/`pyproject`
  or the marker warns.

---

## Test coverage assessment

**Numbers I verified myself.** Sprint suite (20 files): **256 passed**, 0
failed, 1.99 s. Regression set (`test_health_metrics`, `test_inference_scheduler`,
`test_scheduler`, `test_costs_persistence`, `test_activity_rotation`): **40
passed**. I did not re-run the 181-file differential; the orchestrator's verified
"17 fail on both `main` and this branch, 0 fail only on this branch" is the
record I am relying on, and it is the right kind of evidence.

**Failure-mode coverage:** F1, F2, F5, F6, F7, F8, F10, F11(partial), F12,
F13(partial), F14, F15, F17(partial), F18, F19, F20 have named tests. **F4** is
covered indirectly. **F9's wiring, F16 entirely, and F3's static guard have no
test.**

**Quality problems, ranked.**

1. **F17's copy assertion is a self-comparison** (B4) — the single most
   important test in the strategy, and it cannot fail.
2. **No test pins any of the five `speech_gate` call sites** (F9). Delete the
   gate from `_builtin_buddy._emit` and all 256 tests still pass. QA-BLIND-2 is
   the designated confirmation, but it is authored blind and may not reach those
   exact five funnels. Five three-line tests would close this without leaking
   anything to QA.
3. **F20's identity test is tautological** (D2).
4. **`/api/admin/agent-trace-stream` is absent from the F13 parameterised
   list** (`tests/…:37`) — the one new endpoint that serialises bodies.
5. **Conditional assertions** — `tests/…:257`'s `if … != -1:`, and
   `test_agent_trace.py:85`'s read-only-dir test would pass vacuously as root.
6. **Source-text tests where behaviour was available**:
   `test_browser_source_has_three_agent_call_sites`,
   `test_world_routes_system_call_labels_match_inference_slot_labels`. These pin
   implementation, and the builder's own deviation #6 concedes world-forge/
   review/grow have no functional coverage.
7. **W1's "within 2 s" is not tested as designed.** The structural half exists
   (`test_agent_trace.py:272,310` — push before return, including the foreign-
   thread `call_soon_threadsafe` handover) and the no-polling half exists
   (weakly, item 5). **The one wall-clock `@pytest.mark.timing` TestClient SSE
   test the architecture specified does not exist.** And
   `test_subscribe_receives_a_record_pushed_before_record_returns` awaits
   with a 1 s timeout rather than asserting `q.qsize() == 1` synchronously after
   `record()` returns, which is what "before it returns" means. Deterministic
   and cheap to fix.
8. The empty-collection class the orchestrator caught is genuinely fixed where
   it was found — `test_flight_recorder.py:158,202` now assert "at least one
   matching event was emitted" before asserting "no body", and read
   `researcher.activity_log` / `browser.activity_log` (the module-under-test's
   own binding). That fix is correct and is the right pattern.

**Verdict on `_isolated_agent_observability_data_root` (`tests/conftest.py:249`).**
**Necessary, correctly diagnosed, and one concern too many.** The sprint put a
disk write on a path every one of ~5819 tests can reach, so a repo-wide autouse
redirect of `config.DATA_DIR` is the right shape, and it belongs in the existing
`_no_ambient_*` family. The diagnosis of why `ActivityLog._instance = None` was
a no-op is exactly right and worth keeping in the docstring.

But it fuses **hermeticity** (redirect `DATA_DIR`, clear the trace/context/
buffer state) with a **product default** (`(tmp_path / ".world-prompt-seen").touch()`
— declare every lab in the repo already onboarded). Those are different
concerns with different blast radii, and as one fixture you cannot take the
first without the second. A test that wants a genuinely fresh data root must now
know to monkeypatch `portal.app._world_prompt_marker`, an unrelated portal
internal — which is a trap, and the fixture's own git history (three attempts,
two of them wrong in opposite directions) is the evidence. It also makes the
suite structurally blind to first-run-state regressions: `test_onboarding.py`
and `test_world_first_impression.py` opt out today; the next first-run test will
be silently pre-decided.

Second weakness: the fixture clears `activity_mod.activity_log._buffer`, so any
test that `importlib.reload`s `arail.activity` (as `test_boot_security_scan.py`
does) makes the fixture's isolation silently ineffective for modules holding the
old reference. The builder fixed the two tests that bit; the fixture is still
not robust to it.

**What would satisfy me:** split it into `_isolated_data_root` (the hermeticity
half, unconditional) and `_ambient_onboarded_default` (the product default,
which a test can drop with a one-line `request.getfixturevalue` override or a
marker). Not a blocker — the differential shows 0 branch-only failures — but
file it, because QA is about to write blind tests inside this environment.

**Verdict on the `ActivityLog` singleton-by-reference conclusion.** **The
builder is right, and I verified it independently rather than accepting it.**
`grep -rn "importlib.reload" src/` → zero hits. `config.DATA_DIR` is assigned
once (`config.py:85`) and never rebound anywhere in `src/`. Each concurrent
World is its own process (`docs/concurrent-worlds.md`) with its own
`ARAIL_DATA_DIR` live before `arail.activity` imports. So in production there is
exactly one `ActivityLog` per process, bound to the right root, for the process's
life — no F14-class leak. The residual risk is entirely test-credibility (above),
and it deserves the one-line mention in `docs/agent-observability.md` the builder
suggested. Note it is B2 — not this — that makes the singleton matter
operationally: the purge has to reach that object.

---

## Performance assessment

**Measured, not trusted.** Fake backend, `cost_tracker.track` stubbed out to
isolate the sprint's additions from the pre-existing `costs.json` full rewrite,
4000 calls after 200 warm-up, this worktree, this box:

| Configuration | p50 | p95 | p99 | max |
|---|---|---|---|---|
| Full chokepoint additions (context + `_slot_info` + `_halted` + `recorder_on` + `record()` **with disk append**) | 0.036 ms | **0.048 ms** | 0.055 ms | 0.34 ms |
| Ring only (`ARAIL_TRACE_PERSIST=0`) | 0.006 ms | 0.006 ms | 0.007 ms | 0.030 ms |
| `record()` stubbed to a no-op (i.e. the non-trace additions alone) | 0.003 ms | 0.004 ms | 0.004 ms | 0.019 ms |

**Design budget ≤ 1.0 ms p95: passed with 20× headroom. DE4's 5 ms kill line:
100× headroom.** For scale, the *unmodified* `complete()` including
`cost_tracker.track()` measures 2.79 ms p50 on this box — the pre-existing
`costs.json` rewrite is ~58× the entire cost of this sprint's instrumentation.
The architecture's claim that the trace write is an order of magnitude cheaper
than what is already on the path is confirmed and then some.

Structural checks:

- **No lock is held across I/O on the steady-state hot path.** `record()` →
  `_append_disk` takes no lock. `recorder_on()` takes `_recorder_lock` but
  `_load_recorder_locked` short-circuits after the first call per process.
  `jobs_halted()` takes `_halt_lock` and reads disk only on first call.
  Remaining windows are operator-toggle-only: `set_recorder_enabled` holds
  `_recorder_lock` across `write_text`, and `halt_all_jobs` holds `_halt_lock`
  across `_persist_halt_locked` — every concurrent inference briefly waits on
  one small write. Acceptable; worth knowing.
- **Bounded growth, all of it.** Ring `deque(maxlen∈[50,5000])`; disk 2 files ×
  5 MB checked every 64 records (worst case ~60 KB overshoot); `_SUBSCRIBERS`
  per client and pruned; `lanes_snapshot`'s per-call dicts derived from the ring.
  No per-agent dict keyed by an unbounded id — except `costs.calls_by_source`
  (D8), which is persisted and is the one place a cardinality bomb could land.
- **JSON serialisation per call** is ~33 small fields, no large structures.
  Bodies are capped at 2000+1000 chars *after* redaction.
- **A trace failure cannot raise into an inference** — `record()` is
  double-wrapped, `capture_body` fails closed to `None`, `recorder_on()`,
  `_halted()`, `call_site()` and `_slot_info()`'s *call* all have their own
  boundaries. The two exceptions are the unguarded imports in D6.
- **One-time subprocess cost:** `from arail.portal import scheduler` in a fresh
  process is ~20 ms and does **not** pull in `app.py`. Paid once per goal-parser
  child, which already loads the router. Fine.
- `/api/admin/agent-lanes` in `FAST_PATH_PREFIXES` contaminates DE4's own
  `fast_path_ms` measurement — see D7.

---

## Tech debt delta vs. ARCHITECTURE.md's prediction

**Predicted and incurred as expected:** the third persistence path; a contextvar
at eleven L3 sites; one flag with two surfaces; `tokens` as a deprecated alias;
`ARAIL_AGENT_STREAM_FAST`; the awkwardly-named SSE route. All five BACKLOG
entries the architecture required were filed (`sprints/BACKLOG.md`: portal auth,
the 17 ungated admin endpoints, the gateway gated on DE1, the MLX
`stream_complete` signature bug + the concurrent `costs.json` write, the `tokens`
alias removal).

**Predicted and repaid as expected:** always-on unredacted bodies (mostly — B1,
B2, S1 and S3 are the caveats); `billing_source="agent"` for everything;
`max_tokens` presented as usage; the goal-parser's untraced leak; `snapshot()`
as the only slot read; Buddy emitting nothing.

**New debt the architect did not anticipate — add these to ARCHITECTURE.md's
Added table before a PASS:**

| Debt | Why it exists | Home |
|---|---|---|
| `agent_traces.jsonl` can hold prompt/response bodies with no purge path and no recorder-state gate on the three admin read paths | The sprint built F12 for the *old* store and none for the *new* one | S1 — fix or file |
| `spawn_thread` is shipped, tested, and called from nowhere; the F3 static guard does not exist | The one legitimate site was not converted | D5 |
| `SYS_LANES` is a constant nothing reads; `lanes_snapshot()` emits no `sys:*` lane | The four non-agent callers are attributed but laneless | D3 |
| `/api/admin/agent-trace/{trace_id}` has no UI caller | S6 shipped the endpoint, not the drill-in | D3 |
| `/api/admin/agent-lanes` is inside `FAST_PATH_PREFIXES`, so it self-inflates DE4's gate metric, amplified one request per trace by the card | Not foreseen in contract #6, which only worried about the SSE route | D7 |
| `costs.calls_by_source` key cardinality is now unbounded in principle | Per-agent attribution replaced one key with N | D8 |
| Streaming changes `requests`' timeout semantics from total to per-read on Buddy's fast path | `ARAIL_AGENT_STREAM_FAST` default ON | D1 |
| `tests/conftest.py`'s autouse fixture fuses hermeticity with a first-run product default for all ~5819 tests | The hermeticity fix needed a companion default | conftest verdict |

**Net:** still negative (security and truth-in-UI repaid more than added) —
**conditional on B1, B2 and B5**, without which the security half of the ledger
does not close.

---

## Required actions before merge

### Must fix before QA (blocking)

1. **B1** — `redact.py:120-126`: make a known-value-pass failure fail **closed**
   (return `None` from `capture_body`), catch `ValueError`/`UnicodeDecodeError`
   in `_parse_secrets_env`, and add a test that patches `_known_values` to raise.
2. **B2** — `activity.py:232-272`: `purge_legacy_bodies()` must strip
   `prompt`/`response` from `activity_log._buffer` in place; test that
   `activity_log.recent(200)` carries no planted body after a purge.
3. **B3** — `_nav.html:118-119,177`: relabel the button, `title` and `confirm`
   to state what the switch now holds, matching the Admin card's clauses.
4. **B5** — build the widened-bind × live-recorder warning + banner, **or** take
   the deferral to the operator and record it in `SPRINT.md`. Not silently.
5. **B4** — `tests/test_admin_agent_lanes_endpoints.py:186`: assert F17's copy
   against the actual template text (both templates, after B3), not against a
   duplicate declared in the test.
6. **B6** — `admin.html:1696`: render model, backend, tokens_in and effort;
   test that all seven W1 field names appear in the lane markup.
7. **D6** — `core.py:161`, `agent_context.py:369`: move both imports inside
   their `try`. Two lines; the "observability never breaks inference" guarantee
   is absolute or it isn't.
8. **F9 wiring tests** — five tests, one per gated funnel, asserting the site
   goes silent while held. Without them the sprint's headline guardrail is
   deletable without a red test, and QA-BLIND-2 stays genuinely blind.
9. **F13 gap** — add `/api/admin/agent-trace-stream` to `_ENDPOINTS`
   (`tests/…:37`).
10. **D8's conditional assertion** — `tests/…:257`: `assert lanes_section_start
    != -1` before using it.

### File as debt (BACKLOG, not this sprint)

- **S1** — recorder-off gating on the three admin read paths, and a purge for
  `agent_traces.jsonl` bodies. (If the operator would rather have this now, it
  moves up — ask him.)
- **S2** — `goal_parser/__init__.py:250`: `error_class` must be a class name.
  *(One line; promote to must-fix if the builder is already in the file.)*
- **S3** — Buddy's dream preview still puts model output in `activity.jsonl`.
  Pre-existing. **Hand this to QA as a known non-`prompt_trace` leak so
  QA-BLIND-1's tree grep is interpreted correctly.**
- **D1** — bound the streamed fast path's total duration; document the timeout
  change.
- **D2** — structural test that Ollama's two request bodies agree except
  `stream`.
- **D3** — render `user_defined`, `dropped_writes`, `overlap_pct`; wire the
  drill-in; emit or delete `SYS_LANES`.
- **D5** — convert `_builtin_presence.py:127` to `spawn_thread`; add the F3
  static guard; say in `docs/agents.md` and the Admin copy that a call which
  lost its context is visible but **not** held.
- **D7** — SSE backpressure behaviour; debounce the card; decide how DE4's
  number is taken.
- **F2** — count a rotation failure in `dropped_writes`.
- **W1's wall-clock SSE test** (`@pytest.mark.timing`) and the stronger
  `q.qsize()` assertion.
- **conftest** — split the fixture into hermeticity + ambient default.
- **D8's remainder** — the `/api/agents/status` fallback loop bug, the
  `calls_by_source` cardinality cap, the streamed `model` provenance, the
  abandoned-stream missing trace, the purged-event UI string,
  `pytest.mark.perf` registration.

### Needs the operator (not the builder's call)

1. **D4 — what "hold" promises.** The control says "agents stop calling models
   and stop speaking." Truthfully: 5 of 84 agent emit sites are gated (findings
   and suggestions; not diagnostics, not SRE, not Buddy's boot/dream notices),
   and a user-defined agent that spawns its own thread keeps calling models
   entirely. Narrow the copy, or widen the gating, or both — his "I want the lab
   to shut up for ten minutes" is the requirement being partially met.
2. **B5 — the widened-bind banner**: build now or defer on the record.
3. **S1 — recorder-off exposure**: does "off" mean "stop capturing" or "stop
   showing"? His answer decides whether S1 is debt or a must-fix.
4. **W5's witness line** is still unwritten and must be his own words, signed,
   in `SPRINT.md`. Do not let it be inferred. W1 must pass first (B6), or the
   narration test is being run against a screen missing the model name.

---

## What I want on the re-review

The ten must-fix items, the diff for each, and one number: `redactions >= 1`
with a deliberately unreadable `secrets.env`, and a `grep -r` of the real
`DATA_DIR` tree plus `curl /api/activity/recent` after a purge, both clean.
Everything else can be read.

---
---

# Re-review: the fix loop

**Date:** 2026-09-21 (appended; the original review above is unchanged)
**Reviewing:** `git diff 0c13a176..HEAD` — 10 commits, `da70792d`…`eb4950b0`, 25 files, +1866/−112
**Against:** the original BLOCK's "Must fix before QA" list (10 items) + the three
binding operator decisions recorded in `SPRINT.md` after it
**Mode:** review

I did not take the fix loop on trust. I ran the sprint suite (**303 passed, 1
skipped**, 2.3 s, no hang), ran 18 at-risk *pre-existing* suites (**318 passed,
1 skipped**), reproduced my two original security defects to confirm they are
gone, audited every code path that can serve a body, and **mutation-tested 17
production edits** — breaking each fix in turn and checking that the claimed
proving test goes red, then restoring. Three of those mutations came back green,
which is where this section's new findings are.

## Verdict: WEAK_PASS

No BLOCK-class defect remains. Both security BLOCKs are closed and
mutation-pinned. Both undeclared operator decisions are implemented. The LAN
banner is built. W1's fields render. D6, F13, D8 and the seven `speech_gate`
pins are all genuinely non-vacuous.

Five ASKs remain, and they must land before the PR — not before QA. One is a
residual fail-open in redaction that I am ruling on explicitly below; one is a
partial non-implementation of operator decision (a); three are test-strength
problems, **including one assertion the fix loop actively weakened**. None of
the five prevents QA from doing its job, and none is reachable in a default
configuration.

---

## Must-fix findings: satisfied / partially / not

| id | Status | Evidence |
|---|---|---|
| **B1** redaction fails open | **PARTIALLY** | `redact.py:141-163` — `_redact_strict()` has no inner `try` around the known-values pass, so a raise propagates to `capture_body`'s boundary → `None`. `_parse_secrets_env` (`:70-90`) now reads `errors="replace"` and catches `(OSError, ValueError)`. Mutation **M13** (restore the lenient swallow) → 2 tests red. My original repro (non-UTF-8 `secrets.env`) is fixed *and improved*: the other values in the file are still redacted rather than the whole pass being lost. **Residual, reproduced — see R1.** |
| **B2** purge leaves bodies in memory | **SATISFIED** | `activity.py:245-267` — disk via the shared atomic `jsonl_purge.purge_jsonl_bodies` (tmp file in the same dir + `os.replace`, `jsonl_purge.py:66`), then an in-place pass over `activity_log._buffer`. My named proof re-run: `/api/activity/recent` body-free after a purge, disk clean, `{"purged": 1, "purged_memory": 1}`. Mutation **M12** (neuter the memory pass) → 2 tests red. |
| **B3** halt control not relabelled | **SATISFIED** | `_nav.html:118-119` label + `title`, `:177` `confirm()`. Carries operator decision (a)'s exact sentence verbatim. Mutation **M3** (revert the label to "Halt jobs") → red. Doc comments in `lab_brain.py:76-79`, `dream_daemon.py:166`, `job_daemon.py:241-242` updated too — a thoroughness I did not ask for and welcome. |
| **B4** F17 copy test self-comparison | **PARTIALLY** | `tests/…:374-398` — `_admin_hold_control_held_copy()` now regex-extracts the real held-branch literal from `admin.html` and asserts the ternary exists ("this test would otherwise silently check nothing"). The self-comparison is genuinely gone. **But** mutation **M4** — rewriting the copy to *"All running calls are cancelled immediately"* — leaves all 27 admin tests green. See R3. |
| **B5** LAN-bind banner dropped | **SATISFIED** | `config.bind_is_loopback():46-55` (one definition, `app.py:11784-11786`'s pre-existing airgap gate now delegates to it — behaviour identical, and all four airgap-toggle suites still pass), `agent_trace._lan_exposed():499-511`, `lan_exposed` on both `recorder_state()` and `set_recorder_enabled()`, banner at `admin.html:728` + `renderLanRecorderWarning():1719-1733` + a warning appended to the toggle's own copy `:1705-1709`. Probed directly: `lan_exposed` True on `0.0.0.0`, False on loopback. |
| **B6** W1 fields not on screen | **PARTIALLY** | `agent_trace.py:405-408,414` hoists `model`/`backend`/`tokens_in`; `admin.html:1696-1719` renders nine columns. All seven W1 fields now reach the markup. Mutation **M16** (drop the `lane.model` data cell) → red. **But** mutation **M15** (drop `<th>Model</th>` and keep everything else) → green. See R4. |
| **D6** unguarded imports | **SATISFIED** | `agent_context.py:375-381` and `router/core.py:159-168` — both imports inside the `try`. Mutation **M14** (hoist the import back out) → red. `halt_gate` now fails **open** on any failure; correct trade for availability (refusing every inference on an `ImportError` would brick the lab), and documented inline. |
| **F9** no test pins the gate sites | **SATISFIED** | `tests/test_speech_gate_wiring.py` — 14 tests, a held/not-held pair per site, seven sites (Buddy `_emit`, Buddy boot notice, Buddy dream, Librarian `_emit`, Presence `_tick`, Debt Advisor, Consolidation Analyzer). I spot-checked three by mutation (**M5** Buddy `_emit`, **M6** Presence, **M7** Buddy's gate argument) — each turned exactly one test red. Each test reads the module-under-test's own bound `activity_log`, the pattern learned from defect-loop regression B. |
| **F13** stream endpoint ungated in test | **SATISFIED** | `tests/…:41` adds `/api/admin/agent-trace-stream` to `_ENDPOINTS`, plus `test_agent_trace_stream_gated_and_reachable:76-163`, which drives the raw ASGI app (bounded by `asyncio.wait_for`) and additionally asserts `_SUBSCRIBERS` returns to its pre-request length after task cancellation. Better than I asked for. |
| **D8** conditional assertion | **SATISFIED** | `tests/…:334` — `assert lanes_section_start != -1` is now unconditional. |
| **S1** recorder-off must stop showing (promoted to must-fix by the operator) | **SATISFIED** | `agent_trace._strip_bodies_if_recorder_off():216-240` applied at `find():248`, `subscribe():310`, `lanes_snapshot()`'s `last:400`. I audited every body read path independently: `grep` for `get("bodies")`/`["bodies"]` across `src/` returns only `agent_trace.py`'s own gate/purge helpers and `app.py:5212` (`/api/agents/prompts`, already gated on *current* state), and `agent_trace.ring()` has **exactly one** caller (`app.py:5195`, that same gated endpoint). No fifth path. The gate sits **inside** `subscribe()` rather than in the route, so the SSE stream cannot bypass it and any future consumer inherits it — the right placement. Mutations **M8/M9/M10** (remove each strip) → red; **M11** (neuter the purge's ring pass) → red. `purge_flight_recorder_bodies():556-590` reuses the same `jsonl_purge` helper, so there is one purge mechanism, as decision (c) required. |
| **D4** copy overclaims | **PARTIALLY** | Held-branch copy narrowed to the operator's exact sentence in both templates; Buddy's two remaining proactive lines gated, so "announcements" is now true of Buddy. **But** the not-held branch still carries the pre-decision wording. See R2. |

## Updated F1–F20 rows (only those that changed)

| # | Was | Now |
|---|---|---|
| **F1** | mitigated in code, detection half incomplete (no UI) | **unchanged** — `drops.dropped_writes` is still not rendered on the card; correctly filed as debt (D3). |
| **F9** | 5 of 84 emit sites gated, UI claim overshoots the code, `speech_gate()` tested only in isolation | **mitigated as designed** — 7 sites gated (`_builtin_buddy.py:1264-1272,1487-1497` are the two new ones), each pinned by a mutation-verified test pair, and the held copy now names what is actually silenced. The remaining ~77 emit sites are operational/diagnostic lines the operator's decision (a) explicitly leaves running. |
| **F11** | **NOT mitigated** — `capture_body` could return unredacted text | **mitigated as designed, with one residual** — the designed hole is closed and pinned; R1 is a narrower, different door. |
| **F12** | mechanism built, disclosure half absent, purge incomplete | **mitigated as designed** — in-memory purge added (B2); the notice + Purge/Keep buttons exist (`admin.html:731`, `loadLegacyBodiesNotice():1839-1864`, called at boot `:1893`); `test_legacy_bodies_notice_and_buttons_exist_in_admin_ui` pins the UI. |
| **F13** | gate correct, stream endpoint untested | **mitigated as designed** — all five endpoints parameterised; the stream gets its own bounded reachability + subscriber-cleanup test. |
| **F16** | **NOT mitigated as designed** — an unattributed call escapes the hold, undocumented | **unchanged, now correctly filed** — `halt_gate` still admits `ctx is None` (by design, contract §4). D5 in `sprints/BACKLOG.md` carries both halves (convert `_builtin_presence.py:127`; say it in `docs/agents.md` **and** the Admin copy). Still the one place the UI's "agents stop calling models" is broader than the code. |
| **F17** | behaviour tested, copy not tested, copy overclaims | **partially mitigated** — the copy is now read from the real template and three of its four clauses are pinned; the admission-control clause is not (R3), and the not-held branch is neither narrowed nor pinned (R2). |
| **S3** (not an F-row, but changed materially) | a theoretical leak — the line could never fire | **now live** — see R5. |

---

## New findings introduced by the fix loop

Ten commits is where regressions hide. These are the ones I found.

### R1 — [ASK] B1's residual: an *existing-but-unreadable* `secrets.env` still yields a captured, under-redacted body

`src/arail/redact.py:78-80` · `:99`

```python
    try:
        text = path.read_text(errors="replace")
    except (OSError, ValueError):
        return values          # ← "couldn't read it" is indistinguishable from "it was empty"
```

`_known_values()` gates the parse on `path.exists()` (`:99`), so the code
*knows* the file is there. A permission/IO failure collapses to `[]`, the
known-value pass finds nothing, `_redact_strict` runs the shape pass only, and
`capture_body` returns a body with `redactions: 0`.

**Reproduced** (`secrets.env` present, `chmod 000`, recorder on):

```
capture_body("here is supersecretvalue123 in a prompt", "resp")
  -> {'prompt': 'here is supersecretvalue123 in a prompt', 'response': 'resp',
      'truncated': False, 'redactions': 0}
```

**My ruling, since the orchestrator asked for it explicitly: this is a residual
fail-open and it must return `None`, but it is not BLOCK-class.**

Why it must be fixed: the code distinguishes the three states (absent / present
and readable / present and unreadable) and deliberately collapses the third into
the first. That is the same shape as the bug I blocked on — "the pass silently
did nothing" looking identical to "the pass ran and found nothing" — and it is
undetectable from the outside, because `redactions: 0` is also the normal
result for a clean prompt. `path.exists()` is already in hand, so the fix is
about six lines: have `_parse_secrets_env` signal unreadability (raise, or
return `(values, ok)`), let `_known_values` propagate it, and let
`_redact_strict`'s no-`try` policy carry it to `capture_body`'s `None`. Note
`redact()` (the lenient public function, used for display text) must keep
swallowing — the split the builder introduced is exactly the right structure and
this fix rides on it.

Why it is not BLOCK-class: it needs the recorder **on** (an explicit admin
action, not the default) **and** a `secrets.env` the portal cannot read. And
there is a real mitigating coupling — if the portal cannot read `secrets.env`, it
never loaded those provider keys either, so they are not in prompts this process
builds. The exposed window is narrow: keys loaded at boot and the file becoming
unreadable afterwards, or a differently-privileged reader (this product does ship
`scripts/install-daemon.sh` and per-instance `secrets.env` files, so "created
under a different uid" is not hypothetical). The shape pass still runs throughout,
catching `sk-`/`hf_`/`nvapi-`/`ghp_`/`AKIA`/`Bearer`/`key=` forms.

### R2 — [ASK] operator decision (a) narrowed only half the hold copy; the *un*-flipped state still overclaims

`src/arail/portal/templates/admin.html:1686-1687` — the only remaining
"proactive speech" string anywhere in `src/` or `docs/` (verified by grep):

```js
    : `Agents are not held. Flip this to stop every agent's inference and ` +
      `proactive speech immediately (SRE's crash watcher is exempt).`;
```

This is what the operator reads **at the moment of decision** — before flipping —
and it is the wording decision (a) replaced. One second later the same control
tells him something narrower and more accurate. `_admin_hold_control_held_copy()`
extracts only the `hold.held` branch, so nothing pins this string either.

**What would satisfy me.** Rewrite the not-held branch with the same clauses
("Flip this and agents stop calling models and stop posting findings,
suggestions and announcements. Operational/error lines and SRE crash alerts
continue.") and extend the extractor to pin both branches.

### R3 — [ASK] the fix loop's F17 test still cannot catch a lying admission-control clause

`tests/test_admin_agent_lanes_endpoints.py:401-451`

The test asserts four **phrases** are present in the extracted copy. The clause
my F17 row cares about most — *"Calls already running finish (N in flight)"*,
the one that stops the UI implying cancellation — is not among them.

**Mutation M4**, run against the whole file:

```
admin.html: "Calls already running finish `"  ->  "All running calls are cancelled immediately. `"
result: 27 passed, 1 skipped
```

The copy can be made to state the exact opposite of the code's behaviour
(`test_hold_is_admission_control_not_cancellation` proves in-flight calls *do*
finish) with every test green. Also unpinned: the presence of the
`${hold.in_flight}` slot — `held_copy.replace(...)` at `:410` is a silent no-op
if it is gone.

**What would satisfy me.** Add the fifth assertion (`"Calls already running
finish" in rendered` and `"${hold.in_flight}" in held_copy`) beside the
behavioural test that already proves it.

### R4 — [ASK] B6's column-header assertions can be satisfied by the function's own comment

`tests/test_admin_agent_lanes_endpoints.py:196-223`

The test slices `renderAgentLanes()` out of `admin.html` and asserts each header
word appears *somewhere in that slice*. The explanatory comment the fix loop
added inside the function contains `Model/backend/tokens in were in the JSON` —
so the capitalised word "Model" is present twice, once in a comment and once in
the real `<th>`.

**Mutation M15**: delete `<th>Model</th>` from the markup → **green**.
**Mutation M17**: delete it *and* the word from the comment → red.
**Mutation M16**: delete the `lane.model` data cell → red.

So the data-read half of B6 is properly pinned; the header half is not, and a
column can lose its name while keeping its values. Cosmetic in effect, but it is
the vacuous-assertion class and the fix is to assert the markup
(`"<th>Model</th>"`) rather than the bare word.

### R5 — [BLOCK-adjacent, ruled ASK] the `dream()` NameError fix is correct and in scope, but it **activates a path that has never run in production** — and one of its effects is the S3 leak

`src/arail/agents/_builtin_buddy.py:1454` (the added
`from arail.activity import activity_log`)

I verified the builder's claim against pristine main: `activity_log` is bound
**only** inside `_DefaultHost.emit()` (`:109`) and is not a module-level name, so
`dream()`'s `activity_log.emit(...)` at main's line 1466 raised
`NameError: name 'activity_log' is not defined` on every call. Genuinely
pre-existing, genuinely a blocker for testing the widened gate, genuinely a
one-line import adjacent to the gated line. **In scope. Correct.**

But its consequences are a behaviour change, and they are not in the BUILD_LOG:
`dream_daemon._dream_once` (`:102-108`) caught that `NameError` and emitted
*"buddy dream failed: NameError…"* as a **warn**, every night, while the dream
file itself had already been written — so `dream()` never reached its emit, its
`_recent_actions.append`, its `_sync_workflow`, or its `return reflection`. After
the fix all four run and `_dream_once` takes its success branch
(*"buddy dreamed · N chars"*).

The security-relevant consequence: `data={"preview": reflection[:160]}` — 160
characters of raw model output into `activity.jsonl`, unredacted and
recorder-independent — is my S3 finding, which I filed as debt partly because it
was theoretical. **It is now live.** The BACKLOG entry for S3 says the line was
"gated this fix loop", which is true, but a gate only silences it while *held*;
on an unheld lab it now writes where it previously crashed.

**What would satisfy me.** No code change required beyond what is filed, but:
(1) `sprints/BACKLOG.md`'s S3 entry should say the line is newly *live*, not
merely gated; and (2) **QA must exercise the dream path** — it has never
executed to completion in production, so everything after that emit is untested
code in practice.

### R6 — [ASK] the fix loop **weakened** an assertion: `test_reachable_on_maximus` is now vacuous

`tests/test_admin_agent_lanes_endpoints.py:64-72`

Before the fix loop this test asserted `status in (200, 404)` **and then
tightened it** — exactly 404 for the unknown-trace-id case, exactly 200 for
everything else. The fix loop added the `_STREAM_PATHS` skip and **deleted the
tightening branch**, leaving only `assert resp.status_code in (200, 404)`. A 404
now satisfies both this test and `test_f13_404_on_minimalist`.

**Mutation M1b** — change `admin_agent_lanes`'s gate to
`_require_surface("no_such_surface")` so the endpoint 404s on *maximus* too, then
run only this test:

```
4 passed, 1 skipped
```

In the original review I explicitly *retracted* a vacuity complaint about this
test because the tightening branch existed. It no longer does. Other tests in the
file do catch a broken gate (M1 over the whole file fails), so the endpoint is
not unprotected — but this is a real, avoidable loss of test strength introduced
while fixing a test-strength finding, and it is two lines to restore.

### R7 — [INFO] smaller things the fix loop added

- **`POST /api/admin/flight-recorder {"purge": true}` with no `enabled` key
  silently turns the recorder off** — `bool(body.get("enabled"))` is `False`
  (`app.py:6346`). The UI always sends both (`admin.html:1826`), so this is a
  curl-only footgun, but the purge also rewrites `flight_recorder.json` and
  resets `changed_at` on a purge-only call. Consider making `enabled` optional
  and defaulting to the current state.
- **`loadLegacyBodiesNotice()` runs a full `activity.jsonl` (+ rotation) scan on
  every admin page load**, synchronously on the event loop, where the
  architecture specified a one-shot boot scan. **Measured: 43 ms over 17 MB /
  40 000 lines** — comfortably fine, ~50 ms at the 10 MB×2 ceiling. Noting it
  because it is event-loop-blocking work that grows with lab age, not because it
  is a problem today.
- **`_FIELDS` gained `bodies_purged` while `SCHEMA` stayed
  `arail.agent_trace/v1`** (`agent_trace.py:56`). Additive and nullable, and no
  consumer asserts an exact key set, so no reader breaks — but a v1 record shape
  grew a field. Acceptable; worth a line in `docs/agent-observability.md`.
- **Cosmetics:** `_nav.html:177`'s confirm string reads "Hold all agents? agents
  stop calling models…" (lowercase mid-sentence); the adjacent Resume button's
  title is still "Resume scheduler" though it now resumes agents too;
  `loadLegacyBodiesNotice` double-escapes `bySource` (`esc()` per entry then
  `esc()` on the join) — over-escaped, which is the safe direction.
- **`sprints/BACKLOG.md`'s conftest-split entry misdescribes the second
  concern** as "ambient defaults (tier, etc.)". It is the `.world-prompt-seen`
  onboarded-ness marker. As written the entry is harder to act on than it needs
  to be.

---

## Debt filing: verified

Every item I marked "file as debt" is in `sprints/BACKLOG.md` under
*"Buddy-front-and-center's BLOCK-review fix loop — filed as debt, not fixed"*
(commit `f9deb6e5`), each with a file:line or a concrete fix sketch: **S2, S3,
D1, D2, D3, D5, D7, F2**, W1's wall-clock test, the conftest split, and D8's
remainder. It even orders them by exposure gradient and flags the S3/QA briefing.

**Nothing I marked must-fix was quietly moved to debt.** All ten are worked in
code; S1 was *promoted* (by the operator, not the builder) from ASK to must-fix
and got both halves of its either/or.

**D7 is still correctly filed as debt, and the orchestrator's question about it
has a clean answer: only the test was wrong.** The hang was a test artifact —
`client.stream()` against a generator that never terminates on its own. The
production generator *does* end on disconnect, via ASGI task cancellation
reaching `subscribe()`'s `finally`, and the rewritten test now **asserts** that
(`_SUBSCRIBERS` returns to its pre-request length after `app_task.cancel()`),
which is strictly more than existed before. D7's actual content is different and
untouched: `_fanout`'s same-loop `QueueFull` branch (`agent_trace.py:255-258`)
removes a subscriber that is still awaiting `q.get()`, so a backpressured client
keeps an open connection and silently never receives another frame — plus the
one-snapshot-fetch-per-trace amplification. Neither is a leak and neither is
reachable without a burst, so debt is the right home. The builder's
`is_disconnected()` grep is also correct: zero hits anywhere, including the
pre-existing `/api/activity/stream` — not a gap this sprint introduced.

## Regression check on the fix loop

- Sprint suite (22 files): **303 passed, 1 skipped**, 2.3 s, no hang, no stray
  process. (The orchestrator's 316 is a slightly different file list; same
  result — zero failures.)
- 18 pre-existing suites most exposed to the fix loop's edits — `test_lab_brain`,
  `test_lab_brief`, all four airgap-toggle suites (the `_toggle_bind_is_loopback`
  delegation), `test_boot_security_scan` (the `importlib.reload` hazard),
  `test_onboarding`, `test_boot_overlay`, `portal/test_base_template_smoke`,
  `test_world_first_impression` (the conftest first-run trio),
  `test_activity_rotation`, `test_health_metrics`, `test_imports`,
  `test_agent_workflows`, `test_debt_finance_agents`: **318 passed, 1 skipped**.
- No `AERO_*` / `aerollm-api` / frozen-identifier change in the fix-loop diff.
- `/metrics` and `per_label_snapshot()` untouched in the fix-loop diff.
- 17 mutations run and reverted; every file byte-identical afterwards
  (asserted programmatically after each restore).

---

## What QA should hit first

1. **QA-BLIND-1, with two additions.** (a) Brief QA on **R5/S3** before they
   start: Buddy's dream announcement writes 160 chars of raw model output into
   `activity.jsonl`, unredacted and recorder-independent, and the
   legacy-bodies scanner will never find it because it is not a `prompt_trace`.
   A tree-grep hit there is a **real pre-existing finding**, not a false
   positive. (b) Add a variant that plants a key in `secrets.env`, makes that
   file unreadable (`chmod 000`), turns the recorder on, and greps the tree —
   that is **R1**, and QA finding it independently would be worth more than my
   reading it.
2. **The dream path end to end.** It has never run to completion in production
   (R5). Everything after `dream()`'s emit — `_recent_actions`,
   `_sync_workflow`, the returned reflection, `_dream_once`'s success branch —
   is untested-in-practice code that this fix loop switched on.
3. **QA-BLIND-2 against all seven gated sites**, plus the two Buddy lines the
   operator's decision (a) added (boot notice, dream announcement). Then the
   honest inverse: confirm what still speaks while held (researcher's 42
   diagnostic emits, browser's 8, loader/seed/forge boot notices, SRE by
   design) and judge it against the control's new copy. F16's hole is the one
   to probe deliberately: a user-defined agent that spawns a bare
   `threading.Thread` inside `start()` keeps calling models while held.
4. **The recorder-off contract on all four read paths**
   (`/api/agents/prompts`, `/api/admin/agent-lanes`,
   `/api/admin/agent-trace/{id}`, `/api/admin/agent-trace-stream`) and the
   *distinction* decision (c) created: the read-gate is **reversible** (recorder
   back on re-exposes old bodies), the purge is **permanent**. Both behaviours
   are intended; confirm the UI does not imply otherwise.
5. **The purge's concurrent-write window**, unchanged from the original review:
   `ActivityLog.emit()` takes no lock, so activity lines emitted *during* a
   purge are lost, and the docstring still claims "the exact line count never
   changes". Small, real, easy to demonstrate with a burst.
6. **The legacy notice/Purge/Keep UI on a lab that actually has legacy bodies** —
   the endpoints were tested from the start; the UI landed in this fix loop and
   has one structural test.

## Required before the PR (not before QA)

1. **R1** — `redact.py:78-80`: an existing-but-unreadable `secrets.env` must
   produce `None`, not a shape-pass-only body.
2. **R2** — `admin.html:1686-1687`: narrow the not-held branch; pin both
   branches.
3. **R6** — `tests/…:72`: restore the deleted tightening branch.
4. **R3** — pin the admission-control clause and the `${hold.in_flight}` slot.
5. **R4** — assert `"<th>Model</th>"`, not the bare word `Model`.
6. **R7's BACKLOG corrections** — S3 is live, not merely gated; the conftest
   entry's second concern is `.world-prompt-seen`.

## Still needs the operator

**W5's witness line.** Unwritten, and the builder correctly refused to write it.
It must be the operator's own words, signed, in `SPRINT.md`, after opening the
lane view on an idle lab and narrating for ten minutes without a log file or a
terminal. W1's four missing fields now render, so the screen can finally support
that test — but if W5 fails, the next sprint's first task is a rewrite of the
view, not more instrumentation.
