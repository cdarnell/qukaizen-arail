# Architecture: ARAIL deep, honest

**Date:** 2026-10-29
**Spec:** [VISION.md](./VISION.md) at `942cbda8`. Ledger [SPRINT.md](./SPRINT.md) at `98a99cd2`.
**Code read:** arail `62d5ebdf`. This branch adds only sprint docs, so every arail `file:line` below is at `62d5ebdf`. QueueLLM `e730d5fe`, read only. Buddy S6 `DAY1.json`, `BUILD_LOG.md` (step 4, architect feedback), `ARCHITECTURE.md` (day-1, A1-A20, tech debt), buddy `DEBT.md` D-S6-8, D-S6-10, D-S6-11.
**Operator records read:** counts and timestamps only, from `lab/instances/*/data/activity.jsonl`, `lab/instances/*/log/portal.log`, `lab/worlds/*/evolution.json`, `lab/data/model_registry.json`. No World or term names are quoted here.

## Read this first

1. **The day-1 check ran today, scratch only. Item 2 is OUT.** QueueLLM main `e730d5fe` builds cleanly and passes QueueLLM's preamble check. It still answers 1 of 5. So do the 2026-10-11 build and bundle v1.1.0. The vision's pre-committed table says: only the honest refusal ships. See [Day-1 check](#day-1-check-the-half-day-scratch-check).
2. **The vision is wrong that Curator grow is latent.** Grow has already lied 20 times. Twenty overnight passes in three Worlds hit the call-2 failure on every call after the first. Each logged "evolved via aerollm" and resealed and swapped the bundle. See [Corrections](#corrections-to-the-vision), V1.
3. **One deliberate deviation.** A recorded deep failure is cleared only by a passing probe of the same binary. A later successful call does not clear it. See [Deviations](#deviations-from-the-vision), D1.
4. **DAY1.json is not written by me.** The orchestrator said "touch no other file". Appendix A is its exact content. The builder commits it as step 0, with `ISSUE.md` (Appendix B).

## Restatement

ARAIL's deep slot runs QueueLLM in-process. Every QueueLLM build on this Mac answers the first deep call in a process and fails every later one. ARAIL does not say so. Its status surfaces test import, policy and residency. None tests generation. The one surface that records a real failure, tier-1 registry health, is erased by the next re-probe. The background agents swap in the fast model behind one easy-to-miss line. The Curator skips failed judge calls and reports a clean pass, and grow reseals the World anyway. The repair route, `deep rebuild`, cannot build QueueLLM main. It prints a false reason. On this Mac it would install a copy that a pip package shadows.

This sprint adds `./arailctl deep probe`: a generation test through ARAIL's own backend, in a fresh process. It adds one stored verdict per binary. Every surface reads it. Only a passing probe clears it. The Curator counts its failed calls and never calls a partial pass done. The build script tells the truth. The QueueLLM bug itself is not ARAIL's to fix. The win: one scripted run on today's broken wheel, with the operator's venv untouched, ends with zero silent outcomes.

## Corrections to the vision

| # | Vision says | What is true | Evidence |
|---|---|---|---|
| V1 | Curator grow: "Code read, not run". Review and grow are latent ("the logs show 20 reviews ... of a 2-term test World"). | Grow ran and lied 20 times. `ai`: 18 passes, 2026-09-08 to 2026-09-29, each "evolved via aerollm: +0 new terms, 0 corrections" (one had 1 correction). `debt-finance`: 1 empty pass, 2026-08-15. `sophie-school`: 1 empty pass, 2026-08-17. Each resealed and swapped the bundle (`portal/world_routes.py:1049-1051` runs unconditionally). | `lab/worlds/ai/evolution.json`: 20 passes, 18 empty, 19 by `aerollm`. `lab/worlds/debt-finance/evolution.json` and `lab/worlds/sophie-school/evolution.json`: 1 empty `aerollm` pass each. `lab/instances/ai/log/portal.log`: 430 "judge call failed" and 18 "propose_new_terms: ask failed", all `rms_norm`. `debt-finance` and `sophie-school` portal logs: 23 judge failures and 1 ask failure each. |
| V2 | "It failed in his real lab for seven weeks" (from 2026-09-09). | The first hidden failure is 2026-08-15, about 10.5 weeks before the S6 probe. Curator failures never reach the activity log. Its router is not wrapped by `_ReportingBackend`, so `report_failure` never fires. The Curator alone hid 496 failed deep calls. | `agents/deep_policy.py:187` (a bare `ModelRouter(backend="aerollm")`); `portal/world_routes.py:125-136`. The `debt-finance` and `sophie-school` activity logs have 0 `rms_norm` lines; their portal logs have 24 each. |
| V3 | "The status bar shows no tier1 health at all (`registry/core.py:470-481`)." | The server string has none. The client pill draws a health dot for tier 1 (`templates/_model_switcher.html:110-117`, element in `templates/_nav.html:112-114`). The degradation banner also shows tier 1 when unhealthy, because the operator binds `reasoning` and `build` to `tier1-aerollm`. Today both turn green on any re-probe. | `lab/data/model_registry.json` bindings; defaults `registry/store.py:463`; banner `_model_switcher.html:120-147`. |
| V4 | "Which agent is not verified." | Most likely Buddy's nightly dream. On all three days the failure line comes 3.1, 78.0 and 198.4 ms after "buddy entering dream window". Then "buddy dream failed: NameError" follows 1.9 to 2.2 s later. That NameError was raised after the dream's model call returned (`agents/_builtin_buddy.py:1446-1453`), so the dream's model call ran between the two lines: a deep failure, then about 2 s of fast answer. Path: `agents/dream_daemon.py:86-101` → `_builtin_buddy.py:1591-1596` → `ArailHost.llm_complete` `:175-188`. The grow pass that started in the same second cannot report (V2). Still inference, not proof. | `lab/instances/ai/data/activity.jsonl`, 2026-09-09, 09-11 and 09-21: dream line, failure line, NameError line. |
| V5 | Every deep warm re-probes (`portal/model_warmth.py:111`, `:251`, `:452`). | `:251` is the tier-0 keep-watch, not deep. The deep re-probes are `:111` (preload) and `:452` (boot warm). | `portal/model_warmth.py:190-251`. |
| V6 | The probe prints "the file `import aerollm_api` resolves to, and that file's sha256". | In this venv the import resolves to a 127-byte package `__init__.py`. Its hash says nothing about the binary. The probe prints the resolved file and the native file it loads. It keys everything by the native file's sha256 (`d62e1e16…`). | `find_spec` origin and a `sys.modules` diff, both run today. |
| V7 | Fact 5: provenance of the live `.so` is unknown. | Narrowed. The dist is an **editable** install from `file://<home>/ProJects/aerollm/crates/aerollm-api`. Its RECORD hash for the `.so` is `80bfe0f0…` (20 260 240 bytes). The file is `d62e1e16…` (21 894 128 bytes). Who built the replacement is still not recorded. | `aerollm_api-0.1.0.dist-info/RECORD`, `direct_url.json`. |

The vision's other facts check out. Fact 1: Metal works today (exit 0, 32023.921). Fact 2: 52 commits ahead of main, install name `@rpath/queuellm_api.abi3.so`, exports only `PyInit_queuellm_api`; its preamble count is 0. Fact 3: the tarball sha256 `57f30364…` matches `pyproject.toml:282`; the `.so` is `7c77aa85…`. Fact 4: three lines in `ai`. Fact 5: the shadowing. The Chat correction holds.

## Deviations from the vision

**D1. The failing state is stored per binary and cleared only by a passing probe.** The vision has two states. A process state is cleared by "a later successful call in the same process". A probe state is cleared by a 5/5 probe. This design stores every failure (probe, call, load) in one checkout-wide record, keyed by the binary's sha256. Only a 5/5 probe of that binary clears it. Why:
- The orchestrator's instruction for this phase: "who clears it (only a passing probe)".
- The failures happened in World instances (`ai`, `debt-finance`, `sophie-school`), never in the root lab. The root lab's activity log has 0 `rms_norm` lines. A process-local state would never have reached the root lab, and a restart erases it. That is how ten weeks passed unnoticed.
- With the call-2 bug no later call succeeds, so the vision's clear rule never fires. It only matters for an intermittent bug. There it would flap and reopen the silence.
- Cost: one failure keeps deep "failing" until the operator runs `deep probe`. On a broken build that takes 12-23 s today. Filed as TD4.

**D2. The verdict store is checkout-scoped, not per lab.** It lives at `<checkout>/lab/data/deep/verdict.json`. All World instances share the checkout's venv, so they share the binary (`scripts/lib/instances.sh:42`; env pack `scripts/start.sh:937-951`). The precedent for a checkout path from `__file__` is `nucleus/runtime_names.py:40` and `portal/app.py:4652`.

**D3. The World forge is in, minimally.** The vision left it "not verified". It is the same failure class: `dac_world/forge.py:139-142` skips each failed call. With the call-2 bug a forge ends in `GateRefused` with the wrong advice ("try a richer model or a clearer subject", `portal/world_routes.py:202-206`). With an intermittent bug it would show a smaller draft and say nothing. The forge counts its failed calls and says so. It does not block the draft, which is a preview the operator inspects before mounting.

## Day-1 check (the half-day scratch check)

Run today, 2026-10-29, 13:35-13:41 UTC, by me, scratch only. Same method as S6.

**Where the venv lives.** No venv was created or changed. The probes used the live venv's interpreter, read only: `~/ProJects/qukaizen-arail/.venv/bin/python` under `env -i`, with:
- `PYTHONDONTWRITEBYTECODE=1`;
- `PYTHONPATH=<shim dir>:<worktree>/src`, so ARAIL's own `AeroLLMBackend` from this branch was used;
- `ARAIL_DATA_DIR`, `LAB_ROOT`, `LAB_PKB`, `ARAIL_ENV_FILE` and `ARAIL_MODEL_DEFAULTS_FILE` in scratch, no portal;
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`;
- `AEROLLM_MODEL=Qwen2.5-7B-Instruct-4bit`, `ARAIL_MODELS_DIR=/Users/Shared/models`, `AEROLLM_KV_BUDGET_PCT=0.60`, `LAB_TIER=maximus`.

QueueLLM was exported with `git archive`. Cargo used a scratch `CARGO_TARGET_DIR`. Afterwards: no file in the live site-packages was newer than the probe script; `aerollm_api/__pycache__` was still dated 2026-06-21; `git -C ~/ProJects/qukaizen-queuellm status --short` was empty; the worktree was clean.

**The bar.** 5 of 5 sequential calls answer with non-empty text, through ARAIL's own `AeroLLMBackend.complete`, in one fresh process. A main build must first pass QueueLLM's preamble check: the count of `downloadComponent` in the artifact must be 0 (`docs/self-hosted-runner-setup.md:189-205` in QueueLLM). A build that fails it is thrown away unprobed. Median latency is recorded, not gated. The 120 s line belongs to `/deep` (D-S6-8).

**Steps, exactly as run.**
1. D1: `xcrun -sdk macosx metal --version`. Exit 0, metal 32023.921.
2. Spike: probe the live wheel, no shim. Purpose: confirm the mixed prompt set still reproduces S6's `DAY1.json`.
3. D2: copy `~/ProJects/queuellm-gptoss-fix-wt/target/release/libqueuellm_api.dylib` to `<scratch>/shim1011/queuellm_api.abi3.so`. Add `<scratch>/shim1011/aerollm_api/__init__.py` containing `from queuellm_api import *` plus `__version__` and `__doc__`. Probe with that dir first on `PYTHONPATH`.
4. D3: `tar xzf ~/ProJects/qukaizen-arail/dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz -C <scratch>/bundle110` (its own empty dir; tarball sha256 `57f30364…` = `pyproject.toml:282`). Probe with that dir first on `PYTHONPATH`.
5. D4: `git -C ~/ProJects/qukaizen-queuellm archive e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715 | tar -x -C <scratch>/d4/qsrc`. Then `cd <scratch>/d4/qsrc && CARGO_TARGET_DIR=<scratch>/d4/qtarget cargo build --release --offline -p queuellm-api --features extension-module`. Then `strings <artifact> | grep -c downloadComponent` must print 0. Shim as in D2. Probe.
6. D5: Appendix A. The builder commits it as `DAY1.json`.

The probe made 5 calls in this order: a short question; a long prompt (1,933 tokens); the short question again, verbatim; a long prompt with the same 6,680-character lab-context prefix and a different question; a second short question. `max_tokens=384`, `temperature=0.2`. Recipe in Appendix C. Prompt-set sha256 `b5bcb562…`.

**Result.**

| Binary | sha256 of the native file | Load | Call 1 | Calls 2-5 | Error at call 2 |
|---|---|---|---|---|---|
| live venv wheel, built 2026-06-28, reports `0.1.0-rc.2` | `d62e1e16…` | 2.21 s | ok, 9.65 s | fail, 0.00-0.01 s each | `Qwen2Model::forward (prefill head)` … `[rms_norm] … has 1 elements` |
| D2: the 2026-10-11 build (post-main branch), reports `1.1.0` | `f92a4cdd…` | 1.86 s | ok, 17.61 s | fail, 0.00-0.02 s | `Qwen2 layer (chunked prefill head)` … `[rms_norm]` |
| D3: bundle v1.1.0 (`2ae56afb`), reports `1.0.0` | `7c77aa85…` | 1.30 s | ok, 17.53 s | fail, 0.00-0.01 s | `Qwen2 layer (chunked prefill head)` … `[rms_norm]` |
| D4: main `e730d5fe`, built in 1 min 57 s, preamble 0, reports `1.1.0` | `d7e5aa57…` | 1.52 s | ok, 20.65 s | fail, 0.00-0.02 s | `Qwen2 layer (chunked prefill head)` … `[rms_norm]` |

Every run used ring depth 2 (`profile:balanced`) and loaded the tokenizer. Every later call fails in under 0.02 s. That holds for 33 and for 1,933 prompt tokens, and when the call repeats call 1 exactly.

**Decision.** The vision's table, row 3: "main fails call 2". **Item 2 is OUT.** Only the honest refusal ships. Wedge rows 1-5 ship. The live venv is not touched. Item 2 and `/deep` reopen only when a QueueLLM build passes `deep probe` 5/5 (and, for `/deep`, a median of at most 120 s).

**What ships because it failed.** `deep probe`. The stored verdict and every surface that reads it. Curator counting. Build-script honesty, including a refusal of the renamed crate with the true reason. `DAY1.json`. `ISSUE.md`, now with four builds of evidence.

**Re-running it** (after a QueueLLM fix, or at the 2026-11-19 revisit). Same steps, with the shipped `./arailctl deep probe` in place of the scratch probe, still with `PYTHON=<live venv python>` read only and `ARAIL_DEEP_STATE_DIR=<scratch>`. If a build gets 5/5, item 2 becomes its own sprint. A sketch for that sprint, not designed here: an ARAIL-owned `aerollm_api` package shim over `queuellm_api`, which is required because a `queuellm_api` binary exports only `PyInit_queuellm_api` and cannot load under the frozen name; a build marker with the QueueLLM commit and the artifact sha256; and sha256 verification as in C7.

## Assumptions

V = verified today (code read or measured). N = not verified.

| # | Assumption | V/N | Evidence |
|---|---|---|---|
| A1 | Every build on this Mac answers call 1 and fails calls 2-5 in one process. | V | Day-1 table; S6 `DAY1.json`. |
| A2 | `import aerollm_api` in the live venv resolves to the package `site-packages/aerollm_api/__init__.py`. It loads `aerollm_api/aerollm_api.abi3.so` (`d62e1e16…`). The top-level `site-packages/aerollm_api.abi3.so` (17 411 888 bytes, 2026-05-02, `b780d724…`) is shadowed and never loaded. | V | `find_spec` (no import); `sys.modules` diff in scratch. |
| A3 | The live dist is editable from `file://<home>/ProJects/aerollm/crates/aerollm-api`. Its RECORD hash for the `.so` (`80bfe0f0…`) does not match the file. No bundle marker exists. | V | `aerollm_api-0.1.0.dist-info/RECORD`, `direct_url.json`; no `aerollm_api.bundle.json`. |
| A4 | Every in-process deep generation goes through `AeroLLMBackend.complete` (`router/backends.py:1847-1890`): Chat (`portal/app.py:7974-7984`, `:8161-8169`, backend from `:9584-9631`); agents and Curator (`agents/deep_policy.py:187`); registry routers (`registry/binding.py:103-121` wraps `deep._backend`); the researcher (`agents/researcher.py:212-229`). | V | Code read. |
| A5 | Nucleus builds its own `Runtime` and does not use `AeroLLMBackend`. | N | Docstring only: `nucleus/runtime_names.py:10-15`. |
| A6 | A generation cannot be cancelled. `.result()` has no timeout (`router/backends.py:1866-1868`). A probe timeout must kill a process. | V | Code; S6 A3. |
| A7 | The native runtime can take the whole process down. | V (history) | The operator's `~/ProJects/qukaizen-arail/model_defaults.yaml:10-19` records SIGABRT and MTLReportFailure at load since 2026-08-06. Not reproduced today. |
| A8 | Tier-1 health is overwritten by every re-probe. `_probe_aerollm` says healthy when resident (`registry/health.py:132-140`). Callers: Check (`portal/models_api.py:57-66` → `registry/health.py:206-218`), preload (`portal/model_warmth.py:111`), boot warm (`:452`), the opt-in loop (`registry/health.py:261-272`), `recheck_unusable` (`registry/core.py:306-337`). `report_success` also sets healthy (`registry/core.py:290-304`). | V | Code read. |
| A9 | The status bar draws the tier-1 dot. The banner shows tier 1 when unhealthy, because the operator binds `reasoning` and `build` to it. | V | V3. |
| A10 | The Curator's deep router is a bare `ModelRouter(backend="aerollm")`, so its failures never reach `report_failure`. | V | `agents/deep_policy.py:187`; V2 logs. |
| A11 | `dac_world` skips each failed call and continues (`dac_world/reconcile.py:114-116`, `:140-141`, `:170-171`, `:213-217`; `dac_world/forge.py:139-142`). It only calls `router.complete(...)` and reads `.text` and `.model`, so a wrapper passed as `router=` sees every call. | V | Code read; V1 logs. |
| A12 | World instances share the checkout's venv. They live under `<checkout>/lab/instances/<slug>/`, each with its own `ARAIL_DATA_DIR`. | V | `scripts/lib/instances.sh:42`; `scripts/start.sh:937-951`, `:975-989`. |
| A13 | The operator's root lab uses the default data dir. | V | No `ARAIL_DATA_DIR` in `~/ProJects/qukaizen-arail/.env`. |
| A14 | The truth strip renders any non-`ok` reason as escaped text. | V | `portal/app.py:4559-4578`; `templates/research.html:892-894` (`esc`). |
| A15 | Traces already carry `deep_reason_code` and `deep_reason_detail`, and Admin shows them. No caller sets them today. | V | `router/core.py:213-262`; `agent_context.py:52-53`; `templates/admin.html:1763`; grep. |
| A16 | `lab/data/` is git-ignored. | V | `.gitignore:42`. |
| A17 | A broken-build probe takes about 12-23 s. A passing build would take about 5 × (10 to 70 s). | V / N | Day-1 table / no passing build exists. |
| A18 | The mixed prompt set reproduces the failure at call 2 on all four builds. Whether it catches a partial fix cannot be known until one exists. | V / N | Day-1 table. |
| A19 | The probe needs no network. The tokenizer loads from the model dir with HF offline set. | V | All four runs were offline. |
| A20 | Main has `crates/queuellm-api` (lib `queuellm_api`). Bundle commit `2ae56afb` has `crates/aerollm-api`. A `queuellm_api` binary cannot load under the name `aerollm_api` without a Python shim. | V | `git ls-tree`; `Cargo.toml` `[lib]`; `nm -gU` shows only `PyInit_queuellm_api`; D2 and D4 needed the shim. |
| A21 | The Metal toolchain works today. It failed on 2026-10-27, with no reboot between. | V (state) / N (cause) | D1; S6 `DAY1.json`. |
| A22 | Ring depth comes from the profile when the runtime is built: 1, 2 or 4 (`runtime_profile.py:34-64`). Day-1 used 2. The night failures in `ai` likely ran at 4. | V / N | Code; `ai` log "Profile → throughput (window)" at 03:00 UTC on 2026-09-21. |
| A23 | Exit code 5 is unused in the CLI contract, and codes are additive. | V | `docs/cli.md:26-41`. |
| A24 | `tests/test_no_user_visible_aerollm.py` flags new user-visible "AeroLLM". Frozen tokens pass: `aerollm_api`, `aerollm-api`, `tier1-aerollm`, `build-aerollm.sh`, `AEROLLM_*`. | V | `tests/test_no_user_visible_aerollm.py:27-48`. |
| A25 | `fcntl.flock` and atomic `os.replace` work on every platform ARAIL runs on (macOS, Linux, WSL). | V | POSIX; CI runs `ubuntu-latest` (`.github/workflows/*.yml`). |
| A26 | `psutil` is a core dependency (the probe's memory line). | V | `pyproject.toml:27`. |
| A27 | Outside users of `deep install` exist. | N | Vision fact 3. If they do, each has a deep slot that answers once per process (D3). |

## Data flow

### F1. `./arailctl deep probe`

```
operator ─▶ ./arailctl deep probe [--json] [--force]
            arailctl sources .env (:146); deep branch :784-809 sets PYTHON=$REPO_ROOT/.venv/bin/python unless set
            exec "$PYTHON" -m arail.deep_probe "$@"
SUPERVISOR (never imports aerollm_api)
  1 import arail.config         .env + model_defaults: the model resolves exactly as in the portal
  2 take <state>/probe.lock     flock LOCK_EX|LOCK_NB; held ─▶ exit 5 "another deep probe is running (pid N …)"
  3 find_spec("aerollm_api")    None ─▶ exit 5 "aerollm_api is not importable by <python>"
    static scan of sys.path      every aerollm_api* and queuellm_api* candidate: the winner and the shadowed ones
  4 model dir exists?            no ─▶ exit 5 (a config fault, not the binary's; nothing recorded)
  5 memory                       available (psutil) vs weights on disk + 2 GiB
    holders                      lsof -t -n -P -- <static native path>: pids that already mapped this binary
                                 print the second-copy line; too little ─▶ exit 5 unless --force
  6 spawn CHILD                  argv list, own process group, env + ARAIL_DEEP_PROBE_CHILD=1,
                                 HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1
CHILD  (python -m arail.deep_probe --child)
  a protocol fd = dup(1); dup2(2, 1)   stray native stdout goes to stderr, never into the protocol
  b snapshot sys.modules; import aerollm_api
    identity = the one new native module ─▶ {"ev":"identity", path, sha256, bytes, mtime, version}
  c AeroLLMBackend()                     ARAIL's own: tokenizer, chat wrap, KV budget, ring depth
                                         ok ─▶ {"ev":"loaded", …}; raised ─▶ {"ev":"load_failed", error}
  d for i in 1..5: backend.complete(p_i, max_tokens=384, temperature=0.2)
                                         ─▶ {"ev":"call", i, kind, prompt_tokens, ok, s, words | error}
  e {"ev":"done"}; os._exit(0)           skips atexit, whose executor shutdown would wait on the runtime
SUPERVISOR (reads protocol lines with deadlines)
  load deadline 300 s, call deadline 300 s ─▶ SIGTERM the group, wait 2 s, SIGKILL; "call i: no answer in 300 s"
  EOF or exit before "done"                ─▶ "call i: the probe child died (signal 6, SIGABRT)"; stderr tail kept
  SIGINT                                   ─▶ kill the child, record nothing, exit 130
  7 result: passed/5, median of the passing calls, first error and its index, identity
  8 deep_verdict.record_probe(result)       verdict.lock + atomic replace (C2)
       5/5: clears failures last seen before this probe started
       fewer: failing, source "probe"
  9 one activity line in this lab's log; print text (or JSON) with the teaching legend
 10 exit 0 (5/5) | 1 (fewer) | 5 (not run) | 2 (usage) | 130 (SIGINT)
OPERATOR SEES  the lines in C3. From then on every surface in F2 reads the record.
```

### F2. The failure that stays visible

```
WHO SETS "failing"   keyed by the sha256 of the native binary the process loaded
  AeroLLMBackend.complete  raised, or returned empty text ─▶ deep_verdict.note_call(ok=False, error, consumer)
     every consumer passes here: Chat, agents, Curator, researcher, registry routers, the probe child
  AeroLLMBackend.__init__  Runtime(...) or .start() raised  ─▶ note_load_failure(persist=True)
                           model dir missing                ─▶ note_load_failure(persist=False)  this process only
  deep probe               fewer than 5 of 5                ─▶ record_probe(fail)
  the probe child          ARAIL_DEEP_PROBE_CHILD=1: note_call stays in memory; the supervisor is the only writer
WHERE IT LIVES
  <checkout>/lab/data/deep/verdict.json   (ARAIL_DEEP_STATE_DIR overrides; schema in C2)
  plus an in-process copy, used when the file cannot be written and for model-dir faults
WHO CLEARS IT  (only)
  deep probe 5/5 on the same sha256 ─▶ clears failures last seen before that probe started
  a new binary has its own key: it starts "never tested", not "passing"
WHO READS IT   deep_verdict.current(): cached identity, cached file read, never generates
  deep_policy.explain()  ─▶ "deep_failing" ─▶ truth strip, the researcher's run line, agents, Curator
  tier-1 health: a read-time overlay in to_state() and _gate_reason() while failing; _probe_aerollm applies
     the same overlay ─▶ status-bar dot, banner, switcher, resolve() fallback
  model_warmth boot and preload lines ─▶ never "ready" while failing
  build-aerollm.sh status ─▶ the "generation:" line
THE MODELS "CHECK" BUTTON  POST /api/models/health/refresh → run_preflight → probe_entry → _probe_aerollm
  It re-reads the verdict and residency. It never generates. It never clears.
  While failing, tier 1 stays "unhealthy": "… Check re-reads this; only ./arailctl deep probe can clear it."
OPERATOR SEES  a red tier-1 dot; the banner "reasoning: Qwen2.5-7B-Instruct-4bit is unhealthy — generation
  failing …"; the research truth strip "Deep passes: … — deep generation is failing on this build …";
  deep status "generation: FAILING …"; Admin agent lanes "Deep reason"; one activity warn line per transition.
```

### F3. Background agents' failed deep calls (`agents/deep_policy.py:294-303`)

```
agent tick or dream daemon ─ agent_call(id) ─▶ ArailHost.llm_complete ─▶ complete_preferring_deep(foreground=False)
  ok, code, detail = explain(foreground=False)
  ├ code == "deep_failing" ─▶ fast path inside agent_context.with_deep_reason("deep_failing", detail)
  │                           no deep attempt; in-process counter fast_instead_of_deep += 1
  ├ another non-ok code    ─▶ fast path, unchanged (no stamping this sprint)
  └ ok ─▶ get_deep_router().complete(...)
           ├ non-empty text ─▶ return it; note_call(ok=True) updates counters only, clears nothing
           └ raised or empty ─▶ AeroLLMBackend already called note_call(ok=False):
                                  verdict failing (persisted), one activity warn line on transition
                                registry.report_failure(TIER1_ID, exc)   existing, on a raise only
                                fast path inside with_deep_reason("deep_failing", <this error>)
WHAT IS RECORDED
  verdict.json failure: since, last_at, source "call", consumer "agent:<id>", lab, error, count
  trace of the deep call: outcome "error", error_class (existing, router/core.py:306-309)
  trace of the fast call: the fast backend, deep_reason_code "deep_failing", the detail (the new stamp)
  the agent's own output is unchanged; it never says "deep" and never did
OPERATOR SEES  the F2 surfaces; Admin → agent lanes, "Deep reason" column (templates/admin.html:1763)
```

### F4. The Curator (review, grow) and the forge

```
POST /api/worlds/review │ POST /api/worlds/grow │ growth_tick (Librarian, overnight) │ forge with brain auto|deep
  router_ = _review_router() or _curation_router(brain)    deep only when explain(foreground=True) is ok
  judge_model = router_.backend_name
  deep_skip   = explain()'s code when the deep router was not chosen (for example "deep_failing")
  counted = _CountedRouter(router_)                         new, in world_routes.py; dac_world is not edited
  wf.reconcile_terms / wf.propose_new_terms / wf.forge_world(..., router=counted)
     each complete(): attempted += 1; on a raise: failed += 1, keep first_error, re-raise
     dac_world then skips the call exactly as before
REVIEW  planned = min(REVIEW_BATCH, len(terms)); judged = attempted − failed
  failed == 0 and judged == planned ─▶ outcome "complete"; "All clear" only when there are no flags
  otherwise                         ─▶ outcome "partial" (judged ≥ 1) or "failed" (judged 0)
     review.json reviewed_count = judged, never min(16, len); _review_state "partial" or "failed"
     activity warn "Curator review of '<W>' is partial: K of N terms judged …" (C8); never "All clear"
GROW    failed > 0 ─▶ stop before the gate, reseal and swap (world_routes.py:1044-1051) and before evolution.json
           _grow_state {"state": "not_applied", message}; activity warn "Growth pass … not applied …"
        failed == 0 ─▶ as today, plus "(all A model calls answered)"
FORGE   failed > 0 ─▶ the draft is still shown (a preview); the state message and the activity line say
           "F of A model calls failed (<error>)"; GateRefused with failures says so instead of
           "try a richer model or a clearer subject" (world_routes.py:202-206)
DEEP ALREADY FAILING BEFORE THE PASS ─▶ the router is the fast one; every report names it and adds
           "deep failing: <detail>"; nothing is counted as deep
OPERATOR SEES  the World Terms notice line (world-terms.js), GET /api/worlds/review and /grow fields (C6),
               the activity lines (C8)
```

How a failed call is counted: one exception raised by `router.complete` inside a pass is one failed call. A call that returns text that does not parse still counts as judged. The vision's bar is "judge calls that returned" (TD9).

### F5. The build script

```
./arailctl deep rebuild ─▶ build-aerollm.sh build        ./arailctl deep install ─▶ … bundle
setup.sh:684 ─▶ … auto                                    ./arailctl deep update  ─▶ … update
DETECT (one function, every mode)   repo = $ARAIL_AEROLLM_REPO (default unchanged, build-aerollm.sh:45)
   no dir                          ─▶ "no-repo"
   crates/aerollm-api/Cargo.toml   ─▶ "old"  (buildable)
   crates/queuellm-api/Cargo.toml  ─▶ "new"  (renamed; not buildable here)
   neither                         ─▶ "none"
build
   no-repo ─▶ "No QueueLLM checkout at <repo>." plus the clone hint (now true); exit 1
   new     ─▶ refuse: the renamed crate; nothing built or installed; points at deep install; exit 1  ◀ this Mac today
   none    ─▶ refuse: not a checkout this ARAIL can build; exit 1
   old     ─▶ cargo build -p aerollm-api … ─▶ artifact
              preamble: LC_ALL=C grep -a -c downloadComponent <artifact> must be 0, else refuse
              import target: find_spec in $PY (no import) must resolve to <site>/aerollm_api.abi3.so;
                 if a package dir or a PYTHONPATH entry wins ─▶ refuse "would be shadowed by <path>"; nothing copied
              copy ─▶ verify: a fresh $PY runs `python -m arail.deep_verdict identity --json`;
                 the loaded sha256 must equal the artifact's, else remove the copy and exit 1
              write <site>/aerollm_api.build.json {queuellm_commit, dirty, crate, artifact_sha256, built_at}
              "QueueLLM installed (source build, sha256 <12>…). Generation not tested yet: ./arailctl deep probe"
auto    old ─▶ build; new or none ─▶ refuse, no channel switch; no-repo ─▶ release or bundle as today
bundle  the same import-target check before F7 (build-aerollm.sh:213-218); sha256 check after; no "ready"
update  pip, then check the loaded file is the one pip's RECORD lists; no "ready"
status  the crate line names what it found; then import, binary, sha256, shadowed, channel (provable or
        "unknown — <reason>"), generation (from the verdict)
The frozen import never changes. The old crate's binary is aerollm_api.abi3.so. The renamed crate is refused,
so no binary that `import aerollm_api` cannot load is ever installed.
OPERATOR SEES (this Mac, today)  deep rebuild ─▶ the renamed-crate refusal; deep status ─▶ the C7 lines
```

## Interface contracts

### C1. `arail.deep_verdict` (new: `src/arail/deep_verdict.py`)

It owns one question: does deep generate on the binary this process loaded. It never generates. It never constructs `AeroLLMBackend`. It never raises to a caller. It uses no network.

The frozen names live in one block, the pattern of `nucleus/runtime_names.py:29-33`:
```python
# frozen surface: never rename. `aerollm_api` is what ARAIL imports.
FROZEN_MODULE = "aerollm_api"
NATIVE_MODULE_NAMES = ("aerollm_api", "queuellm_api")   # names a native module may load under
```

```python
@dataclass(frozen=True)
class Binary:
    module: str          # "aerollm_api"
    resolves_to: str     # spec.origin: a package __init__.py or the extension itself
    native_path: str     # the one native extension the import loads
    sha256: str          # 64 hex
    bytes: int
    mtime: str           # ISO UTC
    version: str         # the module's __version__, or "unknown"
    pinned: bool         # True once this process has loaded it

@dataclass(frozen=True)
class Verdict:
    state: str                     # "failing" | "passing" | "untested"
    binary: Optional[Binary]
    detail: str                    # one sentence, user-facing; explain()'s detail when failing (C8)
    since: Optional[str]           # ISO UTC
    source: Optional[str]          # "probe" | "call" | "load" | "process" | "store_unreadable"
    failure: Optional[dict]        # the C2 failure object
    last_probe: Optional[dict]     # the C2 last_probe object
    binary_changed_on_disk: bool
    store_error: Optional[str]

def state_dir() -> Path
def identity(*, import_if_needed: bool = True) -> Optional[Binary]
def current(*, import_if_needed: bool = True) -> Verdict
def is_failing() -> bool
def note_call(ok: bool, *, error: object = None, consumer: Optional[str] = None) -> None
def note_load_failure(error: object, *, persist: bool, consumer: Optional[str] = None) -> None
def record_probe(result: dict) -> tuple[bool, Optional[str]]      # (written, error)
# CLI: python -m arail.deep_verdict status           the lines for build-aerollm.sh status
#      python -m arail.deep_verdict identity --json   for the build script's verification
```

- **One definition of a working deep call:** no exception, and non-empty text after `_strip_special`. The probe and every consumer use it.
- **Identity, loaded:** the unique module in `sys.modules` named `aerollm_api`, `aerollm_api.*`, `queuellm_api` or `queuellm_api.*` whose `__file__` ends with an extension suffix (`importlib.machinery.EXTENSION_SUFFIXES`). Verified today for three layouts: a package (live), a top-level extension (bundle), and a shim over `queuellm_api` (D2, D4). Zero or two matches give `None`: untested, with the reason in the detail.
- **Identity, static** (`import_if_needed=False` and not loaded yet): `find_spec`. An extension origin is the file. In a package dir, the single `aerollm_api.*` extension is the file. Anything else gives `None`. Registry readers use static mode, so a minimalist lab never imports the module.
- **Pinning:** the first loaded identity is frozen for the process, with its `(size, mtime_ns, inode)`. If the file later differs, `binary_changed_on_disk` is true and the detail says "restart the lab to load the new build". The process keeps the old key. A probe of the new file never clears the old binary's state in a process that still runs the old one.
- **`state_dir()`:** `ARAIL_DEEP_STATE_DIR` if set. Else `<checkout>/lab/data/deep`, where `<checkout> = Path(__file__).resolve().parents[2]` and holds an `arailctl` file. Else `config.DATA_DIR / "deep"`. Never inside a venv.
- **Reads** are cached by the `(mtime_ns, size)` of `verdict.json`. A read never takes the lock.
- **Writes:** `verdict.lock` with `flock(LOCK_EX | LOCK_NB)`, retried for up to 2 s. Read, modify, write `verdict.json.tmp-<pid>`, `fsync`, `os.replace`. The dir is `0700`, files are `0600`, the lock opens with `O_NOFOLLOW`. On a lock timeout or `OSError`: keep the failure in memory and set `store_error`. A caller never waits longer than 2 s.
- **Corrupt or wrong-schema file:** fail closed. `current()` returns `failing`, source `store_unreadable`, and the detail names the path and the error. The next writer renames the file to `verdict.json.corrupt-<UTC stamp>` and writes a fresh one. A missing file means `untested`.
- **Transition line:** the first time this process sees its binary turn failing, it emits one activity warn line (C8). The probe child never does.

### C2. `verdict.json` (schema `arail.deep-verdict/v1`)

```json
{
  "schema": "arail.deep-verdict/v1",
  "updated_at": "2026-10-29T13:36:02Z",
  "binaries": {
    "<sha256, 64 hex>": {
      "module": "aerollm_api",
      "native_path": "/…/site-packages/aerollm_api/aerollm_api.abi3.so",
      "bytes": 21894128,
      "version": "0.1.0-rc.2",
      "last_probe": {
        "started_at": "…Z", "started_ts": 1793280950.1, "at": "…Z",
        "outcome": "fail",
        "passed": 1, "total": 5, "median_ok_s": 9.65,
        "first_error": {"call": 2, "kind": "long", "error": "≤ 300 chars"},
        "model": "Qwen2.5-7B-Instruct-4bit", "ring_depth": 2, "ring_depth_source": "profile:balanced",
        "max_tokens": 384, "prompt_set_sha256": "b5bcb562…", "python": "/…/bin/python", "lab": "root"
      },
      "failure": {
        "since": "…Z", "since_ts": 1793280952.0, "last_at": "…Z", "last_ts": 1793280952.0,
        "source": "probe",
        "consumer": "deep-probe",
        "lab": "root",
        "error": "the first error, ≤ 300 chars", "last_error": "the latest, ≤ 300 chars",
        "count": 1
      },
      "last_pass_at": null
    }
  }
}
```

Value sets: `last_probe.outcome` is `pass` or `fail`. `failure.source` is `probe`, `call` or `load`. `failure.consumer` is `deep-probe`, `agent:<id>`, `system:<label>` or `unattributed`. `failure.lab` is `root` or the `ARAIL_INSTANCE` slug. `last_probe` and `failure` may be `null`.

Rules. `failure` not null means failing. `failure` null and `last_probe.outcome == "pass"` means passing. Otherwise untested. `record_probe(pass)` sets `failure = null` and `last_pass_at` only if `failure.last_ts < started_ts`; a failure seen during the probe survives it. At most 32 binaries; the oldest activity is evicted. Changes are additive only; anything else gets a new schema string.

### C3. `./arailctl deep probe`

**Shape.**
```
./arailctl deep probe [--json] [--force]
python -m arail.deep_probe [--json] [--force]           the same thing
```
- `--json`: one JSON object on stdout and nothing else. Same exit codes.
- `--force`: skips only the memory refusal. Never the lock.
- Env: `ARAIL_DEEP_STATE_DIR` (C1), `ARAIL_DEEP_PROBE_CALL_TIMEOUT_S` (default 300), `ARAIL_DEEP_PROBE_LOAD_TIMEOUT_S` (default 300). Everything else is the lab's own config.
- Explicit only. Nothing calls it at boot or on a timer.
- The `arailctl` help (`arailctl:255-256`) and the `deep` usage error (`:807`) list `probe`.

**Exit codes**, additive to `docs/cli.md:26-41`:

| Code | Meaning |
|---|---|
| 0 | 5 of 5 answered. Recorded `passing`. |
| 1 | The probe ran and fewer than 5 answered (includes a load failure, a crash, a timeout). Recorded `failing`. |
| 2 | Usage error. |
| 5 | **New.** Not run; nothing recorded. `aerollm_api` not importable, model dir missing, another probe running, not enough available memory without `--force`, or the binary could not be identified. |
| 130 | Interrupted (SIGINT). Child killed. Nothing recorded. |

**Text output** for the live wheel today (`<site>` is the venv's site-packages):
```
QueueLLM deep probe: 5 calls in a fresh process, through ARAIL's own deep backend. Token-free, no network.
python    ~/ProJects/qukaizen-arail/.venv/bin/python
import    aerollm_api -> <site>/aerollm_api/__init__.py (package)
binary    <site>/aerollm_api/aerollm_api.abi3.so
sha256    d62e1e16e93c264b79eae35e5766a84f56091ed8e4cc66a89e18791effc7579e · 21894128 bytes · modified 2026-06-28 · version 0.1.0-rc.2
shadowed  <site>/aerollm_api.abi3.so · sha256 b780d7240e73… · modified 2026-05-02 · on the path, never loaded
model     Qwen2.5-7B-Instruct-4bit · /Users/Shared/models · 4.0 GiB on disk
memory    16.2 GiB available · no other process has this binary loaded · this probe holds its own copy until it exits
load      2.2 s · ring depth 2 (profile:balanced) · up to 384 new tokens per call
call 1/5  short                 33 tokens in   ok       9.7 s   38 words
call 2/5  long                1933 tokens in   FAILED   0.0 s   RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): "[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements…
call 3/5  short-repeat          33 tokens in   FAILED   0.0 s   same error as call 2
call 4/5  long-shared-prefix  1933 tokens in   FAILED   0.0 s   same error as call 2
call 5/5  short-2               34 tokens in   FAILED   0.0 s   same error as call 2
result    1/5 passed. Deep generation is FAILING on this binary.
median    9.7 s over the 1 call that answered
first     call 2 (long): RuntimeError: backend `mlx-native` failed: …[rms_norm]…
recorded  failing · lab/data/deep/verdict.json · agents and the Curator use the fast model until a probe of a new build passes 5/5

What the numbers mean
  <one entry per kind of number shown above: meaning · bar and why · if out of range · where to look>
```

When a holder exists the memory line reads: `memory    16.2 GiB available · pid 4242 (uvicorn arail.portal.app…) already has this binary loaded; if it holds the model, this probe is a second copy (4.0 GiB) until it exits`.

Not-run lines (exit 5) read `not run   <reason> — <what to do>`:
- `aerollm_api is not importable by <python> — install QueueLLM first: ./arailctl deep install`
- `another deep probe is running (pid 4242, since 13:35:50 UTC) — wait for it`
- `model dir missing: <path> — check AEROLLM_MODEL and ARAIL_MODELS_DIR`
- `not enough available memory for another copy: 3.1 GiB available, 6.0 GiB needed (4.0 GiB weights + 2 GiB headroom) — stop the lab or pass --force`

Other rows. Timeout: `call 2/5  long  1933 tokens in  NO ANSWER  300.0 s  did not finish within 300 s; the probe stopped its child process`. Crash: `call 3/5  short-repeat  33 tokens in  CRASHED  the probe child died (signal 6, SIGABRT) during this call; its last stderr lines are in the record`. Not saved: `recorded  NOT SAVED (<error>) — status surfaces will not show this result`.

**JSON output** (schema `arail.deep-probe/v1`):
```json
{
  "schema": "arail.deep-probe/v1",
  "outcome": "fail",
  "exit_code": 1,
  "not_run_reason": null,
  "started_at": "…Z", "finished_at": "…Z", "duration_s": 14.1,
  "python": "/…/bin/python",
  "binary": {"module": "aerollm_api", "resolves_to": "…/aerollm_api/__init__.py",
             "native_path": "…/aerollm_api/aerollm_api.abi3.so", "sha256": "d62e…", "bytes": 21894128,
             "mtime": "…Z", "version": "0.1.0-rc.2",
             "shadowed": [{"path": "…/aerollm_api.abi3.so", "sha256": "b780…", "mtime": "…Z"}]},
  "model": {"name": "Qwen2.5-7B-Instruct-4bit", "dir": "/Users/Shared/models", "disk_gib": 4.0},
  "runtime": {"ring_depth": 2, "ring_depth_source": "profile:balanced", "kv_budget_gib": 12.25,
              "max_tokens": 384, "temperature": 0.2, "prompt_set_sha256": "b5bcb562…"},
  "memory": {"available_gib": 16.2, "needed_gib": 6.0, "holders": [], "forced": false},
  "load": {"ok": true, "seconds": 2.21, "error": null},
  "calls": [
    {"index": 1, "kind": "short", "prompt_tokens": 33, "ok": true, "seconds": 9.65, "words_out": 38, "error": null, "end": "answered"},
    {"index": 2, "kind": "long", "prompt_tokens": 1933, "ok": false, "seconds": 0.0, "words_out": 0, "error": "RuntimeError: …", "end": "error"}
  ],
  "passed": 1, "total": 5, "median_ok_s": 9.65,
  "first_error": {"call": 2, "kind": "long", "error": "RuntimeError: …"},
  "stderr_tail": [],
  "verdict_store": {"path": "…/verdict.json", "written": true, "error": null},
  "teaching": {"passed": {"meaning": "…", "threshold": "…", "out_of_range": "…", "failure_lives_in": "…"}}
}
```
`outcome` is `pass`, `fail` or `not_run`. `calls[].end` is `answered`, `error`, `empty`, `timeout`, `crashed` or `not_reached`. The `teaching` object has one entry per numeric field (see Teaching).

**Child protocol** (internal; the child's stdout only): one JSON object per line, each with `"probe": 1` and `"ev"` in `identity | loaded | load_failed | call | done`. Lines without `"probe": 1` go to the stderr log.

**Prompts.** `src/arail/deep_probe_prompts.py` holds the day-1 set (Appendix C). A unit test pins `PROMPT_SET_SHA256 == "b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e"`. So the shipped probe is provably the day-1 probe.

### C4. `explain()`, the agents' fast path, and the backend hook

- `deep_policy.explain()` gains one code, `deep_failing`. Order: `disabled`, `tier_locked`, `wheel_missing`, **`deep_failing`**, then foreground `ok` or the background gate (`deferred_now`), then `ok`. It applies to `foreground=True` and `False`. The detail is `Verdict.detail` (C8). Existing codes keep their meaning. `deep_failing` stays distinct from `deferred_now`. The docstring (`agents/deep_policy.py:141-142`) lists it.
- `prefer_deep()` stays the boolean projection. So while failing, the Curator (`portal/world_routes.py:130`), the researcher (`agents/researcher.py:1428-1429`) and the preload loop (`portal/model_warmth.py:90`) stop choosing deep. The researcher already prints "Deep passes off for this run: <detail>." (`agents/researcher.py:1137-1142`).
- `boot_warm_gate()` does not change. Weights may still load for Chat. Its lines change (C8).
- `complete_preferring_deep()` calls `explain()` once. Fast calls made because of `deep_failing`, or after a deep call in this call failed, run inside `agent_context.with_deep_reason("deep_failing", detail)`. That helper is new and additive. It re-sets the current `AgentCall` with only the two reason fields replaced (`dataclasses.replace`). It does nothing when there is no context. The `AgentHeldError` paths do not change.
- `AeroLLMBackend.complete` (`router/backends.py:1866-1868`): `note_call` after `.result()`. On an exception: `note_call(ok=False)`, then re-raise the same exception. Chat's loud error does not change.
- `AeroLLMBackend.__init__`: pin the identity right after `import aerollm_api` (`:1563`). A raise from `_init_runtime` (`:1699`) calls `note_load_failure(persist=True)`. The model-dir error (`:1599-1609`) calls `note_load_failure(persist=False)`.
- `AeroLLMBackend.health_check()` (`:1892-1893`) means resident and not failing.
- Buddy's contract (`buddy.host/v1`) does not change. `brain.chat` never reads `explain()`.

### C5. Status surfaces and their wire shapes

**Truth strip.** `GET /api/research/status` and `POST /api/research/deep` return `models.deep` (`portal/app.py:4566-4578`):
```json
{"entry_id": "tier1-aerollm", "display_name": "Qwen2.5-7B-Instruct-4bit", "available": true,
 "enabled_by_user": true, "eligible_now": false,
 "reason_code": "deep_failing", "reason": "<Verdict.detail>",
 "generation": {"state": "failing", "since": "…Z", "source": "probe", "consumer": "deep-probe",
                "lab": "root", "failures": 1, "last_probe": {"at": "…Z", "passed": 1, "total": 5},
                "sha256_12": "d62e1e16e93c"}}
```
`generation` is additive. `failures` is `failure.count`: failure events since the last pass (a failed probe is one event; each failed deep call in any lab is one). Otherwise `state` is `untested` or `passing`, and `last_probe` may be null. `templates/research.html:892-894` renders `deep_failing` with the danger class instead of dim. The text is the escaped `reason`.

**Tier-1 health.** `GET /api/models/state`, the `tier1-aerollm` entry, while failing:
```json
{"status": "unhealthy", "latency_ms": null, "checked_at": 1793280962.0, "endpoint": null,
 "detail": "generation failing on this build (sha256 d62e1e16e93c): <error ≤ 120> — since 2026-10-29 13:35 UTC. Check re-reads this; only ./arailctl deep probe can clear it."}
```
For any entry with `provider_type == "aerollm"`, a read-time overlay applies in `to_state()` (`registry/core.py:483`) and `_gate_reason()` (`:122`). While `deep_verdict.current(import_if_needed=False)` is failing, health reads `unhealthy` with the detail above. Otherwise the stored health stands, exactly as today; so a minimalist or wheel-less lab sees no change. `_probe_aerollm` (`registry/health.py:120`) applies the same overlay first, so Check, preflight, the warms and `recheck_unusable` store it too. No stored write (`report_success`, a warm, a Check) can show healthy while failing, because the overlay is applied on read. After a passing probe the overlay stops. A stored `unhealthy` whose detail carries the overlay's own prefix ("generation failing on this build") is then re-probed with `probe_entry()` on the next read, so the dot turns green without a Check.

**Check** (`POST /api/models/health/refresh`, `portal/models_api.py:57-66`): the same response shape. It re-reads. It never generates. It never clears.

**Opt-in health loop.** `run_preflight` (`registry/health.py:206-251`) prints "MODEL TIER DOWN" for an entry only when its status or detail changed since the last announcement in this process. A sticky failure must not print a line every 60 s.

**Boot and preload lines** (`portal/model_warmth.py:115`, `:461-463`): see C8.

**`deep status`:** see C7.

### C6. Curator wire shapes

`review.json` (`arail.world-review/v1`, additive fields) and `GET /api/worlds/review`:
```json
{"schema": "arail.world-review/v1", "world": "…", "reviewed_at": "…Z", "model": "aerollm",
 "reviewed_count": 1, "flags": [],
 "outcome": "partial", "planned": 6, "judged": 1, "failed": 5,
 "judge_model": "aerollm", "deep_skip": null, "deep_error": "RuntimeError: … ≤ 160",
 "teaching": {"planned": {}, "judged": {}, "failed": {}, "reviewed_count": {}}}
```
`outcome` is `complete`, `partial` or `failed`. Old files without it read as `null`. `deep_skip` holds explain()'s code when the deep router was not chosen. `_review_state["state"]` adds the terminal values `partial` and `failed`.

`GET /api/worlds/grow` adds the terminal `state: "not_applied"`, a `message`, `calls: {"attempted": 7, "failed": 6}` and `teaching`. `evolution.json` gets no entry for a pass that was not applied. The forge's `_forge_state.message` carries the failure count, and its `stats` gain `failed_calls`.

`static/js/world-terms.js`: one notice line under the head when `outcome` is `partial` or `failed`, or when grow `state` is `not_applied`. The text is the API `message`, inserted as text through the existing `el()` helper. `pollGrow` treats `not_applied` as terminal.

### C7. `scripts/build-aerollm.sh`

Modes do not change: `auto | build | update | bundle | status`. Exit codes do not change: 0 done, 1 refused or failed, 2 usage.

Messages that must exist (the builder may tighten wording, not facts):
- No repo: `No QueueLLM checkout at <repo>.` then the clone hint (`build-aerollm.sh:122-129`, now true).
- Renamed crate: `<repo> has the renamed crate crates/queuellm-api (module queuellm_api). This ARAIL can build only the pre-rename crate crates/aerollm-api, so deep rebuild stops here. Nothing was built or installed. For the prebuilt QueueLLM: ./arailctl deep install.`
- Neither crate: `<repo> has neither crates/aerollm-api nor crates/queuellm-api, so it is not a QueueLLM checkout this ARAIL can build. Nothing was built or installed.`
- Poisoned build: `The build carries the Metal toolchain error marker (downloadComponent). It would fail at runtime. Not installed. See QueueLLM docs/self-hosted-runner-setup.md.`
- Shadowed target: `import aerollm_api in <PY> loads <path> (<owner>). A copy at <site>/aerollm_api.abi3.so would never load. Nothing was installed. To use this build, remove that first, for example: <PY> -m pip uninstall aerollm-api (this changes your venv).` `<owner>` is "pip package aerollm-api 0.1.0, editable from <url>", "a PYTHONPATH entry", or "no install record".
- Installed: `QueueLLM installed (<channel>, sha256 <12>…). Generation is not tested yet: run ./arailctl deep probe.`
- Verification failed: `Installed <dest>, but import aerollm_api loads <path> (sha256 <12>…). Removed the copy.` Exit 1.
- `auto` with `new` or `none`: the refusal above, then `Not switching to another channel on my own. Prebuilt instead: ./arailctl deep install (or AEROLLM_CHANNEL=bundle).`
- The F7 text (`:214-216`) says only what is known: `an aerollm_api.abi3.so with no install record is already at <path>`.
- Never printed again: `QueueLLM ready` (`:155`, `:171`, `:366`); `(missing → release/bundled channel)` (`:403`); `(found → source/dev channel)` (`:401`); a guessed `channel: release` (`:391-392`).

`status` prints:
```
• QueueLLM (2nd inference) status
    repo:         ~/ProJects/qukaizen-queuellm
    crate:        crates/queuellm-api, the renamed crate; deep rebuild cannot build it yet
    index:        https://pypi.qukaizen.com/simple/
    python:       ~/ProJects/qukaizen-arail/.venv/bin/python
    import:       aerollm_api -> <site>/aerollm_api/__init__.py (package)
    binary:       <site>/aerollm_api/aerollm_api.abi3.so
    sha256:       d62e1e16e93c… · 21894128 bytes · modified 2026-06-28 · version 0.1.0-rc.2
    shadowed:     <site>/aerollm_api.abi3.so · sha256 b780d7240e73… · modified 2026-05-02 · never loaded
    channel:      unknown — pip package aerollm-api 0.1.0 (editable, file://<home>/ProJects/aerollm/crates/aerollm-api), but its binary was replaced outside pip (RECORD 80bfe0f033fa…, file d62e1e16e93c…)
    generation:   FAILING — 1/5 at the last probe (2026-10-29 13:35 UTC); first error at call 2: RuntimeError: …
                  5/5 is the bar: a deep answer must work every time, not once per process. The fault is in the binary above, not in ARAIL.
    bg pressure:  0.60 (default)
```
Other `generation` forms: `never tested on this binary — run ./arailctl deep probe (an import is not a generation test)`; `passing — 5/5 at the last probe (…), median 21.3 s`; `FAILING — a deep call failed in lab 'ai' (agent:buddy) at …: …`. The channel is `bundled` only when the bundle marker's `sha256` equals the loaded file's; `source build` only when `aerollm_api.build.json`'s `artifact_sha256` matches; `pip (<dist> <version>)` only when the dist's RECORD hash matches. Otherwise it is `unknown — <reason>`. `status` exits 0: it reports, and `deep probe` decides.

Build marker `<site>/aerollm_api.build.json`, schema `arail.aerollm-build/v1`: `queuellm_commit` (40 hex), `queuellm_dirty`, `crate`, `artifact_sha256`, `artifact_bytes`, `built_at`, `built_by: "scripts/build-aerollm.sh"`. Same role as `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`.

`scripts/setup.sh:678` and `:685` stop claiming readiness. They become `aerollm_api already present — generation not tested here; run ./arailctl deep probe.` and `QueueLLM installed — run ./arailctl deep probe to test generation.` Two hints that point at a route that no longer builds main are corrected: `router/backends.py:1570-1575` (deep rebuild works only with a pre-rename checkout) and `registry/health.py:124-125` (point at `deep install`).

### C8. Activity lines and details (exact)

| Source | Level | Text |
|---|---|---|
| `deep_verdict` transition | warn | `Deep model failed in <consumer> (<lab>): <error ≤160>. Deep generation is now marked failing for this build (sha256 <12>); agents and the Curator use the fast model until ./arailctl deep probe passes 5 of 5.` |
| `deep probe`, fail | warn | `Deep probe: <P>/5 passed on build <12> — generation failing; first error at call <i>: <error ≤120>.` |
| `deep probe`, pass | success | `Deep probe: 5/5 passed on build <12> — median <s> s. Deep is cleared for agents and the Curator.` |
| `explain()` detail (`Verdict.detail`) | — | `deep generation is failing on this build (QueueLLM <version>, sha256 <12>): <what> — since <UTC>. Agents and the Curator use the fast model. Re-test with ./arailctl deep probe after installing a new build.` Here `<what>` is `last probe <P>/5, first error at call <i>: <err ≤120>`, or `a deep call failed in <lab> (<consumer>): <err ≤120>`, or `the verdict file is unreadable (<path>: <err>)`. |
| Curator review, complete | success or warn | `Curator reviewed all <N> of <N> terms in '<W>' with <model> — <n> flag(s). (<N> of <N> is a full review; fewer is reported as partial. A flag is a term the judge wants changed: Knowledge → World Terms.)` With no flags: `… — no flags. All clear. (…)` |
| Curator review, partial or failed | warn | `Curator review of '<W>' is partial: <K> of <N> terms judged. A full review judges all <N>; the <N−K> unjudged terms have no flags, so their silence means nothing. <model> failed on <F> call(s): <error ≤160>. The fault is in the deep runtime, not the World: run ./arailctl deep probe.` With K = 0 it says `failed` instead of `partial`. |
| Grow, not applied | warn | `Growth pass on '<W>' not applied: <model> failed on <F> of <A> calls (<error ≤160>). A pass is applied only when every call answers, so the World is unchanged: no reseal, no swap, no evolution entry. Run ./arailctl deep probe.` |
| Grow, applied | success | Today's text (`portal/world_routes.py:1074-1077`) plus `(all <A> model calls answered)`. |
| Forge, partial | warn | `Forge of '<S>' finished with <F> of <A> model calls failed (<error ≤160>); the draft is incomplete. Review it before mounting.` With `GateRefused`: `Forge of '<S>' produced nothing usable: <F> of <A> model calls failed (<error ≤160>).` |
| Curator with a fast router because deep is failing | — | Every Curator line above adds ` Judged by <fast model>; deep failing: <detail ≤120>.` |
| Boot warm, failing | warn | `Deep model loaded at boot (weights resident), but generation is failing on this build: <detail ≤160>. Chat can still pick it; agents and the Curator will not use it.` |
| Boot warm, untested or passing | success | `Deep model loaded at boot (weights resident). Generation not tested on this build yet: ./arailctl deep probe.` Or: `… Last probe <P>/5 at <UTC>.` The words "and ready" go. |

All user-visible text says QueueLLM, never AeroLLM (A24). Any mention of cost says "token-free".

## Teaching requirement (tested)

**Rule.** Every number that the probe, the Curator, `deep status` and the win script print carries four parts: what it means, the bar and why, what out of range means, and where the failure lives. The model is the `teaching` block of S6's `DAY1.json`.

**How it is built.** A surface prints numbers only through a renderer that takes `(teaching_key, value)` pairs. `TEACHING` dicts live next to the renderers: `deep_probe.TEACHING`, `world_routes._CURATOR_TEACHING`, `deep_verdict.TEACHING`, and the win driver's own. Each entry has four non-empty strings: `meaning`, `threshold`, `out_of_range`, `failure_lives_in`.
- Probe text: each number line carries its key. A "What the numbers mean" legend prints every key used, once, with all four parts.
- Probe JSON and Curator JSON: a `teaching` object covers every numeric leaf.
- Activity lines: one short clause per number (the C8 texts).

**Identifiers are not numbers.** sha256 values, pids, paths, timestamps, versions, file byte sizes and `prompt_set_sha256` are labelled, not taught.

**Tests.** Each fails the build if a number lacks teaching.
- TQ1: walk the probe JSON for every fake-runtime mode. Every numeric leaf (bools excluded; list indices normalised to `[]`) has a four-part `teaching` entry.
- TQ2: render the probe text for every mode. Every numeric token on a number line maps to a legend entry with four non-empty parts. The legend prints each key used.
- TQ3: `review.json`, `GET /api/worlds/review` and `GET /api/worlds/grow` for complete, partial, failed and not_applied. Every numeric field has teaching. Every C8 Curator line contains its clauses.
- TQ4: every `deep_verdict status` line with a number carries the bar sentence. The win script's table has a filled teaching cell for every row.

Probe entries (the builder may tighten wording, not drop parts):

| Key | Meaning | Bar and why | Out of range | Where the failure lives |
|---|---|---|---|---|
| `passed` | deep calls that answered with non-empty text, out of 5 in a row in one fresh process | 5/5: every consumer makes more than one call per process, and call 2 is where every known broken build fails | 1/5: answers once per process (the call-2 bug). 0/5: cannot answer at all. 2-4: intermittent, treated as failing | the QueueLLM binary on the `binary` line, not ARAIL settings; read `first` |
| `median_ok_s` | middle wall time of the calls that answered: prefill plus up to 384 new tokens | none for this probe; an interactive deep answer should stay under 120 s | over 120 s: memory pressure or a slow build. No value: nothing answered | runtime decode speed, or memory on this Mac (see `memory`) |
| `calls[].seconds` | wall time of one call | must end within 300 s, the per-call deadline | about 0.0 s with an error: it failed before the first token (prefill) | the runtime |
| `calls[].prompt_tokens` | prompt length after ARAIL's chat wrap, by the model's tokenizer | short about 33; long about 1,933, the size of a chat turn with lab context | absent: the tokenizer did not load (the prompts were still sent) | the model dir's tokenizer files |
| `calls[].words_out` | words in the answer | above 0; an empty answer counts as a failure | 0: an empty answer | runtime decode |
| `first_error.call` | index of the first failed call | absent when all pass | 2: the known call-2 bug. 1: cannot answer at all | the runtime |
| `load.seconds` | weights load plus runtime start | must end within 300 s | slow: disk or memory pressure | the model dir and the runtime |
| `memory.available_gib` | memory available before loading | at least weights + 2 GiB, or the probe refuses unless `--force` | below: a second copy could push the Mac into swap | other processes (see holders) |
| `memory.needed_gib` | weights on disk + 2 GiB headroom | the bar for `memory.available_gib`; 2 GiB covers activations and the runtime beyond the KV budget | far above weights + 2 GiB: the weights size was misread | `model.disk_gib` and the model dir |
| `model.disk_gib` | weights on disk, about the resident size | input to the memory bar; about 4.0 GiB for the 4-bit 7B | far from 4 GiB for this model: the wrong model dir | `AEROLLM_MODEL`, `ARAIL_MODELS_DIR` |
| `runtime.ring_depth` | layer blocks QueueLLM keeps resident, set by the profile | none; recorded because a pass at one depth does not prove another | a depth other than the lab runtime's: the result may not carry over to the lab | `AEROLLM_RING_DEPTH` or the runtime profile |
| `runtime.kv_budget_gib` | the KV cache budget ARAIL gave the runtime | set from `AEROLLM_KV_BUDGET_PCT` and available memory | very small: prompts may fail for memory, not for the bug | `.env`, available memory |
| `runtime.max_tokens`, `runtime.temperature` | per-call settings | 384 and 0.2, the same as S6's day-1, so results compare | other values: results no longer compare with `DAY1.json` | the `deep_probe.py` constants |
| `total` | calls attempted | 5 | fewer: the probe stopped early (crash or timeout) | the supervisor's record and `stderr_tail` |
| `duration_s` | whole probe wall time | under 30 min by the deadlines | longer: a deadline did not fire | `deep_probe.py` |

Curator entries: `planned` (terms the pass meant to judge, `min(16, terms)`); `judged` (judge calls that returned; the bar is `planned`); `failed` (calls that raised; the bar is 0; above 0 means partial, and the failure lives in the deep runtime); `reviewed_count` (equals `judged`, kept for old readers); grow `attempted` and `failed` (the bar is 0 failed, or the pass is not applied).

## Failure modes

| # | Failure | Detection | What the operator sees / recovery | Test |
|---|---|---|---|---|
| F1 | A runtime that answers call 1 and fails call 2 (today's bug) | `note_call(ok=False)` on call 2 | Every surface says failing. Agents run on fast under `deep_failing`. The Curator reports partial or not applied. `deep probe` prints 1/5, exit 1. | U5, P2, C1, C4, Q2; live W1-W6 |
| F2 | The probe child crashes mid-call (SIGABRT, A7) | the child exits before `done`; the supervisor sees the signal | A `CRASHED` row; 1/5 or less; exit 1; recorded failing with the stderr tail | Q3 |
| F3 | The probe times out (a call or the load never returns) | per-call and load deadlines in the supervisor | `NO ANSWER … 300 s`; the child's process group is killed; exit 1; recorded failing | Q4 |
| F4 | The verdict file is missing | `FileNotFoundError` on read | "never tested" everywhere; `explain()` ok; the first failure or probe creates it | U3 |
| F5 | The verdict file is corrupt or has the wrong schema | JSON or schema check fails | Fail closed: failing, and the detail names the file. The next writer moves it to `.corrupt-<stamp>`. | U4, Q13 |
| F6 | Two probes at once | `probe.lock` is held | The second prints `not run … another deep probe is running (pid N, since …)` and exits 5. The first is unaffected. | Q7 |
| F7 | The wheel imports from the wrong path (a shadowed copy, another interpreter, `PYTHONPATH`) | the probe prints the resolved and native files and `shadowed` lines; the record is keyed by the loaded sha256 | A probe of a different binary cannot vouch for the one the lab loads. The lab's binary stays "never tested", and `deep status` names the other probe. | U1, Q12, S4 |
| F8 | The build script would install a copy the import never loads | `find_spec` target check before the copy; sha256 check after | A refusal naming the owner of the winning path; nothing copied | S4, S6, S7 |
| F9 | The sibling repo has the renamed crate (today) | `crates/queuellm-api` present | A refusal with the true reason; nothing built; `auto` does not switch channel | S2, S8; W7 |
| F10 | A build exits 0 but is poisoned (Metal toolchain down at build time) | `downloadComponent` count above 0 | A refusal; not installed | S5 |
| F11 | The binary is replaced on disk while a lab runs | the pinned `(size, mtime_ns, inode)` differs | Detail: "restart the lab to load the new build". The old key's state stays. | U2 |
| F12 | The verdict store is not writable, or the lock is busy over 2 s | `OSError` or lock timeout | Failing still shows in that process; status adds "not saved: <err>"; the probe prints `NOT SAVED` | U8, Q17 |
| F13 | Check, preload, boot warm, `recheck_unusable` or `report_success` try to say healthy | the read-time overlay (C5) | Tier 1 stays unhealthy, with the detail | H1-H6, H8 |
| F14 | A failure lands while a passing probe runs | `failure.last_ts > started_ts` | The pass does not clear it. The probe prints "a deep call failed elsewhere while the probe ran; still failing". | U6 |
| F15 | A one-off failure that is not the bug | same as F1 | Deep stays failing until a probe passes (D1). The detail says what to run. | U5; TD4 |
| F16 | A Curator pass starts while deep is already failing | `explain()` returns `deep_failing`; the fast router is chosen | The report names the fast model and the reason; nothing counted as deep | C6 |
| F17 | A grow pass has any failed call | `_CountedRouter.failed > 0` | Not applied; bundle bytes unchanged; no evolution entry | C4 |
| F18 | A Curator review is cancelled mid-pass | `attempted < planned`, `failed == 0` | Outcome partial ("stopped"), never "All clear" | C1b |
| F19 | The model dir is missing | the `AeroLLMBackend.__init__` error | Probe: not run, exit 5. Lab: failing in that process only, and the detail names the dir. | Q18, U12 |
| F20 | Not enough memory for a second copy (a 16 GB Mac with the lab holding the model) | available < weights + 2 GiB | `not run … stop the lab or pass --force`; exit 5 | Q9 |
| F21 | The supervisor is killed with SIGKILL | the child sees `os.getppid()` change before its next call | The child exits; nothing recorded. A call already running finishes first (no cancel, A6). | Q19 |
| F22 | Native code writes to the child's stdout | protocol lines need `"probe": 1` | No effect on the result | Q8 |
| F23 | A test writes the operator's real verdict | the autouse fixture sets `ARAIL_DEEP_STATE_DIR` per test | A session check: `<checkout>/lab/data/deep/verdict.json` is unchanged by the suite | U13 |
| F24 | The probe or status reaches the network | a socket guard in tests; HF offline in the child | None expected | SEC3 |
| F25 | Error text carries HTML or quotes | escaped renderers (`esc`, `textContent`) | Rendered as text | SEC2 |
| F26 | New user-visible "AeroLLM" text | `tests/test_no_user_visible_aerollm.py` | The build fails until it says QueueLLM | regression |
| F27 | The probe passes, then a portal session fails (disconfirming evidence 1) | a failure recorded after a pass | Status turns failing again at once (the F1 path). No "deep works" claim stands. | U5, U6 |
| F28 | Ring depth differs between the probe and the lab | both are recorded | Status shows the probe's ring depth. The teaching says a pass at one depth does not prove another (TD8). | Q1 |
| F29 | A number is printed without teaching | TQ1-TQ4 | The build fails | TQ1-TQ4 |
| F30 | The win run alters the operator's venv | the before/after manifest in the win script | Row W8 fails; stop | W8 |
| F31 | The opt-in health loop repeats "MODEL TIER DOWN" every 60 s while failing | announce only on a change | One line per change | H10 |

## Test strategy

### The fake runtime (everything except the one live check)

`tests/fakes/fake_deep_runtime.py` provides the fixture `fake_deep(tmp_path, monkeypatch, mode=...)`.
- It writes `<tmp>/fakepkg/aerollm_api/__init__.py`: a pure-Python `Runtime` with `start()` and `generate()`, and `__version__ = "0.0.0-fake"`. It registers a module `aerollm_api._native` in `sys.modules` with `__file__ = <tmp>/fakepkg/aerollm_api/_native.abi3.so`, a dummy file. Identity then works with no compiled code and no product seam.
- The mode comes from `ARAIL_FAKE_DEEP_MODE`, which only the fake reads: `pass`, `fail2` (with the real bug's message text), `empty2`, `crash3` (`os.kill(os.getpid(), SIGABRT)` on call 3), `hang2`, `loadfail`, `slow:<s>`, `stdout-noise`.
- It points `AEROLLM_MODEL` and `ARAIL_MODELS_DIR` at a tmp dir. It resets `AeroLLMBackend._shared`, `deep_policy._reset_for_tests()` and the `deep_verdict` caches. Subprocesses get `PYTHONPATH=<tmp>/fakepkg:<checkout>/src`. Call counts go to a tmp file, for cross-process asserts.
- It runs on Linux CI: no Metal; without transformers the tokenizer falls back to the ChatML wrap.

`tests/conftest.py` gets one autouse fixture: `ARAIL_DEEP_STATE_DIR=<tmp_path>/deep` and `deep_verdict._reset_for_tests()`.

### Unit and integration tests (fake runtime; run in CI)

- **deep_verdict** (`tests/test_deep_verdict.py`). U1 identity for the package, top-level and shim layouts, and the zero and two-native cases. U2 pinning and `binary_changed_on_disk`. U3 missing file → untested. U4 corrupt and wrong schema → failing, then moved aside. U5 `note_call(False)` persists and another process sees it; a later `note_call(True)` does not clear. U6 a pass clears only failures before `started_ts`. U7 a fail records the first error and its index. U8 an unwritable dir → failing in process, with `store_error`. U9 8 processes × 50 writes: the final file is valid and `count` is exact. U10 two binaries are independent. U11 `state_dir()` rules, never under site-packages. U12 a model-dir failure is not persisted. U13 session check: the suite never changed `<checkout>/lab/data/deep/verdict.json`.
- **Backend hook** (`tests/test_deep_backend_hook.py`). B1 a failure is noted once and re-raised unchanged. B2 a success clears nothing. B3 a raising `note_call` never breaks `complete()`. B4 a load failure is persisted; a model-dir failure is not. B5 `health_check()` is false while failing.
- **Policy** (`tests/test_deep_policy_failing.py`). P1 `deep_failing` for both foreground values; earlier codes still win; existing codes unchanged. P2 a real `AeroLLMBackend` over `fail2`: call 1 deep; call 2 fast and stamped; call 3 makes no deep attempt (the fake's count stays 2) and is stamped. P3 `empty2` behaves the same. P4 `AgentHeldError` unchanged. P5 `with_deep_reason` keeps the trace id, brain, effort and foreground.
- **Registry** (`tests/registry/test_deep_verdict_overlay.py`). H1 `_probe_aerollm` says unhealthy while failing, resident or not. H2 **Check stays unhealthy** (the erase regression). H3 `report_success` cannot show healthy. H4 the preload and boot-warm re-probes. H5 passing → today's statuses. H6 `recheck_unusable`. H7 the boot and preload lines never say "ready" while failing. H8 `to_state()` in a fresh registry with no re-probe ever run already says unhealthy. H9 on minimalist, registry reads never import `aerollm_api`. H10 the opt-in loop announces once per change. H11 a wheel-less or minimalist lab: tier-1 health and the banner are exactly as today. H12 after a passing probe, the next `GET /api/models/state` shows today's residency status with no Check.
- **Truth strip** (`tests/test_research_truth_deep_failing.py`). T1 the wire shape in C5. T2 the `research.html` danger branch exists.
- **Chat stays loud** (`tests/test_chat_deep_stays_loud.py`). With `fail2`, two `POST /api/chat` calls with `backend: aerollm`: the second reply starts `Inference failed:`, and the verdict is failing.
- **Probe** (`tests/test_deep_probe.py`; supervisor and child as subprocesses). Q1 `pass` → exit 0, 5/5, recorded passing, ring depth recorded. Q2 `fail2` → exit 1, 1/5, first error at call 2: the fake twin of the win. Q3 `crash3`. Q4 `hang2` with a 2 s deadline; no orphan pid. Q5 `loadfail`. Q6 not importable → exit 5, nothing recorded. Q7 two probes at once. Q8 `stdout-noise`. Q9 the memory refusal and `--force` (memory reader injected in-process). Q10 the JSON schema. Q11 the text renderer and legend. Q12 shadow listing. Q13 a corrupt store before the probe. Q14 the supervisor never imports `aerollm_api`. Q15 SIGINT → 130, nothing recorded. Q16 `PROMPT_SET_SHA256` pinned to `b5bcb562…`. Q17 store not writable → `NOT SAVED`. Q18 model dir missing → exit 5. Q19 supervisor SIGKILL → the child exits after its call. Q20 the `arailctl deep probe` dispatch, help line and the `docs/cli.md` exit-code row.
- **Curator** (`tests/test_curator_honest.py`; scripted routers like `tests/test_world_growth.py:30-56`). C1 a review with failures from call 2: `judged 1`, `failed N-1`, outcome partial, no "All clear", and `review.json` agrees. C1b cancelled mid-pass → partial. C2 all fail → failed. C3 complete with no flags → "All clear" with the full-review clause. C4 grow with a failure: bundle files byte-identical, `verify_seal` unchanged, `evolution.json` unchanged, state `not_applied`. C5 grow without failures: the existing tests pass unchanged. C6 deep failing before the pass: the fast judge is named with the reason. C7 the forge partial and `GateRefused` messages. C8 `_CountedRouter` passes attributes through and re-raises unchanged. C9 teaching (TQ3). C10 `world-terms.js` renders the notice with `el()` and treats `not_applied` as terminal (a static check, weak: TD11).
- **Build script** (`tests/test_build_aerollm_honest.py`; scratch venvs as in `tests/test_aerollm_bundle_install.py:52-110`; a fake `cargo` on `PATH` that records argv and writes an artifact). S1 no repo → the true message. S2 renamed crate → refusal; cargo never invoked; site dir unchanged. S3 neither crate. S4 old crate plus a pip-owned `aerollm_api/` package (dist-info with RECORD) in the scratch site → shadow refusal, nothing copied. S5 an artifact with `downloadComponent` → refusal. S6 old crate, clean site, a fake package so identity resolves → installed, sha256 checked, `aerollm_api.build.json` with commit and sha256. S7 sha256 mismatch → copy removed, exit 1. S8 `auto` with the renamed crate → refusal, no bundle attempt (`AEROLLM_BUNDLE_FILE` points at a tarball that must not be read). S9 `status` against a fixture site that mimics the live layout → the C7 lines, channel `unknown — … replaced outside pip`. S10 the false strings never appear in any mode's output. S11 `bundle` with a pip-owned package → shadow refusal before F7.

### The one live check: `scripts/deep-honest-win.sh`

This is the only test that needs the real wheel and the real model. It runs on this Mac, on today's broken wheel, and never changes the operator's venv. It is not part of pytest.

- **Inputs.** `ARAIL_WIN_PYTHON`, default `$REPO_ROOT/.venv/bin/python`. From the worktree, set it to `~/ProJects/qukaizen-arail/.venv/bin/python`. `ARAIL_WIN_ENV_FROM`, default `$REPO_ROOT/.env`: only `AEROLLM_MODEL`, `ARAIL_MODELS_DIR`, `AEROLLM_KV_BUDGET_PCT` and `LAB_TIER` are copied into a scratch `.env`. No home path is hard-coded.
- **Isolation.** One `mktemp -d` root. Each row gets its own `ARAIL_DATA_DIR`, `LAB_ROOT`, `LAB_PKB`, `ARAIL_WORLDS_DIR`, `ARAIL_DEEP_STATE_DIR`, `ARAIL_ENV_FILE`, and an absent `ARAIL_MODEL_DEFAULTS_FILE`. Always set: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=<checkout>/src:<checkout>`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `LAB_MODE=airgapped`, `ARAIL_EDGE_PORT=0`, a random scratch portal token (0600), a random `ARAIL_PASSWORD`, `ARAIL_ALLOWED_HOSTS=testserver`. No port is bound. Rows drive the portal app in-process with `TestClient` and no lifespan, so the consumer under test is the only deep caller (TD7).
- **Rows** (`scripts/deep_honest_win.py`; one fresh child process per row):
  - W0 preflight. `aerollm_api` resolves; the model dir exists. Take a manifest (path, size, mtime, sha256) of `site-packages/aerollm_api*` and its dist-info, and a site-packages listing. Record QueueLLM `HEAD` and `status --short`.
  - W1 Chat ×5 with the mixed prompts, `POST /api/chat` with `backend: aerollm`. Reply 1 answers. Replies 2-5 start `Inference failed:`. Then read the surfaces.
  - W2 Background agents ×5. Open the gate with the portal's own endpoints (window override `heavy`, profile `throughput`). Then Buddy ×2, debt advisor ×2 and consolidation analyzer ×1, each through its `ArailHost.llm_complete` inside `agent_call(<id>)`. Expect 2 deep attempts (1 ok, 1 error), then fast calls stamped `deep_failing`, whether or not Ollama answers. The row prints which. If `explain(foreground=False)` is not `ok` before call 1 (for example Metal memory pressure at or above the 0.60 ceiling), the row stops as INCONCLUSIVE with the reason.
  - W3 Curator review of a scratch 6-term World built with `tests/world_bundle_builder.py`. Expect outcome partial, judged 1 of 6, failed 5, and no "All clear" in the scratch activity log.
  - W4 Curator grow of the same World. Expect state `not_applied`; the sha256 of `terms.json`, `manifest.json` and `evolution.json` unchanged; no "evolved via" line.
  - W5 `PYTHON=$ARAIL_WIN_PYTHON ./arailctl deep probe --json` with a fresh state dir. Expect exit 1; `passed 1`, `total 5`; `first_error.call 2` containing `rms_norm`; `resolves_to` ending in `aerollm_api/__init__.py`; `binary.sha256 == d62e1e16…` and `version == 0.1.0-rc.2`, both checked against `DAY1.json`.
  - W6 a fresh process on W5's state dir, with no deep call. Every surface says failing. Press Check. Still failing.
  - W7 build script. `deep status` with the live python on W5's state dir gives the C7 lines with channel `unknown`. `deep rebuild` and `build-aerollm.sh auto`, with a scratch empty venv's python and the real sibling repo, refuse with exit 1. These five strings appear 0 times across all three outputs: `No QueueLLM sibling repo`, `QueueLLM ready`, `(missing → release/bundled channel)`, `(found → source/dev channel)`, and a `channel:` line that says `release` or `dev` without proof.
  - W8 postflight. The W0 manifest and listing are identical. QueueLLM `HEAD` and status are identical. `git diff --quiet 62d5ebdf -- src/dac_world` passes. No probe child pid is left.
- **Surfaces read after W1-W4, and in W6.** Each is a silent-outcome check. `explain(foreground=True)` and `explain(foreground=False)` → `deep_failing`. `GET /api/research/status` → `reason_code` `deep_failing` and `eligible_now` false. `GET /api/models/state` → tier 1 `unhealthy`. `POST /api/models/health/refresh`, then `GET` → still `unhealthy`. `deep status` → `generation: FAILING`. The boot-line renderer → no "ready".
- **Output.** A table: row, deep calls attempted and failed, silent outcomes found, PASS or FAIL, and a teaching cell for each number. For "silent outcomes": it means places where ARAIL said deep was fine or done when it was not; the bar is 0, because any one is the bug this sprint fixes; out of range names the row and the surface; the failure lives in that surface's code. Exit 0 only when every row passes. An INCONCLUSIVE row is not a pass. About 3-5 minutes.

### Which tests need the real wheel

Only `scripts/deep-honest-win.sh`, rows W1-W7. Nothing in pytest needs it. W5 is also the "probe matches DAY1.json" check.

### Regression

The full suite stays green. That includes `tests/test_deep_policy*.py` (codes unchanged), `tests/test_world_growth.py`, `tests/test_aerollm_bundle_*.py` and `tests/test_aerollm_local_sibling_build.py` (assertions that looked for "QueueLLM ready" change on purpose), `tests/test_no_user_visible_aerollm.py`, and `tests/test_host_contract_vendor.py` (the Buddy contract bytes do not change). `src/dac_world/` stays byte-identical to `62d5ebdf`.

### Performance

- `explain()` with a warm cache: 10 000 calls under 1 s on this Mac (about one `stat` each).
- The first identity hash of a 22 MB binary: under 150 ms, once per process.
- `note_call(ok=False)` with a write: under 20 ms.
- Check adds no generation. The fake runtime's generate count does not change under Check, preflight, preload or boot warm.
- A probe on a broken build: about 12-23 s today (Day-1). Recorded, not gated.

### Security

- SEC1: `deep/` is 0700 and its files 0600. A symlink planted at `verdict.json` is replaced, not followed. The lock opens with `O_NOFOLLOW`.
- SEC2: an error containing `<img src=x onerror=…>` renders as text in the truth strip (`esc`), the switcher (`esc`) and World Terms (`textContent`).
- SEC3: the probe child under a socket guard (a `sitecustomize.py` that raises on `connect`) still completes with the fake runtime. The child env carries `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
- SEC4: every subprocess is an argv list (`lsof -t -n -P -- <path>`, `ps -o command= -p <pid>`). No shell string is built from a path or an error.
- SEC5: a refused install leaves the site dir listing identical.
- SEC6: a canary env value never appears in probe output, the verdict file or activity lines.
- SEC7: no new egress. Airgapped mode is unchanged; the probe and status are local only.

## Tech debt

**Added**
- TD1: the verdict is keyed by binary only, not binary + model + ring depth. A pass with one model vouches for another. Trigger: the 2026-11-19 model question, or a depth-specific bug.
- TD2: nucleus builds its own `Runtime`, so its failures do not feed the verdict (A5). Trigger: nucleus becomes a regular deep consumer.
- TD3: `explain()` now has `deep_failing`, but Buddy's contract has no `/deep`. When `/deep` reopens (D-S6-8), the contract must carry the code (S6 route step 4).
- TD4: one failure keeps deep failing until a probe passes (D1). Trigger: the operator reports a lockout after a one-off error.
- TD5: `deep rebuild` still cannot build QueueLLM main. D-S6-10 stays open but narrower: the false lines and the shadowing are fixed, and the build is moot while main fails the probe. Trigger: a QueueLLM build passes the probe 5/5.
- TD6: the old-crate source path is now reachable only with a pre-rename checkout. Trigger: drop it in TD5's sprint.
- TD7: the win harness drives the app with `TestClient` and no lifespan. Boot paths are covered by unit tests only.
- TD8: the probe tests one ring depth: the one the profile gives when it runs.
- TD9: a judge answer that does not parse still counts as judged (pre-existing; the vision's bar is "returned").
- TD10: `deferred_now` and the other non-failing codes are still not stamped on traces.
- TD11: the World Terms notice has only a static test. The portal has no JS harness.
- TD12: the 20 empty grow passes stay in three Worlds' `evolution.json`, and those bundles were resealed 20 times. They are records of what happened. Not repaired here.

**Repaid**
- S6's ARAIL finding: agents hide the failure (S6 ARCHITECTURE, Tech debt).
- Re-probes erasing the tier-1 failure (vision, Problem, bullet 1).
- The Curator's false "All clear" and false "evolved", and resealing after failed calls (V1).
- `deep status`'s false crate, channel and readiness lines. The build script's false "No QueueLLM sibling repo" and "QueueLLM ready". The shadowed install. (D-S6-10, in part.)
- D-S6-11 gets its text and four builds of evidence. Filing stays the operator's (O3).

**Net:** negative. Five silent paths close. Twelve small rows are filed, each with a trigger. The builder adds TD1-TD12 to `sprints/BACKLOG.md`.

## Cut

- Item 2, building QueueLLM main: OUT by the day-1 rule (main answers 1/5).
- Buddy `/deep`, `brain.deep`, and any contract delta.
- Any QueueLLM code change, the call-2 fix included.
- The opt-in fall-back switch, and new Chat error wording.
- The bundle channel itself, and a re-pin of v1.1.0.
- The `AERO_*` → `QUEUELLM_*` migration, and every rename.
- Moving the deep slot to another model (the 2026-11-19 question).
- Code changes to the researcher and nucleus failure paths (both loud). The backend hook records the researcher's failures without touching it.
- Repairing past `evolution.json` entries and bundles (TD12).
- A uvicorn-based win harness, a ring-depth matrix, and `deferred_now` stamping.
- Any change to `src/dac_world/` (vendored, ADR-0004).

## Cross-repo pins and stamps

- Nothing new is vendored into ARAIL. `src/dac_world/` does not change (W8 checks it). Buddy's vendored contract does not change (`tests/test_host_contract_vendor.py`).
- Evidence pins, recorded in `DAY1.json` and `ISSUE.md`:
  - QueueLLM main `e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715`; artifact `d7e5aa57…`.
  - Bundle commit `2ae56afb3f8807c5df29daccf37ffb2413966f51`; `.so` `7c77aa85…`; tarball `57f30364…` = `pyproject.toml:282`.
  - The 2026-10-11 artifact `f92a4cdd…`, from branch `qukaizen/queuellm-gpt-oss-fix` (head `25dc9aab`). Its exact source commit is unknown, and the record says so.
  - The live binary `d62e1e16…`.
- Every probe record carries the binary sha256, the version, `prompt_set_sha256` and the python path. Every source build carries `aerollm_api.build.json` with the QueueLLM commit, the dirty flag and the artifact sha256 (C7). That is the same role as `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`.
- `BUNDLE.json`, `aerollm_bundle_tag` and `aerollm_bundle_sha256` are untouched.

## Operator items

The vision's list stands, plus one new item:
- **New:** after merge, run `./arailctl deep probe` once in the main checkout. Until then every lab says "never tested" (true) until a deep call fails. Expected today: 1/5, exit 1.
- O2: removing the shadowed top-level `aerollm_api.abi3.so` (2026-05-02) from the live venv is optional cleanup and his call. Nothing in this sprint needs it.
- O3: file `ISSUE.md`. O4: the witness line (disconfirming evidence 2). O5: the 2026-11-19 decision.
- For his information: three Worlds carry 20 empty "evolved via aerollm" passes (V1). They changed no terms.

## Recommended implementation order

0. Commit `DAY1.json` (Appendix A, verbatim) and `ISSUE.md` (Appendix B). Docs only.
1. `src/arail/deep_verdict.py` (C1, C2), the conftest fixture, the fake runtime, and U1-U13.
2. The backend hook in `router/backends.py`, and B1-B5.
3. `explain()`, `complete_preferring_deep()`, `agent_context.with_deep_reason()`, and P1-P5.
4. The registry overlay and loop announcement, the `model_warmth` lines, the truth strip and `research.html`, the Chat-stays-loud test, H1-H12, T1-T2.
5. `src/arail/deep_probe.py` and `deep_probe_prompts.py`; the `arailctl` dispatch and help; `docs/cli.md` (probe, exit code 5, status lines, what Check does); Q1-Q20, TQ1, TQ2.
6. `portal/world_routes.py` (`_CountedRouter`; review, grow, forge), `world-terms.js`, C1-C10, TQ3.
7. `scripts/build-aerollm.sh` (C7), `scripts/setup.sh:678` and `:685`, the two hints, S1-S11, TQ4.
8. `scripts/deep-honest-win.sh` and its driver. Run it on this Mac. Record each row in BUILD_LOG with its teaching.
9. The full suite. `tests/test_no_user_visible_aerollm.py` green. `src/dac_world/` byte-identical. TD rows in `sprints/BACKLOG.md`.

Stop and come back to design if any step needs an edit to `src/dac_world/`, a new long-running process, or a Buddy contract change (vision, disconfirming evidence 4).

## Appendix A: `DAY1.json`

Commit verbatim as `sprints/2026-10-29-arail-deep-honest/DAY1.json`.

```json
{
 "date": "2026-10-29",
 "sprint": "2026-10-29-arail-deep-honest",
 "gate": "VISION day-1 check: a QueueLLM build after main must answer 5 of 5 sequential deep calls through ARAIL's own AeroLLMBackend in one fresh process; a main build must first pass QueueLLM's preamble check (downloadComponent count 0)",
 "verdict": "Item 2 OUT: main e730d5fe builds and passes the preamble check but answers 1 of 5; the 2026-10-11 build and bundle v1.1.0 also answer 1 of 5",
 "pins": {
  "arail": "62d5ebdf",
  "queuellm_main": "e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715",
  "bundle_commit": "2ae56afb3f8807c5df29daccf37ffb2413966f51",
  "gptoss_branch": "qukaizen/queuellm-gpt-oss-fix, head 25dc9aab (2026-10-11 21:42); the artifact was built 2026-10-11 11:13, so from an earlier commit that is not recorded"
 },
 "d1_metal": {
  "command": "xcrun -sdk macosx metal --version",
  "exit": 0,
  "version": "Apple metal version 32023.921 (metalfe-32023.921.6)",
  "note": "failed on 2026-10-27; no reboot since (booted 2026-10-21); cause not verified"
 },
 "method": {
  "python": "~/ProJects/qukaizen-arail/.venv/bin/python, read only, env -i, PYTHONDONTWRITEBYTECODE=1",
  "arail_code": "branch qukaizen/arail-deep-honest src on PYTHONPATH, so ARAIL's own AeroLLMBackend",
  "shadowing": "a scratch dir first on PYTHONPATH for D2, D3, D4; none for the live wheel",
  "state": "ARAIL_DATA_DIR, LAB_ROOT, LAB_PKB, ARAIL_ENV_FILE, ARAIL_MODEL_DEFAULTS_FILE in scratch; no portal",
  "env": "AEROLLM_MODEL=Qwen2.5-7B-Instruct-4bit, ARAIL_MODELS_DIR=/Users/Shared/models, AEROLLM_KV_BUDGET_PCT=0.60, LAB_TIER=maximus, HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1",
  "calls": "5 sequential AeroLLMBackend.complete calls: short, long, the short repeated verbatim, long with the same lab-context prefix, a second short; max_tokens 384; temperature 0.2",
  "prompt_set_sha256": "b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e",
  "ring_depth": "2 (profile:balanced) in every run",
  "s6_comparison": "S6 asked the same question 5 times; this check mixes short and long prompts; both give 1 of 5 on the live wheel"
 },
 "runs": {
  "live_venv_wheel": {
   "resolves_to": "site-packages/aerollm_api/__init__.py (package)",
   "native": "site-packages/aerollm_api/aerollm_api.abi3.so",
   "native_sha256": "d62e1e16e93c264b79eae35e5766a84f56091ed8e4cc66a89e18791effc7579e",
   "bytes": 21894128,
   "version": "0.1.0-rc.2",
   "pip_record_sha256": "80bfe0f033fa6341c8e951af81924c2cfb2539e2fc5945e8b4f2debac54c4465",
   "pip_direct_url": "file://<home>/ProJects/aerollm/crates/aerollm-api (editable)",
   "shadowed": "site-packages/aerollm_api.abi3.so, 17411888 bytes, 2026-05-02, sha256 b780d7240e7383586a7860349ad9aa02435870cd70f47a05ee6a3c494e9500a3",
   "load_s": 2.21,
   "calls_ok": 1,
   "calls_total": 5,
   "ok_seconds": [9.65],
   "failed_seconds": [0.0, 0.01, 0.01, 0.01],
   "prompt_tokens": [33, 1933, 33, 1933, 34],
   "first_error_call": 2,
   "first_error": "RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2Model::forward (prefill head): \"[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements."
  },
  "d2_build_2026_10_11": {
   "source": "~/ProJects/queuellm-gptoss-fix-wt/target/release/libqueuellm_api.dylib, copied to scratch as queuellm_api.abi3.so under an aerollm_api shim",
   "native_sha256": "f92a4cdd1af06a8f90287295131a3588fe3896dca90a851a1fdd8705ac719fc4",
   "bytes": 19499344,
   "version": "1.1.0",
   "preamble_downloadComponent": 0,
   "load_s": 1.86,
   "calls_ok": 1,
   "calls_total": 5,
   "ok_seconds": [17.61],
   "failed_seconds": [0.0, 0.02, 0.02, 0.02],
   "first_error_call": 2,
   "first_error": "RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): \"[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements."
  },
  "d3_bundle_v1_1_0": {
   "source": "~/ProJects/qukaizen-arail/dist/aerollm-bundle/aerollm-api-v1.1.0-macos-arm64.tar.gz, extracted to its own scratch dir; no download",
   "tarball_sha256": "57f30364738580d4a7808f9165ddfdf692eb52afaa435e491b4eda39afb63e93",
   "tarball_matches_pin": "pyproject.toml:282",
   "native_sha256": "7c77aa8511d019a320374f539608ca073a4d5e019ae1e58b0102b922cb360ce7",
   "bytes": 22188336,
   "version": "1.0.0",
   "load_s": 1.3,
   "calls_ok": 1,
   "calls_total": 5,
   "ok_seconds": [17.53],
   "failed_seconds": [0.0, 0.01, 0.01, 0.01],
   "first_error_call": 2,
   "first_error": "RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): \"[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements."
  },
  "d4_main_e730d5fe": {
   "build": {
    "commands": [
     "git -C ~/ProJects/qukaizen-queuellm archive e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715 | tar -x -C <scratch>/d4/qsrc",
     "cd <scratch>/d4/qsrc && CARGO_TARGET_DIR=<scratch>/d4/qtarget cargo build --release --offline -p queuellm-api --features extension-module"
    ],
    "exit": 0,
    "wall": "1 min 57 s",
    "offline": true,
    "preamble_downloadComponent": 0,
    "artifact": "libqueuellm_api.dylib",
    "artifact_sha256": "d7e5aa57929d23fea49f6297f53bead3df3b74a6414a22f55ef46159bd074fd8",
    "bytes": 22919408
   },
   "version": "1.1.0",
   "load_s": 1.52,
   "calls_ok": 1,
   "calls_total": 5,
   "ok_seconds": [20.65],
   "failed_seconds": [0.0, 0.02, 0.02, 0.02],
   "first_error_call": 2,
   "first_error": "RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): \"[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements."
  }
 },
 "untouched": "after the runs no file in the live site-packages was newer than the probe script; aerollm_api/__pycache__ still dated 2026-06-21; qukaizen-queuellm git status empty before and after; the arail worktree clean",
 "decision": "VISION cut table, row 3 (main fails call 2): item 2 out; only the honest refusal ships; wedge rows 1-5 ship; the live venv is not touched",
 "teaching": {
  "calls_ok": "Meaning: deep answers that came back non-empty, out of 5 in a row in one process. Threshold: 5 of 5, because every ARAIL consumer makes more than one deep call per process and call 2 is where every known broken build fails. Out of range: 1 of 5 means the runtime answers once per process; a lab using it gets one deep answer per restart and silent fall-backs after. Failure lives in: the QueueLLM binary (mlx-native Qwen2 prefill, rms_norm weight of 1 element), not in ARAIL.",
  "ok_seconds": "Meaning: wall time of the one call that answered (prefill plus up to 384 tokens; 38 to 44 words here). Threshold: none for this check; /deep would need a median of at most 120 s. Out of range: not decidable, because only one call per build answered. Failure lives in: not applicable.",
  "failed_seconds": "Meaning: time until a failing call raised. Threshold: none. Out of range: 0.00 to 0.02 s means it failed at the first prefill step, before any decoding, for 33 and for 1,933 prompt tokens, and even when it repeated call 1 exactly. Failure lives in: state the runtime carries from call 1 to call 2 inside one process (QueueLLM).",
  "prompt_tokens": "Meaning: prompt length after ARAIL's chat wrap, counted by the model's tokenizer. Threshold: short about 33; long about 1,933, the size of a chat turn with lab context. Out of range: not applicable. Failure lives in: not applicable.",
  "load_s": "Meaning: weights load plus runtime start. Threshold: none here (the shipped probe allows 300 s). Out of range: not applicable. Failure lives in: not applicable.",
  "preamble_downloadComponent": "Meaning: count of the Metal toolchain error marker in a built binary. Threshold: 0, because a build made while the toolchain is down exits 0 and then fails at runtime. Out of range: above 0 means throw the build away unprobed. Failure lives in: the Metal toolchain at build time.",
  "build_wall": "Meaning: time for the offline release build of queuellm-api from a clean scratch target. Threshold: the vision's 3-hour box for build plus probe. Out of range: not applicable. Failure lives in: not applicable."
 }
}
```

## Appendix B: what `ISSUE.md` must contain

Filed only with the operator's OK (O3). Draft in the sprint folder.
- Title: "Qwen2.5-7B-Instruct-4bit: the runtime answers once per process; every later generate fails in prefill with [rms_norm] … has 1 elements".
- S6's text verbatim (buddy S6 `BUILD_LOG.md`, "Architect feedback required", item 2).
- A table of all seven builds tested: S6's three (2026-06-28, 2026-07-22, 2026-08-05) and today's four. For each: provenance, sha256, reported version, call-1 time, error wording.
- Reproduction: ARAIL's `AeroLLMBackend` builds `Runtime(model_path, kv_memory_budget=…, ring_depth=2)`, calls `.start()`, then `.generate(<chat-wrapped prompt>, max_new_tokens=384, temperature=0.2)` five times. Once merged, `./arailctl deep probe` reproduces it.
- Observations: call 2 fails in under 0.02 s at the first prefill step, whatever the prompt length (33 or 1,933 tokens), and even when it repeats call 1. The June build words it `Qwen2Model::forward (prefill head)`. The later builds say `Qwen2 layer (chunked prefill head)`. ARAIL's production failures happened at night, probably at ring depth 4 (A22).
- Environment: M5 Max, 36 GB; Darwin 25.6.0; metal 32023.921; Python 3.11.15.
- Context: ARAIL's deep slot is dense Qwen2.5-7B. QueueLLM is MoE-exclusive (`CLAUDE.md:49-63` in QueueLLM). The question: is a fix planned? ARAIL revisits on 2026-11-19.
- No lab data, no World or term names.

## Appendix C: the day-1 prompt set

`deep_probe_prompts.py` must reproduce this exactly. The test pins the hash.

```python
caps = ("You are the lab's AI engineer. The lab runs locally. Agents: Buddy (lab partner), SRE (crash watcher), "
        "Researcher (autoresearch engine). Surfaces: Dashboard, Chat, Autoresearch, Knowledge Base, Agents. "
        "Answer briefly; cite the note you used. ")
state = ("Live state: tier maximus; mode hybrid; active goal \"reduce validation loss on the tiny-shakespeare char model below 1.45\"; "
         "last experiment #41 lr=3e-4 warmup=200 steps val_loss=1.512; next planned #42 lr=2e-4 warmup=400. ")
notes = []
for k in range(1, 4):
    notes.append(f"[note {k}] " + " ".join(
        f"Observation {k}.{j}: run {40+j} used batch {32*j} and gradient clipping {0.5+j/10:.1f}; loss curve flattened after step {300*j}; "
        f"the researcher suggested trying cosine decay with a floor of {j}e-5 and logging grad-norm every 50 steps."
        for j in range(1, 9)))
LAB = (caps * 6) + "\n\n" + state + "\n\n" + "\n\n".join(notes) + "\n"      # 6,680 chars
S1 = "In two sentences, what does learning-rate warmup do?"
S2 = "Name one risk of evaluating a model on its own training data."
PROMPTS = [["short", S1],
           ["long", LAB + "\n\nQuestion: summarise the lab's current goal in three bullet points."],
           ["short-repeat", S1],
           ["long-shared-prefix", LAB + "\n\nQuestion: which agent should run next, and why? One paragraph."],
           ["short-2", S2]]
PROMPT_SET_SHA256 = hashlib.sha256(
    json.dumps(PROMPTS, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
# == "b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e"
```
