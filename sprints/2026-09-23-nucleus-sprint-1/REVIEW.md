# Review: Model Forge (`local` profile) — arail.nucleus, Gate A

**Date:** 2026-09-23
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `803d7d99` (build commits `fe2b0b69..14dd54aa`, diff base `db31b2ff`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `58b754bc`
**Reviewer:** architect (review mode)

## Verdict: BLOCK

The component modules are mostly good. Seal, CertStore, contamination, closed metrics, the hash dataclass, the
gateway client, and the `/forge` path guards are real and well tested. The orchestration layer is the problem.
`build.py` and `certify.py` are what turn those modules into a signed card, and they don't wire them in. Instead
they write hardcoded numbers and placeholder hashes into the card and then **sign it**. One gap in stub detection
lets a stub build come out as a card that is `runtime: queuellm`, signed with the lab key, `key: trusted`, and
ledgered. I reproduced this (B1).

The deliverable of this sprint is the *yardstick* (ARCHITECTURE §1). Right now the yardstick can give a trusted
seal to numbers nobody measured. That can't pass Gate A, even though the real-runtime path is out of scope.

Test run on this worktree: `.venv/bin/pytest tests/nucleus -q` gave **324 passed, 2 skipped**, 0 failed.
With `NUCLEUS_QKZ_BIN=~/ProJects/qukaizen-nucleus/qkz/target/release/qkz`, `tests/nucleus/test_seal.py` gave
**14 passed**, and T-SEAL-8 passed against the real Rust verifier. The suite is green because several of the
§6-mandated tests are tautological (listed under Test coverage).

---

## Spec adherence

| Area | Status |
|---|---|
| §10 order, 27 commits, atomic | Followed. The 9 deltas in BUILD_LOG are reasonable and documented. |
| §4.1 frozen-name rule | **Honored.** Grepping `aerollm\|AERO_\|QUEUELLM_` over `src/arail/nucleus` finds hits only in `runtime_names.py`. Nothing in `arail.nucleus` writes to `os.environ`. `phases.worker_offline_env` builds a *child* env dict and adds only `HF_*_OFFLINE`/`TOKENIZERS_PARALLELISM`, which are not runtime knobs. The diff removes no `aerollm`/`AERO_` line outside the deleted `/build` files. |
| §4.10 seal format | **Honored.** Exactly 5 legacy fields (`seal.py:28,168`). Signing uses the default `json.dumps(sort_keys=True)` separators, an ASCII check, and no floats (`seal.py:121-137`). Contamination is recorded as a string (`seal.py:147`). Verified live against the real `qkz isotope verify`. |
| §4.10 `verify` | **Not honored.** See B2: `chain` is never computed, and the CLI never recomputes `eval_hash`. |
| §4.5 preflight | **Partially honored.** The estimator port and refusal message are fine. But `build` runs preflight **with no models** (`build.py:340`), so no memory rows are ever produced. Only 3 of the 10 designed capability rows exist (`preflight.py:217-222`): logprobs probe, parity, disk, corpus-staged, cert-count, checkpatch and student<8B are missing. `plan <slug>` still prints "preflight … not yet available" (`plan.py:111-114`). |
| §4.6 residency | Classifier exists and is tested. **The sampler never runs.** Certify hardcodes `residency_status="unmeasured"` (`certify.py:71`). |
| §4.8 phases/D3 | Real. One subprocess per phase, pid ledger, non-overlap asserted under a real subprocess run. `--resume` skips phases without re-verifying output hashes (`build.py:375`), which drifts from the §4.8 promise (ASK). |
| §4.9 evals | Modules are real, **but PC wires none of the judge or executable checks.** It evaluates `domain.student_base`, not the fused student (`build.py:239`), and never scores the base separately. |
| §4.10 output location (N7) | **Not honored.** The card, seal and report go to `NUCLEUS_DATA/runs/<id>/forge_out` (`certify.py:150`). Only the stub fuse marker reaches `FORGE_ROOT`. `/forge` and `verify <shard>@<ver>` can never find a certified card. |
| §8 `/build` retirement | Clean, no scope creep (details below). |

## Findings

### BLOCK

- **[BLOCK] B1: Stub results can be laundered into a trusted, ledgered, "real" card.**
  - **Where:** `certify.py:15-18,103,147`.
  - **What happens:** `certify` decides whether a build is a stub from `ARAIL_NUCLEUS_STUB` *at certify time*. Nothing records that the build itself ran on the stub provider.
  - **Reproduced:** I ran the Gate A fixture with `build` under `ARAIL_NUCLEUS_STUB=1` and then `certify` without it. The results:
    - `certify` exited 0 and wrote a card with `runtime: queuellm`.
    - The card was signed with a newly generated **lab** key: `key_fingerprint` is a real fingerprint, not `ephemeral-stub`.
    - The card was **appended to `CERTIFIED_SHARDS.md`**.
    - `nucleus verify` exited 0: `signature: valid / key: trusted / card_hash: match / chain: match`.
  - **Why it matters:** this defeats §6 #36 (T-STUB-2) end to end.
  - **Fix:**
    1. At build start, record `{"provider": "stub"|"queuellm"|…, "stub": bool}` in `context.json` and `run.json`, and have every phase stamp its actual provider runtime into its phase output.
    2. `certify` must derive `runtime` and `ephemeral=` from that record, never from the environment.
    3. If the build record and the current environment disagree, `certify` refuses (exit 3).
    4. Add a regression test that does exactly the reproduction above and asserts refusal, or at least `runtime: stub` + `ephemeral-stub` + not ledgered.

- **[BLOCK] B2: The verifier reports checks it never performed.**
  - **What's wrong:**
    - `seal.py:282`: `chain_status = "skipped" if fast else "match"`. The chain is **never recomputed**, yet non-fast verify prints `chain: match`.
    - `seal.py:317`: the CLI calls `verify(card, fast=fast)` without `eval_hash_recompute`, so `eval_hash` is always `skipped`. `seal.py:234` then counts `skipped` as OK, so `arailctl nucleus verify` exits 0 without ever reading `eval-config.lock`. That contradicts §4.10: "exits 0 only if all are valid/trusted/match".
    - `forge_api.py:131-133`: the `/forge` badge shows `trusted` when the signature is valid and **ignores `card_hash: mismatch`**. A card whose metrics were hand-edited after signing still shows a green "trusted" badge.
  - **Fix:**
    1. Until chain re-derivation exists, report `chain: not_checked` (never `match`) and make `all_ok` false for any `not_checked`/`skipped` field in the non-`--fast` CLI.
    2. The CLI must load `eval-config.lock` next to the card (`read_eval_config_lock`), recompute the hash, and pass it in. A missing lock means `mismatch`.
    3. The `/forge` badge must render `invalid` (or a distinct `tampered`) whenever `card_hash != match`.
    4. Add tests for all three.

- **[BLOCK] B3: Hardcoded or placeholder values go into a signed card as if measured.** Each of these ends up in the card and, via `card_sha256`, under the seal:
  - `build.py:259-263`: `open.lc_win_rate: 0.5`, `patch_applies: 0.0`, `checkpatch_clean: 0.0`. These feed the composite (`composite.py:35-39`), so the headline composite is `0.4·F1 + 0.15`. The probe card shows `composite.value 0.15` while `open_ended` says `not_run`.
  - `certify.py:68-69`: `base_composite = 0.0`, so `beats_base` is always true, W2 is never tested, and `KNOWN_ISSUE` can't be reached.
  - `certify.py:110-112`: `tokenizer_parity: True`, `captured_mass: 1.0`, `dropped_mass: 0.0` are all constants.
  - `certify.py:102,139,142`: `pipeline_hash: "sha256:not_computed"`, `teacher_hash: "teacher-hash-not-wired"`, `training_hash: sha256:000…`. The `chain_hash` in the seal therefore binds nothing.
  - `build.py:239`: PC's "student" is `domain.student_base`, i.e. the **base** weights, not the fused shard.

  **Fix (Gate A scope, no real runtime needed):**
  1. Every metric or provenance field that isn't actually computed must use the schema's `{status: not_run, reason}` form. Numbers are allowed only when computed.
  2. The composite must refuse, or return `not_computed`, when any input is `not_run`. `decide()` must then return a non-shipping decision (add `NOT_EVALUATED` to `decision_rule/v1` or refuse certify).
  3. `beats_base` must come from a real base-student score produced in PC, or be `unknown`, which blocks CERTIFIED/COMPATIBLE.
  4. Compute `pipeline_hash` with the existing `evals/hash.py::pipeline_hash`. It is implemented and tested, just never called.
  5. Compute `training_hash` over the adapter and fuse output. For the stub, hash the stub adapter bytes.
  6. PC must load the fused output dir recorded by `fuse`.
  7. Wire the LC judge and executable checks into PC for the stub path, since the stub already has a fixture judge table and fixture repo. Otherwise mark them `not_run` per item 1.

- **[BLOCK] B4: `eval_hash` does not change *iff* the yardstick changes.** The unit tests on `EvalHashInputs` are sound. The inputs `certify` actually builds are not:
  - `certify.py:79`: `"composite_formula": str(composite_result.inputs)`. `inputs` holds the **metric values** (`composite.py:41-43`), so two students scored on an identical yardstick get **different** `eval_hash`es. The same string is published as the card's `composite.formula` (`certify.py:129`), which violates the Q4 decision ("formula string is published in the card").
  - `certify.py:76`: `prompts="linux-kernel-task-adapters-v1"` is a hand-maintained label, not template bytes. Editing the templates in `evals/tasks/linux_kernel.py` leaves the hash unchanged.
  - `certify.py:88`: decoding records only `temperature`. `max_new_tokens`, `top_p`, `stop` and `seed` are missing.
  - `certify.py:80,84`: judge rubric, judge identity and checkpatch sha are the literal `"not_run"`, which is acceptable only while those metrics really are `not_run` (ties to B3).
  - **Fix:**
    1. Add a constant formula-string registry in `composite.py`, e.g. `"0.4*closed.mean_f1+0.3*open.lc_win_rate+0.3*mean(patch_applies,checkpatch_clean)"`, and use it in both places.
    2. Hash the actual prompt template strings, either exported from `evals/tasks/linux_kernel.py` or produced by rendering a fixed dummy item.
    3. Record full `Decoding` for every role.
    4. Add an **integration** test: two `run_certify` calls with different `metrics.json` and the same yardstick give equal `eval_hash`. Changing a template string or `max_new_tokens` gives a different one.

- **[BLOCK] B5: The closed-task yardstick leaks the answer and misparses negations.**
  - `evals/tasks/linux_kernel.py:44-52`: `subsystem_routing` gold is the subject prefix before `:`, and the prompt contains `Subject: {subject}`. The model can copy the answer, so macro-F1 measures string copying.
  - `linux_kernel.py:55-63`: `parse_closed_answer` does substring matching in `valid` order `("cve","not")`, so "This is not a CVE fix" parses as `cve`. The headline cve_detection F1 is corrupted by any explanatory answer.
  - **Fix:**
    1. Strip the `<subsystem>:` prefix from the subject shown in the routing prompt. Alternatively, derive gold from `MAINTAINERS` or the file paths as §3/Assumption 9 designed, and keep the subject prefix out of the prompt.
    2. Make the parser strict: match the first whole-word token of the stripped answer against `valid`, and treat anything else as `invalid`.
    3. Add tests: the routing prompt must not contain the gold label, and the text "not a cve" must parse to `not`.

- **[BLOCK] B6: "Preflight never names Buddy as the model to drop" is structurally untestable, and the test is tautological.**
  - `preflight.py:293`: `protected = ["Buddy"]` is a literal label. `drop` holds model *directory names* (`preflight.py:358`), so `drop ∩ protected = ∅` holds trivially.
  - The design's judge alias `ai-engineer → Qwen2.5-7B-Instruct-4bit` is the same model as Buddy's deep model in the §4.5 example. A Phase-C refusal can therefore say "Drop: Qwen2.5-7B-Instruct-4bit", which tells the user to drop Buddy's model.
  - T-PRE-3 (`test_preflight.py:68-83`) only varies a teacher named `teacher-x`, so it cannot catch this.
  - Also, `build.run` passes no models to `run_preflight` (`build.py:340`), so the memory plan never runs for a real build.
  - **Fix:**
    1. `protected` must be the resolved Buddy model identities: the Ollama tier model names plus the deep-mode model dir from `runtime_names.buddy_deep_model_env_value()`.
    2. `_refuse` must pick the largest **non-protected** model, compared by `model_identity`, not by name.
    3. When the only oversized model is protected, the refusal must say so, with no drop candidate.
    4. `build.run` and `plan <slug>` must resolve student, teacher, base and judge and pass them in.
    5. The property test must include cases where Buddy's model is the judge or the largest Phase-C model, and assert it is never in `drop`.

- **[BLOCK] B7: Real-runtime builds are not honestly gated.** The `_mlx_train_cycle_fn` stub itself (`build.py:177-193`) is acceptable for Gate A: the sprint scope excludes Gate B, and it refuses with a clear message and a BACKLOG entry. What isn't acceptable is *where* the failure surfaces.
  - A non-stub `build` passes preflight, because no model rows or logprob probe run.
  - It then spawns PA, which calls `resolve_model(context.get("teacher_name", ""))` (`build.py:88,134`). `teacher_name` is never written to the context and `select_teacher` is never called, so the user gets a misleading "model '' not found … hf download" error, after the lock and run dir are already created.
  - `probe_logprobs_capability` (`providers/queuellm.py:83`) is never called outside tests.
  - **Fix:**
    1. In `build.run`, before any lock, run dir or phase, refuse non-stub builds with exit 3 and one plain sentence. For example: "Real-runtime builds are not wired yet in sprint 1 (teacher selection, logprob probe, and MLX training cycle — see BACKLOG 'Model Forge's real MLX training-cycle wiring'). Gate A runs with ARAIL_NUCLEUS_STUB=1."
    2. Add a CLI test for that refusal.
    3. Expand the BACKLOG entry to list everything unwired: teacher auto-select, parity, logprob probe, residency sampler, judge, executable checks, pipeline/training hashes, and provenance (see tech debt below).
  - With that gate in place, the MLX stub is acceptable for Gate A.

- **[BLOCK] B8: The hardened git runner still executes repo-configured programs.**
  - **Reproduced:** in a local repo, `git config log.showSignature true` plus `git config gpg.program <script>`, with HEAD a commit carrying a `gpgsig` header. `arail.nucleus.corpus._git_env.run_git(["log", …])` **ran the script**: a marker file was created in the scratchpad.
  - `_git_env.py:43-48` disables only hooks, fsmonitor and protocols. The repo-local `.git/config` is still honored. That contradicts the module's own contract (`_git_env.py:4-6`: "without letting it execute anything").
  - T-SEC-GIT-1 (`test_corpus_stage.py:101-133`) is tautological: `git log` never runs `post-checkout` or fsmonitor, so it would pass with no hardening at all.
  - **Fix:**
    1. Add `-c log.showSignature=false -c gpg.program=/usr/bin/false -c core.pager=cat -c diff.external= -c core.sshCommand=/usr/bin/false` to both `_git_env.run_git` and the inline invocations in `executable_kernel.py:86-98` (or better, route those through `run_git`).
    2. Pass `--no-show-signature --no-ext-diff --no-textconv` to `log`.
    3. Replace the test vector with the `gpg.program` + signed-commit repro, which does fire without the fix.

- **[BLOCK] B9: Certified output never reaches `FORGE_ROOT`, and the version drifts.**
  - `certify.py:150` writes to `runs/<id>/forge_out` because `shard_output_dir` is never set.
  - `fuse` picks `next_patch_version()` (`build.py:202`), but the card says `context.get("version") or "0.1.0"` (`certify.py:100`).
  - Consequences: the `/forge` list (`forge_api.py:66-85`) and `verify <shard>@<ver>` (`seal.py:290-296`) can never see a certified card, and card and weights may disagree on version.
  - **Fix:** `fuse` records `{shard, version, shard_dir}` in the run ledger. `certify` writes `dna-card.yaml`, `seal.json`, `build-report.md` and `eval-config.lock` into that `shard_dir`, and the card's version comes from the ledger. The e2e test then asserts the card appears at `FORGE_ROOT/<shard>/<ver>/`, in `/api/forge/cards`, and via `verify <shard>@<ver>`.

- **[BLOCK] B10: Gate A's capstone asserts no golden metrics.**
  - T-E2E-1 (§7) requires "golden metric values match".
  - `test_e2e_gate_a.py` asserts no metric, composite or decision value.
  - `test_certify.py:29` asserts `decision in (all four decisions)`, which is tautological.
  - The stub fixture currently produces `closed.mean_f1 = 0.0` (seen in the B1 probe), so the stub student answers nothing correctly. The fixture was supposed to give exact-rational goldens (§7.1).
  - **Fix:**
    1. Tune the stub answer tables so each metric is a known non-trivial rational.
    2. Assert exact `closed_ended` per-task F1, composite value and formula id, and decision in the e2e test.
    3. Also assert `stub` is not ledgered, and assert `eval-config.lock` recomputes to the card's `eval_hash`.

### ASK

- **[ASK]** `runtime_names.py:135-137` hashes `module.__file__`. For the installed package that is `aerollm_api/__init__.py`, not `aerollm_api.abi3.so`, so `source` always reads `local-build`. Also, `runtime_provenance` is never called by the pipeline. Hash the extension file (`aerollm_api.aerollm_api.__file__` or the `.so` in the package dir) and add a test using a fake package layout. This must be fixed before Gate B.
- **[ASK]** Contamination: `certify.py:48-50` feeds only raw train corpus items. §4.9 requires "prompts + teacher outputs" (the `extract/*.npz` and teacher text). `contamination.py:73,81` keeps every train doc's n-gram set in memory, which is O(train), not the designed O(cert), and will fail the "1 M lines < 1 GB" perf target. There is no boundary test at exactly 8/800 = 0.01. Dates are compared as strings (`:95`), so a timestamped date on the cutoff day counts as a leak.
- **[ASK]** T-JUDGE-1 (`test_open_lc_judge.py:14-44`) compares literal strings, so "by content, not name" is asserted but never exercised. Add a test with two tmp model dirs, one reached by alias and one a byte-identical copy, going through `resolve_model` → `model_identity` → `assert_judge_identity_distinct`. Wire the check into PC (B3).
- **[ASK]** T-EGR-1 (`test_e2e_gate_a.py:199-258`) only covers `stage` + `CertStore.create`, not build phases or certify. Extend the in-process socket guard over `run_phase_body` for all phases and `run_certify`.
- **[ASK]** Tests write into the real `lab/data` of the checkout: `lab/data/nucleus/build.lock` (test pid 81811) and 26 `source: nucleus` lines appended to `lab/data/activity.jsonl` during this review's run. On a user machine that pollutes Buddy/SRE's activity feed. Point `activity_log` and `nucleus_data()` at tmp in an autouse fixture in `tests/nucleus/conftest.py`.
- **[ASK]** `/forge` on minimalist: `cryptography` is maximus-only, so `_verify_badge` raises, is caught, and every card shows `invalid` (`forge_api.py:125-130`). The design said "signature not checked". Render a neutral `unchecked` badge when `cryptography` is missing.
- **[ASK]** Cert set: `stage` mints a cert only with `--new-cert-version`, and `docs/nucleus.md` never mentions that flag. A first build fails at PA2, *after* Phase A. Add a preflight "cert set present" row and document the step.
- **[ASK]** `seal.py:69` checks the key's mode but not its owner (§6 #35 says "0600, owner"). `seal.py:240-242`: malformed `public_key_hex`/`signature_hex` raises outside the `try`, so it becomes exit 1 "internal error" instead of `signature: invalid`.
- **[ASK]** `spike.py:115-120,99-102`: the real provider path refuses, so the "Gate B harness" can't run on the M5 as-is, and B3 always passes via `classify([])` = `unmeasured`. File a BACKLOG entry. B3 must never read "pass" when unmeasured on a real run.
- **[ASK]** `certify.run_certify` is ~170 lines (`certify.py:21-188`), with card assembly, hashing, signing, report and ledger inline. Once B3/B4/B9 land, split out `_eval_hash_inputs(domain, …)` and `_assemble_card(…)`.

### INFO

- **[INFO]** `executable_kernel.py:86-89`: `base_commit` goes to `git read-tree` without validation. Values come from the operator's corpus, not the model, but check `^[0-9a-f]{7,40}$` anyway. Read-tree `TimeoutExpired` is not caught.
- **[INFO]** The `/build` retirement (`82ed6c78`) is clean: only §8 files are deleted, `/build` → 308 `/forge` keeps the query string, and there are no remaining `arail.build` / `build_api` / `/api/build` imports or routes. Two cosmetic leftovers:
  - `_model_switcher.html:89` still offers a "Model Building" per-tab override for a tab that no longer exists.
  - `docs/maximus.plan.md:8-11` still points to "the `/build` tab explainer".
- **[INFO]** BUILD_LOG says pre-existing failures were confirmed by "a scoped `git stash`-and-rerun". This machine shares its stash stack across worktrees and concurrent sessions, so prefer a temp WIP commit or `git worktree add` at the base SHA.
- **[INFO]** `resume` + lock ordering: `context.json` and the run dir are written before `build_lock` is taken (`build.py:349-373`). That's harmless today and worth tidying up with B7.

## Security findings

What I checked, specifically:

- **Build token:**
  - Sent only in `Authorization: Bearer` (`gateway.py:83`), with `allow_redirects=False` and 3xx treated as an error.
  - `__repr__` is redacted (`:70-71`). Exception messages carry host + status only (`:87-96`). The egress reason is `nucleus-gateway:<build_id>` with no token (`:79`).
  - Tests cover repr, exception, header and egress.jsonl (`test_gateway.py:177-210`). **OK.**
- **Secrets file:** Nucleus only *reads* `DATA_DIR/secrets.env` (`gateway.py:20-38`). The portal writes it through `_chmod_600` (`portal/app.py:1495-1582`). The signing key is created with `os.open(..., 0o600)` inside a 0700 dir, and non-0600 keys are refused (`seal.py:68-87`). **OK**, owner check aside (ASK).
- **Airgapped `gateway`:**
  - `profile_gate` runs before preflight in `build.run` (`build.py:331-333`). `GatewayClient.__init__` checks airgap before any session.
  - Both raise `ProfileRefused(AIRGAPPED_NOTICE)`, which subclasses `RefusedByPolicy`, so the CLI exits 3.
  - The test compares `str(exc) == airgap.AIRGAPPED_NOTICE` verbatim, and checks there is no socket connect and no `egress.jsonl`. **Real, not tautological.** Gap: no CLI-level test (stderr + exit 3) as T-GW-1 specified. Minor.
- **`domain.yaml` traversal:**
  - Sources are constrained by the schema pattern `^(git|cve|lkml|lwn|world):[a-z0-9][a-z0-9_-]*$` plus the `/` check (`domain.py:173-177`), and source names are never used as path components (`stage.py:69-104`).
  - The eyeball file is realpath-contained, with tests for `../`, absolute and symlink escapes. **OK.**
- **Model patches:** they reach only `git apply --check --cached` against a throwaway `GIT_INDEX_FILE`, with hooks and fsmonitor off, no `--unsafe-paths`, and pre-screening for size, NUL, symlink, absolute and `..`. `compiles` has no subprocess. **OK**, apart from B8's config-exec vector, which applies to the shared runner. Note: `patch_applies` is not reached by the pipeline at all today (B3).
- **Network in tests:** nothing in `tests/nucleus` hits a non-loopback host. The gateway mock is `127.0.0.1`, and preflight's Ollama probe is loopback with a 1 s timeout. T-EGR-1's scope is narrow (ASK).
- **Frozen surface:** confirmed unchanged (see Spec adherence).
- **Writes to repo `models/`:** none. The diff touches no `models/` or `lab/` path. `lab/models/` and `lab/data/` are git-ignored (`.gitignore:42-43`), and `guard_committable_output` enforces it at runtime. The stub fuse marker goes to `$ARAIL_MODELS_DIR/forge/…`. The card does *not* (B9).

## Test coverage assessment

- 324 pass / 2 skip (`requires_mlx`, and `requires_qkz_bin`, which passes when the binary is supplied).
- Real and adequate §6 tests: #13, #19 (tamper → `CertTampered`), #21 (10/800 refuse, 7/800 pass, exact-sha, temporal), #22, #23 (dataclass level), #24 (no `accuracy` key, schema rejects), #28–#30, #31–#35 (seal, including the real binary), #37–#41, #42–#46, #47–#50, #53–#54.
- **Tautological or mis-aimed:**
  - #8 T-PRE-3 (B6)
  - #17 T-SEC-GIT-1 (B8)
  - #25 T-JUDGE-1 (ASK)
  - #15 T-EGR-1 scope (ASK)
  - T-E2E-1 goldens (B10)
  - `test_certify.py:29` (B10)
- **Missing:**
  - #7 via `build` (B6)
  - #1 preflight-level refusal (B7)
  - #10 sampler during build
  - #36 across processes (B1)
  - #55 disk row (T-PRE-5 only checks a `disk_gb` key exists)
  - #23 at the pipeline level (B4)
- Changed-line coverage was not measured (no `pytest-cov` run in BUILD_LOG). QA should report it. Given the unwired orchestration, I expect `build.py`'s non-stub branches and `certify.py`'s real-mode branches to be well under 80 %.

## Performance assessment

Not on a hot path for Gate A. One concern for Gate B: contamination memory is O(train docs × n-grams) (ASK). No benchmark was run, and none was required for Gate A.

## Tech debt delta (vs ARCHITECTURE §9)

The architect predicted "positive (moderate)" and 3 tickets. **Actual: positive (significant).** Filed so far: §9 #1–#3 and the MLX wiring (`sprints/BACKLOG.md:1420-1506`). **Not anticipated and not filed:**

1. The orchestration layer doesn't wire teacher auto-select, tokenizer parity, logprob probe, residency sampler, LC judge, executable checks, `pipeline_hash`, `training_hash`/`teacher_hash`, or runtime provenance into `build`/`certify`. Everything in B3 and B7.
2. The Spike harness has no real-runtime provider, and B3 is always pass.
3. Card output lives outside `FORGE_ROOT` (B9, to be fixed, not filed).
4. Tests leak into the real `lab/data`.

Before PASS, the builder must either fix these (the B-items) or, for anything legitimately deferred to Gate B, file each one in `sprints/BACKLOG.md` under a single "Model Forge real-runtime wiring" umbrella, with a line added to ARCHITECTURE §9 "Added".

## Required actions before merge

1. **B1:** record stub-ness at build time; `certify` derives runtime and key from the build record and refuses on mismatch; add the repro regression test.
2. **B2:** `verify` must not claim `chain: match`; the CLI recomputes `eval_hash` from `eval-config.lock`; `skipped`/`not_checked` fails the non-fast CLI; the `/forge` badge honors `card_hash`.
3. **B3:** no hardcoded metric or provenance value in a card:
   - uncomputed fields become `not_run`;
   - composite and decision handle `not_run` inputs;
   - compute `pipeline_hash` and `training_hash`;
   - PC evaluates the fused student and scores the base;
   - `beats_base` is real or `unknown`.
4. **B4:** constant formula string (card and hash); hash real template bytes and full decoding; add a pipeline-level iff test.
5. **B5:** remove the subsystem label from the routing prompt; strict whole-token closed-answer parse; add tests.
6. **B6:** `protected` = resolved Buddy models by identity; `build`/`plan` pass resolved models into preflight; a real property test with Buddy's model as a candidate.
7. **B7:** refuse non-stub `build` (and `certify` of a non-stub build) up front with exit 3 and a plain message; expand the BACKLOG entry.
8. **B8:** block repo-config exec vectors in the git runner (all call sites); replace T-SEC-GIT-1 with the `gpg.program` repro.
9. **B9:** card, seal, report and lock go to `FORGE_ROOT/<shard>/<ver>` with the version from the fuse record; the e2e test asserts `/api/forge/cards` and `verify <shard>@<ver>`.
10. **B10:** exact golden metrics, composite and decision in the Gate A e2e test; drop the tautological decision assertion.
11. Resolve or ticket every ASK above, and file the unanticipated debt in BACKLOG and ARCHITECTURE §9.

Then re-request architect review. QA should not start on this build; most QA time would go to rediscovering the items above.
