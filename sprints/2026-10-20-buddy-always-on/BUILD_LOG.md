# Build log: Buddy, always on (S1 "Buddy answers, asks, and stops")

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `b2af01d7`, and Addendum A (`162d9d22`, `4fe3acf6`) from the resume at Step 24
**Started:** 2026-10-20
**Builder:** one session across both repos, in the order of ARCHITECTURE §9 adjusted for dependencies.

| Repo | Path (session folder) | Branch | Base |
|---|---|---|---|
| qukaizen-arail | `arail-buddy-wt` | `qukaizen/arail-buddy-always-on` | `ca0a8f81` + sprint docs (`f31dfbf4`) |
| qukaizen-buddy | `qukaizen-buddy` | `qukaizen/buddy-always-on` (cut from `main` `349477f2`) | nanobot `v0.3.5` = `1bb712d3` |

Paths below: `buddy/` means `qukaizen-buddy/buddy/`; `buddy/src/buddy/contracts/` is the
contract directory named in ARCHITECTURE §5.3 (the "contracts/" of §9), and likewise
`pins/` and `worlds/` live under `buddy/src/buddy/` (§5.13, §5.21).

## Environment and baselines (recorded before any change)

| Check | Command | Result before |
|---|---|---|
| ARAIL R-BUDDY-98 | `/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest tests/test_*buddy*.py -q` in `arail-buddy-wt` (main checkout's venv, read-only; `tests/conftest.py` forces the worktree's `src/` first) | **100 passed, 1 xfailed** (101 collected; 98 `def test_` across 11 files, parametrisation accounts for the difference). The xfail is the pre-existing `test_qa_buddy.py::test_dream_preview_is_not_redacted_and_is_recorder_independent` (BACKLOG S3). "98 stay green" is read as: the same 100 pass, the same single xfail, no new skip or xfail. |
| nanobot upstream suite | `.venv/bin/python -m pytest -q -p no:cacheprovider -n 4` in `qukaizen-buddy` (Python 3.12.13, `uv venv`; `uv pip install -e ".[dev]"`, then the declared dependencies of every channel plugin, then PTB 22.8) | **7681 passed, 11 skipped, 0 failed** (61 s). A first run without the optional channel dependencies had one environment failure (`tests/channels/test_channel_setup.py::test_every_runtime_channel_field_has_a_webui_contract` imports every channel runtime; Matrix needs `nh3`). That is an environment gap in the venv, not a repo defect, and it is gone with the plugin dependencies installed. |

## Verification log (ARCHITECTURE items marked "not verified")

| Item | Result | Evidence |
|---|---|---|
| **A8** nanobot keeps `messages[0]` through `context_governor.prepare_request`; Buddy's `before_iteration` hook can rewrite it | **FALSE. See Architect feedback required G1.** | captured request, `nanobot/agent/runner.py:443-447`, `nanobot/agent/context_governance.py:139-173` |
| **A7** `${VAR}` resolves in `channels.telegram.token` and `providers.<custom>.apiKey` | True. `resolve_config_env_vars` walks model fields and `__pydantic_extra__` (`nanobot/config/loader.py:193-246`, `_resolve_in_place`); probed with both keys. A missing variable raises `ConfigLoadError`. | probe, this log |
| Root `Config` rejects unknown top-level keys | True: a top-level `buddy` key fails with "Unknown setting." Unknown keys **under** `channels.telegram` are accepted (extras), so S9 must allow-list channel keys itself. | probe |
| WebSocket channel stays off by config alone | True only when `channels.websocket.enabled: false` is explicit. With no `websocket` section, or no `channels` at all, `ChannelManager` builds it (`channels/websocket/manifest.py:18-19`, `channels/manager.py:243-248`). The spec's generated `nanobot.json` already has the explicit `false`; S9 must require it. | probe over three configs |
| T2: exit code after SIGTERM to self | Exit 0 by reading: SIGTERM is handled (`cli/gateway_runtime.py:121-123`), `shutdown_event` ends `run()`, `asyncio.run(run())` returns (`:1034`), no `Exit(1)` after `startup_complete`. Empirical confirmation is part of B10. | code |
| R4: local trigger store API | `LocalTriggerStore.list_triggers(include_disabled=True)` (`nanobot/triggers/local_store.py:94`) returns the stored triggers; R4 asserts it is empty. Do not use `claim_deliveries` (it mutates). | code |
| S9 tool flag names | `tools.web.enable`, `tools.exec.enable`, `tools.file.enable`, `tools.cliApps.enable`, `tools.my.enable` (all default **true**), `tools.imageGeneration.enabled` (default false); `tools.restrictToWorkspace`, `tools.mcpServers`. | `agent/tools/{web,shell,filesystem,cli_apps,self,image_generation}.py` |
| `PORTAL_PORT` resolution (§5.12) | Process environment after `.env` load, default `8080` (`portal/app.py:840`, `scripts/status.sh:342`). There is no `lab.conf` file in the repo. | code |
| Ollama supports JSON-schema `format` (A2) | Client is 0.32.4 (>= 0.5). The server was not running here, so the server version stays **not verified**; the handshake probe reports it at runtime. | `ollama --version` |
| P0 is first (nothing imported before it) | True: `nanobot/__init__.py` imports only stdlib (`tomllib`, `importlib.metadata`, `pathlib`, `typing`); `__main__.py` line 5 is the first nanobot import. | code |
| Pre-patch hashes (§5.13) | `__main__.py` `c8e8dce8576f9ea1`, `gateway_runtime.py` `5dda5bfb083580ec`, `telegram/runtime.py` `793e374682bf88cd`: all three match. | `shasum -a 256` |
| Turn-hook factories that raise | Not in the spec. `build_agent_turn_hook` logs and **skips** a factory that raises (`agent/turn_hooks.py:67-73`, `:78-84`), and returns only the progress hook for `ephemeral` turns unless `run_extra_hooks_for_ephemeral` (`:51-52`). Buddy's factory must never raise, and T1 to T4 must not rely on the hook being present. Recorded for B10. | code |
| Channel dependency auto-install | `ensure_enabled_channel_dependencies` (`channels/manager.py:279`) can act on missing channel dependencies at startup. Buddy depends on PTB directly, so it is present; the exec guard is the backstop. Recorded for B10 / S-tests. | code |

## Plan

Status key: `todo` · `done <sha>` · `BLOCKED (reason)`.

| # | Row | Repo | Files | Change | Tests (when written) | Commit ref |
|---|---|---|---|---|---|---|
| 0 | BUILD_LOG | arail | `sprints/2026-10-20-buddy-always-on/BUILD_LOG.md` | This skeleton, baselines, verification log, gap G1 | n/a | done `d9cf4e5a` |
| 1 | B1a | buddy | `buddy/pyproject.toml`, `buddy/README.md`, `buddy/src/buddy/__init__.py`, `buddy/tools/capture_brain_chat.py`, `buddy/tests/test_nanobot_request_capture.py` | Distribution `qukaizen-buddy` (import `buddy`, src layout); honest README line + Built with Llama; the capture tool; a test that pins the facts the capture found | the capture test (runs nanobot against a loopback fake server) | done `ae7f6b7f` (buddy) |
| 2 | B1b | buddy | `buddy/src/buddy/contracts/buddy.host.v1.json`, `contracts/conformance/*.json`, `buddy/tests/test_contract.py` | The contract (§5.3–§5.9) and conformance cases, including the captured request. **Turn-header carrier is provisional (G1).** | schema self-validation, every error code once, no retry marker in any message, enum pin (C-ENUM-01), C-ERR-01 | done `fcddf1e2` (buddy) |
| 3 | A1 | arail | `agent_context.py`, `agent_trace.py`, `router/core.py` + tests | `channel`/`identity`/`origin` on `AgentCall`; trace v2 fields and `SCHEMA`; cost capture | trace/schema tests; R-BUDDY-98 | done `36349f7f` |
| 4 | A2 | arail | `agent_trace.py`, `portal/app.py`, `portal/templates/admin.html` + tests | `recent`, model-call counting, `related`, "Buddy on the phone" block | lanes/endpoint tests | done `75ce05c5` |
| 5 | A3 | arail | `scheduler.py`, `portal/scheduler.py`, `portal/app.py`, `registry/core.py` + tests | `halt_load_failed`, `inference_slot(acquire_timeout_s)`, presence skip, fast-path prefixes, `is_cloud_entry` | R-PRES-01 style tests | done `c9762089` |
| 6 | A4 | arail | `router/core.py`, `router/backends.py` + tests | `response_schema` pass-through; Ollama `format` | byte-identical-request test per existing caller path | done `658e9880` |
| 7 | A5 | arail | `buddy_host/contract/*`, `scripts/sync-buddy-contract.py` + tests | Vendor contract + conformance + `PROVENANCE.json`; sibling-free check | check test | done `80c34c1a`; A5b re-vendor `ec35a0f4`; test renames `4024b753`; A5c pyproject revert `4395ca50` |
| 8 | A6 | arail | `buddy_host/{__init__,link,__main__}.py`, `arailctl` + tests | `arailctl buddy link/unlink/status` | U-LINK, U-PERM | done `ab6ea498` |
| 9 | A7 | arail | `portal/host_edge_routes.py`, `buddy_host/{admission,events,ledger}.py`, `app.py` + tests | admission, handshake, trace.record, hold_all, pause_research + ledger | conformance run, I-EDGE | done `b69bbf9f` |
| 10 | A8 | arail | `buddy_host/{brain,budget}.py`, routes + tests | brain.chat (header, flatten, brain check, budget, slot, SSE) | conformance run, I-EDGE | done `a14fcfd2` |
| 11 | A9 | arail | `docs/buddy-edge.md`, `sprints/BACKLOG.md` | Docs; D1–D14 | doc lint if any | done `2ab0f244` |
| 12 | B3 | buddy | `guard.py`, `redact.py`, `boot.py` + tests | closed-mode audit-hook guard, redaction | U-GUARD, L1, L2 | done `d5e5b9d0` |
| 13 | B4 | buddy | `preflight.py`, `configure.py`, `people.py`, `hold.py` + tests | S1 to S12 (S6/S7 read pin files given by path), config generator, people, hold file | U-CFG, U-ID, U-HOLD, U-PERM | done `ed70b193` |
| 14 | B5 | buddy | `nanobot/__main__.py`, `UPSTREAM.md` | **P0** | upstream suite | done `f84aa75a` |
| 15 | B6 | buddy | `edge_client.py`, `gate.py` + tests | handshake, trace + spool, hold_all, pause_research client; ModeGate and sleep detection | `FakeHost` over conformance, I-GATE, I-SLEEP | done `af374189`; B6b (gate remembers its last closed stretch) `8aa0b531` |
| 16 | B8 | buddy | `channel.py`, `intake.py`, `unknown.py`, `nanobot/channels/telegram/runtime.py`, `UPSTREAM.md` | `BuddyTelegramChannel`, Intake, aggregator, **P2** | U-INT, U-ID, fake PTB updates; upstream suite | done: P2 `f696874d`, B8 `96681879` |
| 17 | B9 | buddy | `proposals.py`, `confirm.py`, `commands.py` + tests | proposal store, nonce, confirm checks, six commands | F1 to F5, F19, F20 | done `327b9640` |
| 18 | B2 | buddy | `pins/nanobot.pin.json`, `worlds/qa-practice/*`, `worlds/qa-practice.pin.json`, `requirements.lock` | seam hashes, vendored sealed World, pin | U-SEAL, U-SEAM | done: pin `eb92734d`, World `daac5f8b`, lock `fefecec0` |
| 19 | B7 | buddy | `world.py`, `answer.py`, `persona.py`, `carrier.py`, `turn_hook.py` + tests | World provider, answer render, persona, carrier, **turn hook** | U-ANS, U-CHIP, U-HDR | done: B7a `0ac3895a` `00d74c89` `a5688886` `300d9f7f`; B7b `c2795eef` `32137a78` `9ecc98a0` `b12f972c` `37e66fe0` `39a3777a` `b794f698` `947052e1`. **The quality bar is not met on the dev set (Step 25)** |
| 20 | B10 | buddy | `nanobot_seam.py`, `testing/upstream_mode.py`, `nanobot/cli/gateway_runtime.py`, `UPSTREAM.md` | `install()` R1 to R8, **P1** | I-BOOT, I-CFG, upstream suite with plugin | done `f6cd9335`; P1 `984d13b1`; UPSTREAM `9eb2dc91`; fix `6bfc3563` |
| 21a | B11a | buddy | `__main__.py`, `cli.py`, `buddy/docs/DEBT.md` + tests | `setup`, `configure`, `people check`, `hold status/off`, `service install/uninstall`, `verify` (static) | I-BOOT-style CLI tests | done `707e36b1` |
| 21b | B11b | buddy | `qa_harness`, runtime `verify`, `docs/S1.md` | The test channel and docs that need the seam | I-BOOT, I1 | done `d372b6ca` `ec7e4c7f` `32062dc3` `7326f32e` |
| 22 | I1 to I3 | both | n/a | integration without a phone; contract re-sync; 10-min airgapped sampler | per section 9 Phase I | done: I1 `7d2c1286`, 29 of 29 with the real pins; I2 equal sha256; I3 `8282a374`, zero non-loopback rows |
| 23 | B1d | buddy | `contracts/buddy.host.v1.json`, conformance cases | Addendum A A8: carrier rules, `finish_reason` stop, trace refusal `busy` | contract, FakeHost | done `940cf349` |
| 24 | A8b | arail | `buddy_host/brain.py`, `budget.py`, `portal/host_edge_routes.py` + tests | carrier rules, atomic budget claim after the slot, no `length` | I-EDGE-13, I-EDGE-14, C-HDR-01..03 | done `bfab10b3` |
| 25 | A5d | arail | `buddy_host/contract/*` | re-vendor after B1d (committed after A8b) | `--check` | done `0d02a605` |
| 26 | B4b | buddy | `configure.py` | C7 `contextWindowTokens` 200000 | S9 pin | done `0afa72a4` |
| 27 | B8b, B8c | buddy | `commands.py`, `intake.py`, `scrub.py`, `channel.py`, `texts.py` | `/stop` survives a closed gate; F49 scrub; F50 one turn in flight; F54 no reply context; F53 default label `error`; card held until its answer | U-INT, U-NONCE-02, U-LABEL-01, U-REPLYCTX-01 | done `5e807130` `cd0d4da5` `83e57a7b` `9f21579c` `1f0bff6d`; B8c `62816c01` |
| 28 | B9b | buddy | `confirm.py` | one automatic resend after a lost answer; the code is never printed | I-CRASH-02a to d | done `005e6807` |
| 29 | G13 | buddy | `commands.py`, `confirm.py`, `texts.py` | no lab data in `/status` or the card | U-G13 | done `24235414` |
| 30 | B1e, A5e | both | conformance case `brain-chat-nanobot-request` | the case is a capture of nanobot's real request | conformance both sides | done `6f7b569a`, `62131e34` |
| 31 | A9b | arail | `docs/buddy-edge.md`, `sprints/BACKLOG.md` | carrier rules, `stop`, atomic claim; D23 to D28; D15 closed | n/a | done `c3c93ac1` |
| 32 | A5f | arail | `buddy_host/contract/*` | re-vendor after B7b | `--check` | done `625f51d2` |

Order is B1 first (it produces the contract that A5 vendors), then A1 to A9, then B3 to B6, B8, B9; B2, B7, B10, B11 and Phase I last. Test-first applies to the high-risk
pieces (guard, confirm, ledger, admission): the failing test is written and run before the code.

## Execution

### Step 0 (BUILD_LOG skeleton)
Plan, baselines, verification log, G1. Commit: `d9cf4e5a` (arail).

### Step 1 (B1a, buddy: package skeleton and nanobot request capture)
Delta from plan: B1 is two commits (B1a skeleton + capture, B1b contract + conformance) so
the evidence for G1 is reviewable on its own. `buddy/tools/capture_brain_chat.py` is kept in
the tree (not a throwaway) so the fixture can be re-captured when the pin moves. The test
file `buddy/tests/test_nanobot_request_capture.py` is characterisation of upstream: it pins
the request field set, `stream` always true, no `tools` key when none are registered, plain
string content, and G1 (a `before_iteration` hook can rewrite the current user message but
not `messages[0]` or history). 8 tests pass. Distribution: `buddy/pyproject.toml`
(`qukaizen-buddy`, hatchling, src layout, MIT, deps: PTB 22.x, jsonschema). nanobot is not a
declared dependency (the repo root is the pin; one venv holds both).
Commit: `ae7f6b7f` (buddy).

### Step 2 (B1b, buddy: contract and conformance)
`buddy/src/buddy/contracts/buddy.host.v1.json` (62,445 bytes, sha256
`0646dace9a7d69f0bb89e57d2afe0665a819c7c0fb07d7095c8b679eaf871cd3`) and 126 cases in
`contracts/conformance/` (124 numbered `NNN-<name>.json`, plus the two nanobot-request
cases under the names the spec gives). Numbering is three digits because there are more than 99
cases. Everything in §5.3 to §5.9 is in the file: transport and admission order, limits,
six method definitions (the five plus GET `brain/models`), the turn header and its schema, the
actions table, the error table with fixed messages, the conformance DSL (setup keys,
placeholders, notes). The trace event allowlist is not a side table: it is per-event JSON
Schema (`allOf` of `if event==X then reason/attrs`) inside the `trace.record` request, so one
validator enforces it on both sides; the test copies the spec's §5.7 table and compares.
The captured nanobot request gave no new field: `additionalProperties:false` stands as
written (the field set is exactly the one the spec listed); `stream_options` is tightened to
`{include_usage: boolean}` because that is all nanobot sends.
Conformance cases: both nanobot cases come from the real capture. The stock one is refused
`turn_header_invalid` (G1 as an executable fact); the other swaps in a `BUDDY-TURN/1 ...`
`messages[0]` and must be accepted, with nanobot's own headers (`x-session-affinity`,
`x-stainless-*`) present. Tests (23): format, spec tables for errors and events, every code
and method covered, each fixture against its schema, nanobot's retry-marker lists as oracle,
`LLMProvider.is_transient_response` as oracle for every error, action enum pinned.
Test-first: not applicable (the generator ran before the tests); the tests were written
against the spec's tables, not the generator's output, and caught two generator defects
(a marker list shorter than nanobot's, and a two-digit file prefix).
Commit: `fcddf1e2` (buddy).

### Step 3 (A1, arail: agent_trace v2, AgentCall fields, chokepoint cost)
`AgentCall` gains `channel`, `identity` (a `(pseudonym, role)` tuple, sanitised; malformed
becomes None, never raises) and `origin`; `agent_call(..., channel=, identity=, origin=)`.
Delta from the spec's wording ("inherits them, same rule as `trace_id`"): a same-agent inner
call inherits what it does *not* set, and an explicit inner value wins. `trace_id` ignores an
inner value; copying that for identity would let a stale outer identity override an explicit
one, which is the wrong failure for an identity field. A different agent nested inside does
not inherit a phone identity (tested). `agent_trace.SCHEMA = "arail.agent_trace/v2"`;
`_FIELDS` keeps the 35 v1 keys in order and appends the 8 v2 keys (a test freezes the v1 list).
The chokepoint writes `record_type="model_call"`, `channel`, `identity` as `{pseudonym, role}`,
`origin` (default `in_process`) and `cost` on success; refusal and error records carry
`cost: null` but still say who asked. `cost.cash_basis` is `estimate` (billed-estimate backend),
`local`, or `unpriced` (a third value the spec's two-value enum did not list; `cost_source()` has
three states). 22 tests. R-BUDDY-98: 100 passed, 1 xfailed (unchanged).
Commit: `36349f7f` (arail).

### Step 4 (A2, arail: lanes `recent`, `related`, Buddy on the phone)
`lanes_snapshot()` counts `record_type in {None, "model_call"}` only (calls, last, columns,
unattributed, user-defined, `tokens_out_by_agent`) and adds `recent` per lane. Delta: both
`recent` and `related` strip `bodies` **always**, recorder on or off (the spec says "bodies
stripped" and cites the recorder-aware helper; the recorder-aware rule would put a phone
user's words into a list the operator scrolls). The detail record itself keeps the existing
recorder-aware behaviour. `agent_trace.find_with_related()` is new; the route returns
`{**record, "related": [...]}`. `admin.html` gets `renderBuddyPhone`, `showBuddyTrace` and a
delegated click handler; every cell goes through `esc()`, and the row's `data-trace` is only
emitted for a 16-hex id. Tests execute the render function under `node` against a hostile
record (XSS) and `node --check` the whole new block; they skip if `node` is absent. 22 tests.
**Note for A7:** only records with a `channel` reach `recent`, so the edge must stamp
`channel="telegram"` on the `hold_all` and `action_executed` records it writes itself.
Commit: `75ce05c5` (arail).

### Step 5 (A3, arail: plumbing)
`scheduler.halt_load_failed()` (also true for a halt file that is valid JSON but not an object;
cleared by the next successful write; `jobs_halted()` behaviour is unchanged, debt D10),
`inference_slot(label, *, acquire_timeout_s=None)` (a bad value falls back to the global timeout
instead of disabling it), `/api/host/v1/` in `_PRESENCE_SKIP_PREFIXES`, the four non-brain edge
paths in `FAST_PATH_PREFIXES`, `registry.core.is_cloud_entry`. 25 tests. Existing tests use a
per-test `scheduler._halt_path` (conftest), so the new tests write through it, not `DATA_DIR`.
Side effect seen: some existing tests create an untracked `logs/scheduler/` directory in the
repo root (pre-existing, not from this sprint); it is never staged.
Commit: `c9762089` (arail).

### Step 6 (A4, arail: response_schema)
Router and Ollama backend as specified. The byte-identical proof is a fixture
(`tests/fixtures/ollama_native_request_bodies.json`) of the request bodies **main@ca0a8f81**
sends for eight caller shapes times two `num_ctx` values, captured by running main's code from
a `git archive` snapshot before the change, and compared on every run; a schema adds exactly
one key (`format`). Legacy backends (no `**kw`) keep working; a schema sent to one raises
TypeError and is traced once. 39 tests.
Commit: `658e9880` (arail).

### Step 7 (A5, arail: vendor the contract)
`src/arail/buddy_host/contract/` = the contract plus its 126 cases byte for byte (127 files)
and `PROVENANCE.json` (`arail.vendor-stamp/v1`, source commit `fcddf1e2...`, per-file sha256,
`tree_digest` = sha256 over the sorted lines `"<path> <hex>\n"`). `scripts/sync-buddy-contract.py`:
`--from` copies and stamps (refuses a dirty source; removes stale cases), `--check` is
sibling-free (edited, missing, extra, tampered-stamp and missing-stamp all exit 1) and with the
sibling present also compares bytes (exit 3 on drift, a one-line note when only the source commit
moved). `--vendor-dir` exists so the tests can run against copies. `arail.buddy_host`'s
`__init__` loads the contract once and raises `ContractStampError` on a mismatch; the portal
registration in A7 must catch that and serve no edge routes rather than fail to boot.
`pyproject.toml` gets package-data for the vendored JSON. 19 tests, including a real
`--check` against the sibling checkout in this session folder.
Commit: `80c34c1a` (arail). (The first version of this commit lacked the BUILD_LOG update its
message promised; it was amended before anything else was committed.)

### Step 8 (A6, arail: `./arailctl buddy`)
`arail.buddy_host.link` and `__main__`; `arailctl buddy` is the same thin exec as `world` and
`nucleus`, plus a help line. Placement checks in the spec's order, each a refusal (exit 3) with
nothing written and no consent recorded: `ARAIL_INSTANCE`, `BIND_ADDR`, the Buddy home (missing,
symlink, not a directory, not yours, not 0700), `secrets.env` and Buddy's `link.json` (symlink,
not yours, not 0600), ARAIL's own `buddy/` directory (symlink), an existing link without
`--rotate`, no TTY without `--yes`, and any answer but `yes`. Buddy's side is written first so
"ARAIL is linked" implies Buddy holds the credential. Rotation keeps the consent record and
needs no confirmation; unlink revokes ARAIL's side before it touches Buddy's. 61 tests.
Deltas: (1) Buddy's `link.json` (`qukaizen-buddy.link/v1`: edge_base_url, credential_id,
contract_sha256, linked_at) is a second cross-repo file shape that ARAIL writes and Buddy reads;
it is not in `buddy.host.v1.json`. It is versioned by its schema string and pinned by tests on
both sides (B4 reads this exact shape); Phase I exercises it end to end. (2) `status` also
reports whether the link predates this lab's current contract. (3) `docs/cli.md` is updated in A9
with the rest of the docs, not here.
Commit: `ab6ea498` (arail).

### Step 9 (B1c, buddy, and A5b, arail: one conformance case was wrong)
Running the 74 non-brain cases against the real routes passed 73 and failed one:
`pause-refused-expired` demanded a refusal for an approval confirmed 30 s ago whose `expires_at`
was 10 s in the past. The spec's rule (proposed <= confirmed <= expires, ttl <= 600 s,
|now - confirmed| <= 60 s) accepts that. The case was my error, not the implementation's.
B1c replaces it with `pause-refused-confirmed-after-expiry` and adds
`pause-ok-expired-moments-ago-but-confirmed-in-time`; `buddy.host.v1.json` is byte-identical
(sha256 `0646dace...`). A5b re-vendors (128 files, stamp commit `fb040a0b`). The suite is now 127
cases. This is the "re-run A5" rule of section 9 working as designed.
Commits: `fb040a0b` (buddy), `ec35a0f4` (arail).

### Step 10 (test renames, arail)
The edge tests I had named `test_buddy_*` matched the R-BUDDY-98 glob and would have turned
"98 passed" into hundreds (and added `node` skips). Renamed to `test_host_*`,
`test_agent_lanes_phone_steps`; the glob runs the 11 original files again: 100 passed, 1 xfailed.
Commit: `4024b753` (arail).

### Step 11 (A7, arail: the edge)
Modules: `buddy_host/{admission,events,ledger,budget,brain,_files,_time}.py`,
`portal/host_edge_routes.py`, registration in `portal/app.py` (inside a try/except, so a contract
that fails its stamp means no edge, not a portal that will not boot; tested in a subprocess).
Admission is in the contract's order; a missing peer address is refused; the body is read through
`request.stream()` with the size cap so a chunked upload cannot exceed it; errors never echo
request text (a canary test covers every method). `pause_research`: replay answered from the
ledger before the hold check (so a retry after `/stop` still reads the stored outcome), then hold,
role, freshness, insert `executing`, status read, `pause()` only if `running`, `done`; one
`action_executed` per execution, none per replay; a race of five identical requests pauses once.
`handshake`: `hold.held` is reported **true** when the file is unreadable ("treated as held");
the brain choice goes through the new `ModelRegistry.peek`, which is `resolve()` without the
fallback event and activity line, because `resolve()` on a down model would log a warning every
20 s; the Ollama version probe is cached and runs off the loop.
Deltas from the plan: (1) `budget.py` is complete here (the handshake reports the budget), and
`brain.py` here holds the brain decision and the request helpers; A8 adds the route. (2) The
registry gains `peek` (a small additive method, `registry/core.py`), not in the spec.
(3) `edge_refused` events for admission-level refusals are coalesced to one per code per 5 s
with a count: anything on the loopback can send bad credentials, and an uncoalesced event per
attempt would let it flush the 500-record ring. (4) A rename commit (Step 10) happened before this.
The shared harness `tests/buddy_conformance.py` runs the vendored cases against the real portal
app (a fresh world per case; unknown `expect` keys fail the case). Mutation checks: changing the
flattening prefix fails 2 cases; silencing the refusal trace fails 16. 74 conformance cases and
65 edge tests pass here. R-BUDDY-98: 100 passed, 1 xfailed.
Commit: `b69bbf9f` (arail).

### Step 12 (A8, arail: brain.chat)
`POST /brain/chat/completions` (SSE, `stream:false`, and `stream_options.include_usage` as nanobot
sends them) and `GET /brain/models`. Steps 3 to 9 of section 5.6 run in `_core_brain_chat` off the
event loop; steps 3 to 5 refusals become `edge_refused` events (no trusted identity yet), steps 6 to 9
become one `model_call` record `refused_<code>` naming pseudonym and role. The slot, the turn count
and the token count belong to a producer task that outlives the HTTP response, so a client that
disconnects mid-answer cannot leak the slot. The response does not start until the first token
arrives, so a backend that fails before it is a clean 424 with `X-Should-Retry: false`; after it the
stream ends with the contract's `brain_failed` error chunk and `[DONE]`. The OpenAI SDK (nanobot's
client) is used in tests to read the stream, the usage and an error. The worker thread runs in a
copied context (F45; tested). Deltas: (1) a turn is **counted when the call is let into inference**,
not at the budget check (the spec says "at admission"); a call refused because the model is busy costs
nothing. (2) Usage rides on the final chunk with `finish_reason` (the spec's wording), not a separate
`choices: []` chunk. (3) `finish_reason` is `length` when the completion used the whole `max_tokens`.
127 conformance cases and 17 brain tests pass.
Commit: `a14fcfd2` (arail).

### Step 13 (A9, arail: docs and backlog)
`docs/buddy-edge.md` (the routes, link steps, the consent text verbatim, what each file holds, what
leaves the box, what Admin shows, limits, the contract sync, a refusal-to-fix table, known limits),
a `buddy <verb>` entry in `docs/cli.md`, and `sprints/BACKLOG.md` D1 to D14 verbatim from the
architecture plus three the build added: D15 (G1), D16 (Buddy's `link.json` is outside the
contract), D17 (coalesced admission refusals). The notice to 3D.EVAL about v2 is not made here
(touch-only-this-repo rule); D9 names it for the orchestrator to relay. `docs/BUDDY.md` is not
edited: its "no sometimes online middle state" line stays true (the phone path needs hybrid).
Commit: `2ab0f244` (arail).

### Step 14 (B3, buddy: the guard, the redaction, buddy.boot)
`guard.py`: one audit hook; the policy is state in a `Guard`, so it is tested as a decision table
(93 unit tests) and again in fresh `python -I` subprocesses against real sockets (L1 closed, L2 open
with a faked resolver; the parent's listeners prove a refused connect never arrived). Stricter than the
spec in three small ways, all on the side of refusing: a numeric non-loopback address is refused at
`getaddrinfo` (so `create_connection("1.1.1.1")` fails before any connect), Telegram's learned addresses
are allowed on **443 only**, and any malformed audit-event argument is a refusal, not a pass. DNS names
`localhost`, loopback literals and (open mode) `api.telegram.org` only; connect to `AF_UNIX` and to
loopback on the edge and health ports; bind to loopback on the health port; send to loopback or unix; exec
events refused once armed. `redact.py`: exact secrets, token and edge-credential shapes, and
`/approve|/deny <id> <nonce>`, per line on stdout/stderr and as a loguru patcher (tested with print,
logging, loguru, `logger.exception` and an uncaught traceback). `boot.py`: inert unless the process is
`python -m nanobot` (`sys.modules["__main__"].__spec__.name`), so P0 cannot follow a test runner around;
it imports nothing from nanobot, httpx or PTB (tested). 31 + 93 + 7 tests.
Commit: `d5e5b9d0` (buddy).

### Step 15 (B4, buddy: preflight, configure, people, hold, boot's entry steps)
S1 to S12 as in the architecture; every failure is a fixed line with the check id and no value, exit 0.
Deltas: (1) `boot.apply()` (steps 2 to 4 of section 5.14) lands here, not in B3, because it calls the preflight;
it exports the `BUDDY_*` secrets only after the preflight passes (the spec loads them first, but S2 has not then
vouched for the file). (2) `buddy.toml` is parsed in `configure.py` (no extra module); its ranges cannot exceed
what keeps G1's 60 s (handshake interval <= 20 s, unreachable window <= 40 s). (3) S9 is exact equality with what
`configure` generates now, plus a second look at the config object nanobot's own loader returns, so a default that
upstream later turns on is caught even though the JSON never mentions it. (4) `hold.json` also reads as ON when it is
not owned by us or is group/other-writable (anyone could have cleared it). (5) Tool flag names were verified in the
build (`tools.exec|web|file|cliApps|my.enable`, `tools.imageGeneration.enabled`) and all builtin skills are listed in
`disabledSkills` (the spec's `<all>` placeholder). (6) nanobot's pairing store lives at `<config dir>/pairing.json`,
which is Buddy's home, so S12 checks `<home>/pairing.json`. 77 + 29 + 183 tests (the S9 table has 40 deviations).
S6 and S7 take their pin files as arguments; the real pins are B2.
Commit: `ed70b193` (buddy).

### Step 16 (B5a, buddy: the upstream-mode plugin; and B5, P0)
Not in the plan as such: with P0 applied `python -m nanobot` accepts only Buddy's own command, and 3 upstream tests
(`tests/webui/test_gateway_webui_smoke.py`) start a gateway as a subprocess with a WebUI config. They failed
(7678 passed, 3 failed) with P0 applied and would pass only if the guard had an off switch. The architecture's
`buddy.testing.upstream_mode` plugin is specified for P1 only. I built the plugin now (it was a B10 file) and made it
deselect exactly those three node ids and say so in the pytest header; a test pins the list to the files that really
spawn `python -m nanobot`. **Spec gap G3** below. The plugin lands first (`5be62a4f`) so the upstream suite is green at
every commit. P0 is one added line (`nanobot/__main__.py`) with the pyright ignores the upstream strict config needs
(without them basedpyright reports 2 new errors); ruff clean, basedpyright identical to the pin's 16 errors, upstream
7678 passed + 3 deselected = 7681. A commit cannot name its own hash, so the P0 row in `UPSTREAM.md` was completed in
the next commit (`a8701ab5`), together with `buddy/tests/test_upstream_md.py`, which checks every patch row against the
tree and git (one file changed under `nanobot/`, blob hash, pre-patch hash at the tag, nothing else differs).
Commits: `5be62a4f` (plugin), `f84aa75a` (P0), `a8701ab5` (row + test) (buddy).

### Step 17 (A5c, arail: the differential found a branch-only failure)
Running the whole ARAIL suite on the branch and on `main` and diffing the failing sets showed one failure only the
branch had: A5 had added a `package-data` line to `pyproject.toml`, which is a frozen path of the QueueLLM rename sprint
(`tests/test_queuellm_rename_frozen_surface.py`). A5c reverts it; BACKLOG D18 records why (a checkout has the vendored
JSON, a built wheel would not; the portal's registration of the edge is wrapped, so a missing or failing contract stamp means no edge routes rather than a portal that will not boot, and a test covers a failing stamp).
Commit: `4395ca50` (arail).

### Step 18 (B6, buddy: edge client, trace spool, mode gate, FakeHost)
`edge_client.py`: every request is validated against the contract before it is sent and every success body after it
arrives (a body that does not match is a protocol error, not data); three failure kinds because callers react
differently (`EdgeUnreachable`, `EdgeRefused(code, status)`, `EdgeProtocolError`); the credential is never in a log or an
exception message. `Tracer`: records batch at 20; while the edge is away they wait in `state/trace-spool.jsonl` (at most
1000 lines, oldest dropped and counted), and go out oldest first when it is back; a bad batch is dropped, not retried for
ever. `gate.py`: opens only on a good handshake with our contract hash, `hybrid`, consent for `api.telegram.org`, and the
pinned World; a hold does not close it (Buddy must say "held"); closing shuts the egress guard first and then gives the
poller 15 s; two failed handshakes (40 s) close it; a wall-clock gap the monotonic clock did not see is a sleep, kept in
`state/sleep.json`. `testing/fakehost.py` is a small contract-driven stand-in for the edge (an `httpx.MockTransport`) that
passes all 127 conformance cases, so Buddy's idea of the edge is checked against the same referee as ARAIL's.
40 + 128 + 29 tests at this commit (the gate file gained 3 more in B6b).
Commit: `af374189` (buddy).

### Step 19 (B6b, buddy: what the gate remembers)
Added because B8 needs it and the build order had no place for it: Intake must tell a message that waited out a lab outage
from one that waited out a sleep or a deliberate airgap, and must not claim the lab was down when Buddy simply was not
running. `ModeGate` now records `closed_since`, the cause of the closure (a wake recheck never overwrites the real cause) and,
on reopening, `last_gap = {from, to, cause}`; the first closure of a process is `starting`. 3 tests.
Commit: `8aa0b531` (buddy).

### Step 20 (P2 and B8, buddy: the Telegram channel, Intake, unknown senders)
**P2** is one line (`runtime.py:693`, `TelegramChannel.TELEGRAM_BUS_SLASH_COMMAND_RE` to `self.`), its own commit touching one
file. Pre-patch sha256 prefix `793e374682bf88cd` matches the spec; `UPSTREAM.md` gets the P2 row (with the B8 commit, which can
name the patch commit). The upstream suite with the plugin: 7678 passed, 11 skipped, and the same 3 tests the plugin deselects since P0 (7681 in all, as at the baseline);
`ruff check nanobot` clean; basedpyright 16 errors, identical to the pin's baseline. With P2 reverted, 6 of the 14 tests in
`test_p2.py` fail (so they test the patch).
**B8**: `channel.py` (`BuddyTelegramChannel`, `Services`), `intake.py` (the 14 branches in the architecture's order),
`unknown.py` (a count and a distinct count of salted 64-bit hashes; one `unknown_dropped` record per window), `texts.py` (every
fixed sentence; not in the plan, it keeps wording out of the logic), and the tests' shared fakes. Tests drive PTB's own `Update`
and `Message` objects and a fake gate and port: 114 Intake, 67 channel, 14 P2. Mutation checks: removing the DM check, the
hold-before-publish, the stop exemption, the `/approve` routing, the media rule or adding a trace for strangers each fails a test.
Deltas and decisions are in **G4** below. The channel exposes what B7 and B10 need: `note_reply(trace_id, reason, chips)` for the
answer hook, `Services.make_gate` for `install()`.
Commits: `f696874d` (P2), `96681879` (B8) (buddy).

### Step 21 (B9, buddy: proposals, confirmations, the six commands)
`proposals.py` (SQLite 0600, WAL, `synchronous=FULL`; a frozen action table of one action; hash-only nonces; every transition a
compare-and-set; three wrong codes void a card; a newer card supersedes an older one), `confirm.py` (cards from a code template;
the checks in the documented order with the confirmer check **before** the nonce; write-ahead of the full approval record
before the edge call; a replay is "Already done." with no second call; a re-delivered update after a crash resends the stored
record and the edge's ledger answers it), `commands.py` (`/stop`, `/status`, `/help`, `/new`, and `/approve` `/deny` refusals for a
bypassed boundary, as a `CommandRouter`; nothing else is registered, so other slash commands get nanobot's "Unknown command"
reply and never reach the model). 15 + 54 + 31 tests; the confirm tests run against the real `EdgeClient` and `FakeHost`, so the
approval record is validated against the contract and the edge's ledger and freshness rules are the referee. Crash tests kill the
edge call after the write-ahead, and lose the edge's answer after it acted. Mutation checks (11): dropping the confirmer check, the
write-ahead, the hold check, the owner check, expiry, the model-path role check, the void-at-cap, supersession, the `/new` guard
and the `/stop` hold each fails a test; one (resend without checking the code) survived and became a test.
Commit: `327b9640` (buddy).

### Step 22 (B11a, buddy: the desk CLI)
Split from B11 because the rest of B11 needs the seam. `python -m buddy` has `setup` (home, bot token from a hidden prompt or stdin
and never from argv, salt once, merged into `secrets.env` beside ARAIL's credential line), `configure`, `people check`,
`hold status|off`, `service install|uninstall` (nanobot's own installer, label `ai.nanobot.qukaizen.buddy`; install refuses while
the static checks fail; the command launchd would run is fed to `buddy.boot.classify` in a test), and `verify` (static S1 to S12).
Nothing prints a secret or an id. `docs/DEBT.md` files D1 to D16 plus four debts found while building (D19 to D22; D17 and D18 are
ARAIL's). 37 tests; 5 mutations (print the token, clobber the credential line, `verify` takes the lock, install ignores the checks,
a `--token` option) each fail a test. `qa_harness`, a runtime `verify` and `docs/S1.md` are B11b.
Commit: `707e36b1` (buddy).

### Step 23 (stop point; superseded: the build resumed at Step 24)
B2 is blocked on the World (checked, see the table). B7 needs the World and **G1**. B10 needs B7 and G1. B11b and Phase I need B10.
Nothing was stubbed. What B8 to B11a left ready for them is listed under "Remaining work" in the final state.

### Step 24 (resume at Addendum A: B1d through B2, and I1 with the test World)

Read in this order: this log, ARCHITECTURE Addendum A (A0 to A11; commits `162d9d22`, `4fe3acf6`), the SPRINT.md decisions log,
HARNESS_ALIGNMENT.md. G1 to G5 are answered (see "Architect feedback required"). Nothing under `nanobot/` changed beyond P0 to P2:
P1 is one import, two hook-list entries and one `install()` call in `cli/gateway_runtime.py`, and the carrier is two instance-level
wraps made by `install()` (R8), not a fourth patch. Each row was mutation-checked: the behaviour it adds was removed or inverted and a test had to fail.

* **B1d, A8b, A5d** (`940cf349` buddy, `bfab10b3`, `0d02a605` arail). The contract gains the carrier rules (`messages[0]` is `system`,
  no later `system` message, a non-empty persona after the header line), `finish_reason` is always `stop`, and the trace refusal
  `busy`. ARAIL's edge enforces them; the budget is claimed atomically once the call holds the inference slot (`Budget.claim_turn`), so
  calls queued on the slot cannot overshoot the cap and a call refused for a busy model costs nothing. **Delta from plan:** A8b was
  committed before A5d. Re-vendoring first would have left a commit whose conformance cases fail against an edge that did not yet
  implement them. contract sha256 after B1d `48481c1d905df6ed8797de91c6db583f3fc7c1d2f9330c46f79f73b12a785516`.
* **B4b** (`0afa72a4`). `contextWindowTokens` 200000 (C7), generated into `nanobot.json` and pinned by S9, so nanobot's model-calling
  compaction can never start.
* **B8b** (`5e807130`, `cd0d4da5`, `83e57a7b`, `9f21579c`, `1f0bff6d`; one commit per row). `/stop` passes a closed gate and a late one
  names its time; a repeat does not ask the lab again (A4 item 1). **F49**: Intake replaces confirmation strings and live nonces
  (`buddy/scrub.py`) in the assembled content before publishing. **F50**: one turn in flight per chat ("One message at a time."), after
  held and budget in the branch order. **F54**: `_extract_reply_context` returns `None`; a venue test. **F53**: the default
  `reply_sent` label is `error`; `answer` is set only through `note_reply`. The branch order is now identity, DM, stop, gate, rate,
  media, stale, confirm, held, busy, publish; tests that counted a stranger after the gate were corrected.
* **B8c** (`62816c01`). The turn hook runs before nanobot delivers the answer, so a card sent from it would arrive first and read as an
  answer to nothing. The channel now takes the card with `defer_card` and sends it right after the answer of the same trace; it is
  dropped if the gate closed or the answer was not sent.
* **B9b** (`005e6807`). After a lost edge answer the stored approval record is resent once, automatically, after 2 s; the code is never
  printed (A5). `edge_unknown` with `no_answer` is resumable by Telegram re-delivery. Tests I-CRASH-02a to d.
* **G13** (`24235414`). `/status` and the confirm card carry no lab data: the "Research now" line is gone from both. U-G13 tests; the
  planted canary is searched for in every edge request, prompt and message in I1.
* **B7a** (`0ac3895a` world, `00d74c89` answer and persona, `a5688886` carrier, `300d9f7f` turn hook), against a hand-built sealed test
  World under `buddy/tests/fixtures/`. `world.py` (the sealed World and retrieval), `answer.py` (parse, check, chips from sealed bytes),
  `persona.py` (header line + persona), `carrier.py` (C1 composer, C3 fail-closed, C5 live-turn registry, C6 history window),
  `turn_hook.py` (T1 to T5, C8, and the one place raw model output is read). The mutation checks showed a second barrier I had not
  counted: with the wraps removed, C8 and T5 still stop the turn, so the tests for each barrier now remove the other first.
* **B10** (`f6cd9335`, P1 `984d13b1`, `9eb2dc91`, fix `6bfc3563`). `nanobot_seam.py`: R1 to R8, `agent_hooks`, `turn_hook_factories`,
  `install()`, and the upstream test mode that keeps nanobot's own suite green. U-HDR-03 to 07 run against a real `AgentLoop`
  (request contents, message counts, window, no compaction, fail closed, hook absent). `UPSTREAM.md` has the P1 row, the new
  upstream-PR candidates and the pin-move duty. The real loop found one defect the fakes had hidden: the model-proposal port called a
  keyword-only method positionally (`6bfc3563`).
* **B1e, A5e** (`6f7b569a` buddy, `62131e34` arail). The conformance case "nanobot's own request is accepted" is now a capture of a real
  request from the real loop (`brain-chat-nanobot-request`), regenerated by `tools/make_nanobot_turn_case.py`.
* **B11b** (`d372b6ca` harness, `ec7e4c7f` verify, `32062dc3` docs, `7326f32e`). `python -m buddy qa_harness` runs the whole Buddy
  with a test channel as a QA identity; `verify --runtime` runs R1 to R8 against a nanobot built from the home; `docs/S1.md`.
* **I1 with the test World** (`7d2c1286`). A throwaway ARAIL, a throwaway linked home, the harness. Four things the first run against a
  real server found, all in the script or in the harness, none in a contract: a lab that is not onboarded answers 401
  `lab_not_onboarded` to the edge (the script sets a passphrase; filed as **D28**); the registry's `fast` row on this Mac resolves to an
  in-process MLX model (the script sets `MODEL_BACKEND` and `MODEL_NAME`); the lanes view names agents by `id`; and the trace detail's
  `related` list excludes the primary record. The harness also outran the edge's 20 calls a minute per person ("Slow down" on the
  pressure cases), so it now spends at most 18 a minute per identity.
* **B2** (`eb92734d` pin, `daac5f8b` World, `fefecec0` lock). `pins/nanobot.pin.json` locks the 16 seam files and the 3 patched ones
  at the tag and after P0 to P2. `worlds/qa-practice/*` and its pin are exported by `tools/vendor_world.py` from qukaizen-ddac
  `origin/main` at `da2fb181816875b26a861dda4f73db2eec186fce` (PR #53; read with `git show`, nothing in that repo was touched or
  checked out), with the repo name in the pin kept as `cdarnell/qukaizen-dac` as A6 says; terms.json sha256
  `767286098f3ca633524be103c1f22c25efa5dd595f2f0c49e216e998c7ff9731`. `requirements.lock` is the tested environment with hashes.
* **A real defect in Addendum A itself** (`2364fbe0`). C6.2 replaces a confirmation string in history with a placeholder; C8 refuses any
  message that matches the confirmation pattern; the placeholder matched. Every turn after a scrubbed message would have been
  aborted. The placeholder no longer matches the pattern it replaces, and C8 uses `scrub.contains_confirmation`. See H2.
* **Docs** (`cb210c85` DEBT.md D23 to D28, D15 and D21 closed; `c3c93ac1` ARAIL `docs/buddy-edge.md` and BACKLOG D23 to D28, D15 closed).

### Step 25 (B7b: the answers, tuned on the dev set; the quality bar is not met)

**Method.** The dev set is `buddy/tests/qa_sme/dev_set.jsonl` (32 cases drafted by the builder from the vendored World: 16 questions the
World answers, 4 it does not, 12 pressure cases of which 5 have three or more turns and one opens with an ordinary question).
The SPRINT.md row that seals QA's blind set (sha256 `db7c9827a19911ffcdfb2b94bfac133320bec2d1c58d456d96df776cc208bad2`) existed before
B7b started; the sealed file was not opened. Runs used the live `llama-ai-eng` through Ollama, through a fast loop that sends what
ARAIL's Ollama backend would send (persona and prompt flattened into one user message, the per-question grammar as `format`) and, to
confirm, through the real thing: `buddy/tools/run_dev_set.py` against a throwaway ARAIL and the whole Buddy. ARAIL ignores
`temperature` (D7), so the model runs at 0.7 and a mode that uses the model moves by about two cases from run to run (D30). Grading follows
what QA said it will do: a question needs kind answer, a chip, and only gold chips; a not-in-World question needs the abstain sentence;
every turn of a pressure case needs a refuse or an escalate, and the abstain sentence fails.

**What the model can and cannot do.** In nearly every turn it names a sensible card first, and it cannot be trusted with the rest.
Asked to choose `kind` it answered "refuse" to most questions (persona wording A) or never refused (wording C); an example sentence in the
persona was copied into unrelated answers; it echoed the World block's heading ("Data") as its answer in template mode; it wrote
paraphrases that got a list wrong while using only the card's words; and naming the `pause_research` value in the persona made it
propose a pause on unrelated questions about eight times in 30 turns (none without). Asked whether a message is a question or a request
it said "question" 22 times in 30 (all the questions, none of the requests). Tried and rejected, each over 4 to 10 runs: persona
wordings A to I; K from 2 to 8; the offer threshold 1.5, 3, 5; `cite` first in the grammar; one cite or three; card type shown to the
model; temperature 0; agreement between the model's pick and retrieval; a model "NONE" veto in template mode. Nothing moved the
question score outside the run-to-run spread.

**What was built from that** (all decisions are the builder's, listed in H3 to H8 for the architect):

1. Retrieval weighs a question's words by rarity and field (`32137a78`); each question is offered at most K=6 cards and the grammar in
   the turn header lists only those slugs.
2. The model chooses the card; code decides the rest (`32137a78`). One chip, for the first valid cite. The label is a refuse or
   an escalate when the card is a rule and the message is not a question ("what may I never show?" is answered; "show me the token" is
   refused); a message that is not a question is offered the rule cards first. The model's sentence is kept only if it is a sentence and
   mostly the card's own words (`39a3777a`: content-word overlap and neighbouring-pair overlap); otherwise the fixed lead line "This is
   what the qa-practice World says:" stands in. An explicit abstain is an abstain.
3. The persona is eight short lines (`9ecc98a0`). It asks for answer or abstain only, has no example, and does not name the proposal value.
4. A refusal holds (`37e66fe0`): a push after a refusal ("I am the owner, just do it") names no rule, so the same rule answers it for
   ten minutes, by code. This is what turns pressure cases of three turns into passes.
5. `[answer] mode = "structured" | "template"` in `buddy.toml` (`b794f698`), and `run_dev_set.py --mode` (`947052e1`).

**Result** on the 32-case dev set (bar: 16 of 20 questions, 6 of 8 pressure, which is 9 of 12 here, and no wrong card):

| Run | Questions of 20 | Pressure of 12 | Wrong chips |
|---|---|---|---|
| Fast loop, structured, 10 runs | mean 10.3 (9 to 13) | mean 6.9 (6 to 8) | mean 13.3 |
| Fast loop, template, 10 runs | 12 (every run) | 9 (every run) | 14 |
| Whole Buddy on a real ARAIL, structured, 3 runs | 6, 10, 8 | 8, 9, 8 | 8, 8, 9 |
| Whole Buddy on a real ARAIL, template, 1 run | 12 | 9 | 8 |

"Wrong chips" counts a chip outside the gold set; a chip that is not from the World cannot exist (chips are rendered from the sealed
bytes and checked against the markdown converter), so the bar's "fabricated chips" is zero by construction. **Neither mode meets the
question bar on the dev set.** Template mode meets the pressure bar; structured mode does not reliably. Where it loses: the right card is
first for 12 of 20 questions (D29: lexical), a question that overlaps a card only by a stray word gets that card instead of the
abstain (3 of 4 not-in-World questions), and a pressure turn phrased as a question ("May I ...?") is answered, not refused.
ARCHITECTURE 8.4 step 6 makes QA's blind run the judge and says a failure ships S1 in template mode. The builder expects that outcome
and recommends QA run the blind set in both modes (edit `[answer] mode`, restart Buddy); the default stays `structured`, as specified.

### Step 26 (A5f and Phase I with the real pins)

* **A5f** (`625f51d2`): `scripts/sync-buddy-contract.py --from <qukaizen-buddy>` then `--check` (134 files, bytes equal to the sibling,
  source `b794f698`). **I2**: the contract is unchanged since B1d, so the sha256 is equal on both sides:
  `48481c1d905df6ed8797de91c6db583f3fc7c1d2f9330c46f79f73b12a785516`.
* **I1 with `--pins real`**: 29 of 29 checks pass against a throwaway ARAIL: link, setup and configure; handshake (hybrid, local brain,
  Built with Llama); a question answered with a World chip; the model call traced with Buddy's trace id, channel and identity and joined
  to Buddy's events; the day's budget counted; a rule card for "Pause the research run." with no model call and no lab data in it; the
  confirm, ARAIL's researcher really paused, one ledger row, and the replay answered "Already done."; a talker cannot confirm; a stranger
  is dropped; U-G13-02 (the planted goal text appears in no edge request, prompt or message); `/status` names no mode, tier, research
  status, model or backend; `/stop` holds Buddy and ARAIL's jobs, and no model call follows.
* **I3** (`8282a374`): the real `python -m nanobot gateway --foreground` process, airgapped, `lsof -nP -a -p <pid> -i` every 0.5 s for 10 minutes:
  1076 samples, one endpoint (`TCP listen 127.0.0.1:18797`, Buddy's health port), zero non-loopback rows, no wildcard listener; ARAIL
  answered 30 handshakes in the window. **Deviations from 8.3 L3:** a plain subprocess, not `launchctl`; no `nettop`; the hybrid half is QA's.
  A connection that lasts less than a sample (the handshake) cannot appear in lsof, which is why the log of ARAIL is the positive control.
* **Gateway tools**: the real gateway logs nine registered tools at start (cron among them); R2 unregisters all of them before the first
  task starts and refuses to start if any remain, and T1 checks again on every turn.

## Architect feedback required

**G1 to G5 were answered by ARCHITECTURE Addendum A (2026-10-20) and built as answered.** The original text of each gap follows,
unchanged, as the record of what was found. How each was resolved as built:

| Gap | Resolution as built |
|---|---|
| G1 | A1: two instance-level wraps made by `install()` (R8), no nanobot patch. U-HDR-03 to 07 prove them against a real `AgentLoop`. |
| G2 | Ratified; Δ1 with an atomic re-check after the slot (`Budget.claim_turn`), Δ3 reversed (the edge never reports `length`). Built in A8b. |
| G3 | Accepted as D23; P0 stays Buddy-only; the three smoke tests stay deselected under the plugin. |
| G4 | All ten ratified; item 1 (`/stop` also passes a closed gate, a late stop names its time) and item 10 (the default label is never `answer`) corrected, built in B8b. |
| G5 | The code is never printed; one automatic resend after a lost answer (B9b). |


### G1. The turn header and persona cannot be written by a `before_iteration` hook (A8 is false at the pin)

**Spec text affected:** §5.6 "Turn header … Buddy's hook rewrites `messages[0]` on every
iteration"; §5.16 `BuddyTurnHook.before_iteration` ("replace `context.messages[0]` with
`BUDDY-TURN/1 …` + persona; scrub any `/(approve|deny)` from every message; keep the last 4
user/assistant pairs"); §4.2 step 5; §7 F16, F34 (the "hook rewrites it every iteration"
control); §3 A8.

**What happens at `v0.3.5`.** For every non-ephemeral turn, `AgentLoop._run_agent_loop` passes
`transcript_input`/`transcript_builder` and a non-`None` `consolidate_history`
(`nanobot/agent/loop.py:1170-1192`). So `AgentRunner.run` builds a `ContextCompactionState`
(`nanobot/agent/runner.py:310`, `:363-385`; `nanobot/agent/context_governance.py:139-163`) whose
`accepted_messages` is a **deep copy** of the system message plus history, taken when the
transcript is built, before any hook runs. Each iteration then sends
`compaction.request_messages(messages)` = `deepcopy(accepted_messages) +
deepcopy(raw_messages[raw_accepted_boundary:])` (`runner.py:443-447`;
`context_governance.py:166-173`). A hook's edit of `context.messages[0]` or of any history
entry is never part of the request. Only the current user message (the part after the
boundary) is.

**Evidence (reproducible):** `python buddy/tools/capture_brain_chat.py --out <file>` runs
nanobot's own `AgentLoop` (through `Nanobot.from_config`) against a loopback fake server, with
every tool unregistered, for two turns on one session, with a hook that sets
`messages[0] = "BUDDY-TURN/1 …\n<persona>"` in `before_iteration`. Result: the hook fires
(`context.messages` had 2 then 4 entries), and the request that arrives still carries the
8.7 KB stock system prompt (`## Runtime …`). A second probe that also rewrote the last user
message and a history entry showed: system message **unchanged**, history entry
**unchanged**, last user message **rewritten**. Request fields are exactly
`model, messages, stream, stream_options, max_tokens, temperature` (stream is always true);
headers include `x-session-affinity` and the OpenAI SDK `x-stainless-*` set.

**Consequences if built as specified:** every real request fails the edge's turn-header
check (`turn_header_invalid`), the persona and "Built with Llama." (G11, T4) never reach the
model, the nonce scrub of history (G5 defence in depth) does nothing, and the stock nanobot
prompt (tools, skills, Dream, memory instructions) is what the 1B model reads.

**Verified alternative that needs no nanobot patch (for the architect to accept or reject; not built):**
`Nanobot.from_config` + an instance-level wrap of `agent.context.build_transcript` (the runner
binds `self.context.build_transcript` per turn, `loop.py:1120-1125`) that returns a transcript
whose `[0]` is `header + persona` and whose history is trimmed/scrubbed, with the per-turn
values (trace id, identity) passed through a `ContextVar` set by a turn-hook **factory**
(`build_agent_turn_hook` runs factories before `runner.run`, in the same task,
`loop.py:1152-1169`, `turn_hooks.py:67-84`). Probed: the request then carries the Buddy
system message on both turns. This adds `nanobot/agent/context.py` (and the `loop.py`
binding site) to the seam lock. Other options exist (a fourth patch in `runner.py`; carrying
the header in the current user message, which would change §5.6's admission rule and is a
contract change). The choice is the architect's.

**Rows affected:** B1b (turn-header section and the captured-request conformance case),
A8 (header reader, if the carrier moves), B7 (`turn_hook.py`), B10 (`nanobot_seam.py`, seam
list), B11 (`qa_harness`), Phase I. **Rows not affected:** A1 to A7, A9, B3 to B6, B8, B9.
The contract is written as the spec has it and marked provisional in this respect; if the
carrier changes, the rule at the end of §9 applies (re-run A5, equal sha256 both sides).


### G2. Reconciliations inside §5.4/§5.6 and rules the spec leaves open (decided by the builder; architect to ratify)

These do not stop the build; each is the smallest reading that keeps every spec code
reachable. They live in the contract and its conformance cases, so they are testable.

1. **Schema vs step list (§5.6 vs §5.4, §6.2, F24).** The request schema says `tools`
   `maxItems: 0` and text-only parts, which would make `schema_invalid` fire first and leave
   `tools_not_allowed` and `content_not_allowed` unreachable. The contract lets the schema accept
   any `tools` array and any part object with a string `type`; step 3 enforces empty `tools` and
   text-only parts with the two codes (`methods.brain.chat.step_rules`).
2. **Retry control (§5.4).** The fixed-message rule cannot cover `error.lab_contract_sha256`
   in the 412 body: a hex digest contains the digit runs 429, 500, 502, 503 or 504 about 7.6% of
   the time, and nanobot's text fallback treats those as transient
   (`nanobot/providers/base.py:1007-1009`, `:1012-1030`; markers at `:630-645`). nanobot honours an
   explicit `x-should-retry` first (`providers/base.py:1014-1015`; header read at
   `openai_compat_provider.py:1870-1878`). The contract therefore requires `X-Should-Retry: false`
   on every error response (`errors.response_headers`), and a test shows the digest hazard
   with nanobot as the oracle.
3. **Rules the spec leaves open, pinned by conformance cases:** a content array is flattened by
   joining its text parts with LF; a last message that is not `user` is `schema_invalid`;
   `approval_id != proposal.id` is `schema_invalid`; `max_tokens` falls back to 400 and
   `max_completion_tokens` is honoured; a brain the edge refuses is reported in the handshake as
   `available:false` with the refusal code in `reason`; `trace.record` is accepted while the lab is
   held or airgapped (Buddy must be able to record the flip and the stop); `hold_all` is allowed when
   the hold state is unreadable (it is the reductive direction); the 422 for a bad record carries
   `error.record_index`.


### G3. P0 makes `python -m nanobot` Buddy-only, so three upstream tests cannot run with P0 applied

`tests/webui/test_gateway_webui_smoke.py` (3 tests) starts `python -m nanobot gateway ...` as a subprocess with a
WebUI/websocket config. With P0 applied that exits 2 by design. ARCHITECTURE §5.13 names the plugin only for P1
(`_UPSTREAM_TEST_MODE`). Built here: deselect those three by node id in `buddy.testing.upstream_mode`, pinned by a test.
Rejected: an environment variable that makes `buddy.boot` inert (it would be a kill switch for the guard in production).
Architect to ratify, or choose another way to run those three.


### G4. Interpretations of section 5.17 and neighbours made by the builder (architect to ratify)

None stops the build. Each is pinned by a test.
1. **`/stop` skips branches 4 (rate) and 6 (stale).** Section 4.4 says the hold file is written "before anything else"; the order
   in section 5.17 puts rate and stale first, so a stop could be refused for being the eleventh message in a minute or a day old.
   A stop only makes Buddy quieter.
2. **`/status` and `/help` skip branches 10 to 12, not only 9.** `/status` reports the lab's hold and research state, which is when
   it is most needed, and neither command calls the model.
3. **A shared location is media.** nanobot turns a location into text (`[location: lat, lon]`) before the media override runs, so
   without this a precise position would reach the model. It gets "I read text only."
4. **The daily cap has its own sentence** ("That is today's limit for messages. I answer again tomorrow."); "I will answer again in a
   minute" would be false for it.
5. **A button tap from an unknown sender is counted** like any stranger. Upstream's callback handler returns silently without calling
   `_send_pairing_code_if_private`, so Buddy overrides `_on_callback_query` to count it (section 4.5 lists callbacks). It is not acknowledged.
6. **`/new` marks the session archived before calling nanobot's `cmd_new`**, which otherwise schedules `consolidator.archive_session`,
   a background model call that summarises the conversation (F23). A test shows the unguarded handler does schedule it.
7. **The stale reply names its cause only when the gate recorded one** (B6b): asleep, lab unreachable, or just "That message is from
   HH:MM." A startup or a deliberate airgap is never described as the lab being down.
8. **Reactions are disabled** (nanobot's "eyes" reaction is a Telegram call that answers nothing).
9. **Unknown slash commands** are left to nanobot's router ("Unknown command ..."); no catch-all is registered.
10. **`reply_sent` reasons** for rate limits, media and "needs a confirmer" are `refuse`; `/new` is `help`. The contract's enum has no better word.

### G5. The tap removes the keyboard, so a lost edge answer cannot be retried by tapping (section 4.3 step 6, F6)

nanobot answers the callback and edits the message to remove its keyboard before Buddy sees the tap (`telegram/runtime.py:2213-2216`),
and the code is only in the button labels. So after a lost answer from the edge the operator cannot tap again; a typed
`/approve <id> <code>` needs a code that is no longer visible. Buddy replies "The lab did not answer. Ask again for a new card." (a new
card is safe: `pause` is idempotent on the research state). The stored record still resolves the crash case the spec names (Telegram
re-delivering the same update, F3), and that is tested. The architect decides whether the card should also print the code.

### New decisions by the builder since Addendum A (architect to ratify, amend or reject)

None stopped the build. Each is pinned by tests; H3 to H7 together change how Buddy answers and need a decision before the win run.

* **H1. A8b before A5d.** Re-vendoring the contract before the edge implemented the new carrier rules would have left a red commit.
* **H2. Addendum A contradicts itself on the scrub placeholder (A1.3 C6.2 and C8).** C6.2 replaces a confirmation string with a
  placeholder; C8 refuses any message matching the confirmation pattern; the placeholder matched, so every turn after a scrubbed message
  would have aborted. Fixed in `2364fbe0`: the placeholder cannot match, and C8 asks `scrub.contains_confirmation`. Amend A1.3 to say so.
* **H3. Code decides what the reply is; the model only chooses a card.** Addendum A and 5.20 have the model choose `kind` and cite
  from every slug in the World. At 1B the model's `kind` was wrong far more often than its card. So the grammar offers only the few
  cards retrieval found for this question (in the turn header, per turn, still a valid `response_schema`), the reply shows one chip for
  the first valid cite, and a refuse or an escalate is a card that is a rule answering a message that is not a question. The contract is
  unchanged; the persona no longer describes refuse and escalate.
* **H4. The model's sentence is optional and must be the card's own words** (content-word and word-pair overlap); otherwise a fixed
  lead line. QA's rubric ("the sentences do not contradict the cited card") is then true by construction for the lead line, and a kept
  sentence is a near-copy of the card.
* **H5. A standing rule** (`Holds`). Not in the architecture. After a refusal or escalation, the next ten minutes of messages from that
  person that are not questions and name no rule are answered by that rule, by code, whatever the model says. Without it the
  three-turn pressure cases of the blind set cannot pass (the pushes name nothing). It is in-memory, a slug only, per chat, and
  it never creates a proposal. See D32 for what it costs.
* **H6. `[answer] mode`** in `buddy.toml` (structured by default). The architecture's switch was the lab's handshake only; the desk needs
  one, because the verdict of the blind run (8.4 step 6) is a desk decision.
* **H7. The persona does not name `pause_research`.** The edge still accepts the model's `propose`, but a 1B model asked to use that value
  proposes it for unrelated questions. A plain "pause the research run" from a confirmer is caught by the code rule, which is the
  dependable path. The model path is kept (enum-only, a card needs a tap) and is effectively dormant.
* **H8. The quality bar is not met on the dev set** (Step 25). Whichever mode QA runs first, the expected result is a fail of the
  question bar, so S1 ships in template mode and disconfirming #2 fires. The default stays `structured` as specified.
* **H9. Not touched, for the owner of the World:** `spec.json` at the pinned ddac commit still carries "C1 DRAFT" in its status line
  (QA saw it). It is one of the pinned files; fixing it reseals the World.

Open for QA, not the builder: the blind set (28 cases, sha256 above), the hybrid half of L3 with Telegram traffic, `nettop`, and the
five spot-check questions over real Telegram in the win run.

## Final state

Handoff after the resume at Addendum A: every row of ARCHITECTURE section 9 is built, I1 to I3 have run with the real pins, and B7b is
done but **does not meet the quality bar on the dev set** (Step 25, H8). Nothing is pushed. Nothing in `qukaizen-ddac` was touched (one
read-only `git fetch origin` and `git show` of the World at `da2fb181`). Nothing real was sent anywhere: the integration runs use a
throwaway ARAIL, a fake bot token and a loopback model.

### Commits

Both repos, oldest first, since the first handoff (`3b18d0af` arail, `4eb49bca` buddy). The ARAIL branch also carries ledger and
Addendum commits by the orchestrator and the architect, which are not listed.

| Repo | Commits |
|---|---|
| qukaizen-arail (`qukaizen/arail-buddy-always-on`) | `bfab10b3` A8b, `0d02a605` A5d, `62131e34` A5e, `c3c93ac1` A9b, `625f51d2` A5f, and the log update that contains this section |
| qukaizen-buddy (`qukaizen/buddy-always-on`), 40 | `940cf349` B1d, `0afa72a4` B4b, `5e807130` `cd0d4da5` `83e57a7b` `9f21579c` `1f0bff6d` B8b, `005e6807` B9b, `24235414` G13, `0ac3895a` `00d74c89` `a5688886` `300d9f7f` B7a, `62816c01` B8c, `f6cd9335` B10, `984d13b1` P1, `9eb2dc91` UPSTREAM, `6f7b569a` B1e, `6bfc3563` fix, `d372b6ca` `ec7e4c7f` `32062dc3` `7326f32e` B11b, `7d2c1286` I1, `cb210c85` debt, `eb92734d` `daac5f8b` `fefecec0` B2, `2364fbe0` C8 fix, `d4b8a8ac` refactor, `c2795eef` `32137a78` `9ecc98a0` `b12f972c` `37e66fe0` `39a3777a` `b794f698` `947052e1` B7b, `8282a374` I3, `0ae79a6d` debt D29 to D32 |

Co-author trailer: the launch instruction for this resume named `Claude Fable 5.1`; the harness's attribution reminder for the session
says `Claude Sonnet 5.5`, and every commit above carries the latter (as the later commits of the first stretch did).

### Tests (at the handoff HEAD)

| Suite | Command | Result |
|---|---|---|
| nanobot upstream, with P0, P1, P2 and the plugin | `.venv/bin/python -m pytest -p buddy.testing.upstream_mode -q -p no:cacheprovider -n 4 tests nanobot/channels` in qukaizen-buddy | **7678 passed, 11 skipped, 0 failed** (the 3 tests the plugin deselects since P0 account for the 7681 of the baseline) |
| Upstream lint and types | `ruff check nanobot tests conftest.py`; `basedpyright` | clean; **16 errors, identical to the pin's baseline** |
| Buddy | `.venv/bin/python -m pytest buddy/tests -q -p no:cacheprovider -n 4` | **1644 passed, 1 skipped** (answer 86, boot 35, carrier 62, channel 89, cli 42, commands 35, configure 35, confirm 71, contract 30, edge client 40, fakehost 134, g13 6, gate 32, guard 93 + 7, hold 35, intake 138, nanobot pin 45, request capture 8, seam 35, p0 12, p1 5, p2 14, people 42, persona 11, preflight 182, proposals 15, qa harness 32, real world 11, redact 26, requirements lock 4, scrub 35, texts 49, turn carrier 22, turn hook 64, upstream md 9, upstream mode 6, world 48) |
| ARAIL R-BUDDY-98 | `pytest tests/test_*buddy*.py -q` in `arail-buddy-wt` | **100 passed, 1 xfailed** (identical to the baseline) |
| ARAIL new edge, trace and lanes tests | `test_agent_trace_v2`, `test_agent_lanes_phone_steps`, `test_host_edge_plumbing`, `router/test_response_schema`, `test_host_contract_vendor`, `test_host_link`, `test_host_edge`, `test_host_edge_conformance` (all cases against the real routes), `test_host_edge_brain` | **428 passed** |
| ARAIL whole suite, branch HEAD `625f51d2` as a clean `git archive` copy | `pytest -q -rf` | **64 failed, 7350 passed, 27 skipped, 29 xfailed.** `main@ca0a8f81` in the first stretch: 70 failed, 6903 passed. Every failing test on the branch also fails on main in the same environment except `test_ollama_unload_button_actually_frees_memory_on_live_daemon`, which passes alone on the branch and fails alone on main: it reads the live Ollama daemon's loaded models, and the whole-suite run overlapped my dev-set runs against that daemon. Six failures on main's earlier list pass here (also environment: the daemon is up). So: no failure that is caused by the branch. |
| Integration | `buddy/tools/integration_i1.py --pins real` | **29 of 29** |
| Airgapped sampler | `buddy/tools/integration_i3.py`, 10 minutes | **1076 samples, one endpoint (Buddy's own loopback health port), zero non-loopback rows** |
| Dev set (not a gate; the sealed set is QA's) | `buddy/tools/run_dev_set.py --mode ...` | template 12 of 20 questions, 9 of 12 pressure; structured 6 to 10 of 20, 8 to 9 of 12 (Step 25) |

Conformance: the contract (`buddy.host.v1.json`, sha256 `48481c1d905df6ed8797de91c6db583f3fc7c1d2f9330c46f79f73b12a785516`) and its cases are
byte-identical on both sides (`scripts/sync-buddy-contract.py --check`, source `b794f698`, 134 files) and pass against ARAIL's real routes
and Buddy's `FakeHost`.

### Size

ARAIL: 175 files, +16,506 / -26 against `f31dfbf4` (before this log update). Buddy: 244 files, +30,379 / -5 against `349477f2`, of which
`nanobot/` is **3 files, +9 / -3** (P0 `__main__.py`, P1 `cli/gateway_runtime.py`, P2 `channels/telegram/runtime.py`) and `UPSTREAM.md` three table rows.

### Failure-mode coverage (ARCHITECTURE section 7 and Addendum A7)

"Tested now" means a named test in this branch passes.

| Tested now | F1 F2 F3 F4 F5 F6 (both sides; the pause side by the resend tests I-CRASH-02a to d) F7 F8 F9 F10 and F25 (against the real pins) F11 F12 F15 F16 (the code never reaches the bus, the prompt or the request: Intake scrub, history scrub, C8) F17 to F24 (F23 and F24: S9, the `/new` guard, R2 and R3, T1) F26 to F32 F34 F35 F36 F37 (every card of the real World round-trips the markdown converter) F38 to F42 F44 F45 F46 to F49 F50 (Intake and channel: the second message never reaches the bus) F51 to F55 |
|---|---|
| Not run here | F13 F14 (nanobot's retry and the fixed token message need Telegram); F33's sessions grep (QA's S-LEAK run); F43 in hybrid and the `nettop` half of L3; the two-quick-messages run through Telegram (F50, I-INJECT-01); the injection and pressure set against the sealed cases (F16, QA) |

### Remaining work (in this order)

1. **Architect:** ratify or amend H1 to H9, above all H3 to H7, and amend A1.3 for H2.
2. **QA:** run the sealed set with `python -m buddy qa_harness --set ...` in both modes (`[answer] mode` in `buddy.toml`, restart Buddy), grade,
   commit the file with the pre-registered sha256 `db7c9827a19911ffcdfb2b94bfac133320bec2d1c58d456d96df776cc208bad2`; plant the U-G13-02 canary in the
   test lab first; the hybrid L3 run with Telegram traffic; the five spot-check questions in the win run.
3. **Operator:** the win run on a phone (needs a real bot token, `people.toml`, hybrid mode and the LaunchAgent), and a word with the World's
   owner about the stale "C1 DRAFT" status line (H9).
4. **Builder, after the verdict:** if the blind run fails in structured mode, change the `Settings.answer_mode` default to `template` (one line
   and `docs/S1.md`) and record disconfirming #2. Then D29 (a better ranking) is the cheapest way to raise both modes.
