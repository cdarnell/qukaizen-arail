# The 54 red tests — what they actually are

> Status: **triage complete; root cause found and 16 of 54 fixed.**
> 2026-08-17.
> Filed from `docs/plans/autoresearch-integration.md`, whose H0 finding
> (the tuning loop could not commit at all) survived precisely because
> nobody was watching this red.

## Why this exists

Running the suite the way `CONTRIBUTING.md:54` tells you to —
`python -m pytest` — produces **54 failures out of 5,654** on a clean
checkout. That number is worse than useless: a contributor cannot tell
their breakage from the standing breakage, so the suite has stopped
being a tripwire. H0 is the proof. The tuning loop's headline behaviour
was broken in a way one real `git add` would have caught, and the red it
would have shown up in was already ignored.

## The headline: most of it isn't broken product

**33 of the 54 failures pass when their file is run on its own.** They
are cross-test pollution — state leaking from an earlier test in the
same process — not defects in the code they point at.

Method: extract every failing file from a full `-p no:randomly` run, then
run each file standalone and compare.

(Counts below are the pre-fix numbers, kept because the classification
is what matters; 16 of the 33 pollution victims are now fixed.)

| Class | Failures | What it means |
|---|---:|---|
| **Pollution victims** — file passes alone | **33** | Not a product bug. The test is fine, the code is fine, an earlier test left the process dirty. |
| **Genuine** — still fails alone | **21** | Real: a stale assertion, a drifted contract, or an actual defect. |

Files that pass in isolation and fail in the suite:

```
test_r1_r3_chat_models (4)      test_w9_embedder_swap (3)
test_loader_skills_only_agents (3)  test_aerollm_model_ready (3)
test_build_qkz_corpus (2)       test_deep_default_and_tier (2)
test_model_ux_phase0_warmth_probe (2)
test_qa_model_ux_memory_and_eject_fidelity (2)
test_qa_provider_dropdown_paranoid (2)
test_aerollm_compute_source (1) test_autochecks_boot (1)
test_model_separation (1)       test_models_settle_endpoint (1)
test_onboarding (1)             test_recap_core (1)
test_runtime_profile_api (1)    test_b1_cloud_gallery_contract (1)
```

The victims cluster hard around one surface: `/api/chat/models` and the
model-readiness payload. The failures read as a contract regression —
`KeyError: 'optional_backends'`, `'deep'`, `'compact'`, `'kwargs'`,
"Legacy branch dropped keys: {onboarding, slots, switchable, …}" — which
is exactly the trap. Those keys are all present in `src/`, and every one
of those tests passes alone. **Nothing is missing from the API.** Anyone
triaging by reading the assertion message would go hunting a regression
that does not exist.

## Genuinely failing standalone (21)

These deserve individual judgement; none is diagnosed here beyond its
error, and some are near-certainly stale tests rather than live bugs.

| File | n | Error |
|---|---:|---|
| `portal/test_build_tab.py` | 4 | `404` where `200`/`409` expected — routes gone or tier-gated |
| `test_aerollm_defaults.py` | 4 | `KeyError: 'kwargs'` |
| `test_dashboard_layout_v2.py` | 2 | Mission Status card is `class="card full"`, test wants `card` |
| `test_reset_stop_scope.py` | 2 | temp-dir/port assertions |
| `test_swarm_goal_surfaces.py` | 2 | "Draft Swarm Plan" absent from dashboard + research HTML |
| `portal/test_models_api.py` | 1 | status `unknown` not in the allowed set |
| `portal/test_opencode_config_lifecycle.py` | 1 | `OPENCODE_CONFIG_DIR` missing from Popen env |
| `portal/test_opencode_lifecycle.py` | 1 | log not rotated to `.log.1` |
| `portal/test_token_compliance.py` | 1 | design-token ratchet |
| `test_cache_prewarm.py` | 1 | prompt text drifted |
| `test_dac_rename.py` | 1 | nav `class="active"` |
| `test_instance_isolation_audit.py` | 1 | `hardware.py` re-reads `ARAIL_DATA_DIR`, breaking A32.1's single-exception rule |
| `test_instance_ports.py` | 1 | `8110 != 8100` |
| `test_model_hosting_reframe_qa.py` | 1 | backend must `RuntimeError` on the sentinel |
| `test_shell_source_safety.py` | 1 | **in CI's `dac-feature-tests` list** — so that workflow is red, or this is machine-dependent |

`test_instance_isolation_audit` is the one I would look at first: it is
an architectural guard, it names a specific offender (`hardware.py`), and
a guard that has been red long enough to be ignored is worse than no
guard.

## Why it got this bad: nothing runs the whole suite

Three workflows exist, and each runs a hand-picked file list:

- `dac-feature-tests.yml` — ~22 named files
- `db-ensure-ci.yml` — ~7 named files
- `blueprint-smoke.yml` — `tests/test_imports.py`

There is no Makefile, no full-suite script, and no job that enumerates
`tests/`. A test not on one of those lists can rot forever without
anyone seeing it. Meanwhile `CONTRIBUTING.md` tells humans to run the
whole thing.

That gap is the actual defect. The 54 are a symptom.

## Recommended order

1. ~~Find and fix the polluter~~ — **done for the cascade: 54 → 38.**
   The chain is traced below and cut in three places. What remains is the
   `torch` double-import at the head of it, which no longer reaches the
   router but is still wrong and will resurface elsewhere.
2. **Find the torch double-import.** See "still open" below for what is
   already ruled out.
3. **Then gate it.** A full-suite CI job. Doing this before the count is
   near zero just institutionalises the red.
4. **Then work the 21** individually, starting with
   `test_instance_isolation_audit`.

Do not "fix" pollution victims by loosening their assertions. They are
correct; the process they run in is not.

## The cascade — found, and 16 of the 54 fixed

**The whole chain, end to end.** In a long run, `torch` gets imported a
second time, which is impossible in one process:

```
RuntimeError: function '_has_torch_function' already has a docstring
```

That poisons `transformers`, which makes `mlx_lm` unimportable, which
makes `MLXBackend.__init__` raise, which makes
`_get_primary_router()` raise, which makes `/api/chat/models` hit its
blanket `except` at `app.py:9425` and return a degraded four-key payload
`{backend, current, models, error}`. Every test asserting on the real
shape then fails with `KeyError: 'optional_backends'` / `'deep'` /
`'compact'` or "Legacy branch dropped keys" — which is why this looked
like an API regression and was not one.

Two things had to be true at once, which is why no single file and
neither half of the suite reproduced it:

1. `torch` had been double-imported somewhere earlier, and
2. `MODEL_BACKEND` was absent from the environment, so the router chose
   MLX at all.

(2) happens because several tests call `importlib.reload(arail.config)`;
`arail.config` exports `MODEL_BACKEND` into `os.environ` as an import
side effect, and a reload under a sandboxed env drops it.

**Fixed (54 → 38 failures, nothing newly broken):**

- `router/core.py` no longer falls back to the literal `"mlx"` when
  `MODEL_BACKEND` is unset. It reads `config.MODEL_BACKEND` — the
  project's own default, `"auto"` — lazily, so a reload cannot freeze it.
- `_auto_detect` no longer claims MLX on the strength of hardware alone.
  It verifies by **actually importing** `mlx_lm`, not by `find_spec`:
  a spec that cannot execute is exactly this bug, and would have
  "verified" a backend whose constructor then raised. Apple Silicon that
  cannot load MLX now resolves to `ollama_native`.
- `backends.py` stopped reporting every MLX import failure as
  "MLX not installed. Run: pip install mlx mlx-lm". It now preserves the
  real cause and chains it. That message is what made this expensive to
  find — the package *was* installed.

That last one is the honest lesson: a diagnostic that guesses its own
cause cost more than the bug did.

**Still open: the torch double-import itself.** The fixes above stop it
cascading into the router, but something is still re-importing `torch`,
and it will surface elsewhere eventually. What is ruled out, each with a
run behind it:

- No single file reproduces it: all nine 32-file chunks preceding the
  victim came back clean, and so did each half of the 283-file prefix.
- Not the files that reload `arail.config`/`arail.portal.app`
  (`test_skill_packs`, `test_w9_embedder_swap`, `test_setup_extras`,
  `test_docs_registry_qa`, `test_observability_under_load`) — each is
  clean paired with the victim.
- Not the two files that reference `transformers`/`torch` directly
  (`test_pkb_index_qa`, `router/test_aerollm_chat_template`) nor
  `test_build_qkz_corpus`, alone or all three together.
- Not a live-Ollama probe: the victim passes with `OLLAMA_HOST` at a dead
  port and with `ARAIL_SKIP_OLLAMA=1`.

Next step I would take: instrument rather than bisect. A `conftest`
hook that stamps `id(sys.modules.get("torch"))` after every test and
prints when it changes will name the culprit in one run, where file-level
bisection has already cost several and found nothing.

**Reproduction.** Deterministic, no random ordering involved:

```bash
.venv/bin/python -m pytest $(sed -n '1,283p' order.txt) \
  "tests/test_r1_r3_chat_models.py::test_r1_empty_provider_param_goes_to_legacy_branch" \
  -q -p no:randomly
```

where `order.txt` is the collection order from `--collect-only`. The
victim fails there and passes standalone, every time.

**Ruled out:**

| Hypothesis | Test | Result |
|---|---|---|
| One bad file | Each of the 9 × 32-file chunks + victim | **every chunk clean** |
| Something in the first half | files 1–141 + victim | clean |
| Something in the second half | files 142–283 + victim | clean |
| A live-Ollama probe degrading under load | victim with `OLLAMA_HOST` at a dead port, and with `ARAIL_SKIP_OLLAMA=1` | **passes both ways** — the payload does not depend on the probe |

**What the shape implies.** No 32-file window and neither half alone
reproduces it, but the union of the halves does. So it is not a single
polluting file. Two candidates remain:

1. **A pair split across the halves** — file A in 1–141 and file B in
   142–283, each harmless alone. This is the likeliest, and it is
   findable: hold all of half 1 fixed and bisect half 2 to isolate B,
   then hold B fixed and bisect half 1 for A. Budget ~8 runs of 4–6
   minutes.
2. **An accumulation threshold** — a module-level dict, an `lru_cache`,
   or repeated FastAPI startup/shutdown that only misbehaves past some
   count. `CLAUDE.md` flags a real instance of this shape: `pkb_index`'s
   degraded-state tracking (`_degraded_codes`, `_pending`,
   `_initialized_roots`, `_pkb_root_cache`) is **process-global, not
   per-root**, and is only safe because one process serves one PKB root.
   A suite runs hundreds of roots through one process. That is the first
   place I would look.

Distinguishing them is cheap: rerun the full prefix with the *first
half shuffled*. A pair-based cause usually survives; a count-based one
always does.

## Caveats

- Counts are from a single `-p no:randomly` run. With random ordering an
  earlier run showed **60** failures — the extra 6 are further pollution,
  which is corroboration, not noise.
- `test_dashboard_layout_v2` fails **2** standalone but **1** in the full
  suite: pollution can mask a failure as well as cause one.
- Environment matters and is not fully separated here: this machine has
  no `transformers` and no MLX, and has a live Ollama with
  `ai-engineer:latest` installed. Some of the 21 may be machine-shaped
  rather than broken — `test_model_separation` reads real local Ollama
  state, for instance. A clean-machine run would sharpen the split.
