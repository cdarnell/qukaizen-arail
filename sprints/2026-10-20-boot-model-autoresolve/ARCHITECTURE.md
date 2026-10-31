# Architecture: boot-time model auto-resolution (retire the picker/settle flow)

**Date:** 2026-10-20
**Spec:** [SPRINT.md](./SPRINT.md) at `0fd93a9e` (no VISION.md; think phase skipped by operator decision)
**Base:** PR #186 (two-slot boot picker + `POST /api/models/settle`), on main

## Restatement

Today ARAIL asks the operator to "settle" two model slots through a boot banner.
`model_defaults.yaml` is the settle record: its presence gates the picker, and
any mismatch between it and the process env is shown as a drift problem. The
operator wants the opposite. When ARAIL starts, it works out the two models
itself: slot A is the fast, resident Ollama chat model, and slot B is the deep
model that QueueLLM streams in through the `aerollm` backend. It picks them from
what is installed and what this machine can hold, then loads both without
blocking boot. The yaml becomes a cache of the last result, rewritten on every
boot. It is never a gate and never a source of "drift". The operator can still
pin either slot, and a pin never blocks anything. Separately, the "fast:
ai-engineer … unhealthy — HTTPError 400 … /api/chat" banner is a real bug with a
root cause found below, and gets fixed. Nothing may hard-fail boot. Airgapped
stays the default. Model paths stay env-driven.

## Root-cause findings (investigated, not guessed)

### F1: the 400 on `/api/chat` is `keep_alive: "-1"` (a string)

This is not a model-name problem. Evidence was gathered on this Mac against
Ollama **0.32.4**:

- `ollama list` contains both `ai-engineer:latest` (4.7 GB) and `llama-ai-eng:latest`.
  The configured name `ai-engineer:latest` resolves fine.
- Direct `POST /api/chat` with `model: ai-engineer:latest`, `ai-engineer`, or
  `llama-ai-eng:latest` returns **200**, with or without `think:false`.
- The same request with `"keep_alive":"-1"` returns
  **400 `{"error":"time: missing unit in duration \"-1\""}`**. With
  `"keep_alive":-1` (JSON number) it returns **200**. With
  `"think":true` it returns 400 ("does not support thinking"), but nothing sends `true`.
- `OllamaNativeBackend._keep_alive()` (`src/arail/router/backends.py:1946-1954`)
  returns the **string** `"-1"` exactly when `_is_registry_tier0_model()` is
  true (with `ARAIL_RESIDENT_PIN` on by default). That is why only the tier-0 model
  (`ai-engineer:latest`, from `.env MODEL_NAME` and the yaml `default_a`) fails,
  and why `llama-ai-eng` picked ad hoc works: it gets `"2h"`.
- Failure path: `_warm_primary_router` → `router.complete` → `resp.raise_for_status()`
  → `binding` calls `report_failure(tier0)` → health is set to `unhealthy` with
  detail `HTTPError: 400 Client Error…` → `_gate_reason` makes the fallback
  event that the switcher renders as "fast: ai-engineer @ … is unhealthy".
- `raise_for_status()` throws away Ollama's JSON `error` body. That is why the
  banner never named the real cause.
- `tests/router/test_keep_alive.py:72,104` assert `== "-1"`. **The tests
  encode the bug.**

### F2: the "env no longer matches the settled value (None)" banner is three sources of truth fighting

1. The yaml has `default_b: null`. The operator set this on purpose: loading Qwen2.5-7B in
   `aerollm_backend_mlx_native` SIGABRTs, per the comment in the operator's yaml.
2. `model_defaults.apply()` pops `AEROLLM_MODEL`.
3. `store._seed_from_env` falls back to the hardcoded `"Qwen2.5-7B-Instruct-4bit"`
   when `AEROLLM_MODEL` is unset (`store.py:242`). The tier-1 registry entry still names Qwen.
4. `app._export_registry_env()` (`app.py:6825`) writes the registry's tier-1
   `model_id` back into `AEROLLM_MODEL`.
5. `_boot_settlement()` compares env (Qwen) with file (None) and reports drift.

**Safety corollary:** with B explicitly "none", the registry still held an
enabled Qwen tier-1 entry. The autochecks-gated preload loop would have loaded
the model the operator had disabled because it crashes the process. The new design
must make "B = none" unrepresentable as "B = Qwen".

## Assumptions

- A1. Ollama ≥ 0.32 accepts a numeric `keep_alive` (`-1` = forever, `0` = unload)
  and duration strings with units. It rejects unitless numeric strings. This was
  verified on 0.32.4 only. Older Ollama versions may have accepted `"-1"`; the fix must work on both.
- A2. `GET /api/tags` on localhost is the cheap, authoritative "installed" list
  (1.5 s timeout today). It reports `details.family` / `details.parameter_size`
  but not capabilities. `/api/show` reports `capabilities` (`completion`, `embedding`,
  `thinking`, …) at one request per model.
- A3. Directories under `ARAIL_MODELS_DIR` are the full candidate set for slot B.
  `model_specs.resolve_params_b(id, path)` can size most of them. Unknown
  size is treated as **not auto-eligible** for B; the user can still pin it.
- A4. `hardware.load_or_discover()` and `secondary_model_cap_b()` are fast
  (they read a persisted profile) and give a total-RAM figure good enough for budgeting.
- A5. Loading B in-process can crash the portal process (SIGABRT, Metal
  assertion). This is observed, not hypothetical, and Python cannot catch it.
- A6. The operator runs one World at a time (memory: worlds-serial-usage).
  One resolver run per portal process is enough. Concurrent Worlds each resolve
  on their own and may each try to warm. That is acceptable, but the RAM budget reads live free memory, not just total.
- A7. Users' existing `.env` `MODEL_NAME` / `AEROLLM_MODEL` values express
  intent, and silently ignoring them would be a regression. (The operator's `.env` sets both.)
- A8. Warming a local model is local work with no egress. The 2026-07-23 quiet-boot
  contract was about *remote probes and unasked work*. This sprint's operator decision
  ("models load when ARAIL loads") deliberately supersedes it **for model warm
  only**. The interval health loop and Claude-cache prewarm stay autochecks-gated.

## Design

### Resolution precedence (per slot)

```
pin (model_defaults.yaml `pins.<slot>`)   ← UI override / hand edit; may be "none" or "auto"
  > env preference (.env MODEL_NAME / AEROLLM_MODEL, if set and not stamped by us)
  > auto (installed × fits × ranked)
  > none (honest reason)
```

Every candidate at every level is **validated**: installed, completion-capable
(A), under the ceiling, and inside the RAM budget. An invalid pin or env value does not
block anything. It falls through to the next level and records a `reason`, which is shown
once in the degraded strip. The exception is pin `none` for B: it is honoured
as-is. That keeps the operator's crash workaround intact.

### Auto ranking

- **A** (Ollama): from `/api/tags`, drop non-completion models (`/api/show`
  capabilities lacks `completion`; fallback heuristic `embed` in name/family).
  Drop params ≥ `PRIMARY_CEILING_B` and size > A-budget. Rank in this order: the shipped
  default `llama-ai-eng` → catalog `recommended` → `flagship` → smallest
  remaining. (Prefer "smallest that's installed" so that "fast" stays true.)
- **B** (aerollm): only if `tier.is_maximus()` and `find_spec("aerollm_api")`.
  From `_mlx_dir_installed_models()`, keep `params ≤ secondary_model_cap_b()`,
  known size, and `A_size + B_size ≤ 0.60 × total_ram` (same ceiling as
  preload). Rank: MoE first (product direction), then catalog tier, then largest fitting.
- If A and B together exceed the budget, **A keeps priority** and B becomes `none` with
  reason `"insufficient_ram"`.

### model_defaults.yaml v2 (cache + pins, one file)

```yaml
# managed by ARAIL — `resolved` is rewritten every boot; edit only `pins`
version: 2
pins:            # preserved across boots; null = auto
  a: null
  b: none        # literal "none" = keep the deep slot off
resolved:        # cache — overwritten each boot, never compared for drift
  resolved_at: 2026-10-20T19:02:11-0700
  a: {model: llama-ai-eng:latest, source: auto, reason: null, size_gb: 1.3}
  b: {model: null, source: pin, reason: "pinned off", size_gb: null}
  warm: {a: ok, b: skipped}
```

**v1 migration.** A file with top-level `default_a`/`default_b` and no `version`
is read once. `default_a` becomes `pins.a` and `default_b: null` becomes
`pins.b: none` (this preserves the crash workaround). The file is rewritten as v2
and the migration is logged once to activity. The legacy free-form comment
block in the operator's file is lost on rewrite. Move its text into the
`reason` of the migrated pin (`"migrated from v1 default_b: null"`) and keep
a one-time `.model_defaults.v1.bak` beside it.

### Data flow

```
 portal startup (app.py lifespan)
   │
   ├─ config import → model_defaults.apply()          [cheap, no network]
   │      reads cache `resolved` → stamps MODEL_NAME / AEROLLM_MODEL
   │      (so CLI/agents/non-portal processes see last-resolved values)
   │
   ├─ asyncio.to_thread(model_resolve.resolve_boot, timeout=5s)   NEW
   │      ├─ pins (yaml) ─┐
   │      ├─ env pref ────┼─► validate ─► rank ─► SlotResolution(a,b)
   │      ├─ /api/tags + /api/show (localhost, 1.5s each, capped)
   │      ├─ ARAIL_MODELS_DIR scan
   │      └─ hardware profile + RAM budget
   │      on timeout/exception → keep cache values, reason="resolver_timeout"
   │
   ├─ model_defaults.write_cache(resolution)        atomic replace, pins untouched
   ├─ stamp env (MODEL_NAME, AEROLLM_MODEL / pop when none)
   ├─ store.reconcile_from_resolution(reg, resolution)   tier0/tier1 follow; tier1
   │      disabled + model_id="" when B is none (kills the Qwen fallback)
   │   (_export_registry_env DELETED — direction was registry→env; now env←resolution→registry)
   │
   ├─ create_task(warm_a)  → _warm_primary_router (1-token completion,
   │      keep_alive=-1 int) → registry probe → resolved.warm.a
   └─ create_task(warm_b)  → crash sentinel check → memory gate (NOT presence)
          → deep_policy.get_deep_router() under inference_slot
          → registry probe → resolved.warm.b
                 │
 GET /api/models/boot ──► {mode: hidden|degraded, resolution, degraded[]}
 _model_boot_banner.html ─► nothing, or one-line honest strip + "Change…" (opens slot chip)
 POST /api/models/pin  ──► write pins → re-resolve that slot → warm (async)
 start.sh banner / status ─► python -m arail.model_defaults --banner|--json (reads cache)
```

### Interface contracts

**`arail.model_resolve` (new module; candidate/fit helpers move here from `models_api.py` and `model_defaults.py`)**

- `resolve_boot(*, pins, env_pref, now) -> BootResolution`
  - Pre: none. It must tolerate Ollama being down, a missing models dir, and a missing hardware profile.
  - Post: always returns. Each slot is `{model: str|None, source: "pin"|"env"|"auto"|"cache"|"none",
    reason: str|None, size_gb, params_b}`. A is `None` only when no completion-capable
    model is installed or Ollama is unreachable *and* there is no cache. Never raises, and
    never performs a non-loopback request. Assert the host is loopback, and reuse the airgap
    `is_local_ip` guard for `OLLAMA_HOST`.
  - Bad input (pin names an absent model, a malformed yaml): falls through, with reason text naming
    the exact model and the fix command (`ollama pull X` /
    `hf download … --local-dir $ARAIL_MODELS_DIR/X`). Download hints are never
    offered as links while airgapped. The existing `hf_url` rule carries over.
- `fit_primary(model_id)`, `fit_secondary(model_id, path)`: moved verbatim from
  `models_api._fit_primary/_fit_secondary`, plus a RAM-budget variant.
- `slot_a_candidates()`, `slot_b_candidates()`: moved from `models_api`.
  Installed-only for resolution. The catalog-augmented variant stays for the override UI.
- Reuse note: `_gather_slot_facts()` is **rewritten as a projection of
  `BootResolution`** (reading the cache), not a parallel computation. `resolve_slots()` loses
  `settled` and gains `source`/`reason` per slot.

**`arail.model_defaults`**

- `apply(path=None)`: reads v2 `resolved` (or v1 → treated as pins, no write
  from apply; migration writes only happen in the portal resolver). It never raises, as today.
- `write_cache(resolution, path=None)`: atomic, and preserves `pins` byte-for-byte.
- `write_pin(slot, value, path=None)`: value ∈ {model id, `"none"` (B only), `None`/`"auto"`}.
- `write_defaults()`: **deprecated shim** that maps to `write_pin` for both slots, with a
  `DeprecationWarning`. Remove next release.
- CLI: `--banner` (unchanged flag; new lines show `source` and `reason`), `--get default_a|default_b`
  (unchanged; reads `resolved`), `--json` (shape v2; drops `settled`, adds `version`, `pins`,
  `resolved`). No `--resolve` flag. The portal is the only resolver (no second implementation).

**`OllamaNativeBackend._keep_alive()`** returns `int | str | None`.
- Pin case returns **int `-1`**.
- `ARAIL_OLLAMA_KEEP_ALIVE`: a value that parses as an int is sent as int. Otherwise it is
  passed through as a string. Empty means omit, as today.
- `complete`/`stream_complete`: on a non-2xx response, raise an error whose message includes
  Ollama's `error` field, truncated to 160 chars. No secrets are in that body; the bearer header is never echoed.

**HTTP**
- `GET /api/models/boot` → `{mode: "hidden"|"degraded", resolution:{a,b}, warm:{a,b},
  degraded:[{slot, kind: missing|unfit|insufficient_ram|ollama_down|crashed_last_boot|resolver_timeout, detail}],
  airgapped, hardware}`. `picker`/`problem` and `settlement` are never emitted.
  Fetched once per page load, as today.
- `POST /api/models/pin {slot:"a"|"b", model: str|null|"none"}` uses the same validation
  as today's settle: installed, ceiling via `resolve_answering_model`, and `ARAIL_MODELS_DIR`.
  It writes the pin, re-resolves and stamps env/registry, schedules a warm (A: async; B: if a
  different runtime is resident, it returns `restart_required: true`, reusing
  `_tier1_resident_model_differs`), and returns within the request without
  waiting on the warm.
- `POST /api/models/settle`: **deprecated alias** for one release. It maps to two pin
  writes and adds `Deprecation: true` + `Link: </api/models/pin>; rel="successor-version"`
  headers and `"deprecated": true` in the body. Delete next release.
- `POST /api/chat/default` with `slot=deep` / resident: re-routed through `write_pin` so that a
  UI chip pick survives reboot. Today it writes only env/registry and is lost when the
  registry is re-seeded. `ARAIL_CHAT_DEFAULT_MODEL` behaviour for cloud defaults is unchanged.
- `GET /api/instance`: additive fields `warm_a`, `warm_b` (closed vocabulary:
  `ok|skipped|failed|deferred|disabled`). The existing `warm`/`warm_ms`/`warm_skipped` keep their meaning for A.

### Warm-up policy

- Boot warm becomes **default on**, independent of `ARAIL_AUTOCHECKS`.
  Opt out with `ARAIL_BOOT_WARM=0` (both slots), or `ARAIL_TIER0_BOOT_WARM=0` /
  `ARAIL_AEROLLM_PRELOAD=0` (per slot; existing names keep working).
  `./arailctl start --warm` keeps its reporting role.
- A: the existing `_warm_primary_router` (single warm path, per elite-cli Ruling 6).
- B: a **one-shot** boot warm, separate from `aerollm_preload_loop`. It uses a new
  `deep_policy.boot_warm_safe()` that checks tier, wheel, and memory pressure < 0.60
  and fit, but **not operator presence**: the operator is always present at boot, so
  `background_safe()` would never pass. The loop stays autochecks-gated as the later re-warmer.
- **Crash sentinel** (handles A5): before the B load, write
  `$DATA_DIR/.aerollm_boot_warm.inflight` containing `{model, pid, started_at}`.
  Delete it on success or on a Python-level failure. If the next boot finds a sentinel whose pid
  is dead, B for that model is auto-skipped with
  `crashed_last_boot: "<model> crashed ARAIL while loading last boot — skipped. Pin a different
  deep model or clear the pin to retry."` This stays in effect until the pin/model changes or the
  operator retries via `POST /api/models/pin`.
- Warms run under `scheduler.inference_slot`, A before B, so a chat turn
  arriving at boot queues behind at most one warm.

## Failure modes

| # | Failure | Detection | Recovery |
|---|---|---|---|
| FM1 | Ollama not running / unreachable | `/api/tags` connection error/timeout (1.5 s) | A = cache value with `source: cache`, or `None` if there is no cache; degraded strip says "Ollama isn't running — start it with `ollama serve`"; warm A skipped; boot continues |
| FM2 | No completion-capable model installed (fresh box, or only `nomic-embed-text`) | candidate list empty after capability filter | A = none; strip gives the exact `ollama pull llama3.2:1b && ollama create llama-ai-eng -f models/ai-eng/Modelfile.default` (same text as `start.sh`); chat shows the existing no-model state |
| FM3 | Pinned/env model not installed | validation miss | fall through to auto; strip: "`X` (pinned) isn't installed — using `Y`. Run `ollama pull X`" |
| FM4 | Insufficient RAM for B (or A+B) | budget check vs `0.60 × total` / live pressure | B = none, `insufficient_ram` with numbers; A unaffected |
| FM5 | B model crashes the process on load (SIGABRT) | stale inflight sentinel with dead pid at next boot | B auto-skipped with `crashed_last_boot`; never retried automatically for that model |
| FM6 | B load raises a Python exception / returns None | `get_deep_router()` result | `warm.b = failed`, registry tier1 unhealthy with the real detail; chat deep falls back visibly (existing) |
| FM7 | Ollama rejects a request (400) | non-2xx | error message carries Ollama's `error` text (F1 fix); never a bare "400 Bad Request" again |
| FM8 | `keep_alive` type regression | unit test on body type; real-Ollama integration test | int `-1` |
| FM9 | Resolver hangs/slow (`/api/show` × N models, big dir scan) | 5 s overall timeout in `to_thread`, `/api/show` capped at 8 models then name heuristic | keep cache; `resolver_timeout` reason; boot never waits |
| FM10 | yaml corrupt / unwritable (read-only checkout) | parse error / `OSError` on atomic write | resolve in memory, log once, strip notes "couldn't save model cache"; pins treated as none |
| FM11 | v1 file migration loses operator intent (`default_b: null`) | migration test | `null` → `pins.b: none`; `.bak` kept |
| FM12 | B = none but registry still names a tier-1 model (F2) | `reconcile_from_resolution` + test | tier1 `enabled=False`, `model_id=""`; hardcoded Qwen fallback removed from `_seed_from_env` |
| FM13 | Pin write races a boot resolve | single `threading.Lock` around resolve+write in `model_resolve` | last writer wins on `resolved` only; pins are only written by `write_pin` |
| FM14 | Airgap: resolver or strip offers remote fetch | host assertion + template test | loopback-only requests; `hf_url` suppressed when airgapped; install commands shown as text only |
| FM15 | Embedding model picked as chat A | capability filter | excluded; test with `nomic-embed-text` fixture |
| FM16 | Warm A pinned forever on a 16 GB box with a 7B A (`ai-engineer`, 4.7 GB) | budget: A-size counts against the 0.60 ceiling | B dropped first; docstring claim "resident ≤3B" corrected |
| FM17 | Concurrent World instances both warm B | live memory pressure check right before load | second instance defers (`deferred`), honest strip |
| FM18 | `OLLAMA_PORT` non-default ignored (`_ollama_installed_models` hardcodes 11434) | test with `OLLAMA_PORT` set | resolver passes the env port through |

## Test strategy

**Unit**
- `test_keep_alive.py`: change `"-1"` → `-1` (int) assertions. Add: an env `"-1"` is coerced to int, `"5m"`
  passes through, an empty value is omitted. (FM8)
- Ollama error surfacing: a fake session returns 400 with `{"error":"…"}` and the raised message contains it. (FM7)
- `model_resolve.resolve_boot`, table-driven over fixtures:
  pin valid / pin missing (FM3) / pin `none` for B / env pref / auto ranking A
  (llama-ai-eng preferred; embed excluded, FM15) / auto ranking B (MoE first, unknown-size
  excluded) / RAM: A+B over budget drops B (FM4, FM16) / Ollama down with and without cache (FM1) /
  empty install (FM2) / timeout (FM9) / non-maximus or no wheel gives B none / `OLLAMA_PORT` (FM18).
- `model_defaults`: v2 read/write round-trip keeps pins byte-stable; v1 migration incl.
  `default_b: null → none` and `.bak` (FM11); corrupt and read-only file (FM10); `write_defaults`
  emits a DeprecationWarning.
- `store.reconcile_from_resolution`: B none gives tier1 disabled with no Qwen (FM12, the F2 regression).
- Crash sentinel: stale sentinel with dead pid causes skip; sentinel with live pid (another instance) gives
  `deferred`; success deletes it. (FM5, FM17)
- Concurrency: `write_pin` during `resolve_boot` keeps the pin. (FM13)

**Integration (TestClient, Ollama/aerollm stubbed)**
- Portal boot with Ollama down: `/api/ready` reaches ready, `/api/models/boot` is `degraded/ollama_down`,
  and no exception reaches the log as a traceback to the user.
- Boot with the v1 yaml from the operator's real shape: no `drift` anywhere in `/api/models/boot`
  (regression for "env no longer matches the settled value (None)").
- `POST /api/models/pin` for A: returns before warm completes; the next `/api/models/boot` reflects it;
  the yaml `pins.a` is set and survives a simulated restart.
- `POST /api/models/settle`: still 200, with `Deprecation` header and pins written.
- `POST /api/chat/default slot=deep` persists as `pins.b`.
- `_model_boot_banner.html` DOM: no picker markup, strip only in `degraded`, airgapped renders no links (FM14).
- CLI: `python -m arail.model_defaults --banner/--json/--get` against v2 cache fixtures.

**Integration (real local Ollama, skipped when absent; marked `requires_ollama`)**
- `OllamaNativeBackend(model=<tier0>).complete("ok",1)` with pin keep_alive → 200 (F1 end-to-end).
- Resolver against the live `/api/tags` returns a completion-capable model.

**Regression**
- F1 (keep_alive string), F2 (drift banner / Qwen fallback), plus both QA files from PR #186
  (`test_models_boot_endpoint.py`, `test_models_settle_endpoint.py`) rewritten, not deleted
  silently. Each removed assertion must be listed in BUILD_LOG with its replacement.

**Performance**
- `resolve_boot` p95 < 1.0 s with Ollama up and 10 installed models (stubbed latency 20 ms/req),
  hard cap 5 s. Portal time-to-`/api/ready` must not regress by > 200 ms vs main (the resolver is off-loop).

**Security**
- Resolver performs only loopback requests (assert by patching `requests`/`urllib` to fail on
  non-loopback hosts) with `LAB_MODE=airgapped`.
- `POST /api/models/pin` rejects path traversal in `model` for B (`../`, absolute paths outside
  `ARAIL_MODELS_DIR` unless the operator's env already points there). Reuse the
  `os.path.isabs` handling but add a realpath containment check. This is a new check: settle today accepts absolute paths.
- Error text from Ollama is truncated and contains no `Authorization` header.

**Setup-on-clean-machine (ARAIL gating)**
- Fresh clone, minimalist, Ollama with only `llama-ai-eng`: boot resolves A = llama-ai-eng, B = none
  ("deep model needs maximus"). The strip is hidden or informational only. No yaml exists before; one exists after.

## Removals from PR #186 and their deprecation path

| PR #186 surface | Fate | Path |
|---|---|---|
| Picker mode in `_model_boot_banner.html` (~310 lines) | removed | replaced by a ≤60-line degraded strip; "Change…" opens the existing slot chip in chat |
| `mode: picker\|problem`, `settlement`, drift problems in `GET /api/models/boot` | removed | `mode` stays a key with values `hidden\|degraded`; any external reader of `picker` simply never sees it |
| `POST /api/models/settle` | deprecated | alias to pins for one release, headers as above; delete in the following release (file a BACKLOG entry) |
| `model_defaults.write_defaults()` | deprecated | shim → `write_pin` + DeprecationWarning |
| `resolve_slots()["settled"]` | removed | `--json` consumers: none outside the repo (verify with `git grep settled` in arail + workspace) |
| "Delete model_defaults.yaml to re-run the picker" | removed | deleting it now just means "re-resolve, no pins" |
| `_boot_settlement`, `_BOOT_CANDIDATES_CACHE` usage for boot | removed | candidate lists remain available to the override UI via `slot_*_candidates` |
| `app._export_registry_env()` | removed | its job (env↔registry convergence) is done by resolve → stamp → reconcile |
| `store._seed_from_env` "env wins only when env moved" fingerprint for tier0/tier1 | replaced | tier0/tier1 always follow the resolution; `seed_state` keys left in the json, ignored (no migration needed) |

## Docs / CLI / tests that must change

- `docs/cli.md` §start (~lines 194-210): rewrite "Model selection, settled once…" to describe
  auto-resolution, pins, sources, and the warm opt-outs. §install/doctor `[4/5] models` (line 106):
  "expected primary" = resolved A.
- `model_defaults.yaml.example`: v2 schema with pins-only guidance.
- `scripts/start.sh`: `_check_default_ollama_model` reads resolved A (unchanged call, new
  semantics). Banner lines print `source` and `reason`.
- `./arailctl status` / `scripts/status.sh`: add an optional `models` object
  (`{a:{model,source,warm}, b:{…}}`) read from the cache, with no network. docs/cli.md states
  codes and fields are additive, so this stays `arail.status/v2`. The builder must confirm the
  schema doc allows additive keys before shipping; otherwise bump.
- `docs/conversation-memory.md`: untouched. `CLAUDE.md`: one line under conventions:
  "boot auto-resolves models; model_defaults.yaml = pins + last-resolved cache".
- Tests to rewrite: `tests/test_models_boot_endpoint.py`, `tests/test_models_settle_endpoint.py`,
  `tests/test_model_boot_banner_dom.py`, `tests/test_model_defaults.py` (settled tests at 252-281,
  CLI json at 384), `tests/router/test_keep_alive.py`, `tests/registry/test_env_write_through.py`,
  `tests/test_chat_default.py`, `tests/test_chat_slots_contract.py`, plus `tests/cli/lib.sh` if it
  greps the banner. `tests/conftest.py` keeps `ARAIL_MODEL_DEFAULTS_FILE` isolation. Add a session
  fixture that stubs the resolver's network so no test hits a real Ollama unless marked.

## Tech debt

**Added**
- The yaml now mixes operator intent (pins) with machine cache (resolved). This is accepted to keep a single
  "what's active" file; it is clearly sectioned.
- One release of `POST /api/models/settle` + `write_defaults` shims (file a BACKLOG entry to delete).
- Crash sentinel is a heuristic. A SIGKILL from the OOM killer looks like a crash. That is acceptable: the message says "crashed or was killed".

**Repaid**
- Three sources of truth (yaml / env / registry) with two-directional sync → one direction
  (resolution → env + registry). `_export_registry_env` and the hardcoded Qwen fallback are deleted.
- ~310-line picker template + settle drift logic removed.
- Duplicate fit/candidate logic in `model_defaults._gather_slot_facts` vs `models_api._fit_*` → one module.
- Silent Ollama errors (bare "400 Bad Request") → real error text.
- `_ollama_installed_models` port hardcode (fixed in the resolver path; chat gallery caller noted).

**Net:** negative.

## Recommended implementation order

1. **F1 fix first, as its own commit:** `_keep_alive` int + Ollama error surfacing + `test_keep_alive.py`.
   Ship-worthy alone; verify against local Ollama.
2. `model_resolve` module: move fit/candidate helpers, add `resolve_boot` + unit tests (no wiring).
3. `model_defaults` v2: `write_cache`, `write_pin`, v1 migration, `apply()` on v2, CLI `--json/--banner`; tests.
4. `store.reconcile_from_resolution`; remove the Qwen fallback; F2 regression test.
5. Portal wiring: resolver task, stamp, reconcile; delete `_export_registry_env`; boot-warm policy for A.
6. B boot warm with `boot_warm_safe()` + crash sentinel; `/api/instance` fields.
7. `GET /api/models/boot` v2, `POST /api/models/pin`, settle alias, `/api/chat/default` → `write_pin`.
8. Banner template → degraded strip; DOM tests.
9. `start.sh` banner, `status` models block, docs/cli.md, yaml example, CLAUDE.md line.
10. Clean-machine run (minimalist, Ollama with only `llama-ai-eng`) + Ollama-down boot, logged in BUILD_LOG.
