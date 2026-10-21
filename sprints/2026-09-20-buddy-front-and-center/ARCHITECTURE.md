# Architecture: P1 — observability + guardrails over the agents that already exist

**Date:** 2026-09-21
**Spec:** [VISION.md](./VISION.md) at commit `2884569f` · ledger decisions at `0e787477`
**Branch:** `qukaizen/arail-buddy-front-and-center` (worktree `~/ProJects/arail-buddy-wt`)
**Mode:** design

Every code claim in this document was read in this worktree on 2026-09-21. Where
VISION.md and the code disagree, the code wins and the disagreement is named in
§"Where the spec is wrong or unbuildable as scoped". That section is not
decoration — three of its nine items change what the builder must do.

---

## Restatement

The lab has four verified holes in exactly the code path the next three sprints
are about to load: Buddy's inference emits nothing, agent calls run outside the
inference semaphore, fifteen of nineteen agent modules ignore the halt flag, and
two agents write full prompt and response bodies to an unauthenticated
on-disk log by default. This sprint does not add a feature. It makes one
existing funnel — `ModelRouter.complete()` / `.stream_complete()` — record who
called it, on which brain, how fast the first token arrived, and whether the
inference slot was contended; it makes the existing halt flag actually stop
agent inference and proactive speech; it turns always-on body capture into an
off-by-default, redacted, capped flight recorder; it puts one maximus-only Admin
card over the result; and it counts slot overlap so a later sprint can decide
whether the deferred inference gateway is urgent or theoretical. No new agent
behaviour ships. The inference gateway, per-agent budgets, the typed-action
scaffold and the control room's scrub/conveyor/gauges are out, per the ledger.

**I can build the wedge without the gateway.** The funnel is genuinely single
(`router/core.py:145`, `:172`); the eleven acquisition sites all terminate there.
The gateway is not required and does not enter this design.

---

## Assumptions

Each is stated so review mode can check whether it held. A1–A4 are the ones I
could not settle by reading and that S0 exists to prove.

| # | Assumption | If wrong |
|---|---|---|
| **A1** | `asyncio.create_task` copies the calling `contextvars.Context`, so a var set around `instance.start()` reaches the whole agent loop spawned inside it (`_builtin_buddy.py:1262`, `_builtin_sre.py:508`). | The loader contract (L1 below) collapses; attribution becomes N explicit edits with no "cannot forget" layer. S0 proves or kills this. |
| **A2** | `asyncio.to_thread` copies the context into the worker thread (`_builtin_buddy.py:1569`, `_builtin_sre.py:581`, `_builtin_librarian.py:151,178`). | Every Buddy/SRE/librarian model call is unattributed; needs an explicit wrapper at each `to_thread` site. |
| **A3** | A bare `threading.Thread` does **not** copy the context (`_builtin_presence.py:117` is the only agent site). | The `spawn_thread` shim and its guard test are unnecessary. Harmless if wrong. |
| **A4** | `loop.run_in_executor` does not copy context, and **no agent path uses it** (grep: zero hits under `src/arail/agents/`). | A silent unattributed hop appears. Guarded by a static test, not by hope. |
| A5 | `DATA_DIR` (`config.py:85`) is resolved per process from `ARAIL_DATA_DIR` and never rebound in-process, so any store under it is per-World-instance by construction — the same reasoning that makes `PKB_ROOT` per-World (`pkb_index.py`). | Traces from two instances would interleave. Directly tested (F14). |
| A6 | `scheduler.halt_all_jobs()` already persists to `DATA_DIR/halt.json` and reloads lazily in a fresh process (`scheduler.py:216-282`). Verified by reading. | Restart would silently un-hold. Tested (F15). |
| A7 | **Almost** every agent-side caller of `router.complete()` already swallows broadly, so `AgentHeldError(RuntimeError)` degrades to the existing fallback path rather than crashing a loop. Verified individually — **swallows:** `deep_policy.complete_preferring_deep:223,239` (covers buddy, debt_advisor, consolidation_analyzer), `researcher._llm:262`, `browser:303`, `forge._voice:321`, `librarian_scout.draft_proposal:307`. **Does NOT swallow:** `_builtin_drafter.compose:161` lets it propagate — and that module has **no production caller in `src/`** (grep `.compose(`: zero hits outside itself), so it is unwired today. Non-agent callers (`dictionary.generate_pass:395`, `expand_term:423`, `world_routes`, `goal_parser`) also propagate, but they are `kind="system"` and are never refused. | Every swallowing site is covered. `_builtin_drafter` gets an explicit guard in S4 *before* it can be wired, and a test asserts it. Never assume a new caller swallows. |
| A8 | The operator's machine is the measurement environment (M5 Max / 36 GB, maximus). Numbers in §Performance are for that box only. | Thresholds are wrong on 16 GB; they are recorded as observations, not gates, except DE4's. |
| A9 | The portal is single-worker uvicorn (`scripts/start.sh:1036`), so a module-global ring and an `os.replace` rotation are safe — the same assumption `activity.py:92-101` already documents. | Rotation could tear a file. Same exposure as today, not new. |
| A10 | `stream_complete`'s first yielded item being a `ModelResponse` means the backend emulated the stream (`backends.py:152-163`). There is no backend that yields a terminal response first *and* streamed. Verified across all nine backends. | TTFT would be fake for some backend. This is the single most important correctness assumption in the TTFT design. |

---

## Data flow

```
                        ┌─────────────────────── ATTRIBUTION SET HERE ───────────────────────┐
                        │                                                                    │
  agents/loader.py      │  dream_daemon / job_daemon    top-level modules      portal routes  │
  start_all_auto()      │  _dream_once / _run_job       researcher/browser/    chat, /ask     │
  load_one().start()    │                               drafter/forge/         world_routes   │
        │               │        │                      librarian_scout/       dictionary     │
        │  L1           │        │  L2                  recap  │  L3                │  L3     │
        ▼               │        ▼                             ▼                    ▼         │
  agent_call(id) ───────┴────────┴─────────────────────────────┴────────────────────┘
        │                                                              system_call("world-forge") etc.
        │  contextvar  _CALL: AgentCall | None
        │
        ├─ create_task  (A1: context copied)  ──► agent loop
        ├─ to_thread    (A2: context copied)  ──► blocking model call
        ├─ spawn_thread (A3 shim: copy_context().run) ──► presence loop
        └─ subprocess   (no copy) ──► JSON protocol carries {trace_id, agent_id, brain}
                                          │
                                          ▼
                              _subprocess_runner sets _CALL, calls router
                              returns {ok, text, model, backend, tokens_used}
                                          │  (parent authors the trace)
   ┌──────────────────────────────────────┴──────────────────────────────────────┐
   │                      THE CHOKEPOINT — router/core.py                        │
   │                                                                             │
   │  complete()                          stream_complete()                      │
   │    1. ctx = agent_context.current()    1. ctx = agent_context.current()      │
   │    2. halt_gate(ctx)  ─┐               2. halt_gate(ctx)  ─┐                 │
   │    3. slot = slot_pressure()           3. slot = slot_pressure()             │
   │    4. rec_bodies = recorder_on()       4. rec_bodies = recorder_on()         │
   │    5. backend.complete(...)            5. for item in backend.stream(...):   │
   │    6. cost_tracker.track(source=…)        first str delta → ttft_ms          │
   │    7. agent_trace.record(...)              ModelResponse first → emulated    │
   │       ttft_status="non_streaming"      6. cost_tracker.track(source=…)       │
   │                       │                7. agent_trace.record(...)            │
   └───────────────────────┼────────────────────────────┼────────────────────────┘
                           │ refused                    │
                           ▼                            ▼
              AgentHeldError + trace         ┌───────────────────────────┐
              outcome="refused_halted"       │  agent_trace  (NEW)       │
                                             │  ring deque(maxlen=500)   │
                                             │  DATA_DIR/agent_traces    │
                                             │    .jsonl  (5 MB × 2)     │
                                             │  SSE fan-out (activity.py │
                                             │    call_soon_threadsafe   │
                                             │    idiom, copied not      │
                                             │    re-derived)            │
                                             └────┬──────────┬───────────┘
                                                  │          │
                       ┌──────────────────────────┘          └────────────┐
                       ▼                                                  ▼
       GET  /api/admin/agent-lanes            (404 non-maximus)   GET /api/admin/agent-trace/{id}
       GET  /api/admin/agent-trace-stream     (404 non-maximus)   POST /api/admin/agents/hold
                       │                                          POST /api/admin/flight-recorder
                       ▼
       admin.html "Agent lanes" card
                       │
   UNCHANGED, READ-ONLY, NOT DELETED:  /metrics  ·  scheduler.per_label_snapshot()
   CHANGED SHAPE:  /api/agents/prompts (metadata-only when recorder off)
                   /api/agents/status  (tokens → real usage, V7)
   STOPS WRITING BODIES:  researcher.py:246  ·  browser.py:310,375  → activity.jsonl
```

---

## Interface contracts

### 1. `arail/agent_context.py` — NEW

```python
@dataclass(frozen=True)
class AgentCall:
    agent_id: str | None      # None only for kind="system"
    kind: str                 # "agent" | "system"
    label: str                # agent_id, or the system label ("world-forge")
    trace_id: str             # 16 hex chars, minted at context entry
    brain: str | None         # "reflex"|"standard"|"deep"|None (caller hint)
    effort: str | None
    foreground: bool | None
    deep_reason_code: str | None      # verbatim from deep_policy.explain()
    deep_reason_detail: str | None
    out_of_process: bool = False

def current() -> AgentCall | None                 # never raises
@contextmanager
def agent_call(agent_id, *, brain=None, effort=None, foreground=None,
               deep_reason=None, trace_id=None) -> Iterator[AgentCall]
@contextmanager
def system_call(label, *, trace_id=None) -> Iterator[AgentCall]
def spawn_thread(target, *a, **kw) -> threading.Thread   # copy_context().run wrapper
def to_subprocess_payload() -> dict                      # {trace_id, agent_id, brain, effort}
def from_subprocess_payload(d: dict) -> AbstractContextManager
def billing_source(ctx: AgentCall | None) -> str
```

**Promises.** `current()` returns the innermost active record or `None`; it never
raises and never allocates. `agent_call` is reentrant — an inner `agent_call`
with the same `agent_id` reuses the outer `trace_id` (so a multi-step agent
decision shares one id) and a *different* `agent_id` nests (the innermost wins,
and the record carries `parent_agent_id`). Both CMs reset their token in
`finally`, so a raising body cannot leak attribution into a sibling task.

**Requires.** Nothing. Callers may pass anything; a non-string `agent_id` is
coerced with `str()` and truncated at 64 chars.

**Bad input.** `agent_id=""` or `None` in `agent_call` → treated as an
*unattributed* call, i.e. the CM sets nothing and `current()` still returns the
outer value. It does **not** invent a label. `agent_id` containing `/`, `..`,
whitespace or control characters → sanitised to `[a-z0-9_.-]{1,64}` lowercased,
because the id reaches a JSON file, a `billing_source` key and a DOM attribute.

**`billing_source()` — the W2 contract.**

| Context | `billing_source` |
|---|---|
| `kind="agent"`, id `buddy` | `agent:buddy` |
| `kind="system"`, label `world-forge` | `sys:world-forge` |
| no context, router built with `billing_source="ui"` | `ui` (unchanged) |
| no context, router built with `billing_source="agent"` | **`unattributed`** |

The last row is load-bearing. An unattributed call is **never** recorded as
`agent`, never as a plausible agent id, and never dropped. It also captures
`call_site` — `f"{frame.f_globals['__name__']}:{frame.f_lineno}"` from
`sys._getframe(2)`, one frame, ~1 µs, no `traceback.format_stack()`. The Admin
view renders a permanent **Unattributed** lane listing those call sites whenever
the count is > 0. This is DE2's instrument.

### 2. `arail/agent_trace.py` — NEW

```python
SCHEMA = "arail.agent_trace/v1"

def record(**fields) -> None       # never raises, never blocks on the caller's behalf
def ring(n: int = 200) -> list[dict]
def lanes_snapshot() -> dict       # what the Admin endpoint serialises
def stats() -> dict                # {"recorded": n, "dropped_writes": n, "overlap_pct": f}
async def subscribe() -> AsyncGenerator[dict, None]
def _reset_for_tests() -> None
```

**Record shape (`arail.agent_trace/v1`).** Every key is always present; absent
data is `null`, never `0`, never `""`, never `"n/a"`.

```
schema, trace_id, ts, iso
agent_id, parent_agent_id, kind, attribution, label, call_site
model, backend, provider, entry_id
brain, effort, foreground
deep_reason_code, deep_reason_detail          # verbatim, never re-worded
ttft_ms, ttft_status
prefill_ms, prefill_source
tokens_in, tokens_out, latency_ms
streamed, out_of_process
slot: {capacity, in_flight, pending, held_by_other}
halted
outcome, error_class
bodies: null | {prompt, response, truncated, redactions}
```

**Promises.** `record()` appends to the in-memory ring **first**, then fans out
to SSE subscribers, then attempts the disk append — in that order, so a disk
failure cannot cost the live view. It has no `raise` path: the whole body is
inside `try/except Exception`, which increments `dropped_writes` and logs at most
once per 60 s through stdlib `logging` — **never** through `activity_log`, which
would recurse into the same disk. `dropped_writes` is surfaced in
`lanes_snapshot()`, so loss is visible rather than silent.

**Requires.** Nothing. Unknown kwargs are dropped (forward-compat with a v2
producer reading into a v1 store is not supported; the `schema` string is how a
reader detects that).

**Bounds.** Ring `deque(maxlen=int(ARAIL_TRACE_RING or 500))`, clamped [50, 5000].
Disk: `DATA_DIR/agent_traces.jsonl`, append one line per record, size checked
every 64 records, `os.replace` to `.jsonl.1` above 5 MB — two files, hard 10 MB
ceiling, the same idiom as `activity.py:92-101`. `ARAIL_TRACE_PERSIST=0` disables
the disk half and keeps the ring. No time-based retention: two bounded files are
simpler than a reaper and the operator can delete them.

**Per-instance isolation.** The path resolves `DATA_DIR` *lazily, per call* (not
a module constant, unlike `activity.py:22` — that constant is why activity tests
need gymnastics). Nothing in this module enumerates `lab/instances/`; nothing
aggregates across roots. A test asserts both (F14).

### 3. `ModelRouter.complete` / `.stream_complete` — MODIFIED (`router/core.py`)

**Preconditions unchanged.** Signatures unchanged. Return types unchanged.

**New postconditions.**
1. Exactly one `agent_trace.record()` per call, including on failure
   (`outcome="error"`, `error_class`) and on refusal
   (`outcome="refused_halted"`, no backend call made).
2. `cost_tracker.track(source=...)` receives `agent_context.billing_source(ctx)`
   instead of `self.billing_source`, unless `self.billing_source == "ui"` (chat
   keeps its bucket).
3. `halt_gate(ctx)` is evaluated **before** the backend call. When it refuses,
   the backend is not touched.
4. `stream_complete` yields exactly what it yields today. TTFT measurement adds
   no item and swallows no item.

**Bad input.** Unchanged — the backend still owns prompt validation.

**On exception from the backend:** the trace is recorded in a `finally`-adjacent
`except` and the exception re-raised unchanged. Callers' existing `except
Exception` behaviour is preserved bit for bit.

**TTFT rules (the honesty contract).**

| Situation | `ttft_ms` | `ttft_status` |
|---|---|---|
| `stream_complete`, first yielded item is a non-empty `str` | measured from `perf_counter()` before the generator was entered | `measured` |
| `stream_complete`, first yielded item is a `ModelResponse` | `null` | `emulated_stream` |
| `stream_complete`, generator yielded only empty strings then a response | `null` | `no_tokens` |
| `stream_complete` raised before any item | `null` | `error` |
| `complete()` — any backend | `null` | `non_streaming` |

`ttft_ms` is **never** derived from `latency_ms`. `prefill_ms` is a *separate*
field, populated only where a backend server reports it (Ollama
`prompt_eval_duration`), always carrying `prefill_source="server_reported"`, and
the endpoint contract states a client MUST NOT render it as TTFT. This is the
direct answer to VISION note 3: two names, two meanings, one provenance field
each — not `mlx_backend.py:261`'s renamed-but-aliased situation.

### 4. `halt_gate` / `speech_gate` — NEW (in `agent_context.py`)

```python
HOLD_EXEMPT_SPEAKERS = frozenset({"sre"})   # ledger OQ3, with the reason inline

class AgentHeldError(RuntimeError): ...

def halt_gate(ctx) -> None       # raises AgentHeldError, or returns
def speech_gate(agent_id) -> bool
def hold_state() -> dict         # {held, changed_at, exempt_speakers, in_flight}
```

`halt_gate` raises **only** when `scheduler.jobs_halted()` and
`ctx is not None and ctx.kind == "agent"`. `kind="system"`, `ui`, and
unattributed calls pass — holding agents must not break the operator's own chat
turn while he is holding them. `speech_gate(agent_id)` returns `False` when held
unless `agent_id in HOLD_EXEMPT_SPEAKERS`.

**Semantics of "hold", stated so the UI cannot overclaim:** hold is **admission
control, not cancellation**. Backends are blocking C/HTTP calls with no
cancellation hook (`AeroLLMBackend` submits to a dedicated executor thread and
`.result()`s it; MLX's `_stream_generate` is a sync generator). An inference
already inside a backend when the switch flips **runs to completion**. The
control therefore reads:

> **Hold all agents** — agents stop calling models and stop speaking. Calls
> already running finish (N in flight). The SRE crash watcher keeps watching and
> may still post a plain, non-model alert. This World only; survives a restart.

Every clause of that string is true against the code. It is a test fixture
(F17): the copy and the behaviour are asserted together so they cannot drift.

### 5. `arail/redact.py` — NEW

```python
REDACTED = "***REDACTED***"
def redact(text: str) -> tuple[str, int]        # (redacted, n_redactions)
def capture_body(prompt, response) -> dict | None
```

`capture_body` is **the only function in the codebase permitted to put a body
into a trace record.** It cannot return unredacted text: it either returns a
dict whose strings have passed both redaction passes, or `None`. Any exception
inside it is caught and returns `None` — **fail-closed on bodies, fail-open on
the metadata record.** That is the structural enforcement of "secrets are never
logged", rather than a rule someone must remember.

Two passes, both always applied, in this order:

1. **Known-value pass.** Replace every value from `_read_secrets()` (the
   `secrets.env` parser) plus `ARAIL_PASSWORD` and
   `OPEN_NOTEBOOK_ENCRYPTION_KEY`, length ≥ 8, with `REDACTED`. Lifted from
   `portal/services/opencode.py:97` — the existing proven pattern. Values cached
   and refreshed on `secrets.env` mtime change.
2. **Shape pass.** Catches secrets that were never in `secrets.env` — which is
   exactly what W4's QA test plants. Patterns: `sk-[A-Za-z0-9_-]{16,}`,
   `hf_[A-Za-z0-9]{20,}`, `nvapi-\S{16,}`, `(ghp|gho|github_pat)_\S{16,}`,
   `AKIA[0-9A-Z]{16}`, `Bearer\s+[A-Za-z0-9._\-]{20,}`, and
   `(?i)\b(api[_-]?key|token|secret|password|passphrase)\b\s*[:=]\s*\S{8,}`.

**Order matters: redact, then truncate.** Truncating first could split a key so
the shape pass no longer matches its head fragment. Caps after redaction:
prompt ≤ 2000 chars, response ≤ 1000 — a **reduction** from today's 3000/2000
(`researcher.py:251-252`).

### 6. Admin endpoints — NEW

All four are gated by `_require_surface("admin")` (`app.py:2213`), which 404s on
minimalist exactly like `/admin`, `/api/admin/security` and the tuning routes.
Source of truth is `_TIER_SURFACES` (`app.py:190`) — unmodified.

| Endpoint | Method | Contract |
|---|---|---|
| `/api/admin/agent-lanes` | GET | `lanes_snapshot()`. Added to `FAST_PATH_PREFIXES` so it never queues behind an inference. |
| `/api/admin/agent-trace-stream` | GET | SSE, one frame per new trace. **Deliberately a different prefix** from the snapshot: `FAST_PATH_PREFIXES` matches with `startswith`, so `/api/admin/agent-lanes/stream` would be fast-path-timed and inflate `fast_path_ms` p95 — the exact bug `_METRICS_EXCLUDED_PREFIXES` (`app.py:524`) documents. The awkward name is the fix; the reason goes in a code comment. |
| `/api/admin/agent-trace/{trace_id}` | GET | One record, the "why?" drill-in. `404` on unknown id. Bodies present only if captured. |
| `/api/admin/agents/hold` | POST | `{hold: bool}` → `halt_all_jobs()` / `resume_all_jobs()`. CSRF comes free from the existing `local_trust_boundary` middleware (`app.py:683`) — it rejects cross-site/cross-origin mutating methods. No new CSRF code; and **no GET may mutate**. |
| `/api/admin/flight-recorder` | POST | `{enabled: bool}` → writes `DATA_DIR/flight_recorder.json`. Admin-only, therefore a minimalist lab can never turn bodies on. |

`lanes_snapshot()` shape:

```json
{"schema": "arail.agent_lanes/v1",
 "lanes": [{"id":"buddy","display":"Buddy","group":"builtin",
            "calls":12,"last":{...trace...},
            "brain":"reflex","effort":"reflex",
            "ttft_ms":412.0,"ttft_status":"measured",
            "tokens_out":1180,
            "deep_reason_code":"deferred_now",
            "deep_reason_detail":"operator present (interactive profile) — …",
            "empty_reason": null}],
 "unattributed": {"calls": 0, "call_sites": []},
 "hold": {...hold_state()...},
 "recorder": {"enabled": false, "changed_at": null},
 "slot": {"capacity":1,"in_flight":0,"pending":0,"overlap_pct":3.2,"samples":214},
 "drops": {"dropped_writes": 0},
 "window": {"ring_size": 500, "recorded": 214}}
```

**Roster (ledger OQ4).** A code constant `FIXED_LANES`, **not**
`/api/agents/status`'s hardcoded five (two of which — `curator`, `browser` — are
helper modules, not ticking agents; see `loader.py`'s `_SKILLS_ONLY` docstring):

```
buddy · researcher · browser · curator · sre · librarian · presence
debt_advisor · consolidation_analyzer · drafter · forge
```

plus `sys:*` lanes for the four non-agent callers, plus **one** generic
`user-defined` lane aggregating every loader agent id outside `FIXED_LANES`,
plus the mandatory `unattributed` lane. A lane with zero traces renders with an
`empty_reason`: `"no calls yet this session"`, or — for `sre`, `presence`,
`librarian` — `"does not call a model"`, which is *true today* and teaches the
operator something instead of showing a dead lane. Nothing is ever hidden
(VISION note 9).

### 7. `GET /api/agents/prompts` — CHANGED SHAPE (breaking)

```json
{"recorder": {"enabled": false, "reason": "off_by_default"},
 "empty_state": "flight recorder off — flip to capture prompt bodies",
 "traces": [{"ts":…,"source":"researcher","trace_id":"…",
             "model":…,"backend":…,"tokens_out":…,"latency_ms":…,
             "ttft_ms":null,"ttft_status":"non_streaming"}]}
```

When the recorder is off, `prompt` and `response` keys are **absent**, not empty
strings — a client cannot render `''` as a body. `agents.html:1141-1143` reads
`prompt_trace.prompt`/`.response` directly and **must be updated in the same
commit**; the empty-state string is the ledger's copy verbatim.

Exposure is a strict reduction: this endpoint is every-tier and unauthenticated
today and serves up to 5000 chars of bodies. After this sprint it serves bodies
only when an admin-only toggle is on, which a minimalist lab cannot flip. No new
surface widens what is already exposed (VISION note 11).

### 8. `GET /api/agents/status` — V7 FIX

`tokens` today sums `prompt_trace.max_tokens` — the *requested ceiling*
(`app.py:5088`). Replace with real usage, sourced from the trace ring
(`sum(tokens_out for t in ring if t.agent_id == x)`), falling back to
`prompt_trace.tokens_out` for compatibility. Emit both `tokens_out` (new,
correct) and `tokens` (deprecated alias, now carrying the *corrected* value for
one release, because `agents.html` reads it). Also relabel the figure in the
template — the number and its label must agree (truth-in-UI, 2026-07-23).

### 9. Subprocess protocol — EXTENDED (`skills/goal_parser/`)

Stdin gains `{"trace_id","agent_id","brain","effort"}`; stdout gains
`{"model","backend","tokens_used"}`. The child sets `_CALL` from the payload so
its own `cost_tracker.track` is attributed. **The child does not write the trace
file** — the parent authors one record with `out_of_process: true`,
`ttft_status: "non_streaming"`, so there is exactly one trace writer per
process and no interleaved appends. DE2's known leak is therefore *closed*, not
merely labelled.

### 10. `scheduler.slot_pressure()` — NEW (`portal/scheduler.py`)

```python
def slot_pressure() -> dict   # {"capacity","in_flight","pending"} — no percentiles
```

`snapshot()` sorts up to 256 floats per label per call (`_percentile` →
`sorted()`), which is ~50–200 µs and the wrong thing to put on every inference.
`slot_pressure()` reads three module globals. `snapshot()`,
`per_label_snapshot()` and `/metrics` are **untouched** (V9 / VISION note 8) — a
test asserts `/metrics` still renders its existing `arail_inference_*` lines.

At the chokepoint, `held_by_other = in_flight > 0`, evaluated before the backend
call. Agent calls are never *in* the slot (V3), so any non-zero `in_flight` is
someone else. **Known imprecision, stated not hidden:** `_INFLIGHT` is a plain
int mutated from the event-loop thread and read here from a `to_thread` worker,
so an individual sample may be stale by microseconds. That is acceptable for a
percentage over hundreds of samples and a lock on the hot path for a metric
would be the wrong trade. `overlap_pct` is DE1's evidence.

---

## Attribution: which layer sets the context, and why three

**L1 — the loader contract (the "cannot be forgotten" layer).**
`agents/loader.py` wraps every loaded instance's `start()`, `tick()`, `dream()`
and `ask()` in `agent_call(folder_name)` before invoking. Because
`create_task` copies the context (A1), a user-defined agent that spawns its loop
*inside* `start()` is attributed for the life of that loop, for free. This is
the single answer to "a new agent cannot forget it", and it is why the loader is
the right place rather than each agent. Documented in `docs/agents.md` as part of
the loader contract, alongside the hold contract.

**L2 — daemons that call agents from outside `start()`.**
`dream_daemon._dream_once` and `job_daemon._run_job` set the var per agent/job.
Without this, a nightly Buddy dream would be attributed to whatever context the
daemon's own task carries.

**L3 — the modules the loader never touches.** `researcher`, `browser`,
`curator`, `librarian_scout`, `_builtin_drafter`, `forge`, `recap/router_adapter`
are wired directly at the top level (`loader.py`'s `_SKILLS_ONLY` docstring
explains why), and `world_routes`, `dictionary`, `skills/goal_parser` are not
agents at all. Each gets one explicit `with agent_call(...)` / `system_call(...)`
at its own model-acquisition helper. **Eleven edits.** The reason this is
acceptable where the gateway's eleven edits were not: each is an additive
wrapper with no change to control flow or to what the callee receives, and a
missed one produces a loud **Unattributed** lane with the exact
`module:lineno` printed on screen — not a wrong number.

**Why not router attributes.** `binding.py:112` already documents the hazard:
"Rewrap the shared resident backend in a fresh router so per-call attribution
(tab/entry) never mutates the deep_policy singleton." Routers *are* cached and
shared — `deep_policy._deep_router`, `deep_policy._fast_router`,
`researcher._router_cache:718`, `_builtin_drafter._router:108`. Stamping
`agent_id` on a shared router is an aliasing bug and a race.

**Why not a new parameter.** Adding `agent=` to `complete()`/`stream_complete()`
means changing nine backend signatures and eleven call sites — that is the
gateway, which the ledger cut.

**Why a contextvar is safe here.** Prior art in this exact file:
`costs._recap_depth_tls` is set by `recap/router_adapter.py:115` and read inside
`cost_tracker.track()` — the same variable shape, the same reader, the same
threading exposure. Zero new concepts.

**The legacy cost bucket — W2 is unmeasurable without this.**
`calls_by_source` is *persisted* in `costs.json` (`costs.py:231,275`), so the
accumulated `"agent"` key survives forever and W2's "0 calls in a generic
`agent` bucket" can never be satisfied by new code alone. On first `_load()`
after upgrade, rename `"agent"` → `"agent:pre-p1-legacy"` (idempotent, because
nothing writes the bare key again). W2 is then a real assertion.

---

## Hot-path cost budget

DE4's kill threshold is **> 5 ms on agent-call p95** or **> 2 % on
`fast_path_ms` p95**. I set the **design budget at one fifth of that: ≤ 1.0 ms
p95 per inference, ≤ 0.5 % on the portal fast path**, so there is headroom
before the disconfirming evidence fires.

Accounting for one `record()`:

| Step | Cost |
|---|---|
| `current()` — contextvar get | < 1 µs |
| `slot_pressure()` — three global reads | < 1 µs |
| `recorder_on()` — cached bool | < 1 µs |
| `_getframe(2)` for `call_site`, unattributed only | ~1 µs |
| `deque.append` | < 1 µs |
| SSE fan-out, 0–1 subscriber | ~5 µs |
| `json.dumps` of ~28 small fields | ~10 µs |
| `open(…, "a")` + one `write` + close | ~30–80 µs (APFS) |
| rotation `stat()`, every 64th record | amortised < 1 µs |
| `redact()` + truncate, **only when the recorder is on** | ~50–300 µs on 3 KB |
| **Total, recorder off** | **~50–100 µs** |

For scale: `cost_tracker.track()` already `json.dumps`es up to 500 history rows
and `write_text`s the whole file on *every* call (`costs.py:411`). The trace
write is an order of magnitude cheaper than what is already on this path. The
budget is comfortable.

**When the trace write fails, it must never fail or slow an inference.**
Enforced three ways: (1) `record()` has no `raise` path — the entire body is
inside one `except Exception`; (2) the ring append happens *before* the disk
write, so disk failure still leaves the operator's live view working; (3) the
error log goes to stdlib `logging` at most once per 60 s, never to
`activity_log` — which writes to the same disk and would recurse. Loss is
counted in `dropped_writes` and rendered in the Admin card, so a full disk is a
visible number, not a silence. Tested against a read-only `DATA_DIR` (F1).

---

## Failure modes

Every row has a test in §Test strategy. Review mode should cross-reference by id.

| # | Failure | Detection | Recovery |
|---|---|---|---|
| **F1** | Trace disk write fails (permissions, disk full, path removed mid-run). | `record()`'s `except` increments `dropped_writes`; surfaced in `lanes_snapshot().drops` and on the Admin card. | Ring keeps serving the live view. No exception reaches the caller. Rate-limited stdlib warning. Inference unaffected. |
| **F2** | Disk full → rotation `os.replace` fails. | Same counter; the size check is inside the same `try`. | Writes stop, ring continues, counter climbs. Bounded by design (10 MB) so this should be a foreign cause, not ours. |
| **F3** | Attribution lost across a thread hop (`to_thread` behaviour differs, a new `run_in_executor`/bare `Thread` appears). | The call records `attribution="unattributed"` with `call_site`, and the Admin **Unattributed** lane appears with the module:lineno. | Static test forbids `run_in_executor` and bare `threading.Thread(` under `src/arail/agents/`; `spawn_thread` shim for the one legitimate site. Never defaults to an agent id. |
| **F4** | A new agent author forgets to set the context. | L1 loader wrapper means they cannot, for anything reached through `start`/`tick`/`dream`/`ask`. Anything else lands in the Unattributed lane by name. | No silent default. Documented in `docs/agents.md`. |
| **F5** | Two agents share a cached router and attribution crosses. | Contextvar is call-scoped, not router-scoped; a test runs two concurrent tasks through *one* cached router and asserts each trace carries its own id. | By construction. This is why router attributes were rejected. |
| **F6** | TTFT is a fake number derived from total latency on a non-streaming backend. | The first yielded item is a `ModelResponse` → `ttft_status="emulated_stream"`, `ttft_ms=null`. Tested per backend class with a fake backend that emulates. | `ttft_ms` is `null`; JS test asserts the renderer prints the status, never a number, for every non-`measured` status. |
| **F7** | A halted agent still calls a model. | `halt_gate` at the chokepoint — one place, cannot be bypassed by a code path that reaches a model, including user-defined agents and the subprocess (the child re-checks). | `AgentHeldError`; trace with `outcome="refused_halted"`; W3 counts new admitted traces. |
| **F8** | A halted agent raises `AgentHeldError` into its own loop and crashes. | Per-module test that each model-calling agent survives a refusal and reaches its fallback. | A7 verified six of seven agent callers already swallow. `complete_preferring_deep` gets an explicit early return so it does not attempt deep-then-fast and emit two refusals. **`_builtin_drafter.compose:161` does *not* swallow** — it gets an explicit `AgentHeldError` branch returning `Draft(text="", metadata={"error":"held"})`, mirroring its existing `no router available` path. It is unwired today, which is exactly why the guard must land *before* someone wires it. |
| **F9** | A halted agent still **speaks** (the operator wanted silence, got chatter). | `speech_gate` in buddy / librarian / presence / debt_advisor / consolidation_analyzer proactive emit paths. `sre` is the documented exemption. | UI copy states the exemption; F17 asserts copy and behaviour together. |
| **F10** | Recorder toggled mid-request → a body is captured that the operator thought was off, or vice-versa. | Defined and tested in both directions: the flag is **latched at call start**. off→on during a call does **not** capture it; on→off during a call **does** capture it. | Documented behaviour, two tests. Latching at start is the safer half (turning it on never retroactively captures in-flight work). |
| **F11** | A secret reaches disk in a captured body. | `capture_body` is the only path; both redaction passes always run; `redactions` count in the record. QA plants a key-shaped string and greps the whole `DATA_DIR` tree. | Fail-closed: any exception in `capture_body` returns `None` and the body is dropped, metadata kept. |
| **F12** | Bodies **already** on disk in `activity.jsonl` from before this sprint. | One-shot scan on first boot after upgrade: if any `prompt_trace.prompt` is present in `activity.jsonl` or `.jsonl.1`, emit one warn line + a dismissible Admin notice with the count. | Operator-initiated `[Purge]` streams the file, strips `prompt`/`response`, stamps `body_purged: true`, writes a temp file in the same dir, `os.replace`. **Never automatic** — silently rewriting the operator's log is worse than the disclosure. `[Keep]` is honoured and remembered. |
| **F13** | New GET endpoints widen the unauthenticated surface. | All four admin endpoints are `_require_surface("admin")`. Test asserts 404 on minimalist for each. `/api/agents/prompts` returns strictly less than today. | Nothing new is readable that was not readable before; bodies now need an admin-only flip. |
| **F14** | Two World instances running concurrently; traces or hold state leak between them. | `DATA_DIR` resolved lazily per call; module-global ring is per process. Test sets `ARAIL_DATA_DIR` to two temp roots and asserts `agent_traces.jsonl`, `halt.json` and `flight_recorder.json` land in the right one and that no code path globs `lab/instances/*/data/`. | No cross-World aggregate exists, deliberately (VISION note 10). Hold is per-instance and the UI says "this World only". |
| **F15** | Portal restart mid-hold silently un-holds. | `scheduler.halt_all_jobs()` already persists `halt.json` and `_load_halt_locked()` re-reads it on first query in a fresh process. Test resets `_halt_loaded` and asserts the gate is still closed. | Already correct (A6); no new persistence. The flight-recorder flag uses the same idiom. |
| **F16** | A user-defined loader agent ignores the contract (never sets a context, never checks halt, spawns a bare thread). | It cannot ignore halt — the chokepoint refuses. It cannot escape attribution for anything reached via `start`/`tick`/`dream`/`ask` (L1). If it spawns a bare `Thread` inside a method the loader did not wrap, its calls appear in the Unattributed lane by `module:lineno`. | Degrades to visible, never to a wrong agent id. |
| **F17** | The switch's UI copy drifts from what the code does (claims in-flight work stops, or hides the SRE exemption). | One test asserts the rendered control string *and* the three behaviours it claims (inference refused · proactive speech silenced · SRE template alert still allowed) in the same test body. | Copy and behaviour cannot diverge without a red test. |
| **F18** | Trace volume evicts the operator's activity history. | The trace store is separate; nothing new is written to `activity_log`. Test asserts a burst of 1000 traces leaves `activity_log.recent(200)` untouched. | This is the whole reason for a third store (VISION note 7). |
| **F19** | The chat tab regresses (it shares the chokepoint). | `billing_source == "ui"` short-circuits the attribution rewrite; chat's `source="ui"` bucket is byte-identical. Existing chat streaming tests must pass unchanged. | Regression suite; `ui` is never rewritten. |
| **F20** | `ARAIL_AGENT_STREAM_FAST` (S3's agent streaming) changes Buddy's output or breaks on an Ollama version without `stream:true` on `/api/chat`. | Unconditional `except Exception` → fall back to `complete()`. Test asserts the joined stream text equals what `complete()` returns for a fake backend. | Flip the env default to `0` and Buddy's TTFT becomes an honest `n/a`. One env var, not a revert. |

---

## Test strategy

Mapped to W1–W5. QA's allocation for this sprint tilts to security + regression
per VISION note 12; the tilt must be recorded in `SPRINT.md`, not silently
applied.

### Authored blind / held out (QA's standing rule from the ingress-spine sprint)

The builder **must not pre-write** these three. QA authors them from the
contracts in this document only, without reading the implementation:

- **QA-BLIND-1 (W4, security).** Plant a key-shaped string (not present in
  `secrets.env`) into an agent prompt. With the recorder **off**: assert zero
  `prompt`/`response` keys anywhere under `DATA_DIR`. With it **on**: assert the
  planted string appears nowhere in the tree and `redactions >= 1`. Grep the
  whole tree, not just the file you expect.
- **QA-BLIND-2 (W3, security).** Halt, then drive every one of the eleven
  `FIXED_LANES` agents' model paths and every proactive speech path. Assert zero
  new traces with `outcome="ok"` and `kind="agent"`, zero new proactive lines
  from the five gated speakers, and that an SRE template alert **still fires**.
- **QA-BLIND-3 (W2, correctness).** Drive a realistic session (chat turn,
  Buddy line, researcher call, dictionary lookup, world forge, goal parse).
  Assert `calls_by_source` has ≥ 2 `agent:*` keys, exactly `0` under the bare
  key `agent`, and `unattributed == 0`. A non-zero `unattributed` is a **fail
  with the printed call sites as the bug report** — that is DE2 firing.

### Unit

- `agent_context`: reentrancy (same id reuses `trace_id`; different id nests and
  records `parent_agent_id`); `finally` reset on a raising body; sanitisation of
  hostile `agent_id` (`../`, control chars, 200 chars, `""`, `None`);
  `billing_source()` table exhaustively, including the `unattributed` row.
- `agent_trace.record()`: never raises with `DATA_DIR` read-only (**F1**); ring
  bound honoured; `dropped_writes` increments and is exposed; rotation at 5 MB
  produces exactly two files (**F2**); schema string present on every record;
  every documented key present with `null` rather than absent.
- TTFT truth table, one test per row (**F6**), using fake backends: a streaming
  one, one that emulates via `BaseBackend.stream_complete`, one that yields only
  empty strings, one that raises. Assert `ttft_ms is None` for all but the first
  and that no code path can produce `ttft_ms == latency_ms`.
- `redact()`: each shape pattern; the known-value pass; ordering (a key spanning
  the truncation boundary is still redacted — **redact-then-truncate**);
  `capture_body` returns `None` when the redactor raises (**F11**).
- `halt_gate`: refuses `kind="agent"`, passes `system`/`ui`/`None`;
  `speech_gate` exempts `sre` only.
- `slot_pressure()` returns the three keys and computes no percentile (assert by
  patching `_percentile` to raise and calling `slot_pressure()`).
- Legacy `calls_by_source["agent"]` → `"agent:pre-p1-legacy"` migration, and
  idempotence across two loads.

### Integration

- **W1 — seven fields, 3/3 calls.** Drive a Buddy proactive line, a researcher
  LLM call and a dictionary/librarian call against fake backends. Assert each
  produces exactly one trace whose seven W1 fields are non-null, *or* carry the
  documented explicit `n/a` (`ttft_status != "measured"` with `ttft_ms is None`).
  A blank fails. `deep_reason_detail` must be byte-identical to
  `deep_policy.explain()`'s string — assert equality against a live call to
  `explain()`, not against a hardcoded copy, so the two cannot drift.
- **W1's "within 2 s", tested deterministically.** Do **not** sleep and look.
  Three assertions:
  1. **Structural:** the path is push-based end to end. Assert `record()` has
     placed the event on a subscriber's `asyncio.Queue` **before it returns**
     (no scheduler round-trip, no poll interval) — the same property
     `activity.py:107-139` relies on, including the
     `call_soon_threadsafe` handover for records emitted from a `to_thread`
     worker. A test emits from a foreign thread and asserts the subscriber wakes.
  2. **No-polling guard:** a static assertion that the Admin card's JS uses the
     SSE endpoint and contains no `setInterval` refresh for lane data — because
     a poll interval is the only way this path could exceed 2 s.
  3. **One wall-clock test**, the only timing-sensitive test in the suite:
     `TestClient` reads the SSE stream, a trace is recorded, assert the frame
     arrives in `< 2000 ms` by `perf_counter`. Marked `@pytest.mark.timing` so a
     loaded CI box can be diagnosed rather than guessed at.
- Attribution propagation across all four hops (**A1–A4**, **F3**):
  `create_task`, `to_thread`, `spawn_thread`, subprocess round-trip. This is S0
  and it runs first.
- Two concurrent tasks through one *cached* router keep separate attribution
  (**F5**).
- Recorder toggled mid-request, both directions (**F10**).
- Two `ARAIL_DATA_DIR` roots: traces, hold and recorder state stay put; no glob
  of `lab/instances/` exists (**F14**).
- Restart mid-hold: reset module state, assert still held (**F15**).
- Per-module halt survival: each of the eleven model-calling agents survives
  `AgentHeldError` and reaches its fallback (**F8**).
- Legacy-bodies notice: seed an `activity.jsonl` containing bodies, assert the
  notice appears with the right count, assert `[Purge]` strips bodies and
  preserves every other field and line count, assert `[Keep]` is remembered and
  the notice does not return (**F12**).

### Security

- All four admin endpoints 404 on minimalist (**F13**) — parameterised over the
  endpoint list so a fifth endpoint added without a gate fails the test.
- `/api/agents/prompts` on minimalist with the recorder flag file manually
  planted: still no bodies (the toggle is admin-only, so this asserts the
  structural claim rather than the UI path).
- POST endpoints reject `Sec-Fetch-Site: cross-site` and a mismatched `Origin`
  (the `local_trust_boundary` contract) — asserts the CSRF story is real, not
  assumed.
- No new `GET` mutates state (static check over the new route decorators).
- `BIND_ADDR` non-loopback **and** recorder on → assert a warn-level activity
  line and an Admin banner naming the combination. This is a label, not auth.
- **Trace endpoint payloads never contain a body when the recorder is off** —
  asserted at the JSON level, checking key *absence*, not empty strings.

### Regression

- `/metrics` still renders its existing `arail_inference_*` lines and
  `per_label_snapshot()` output is unchanged (V9 / note 8 — nobody deletes a
  working surface).
- Chat streaming: existing chat tests pass unchanged; `calls_by_source["ui"]`
  increments exactly as before (**F19**).
- A burst of 1000 traces leaves `activity_log.recent(200)` untouched (**F18**).
- `agents.html` renders with the new `/api/agents/prompts` shape and with the new
  `tokens_out` key; the Prompt Inspector shows the ledger's empty-state string.
- `deep_policy.complete_preferring_deep` returns the same string with
  `ARAIL_AGENT_STREAM_FAST` on and off, for a fake backend (**F20**).
- Known-failing-on-main tests stay known-failing and are not "fixed" by this
  sprint: `test_notice_byte_identical_to_sibling_when_available`,
  `test_backends_raises_on_sentinel_before_any_load`, and (fresh worktree only)
  `test_cross_link_audit_all_internal_links_resolve`.

### Performance (DE4's gate)

- Microbench: 2000 `record()` calls with the recorder off, assert p95 added time
  **< 1.0 ms** (design budget) and report the number; DE4 kills at 5 ms.
- `fast_path_ms` p95 before/after over a scripted portal session, assert
  **< 0.5 %** delta; DE4 kills at 2 %.
- Report `overlap_pct` and its sample count after a scripted session — this is
  DE1's instrument being exercised, not a gate.

### W5 — the witness test

Not automatable and must not be faked. The operator opens the lane view on an
otherwise idle lab, narrates for 10 minutes without a log file or terminal, and
records pass/fail in `SPRINT.md` in his own words, signed. QA's job is to make
sure the line exists and is his, not to write it.

---

## Where the spec is wrong or unbuildable as scoped

Read before building. Items 1–3 change the work.

1. **Instrumenting `stream_complete` yields zero agent TTFT.** V6 says TTFT is
   missing on the agent path and prescribes a first-chunk timestamp in
   `stream_complete`. But the only production caller of `router.stream_complete`
   is `portal/app.py:7608` — the chat tab (grep: `mlx_openai_server.py` and
   `binding.py`'s proxy are the only others). **No agent calls it.** So the
   prescribed fix alone makes W1's TTFT `n/a` on 3/3 calls — technically a pass
   under W1's letter, and vacuous. To make "real TTFT on the agent path"
   non-vacuous, **one** agent path must stream: the **fast branch** of
   `deep_policy.complete_preferring_deep` (which serves Buddy, debt_advisor and
   consolidation_analyzer) consumes `stream_complete` and joins the deltas,
   returning the identical string. Tier-0 resolves to `OllamaNativeBackend`
   (`registry/store.py:203-225`), whose `stream_complete` yields real deltas
   (`backends.py:2118-2132`). Gated `ARAIL_AGENT_STREAM_FAST`, default on,
   unconditional fallback to `complete()`. **Operator question below.**
2. **`AeroLLMBackend` has no `stream_complete` at all** (`backends.py:1503-1888`
   defines `complete` and `health_check` only). It inherits
   `BaseBackend.stream_complete`, which is `yield self.complete(...)`. So Buddy's
   *deep brain* — the maximus differentiator and the operator's preferred voice
   under D17 — can **never** report a TTFT. Honest `emulated_stream` forever,
   until QueueLLM ships streaming through `aerollm_api`. The operator should know
   his headline metric is unavailable on his preferred brain; the lane must say
   so in words, not with a blank.
3. **W2 is unmeasurable as written.** `calls_by_source` is persisted
   (`costs.py:231,275,354`), so the accumulated `"agent"` bucket never returns to
   zero no matter what new code does. Needs the one-time legacy-key rename
   (designed above), and W2 should be read as "0 *new* calls in a generic bucket".
4. **V4's remedy is cheaper than V4 implies.** The chokepoint refusal covers
   *inference* for all nineteen modules in one edit. Only *speech* needs
   per-module work, and only for five modules — because `sre` is exempt by the
   ledger's OQ3 answer and the rest do not speak proactively from model output.
   Fifteen edits become five.
5. **SRE calls no model today.** `_builtin_sre.py` contains zero router
   references; all five watchers are file tails, service probes and a dependency
   scan. OQ3's "SRE stops calling models" is therefore vacuous *today* — the live
   half of OQ3 is that SRE keeps *speaking*. The UI copy must state what is
   actually true, not a hypothetical.
6. **`/api/agents/status`'s five agents are the wrong roster.** Two of them
   (`curator`, `browser`) are helper modules the researcher calls, not ticking
   agents — `loader.py`'s `_SKILLS_ONLY` docstring says so explicitly. The lane
   roster comes from a code constant instead.
7. **Pre-existing bug, flagged not fixed:** `MLXBackend.stream_complete`
   (`backends.py:330-332`) does not accept `system=` or `messages=`, but
   `ModelRouter.stream_complete` (`core.py:177-184`) always passes both →
   `TypeError` on any MLX streaming call through the router. Currently
   unreachable because chat resolves through the registry to
   Ollama/OpenAICompat. Filed to `sprints/BACKLOG.md`; **do not fix in this
   sprint** — it is a behaviour change on a path this sprint does not touch.
8. **Pre-existing, flagged not fixed:** the goal-parser child process runs its
   own `cost_tracker` singleton against the *same* `costs.json`, and `_save()`
   is a non-atomic `write_text` (`costs.py:263`). Parent and child can race. Not
   introduced here and not widened here (the trace store is append-only with one
   writer per process, which is *safer* than what costs.json does). Filed.
9. **`_builtin_drafter` is unwired and does not swallow.** `Drafter.compose`
   (`_builtin_drafter.py:161`) calls `router.complete()` with no `try`, and grep
   finds **no caller of `.compose(` anywhere in `src/`** — the only production
   "drafter" is `research/program_drafter.py`, a different, LLM-free thing. So
   today it is dormant code on a model path with no exception handling. It still
   gets a lane (it is in `FIXED_LANES`, rendering `"no calls yet this session"`)
   and it still gets the S4 hold guard, because the cost of adding it now is one
   branch and the cost of discovering it later is a crashed agent loop.
10. **"Hold" is admission control, not cancellation.** W3's wording ("zero new
   agent-sourced inference traces") is already correct for this, which is why
   W3 is satisfiable. The UI must not imply in-flight work stops. See the copy
   contract in §4.

**The portal has no authentication — stated plainly.** The
`onboarding_gate` middleware (`app.py:395`) is *not* auth: it blocks every
surface only until a passphrase exists, then every request passes with no
per-request credential check. There is no `Depends(`, no token compare. Default
bind is `127.0.0.1` (`scripts/start.sh:100`); `BIND_ADDR=0.0.0.0` is supported
and `_host_is_trusted` (`app.py:666-680`) explicitly accepts a wildcard bind on
the grounds that the operator opted into exposure. `local_trust_boundary`
(`app.py:683`) gives DNS-rebinding and cross-site protection for *mutating*
methods only. **Therefore: any process running as any user on the machine — and,
on a widened bind, any host on the LAN — can read every portal GET.** What this
sprint fixes is the *amount of sensitive content* behind that surface by
default: bodies go from always-on to off, and from 5000 chars unredacted to
2000+1000 redacted behind an admin-only switch. What it does **not** fix is the
absence of auth. In scope: the loud banner when a widened bind meets a live
recorder. Out of scope and filed to `sprints/BACKLOG.md`: portal authentication.

---

## Tech debt

**Added**

| Debt | Mitigation / home |
|---|---|
| A third persistence path (`agent_traces.jsonl`) beside `activity.jsonl` and `costs.json`. | Justified in §2: the 200-event ring would evict the operator's history (F18), and `cost_tracker` rewrites its whole file per call. Partly offset: bodies now live in **one** place instead of being smeared through `activity.jsonl`. |
| A contextvar that must be set at eleven explicit L3 sites. | L1 loader contract covers everything reachable via the loader; a miss is a **visible** Unattributed lane with `module:lineno`, never a wrong number. |
| A second UI view of the same halt flag, with widened meaning. | One flag, two surfaces, one copy string asserted by F17. Operator question below. |
| `tokens` kept as a deprecated alias on `/api/agents/status` for one release. | Now carries the *correct* value; removal filed to `sprints/BACKLOG.md`. |
| `ARAIL_AGENT_STREAM_FAST` escape hatch. | One env var; documented; removal filed once P2's bake-off settles the brain. |
| An awkwardly-named SSE route (`agent-trace-stream`) to dodge `FAST_PATH_PREFIXES` prefix matching. | Reason in a code comment. The alternative (fast-path-timing an SSE stream) is a known metrics bug. |

**Repaid**

| Debt | How |
|---|---|
| Always-on unredacted bodies on an unauthenticated endpoint (V5). | Off by default, redacted, capped, admin-gated; historical bodies disclosed with an explicit purge. This is the largest single item and it is a security fix. |
| `billing_source="agent"` for everything (V2, V8). | Per-agent attribution; four non-agent callers stop masquerading as agents. |
| `max_tokens` presented as token usage (V7). | Real usage, correct label, both surfaces. |
| DE2's known leak — the out-of-process goal parser. | Traced via the JSON protocol, not merely labelled "untraced". |
| `snapshot()` was the only slot read and it sorts 256 floats per label. | `slot_pressure()` gives a cheap read; `snapshot()` untouched. |
| Buddy emitted nothing at all about its own inference (V2). | Every Buddy call now leaves a trace with a brain, a reason code and a slot reading. |

**Net: negative** (debt repaid) on security and truth-in-UI; mildly positive on
module count — three new small modules (`agent_context`, `agent_trace`,
`redact`), each with one job, none importing the portal.

---

## Slice plan

Ordered so the riskiest assumption is proven first and a BLOCK in review costs
one slice, not the sprint. Each slice is independently committable and
independently revertable.

**S0 — Prove the attribution mechanism (tests only, no production behaviour).**
A test module that proves A1–A4: the contextvar survives `create_task`,
`to_thread`, a `copy_context`-wrapped `Thread`, and a subprocess JSON
round-trip, using throwaway harness code. *Riskiest first:* every other slice
depends on this and it is the only assumption I could not settle by reading —
it is Python-version *and* codebase-shape dependent. If S0 fails, the mechanism
is wrong and we have spent one slice, not the sprint.
→ Commit: `test(agents): prove contextvar propagation across task/thread/subprocess hops`

**S1 — The spine: context + trace store + chokepoint, no agent edits.**
`agent_context.py`, `agent_trace.py`, `scheduler.slot_pressure()`, the
chokepoint recording a metadata trace, `billing_source` rewrite, the
`unattributed` sentinel with `call_site`, the legacy `calls_by_source`
migration. **Deliberate intermediate state:** after S1 every agent call is
`unattributed`, which is honest and *proves the sentinel works* before anything
depends on it. Ships DE4's microbench.
→ `feat(observability): agent-call context + bounded trace store at the router chokepoint`

**S2 — Attribution wiring.** L1 loader contract, L2 daemons, the eleven L3
sites, `system_call` for the four non-agent callers, the subprocess protocol
extension. After S2, `unattributed` should be 0 in a normal session — which is
simultaneously W2's precondition and **DE2's measurement**.
→ `feat(agents): per-agent attribution via loader contract and explicit call sites`

**S3 — TTFT.** `ttft_ms`/`ttft_status` in `stream_complete`, `prefill_ms` with
its provenance field, and the fast-branch streaming in `deep_policy` behind
`ARAIL_AGENT_STREAM_FAST`. Independently revertable to "honest n/a everywhere"
by flipping one default.
→ `feat(router): honest TTFT with an explicit status enum; stream the agent fast path`

**S4 — The kill switch made real.** `halt_gate` at the chokepoint,
`speech_gate` in the five proactive speakers, `HOLD_EXEMPT_SPEAKERS = {"sre"}`,
`refused_halted` traces, per-module survival tests. No UI yet — drive it with
`POST /api/jobs/halt`, which already exists.
→ `feat(agents): hold-all-agents enforced at the chokepoint, SRE watcher exempt`

**S5 — Flight recorder.** `redact.py`, the toggle + state file, body capture via
`capture_body`, **removal of bodies from `researcher.py` / `browser.py`
`prompt_trace`**, the `/api/agents/prompts` contract change plus the
`agents.html` update, the legacy-bodies notice and the operator-initiated purge.
The security slice; QA-BLIND-1 targets it.
→ `feat(security): flight recorder off by default; stop writing bodies to activity.jsonl`

**S6 — Admin surface.** The four endpoints (tier-gated), the SSE stream, the
lanes card in `admin.html`, the V7 `/api/agents/status` fix and its label, the
hold and recorder controls with the exact copy string from §4, `overlap_pct`,
honest empty states, the widened-bind banner. Last because everything it renders
must already be true.
→ `feat(admin): maximus-only agent lanes view over the trace store`

**S7 — Docs and backlog.** `docs/agent-observability.md`; `docs/agents.md`
gains the attribution + hold contract for user-defined agents; file to
`sprints/BACKLOG.md`: portal authentication, the gateway gated on DE1's number,
the MLX `stream_complete` signature bug, the concurrent `costs.json` write, and
the `tokens` alias removal.
→ `docs(agents): observability + hold contract; file the deferred debt`

---

## Questions that genuinely need the operator before build

1. **The existing dashboard "Halt jobs" control changes meaning.** This design
   uses the single existing `scheduler.jobs_halted()` flag, so the control the
   operator already has will now *also* refuse agent inference and silence
   proactive speech. One switch with wider meaning (my recommendation — he asked
   for one visible switch), or a separate agent-hold flag alongside the job
   halt? This changes an existing control's behaviour, which is his call.
2. **Legacy bodies in `activity.jsonl`.** I designed disclose-and-offer-purge
   rather than auto-purge, because silently rewriting his log is worse than
   telling him. Accept, or purge automatically on upgrade, or leave silently?
3. **`ARAIL_AGENT_STREAM_FAST` default on.** It is the only way to get a real
   TTFT number for any agent (see finding 1), and it changes how Buddy's fast
   lines are fetched from Ollama — same `/api/chat` endpoint, `stream:true`
   instead of `stream:false`, deltas joined to the identical string. Accept the
   change for a real number, or ship `n/a` across the board this sprint and
   revisit at P2's bake-off?
