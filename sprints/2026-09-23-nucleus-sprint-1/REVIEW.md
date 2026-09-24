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

---

# Round 2

**Date:** 2026-09-23
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) "Review loop 1" at `f17828eb` (fix commits `2cc89e5b..886a0e1c`, diff base `a7f91027`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md). §4.9 and §9 item 7 were amended in this round's commit to record the `composite/v1-open` decision and the open-eval drift.
**Reviewer:** architect (review mode, round 2)

## Verdict: BLOCK

The loop fixed most of what round 1 found. B1, B2, B5, B7, B8 and B9 are resolved, and I re-ran my own reproductions to confirm it. The orchestration layer now mostly computes what it signs.

Three BLOCKs remain. Each one is round-1 work that stopped short, and each is small:

- **R1:** the signed card contradicts its own composite.
- **R2:** `eval_hash` does not cover the open-ended yardstick that the loop newly wired in.
- **R3:** B6's Buddy guard misses Buddy's model when it is configured by absolute path, which is a supported and documented configuration.

No round-1 ASK is promoted to BLOCK. The one ASK that belonged there (base-student numbers missing from the card) is folded into R1.

**Test run on this worktree:**
- `.venv/bin/pytest tests/nucleus tests/portal/test_forge_viewer.py -q` gave **358 passed, 2 skipped**, which matches the orchestrator's count.
- I touched a sentinel before the run and ran `find lab models -newer <sentinel>` after it. Result: **nothing written** under `lab/` or `models/`, and `git status` was clean apart from the pre-existing untracked `.venv`/`logs/`. The conftest isolation ASK is resolved.
- `tests/nucleus/test_seal.py` with `NUCLEUS_QKZ_BIN=~/ProJects/qukaizen-nucleus/qkz/target/release/qkz` gave **18 passed**. The real Rust verifier still accepts the seal.

## Round-1 BLOCKs, one by one

### B1: Resolved (with an ASK)

**Reproduction re-run.** I ran the Gate A fixture through the real CLI as subprocesses: `build` under `ARAIL_NUCLEUS_STUB=1`, then `certify` with the variable unset.
- `certify` now exits **3**: "this build ran with stub=True (recorded at build time) but the current environment has ARAIL_NUCLEUS_STUB=<unset>".
- No card is written, and `CERTIFIED_SHARDS.md` does not exist.
- `context.json` records `"stub": true, "provider": "stub"`, and every phase output carries `"provider": "stub"`.

**Test.** `test_certify_refuses_when_env_disagrees_with_build_record` reads `context.json` from disk (`context=None`). Without the fix, `run_certify` would proceed and the `pytest.raises` would fail, so the test is real.

**Residual (ASK A1).** If I hand-edit `context.json` to `"stub": false` and run `certify` without the stub variable, the result is exit 0: a **lab-key-signed, ledgered, CERTIFIED** card built from stub metrics.
- The per-phase `provider` stamps that fix item 1 added are never read by `certify`.
- This is not a security boundary, since the key owner could sign anything anyway.
- The check is still cheap. B7 refuses every non-stub build this sprint, so any `stub: false` record is by construction not from `build.run`.

### B2: Resolved

What `verify` prints now matches what it computed:
- `signature`, `key` and `card_hash` are computed as before.
- Non-fast `eval_hash` is recomputed from the `eval-config.lock` next to the card. I observed `match` with the lock present and `mismatch` with the lock deleted.
- `chain` now reads `not_checked` and never `match`.
- In non-fast mode, `all_ok` requires `eval_hash == match` and `chain == match`.

`/forge`'s `_verify_badge` checks `card_hash` first. I edited one metric in a signed card: `verify --fast` reported `card_hash: mismatch`, and the badge path returns `tampered`.

The three cited tests would fail without the fix:
- The old code returned `chain="match"`.
- The old CLI never read the lock.
- The old badge returned `trusted` for a hash mismatch.

Consequence to document (ASK A9): the non-fast `verify` can currently **never exit 0** for any card, because `chain` is always `not_checked`. That is honest and is what I asked for, but `docs/nucleus.md` and the CLI output should say so.

### B3: Partially resolved, residual is BLOCK R1

**Fixed:**
- `pipeline_hash` is real: `evals/hash.py::pipeline_hash`.
- `training_hash` is a content hash of fuse's output directory.
- `teacher_hash` / `teacher identity` is honestly `unresolved:auto`.
- `tokenizer_parity` is `false`, with the reason stated.
- `captured_mass`/`dropped_mass` are omitted.
- The executable checks are `not_run`.
- PC now scores the **fused** output directory and, separately, the base.
- `beats_base` is a real comparison, with `None` treated as not-beating.

**Not fixed: the card still contradicts itself.** This is the same symptom I quoted in round 1 ("composite.value 0.15 while open_ended says not_run"). The Gate A card I generated shows:
- `composite: {formula_id: composite/v1-open, value: 0.566191}`, which is `0.6·0.6103175 + 0.4·0.5`, so it consumes `open.lc_win_rate = 0.5`.
- `open_ended: {patch_explanation: {status: not_run, reason: "judge not wired in this generic certify path"}}`, which comes from `certify.py:278`, still hardcoded.
- `baselines: {}`, although the CERTIFIED vs KNOWN_ISSUE decision depends on `base_closed.mean_f1 = 0.4293`, which is not in the signed card.

**The measured LC value is also a self-comparison.** In `build.py::_score_open_lc`:
- Neither the fused provider nor the base provider gets an answer table, so both return `"[stub:good] deterministic answer for eyeball-i"`. The texts are identical.
- `StubJudge()` with no preference table always answers `A`.
- So `lc_win_rate` equals the fraction of items where the seeded randomisation put the model in position A. That comes out at exactly 0.5.
- A bug that inverted the un-swap would give `1 − 0.5 = 0.5`. The Gate A golden therefore cannot detect a broken judge path.
- This is B10's "known non-trivial rational" requirement unmet for the open metric.

### B4: Partially resolved, residual is BLOCK R2

**Fixed:**
- The formula string is a constant registry (`composite.FORMULA_STRINGS`), used for both the card and the hash.
- `prompts` hashes the real template bytes.
- `decoding` records the full `Decoding()`.
- `test_eval_hash_identical_for_different_metric_values_same_yardstick` would fail on the old `str(composite_result.inputs)`, so it is a genuine iff test.

**Not fixed.** The loop turned `open.lc_win_rate` from a constant into a measured input to the composite and the decision. The yardstick behind that measurement is still outside `eval_hash`:
- `scoring.judge_rubric` and `scoring.judge_model_identity` are still the literal `"not_run"` (`certify.py`). Round 1 said that literal is acceptable **only while those metrics really are not_run**, and they no longer are.
- The prompt set PC actually judges (the domain's eyeball file, `_score_open_lc`) is not in `prompts`. Only the closed templates are.

So editing the eyeball file, or swapping the judge, changes `lc_win_rate` → composite → decision without changing `eval_hash`. That directly fails the brief §7 acceptance line "eval_hash changes when any of … prompt / scoring … changes, and only then".

**Weaker, ASK A5:**
- `decoding` is asserted from `Decoding()` in `certify.py`, not derived from what the phases actually used.
- `test_eval_hash_changes_when_decoding_changes` patches only `certify_mod.Decoding`, the recorded side, so it cannot catch a provider that decodes differently.

### B5: Resolved

- The routing prompt now shows only the text after the first `:`, and gold is the prefix. `test_subsystem_routing_prompt_never_contains_the_gold_label` asserts this.
- `parse_closed_answer` is whole-token and first-in-answer-order, so "not a cve" parses to `not`.
- The old substring loop in `valid` order would fail the negation test.

INFO: nested prefixes such as `net: ipv4: …` still show `ipv4:` to the model. That is not the gold label, so it is acceptable.

### B6: Partially resolved, residual is BLOCK R3

**Fixed:**
- `protected` is now Buddy's configured names (`MODEL_NAME` and `buddy_deep_model_env_value()`), resolved through `resolve_model` → `model_identity`.
- `_refuse` picks the largest non-protected candidate.
- `build.run` and `plan` pass the resolved models in.

**Reproduced failure.** `resolve_model` rejects any value containing `/`. When `AEROLLM_MODEL`/`QUEUELLM_MODEL` is an **absolute path**, that resolution fails, and the protected key falls back to the raw path string. A candidate model, on the other hand, is keyed by its content hash, so the two never match. The absolute-path form is supported: `AeroLLMBackend` accepts it, and `docs/verification/aerollm-1.0.0-pin.md` uses it.

My probe setup:
- Real tmp model dirs.
- `AEROLLM_MODEL=<abs path>/Qwen2.5-7B-Instruct-4bit`.
- The judge set to `resolve_model("ai-engineer")`.
- Phase C over budget.

Result: `PreflightRefusal.drop == ['Qwen2.5-7B-Instruct-4bit']`. That tells the user to drop **Buddy's model**, which is the exact round-1 scenario. With the bare-name form, the same probe correctly drops `student`.

**Tests.** The B6 tests and the 200-combo property test use `SimpleNamespace` doubles with no `.path`, so every comparison is name against name. The identity path the fix depends on is never exercised.

The `PreflightRefusal` invariant `drop ∩ protected = ∅` also compares **names**, so it cannot catch this case either.

### B7: Resolved

`build.run` refuses a non-stub build before any lock, run dir or phase exists. The refusal happens right after the domain loads and before `profile_gate`. Exit is 3, and the message is plain.

The BACKLOG umbrella entry exists. It is partly stale (ASK A10).

INFO: because the B7 refusal runs before `profile_gate`, a non-stub `--profile gateway` build under airgapped now gets the "not wired" message instead of the airgap banner. That is harmless while B7 stands.

### B8: Resolved

**Reproduction re-run.** I built a hostile repo with:
- `gpg.program`, `gpg.ssh.program`, `core.pager` and `diff.external` each pointed at marker scripts;
- `log.showSignature=true`;
- `diff.evil.textconv` enabled through `.gitattributes`;
- a fabricated `gpgsig` commit.

With the **old** argument set:
- `log --format=%H`, `log --format='%H %G?'`, `log --stat` and `show HEAD` each fired **gpg**;
- `log -p` fired **gpg and textconv**.

With the new `run_git`, **none of them fired**.

The cited test would fail without the fix. `git_kernel.extract` is the only `run_git` caller and it only runs `log`, which also gets `--no-textconv`. `executable_kernel.py`'s two inline calls use `HARDENED_CONFIG_ARGS`.

INFO:
- `diff.<driver>.textconv` is neutralised only for `log`. Any future `show`/`diff` caller must add `--no-textconv`.
- `run_git` does not pass `stdin=DEVNULL` when there is no input, so a repo-configured program that reads stdin would inherit the parent's stdin.
- `paths.py:145` runs `git check-ignore` unhardened, but against ARAIL's own checkout, so that is acceptable.

### B9: Resolved

Observed:
- `fuse.json` records `{shard, version, shard_dir}`.
- The card, seal, report and lock land at `$ARAIL_MODELS_DIR/forge/qkz-kernel/0.1.0/`.
- The card's `version` comes from fuse.
- `verify qkz-kernel@0.1.0` finds it.
- The e2e test asserts `/api/forge/cards` lists it.

INFO:
- A refused certify (for example, contamination) leaves fuse's uncertified weights directory under `FORGE_ROOT`.
- Re-certifying the same build overwrites the signed card in place.

### B10: Mostly resolved, residual folded into R1

- The e2e test asserts exact per-task F1, macro-F1, composite value, formula id and string, the decision, the executable `not_run` fields, lock→`eval_hash` recompute, and not-ledgered.
- The fixture is byte-reproducible (`GIT_COMMITTER_DATE`).
- The tautological `decision in (…)` assertion is gone.

The open metric's golden is the 0.5 self-comparison described under B3 (R1).

INFO: the comment in `test_certify.py` says fused and base "score IDENTICALLY … same deterministic fallback text". That has been false since B10 added answer tables (salts `fused-v1`/`base-v1`, wrong-every 5 vs 2). The `KNOWN_ISSUE` asserted there is a property of the staged fixture's items, not the stated reason (ASK A6).

## The builder's flagged gap: `composite/v1-open`: accepted, not a BLOCK

**Decision:** acceptable for Gate A. I have recorded it in ARCHITECTURE §4.9 and §9 item 7.

**Reasoning:**
- Brief §5.5 requires the formula to be "published, versioned" in the card. `v1-open` meets that: it has a distinct `formula_id`, a constant formula string in the card, and that string goes into `eval_hash`.
- VISION risk #4 prescribes exactly this mechanism for unmeasurable executable checks: mark them `not_run`, which "changes `eval_hash` and the composite formula version", and "do not substitute a proxy".
- `v1-open` is selected by rule (`select_formula_id`), never by hand, by the same mechanism that already picks `v1-nc`.
- The alternative, keeping `executable` in the formula and refusing certify, would make Gate A unreachable without building a patch-generation task. That would be new capability, not wiring, and the builder was right not to improvise it.

**Conditions (now in ARCHITECTURE §4.9):**
1. **Gate B precondition:** before B7's non-stub refusal is lifted, a `v1-open` card must be capped at `COMPATIBLE`. A kernel shard with zero executable evidence must not read `CERTIFIED`. This sprint only produces stub cards, which are ephemeral-keyed, never ledgered and badged `STUB`, so the cap is not needed for Gate A.
2. Retire `v1-open` once `patch_applies`/`checkpatch_clean` are wired.
3. The 0.6/0.4 weights are not the proportional renormalisation of v1 (4/7, 3/7). That is fine because they are published, but note it in the formula registry docstring.
4. `v1-open` is only honest if the card carries the open input it consumes. That is R1.

**On `NOT_EVALUATED`:** refusing certify is the right call. The card schema is a frozen wire contract, and widening the decision enum would be an architecture change I am not making. `decide()` returning `NOT_EVALUATED` immediately before the refusal is acceptable as documentation.

## Round-1 ASKs: status

| Round-1 ASK | Status |
|---|---|
| `runtime_names` hashes `__init__.py`, not the `.so`; `runtime_provenance` never called | **Open.** Unchanged, and in the BACKLOG umbrella. Required before Gate B. |
| Contamination: raw train only, not prompts + teacher outputs; O(train) memory; no 8/800 boundary test; string date compare | **Open.** Unchanged. It becomes load-bearing at Gate B. Not promoted, because stub train material has no teacher outputs of consequence. |
| T-JUDGE-1 literal strings; judge identity check not wired | **Open, and more pressing.** PC now runs a judge but never calls `assert_judge_identity_distinct`. For StubJudge that is moot; R2's judge-identity recording is the seam for it. |
| T-EGR-1 scope | **Open.** |
| Tests write into real `lab/data` | **Resolved** (`2cc89e5b`, verified by the sentinel run). |
| `/forge` on minimalist shows `invalid` without `cryptography` | **Open.** `_verify_badge` still maps any exception to `invalid`. |
| Cert set needs `--new-cert-version`, undocumented; no preflight row | **Open.** `docs/nucleus.md` still never mentions the flag. |
| `seal.py` key owner check; malformed hex raises outside `try` | **Open.** |
| `spike.py` real path refuses; residency B3 "passes" when unmeasured | **Open, and not in the BACKLOG umbrella.** Must be filed. |
| `run_certify` too long | **Worse.** It is now **342 lines** (was ~170), with three nested helpers. Not a correctness issue, so not promoted. R1 and R2 both edit this function again, so extract `_eval_hash_inputs`, `_assemble_card` and `_provenance_hashes` while doing them. |

## New findings this round

### BLOCK

- **[BLOCK] R1: The signed card must carry, consistently, every number its composite and decision are computed from.**
  - **Where:** `certify.py:278` (`open_ended` hardcoded `not_run`), `certify.py` `build_card(... baselines` omitted `)`, and `build.py::_score_open_lc` (identical fused and base texts).
  - **Fix:**
    1. Build `open_ended` from `metrics["open"]`. When `lc_win_rate` is a number, write `{lc_win_rate_vs_base, judge, n, ci95}`; the schema already permits these fields. Write `not_run` only when `metrics["open"]["lc_win_rate"] == "not_run"`. Name the entry after the set actually judged (for example `eyeball_explanation`). Keep `patch_explanation` only once the open eval runs on cert items.
    2. Write `baselines.base_student` with the base `closed.mean_f1` and rows that `beats_base` was computed from. State in the card, or in the formula docstring, that `beats_base` compares `closed.mean_f1`.
    3. Make the stub LC path discriminating:
       - Give the fused and base stub providers different open answers, for example an `(eyeball-i, "open")` answer table per salt.
       - Give `StubJudge` a preference table keyed so the true preference is known.
       - The golden must be an exact value that is **not 0.5**, and a position-unswap inversion must change it.
    4. Add an invariant test: recompute the composite and the decision **from the card alone**, from `closed_ended`, `open_ended`, `executable` and `baselines` through `composite.compute`/`decide`, and assert they equal `composite.value` and `fidelity.decision`.

- **[BLOCK] R2: `eval_hash` must cover the open-ended yardstick now that it is measured.**
  - **Fix:**
    1. When `open.lc_win_rate` is measured, set `scoring.judge_model_identity` to the judge's identity:
       - stub: `"stub-judge/v1:" + sha256(canonical preferences table)`;
       - real: `model_identity`.
       Set `scoring.judge_rubric` to the rubric bytes. For StubJudge, use a constant rubric id string declared next to the class. Keep `"not_run"` only when open is `not_run`.
    2. Put the bytes of the open-eval prompt set, meaning the eyeball file content PC judged, into `prompts`. For example, extend the joined payload with `open_eval=<the prompt lines>`.
    3. Add pipeline-level tests:
       - editing the eyeball file changes `eval_hash`;
       - changing the StubJudge preference table changes `eval_hash`;
       - the existing "different metrics, same yardstick → same hash" test still passes.

- **[BLOCK] R3: The Buddy guard must match Buddy's model by identity in every supported configuration form.**
  - **Where:** `preflight.py::_resolve_protected_identities` and `PreflightRefusal.__init__`'s invariant.
  - **Fix:**
    1. When a protected value is an absolute path to a directory containing `config.json`, build the `LocalModel` for it directly. Add a small helper such as `models.local_model_at(path)`. Do **not** route this through `resolve_model`, whose path ban exists for `domain.yaml`. Then key it by `model_identity`.
    2. Check the invariant by identity, not by display name.
    3. Replace the `SimpleNamespace`-only B6 tests with (or add) tests on **real tmp model dirs**, with Buddy configured as:
       - a bare name;
       - an absolute path;
       - a byte-identical copy under another name.

       In each case the judge is `resolve_model("ai-engineer")`, and Phase C is the largest and over budget. Assert that Buddy's directory is never in `drop`, and that the drop is the largest non-protected candidate.

### ASK

- **[ASK] A1:** `certify` should cross-check `context.json`'s `stub` against the per-phase `provider` stamps and refuse on disagreement. While B7 stands, it should refuse `stub: false` outright ("no non-stub build path exists in this sprint").
- **[ASK] A3:** `build-report.md`'s eyeball section still renders `"(not generated in this generic certify path)"` × 10 for the student and base columns, although PC now generates exactly those outputs. Brief §7: "includes the 10 eyeball prompts with outputs". Persist PC's fused and base eyeball generations and render them.
- **[ASK] A4:** `pipeline_hash` needs fixing in two places:
  - it passes `training_hyperparams={"target": fidelity_target}`, which is not a training hyperparameter; record LoRA rank, lr, cycles, stop rule;
  - `distill_params` lacks `renorm` and teacher decoding (§4.9).

  `training_hash` has two problems:
  - it omits the adapter;
  - `_dir_content_hash` reads each file fully into memory. Switch to the streaming, cached `models.content_hash` before real fused weights exist.
- **[ASK] A5:** derive the `decoding` recorded in `eval_hash` from what the phases actually used (record it in the phase outputs). Make the decoding test perturb the provider side.
- **[ASK] A6:** fix the false "score IDENTICALLY" comments in `test_certify.py`. Assert the fused and base F1 values that make `KNOWN_ISSUE` the outcome there, so it is not a salt coincidence.
- **[ASK] A7:** the executable `not_run` reason says "not available in this generic certify path". Say why: "no patch-generation task in sprint 1; see BACKLOG".
- **[ASK] A9:** document that non-fast `verify` exits non-zero until chain re-derivation lands (Gate B). Have the CLI print a one-line explanation under `chain: not_checked`. Also give the `tampered` badge a style in `forge.html`; today it is just a class name with no CSS.
- **[ASK] A10:** refresh the BACKLOG umbrella entry.
  - Its "tokenizer parity hardcoded" and "`pipeline_hash` never called" lines are stale.
  - Add these items:
    - the `v1-open` COMPATIBLE cap;
    - moving the open eval onto cert items;
    - `residency: unmeasured` currently permits CERTIFIED;
    - the spike harness has no real provider (round-1 ASK);
    - the contamination scope ASK.

### INFO

- The `select_formula_id` precedence is right: all three executable checks `not_run` → `v1-open`; only `compiles` `not_run` → `v1-nc`.
- The stub path legitimately ignores `model_path`, so "PC evaluates the fused student" is structurally true but behaviourally invisible under the stub. The closed-task tables are what differentiate fused from base.

## Security findings (round 2, what I checked)

- **Git config exec:** repro re-run against old and new argument sets (B8 above). Closed for every current call site.
- **Stub laundering:** CLI repro re-run (B1 above). Closed for the environment-mismatch path. The forged-record path is ASK A1. That path is not a boundary, because it requires the key owner to edit a file.
- **Tamper visibility:** metric edited after signing gives `card_hash: mismatch` and the `tampered` badge. Verified.
- **Writes outside tmp during tests:** none, verified by the sentinel.
- **Frozen surface:** the diff adds no `aerollm`/`AERO_` spelling outside `runtime_names.py`. The test files set `AEROLLM_MODEL` via `monkeypatch` only, and `arail.nucleus` still never writes `os.environ`.

## Test coverage assessment (round 2)

358 passed and 2 skipped; 18 seal tests pass with the real binary. Changed-line coverage was still not measured, and QA should report it.

Round-1 tautological tests now real:
- T-SEC-GIT-1 via the gpg test;
- T-E2E-1 goldens (except LC);
- B2's three tests;
- B4's metric-invariance test.

Still weak:
- the B6 tests (name-only doubles), R3;
- the LC golden (self-comparison), R1;
- the decoding-change test (recorded side only), A5.

## Tech debt delta (round 2)

ARCHITECTURE §9 now lists item 9 (the orchestration umbrella). This round, I added to §4.9 and §9 item 7:
- `v1-open` as a third formula, with its Gate B cap;
- the open-eval-on-eyeball drift.

`run_certify` doubled in size. Net debt is still **positive (significant)**, and it is now fully homed once A10 refreshes BACKLOG.

## Required actions before merge (round 2)

1. **R1:** make the card consistent and complete: open metric, baselines, a discriminating stub LC golden, and a test that recomputes the composite and decision from the card alone.
2. **R2:** put the judge identity, rubric, and open-eval prompt bytes into `eval_hash`, with iff tests.
3. **R3:** match protected Buddy models by identity for absolute-path configs, check the invariant by identity, and test on real model dirs.
4. Resolve or ticket ASKs A1 and A3–A10, plus the open round-1 ASKs listed above. Tickets go in the BACKLOG umbrella.

Then re-request architect review (round 3), which should be a short pass over R1–R3.

**What QA should target first, once round 3 passes:**
1. Card self-consistency: recompute the composite and decision from the card alone, and try hand-edited cards against `verify` and `/forge`.
2. `eval_hash` iff over every yardstick input: templates, eyeball file, judge, decoding, cert set. Also confirm that metrics, paths and build id leave it unchanged.
3. Preflight with Buddy configured in every form (bare name, alias, absolute path, byte-identical copy) on real model dirs.
4. Stub-laundering variants: environment mismatch, a forged `context.json`, and a missing `fuse.json`.
5. `verify` exit-code semantics.
6. Changed-line coverage on `build.py` and `certify.py`.
